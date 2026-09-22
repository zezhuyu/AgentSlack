'use strict';

const { app, BrowserWindow, Menu, Tray, clipboard, dialog, ipcMain, nativeImage, session, shell, systemPreferences, safeStorage } = require('electron');
const { spawn, execFile, execFileSync } = require('child_process');
const fs = require('fs');
const http = require('http');
const https = require('https');
const crypto = require('crypto');
const os = require('os');
const path = require('path');

let backend = null;
let mainWindow = null;
let backendPort = null;
let tray = null;
let settings = null;
let tunnelClient = null;
let tunnelStatus = { state: 'stopped', message: '' };
let isQuitting = false;

const hasSingleInstanceLock = app.requestSingleInstanceLock();
if (!hasSingleInstanceLock) app.quit();

function backendRoot() {
  return app.isPackaged
    ? path.join(process.resourcesPath, 'backend')
    : path.resolve(__dirname, '..');
}

function backendLaunch() {
  const args = [];
  if (app.isPackaged) {
    return {
      command: path.join(process.resourcesPath, 'backend', 'agent-slack-backend'),
      args,
      cwd: process.resourcesPath,
    };
  }
  const frozenBackend = path.join(__dirname, 'backend', 'agent-slack-backend');
  if (executable(frozenBackend)) {
    return { command: frozenBackend, args, cwd: __dirname };
  }
  const root = backendRoot();
  return {
    command: findPython(),
    args: [path.join(root, 'run.py')],
    cwd: root,
  };
}

function executable(pathname) {
  try {
    fs.accessSync(pathname, fs.constants.X_OK);
    return true;
  } catch (_) {
    return false;
  }
}

function findPython() {
  const configured = process.env.AGENT_SLACK_PYTHON;
  const candidates = [
    configured,
    '/opt/homebrew/bin/python3',
    '/usr/local/bin/python3',
    '/usr/bin/python3',
  ].filter(Boolean);
  for (const candidate of candidates) {
    if (executable(candidate)) return candidate;
  }
  try {
    const discovered = execFileSync('/usr/bin/which', ['python3'], { encoding: 'utf8' }).trim();
    if (discovered && executable(discovered)) return discovered;
  } catch (_) {
    // The startup error below gives the actionable requirement.
  }
  throw new Error('Python 3 is required. Install Python 3 or set AGENT_SLACK_PYTHON.');
}

function loadSettings() {
  const defaults = {
    allowLan: true, launchAtLogin: false, port: 8899,
    mcpTunnel: { enabled: false, tunnelId: '', profile: 'agent-slack', executablePath: '', serverUrl: '' },
    claudeTunnel: { enabled: false, domain: '', subdomain: 'agent-slack', routePath: 'mcp' },
  };
  try {
    const saved = JSON.parse(fs.readFileSync(path.join(app.getPath('userData'), 'settings.json'), 'utf8'));
    return {
      allowLan: Boolean(saved.allowLan),
      launchAtLogin: Boolean(saved.launchAtLogin),
      port: Number.isInteger(saved.port) && saved.port > 0 && saved.port < 65536 ? saved.port : defaults.port,
      mcpTunnel: { ...defaults.mcpTunnel, ...(saved.mcpTunnel || {}) },
      claudeTunnel: { ...defaults.claudeTunnel, ...(saved.claudeTunnel || {}) },
    };
  } catch (_) {
    return defaults;
  }
}

function claudeTunnelRoot() {
  return path.join(app.getPath('userData'), 'claude-mcp-tunnel');
}

function claudeTunnelToken() {
  try {
    const encrypted = fs.readFileSync(path.join(app.getPath('userData'), 'claude-tunnel-token.bin'));
    return safeStorage.isEncryptionAvailable() ? safeStorage.decryptString(encrypted) : '';
  } catch (_) {
    return '';
  }
}

function saveClaudeTunnelToken(value) {
  const target = path.join(app.getPath('userData'), 'claude-tunnel-token.bin');
  if (!value) {
    try { fs.unlinkSync(target); } catch (_) {}
    return;
  }
  if (!safeStorage.isEncryptionAvailable()) throw new Error('macOS secure storage is unavailable.');
  fs.writeFileSync(target, safeStorage.encryptString(value));
}

function runCommand(command, args, options = {}) {
  return new Promise((resolve, reject) => {
    execFile(command, args, options, (error, stdout, stderr) => {
      if (error) reject(new Error(stderr || error.message));
      else resolve({ stdout, stderr });
    });
  });
}

function writeClaudeTunnelFiles() {
  const config = settings.claudeTunnel || {};
  const domain = String(config.domain || '').trim().toLowerCase();
  const subdomain = String(config.subdomain || 'agent-slack').trim().toLowerCase();
  const routePath = String(config.routePath || 'mcp').trim().replace(/^\/+/, '') || 'mcp';
  if (!/^[a-z0-9.-]+\.tunnel\.anthropic\.com$/.test(domain)) {
    throw new Error('Claude tunnel domain must look like abcd1234.tunnel.anthropic.com.');
  }
  if (!/^[a-z0-9-]+$/.test(subdomain)) throw new Error('Claude tunnel subdomain may contain only letters, numbers, and hyphens.');
  const root = claudeTunnelRoot();
  const data = path.join(root, 'data');
  const configDir = path.join(root, 'config');
  fs.mkdirSync(data, { recursive: true, mode: 0o700 });
  fs.mkdirSync(configDir, { recursive: true, mode: 0o700 });
  const caKey = path.join(data, 'ca.key');
  const caCert = path.join(data, 'ca.crt');
  const tlsKey = path.join(data, 'tls.key');
  const tlsCert = path.join(data, 'tls.crt');
  if (!fs.existsSync(caKey) || !fs.existsSync(caCert)) {
    return runCommand('/usr/bin/openssl', [
      'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', caKey, '-out', caCert,
      '-days', '3650', '-subj', '/CN=agent-slack-mcp-tunnel-ca',
      '-addext', 'basicConstraints=critical,CA:TRUE',
      '-addext', 'keyUsage=critical,keyCertSign,cRLSign',
      '-addext', 'subjectKeyIdentifier=hash',
    ]).then(() => writeClaudeTunnelFiles());
  }
  if (!fs.existsSync(tlsKey) || !fs.existsSync(tlsCert)) {
    const ext = path.join(data, 'tls.ext');
    const csr = path.join(data, 'server.csr');
    fs.writeFileSync(ext, `subjectAltName = DNS:${domain},DNS:*.${domain}\nauthorityKeyIdentifier = keyid,issuer\nextendedKeyUsage = serverAuth\n`);
    return runCommand('/usr/bin/openssl', [
      'req', '-newkey', 'rsa:2048', '-nodes', '-keyout', tlsKey, '-out', csr,
      '-subj', `/CN=${domain}`,
    ]).then(() => runCommand('/usr/bin/openssl', [
      'x509', '-req', '-in', csr, '-CA', caCert, '-CAkey', caKey, '-CAcreateserial',
      '-out', tlsCert, '-days', '90', '-extfile', ext,
    ])).then(() => writeClaudeTunnelFiles());
  }
  fs.chmodSync(caKey, 0o600);
  fs.chmodSync(tlsKey, 0o600);
  fs.writeFileSync(path.join(configDir, 'mcp-proxy.yaml'), [
    'listen_addr: ":8080"',
    'log_level: info',
    'shutdown_timeout: 30s',
    `tunnel_domain: ${domain}`,
    'tls:',
    '  cert_file: /data/tls.crt',
    '  key_file: /data/tls.key',
    'routes:',
    `  ${subdomain}: http://host.docker.internal:${backendPort || configuredPort()}`,
    '',
  ].join('\n'));
  fs.writeFileSync(path.join(root, 'docker-compose.yaml'), [
    'services:',
    '  mcp-proxy:',
    '    image: us-docker.pkg.dev/anthropic-public-registry/images/mcp-proxy@sha256:efb27b299d627e4134815663cb8896641eeaee025d734c0f695582b4df38f013',
    '    volumes:',
    '      - ./config/mcp-proxy.yaml:/etc/mcp-gateway/config.yaml:ro',
    '      - ./data:/data:ro',
    '    restart: unless-stopped',
    '    user: "65532:65532"',
    '    read_only: true',
    '    security_opt:',
    '      - no-new-privileges:true',
    '    cap_drop:',
    '      - ALL',
    '    stop_grace_period: 30s',
    '  cloudflared:',
    '    image: cloudflare/cloudflared@sha256:6b599ca3e974349ead3286d178da61d291961182ec3fe9c505e1dd02c8ac31b0',
    '    command: tunnel --no-autoupdate run --url http://localhost:8080',
    '    environment:',
    '      - TUNNEL_TOKEN',
    '    network_mode: "service:mcp-proxy"',
    '    restart: unless-stopped',
    '    user: "65532:65532"',
    '    read_only: true',
    '    security_opt:',
    '      - no-new-privileges:true',
    '    cap_drop:',
    '      - ALL',
    '    stop_grace_period: 30s',
    '',
  ].join('\n'));
  return { root, caCert, url: `https://${subdomain}.${domain}/${routePath}` };
}

async function startClaudeTunnel() {
  const config = settings.claudeTunnel || {};
  const token = claudeTunnelToken();
  if (!config.enabled) return claudeTunnelInfo();
  if (!token) throw new Error('Claude tunnel token is required.');
  const files = await writeClaudeTunnelFiles();
  await runCommand('/usr/local/bin/docker', ['compose', 'up', '-d'], { cwd: files.root, env: { ...process.env, TUNNEL_TOKEN: token } });
  return claudeTunnelInfo();
}

async function stopClaudeTunnel() {
  const root = claudeTunnelRoot();
  if (fs.existsSync(path.join(root, 'docker-compose.yaml'))) {
    await runCommand('/usr/local/bin/docker', ['compose', 'down'], { cwd: root });
  }
  return claudeTunnelInfo();
}

function claudeTunnelInfo() {
  const config = settings?.claudeTunnel || {};
  const domain = String(config.domain || '').trim();
  const subdomain = String(config.subdomain || 'agent-slack').trim();
  const routePath = String(config.routePath || 'mcp').replace(/^\/+/, '');
  return {
    ...config,
    tokenConfigured: Boolean(claudeTunnelToken()),
    filesReady: fs.existsSync(path.join(claudeTunnelRoot(), 'data', 'ca.crt')) && fs.existsSync(path.join(claudeTunnelRoot(), 'docker-compose.yaml')),
    url: domain ? `https://${subdomain}.${domain}/${routePath}` : '',
    root: claudeTunnelRoot(),
  };
}

function encryptedTunnelKey() {
  try {
    const encrypted = fs.readFileSync(path.join(app.getPath('userData'), 'mcp-tunnel-key.bin'));
    return safeStorage.isEncryptionAvailable() ? safeStorage.decryptString(encrypted) : '';
  } catch (_) {
    return '';
  }
}

function saveEncryptedTunnelKey(value) {
  const target = path.join(app.getPath('userData'), 'mcp-tunnel-key.bin');
  if (!value) {
    try { fs.unlinkSync(target); } catch (_) {}
    return;
  }
  if (!safeStorage.isEncryptionAvailable()) throw new Error('macOS secure storage is unavailable.');
  fs.writeFileSync(target, safeStorage.encryptString(value));
}

function saveSettings() {
  const pathname = path.join(app.getPath('userData'), 'settings.json');
  fs.mkdirSync(path.dirname(pathname), { recursive: true });
  fs.writeFileSync(pathname, `${JSON.stringify(settings, null, 2)}\n`);
}

function configuredPort() {
  const override = Number.parseInt(process.env.AGENT_SLACK_PORT || '', 10);
  return Number.isInteger(override) && override > 0 && override < 65536 ? override : settings.port;
}

function tunnelServerUrl() {
  return `http://127.0.0.1:${backendPort || configuredPort()}/mcp`;
}

function findTunnelClient() {
  const configured = settings?.mcpTunnel?.executablePath;
  if (configured && executable(configured)) return configured;
  const downloaded = path.join(app.getPath('userData'), 'tools', 'tunnel-client', 'tunnel-client');
  if (executable(downloaded)) return downloaded;
  for (const candidate of ['/opt/homebrew/bin/tunnel-client', '/usr/local/bin/tunnel-client']) {
    if (executable(candidate)) return candidate;
  }
  try {
    const discovered = execFileSync('/usr/bin/which', ['tunnel-client'], { encoding: 'utf8' }).trim();
    if (discovered && executable(discovered)) return discovered;
  } catch (_) {}
  return null;
}

function requestJson(url) {
  return new Promise((resolve, reject) => {
    const request = https.get(url, { headers: { 'User-Agent': 'AgentSlack', Accept: 'application/vnd.github+json' } }, (response) => {
      if (response.statusCode >= 300 && response.statusCode < 400 && response.headers.location) {
        response.resume();
        return requestJson(response.headers.location).then(resolve, reject);
      }
      if (response.statusCode !== 200) {
        response.resume();
        return reject(new Error(`Download metadata failed with HTTP ${response.statusCode}.`));
      }
      let body = '';
      response.setEncoding('utf8');
      response.on('data', (chunk) => { body += chunk; });
      response.on('end', () => {
        try { resolve(JSON.parse(body)); } catch (_) { reject(new Error('Invalid tunnel-client release metadata.')); }
      });
    });
    request.on('error', reject);
  });
}

function downloadBuffer(url) {
  return new Promise((resolve, reject) => {
    const request = https.get(url, { headers: { 'User-Agent': 'AgentSlack', Accept: 'application/octet-stream' } }, (response) => {
      if (response.statusCode >= 300 && response.statusCode < 400 && response.headers.location) {
        response.resume();
        return downloadBuffer(response.headers.location).then(resolve, reject);
      }
      if (response.statusCode !== 200) {
        response.resume();
        return reject(new Error(`Tunnel-client download failed with HTTP ${response.statusCode}.`));
      }
      const chunks = [];
      response.on('data', (chunk) => chunks.push(chunk));
      response.on('end', () => resolve(Buffer.concat(chunks)));
    });
    request.on('error', reject);
  });
}

function findExtractedTunnelClient(root) {
  const visit = (directory) => {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      const candidate = path.join(directory, entry.name);
      if (entry.isDirectory()) {
        const result = visit(candidate);
        if (result) return result;
      } else if (entry.name === 'tunnel-client') {
        return candidate;
      }
    }
    return null;
  };
  return visit(root);
}

async function downloadTunnelClient() {
  const targetRoot = path.join(app.getPath('userData'), 'tools', 'tunnel-client');
  const target = path.join(targetRoot, 'tunnel-client');
  if (executable(target)) return target;
  tunnelStatus = { state: 'downloading', message: 'Downloading the official OpenAI tunnel-client…' };
  const metadata = await requestJson('https://api.github.com/repos/openai/tunnel-client/releases/latest');
  const architecture = process.arch === 'arm64' ? 'arm64' : 'amd64';
  // The release also publishes cloudflared runtime archives with the same
  // platform suffix. Select the actual tunnel-client package, not the
  // runtime-only archive.
  const asset = (metadata.assets || []).find((item) => (
    item.name.startsWith('tunnel-client-v') && item.name.endsWith(`darwin-${architecture}.zip`)
  ));
  if (!asset || !asset.browser_download_url) throw new Error(`No macOS ${architecture} tunnel-client release was found.`);
  const archive = await downloadBuffer(asset.browser_download_url);
  if (asset.digest && asset.digest.startsWith('sha256:')) {
    const actual = crypto.createHash('sha256').update(archive).digest('hex');
    if (actual !== asset.digest.slice('sha256:'.length)) throw new Error('Tunnel-client checksum verification failed.');
  }
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-slack-tunnel-'));
  try {
    const archivePath = path.join(temporary, asset.name);
    fs.writeFileSync(archivePath, archive);
    fs.mkdirSync(targetRoot, { recursive: true });
    await new Promise((resolve, reject) => {
      execFile('/usr/bin/ditto', ['-x', '-k', archivePath, temporary], (error, _stdout, stderr) => {
        if (error) reject(new Error(stderr || error.message));
        else resolve();
      });
    });
    const extracted = findExtractedTunnelClient(temporary);
    if (!extracted) throw new Error('The downloaded archive did not contain a runnable tunnel-client.');
    fs.copyFileSync(extracted, target);
    fs.chmodSync(target, 0o755);
    settings.mcpTunnel.executablePath = target;
    saveSettings();
    return target;
  } finally {
    fs.rmSync(temporary, { recursive: true, force: true });
  }
}

function tunnelInfo() {
  const config = settings?.mcpTunnel || {};
  const discovered = findTunnelClient();
  return {
    ...config,
    serverUrl: tunnelServerUrl(),
    runtimeKeyConfigured: Boolean(encryptedTunnelKey()),
    executableFound: Boolean(findTunnelClient()),
    executablePath: discovered || config.executablePath || '',
    status: tunnelStatus,
  };
}

function claudeConfigPath() {
  return path.join(app.getPath('home'), 'Library', 'Application Support', 'Claude', 'claude_desktop_config.json');
}

function claudeMcpLaunch() {
  const dataRoot = path.join(app.getPath('userData'), 'data');
  if (app.isPackaged) {
    return {
      command: path.join(process.resourcesPath, 'backend', 'agent-slack-backend'),
      args: ['--mcp-stdio', '--data-root', dataRoot],
    };
  }
  const launch = backendLaunch();
  return {
    command: launch.command,
    args: [...launch.args, '--mcp-stdio', '--data-root', dataRoot],
  };
}

function claudeMcpInfo() {
  const configPath = claudeConfigPath();
  let installed = false;
  try {
    const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
    const entry = config?.mcpServers?.['agent-slack'];
    const launch = claudeMcpLaunch();
    installed = Boolean(entry && entry.command === launch.command && JSON.stringify(entry.args || []) === JSON.stringify(launch.args));
  } catch (_) {}
  const launch = claudeMcpLaunch();
  return { configPath, installed, backendAvailable: executable(launch.command), launch };
}

function installClaudeMcp() {
  const configPath = claudeConfigPath();
  fs.mkdirSync(path.dirname(configPath), { recursive: true });
  let config = {};
  try {
    config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
  } catch (_) {}
  if (!config || typeof config !== 'object' || Array.isArray(config)) config = {};
  if (!config.mcpServers || typeof config.mcpServers !== 'object' || Array.isArray(config.mcpServers)) config.mcpServers = {};
  if (fs.existsSync(configPath)) {
    fs.copyFileSync(configPath, `${configPath}.agent-slack.bak`);
  }
  const launch = claudeMcpLaunch();
  config.mcpServers['agent-slack'] = { command: launch.command, args: launch.args };
  fs.writeFileSync(configPath, `${JSON.stringify(config, null, 2)}\n`);
  return claudeMcpInfo();
}

function startTunnelProcess() {
  if (tunnelClient) return Promise.resolve(tunnelInfo());
  const config = settings.mcpTunnel || {};
  if (!config.enabled) return Promise.resolve(tunnelInfo());
  if (!config.tunnelId) {
    tunnelStatus = { state: 'error', message: 'Tunnel ID is required.' };
    return Promise.resolve(tunnelInfo());
  }
  const runtimeKey = encryptedTunnelKey();
  if (!runtimeKey) {
    tunnelStatus = { state: 'error', message: 'Runtime API key is required.' };
    return Promise.resolve(tunnelInfo());
  }
  const profile = config.profile || 'agent-slack';
  const serverUrl = tunnelServerUrl();
  const launch = async () => {
    const executablePath = findTunnelClient() || await downloadTunnelClient();
    tunnelStatus = { state: 'initializing', message: 'Configuring Secure MCP Tunnel…' };
    return new Promise((resolve) => {
    execFile(executablePath, [
      'init', '--force', '--profile', profile,
      '--health-listen-addr', '127.0.0.1:0',
      '--tunnel-id', config.tunnelId, '--mcp-server-url', serverUrl,
    ], { env: { ...process.env, CONTROL_PLANE_API_KEY: runtimeKey } }, (error, _stdout, stderr) => {
      if (error) {
        tunnelStatus = { state: 'error', message: stderr || error.message };
        return resolve(tunnelInfo());
      }
      tunnelStatus = { state: 'starting', message: 'Starting Secure MCP Tunnel…' };
      const logPath = path.join(app.getPath('userData'), 'logs', 'tunnel.log');
      const log = fs.openSync(logPath, 'a');
      tunnelClient = spawn(executablePath, ['run', '--profile', profile], {
        env: { ...process.env, CONTROL_PLANE_API_KEY: runtimeKey },
        cwd: app.getPath('userData'),
        stdio: ['ignore', log, log],
      });
      tunnelStatus = { state: 'running', message: `Tunnel ${config.tunnelId} is running.` };
      tunnelClient.once('exit', (code) => {
        tunnelClient = null;
        tunnelStatus = { state: code === 0 ? 'stopped' : 'error', message: code === 0 ? '' : `Tunnel exited with code ${code}.` };
      });
      resolve(tunnelInfo());
    });
    });
  };
  return launch().catch((error) => {
    tunnelStatus = { state: 'error', message: error.message };
    return tunnelInfo();
  });
}

function stopTunnelProcess() {
  if (!tunnelClient) {
    tunnelStatus = { state: 'stopped', message: '' };
    return Promise.resolve(tunnelInfo());
  }
  const child = tunnelClient;
  tunnelClient = null;
  child.kill('SIGTERM');
  tunnelStatus = { state: 'stopped', message: '' };
  return Promise.resolve(tunnelInfo());
}

function bindHost() {
  if (process.env.AGENT_SLACK_IP) return process.env.AGENT_SLACK_IP;
  if (process.env.AGENT_SLACK_HOST) return process.env.AGENT_SLACK_HOST;
  return settings.allowLan ? '0.0.0.0' : '127.0.0.1';
}

function lanAddress() {
  for (const addresses of Object.values(os.networkInterfaces())) {
    for (const address of addresses || []) {
      if (address.family === 'IPv4' && !address.internal) return address.address;
    }
  }
  return '127.0.0.1';
}

function publicApiUrl() {
  const configuredHost = bindHost();
  const host = configuredHost === '0.0.0.0' ? lanAddress() : configuredHost;
  return `http://${host}:${backendPort || configuredPort()}/api/v1`;
}

function waitForHealth(port, attempts = 80) {
  return new Promise((resolve, reject) => {
    let remaining = attempts;
    const poll = () => {
      const request = http.get(`http://127.0.0.1:${port}/api/v1`, { timeout: 1000 }, (response) => {
        let body = '';
        response.setEncoding('utf8');
        response.on('data', (chunk) => { body += chunk; });
        response.on('end', () => {
          try {
            const payload = JSON.parse(body);
            if (response.statusCode === 200 && payload.service === 'agent-slack') return resolve();
          } catch (_) {
            // A different service on the configured port is not a healthy backend.
          }
          retry();
        });
      });
      request.on('error', retry);
      request.on('timeout', () => request.destroy());
    };
    const retry = () => {
      remaining -= 1;
      if (remaining <= 0) return reject(new Error('Agent Slack backend did not become ready.'));
      setTimeout(poll, 250);
    };
    poll();
  });
}

async function startBackend() {
  const port = configuredPort();
  const launch = backendLaunch();
  const dataRoot = path.join(app.getPath('userData'), 'data');
  const logRoot = path.join(app.getPath('userData'), 'logs');
  const defaultWhisperModel = path.join(app.getPath('userData'), 'models', 'ggml-base.bin');
  fs.mkdirSync(dataRoot, { recursive: true });
  fs.mkdirSync(logRoot, { recursive: true });
  const log = fs.openSync(path.join(logRoot, 'backend.log'), 'a');
  const pathValue = [
    '/opt/homebrew/bin',
    '/usr/local/bin',
    process.env.PATH || '',
  ].filter(Boolean).join(path.delimiter);

  backend = spawn(launch.command, [
    ...launch.args,
    '--host', bindHost(),
    '--port', String(port),
    '--data-root', dataRoot,
  ], {
    cwd: launch.cwd,
    env: {
      ...process.env,
      PATH: pathValue,
      ...(process.env.AGENT_SLACK_WHISPER_MODEL || !fs.existsSync(defaultWhisperModel)
        ? {}
        : { AGENT_SLACK_WHISPER_MODEL: defaultWhisperModel }),
      PYTHONUNBUFFERED: '1',
      PYTHONDONTWRITEBYTECODE: '1',
    },
    stdio: ['ignore', log, log],
  });
  backend.once('exit', () => { backend = null; });
  await waitForHealth(port);
  backendPort = port;
  if (settings.mcpTunnel?.enabled) startTunnelProcess();
  refreshTrayMenu();
  return port;
}

function stopBackend() {
  return new Promise((resolve) => {
    if (!backend) return resolve();
    const child = backend;
    const timeout = setTimeout(() => child.kill('SIGKILL'), 3000);
    child.once('exit', () => {
      clearTimeout(timeout);
      resolve();
    });
    child.kill('SIGTERM');
  });
}

async function restartBackend() {
  await stopBackend();
  backendPort = null;
  const port = await startBackend();
  if (mainWindow) mainWindow.loadURL(`http://127.0.0.1:${port}`);
}

function configureMicrophoneAccess() {
  const defaultSession = session.defaultSession;
  defaultSession.setPermissionRequestHandler((webContents, permission, callback) => {
    callback(permission === 'media');
  });
  defaultSession.setPermissionCheckHandler((webContents, permission) => permission === 'media');
  if (process.platform === 'darwin' && systemPreferences.askForMediaAccess) {
    systemPreferences.askForMediaAccess('microphone').catch(() => {});
  }
}

function createWindow(port) {
  if (process.platform === 'darwin') app.dock.show();
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 920,
    minWidth: 980,
    minHeight: 640,
    title: 'Agent Slack',
    backgroundColor: '#221527',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  mainWindow.loadURL(`http://127.0.0.1:${port}`);
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//i.test(url)) shell.openExternal(url);
    return { action: 'deny' };
  });
  mainWindow.webContents.on('will-navigate', (event, url) => {
    if (!url.startsWith(`http://127.0.0.1:${port}`)) event.preventDefault();
  });
  mainWindow.on('close', () => {
    if (!isQuitting && process.platform === 'darwin') app.dock.hide();
  });
  mainWindow.on('closed', () => { mainWindow = null; });
}

function showWindow() {
  if (process.platform === 'darwin') app.dock.show();
  if (!mainWindow && backendPort) createWindow(backendPort);
  if (mainWindow) {
    mainWindow.show();
    mainWindow.focus();
  }
}

function quitEntirely() {
  isQuitting = true;
  app.quit();
}

function configureDockMenu() {
  if (process.platform !== 'darwin') return;
  app.dock.setMenu(Menu.buildFromTemplate([
    { label: 'Open Agent Slack', click: showWindow },
    { label: 'Quit Agent Slack Entirely', click: quitEntirely },
  ]));
}

function configureApplicationMenu() {
  const template = [];
  if (process.platform === 'darwin') {
    template.push({
      label: app.name,
      submenu: [
        { role: 'about' },
        { type: 'separator' },
        { role: 'hide' },
        { role: 'hideOthers' },
        { role: 'unhide' },
        { type: 'separator' },
        {
          label: 'Quit Agent Slack Entirely',
          accelerator: 'CommandOrControl+Q',
          click: quitEntirely,
        },
      ],
    });
  }
  template.push({ role: 'fileMenu' }, { role: 'editMenu' }, { role: 'windowMenu' });
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

async function setLanAccess(enabled) {
  const previous = settings.allowLan;
  settings.allowLan = enabled;
  saveSettings();
  try {
    await restartBackend();
  } catch (error) {
    settings.allowLan = previous;
    saveSettings();
    await restartBackend().catch(() => {});
    dialog.showErrorBox('Could not change API access', error.message);
  }
  refreshTrayMenu();
}

function setLaunchAtLogin(enabled) {
  settings.launchAtLogin = enabled;
  saveSettings();
  app.setLoginItemSettings({ openAtLogin: enabled });
  refreshTrayMenu();
}

function refreshTrayMenu() {
  if (!tray || !settings) return;
  const apiUrl = publicApiUrl();
  tray.setToolTip(`Agent Slack Server\n${apiUrl}`);
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: 'Open Agent Slack', click: showWindow },
    { label: `API: ${apiUrl}`, enabled: false },
    { label: 'Copy API URL', click: () => clipboard.writeText(apiUrl) },
    { type: 'separator' },
    {
      label: 'Allow Trusted LAN Access',
      type: 'checkbox',
      checked: settings.allowLan,
      click: (item) => setLanAccess(item.checked),
    },
    {
      label: 'Launch at Login',
      type: 'checkbox',
      checked: settings.launchAtLogin,
      click: (item) => setLaunchAtLogin(item.checked),
    },
    { type: 'separator' },
    {
      label: 'Quit Agent Slack Entirely',
      click: quitEntirely,
    },
  ]));
}

function createTray() {
  const iconPath = app.isPackaged
    ? path.join(process.resourcesPath, 'menu-icon.png')
    : path.join(__dirname, 'assets', 'menu-icon.png');
  const icon = nativeImage.createFromPath(iconPath).resize({ width: 18, height: 18 });
  icon.setTemplateImage(true);
  tray = new Tray(icon);
  tray.on('click', showWindow);
  refreshTrayMenu();
}

ipcMain.handle('agent-slack:select-folder', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: 'Choose Agent System Folder',
    properties: ['openDirectory', 'createDirectory'],
  });
  return result.canceled ? null : result.filePaths[0];
});

ipcMain.handle('agent-slack:select-image', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: 'Choose Server Logo',
    properties: ['openFile'],
    filters: [{ name: 'Images', extensions: ['png', 'jpg', 'jpeg', 'webp', 'gif'] }],
  });
  return result.canceled ? null : result.filePaths[0];
});

ipcMain.handle('agent-slack:server-info', () => ({
  apiUrl: publicApiUrl(),
  allowLan: settings.allowLan,
  port: backendPort || configuredPort(),
}));

ipcMain.handle('agent-slack:tunnel-info', () => tunnelInfo());
ipcMain.handle('agent-slack:select-tunnel-client', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: 'Choose OpenAI tunnel-client', properties: ['openFile'],
  });
  return result.canceled ? null : result.filePaths[0];
});
ipcMain.handle('agent-slack:open-tunnel-docs', () => {
  shell.openExternal('https://platform.openai.com/settings/organization/tunnels');
});
ipcMain.handle('agent-slack:save-tunnel', async (_event, payload = {}) => {
  settings.mcpTunnel = {
    ...(settings.mcpTunnel || {}),
    enabled: Boolean(payload.enabled),
    tunnelId: String(payload.tunnelId || '').trim(),
    profile: String(payload.profile || 'agent-slack').trim() || 'agent-slack',
    executablePath: String(payload.executablePath || '').trim(),
  };
  const runtimeKey = String(payload.runtimeKey || '').trim();
  if (runtimeKey) saveEncryptedTunnelKey(runtimeKey);
  saveSettings();
  await stopTunnelProcess();
  if (settings.mcpTunnel.enabled) await startTunnelProcess();
  return tunnelInfo();
});
ipcMain.handle('agent-slack:start-tunnel', () => startTunnelProcess());
ipcMain.handle('agent-slack:stop-tunnel', () => stopTunnelProcess());
ipcMain.handle('agent-slack:download-tunnel-client', async () => {
  try {
    await downloadTunnelClient();
    tunnelStatus = { state: 'stopped', message: 'Official tunnel-client is ready.' };
  } catch (error) {
    tunnelStatus = { state: 'error', message: error.message };
  }
  return tunnelInfo();
});
ipcMain.handle('agent-slack:claude-mcp-info', () => claudeMcpInfo());
ipcMain.handle('agent-slack:install-claude-mcp', () => installClaudeMcp());
ipcMain.handle('agent-slack:open-claude-config', () => shell.openPath(claudeConfigPath()));
ipcMain.handle('agent-slack:claude-tunnel-info', () => claudeTunnelInfo());
ipcMain.handle('agent-slack:open-claude-tunnels', () => {
  shell.openExternal('https://platform.claude.com/settings/mcp-tunnels');
});
ipcMain.handle('agent-slack:save-claude-tunnel', async (_event, payload = {}) => {
  settings.claudeTunnel = {
    ...(settings.claudeTunnel || {}),
    enabled: Boolean(payload.enabled),
    domain: String(payload.domain || '').trim(),
    subdomain: String(payload.subdomain || 'agent-slack').trim() || 'agent-slack',
    routePath: String(payload.routePath || 'mcp').trim() || 'mcp',
  };
  const token = String(payload.token || '').trim();
  if (token) saveClaudeTunnelToken(token);
  saveSettings();
  await stopClaudeTunnel().catch(() => {});
  if (settings.claudeTunnel.enabled) await startClaudeTunnel();
  return claudeTunnelInfo();
});
ipcMain.handle('agent-slack:start-claude-tunnel', () => startClaudeTunnel());
ipcMain.handle('agent-slack:stop-claude-tunnel', () => stopClaudeTunnel());

app.whenReady().then(async () => {
  try {
    settings = loadSettings();
    if (settings.launchAtLogin) app.setLoginItemSettings({ openAtLogin: true });
    configureMicrophoneAccess();
    const port = await startBackend();
    if (settings.claudeTunnel?.enabled) startClaudeTunnel().catch((error) => {
      console.error(`Claude MCP tunnel: ${error.message}`);
    });
    createTray();
    configureDockMenu();
    configureApplicationMenu();
    const openedAtLogin = process.platform === 'darwin' && app.getLoginItemSettings().wasOpenedAtLogin;
    if (openedAtLogin) app.dock.hide();
    else createWindow(port);
  } catch (error) {
    dialog.showErrorBox('Agent Slack could not start', error.message);
    app.quit();
  }
});

app.on('activate', () => {
  showWindow();
});

app.on('second-instance', () => {
  showWindow();
});

app.on('window-all-closed', () => {
  // Closing the UI must not stop the background API server. The tray or a
  // second app launch can create a new window; only explicit quit stops it.
  if (process.platform === 'darwin') app.dock.hide();
});

app.on('before-quit', () => {
  isQuitting = true;
  if (tunnelClient) tunnelClient.kill('SIGTERM');
  if (backend) backend.kill('SIGTERM');
  backendPort = null;
});
