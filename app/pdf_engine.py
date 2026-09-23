"""Read-only PDFium rendering and offline RapidOCR. Never rewrites imported PDFs."""
import ctypes
import io
import threading
from contextlib import contextmanager
from functools import lru_cache

import pypdfium2 as pdfium
import pypdfium2.raw as raw
from pypdf import PdfReader

# PDFium requires serialization even when different documents are accessed.
_pdf_lock = threading.RLock()
_ocr_lock = threading.Lock()


@contextmanager
def open_pdf(path):
    with _pdf_lock:
        doc=pdfium.PdfDocument(str(path))
        try:
            yield doc
        finally:
            doc.close()


def validate(path):
    with path.open('rb') as stream:
        reader=PdfReader(stream)
        if reader.is_encrypted:
            raise ValueError('Encrypted PDF')
    with open_pdf(path) as doc:
        if not 0 < len(doc) <= 3000:
            raise ValueError('PDF page count out of bounds')
        return len(doc)


def document_author(path):
    """PDF 元数据里的作者；没有就返回空串（不猜、不用文件名顶替）。

    作者只用于检索与筛选，因此这里不做任何推断：读不到就是空，由用户自己填。

    **只读 /Author（含 /author 拼写）**：这是 PDF 里唯一表示"作者"的标准字段。
    不要再把 /Creator 加回来——它记录的是生成该 PDF 的**软件**（Microsoft Word、
    LaTeX、CNKI 导出器等），不是作者。把它当作者有两个后果：卡片上显示
    "@Microsoft Word"，以及**搜"word"会命中这些文献**，让按作者检索返回假匹配。
    """
    try:
        with path.open('rb') as stream:
            reader=PdfReader(stream)
            meta=reader.metadata or {}
            for key in ('/Author','/author'):
                value=meta.get(key)
                if isinstance(value,str) and value.strip():
                    return value.strip()[:200]
    except Exception:
        return ''
    return ''


def visual_point(page,x,y):
    width,height=page.get_size()
    px,py=ctypes.c_int(),ctypes.c_int()
    if not raw.FPDF_PageToDevice(page,0,0,round(width*100),round(height*100),0,x,y,ctypes.byref(px),ctypes.byref(py)):
        raise ValueError('Cannot map PDF coordinates')
    return px.value/100,py.value/100


def visual_box(page,box):
    x0,y0,x1,y1=box
    points=[visual_point(page,x,y) for x,y in [(x0,y0),(x0,y1),(x1,y0),(x1,y1)]]
    return [min(p[0] for p in points),min(p[1] for p in points),max(p[0] for p in points),max(p[1] for p in points)]


def page_data(page):
    textpage=page.get_textpage()
    try:
        width,height=page.get_size()
        spans=[]
        characters=[]
        line_characters=[]
        letters=[]
        boxes=[]
        sizes=[]
        def finish():
            if letters and ''.join(letters).strip() and boxes:
                box=[min(b[0] for b in boxes),min(b[1] for b in boxes),max(b[2] for b in boxes),max(b[3] for b in boxes)]
                spans.append({'text':''.join(letters),'bbox':box,'size':sum(sizes)/len(sizes) if sizes else 12})
                # Use a consistent line height for selection while retaining each glyph's x bounds.
                if line_characters:
                    line_characters[-1]['line_end']=True
                    if box[2]-box[0]>box[3]-box[1]:
                        for glyph in line_characters:
                            glyph['bbox'][1]=max(0,box[1]-.5)
                            glyph['bbox'][3]=min(height,box[3]+.5)
                    else:
                        for glyph in line_characters:
                            glyph['bbox'][0]=max(0,box[0]-.5)
                            glyph['bbox'][2]=min(width,box[2]+.5)
            letters.clear();boxes.clear();sizes.clear()
            line_characters.clear()
        for i in range(textpage.count_chars()):
            char=textpage.get_text_range(i,1)
            if char in ('\r','\n'):
                finish()
                continue
            if not char:
                continue
            try:
                box=visual_box(page,textpage.get_charbox(i))
            except (ValueError,pdfium.PdfiumError):
                continue
            letters.append(char);boxes.append(box)
            if box[2]>box[0] and box[3]>box[1]:
                glyph={'text':char,'bbox':list(box)}
                characters.append(glyph);line_characters.append(glyph)
            sizes.append(raw.FPDFText_GetFontSize(textpage,i))
        finish()
        # Existing stored highlight boxes used MuPDF's unrotated top-left space.
        rotation=page.get_rotation()
        matrix={0:[1,0,0,1,0,0],90:[0,1,-1,0,width,0],180:[-1,0,0,-1,width,height],270:[0,-1,1,0,0,height]}[rotation]
        return {'width':width,'height':height,'spans':spans,'characters':characters,'text':textpage.get_text_range(),'rotation_matrix':matrix}
    finally:
        textpage.close()


def render(page,scale):
    bitmap=page.render(scale=scale)
    try:
        return bitmap.to_pil().copy()
    finally:
        bitmap.close()


def page_png(page):
    image=render(page,min(3.0,3600/max(page.get_size())))
    try:
        buffer=io.BytesIO();image.save(buffer,format='PNG');return buffer.getvalue()
    finally:
        image.close()


@lru_cache(maxsize=1)
def ocr_engine():
    from rapidocr_onnxruntime import RapidOCR
    return RapidOCR(intra_op_num_threads=2,inter_op_num_threads=1)


def recognize(image):
    import numpy as np
    with _ocr_lock:
        result,_=ocr_engine()(np.array(image.convert('RGB')))
    return '\n'.join(row[1] for row in (result or [])).strip()


def recognize_layout(image):
    """识别整页并保留每行的框。

    扫描页没有文本层，引用要能落到区域就只能靠这些行框。
    返回 (文本, [{'text':行文字, 'bbox':[x0,y0,x1,y1]}])，坐标是**图像像素**，
    由调用方按渲染尺寸换算成页面比例（本函数不假设页面大小）。
    """
    import numpy as np
    with _ocr_lock:
        result,_=ocr_engine()(np.array(image.convert('RGB')))
    lines=[]
    for row in (result or []):
        try:
            points,text=row[0],row[1]
        except (TypeError,IndexError,KeyError):
            continue
        if not isinstance(text,str) or not text.strip():continue
        xs=[float(point[0]) for point in points];ys=[float(point[1]) for point in points]
        lines.append({'text':text,'bbox':[min(xs),min(ys),max(xs),max(ys)]})
    return '\n'.join(line['text'] for line in lines).strip(),lines


def region_content(path,page_number,coords):
    with open_pdf(path) as doc:
        page=doc[page_number-1]
        try:
            data=page_data(page)
            x0,y0,x1,y1=coords
            box=[x0*data['width'],y0*data['height'],x1*data['width'],y1*data['height']]
            # Select individual characters by center, so a narrow crop does not include a whole line.
            tp=page.get_textpage()
            try:
                selected=[]
                for i in range(tp.count_chars()):
                    b=visual_box(page,tp.get_charbox(i));cx=(b[0]+b[2])/2;cy=(b[1]+b[3])/2
                    if box[0]<=cx<=box[2] and box[1]<=cy<=box[3]:
                        selected.append(tp.get_text_range(i,1))
                text=''.join(selected).strip()
            finally:
                tp.close()
            if text:
                return text,box
            image=render(page,min(2,2600/max(page.get_size())))
            crop=image.crop((round(x0*image.width),round(y0*image.height),round(x1*image.width),round(y1*image.height)))
            image.close()
        finally:
            page.close()
    try:
        return recognize(crop),box
    finally:
        crop.close()
