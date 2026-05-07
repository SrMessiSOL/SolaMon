import { Connection } from '@solana/web3.js';
import { loadJson } from './keys.mjs';
import { readCompressedCompactSave } from './save-blob.mjs';

const [owner, characterMint, saveHash, gameSlotArg] = process.argv.slice(2);
if (!owner || !characterMint || !saveHash) {
  console.error('Usage: node read-compact-save.mjs <owner> <characterMint> <saveHash> [gameSlot]');
  process.exit(1);
}

const collections = loadJson('devnet-collections.json');
const connection = new Connection(collections.rpcUrl, 'confirmed');
const jsonText = await readCompressedCompactSave({
  connection,
  owner,
  characterMint,
  saveHash,
  gameSlot: Number(gameSlotArg ?? 1),
});

if (jsonText === null) {
  console.log(JSON.stringify(null));
} else {
  console.log(jsonText);
}
