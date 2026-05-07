param(
    [Parameter(Mandatory = $true)]
    [string]$AuthorityUrl,

    [Parameter(Mandatory = $true)]
    [string]$MultiplayerUrl,

    [string]$RpcUrl = "https://api.devnet.solana.com",
    [string]$OutputName = "SolamonTester"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$packageDir = Join-Path $root "dist\$OutputName"
$zipPath = Join-Path $root "dist\$OutputName.zip"

Set-Location $root
py -3 -m pip install -r requirements.txt
py -3 -m pip install cx_Freeze
Push-Location tools\solana
npm install --omit=dev
Pop-Location

py -3 buildconfig\setup_cx_freeze.py build

$buildDir = Get-ChildItem -Path "build" -Directory |
    Where-Object { $_.Name -like "exe.*" } |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
if (-not $buildDir) {
    throw "cx_Freeze build output was not found."
}

if (Test-Path $packageDir) {
    Remove-Item -LiteralPath $packageDir -Recurse -Force
}
New-Item -ItemType Directory -Path $packageDir -Force | Out-Null
Copy-Item -Path (Join-Path $buildDir.FullName "*") -Destination $packageDir -Recurse -Force
Copy-Item -Path "tools\solana" -Destination (Join-Path $packageDir "tools\solana") -Recurse -Force
Remove-Item -LiteralPath (Join-Path $packageDir "tools\solana\devnet-authority.json") -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath (Join-Path $packageDir "tools\solana\devnet-program-keypair.json") -Force -ErrorAction SilentlyContinue

$envFile = Join-Path $packageDir "solamon-production.env"
@"
SOLAMON_REQUIRE_AUTHORITY=1
SOLAMON_AUTHORITY_URL=$($AuthorityUrl.TrimEnd("/"))
SOLAMON_MULTIPLAYER_URL=$MultiplayerUrl
SOLAMON_RPC_URL=$RpcUrl
"@ | Set-Content -Path $envFile -Encoding ASCII

$launcher = Join-Path $packageDir "Run Solamon.ps1"
@"
`$ErrorActionPreference = "Stop"
`$env:SOLAMON_REQUIRE_AUTHORITY = "1"
`$env:SOLAMON_AUTHORITY_URL = "$($AuthorityUrl.TrimEnd("/"))"
`$env:SOLAMON_MULTIPLAYER_URL = "$MultiplayerUrl"
`$env:SOLAMON_RPC_URL = "$RpcUrl"
Set-Location `$PSScriptRoot
.\run_tuxemon.exe
"@ | Set-Content -Path $launcher -Encoding ASCII

if (Test-Path $zipPath) {
    Remove-Item -LiteralPath $zipPath -Force
}
Compress-Archive -Path (Join-Path $packageDir "*") -DestinationPath $zipPath -Force
Write-Host "Built tester package: $zipPath"
