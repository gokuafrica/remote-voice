'use strict';

const { EventEmitter } = require('events');
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');
const { app } = require('electron');

const PROBE_TIMEOUT_MS = 120000;  // bundled model may load before the first ping
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

  // Per-user writable HF cache. Program Files is read-only for the cache's
  // lock/atomic-rename traffic, so the engine must never download there —
  // HF_HOME/HUGGINGFACE_HUB_CACHE for the engine process point here.
  modelCacheDir() {
    const base = process.env.LOCALAPPDATA || app.getPath('appData');
    return path.join(base, 'Remote Voice', 'model-cache');
  }

  // One-time seed: copy the bundled hf_cache (from resources) into the
  // per-user model cache. Non-overwriting — an existing cache (previous
  // seed, or a newer download) always wins.
  seedModelCache() {
    const dst = this.modelCacheDir();
    const marker = path.join(dst, '.seeded');
    if (fs.existsSync(marker)) return dst;
    const src = app.isPackaged
      ? path.join(process.resourcesPath, 'hf_cache')
      : path.join(__dirname, '..', '..', 'packaging', 'stage', 'hf_cache');
    if (!fs.existsSync(src)) {
      log(`no bundled model cache at ${src} — engine will use/extend ${dst}`);
      return dst;
    }
    try {
      fs.mkdirSync(dst, { recursive: true });
      fs.cpSync(src, dst, { recursive: true, force: false, errorOnExist: false });
      fs.writeFileSync(marker, new Date().toISOString() + '\n');
      log(`seeded model cache ${dst} from ${src}`);
    } catch (e) {
      log(`model cache seed failed (${e.message}) — continuing with ${dst}`);
    }
    return dst;
  }

  spawnEnv() {
    const cache = this.seedModelCache();
    return {
      ...process.env,
      HF_HOME: cache,
      HUGGINGFACE_HUB_CACHE: path.join(cache, 'hub'),
    };
  }

  _setStatus(ready, model, error) {
    this.ready = ready;
    this.model = model !== undefined ? model : this.model;
    this.error = error !== undefined ? error : this.error;
    this.emit('status', { ready: this.ready, model: this.model, error: this.error });
  }

  _candidates(config) {
    // config.python_cmd override wins over everything.
    if (config && config.python_cmd) {
      const parsed = parsePythonCmd(String(config.python_cmd));
      if (parsed) return [parsed];
    }
    const candidates = [];
    if (app.isPackaged) {
      // Bundled runtime first: <resources>\python311\python.exe, installed by
      // the setup. Its site-packages carry every engine dependency, so the
      // app works on a clean machine without a system Python.
      candidates.push({
        cmd: path.join(process.resourcesPath, 'python311', 'python.exe'),
        prefix: [],
      });
    } else {
      // Dev: use the build staging tree if present (packaging\stage\python311),
      // so dev exercises the same runtime the installer ships.
      const stagePy = path.join(__dirname, '..', '..', 'packaging', 'stage', 'python311', 'python.exe');
      if (fs.existsSync(stagePy)) candidates.push({ cmd: stagePy, prefix: [] });
    }
    // System fallbacks (dev machines / installs without the bundled runtime).
    candidates.push(
      { cmd: 'python', prefix: [] },
      { cmd: 'py', prefix: ['-3.14'] },
      { cmd: 'py', prefix: ['-3.11'] },
    );
    return candidates;
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

  // Stop the current engine process and spawn a new one (model device changes
  // require a full reload). Async-kill of the old process is handled inside
  // shutdown(); the old proc's exit handler no longer owns status because it
  // no longer matches this.proc once the replacement spawns.
  restart(config) {
    if (this.started) this.shutdown();
    this.started = false;
    this.shuttingDown = false;
    this.ready = false;
    this.model = null;
    this.error = null;
    this._setStatus(false, null, null);
    this.start(config);
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
        env: this.spawnEnv(),
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
      // Only the live engine owns status/pending state. During restart() the
      // old proc exits after the replacement spawns (graceful-kill grace
      // period); rejecting the new engine's pending requests here would kill
      // its startup probes.
      if (this.proc !== proc) return;
      this._rejectAllPending('engine exited');
      this.proc = null;
      this._setStatus(false, null, `engine exited (code ${code})`);
    });

    // probe: first successful ping response locks the candidate
    this.request('ping', {}, PROBE_TIMEOUT_MS)
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
            const device = resp.device ? ` [${resp.device}]` : '';
            log(`model ready: ${resp.model}${device}`);
            this._setStatus(true, `${resp.model || 'unknown model'}${device}`, null);
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

  transcribePcm(pcm, sampleRate = 16000, timeoutMs = TRANSCRIBE_TIMEOUT_MS) {
    const bytes = Buffer.isBuffer(pcm) ? pcm : Buffer.from(pcm);
    return this.request('transcribe', {
      pcm_s16le_b64: bytes.toString('base64'),
      sample_rate: sampleRate,
    }, timeoutMs);
  }

  // Kept for file-based engine clients and smoke tests. The desktop recorder
  // uses transcribePcm so microphone audio never needs a temporary WAV file.
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
