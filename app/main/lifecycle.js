'use strict';

// Local process-lifecycle evidence only. Never log audio, transcripts, settings,
// exception messages, or upload anything. Initialize after the instance lock.
const fs = require('fs');
const path = require('path');

function install(app, runtime = process, directory = app.getPath('userData')) {
  const journal = path.join(directory, 'lifecycle.jsonl');
  const session = path.join(directory, 'lifecycle-session.json');
  let quitting = false;
  function write(event, details = {}) {
    try {
      fs.mkdirSync(directory, { recursive: true });
      if (fs.existsSync(journal) && fs.statSync(journal).size > 512 * 1024) {
        fs.copyFileSync(journal, `${journal}.previous`);
        fs.writeFileSync(journal, '');
      }
      fs.appendFileSync(journal, JSON.stringify({ time: new Date().toISOString(), pid: runtime.pid, event, ...details }) + '\n');
    } catch (_) { /* diagnostic storage must not interrupt dictation */ }
  }
  function save(status) {
    try { fs.writeFileSync(session, JSON.stringify({ pid: runtime.pid, status })); } catch (_) {}
  }
  try {
    const previous = JSON.parse(fs.readFileSync(session, 'utf8'));
    if (previous.status !== 'exited') write('previous-session-no-clean-exit', { previousPid: previous.pid });
  } catch (_) {}
  write('started', { parentPid: runtime.ppid, version: app.getVersion() });
  save('running');
  app.on('before-quit', () => { quitting = true; write('before-quit'); save('quitting'); });
  app.on('will-quit', () => write('will-quit'));
  app.on('quit', (_event, exitCode) => write('quit', { exitCode }));
  app.on('child-process-gone', (_event, details) => write('child-process-gone', { type: details.type, reason: details.reason, exitCode: details.exitCode }));
  // Monitor preserves Node/Electron's exception behavior; it does not catch or
  // suppress an exception and does not keep a corrupted process running.
  runtime.on('uncaughtExceptionMonitor', (error, origin) => write('uncaught-exception', { name: error && error.name, code: error && error.code, origin }));
  runtime.on('exit', (exitCode) => { write('process-exit', { exitCode, quitting }); save('exited'); });
  return { write };
}

module.exports = { install };
