import fs from 'node:fs';
import {
  Connection,
  Keypair,
  Transaction,
} from '@solana/web3.js';
import { playerSaveInstructions } from './player-instructions.mjs';
import { writeCompressedCompactSave } from './save-blob.mjs';
import { sendAndConfirmWithAuthority } from './authority-client.mjs';

const [profilePath, projectionPath, compactPath, mode = 'send'] = process.argv.slice(2);

if (!profilePath || !projectionPath || !compactPath) {
  console.error('Usage: node submit-compact-save.mjs <profile.json> <projection.chain.json> <compact.json> [send|dry-run]');
  process.exit(1);
}

const secret = process.env.SOLAMON_SECRET_KEY_BASE64;
if (!secret) {
  console.error('SOLAMON_SECRET_KEY_BASE64 is required');
  process.exit(1);
}

const profile = JSON.parse(fs.readFileSync(profilePath, 'utf8'));
const projection = JSON.parse(fs.readFileSync(projectionPath, 'utf8'));
const compact = JSON.parse(fs.readFileSync(compactPath, 'utf8'));
const gameAction = readGameAction();
const player = Keypair.fromSecretKey(Buffer.from(secret, 'base64'));
const connection = new Connection(projection.rpc_url, 'confirmed');

if (player.publicKey.toBase58() !== profile.owner) {
  throw new Error(`Unlocked wallet ${player.publicKey.toBase58()} does not match character owner ${profile.owner}`);
}

projection.save_hash = compact.hash;
projection.save_blob_uri = `compact:1:${compact.base}`;
projection.projection_path = projectionPath;

const compactWrite = await writeCompressedCompactSave({
  connection,
  payer: player,
  rentPayer: player,
  profile,
  compact,
  gameSlot: 1,
});
const save = await playerSaveInstructions({ connection, profile, projection, gameAction });
const transaction = new Transaction().add(compactWrite.instruction, ...save.instructions);
transaction.feePayer = player.publicKey;
transaction.recentBlockhash = (await connection.getLatestBlockhash()).blockhash;

const signers = [player];
const compactTx = await buildTransaction([compactWrite.instruction]);
const saveTx = await buildTransaction(save.instructions);
const compactWireBytes = transactionWireBytes(compactTx, signers);
const saveWireBytes = transactionWireBytes(saveTx, signers);
if (compactWireBytes > 1232) {
  throw new Error(
    `Compressed compact save transaction is ${compactWireBytes} bytes, max is 1232; use full save fallback`
  );
}
if (saveWireBytes > 1232) {
  throw new Error(
    `Compact save update transaction is ${saveWireBytes} bytes, max is 1232; use full save fallback`
  );
}
const combinedWireBytes = transactionWireBytesOrInfinity(transaction, signers);
const split = combinedWireBytes > 1232;
if (mode === 'dry-run') {
  console.log(JSON.stringify({
    mode,
    split,
    compactHash: compact.hash,
    baseSaveHash: compact.base,
    saveCompact: compactWrite.saveCompact.toBase58(),
    compressedBytes: compactWrite.compressedBytes,
    playerState: save.playerState.toBase58(),
    gameAction,
    instructionCount: transaction.instructions.length,
    messageBytes: Number.isFinite(combinedWireBytes)
      ? transaction.serializeMessage().length
      : null,
    combinedWireBytes,
    compactWireBytes,
    saveWireBytes,
  }, null, 2));
} else if (split) {
  const compactWriteSignature = await connection.sendTransaction(compactTx, signers);
  await connection.confirmTransaction(compactWriteSignature, 'confirmed');
  const saveSignature = await sendAndConfirmWithAuthority({
    connection,
    transaction: saveTx,
    signers,
    gameAction,
  });
  printResult({
    compactWriteSignature,
    saveSignature,
    split: true,
    splitMode: 'compact-write-then-save',
    combinedWireBytes,
  });
} else {
  const signature = await sendAndConfirmWithAuthority({
    connection,
    transaction,
    signers,
    gameAction,
  });
  printResult({ signature, split: false, combinedWireBytes });
}

function readGameAction() {
  const raw = process.env.SOLAMON_GAME_ACTION;
  if (!raw) {
    return null;
  }
  return JSON.parse(raw);
}

async function buildTransaction(instructions) {
  const built = new Transaction().add(...instructions);
  built.feePayer = player.publicKey;
  built.recentBlockhash = (await connection.getLatestBlockhash()).blockhash;
  return built;
}

function printResult(extra) {
  console.log(JSON.stringify({
    mode,
    compactHash: compact.hash,
    baseSaveHash: compact.base,
    saveCompact: compactWrite.saveCompact.toBase58(),
    compressedBytes: compactWrite.compressedBytes,
    playerState: save.playerState.toBase58(),
    gameAction,
    instructionCount: transaction.instructions.length,
    ...extra,
  }, null, 2));
}

function transactionWireBytes(candidate, candidateSigners) {
  candidate.sign(...candidateSigners);
  return shortVecLength(candidate.signatures.length)
    + candidate.signatures.length * 64
    + candidate.serializeMessage().length;
}

function transactionWireBytesOrInfinity(candidate, candidateSigners) {
  try {
    return transactionWireBytes(candidate, candidateSigners);
  } catch (error) {
    if (isTransactionTooLargeError(error)) {
      return Number.POSITIVE_INFINITY;
    }
    throw error;
  }
}

function isTransactionTooLargeError(error) {
  const message = String(error?.message ?? error ?? '');
  return message.includes('offset') && message.includes('out of range');
}

function shortVecLength(value) {
  let remaining = value;
  let length = 0;
  do {
    remaining >>= 7;
    length += 1;
  } while (remaining > 0);
  return length;
}
