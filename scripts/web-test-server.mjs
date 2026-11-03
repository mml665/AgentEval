// Isolated server for browser tests; never uses the user's normal database.
import { spawn, spawnSync } from 'node:child_process';
import { existsSync, mkdirSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { randomUUID } from 'node:crypto';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
mkdirSync(path.join(root, '.build'), { recursive: true });
const venv = path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const python = process.env.AGENTEVAL_TEST_PYTHON || (existsSync(venv) ? venv : 'python');
const db = path.join(root, '.build', `web-test-${randomUUID()}.db`).replaceAll('\\', '/');
const env = { ...process.env, PYTHONPATH: path.join(root, 'backend'), AGENTEVAL_DATABASE_URL: `sqlite:///${db}`, AGENTEVAL_API_TOKEN: 'local-browser-test-token-only' };
const migration = spawnSync(python, ['-m', 'agenteval', 'init-db'], { cwd: root, env, stdio: 'inherit', windowsHide: true });
if (migration.status !== 0) process.exit(migration.status || 1);
const server = spawn(python, ['-m', 'agenteval', 'serve', '--port', process.env.AGENTEVAL_WEB_TEST_PORT || '8127'], { cwd: root, env, stdio: 'inherit', windowsHide: true });
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => server.kill());
server.on('exit', code => process.exit(code || 0));
