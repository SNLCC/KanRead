# 校验一个 exe 里是否真的带了图标资源（RT_GROUP_ICON）。
#
# 为什么需要单独检查：csc 的 /win32icon 在某些情况下只是**警告**，编译仍返回 0，
# 于是会产出一个没有图标的 exe——而"双击启动器发现图标是空白页"这种问题只有用户会看见。
#
# 用法：powershell -NoProfile -ExecutionPolicy Bypass -File scripts/check-exe-icon.ps1 -Path 勘读.exe
param(
    [Parameter(Mandatory = $true)][string]$Path
)

$ErrorActionPreference = 'Stop'
$full = (Resolve-Path -LiteralPath $Path).Path
$bytes = [System.IO.File]::ReadAllBytes($full)

# 1) 必须是 PE 文件
if ($bytes.Length -lt 0x40 -or $bytes[0] -ne 0x4D -or $bytes[1] -ne 0x5A) {   # "MZ"
    throw "Not a PE file: $full"
}
$peOffset = [BitConverter]::ToInt32($bytes, 0x3C)
if ($bytes[$peOffset] -ne 0x50 -or $bytes[$peOffset + 1] -ne 0x45) {           # "PE"
    throw "Not a PE file: $full"
}

# 2) 从可选头里取资源目录（数据目录第 3 项）的 RVA 与大小
$optional = $peOffset + 24
$magic = [BitConverter]::ToUInt16($bytes, $optional)
$directories = if ($magic -eq 0x20B) { $optional + 112 } else { $optional + 96 }   # PE32+ / PE32
$resourceRva = [BitConverter]::ToInt32($bytes, $directories + 16)                  # index 2 = 资源表
$resourceSize = [BitConverter]::ToInt32($bytes, $directories + 20)
if ($resourceRva -eq 0 -or $resourceSize -eq 0) { throw "No resource directory in $full" }

# 3) RVA → 文件偏移（遍历节表）
$sections = [BitConverter]::ToUInt16($bytes, $peOffset + 6)
$sectionTable = $optional + [BitConverter]::ToUInt16($bytes, $peOffset + 20)
$offset = -1
for ($index = 0; $index -lt $sections; $index++) {
    $header = $sectionTable + 40 * $index
    $virtualSize = [BitConverter]::ToInt32($bytes, $header + 8)
    $virtualAddress = [BitConverter]::ToInt32($bytes, $header + 12)
    $rawSize = [BitConverter]::ToInt32($bytes, $header + 16)
    $rawPointer = [BitConverter]::ToInt32($bytes, $header + 20)
    if ($resourceRva -ge $virtualAddress -and $resourceRva -lt $virtualAddress + [Math]::Max($virtualSize, $rawSize)) {
        $offset = $rawPointer + ($resourceRva - $virtualAddress)
        break
    }
}
if ($offset -lt 0) { throw "Cannot map resource RVA in $full" }

# 4) 资源目录顶层条目：类型 14（RT_GROUP_ICON）必须存在
$named = [BitConverter]::ToUInt16($bytes, $offset + 12)
$ids = [BitConverter]::ToUInt16($bytes, $offset + 14)
$types = @()
for ($index = 0; $index -lt $named + $ids; $index++) {
    $entry = $offset + 16 + 8 * $index
    $name = [BitConverter]::ToInt32($bytes, $entry)
    if ($name -gt 0) { $types += $name }        # 高位为 0 = 整数 ID
}
if ($types -notcontains 14) { throw "No RT_GROUP_ICON (type 14) in $full" }

Write-Output "OK: $([System.IO.Path]::GetFileName($full)) carries an icon (RT_GROUP_ICON); resource types: $($types -join ', ')"
