import fs from "node:fs";
import { createNft, mplTokenMetadata } from "@metaplex-foundation/mpl-token-metadata";
import {
  createSignerFromKeypair,
  generateSigner,
  keypairIdentity,
  percentAmount,
  publicKey,
} from "@metaplex-foundation/umi";
import { createUmi } from "@metaplex-foundation/umi-bundle-defaults";
import { Keypair } from "@solana/web3.js";
import { defaultKeypairPath, loadJson, saveJson } from "./keys.mjs";
import { requireNftApproval } from "./nft-approval.mjs";

const [profilePath] = process.argv.slice(2);
if (!profilePath) {
  throw new Error("Usage: node mint-character.mjs <character-profile.json>");
}

const collectionsPath = process.env.SOLAMON_COLLECTIONS_PATH ?? "devnet-collections.json";
const collections = loadJson(collectionsPath);
const profile = loadJson(profilePath);
const secret = process.env.SOLAMON_SECRET_KEY_BASE64;
const authority = secret
  ? Keypair.fromSecretKey(Buffer.from(secret, "base64"))
  : Keypair.fromSecretKey(Uint8Array.from(loadJson(defaultKeypairPath)));

const umi = createUmi(collections.rpcUrl).use(mplTokenMetadata());
const umiAuthority = umi.eddsa.createKeypairFromSecretKey(authority.secretKey);
umi.use(keypairIdentity(createSignerFromKeypair(umi, umiAuthority)));

if (authority.publicKey.toBase58() !== profile.owner) {
  throw new Error(`Character mint payer ${authority.publicKey.toBase58()} must match profile owner ${profile.owner}`);
}

await requireNftApproval({
  operation: "mint_character",
  owner: profile.owner,
  name: profile.name,
  avatar: profile.avatar_slug ?? "adventurer",
  createdAt: profile.created_at ?? "",
});

const mint = generateSigner(umi);
const metadataDir = "metadata";
fs.mkdirSync(metadataDir, { recursive: true });
const metadataPath = `${metadataDir}/character-${profile.owner}.json`;
const metadataUri = `https://solamon.local/metadata/characters/${profile.owner}.json`;
const metadataJson = {
  name: profile.name,
  symbol: "SLPLYR",
  description: "Solamon player character",
  image: `https://solamon.local/assets/characters/${profile.avatar_slug ?? "adventurer"}.png`,
  attributes: [
    { trait_type: "Game", value: "Solamon" },
    { trait_type: "Owner", value: profile.owner },
    { trait_type: "Skin", value: profile.avatar_slug ?? "adventurer" },
    { trait_type: "Avatar", value: profile.avatar_slug ?? "adventurer" },
    { trait_type: "Created At", value: profile.created_at ?? "" },
  ],
  properties: {
    category: "image",
    files: [
      {
        uri: `https://solamon.local/assets/characters/${profile.avatar_slug ?? "adventurer"}.png`,
        type: "image/png",
      },
    ],
  },
};
saveJson(metadataPath, metadataJson);

await createNft(umi, {
  mint,
  name: profile.name,
  symbol: "SLPLYR",
  uri: metadataUri,
  tokenOwner: publicKey(profile.owner),
  sellerFeeBasisPoints: percentAmount(0),
  collection: {
    key: publicKey(collections.character),
    verified: false,
  },
}).sendAndConfirm(umi, { confirm: { commitment: "confirmed" } });

profile.character_mint = mint.publicKey.toString();
profile.character_collection_mint = collections.character;
profile.character_mint_status = "minted-devnet";
profile.character_metadata_uri = metadataUri;
profile.character_metadata_path = metadataPath;
saveJson(profilePath, profile);
console.log(JSON.stringify({
  owner: profile.owner,
  characterMint: profile.character_mint,
  collectionMint: profile.character_collection_mint,
}, null, 2));
