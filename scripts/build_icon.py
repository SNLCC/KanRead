"""把一张生成的图标底图加工成成品图标：抠底、成方、逐档缩放、出 .ico。

这个脚本是**一次性/可重复**的构建工具：给它一张方形底图（生图 AI 出的、或手绘的），
它输出 `static/icon*.png`、仓库根的 `勘读.ico` 与一张多尺寸预览。

> **术语**：用户可见的说明（`docs/ICON.md`、`assets/README.md`）把这个圆角方块叫
> **圆角底**；下面的测量代码沿用了简称"碑"，两者指同一个东西。
> 改那三十多处测量注释的收益只在措辞，所以没有翻新。

它做四件事，每一件都是为了解决"生成的图直接当图标用"会遇到的真实问题：

1. **抠底**：生成图通常带一层纯色背景（本项目的图是近白 #fdfdfd）。直接把白底当图标，
   在深色任务栏上就是一个白方块。这里按颜色切出"圆角底 + 横画 + 墨点"，其余全部透明。
2. **重画圆角**：生成图的圆角边缘带压缩噪点与锯齿，缩到 16px 会变成毛边。这里**测量**
   原图的圆角半径，再用高质量圆角矩形重新渲染一次边缘（4× 超采样）。
3. **笔画取下采样后的平滑轮廓**：笔触是手绘感的有机形状，硬描边会失真，所以从原图取
   0/1 掩膜、降采样平滑、再放大回主稿——保留造型、去掉锯齿。
4. **墨点重画成干净的正圆，并加深**：原图的墨点与底色亮度只差 11%，16px 下会彻底消失
   （`tests/test_icon.py` 里有对应的对比度断言）。直径与位置仍按原图测量值。

    .venv\\Scripts\\python scripts/build_icon.py <底图.png>
    .venv\\Scripts\\python scripts/build_icon.py --check      # 只做对比度与几何自检
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
SIZES = (16, 24, 32, 48, 64, 128, 256)
ICO_NAME = '勘读.ico'
MASTER = 1024
SS = 4                                   # 圆角与正圆的超采样倍数

# 成品颜色（由底图测量后**吸附到项目色板**，见 docs/ICON.md）
TILE = (40, 101, 78)                     # 应用主色 #28654e 的深色版（界面 --accent 同族）
CREAM = (233, 238, 229)                  # 界面纸色 #e9eee5
INK = (13, 30, 24)                       # 墨点：比底图深，见文件头第 4 条
BACKGROUND = (255, 255, 255, 255)

# 由底图测得并写死的几何比例（相对碑的边长）；换底图时这两组数字要重新测。
CORNER_RATIO = 0.125                     # 圆角半径 / 碑边长（弧的下界，实测值）
DOT_DIAMETER_RATIO = 0.104               # 墨点直径 / 碑边长（兜底用）
DOT_CENTER = (0.553, 0.626)              # 墨点中心（相对碑左上角，兜底用）
STROKE_STEPS = 160                       # 笔触中心线的采样数


def measure(source):
    """从底图测出：碑的外接框与圆角半径、笔触掩膜、墨点的中心与直径。"""
    image = Image.open(source).convert('RGB')
    width, height = image.size
    pixels = image.load()

    def near(colour, target, tolerance):
        return sum((a - b) ** 2 for a, b in zip(colour, target)) <= tolerance * tolerance

    tile_colour = pixels[width // 2, height // 8]          # 碑底：取上方正中
    xs, ys = [], []
    for y in range(height):
        for x in range(width):
            if near(pixels[x, y], tile_colour, 26):
                xs.append(x)
                ys.append(y)
    box = (min(xs), min(ys), max(xs), max(ys))
    side = box[2] - box[0]

    # 圆角半径：沿左墙测"行内缩进"衰减到 1px 的位置。原图圆角边缘有压缩噪点与轻微软化，
    # 逐像素判色只能测到弧的下界；这里取弧的**内接**估计并把弧面往上补一段，
    # 否则复现出来的角会比原图明显更方（第一版半径测成 13.4%，画出来像个方砖）。
    radius = 1
    for offset in range(1, side // 2):
        y = box[1] + offset
        row = [x for x in range(box[0], box[2] + 1) if near(pixels[x, y], tile_colour, 26)]
        if row and min(row) - box[0] <= 1:
            radius = offset
            break
    radius = int(max(radius, side * CORNER_RATIO) * 1.16)

    # 笔触与墨点：按亮度分两类。分界用"碑底亮度 ±"，不用固定阈值。
    #
    # 取墨点**不能**只在整块碑上做阈值：生图在碑的角落留了几处暗斑与暗边（实测有上万像素），
    # 它们会把墨点的质心拽到碑中心去（画出来的点整个跑到笔画上方）。所以先定位"碑内部最暗的
    # 一小块"当种子，再用**洪泛**取出与它相连的那一块——只认这一块，别的暗斑一概不认。
    tile_luminance = 0.2126 * tile_colour[0] + 0.7152 * tile_colour[1] + 0.0722 * tile_colour[2]

    def brightness(x, y):
        colour = pixels[x, y]
        return 0.2126 * colour[0] + 0.7152 * colour[1] + 0.0722 * colour[2]

    inset = int(side * 0.06)                             # 避开碑边那一圈软化像素
    seed, seed_value = None, None
    for y in range(box[1] + inset, box[3] - inset):
        for x in range(box[0] + inset, box[2] - inset):
            value = brightness(x, y)
            if seed_value is None or value < seed_value:
                seed, seed_value = (x, y), value

    stroke = Image.new('L', (width, height), 0)
    dot = Image.new('L', (width, height), 0)
    pen_stroke, pen_dot = stroke.load(), dot.load()

    # **先把纵向范围收在主体附近**：碑的边缘有一圈抗锯齿像素，亮度也在"纸色"一侧，
    # 不加限制的话它们会被当成笔画，掩膜的外接框直接变成整块碑（实测 0.026~0.974，
    # 而笔画本身只占 0.407~0.525）。做法是找出"亮点最多的那一行"当基准，
    # 只保留它上下各 15% 碑高以内的行——笔画与墨点都落在这个带里。
    band = (box[1], box[3])
    rows = {}
    for y in range(box[1], box[3] + 1):
        rows[y] = sum(1 for x in range(box[0], box[2] + 1)
                      if brightness(x, y) > tile_luminance + 60)
    if rows:
        busiest = max(rows, key=lambda y: rows[y])
        reach = int(side * 0.15)
        band = (max(busiest - reach, box[1]), min(busiest + reach, box[3]))

    for y in range(band[0], band[1] + 1):
        for x in range(box[0], box[2] + 1):
            if brightness(x, y) > tile_luminance + 60:
                pen_stroke[x, y] = 255                   # 纸色笔触
    # 掩膜的裁剪框就是**纵向带 × 碑的横向范围**：用整块碑的框去裁，
    # 中间的空白会让笔画在缩放后只占一小条（实测笔画被压到原高的 60%）。
    if seed is not None:
        # 容差 20 而不是 42：碑的边缘有一圈抗锯齿像素（实测亮度 89~127），
        # 容差放到 42 时洪泛会顺着这圈边缘**漏出墨点、绕碑一圈**，整块碑都被当成墨点
        # （实测把直径画成了 1070/1024，等于没有墨点）。墨点自身只有 42~48，
        # 碑底 83 且极均匀，两者之间取 65 附近做界最稳。
        limit = seed_value + 20
        stack, seen = [seed], set()
        while stack:
            x, y = stack.pop()
            if (x, y) in seen or not (box[0] <= x <= box[2] and box[1] <= y <= box[3]):
                continue
            seen.add((x, y))
            if brightness(x, y) > limit:
                continue
            pen_dot[x, y] = 255
            stack.extend(((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)))

    # 笔画掩膜的**紧致**外接框：只用来裁剪掩膜，不参与坐标换算
    xs = [x for y in range(band[0], band[1] + 1) for x in range(box[0], box[2] + 1)
          if pen_stroke[x, y]]
    ys = [y for y in range(band[0], band[1] + 1) for x in range(box[0], box[2] + 1)
          if pen_stroke[x, y]]
    stroke_box = (min(xs), min(ys), max(xs), max(ys)) if xs else box
    return box, side, max(radius, 1), stroke, dot, stroke_box


def stroke_profile(mask, box, band):
    """从笔画掩膜里量出每一列的**上下边**，返回 [(x, 上边, 下边)] 与轮廓外接框。

    为什么要这样量：笔触是手绘感的有机形状（中间厚、两端收细、整体微微上拱）。
    把整帧掩膜降采样再放大，笔触只剩十几像素高，拱形与收锋全被抹平成一根直条
    ——实测就是这样。改成"量出上下边、平滑后重画多边形"，形状才保得住。
    """
    columns = []
    for x in range(box[0], box[2] + 1):
        ys = [y for y in range(band[0], band[1] + 1) if mask.getpixel((x, y))]
        if ys:
            columns.append((x, min(ys), max(ys)))
    if not columns:
        return [], box
    # **只保留"最长的连续粗段"**：生成图在碑的左右边缘各留了一块斜的亮楔，它们比横画本身
    # 还厚（实测端部 47~130px），按"厚度 vs 中位数"剪根本剪不掉——实测横画因此被拉成
    # 11%~89%（真实是 16.4%~83.8%），两端多出两块模糊的斜块。
    # 横画本体厚 100~132px，两端伪影薄于 80px，用固定下限切出连续段最稳。
    thickness = [bottom - top + 1 for _, top, bottom in columns]
    floor = max(30, int(sorted(thickness)[len(thickness) // 2] * 0.62))
    runs, current = [], []
    for column, value in zip(columns, thickness):
        if value >= floor:
            current.append(column)
        elif current:
            runs.append(current)
            current = []
    if current:
        runs.append(current)
    if runs:
        columns = max(runs, key=len)
    left, right = columns[0][0], columns[-1][0]
    outline = (min(c[1] for c in columns), max(c[2] for c in columns))
    return columns, (left, outline[0], right, outline[1])


def rebuild_stroke(columns, crop, tile_box, side, master, cap_radius_ratio=0.058,
                   arc=0.0, taper=0.0):
    """把量到的上下边重画成一条平滑多边形（含两端的圆头），映射回主稿坐标。

    `arc`（碑高的比例）把笔画中段整体上抬一点，`taper` 让两端略微收细——两者都是
    **可选的美化**，默认 0 就是严格照搬底图（`--refine-stroke` 才打开）。
    """
    scale = master / side
    # 先把上下边各自做一次滑动平均：原图边缘有噪点，不平均会出现锯齿状抖动
    window = max(3, len(columns) // 60)
    top = [c[1] for c in columns]
    bottom = [c[2] for c in columns]
    def smooth(values):
        out = []
        for index in range(len(values)):
            low, high = max(index - window, 0), min(index + window + 1, len(values))
            out.append(sum(values[low:high]) / (high - low))
        return out
    top, bottom = smooth(top), smooth(bottom)

    # 可选美化：中间微微上拱（像被按下去又弹起的笔），两端略收细。
    # 底图的横画是完全平直的——这是设计者有意的取舍（见 docs/ICON.md 的含义说明），
    # 所以默认不改，只在 --refine-stroke 时应用。
    if arc:
        span = max(columns[-1][0] - columns[0][0], 1)
        for index, column in enumerate(columns):
            t = (column[0] - columns[0][0]) / span
            lift = arc * side * 4 * t * (1 - t)          # 抛物线：两端为 0、中段最大
            top[index] -= lift
            bottom[index] -= lift
    if taper:
        span = max(len(columns) - 1, 1)
        for index in range(len(columns)):
            t = index / span
            edge = min(t, 1 - t) / 0.18                  # 只影响两端各 18%
            if edge >= 1:
                continue
            keep = edge * edge * (3 - 2 * edge)          # 0（最端）→1（进入中段）
            keep = 1 - (1 - keep) * taper                # taper=0.3 时最端保留 70% 厚度
            middle = (top[index] + bottom[index]) / 2
            top[index] = middle + (top[index] - middle) * keep
            bottom[index] = middle + (bottom[index] - middle) * keep

    top_points = [(columns[i][0], top[i]) for i in range(len(columns))]
    bottom_points = [(columns[i][0], bottom[i]) for i in reversed(range(len(columns)))]
    polygon = top_points + bottom_points

    mask = Image.new('L', (master, master), 0)
    draw = ImageDraw.Draw(mask)

    def at(point):
        return (int(round((point[0] - tile_box[0]) * scale)), int(round((point[1] - tile_box[1]) * scale)))

    draw.polygon([at(point) for point in polygon], fill=255)
    # 两端补**圆头**：多边形在收锋处会留下一个尖角（看起来像鱼尾）。
    # 注意不要用"斜着点一串小圆"去补——那会在两角各留一个尖，实测就是这样画出鱼尾的。
    for column, centre_y in ((columns[0], (top[0] + bottom[0]) / 2),
                             (columns[-1], (top[-1] + bottom[-1]) / 2)):
        centre = at((column[0], centre_y))
        radius = max(2, int(round(side * cap_radius_ratio * scale / 2)))
        draw.ellipse((centre[0] - radius, centre[1] - radius,
                      centre[0] + radius, centre[1] + radius), fill=255)
    # 轮廓用 4× 超采样画，缩回来即得抗锯齿；再轻微模糊去掉多边形的硬折角
    big = Image.new('L', (master * 4, master * 4), 0)
    ImageDraw.Draw(big).polygon(
        [(int(round((x - tile_box[0]) * scale * 4)), int(round((y - tile_box[1]) * scale * 4)))
         for x, y in polygon], fill=255)
    mask = big.resize((master, master), Image.BOX).filter(ImageFilter.GaussianBlur(master / 512))
    return mask, crop


def circle_mask(center, diameter, master, supersample=SS):
    """高质量正圆掩膜（超采样后再缩回）。"""
    size = master * supersample
    radius = diameter * supersample / 2
    cx, cy = center[0] * supersample, center[1] * supersample
    mask = Image.new('L', (size, size), 0)
    ImageDraw.Draw(mask).ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=255)
    return mask.resize((master, master), Image.BOX)


def tile_mask(side, radius, master, supersample=SS):
    """高质量圆角矩形掩膜。"""
    size = master * supersample
    mask = Image.new('L', (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1),
                                           radius=int(radius * supersample), fill=255)
    return mask.resize((master, master), Image.BOX)


def paint(base, colour, mask):
    """把 `colour` 按 `mask` 贴到 `base` 上：**颜色与掩膜一起预乘**，避免边缘出彩边。

    直接把颜色贴上去会让掩膜的半透明边缘与底色做一次普通混合，边缘会出现一圈比两侧都暗的
    描边；预乘之后边缘就是"颜色与底色之间"的正常过渡。
    """
    layer = Image.new('RGBA', base.size, colour + (255,))
    layer.putalpha(mask)
    return Image.alpha_composite(base, layer) if base.mode == 'RGBA' else layer


REFINE = {'arc': 0.016, 'taper': 0.22}   # --refine-stroke 用的两组美化参数


def compose(source, refine=False):
    """合成主稿：圆角底 → 横画（勘的准线）→ 墨点（"句读"）。

    `refine=True` 时给横画加**轻微上拱**与**两端收细**。底图的横画是完全平直的
    ——这是设计者有意的取舍（它是一条准线，不是书法的一笔），所以默认照搬，
    只有显式要求时才动它。见 docs/ICON.md。
    """
    box, side, radius, stroke_src, dot_src, stroke_box = measure(source)
    tile = tile_mask(side, radius, MASTER)

    # 横画：量出每一列的上下边 → 平滑 → 重画成多边形（保留原图的厚度与走向）
    columns, outline = stroke_profile(stroke_src, stroke_box, (stroke_box[1], stroke_box[3]))
    stroke_layer, _ = rebuild_stroke(columns, outline, box, side, MASTER,
                                     **(REFINE if refine else {}))

    # 墨点：位置与直径取原图测量值（用质心，不受边缘噪点影响）
    dot_pixels = [(x, y) for y in range(box[1], box[3] + 1) for x in range(box[0], box[2] + 1)
                  if dot_src.getpixel((x, y))]
    if dot_pixels:
        cx = sum(x for x, _ in dot_pixels) / len(dot_pixels)
        cy = sum(y for _, y in dot_pixels) / len(dot_pixels)
        area = len(dot_pixels)
    else:                                     # 兜底：用比例表
        cx, cy = box[0] + DOT_CENTER[0] * side, box[1] + DOT_CENTER[1] * side
        area = 3.14159 * (DOT_DIAMETER_RATIO * side / 2) ** 2

    scale = MASTER / side
    dot_center = ((cx - box[0]) * scale, (cy - box[1]) * scale)
    dot_diameter = 2 * (area / 3.14159) ** 0.5 * scale

    master = Image.new('RGBA', (MASTER, MASTER), (0, 0, 0, 0))
    master.paste(Image.new('RGBA', (MASTER, MASTER), TILE + (255,)), (0, 0), tile)
    # 笔触掩膜已在主稿坐标系里，直接贴
    master = paint(master, CREAM, stroke_layer)
    master = paint(master, INK, circle_mask(dot_center, dot_diameter, MASTER))
    return master, dict(box=box, side=side, radius=radius, corner_ratio=radius / side,
                        dot_center=dot_center, dot_diameter=dot_diameter)


def downscale(image, size):
    """预乘 alpha + 逐级对半的盒式平均（不要用 LANCZOS：会振铃出黑边）。"""
    premultiplied = Image.new('RGBA', image.size)
    premultiplied.putdata([(red * a // 255, green * a // 255, blue * a // 255, a)
                           for red, green, blue, a in image.get_flattened_data()])
    target = (size, size)
    current = premultiplied
    while current.width // 2 >= target[0] and current.width % 2 == 0 and current.width > target[0]:
        current = current.resize((current.width // 2, current.height // 2), Image.BOX)
    if current.size != target:
        current = current.resize(target, Image.BOX)
    restored = []
    for red, green, blue, a in current.get_flattened_data():
        restored.append((0, 0, 0, 0) if a == 0 else
                        (min(255, red * 255 // a), min(255, green * 255 // a),
                         min(255, blue * 255 // a), a))
    out = Image.new('RGBA', current.size)
    out.putdata(restored)
    return out


def png_bytes(image):
    import io
    buffer = io.BytesIO()
    image.save(buffer, format='PNG', optimize=True)
    return buffer.getvalue()


def ico_bytes(images):
    import struct
    payloads = [png_bytes(image) for _, image in images]
    header = struct.pack('<HHH', 0, 1, len(payloads))
    offset = 6 + 16 * len(payloads)
    entries = b''
    for (size, _), payload in zip(images, payloads):
        entries += struct.pack('<BBBBHHII', 0 if size >= 256 else size, 0 if size >= 256 else size,
                               0, 0, 1, 32, len(payload), offset)
        offset += len(payload)
    return header + entries + b''.join(payloads)


def preview(master):
    cell, pad = 200, 16
    sheet = Image.new('RGBA', (pad + len(SIZES) * (cell + pad), cell + 2 * pad), BACKGROUND)
    for index, size in enumerate(SIZES):
        icon = downscale(master, size)
        sheet.alpha_composite(icon, (pad + index * (cell + pad) + (cell - size) // 2,
                                     pad + (cell - size) // 2))
    return sheet


def report(master):
    """自检：16px 下笔触与墨点的对比度必须足够（墨点在底图里只有 1.9:1，会看不见）。"""
    def luminance(pixel):
        return (0.2126 * pixel[0] + 0.7152 * pixel[1] + 0.0722 * pixel[2]) * (pixel[3] / 255)

    tile = luminance(TILE + (255,))
    cream = luminance(CREAM + (255,))
    ink = luminance(INK + (255,))
    stroke_ratio = (max(cream, tile) + 5) / (min(cream, tile) + 5)
    dot_ratio = (max(ink, tile) + 5) / (min(ink, tile) + 5)
    print(f'碑 {TILE} lum={tile:.0f}｜笔触 {CREAM} lum={cream:.0f}｜墨点 {INK} lum={ink:.0f}')
    print(f'对比度：笔触/碑 {stroke_ratio:.2f}:1，墨点/碑 {dot_ratio:.2f}:1')
    tiny = downscale(master, 16)
    opaque = [(x, y) for y in range(16) for x in range(16) if tiny.getpixel((x, y))[3] > 250]
    cream_pixels = [p for p in opaque if luminance(tiny.getpixel(p)) > 150]
    ink_pixels = [p for p in opaque if luminance(tiny.getpixel(p)) < 60]
    print(f'16px 实心像素 {len(opaque)} 个，其中亮（笔触）{len(cream_pixels)}、暗（墨点）{len(ink_pixels)}')
    return stroke_ratio, dot_ratio, len(ink_pixels)


def build(source, refine=False, compare=False):
    master, facts = compose(source, refine=refine)
    print('底图测量：碑 {box} 边长 {side}px，圆角 {radius}px（{ratio:.1%}），'
          '墨点中心 {center} 直径 {diameter:.0f}/1024'.format(
              box=facts['box'], side=facts['side'], radius=facts['radius'],
              ratio=facts['corner_ratio'], center=tuple(round(v) for v in facts['dot_center']),
              diameter=facts['dot_diameter']))
    report(master)

    written = []
    for size, name in ((512, 'icon.png'), (256, 'icon-256.png')):
        target = ROOT / 'static' / name
        downscale(master, size).save(target, format='PNG', optimize=True)
        written.append(target)
    ico = ROOT / ICO_NAME
    ico.write_bytes(ico_bytes([(size, downscale(master, size)) for size in SIZES]))
    written.append(ico)
    sheet = Path(__file__).resolve().parent / 'icon-preview.png'
    preview(master).save(sheet, format='PNG', optimize=True)
    written.append(sheet)
    for path in written:
        print(f'{path.relative_to(ROOT)}  {path.stat().st_size} B')

    if compare:
        # 并排放两版（照搬底图 / 轻微美化），供人挑；只写预览，不动成品。
        refined, _ = compose(source, refine=True)
        cell, pad = 320, 20
        side_by_side = Image.new('RGBA', (cell * 2 + pad * 3, cell + pad * 2), BACKGROUND)
        for index, candidate in enumerate((master, refined)):
            side_by_side.alpha_composite(downscale(candidate, cell), (pad + index * (cell + pad), pad))
        target = Path(__file__).resolve().parent / 'icon-compare.png'
        side_by_side.save(target, format='PNG', optimize=True)
        print(f'对比图（左：照搬底图｜右：轻微美化）{target.relative_to(ROOT)}')


if __name__ == '__main__':
    arguments = [item for item in sys.argv[1:]]
    flags = {item for item in arguments if item.startswith('--')}
    paths = [item for item in arguments if not item.startswith('--')]
    if not paths:
        raise SystemExit('用法：python scripts/build_icon.py <底图.png> [--refine-stroke] [--compare]')
    source = Path(paths[0])
    if not source.is_file():
        raise SystemExit(f'底图不存在：{source}')
    build(source, refine='--refine-stroke' in flags, compare='--compare' in flags)
