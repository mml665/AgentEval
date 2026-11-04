// Isolated server for browser tests; never uses the user's normal database.
import { spawn, spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, writeFileSync } from 'node:fs';
import net from 'node:net';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { randomUUID } from 'node:crypto';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
mkdirSync(path.join(root, '.build'), { recursive: true });
const venv = path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const python = process.env.AGENTEVAL_TEST_PYTHON || (existsSync(venv) ? venv : 'python');
const db = path.join(root, '.build', `web-test-${randomUUID()}.db`).replaceAll('\\', '/');
const probe = net.createServer();
await new Promise(resolve => probe.listen(0, '127.0.0.1', resolve));
const fixturePort = probe.address().port;
await new Promise(resolve => probe.close(resolve));
writeFileSync(path.join(root, '.build/http-browser-fixture.json'), JSON.stringify({ endpoint: `http://127.0.0.1:${fixturePort}/invoke` }));
const env = { ...process.env, PYTHONPATH: path.join(root, 'backend'), AGENTEVAL_DATABASE_URL: `sqlite:///${db}`, AGENTEVAL_API_TOKEN: 'local-browser-test-token-only', AGENTEVAL_AUTH_MODE: 'legacy', AGENTEVAL_EXECUTION_MODE: 'local', AGENTEVAL_SESSION_COOKIE_SECURE: '0', AGENTEVAL_SESSION_ORIGINS: `http://127.0.0.1:${process.env.AGENTEVAL_WEB_TEST_PORT || 8127}`, AGENTEVAL_JUDGE_ENDPOINT: '', AGENTEVAL_JUDGE_MODEL: '', AGENTEVAL_JUDGE_API_KEY: '',
  AGENTEVAL_ENVIRONMENT_ROOT: path.join(root, '.build', `web-environments-${randomUUID()}`),
  AGENTEVAL_AGENT_SECRET_BROWSER_HTTP: 'browser-http-fixture-secret-not-real', AGENTEVAL_CREDENTIAL_FINGERPRINT_KEY: 'browser-fixture-fingerprint-key-not-real-32',
  AGENTEVAL_HTTP_ALLOWLIST: `127.0.0.1:9001,localhost:9001,127.0.0.1:${fixturePort}`, AGENTEVAL_BOOTSTRAP_PASSWORD: 'browser-admin-password-fixture-only' };
const migration = spawnSync(python, ['-m', 'agenteval', 'init-db'], { cwd: root, env, stdio: 'inherit', windowsHide: true });
if (migration.status !== 0) process.exit(migration.status || 1);
const initialize = spawnSync(python, ['-m', 'agenteval', 'init-admin', '--username', 'fixtureadmin'], { cwd: root, env, stdio: 'inherit', windowsHide: true });
if (initialize.status !== 0) process.exit(initialize.status || 1);
delete env.AGENTEVAL_BOOTSTRAP_PASSWORD;
const requiredFixtureEnv = new Set(['PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'HOME', 'USERPROFILE', 'SYSTEMDRIVE', 'COMSPEC', 'PATHEXT', 'LANG', 'LC_ALL']);
const fixtureEnv = { ...Object.fromEntries(Object.entries(env).filter(([k]) => requiredFixtureEnv.has(k.toUpperCase()))), AGENTEVAL_AGENT_SECRET_BROWSER_HTTP: env.AGENTEVAL_AGENT_SECRET_BROWSER_HTTP };
const fixture = spawn(python, ['examples/auth_http_fixture.py', '--port', String(fixturePort)], { cwd: root, env: fixtureEnv, stdio: 'inherit', windowsHide: true });
const server = spawn(python, ['-m', 'agenteval', 'serve', '--port', process.env.AGENTEVAL_WEB_TEST_PORT || '8127'], { cwd: root, env, stdio: 'inherit', windowsHide: true });
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => { server.kill(); fixture.kill(); });
server.on('exit', code => { fixture.kill(); process.exit(code || 0); });
