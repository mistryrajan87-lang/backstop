<#
    Backstop - the live API call, as a self-running demo.

    The hackathon asks for visible evidence of a real call: the code and the
    response. This runs one, against the live CoinMarketCap API, and prints both
    at a size that survives video compression. It pauses between sections so the
    recording is readable without editing.

    The key is read from $env:CMC_API_KEY and is never printed, never placed in a
    URL, and never written to disk.

    Usage:
        $env:CMC_API_KEY = "your-key-with-the-dashes"
        cls
        powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\demo.ps1
#>

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Key = $env:CMC_API_KEY
if ([string]::IsNullOrWhiteSpace($Key)) {
    Write-Host ""
    Write-Host "  CMC_API_KEY is not set." -ForegroundColor Red
    Write-Host "  Run this first, then re-run:" -ForegroundColor Red
    Write-Host '      $env:CMC_API_KEY = "your-key-with-the-dashes"' -ForegroundColor Yellow
    Write-Host ""
    exit 1
}

function Section($text) {
    Write-Host ""
    Write-Host "  $text" -ForegroundColor Cyan
    Write-Host ("  " + ("-" * ($text.Length))) -ForegroundColor DarkGray
    Start-Sleep -Milliseconds 700
}

Clear-Host
Write-Host ""
Write-Host "  BACKSTOP - live call against the CoinMarketCap RWA API" -ForegroundColor White
Start-Sleep -Seconds 2

# ---------------------------------------------------------------------------
Section "1. Gold is one row on CoinMarketCap. Ask the API for it."

Write-Host '  $r = Invoke-RestMethod -Headers @{"X-CMC_PRO_API_KEY"=$env:CMC_API_KEY} `' -ForegroundColor Yellow
Write-Host '       "https://pro-api.coinmarketcap.com/v5/real-world-assets/quotes/latest?rwa_id=1&convert=USD"' -ForegroundColor Yellow
Start-Sleep -Seconds 3

$r = Invoke-RestMethod -Headers @{ "X-CMC_PRO_API_KEY" = $Key } `
     "https://pro-api.coinmarketcap.com/v5/real-world-assets/quotes/latest?rwa_id=1&convert=USD"

$asset = $r.data.rwa_assets[0]
Write-Host ""
Write-Host ("  HTTP OK   error_code={0}   credits={1}   elapsed={2}ms" -f `
    $r.status.error_code, $r.status.credit_count, $r.status.elapsed) -ForegroundColor Green
Write-Host ("  asset: {0} ({1})   type: {2}" -f $asset.name, $asset.symbol, $asset.asset_type)
Start-Sleep -Seconds 4

# ---------------------------------------------------------------------------
Section "2. Underneath that one row: seven tokens, from six different issuers."

Write-Host '  $r.data.rwa_assets[0].tokens | Format-Table symbol, issuer_name, market_cap' -ForegroundColor Yellow
Start-Sleep -Seconds 2

$asset.tokens |
    Sort-Object market_cap -Descending |
    Format-Table @{L='TOKEN';E={$_.symbol};W=10},
                 @{L='ISSUER';E={$_.issuer_name};W=24},
                 @{L='MARKET CAP';E={ if ($null -eq $_.market_cap) { "null" }
                                      else { '{0,18:N0}' -f $_.market_cap } };W=20} |
    Out-String | Write-Host

$distinct = ($asset.tokens | Select-Object -ExpandProperty issuer_name -Unique).Count
Write-Host ("  {0} tokens.  {1} distinct issuers.  One asset." -f $asset.tokens.Count, $distinct) -ForegroundColor Green
Start-Sleep -Seconds 5

# ---------------------------------------------------------------------------
Section "3. Why the obvious method inflates the market."

$sum = ($asset.tokens | Measure-Object market_cap -Sum).Sum
Write-Host ("  the asset's own market cap        {0,20:N2}" -f $asset.tokenized_market_cap)
Write-Host ("  the sum of its tokens             {0,20:N2}" -f $sum)
Write-Host ("  ratio                             {0,20:N4}" -f ($sum / $asset.tokenized_market_cap)) -ForegroundColor Green
Start-Sleep -Seconds 3
Write-Host ""
Write-Host "  Credit that cap to every issuer who mints gold and the market looks" -ForegroundColor White
Write-Host ("  {0} times bigger than it is." -f $distinct) -ForegroundColor White
Write-Host "  Backstop adds each token to its own issuer instead." -ForegroundColor White
Start-Sleep -Seconds 5

# ---------------------------------------------------------------------------
Section "4. Done across all 790 tokenised assets, that gives:"

Write-Host "    790 assets carrying tokens, out of 7,811 in the map"
Write-Host "    `$7.43bn of reported market cap"
Write-Host "    15 issuers hold all of it - the top five, 94.1%"
Write-Host "    and 63% of the whole book is this one asset"
Write-Host ""
Write-Host "    https://mistryrajan87-lang.github.io/backstop/" -ForegroundColor Cyan
Write-Host ""
Start-Sleep -Seconds 4
