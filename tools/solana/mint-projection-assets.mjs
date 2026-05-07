import fs from "node:fs";
import crypto from "node:crypto";
import { createNft, mplTokenMetadata } from "@metaplex-foundation/mpl-token-metadata";
import {
  createSignerFromKeypair,
  generateSigner,
  keypairIdentity,
  percentAmount,
  publicKey,
} from "@metaplex-foundation/umi";
import { createUmi } from "@metaplex-foundation/umi-bundle-defaults";
import {
  Keypair,
} from "@solana/web3.js";
import { loadJson, saveJson } from "./keys.mjs";
import { requireNftApproval } from "./nft-approval.mjs";

const [projectionPath, registryPath] = process.argv.slice(2);
if (!projectionPath || !registryPath) {
  throw new Error("Usage: node mint-projection-assets.mjs <projection.chain.json> <asset-registry.json>");
}

const collections = loadJson(process.env.SOLAMON_COLLECTIONS_PATH ?? "devnet-collections.json");
const projection = loadJson(projectionPath);
const registry = fs.existsSync(registryPath) ? loadJson(registryPath) : { schema: "solamon-asset-registry-v1", assets: {} };
const owner = projection.character?.owner;
const gameAction = readGameAction();
const STATE_ONLY_ITEM_SLUGS = new Set([
  "friendship_scroll",
  "nu_phone",
  "app_banking",
  "app_map",
  "app_tuxepedia",
]);

if (!owner) {
  throw new Error("Projection is missing character.owner");
}

const umi = createUmi(projection.rpc_url ?? collections.rpcUrl).use(mplTokenMetadata());
const playerSecret = process.env.SOLAMON_SECRET_KEY_BASE64;
const player = playerSecret
  ? Keypair.fromSecretKey(Buffer.from(playerSecret, "base64"))
  : null;

if (!player) {
  throw new Error("SOLAMON_SECRET_KEY_BASE64 is required so the player vault pays NFT mint fees");
}
if (player.publicKey.toBase58() !== owner) {
  throw new Error(`Player vault ${player.publicKey.toBase58()} does not match projection owner ${owner}`);
}

const umiPlayer = umi.eddsa.createKeypairFromSecretKey(player.secretKey);
umi.use(keypairIdentity(createSignerFromKeypair(umi, umiPlayer)));

let mintedCount = 0;
for (const asset of projection.nfts ?? []) {
  if (isStateOnlyItem(asset)) {
    asset.mint_status = "skipped-state-only";
    continue;
  }
  const registryKey = `${owner}:${asset.kind}:${asset.instance_id}`;
  const existing = registry.assets[registryKey];
  if (existing?.mint) {
    existing.latest_state_hash = asset.state_hash;
    asset.mint = existing.mint;
    asset.mint_status = "minted-devnet";
    asset.metadata_uri = existing.uri;
    continue;
  }

  const collectionMint = collectionForAsset(asset, collections);
  if (!collectionMint) {
    asset.mint_status = "skipped-no-collection";
    continue;
  }

  const mint = generateSigner(umi);
  const uri = `https://solamon.local/metadata/${asset.kind}s/${asset.instance_id}.json`;
  await requireNftApproval({
    operation: "mint_asset",
    owner,
    characterMint: projection.character?.character_mint,
    projectionOwner: projection.character?.owner,
    projectionSaveHash: projection.save_hash,
    currentSaveHash: projection.character?.latest_save_hash,
    gameAction,
    asset,
    collectionMint,
    uri,
  });
  await createNft(umi, {
    mint,
    name: assetName(asset),
    symbol: symbolForAsset(asset.kind),
    uri,
    tokenOwner: publicKey(owner),
    sellerFeeBasisPoints: percentAmount(0),
    collection: {
      key: publicKey(collectionMint),
      verified: false,
    },
  }).sendAndConfirm(umi, { confirm: { commitment: "confirmed" } });

  registry.assets[registryKey] = {
    owner,
    kind: asset.kind,
    game_id: asset.game_id,
    instance_id: asset.instance_id,
    collection_mint: collectionMint,
    mint: mint.publicKey.toString(),
    update_authority: owner,
    uri,
    first_state_hash: asset.state_hash,
    latest_state_hash: asset.state_hash,
  };
  asset.mint = mint.publicKey.toString();
  asset.mint_status = "minted-devnet";
  asset.metadata_uri = uri;
  mintedCount += 1;
  projection.asset_registry_path = registryPath;
  projection.projection_hash = stableProjectionHashPlaceholder(projection);
  saveJson(registryPath, registry);
  saveJson(projectionPath, projection);
}

projection.asset_registry_path = registryPath;
projection.projection_hash = stableProjectionHashPlaceholder(projection);
saveJson(registryPath, registry);
saveJson(projectionPath, projection);

console.log(JSON.stringify({
  owner,
  mintedCount,
  assets: Object.values(registry.assets).filter((asset) => asset.owner === owner),
}, null, 2));

function collectionForAsset(asset, collectionConfig) {
  if (asset.collection_mint) {
    return asset.collection_mint;
  }
  if (asset.kind === "item") {
    return collectionConfig.item;
  }
  if (asset.kind === "monster") {
    return collectionConfig.monster;
  }
  if (asset.kind === "badge") {
    return collectionConfig.badge;
  }
  return null;
}

function isStateOnlyItem(asset) {
  return asset.kind === "item" && STATE_ONLY_ITEM_SLUGS.has(String(asset.game_id ?? asset.metadata?.slug ?? ""));
}

function symbolForAsset(kind) {
  if (kind === "item") {
    return "SLITEM";
  }
  if (kind === "monster") {
    return "SLMON";
  }
  if (kind === "badge") {
    return "SLBDG";
  }
  return "SLAST";
}

function assetName(asset) {
  const base = String(asset.game_id ?? asset.kind ?? "asset");
  return base
    .split("_")
    .filter(Boolean)
    .map((part) => part[0]?.toUpperCase() + part.slice(1))
    .join(" ")
    .slice(0, 32);
}

function stableProjectionHashPlaceholder(value) {
  const clone = { ...value };
  delete clone.projection_hash;
  return crypto
    .createHash("sha256")
    .update(JSON.stringify(sortForHash(clone)))
    .digest("hex");
}

function readGameAction() {
  const raw = process.env.SOLAMON_GAME_ACTION;
  return raw ? JSON.parse(raw) : null;
}

function sortForHash(value) {
  if (Array.isArray(value)) {
    return value.map(sortForHash);
  }
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value)
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([key, entry]) => [key, sortForHash(entry)]),
    );
  }
  return value;
}
