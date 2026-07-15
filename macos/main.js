'use strict';

const { app, BrowserWindow, dialog, ipcMain, shell } = require('electron');
const { spawn, execFileSync } = require('child_process');
const fs = require('fs');
const http = require('http');
const net = require('net');
const path = require('path');

let backend = null;
let mainWindow = null;
let backendPort = null;

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

function allocatePort() {
  return new Promise((resolve, reject) => {
    const probe = net.createServer();
    probe.unref();
    probe.on('error', reject);
    probe.listen(0, '127.0.0.1', () => {
      const address = probe.address();
      const port = address && typeof address === 'object' ? address.port : 0;
      probe.close(() => resolve(port));
    });
  });
}

function waitForHealth(port, attempts = 80) {
  return new Promise((resolve, reject) => {
    let remaining = attempts;
    const poll = () => {
      const request = http.get(`http://127.0.0.1:${port}/api/health`, { timeout: 1000 }, (response) => {
        response.resume();
        if (response.statusCode === 200) return resolve();
        retry();
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
  const port = await allocatePort();
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
    '--host', '127.0.0.1',
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
  return port;
}

function createWindow(port) {
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
  mainWindow.on('closed', () => { mainWindow = null; });
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

app.whenReady().then(async () => {
  try {
    const port = await startBackend();
    createWindow(port);
  } catch (error) {
    dialog.showErrorBox('Agent Slack could not start', error.message);
    app.quit();
  }
});

app.on('activate', () => {
  if (mainWindow) mainWindow.show();
  else if (backendPort) createWindow(backendPort);
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

app.on('before-quit', () => {
  if (backend) backend.kill('SIGTERM');
  backendPort = null;
});
