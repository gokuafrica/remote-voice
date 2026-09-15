'use strict';

/*
 * ui_driver.js — drive the REAL settings window of a running "Remote Voice"
 * app over the Chrome DevTools Protocol (no jsdom, no mocks).
 *
 * Start the app with remote debugging + auto-opened settings window:
 *
 *   npm start -- --remote-debugging-port=9222 --open-settings
 *
 * Then from app/ (or anywhere):
 *
 *   node tools/ui_driver.js read
 *     -> prints the settings window's current UI state as JSON
 *
 *   node tools/ui_driver.js set --mode push_to_talk --mic "System Default"
 *     -> clicks the mode radio + changes the mic dropdown in the REAL
 *        renderer (triggering the same autosave handlers a user would),
 *        waits for the save round-trip, then prints the resulting UI state.
 *        Omit --mode/--mic to change only one.
 *
 *   node tools/ui_driver.js wait [--timeout 30]
 *     -> waits until the settings window target exists on the debug port.
 *
 * Exit code 0 = commands succeeded. Errors go to stderr.
 */

const DEBUG_PORT_DEFAULT = 9222;

function parseArgs(argv) {
  const args = { _: [] };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--port') args.port = Number(argv[++i]);
    else if (a === '--timeout') args.timeout = Number(argv[++i]);
    else if (a === '--mode') args.mode = argv[++i];
    else if (a === '--mic') args.mic = argv[++i];
    else if (a === '--auto-start') args.autoStart = argv[++i];
    else args._.push(a);
  }
  return args;
}

async function httpJson(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url} -> HTTP ${res.status}`);
  return res.json();
}

async function findSettingsTarget(port, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    let targets = [];
    try {
      targets = await httpJson(`http://127.0.0.1:${port}/json/list`);
    } catch (_) { /* app not up yet */ }
    const t = targets.find((x) => x.type === 'page' && /settings\.html/i.test(x.url));
    if (t) return t;
    if (Date.now() > deadline) {
      throw new Error(`no settings.html target on port ${port}; start the app with: npm start -- --remote-debugging-port=${port} --open-settings`);
    }
    await new Promise((r) => setTimeout(r, 500));
  }
}

class Cdp {
  constructor(wsUrl) {
    this.wsUrl = wsUrl;
    this.id = 0;
    this.pending = new Map();
  }

  async connect() {
    this.ws = new WebSocket(this.wsUrl);
    await new Promise((resolve, reject) => {
      this.ws.onopen = resolve;
      this.ws.onerror = (e) => reject(new Error('websocket connect failed'));
    });
    this.ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id && this.pending.has(msg.id)) {
        const { resolve, reject } = this.pending.get(msg.id);
        this.pending.delete(msg.id);
        if (msg.error) reject(new Error(msg.error.message));
        else resolve(msg.result);
      }
    };
  }

  send(method, params = {}) {
    const id = ++this.id;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }

  async eval(expression) {
    const res = await this.send('Runtime.evaluate', {
      expression,
      returnByValue: true,
      awaitPromise: true,
    });
    if (res.exceptionDetails) {
      const d = res.exceptionDetails;
      throw new Error(d.exception && d.exception.description ? d.exception.description : d.text);
    }
    return res.result && res.result.value;
  }

  close() {
    try { this.ws.close(); } catch (_) { /* ignore */ }
  }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function readState(cdp) {
  return cdp.eval(`(() => {
    const checked = document.querySelector('input[name="mode"]:checked');
    const sel = document.getElementById('mic-select');
    const hint = document.getElementById('mic-hint');
    return {
      mode: checked ? checked.value : null,
      auto_start: !!(document.getElementById('auto-start') && document.getElementById('auto-start').checked),
      mic_value: sel ? sel.value : null,
      mic_text: sel && sel.selectedOptions && sel.selectedOptions[0] ? sel.selectedOptions[0].textContent : null,
      mic_options: sel ? [...sel.options].map((o) => o.value) : null,
      mic_hint: hint ? hint.textContent : null,
      history_hint: document.getElementById('history-hint') ? document.getElementById('history-hint').textContent : null,
      hotkey: document.getElementById('hotkey-display') ? document.getElementById('hotkey-display').textContent : null,
      toast: (() => {
        const t = document.getElementById('toast');
        return t ? { text: t.textContent, shown: t.classList.contains('show') } : null;
      })(),
    };
  })()`);
}

async function cmdWait(args) {
  const timeoutMs = args.timeout ? args.timeout * 1000 : 30000;
  await findSettingsTarget(args.port, timeoutMs);
  console.log(JSON.stringify({ ok: true }));
}

async function cmdRead(args) {
  const target = await findSettingsTarget(args.port, args.timeout ? args.timeout * 1000 : 30000);
  const cdp = new Cdp(target.webSocketDebuggerUrl);
  await cdp.connect();
  try {
    console.log(JSON.stringify(await readState(cdp), null, 2));
  } finally {
    cdp.close();
  }
}

async function cmdSet(args) {
  if (!args.mode && !args.mic && !args.autoStart) {
    throw new Error('nothing to set: pass --mode, --mic, or --auto-start <on|off>');
  }
  const target = await findSettingsTarget(args.port, args.timeout ? args.timeout * 1000 : 30000);
  const cdp = new Cdp(target.webSocketDebuggerUrl);
  await cdp.connect();
  const toasts = [];
  const captureToast = async () => {
    // the toast shows for ~1.8s after saveAll() resolves — grab it within that window
    const t = await cdp.eval(`(() => {
      const t = document.getElementById('toast');
      return t ? { text: t.textContent, shown: t.classList.contains('show') } : null;
    })()`);
    if (t && t.shown) toasts.push(t.text);
  };
  try {
    if (args.mode) {
      // click() fires the change event -> settings.js saveAll() (instant path)
      await cdp.eval(`(() => {
        const r = document.querySelector('input[name="mode"][value=${JSON.stringify(args.mode)}]');
        if (!r) throw new Error('no mode radio with value ' + ${JSON.stringify(args.mode)});
        r.click();
        return r.checked;
      })()`);
      // saveAll awaits the IPC round-trip (settings:save -> applyChange);
      // give it time so the main-process log/persist lands before we exit
      await sleep(600);
      await captureToast();
      await sleep(400);
    }
    if (args.mic) {
      const wantsDefault = args.mic === 'default';
      await cdp.eval(`(() => {
        const sel = document.getElementById('mic-select');
        if (!sel) throw new Error('mic-select not found');
        const want = ${JSON.stringify(args.mic)};
        const opt = [...sel.options].find((o) => (want === 'default' ? o.value === 'default' : o.value === want));
        if (!opt) throw new Error('no mic option for ' + want + '; available: ' + [...sel.options].map((o) => o.value).join(' | '));
        sel.value = opt.value;
        sel.dispatchEvent(new Event('change', { bubbles: true }));
        return opt.textContent;
      })()`);
      await sleep(600);
      await captureToast();
      await sleep(400);
    }
    if (args.autoStart) {
      const enabled = !['off', 'false', '0', 'no'].includes(String(args.autoStart).toLowerCase());
      await cdp.eval(`(() => {
        const input = document.getElementById('auto-start');
        if (!input) throw new Error('auto-start control not found');
        input.checked = ${enabled};
        input.dispatchEvent(new Event('change', { bubbles: true }));
        return input.checked;
      })()`);
      await sleep(600);
      await captureToast();
      await sleep(400);
    }
    const state = await readState(cdp);
    if (toasts.length) state.toasts_seen = toasts;
    console.log(JSON.stringify(state, null, 2));
  } finally {
    cdp.close();
  }
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  args.port = args.port || DEBUG_PORT_DEFAULT;
  const cmd = args._[0] || 'read';
  if (cmd === 'wait') return cmdWait(args);
  if (cmd === 'read') return cmdRead(args);
  if (cmd === 'set') return cmdSet(args);
  throw new Error(`unknown command '${cmd}' (use: wait | read | set)`);
}

main().then(
  () => process.exit(0),
  (err) => {
    console.error(`[ui_driver] ${err.message}`);
    process.exit(1);
  }
);
