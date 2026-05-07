import crypto from 'node:crypto';
import fs from 'node:fs';
import {
  Connection,
  Keypair,
  PublicKey,
  SystemProgram,
  Transaction,
  sendAndConfirmTransaction,
} from '@solana/web3.js';

const [destinationText, ...walletArgs] = process.argv.slice(2);

if (!destinationText || walletArgs.length === 0) {
  console.error(
    'Usage: node sweep-encrypted-wallets.mjs <destination> <wallet.json=password>...',
  );
  process.exit(1);
}

const destination = new PublicKey(destinationText);
const connection = new Connection('https://api.devnet.solana.com', 'confirmed');
const FEE_BUFFER_LAMPORTS = 5000;

for (const arg of walletArgs) {
  const separator = arg.indexOf('=');
  if (separator <= 0) {
    throw new Error(`Expected wallet argument as path=password: ${arg}`);
  }
  const walletPath = arg.slice(0, separator);
  const password = arg.slice(separator + 1);
  const encrypted = JSON.parse(fs.readFileSync(walletPath, 'utf8'));
  const keypair = decryptWallet(encrypted, password);
  const balance = await connection.getBalance(keypair.publicKey);
  const amount = Math.max(0, balance - FEE_BUFFER_LAMPORTS);
  if (amount <= 0) {
    console.log(JSON.stringify({
      walletPath,
      source: keypair.publicKey.toBase58(),
      balance,
      skipped: true,
      reason: 'balance below fee buffer',
    }));
    continue;
  }

  const tx = new Transaction().add(
    SystemProgram.transfer({
      fromPubkey: keypair.publicKey,
      toPubkey: destination,
      lamports: amount,
    }),
  );
  const signature = await sendAndConfirmTransaction(connection, tx, [keypair], {
    commitment: 'confirmed',
  });
  console.log(JSON.stringify({
    walletPath,
    source: keypair.publicKey.toBase58(),
    destination: destination.toBase58(),
    previousBalance: balance,
    transferred: amount,
    retainedForFee: balance - amount,
    signature,
  }));
}

function decryptWallet(encrypted, password) {
  if (encrypted.kdf !== 'pbkdf2-hmac-sha256+aes-256-gcm') {
    throw new Error(`Unsupported wallet KDF: ${encrypted.kdf}`);
  }
  const key = crypto.pbkdf2Sync(
    Buffer.from(password, 'utf8'),
    Buffer.from(encrypted.salt, 'base64'),
    Number(encrypted.iterations),
    32,
    'sha256',
  );
  const ciphertext = Buffer.from(encrypted.ciphertext, 'base64');
  if (ciphertext.length < 17) {
    throw new Error('Encrypted wallet payload is too short');
  }
  const data = ciphertext.subarray(0, ciphertext.length - 16);
  const tag = ciphertext.subarray(ciphertext.length - 16);
  const decipher = crypto.createDecipheriv(
    'aes-256-gcm',
    key,
    Buffer.from(encrypted.nonce, 'base64'),
  );
  decipher.setAAD(Buffer.from(encrypted.schema, 'utf8'));
  decipher.setAuthTag(tag);
  const raw = Buffer.concat([decipher.update(data), decipher.final()]);
  if (raw.length === 32) {
    return Keypair.fromSeed(raw);
  }
  return Keypair.fromSecretKey(raw);
}
