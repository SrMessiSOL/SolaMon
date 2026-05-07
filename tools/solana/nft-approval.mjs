export async function requireNftApproval(payload) {
  const authorityUrl = process.env.SOLAMON_AUTHORITY_URL;
  if (!authorityUrl) {
    if (process.env.SOLAMON_REQUIRE_AUTHORITY === '1') {
      throw new Error('SOLAMON_AUTHORITY_URL is required for NFT approval');
    }
    return { mode: 'dev-local', approved: true };
  }

  const response = await fetch(authorityEndpoint(authorityUrl, 'approve-nft'), {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(`NFT authority rejected ${payload.operation}: ${await response.text()}`);
  }
  return response.json();
}

function authorityEndpoint(authorityUrl, path) {
  const base = authorityUrl.endsWith('/') ? authorityUrl : `${authorityUrl}/`;
  return new URL(path, base);
}
