<#
.SYNOPSIS
    Installiert die gebaute FPV Gamification APK per adb auf einem angeschlossenen
    Android-Geraet (USB-Debugging) und startet die App.

.PARAMETER Apk
    Pfad zu einer bestimmten APK. Standard: neueste APK unter app\build\outputs\apk.

.PARAMETER ConnectWifi
    Optional: "<ip>:<port>" fuer WLAN-ADB (z.B. nach "adb tcpip 5555" am Handy),
    falls kein USB-Kabel verfuegbar ist.

.PARAMETER Uninstall
    Deinstalliert eine vorhandene Version vor der Neuinstallation.

.PARAMETER NoLaunch
    App nach der Installation NICHT automatisch starten.

.EXAMPLE
    .\install_apk.ps1
    .\install_apk.ps1 -ConnectWifi 192.168.1.42:5555
#>

[CmdletBinding()]
param(
    [string]$Apk,
    [string]$ConnectWifi,
    [switch]$Uninstall,
    [switch]$NoLaunch
)

$ErrorActionPreference = "Stop"

$root = $PSScriptRoot
$appId = "com.fpv.gamification.app"

function Write-Step($msg) {
    Write-Host ""
    Write-Host ">> $msg" -ForegroundColor Cyan
}

function Find-Adb {
    $bundled = Join-Path $root ".android-sdk\platform-tools\adb.exe"
    if (Test-Path $bundled) { return $bundled }

    $cmd = Get-Command adb -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }

    throw "adb wurde nicht gefunden. Fuehre zuerst .\setup_and_build.ps1 aus oder installiere die Android Platform-Tools."
}

$adb = Find-Adb

if ($ConnectWifi) {
    Write-Step "Verbinde per WLAN-ADB mit $ConnectWifi ..."
    & $adb connect $ConnectWifi
}

if (-not $Apk) {
    $searchDir = Join-Path $root "app\build\outputs\apk"
    $candidates = Get-ChildItem $searchDir -Recurse -Filter "*.apk" -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending
    if (-not $candidates) {
        throw "Keine APK unter $searchDir gefunden. Fuehre zuerst .\setup_and_build.ps1 aus oder gib -Apk <Pfad> an."
    }
    $Apk = $candidates[0].FullName
}

Write-Step "Suche verbundene Geraete ..."
$devicesOutput = & $adb devices
$deviceLines = $devicesOutput | Select-Object -Skip 1 | Where-Object { $_.Trim() -ne "" }
$ready = $deviceLines | Where-Object { $_ -match "\tdevice$" }

if (-not $ready) {
    $devicesOutput | ForEach-Object { Write-Host $_ }
    throw "Kein einsatzbereites Geraet gefunden. USB-Debugging aktivieren, Kabel pruefen und den " +
          "RSA-Fingerprint-Dialog auf dem Handy bestaetigen (oder -ConnectWifi <ip>:<port> fuer WLAN-ADB nutzen)."
}

if ($Uninstall) {
    Write-Step "Deinstalliere vorhandene Version ($appId) ..."
    & $adb uninstall $appId | Out-Null
}

Write-Step "Installiere $Apk ..."
& $adb install -r $Apk
if ($LASTEXITCODE -ne 0) {
    throw "adb install fehlgeschlagen (Exit code $LASTEXITCODE)."
}

if (-not $NoLaunch) {
    Write-Step "Starte App auf dem Geraet ..."
    & $adb shell am start -n "$appId/$appId.MainActivity" | Out-Null
}

Write-Host ""
Write-Host "Fertig! App installiert." -ForegroundColor Green
