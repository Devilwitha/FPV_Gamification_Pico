<#
  TX15 Werkstatt – ESC Configurator und Betaflight Configurator bauen

  Lädt den Quellcode der beiden Open-Source-Projekte von GitHub, baut daraus die
  Web-Version und legt sie unverändert ab:

    apps/esc-configurator/   https://github.com/stylesuxx/esc-configurator       (AGPL-3.0)
    apps/betaflight/         https://github.com/betaflight/betaflight-configurator (GPL-3.0)

  Voraussetzungen: Git und Node.js (ab Version 22) müssen installiert sein.
  Der erste Lauf dauert einige Minuten. Danach mit start-server.ps1 starten
  oder den Projektordner auf den Webspace hochladen.

  Beispiele:
    .\tools\build-webapps.ps1                 # beide bauen
    .\tools\build-webapps.ps1 -Only esc       # nur ESC Configurator
    .\tools\build-webapps.ps1 -Only betaflight
    .\tools\build-webapps.ps1 -Ref 11.0.0     # bestimmten Git-Tag/Branch bauen (bei -Only)
#>
param(
  [ValidateSet('all', 'esc', 'betaflight')][string]$Only = 'all',
  [string]$Ref
)

$ErrorActionPreference = 'Stop'
$project = Split-Path $PSScriptRoot -Parent
$work = Join-Path $env:TEMP 'tx15-werkstatt-build'   # Quellcode und node_modules bleiben außerhalb des Projekts
New-Item -ItemType Directory -Force $work | Out-Null

foreach ($cmd in 'git', 'node', 'npm', 'npx') {
  if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) { throw "$cmd fehlt. Bitte Git und Node.js installieren." }
}

function Get-Source($name, $repo, $ref) {
  $dir = Join-Path $work $name
  if (Test-Path (Join-Path $dir '.git')) {
    Write-Host "Aktualisiere $name ..."
    git -C $dir fetch --depth 1 origin ($(if ($ref) { $ref } else { 'HEAD' })) | Out-Null
    git -C $dir reset --hard FETCH_HEAD | Out-Null
  } else {
    Write-Host "Lade Quellcode von $repo ..."
    if ($ref) { git clone --depth 1 --branch $ref $repo $dir } else { git clone --depth 1 $repo $dir }
  }
  return $dir
}

function Publish-App($name, $from, $src, $repo, $license) {
  $dest = Join-Path $project "apps/$name"
  if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
  New-Item -ItemType Directory -Force $dest | Out-Null
  Copy-Item -Recurse -Force (Join-Path $from '*') $dest
  Copy-Item (Join-Path $src 'LICENSE') (Join-Path $dest 'LICENSE.txt')
  $commit = (git -C $src rev-parse HEAD).Trim()
  @"
$name – unverändert gebaut aus dem offiziellen Quellcode.
Quellcode: $repo
Commit:    $commit
Lizenz:    $license (siehe LICENSE.txt)
Gebaut am: $(Get-Date -Format 'yyyy-MM-dd HH:mm')
"@ | Set-Content -Encoding utf8 (Join-Path $dest 'SOURCE.txt')
  $n = (Get-ChildItem $dest -Recurse -File | Measure-Object).Count
  Write-Host "Fertig: apps/$name ($n Dateien, Commit $($commit.Substring(0, 8)))"
}

if ($Only -in 'all', 'esc') {
  $repo = 'https://github.com/stylesuxx/esc-configurator.git'
  $src = Get-Source 'esc-configurator' $repo $(if ($Only -eq 'esc') { $Ref })
  Push-Location $src
  try {
    # Das Projekt nutzt react-scripts 5 (Webpack 4-Hashes) und Yarn 1.
    # --ignore-scripts überspringt das native Test-Modul 'sleep', das mit neuem Node nicht kompiliert.
    $env:NODE_OPTIONS = '--openssl-legacy-provider'
    $env:PUBLIC_URL = '.'
    $env:GENERATE_SOURCEMAP = 'false'
    $env:CI = 'false'
    $env:YARN_IGNORE_ENGINES = 'true'   # Projekt verlangt Node < 19, baut aber auch mit neuerem Node
    Write-Host 'Installiere Abhängigkeiten (ESC Configurator) ...'
    npx --yes yarn@1.22.22 install --ignore-engines --ignore-scripts --frozen-lockfile --network-timeout 600000
    if ($LASTEXITCODE) { throw 'Installation fehlgeschlagen.' }
    Write-Host 'Baue ESC Configurator ...'
    npx --yes yarn@1.22.22 build
    if ($LASTEXITCODE) { throw 'Build fehlgeschlagen.' }
  } finally {
    Remove-Item Env:NODE_OPTIONS, Env:PUBLIC_URL, Env:GENERATE_SOURCEMAP, Env:CI, Env:YARN_IGNORE_ENGINES -ErrorAction SilentlyContinue
    Pop-Location
  }
  Publish-App 'esc-configurator' (Join-Path $src 'build') $src $repo 'AGPL-3.0'
}

if ($Only -in 'all', 'betaflight') {
  $repo = 'https://github.com/betaflight/betaflight-configurator.git'
  $src = Get-Source 'betaflight' $repo $(if ($Only -eq 'betaflight') { $Ref })
  Push-Location $src
  try {
    Write-Host 'Installiere Abhängigkeiten (Betaflight) ...'
    npm ci --engine-strict=false --no-audit --no-fund
    if ($LASTEXITCODE) { throw 'Installation fehlgeschlagen.' }
    Write-Host 'Baue Betaflight Configurator ...'
    npm run build
    if ($LASTEXITCODE) { throw 'Build fehlgeschlagen.' }
  } finally { Pop-Location }
  # Vite baut hier nach src/dist (root = src)
  $out = @('src/dist', 'dist') | ForEach-Object { Join-Path $src $_ } | Where-Object { Test-Path (Join-Path $_ 'index.html') } | Select-Object -First 1
  if (-not $out) { throw 'Build-Ausgabe von Betaflight nicht gefunden.' }
  Publish-App 'betaflight' $out $src $repo 'GPL-3.0'
}

Write-Host ''
Write-Host 'Alles gebaut. Start: .\start-server.ps1  ->  http://localhost:8080/'
