'use strict';

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('agentSlack', {
  selectFolder: () => ipcRenderer.invoke('agent-slack:select-folder'),
  selectImage: () => ipcRenderer.invoke('agent-slack:select-image'),
  serverInfo: () => ipcRenderer.invoke('agent-slack:server-info'),
  tunnelInfo: () => ipcRenderer.invoke('agent-slack:tunnel-info'),
  selectTunnelClient: () => ipcRenderer.invoke('agent-slack:select-tunnel-client'),
  openTunnelDocs: () => ipcRenderer.invoke('agent-slack:open-tunnel-docs'),
  saveTunnel: (payload) => ipcRenderer.invoke('agent-slack:save-tunnel', payload),
  startTunnel: () => ipcRenderer.invoke('agent-slack:start-tunnel'),
  stopTunnel: () => ipcRenderer.invoke('agent-slack:stop-tunnel'),
  downloadTunnelClient: () => ipcRenderer.invoke('agent-slack:download-tunnel-client'),
  claudeMcpInfo: () => ipcRenderer.invoke('agent-slack:claude-mcp-info'),
  installClaudeMcp: () => ipcRenderer.invoke('agent-slack:install-claude-mcp'),
  openClaudeConfig: () => ipcRenderer.invoke('agent-slack:open-claude-config'),
  claudeTunnelInfo: () => ipcRenderer.invoke('agent-slack:claude-tunnel-info'),
  openClaudeTunnels: () => ipcRenderer.invoke('agent-slack:open-claude-tunnels'),
  saveClaudeTunnel: (payload) => ipcRenderer.invoke('agent-slack:save-claude-tunnel', payload),
  startClaudeTunnel: () => ipcRenderer.invoke('agent-slack:start-claude-tunnel'),
  stopClaudeTunnel: () => ipcRenderer.invoke('agent-slack:stop-claude-tunnel'),
  platform: process.platform,
});
