$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$compilerPath = Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
if (-not (Test-Path -LiteralPath $compilerPath)) { throw 'Windows .NET Framework C# compiler not found' }
$outputPath = Join-Path $projectRoot '勘读.exe'
$sourcePath = Join-Path $PSScriptRoot 'ReaderLauncher.cs'
# 图标随 exe 一起编译进去（资源管理器、任务栏、Alt-Tab 都取它）。
# 图标由 scripts/build_icon.py 从设计者提供的底图加工而来（含义与来历见 docs/ICON.md），
# 不是外部下载的素材。
$iconPath = Join-Path $projectRoot '勘读.ico'
if (-not (Test-Path -LiteralPath $iconPath)) { throw 'Icon missing: build it with scripts/build_icon.py first' }
& $compilerPath /nologo /target:winexe /reference:System.Windows.Forms.dll "/win32icon:$iconPath" "/out:$outputPath" $sourcePath
if ($LASTEXITCODE -ne 0) { throw 'Launcher compilation failed' }
# 再独立校验一遍产物：/win32icon 失败时 csc 可能只给警告、仍返回 0，
# 那样会产出一个"没有图标的 exe"，而这只会在用户双击时才被看见。
& (Join-Path $PSScriptRoot 'check-exe-icon.ps1') -Path $outputPath
