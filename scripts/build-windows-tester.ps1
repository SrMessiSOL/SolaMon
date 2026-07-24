param(
    [Parameter(Mandatory = $true)]
    [string]$AuthorityUrl,

    [Parameter(Mandatory = $true)]
    [string]$MultiplayerUrl,

    [string]$RpcUrl = "https://solamon-authority.vercel.app/api/rpc",
    [string]$OutputName = "SolamonTester",
    [string]$Version = "0.1.0"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$packageDir = Join-Path $root "dist\$OutputName"
$zipPath = Join-Path $root "dist\$OutputName-$Version.zip"
$checksumPath = "$zipPath.sha256"

Set-Location $root
$buildTempDir = Join-Path $root ".buildtmp"
if (Test-Path $buildTempDir) {
    attrib -R -H -S (Join-Path $buildTempDir "*") /S /D 2>$null
    Remove-Item -LiteralPath $buildTempDir -Recurse -Force -ErrorAction SilentlyContinue
}
New-Item -ItemType Directory -Path $buildTempDir -Force | Out-Null
$env:TEMP = $buildTempDir
$env:TMP = $buildTempDir
py -3 -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "pip install requirements failed with exit code $LASTEXITCODE" }
py -3 -m pip install cx_Freeze
if ($LASTEXITCODE -ne 0) { throw "pip install cx_Freeze failed with exit code $LASTEXITCODE" }
Push-Location tools\solana
if (-not (Test-Path "node_modules")) {
    npm install --omit=dev
    if ($LASTEXITCODE -ne 0) { throw "npm install failed with exit code $LASTEXITCODE" }
}
Pop-Location

py -3 buildconfig\setup_cx_freeze.py build
if ($LASTEXITCODE -ne 0) { throw "cx_Freeze build failed with exit code $LASTEXITCODE" }

$buildDir = Get-ChildItem -Path "build" -Directory |
    Where-Object { $_.Name -like "exe.*" } |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
if (-not $buildDir) {
    throw "cx_Freeze build output was not found."
}

if (Test-Path $packageDir) {
    attrib -R (Join-Path $packageDir "*") /S /D 2>$null
    Remove-Item -LiteralPath $packageDir -Recurse -Force
}
New-Item -ItemType Directory -Path $packageDir -Force | Out-Null
Copy-Item -Path (Join-Path $buildDir.FullName "*") -Destination $packageDir -Recurse -Force
$rootMods = Join-Path $packageDir "mods"
$libMods = Join-Path $packageDir "lib\mods"
if (Test-Path $rootMods) {
    if (Test-Path $libMods) {
        Remove-Item -LiteralPath $libMods -Recurse -Force
    }
    Copy-Item -LiteralPath $rootMods -Destination $libMods -Recurse -Force
}
$packageToolsDir = Join-Path $packageDir "tools\solana"
if (Test-Path $packageToolsDir) {
    attrib -R (Join-Path $packageToolsDir "*") /S /D 2>$null
    Remove-Item -LiteralPath $packageToolsDir -Recurse -Force
}
New-Item -ItemType Directory -Path (Split-Path -Parent $packageToolsDir) -Force | Out-Null
Copy-Item -Path "tools\solana" -Destination $packageToolsDir -Recurse -Force
$nodeCommand = Get-Command node.exe -ErrorAction Stop
$runtimeDir = Join-Path $packageDir "runtime"
New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
Copy-Item -LiteralPath $nodeCommand.Source -Destination (Join-Path $runtimeDir "node.exe") -Force
foreach ($secretFile in @(
    "devnet-program-keypair.json",
    "devnet-authority.json",
    ".env",
    ".env.local"
)) {
    $secretPath = Join-Path $packageToolsDir $secretFile
    if (Test-Path $secretPath) {
        attrib -R -H -S $secretPath 2>$null
        Remove-Item -LiteralPath $secretPath -Force -ErrorAction Stop
    }
}
Get-ChildItem -LiteralPath (Join-Path $packageDir "tools\solana\metadata") -File -Filter "*.json" -ErrorAction SilentlyContinue |
    Remove-Item -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath (Join-Path $packageDir ".vercel") -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath (Join-Path $packageDir ".render-cli") -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath (Join-Path $packageDir "players") -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath (Join-Path $packageDir "saves") -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath (Join-Path $packageDir "tuxemon_error.log") -Force -ErrorAction SilentlyContinue
Get-ChildItem -LiteralPath $packageDir -Recurse -Filter "solamon_wallet.json" -ErrorAction SilentlyContinue |
    Remove-Item -Force

foreach ($configFile in @("devnet-collections.json", "devnet-currency.json")) {
    $configPath = Join-Path $packageDir "tools\solana\$configFile"
    if (Test-Path $configPath) {
        $json = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
        $json.rpcUrl = $RpcUrl
        $json | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $configPath -Encoding ASCII
    }
}

$forbiddenFiles = @(
    "tools\solana\devnet-authority.json",
    "tools\solana\devnet-program-keypair.json",
    ".vercel\project.json",
    ".render-cli\cli.yaml",
    "players",
    "saves"
)
foreach ($relativePath in $forbiddenFiles) {
    if (Test-Path (Join-Path $packageDir $relativePath)) {
        throw "Forbidden packaged path found: $relativePath"
    }
}
if (-not (Test-Path (Join-Path $packageDir "runtime\node.exe"))) {
    throw "Required packaged runtime missing: runtime\node.exe"
}

$leakCandidates = Get-ChildItem -LiteralPath $packageToolsDir -Recurse -File -ErrorAction SilentlyContinue |
    Where-Object {
        $_.FullName -notlike "*\node_modules\*" -and
        $_.Extension -in @(".js", ".mjs", ".cjs", ".json", ".txt", ".md", ".env", ".yaml", ".yml")
    }
$leaks = $leakCandidates |
    Select-String -Pattern "api-key=|devnet\.helius-rpc\.com|3d0ad7ca-7869-4a97-9d3e-a57131ae89db" -List
if ($leaks) {
    $paths = ($leaks | ForEach-Object { $_.Path }) -join "`n"
    throw "Sensitive data marker found in package:`n$paths"
}

$launcher = Join-Path $packageDir "Run Solamon.ps1"
@"
`$ErrorActionPreference = "Stop"
`$env:SOLAMON_REQUIRE_AUTHORITY = "1"
`$env:SOLAMON_AUTHORITY_URL = "$($AuthorityUrl.TrimEnd("/"))"
`$env:SOLAMON_MULTIPLAYER_URL = "$MultiplayerUrl"
`$env:SOLAMON_RPC_URL = "$RpcUrl"
`$env:SOLAMON_CLIENT_VERSION = "$Version"
`$env:SOLAMON_USER_DIR = Join-Path `$env:LOCALAPPDATA "Solamon"
`$env:PATH = (Join-Path `$PSScriptRoot "runtime") + ";" + `$env:PATH
New-Item -ItemType Directory -Path `$env:SOLAMON_USER_DIR -Force | Out-Null
Set-Location `$PSScriptRoot
.\Solamon.exe
"@ | Set-Content -Path $launcher -Encoding ASCII

$batLauncher = Join-Path $packageDir "Run Solamon.bat"
@"
@echo off
setlocal
set "SOLAMON_REQUIRE_AUTHORITY=1"
set "SOLAMON_AUTHORITY_URL=$($AuthorityUrl.TrimEnd("/"))"
set "SOLAMON_MULTIPLAYER_URL=$MultiplayerUrl"
set "SOLAMON_RPC_URL=$RpcUrl"
set "SOLAMON_CLIENT_VERSION=$Version"
set "SOLAMON_USER_DIR=%LOCALAPPDATA%\Solamon"
set "PATH=%~dp0runtime;%PATH%"
if not exist "%SOLAMON_USER_DIR%" mkdir "%SOLAMON_USER_DIR%"
cd /d "%~dp0"
start "" "%~dp0Solamon.exe"
"@ | Set-Content -Path $batLauncher -Encoding ASCII

$testerReadme = Join-Path $packageDir "README-TESTERS.txt"
@"
Solamon closed devnet test client $Version

1. Extract the entire ZIP to a normal folder.
2. Double-click "Run Solamon.bat".
3. Create or unlock your own test wallet. Never share its recovery data.
4. All currency, NFTs, and transactions in this build use Solana devnet.
5. Multiplayer presence connects to:
   $MultiplayerUrl
6. Saves and approved actions connect to:
   $($AuthorityUrl.TrimEnd("/"))

Report the approximate time, map, and action when something fails.
"@ | Set-Content -LiteralPath $testerReadme -Encoding ASCII

Remove-Item -LiteralPath (Join-Path $packageDir "run_tuxemon.exe") -Force -ErrorAction SilentlyContinue
foreach ($artifact in @($zipPath, $checksumPath)) {
    if (Test-Path -LiteralPath $artifact) {
        Remove-Item -LiteralPath $artifact -Force
    }
}
Compress-Archive -Path (Join-Path $packageDir "*") -DestinationPath $zipPath -CompressionLevel Optimal
$hash = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash.ToLowerInvariant()
"$hash  $(Split-Path -Leaf $zipPath)" | Set-Content -LiteralPath $checksumPath -Encoding ASCII
Write-Host "Built clean tester folder: $packageDir"
Write-Host "Built tester ZIP: $zipPath"
Write-Host "SHA-256: $hash"
