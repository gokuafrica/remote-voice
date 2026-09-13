'use strict';

const { EventEmitter } = require('events');
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');
const { app } = require('electron');

const PROBE_TIMEOUT_MS = 20000;   // per python candidate: wait for first sign of life
const READY_TIMEOUT_MS = 120000;  // model load budget
const PING_TIMEOUT_MS = 15000;
const TRANSCRIBE_TIMEOUT_MS = 30000;
const SET_FIXES_TIMEOUT_MS = 10000;
const SHUTDOWN_GRACE_MS = 3000;

function log(msg) {
  console.log(`[engine] ${msg}`);
}

function parsePythonCmd(cmd) {
  // supports: "python", "py -3.11", "C:\\path\\with space\\python.exe -3.11"
  const parts = [];
  const re = /"([^"]*)"|(\S+)/g;
  let m;
  while ((m = re.exec(cmd)) !== null) parts.push(m[1] !== undefined ? m[1] : m[2]);
  if (parts.length === 0) return null;
  return { cmd: parts[0], prefix: parts.slice(1) };
}

class Engine extends EventEmitter {
  constructor() {
    super();
    this.proc = null;
    this.pending = new Map();
    this.nextId = 1;
    this.ready = false;
    this.model = null;
    this.error = null;
    this.buffer = '';
    this.started = false;
    this.shuttingDown = false;
  }

  scriptPath() {
    if (app.isPackaged) {
      // packaged: engine/ is bundled as an extraResource
      return path.join(process.resourcesPath, 'engine', 'engine.py');
    }
    return path.join(__dirname, '..', '..', 'engine', 'engine.py');
  }

  workDir() {
    if (app.isPackaged) return process.resourcesPath;
    return path.join(__dirname, '..', '..'); // worktree root
  }

  _setStatus(ready, model, error) {
    this.ready = ready;
    this.model = model !== undefined ? model : this.model;
    this.error = error !== undefined ? error : this.error;
    this.emit('status', { ready: this.ready, model: this.model, error: this.error });
  }

  _candidates(config) {
    if (config && config.python_cmd) {
      const parsed = parsePythonCmd(String(config.python_cmd));
      if (parsed) return [parsed];
    }
    return [
      { cmd: 'python', prefix: [] },
      { cmd: 'py', prefix: ['-3.14'] },
      { cmd: 'py', prefix: ['-3.11'] },
    ];
  }

  start(config) {
    if (this.started) return;
    this.started = true;
    const script = this.scriptPath();
    if (!fs.existsSync(script)) {
      log('engine/engine.py not found — running shell-only');
      this._setStatus(false, null, 'engine missing (shell-only)');
      return;
    }
    const cfg = config && typeof config.get === 'function' ? config.get() : (config || {});
    this._startWithCandidates(this._candidates(cfg), script, config);
  }

  _startWithCandidates(candidates, script, config) {
    if (candidates.length === 0) {
      log('no working python command found');
      this._setStatus(false, null, 'no python with onnx_asr found (set python_cmd in config.json)');
      return;
    }
    const cand = candidates[0];
    const args = [...cand.prefix, script, '--config', config.configPath()];
    log(`spawning: ${cand.cmd} ${args.join(' ')}`);
    let proc;
    try {
      proc = spawn(cand.cmd, args, {
        stdio: ['pipe', 'pipe', 'pipe'],
        windowsHide: true,
        cwd: this.workDir(),
      });
    } catch (e) {
      log(`spawn failed for ${cand.cmd}: ${e.message}`);
      this._startWithCandidates(candidates.slice(1), script, config);
      return;
    }

    this.proc = proc;
    this.buffer = '';

    const failOver = (reason) => {
      if (this.proc !== proc) return;
      log(`candidate "${cand.cmd} ${cand.prefix.join(' ')}" failed: ${reason}`);
      this._teardownProc(proc);
      this._startWithCandidates(candidates.slice(1), script, config);
    };

    const probeTimer = setTimeout(() => failOver('probe timeout (no ping response)'), PROBE_TIMEOUT_MS);

    proc.on('error', (e) => {
      clearTimeout(probeTimer);
      failOver(e.message);
    });

    proc.stdout.on('data', (chunk) => {
      this.buffer += chunk.toString('utf8');
      let idx;
      while ((idx = this.buffer.indexOf('\n')) !== -1) {
        const line = this.buffer.slice(0, idx).trim();
        this.buffer = this.buffer.slice(idx + 1);
        if (line) this._handleLine(line, proc);
      }
    });

    proc.stderr.on('data', (chunk) => {
      for (const line of chunk.toString('utf8').split(/\r?\n/)) {
        if (line.trim()) log(`stderr: ${line.trim()}`);
      }
    });

    proc.on('exit', (code) => {
      clearTimeout(probeTimer);
      if (this.shuttingDown) return;
      this._rejectAllPending('engine exited');
      if (this.proc === proc) {
        this.proc = null;
        this._setStatus(false, null, `engine exited (code ${code})`);
      }
    });

    // probe: first successful ping response locks the candidate
    this.request('ping', {}, PING_TIMEOUT_MS)
      .then(() => {
        clearTimeout(probeTimer);
        if (this.proc !== proc) return;
        log(`engine alive via "${cand.cmd} ${cand.prefix.join(' ')}"`);
        this._setStatus(false, null, null);
        this._waitReady(config);
      })
      .catch(() => {
        clearTimeout(probeTimer);
        failOver('no ping response');
      });
  }

  _waitReady(config) {
    const deadline = Date.now() + READY_TIMEOUT_MS;
    const poll = () => {
      if (!this.proc || this.shuttingDown) return;
      this.request('ping', {}, PING_TIMEOUT_MS)
        .then((resp) => {
          if (!this.proc || this.shuttingDown) return;
          if (resp.ready) {
            log(`model ready: ${resp.model}`);
            this._setStatus(true, resp.model || null, null);
            if (config.get().pronunciation_fixes) {
              this.setFixes(config.get().pronunciation_fixes).catch((e) =>
                log(`initial set_fixes failed: ${e.message}`));
            }
          } else if (Date.now() < deadline) {
            setTimeout(poll, 1000);
          } else {
            this._setStatus(false, null, 'model load timed out');
          }
        })
        .catch((e) => {
          if (this.proc && !this.shuttingDown && Date.now() < deadline) {
            setTimeout(poll, 1000);
          } else {
            this._setStatus(false, null, `ping failed: ${e.message}`);
          }
        });
    };
    poll();
  }

  _handleLine(line, proc) {
    let msg;
    try {
      msg = JSON.parse(line);
    } catch (e) {
      log(`unparseable stdout line: ${line.slice(0, 200)}`);
      return;
    }
    if (typeof msg.id !== 'number') {
      log(`notification: ${line.slice(0, 200)}`);
      return;
    }
    const entry = this.pending.get(msg.id);
    if (!entry) return;
    this.pending.delete(msg.id);
    clearTimeout(entry.timer);
    if (msg.ok) entry.resolve(msg);
    else entry.reject(new Error(msg.error || 'engine error'));
  }

  request(op, params = {}, timeoutMs = 10000) {
    return new Promise((resolve, reject) => {
      if (!this.proc) {
        reject(new Error('engine not running'));
        return;
      }
      const id = this.nextId++;
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`${op} timed out after ${timeoutMs}ms`));
      }, timeoutMs);
      this.pending.set(id, { resolve, reject, timer });
      try {
        this.proc.stdin.write(JSON.stringify({ id, op, ...params }) + '\n');
      } catch (e) {
        clearTimeout(timer);
        this.pending.delete(id);
        reject(e);
      }
    });
  }

  ping(timeoutMs = PING_TIMEOUT_MS) {
    return this.request('ping', {}, timeoutMs);
  }

  transcribe(wavPath, timeoutMs = TRANSCRIBE_TIMEOUT_MS) {
    return this.request('transcribe', { wav_path: wavPath }, timeoutMs);
  }

  setFixes(fixes, timeoutMs = SET_FIXES_TIMEOUT_MS) {
    return this.request('set_fixes', { fixes: fixes || {} }, timeoutMs);
  }

  _rejectAllPending(reason) {
    for (const [id, entry] of this.pending) {
      clearTimeout(entry.timer);
      entry.reject(new Error(reason));
    }
    this.pending.clear();
  }

  _teardownProc(proc) {
    if (!proc) return;
    try { proc.removeAllListeners(); } catch (_) { /* ignore */ }
    try { proc.stdout.removeAllListeners(); } catch (_) { /* ignore */ }
    try { proc.stderr.removeAllListeners(); } catch (_) { /* ignore */ }
    try { proc.stdin.end(); } catch (_) { /* ignore */ }
    try { proc.kill(); } catch (_) { /* ignore */ }
  }

  shutdown() {
    this.shuttingDown = true;
    const proc = this.proc;
    this.proc = null;
    this._rejectAllPending('shutting down');
    if (!proc) return;
    try {
      proc.stdin.write(JSON.stringify({ id: this.nextId++, op: 'shutdown' }) + '\n');
    } catch (_) { /* ignore */ }
    setTimeout(() => {
      try { proc.kill(); } catch (_) { /* ignore */ }
    }, SHUTDOWN_GRACE_MS);
  }
}

module.exports = new Engine();
