'use strict';

// Keep auto-start per-user. Electron writes the Windows login item in the
// signed-in user's profile, so changing this setting never needs elevation.
function sync(appModule, enabled, runtime = process) {
  if (runtime.platform !== 'win32') {
    return { supported: false, enabled: false };
  }

  const options = { openAtLogin: Boolean(enabled) };
  if (appModule.isPackaged) {
    options.path = runtime.execPath;
  } else {
    // In development Electron itself must be launched with the app path.
    options.path = runtime.execPath;
    if (typeof appModule.getAppPath === 'function') {
      options.args = [appModule.getAppPath()];
    }
  }

  appModule.setLoginItemSettings(options);
  return { supported: true, enabled: options.openAtLogin, options };
}

module.exports = { sync };
