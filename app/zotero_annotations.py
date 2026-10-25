"""Zotero 批注/高亮 → 本应用批注的映射规则（纯函数，不做 I/O、不联网）。

为什么单独一个文件：迁移逻辑里最容易出错的是"字段怎么解释"——颜色、类型、页码、
以及哪个条目根本不该导。把这些判断写成纯函数，就能用真实 Zotero 数据格式做单元测试，
而不必启动服务；读取 Zotero 与写库仍留在适配层（`app/plugins/zotero_feature.py`）。

设计口径：
- **只迁移用户自己写的标注**（高亮、下划线、便签/文字批注），不迁移墨迹与截图区域：
  它们没有可检索的文字，导进来只会变成一堆无法核对的空框。跳过的必须在报告里说清数量与原因。
- **位置以本应用自己的定位结果为准**：Zotero 的 `annotationPosition` 只用来确定页码；
  具体框选位置用本应用已有的"逐字定位"（`app/citation.py`）在页面上重新找。
  这样不依赖 Zotero 内部坐标系的旋转/缩放约定，也不会把框画到错误的行上。
  定位不到时如实降级为"页级批注"，不猜位置。
- **颜色只做名称映射**：Zotero 存十六进制色值，本应用用固定的颜色名。未知色值回落到
  yellow 并在报告里标注，不新增颜色名（否则前端样式表会出现没有样式的空标记）。
"""

# Zotero 默认的 8 种高亮颜色 → 本应用已有颜色名。映射是**事实性**的（颜色名与色值对应），
# 不是照搬任何第三方代码；本应用自己的调色板见 static/style.css 的 .annotation-mark.*。
COLOR_BY_HEX = {
    '#ffd400': 'yellow',
    '#ff6666': 'pink',      # Zotero 的 red
    '#5fb236': 'green',
    '#2ea8e5': 'blue',
    '#a28ae5': 'purple',
    '#e56eee': 'pink',      # Zotero 的 magenta，本应用没有单独颜色
    '#f19837': 'orange',
    '#aaaaaa': 'yellow',    # Zotero 的 gray，本应用没有灰色批注
}

# 可迁移的标注类型 → 本应用的批注样式。None 表示不可迁移（在报告里给出原因）。
STYLE_BY_TYPE = {
    'highlight': 'highlight',
    'underline': 'underline',
    'note': 'margin',
    'text': 'margin',
}

TYPE_LABELS = {
    'highlight': '高亮',
    'underline': '下划线',
    'note': '便签',
    'text': '文字批注',
    'image': '区域截图',
    'ink': '手写墨迹',
}

MAX_IMPORT = 500

import json


def palette_color(value):
    """十六进制色值 → 本应用颜色名 + 是否未知。"""
    raw = str(value or '').strip().lower()
    if not raw:
        return 'yellow', False
    if not raw.startswith('#'):
        raw = '#' + raw
    known = COLOR_BY_HEX.get(raw)
    if known:
        return known, False
    return 'yellow', True


def attachment_key(identity):
    """从文献的 `external_key`（Zotero 身份串）里取出附件条目标识。

    身份串形如 `local:users:0:<server-id>:<itemkey>[:<本地路径>]`，云端模式没有路径段。
    因此从后往前找第一个 8 位大写字母数字的段；找不到就返回空串（由调用方给出可执行提示）。
    """
    for part in reversed(str(identity or '').split(':')):
        if len(part) == 8 and part.isupper() and part.isalnum():
            return part
    return ''


def position_page(data, total_pages):
    """从 `annotationPosition` 里读页码（1 基）+ 失败原因。

    Zotero 的字段是 JSON 字符串，`pageIndex` 从 0 开始。缺失/损坏/越界都返回 None，
    由调用方计入"无法定位"而不是丢进一个随机页。
    """
    raw = data.get('annotationPosition')
    if not raw:
        return None, '这条批注没有记录位置（Zotero 未同步页面坐标）'
    try:
        position = json.loads(raw) if isinstance(raw, str) else raw
        index = int(position.get('pageIndex'))
    except (TypeError, ValueError, AttributeError):
        return None, '这条批注的位置数据无法解析'
    page = index + 1
    if page < 1:
        return None, '这条批注的页码无效'
    if total_pages and page > total_pages:
        return None, f'这条批注记在第 {page} 页，但当前文献只有 {total_pages} 页（附件可能已更换）'
    return page, ''


def mapping_of(data, total_pages):
    """把一条 Zotero annotation 的 data 映射成"待导入"，或给出跳过原因。

    返回 `{'ok': bool, 'reason': str, ...}`；不碰数据库、不碰网络。
    """
    data = data or {}
    kind = str(data.get('annotationType') or '')
    quote = str(data.get('annotationText') or '').strip()
    comment = str(data.get('annotationComment') or '').strip()
    zotero_key = str(data.get('key') or '') or str(data.get('itemKey') or '')

    style = STYLE_BY_TYPE.get(kind)
    if style is None:
        label = TYPE_LABELS.get(kind, kind or '未知类型')
        return {'ok': False, 'category': 'type',
                'reason': f'{label}没有可迁移的文字：{label}类标注（手写/截图）本应用无法复用',
                'zotero_key': zotero_key, 'type': kind, 'quote': quote, 'comment': comment}

    if not quote and not comment:
        return {'ok': False, 'category': 'empty', 'reason': '这条标注既没有原文也没有批注内容',
                'zotero_key': zotero_key, 'type': kind, 'quote': quote, 'comment': comment}

    page, reason = position_page(data, total_pages)
    if page is None:
        return {'ok': False, 'category': 'page', 'reason': reason, 'zotero_key': zotero_key,
                'type': kind, 'quote': quote, 'comment': comment}

    color, unknown_color = palette_color(data.get('annotationColor'))
    return {
        'ok': True, 'reason': '',
        'zotero_key': zotero_key, 'type': kind, 'page': page,
        'quote': quote[:12000], 'comment': comment[:20000],
        'color': color, 'unknown_color': unknown_color,
        'style': style,
        'sort_index': str(data.get('annotationSortIndex') or ''),
    }
