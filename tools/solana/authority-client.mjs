import { PublicKey } from '@solana/web3.js';
import {
  SOLAMON_GAME_AUTHORITY,
  SOLAMON_PROGRAM_ID,
  gameAuthorityKeypair,
} from './player-instructions.mjs';

const TYPED_TAGS = new Set([7, 8, 9, 10, 11, 12, 13, 14]);
const CREATE_PLAYER_TAG = 0;

export async function sendAndConfirmWithAuthority({
  connection,
  transaction,
  signers,
  gameAction = null,
  commitment = 'confirmed',
}) {
  requireAuthorityInstruction(transaction, gameAction);
  const authorityUrl = process.env.SOLAMON_AUTHORITY_URL;
  const localSigners = uniqueSigners(signers);

  if (!authorityUrl) {
    transaction.sign(...uniqueSigners([...localSigners, gameAuthorityKeypair()]));
    const signature = await connection.sendRawTransaction(transaction.serialize());
    await connection.confirmTransaction(signature, commitment);
    return signature;
  }

  transaction.partialSign(...localSigners);
  const signed = await requestAuthoritySignature({
    authorityUrl,
    transaction,
    gameAction,
  });
  const signature = await connection.sendRawTransaction(signed.serialize());
  await connection.confirmTransaction(signature, commitment);
  return signature;
}

async function requestAuthoritySignature({ authorityUrl, transaction, gameAction }) {
  const response = await fetch(authorityEndpoint(authorityUrl, 'sign'), {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({
      transaction: transaction.serialize({
        requireAllSignatures: false,
        verifySignatures: false,
      }).toString('base64'),
      gameAction,
    }),
  });
  if (!response.ok) {
    throw new Error(`Authority signer rejected transaction: ${await response.text()}`);
  }
  const body = await response.json();
  if (!body.transaction) {
    throw new Error('Authority signer returned no transaction');
  }
  return transaction.constructor.from(Buffer.from(body.transaction, 'base64'));
}

function authorityEndpoint(authorityUrl, path) {
  const base = authorityUrl.endsWith('/') ? authorityUrl : `${authorityUrl}/`;
  return new URL(path, base);
}

function requireAuthorityInstruction(transaction, gameAction) {
  for (const instruction of transaction.instructions) {
    if (!instruction.programId.equals(SOLAMON_PROGRAM_ID)) {
      continue;
    }
    const tag = instruction.data[0];
    if (tag === CREATE_PLAYER_TAG || TYPED_TAGS.has(tag)) {
      const authorityMeta = instruction.keys.find((key) =>
        key.pubkey.equals(SOLAMON_GAME_AUTHORITY),
      );
      if (!authorityMeta?.isSigner) {
        throw new Error('Solamon game authority signer is missing');
      }
      if (TYPED_TAGS.has(tag) && !gameAction) {
        throw new Error('Typed Solamon action requires gameAction context');
      }
      return;
    }
  }
  throw new Error('Transaction does not contain an authority-gated Solamon instruction');
}

function uniqueSigners(signers) {
  const map = new Map();
  for (const signer of signers) {
    if (signer?.publicKey instanceof PublicKey) {
      map.set(signer.publicKey.toBase58(), signer);
    }
  }
  return [...map.values()];
}
