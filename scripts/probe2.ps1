<#
    Backstop - second-round endpoint probe.

    The first probe settled the response shapes. This one settles the things the
    pipeline's cost and correctness depend on, which cannot be guessed:

      1. Does quotes/latest accept a batch of rwa_ids, and what does a batch cost?
      2. Is tradfi_markets ever populated? (It was empty for Gold.)
      3. Do assets/list and issuers/list page with start/limit as expected?
      4. How does the single-issuer endpoint page its tokens?
      5. Is /v2/cryptocurrency/info reachable on this plan? It is the only route
         to a chain for each token, since the RWA endpoints carry no chain field.

    Costs roughly 15 credits of the 15,000 monthly allowance.

    Usage (from the repository root):
        powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\probe2.ps1
#>

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Key = $env:CMC_API_KEY
if ([string]::IsNullOrWhiteSpace($Key)) { $Key = Read-Host "Paste your CoinMarketCap API key (keep the dashes)" }
$Key = $Key.Trim()
if ([string]::IsNullOrWhiteSpace($Key)) { Write-Error "No key supplied."; exit 1 }

$Base    = "https://pro-api.coinmarketcap.com"
$Headers = @{ "X-CMC_PRO_API_KEY" = $Key; "Accept" = "application/json" }
$Results = New-Object System.Collections.ArrayList

function Invoke-Cmc {
    param([string]$Label, [string]$Path, [hashtable]$Params = @{})
    $qs = ""
    if ($Params.Count -gt 0) {
        $pairs = foreach ($k in $Params.Keys) { "{0}={1}" -f $k, [uri]::EscapeDataString([string]$Params[$k]) }
        $qs = "?" + ($pairs -join "&")
    }
    Write-Host ""
    Write-Host ("=" * 76)
    Write-Host ("{0}`n  GET {1}{2}" -f $Label, $Path, ($qs.Substring(0, [Math]::Min(120, $qs.Length))))
    $code = 0; $raw = $null
    try {
        $r = Invoke-WebRequest -Uri ($Base + $Path + $qs) -Headers $Headers -Method Get -UseBasicParsing -TimeoutSec 60
        $code = [int]$r.StatusCode; $raw = $r.Content
    } catch {
        if ($_.Exception.Response) { try { $code = [int]$_.Exception.Response.StatusCode } catch { } }
        if ($_.ErrorDetails -and $_.ErrorDetails.Message) { $raw = $_.ErrorDetails.Message }
        elseif ($_.Exception.Response) {
            try { $sr = New-Object IO.StreamReader($_.Exception.Response.GetResponseStream()); $raw = $sr.ReadToEnd(); $sr.Close() } catch { }
        }
        if (-not $raw) { $raw = "REQUEST FAILED: " + $_.Exception.Message }
    }
    $parsed = $null
    if ($raw) { try { $parsed = $raw | ConvertFrom-Json } catch { } }
    if ($parsed -and $parsed.status) {
        Write-Host ("  HTTP {0}  error_code={1}  credits={2}  elapsed={3}ms" -f `
            $code, $parsed.status.error_code, $parsed.status.credit_count, $parsed.status.elapsed)
        if ($parsed.status.error_message) { Write-Host ("  error: {0}" -f $parsed.status.error_message) -ForegroundColor Yellow }
    } else {
        Write-Host ("  HTTP {0}" -f $code)
    }
    [void]$Results.Add([pscustomobject]@{ label=$Label; path=$Path; query=$qs; http=$code; raw=$raw })
    Start-Sleep -Milliseconds 1400   # 50 req/min ceiling
    return $parsed
}

Write-Host "Backstop probe 2 - batching, paging and cross-endpoint reach" -ForegroundColor Cyan

# --- 1. A page of the asset universe, to harvest real rwa_ids ---------------
$page = Invoke-Cmc -Label "assets/list page 1 (limit 250)" `
                   -Path "/v5/real-world-assets/assets/list" -Params @{ start = 1; limit = 250; convert = "USD" }

$ids = @()
$tokenised = @()
if ($page -and $page.data -and $page.data.rwa_assets) {
    foreach ($a in $page.data.rwa_assets) {
        $ids += $a.rwa_id
        if ($a.has_tokens) { $tokenised += $a.rwa_id }
    }
    Write-Host ("  >>> returned {0} assets, total_size {1}, has_more {2}; {3} have tokens" -f `
        $page.data.rwa_assets.Count, $page.data.total_size, $page.data.has_more, $tokenised.Count) -ForegroundColor Green
}

# --- 2. Paging: does start= actually advance? -------------------------------
$page2 = Invoke-Cmc -Label "assets/list page 2 (start 251) - does start= advance?" `
                    -Path "/v5/real-world-assets/assets/list" -Params @{ start = 251; limit = 250; convert = "USD" }
if ($page2 -and $page2.data -and $page2.data.rwa_assets) {
    $firstId2 = $page2.data.rwa_assets[0].rwa_id
    Write-Host ("  >>> page 2 first rwa_id = {0} (page 1 first was {1}) - {2}" -f `
        $firstId2, $ids[0], $(if ($firstId2 -ne $ids[0]) { "PAGING WORKS" } else { "PAGING IGNORED" })) -ForegroundColor Green
}

# --- 3. quotes/latest batching ---------------------------------------------
if ($tokenised.Count -ge 3) {
    $b3 = ($tokenised | Select-Object -First 3) -join ","
    $q3 = Invoke-Cmc -Label "quotes/latest - batch of 3 rwa_ids" `
                     -Path "/v5/real-world-assets/quotes/latest" -Params @{ rwa_id = $b3; convert = "USD" }
    if ($q3 -and $q3.data -and $q3.data.rwa_assets) {
        Write-Host ("  >>> asked for 3, got {0} back, cost {1} credit(s)" -f `
            $q3.data.rwa_assets.Count, $q3.status.credit_count) -ForegroundColor Green
    }
}
if ($tokenised.Count -ge 100) {
    $b100 = ($tokenised | Select-Object -First 100) -join ","
    $q100 = Invoke-Cmc -Label "quotes/latest - batch of 100 rwa_ids (is there a cap?)" `
                       -Path "/v5/real-world-assets/quotes/latest" -Params @{ rwa_id = $b100; convert = "USD" }
    if ($q100 -and $q100.data -and $q100.data.rwa_assets) {
        Write-Host ("  >>> asked for 100, got {0} back, cost {1} credit(s)" -f `
            $q100.data.rwa_assets.Count, $q100.status.credit_count) -ForegroundColor Green
    }
}

# --- 4. Is tradfi_markets EVER populated? ----------------------------------
# Gold returned an empty array. Equities and treasuries are the likeliest to
# carry a real underlying market, so try one of each by asset_type.
$byType = @{}
if ($page -and $page.data -and $page.data.rwa_assets) {
    foreach ($a in $page.data.rwa_assets) {
        if ($a.has_tokens -and -not $byType.ContainsKey($a.asset_type)) { $byType[$a.asset_type] = $a.rwa_id }
    }
}
Write-Host ""
Write-Host ("  asset_types on page 1: {0}" -f (($byType.Keys | Sort-Object) -join ", ")) -ForegroundColor Green
$probeTypes = @($byType.Keys | Sort-Object | Select-Object -First 6)
foreach ($t in $probeTypes) {
    $q = Invoke-Cmc -Label ("quotes/latest - tradfi_markets check, asset_type '{0}'" -f $t) `
                    -Path "/v5/real-world-assets/quotes/latest" -Params @{ rwa_id = $byType[$t]; convert = "USD" }
    if ($q -and $q.data -and $q.data.rwa_assets) {
        $a = $q.data.rwa_assets[0]
        $tf = 0; if ($a.tradfi_markets) { $tf = @($a.tradfi_markets).Count }
        $tk = 0; if ($a.tokens) { $tk = @($a.tokens).Count }
        Write-Host ("  >>> {0} ({1}): {2} tokens, {3} tradfi_markets" -f $a.symbol, $t, $tk, $tf) -ForegroundColor Green
    }
}

# --- 5. Issuer directory in one call, and token paging ---------------------
$iss = Invoke-Cmc -Label "issuers/list - can one call hold all 25?" `
                  -Path "/v5/real-world-assets/issuers/list" -Params @{ start = 1; limit = 250 }
$issuerId = $null
if ($iss -and $iss.data -and $iss.data.issuers) {
    Write-Host ("  >>> {0} issuers returned, total_size {1}, has_more {2}" -f `
        $iss.data.issuers.Count, $iss.data.total_size, $iss.data.has_more) -ForegroundColor Green
    $issuerId = $iss.data.issuers[0].issuer_id
    $total = 0
    foreach ($i in $iss.data.issuers) { $total += $i.num_tokens }
    Write-Host ("  >>> issuers: {0}" -f (($iss.data.issuers | ForEach-Object { "$($_.name) ($($_.num_tokens))" }) -join "; ")) -ForegroundColor Green
    Write-Host ("  >>> total num_tokens across all issuers = {0}" -f $total) -ForegroundColor Green
}

if ($issuerId) {
    $d1 = Invoke-Cmc -Label "issuers - token page 1" -Path "/v5/real-world-assets/issuers" -Params @{ issuer_id = $issuerId; limit = 250 }
    if ($d1 -and $d1.data) {
        Write-Host ("  >>> limit=250 returned {0} tokens (has_more {1})" -f @($d1.data.tokens).Count, $d1.data.has_more) -ForegroundColor Green
    }
    $d2 = Invoke-Cmc -Label "issuers - token page 2 via start=" -Path "/v5/real-world-assets/issuers" -Params @{ issuer_id = $issuerId; start = 101; limit = 100 }
    if ($d2 -and $d2.data -and $d2.data.tokens) {
        $s1 = $null; if ($d1 -and $d1.data -and $d1.data.tokens) { $s1 = @($d1.data.tokens)[0].symbol }
        $s2 = @($d2.data.tokens)[0].symbol
        Write-Host ("  >>> start=101 first token '{0}' vs page-1 first '{1}' - {2}" -f `
            $s2, $s1, $(if ($s2 -ne $s1) { "TOKEN PAGING WORKS" } else { "start= IGNORED on this endpoint" })) -ForegroundColor Green
    }
}

# --- 6. Cross-endpoint reach: can we get a chain for a token? --------------
# RWA tokens carry crypto_id but no chain. /v2/cryptocurrency/info is the only
# route to a platform. Confirm the plan allows it before designing around it.
$cryptoId = $null
if ($tokenised.Count -ge 1) {
    $q = Invoke-Cmc -Label "quotes/latest - harvest a crypto_id from a token" `
                    -Path "/v5/real-world-assets/quotes/latest" -Params @{ rwa_id = $tokenised[0]; convert = "USD" }
    if ($q -and $q.data -and $q.data.rwa_assets -and $q.data.rwa_assets[0].tokens) {
        $cryptoId = @($q.data.rwa_assets[0].tokens)[0].crypto_id
    }
}
if ($cryptoId) {
    $ci = Invoke-Cmc -Label "v2/cryptocurrency/info - is it on this plan, and does it carry a platform?" `
                     -Path "/v2/cryptocurrency/info" -Params @{ id = $cryptoId }
    if ($ci -and $ci.data) {
        $rec = $ci.data.PSObject.Properties | Select-Object -First 1
        if ($rec) {
            $v = $rec.Value
            $plat = "none"
            if ($v.platform) { $plat = "$($v.platform.name)" }
            Write-Host ("  >>> {0}: platform = {1}" -f $v.symbol, $plat) -ForegroundColor Green
        }
    }
}

# --- save -------------------------------------------------------------------
$outDir = if ($PSScriptRoot) { Split-Path $PSScriptRoot -Parent } else { (Get-Location).Path }
$outFile = Join-Path $outDir "probe2_output.json"
ConvertTo-Json -InputObject @($Results) -Depth 6 | Set-Content -Path $outFile -Encoding UTF8

Write-Host ""
Write-Host ("DONE. {0} calls recorded -> {1}" -f $Results.Count, $outFile) -ForegroundColor Cyan
Write-Host "Tell Claude it has finished."
