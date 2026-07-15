'use strict';

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('agentSlack', {
  selectFolder: () => ipcRenderer.invoke('agent-slack:select-folder'),
  selectImage: () => ipcRenderer.invoke('agent-slack:select-image'),
  serverInfo: () => ipcRenderer.invoke('agent-slack:server-info'),
  platform: process.platform,
});
