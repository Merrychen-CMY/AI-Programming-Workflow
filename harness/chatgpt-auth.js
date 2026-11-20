import { randomBytes, randomUUID, createHash } from 'node:crypto';
import { mkdir, rmdir } from 'node:fs/promises';
import { jwtVerify, createLocalJWKSet } from './vendor/jose/dist/webapi/index.js';
import { SetupError, networkFailure } from './diagnostics.js';

const ISSUER = 'https://auth.openai.com';
const TOKEN = `${ISSUER}/api/accounts/oauth/token`;
const RESOURCE = 'https://api.openai.com/v1';
const scopes = 'openid profile email offline_access resource.invoke chatgpt.tokens.use.direct';
export const profileDigest = profile => createHash('sha256').update(`${profile.client_id}:${profile.subject}`).digest('hex');

async function jsonFetch(fetchImpl, url, options = {}) {
  let response;
  try { response = await fetchImpl(url, { ...options, redirect: 'error', signal: AbortSignal.timeout(20000) }); }
  catch (error) { throw networkFailure(error, 'credentials'); }
  if (!response.ok) throw new SetupError('CHATGPT_AUTH', 'credentials', response.status);
  const reader = response.body?.getReader();
  if (!reader) throw new SetupError('CHATGPT_AUTH');
  const chunks = []; let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read(); if (done) break;
      if ((size += value.byteLength) > 500000) throw new SetupError('API_RESPONSE_SIZE');
      chunks.push(Buffer.from(value));
    }
  } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
  const text = Buffer.concat(chunks).toString('utf8');
  try { return JSON.parse(text); } catch { throw new SetupError('CHATGPT_AUTH'); }
}

/** All renewable credentials stay in the host vault, never in task files or worker environments. */
export function createChatGPTAuth({ directory, credentials, keyRef, fetchImpl = fetch }) {
  const ref = `${keyRef}_CHATGPT`;
  let pending;
  const read = async () => {
    try { const value = await credentials.resolve(ref); return value?.value ? JSON.parse(value.value) : { profiles: [] }; }
    catch { throw new SetupError('CREDENTIAL_READ', 'credentials'); }
  };
  const write = async value => {
    try { await credentials.set(ref, JSON.stringify(value)); }
    catch { throw new SetupError('CREDENTIAL_WRITE', 'credentials'); }
  };
  // Shared by all engine instances and processes; never replay a rotating refresh concurrently.
  const locked = async operation => {
    await mkdir(directory, { recursive: true });
    const path = `${directory}/chatgpt-auth.lock`;
    try { await mkdir(path); } catch { throw new SetupError('CHATGPT_BUSY'); }
    try { return await operation(); } finally { await rmdir(path); }
  };
  const publicProfile = p => ({ id: p.client_id, label: `${p.email || 'ChatGPT'} · ${p.client_id.slice(-8)}`,
    connected: Boolean(p.access_token), plan_enabled: p.scopes?.includes('chatgpt.tokens.use.direct') === true });
  async function status() {
    const state = await read();
    return { active: state.active || null, profiles: state.profiles.map(publicProfile),
      pending: Boolean(pending && pending.expires > Date.now()) };
  }
  async function begin(redirectUri, selected) {
    const uri = new URL(redirectUri);
    if (uri.protocol !== 'http:' || uri.hostname !== '127.0.0.1' || uri.pathname !== '/auth/callback')
      throw new SetupError('CHATGPT_AUTH');
    return locked(async () => {
      const state = await read();
      if (!state.host_id) { state.host_id = `urn:uuid:${randomUUID()}`; await write(state); }
      const profile = selected ? state.profiles.find(p => p.client_id === selected) : null;
      if (selected && !profile) throw new SetupError('CHATGPT_AUTH');
      const verifier = randomBytes(48).toString('base64url');
      pending = { state: randomBytes(32).toString('hex'), nonce: randomBytes(32).toString('hex'), verifier,
        redirectUri, client_id: profile?.client_id, subject: profile?.subject, expires: Date.now() + 600000 };
      const query = new URLSearchParams({ client_id: profile?.client_id || 'dynamic_agent_client',
        ext_agent_host_id: state.host_id, response_type: 'code', redirect_uri: redirectUri, scope: scopes,
        resource: RESOURCE, state: pending.state, nonce: pending.nonce, code_challenge_method: 'S256',
        code_challenge: createHash('sha256').update(verifier).digest('base64url') });
      if (!profile) query.set('agent_name_hint', 'ReqPilot');
      if (profile?.id_token) query.set('id_token_hint', profile.id_token);
      return `${ISSUER}/api/accounts/authorize?${query}`;
    });
  }
  async function discovery() {
    const data = await jsonFetch(fetchImpl, `${ISSUER}/.well-known/openid-configuration`);
    if (data.issuer !== ISSUER) throw new SetupError('CHATGPT_AUTH');
    const endpoint = key => {
      const url = new URL(data[key]);
      if (url.origin !== ISSUER || url.username || url.password || url.hash) throw new SetupError('CHATGPT_AUTH');
      return url.href;
    };
    return { jwks: endpoint('jwks_uri'), revoke: data.revocation_endpoint ? endpoint('revocation_endpoint') : null };
  }
  async function validateIdentity(token, client, nonce, subject) {
    try {
      const doc = await discovery();
      const jwks = createLocalJWKSet(await jsonFetch(fetchImpl, doc.jwks));
      const { payload } = await jwtVerify(token, jwks, { issuer: ISSUER, audience: client,
        algorithms: ['RS256', 'ES256'], requiredClaims: ['sub', 'exp', 'iat'], clockTolerance: 5 });
      if (!payload.sub || (nonce && payload.nonce !== nonce) || (subject && payload.sub !== subject))
        throw new Error('Invalid identity');
      return payload;
    } catch { throw new SetupError('CHATGPT_IDENTITY'); }
  }
  function tokenSet(tokens, previous = {}) {
    const granted = typeof tokens.scope === 'string' ? tokens.scope.split(/\s+/) : previous.scopes || [];
    if (!granted.includes('chatgpt.tokens.use.direct') || !granted.includes('resource.invoke'))
      throw new SetupError('CHATGPT_CONSENT');
    if (typeof tokens.access_token !== 'string' || !tokens.access_token || tokens.token_type?.toLowerCase() !== 'bearer'
      || !Number.isFinite(tokens.expires_in) || tokens.expires_in <= 0) throw new SetupError('CHATGPT_AUTH');
    return { ...previous, access_token: tokens.access_token, refresh_token: tokens.refresh_token || previous.refresh_token,
      id_token: tokens.id_token || previous.id_token, scopes: granted, expires_at: Date.now() + tokens.expires_in * 1000 };
  }
  async function exchange(fields) {
    return jsonFetch(fetchImpl, TOKEN, { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({ ...fields, resource: RESOURCE }) });
  }
  async function callback(url) {
    const p = pending; pending = undefined;
    if (!p || p.expires <= Date.now() || url.searchParams.get('state') !== p.state) throw new SetupError('CHATGPT_STATE');
    if (url.searchParams.has('error')) throw new SetupError('CHATGPT_CONSENT');
    const client = url.searchParams.get('client_id') || p.client_id;
    if (!client || client === 'dynamic_agent_client' || !/^[A-Za-z0-9_-]{1,200}$/.test(client)
      || (p.client_id && client !== p.client_id) || !url.searchParams.get('code')) throw new SetupError('CHATGPT_STATE');
    return locked(async () => {
      const state = await read();
      let profile = state.profiles.find(x => x.client_id === client);
      // Keep the issued registration if exchange fails, so retry does not create another client.
      if (!profile) { profile = { client_id: client }; state.profiles.push(profile); await write(state); }
      const tokens = await exchange({ grant_type: 'authorization_code', client_id: client,
        code: url.searchParams.get('code'), code_verifier: p.verifier, redirect_uri: p.redirectUri });
      const identity = await validateIdentity(tokens.id_token, client, p.nonce, p.subject || profile.subject);
      const next = tokenSet(tokens, profile);
      Object.assign(profile, next, { subject: identity.sub, email: identity.email || '' });
      state.active = client; await write(state);
      return publicProfile(profile);
    });
  }
  async function access(client) {
    return locked(async () => {
      const state = await read();
      const profile = state.profiles.find(p => p.client_id === client);
      if (!profile?.subject || !profile.access_token) throw new SetupError('CHATGPT_LOGIN_REQUIRED');
      if (profile.expires_at <= Date.now() + 60000) {
        if (!profile.refresh_token) throw new SetupError('CHATGPT_LOGIN_REQUIRED');
        const tokens = await exchange({ grant_type: 'refresh_token', client_id: client, refresh_token: profile.refresh_token });
        if (tokens.id_token) await validateIdentity(tokens.id_token, client, null, profile.subject);
        Object.assign(profile, tokenSet(tokens, profile)); await write(state);
      }
      if (!profile.scopes?.includes('chatgpt.tokens.use.direct')) throw new SetupError('CHATGPT_CONSENT');
      return { token: profile.access_token, identity: profileDigest(profile) };
    });
  }
  async function identity(client) {
    const state = await read(), profile = state.profiles.find(p => p.client_id === client);
    if (!profile?.access_token || !profile.subject) throw new SetupError('CHATGPT_LOGIN_REQUIRED');
    return profileDigest(profile);
  }
  async function models(client) {
    const { token } = await access(client);
    const data = await jsonFetch(fetchImpl, `${RESOURCE}/models`, { headers: { Authorization: `Bearer ${token}` } });
    if (!Array.isArray(data.models)) throw new SetupError('CHATGPT_MODELS');
    return data.models.filter(m => m.visibility === 'list' && typeof m.slug === 'string')
      .map(m => ({ id: m.slug, name: m.display_name || m.slug }));
  }
  async function logout(client) {
    return locked(async () => {
      const state = await read(), profile = state.profiles.find(p => p.client_id === client);
      if (!profile) throw new SetupError('CHATGPT_LOGIN_REQUIRED');
      let revoked = false;
      try {
        const doc = await discovery();
        if (doc.revoke && profile.refresh_token) {
          const response = await fetchImpl(doc.revoke, { method: 'POST', redirect: 'error', signal: AbortSignal.timeout(20000),
            headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
            body: new URLSearchParams({ token: profile.refresh_token, token_type_hint: 'refresh_token', client_id: client }) });
          revoked = response.status === 200;
        }
      } catch { /* Local sign-out still stops usage; remote status is reported truthfully. */ }
      for (const key of ['access_token', 'refresh_token', 'id_token', 'expires_at', 'scopes']) delete profile[key];
      if (state.active === client) delete state.active;
      await write(state); return { revoked };
    });
  }
  return { status, begin, callback, access, identity, models, logout, cancel: () => { pending = undefined; } };
}
