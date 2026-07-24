# Solamon Tester Deployment

## Authority on Vercel

Deploy the repository root to Vercel. The HTTP API is:

- `GET /api/health`
- `POST /api/sign`
- `POST /api/approve-nft`

Required Vercel environment variables:

- `SOLAMON_GAME_AUTHORITY_SECRET_BASE64`
- `SOLAMON_RPC_URL`

Use the same game authority key that matches `tools/solana/devnet-collections.json`.
Do not commit `devnet-authority.json`.

Current devnet authority deployment:

```text
https://solamon-authority.vercel.app/api
```

## Multiplayer on Render

Render can use `render.yaml` from the repository root.

The service command is:

```text
python tools/multiplayer/presence_server.py --host 0.0.0.0 --port $PORT
```

Set `SOLAMON_RPC_URL` in Render to the same Solana RPC URL used by Vercel.
Current devnet multiplayer deployment:

```text
wss://solamon-presence.onrender.com
```

## Tester Client

For local testing without freezing:

```powershell
.\scripts\run-production-client.ps1 `
  -AuthorityUrl "https://YOUR-VERCEL-PROJECT.vercel.app/api" `
  -MultiplayerUrl "wss://solamon-presence.onrender.com" `
  -RpcUrl "https://api.devnet.solana.com"
```

For a Windows zip:

```powershell
.\scripts\build-windows-tester.ps1 `
  -AuthorityUrl "https://YOUR-VERCEL-PROJECT.vercel.app/api" `
  -MultiplayerUrl "wss://solamon-presence.onrender.com" `
  -RpcUrl "https://api.devnet.solana.com"
```

The output is `dist\SolamonTester-0.1.0.zip` with a matching
`.sha256` checksum file. Testers should extract the full ZIP and launch
`Run Solamon.bat`.

## First Test

Use a fresh devnet wallet/character.

1. Create wallet and character NFT.
2. Pick starter and confirm the starter NFT/save works.
3. Receive 5 Solaballs and confirm a single grant action.
4. Win the rival battle and confirm duplicate rewards cannot replay.
5. Open a second tester client and verify multiplayer position/name/skin.
