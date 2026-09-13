'use strict';

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('remotevoice', {
  onOverlayState(cb) {
    ipcRenderer.on('overlay:state', (e, payload) => cb(payload));
  },
  overlayCancel() {
    ipcRenderer.send('overlay:cancel');
  },
  settingsGet() {
    return ipcRenderer.invoke('settings:get');
  },
  settingsSave(config) {
    return ipcRenderer.invoke('settings:save', config);
  },
  settingsDefaults() {
    return ipcRenderer.invoke('settings:defaults');
  },
  listMics() {
    return ipcRenderer.invoke('mics:list');
  },
  historyList(query) {
    return ipcRenderer.invoke('history:list', query);
  },
  historyDelete(id) {
    return ipcRenderer.invoke('history:delete', id);
  },
  onEngineStatus(cb) {
    ipcRenderer.on('engine:status', (e, payload) => cb(payload));
  },
  engineStatusGet() {
    return ipcRenderer.invoke('engine:status:get');
  },
});
