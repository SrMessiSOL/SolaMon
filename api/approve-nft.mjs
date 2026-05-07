import { handleAuthorityHttpRequest } from '../tools/solana/authority-server.mjs';

export default async function handler(request, response) {
  const result = await handleAuthorityHttpRequest({
    method: request.method,
    url: '/api/approve-nft',
    bodyText: await readBody(request),
  });
  for (const [key, value] of Object.entries(result.headers)) {
    response.setHeader(key, value);
  }
  response.status(result.status).send(result.body);
}

async function readBody(request) {
  const chunks = [];
  for await (const chunk of request) {
    chunks.push(chunk);
  }
  return Buffer.concat(chunks).toString('utf8');
}
