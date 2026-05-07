import http from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import zlib from 'node:zlib';
import {
  Connection,
  PublicKey,
  Transaction,
} from '@solana/web3.js';
import { loadJson } from './keys.mjs';
import {
  SOLAMON_GAME_AUTHORITY,
  SOLAMON_PROGRAM_ID,
  derivePlayerStatePda,
  gameAuthorityKeypair,
} from './player-instructions.mjs';
import {
  COMPRESSION_GZIP_JSON,
  deriveSaveCompact,
  readCompressedCompactSave,
  readCompressedSaveBlob,
} from './save-blob.mjs';

const PORT = Number(process.env.SOLAMON_AUTHORITY_PORT ?? 40082);
const HOST = process.env.SOLAMON_AUTHORITY_HOST ?? '127.0.0.1';
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const GAME_AUTHORITY = gameAuthorityKeypair();
const COLLECTIONS = loadJson(path.join(__dirname, 'devnet-collections.json'));
const CONNECTION = new Connection(process.env.SOLAMON_RPC_URL ?? COLLECTIONS.rpcUrl, 'confirmed');
const ACTION_TAGS = {
  choose_starter: { tag: 7, code: 1, spends: false },
  grant_items: { tag: 8, code: 2, spends: false },
  battle_result: { tag: 9, code: 3, spends: false },
  catch_solamon: { tag: 10, code: 4, spends: false },
  buy_item: { tag: 11, code: 5, spends: true },
  sell_item: { tag: 12, code: 6, spends: false },
  heal_party: { tag: 13, code: 7, spends: true },
  release_solamon: { tag: 14, code: 8, spends: false },
};
const ACTION_TAG_VALUES = new Set(Object.values(ACTION_TAGS).map((entry) => entry.tag));
const STATE_ONLY_BATTLE_REWARD_ITEMS = new Set([
  'friendship_scroll',
]);
const STATE_ONLY_UTILITY_ITEMS = new Set([
  'friendship_scroll',
  'nu_phone',
  'app_banking',
  'app_map',
  'app_tuxepedia',
]);

if (!GAME_AUTHORITY.publicKey.equals(SOLAMON_GAME_AUTHORITY)) {
  throw new Error(
    `Authority key ${GAME_AUTHORITY.publicKey.toBase58()} does not match ${SOLAMON_GAME_AUTHORITY.toBase58()}`,
  );
}

export async function handleAuthorityHttpRequest({ method, url, bodyText }) {
  try {
    if (method === 'GET' && (url === '/health' || url === '/api/health')) {
      return jsonResponse(200, {
        ok: true,
        authority: GAME_AUTHORITY.publicKey.toBase58(),
        program: SOLAMON_PROGRAM_ID.toBase58(),
      });
    }
    if (method === 'POST' && (url === '/approve-nft' || url === '/api/approve-nft')) {
      const body = JSON.parse(bodyText || '{}');
      await validateNftApproval(body);
      return jsonResponse(200, {
        approved: true,
        authority: GAME_AUTHORITY.publicKey.toBase58(),
        operation: body.operation,
      });
    }
    if (method !== 'POST' || !(url === '/sign' || url === '/api/sign')) {
      return textResponse(404, 'not found');
    }
    const body = JSON.parse(bodyText || '{}');
    const transaction = Transaction.from(Buffer.from(body.transaction ?? '', 'base64'));
    await validateTransaction(transaction, body.gameAction ?? null);
    transaction.partialSign(GAME_AUTHORITY);
    return jsonResponse(200, {
      authority: GAME_AUTHORITY.publicKey.toBase58(),
      transaction: transaction.serialize({
        requireAllSignatures: false,
        verifySignatures: false,
      }).toString('base64'),
    });
  } catch (error) {
    return textResponse(403, error instanceof Error ? error.message : String(error));
  }
}

function jsonResponse(status, body) {
  return {
    status,
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  };
}

function textResponse(status, body) {
  return {
    status,
    headers: { 'content-type': 'text/plain' },
    body,
  };
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  http.createServer(async (request, response) => {
    const result = await handleAuthorityHttpRequest({
      method: request.method,
      url: request.url,
      bodyText: await readBody(request),
    });
    response.writeHead(result.status, result.headers);
    response.end(result.body);
  }).listen(PORT, HOST, () => {
    console.log(`Solamon authority signer listening on http://${HOST}:${PORT}`);
  });
}

async function validateTransaction(transaction, gameAction) {
  let gatedInstructionCount = 0;
  let actionContext = null;
  for (const instruction of transaction.instructions) {
    if (!instruction.programId.equals(SOLAMON_PROGRAM_ID)) {
      continue;
    }
    const tag = instruction.data[0];
    if (tag === 1 || tag === 6) {
      throw new Error('generic save instructions are forbidden');
    }
    if (tag === 0) {
      requireAuthorityMeta(instruction);
      gatedInstructionCount += 1;
      continue;
    }
    if (ACTION_TAG_VALUES.has(tag)) {
      requireAuthorityMeta(instruction);
      actionContext = validateActionPayload(instruction, tag, gameAction);
      gatedInstructionCount += 1;
      continue;
    }
  }
  if (gatedInstructionCount !== 1) {
    throw new Error(`expected exactly one authority-gated instruction, got ${gatedInstructionCount}`);
  }
  if (actionContext) {
    await validateStateTransition(transaction, actionContext);
  }
}

function requireAuthorityMeta(instruction) {
  const authority = instruction.keys.find((key) =>
    key.pubkey.equals(SOLAMON_GAME_AUTHORITY),
  );
  if (!authority?.isSigner) {
    throw new Error('missing game authority signer account');
  }
}

function validateActionPayload(instruction, tag, gameAction) {
  if (!gameAction?.kind) {
    throw new Error('missing game action context');
  }
  const descriptor = ACTION_TAGS[gameAction.kind];
  if (!descriptor || descriptor.tag !== tag) {
    throw new Error(`action ${gameAction.kind} does not match instruction tag ${tag}`);
  }
  const decoded = decodeAction(instruction.data, descriptor.spends);
  if (decoded.code !== descriptor.code) {
    throw new Error(`action code ${decoded.code} does not match ${gameAction.kind}`);
  }
  compareNumber('amount', decoded.amount, gameAction.amount ?? 1);
  compareNumber('auxAmount', decoded.auxAmount, gameAction.auxAmount ?? gameAction.aux_amount ?? 0);
  compareString('primaryId', decoded.primaryId, gameAction.primaryId ?? gameAction.primary_id ?? '');
  compareString('secondaryId', decoded.secondaryId, gameAction.secondaryId ?? gameAction.secondary_id ?? '');
  const saveUpdate = decodeSaveUpdate(instruction.data, decoded.offset);
  const owner = instruction.keys[0]?.pubkey;
  const characterMint = instruction.keys[2]?.pubkey;
  if (!owner || !characterMint) {
    throw new Error('missing owner or character mint account');
  }
  return {
    ...decoded,
    kind: gameAction.kind,
    tag,
    spends: descriptor.spends,
    owner,
    characterMint,
    saveUpdate,
  };
}

function decodeAction(data, spends) {
  let offset = spends ? 1 + 8 + 32 : 1;
  if (data.length < offset + 5) {
    throw new Error('instruction data is too short for action payload');
  }
  const code = data.readUInt8(offset);
  offset += 1;
  const amount = data.readUInt16LE(offset);
  offset += 2;
  const auxAmount = data.readUInt16LE(offset);
  offset += 2;
  const primary = readFixedString(data, offset);
  offset = primary.offset;
  const secondary = readFixedString(data, offset);
  offset = secondary.offset;
  return {
    code,
    amount,
    auxAmount,
    primaryId: primary.value,
    secondaryId: secondary.value,
    offset,
  };
}

function decodeSaveUpdate(data, offset) {
  const expectedPreviousHash = data.subarray(offset, offset + 32).toString('hex');
  offset += 32;
  const saveHash = data.subarray(offset, offset + 32).toString('hex');
  offset += 32;
  const saveUri = readFixedString(data, offset, 200);
  offset = saveUri.offset;
  const mapId = readFixedString(data, offset, 32);
  offset = mapId.offset;
  const tileX = data.readUInt16LE(offset);
  offset += 2;
  const tileY = data.readUInt16LE(offset);
  offset += 2;
  const partyLen = data.readUInt8(offset);
  offset += 1;
  const party = [];
  for (let index = 0; index < partyLen; index += 1) {
    party.push(new PublicKey(data.subarray(offset, offset + 32)).toBase58());
    offset += 32;
  }
  return {
    expectedPreviousHash,
    saveHash,
    saveUri: saveUri.value,
    mapId: mapId.value,
    tileX,
    tileY,
    party,
  };
}

function readFixedString(data, offset, maxLength = 32) {
  if (offset >= data.length) {
    throw new Error('missing fixed string length');
  }
  const len = data.readUInt8(offset);
  offset += 1;
  if (offset + len > data.length || len > maxLength) {
    throw new Error('invalid fixed string length');
  }
  return {
    value: data.subarray(offset, offset + len).toString('utf8'),
    offset: offset + len,
  };
}

function compareNumber(label, actual, expected) {
  if (Number(actual) !== Number(expected)) {
    throw new Error(`${label} mismatch: ${actual} != ${expected}`);
  }
}

function compareString(label, actual, expected) {
  if (String(actual) !== String(expected)) {
    throw new Error(`${label} mismatch: ${actual} != ${expected}`);
  }
}

async function validateStateTransition(transaction, context) {
  const current = await readCurrentPlayerState(context.owner, context.characterMint);
  if (!current.initialized) {
    throw new Error('player state is not initialized');
  }
  if (current.saveHash !== context.saveUpdate.expectedPreviousHash) {
    throw new Error('previous save hash does not match on-chain state');
  }

  const proposedPayload = await proposedSavePayload(transaction, context);
  if (!proposedPayload) {
    throw new Error('unable to load proposed save payload for validation');
  }

  validateLocation(context, proposedPayload);
  const currentPayload =
    await currentSavePayload(current, context)
    ?? recoveryCurrentPayload(context, current, proposedPayload);
  validateByAction(context, currentPayload, proposedPayload);
}

function recoveryCurrentPayload(context, current, proposed) {
  if (context.kind === 'choose_starter') {
    return emptyFullPayload(context, [], []);
  }
  if (context.kind === 'grant_items') {
    const proposedMonsters = proposed.payload?.npc_state?.monsters ?? [];
    if (current.party.length !== proposedMonsters.length) {
      throw new Error('current on-chain save payload is unavailable');
    }
    return emptyFullPayload(context, proposedMonsters, []);
  }
  throw new Error('current on-chain save payload is unavailable');
}

function emptyFullPayload(context, monsters, items) {
  return {
    kind: 'full',
    payload: {
      npc_state: {
        current_map: context.saveUpdate.mapId,
        tile_pos: [context.saveUpdate.tileX, context.saveUpdate.tileY],
        items,
        monsters,
      },
    },
  };
}

async function readCurrentPlayerState(owner, characterMint) {
  const [playerState] = derivePlayerStatePda(owner, characterMint);
  const account = await CONNECTION.getAccountInfo(playerState);
  if (!account) {
    throw new Error('missing player state account');
  }
  const data = account.data;
  const partyLen = data.readUInt8(414);
  const party = [];
  for (let index = 0; index < partyLen; index += 1) {
    party.push(new PublicKey(data.subarray(415 + index * 32, 447 + index * 32)).toBase58());
  }
  return {
    initialized: data.readUInt8(0) !== 0,
    saveHash: data.subarray(138, 170).toString('hex'),
    saveUri: decodeAccountFixed(data.subarray(170, 370)),
    party,
  };
}

async function proposedSavePayload(transaction, context) {
  const compact = compactPayloadFromTransaction(transaction, context.saveUpdate.saveHash);
  if (compact) {
    return expandCompactPayload(context, compact);
  }
  return readSavePayloadFromChain(
    context,
    context.saveUpdate.saveHash,
    context.saveUpdate.saveUri,
  );
}

async function currentSavePayload(current, context) {
  return readSavePayloadFromChain(context, current.saveHash, current.saveUri);
}

async function readSavePayloadFromChain(context, saveHash, saveUri = '') {
  const compactMatch = /^compact:(\d+):/.exec(saveUri);
  if (compactMatch) {
    const text = await readCompressedCompactSave({
      connection: CONNECTION,
      owner: context.owner,
      characterMint: context.characterMint,
      saveHash,
      gameSlot: Number(compactMatch[1]),
    });
    return text ? expandCompactPayload(context, JSON.parse(text)) : null;
  }
  const blobText = await readCompressedSaveBlob({
    connection: CONNECTION,
    owner: context.owner,
    characterMint: context.characterMint,
    saveHash,
    gameSlot: 1,
  });
  return blobText ? { kind: 'full', payload: JSON.parse(blobText) } : null;
}

async function expandCompactPayload(context, compact) {
  const base = await readSavePayloadFromChain(context, compact.base);
  if (!base || base.kind !== 'full') {
    throw new Error('compact save base full payload is unavailable');
  }
  return {
    kind: 'full',
    payload: applyCompactToFullSave(base.payload, compact),
    compact,
  };
}

function applyCompactToFullSave(basePayload, compact) {
  const payload = JSON.parse(JSON.stringify(basePayload));
  const npc = payload.npc_state ?? {};
  const loc = compact.loc ?? {};
  if (loc.map_id) {
    npc.current_map = loc.map_id;
  }
  npc.tile_pos = [Number(loc.tile_x ?? 0), Number(loc.tile_y ?? 0)];
  if (loc.position) {
    npc.position = loc.position;
  }
  if (loc.facing) {
    npc.facing = loc.facing;
  }
  const money = npc.money ?? {};
  money.money = Number(compact.cur?.w ?? money.money ?? 0);
  money.bank_account = Number(compact.cur?.b ?? money.bank_account ?? 0);
  npc.money = money;

  const gameVariables = npc.game_variables ?? {};
  if (compact.vars && typeof compact.vars === 'object' && !Array.isArray(compact.vars)) {
    for (const [key, value] of Object.entries(compact.vars)) {
      gameVariables[String(key)] = value;
    }
  }
  npc.game_variables = gameVariables;

  if (Array.isArray(compact.full_items)) {
    npc.items = JSON.parse(JSON.stringify(compact.full_items));
  } else {
    const items = npc.items ?? [];
    for (let index = 0; index < Math.min(items.length, compact.items?.length ?? 0); index += 1) {
      items[index].quantity = Number(compact.items[index].q ?? items[index].quantity ?? 1);
      items[index].wear = Number(compact.items[index].w ?? items[index].wear ?? 0);
    }
    for (let index = items.length; index < (compact.items?.length ?? 0); index += 1) {
      const compactItem = compact.items[index];
      items.push({
        instance_id: compactItem.id ?? null,
        slug: compactItem.slug ?? compactItem.game_id ?? '',
        quantity: Number(compactItem.q ?? 1),
        wear: Number(compactItem.w ?? 0),
      });
    }
    npc.items = items;
  }

  if (Array.isArray(compact.full_mons)) {
    npc.monsters = JSON.parse(JSON.stringify(compact.full_mons));
  } else {
    const monsters = npc.monsters ?? [];
    for (let index = 0; index < Math.min(monsters.length, compact.mons?.length ?? 0); index += 1) {
      const compactMonster = compact.mons[index];
      monsters[index].current_hp = compactMonster.hp ?? monsters[index].current_hp;
      monsters[index].level = compactMonster.lv ?? monsters[index].level;
      monsters[index].total_experience = compactMonster.xp ?? monsters[index].total_experience;
      monsters[index].status = compactMonster.st ?? monsters[index].status ?? [];
      monsters[index].training_points = compactMonster.tp ?? monsters[index].training_points ?? {};
    }
    npc.monsters = monsters;
  }
  payload.npc_state = npc;
  return payload;
}

function compactPayloadFromTransaction(transaction, saveHash) {
  for (const instruction of transaction.instructions) {
    if (!instruction.programId.equals(SOLAMON_PROGRAM_ID) || instruction.data[0] !== 5) {
      continue;
    }
    const data = instruction.data;
    const ixSaveHash = data.subarray(1, 33).toString('hex');
    if (ixSaveHash !== saveHash) {
      continue;
    }
    const compression = data.readUInt8(66);
    const len = data.readUInt16LE(67);
    if (compression !== COMPRESSION_GZIP_JSON) {
      throw new Error(`unsupported compact compression ${compression}`);
    }
    return JSON.parse(zlib.gunzipSync(data.subarray(69, 69 + len)).toString('utf8'));
  }
  return null;
}

function validateLocation(context, proposed) {
  const loc = proposed.kind === 'compact'
    ? proposed.payload.loc
    : proposed.payload?.npc_state;
  const mapId = proposed.kind === 'compact'
    ? loc?.map_id
    : loc?.current_map;
  const tile = proposed.kind === 'compact'
    ? [loc?.tile_x, loc?.tile_y]
    : loc?.tile_pos;
  if (String(mapId ?? '') !== context.saveUpdate.mapId) {
    throw new Error('save map does not match signed player state');
  }
  if (Number(tile?.[0] ?? 0) !== context.saveUpdate.tileX || Number(tile?.[1] ?? 0) !== context.saveUpdate.tileY) {
    throw new Error('save tile does not match signed player state');
  }
}

function validateByAction(context, current, proposed) {
  switch (context.kind) {
    case 'grant_items':
      validateGrantItems(context, current, proposed);
      return;
    case 'buy_item':
      validateBuyItem(context, current, proposed);
      return;
    case 'sell_item':
      validateSellItem(context, current, proposed);
      return;
    case 'heal_party':
      validateHealParty(context, current, proposed);
      return;
    case 'battle_result':
      validateBattleResult(context, current, proposed);
      return;
    case 'catch_solamon':
    case 'choose_starter':
    case 'release_solamon':
      validatePartyAction(context, current, proposed);
      return;
    default:
      throw new Error(`unhandled action kind ${context.kind}`);
  }
}

function validateGrantItems(context, current, proposed) {
  if (context.primaryId === '__state_bundle__') {
    validateStateOnlyItemBundle(current, proposed);
    return;
  }
  const deltas = itemDeltas(current, proposed);
  const matching = deltas.filter((delta) => delta.slug === context.primaryId && delta.quantityDelta > 0);
  if (matching.length !== 1 || matching[0].quantityDelta !== context.amount) {
    throw new Error('grant_items item delta does not match action');
  }
  rejectUnexpectedItemDeltas(deltas, [matching[0]]);
  rejectMonsterMembershipChange(current, proposed);
}

function validateStateOnlyItemBundle(current, proposed) {
  const changed = itemDeltas(current, proposed).filter((delta) => delta.quantityDelta !== 0);
  if (changed.length === 0) {
    throw new Error('state item bundle has no item deltas');
  }
  for (const delta of changed) {
    if (!STATE_ONLY_UTILITY_ITEMS.has(delta.slug)) {
      throw new Error(`unexpected item delta ${delta.slug}: ${delta.quantityDelta}`);
    }
    if (delta.quantityDelta < 0 || delta.quantityDelta > 1) {
      throw new Error(`invalid state item bundle delta ${delta.slug}: ${delta.quantityDelta}`);
    }
  }
  rejectMonsterMembershipChange(current, proposed);
}

function validateBuyItem(context, current, proposed) {
  validateGrantItems(context, current, proposed);
}

function validateSellItem(context, current, proposed) {
  const deltas = itemDeltas(current, proposed);
  const matching = deltas.filter((delta) => delta.slug === context.primaryId && delta.quantityDelta < 0);
  if (matching.length !== 1 || Math.abs(matching[0].quantityDelta) !== context.amount) {
    throw new Error('sell_item item delta does not match action');
  }
  rejectUnexpectedItemDeltas(deltas, [matching[0]]);
  rejectMonsterMembershipChange(current, proposed);
}

function validateHealParty(context, current, proposed) {
  rejectItemDeltas(current, proposed);
  rejectMonsterMembershipChange(current, proposed);
  const currentMons = monstersByIndex(current);
  const proposedMons = monstersByIndex(proposed);
  for (let index = 0; index < Math.min(currentMons.length, proposedMons.length); index += 1) {
    if (Number(proposedMons[index].xp ?? 0) !== Number(currentMons[index].xp ?? 0)) {
      throw new Error('heal_party cannot change monster XP');
    }
    if (Number(proposedMons[index].lv ?? 0) !== Number(currentMons[index].lv ?? 0)) {
      throw new Error('heal_party cannot change monster level');
    }
    if (Number(proposedMons[index].hp ?? 0) < Number(currentMons[index].hp ?? 0)) {
      throw new Error('heal_party cannot lower monster HP');
    }
  }
}

function validateBattleResult(context, current, proposed) {
  if (context.primaryId === 'battle_reward') {
    validateTrainerBattleRewardMarker(current, proposed);
  }
  validateBattleResultItemDeltas(current, proposed);
  rejectMonsterMembershipChange(current, proposed);
  const currentMons = monstersByIndex(current);
  const proposedMons = monstersByIndex(proposed);
  for (let index = 0; index < Math.min(currentMons.length, proposedMons.length); index += 1) {
    if (Number(proposedMons[index].lv ?? 0) < Number(currentMons[index].lv ?? 0)) {
      throw new Error('battle_result cannot lower monster level');
    }
    if (Number(proposedMons[index].xp ?? 0) < Number(currentMons[index].xp ?? 0)) {
      throw new Error('battle_result cannot lower monster XP');
    }
    if (Number(proposedMons[index].xp ?? 0) - Number(currentMons[index].xp ?? 0) > 5000) {
      throw new Error('battle_result XP gain is too large');
    }
  }
}

function validateTrainerBattleRewardMarker(current, proposed) {
  const currentVars = gameVariables(current);
  const proposedVars = gameVariables(proposed);
  const battleId = String(proposedVars.battle_current_id ?? '');
  if (!battleId) {
    throw new Error('battle_reward requires a trainer battle id');
  }
  const completedKey = `trainer_battle_completed_${battleId}`;
  const rewardedKey = `trainer_battle_rewarded_${battleId}`;
  if (currentVars[rewardedKey] === 'yes') {
    throw new Error(`duplicate trainer battle reward ${battleId}`);
  }
  if (proposedVars[completedKey] !== 'yes' || proposedVars[rewardedKey] !== 'yes') {
    throw new Error('battle_reward must mark trainer battle completed and rewarded');
  }
}

function validateBattleResultItemDeltas(current, proposed) {
  const deltas = itemDeltas(current, proposed).filter((delta) => delta.quantityDelta !== 0);
  for (const delta of deltas) {
    if (!STATE_ONLY_BATTLE_REWARD_ITEMS.has(delta.slug)) {
      throw new Error(`unexpected item delta ${delta.slug}: ${delta.quantityDelta}`);
    }
    if (delta.quantityDelta < 0 || delta.quantityDelta > 1) {
      throw new Error(`invalid battle reward item delta ${delta.slug}: ${delta.quantityDelta}`);
    }
  }
}

function validatePartyAction(context, current, proposed) {
  if (context.kind === 'choose_starter') {
    validateStarterItemDeltas(current, proposed);
  } else if (context.kind === 'catch_solamon') {
    validateCatchItemDeltas(context, current, proposed);
  } else {
    rejectItemDeltas(current, proposed);
  }
  const currentIds = monsterIds(current);
  const proposedIds = monsterIds(proposed);
  if (context.kind === 'choose_starter' && !(currentIds.length === 0 && proposedIds.length === 1)) {
    throw new Error('choose_starter must create exactly one monster');
  }
  if (context.kind === 'catch_solamon' && proposedIds.length > currentIds.length + 1) {
    throw new Error('catch_solamon can add at most one monster');
  }
  if (context.kind === 'release_solamon' && proposedIds.length >= currentIds.length) {
    throw new Error('release_solamon must remove a monster');
  }
}

function validateCatchItemDeltas(context, current, proposed) {
  const deltas = itemDeltas(current, proposed);
  const changed = deltas.filter((delta) => delta.quantityDelta !== 0);
  if (changed.length === 0) {
    return;
  }
  if (changed.length !== 1) {
    throw new Error('catch_solamon can spend only one item stack');
  }
  const [delta] = changed;
  if (delta.quantityDelta !== -1) {
    throw new Error('catch_solamon item spend must be exactly one');
  }
  if (context.secondaryId && delta.slug !== context.secondaryId) {
    throw new Error('catch_solamon item spend does not match action');
  }
}

function validateStarterItemDeltas(current, proposed) {
  const deltas = itemDeltas(current, proposed);
  if (deltas.length > 3) {
    throw new Error('choose_starter grants too many item stacks');
  }
  for (const delta of deltas) {
    if (delta.quantityDelta <= 0) {
      throw new Error('choose_starter cannot remove items');
    }
    if (delta.quantityDelta > 5) {
      throw new Error('choose_starter item grant is too large');
    }
  }
}

function rejectItemDeltas(current, proposed) {
  rejectUnexpectedItemDeltas(itemDeltas(current, proposed), []);
}

function rejectUnexpectedItemDeltas(deltas, allowed) {
  for (const delta of deltas) {
    if (delta.quantityDelta === 0) {
      continue;
    }
    if (!allowed.includes(delta)) {
      throw new Error(`unexpected item delta ${delta.slug}: ${delta.quantityDelta}`);
    }
  }
}

function rejectMonsterMembershipChange(current, proposed) {
  const before = monsterIds(current).join(',');
  const after = monsterIds(proposed).join(',');
  if (before !== after) {
    throw new Error('action cannot change monster membership');
  }
}

function itemDeltas(current, proposed) {
  const before = itemsBySlug(current);
  const after = itemsBySlug(proposed);
  const slugs = new Set([...before.keys(), ...after.keys()]);
  return [...slugs].map((slug) => ({
    slug,
    before: before.get(slug) ?? 0,
    after: after.get(slug) ?? 0,
    quantityDelta: (after.get(slug) ?? 0) - (before.get(slug) ?? 0),
  }));
}

function itemsBySlug(save) {
  const items = save.kind === 'compact'
    ? save.payload.items ?? []
    : save.payload?.npc_state?.items ?? [];
  const baseItems = save.kind === 'compact' ? [] : items;
  const map = new Map();
  for (const item of baseItems) {
    const slug = item.slug ?? item.game_id;
    if (slug) {
      map.set(slug, Number(item.quantity ?? 1));
    }
  }
  if (save.kind === 'compact') {
    for (let index = 0; index < items.length; index += 1) {
      map.set(`index:${index}`, Number(items[index].q ?? 0));
    }
  }
  return map;
}

function monsterIds(save) {
  if (save.kind === 'compact') {
    return (save.payload.mons ?? []).map((_, index) => `index:${index}`);
  }
  return (save.payload?.npc_state?.monsters ?? []).map((monster, index) =>
    String(monster.instance_id ?? monster.slug ?? `index:${index}`),
  );
}

function monstersByIndex(save) {
  if (save.kind === 'compact') {
    return (save.payload.mons ?? []).map((monster) => ({
      hp: monster.hp,
      lv: monster.lv,
      xp: monster.xp,
    }));
  }
  return (save.payload?.npc_state?.monsters ?? []).map((monster) => ({
    hp: monster.current_hp,
    lv: monster.level,
    xp: monster.total_experience,
  }));
}

function gameVariables(save) {
  return save.payload?.npc_state?.game_variables ?? {};
}

function decodeAccountFixed(bytes) {
  const nul = bytes.indexOf(0);
  return bytes.subarray(0, nul === -1 ? bytes.length : nul).toString('utf8');
}

async function readBody(request) {
  const chunks = [];
  for await (const chunk of request) {
    chunks.push(Buffer.from(chunk));
  }
  return Buffer.concat(chunks).toString('utf8');
}

async function validateNftApproval(body) {
  const operation = String(body.operation ?? '');
  if (!operation) {
    throw new Error('missing NFT operation');
  }
  if (!body.owner) {
    throw new Error('missing NFT owner');
  }
  new PublicKey(body.owner);

  switch (operation) {
    case 'mint_character':
      validateCharacterApproval(body);
      return;
    case 'mint_asset':
    case 'sync_metadata':
      await validateAssetApproval(body);
      return;
    case 'burn_asset':
      await validateBurnApproval(body);
      return;
    default:
      throw new Error(`unsupported NFT operation ${operation}`);
  }
}

function validateCharacterApproval(body) {
  if (!body.name || String(body.name).length > 32) {
    throw new Error('invalid character name');
  }
  if (!body.avatar || String(body.avatar).length > 32) {
    throw new Error('invalid character avatar');
  }
}

async function validateAssetApproval(body) {
  const asset = body.asset;
  if (!asset || typeof asset !== 'object') {
    throw new Error('missing asset');
  }
  if (!['item', 'monster', 'badge'].includes(asset.kind)) {
    throw new Error(`invalid asset kind ${asset.kind}`);
  }
  if (!asset.instance_id || !asset.game_id || !asset.state_hash) {
    throw new Error('asset is missing id/state fields');
  }
  if (body.projectionOwner && body.projectionOwner !== body.owner) {
    throw new Error('projection owner does not match NFT owner');
  }
  if (body.operation === 'mint_asset') {
    validateMintAssetAction(body, asset);
    return;
  }
  await validateAssetExistsInCurrentSave(body, asset);
}

async function validateBurnApproval(body) {
  if (!['item', 'monster', 'badge'].includes(body.kind)) {
    throw new Error(`invalid burn kind ${body.kind}`);
  }
  if (!body.instanceId) {
    throw new Error('missing burn instance id');
  }
  if (body.characterMint) {
    const save = await currentSaveForNftOwner(body.owner, body.characterMint);
    if (save && assetExistsInSave(save, { kind: body.kind, instance_id: body.instanceId })) {
      throw new Error('cannot burn asset that still exists in current on-chain save');
    }
  }
}

function validateMintAssetAction(body, asset) {
  const action = body.gameAction;
  if (!action?.kind) {
    throw new Error('mint_asset requires gameAction approval context');
  }
  const slug = asset.metadata?.slug ?? asset.game_id;
  if (asset.kind === 'monster') {
    if (!['choose_starter', 'catch_solamon'].includes(action.kind)) {
      throw new Error(`monster mint is not allowed for action ${action.kind}`);
    }
    if (slug !== (action.primaryId ?? action.primary_id)) {
      throw new Error('monster mint slug does not match action');
    }
    return;
  }
  if (asset.kind === 'item') {
    if (!['choose_starter', 'grant_items', 'buy_item'].includes(action.kind)) {
      throw new Error(`item mint is not allowed for action ${action.kind}`);
    }
    if (action.kind !== 'choose_starter' && slug !== (action.primaryId ?? action.primary_id)) {
      throw new Error('item mint slug does not match action');
    }
    const quantity = Number(asset.metadata?.quantity ?? asset.amount ?? 1);
    if (action.kind === 'choose_starter' && quantity > 5) {
      throw new Error('starter item mint quantity is too large');
    }
    if (action.kind !== 'choose_starter' && quantity < Number(action.amount ?? 1)) {
      throw new Error('item mint quantity is smaller than action amount');
    }
    return;
  }
  if (asset.kind === 'badge' && action.kind !== 'grant_items') {
    throw new Error(`badge mint is not allowed for action ${action.kind}`);
  }
}

async function validateAssetExistsInCurrentSave(body, asset) {
  if (!body.characterMint) {
    throw new Error('sync_metadata requires characterMint');
  }
  const save = await currentSaveForNftOwner(body.owner, body.characterMint);
  if (!save) {
    throw new Error('current on-chain save is unavailable for NFT sync');
  }
  if (!assetExistsInSave(save, asset)) {
    throw new Error('asset is not present in current on-chain save');
  }
}

async function currentSaveForNftOwner(owner, characterMint) {
  const current = await readCurrentPlayerState(new PublicKey(owner), new PublicKey(characterMint));
  if (!current.initialized || current.saveHash === '0'.repeat(64)) {
    return null;
  }
  return currentSavePayload(current, {
    owner: new PublicKey(owner),
    characterMint: new PublicKey(characterMint),
  });
}

function assetExistsInSave(save, asset) {
  const npc = save.payload?.npc_state ?? {};
  if (asset.kind === 'item') {
    return (npc.items ?? []).some((item) =>
      String(item.instance_id ?? '') === String(asset.instance_id ?? asset.instanceId ?? '')
      || String(item.slug ?? '') === String(asset.metadata?.slug ?? asset.game_id ?? ''),
    );
  }
  if (asset.kind === 'monster') {
    return (npc.monsters ?? []).some((monster) =>
      String(monster.instance_id ?? '') === String(asset.instance_id ?? asset.instanceId ?? '')
      || String(monster.slug ?? '') === String(asset.metadata?.slug ?? asset.game_id ?? ''),
    );
  }
  return true;
}
