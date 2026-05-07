import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import crypto from 'node:crypto';
import {
  fetchMetadataFromSeeds,
  findMetadataPda,
  mplTokenMetadata,
  updateMetadataAccountV2,
} from '@metaplex-foundation/mpl-token-metadata';
import {
  createSignerFromKeypair,
  keypairIdentity,
  publicKey,
  some,
} from '@metaplex-foundation/umi';
import { createUmi } from '@metaplex-foundation/umi-bundle-defaults';
import { Keypair } from '@solana/web3.js';
import { defaultKeypairPath, loadJson, saveJson } from './keys.mjs';
import { requireNftApproval } from './nft-approval.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const [projectionPath, registryPath, mode = 'send', kindFilterArg = 'all'] = process.argv.slice(2);

if (!projectionPath || !registryPath) {
  console.error('Usage: node sync-nft-metadata.mjs <projection.chain.json> <asset-registry.json> [send|dry-run] [all|item,monster,badge]');
  process.exit(1);
}

const projection = loadJson(projectionPath);
const registry = fs.existsSync(registryPath)
  ? loadJson(registryPath)
  : { schema: 'solamon-asset-registry-v1', assets: {} };
const owner = projection.character?.owner;

if (!owner) {
  throw new Error('Projection is missing character.owner');
}

const umi = createUmi(projection.rpc_url).use(mplTokenMetadata());
const playerSecret = process.env.SOLAMON_SECRET_KEY_BASE64;
const player = playerSecret
  ? Keypair.fromSecretKey(Buffer.from(playerSecret, 'base64'))
  : null;

if (mode !== 'dry-run' && !player) {
  throw new Error('SOLAMON_SECRET_KEY_BASE64 is required so the player vault pays NFT metadata fees');
}
if (player && player.publicKey.toBase58() !== owner) {
  throw new Error(`Player vault ${player.publicKey.toBase58()} does not match projection owner ${owner}`);
}
if (player) {
  const umiPlayer = umi.eddsa.createKeypairFromSecretKey(player.secretKey);
  umi.use(keypairIdentity(createSignerFromKeypair(umi, umiPlayer)));
}

const legacyAuthority = loadLegacyAuthoritySigner();

const metadataDir = path.join(__dirname, 'metadata');
fs.mkdirSync(metadataDir, { recursive: true });
const kindFilter = kindFilterArg === 'all'
  ? null
  : new Set(kindFilterArg.split(',').map((kind) => kind.trim()).filter(Boolean));
const STATE_ONLY_ITEM_SLUGS = new Set([
  'friendship_scroll',
  'nu_phone',
  'app_banking',
  'app_map',
  'app_tuxepedia',
]);

const synced = [];
const skipped = [];

for (const asset of projection.nfts ?? []) {
  if (isStateOnlyItem(asset)) {
    skipped.push({
      kind: asset.kind,
      gameId: asset.game_id,
      instanceId: asset.instance_id,
      reason: 'state-only-item',
    });
    continue;
  }
  if (kindFilter && !kindFilter.has(asset.kind)) {
    skipped.push({
      kind: asset.kind,
      gameId: asset.game_id,
      instanceId: asset.instance_id,
      reason: 'kind-filter',
    });
    continue;
  }

  const registryKey = `${owner}:${asset.kind}:${asset.instance_id}`;
  const existing = registry.assets[registryKey];
  if (!existing?.mint) {
    skipped.push({ registryKey, reason: 'missing-mint' });
    continue;
  }

  const metadata = buildMetadataJson(asset, existing.mint);
  const localPath = path.join(metadataDir, `${asset.kind}-${asset.instance_id}-${asset.state_hash}.json`);
  saveJson(localPath, metadata);

  const uri = `https://solamon.local/metadata/${asset.kind}s/${asset.instance_id}-${asset.state_hash}.json`;
  if (mode !== 'dry-run') {
    await requireNftApproval({
      operation: 'sync_metadata',
      owner,
      characterMint: projection.character?.character_mint,
      projectionOwner: projection.character?.owner,
      projectionSaveHash: projection.save_hash,
      asset,
      mint: existing.mint,
      uri,
    });
  }
  if (
    mode !== 'dry-run'
    && existing.latest_state_hash === asset.state_hash
    && existing.uri === uri
  ) {
    asset.mint = existing.mint;
    asset.mint_status = 'minted-devnet';
    asset.metadata_uri = uri;
    synced.push({
      kind: asset.kind,
      gameId: asset.game_id,
      instanceId: asset.instance_id,
      mint: existing.mint,
      stateHash: asset.state_hash,
      uri,
      changed: false,
      source: 'registry',
    });
    continue;
  }

  const metadataPda = findMetadataPda(umi, { mint: publicKey(existing.mint) });
  const current = await fetchMetadataFromSeeds(umi, { mint: publicKey(existing.mint) });
  const currentUpdateAuthority = current.updateAuthority?.toString?.() ?? String(current.updateAuthority);
  const updateAuthority = existing.update_authority ?? currentUpdateAuthority;
  const updateAuthoritySigner = signerForUpdateAuthority(currentUpdateAuthority);

  if (mode !== 'dry-run' && current.uri !== uri) {
    if (!updateAuthoritySigner) {
      skipped.push({
        registryKey,
        reason: 'missing-update-authority-signer',
        mint: existing.mint,
        updateAuthority: currentUpdateAuthority,
      });
      continue;
    }
    await updateMetadataAccountV2(umi, {
      metadata: metadataPda,
      data: some({
        name: current.name,
        symbol: current.symbol,
        uri,
        sellerFeeBasisPoints: current.sellerFeeBasisPoints,
        creators: current.creators,
        collection: current.collection,
        uses: current.uses,
      }),
      updateAuthority: updateAuthoritySigner,
    }).sendAndConfirm(umi, { confirm: { commitment: 'confirmed' } });
  }

  existing.latest_state_hash = asset.state_hash;
  existing.uri = uri;
  existing.local_metadata_path = localPath;
  existing.update_authority = updateAuthority;
  asset.mint = existing.mint;
  asset.mint_status = 'minted-devnet';
  asset.metadata_uri = uri;

  synced.push({
    kind: asset.kind,
    gameId: asset.game_id,
    instanceId: asset.instance_id,
    mint: existing.mint,
    stateHash: asset.state_hash,
    uri,
    changed: current.uri !== uri,
    updateAuthority: currentUpdateAuthority,
  });
}

projection.asset_registry_path = registryPath;
projection.projection_hash = stableProjectionHashPlaceholder(projection);
if (mode !== 'dry-run') {
  saveJson(registryPath, registry);
  saveJson(projectionPath, projection);
}

console.log(JSON.stringify({ owner, mode, synced, skipped }, null, 2));

function loadLegacyAuthoritySigner() {
  if (!fs.existsSync(defaultKeypairPath)) {
    return null;
  }
  try {
    const authority = Keypair.fromSecretKey(Uint8Array.from(loadJson(defaultKeypairPath)));
    const umiAuthority = umi.eddsa.createKeypairFromSecretKey(authority.secretKey);
    return {
      address: authority.publicKey.toBase58(),
      signer: createSignerFromKeypair(umi, umiAuthority),
    };
  } catch {
    return null;
  }
}

function signerForUpdateAuthority(updateAuthority) {
  if (!player) {
    return null;
  }
  if (updateAuthority === player.publicKey.toBase58()) {
    return umi.identity;
  }
  if (legacyAuthority?.address === updateAuthority) {
    return legacyAuthority.signer;
  }
  return null;
}

function buildMetadataJson(asset, mint) {
  const attributes = Object.entries(flattenAttributes(asset.metadata ?? {}))
    .filter(([, value]) => value !== undefined && value !== null && value !== '')
    .map(([trait_type, value]) => ({ trait_type, value }));
  return {
    name: assetName(asset),
    symbol: symbolForAsset(asset.kind),
    description: `Solamon ${asset.kind} synchronized from on-chain game state.`,
    image: '',
    external_url: 'https://solamon.local',
    mint,
    attributes,
  };
}

function flattenAttributes(metadata) {
  const out = {
    Kind: metadata.kind,
    Slug: metadata.slug,
    Location: metadata.location,
    Quantity: metadata.quantity,
    Wear: metadata.wear,
    Level: metadata.level,
    EXP: metadata.total_experience,
    HP: metadata.current_hp,
    Status: Array.isArray(metadata.status)
      ? metadata.status.map((entry) => entry.slug ?? entry).join(',')
      : '',
  };
  return out;
}

function symbolForAsset(kind) {
  if (kind === 'item') return 'SLITEM';
  if (kind === 'monster') return 'SLMON';
  if (kind === 'badge') return 'SLBDG';
  return 'SLAST';
}

function isStateOnlyItem(asset) {
  return asset.kind === 'item' && STATE_ONLY_ITEM_SLUGS.has(String(asset.game_id ?? asset.metadata?.slug ?? ''));
}

function assetName(asset) {
  const base = String(asset.game_id ?? asset.kind ?? 'asset');
  return base
    .split('_')
    .filter(Boolean)
    .map((part) => part[0]?.toUpperCase() + part.slice(1))
    .join(' ')
    .slice(0, 32);
}

function stableProjectionHashPlaceholder(value) {
  const clone = { ...value };
  delete clone.projection_hash;
  return crypto
    .createHash('sha256')
    .update(JSON.stringify(sortForHash(clone)))
    .digest('hex');
}

function sortForHash(value) {
  if (Array.isArray(value)) return value.map(sortForHash);
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value)
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([key, entry]) => [key, sortForHash(entry)]),
    );
  }
  return value;
}
