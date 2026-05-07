import fs from 'node:fs';
import {
  Connection,
  Keypair,
  PublicKey,
  Transaction,
  TransactionInstruction,
  sendAndConfirmTransaction,
} from '@solana/web3.js';
import { loadJson } from './keys.mjs';
import { requireNftApproval } from './nft-approval.mjs';

const TOKEN_PROGRAM_ID = new PublicKey('TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA');
const [registryPath, ownerArg, kind, instanceId, characterMintArg = '', mode = 'send'] = process.argv.slice(2);

if (!registryPath || !ownerArg || !kind || !instanceId) {
  console.error('Usage: node burn-asset.mjs <registry.json> <owner> <kind> <instanceId> [characterMint] [send|dry-run]');
  process.exit(1);
}

const secret = process.env.SOLAMON_SECRET_KEY_BASE64;
if (!secret) {
  throw new Error('SOLAMON_SECRET_KEY_BASE64 is required');
}

const registry = loadJson(registryPath);
const collections = loadJson('devnet-collections.json');
const owner = new PublicKey(ownerArg);
const signer = Keypair.fromSecretKey(Buffer.from(secret, 'base64'));
if (signer.publicKey.toBase58() !== owner.toBase58()) {
  throw new Error(`Signer ${signer.publicKey.toBase58()} does not match owner ${owner.toBase58()}`);
}

const registryKey = `${owner.toBase58()}:${kind}:${instanceId}`;
const asset = registry.assets?.[registryKey];
if (!asset?.mint) {
  throw new Error(`Missing asset registry entry ${registryKey}`);
}

const connection = new Connection(collections.rpcUrl, 'confirmed');
const mint = new PublicKey(asset.mint);
const tokenAccount = await findTokenAccount(connection, owner, mint);
if (mode !== 'dry-run') {
  await requireNftApproval({
    operation: 'burn_asset',
    owner: owner.toBase58(),
    kind,
    instanceId,
    characterMint: characterMintArg,
    mint: mint.toBase58(),
    burnedAsset: asset,
  });
}
const transaction = new Transaction().add(createBurnInstruction({
  tokenAccount,
  mint,
  owner,
  amount: 1n,
}));
transaction.feePayer = owner;
transaction.recentBlockhash = (await connection.getLatestBlockhash()).blockhash;

if (mode === 'dry-run') {
  console.log(JSON.stringify({
    mode,
    registryKey,
    mint: mint.toBase58(),
    tokenAccount: tokenAccount.toBase58(),
    messageBytes: transaction.serializeMessage().length,
  }, null, 2));
} else {
  const signature = await sendAndConfirmTransaction(connection, transaction, [signer], {
    commitment: 'confirmed',
  });
  asset.burned_at = new Date().toISOString();
  asset.burn_signature = signature;
  fs.writeFileSync(registryPath, `${JSON.stringify(registry, null, 2)}\n`);
  console.log(JSON.stringify({
    mode,
    signature,
    registryKey,
    mint: mint.toBase58(),
    tokenAccount: tokenAccount.toBase58(),
  }, null, 2));
}

async function findTokenAccount(connection, owner, mint) {
  const response = await connection.getTokenAccountsByOwner(owner, { mint });
  for (const { pubkey, account } of response.value) {
    const amount = account.data.readBigUInt64LE(64);
    if (amount > 0n) {
      return pubkey;
    }
  }
  throw new Error(`No token account holding ${mint.toBase58()} for ${owner.toBase58()}`);
}

function createBurnInstruction({ tokenAccount, mint, owner, amount }) {
  const data = Buffer.alloc(9);
  data.writeUInt8(8, 0);
  data.writeBigUInt64LE(amount, 1);
  return new TransactionInstruction({
    programId: TOKEN_PROGRAM_ID,
    keys: [
      { pubkey: tokenAccount, isSigner: false, isWritable: true },
      { pubkey: mint, isSigner: false, isWritable: true },
      { pubkey: owner, isSigner: true, isWritable: false },
    ],
    data,
  });
}
