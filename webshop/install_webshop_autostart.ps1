<#
  Richtet den gemeinsamen Produktionsstart aller Bollisoft-Server ein.

  1. Aufgabe "Bollisoft Server" in der Aufgabenplanung: startet beim
     Systemstart serve_production.py (Webshop, TX15 Werkstatt, Cover Store,
     Blackbox Viewer).
  2. Die alte Einzelaufgabe "Bollisoft Cover Store" wird beendet und
     deaktiviert, weil der Cover Store jetzt von serve_production.py
     gestartet wird (sonst laufen zwei Instanzen auf Port 9090).
  3. IIS-Site "Bollisoft" (IIS-Manager > URL-Rewrite): Reverse-Proxy-Regeln
     fuer /coverstore-maker, /tx15-werkstatt, /blackbox-viewer und den
     Webshop werden
     angelegt bzw. aktualisiert, damit alles von aussen erreichbar ist.
     Fuer /api/tx15-backups (Backup Studio) wird das Upload-Limit angehoben.

  Ausfuehren in einer PowerShell mit Administratorrechten:
    powershell -ExecutionPolicy Bypass -File install_webshop_autostart.ps1
#>
param(
    [string]$TaskName = 'Bollisoft Server',
    [string]$LegacyCoverStoreTask = 'Bollisoft Cover Store',
    [string]$SiteName = 'Bollisoft',
    [int]$WebshopPort = 5000,
    [int]$CoverStorePort = 9090,
    [int]$BlackboxViewerPort = 8787,
    # Upload-Grenze (Bytes) fuer /api/tx15-backups, etwas ueber TX15_BACKUP_MAX_BYTES.
    [long]$BackupUploadLimit = 300000000,
    # Upload-Grenze (Bytes) fuer Leaderboard-Einreichungen (Video + Log).
    [long]$LeaderboardUploadLimit = 720000000,
    # Upload-Grenze (Bytes) fuer Forenbeitraege mit Bildern/Videos (community.py: 100 MB).
    [long]$ForumUploadLimit = 110000000,
    # Upload-Grenze (Bytes) fuer Musik im Admin-Dashboard (musik.py: 900 MB je Upload).
    [long]$MusicUploadLimit = 950000000
)

$ErrorActionPreference = 'Stop'

Set-Location $PSScriptRoot

# --- 1. Python ermitteln -----------------------------------------------------
$python = 'C:\Users\Administrator\AppData\Local\Programs\Python\Python314\python.exe'
if (-not (Test-Path $python)) {
    $python = (Get-Command python.exe -ErrorAction Stop).Source
}
$entrypoint = Join-Path $PSScriptRoot 'serve_production.py'
if (-not (Test-Path $entrypoint)) {
    throw "Entrypoint nicht gefunden: $entrypoint"
}

# --- 2. Alte Einzelaufgabe fuer den Cover Store abschalten ------------------
$legacy = Get-ScheduledTask -TaskName $LegacyCoverStoreTask -ErrorAction SilentlyContinue
if ($legacy) {
    Stop-ScheduledTask -TaskName $LegacyCoverStoreTask
    Disable-ScheduledTask -TaskName $LegacyCoverStoreTask | Out-Null
    Write-Host "Alte Aufgabe '$LegacyCoverStoreTask' wurde beendet und deaktiviert."
}

# --- 3. Aufgabe fuer serve_production.py registrieren -----------------------
# python.exe wird direkt gestartet (ohne PowerShell-Zwischenprozess), damit
# "Aufgabe beenden" wirklich den Server beendet. Der Cover Store haengt per
# Job-Objekt am Hauptprozess und wird dabei automatisch mit beendet.
$action = New-ScheduledTaskAction -Execute $python `
    -Argument ('-u "' + $entrypoint + '"') `
    -WorkingDirectory $PSScriptRoot
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
# ExecutionTimeLimit 0 = unbegrenzt (Standard waere ein Abbruch nach 72 h).
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $TaskName
}
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force | Out-Null
Write-Host "Aufgabe '$TaskName' wurde eingerichtet."

# --- 4. IIS-Reverse-Proxy-Regeln -------------------------------------------
Import-Module WebAdministration

# ARR-Proxy global aktivieren (Voraussetzung fuer Rewrite auf http://127.0.0.1).
Set-WebConfigurationProperty -PSPath 'MACHINE/WEBROOT/APPHOST' `
    -Filter 'system.webServer/proxy' -Name 'enabled' -Value $true

$site = Get-Website -Name $SiteName
if (-not $site) {
    throw "IIS-Site '$SiteName' nicht gefunden."
}
$sitePath = [Environment]::ExpandEnvironmentVariables($site.PhysicalPath)
$configPath = Join-Path $sitePath 'web.config'
if (-not (Test-Path $configPath)) {
    Set-Content -Path $configPath -Encoding UTF8 -Value @'
<?xml version="1.0" encoding="UTF-8"?>
<configuration>
    <system.webServer>
    </system.webServer>
</configuration>
'@
}
Copy-Item $configPath "$configPath.bak" -Force

[xml]$config = Get-Content $configPath -Raw

function Get-OrCreateChild([System.Xml.XmlNode]$parent, [string]$name) {
    $node = $parent.SelectSingleNode($name)
    if (-not $node) {
        $node = $parent.AppendChild($parent.OwnerDocument.CreateElement($name))
    }
    return $node
}

$webServer = Get-OrCreateChild $config.DocumentElement 'system.webServer'
$rewrite = Get-OrCreateChild $webServer 'rewrite'
$rules = Get-OrCreateChild $rewrite 'rules'

# Cover Store/Maker nur mit COVERSTORE_ENABLED=true in webshop/.env (Standard: an,
# wenn die Zeile fehlt) - sonst entfaellt seine Regel und /coverstore-maker geht an den Webshop (404).
$coverStoreEnabled = $true
$envFile = Join-Path $PSScriptRoot '.env'
if (Test-Path $envFile) {
    $envLine = Select-String -Path $envFile -Pattern '^\s*COVERSTORE_ENABLED\s*=\s*(\S*)' | Select-Object -Last 1
    if ($envLine -and $envLine.Matches[0].Groups[1].Value) {
        $coverStoreEnabled = $envLine.Matches[0].Groups[1].Value -match '^(1|true|yes|on)$'
    }
}

# Reihenfolge ist wichtig: spezifische Pfade vor dem Webshop-Catch-all.
$proxyRules = @(
    @{
        Name = 'CoverStoreProxy'
        Match = '^coverstore-maker(?:/(.*))?$'
        Target = "http://127.0.0.1:$CoverStorePort/coverstore-maker/{R:1}"
    },
    @{
        Name = 'Tx15WerkstattProxy'
        Match = '^tx15-werkstatt(/.*)?$'
        Target = "http://127.0.0.1:$WebshopPort/tx15-werkstatt{R:1}"
    },
    @{
        # Leaderboard des Blackbox Viewers liegt im Webshop (leaderboard.py),
        # muss deshalb vor der allgemeinen Blackbox-Viewer-Regel stehen.
        Name = 'BlackboxLeaderboardApi'
        Match = '^blackbox-viewer/api/leaderboard(.*)$'
        Target = "http://127.0.0.1:$WebshopPort/api/leaderboard{R:1}"
    },
    @{
        # Der Node-Server erwartet das Praefix (BASE_PATH) und leitet
        # /blackbox-viewer selbst auf /blackbox-viewer/ um.
        Name = 'BlackboxViewerProxy'
        Match = '^blackbox-viewer(/.*)?$'
        Target = "http://127.0.0.1:$BlackboxViewerPort/blackbox-viewer{R:1}"
    },
    @{
        Name = 'ReverseProxyInboundRule1'
        Match = '(.*)'
        Target = "http://127.0.0.1:$WebshopPort/{R:1}"
    }
)

if (-not $coverStoreEnabled) {
    $proxyRules = @($proxyRules | Where-Object { $_.Name -ne 'CoverStoreProxy' })
    $coverRule = $rules.SelectSingleNode("rule[@name='CoverStoreProxy']")
    if ($coverRule) {
        [void]$rules.RemoveChild($coverRule)
    }
    Write-Host "Cover Store ist deaktiviert (COVERSTORE_ENABLED) - keine IIS-Regel fuer /coverstore-maker."
}

foreach ($definition in $proxyRules) {
    $existing = $rules.SelectSingleNode("rule[@name='$($definition.Name)']")
    if ($existing) {
        [void]$rules.RemoveChild($existing)
    }
    $rule = $config.CreateElement('rule')
    $rule.SetAttribute('name', $definition.Name)
    $rule.SetAttribute('stopProcessing', 'true')
    $match = $config.CreateElement('match')
    $match.SetAttribute('url', $definition.Match)
    [void]$rule.AppendChild($match)
    $ruleAction = $config.CreateElement('action')
    $ruleAction.SetAttribute('type', 'Rewrite')
    $ruleAction.SetAttribute('url', $definition.Target)
    $ruleAction.SetAttribute('appendQueryString', 'true')
    [void]$rule.AppendChild($ruleAction)
    [void]$rules.AppendChild($rule)
}

# IIS lehnt Uploads ueber ~28 MB ab (requestFiltering). Nur fuer die
# Backup-API des TX15 Backup Studios anheben - die eigentliche Grenze je
# Backup prueft der Webshop (TX15_BACKUP_MAX_BYTES, siehe tx15_backups.py).
$backupLocation = $config.DocumentElement.SelectSingleNode("location[@path='api/tx15-backups']")
if (-not $backupLocation) {
    $backupLocation = $config.CreateElement('location')
    $backupLocation.SetAttribute('path', 'api/tx15-backups')
    [void]$config.DocumentElement.AppendChild($backupLocation)
}
$requestFiltering = Get-OrCreateChild (Get-OrCreateChild (Get-OrCreateChild $backupLocation 'system.webServer') 'security') 'requestFiltering'
$requestLimits = Get-OrCreateChild $requestFiltering 'requestLimits'
$requestLimits.SetAttribute('maxAllowedContentLength', [string]$BackupUploadLimit)

# Leaderboard-Einreichungen enthalten das Video mit Overlay (siehe
# LEADERBOARD_MAX_VIDEO_BYTES in leaderboard.py).
$leaderboardLocation = $config.DocumentElement.SelectSingleNode("location[@path='blackbox-viewer/api/leaderboard']")
if (-not $leaderboardLocation) {
    $leaderboardLocation = $config.CreateElement('location')
    $leaderboardLocation.SetAttribute('path', 'blackbox-viewer/api/leaderboard')
    [void]$config.DocumentElement.AppendChild($leaderboardLocation)
}
$lbFiltering = Get-OrCreateChild (Get-OrCreateChild (Get-OrCreateChild $leaderboardLocation 'system.webServer') 'security') 'requestFiltering'
$lbLimits = Get-OrCreateChild $lbFiltering 'requestLimits'
$lbLimits.SetAttribute('maxAllowedContentLength', [string]$LeaderboardUploadLimit)

# VS-Duelle: Clip der Wertungszeit mit Overlay plus Log (siehe VS_MAX_VIDEO_BYTES in vs.py).
$vsLocation = $config.DocumentElement.SelectSingleNode("location[@path='api/vs']")
if (-not $vsLocation) {
    $vsLocation = $config.CreateElement('location')
    $vsLocation.SetAttribute('path', 'api/vs')
    [void]$config.DocumentElement.AppendChild($vsLocation)
}
$vsFiltering = Get-OrCreateChild (Get-OrCreateChild (Get-OrCreateChild $vsLocation 'system.webServer') 'security') 'requestFiltering'
$vsLimits = Get-OrCreateChild $vsFiltering 'requestLimits'
$vsLimits.SetAttribute('maxAllowedContentLength', [string]$LeaderboardUploadLimit)

# Forenbeitraege mit mehreren Bildern/Videos (siehe MAX_POST_UPLOAD_BYTES in community.py).
$forumLocation = $config.DocumentElement.SelectSingleNode("location[@path='forum']")
if (-not $forumLocation) {
    $forumLocation = $config.CreateElement('location')
    $forumLocation.SetAttribute('path', 'forum')
    [void]$config.DocumentElement.AppendChild($forumLocation)
}
$forumFiltering = Get-OrCreateChild (Get-OrCreateChild (Get-OrCreateChild $forumLocation 'system.webServer') 'security') 'requestFiltering'
$forumLimits = Get-OrCreateChild $forumFiltering 'requestLimits'
$forumLimits.SetAttribute('maxAllowedContentLength', [string]$ForumUploadLimit)

# Musik fuer den Blackbox Viewer, hochgeladen im Admin-Dashboard (siehe MAX_UPLOAD_BYTES in musik.py).
$musicLocation = $config.DocumentElement.SelectSingleNode("location[@path='admin/musik']")
if (-not $musicLocation) {
    $musicLocation = $config.CreateElement('location')
    $musicLocation.SetAttribute('path', 'admin/musik')
    [void]$config.DocumentElement.AppendChild($musicLocation)
}
$musicFiltering = Get-OrCreateChild (Get-OrCreateChild (Get-OrCreateChild $musicLocation 'system.webServer') 'security') 'requestFiltering'
$musicLimits = Get-OrCreateChild $musicFiltering 'requestLimits'
$musicLimits.SetAttribute('maxAllowedContentLength', [string]$MusicUploadLimit)

$config.Save($configPath)
Write-Host "IIS-Site '$SiteName': Proxy-Regeln in $configPath aktualisiert (Sicherung: web.config.bak)."

# --- 5. Starten ---------------------------------------------------------------
Start-ScheduledTask -TaskName $TaskName
Write-Host "Aufgabe '$TaskName' wurde gestartet."
