import {
  Connection,
  Keypair,
  PublicKey,
  SystemProgram,
  Transaction,
  sendAndConfirmTransaction,
} from '@solana/web3.js';
import { loadJson } from './keys.mjs';

const [destinationArg, amountText, mode = 'send'] = process.argv.slice(2);

if (!destinationArg || !amountText) {
  console.error('Usage: node transfer-sol.mjs <destination> <amountSol> [send|dry-run]');
  process.exit(1);
}

const secret = process.env.SOLAMON_SECRET_KEY_BASE64;
if (!secret) {
  throw new Error('SOLAMON_SECRET_KEY_BASE64 is required');
}

const currencyConfig = loadJson('devnet-currency.json');
const connection = new Connection(currencyConfig.rpcUrl, 'confirmed');
const payer = Keypair.fromSecretKey(Buffer.from(secret, 'base64'));
const destination = new PublicKey(destinationArg);
const lamports = solToLamports(amountText);

const transaction = new Transaction().add(
  SystemProgram.transfer({
    fromPubkey: payer.publicKey,
    toPubkey: destination,
    lamports,
  }),
);
transaction.feePayer = payer.publicKey;
transaction.recentBlockhash = (await connection.getLatestBlockhash()).blockhash;

if (mode === 'dry-run') {
  console.log(JSON.stringify({
    mode,
    from: payer.publicKey.toBase58(),
    destination: destination.toBase58(),
    amountSol: amountText,
    lamports,
    messageBytes: transaction.serializeMessage().length,
  }, null, 2));
} else {
  const signature = await sendAndConfirmTransaction(connection, transaction, [payer], {
    commitment: 'confirmed',
  });
  console.log(JSON.stringify({
    mode,
    signature,
    from: payer.publicKey.toBase58(),
    destination: destination.toBase58(),
    amountSol: amountText,
    lamports,
  }, null, 2));
}

function solToLamports(value) {
  const [whole, fractional = ''] = String(value).trim().split('.');
  if (!whole || !/^\d+$/.test(whole) || !/^\d*$/.test(fractional)) {
    throw new Error(`Invalid SOL amount: ${value}`);
  }
  if (fractional.length > 9) {
    throw new Error('SOL amount can have at most 9 decimal places');
  }
  const padded = `${fractional}${'0'.repeat(9)}`.slice(0, 9);
  return Number(BigInt(whole) * 1_000_000_000n + BigInt(padded || '0'));
}
