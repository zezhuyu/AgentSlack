'use strict';

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('agentSlack', {
  selectFolder: () => ipcRenderer.invoke('agent-slack:select-folder'),
  selectImage: () => ipcRenderer.invoke('agent-slack:select-image'),
  platform: process.platform,
});
