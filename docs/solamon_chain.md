# Solamon chain model

Solamon treats the wallet as the player's identity and signing authority.

## Wallet

The client can create a local Ed25519 keypair and encrypt it with a password:

```bash
py -3 tools/solamon_wallet.py create
```

The wallet file is encrypted with PBKDF2-HMAC-SHA256 and AES-256-GCM. Unlocking
with the password decrypts the private key in memory and allows the game to sign
Solana transaction message bytes.

When `game.chain_enabled` is true, the start menu routes New Game and Load Game
through `SolamonWalletState`. The player must create or unlock the local wallet
before entering the world. The unlocked signer is stored in `client.chain_session`
for later save, SPL, NFT, and transaction flows.

On unlock, the game first tries to load a local character profile. If none is
present, it queries devnet for an NFT owned by the wallet in the Solamon
Characters collection and reconstructs the profile from token metadata.

## Character NFT

After wallet unlock, game entry is routed through `SolamonCharacterState` if the
wallet does not already have a local character profile.

The current implementation mints a real devnet player NFT:

- asks for a character name
- signs a deterministic character creation payload with the unlocked wallet
- mints a Metaplex NFT to the player's wallet
- places it in the devnet Solamon Characters collection
- stores the profile under the user's Solamon save directory
- keeps the profile in `client.chain_session.character`

Saving the game now anchors progress to the character profile. The chain
projection includes:

- character owner wallet
- character NFT mint/id
- character collection mint/status
- character name/avatar
- canonical save hash

The local character profile is updated with the latest save hash and projection
URI after each save.

## Assets

The intended asset model is:

- game money: one fungible SPL token
- items: NFTs in the configured item collection
- monsters: NFTs in the configured monster collection
- badges: NFTs in the configured badge collection

Configuration lives under `game`:

```yaml
game:
  chain_enabled: true
  solana_rpc_url: https://api.devnet.solana.com
  solana_wallet_path: null
  solamon_program_id: EvrG6acfhGmsDPK5gkwcbV5J5yR4Nqz4gGm3jG24Kq1d
  solana_submit_saves: true
  spl_currency_mint: 4D1AJNsxG6DjDWB1tpexmokzCxTnD7kwdPVqg3haafAa
  spl_currency_decimals: 6
  spl_currency_symbol: SLMN
  spl_treasury_owner: 9S8GZ6gVYiBARWLCeWdoPqMdHGVHg9sMR3hYXzrTZtnY
  spl_treasury_token_account: Aqx7tjLDnGrPS47LrdBDzk6FiPKHroHThTSNq4huRfjF
  character_nft_collection_mint: 2rUgg3y4V4fEuPaE2YmDApNPWqynDVy5m8MwfUTCSqtg
  item_nft_collection_mint: EFCEsCf6iWjjYmpyCVfmWgEbhHGB7RcQTYYQ7VUTo5fe
  monster_nft_collection_mint: 2N6yfq5fv67bS8vgKD6EqrqcPb9QQgfT42nmyHjZdg9h
  badge_nft_collection_mint: F88WpN9P5EoB1ZeZzsmr8SrtXQqs7AVR8dSPBpVdsffp
```

Devnet collections:

- Characters: `2rUgg3y4V4fEuPaE2YmDApNPWqynDVy5m8MwfUTCSqtg`
- Items: `EFCEsCf6iWjjYmpyCVfmWgEbhHGB7RcQTYYQ7VUTo5fe`
- Monsters: `2N6yfq5fv67bS8vgKD6EqrqcPb9QQgfT42nmyHjZdg9h`
- Badges: `F88WpN9P5EoB1ZeZzsmr8SrtXQqs7AVR8dSPBpVdsffp`

When `chain_enabled` is true, saving the game also writes a chain projection to
the user save directory under `chain/*.chain.json`. That projection is the
contract for the Solana submitter:

- `currency` maps the player's money and bank balance to the configured SPL
  token mint
- `nfts` maps bag/locker items, party/kennel monsters, and badges to NFT
  collection targets
- each NFT target includes a deterministic state hash so the submitted on-chain
  metadata can be verified against the local game state

For production, SLMN is expected to be an already-created SPL token. The game
uses a treasury token account as the source for rewards and the sink for spends.
For devnet testing, the current mint is:

- SLMN mint: `4D1AJNsxG6DjDWB1tpexmokzCxTnD7kwdPVqg3haafAa`
- Treasury owner: `9S8GZ6gVYiBARWLCeWdoPqMdHGVHg9sMR3hYXzrTZtnY`
- Treasury token account: `Aqx7tjLDnGrPS47LrdBDzk6FiPKHroHThTSNq4huRfjF`
- Test player token account: `AstE1nJrEqPsq8qxekJPqd264fUqicS9HdaqH8tjBURt`
- Devnet treasury/player balances after funding: `999500` / `500`

Any gameplay transaction that changes chain-owned state must also save the game
state. For currency, use `tools/solana/currency-transaction-and-save.mjs` or the
Python wrapper `submit_currency_transaction_and_save_devnet`; these compose the
SPL token transfer and `save_player_state` in one transaction.

Examples:

```bash
node currency-transaction-and-save.mjs <profile.json> <slot.chain.json> reward 25
node currency-transaction-and-save.mjs <profile.json> <slot.chain.json> spend 10
```

`reward` transfers SLMN from treasury to player. `spend` transfers SLMN from
player to treasury. Both append the current save anchor so the chain never sees
money move without a matching game-state commit.

Saving also writes a canonical save blob under `chain/blobs/<save_hash>.json`.
The blob excludes local screenshot UI data and is what the future Solana
program should anchor through `save_player_state`.

Before the save transaction is submitted, `tools/solana/mint-projection-assets.mjs`
fills missing NFT mints for projected items, monsters, and badges. It stores the
mapping in `chain/assets/<owner>.json`, so an existing game instance keeps the
same NFT mint across future saves instead of minting duplicates. The player
state program currently stores party monster mint addresses directly; item and
badge mints are minted and tracked in the projection/registry, but are not yet
validated by the program.

If a projected asset NFT is minted, the minting helper immediately submits a
fresh `save_player_state` anchor with the newly created mint in the projection.
This keeps NFT creation from drifting away from the on-chain player state. The
long-term upgrade is to compose the Metaplex mint and save anchor into one
transaction where size/compute limits allow it.

## Player Save Program

A native Solana program skeleton now lives in `programs/solamon`.

Program id:

```text
EvrG6acfhGmsDPK5gkwcbV5J5yR4Nqz4gGm3jG24Kq1d
```

It exposes two instructions:

- `create_player`: creates a player PDA derived from
  `["player", owner, character_mint]`
- `save_player_state`: updates the PDA with the canonical save hash, save blob
  URI, map/position, slot, and party monster mint list

Both instructions require:

- the owner wallet to sign
- the character NFT mint
- an SPL token account proving the owner currently holds exactly one token for
  that character mint

Save slots are chain-authoritative. The local save file/blob is only treated as
a cache for the payload; the load menu and `load_game` action only expose/load a
slot when its local projection hash matches the latest `save_hash` stored in the
player PDA. `tools/solana/read-player-state.mjs` decodes the PDA for debugging.

Client-side instruction builders live in `tools/solana/player-instructions.mjs`.
For example:

```bash
cd tools/solana
node player-instructions.mjs pda <owner-wallet> <character-mint>
```

The Solamon save flow has a guarded submit hook. With
`game.solana_submit_saves: true`, the unlocked local wallet signs a
`create_player` transaction once and a `save_player_state` transaction every
time the player explicitly saves. If submission fails, the local save still
completes and the error is logged.

Current devnet smoke player:

- Player wallet: `5ALFmgHH5qZeQREc5QAqpzoBWVskNn5kuLGQVkk2mgJg`
- Character NFT: `7PndF6URQHmb7Z9E6uTfPn4vF9YEYFNKDcsw5aPRfd2x`
- Player PDA: `AbxhytHiXUsWxsy7mJRdiCD7TxfjhMRWZBHFfa8B5WxC`
- Starter monster NFT: `7xgnrBp2mBW1mCWrzxrz2euaZ4UZQQaeUVGEwydAxWJA`
- Starter item NFT: `3Uo1DQUT4HTR7SD9koAQeyvJihuxLyJqexkn8mSWmTXm`

## Balance Reads

The configured SPL balance can be read with:

```bash
py -3 tools/solamon_balance.py <wallet-public-key>
```

This uses `getTokenAccountsByOwner` with the configured mint:

```text
4D1AJNsxG6DjDWB1tpexmokzCxTnD7kwdPVqg3haafAa
```

