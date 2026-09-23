"""以设计者交回的成品图标为母本，重出全套图标资源（页签 PNG + 七档 .ico）。

母本的含义、来历与设计取舍见 `docs/ICON.md`；本文件只负责资源重出，不做形状改动。

母本归档在 `assets/icon-master.png`（1254px，设计者提供），**不带参数就是用它**：

    .venv\\Scripts\\python scripts/adopt_icon.py                    # 用归档母本
    .venv\\Scripts\\python scripts/adopt_icon.py <新母本.png|.ico>   # 换一张母本

与 `build_icon.py` 的分工：

- `build_icon.py`：从**生图底图**加工（抠底、重画圆角、按实测轮廓重画横画与墨点）——
  底图是平铺的位图、边缘脏，所以需要重画。
- 本脚本：母本**已经是干净的图标**（七档、32 位、透明边、边缘锐利），所以不再重画任何形状，
  只做三件必要的事：取最大一档当母本 → 预乘 alpha 缩放（避免透明像素把边缘拖黑）→
  重新打包成我们需要的文件名与尺寸。

设计者的取舍一律照搬：不做上拱、不修改横画与墨点的位置和比例。

    .venv\\Scripts\\python scripts/adopt_icon.py "勘读 .ico"
"""
import io
import struct
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SIZES = (16, 24, 32, 48, 64, 128, 256)
ICO_NAME = '勘读.ico'
# 母本最大只有 256px，而页签用 512：这是**放大**，只为让浏览器不必自己放大。
# 逐像素锐度仍由 256 母本决定——真要更锐，需要设计者交回 ≥512 的 PNG 底图。
BANNER_SIZE = 512


def read_best_entry(path):
    """读母本：PNG 直接用；.ico 取最大的一档（PNG 编码的那一档）。"""
    if path.suffix.lower() == '.png' or path.read_bytes()[:8] == b'\x89PNG\r\n\x1a\n':
        image = Image.open(path).convert('RGBA')
        return image, max(image.size), 1
    data = path.read_bytes()
    reserved, kind, count = struct.unpack_from('<HHH', data, 0)
    if (reserved, kind) != (0, 1):
        raise SystemExit(f'{path.name} 既不是 PNG 也不是 ICO 容器')
    best, best_size = None, 0
    for index in range(count):
        width, height, _, _, _, bits, size, offset = struct.unpack_from(
            '<BBBBHHII', data, 6 + 16 * index)
        pixels = width or 256
        blob = data[offset:offset + size]
        if blob[:8] != b'\x89PNG\r\n\x1a\n':
            continue                                  # 只认 PNG 条目（BMP 条目是旧式写法）
        if pixels > best_size:
            best, best_size = Image.open(io.BytesIO(blob)).convert('RGBA'), pixels
    if best is None:
        raise SystemExit(f'{path.name} 里没有可用的 PNG 图标条目')
    return best, best_size, count


def _premultiplied(image):
    """把 RGBA 拆成"预乘颜色"与 alpha 两张图。"""
    premultiplied = Image.new('RGBA', image.size)
    premultiplied.putdata([(red * a // 255, green * a // 255, blue * a // 255, a)
                           for red, green, blue, a in image.get_flattened_data()])
    return premultiplied


def _blend(premultiplied, size, resample):
    """缩放预乘图并**按预乘合成**摊平到透明底上。

    关键：**不要做"除以 alpha 还原"**。第一版就是还原的——半覆盖像素的 RGB 被除以
    0.75 之类的 alpha，颜色被提亮成 (190,150,…) 这种浅绿，于是每一档图标都围着一圈
    发白的边；小尺寸下整块看着就是"糊"。预乘图直接按 `C = C·α + 0·(1-α)` 摊平后，
    半覆盖像素保持原色、只降低不透明度——这正是抗锯齿边应有的样子。
    """
    small = premultiplied.resize(size, resample)
    return Image.merge('RGBA', small.split()[:3] + (small.getchannel('A'),))


def downscale(image, size):
    """缩小：预乘 → 沿轴对半的盒式平均（纯平均不会振铃）→ 预乘合成。"""
    premultiplied = _premultiplied(image)
    target = (size, size)
    current = premultiplied
    while current.width // 2 >= target[0] and current.width % 2 == 0 and current.width > target[0]:
        current = current.resize((current.width // 2, current.height // 2), Image.BOX)
    if current.size != target:
        current = current.resize(target, Image.BOX)
    return Image.merge('RGBA', current.split()[:3] + (current.getchannel('A'),))


def upscale(image, size):
    """放大一档（母本小于目标尺寸时才用）：同样走预乘，避免边缘发暗。"""
    return _blend(_premultiplied(image), (size, size), Image.LANCZOS)


def tighten_alpha(image, low=18, high=210):
    """收紧边缘的 alpha 过渡：母本的边缘过渡**宽达 5 个像素**（实测 23→54→98→171→244），
    缩小到 16/24/32px 后被平摊成两三个半透明像素，Windows 按混合渲染后就是一圈发白的糊边
    ——小图标"看不清是什么"的直接原因。

    做法：灰度低于 `low` 直接透明、高于 `high` 直接不透明，中间用平滑曲线过渡。
    这样边缘仍然是抗锯齿的（不是硬切），但过渡带只剩一两个像素。
    """
    out = Image.new('RGBA', image.size)
    pixels = []
    for red, green, blue, alpha in image.get_flattened_data():
        if alpha <= low:
            pixels.append((red, green, blue, 0))
        elif alpha >= high:
            pixels.append((red, green, blue, 255))
        else:
            t = (alpha - low) / (high - low)
            pixels.append((red, green, blue, round(255 * t * t * (3 - 2 * t))))
    out.putdata(pixels)
    return out


def deepen_dot(image, target=(13, 30, 24), strength=0.75):
    """把"句读"那一点压暗：母本里它与碑底的亮度只差 1.9:1，16px 下几乎看不见。

    做法是按亮度给每个像素算权重（越暗越接近墨点本体），把它的颜色朝目标墨色推。
    **门槛要贴着墨点的亮度定**：本图碑底亮度 ≈83、墨点 ≈48，所以权重从 60 开始、
    到 46 满格——范围放宽到 95 会把整块碑底一起压暗（实测底色从 (37,97,76) 变成 (33,85,67)）。
    位置与直径仍完全照搬设计者，只动明度。
    """
    out = Image.new('RGBA', image.size)
    pixels = []
    for red, green, blue, alpha in image.get_flattened_data():
        value = 0.2126 * red + 0.7152 * green + 0.0722 * blue
        weight = max(0.0, min(1.0, (60 - value) / 14))
        if weight <= 0:
            pixels.append((red, green, blue, alpha))
            continue
        mix = weight * strength
        pixels.append((round(red + (target[0] - red) * mix),
                       round(green + (target[1] - green) * mix),
                       round(blue + (target[2] - blue) * mix), alpha))
    out.putdata(pixels)
    return out


def png_bytes(image):
    buffer = io.BytesIO()
    image.save(buffer, format='PNG', optimize=True)
    return buffer.getvalue()


def ico_bytes(images):
    payloads = [png_bytes(image) for _, image in images]
    header = struct.pack('<HHH', 0, 1, len(payloads))
    offset = 6 + 16 * len(payloads)
    entries = b''
    for (size, _), payload in zip(images, payloads):
        entries += struct.pack('<BBBBHHII', 0 if size >= 256 else size, 0 if size >= 256 else size,
                               0, 0, 1, 32, len(payload), offset)
        offset += len(payload)
    return header + entries + b''.join(payloads)


def preview(masters):
    cell, pad = 200, 16
    sheet = Image.new('RGBA', (pad + len(SIZES) * (cell + pad), cell + 2 * pad), (255, 255, 255, 255))
    for index, size in enumerate(SIZES):
        icon = masters[size]
        sheet.alpha_composite(icon, (pad + index * (cell + pad) + (cell - size) // 2,
                                     pad + (cell - size) // 2))
    return sheet


def report(master):
    """自检：三档颜色是否分得开（亮度差太小的话，小尺寸就只剩一团）。"""
    def luminance(pixel):
        return 0.2126 * pixel[0] + 0.7152 * pixel[1] + 0.0722 * pixel[2]

    opaque = [pixel for pixel in master.get_flattened_data() if pixel[3] > 250]
    values = sorted({round(luminance(pixel)) for pixel in opaque})
    levels = []
    for value in values:
        if not levels or value - levels[-1][-1] > 12:
            levels.append([value])
        else:
            levels[-1].append(value)
    print('母本 %dx%d：实色像素 %d 个，亮度分层 %s' % (
        master.width, master.height, len(opaque),
        ' / '.join('%d~%d' % (group[0], group[-1]) for group in levels)))
    tiny = downscale(master, 16)
    pixels = [pixel for pixel in tiny.get_flattened_data() if pixel[3] > 250]
    bright = [p for p in pixels if luminance(p) > 150]
    dark = [p for p in pixels if luminance(p) < 70]
    print('16px：实心 %d 个，其中亮（横画）%d、暗（墨点）%d' % (len(pixels), len(bright), len(dark)))
    return len(bright), len(dark)


MASTER_ARCHIVE = ROOT / 'assets' / 'icon-master.png'   # 归档的高分辨率母本（设计者提供）
STALE_MASTER_DROP = ROOT / 'assets' / 'icon-master.ico'  # 早先那版只有 256px 的成品，留档不外用


def write_master(data, suffix):
    """把较新的母本写回 `assets/`；失败不致命（归档目录可能只读）。"""
    try:
        path = ROOT / 'assets' / f'icon-master{suffix}'
        if not path.is_file() or path.read_bytes() != data:
            path.write_bytes(data)
            print(f'母本已归档：{path.relative_to(ROOT)}（{len(data)} B）')
    except OSError as error:
        print(f'母本归档失败（不影响成品）：{error}')


def main(source, deepen=True):
    master, size, count = read_best_entry(source)
    print(f'{source.name}：{count} 档，取最大的 {size}px 一档作母本')
    soft = sum(1 for pixel in master.get_flattened_data() if 8 < pixel[3] < 248)
    master = tighten_alpha(master)
    tight = sum(1 for pixel in master.get_flattened_data() if 8 < pixel[3] < 248)
    print(f'边缘收紧：半透明像素 {soft} → {tight}（母本边缘过渡原本有 5 个像素宽）')
    before = report(master)
    if deepen:
        master = deepen_dot(master)
        print('墨点已加深（只压暗"句读"那一点，色相不变）')
        after = report(master)
        print('16px 暗像素：%d → %d' % (before[1], after[1]))
    if source.suffix.lower() in ('.png', '.ico') and source.resolve() != MASTER_ARCHIVE.resolve():
        write_master(source.read_bytes(), source.suffix.lower())

    masters = {target: (downscale(master, target) if target <= size else upscale(master, target))
               for target in SIZES + (BANNER_SIZE,)}
    written = []
    for target, name in ((BANNER_SIZE, 'icon.png'), (256, 'icon-256.png')):
        path = ROOT / 'static' / name
        masters[target].save(path, format='PNG', optimize=True)
        written.append(path)
    ico = ROOT / ICO_NAME
    ico.write_bytes(ico_bytes([(target, masters[target]) for target in SIZES]))
    written.append(ico)
    sheet = Path(__file__).resolve().parent / 'icon-preview.png'
    preview(masters).save(sheet, format='PNG', optimize=True)
    written.append(sheet)
    for path in written:
        print(f'{path.relative_to(ROOT)}  {path.stat().st_size} B')


if __name__ == '__main__':
    arguments = list(sys.argv[1:])
    flags = {item for item in arguments if item.startswith('--')}
    paths = [item for item in arguments if not item.startswith('--')]
    # 不带参数就用归档母本：这样"重出图标"是一条不需要记文件名的命令
    candidate = Path(paths[0]) if paths else MASTER_ARCHIVE
    if not candidate.is_file():
        raise SystemExit(f'母本不存在：{candidate}\n'
                         '用法：python scripts/adopt_icon.py [<母本.png|.ico>] [--keep-dot]')
    main(candidate, deepen='--keep-dot' not in flags)
