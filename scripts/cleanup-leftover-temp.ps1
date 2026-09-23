# 清理工作区里遗留的临时目录（旧 pytest / 探针产物）。
#
# 背景：早期几轮在受限沙箱里建出的临时目录，权限只给创建它的受限令牌；删除时会出现
# "Access to the path ... is denied"。多数可以直接删；少数带拒绝 ACE 的目录连读取都不允许，
# 必须以**管理员**身份运行本脚本才能收拾干净（本机 UAC 会弹「用户账户控制」对话框，点「是」）。
#
# 用法：
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\cleanup-leftover-temp.ps1
# 提权之后才能删掉带拒绝 ACE 的目录；两种做法：
#   · 资源管理器里选中那些目录 → Delete → 权限提示里点「继续」（这一步本身就是提权）
#   · 或在「以管理员身份运行」的 PowerShell 里执行上面那条命令
# （曾经有个双击即提权的 .cmd 入口，用户实测双击没反应、平时也用不上，已按用户要求删除，
#   不再提供第二个入口——需要提权时用上面两种做法即可。）
#
# ─────────────────────────────────────────────────────────────────────────────
# 本文件必须保存为 **UTF-8 带 BOM**（开头三个字节 EF BB BF）。
# Windows PowerShell 5.1 读没有 BOM 的 UTF-8 文件时会按本地代码页（中文系统是 936）解码，
# 中文字符串会被拆坏、引号配不上，脚本**直接语法错误、一行都不执行**——表现就是"双击了，
# 什么也没发生"。2026-09-18 就是这么坏的（一次编辑把 BOM 弄丢了），已由
# tests/test_cleanup_scripts.py 钉死。
# ─────────────────────────────────────────────────────────────────────────────
#
# 安全约束：只处理仓库根目录下、匹配下方固定模式、且被 git 忽略的目录；被 git 跟踪的一律跳过；
# data/ 不在模式内，绝不触碰用户文献与配置。

[CmdletBinding()]
param(
    # 默认取仓库根目录（脚本放在 scripts/ 下）。不能直接在 param 默认值里用 $PSScriptRoot：
    # 以 -File 方式运行时，它在参数绑定阶段还是空的。
    [string]$Root = '',
    # 运行记录写到哪里（默认 scripts\cleanup-last-run.log，已加入 .gitignore）。
    # 提权后的窗口可能一闪而过，留一份记录便于事后核对，也便于自动化测试。
    [string]$LogPath = ''
)

$ErrorActionPreference = 'Continue'
if (-not $Root) { $Root = Split-Path -Parent $PSScriptRoot }
if (-not $Root) { $Root = (Get-Location).Path }

$patterns = '^(\.test-.*|\.demo-ui-.*|\.pytest_cache|\.pytest-tmp|pytest-cache-files-.*|\.mode700-probe|ptb-.*|pt-.*|ptesc.*|probe.*|pywork-.*|tmproot.*|xo-.*)$'
$logPath = if ($LogPath) { $LogPath } else { Join-Path $PSScriptRoot 'cleanup-last-run.log' }

function Write-Report([string]$text, [string]$color = 'Gray') {
    Write-Host $text -ForegroundColor $color
    try { Add-Content -LiteralPath $logPath -Value $text -Encoding UTF8 } catch { }
}

function Test-Elevated {
    try {
        $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
        return ([Security.Principal.WindowsPrincipal]$identity).IsInRole(
            [Security.Principal.WindowsBuiltInRole]::Administrator)
    } catch { return $false }
}

$elevated = Test-Elevated
try {
    Set-Content -LiteralPath $logPath -Encoding UTF8 -Value @(
        ('清理时间：' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')),
        ('仓库根目录：' + $Root),
        ('管理员身份：' + $(if ($elevated) { '是' } else { '否' })),
        ''
    )
} catch { }

Write-Report ('仓库根目录：' + $Root)
Write-Report ('管理员身份：' + $(if ($elevated) { '是' } else { '否（带拒绝 ACE 的目录删不掉，需要提权）' }))

function Test-Ignored([string]$name) {
    # 只用 git 判断"这个目录是不是本来就不该进仓库"，避免误删用户文件。
    try {
        git -C $Root check-ignore --quiet -- $name 2>$null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

$targets = @(Get-ChildItem -LiteralPath $Root -Force -Directory -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match $patterns })

if (-not $targets) {
    Write-Report '没有需要清理的临时目录。' 'Green'
    exit 0
}

Write-Report ('找到 ' + $targets.Count + ' 个匹配的临时目录，逐个核对是否被 git 忽略：')

$removed = @(); $blocked = @(); $skipped = @()
foreach ($dir in $targets) {
    if (-not (Test-Ignored $dir.Name)) {
        $skipped += $dir.Name
        Write-Report ('跳过（未被 git 忽略）：' + $dir.Name) 'Yellow'
        continue
    }
    try {
        Remove-Item -LiteralPath $dir.FullName -Recurse -Force -ErrorAction Stop
        $removed += $dir.Name
        Write-Report ('已删除：' + $dir.Name) 'Green'
    } catch {
        $blocked += $dir.Name
        Write-Report ('权限被拒绝：' + $dir.Name + '（' + $_.Exception.GetType().Name + '）') 'Yellow'
    }
}

Write-Report ''
Write-Report ('结果：删除 ' + $removed.Count + ' 个，跳过 ' + $skipped.Count + ' 个，被拒绝 ' + $blocked.Count + ' 个。')

if ($blocked.Count -eq 0) {
    Write-Report ('完成。详细记录：' + $logPath) 'Green'
    exit 0
}

if ($elevated) {
    Write-Report '已经是管理员身份，仍然被拒绝：这些目录的拒绝 ACE 连管理员也挡住了。' 'Yellow'
    Write-Report '可以在资源管理器里对它们点右键 →「属性」→「安全」→「高级」→ 更改所有者为当前用户后再删除；' 'Yellow'
    Write-Report '或者直接删除整个仓库副本、重新克隆（它们本来就不入库、不含用户数据）。' 'Yellow'
} else {
    Write-Report '这些目录带拒绝 ACE，非管理员身份连读取都不允许，必须提权后再跑一次：' 'Yellow'
    Write-Report '  方式一：在资源管理器里选中这些文件夹 → Delete → 权限提示里点「继续」' 'Yellow'
    Write-Report '  方式二：开始菜单搜索 PowerShell → 右键「以管理员身份运行」，然后执行：' 'Yellow'
    Write-Report ('    powershell -NoProfile -ExecutionPolicy Bypass -File "' + $PSCommandPath + '"') 'Yellow'
}
Write-Report ('详细记录：' + $logPath)
exit 1
