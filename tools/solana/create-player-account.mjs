import fs from 'node:fs';
import {
  Connection,
  Keypair,
  PublicKey,
  Transaction,
} from '@solana/web3.js';
import {
  createPlayerInstruction,
  derivePlayerStatePda,
} from './player-instructions.mjs';
import { loadJson } from './keys.mjs';
import { sendAndConfirmWithAuthority } from './authority-client.mjs';

const [profilePath, mode = 'send'] = process.argv.slice(2);
if (!profilePath) {
  console.error('Usage: node create-player-account.mjs <profile.json> [send|dry-run]');
  process.exit(1);
}

const secret = process.env.SOLAMON_SECRET_KEY_BASE64;
if (!secret) {
  console.error('SOLAMON_SECRET_KEY_BASE64 is required');
  process.exit(1);
}

const collections = loadJson('devnet-collections.json');
const profile = JSON.parse(fs.readFileSync(profilePath, 'utf8'));
const payer = Keypair.fromSecretKey(Buffer.from(secret, 'base64'));
const connection = new Connection(collections.rpcUrl, 'confirmed');

if (payer.publicKey.toBase58() !== profile.owner) {
  throw new Error(`Unlocked wallet ${payer.publicKey.toBase58()} does not match character owner ${profile.owner}`);
}

const owner = new PublicKey(profile.owner);
const characterMint = new PublicKey(profile.character_mint);
const [playerState] = derivePlayerStatePda(owner, characterMint);
const existing = await connection.getAccountInfo(playerState);

if (existing) {
  console.log(JSON.stringify({
    mode: 'already-created',
    owner: owner.toBase58(),
    characterMint: characterMint.toBase58(),
    playerState: playerState.toBase58(),
  }, null, 2));
  process.exit(0);
}

const instruction = await createPlayerInstruction({
  connection,
  owner,
  characterMint,
  name: profile.name,
  avatar: profile.avatar_slug,
});
const transaction = new Transaction().add(instruction);
transaction.feePayer = payer.publicKey;
transaction.recentBlockhash = (await connection.getLatestBlockhash()).blockhash;

if (mode === 'dry-run') {
  const message = transaction.serializeMessage();
  console.log(JSON.stringify({
    mode,
    owner: owner.toBase58(),
    characterMint: characterMint.toBase58(),
    playerState: playerState.toBase58(),
    instructionCount: 1,
    messageBytes: message.length,
  }, null, 2));
} else {
  const signature = await sendAndConfirmWithAuthority({
    connection,
    transaction,
    signers: [payer],
  });
  console.log(JSON.stringify({
    mode,
    signature,
    owner: owner.toBase58(),
    characterMint: characterMint.toBase58(),
    playerState: playerState.toBase58(),
  }, null, 2));
}
