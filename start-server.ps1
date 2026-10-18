<#
  TX15 Werkstatt – lokaler Webserver

  Startet die Werkstatt unter http://localhost:8080/
  Nötig für den ELRS Flasher mit heruntergeladener Firmware (elrs/get-firmware.ps1):
  Browser laden Dateien nur über einen Webserver nach, nicht per Doppelklick (file://).
  Beenden mit Strg+C.
#>
param([int]$Port = 8080, [switch]$NoBrowser)

$root = $PSScriptRoot
$types = @{
  '.html' = 'text/html; charset=utf-8'; '.js' = 'text/javascript; charset=utf-8'; '.json' = 'application/json; charset=utf-8'
  '.css' = 'text/css; charset=utf-8'; '.png' = 'image/png'; '.svg' = 'image/svg+xml'; '.lua' = 'text/plain; charset=utf-8'
  '.bin' = 'application/octet-stream'; '.zip' = 'application/zip'; '.yml' = 'text/yaml; charset=utf-8'
  '.mjs' = 'text/javascript; charset=utf-8'; '.webmanifest' = 'application/manifest+json'; '.map' = 'application/json'
  '.ico' = 'image/x-icon'; '.jpg' = 'image/jpeg'; '.jpeg' = 'image/jpeg'; '.gif' = 'image/gif'; '.webp' = 'image/webp'
  '.woff' = 'font/woff'; '.woff2' = 'font/woff2'; '.ttf' = 'font/ttf'; '.wasm' = 'application/wasm'
  '.txt' = 'text/plain; charset=utf-8'; '.md' = 'text/plain; charset=utf-8'; '.xml' = 'application/xml'; '.hex' = 'text/plain'
  '.mcm' = 'text/plain'; '.mp3' = 'audio/mpeg'; '.wav' = 'audio/wav'
}
$listener = [System.Net.HttpListener]::new()
$listener.Prefixes.Add("http://localhost:$Port/")
$listener.Start()
Write-Host "TX15 Werkstatt läuft: http://localhost:$Port/   (Beenden mit Strg+C)"
if (-not $NoBrowser) { Start-Process "http://localhost:$Port/" }

try {
  while ($listener.IsListening) {
    $ctx = $listener.GetContext()
    $path = [Uri]::UnescapeDataString($ctx.Request.Url.AbsolutePath.TrimStart('/'))
    if ($path -eq '' -or $path.EndsWith('/')) { $path = $path + 'index.html' }
    $file = [System.IO.Path]::GetFullPath((Join-Path $root $path))
    if (-not $file.StartsWith($root) -or -not (Test-Path $file -PathType Leaf)) {
      $ctx.Response.StatusCode = 404
    } else {
      $ext = [System.IO.Path]::GetExtension($file).ToLower()
      $ctx.Response.ContentType = $(if ($types[$ext]) { $types[$ext] } else { 'application/octet-stream' })
      $bytes = [System.IO.File]::ReadAllBytes($file)
      $ctx.Response.ContentLength64 = $bytes.Length
      if ($ctx.Request.HttpMethod -ne 'HEAD') { $ctx.Response.OutputStream.Write($bytes, 0, $bytes.Length) }
    }
    $ctx.Response.Close()
  }
} finally { $listener.Stop() }
