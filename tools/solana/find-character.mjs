import { fetchAllDigitalAssetByOwner, mplTokenMetadata } from "@metaplex-foundation/mpl-token-metadata";
import { publicKey } from "@metaplex-foundation/umi";
import { createUmi } from "@metaplex-foundation/umi-bundle-defaults";
import { loadJson } from "./keys.mjs";

const [owner] = process.argv.slice(2);
if (!owner) {
  throw new Error("Usage: node find-character.mjs <owner-public-key>");
}

const collectionsPath = process.env.SOLAMON_COLLECTIONS_PATH ?? "devnet-collections.json";
const collections = loadJson(collectionsPath);
const umi = createUmi(collections.rpcUrl).use(mplTokenMetadata());
const assets = await fetchAllDigitalAssetByOwner(umi, publicKey(owner));

const character = assets.find((asset) => {
  const collection = asset.metadata.collection?.value;
  return collection?.key?.toString() === collections.character;
});

if (!character) {
  console.log(JSON.stringify(null));
} else {
  console.log(JSON.stringify({
    owner,
    mint: character.publicKey.toString(),
    collection: collections.character,
    name: character.metadata.name,
    uri: character.metadata.uri,
  }, null, 2));
}
