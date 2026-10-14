#!/usr/bin/env node
// Wave 12 (2.2) — **independent** MCP client (official TypeScript SDK,
// @modelcontextprotocol/sdk) against the running hub (compose): proves that a third-party client
// connects the way Langdock would.
//
//   cd platform/loadtest/mcp-client && npm ci && node check.mjs [--out file.md]
//
// Variables: HUB_URL (default http://localhost:8080), HUB_USER / HUB_PASSWORD (default the
// compose demo administrator).
//
// Steps:
//   1. OAuth via the SDK itself: 401 → discovery (RFC 9728 + RFC 8414) → dynamic registration
//      (RFC 7591) → authorization with PKCE S256 and `resource` → the "browser" (this script)
//      logs in to the hub, approves on the consent screen → code exchange → token;
//   2. initialize + tools/list + tools/call (read) + resources/list over Streamable HTTP;
//   3. destructive tool → approval request in the Inbox (does not execute);
//   4. invalid access token → the SDK renews it with the refresh token, without new authorization;
//   5. "header" mode (API key): personal token in Authorization: Bearer (Langdock "API Key").
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';
import { UnauthorizedError } from '@modelcontextprotocol/sdk/client/auth.js';
import { LATEST_PROTOCOL_VERSION } from '@modelcontextprotocol/sdk/types.js';
import { writeFileSync } from 'node:fs';

const HUB = (process.env.HUB_URL || 'http://localhost:8080').replace(/\/$/, '');
const USER = process.env.HUB_USER || 'admin@demo.local';
const PASS = process.env.HUB_PASSWORD || 'admin';
const REDIRECT = 'http://127.0.0.1:53682/callback';
const outIdx = process.argv.indexOf('--out');
const OUT = outIdx > 0 ? process.argv[outIdx + 1] : null;

const lines = [];
let failures = 0;
const t0 = Date.now();
function ok(cond, what, extra = '') {
  const l = `${cond ? 'ok   ' : 'FALHA'} ${what}${extra ? ` — ${extra}` : ''}`;
  console.log(`[${((Date.now() - t0) / 1000).toFixed(1).padStart(5)}s] ${l}`);
  lines.push(`- ${cond ? '✅' : '❌'} ${what}${extra ? ` — ${extra}` : ''}`);
  if (!cond) failures++;
  return cond;
}

/** The user's "browser": logs in to the hub and approves on the consent screen. */
async function browserApprove(authUrl) {
  const login = await fetch(`${HUB}/login`, {
    method: 'POST',
    redirect: 'manual',
    headers: { 'content-type': 'application/x-www-form-urlencoded', origin: HUB },
    body: new URLSearchParams({ email: USER, password: PASS, next: '/' }),
  });
  const cookie = (login.headers.getSetCookie?.() || [])
    .map((c) => c.split(';')[0])
    .find((c) => c.startsWith('hub_session='));
  if (!cookie) throw new Error(`login falhou (HTTP ${login.status})`);
  const page = await fetch(authUrl, { headers: { cookie }, redirect: 'manual' });
  const html = await page.text();
  const nonce = (html.match(/name="consent" value="([0-9a-f]+)"/) || [])[1];
  ok(page.status === 200 && !!nonce, 'tela de consentimento do hub aberta', `cliente "${(html.match(/data-oauth-client="">([^<]+)</) || [])[1] || '?'}"`);
  const body = new URLSearchParams({ consent: nonce, decision: 'approve' });
  body.append('scope', 'mcp');
  body.append('scope', 'api:write');
  const dec = await fetch(`${HUB}/oauth/authorize`, {
    method: 'POST',
    redirect: 'manual',
    headers: { cookie, origin: HUB, 'content-type': 'application/x-www-form-urlencoded' },
    body,
  });
  const loc = new URL(dec.headers.get('location') || 'http://x/');
  ok(dec.status === 303 && loc.searchParams.get('iss') === HUB, 'consentimento aprovado; retorno com code, state e iss (RFC 9207)', `${loc.origin}${loc.pathname}`);
  return loc.searchParams.get('code');
}

class Provider {
  constructor() {
    this.info = undefined;
    this.tok = undefined;
    this.verifier = '';
    this.code = null;
    this.redirects = 0;
  }
  get redirectUrl() { return REDIRECT; }
  get clientMetadata() {
    return {
      client_name: 'Verificação MCP (SDK oficial)',
      redirect_uris: [REDIRECT],
      grant_types: ['authorization_code', 'refresh_token'],
      response_types: ['code'],
      token_endpoint_auth_method: 'none',
    };
  }
  state() { return 'st-' + Date.now(); }
  clientInformation() { return this.info; }
  saveClientInformation(i) { this.info = i; }
  tokens() { return this.tok; }
  saveTokens(t) { this.tok = t; }
  async redirectToAuthorization(url) {
    this.redirects++;
    ok(url.searchParams.get('code_challenge_method') === 'S256' && !!url.searchParams.get('resource'), 'SDK pediu autorização com PKCE S256 e resource (RFC 8707)', url.searchParams.get('resource'));
    this.code = await browserApprove(url.toString());
  }
  saveCodeVerifier(v) { this.verifier = v; }
  codeVerifier() { return this.verifier; }
}

async function connect(provider, extraHeaders) {
  const transport = new StreamableHTTPClientTransport(new URL(`${HUB}/mcp`), {
    authProvider: provider,
    requestInit: extraHeaders ? { headers: extraHeaders } : undefined,
  });
  const client = new Client({ name: 'hub-mcp-client-check', version: '1.0.0' });
  await client.connect(transport);
  return { client, transport };
}

async function main() {
  console.log(`hub: ${HUB} · SDK @modelcontextprotocol/sdk (protocolo ${LATEST_PROTOCOL_VERSION})`);
  // 0) The 401 challenge points to the metadata.
  const r401 = await fetch(`${HUB}/mcp`, { method: 'POST', headers: { 'content-type': 'application/json', accept: 'application/json, text/event-stream' }, body: '{"jsonrpc":"2.0","id":1,"method":"ping"}' });
  ok(r401.status === 401 && /resource_metadata="/.test(r401.headers.get('www-authenticate') || ''), '401 com WWW-Authenticate resource_metadata', r401.headers.get('www-authenticate'));

  // 1) OAuth via the SDK.
  const p = new Provider();
  let conn;
  try {
    conn = await connect(p);
    ok(false, 'o hub deveria exigir autorização');
  } catch (e) {
    ok(e instanceof UnauthorizedError && !!p.code, 'SDK descobriu o servidor de autorização, registrou o cliente (DCR) e obteve o código', p.info?.client_id);
  }
  const first = new StreamableHTTPClientTransport(new URL(`${HUB}/mcp`), { authProvider: p });
  await first.finishAuth(p.code);
  ok(!!p.tok?.access_token && !!p.tok?.refresh_token, 'código trocado por token (PKCE) com refresh token', `escopo "${p.tok?.scope}", expira em ${p.tok?.expires_in}s`);

  // 2) Protocol.
  conn = await connect(p);
  const { client } = conn;
  const sv = client.getServerVersion();
  ok(sv?.name === 'application-hub', 'initialize (Streamable HTTP)', `servidor ${sv?.name} ${sv?.version} · sessão ${conn.transport.sessionId ? 'sim' : 'não'}`);
  const tools = await client.listTools();
  const names = tools.tools.map((t) => t.name);
  ok(names.includes('apps_list') && names.includes('deployments_rollback'), 'tools/list', `${names.length} ferramentas (${names.filter((n) => n.includes('.')).length} de apps)`);
  const res = await client.callTool({ name: 'apps_list', arguments: {} });
  ok(res.isError === false, 'tools/call apps_list (leitura)', (res.content?.[0]?.text || '').slice(0, 60));
  const rs = await client.listResources();
  ok(rs.resources.some((r) => r.uri === 'hub://apps'), 'resources/list');
  // 3) Destructive → human approval.
  const d = await client.callTool({ name: 'deployments_rollback', arguments: { id: `sdk-${Date.now() % 1e6}` } });
  ok(d.structuredContent?.approval_required === true || JSON.stringify(d).includes('approval'), 'ferramenta destrutiva cria pedido de aprovação na Inbox (não executa)', `aprovação ${d.structuredContent?.approval_id || '?'}`);
  await conn.transport.terminateSession().catch(() => {});
  await client.close();

  // 4) Renewal via the refresh token (no new authorization).
  const redirectsBefore = p.redirects;
  const oldRefresh = p.tok.refresh_token;
  p.tok = { ...p.tok, access_token: 'hubo_invalido' };
  conn = await connect(p);
  const again = await conn.client.listTools();
  ok(again.tools.length > 0 && p.redirects === redirectsBefore && p.tok.refresh_token !== oldRefresh, 'token vencido/inválido → SDK renovou com refresh (rotação), sem nova autorização');
  await conn.client.close();
  // Reuse of the old refresh token is detected (the grant is revoked).
  const reuse = await fetch(`${HUB}/oauth/token`, { method: 'POST', headers: { 'content-type': 'application/x-www-form-urlencoded' }, body: new URLSearchParams({ grant_type: 'refresh_token', refresh_token: oldRefresh, client_id: p.info.client_id }) });
  const after = await fetch(`${HUB}/mcp`, { method: 'POST', headers: { authorization: `Bearer ${p.tok.access_token}`, 'content-type': 'application/json', accept: 'application/json, text/event-stream' }, body: '{"jsonrpc":"2.0","id":1,"method":"ping"}' });
  ok(reuse.status === 400 && after.status === 401, 'reuso do refresh antigo → concessão revogada', `reuso ${reuse.status} · token atual ${after.status}`);

  // 5) Header mode (personal token), like Langdock's "API Key".
  const login = await fetch(`${HUB}/login`, { method: 'POST', redirect: 'manual', headers: { 'content-type': 'application/x-www-form-urlencoded', origin: HUB }, body: new URLSearchParams({ email: USER, password: PASS, next: '/' }) });
  const cookie = (login.headers.getSetCookie?.() || []).map((c) => c.split(';')[0]).find((c) => c.startsWith('hub_session='));
  const pt = await fetch(`${HUB}/api/me/tokens`, { method: 'POST', headers: { cookie, origin: HUB, 'content-type': 'application/json' }, body: JSON.stringify({ name: `mcp-client-check ${Date.now()}`, actions: ['mcp'], expires_days: 1 }) });
  const personal = (await pt.json()).token;
  const t = new StreamableHTTPClientTransport(new URL(`${HUB}/mcp`), { requestInit: { headers: { Authorization: `Bearer ${personal}` } } });
  const c2 = new Client({ name: 'hub-mcp-client-check-header', version: '1.0.0' });
  await c2.connect(t);
  const l2 = await c2.listTools();
  ok(l2.tools.length > 0 && !l2.tools.some((x) => x.name === 'deployments_rollback'), 'token pessoal no cabeçalho (sem api:write: só leitura)', `${l2.tools.length} ferramentas`);
  await c2.close();
}

try {
  await main();
} catch (e) {
  ok(false, 'execução', e?.stack || String(e));
}
const summary = failures ? `${failures} FALHA(S)` : 'TODAS AS VERIFICAÇÕES PASSARAM';
console.log(summary);
if (OUT) {
  writeFileSync(OUT, `# Cliente MCP independente (SDK oficial TypeScript) — ${new Date().toISOString()}\n\n- Hub: ${HUB}\n- SDK: @modelcontextprotocol/sdk (protocolo ${LATEST_PROTOCOL_VERSION})\n\n${lines.join('\n')}\n\n**${summary}**\n`);
}
process.exit(failures ? 1 : 0);
