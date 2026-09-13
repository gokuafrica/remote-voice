'use strict';

const { contextBridge, ipcRenderer } = require('electron');

// internal preload for the hidden recorder page (not the public remotevoice API)
contextBridge.exposeInMainWorld('recorderBridge', {
  onRecStart(cb) {
    ipcRenderer.on('rec:start', (e, payload) => cb(payload));
  },
  onRecStop(cb) {
    ipcRenderer.on('rec:stop', (e, payload) => cb(payload));
  },
  onRecEnum(cb) {
    ipcRenderer.on('rec:enum', (e, payload) => cb(payload));
  },
  sendAudio(arrayBuffer) {
    ipcRenderer.send('rec:audio', arrayBuffer);
  },
  sendLevel(level) {
    ipcRenderer.send('rec:level', level);
  },
  sendStarted(payload) {
    ipcRenderer.send('rec:started', payload);
  },
  sendError(payload) {
    ipcRenderer.send('rec:error', payload);
  },
  sendStopped() {
    ipcRenderer.send('rec:stopped');
  },
  sendDevices(devices) {
    ipcRenderer.send('rec:devices', devices);
  },
});
