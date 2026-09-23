# 参与开发

感谢你有兴趣改进勘读。这个文件说明**提交代码时的规则**，主要是为了把授权链留清楚。

## 1. 先知道本项目的许可形态

本项目代码采用 **PolyForm Noncommercial License 1.0.0**（见仓库根 `LICENSE` 与 `NOTICE`）：

- 任何**非商业**目的都可以使用、修改与分发；
- **商业用途需另行取得许可**；
- 按 OSI 的定义，这**不是**开源许可，而是"源码可用"（source-available）。

因此提交代码时，你的贡献也会以同一许可证发布。请确认你接受这一点。

## 2. 提交必须带 Signed-off-by（DCO）

每个提交都要有 `Signed-off-by` 行，声明你有权提交这段代码，并同意它按本项目许可证发布。
用 `-s` 参数即可自动加上：

```bash
git commit -s -m "说明这次改了什么"
```

得到的提交信息末尾会多一行：

```
Signed-off-by: 你的名字 <你的邮箱>
```

这一行的含义（[Developer Certificate of Origin 1.1](https://developercertificate.org/) 原文）：

```
Developer Certificate of Origin
Version 1.1

Copyright (C) 2004, 2006 The Linux Foundation and its contributors.

Everyone is permitted to copy and distribute verbatim copies of this
license file, but changing it is not allowed.


Developer's Certificate of Origin 1.1

By making a contribution to this project, I certify that:

(a) The contribution was created in whole or in part by me and I
    have the right to submit it under the open source license
    indicated in the file; or

(b) The contribution is based upon previous work that, to the best
    of my knowledge, is covered under an appropriate open source
    license and I have the right under that license to submit that
    work with modifications, whether created in whole or in part
    by me, under the same open source license (unless I am
    permitted to submit under a different license), as indicated
    in the file; or

(c) The contribution was provided directly to me by some other
    person who certified (a), (b) or (c) and I have not modified
    it.

(d) I understand and agree that this project and the contribution
    are public and that a record of the contribution (including all
    personal information I submit with it, including my sign-off) is
    maintained indefinitely and may be redistributed consistent with
    this project or the open source license(s) involved.
```

**说清楚一件事**：DCO **不授予**维护者更换许可证的权利（它不转让版权，只证明你有权按本项目许可证提交）。
如果你希望自己的贡献能被用于其他许可证下的版本，请在提交前说明，由维护者与你另行确认。
维护者若将来需要更大的授权弹性，会另行采用贡献者许可协议（CLA）——届时会明确告知，不会追溯适用。

## 3. 不要提交这些内容

- **任何真实文献、批注、笔记、译文**，以及 `data/` 下的任何文件（已在 `.gitignore` 中）；
- API Key、Token、账号信息（`.env` 同样不入库）；
- 从别处复制来的代码，除非其许可证与本项目兼容**且**你保留了原始声明与出处。

测试需要"文献"时，请像 `tests/test_app.py::pdf_bytes` 那样**运行时合成**，不要提交真实 PDF。

## 4. 提交前请自测

```powershell
# 后端全量（沙箱环境需提权，见 HANDOFF 的说明）
.\.venv\Scripts\python.exe -m pytest tests -q

# 前端
node tests\<改动相关>.test.cjs

# 许可证与资源清单（改了依赖或静态资源后必须跑）
.\.venv\Scripts\python.exe scripts\check_licenses.py
```

改动会影响界面契约时，接口版本要三处同步 `+1`：`app/version.py`、`static/app.js` 的
`REQUIRED_API_VERSION`、`scripts/ReaderLauncher.cs` 的 `ExpectedApiVersion`，
并重新编译 `勘读.exe`（`tests/test_ui_contracts.py` 会断言三处一致）。

## 5. 提交信息

用中文，第一行说清"改了什么"，正文说明"为什么这么改"与"边界/未做的部分"。
本项目重视如实披露：改动若降低了核验强度、缩小了覆盖范围或引入了估算，请在提交信息里写明。
