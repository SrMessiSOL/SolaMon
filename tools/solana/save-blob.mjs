import fs from 'node:fs';
import zlib from 'node:zlib';
import {
  Connection,
  Keypair,
  PublicKey,
  SystemProgram,
  Transaction,
  TransactionInstruction,
  sendAndConfirmTransaction,
} from '@solana/web3.js';
import {
  SOLAMON_PROGRAM_ID,
  findCharacterTokenAccount,
} from './player-instructions.mjs';

export const SAVE_BLOB_SEED = 'save_blob';
export const SAVE_BLOB_CHUNK_SEED = 'save_blob_chunk';
export const SAVE_SLOT_BLOB_SEED = 'save_slot_blob';
export const SAVE_COMPACT_SEED = 'save_compact';
export const SAVE_BLOB_CHUNK_SIZE = 750;
export const COMPRESSION_GZIP_JSON = 1;
export const SAVE_SLOT_BLOB_MAX_BYTES = 4096;
export const SAVE_COMPACT_MAX_BYTES = 2048;

export function saveHashPublicKey(saveHash) {
  const bytes = Buffer.from(String(saveHash).replace(/^0x/, ''), 'hex');
  if (bytes.length !== 32) {
    throw new Error('saveHash must be a 32-byte hex string');
  }
  return new PublicKey(bytes);
}

export function deriveSaveBlobManifest(owner, characterMint, saveHash) {
  return PublicKey.findProgramAddressSync(
    [
      Buffer.from(SAVE_BLOB_SEED),
      new PublicKey(owner).toBuffer(),
      new PublicKey(characterMint).toBuffer(),
      saveHashPublicKey(saveHash).toBuffer(),
    ],
    SOLAMON_PROGRAM_ID,
  );
}

export function deriveSaveBlobChunk(owner, characterMint, saveHash, index) {
  const indexBytes = Buffer.alloc(2);
  indexBytes.writeUInt16LE(index);
  return PublicKey.findProgramAddressSync(
    [
      Buffer.from(SAVE_BLOB_CHUNK_SEED),
      new PublicKey(owner).toBuffer(),
      new PublicKey(characterMint).toBuffer(),
      saveHashPublicKey(saveHash).toBuffer(),
      indexBytes,
    ],
    SOLAMON_PROGRAM_ID,
  );
}

export function deriveSaveSlotBlob(owner, characterMint, gameSlot = 1) {
  return PublicKey.findProgramAddressSync(
    [
      Buffer.from(SAVE_SLOT_BLOB_SEED),
      new PublicKey(owner).toBuffer(),
      new PublicKey(characterMint).toBuffer(),
      Buffer.from([gameSlot]),
    ],
    SOLAMON_PROGRAM_ID,
  );
}

export function deriveSaveCompact(owner, characterMint, gameSlot = 1) {
  return PublicKey.findProgramAddressSync(
    [
      Buffer.from(SAVE_COMPACT_SEED),
      new PublicKey(owner).toBuffer(),
      new PublicKey(characterMint).toBuffer(),
      Buffer.from([gameSlot]),
    ],
    SOLAMON_PROGRAM_ID,
  );
}

export async function writeCompressedCompactSave({
  connection,
  payer,
  rentPayer = payer,
  profile,
  compact,
  gameSlot = 1,
}) {
  const owner = new PublicKey(profile.owner);
  const characterMint = new PublicKey(profile.character_mint);
  const characterTokenAccount = await findCharacterTokenAccount(
    connection,
    owner,
    characterMint,
  );
  const compactBytes = Buffer.from(JSON.stringify(compact), 'utf8');
  const compressed = zlib.gzipSync(compactBytes);
  if (compressed.length > SAVE_COMPACT_MAX_BYTES) {
    throw new Error(
      `Compressed compact save is ${compressed.length} bytes, max is ${SAVE_COMPACT_MAX_BYTES}`,
    );
  }
  const [saveCompact] = deriveSaveCompact(owner, characterMint, gameSlot);
  return {
    saveCompact,
    compressedBytes: compressed.length,
    instruction: writeSaveCompactInstruction({
      owner,
      rentPayer: rentPayer.publicKey,
      characterMint,
      characterTokenAccount,
      saveCompact,
      saveHash: compact.hash,
      baseSaveHash: compact.base,
      gameSlot,
      bytes: compressed,
    }),
  };
}

export async function readCompressedCompactSave({
  connection,
  owner,
  characterMint,
  saveHash,
  gameSlot = 1,
}) {
  const [saveCompact] = deriveSaveCompact(owner, characterMint, gameSlot);
  const account = await connection.getAccountInfo(saveCompact);
  if (!account) {
    return null;
  }
  const decoded = decodeSaveCompact(account.data);
  if (!decoded.initialized || decoded.saveHash !== String(saveHash)) {
    return null;
  }
  if (decoded.compression !== COMPRESSION_GZIP_JSON) {
    throw new Error(`Unsupported compact save compression ${decoded.compression}`);
  }
  return zlib.gunzipSync(decoded.bytes).toString('utf8');
}

export async function writeCompressedSaveBlob({
  connection,
  payer,
  rentPayer = payer,
  profile,
  projection,
  gameSlot = 1,
}) {
  const blobPath = projection.save_blob_uri;
  if (!blobPath) {
    return { skipped: true, reason: 'missing-save-blob-uri' };
  }

  const owner = new PublicKey(profile.owner);
  const characterMint = new PublicKey(profile.character_mint);
  const characterTokenAccount = await findCharacterTokenAccount(
    connection,
    owner,
    characterMint,
  );
  const compressed = zlib.gzipSync(fs.readFileSync(blobPath));
  if (compressed.length <= SAVE_SLOT_BLOB_MAX_BYTES) {
    return writeCompressedSaveSlotBlob({
      connection,
      payer,
      rentPayer,
      owner,
      characterMint,
      characterTokenAccount,
      saveHash: projection.save_hash,
      gameSlot,
      compressed,
    });
  }

  const chunkCount = Math.ceil(compressed.length / SAVE_BLOB_CHUNK_SIZE);
  const [manifest] = deriveSaveBlobManifest(
    owner,
    characterMint,
    projection.save_hash,
  );
  const signatures = [];

  const manifestTransaction = new Transaction().add(
    writeManifestInstruction({
      owner,
      rentPayer: rentPayer.publicKey,
      characterMint,
      characterTokenAccount,
      manifest,
      saveHash: projection.save_hash,
      totalLen: compressed.length,
      chunkSize: SAVE_BLOB_CHUNK_SIZE,
      chunkCount,
    }),
  );
  manifestTransaction.feePayer = rentPayer.publicKey;
  signatures.push(
    await sendAndConfirmTransaction(
      connection,
      manifestTransaction,
      uniqueSigners([rentPayer, payer]),
      { commitment: 'confirmed' },
    ),
  );

  for (let index = 0; index < chunkCount; index += 1) {
    const offset = index * SAVE_BLOB_CHUNK_SIZE;
    const chunkBytes = compressed.subarray(
      offset,
      Math.min((index + 1) * SAVE_BLOB_CHUNK_SIZE, compressed.length),
    );
    const [chunk] = deriveSaveBlobChunk(
      owner,
      characterMint,
      projection.save_hash,
      index,
    );
    const chunkTransaction = new Transaction().add(
      writeChunkInstruction({
        owner,
        rentPayer: rentPayer.publicKey,
        characterMint,
        characterTokenAccount,
        chunk,
        saveHash: projection.save_hash,
        index,
        bytes: chunkBytes,
      }),
    );
    chunkTransaction.feePayer = rentPayer.publicKey;
    const signature = await sendAndConfirmTransaction(
      connection,
      chunkTransaction,
      uniqueSigners([rentPayer, payer]),
      { commitment: 'confirmed' },
    );
    signatures.push(signature);
  }

  return {
    mode: 'hash-blob',
    manifest: manifest.toBase58(),
    compressedBytes: compressed.length,
    chunkCount,
    signatures,
  };
}

async function writeCompressedSaveSlotBlob({
  connection,
  payer,
  rentPayer,
  owner,
  characterMint,
  characterTokenAccount,
  saveHash,
  gameSlot,
  compressed,
}) {
  const [saveSlotBlob] = deriveSaveSlotBlob(owner, characterMint, gameSlot);
  const signatures = [];
  for (let offset = 0; offset < compressed.length; offset += SAVE_BLOB_CHUNK_SIZE) {
    const bytes = compressed.subarray(
      offset,
      Math.min(offset + SAVE_BLOB_CHUNK_SIZE, compressed.length),
    );
    const transaction = new Transaction().add(
      writeSaveSlotBlobChunkInstruction({
        owner,
        rentPayer: rentPayer.publicKey,
        characterMint,
        characterTokenAccount,
        saveSlotBlob,
        saveHash,
        gameSlot,
        totalLen: compressed.length,
        offset,
        bytes,
      }),
    );
    transaction.feePayer = rentPayer.publicKey;
    signatures.push(
      await sendAndConfirmTransaction(
        connection,
        transaction,
        uniqueSigners([rentPayer, payer]),
        { commitment: 'confirmed' },
      ),
    );
  }
  return {
    mode: 'slot-blob',
    saveSlotBlob: saveSlotBlob.toBase58(),
    compressedBytes: compressed.length,
    chunkCount: signatures.length,
    signatures,
  };
}

function uniqueSigners(signers) {
  const signerMap = new Map();
  for (const signer of signers) {
    signerMap.set(signer.publicKey.toBase58(), signer);
  }
  return [...signerMap.values()];
}

export async function readCompressedSaveBlob({
  connection,
  owner,
  characterMint,
  saveHash,
  gameSlot = 1,
}) {
  const reusable = await readCompressedSaveSlotBlob({
    connection,
    owner,
    characterMint,
    saveHash,
    gameSlot,
  });
  if (reusable !== null) {
    return reusable;
  }

  const [manifest] = deriveSaveBlobManifest(owner, characterMint, saveHash);
  const manifestAccount = await connection.getAccountInfo(manifest);
  if (!manifestAccount) {
    return null;
  }

  const manifestData = decodeManifest(manifestAccount.data);
  const chunks = [];
  for (let index = 0; index < manifestData.chunkCount; index += 1) {
    const [chunk] = deriveSaveBlobChunk(owner, characterMint, saveHash, index);
    const chunkAccount = await connection.getAccountInfo(chunk);
    if (!chunkAccount) {
      throw new Error(`Missing save blob chunk ${index}`);
    }
    chunks.push(decodeChunk(chunkAccount.data).bytes);
  }
  const compressed = Buffer.concat(chunks, manifestData.totalLen);
  if (manifestData.compression !== COMPRESSION_GZIP_JSON) {
    throw new Error(`Unsupported save blob compression ${manifestData.compression}`);
  }
  return zlib.gunzipSync(compressed).toString('utf8');
}

export async function readCompressedSaveSlotBlob({
  connection,
  owner,
  characterMint,
  saveHash,
  gameSlot = 1,
}) {
  const [saveSlotBlob] = deriveSaveSlotBlob(owner, characterMint, gameSlot);
  const account = await connection.getAccountInfo(saveSlotBlob);
  if (!account) {
    return null;
  }
  const decoded = decodeSaveSlotBlob(account.data);
  if (!decoded.initialized || decoded.saveHash !== String(saveHash)) {
    return null;
  }
  if (decoded.compression !== COMPRESSION_GZIP_JSON) {
    throw new Error(`Unsupported save blob compression ${decoded.compression}`);
  }
  return zlib.gunzipSync(decoded.bytes).toString('utf8');
}

function writeManifestInstruction({
  owner,
  rentPayer,
  characterMint,
  characterTokenAccount,
  manifest,
  saveHash,
  totalLen,
  chunkSize,
  chunkCount,
}) {
  const data = Buffer.alloc(1 + 32 + 1 + 4 + 2 + 2);
  let offset = 0;
  data.writeUInt8(2, offset);
  offset += 1;
  saveHashPublicKey(saveHash).toBuffer().copy(data, offset);
  offset += 32;
  data.writeUInt8(COMPRESSION_GZIP_JSON, offset);
  offset += 1;
  data.writeUInt32LE(totalLen, offset);
  offset += 4;
  data.writeUInt16LE(chunkSize, offset);
  offset += 2;
  data.writeUInt16LE(chunkCount, offset);

  return new TransactionInstruction({
    programId: SOLAMON_PROGRAM_ID,
    keys: [
      { pubkey: owner, isSigner: true, isWritable: true },
      { pubkey: rentPayer, isSigner: true, isWritable: true },
      { pubkey: characterMint, isSigner: false, isWritable: false },
      { pubkey: characterTokenAccount, isSigner: false, isWritable: false },
      { pubkey: manifest, isSigner: false, isWritable: true },
      { pubkey: SystemProgram.programId, isSigner: false, isWritable: false },
    ],
    data,
  });
}

function writeChunkInstruction({
  owner,
  rentPayer,
  characterMint,
  characterTokenAccount,
  chunk,
  saveHash,
  index,
  bytes,
}) {
  const data = Buffer.alloc(1 + 32 + 2 + 2 + bytes.length);
  let offset = 0;
  data.writeUInt8(3, offset);
  offset += 1;
  saveHashPublicKey(saveHash).toBuffer().copy(data, offset);
  offset += 32;
  data.writeUInt16LE(index, offset);
  offset += 2;
  data.writeUInt16LE(bytes.length, offset);
  offset += 2;
  bytes.copy(data, offset);

  return new TransactionInstruction({
    programId: SOLAMON_PROGRAM_ID,
    keys: [
      { pubkey: owner, isSigner: true, isWritable: true },
      { pubkey: rentPayer, isSigner: true, isWritable: true },
      { pubkey: characterMint, isSigner: false, isWritable: false },
      { pubkey: characterTokenAccount, isSigner: false, isWritable: false },
      { pubkey: chunk, isSigner: false, isWritable: true },
      { pubkey: SystemProgram.programId, isSigner: false, isWritable: false },
    ],
    data,
  });
}

function writeSaveSlotBlobChunkInstruction({
  owner,
  rentPayer,
  characterMint,
  characterTokenAccount,
  saveSlotBlob,
  saveHash,
  gameSlot,
  totalLen,
  offset,
  bytes,
}) {
  const data = Buffer.alloc(1 + 32 + 1 + 1 + 4 + 2 + 2 + bytes.length);
  let dataOffset = 0;
  data.writeUInt8(4, dataOffset);
  dataOffset += 1;
  saveHashPublicKey(saveHash).toBuffer().copy(data, dataOffset);
  dataOffset += 32;
  data.writeUInt8(gameSlot, dataOffset);
  dataOffset += 1;
  data.writeUInt8(COMPRESSION_GZIP_JSON, dataOffset);
  dataOffset += 1;
  data.writeUInt32LE(totalLen, dataOffset);
  dataOffset += 4;
  data.writeUInt16LE(offset, dataOffset);
  dataOffset += 2;
  data.writeUInt16LE(bytes.length, dataOffset);
  dataOffset += 2;
  bytes.copy(data, dataOffset);

  return new TransactionInstruction({
    programId: SOLAMON_PROGRAM_ID,
    keys: [
      { pubkey: owner, isSigner: true, isWritable: true },
      { pubkey: rentPayer, isSigner: true, isWritable: true },
      { pubkey: characterMint, isSigner: false, isWritable: false },
      { pubkey: characterTokenAccount, isSigner: false, isWritable: false },
      { pubkey: saveSlotBlob, isSigner: false, isWritable: true },
      { pubkey: SystemProgram.programId, isSigner: false, isWritable: false },
    ],
    data,
  });
}

function writeSaveCompactInstruction({
  owner,
  rentPayer,
  characterMint,
  characterTokenAccount,
  saveCompact,
  saveHash,
  baseSaveHash,
  gameSlot,
  bytes,
}) {
  const data = Buffer.alloc(1 + 32 + 32 + 1 + 1 + 2 + bytes.length);
  let offset = 0;
  data.writeUInt8(5, offset);
  offset += 1;
  saveHashPublicKey(saveHash).toBuffer().copy(data, offset);
  offset += 32;
  saveHashPublicKey(baseSaveHash).toBuffer().copy(data, offset);
  offset += 32;
  data.writeUInt8(gameSlot, offset);
  offset += 1;
  data.writeUInt8(COMPRESSION_GZIP_JSON, offset);
  offset += 1;
  data.writeUInt16LE(bytes.length, offset);
  offset += 2;
  bytes.copy(data, offset);

  return new TransactionInstruction({
    programId: SOLAMON_PROGRAM_ID,
    keys: [
      { pubkey: owner, isSigner: true, isWritable: true },
      { pubkey: rentPayer, isSigner: true, isWritable: true },
      { pubkey: characterMint, isSigner: false, isWritable: false },
      { pubkey: characterTokenAccount, isSigner: false, isWritable: false },
      { pubkey: saveCompact, isSigner: false, isWritable: true },
      { pubkey: SystemProgram.programId, isSigner: false, isWritable: false },
    ],
    data,
  });
}

function decodeManifest(data) {
  return {
    initialized: data.readUInt8(0) !== 0,
    version: data.readUInt8(1),
    owner: new PublicKey(data.subarray(2, 34)).toBase58(),
    characterMint: new PublicKey(data.subarray(34, 66)).toBase58(),
    saveHash: data.subarray(66, 98).toString('hex'),
    compression: data.readUInt8(98),
    totalLen: data.readUInt32LE(99),
    chunkSize: data.readUInt16LE(103),
    chunkCount: data.readUInt16LE(105),
  };
}

function decodeChunk(data) {
  const len = data.readUInt16LE(100);
  return {
    initialized: data.readUInt8(0) !== 0,
    version: data.readUInt8(1),
    index: data.readUInt16LE(98),
    len,
    bytes: data.subarray(102, 102 + len),
  };
}

function decodeSaveSlotBlob(data) {
  const totalLen = data.readUInt32LE(99);
  return {
    initialized: data.readUInt8(0) !== 0,
    version: data.readUInt8(1),
    owner: new PublicKey(data.subarray(2, 34)).toBase58(),
    characterMint: new PublicKey(data.subarray(34, 66)).toBase58(),
    saveHash: data.subarray(66, 98).toString('hex'),
    compression: data.readUInt8(98),
    totalLen,
    gameSlot: data.readUInt8(103),
    bytes: data.subarray(104, 104 + totalLen),
  };
}

function decodeSaveCompact(data) {
  const len = data.readUInt16LE(132);
  return {
    initialized: data.readUInt8(0) !== 0,
    version: data.readUInt8(1),
    owner: new PublicKey(data.subarray(2, 34)).toBase58(),
    characterMint: new PublicKey(data.subarray(34, 66)).toBase58(),
    saveHash: data.subarray(66, 98).toString('hex'),
    baseSaveHash: data.subarray(98, 130).toString('hex'),
    compression: data.readUInt8(130),
    gameSlot: data.readUInt8(131),
    len,
    bytes: data.subarray(134, 134 + len),
  };
}
