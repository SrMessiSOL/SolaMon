import { Connection, PublicKey } from '@solana/web3.js';
import { loadJson } from './keys.mjs';

const [ownerArg] = process.argv.slice(2);
if (!ownerArg) {
  console.error('Usage: node wallet-info.mjs <owner>');
  process.exit(1);
}

const collections = loadJson('devnet-collections.json');
const currency = loadJson('devnet-currency.json');
const connection = new Connection(collections.rpcUrl, 'confirmed');
const owner = new PublicKey(ownerArg);
const mint = new PublicKey(currency.mint);
const solLamports = await connection.getBalance(owner, 'confirmed');
const tokenAccounts = await connection.getParsedTokenAccountsByOwner(owner, { mint });
let tokenAmount = '0';
let tokenUiAmount = 0;
if (tokenAccounts.value.length > 0) {
  const amount = tokenAccounts.value[0].account.data.parsed.info.tokenAmount;
  tokenAmount = amount.amount;
  tokenUiAmount = amount.uiAmount ?? 0;
}

console.log(JSON.stringify({
  owner: owner.toBase58(),
  sol: solLamports / 1_000_000_000,
  lamports: solLamports,
  tokenMint: mint.toBase58(),
  tokenSymbol: currency.symbol ?? 'SLMN',
  tokenAmount,
  tokenUiAmount,
}, null, 2));
