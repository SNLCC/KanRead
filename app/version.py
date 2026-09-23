"""接口版本：前端与启动器都要和它对齐。

改动了前端依赖的后端行为（新增接口、改变返回结构）就 +1。
- `app/main.py` 由 `/api/status`、`/api/health` 暴露这个值；
- `static/app.js` 的 `REQUIRED_API_VERSION` 必须相等，否则界面提示"请重启应用"；
- `scripts/ReaderLauncher.cs` 的 `ExpectedApiVersion` 必须相等，否则启动器会先结束旧后台再启动新的。

三处一致由 `tests/test_ui_contracts.py` 断言，避免"改了后端但界面/启动器还在等旧行为"。
"""
API_VERSION = 46
