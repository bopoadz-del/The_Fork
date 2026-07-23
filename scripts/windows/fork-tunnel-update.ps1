# fork-tunnel-update.ps1 -- read the cloudflared log to find the
# trycloudflare.com URL, then PUT it to Render's OLLAMA_URL env var and
# trigger a redeploy so the chat picks it up.
#
# Idempotent: if the URL hasn't changed since last run, no Render call.
# Waits up to 90 seconds for cloudflared to advertise a URL.

$LogDir         = "$env:LOCALAPPDATA\fork-tunnel"
$TunnelLogFile  = "$LogDir\cloudflared.log"
$LastUrlFile    = "$LogDir\last-url.txt"
$AuditLogFile   = "$LogDir\update.log"
$RenderApiKey   = 'rnd_QqJ5qS97qrfF0IwAVrJhmKpJyNX0'
$RenderService  = 'srv-d8hdc6ek1jcs739rq5sg'

function Write-Log([string]$msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $msg"
    Add-Content -Path $AuditLogFile -Value $line -Encoding UTF8
    Write-Host $line
}

if (-not (Test-Path $TunnelLogFile)) {
    Write-Log "ERROR: tunnel log not found at $TunnelLogFile -- is cloudflared running?"
    exit 1
}

# Wait up to 90 sec for the URL to appear in the tunnel log.
$url = $null
$deadline = (Get-Date).AddSeconds(90)
while (-not $url -and (Get-Date) -lt $deadline) {
    try {
        $fs = [System.IO.File]::Open($TunnelLogFile, 'Open', 'Read', 'ReadWrite')
        $sr = New-Object System.IO.StreamReader($fs)
        $content = $sr.ReadToEnd()
        $sr.Dispose(); $fs.Dispose()
    } catch {
        $content = ''
    }
    if ($content) {
        $m = [regex]::Match($content, 'https://[a-z0-9\-]+\.trycloudflare\.com')
        if ($m.Success) { $url = $m.Value }
    }
    if (-not $url) { Start-Sleep -Seconds 3 }
}

if (-not $url) {
    Write-Log "ERROR: no URL in tunnel log after 90s. Is cloudflared healthy?"
    exit 1
}

Write-Log "tunnel URL: $url"

$previous = if (Test-Path $LastUrlFile) { (Get-Content $LastUrlFile -Raw).Trim() } else { '' }
if ($url -eq $previous) {
    Write-Log "URL unchanged ($url) -- skipping Render update"
    exit 0
}

# Push to Render.
$body = @{value=$url} | ConvertTo-Json -Compress
try {
    Invoke-RestMethod -Method Put `
        -Uri "https://api.render.com/v1/services/$RenderService/env-vars/OLLAMA_URL" `
        -Headers @{Authorization="Bearer $RenderApiKey"; 'Content-Type'='application/json'} `
        -Body $body -TimeoutSec 30 | Out-Null
    Write-Log "Render: env-var OLLAMA_URL updated"
} catch {
    Write-Log "Render: env-var update FAILED: $($_.Exception.Message)"
    exit 1
}

try {
    $deploy = Invoke-RestMethod -Method Post `
        -Uri "https://api.render.com/v1/services/$RenderService/deploys" `
        -Headers @{Authorization="Bearer $RenderApiKey"; 'Content-Type'='application/json'} `
        -Body '{"clearCache":"do_not_clear"}' -TimeoutSec 30
    Write-Log "Render: deploy triggered id=$($deploy.id)"
} catch {
    Write-Log "Render: deploy trigger FAILED: $($_.Exception.Message)"
    exit 1
}

Set-Content -Path $LastUrlFile -Value $url -Encoding ASCII
Write-Log "done -- chat will be live again ~90 seconds after redeploy"
