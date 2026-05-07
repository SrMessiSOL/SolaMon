import { Connection, PublicKey } from '@solana/web3.js';
import { derivePlayerStatePda } from './player-instructions.mjs';
import { loadJson } from './keys.mjs';

const [ownerArg, characterMintArg] = process.argv.slice(2);
if (!ownerArg || !characterMintArg) {
  console.error('Usage: node read-player-state.mjs <owner> <characterMint>');
  process.exit(1);
}

const collections = loadJson('devnet-collections.json');
const connection = new Connection(process.env.SOLAMON_RPC_URL ?? collections.rpcUrl, 'confirmed');
const owner = new PublicKey(ownerArg);
const characterMint = new PublicKey(characterMintArg);
const [playerState] = derivePlayerStatePda(owner, characterMint);
const account = await connection.getAccountInfo(playerState);

if (!account) {
  console.log(JSON.stringify(null));
  process.exit(0);
}

const data = account.data;
const partyLen = data.readUInt8(414);
const party = [];
for (let index = 0; index < partyLen; index += 1) {
  party.push(new PublicKey(data.subarray(415 + index * 32, 447 + index * 32)).toBase58());
}

console.log(JSON.stringify({
  playerState: playerState.toBase58(),
  initialized: data.readUInt8(0) !== 0,
  version: data.readUInt8(1),
  owner: new PublicKey(data.subarray(2, 34)).toBase58(),
  characterMint: new PublicKey(data.subarray(34, 66)).toBase58(),
  name: decodeFixed(data.subarray(66, 98)),
  avatar: decodeFixed(data.subarray(98, 130)),
  saveVersion: Number(data.readBigUInt64LE(130)),
  saveHash: data.subarray(138, 170).toString('hex'),
  saveUri: decodeFixed(data.subarray(170, 370)),
  mapId: decodeFixed(data.subarray(370, 402)),
  tileX: data.readUInt16LE(402),
  tileY: data.readUInt16LE(404),
  savedAtSlot: Number(data.readBigUInt64LE(406)),
  party,
}, null, 2));

function decodeFixed(bytes) {
  const nul = bytes.indexOf(0);
  return bytes.subarray(0, nul === -1 ? bytes.length : nul).toString('utf8');
}
