# 第三方组件与资源

本文件说明：本仓库**实际分发**了哪些第三方成果，以及哪些只是"使用者自己安装"。
区分这两件事很重要——分发者与安装者的义务不同。

本文件是工程记录，**不构成法律意见**；许可原文以各自的许可证文本为准。

## 1. 随本仓库分发的第三方成果

| 成果 | 位置 | 许可证 | 随仓库的声明 |
|---|---|---|---|
| Noto Sans CJK SC（Regular / Bold） | `static/fonts/*.otf` | SIL OFL 1.1 | `static/fonts/OFL.txt`（OFL 原文） |
| 项目图标（底图由生图 AI 生成，经脚本加工） | `static/icon.png`、`static/icon-256.png`、`勘读.ico` | 见 `legal/BRAND.md`：**不随代码许可证授权**；来历见 `docs/ICON.md` | `docs/ICON.md`、`legal/BRAND.md` |
| 上游许可证原件（供核对） | `legal/upstream/` | 各上游许可证 | PaddleOCR、RapidOCR、flatbuffers、tokenizers 的许可证原文 |

字体按 OFL 1.1 分发：保留了原始字体文件与许可证，未修改字体，也未单独售卖字体——
OFL 允许字体与任何许可证的软件一同分发，不要求软件采用特定许可证。

## 2. 由使用者自行安装的依赖（本仓库不分发）

`requirements.txt` / `requirements.lock.txt` 只声明依赖，**不包含也不分发**这些组件的代码或数据。
使用者执行安装命令时，由包索引（PyPI）提供这些组件，因此**安装者本人**是其副本的获取方。

43 个依赖的许可证全部为宽松许可，没有任何 GPL / AGPL / SSPL：

| 许可证 | 数量 | 说明 |
|---|---|---|
| MIT（含 MIT-CMU、`MIT License`） | 17 | 保留版权与许可声明即可 |
| BSD 系（2/3-Clause、多许可并列） | 9 | 同上 |
| Apache-2.0 系 | 7 | 保留声明；如分发需附许可证原文 |
| MPL-2.0 | 2 | `certifi`、`tqdm`。文件级著佐权：只约束被修改的 MPL 文件；更大的作品可采用任何许可证 |
| PSF-2.0 | 1 | `typing_extensions` |
| 其他（`See bundled license`） | 2 | `colorama`、`tokenizers`，各自随包附带许可证 |

许可证清单与哈希见 `legal/inventory.json`，逐项上游声明见 `legal/packages/`。

## 3. OCR 模型：内置在依赖包里，不由本仓库分发

程序**离线可用**（装完依赖即可识别，不需要联网下载模型），但模型文件来自依赖包：

```
.venv/Lib/site-packages/rapidocr_onnxruntime/models/
    ch_PP-OCRv3_det_infer.onnx          2.32 MB
    ch_PP-OCRv3_rec_infer.onnx         10.20 MB
    ch_ppocr_mobile_v2.0_cls_infer.onnx 0.56 MB
```

三个模型是 **`rapidocr-onnxruntime` 这个 wheel 自带的文件**（见该包安装记录 RECORD），
上游为 [RapidAI/RapidOCR](https://github.com/RapidAI/RapidOCR)，模型源自 PaddleOCR，
许可证 **Apache-2.0**。本仓库既不包含这些 `.onnx`，也不在运行时下载它们。

因此：**本仓库不是这些模型的分发者**；本仓库保留 `legal/upstream/` 里的许可证原文是主动的加分项。

**注意**：一旦把 `.venv` 打包成安装包（例如用 PyInstaller 打成单文件），角色就变了——
那时分发者就是打包者本人，Apache-2.0 的声明义务、以及 LGPL 组件（Shapely 附带的 GEOS、
OpenCV 附带的 FFmpeg）要求的"可替换/可修改"条件都会落到打包者头上。
`legal/NOTICE.html` 里对此已有同样的提醒。

## 4. 与代码许可证的关系

本项目的代码采用 PolyForm Noncommercial 1.0.0（见仓库根 `LICENSE` 与 `NOTICE`）。
上述第三方组件**不受该许可证约束**：它们仍按各自许可证授权，
本项目只是把它们（或它们的声明）与本项目一起分发。

各宽松许可证都允许被纳入以其他条款发布的更大作品，因此**不存在冲突**。

## 已核对的官方来源

代码注释与设置面板里标注"已核对"的接口，其依据是下列官方文档。本项目只实现兼容调用，
**不复述文档原文**；平台名称仅用于说明可连接的兼容服务，不代表任何官方合作或背书。

### 语音（`app/speech_protocols.py`）

- MiMo ASR / TTS：小米开放平台官方文档的 `chat/completions` 音频用法（`input_audio` 内容块、
  `asr_options.language`、`audio.format` / `audio.voice`、`choices[0].message.audio.data`）。
- MiniMax ASR：官方 `speech_to_text`（multipart，字段 `model` + `file`）。
- MiniMax TTS：官方 `t2a_v2`（JSON，音频以 hex 返回，状态在 `base_resp`）。

### 模型服务预设（`app/settings.py`）

- 阿里云百炼：Embedding 与对话共用 OpenAI 兼容接口 `compatible-mode/v1`。
- 智谱 GLM：`embeddings` 与 `chat` 同在 `/api/paas/v4` 下。
- 火山方舟（豆包）：`/api/v3` 的对话接口。
- 其余平台与模型：只登记能以官方文档核实的端点与用途，核不准的一律不写，
  宁可由用户自行填写，也不给跑不通的预设。
