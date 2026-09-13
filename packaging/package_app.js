'use strict';

// One-shot packaging script: builds the packaged app dir.
// Usage: node ..\packaging\package_app.js [outDir]
//   outDir defaults to dist\ (npm run package); the installer build passes
//   packaging\stage\_pkg so it can package while a dist\ build is running.
// engine/ and a seed config.json are bundled via extraResource.

const path = require('path');
const { packager } = require(path.join(__dirname, '..', 'app', 'node_modules', '@electron/packager'));

const appDir = path.join(__dirname, '..', 'app');
const outDir = process.argv[2] || path.join(appDir, '..', 'dist');

packager({
  dir: appDir,
  out: outDir,
  name: 'Remote Voice',
  platform: 'win32',
  arch: 'x64',
  executableName: 'Remote Voice',
  electronVersion: require(path.join(appDir, 'node_modules', 'electron', 'package.json')).version,
  appVersion: '0.1.0',
  overwrite: true,
  prune: true,
  extraResource: [
    path.join(appDir, '..', 'engine'),
    path.join(appDir, '..', 'config.json'),
  ],
}).then((paths) => {
  console.log('packaged:', paths.join(', '));
}).catch((e) => {
  console.error('packaging failed:', e);
  process.exit(1);
});
