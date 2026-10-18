<#
  TX15 Werkstatt – ExpressLRS-Firmware für den ELRS Flasher herunterladen

  Der Firmware-Server von ExpressLRS erlaubt keinen Direktzugriff aus dem Browser.
  Dieses Skript lädt die Firmware deshalb einmal herunter und legt sie so ab,
  wie der Flasher sie erwartet:

    elrs/firmware/index.json            Versionsliste (nur die geladenen Versionen)
    elrs/firmware/hardware/             Gerätebeschreibungen (targets.json, Layouts)
    elrs/firmware/<hash>/FCC|LBT|lua/   Firmware je Version

  Danach den ganzen Projektordner auf den Webspace hochladen
  oder lokal mit start-server.ps1 starten.

  Beispiele:
    .\get-firmware.ps1                       # neueste stabile Version
    .\get-firmware.ps1 -Versions 4.1.0,3.6.4 # bestimmte Versionen
    .\get-firmware.ps1 -Latest 3             # die drei neuesten stabilen Versionen
#>
param(
  [string[]]$Versions,
  [int]$Latest = 1
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # macht Invoke-WebRequest deutlich schneller
$base = 'https://artifactory.expresslrs.org/ExpressLRS'
$root = Join-Path $PSScriptRoot 'firmware'
New-Item -ItemType Directory -Force $root | Out-Null

Write-Host 'Lade Versionsliste ...'
$index = Invoke-RestMethod "$base/index.json"
$tags = @{}
$index.tags.PSObject.Properties | ForEach-Object { $tags[$_.Name] = $_.Value }

function Get-VersionKey([string]$v) {
  ($v -split '\.' | ForEach-Object { '{0:D5}' -f [int]$_ }) -join '.'
}

if (-not $Versions) {
  $Versions = $tags.Keys | Where-Object { $_ -notmatch '-' } |
    Sort-Object { Get-VersionKey $_ } -Descending | Select-Object -First $Latest
}
foreach ($v in $Versions) {
  if (-not $tags.ContainsKey($v)) { throw "Version $v gibt es nicht. Verfügbar: $(($tags.Keys | Sort-Object) -join ', ')" }
}
Write-Host "Versionen: $($Versions -join ', ')"

# Gerätebeschreibungen (immer die aktuellsten, wie beim offiziellen Web-Flasher)
Write-Host 'Lade Gerätebeschreibungen ...'
$hwZip = Join-Path $env:TEMP 'elrs-hardware.zip'
Invoke-WebRequest "$base/hardware.zip" -OutFile $hwZip
$hwDir = Join-Path $root 'hardware'
if (Test-Path $hwDir) { Remove-Item -Recurse -Force $hwDir }
Expand-Archive $hwZip -DestinationPath $hwDir
Remove-Item $hwZip

# Firmware je Version
$keep = [ordered]@{}
foreach ($v in $Versions) {
  $hash = $tags[$v]
  $dest = Join-Path $root $hash
  $keep[$v] = $hash
  if (Test-Path (Join-Path $dest 'FCC')) { Write-Host "$v ist schon vorhanden."; continue }
  Write-Host "Lade Firmware $v (ca. 30 MB) ..."
  $zip = Join-Path $env:TEMP "elrs-$hash.zip"
  $tmp = Join-Path $env:TEMP "elrs-$hash"
  Invoke-WebRequest "$base/$hash/firmware.zip" -OutFile $zip
  if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp }
  Expand-Archive $zip -DestinationPath $tmp
  New-Item -ItemType Directory -Force $dest | Out-Null
  Get-ChildItem (Join-Path $tmp 'firmware') | Where-Object { $_.Name -ne 'hardware' -and $_.Name -ne 'flasher.pyz' } |
    Move-Item -Destination $dest
  Remove-Item -Recurse -Force $tmp, $zip
}

# Bereits früher geladene Versionen in der Liste behalten
$old = Join-Path $root 'index.json'
if (Test-Path $old) {
  (Get-Content $old -Raw | ConvertFrom-Json).tags.PSObject.Properties | ForEach-Object {
    if (-not $keep.Contains($_.Name) -and (Test-Path (Join-Path $root "$($_.Value)/FCC"))) { $keep[$_.Name] = $_.Value }
  }
}
[ordered]@{ tags = $keep; branches = @{} } | ConvertTo-Json -Depth 4 | Set-Content -Encoding utf8 $old

Write-Host ''
Write-Host "Fertig. $($keep.Count) Version(en) in $root"
Write-Host 'Jetzt den Projektordner hochladen oder .\start-server.ps1 starten.'
