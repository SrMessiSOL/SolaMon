import fs from 'node:fs';
import {
  Connection,
  Keypair,
  PublicKey,
  SystemProgram,
  Transaction,
  TransactionInstruction,
} from '@solana/web3.js';
import { loadJson, defaultKeypairPath } from './keys.mjs';
import { playerSaveInstructions } from './player-instructions.mjs';
import { writeCompressedCompactSave } from './save-blob.mjs';
import { sendAndConfirmWithAuthority } from './authority-client.mjs';

const TOKEN_PROGRAM_ID = new PublicKey('TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA');
const ASSOCIATED_TOKEN_PROGRAM_ID = new PublicKey('ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL');

const [profilePath, projectionPath, compactPath, direction, amountText, mode = 'send'] = process.argv.slice(2);

if (!profilePath || !projectionPath || !compactPath || !direction || !amountText) {
  console.error('Usage: node currency-compact-transaction-and-save.mjs <profile.json> <projection.chain.json> <compact.json> <reward|spend> <uiAmount> [send|dry-run|simulate]');
  process.exit(1);
}
if (!['reward', 'spend'].includes(direction)) {
  throw new Error('direction must be reward or spend');
}

const playerSecret = process.env.SOLAMON_SECRET_KEY_BASE64;
if (!playerSecret) {
  throw new Error('SOLAMON_SECRET_KEY_BASE64 is required');
}

const profile = loadJson(profilePath);
const projection = loadJson(projectionPath);
const compact = loadJson(compactPath);
const currencyConfig = loadJson('devnet-currency.json');
const gameAction = readGameAction(direction);
const player = Keypair.fromSecretKey(Buffer.from(playerSecret, 'base64'));
const treasury = Keypair.fromSecretKey(Uint8Array.from(loadJson(defaultKeypairPath)));
const connection = new Connection(projection.rpc_url ?? currencyConfig.rpcUrl, 'confirmed');

if (player.publicKey.toBase58() !== profile.owner) {
  throw new Error(`Unlocked wallet ${player.publicKey.toBase58()} does not match character owner ${profile.owner}`);
}

const mint = new PublicKey(projection.currency?.mint ?? currencyConfig.mint);
const treasuryTokenAccount = new PublicKey(
  projection.currency?.treasury_token_account ?? currencyConfig.treasuryTokenAccount,
);
const playerTokenAccount = associatedTokenAddress(player.publicKey, mint);
const rawAmount = uiAmountToRaw(amountText, projection.currency?.decimals ?? currencyConfig.decimals);

const currencyInstructions = [];
if (direction === 'reward') {
  await ensureTokenAccountInstruction({
    connection,
    instructions: currencyInstructions,
    payer: player.publicKey,
    owner: player.publicKey,
    mint,
    tokenAccount: playerTokenAccount,
  });
  currencyInstructions.push(createTokenTransferInstruction({
    source: treasuryTokenAccount,
    destination: playerTokenAccount,
    owner: treasury.publicKey,
    amount: rawAmount,
  }));
} else {
  // Player spends are enforced by the Solamon program's spend-and-save
  // instruction so the save cannot land without the SLMN transfer.
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
const save = await playerSaveInstructions({
  connection,
  profile,
  projection,
  spendCurrency: direction === 'spend'
    ? {
        currencyMint: mint,
        playerTokenAccount,
        treasuryTokenAccount,
        amount: rawAmount,
      }
    : null,
  gameAction,
});
const compactInstructions = [compactWrite.instruction, ...save.instructions];
const signers = uniqueSigners([treasury, player]);

const combined = await buildTransaction([...currencyInstructions, ...compactInstructions]);
const combinedMessageBytes = combined.serializeMessage().length;
const combinedWireBytes = transactionWireBytes(combined, signers);
const split = combinedWireBytes > 1232;

if (mode === 'dry-run' || mode === 'simulate') {
  const compactTx = await buildTransaction(compactInstructions);
  const compactWireBytes = transactionWireBytes(compactTx, signers);
  let simulation = null;
  if (mode === 'simulate') {
    const txToSimulate = split ? compactTx : combined;
    txToSimulate.sign(...signers);
    const response = await connection.simulateTransaction(txToSimulate);
    simulation = {
      simulated: split ? 'compact-only-after-split' : 'combined',
      ...response.value,
    };
  }
  console.log(JSON.stringify({
    mode,
    direction,
    amount: amountText,
    rawAmount: rawAmount.toString(),
    split,
    compactHash: compact.hash,
    baseSaveHash: compact.base,
    saveCompact: compactWrite.saveCompact.toBase58(),
    compressedBytes: compactWrite.compressedBytes,
    combinedMessageBytes,
    combinedWireBytes,
    compactMessageBytes: compactTx.serializeMessage().length,
    compactWireBytes,
    playerState: save.playerState.toBase58(),
    gameAction,
    simulation,
  }, null, 2));
} else if (split) {
  if (direction === 'spend') {
    const compactWriteTx = await buildTransaction([compactWrite.instruction]);
    const spendSaveTx = await buildTransaction(save.instructions);
    const compactWriteSignature = await connection.sendTransaction(compactWriteTx, signers);
    await connection.confirmTransaction(compactWriteSignature, 'confirmed');
    const saveSignature = await sendAndConfirmWithAuthority({
      connection,
      transaction: spendSaveTx,
      signers,
      gameAction,
    });
    printResult({ compactWriteSignature, saveSignature, split: true, splitMode: 'compact-write-then-spend-save' });
  } else {
    const currencyTx = await buildTransaction(currencyInstructions);
    const compactTx = await buildTransaction(compactInstructions);
    const saveSignature = await sendAndConfirmWithAuthority({
      connection,
      transaction: compactTx,
      signers,
      gameAction,
    });
    const currencySignature = await connection.sendTransaction(currencyTx, signers);
    await connection.confirmTransaction(currencySignature, 'confirmed');
    printResult({ saveSignature, currencySignature, split: true, splitMode: 'compact-save-then-currency' });
  }
} else {
  const signature = await sendAndConfirmWithAuthority({
    connection,
    transaction: combined,
    signers,
    gameAction,
  });
  printResult({ signature, split: false });
}

async function buildTransaction(instructions) {
  const transaction = new Transaction().add(...instructions);
  transaction.feePayer = player.publicKey;
  transaction.recentBlockhash = (await connection.getLatestBlockhash()).blockhash;
  return transaction;
}

function printResult(extra) {
  console.log(JSON.stringify({
    mode,
    direction,
    amount: amountText,
    rawAmount: rawAmount.toString(),
    compactHash: compact.hash,
    baseSaveHash: compact.base,
    saveCompact: compactWrite.saveCompact.toBase58(),
    compressedBytes: compactWrite.compressedBytes,
    mint: mint.toBase58(),
    playerTokenAccount: playerTokenAccount.toBase58(),
    treasuryTokenAccount: treasuryTokenAccount.toBase58(),
    playerState: save.playerState.toBase58(),
    gameAction,
    instructionCount: (currencyInstructions.length + compactInstructions.length),
    ...extra,
  }, null, 2));
}

function readGameAction(direction) {
  const raw = process.env.SOLAMON_GAME_ACTION;
  if (raw) {
    return JSON.parse(raw);
  }
  return direction === 'reward'
    ? { kind: 'battle_result', primaryId: 'battle_reward', amount: 1 }
    : null;
}

async function ensureTokenAccountInstruction({
  connection,
  instructions,
  payer,
  owner,
  mint,
  tokenAccount,
}) {
  const existing = await connection.getAccountInfo(tokenAccount);
  if (existing) {
    return;
  }
  instructions.push(new TransactionInstruction({
    programId: ASSOCIATED_TOKEN_PROGRAM_ID,
    keys: [
      { pubkey: payer, isSigner: true, isWritable: true },
      { pubkey: tokenAccount, isSigner: false, isWritable: true },
      { pubkey: owner, isSigner: false, isWritable: false },
      { pubkey: mint, isSigner: false, isWritable: false },
      { pubkey: SystemProgram.programId, isSigner: false, isWritable: false },
      { pubkey: TOKEN_PROGRAM_ID, isSigner: false, isWritable: false },
    ],
    data: Buffer.from([1]),
  }));
}

function createTokenTransferInstruction({ source, destination, owner, amount }) {
  const data = Buffer.alloc(9);
  data.writeUInt8(3, 0);
  data.writeBigUInt64LE(amount, 1);
  return new TransactionInstruction({
    programId: TOKEN_PROGRAM_ID,
    keys: [
      { pubkey: source, isSigner: false, isWritable: true },
      { pubkey: destination, isSigner: false, isWritable: true },
      { pubkey: owner, isSigner: true, isWritable: false },
    ],
    data,
  });
}

function associatedTokenAddress(owner, mint) {
  return PublicKey.findProgramAddressSync(
    [owner.toBuffer(), TOKEN_PROGRAM_ID.toBuffer(), mint.toBuffer()],
    ASSOCIATED_TOKEN_PROGRAM_ID,
  )[0];
}

function uiAmountToRaw(value, decimals) {
  const [whole, fractional = ''] = String(value).split('.');
  if (fractional.length > decimals) {
    throw new Error(`Amount ${value} has more than ${decimals} decimals`);
  }
  const padded = `${fractional}${'0'.repeat(decimals)}`.slice(0, decimals);
  return BigInt(whole) * (10n ** BigInt(decimals)) + BigInt(padded || '0');
}

function uniqueSigners(signers) {
  const map = new Map();
  for (const signer of signers) {
    map.set(signer.publicKey.toBase58(), signer);
  }
  return [...map.values()];
}

function transactionWireBytes(transaction, signers) {
  transaction.sign(...signers);
  return shortVecLength(transaction.signatures.length)
    + transaction.signatures.length * 64
    + transaction.serializeMessage().length;
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
