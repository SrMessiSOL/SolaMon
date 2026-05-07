import { handleAuthorityHttpRequest } from '../tools/solana/authority-server.mjs';

export default async function handler(request, response) {
  const result = await handleAuthorityHttpRequest({
    method: request.method,
    url: '/api/health',
    bodyText: '',
  });
  for (const [key, value] of Object.entries(result.headers)) {
    response.setHeader(key, value);
  }
  response.status(result.status).send(result.body);
}
