# One-command updater for KanRead.
#
# Why this script exists: users update by unpacking a new build over the old directory.
# Done by hand that is easy to get dangerously wrong: data/ can be overwritten, dependencies
# forgotten, and the old server process keeps serving stale code. This script makes the safe
# order the only order:
#
#   1. validate the target is a KanRead program directory and the package looks complete
#   2. stop the running background server
#   3. back up data/ (mandatory; abort if the backup fails)
#   4. snapshot the current program files into _update-backup\program-<stamp>\
#   5. replace program files only (data, .venv, .git, __pycache__, logs, diagnostics,
#      backups are never replaced)
#   6. reinstall pinned dependencies only when requirements.lock.txt changed
#   7. restart KanRead
#
# Any failure in steps 5/6 rolls the replaced files back and exits non-zero. -DryRun prints
# the whole plan and changes nothing (not even the backup).
#
# ASCII-only on purpose: Windows PowerShell 5.1 decodes UTF-8 without a BOM using the local
# code page (936 on Chinese systems). Non-ASCII literals would be corrupted and could break
# quoting. Chinese documentation lives in README.
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\update.ps1 -Zip "<downloads>\KanRead-0.2.zip"
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\update.ps1 -Zip "...zip" -DryRun
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\update.ps1 -Git
#
# Exit codes: 0 = success (or a clean -DryRun), 1 = failed (rolled back when possible),
#             2 = usage / validation error (nothing was changed).

[CmdletBinding()]
param(
    [string]$Zip = '',
    [switch]$Git,
    [switch]$DryRun,
    [switch]$NoRestart,
    [switch]$SkipStop,
    [switch]$SkipDependencies,
    [string]$Root = '',
    [string]$Python = '',
    [int]$Port = 8765
)

$ErrorActionPreference = 'Stop'

# Paths/names that must never be replaced by an update. Lower-case comparison.
$ExcludedSegments = @('data', '.venv', '.git', '__pycache__', 'diagnostics', 'backups',
                      '_update-backup', '_update-tmp', '.pytest_cache', 'node_modules')
$ExcludedFilePatterns = @('*.log', '*.pyc', '*.pyo')
$RequiredPackageEntries = @('app/main.py', 'static/index.html', 'requirements.lock.txt')

function Fail([string]$Message, [int]$Code = 1) {
    Write-Host ('ERROR: ' + $Message) -ForegroundColor Red
    exit $Code
}

function Write-Step([string]$Number, [string]$Text) {
    Write-Host ''
    Write-Host ('== Step ' + $Number + ': ' + $Text) -ForegroundColor Cyan
}

function Test-ExcludedPath([string]$Relative) {
    $parts = $Relative -split '[\\/]'
    foreach ($part in $parts) {
        if ($ExcludedSegments -contains $part.ToLowerInvariant()) { return $true }
    }
    $leaf = $parts[$parts.Count - 1]
    foreach ($pattern in $ExcludedFilePatterns) {
        if ($leaf -like $pattern) { return $true }
    }
    return $false
}

function Read-ZipEntryText([string]$ZipPath, [string]$EntryName) {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [System.IO.Compression.ZipFile]::OpenRead($ZipPath)
    try {
        $entry = $archive.Entries | Where-Object { $_.FullName.Replace('\', '/') -eq $EntryName } | Select-Object -First 1
        if (-not $entry) { return $null }
        $reader = New-Object System.IO.StreamReader($entry.Open(), [System.Text.Encoding]::UTF8)
        try { return $reader.ReadToEnd() } finally { $reader.Dispose() }
    } finally {
        $archive.Dispose()
    }
}

function Get-ZipEntries([string]$ZipPath) {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [System.IO.Compression.ZipFile]::OpenRead($ZipPath)
    try {
        return @($archive.Entries | ForEach-Object { $_.FullName.Replace('\', '/') })
    } finally {
        $archive.Dispose()
    }
}

function Resolve-PackageRoot([string[]]$Entries) {
    # A package may zip the folder itself; locate the single prefix that contains app/main.py.
    $prefix = ''
    $found = $false
    foreach ($entry in $Entries) {
        if ($entry -eq 'app/main.py') { $prefix = ''; $found = $true; break }
        if ($entry.EndsWith('/app/main.py')) {
            $candidate = $entry.Substring(0, $entry.Length - 'app/main.py'.Length)
            if (-not $found -or $candidate.Length -lt $prefix.Length) { $prefix = $candidate; $found = $true }
        }
    }
    $missing = @()
    if (-not $found) {
        $missing = @($RequiredPackageEntries)
    } else {
        foreach ($required in $RequiredPackageEntries) {
            $wanted = $prefix + $required
            if (-not ($Entries -contains $wanted)) { $missing += $required }
        }
    }
    return [pscustomobject]@{ Prefix = $prefix; Missing = $missing; Found = $found }
}

function Read-VersionFromText([string]$Text) {
    if (-not $Text) { return 'unknown' }
    $match = [regex]::Match($Text, 'API_VERSION\s*=\s*(\d+)')
    if ($match.Success) { return $match.Groups[1].Value }
    return 'unknown'
}

function Read-CurrentApiVersion {
    $file = Join-Path $Root 'app\version.py'
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { return 'unknown' }
    return Read-VersionFromText ([System.IO.File]::ReadAllText($file, [System.Text.Encoding]::UTF8))
}

function Get-RequirementsHash {
    $file = Join-Path $Root 'requirements.lock.txt'
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { return '' }
    return (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash
}

function Resolve-Python {
    if ($Python) { return $Python }
    $candidate = Join-Path $Root '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
    return 'python'
}

function Invoke-Git([string[]]$Arguments) {
    Push-Location -LiteralPath $Root
    try {
        $output = & git @Arguments 2>&1
        $code = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    return [pscustomobject]@{ Code = $code; Output = (($output | ForEach-Object { "$_" }) -join "`n") }
}

function Stop-Background {
    if ($SkipStop) { Write-Host '  skipped (-SkipStop).'; return $true }
    $pythonExe = Resolve-Python
    $stopCode = "from app.single_instance import stop_stale; import sys; state, detail = stop_stale($Port, 0, log=print); print(state + ' ' + detail); sys.exit(0 if state != 'blocked' else 3)"
    Push-Location -LiteralPath $Root
    try {
        & $pythonExe -c $stopCode
        $code = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    if ($code -eq 3) { Write-Host '  port is held by another program; refusing to continue.'; return $false }
    if ($code -ne 0) { Write-Host '  could not stop the background server.'; return $false }
    return $true
}

function Invoke-DataBackup([string]$Stamp) {
    $backupScript = Join-Path $PSScriptRoot 'backup-data.ps1'
    if (-not (Test-Path -LiteralPath $backupScript -PathType Leaf)) { Fail ('Missing helper: ' + $backupScript) 1 }
    $pythonExe = Resolve-Python
    Write-Host ('  running: ' + $backupScript)
    # Route the backup helper output through Write-Host: otherwise those marker lines would
    # become part of this function's return value and pollute the reported backup path.
    try {
        & $backupScript -Root $Root -Python $pythonExe -SkipStop | Write-Host
        $backupCode = $LASTEXITCODE
    } catch {
        Write-Host ('  backup helper failed: ' + $_) -ForegroundColor Red
        $backupCode = 1
    }
    Write-Host ('  backup helper exit: ' + $backupCode)
    if ($backupCode -ne 0) { Fail 'Data backup failed; update aborted before touching program files.' 1 }
    $folder = Join-Path $Root 'data\backups'
    $latest = Get-ChildItem -LiteralPath $folder -Filter 'kanread-data-*.zip' -File -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $latest) { Fail 'Backup reported success but no archive was found; update aborted.' 1 }
    Write-Host ('  backup: ' + $latest.FullName) -ForegroundColor Green
    return $latest.FullName
}

function Backup-ProgramFiles([string]$Destination) {
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    $copied = 0
    $files = Get-ChildItem -LiteralPath $Root -Recurse -File -Force
    foreach ($file in $files) {
        $relative = $file.FullName.Substring($Root.Length).TrimStart('\')
        if (Test-ExcludedPath $relative) { continue }
        $target = Join-Path $Destination $relative
        $parent = Split-Path -Parent $target
        if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
            New-Item -ItemType Directory -Path $parent -Force | Out-Null
        }
        Copy-Item -LiteralPath $file.FullName -Destination $target -Force
        $copied++
    }
    return $copied
}

function Apply-ProgramFiles([string]$SourceRoot) {
    $manifest = @()
    $files = Get-ChildItem -LiteralPath $SourceRoot -Recurse -File -Force
    foreach ($file in $files) {
        $relative = $file.FullName.Substring($SourceRoot.Length).TrimStart('\')
        if (Test-ExcludedPath $relative) {
            Write-Host ('  keep local: ' + $relative)
            continue
        }
        $target = Join-Path $Root $relative
        $existed = Test-Path -LiteralPath $target -PathType Leaf
        $backupPath = Join-Path $ProgramBackup $relative
        $parent = Split-Path -Parent $target
        if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
            New-Item -ItemType Directory -Path $parent -Force | Out-Null
        }
        Copy-Item -LiteralPath $file.FullName -Destination $target -Force
        $manifest += [pscustomobject]@{ Target = $target; Existed = $existed; BackupPath = $backupPath }
        Write-Host ('  replaced: ' + $relative)
    }
    return $manifest
}

function Restore-ProgramFiles($Manifest) {
    $restored = 0
    for ($index = $Manifest.Count - 1; $index -ge 0; $index--) {
        $item = $Manifest[$index]
        if ($item.Existed) {
            if (Test-Path -LiteralPath $item.BackupPath -PathType Leaf) {
                Copy-Item -LiteralPath $item.BackupPath -Destination $item.Target -Force
                $restored++
            }
        } elseif (Test-Path -LiteralPath $item.Target -PathType Leaf) {
            Remove-Item -LiteralPath $item.Target -Force -ErrorAction SilentlyContinue
        }
    }
    Write-Host ('  rolled back ' + $restored + ' replaced file(s).') -ForegroundColor Yellow
}

function Update-Dependencies([string]$OldHash) {
    if ($SkipDependencies) { Write-Host '  skipped (-SkipDependencies).'; return $true }
    $newHash = Get-RequirementsHash
    if ($newHash -and $newHash -eq $OldHash) {
        Write-Host '  requirements.lock.txt unchanged; no pip install needed.'
        return $true
    }
    Write-Host '  requirements.lock.txt changed; installing pinned dependencies...'
    $pythonExe = Resolve-Python
    Push-Location -LiteralPath $Root
    try {
        & $pythonExe -m pip install -r requirements.lock.txt --disable-pip-version-check
        $code = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    if ($code -ne 0) { Write-Host '  pip install failed.'; return $false }
    Write-Host '  dependencies installed.' -ForegroundColor Green
    return $true
}

function Restart-Application {
    if ($NoRestart) { Write-Host '  restart skipped (-NoRestart).'; return }
    $launcher = Join-Path $Root ([string]([char]0x52D8) + [string]([char]0x8BFB) + '.exe')
    if (Test-Path -LiteralPath $launcher -PathType Leaf) {
        Start-Process -FilePath $launcher -WorkingDirectory $Root | Out-Null
        Write-Host ('  started: ' + $launcher)
        return
    }
    $batch = Join-Path $Root 'start.bat'
    if (Test-Path -LiteralPath $batch -PathType Leaf) {
        Start-Process -FilePath $batch -WorkingDirectory $Root | Out-Null
        Write-Host ('  started: ' + $batch)
        return
    }
    Write-Host '  neither the KanRead launcher nor start.bat was found; start the app manually.'
}

# ------------------------------------------------------------------ 1. validation
Write-Host 'KanRead updater'
Write-Host '==============='

if (-not $Root) { $Root = Split-Path -Parent $PSScriptRoot }
if (-not $Root) { Fail 'Cannot determine the program directory; pass -Root.' 2 }
$Root = [System.IO.Path]::GetFullPath($Root)
if ($Git -and $Zip) { Fail 'Use either -Zip or -Git, not both.' 2 }
if (-not $Git -and -not $Zip) { Fail 'Choose an update source: -Zip <path> or -Git.' 2 }

Write-Step '1/7' 'validate program directory and update source'
Write-Host ('  program directory: ' + $Root)
foreach ($needed in @('app\main.py', 'static\index.html', 'requirements.lock.txt')) {
    if (-not (Test-Path -LiteralPath (Join-Path $Root $needed) -PathType Leaf)) {
        Fail ('Not a KanRead program directory (missing ' + $needed + '): ' + $Root) 2
    }
}

$Stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$PackagePrefix = ''
$PackageApiVersion = 'unknown'
$RequirementsChanged = $true
$ContentRoot = ''
$ArchiveName = ''

if ($Zip) {
    if (-not (Test-Path -LiteralPath $Zip -PathType Leaf)) { Fail ('Update package not found: ' + $Zip) 2 }
    $archivePath = [System.IO.Path]::GetFullPath($Zip)
    $ArchiveName = Split-Path -Leaf $archivePath
    if ((Get-Item -LiteralPath $archivePath).Length -le 0) { Fail 'Update package is empty (0 bytes).' 2 }
    $entries = Get-ZipEntries $archivePath
    if (-not $entries -or $entries.Count -eq 0) { Fail 'Update package contains no files.' 2 }
    $package = Resolve-PackageRoot $entries
    if ($package.Missing.Count -gt 0) {
        Fail ('Update package is incomplete; missing: ' + ($package.Missing -join ', ')) 2
    }
    $PackagePrefix = $package.Prefix
    Write-Host ('  package root inside zip: "' + $PackagePrefix + '"')
    $PackageApiVersion = Read-VersionFromText (Read-ZipEntryText $archivePath ($PackagePrefix + 'app/version.py'))
    $currentRequirements = ''
    $requirementsPath = Join-Path $Root 'requirements.lock.txt'
    if (Test-Path -LiteralPath $requirementsPath -PathType Leaf) {
        $currentRequirements = [System.IO.File]::ReadAllText($requirementsPath, [System.Text.Encoding]::UTF8)
    }
    $packageRequirements = Read-ZipEntryText $archivePath ($PackagePrefix + 'requirements.lock.txt')
    if ($null -ne $packageRequirements) {
        $RequirementsChanged = (($currentRequirements -replace "`r`n", "`n") -ne ($packageRequirements -replace "`r`n", "`n"))
    }
} else {
    $inside = Invoke-Git @('rev-parse', '--is-inside-work-tree')
    if ($inside.Code -ne 0 -or $inside.Output.Trim() -ne 'true') {
        Fail 'This program directory is not a git checkout; -Git is unavailable. Use -Zip instead.' 2
    }
    $branch = Invoke-Git @('rev-parse', '--abbrev-ref', 'HEAD')
    if ($branch.Code -ne 0 -or $branch.Output.Trim() -eq 'HEAD') {
        Fail 'Detached HEAD is not supported; check out a branch first.' 2
    }
    $upstream = Invoke-Git @('rev-parse', '--abbrev-ref', '@{u}')
    if ($upstream.Code -ne 0) {
        Fail ('No upstream branch for "' + $branch.Output.Trim() + '". Set one with: git branch --set-upstream-to=<remote>/<branch>') 2
    }
    Write-Host ('  branch: ' + $branch.Output.Trim() + ' -> ' + $upstream.Output.Trim())
}

Write-Host ('  current API version: ' + (Read-CurrentApiVersion))
if ($Zip) { Write-Host ('  package API version: ' + $PackageApiVersion) }
Write-Host ('  dependencies change: ' + $(if ($RequirementsChanged) { 'yes' } else { 'no' }))

# ------------------------------------------------------------------ 2. dry run
if ($DryRun) {
    Write-Host ''
    Write-Host 'DRY RUN: no files were changed and no backup was made.' -ForegroundColor Yellow
    Write-Host ('Would first back up data/ to: ' + (Join-Path $Root 'data\backups'))
    Write-Host ('Would then snapshot program files to: ' + (Join-Path $Root ('_update-backup\program-' + $Stamp)))
    if ($Zip) {
        Write-Host ('Would extract package "' + $ArchiveName + '" into a temporary folder and copy program files from prefix "' + $PackagePrefix + '".')
    } else {
        $fetch = Invoke-Git @('fetch', '--all', '--prune', '--tags')
        if ($fetch.Code -ne 0) { Fail ('git fetch failed: ' + $fetch.Output) 1 }
        $oldHead = (Invoke-Git @('rev-parse', 'HEAD')).Output.Trim()
        $newHead = (Invoke-Git @('rev-parse', '@{u}')).Output.Trim()
        Write-Host ('Would run: git reset --hard ' + $newHead + ' (currently ' + $oldHead + ')')
        $changes = Invoke-Git @('diff', '--name-only', 'HEAD', '@{u}')
        if ($changes.Output.Trim()) { Write-Host ('Pending changes:' + "`n" + $changes.Output) }
        else { Write-Host 'Already up to date: no pending changes.' }
    }
    Write-Host 'Never replaced: data, .venv, .git, __pycache__, *.log, diagnostics, backups.'
    if ($SkipDependencies) { Write-Host 'Dependencies: would be skipped (-SkipDependencies).' }
    elseif ($RequirementsChanged) { Write-Host 'Dependencies: would run pip install -r requirements.lock.txt.' }
    else { Write-Host 'Dependencies: no reinstall needed.' }
    if ($NoRestart) { Write-Host 'Restart: would be skipped (-NoRestart).' }
    else { Write-Host 'Restart: would relaunch the KanRead launcher (or start.bat).' }
    Write-Host ''
    Write-Host 'Dry run finished; nothing changed.' -ForegroundColor Green
    exit 0
}

# ------------------------------------------------------------------ 3. stop server
Write-Step '2/7' 'stop the running background server'
if (-not (Stop-Background)) { Fail 'Could not stop the running server; close KanRead and try again.' 1 }

# ------------------------------------------------------------------ 4. back up data
Write-Step '3/7' 'back up data/ (mandatory)'
$DataBackupPath = Invoke-DataBackup $Stamp
Write-Host ('  data/ is safe at: ' + $DataBackupPath)

# ------------------------------------------------------------------ 5. program snapshot
Write-Step '4/7' 'snapshot current program files for rollback'
$ProgramBackup = Join-Path $Root ('_update-backup\program-' + $Stamp)
$snapshotCount = Backup-ProgramFiles $ProgramBackup
Write-Host ('  saved ' + $snapshotCount + ' file(s) to: ' + $ProgramBackup)

# ------------------------------------------------------------------ 6. apply
Write-Step '5/7' 'replace program files'
$OldRequirementsHash = Get-RequirementsHash
$oldHead = ''
if ($Git) {
    $oldHead = (Invoke-Git @('rev-parse', 'HEAD')).Output.Trim()
    $fetch = Invoke-Git @('fetch', '--all', '--prune', '--tags')
    if ($fetch.Code -ne 0) { Fail ('git fetch failed: ' + $fetch.Output) 1 }
    $newHead = (Invoke-Git @('rev-parse', '@{u}')).Output.Trim()
    Write-Host ('  git reset --hard ' + $newHead)
    $reset = Invoke-Git @('reset', '--hard', $newHead)
    if ($reset.Code -ne 0) { Fail ('git reset failed: ' + $reset.Output) 1 }
    Write-Host '  source checkout updated.'
} else {
    $Staging = Join-Path $Root ('_update-tmp-' + $Stamp)
    New-Item -ItemType Directory -Path $Staging -Force | Out-Null
    try {
        Write-Host ('  extracting ' + $ArchiveName)
        Expand-Archive -LiteralPath $archivePath -DestinationPath $Staging -Force
        $ContentRoot = Join-Path $Staging (($PackagePrefix -replace '/', '\').TrimEnd('\'))
        if (-not (Test-Path -LiteralPath $ContentRoot -PathType Container)) { Fail 'Extracted package content not found.' 1 }
        foreach ($required in $RequiredPackageEntries) {
            if (-not (Test-Path -LiteralPath (Join-Path $ContentRoot $required) -PathType Leaf)) {
                Fail ('Extracted package is missing ' + $required) 1
            }
        }
        $Manifest = @(Apply-ProgramFiles $ContentRoot)
        Write-Host ('  replaced ' + $Manifest.Count + ' file(s).')
        $dependenciesOk = Update-Dependencies $OldRequirementsHash
        if (-not $dependenciesOk) {
            Write-Host 'Dependency installation failed; rolling back program files.' -ForegroundColor Red
            Restore-ProgramFiles $Manifest
            Fail 'Update aborted and rolled back. data/ is unchanged; see the backup above.' 1
        }
    } catch {
        Write-Host ('Update failed: ' + $_) -ForegroundColor Red
        if ($Manifest -and $Manifest.Count -gt 0) { Restore-ProgramFiles $Manifest }
        Fail 'Update aborted and rolled back. data/ is unchanged; see the backup above.' 1
    } finally {
        if (Test-Path -LiteralPath $Staging) { Remove-Item -LiteralPath $Staging -Recurse -Force -ErrorAction SilentlyContinue }
    }
}

if ($Git) {
    $dependenciesOk = Update-Dependencies $OldRequirementsHash
    if (-not $dependenciesOk) {
        Write-Host 'Dependency installation failed; rolling the checkout back.' -ForegroundColor Red
        $rollback = Invoke-Git @('reset', '--hard', $oldHead)
        if ($rollback.Code -eq 0) { Write-Host ('  rolled back to ' + $oldHead) -ForegroundColor Yellow }
        else { Write-Host ('  rollback failed; run manually: git reset --hard ' + $oldHead) -ForegroundColor Red }
        Fail 'Update aborted. data/ is unchanged; see the backup above.' 1
    }
}

# ------------------------------------------------------------------ 7. restart
Write-Step '6/7' 'restart KanRead'
Restart-Application
Write-Step '7/7' 'done'
Write-Host ('Data backup: ' + $DataBackupPath) -ForegroundColor Green
Write-Host ('Program snapshot (kept for manual rollback): ' + $ProgramBackup)
exit 0
