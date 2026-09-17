<#
    Backstop - CoinMarketCap RWA endpoint shape discovery (Windows / PowerShell).

    Why this exists: the Claude sandbox and the Claude desktop VM both sit behind
    an egress allowlist that refuses pro-api.coinmarketcap.com outright, so the
    Python probe cannot run there. This is the same probe, dependency-free, meant
    to be run once in a normal PowerShell window on the real desktop.

    It records the raw response text for every endpoint so the schema can be read
    exactly as CMC returned it, rather than after a PowerShell object round-trip.

    Usage (from the repository root):
        powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\probe.ps1

    The key is read from the CMC_API_KEY environment variable if set, otherwise
    prompted for. It is never written to disk or into the output file.
#>

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Key = $env:CMC_API_KEY
if ([string]::IsNullOrWhiteSpace($Key)) {
    $Key = Read-Host "Paste your CoinMarketCap API key (keep the dashes)"
}
$Key = $Key.Trim()
if ([string]::IsNullOrWhiteSpace($Key)) { Write-Error "No key supplied."; exit 1 }
if ($Key -notmatch '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$') {
    Write-Warning "That key is not in the usual 8-4-4-4-12 UUID shape. If it fails to authenticate, re-copy it from the CMC dashboard WITH the dashes."
}

$Base    = "https://pro-api.coinmarketcap.com"
$Headers = @{ "X-CMC_PRO_API_KEY" = $Key; "Accept" = "application/json" }
$Results = New-Object System.Collections.ArrayList

function Invoke-Cmc {
    param(
        [string]   $Label,
        [string]   $Path,
        [hashtable]$Params = @{}
    )

    $qs = ""
    if ($Params.Count -gt 0) {
        $pairs = foreach ($k in $Params.Keys) {
            "{0}={1}" -f $k, [uri]::EscapeDataString([string]$Params[$k])
        }
        $qs = "?" + ($pairs -join "&")
    }

    Write-Host ""
    Write-Host ("=" * 76)
    Write-Host ("{0}   GET {1}{2}" -f $Label, $Path, $qs)
    Write-Host ("=" * 76)

    $code = 0
    $raw  = $null
    try {
        $resp = Invoke-WebRequest -Uri ($Base + $Path + $qs) -Headers $Headers `
                                  -Method Get -UseBasicParsing -TimeoutSec 40
        $code = [int]$resp.StatusCode
        $raw  = $resp.Content
    }
    catch {
        # Windows PowerShell 5.1 throws on any non-2xx. Recover the status and body.
        if ($_.Exception.Response) {
            try { $code = [int]$_.Exception.Response.StatusCode } catch { }
        }
        if ($_.ErrorDetails -and $_.ErrorDetails.Message) {
            $raw = $_.ErrorDetails.Message
        }
        elseif ($_.Exception.Response) {
            try {
                $sr  = New-Object IO.StreamReader($_.Exception.Response.GetResponseStream())
                $raw = $sr.ReadToEnd()
                $sr.Close()
            } catch { }
        }
        if (-not $raw) { $raw = "REQUEST FAILED: " + $_.Exception.Message }
    }

    Write-Host ("  HTTP {0}" -f $code)

    $parsed = $null
    if ($raw) {
        try { $parsed = $raw | ConvertFrom-Json } catch { }
    }
    if ($parsed -and $parsed.status) {
        Write-Host ("  error_code={0}  credit_count={1}  elapsed={2}ms" -f `
            $parsed.status.error_code, $parsed.status.credit_count, $parsed.status.elapsed)
        if ($parsed.status.error_message) {
            Write-Host ("  error_message: {0}" -f $parsed.status.error_message) -ForegroundColor Yellow
        }
    }
    if ($raw) {
        $peek = $raw.Substring(0, [Math]::Min(500, $raw.Length))
        Write-Host ("  body[0..500]: {0}" -f $peek) -ForegroundColor DarkGray
    }

    [void]$Results.Add([pscustomobject]@{
        label = $Label
        path  = $Path
        query = $qs
        http  = $code
        raw   = $raw
    })

    Start-Sleep -Milliseconds 2200   # 30 req/min ceiling
    return $parsed
}

# --- helpers to find an id in a response of unknown shape --------------------

function Get-FirstList {
    param($Data)
    if ($null -eq $Data) { return @() }
    if ($Data -is [System.Object[]]) { return $Data }
    foreach ($p in $Data.PSObject.Properties) {
        if ($p.Value -is [System.Object[]] -and $p.Value.Count -gt 0) { return $p.Value }
    }
    return @()
}

function Get-FieldValue {
    param($Item, [string[]]$Names)
    if ($null -eq $Item) { return $null }
    foreach ($n in $Names) {
        $prop = $Item.PSObject.Properties[$n]
        if ($prop -and $null -ne $prop.Value -and "$($prop.Value)" -ne "") { return $prop.Value }
    }
    return $null
}

# Try an id-scoped endpoint under several plausible parameter names, because the
# published reference does not say which one it wants.
function Invoke-CmcIdScoped {
    param(
        [string]   $Label,
        [string]   $Path,
        [string[]] $ParamNames,
        $Value
    )
    foreach ($pn in $ParamNames) {
        $body = Invoke-Cmc -Label ("{0} [{1}=]" -f $Label, $pn) -Path $Path -Params @{ $pn = $Value }
        if ($body -and $body.status -and -not $body.status.error_code) { return $body }
    }
    return $null
}

# --- the probe ---------------------------------------------------------------

Write-Host ""
Write-Host "Backstop endpoint probe - CoinMarketCap RWA" -ForegroundColor Cyan

# Plan tier and credit budget. Confirms whether hackathon registration actually
# upgraded the account from Basic to Startup.
$keyInfo = Invoke-Cmc -Label "Key info (plan tier)" -Path "/v1/key/info"
if ($keyInfo -and $keyInfo.data -and $keyInfo.data.plan) {
    Write-Host ""
    Write-Host ("  >>> PLAN: {0}   credit limit/month: {1}   rate limit/min: {2}" -f `
        $keyInfo.data.plan.plan_name, `
        $keyInfo.data.plan.credit_limit_monthly, `
        $keyInfo.data.plan.rate_limit_minute) -ForegroundColor Green
}

$map        = Invoke-Cmc -Label "RWA ID map"      -Path "/v5/real-world-assets/map"         -Params @{ limit = 5 }
$assetsList = Invoke-Cmc -Label "RWA assets list" -Path "/v5/real-world-assets/assets/list" -Params @{ limit = 5; convert = "USD" }
$issuerList = Invoke-Cmc -Label "Issuers list"    -Path "/v5/real-world-assets/issuers/list" -Params @{ limit = 5 }

$rwaId = $null
foreach ($src in @($map, $assetsList)) {
    if ($rwaId) { break }
    $first = (Get-FirstList ($src.data)) | Select-Object -First 1
    $rwaId = Get-FieldValue $first @('rwa_id','id','asset_id')
}
$issuerId = $null
$firstIssuer = (Get-FirstList ($issuerList.data)) | Select-Object -First 1
$issuerId    = Get-FieldValue $firstIssuer @('issuer_id','id')

Write-Host ""
Write-Host ("  >>> sample rwa_id    = {0}" -f $rwaId)    -ForegroundColor Green
Write-Host ("  >>> sample issuer_id = {0}" -f $issuerId) -ForegroundColor Green

if ($rwaId) {
    Invoke-CmcIdScoped -Label "RWA info"          -Path "/v5/real-world-assets/info"              -ParamNames @('id','rwa_id') -Value $rwaId | Out-Null
    Invoke-CmcIdScoped -Label "RWA quotes latest" -Path "/v5/real-world-assets/quotes/latest"     -ParamNames @('id','rwa_id') -Value $rwaId | Out-Null
    Invoke-CmcIdScoped -Label "Market pairs (Growth tier - 403/plan error expected)" `
                       -Path "/v5/real-world-assets/market-pairs/list" -ParamNames @('id','rwa_id') -Value $rwaId | Out-Null
} else {
    Write-Warning "No rwa_id found - info / quotes / market-pairs skipped. Check the map and assets/list bodies above."
}

if ($issuerId) {
    Invoke-CmcIdScoped -Label "Single issuer" -Path "/v5/real-world-assets/issuers" `
                       -ParamNames @('id','issuer_id') -Value $issuerId | Out-Null
} else {
    Write-Warning "No issuer_id found - single-issuer call skipped. Check the issuers/list body above."
}

# --- save -------------------------------------------------------------------

$outDir = if ($PSScriptRoot) { Split-Path $PSScriptRoot -Parent } else { (Get-Location).Path }
$outFile = Join-Path $outDir "probe_output.json"
ConvertTo-Json -InputObject @($Results) -Depth 6 | Set-Content -Path $outFile -Encoding UTF8

Write-Host ""
Write-Host ("DONE. {0} calls recorded -> {1}" -f $Results.Count, $outFile) -ForegroundColor Cyan
Write-Host "Tell Claude it has finished; the file has the full raw responses."
