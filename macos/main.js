'use strict';

const { app, BrowserWindow, Menu, Tray, clipboard, dialog, ipcMain, nativeImage, shell } = require('electron');
const { spawn, execFileSync } = require('child_process');
const fs = require('fs');
const http = require('http');
const os = require('os');
const path = require('path');

let backend = null;
let mainWindow = null;
let backendPort = null;
let tray = null;
let settings = null;
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
  const defaults = { allowLan: true, launchAtLogin: false, port: 8899 };
  try {
    const saved = JSON.parse(fs.readFileSync(path.join(app.getPath('userData'), 'settings.json'), 'utf8'));
    return {
      allowLan: Boolean(saved.allowLan),
      launchAtLogin: Boolean(saved.launchAtLogin),
      port: Number.isInteger(saved.port) && saved.port > 0 && saved.port < 65536 ? saved.port : defaults.port,
    };
  } catch (_) {
    return defaults;
  }
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
      PYTHONUNBUFFERED: '1',
      PYTHONDONTWRITEBYTECODE: '1',
    },
    stdio: ['ignore', log, log],
  });
  backend.once('exit', () => { backend = null; });
  await waitForHealth(port);
  backendPort = port;
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
  mainWindow.on('close', (event) => {
    if (!isQuitting) {
      event.preventDefault();
      mainWindow.hide();
      if (process.platform === 'darwin') app.dock.hide();
    }
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
    ? path.join(process.resourcesPath, 'tray-logo.png')
    : path.resolve(__dirname, '..', 'static', 'logo.png');
  const icon = nativeImage.createFromPath(iconPath).resize({ width: 18, height: 18 });
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

app.whenReady().then(async () => {
  try {
    settings = loadSettings();
    if (settings.launchAtLogin) app.setLoginItemSettings({ openAtLogin: true });
    const port = await startBackend();
    createTray();
    configureDockMenu();
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

app.on('before-quit', () => {
  isQuitting = true;
  if (backend) backend.kill('SIGTERM');
  backendPort = null;
});
