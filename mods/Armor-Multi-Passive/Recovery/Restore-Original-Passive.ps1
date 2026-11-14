param([string]$GamePath)

# 还原游戏生成的原版被动文件。
# 原版基准不用安装包里带的副本（游戏每次更新都会重新生成，带的那份很快过期），
# 而是用模组首次运行时自动认领的 ArmoryForge-Multi.original.dl_bin。

$ErrorActionPreference = "Stop"
$BackupName = "ArmoryForge-Multi.original.dl_bin"
$Relative = "data\game\generated_customization_passive_bonuses.dl_bin"

if (Get-Process -Name helldivers2 -ErrorAction SilentlyContinue) {
    throw "请先关闭 Helldivers 2，再还原被动资源。"
}

function Test-GamePath([string]$p) {
    if ([string]::IsNullOrWhiteSpace($p)) { return $false }
    return (Test-Path -LiteralPath (Join-Path $p "bin\helldivers2.exe")) -and
           (Test-Path -LiteralPath (Join-Path $p $Relative))
}

if (-not (Test-GamePath $GamePath)) {
    $candidates = @()
    foreach ($k in @(
        @{ Path = "HKCU:\Software\Valve\Steam"; Name = "SteamPath" },
        @{ Path = "HKLM:\SOFTWARE\WOW6432Node\Valve\Steam"; Name = "InstallPath" },
        @{ Path = "HKLM:\SOFTWARE\Valve\Steam"; Name = "InstallPath" }
    )) {
        try {
            $v = (Get-ItemProperty -Path $k.Path -Name $k.Name -ErrorAction Stop).($k.Name)
            if ($v) { $candidates += (Join-Path $v "steamapps\common\Helldivers 2") }
        } catch { }
    }
    foreach ($d in @("C","D","E","F","G")) {
        $candidates += "${d}:\Steam\steamapps\common\Helldivers 2"
        $candidates += "${d}:\SteamLibrary\steamapps\common\Helldivers 2"
        $candidates += "${d}:\Program Files (x86)\Steam\steamapps\common\Helldivers 2"
    }
    $GamePath = $candidates | Where-Object { Test-GamePath $_ } | Select-Object -First 1
}

if (-not (Test-GamePath $GamePath)) {
    $GamePath = Read-Host "找不到游戏目录，请粘贴 Helldivers 2 文件夹路径（含 bin 和 data）"
}
if (-not (Test-GamePath $GamePath)) { throw "游戏目录无效：$GamePath" }

$target = Join-Path $GamePath $Relative
$source = Join-Path $GamePath $BackupName

if (-not (Test-Path -LiteralPath $source)) {
    Write-Host "没有找到 $BackupName。" -ForegroundColor Yellow
    Write-Host "这份备份由模组首次运行时生成；没有它说明模组还没写过文件，游戏目录里现在就是原版，无需还原。" -ForegroundColor Yellow
    return
}

$sourceHash = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash
$currentHash = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash
if ($currentHash -eq $sourceHash) {
    Write-Host "当前已经是原版被动资源，无需还原。" -ForegroundColor Green
    return
}

$backupDir = Join-Path $GamePath "ModBackups\ArmorMulti"
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$safety = Join-Path $backupDir ("generated_customization_passive_bonuses.$stamp.dl_bin")
Copy-Item -LiteralPath $target -Destination $safety -Force

Copy-Item -LiteralPath $source -Destination $target -Force
$newHash = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash
if ($newHash -ne $sourceHash) { throw "还原后校验失败：$newHash" }

Write-Host "已还原原版被动资源。" -ForegroundColor Green
Write-Host "还原前的文件已存到：$safety"
Write-Host "下次进游戏按 F6 可重新选择被动。"
