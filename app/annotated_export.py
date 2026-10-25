"""导出带批注的 PDF 副本。

为什么单独成一个文件：这件事只读原 PDF、只在内存里拼一份新字节，
既不碰数据库里的批注行，也不碰磁盘上的原文件。把它从路由里拆出来，
才能在没有 FastAPI 的情况下单独测坐标换算与"写不进去"的降级统计。

与前端保持同一套约定（见 static/experience.js 的 annotationKind / drawAnnotations）：
- `rects` 是**归一化 0..1、页面左上角为原点**的矩形列表；
- `style`：highlight（高亮）/ underline（下划线）/ margin（段落侧线）/
  region（区域框选）/ page（整页批注）；
- `color`：yellow / green / blue / pink / purple / orange，十六进制见 ``COLORS``
  （取自 static/style.css 里同一套批注色板，保证副本与本应用里看到的一致）。

三条硬规则：

1. **绝不写回原文件**：只用 ``PdfReader`` 读、``PdfWriter`` 写 ``BytesIO``，
   函数返回字节串；调用方拿不到可以覆盖原文件的句柄。
2. **旋转页不猜坐标**：本应用记录的归一化坐标来自"视觉方向"（pdfium 已按
   ``/Rotate`` 旋转过的页面尺寸），而 PDF 用户空间是**未旋转**的。两者之间的
   变换取决于 /Rotate、MediaBox 和 CropBox 的组合，数据库里没有记录，
   硬猜就可能把批注画到错误的位置——所以这些页上的批注一律不写入，
   如实计入 ``unplaced`` 并说明原因。
3. **一条出问题不毁整次导出**：页码越界、位置数据坏掉、没有文字内容都只降级
   这一条，其余照常写入；报告里的 ``placed + unplaced`` 永远等于批注总条数，
   不假装全部成功。

另外：批注内容属于用户自己的文档，函数内部**不写日志**，也不把原文/批注文字
放进报告或响应头（报告只有数量、页码与原因）。
"""

from __future__ import annotations

import io
import json
import math
from urllib.parse import quote

from pypdf import PdfReader, PdfWriter
from pypdf.annotations import Highlight, MarkupAnnotation, Text
from pypdf.constants import AnnotationFlag
from pypdf.generic import ArrayObject, FloatObject, NameObject, RectangleObject

# 批注色板：与 static/style.css 的 --mark 保持一致，导出副本里看到的颜色与应用内相同。
COLORS = {
    'yellow': 'ebc743',
    'green': '74ba6a',
    'blue': '66aadb',
    'pink': 'e58cac',
    'purple': 'b083d2',
    'orange': 'efaa57',
}
DEFAULT_COLOR = COLORS['yellow']
KNOWN_STYLES = ('highlight', 'underline', 'margin', 'region', 'page')

# 便签（整页批注 / 段落侧线）落在页面右上角：20pt 的图标区，距页边 8pt。
NOTE_SIZE = 20.0
NOTE_GAP = 8.0

# 写入副本的批注统一带 /T 作者，便于用户在阅读器里看出它来自本应用。
TITLE = '勘读 · KanRead'

# 未写入原因：键是 ASCII（要放进响应头），值是给用户看的说明。
# 报告只带键与计数，具体原因文字由前端/文档解释，避免把原文带进头部。
REASON_TEXT = {
    'rotated_page': '页面有旋转，归一化坐标与 PDF 用户空间对不上，未写入副本，请核对',
    'page_out_of_range': '页码超出这份 PDF 的页数',
    'invalid_page': '页码不是有效数字',
    'invalid_rect': '批注位置无效（不是 0..1 内的矩形）',
    'empty_annotation': '批注没有文字内容，便签没有正文可写',
    'write_failed': '写入这一条时出错，已跳过',
}

# Windows 文件名非法字符。文献名来自用户或文件名，直接进 Content-Disposition
# 既可能被浏览器拒绝，也可能带出路径成分，所以先替换再拼扩展名。
_ILLEGAL_NAME_CHARS = '<>:"/\\|?*'
_MAX_NAME_CHARS = 80
_DETAIL_LIMIT = 50


class ExportError(Exception):
    """整份文件级别的问题（打不开 / 写不出）；单条批注的问题不抛这个。"""


class _Skip(Exception):
    """内部信号：这一条批注无法写入，带上原因键与说明。"""

    def __init__(self, reason, detail=''):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


class Underline(MarkupAnnotation):
    """下划线批注。

    为什么自己组：pypdf 6.18.1 的 ``pypdf.annotations`` 只带了 Highlight，
    没有 Underline（实测 ``hasattr(pypdf.annotations, 'Underline')`` 为假）。
    按 PDF 规范（标注字典 /Subtype /Underline、几何用 /QuadPoints、颜色 /C）
    自己拼一个，全部字段都是公开的 PDF 结构，不依赖 pypdf 的非公开实现。
    """

    def __init__(self, *, rect, quad_points, color=(1.0, 0.0, 0.0), **kwargs):
        super().__init__(**kwargs)
        self.update({
            NameObject('/Subtype'): NameObject('/Underline'),
            NameObject('/Rect'): RectangleObject(rect),
            NameObject('/QuadPoints'): quad_points,
            NameObject('/C'): ArrayObject([FloatObject(value) for value in color]),
        })


def effective_style(annotation):
    """把数据库里的 style 归一到五种已知类型。

    老数据的 style 可能是空字符串或未知值：有位置的按高亮画（最接近的默认样式），
    没位置的按整页便签处理——与前端 annotationKind() 的判断保持一致，
    避免"界面上是标记、导出时却悄悄丢掉"。
    """
    style = str(annotation.get('style') or '').strip().lower()
    if style in KNOWN_STYLES:
        return style
    raw = annotation.get('rects')
    return 'highlight' if raw not in (None, '', [], '[]') else 'page'


def page_number_of(annotation):
    """批注页码（1 基）的宽松解析；无法可靠解析时返回 None。"""
    value = annotation.get('page')
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    if isinstance(value, str):
        text = value.strip()
        try:
            return int(text)
        except ValueError:
            return None
    return None


def rects_of(annotation):
    """解析 rects。返回列表表示可用（可能为空列表），返回 None 表示数据坏掉。

    数据来源有两个：正常路径下接口已经 json.loads 成列表；但这个函数也接受原始
    字符串，因为坏行宁可降级成"这一条没写入"，也不该让整次导出 500。
    """
    raw = annotation.get('rects')
    if raw is None or raw == '':
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return None
    if not isinstance(raw, (list, tuple)):
        return None
    result = []
    for item in raw:
        if not isinstance(item, (list, tuple)) or len(item) != 4:
            return None
        try:
            values = [float(value) for value in item]
        except (TypeError, ValueError):
            return None
        if not all(math.isfinite(value) for value in values):
            return None
        x0, y0, x1, y1 = values
        if not (0.0 <= x0 < x1 <= 1.0 and 0.0 <= y0 < y1 <= 1.0):
            return None
        result.append((x0, y0, x1, y1))
    return result


def note_text_of(annotation):
    """便签正文：原文在前、批注文字在后；两者都没有时返回空串。"""
    quote_text = str(annotation.get('quote') or '').strip()
    content = str(annotation.get('content') or '').strip()
    if quote_text and content:
        return f'原文：{quote_text}\n\n批注：{content}'
    if quote_text:
        return f'原文：{quote_text}'
    return content


def color_hex_of(annotation):
    """批注颜色名 → 十六进制；未知颜色用默认黄色，不悄悄丢颜色字段。"""
    name = str(annotation.get('color') or '').strip().lower()
    return COLORS.get(name, DEFAULT_COLOR)


def safe_filename(document_name):
    """生成下载文件名：``{文献名}-批注副本.pdf``。

    去掉了 Windows 不接受的字符与控制字符（否则浏览器/资源管理器保存失败），
    压缩空白、去掉结尾的空格与点，并截断长度。文献名本身带 ``.pdf`` 扩展名时
    先去掉它，避免出现 ``paper.pdf-批注副本.pdf`` 这种双扩展名。
    替换后不可能再含有路径分隔符，所以这里是防路径穿越的第一道（也是够用的一道）。
    """
    text = str(document_name or '').strip()
    cleaned = ''.join(
        '_' if (ord(char) < 32 or ord(char) == 127 or char in _ILLEGAL_NAME_CHARS) else char
        for char in text
    )
    cleaned = ' '.join(cleaned.split()).strip(' .')
    if cleaned.lower().endswith('.pdf'):
        cleaned = cleaned[:-4].strip(' .')
    if not cleaned:
        cleaned = '文献'
    if len(cleaned) > _MAX_NAME_CHARS:
        cleaned = cleaned[:_MAX_NAME_CHARS].strip(' .') or '文献'
    return cleaned + '-批注副本.pdf'


def content_disposition(filename):
    """RFC 5987 的 Content-Disposition：中文文件名按 UTF-8 百分号编码。

    同时给一个 ASCII 的 ``filename=`` 兜底：极老的客户端不支持 ``filename*``
    时也能落到一个固定的英文名，而不是报错或乱码。
    """
    return f"attachment; filename=\"annotated-copy.pdf\"; filename*=UTF-8''{quote(filename, safe='')}"


def _hex_to_rgb(value):
    text = str(value or '').strip().lstrip('#')
    if len(text) != 6:
        text = DEFAULT_COLOR
    try:
        return tuple(int(text[index:index + 2], 16) / 255 for index in (0, 2, 4))
    except ValueError:
        return tuple(int(DEFAULT_COLOR[index:index + 2], 16) / 255 for index in (0, 2, 4))


def _rotation(page):
    """页面 /Rotate 角度；读不出来时返回 None（按"旋转未知"处理，不硬画）。"""
    try:
        value = page.rotation
    except Exception:  # noqa: BLE001 - 坏 /Rotate 不应该让整次导出失败
        return None
    try:
        return int(value or 0) % 360
    except (TypeError, ValueError):
        return None


def _visible_box(page):
    """页面可见区域的 (left, bottom, width, height)。

    用 CropBox 而不是 MediaBox：本应用渲染页面用的 pdfium ``get_size()`` 返回的是
    CropBox 尺寸（实测 MediaBox 595x842 + CropBox 10,20,400,600 时 get_size 为
    390x580），归一化坐标就是按这个可见区域算的。绝大多数 PDF 没有独立 CropBox，
    此时 pypdf 会让它等于 MediaBox——两种情况下都按可见区域换算才是对的。
    尺寸非法时返回 None，由调用方如实记为写不进去。
    """
    try:
        box = page.cropbox
        left = float(box.left)
        bottom = float(box.bottom)
        width = float(box.width)
        height = float(box.height)
    except Exception:  # noqa: BLE001 - 坏页面框由上层降级处理
        return None
    if not all(math.isfinite(value) for value in (left, bottom, width, height)):
        return None
    if width <= 0 or height <= 0:
        return None
    return (left, bottom, width, height)


def _pdf_rect(box, rect):
    """归一化矩形（左上原点）→ PDF 用户空间矩形 (left, bottom, right, top)。

    两处容易写错的地方都在这里处理：
    - y 轴方向相反：归一化的 y 从页面**顶边**往下量，PDF 从**底边**往上量；
    - CropBox 可能不带 (0,0) 原点：必须加上 left/bottom 偏移。
    """
    x0, y0, x1, y1 = rect
    left, bottom, width, height = box
    return (
        left + x0 * width,
        bottom + (1.0 - y1) * height,
        left + x1 * width,
        bottom + (1.0 - y0) * height,
    )


def _quad_points(pdf_rect):
    """PDF 规范里 /QuadPoints 的顺序：上左、上右、下左、下右。"""
    left, bottom, right, top = pdf_rect
    return (left, top, right, top, left, bottom, right, bottom)


def _note_rect(box):
    """便签图标区：页面可见区域右上角，避开页边。"""
    left, bottom, width, height = box
    return (
        left + width - NOTE_GAP - NOTE_SIZE,
        bottom + height - NOTE_GAP - NOTE_SIZE,
        left + width - NOTE_GAP,
        bottom + height - NOTE_GAP,
    )


def _markup(kind, box, rects, color_hex):
    """把一组归一化矩形合成一个标记批注（高亮 / 下划线）。"""
    pdf_rects = [_pdf_rect(box, rect) for rect in rects]
    quad_points = ArrayObject([
        FloatObject(value)
        for pdf_rect in pdf_rects
        for value in _quad_points(pdf_rect)
    ])
    bounds = (
        min(rect[0] for rect in pdf_rects),
        min(rect[1] for rect in pdf_rects),
        max(rect[2] for rect in pdf_rects),
        max(rect[3] for rect in pdf_rects),
    )
    if kind == 'underline':
        annotation = Underline(rect=bounds, quad_points=quad_points,
                               color=_hex_to_rgb(color_hex), title_bar=TITLE)
    else:
        annotation = Highlight(rect=bounds, quad_points=quad_points,
                               highlight_color=color_hex, title_bar=TITLE)
    # 打印标志：用户把副本打印/另存为 PDF 时批注也会保留（默认的 /F=0 在部分阅读器
    # 里只显示不打印）。flags 属性写的是规范的 /F 键，不是 Text() 的 /Flags 参数。
    annotation.flags = AnnotationFlag.PRINT
    return annotation


def _write_one(writer, pages, annotation):
    """把一条批注写进 writer 的对应页；成功返回归一化后的 style，失败抛 _Skip。"""
    page_number = page_number_of(annotation)
    if page_number is None:
        raise _Skip('invalid_page', '页码字段不是整数')
    if not 1 <= page_number <= len(pages):
        raise _Skip('page_out_of_range', f'第 {page_number} 页不在 1..{len(pages)} 之间')

    page = pages[page_number - 1]
    rotation = _rotation(page)
    if rotation is None:
        raise _Skip('rotated_page', f'第 {page_number} 页的旋转信息无法读取，无法确认坐标方向')
    if rotation:
        raise _Skip('rotated_page', f'第 {page_number} 页旋转了 {rotation}°，归一化坐标无法可靠映射')

    box = _visible_box(page)
    if box is None:
        raise _Skip('write_failed', f'第 {page_number} 页的页面尺寸不合法')

    style = effective_style(annotation)
    rects = rects_of(annotation)
    if rects is None:
        raise _Skip('invalid_rect', '位置数据不是 0..1 内的有效矩形')
    text = note_text_of(annotation)

    # 整页批注、段落侧线或"有文字但没位置"的批注：写成便签（Text）。
    # 便签是阅读器普遍支持的注释类型，且不需要嵌入字体，中文不会变方块。
    if style in ('margin', 'page') or not rects:
        if not text:
            raise _Skip('empty_annotation', '没有原文也没有批注文字')
        note = Text(rect=_note_rect(box), text=text, open=False, title_bar=TITLE)
        note.flags = AnnotationFlag.PRINT
        writer.add_annotation(page_number - 1, note)
        return style

    # region（区域框选）没有"区域"这类标准标注，用半透明高亮表达同一块区域；
    # highlight / underline 用各自的标记类型。
    annotation_object = _markup('underline' if style == 'underline' else 'highlight',
                                box, rects, color_hex_of(annotation))
    writer.add_annotation(page_number - 1, annotation_object)
    return style


def _count_unplaced(report, reason, detail, annotation):
    report['unplaced'] += 1
    report['unplaced_reasons'][reason] = report['unplaced_reasons'].get(reason, 0) + 1
    if len(report['unplaced_details']) < _DETAIL_LIMIT:
        report['unplaced_details'].append({
            'id': str(annotation.get('id') or ''),
            'page': page_number_of(annotation),
            'reason': reason,
            'detail': detail,
        })


def _message(report):
    """一句给用户看的中文结论；不含任何原文或批注文字。"""
    placed, unplaced, total = report['placed'], report['unplaced'], report['total']
    if not total:
        return '这份文献还没有批注；已生成一份未改动的副本，原文件未改动。'
    if not unplaced:
        return f'已把 {placed} 条批注写入副本；原文件未改动。'
    reasons = '、'.join(
        f'{REASON_TEXT.get(key, key)} {count} 条'
        for key, count in report['unplaced_reasons'].items()
    )
    if placed:
        return f'已写入 {placed} 条批注；{unplaced} 条未写入（{reasons}）；原文件未改动。'
    return f'没有批注能写入副本：{unplaced} 条未写入（{reasons}）；原文件未改动。'


def build(meta, annotations, pdf_path):
    """生成带批注的 PDF 副本。

    参数：
        meta：documents 表里的那一行（这里只用来核对文档身份，不参与坐标计算）；
        annotations：批注行，``rects`` 允许是列表、JSON 字符串或 None；
        pdf_path：原 PDF 路径，**只读**。

    返回 ``(bytes, report)``：

    - ``placed`` / ``unplaced``：写入与未写入的条数，两者之和等于批注总条数；
    - ``by_style``：写入的批注按 style 计数；
    - ``unplaced_reasons``：未写入原因 → 条数（键是 ASCII，可直接放进响应头）；
    - ``unplaced_details``：前 50 条明细（id / 页 / 原因 / 说明），不含正文；
    - ``pages_with_rotation``：这份 PDF 里带 /Rotate 的页码（1 基），提醒用户核对。

    文件级问题（打不开、写不出）抛 :class:`ExportError`；单条批注的问题只降级。
    """
    annotations = list(annotations or [])
    report = {
        'document_id': str(meta.get('id') or '') if isinstance(meta, dict) else '',
        'total': len(annotations),
        'placed': 0,
        'unplaced': 0,
        'by_style': {},
        'unplaced_reasons': {},
        'unplaced_details': [],
        'pages_with_rotation': [],
        'message': '',
    }

    try:
        reader = PdfReader(str(pdf_path))
        # clone_from 会整份克隆（页面、目录、表单、已有标注），再往页面上加我们的批注；
        # 输出写进内存，从不打开原文件用于写入。
        writer = PdfWriter(clone_from=reader)
    except Exception as exc:  # noqa: BLE001 - 包成对用户可读的错误，保留原始异常链
        raise ExportError('无法读取这份 PDF：文件可能损坏或已加密；没有生成副本，原文件未改动。') from exc

    pages = list(writer.pages)
    report['pages_with_rotation'] = [
        index + 1 for index, page in enumerate(pages) if _rotation(page)
    ]

    for annotation in annotations:
        try:
            style = _write_one(writer, pages, annotation)
        except _Skip as skip:
            _count_unplaced(report, skip.reason, skip.detail, annotation)
            continue
        except Exception as exc:  # noqa: BLE001 - 单条出问题不毁整次导出
            # 只暴露异常类型，不暴露批注正文；其余批注照常写入，这一条如实计为未写入。
            _count_unplaced(report, 'write_failed', f'写入时出错（{type(exc).__name__}）', annotation)
            continue
        report['placed'] += 1
        report['by_style'][style] = report['by_style'].get(style, 0) + 1

    buffer = io.BytesIO()
    try:
        writer.write(buffer)
    except Exception as exc:  # noqa: BLE001
        raise ExportError('生成 PDF 副本时出错；没有生成文件，原文件未改动。') from exc

    report['message'] = _message(report)
    return buffer.getvalue(), report
