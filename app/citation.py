"""把核验过的摘引定位到页面上的具体区域。

三种资料来源都要能定位：

- **本机文本层**：页面文字来自 PDF 文本层，字形（span）带坐标。单栏时顺序一致；
  双栏时 ``layout_text`` 会重排，于是"阅读文本的行"与"字形顺序"不再一一对应——
  所以这里不按整体字符串比较，而是**按行**匹配：先把摘引在页文本里找到，
  切出它覆盖的每一行片段，再把每个片段匹配到版面上的那一行（字形行或 OCR 行）。
- **扫描页 OCR**：没有文本层，但识别时拿得到每行的框，按行匹配同样成立。
- **用户校订文本**：文字可能已被改写，匹配不上就如实报告"未定位"，不猜位置。

返回的坐标一律是**页面比例**（0..1），前端直接按百分比画框，不必知道 PDF 单位。
"""
import re

MIN_NEEDLE=4


def normalize(text):
    """去掉全部空白，并给出每个保留字符在原串中的位置。"""
    chars=[];positions=[]
    for index,char in enumerate(text or ''):
        if char.isspace():continue
        chars.append(char);positions.append(index)
    return ''.join(chars),positions


def line_parts(page_text,start,end):
    """把原串上的 [start,end) 切成逐行片段（保留行首偏移，便于调试）。"""
    parts=[];offset=0
    for line in (page_text or '').split('\n'):
        line_start=offset;line_end=offset+len(line)
        offset=line_end+1
        if line_end<=start or line_start>=end:continue
        piece=line[max(start,line_start)-line_start:min(end,line_end)-line_start]
        if piece.strip():parts.append((piece,line_start))
    return parts


def match_rank(needle,haystack):
    """2 = 整行相同；1 = 该行包含片段；0 = 不匹配。"""
    if not needle:return 0
    if needle==haystack:return 2
    if needle in haystack:return 1
    return 0


def locate(page_text,quote,lines,min_needle=MIN_NEEDLE):
    """在页文本中找出摘引，并映射到 ``lines``（每项含 text 与 bbox）里的行框。

    摘引必须能在该页原文里逐字找到（忽略排版空白）——这是既有的成员校验口径，
    定位不会为"找不到的句子"编造位置。
    """
    normalized,positions=normalize(page_text)
    needle,_=normalize(quote)
    if len(needle)<min_needle:return []
    index=normalized.find(needle)
    if index<0:return []
    start=positions[index];end=positions[index+len(needle)-1]+1
    prepared=[]
    for row in lines or []:
        text=normalize(row.get('text',''))[0]
        box=row.get('bbox')
        if text and box and len(box)==4:prepared.append((text,row))
    if not prepared:return []
    boxes=[];cursor=0
    for piece,_offset in line_parts(page_text,start,end):
        target,_=normalize(piece)
        # 太短的片段（"of"、"第 3"之类）匹配到哪儿都说得通，框出来只会误导。
        if len(target)<min_needle:continue
        best=None;best_rank=0;best_index=cursor
        # 先从上次命中的位置往后找（同一栏内通常是顺序的），找不到再回到开头，
        # 这样双栏重排的页面也能匹配，同时避免重复行选错实例。
        for position in list(range(cursor,len(prepared)))+list(range(0,cursor)):
            rank=match_rank(target,prepared[position][0])
            if rank>best_rank:
                best=prepared[position][1];best_rank=rank;best_index=position
                if rank==2:break
        if best is None:continue
        boxes.append({'bbox':[float(value) for value in best['bbox']],'text':piece[:200]})
        cursor=best_index
    return boxes


def locate_page_text(page_text,quote,width,height,spans,ocr_lines=None):
    """先按文本层字形定位，不行再用 OCR 行框；两者都没有就返回空。"""
    glyphs=[]
    for span in spans or []:
        box=span.get('bbox')
        if not box or len(box)!=4 or not width or not height:continue
        glyphs.append({'text':span.get('text',''),'bbox':[box[0]/width,box[1]/height,box[2]/width,box[3]/height]})
    boxes=locate(page_text,quote,glyphs)
    if boxes:return boxes,'text_layer'
    if ocr_lines:
        boxes=locate(page_text,quote,ocr_lines)
        if boxes:return boxes,'ocr'
    return [],'none'


def search_order(page,total,max_pages=60):
    """从最近到最远的页序（不含自身），用于"这段文字其实不在记录的页上"时找回真正位置。

    只遍历不触发新识别的页（调用方用文本层或已有缓存），因此扫描件也不会因此重跑 OCR。
    """
    order=sorted((candidate for candidate in range(1,total+1) if candidate!=page),
                 key=lambda candidate:(abs(candidate-page),candidate))
    return order[:max(1,min(max_pages,total))]


def squashed(text):
    return re.sub(r'\s+','',text or '')


CLAUSE_SPLIT=re.compile(r'[，。；：、,.;:!?！？()（）\[\]【】「」“”"\'’‘\s]+')


def line_anchor(line,text,minimum=8,limit=200):
    """在页文本里找出正文这一句"用到的原文文字"，作为定位锚点。

    这不是"原文支持这句话"的声明，只是**逐字重合**的定位线索：
    复核没能给出逐字摘引时，用它让引用仍然能落到具体文字而不是只给页码。
    找不到重合（正文是改写表述）就返回空——宁可只给页码，也不编位置。
    """
    if not text:return ''
    normalized,positions=normalize(text)
    for clause in sorted(CLAUSE_SPLIT.split(line or ''),key=len,reverse=True):
        needle,_=normalize(clause)
        if len(needle)<minimum:continue
        # 从句首开始逐步缩短，直到在原文里找到一段逐字重合的文字。
        for size in range(len(needle),minimum-1,-1):
            piece=needle[:size]
            at=normalized.find(piece)
            if at>=0:
                start=positions[at];end=positions[at+size-1]+1
                return text[start:end][:limit]
    return ''


def answer_anchors(answer,sources,minimum=8,limit=200):
    """为"没有逐字摘引"的引用补定位锚点，按来源编号返回 {n: 原文片段}。"""
    found={}
    for line in (answer or '').splitlines():
        if not line.strip():continue
        for match in re.findall(r'\[(\d+)\]',line):
            number=int(match)
            if not 1<=number<=len(sources) or number in found:continue
            source=sources[number-1]
            if source.get('quotes'):continue
            anchor=line_anchor(line,source.get('text',''),minimum,limit)
            if anchor:found[number]=anchor
    return found
