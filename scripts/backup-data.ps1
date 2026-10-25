# Backup the KanRead data directory (PDFs, SQLite databases, local config) into
# data\backups\kanread-data-<yyyyMMdd-HHmmss>.zip.
#
# Why this script exists: users update the program by unpacking a new build over the old
# directory. Before that they need a one-command, verifiable backup of data/ so a bad update
# can never destroy literature, annotations or notes.
#
# ASCII-only on purpose: Windows PowerShell 5.1 decodes UTF-8 without a BOM using the local
# code page (936 on Chinese systems). Non-ASCII literals get corrupted and can even break
# quoting, so this script keeps every literal ASCII. Chinese documentation lives in README.
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\backup-data.ps1
#   powershell ... -Root "<program-dir>" -Python "<program-dir>\.venv\Scripts\python.exe"
#   add -IncludeLogs to also pack application.log*, -SkipStop to keep the server running.
#
# Exit codes: 0 = backup written, 1 = backup failed, 2 = not a KanRead program directory.

[CmdletBinding()]
param(
    [string]$Root = '',
    [string]$Python = '',
    [int]$Port = 8765,
    [switch]$IncludeLogs,
    [switch]$SkipStop
)

$ErrorActionPreference = 'Stop'

function Fail([string]$Message, [int]$Code = 1) {
    Write-Host ('ERROR: ' + $Message) -ForegroundColor Red
    exit $Code
}

if (-not $Root) { $Root = Split-Path -Parent $PSScriptRoot }
if (-not $Root) { Fail 'Cannot determine the program directory; pass -Root.' 2 }
$Root = [System.IO.Path]::GetFullPath($Root)

# 1/4 Confirm this really is a KanRead program directory before touching anything.
$AppDir = Join-Path $Root 'app'
$DataDir = Join-Path $Root 'data'
if (-not (Test-Path -LiteralPath $AppDir -PathType Container)) {
    Fail ('Not a KanRead program directory (missing app\): ' + $Root) 2
}
if (-not (Test-Path -LiteralPath $DataDir -PathType Container)) {
    Fail ('Data directory not found: ' + $DataDir) 2
}

# 2/4 Resolve Python: explicit -Python, then the bundled venv, then whatever "python" is.
$PythonExe = $Python
if (-not $PythonExe) {
    $candidate = Join-Path $Root '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $candidate -PathType Leaf) { $PythonExe = $candidate }
    else { $PythonExe = 'python' }
}
Write-Host ('Program directory: ' + $Root)
Write-Host ('Data directory:    ' + $DataDir)
Write-Host ('Python:            ' + $PythonExe)

# 3/4 Stop the running background server before packing (except when asked to skip it).
# A stopped server is not strictly required (the backup uses SQLite's consistent snapshot
# API), but it keeps files from changing while the zip is being written.
if (-not $SkipStop) {
    $stopCode = "from app.single_instance import stop_stale; import sys; state, detail = stop_stale($Port, 0, log=print); print(state + ' ' + detail); sys.exit(0 if state != 'blocked' else 3)"
    Push-Location -LiteralPath $Root
    try {
        & $PythonExe -c $stopCode
        $stopExit = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    if ($stopExit -eq 3) { Fail ('Port ' + $Port + ' is held by another program; refusing to pack a moving target.') 1 }
    if ($stopExit -ne 0) { Fail ('Could not stop the background server (python exit ' + $stopExit + ').') 1 }
}

# 4/4 Build the zip. app\backup.py snapshots each SQLite database with the backup API
# first, so even a database that is still being written is packed consistently.
$BackupScript = Join-Path $PSScriptRoot 'backup_data.py'
if (-not (Test-Path -LiteralPath $BackupScript -PathType Leaf)) { Fail ('Missing helper: ' + $BackupScript) 1 }
$PythonArgs = @($BackupScript, '--data-dir', $DataDir)
if ($IncludeLogs) { $PythonArgs += '--include-logs' }
& $PythonExe @PythonArgs
if ($LASTEXITCODE -ne 0) { Fail 'Backup failed; nothing was written.' 1 }

Write-Host 'Backup finished.' -ForegroundColor Green
exit 0
