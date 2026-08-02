<#
.SYNOPSIS
    Installiert alle Voraussetzungen (JDK 17, Android SDK Command-Line Tools, Gradle)
    portabel unterhalb dieses Ordners und baut anschliessend die FPV Gamification APK.

.PARAMETER Release
    Baut ein Release-APK (assembleRelease) statt Debug (assembleDebug).

.PARAMETER SkipSdkInstall
    Ueberspringt den Android-SDK-Download/-Install (falls ANDROID_HOME bereits
    vollstaendig eingerichtet ist).

.EXAMPLE
    .\setup_and_build.ps1
    .\setup_and_build.ps1 -Release
#>

[CmdletBinding()]
param(
    [switch]$Release,
    [switch]$SkipSdkInstall
)

$ErrorActionPreference = "Stop"
# Default-Fortschrittsbalken von Invoke-WebRequest/Expand-Archive rendert in manchen
# Terminals (z.B. VS Code) extrem langsam und wirkt dabei wie eingefroren -> aus.
$ProgressPreference = "SilentlyContinue"

$root = $PSScriptRoot
$sdkRoot = Join-Path $root ".android-sdk"
$gradleRoot = Join-Path $root ".gradle-dist"
$gradleVersion = "8.9"

# Offizieller "latest"-Permalink der Android Command-Line Tools (Windows).
$cmdlineToolsUrl = "https://dl.google.com/android/repository/commandlinetools-win-11076708_latest.zip"
$gradleUrl = "https://services.gradle.org/distributions/gradle-$gradleVersion-bin.zip"

function Write-Step($msg) {
    Write-Host ""
    Write-Host ">> $msg" -ForegroundColor Cyan
}

function Test-Command($name) {
    return [bool](Get-Command $name -ErrorAction SilentlyContinue)
}

function Get-RemoteFile($url, $outFile, $label) {
    Write-Host "   Lade $label herunter ..." -ForegroundColor DarkGray
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    Invoke-WebRequest -Uri $url -OutFile $outFile
    $sw.Stop()
    $sizeMb = [math]::Round((Get-Item $outFile).Length / 1MB, 1)
    Write-Host "   -> $label fertig ($sizeMb MB in $([math]::Round($sw.Elapsed.TotalSeconds, 1))s)" -ForegroundColor DarkGray
}

# Sucht ein JDK 17+ direkt auf der Platte statt sich auf PATH/JAVA_HOME des
# aktuellen Prozesses zu verlassen: VS-Code-Terminals (und dieses Skript, wenn es
# aus einer aelteren Shell heraus laeuft) erben oft eine veraltete Umgebung, auch
# in einem frisch geoeffneten Tab - ein frisch von winget installiertes JDK
# taucht dort erst nach einem kompletten Neustart des uebergeordneten Prozesses auf.
function Find-JdkHome {
    $candidates = New-Object System.Collections.Generic.List[string]
    if ($env:JAVA_HOME) { $candidates.Add($env:JAVA_HOME) }
    foreach ($scope in @("Machine", "User")) {
        $value = [Environment]::GetEnvironmentVariable("JAVA_HOME", $scope)
        if ($value) { $candidates.Add($value) }
    }

    $searchRoots = @(
        "C:\Program Files\Eclipse Adoptium",
        "C:\Program Files\Java",
        "C:\Program Files\Microsoft",
        "C:\Program Files\Zulu",
        "C:\Program Files\BellSoft"
    )
    foreach ($searchRoot in $searchRoots) {
        if (Test-Path $searchRoot) {
            Get-ChildItem $searchRoot -Directory -ErrorAction SilentlyContinue |
                ForEach-Object { $candidates.Add($_.FullName) }
        }
    }

    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        $javaExe = Join-Path $candidate "bin\java.exe"
        if (Test-Path $javaExe) {
            $verOutput = (& $javaExe -version 2>&1) -join "`n"
            if ($verOutput -match 'version "(\d+)' -and [int]$Matches[1] -ge 17) {
                return $candidate
            }
        }
    }
    return $null
}

# ---------------------------------------------------------------------------
# 1. JDK 17+ pruefen (Android Gradle Plugin 8.x benoetigt mindestens JDK 17)
# ---------------------------------------------------------------------------
Write-Step "Pruefe Java (JDK 17+) ..."
$jdkHome = Find-JdkHome

if (-not $jdkHome) {
    Write-Host "Kein passendes JDK (17+) gefunden." -ForegroundColor Yellow
    if (Test-Command "winget") {
        Write-Step "Installiere Eclipse Temurin JDK 17 via winget ..."
        winget install --id EclipseAdoptium.Temurin.17.JDK -e --accept-package-agreements --accept-source-agreements
        $jdkHome = Find-JdkHome
        if (-not $jdkHome) {
            throw "JDK 17 wurde installiert, konnte aber nicht automatisch gefunden werden (unerwarteter Installationsort). Bitte VS Code komplett schliessen, neu oeffnen und das Skript erneut starten."
        }
    } else {
        throw "Bitte JDK 17 oder neuer installieren (z.B. https://adoptium.net/) und dieses Skript erneut starten."
    }
}

# Fuer diesen Prozess (und alle davon gestarteten Kindprozesse wie sdkmanager/gradle)
# JAVA_HOME/PATH explizit setzen - unabhaengig davon, was die umgebende Shell hat.
$env:JAVA_HOME = $jdkHome
$env:Path = "$(Join-Path $jdkHome 'bin');$env:Path"
Write-Host "   Verwende JDK: $jdkHome" -ForegroundColor DarkGray

# ---------------------------------------------------------------------------
# 2. Android SDK Command-Line Tools (portabel unter .android-sdk)
# ---------------------------------------------------------------------------
$sdkManager = Join-Path $sdkRoot "cmdline-tools\latest\bin\sdkmanager.bat"

if (-not $SkipSdkInstall) {
    if (-not (Test-Path $sdkManager)) {
        Write-Step "Android SDK Command-Line Tools ..."
        New-Item -ItemType Directory -Force -Path $sdkRoot | Out-Null
        $zipPath = Join-Path $env:TEMP "fpv-cmdline-tools.zip"
        Get-RemoteFile $cmdlineToolsUrl $zipPath "Android Command-Line Tools (~150 MB)"

        $extractTmp = Join-Path $env:TEMP "fpv-cmdline-tools-extract"
        if (Test-Path $extractTmp) { Remove-Item -Recurse -Force $extractTmp }
        Write-Host "   Entpacke ..." -ForegroundColor DarkGray
        Expand-Archive -Path $zipPath -DestinationPath $extractTmp -Force

        New-Item -ItemType Directory -Force -Path (Join-Path $sdkRoot "cmdline-tools") | Out-Null
        $latestDir = Join-Path $sdkRoot "cmdline-tools\latest"
        if (Test-Path $latestDir) { Remove-Item -Recurse -Force $latestDir }
        Move-Item (Join-Path $extractTmp "cmdline-tools") $latestDir -Force

        Remove-Item $zipPath -Force
        Remove-Item -Recurse -Force $extractTmp -ErrorAction SilentlyContinue
    }

    $env:ANDROID_HOME = $sdkRoot
    $env:ANDROID_SDK_ROOT = $sdkRoot

    Write-Step "Akzeptiere SDK-Lizenzen (JVM-Start kann kurz dauern) ..."
    $licenseAnswers = (1..20 | ForEach-Object { "y" }) -join "`n"
    $licenseAnswers | & $sdkManager --licenses --sdk_root=$sdkRoot | ForEach-Object { Write-Host "   $_" -ForegroundColor DarkGray }

    Write-Step "Installiere Platform-Tools, Android 34 Platform & Build-Tools (Download laeuft) ..."
    & $sdkManager --sdk_root=$sdkRoot "platform-tools" "platforms;android-34" "build-tools;34.0.0" |
        ForEach-Object { Write-Host "   $_" -ForegroundColor DarkGray }
} elseif (-not (Test-Path $sdkManager) -and -not $env:ANDROID_HOME) {
    throw "-SkipSdkInstall gesetzt, aber weder $sdkManager noch ANDROID_HOME gefunden."
}

$effectiveSdkRoot = if (Test-Path $sdkManager) { $sdkRoot } else { $env:ANDROID_HOME }

# ---------------------------------------------------------------------------
# 3. local.properties schreiben
# ---------------------------------------------------------------------------
$sdkPathForward = $effectiveSdkRoot -replace '\\', '/'
Set-Content -Path (Join-Path $root "local.properties") -Value "sdk.dir=$sdkPathForward" -Encoding ASCII

# ---------------------------------------------------------------------------
# 4. Portables Gradle (kein Wrapper-Jar noetig)
# ---------------------------------------------------------------------------
$gradleExe = Join-Path $gradleRoot "gradle-$gradleVersion\bin\gradle.bat"
if (-not (Test-Path $gradleExe)) {
    Write-Step "Gradle $gradleVersion ..."
    New-Item -ItemType Directory -Force -Path $gradleRoot | Out-Null
    $gradleZip = Join-Path $env:TEMP "fpv-gradle-$gradleVersion-bin.zip"
    Get-RemoteFile $gradleUrl $gradleZip "Gradle $gradleVersion (~130 MB)"
    Write-Host "   Entpacke ..." -ForegroundColor DarkGray
    Expand-Archive -Path $gradleZip -DestinationPath $gradleRoot -Force
    Remove-Item $gradleZip -Force
}

# ---------------------------------------------------------------------------
# 5. Build
# ---------------------------------------------------------------------------
$task = if ($Release) { "assembleRelease" } else { "assembleDebug" }
Write-Step "Baue APK ($task) - erster Lauf laedt Gradle-Abhaengigkeiten nach, das dauert ein paar Minuten ..."

Push-Location $root
try {
    & $gradleExe $task --console=plain
    if ($LASTEXITCODE -ne 0) {
        throw "Gradle-Build fehlgeschlagen (Exit code $LASTEXITCODE)."
    }
} finally {
    Pop-Location
}

$apkDir = if ($Release) { "app\build\outputs\apk\release" } else { "app\build\outputs\apk\debug" }
$apk = Get-ChildItem (Join-Path $root $apkDir) -Filter "*.apk" -ErrorAction SilentlyContinue |
    Select-Object -First 1

if ($apk) {
    Write-Host ""
    Write-Host "Fertig! APK: $($apk.FullName)" -ForegroundColor Green
    Write-Host "Zum Installieren auf einem angeschlossenen Handy: .\install_apk.ps1" -ForegroundColor Green
} else {
    Write-Host "Build abgeschlossen, aber keine APK unter $apkDir gefunden." -ForegroundColor Yellow
}
