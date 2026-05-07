import fs from 'node:fs';
import {
  Connection,
  Keypair,
  PublicKey,
  Transaction,
} from '@solana/web3.js';
import {
  playerSaveInstructions,
} from './player-instructions.mjs';
import { writeCompressedSaveBlob } from './save-blob.mjs';
import { sendAndConfirmWithAuthority } from './authority-client.mjs';

const [profilePath, projectionPath, mode = 'send'] = process.argv.slice(2);

if (!profilePath || !projectionPath) {
  console.error('Usage: node submit-player-save.mjs <profile.json> <projection.chain.json> [send|dry-run]');
  process.exit(1);
}

const secret = process.env.SOLAMON_SECRET_KEY_BASE64;
if (!secret) {
  console.error('SOLAMON_SECRET_KEY_BASE64 is required');
  process.exit(1);
}

const profile = JSON.parse(fs.readFileSync(profilePath, 'utf8'));
const projection = JSON.parse(fs.readFileSync(projectionPath, 'utf8'));
const gameAction = readGameAction();
const payer = Keypair.fromSecretKey(Buffer.from(secret, 'base64'));
const connection = new Connection(projection.rpc_url, 'confirmed');

if (payer.publicKey.toBase58() !== profile.owner) {
  throw new Error(`Unlocked wallet ${payer.publicKey.toBase58()} does not match character owner ${profile.owner}`);
}

const owner = new PublicKey(profile.owner);
const characterMint = new PublicKey(profile.character_mint);
projection.projection_path = projectionPath;
const { instructions, playerState } = await playerSaveInstructions({
  connection,
  profile,
  projection,
  gameAction,
});

const transaction = new Transaction().add(...instructions);
transaction.feePayer = payer.publicKey;
transaction.recentBlockhash = (await connection.getLatestBlockhash()).blockhash;

if (mode === 'dry-run') {
  const message = transaction.serializeMessage();
  console.log(JSON.stringify({
    mode,
    owner: owner.toBase58(),
    characterMint: characterMint.toBase58(),
    playerState: playerState.toBase58(),
    gameAction,
    instructionCount: instructions.length,
    messageBytes: message.length,
  }, null, 2));
} else {
  const blob = await writeCompressedSaveBlob({
    connection,
    payer,
    rentPayer: payer,
    profile,
    projection,
  });
  const signature = await sendAndConfirmWithAuthority({
    connection,
    transaction,
    signers: [payer],
    gameAction,
  });
  console.log(JSON.stringify({
    mode,
    signature,
    blob,
    owner: owner.toBase58(),
    characterMint: characterMint.toBase58(),
    playerState: playerState.toBase58(),
    gameAction,
    instructionCount: instructions.length,
  }, null, 2));
}

function readGameAction() {
  const raw = process.env.SOLAMON_GAME_ACTION;
  if (!raw) {
    return null;
  }
  return JSON.parse(raw);
}
