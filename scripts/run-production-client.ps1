param(
    [Parameter(Mandatory = $true)]
    [string]$AuthorityUrl,

    [Parameter(Mandatory = $true)]
    [string]$MultiplayerUrl,

    [string]$RpcUrl = "https://api.devnet.solana.com",
    [string]$UserDir = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

$env:SOLAMON_REQUIRE_AUTHORITY = "1"
$env:SOLAMON_AUTHORITY_URL = $AuthorityUrl.TrimEnd("/")
$env:SOLAMON_MULTIPLAYER_URL = $MultiplayerUrl
$env:SOLAMON_RPC_URL = $RpcUrl
if ($UserDir) {
    $env:SOLAMON_USER_DIR = $UserDir
}

Set-Location $root
py -3 run_tuxemon.py
