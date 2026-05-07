import { createNft, mplTokenMetadata } from "@metaplex-foundation/mpl-token-metadata";
import {
  createSignerFromKeypair,
  generateSigner,
  keypairIdentity,
  percentAmount,
} from "@metaplex-foundation/umi";
import { createUmi } from "@metaplex-foundation/umi-bundle-defaults";
import { Connection, Keypair, LAMPORTS_PER_SOL, PublicKey } from "@solana/web3.js";
import { defaultKeypairPath, keypairExists, loadJson, saveJson } from "./keys.mjs";

const rpcUrl = process.env.SOLAMON_RPC_URL ?? "https://api.devnet.solana.com";
const outputPath = process.env.SOLAMON_COLLECTIONS_PATH ?? "devnet-collections.json";

function loadOrCreateAuthority() {
  if (keypairExists(defaultKeypairPath)) {
    return Keypair.fromSecretKey(Uint8Array.from(loadJson(defaultKeypairPath)));
  }

  const authority = Keypair.generate();
  saveJson(defaultKeypairPath, Array.from(authority.secretKey));
  return authority;
}

async function ensureSol(publicKey) {
  const connection = new Connection(rpcUrl, "confirmed");
  const balance = await connection.getBalance(publicKey);
  if (balance >= 0.08 * LAMPORTS_PER_SOL) {
    return balance;
  }

  throw new Error(
    `Devnet authority needs funding. Send devnet SOL to ${publicKey.toBase58()} and rerun this script.`,
  );
}

async function createCollection(umi, { name, symbol, uri }) {
  const mint = generateSigner(umi);
  await createNft(umi, {
    mint,
    name,
    symbol,
    uri,
    sellerFeeBasisPoints: percentAmount(0),
    isCollection: true,
  }).sendAndConfirm(umi, { confirm: { commitment: "confirmed" } });
  return mint.publicKey.toString();
}

const authority = loadOrCreateAuthority();
await ensureSol(authority.publicKey);

const umi = createUmi(rpcUrl).use(mplTokenMetadata());
const umiAuthority = umi.eddsa.createKeypairFromSecretKey(authority.secretKey);
umi.use(keypairIdentity(createSignerFromKeypair(umi, umiAuthority)));

const collections = {
  rpcUrl,
  authority: authority.publicKey.toBase58(),
  character: await createCollection(umi, {
    name: "Solamon Characters",
    symbol: "SLCHAR",
    uri: "https://solamon.local/metadata/characters-collection.json",
  }),
  item: await createCollection(umi, {
    name: "Solamon Items",
    symbol: "SLITEM",
    uri: "https://solamon.local/metadata/items-collection.json",
  }),
  monster: await createCollection(umi, {
    name: "Solamon Monsters",
    symbol: "SLMON",
    uri: "https://solamon.local/metadata/monsters-collection.json",
  }),
  badge: await createCollection(umi, {
    name: "Solamon Badges",
    symbol: "SLBDG",
    uri: "https://solamon.local/metadata/badges-collection.json",
  }),
};

for (const [key, value] of Object.entries(collections)) {
  if (key !== "rpcUrl" && key !== "authority") {
    new PublicKey(value);
  }
}

saveJson(outputPath, collections);
console.log(JSON.stringify(collections, null, 2));
