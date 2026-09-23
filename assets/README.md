# 图标母本（设计者提供的原始文件）

这两个文件是**设计者提供的图标原稿**，归档在这里是为了让图标可复现、可再加工。

| 文件 | 是什么 | 用途 |
| --- | --- | --- |
| `icon-master.png` | **1254×1254 PNG（RGBA，带透明）** ——当前使用的母本 | `scripts/adopt_icon.py` 默认就取它 |
| `icon-master.ico` | 早先交回的七档 .ico（最大 256px） | 留档；分辨率低于上面那个 PNG，正常构建不用它 |

## 设计含义

> **一条平直的横画表示"被核定的文本"，右下一点表示"句读"，即阅读与精读。**

- **横画＝勘的准线**："勘"是拿一条基准去逐字核定文本（校勘、勘误都是这个动作），
  所以它**刻意平直、两端圆头**，不采用毛笔笔法——它不是书法的一笔，而是一条准线。
- **墨点＝读的落点**：句读是阅读留下的痕迹，落在横画右下方。

完整说明见 [`docs/ICON.md`](../docs/ICON.md) §1。

> **图形的来历**：底图由本项目设计者用生图 AI 生成，成品由脚本加工，**不重画任何形状**。
> 底图设计者即本项目自己，因此含义说明是本项目自己的表述。

## 重出成品图标

```powershell
.\.venv\Scripts\python scripts\adopt_icon.py        # 用归档母本，重出页签 PNG 与 勘读.ico
```

脚本只做三件事（**不重画任何形状**）：取母本 → 预乘 alpha 缩放成 512/256 与七档 →
打包 `static/icon.png`、`static/icon-256.png`、`勘读.ico`。唯一的加工是**把墨点压暗**
（母本墨点与底色只差 1.9:1 对比度，16px 下几乎看不见），位置与直径不动。

然后按需执行：

```powershell
.\.venv\Scripts\python scripts\license_inventory.py                                   # 更新资源哈希
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build-launcher.ps1        # 重新编译带图标的 exe
.\.venv\Scripts\python -m pytest -q tests/test_icon.py                                # 图标契约
```

## 授权与归属

- 母本由本项目设计者提供（用生图 AI 产出），**名称与图标不随代码许可证授权**，
  使用边界见 [`legal/BRAND.md`](../legal/BRAND.md)；
- 设计说明与加工细节见 [`docs/ICON.md`](../docs/ICON.md)；第三方组件与资源的授权状况见
  [`legal/THIRD-PARTY.md`](../legal/THIRD-PARTY.md)。
