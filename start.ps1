$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

# 首次安装要建虚拟环境并装依赖，这一步需要机器上已有 Python。
# 检查放在最前面，是为了在"没装 Python"的机器上给出人话提示——否则用户看到的
# 是 PowerShell 的原始报错（"无法将 'python' 项识别为 cmdlet"），看不出该怎么办。
# 这里只写"Python 官网"而不写完整链接：链接会变，写死了反而可能把人引到失效页面。
if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) {
    if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
        Write-Host ''
        Write-Host '未检测到 Python，无法完成首次安装。'
        Write-Host ''
        Write-Host '请这样处理：'
        Write-Host '  1) 先安装 Python 3.11 或更高版本（从 Python 官网下载安装包，安装时勾选“Add Python to PATH”）；'
        Write-Host '  2) 装好后重新双击 start.bat（或再次运行本脚本）。'
        Write-Host ''
        Write-Host '以后启动不需要再做这一步：装好一次之后，直接双击 勘读.exe 即可。'
        exit 1
    }
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Unable to create Python environment' }
}
& './.venv/Scripts/python.exe' -m pip install -r requirements.lock.txt --disable-pip-version-check
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
Write-Host '勘读 · KanRead: http://127.0.0.1:8765'
# 已有后台在跑时不会被这次启动替换掉：先检查接口版本，旧版本要结束掉，
# 否则浏览器用的是新前端、接口却还是旧代码（最难自查的一种状态）。
& './.venv/Scripts/python.exe' -c "from app.single_instance import stop_stale; from app.version import API_VERSION; state, detail = stop_stale(8765, API_VERSION, log=print); print(state, detail)"
# 日志要和「勘读.exe」那条启动路径一致：都写 data/application.log（2MB 轮转、保留 5 份）。
# 原来这里直接跑 `python -m uvicorn`，uvicorn 不会往这个文件写日志，用户关掉控制台窗口后
# 就什么都不剩了。用 -c 在同一个进程里先配置日志再启动，两条路径共用 app/logging_setup.py。
& './.venv/Scripts/python.exe' -c 'from app.logging_setup import configure; configure(); import uvicorn; uvicorn.run(''app.main:app'', host=''127.0.0.1'', port=8765, log_config=None, access_log=False)'
