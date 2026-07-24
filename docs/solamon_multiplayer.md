# Solamon Multiplayer Presence

Solamon multiplayer uses a standalone WebSocket presence server. The server is
not a save authority and must not store gameplay state.

The initial presence packet is signed by the unlocked owner wallet. The server
checks the signature, rejects replayed authentication nonces, and verifies that
the owner/character pair has an initialized Solamon player account on devnet.

## Server Responsibilities

- Verify the player owner and character NFT against the Solamon devnet program.
- Keep only live presence in memory:
  - owner wallet
  - character mint
  - map name
  - tile position
  - facing
  - display name
- Relay presence updates to connected clients.
- Drop all inventory, monster, item, currency, save, and battle-state fields.

## Start The Server

```powershell
py -3 tools\multiplayer\presence_server.py --host 0.0.0.0 --port 40081
```

## Client Config

Set these values in the game config for packaged clients:

```yaml
game:
  multiplayer_enabled: true
  multiplayer_auto_connect: true
  multiplayer_server_host: "127.0.0.1"
  multiplayer_server_port: 40081
```

The client auto-connects only after the wallet is unlocked, the character NFT
profile is loaded, and the on-chain save has created the local world player.

## Current Anti-Cheat Boundary

The Solana program rejects stale save overwrites by requiring each save to
include the previous on-chain save hash. This blocks offline rollback/replay
saves and stale concurrent writes.

Player currency spends now use a program-enforced spend-and-save instruction:
the program validates the player's SLMN token account, transfers SLMN to the
treasury with a CPI to the SPL Token program, and only then updates the player
save hash. Paid actions cannot save without paying the treasury.

The next anti-cheat phase is to move more gameplay transitions into program
instructions: battle rewards, item burns, catch attempts, XP gains, and level-ups.
