import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  Connection,
  Keypair,
  PublicKey,
  SystemProgram,
  TransactionInstruction,
} from '@solana/web3.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ids = JSON.parse(fs.readFileSync(path.join(__dirname, 'program-ids.json'), 'utf8'));
const collections = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'devnet-collections.json'), 'utf8'),
);

export const SOLAMON_PROGRAM_ID = new PublicKey(ids.solamonProgram);
export const SOLAMON_GAME_AUTHORITY = gameAuthorityKeypair().publicKey;

export function gameAuthorityKeypair() {
  const secret = process.env.SOLAMON_GAME_AUTHORITY_SECRET_BASE64;
  if (secret) {
    return Keypair.fromSecretKey(Buffer.from(secret, 'base64'));
  }
  return Keypair.fromSecretKey(
    Uint8Array.from(JSON.parse(fs.readFileSync(path.join(__dirname, 'devnet-authority.json'), 'utf8'))),
  );
}

export function derivePlayerStatePda(owner, characterMint, programId = SOLAMON_PROGRAM_ID) {
  return PublicKey.findProgramAddressSync(
    [
      Buffer.from('player'),
      new PublicKey(owner).toBuffer(),
      new PublicKey(characterMint).toBuffer(),
    ],
    programId,
  );
}

export async function findCharacterTokenAccount(connection, owner, characterMint) {
  const response = await connection.getTokenAccountsByOwner(new PublicKey(owner), {
    mint: new PublicKey(characterMint),
  });

  for (const { pubkey, account } of response.value) {
    const amount = account.data.readBigUInt64LE(64);
    if (amount === 1n) {
      return pubkey;
    }
  }

  throw new Error(`No token account holding character NFT ${characterMint} for ${owner}`);
}

export async function createPlayerInstruction({
  connection,
  owner,
  characterMint,
  characterTokenAccount,
  name,
  avatar = 'player',
}) {
  const ownerKey = new PublicKey(owner);
  const characterMintKey = new PublicKey(characterMint);
  const nftTokenAccount =
    characterTokenAccount ??
    (await findCharacterTokenAccount(connection, ownerKey, characterMintKey));
  const [playerState] = derivePlayerStatePda(ownerKey, characterMintKey);

  return new TransactionInstruction({
    programId: SOLAMON_PROGRAM_ID,
    keys: [
      { pubkey: ownerKey, isSigner: true, isWritable: true },
      { pubkey: SOLAMON_GAME_AUTHORITY, isSigner: true, isWritable: false },
      { pubkey: characterMintKey, isSigner: false, isWritable: false },
      { pubkey: nftTokenAccount, isSigner: false, isWritable: false },
      { pubkey: playerState, isSigner: false, isWritable: true },
      { pubkey: SystemProgram.programId, isSigner: false, isWritable: false },
    ],
    data: Buffer.concat([
      Buffer.from([0]),
      encodeFixedString(name, 32),
      encodeFixedString(avatar, 32),
    ]),
  });
}

export async function playerSaveInstructions({
  connection,
  profile,
  projection,
  spendCurrency = null,
  gameAction = null,
}) {
  if (!gameAction) {
    throw new Error('A typed Solamon gameAction is required for on-chain saves');
  }
  const owner = new PublicKey(profile.owner);
  const characterMint = new PublicKey(profile.character_mint);
  const [playerState] = derivePlayerStatePda(owner, characterMint);
  const existingPlayerState = await connection.getAccountInfo(playerState);
  const instructions = [];

  if (!existingPlayerState) {
    instructions.push(
      await createPlayerInstruction({
        connection,
        owner,
        characterMint,
        name: profile.name,
        avatar: profile.avatar_slug,
      }),
    );
  }

  const location = projection.location ?? {};
  const nfts = hydrateProjectionNftMints(profile, projection);
  const partyMonsterMints = nfts
    .filter((nft) => nft.kind === 'monster' && nft.metadata?.location === 'party')
    .map((nft) => nft.mint)
    .filter(Boolean);

  const saveFields = {
    connection,
    owner,
    characterMint,
    expectedPreviousSaveHash: await readCurrentSaveHash(connection, playerState),
    saveHash: projection.save_hash,
    saveUri: projection.save_blob_uri ?? projection.projection_path ?? '',
    mapId: location.map_id ?? '',
    tileX: location.tile_x ?? 0,
    tileY: location.tile_y ?? 0,
    party: partyMonsterMints,
  };

  instructions.push(
    spendCurrency
      ? await spendAndSavePlayerStateInstruction({
          ...saveFields,
          ...spendCurrency,
          gameAction,
        })
      : await savePlayerStateInstruction({
          ...saveFields,
          gameAction,
        }),
  );

  return {
    instructions,
    owner,
    characterMint,
    playerState,
    partyMonsterMints,
  };
}

export function hydrateProjectionNftMints(profile, projection) {
  const nfts = projection.nfts ?? [];
  const registry = loadAssetRegistry(profile, projection);
  if (!registry) {
    return nfts;
  }

  return nfts.map((nft) => {
    if (nft.mint) {
      return nft;
    }
    const key = `${profile.owner}:${nft.kind}:${nft.instance_id}`;
    const entry = registry.assets?.[key];
    if (!entry?.mint) {
      return nft;
    }
    return {
      ...nft,
      mint: entry.mint,
    };
  });
}

function loadAssetRegistry(profile, projection) {
  const candidates = [
    projection.asset_registry_path,
    projection.projection_path
      ? path.join(path.dirname(projection.projection_path), 'assets', `${profile.owner}.json`)
      : null,
  ].filter(Boolean);

  for (const candidate of candidates) {
    try {
      if (fs.existsSync(candidate)) {
        return JSON.parse(fs.readFileSync(candidate, 'utf8'));
      }
    } catch {
      // Missing or malformed registries should not block saving location/state.
    }
  }
  return null;
}

export async function savePlayerStateInstruction({
  connection,
  owner,
  characterMint,
  characterTokenAccount,
  expectedPreviousSaveHash,
  saveHash,
  saveUri,
  mapId,
  tileX,
  tileY,
  party = [],
  gameAction = null,
}) {
  if (party.length > 6) {
    throw new Error('party can contain at most 6 monster mint public keys');
  }

  const ownerKey = new PublicKey(owner);
  const characterMintKey = new PublicKey(characterMint);
  const nftTokenAccount =
    characterTokenAccount ??
    (await findCharacterTokenAccount(connection, ownerKey, characterMintKey));
  const [playerState] = derivePlayerStatePda(ownerKey, characterMintKey);
  const partyKeys = party.map((mint) => new PublicKey(mint));

  return new TransactionInstruction({
    programId: SOLAMON_PROGRAM_ID,
    keys: [
      { pubkey: ownerKey, isSigner: true, isWritable: false },
      ...(gameAction ? [{ pubkey: SOLAMON_GAME_AUTHORITY, isSigner: true, isWritable: false }] : []),
      { pubkey: characterMintKey, isSigner: false, isWritable: false },
      { pubkey: nftTokenAccount, isSigner: false, isWritable: false },
      { pubkey: playerState, isSigner: false, isWritable: true },
    ],
    data: Buffer.concat([
      Buffer.from([gameActionTag(gameAction, false)]),
      ...(gameAction ? [encodeGameAction(gameAction)] : []),
      encodeSaveHash(expectedPreviousSaveHash ?? ZERO_SAVE_HASH),
      encodeSaveHash(saveHash),
      encodeFixedString(saveUri, 200),
      encodeFixedString(mapId, 32),
      encodeU16(tileX),
      encodeU16(tileY),
      Buffer.from([partyKeys.length]),
      ...partyKeys.map((mint) => mint.toBuffer()),
    ]),
  });
}

export async function spendAndSavePlayerStateInstruction({
  connection,
  owner,
  characterMint,
  characterTokenAccount,
  expectedPreviousSaveHash,
  saveHash,
  saveUri,
  mapId,
  tileX,
  tileY,
  party = [],
  currencyMint,
  playerTokenAccount,
  treasuryTokenAccount,
  amount,
  gameAction = null,
}) {
  if (party.length > 6) {
    throw new Error('party can contain at most 6 monster mint public keys');
  }
  if (amount === undefined || amount === null) {
    throw new Error('amount is required for spendAndSavePlayerStateInstruction');
  }

  const ownerKey = new PublicKey(owner);
  const characterMintKey = new PublicKey(characterMint);
  const nftTokenAccount =
    characterTokenAccount ??
    (await findCharacterTokenAccount(connection, ownerKey, characterMintKey));
  const [playerState] = derivePlayerStatePda(ownerKey, characterMintKey);
  const partyKeys = party.map((mint) => new PublicKey(mint));
  const amountRaw = BigInt(amount);
  const tokenMint = new PublicKey(currencyMint);
  const playerToken = new PublicKey(playerTokenAccount);
  const treasuryToken = new PublicKey(treasuryTokenAccount);

  return new TransactionInstruction({
    programId: SOLAMON_PROGRAM_ID,
    keys: [
      { pubkey: ownerKey, isSigner: true, isWritable: false },
      ...(gameAction ? [{ pubkey: SOLAMON_GAME_AUTHORITY, isSigner: true, isWritable: false }] : []),
      { pubkey: characterMintKey, isSigner: false, isWritable: false },
      { pubkey: nftTokenAccount, isSigner: false, isWritable: false },
      { pubkey: playerState, isSigner: false, isWritable: true },
      { pubkey: playerToken, isSigner: false, isWritable: true },
      { pubkey: treasuryToken, isSigner: false, isWritable: true },
      { pubkey: TOKEN_PROGRAM_ID, isSigner: false, isWritable: false },
    ],
    data: Buffer.concat([
      Buffer.from([gameActionTag(gameAction, true)]),
      encodeU64(amountRaw),
      tokenMint.toBuffer(),
      ...(gameAction ? [encodeGameAction(gameAction)] : []),
      encodePlayerSaveData({
        expectedPreviousSaveHash,
        saveHash,
        saveUri,
        mapId,
        tileX,
        tileY,
        partyKeys,
      }),
    ]),
  });
}

export async function readCurrentSaveHash(connection, playerState) {
  const account = await connection.getAccountInfo(new PublicKey(playerState));
  if (!account) {
    return ZERO_SAVE_HASH;
  }
  return account.data.subarray(138, 170).toString('hex');
}

const ZERO_SAVE_HASH = '0000000000000000000000000000000000000000000000000000000000000000';

export const TOKEN_PROGRAM_ID = new PublicKey('TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA');

function encodePlayerSaveData({
  expectedPreviousSaveHash,
  saveHash,
  saveUri,
  mapId,
  tileX,
  tileY,
  partyKeys,
}) {
  return Buffer.concat([
    encodeSaveHash(expectedPreviousSaveHash ?? ZERO_SAVE_HASH),
    encodeSaveHash(saveHash),
    encodeFixedString(saveUri, 200),
    encodeFixedString(mapId, 32),
    encodeU16(tileX),
    encodeU16(tileY),
    Buffer.from([partyKeys.length]),
    ...partyKeys.map((mint) => mint.toBuffer()),
  ]);
}

const GAME_ACTIONS = {
  choose_starter: { tag: 7, spendTag: null, code: 1 },
  grant_items: { tag: 8, spendTag: null, code: 2 },
  battle_result: { tag: 9, spendTag: null, code: 3 },
  catch_solamon: { tag: 10, spendTag: null, code: 4 },
  buy_item: { tag: null, spendTag: 11, code: 5 },
  sell_item: { tag: 12, spendTag: null, code: 6 },
  heal_party: { tag: null, spendTag: 13, code: 7 },
  release_solamon: { tag: 14, spendTag: null, code: 8 },
};

function gameActionTag(gameAction, spendsCurrency) {
  if (!gameAction) {
    return spendsCurrency ? 6 : 1;
  }
  const descriptor = GAME_ACTIONS[gameAction.kind];
  if (!descriptor) {
    throw new Error(`Unknown Solamon game action: ${gameAction.kind}`);
  }
  const tag = spendsCurrency ? descriptor.spendTag : descriptor.tag;
  if (!tag) {
    throw new Error(
      `Solamon game action ${gameAction.kind} ${
        spendsCurrency ? 'requires no currency spend' : 'requires currency spend'
      }`,
    );
  }
  return tag;
}

function encodeGameAction(gameAction) {
  const descriptor = GAME_ACTIONS[gameAction.kind];
  if (!descriptor) {
    throw new Error(`Unknown Solamon game action: ${gameAction.kind}`);
  }
  return Buffer.concat([
    Buffer.from([descriptor.code]),
    encodeU16(gameAction.amount ?? 1),
    encodeU16(gameAction.auxAmount ?? gameAction.aux_amount ?? 0),
    encodeFixedString(gameAction.primaryId ?? gameAction.primary_id ?? '', 32),
    encodeFixedString(gameAction.secondaryId ?? gameAction.secondary_id ?? '', 32),
  ]);
}

export function encodeFixedString(value, maxLength) {
  const bytes = Buffer.from(value ?? '', 'utf8');
  if (bytes.length > maxLength) {
    throw new Error(`String is ${bytes.length} bytes, max is ${maxLength}`);
  }
  return Buffer.concat([Buffer.from([bytes.length]), bytes]);
}

export function encodeSaveHash(saveHash) {
  const normalized = String(saveHash).replace(/^0x/, '');
  const bytes = Buffer.from(normalized, 'hex');
  if (bytes.length !== 32) {
    throw new Error('saveHash must be a 32-byte hex string');
  }
  return bytes;
}

function encodeU16(value) {
  if (!Number.isInteger(value) || value < 0 || value > 65535) {
    throw new Error(`u16 out of range: ${value}`);
  }
  const out = Buffer.alloc(2);
  out.writeUInt16LE(value);
  return out;
}

function encodeU64(value) {
  if (value < 0n || value > 18446744073709551615n) {
    throw new Error(`u64 out of range: ${value}`);
  }
  const out = Buffer.alloc(8);
  out.writeBigUInt64LE(value);
  return out;
}

if (process.argv[2] === 'pda') {
  const [, , , owner, characterMint] = process.argv;
  if (!owner || !characterMint) {
    console.error('Usage: node player-instructions.mjs pda <owner> <characterMint>');
    process.exit(1);
  }
  const [pda, bump] = derivePlayerStatePda(owner, characterMint);
  console.log(JSON.stringify({
    rpcUrl: collections.rpcUrl,
    program: SOLAMON_PROGRAM_ID.toBase58(),
    playerState: pda.toBase58(),
    bump,
  }, null, 2));
}
