'use strict';

// Hidden recorder page: opens the mic, downsamples to 16 kHz mono int16 via an
// AudioWorklet, streams PCM chunks + RMS level to the main process.

const TARGET_RATE = 16000;

let ctx = null;
let stream = null;
let node = null;
let active = false;
let gen = 0; // start generation; bumping it aborts any in-flight start

const bridge = window.recorderBridge;

function log(msg) {
  console.log(`[rec-renderer] ${msg}`);
}

const BASE_AUDIO_CONSTRAINTS = {
  channelCount: { ideal: 1 },
  echoCancellation: false,
  noiseSuppression: false,
  autoGainControl: false,
};

async function enumerateMics() {
  try {
    let devices = await navigator.mediaDevices.enumerateDevices();
    let mics = devices.filter((d) => d.kind === 'audioinput');
    // labels are empty until mic permission has been granted once:
    // open the default device briefly, then re-enumerate
    if (mics.length && mics.every((d) => !d.label)) {
      try {
        const s = await navigator.mediaDevices.getUserMedia({ audio: true });
        s.getTracks().forEach((t) => t.stop());
        devices = await navigator.mediaDevices.enumerateDevices();
        mics = devices.filter((d) => d.kind === 'audioinput');
      } catch (e) {
        log(`label bootstrap getUserMedia failed: ${e.message}`);
      }
    }
    return mics.map((d, i) => ({
      id: d.deviceId,
      label: d.label || `Microphone ${i + 1}`,
    }));
  } catch (e) {
    log(`enumerateDevices failed: ${e.message}`);
    return [];
  }
}

function matchDevice(devices, wanted) {
  if (!wanted) return null; // system default
  const w = String(wanted).toLowerCase().trim();
  let best = null;
  for (const d of devices) {
    const l = (d.label || '').toLowerCase().trim();
    if (l === w) return d;
    if (!best && l && (l.includes(w) || w.includes(l))) best = d;
  }
  return best;
}

async function openMic(deviceName, myGen) {
  if (deviceName) {
    const devices = await enumerateMics();
    if (gen !== myGen) return null;
    const match = matchDevice(devices, deviceName);
    if (!match) throw new Error(`device "${deviceName}" not found`);
    try {
      return await navigator.mediaDevices.getUserMedia({
        audio: { ...BASE_AUDIO_CONSTRAINTS, deviceId: { exact: match.deviceId } },
      });
    } catch (e) {
      throw new Error(`device "${deviceName}" open failed: ${e.message}`);
    }
  }
  return navigator.mediaDevices.getUserMedia({ audio: BASE_AUDIO_CONSTRAINTS });
}

async function start({ deviceName, sampleRate }) {
  const myGen = ++gen;
  if (active) await stopInternal();

  let mediaStream;
  let usedFallback = false;
  try {
    mediaStream = await openMic(deviceName, myGen);
  } catch (e) {
    if (gen !== myGen) return;
    if (deviceName) {
      // fallback chain: named device failed -> system default
      log(`named device failed (${e.message}), retrying with system default`);
      usedFallback = true;
      try {
        mediaStream = await navigator.mediaDevices.getUserMedia({ audio: BASE_AUDIO_CONSTRAINTS });
      } catch (e2) {
        if (gen !== myGen) return;
        log(`default device also failed: ${e2.message}`);
        bridge.sendError({ message: e2.message });
        return;
      }
    } else {
      log(`default device failed: ${e.message}`);
      bridge.sendError({ message: e.message });
      return;
    }
  }
  if (gen !== myGen) {
    mediaStream.getTracks().forEach((t) => t.stop());
    return;
  }

  stream = mediaStream;
  try {
    ctx = new AudioContext({ sampleRate: TARGET_RATE });
  } catch (e) {
    log(`AudioContext at ${TARGET_RATE} failed (${e.message}), using default rate`);
    ctx = new AudioContext();
  }

  await ctx.audioWorklet.addModule('recorder-worklet.js');
  if (gen !== myGen) {
    await teardown();
    return;
  }
  const source = ctx.createMediaStreamSource(stream);
  node = new AudioWorkletNode(ctx, 'rec-processor', {
    numberOfInputs: 1,
    numberOfOutputs: 1,
    outputChannelCount: [1],
  });
  node.port.onmessage = (ev) => {
    const { pcm, rms } = ev.data;
    if (active && pcm) bridge.sendAudio(pcm);
    if (typeof rms === 'number') bridge.sendLevel(rms);
  };
  source.connect(node);
  node.connect(ctx.destination); // outputs silence; keeps the worklet pulled

  active = true;
  bridge.sendStarted({
    device: usedFallback ? 'System Default (fallback)' : (deviceName || 'System Default'),
    sampleRate: ctx.sampleRate,
  });
}

async function teardown() {
  try {
    if (node) {
      node.port.onmessage = null;
      node.disconnect();
    }
    if (stream) stream.getTracks().forEach((t) => t.stop());
    if (ctx) await ctx.close();
  } catch (e) {
    log(`teardown error: ${e.message}`);
  }
  node = null;
  stream = null;
  ctx = null;
}

async function stopInternal() {
  active = false;
  gen++; // abort any in-flight start
  await teardown();
  bridge.sendStopped();
}

bridge.onRecStart(async (payload) => {
  try {
    await start(payload || {});
  } catch (e) {
    log(`start failed: ${e.message}`);
    bridge.sendError({ message: e.message });
  }
});

bridge.onRecStop(async () => {
  try {
    await stopInternal();
  } catch (e) {
    log(`stop failed: ${e.message}`);
    bridge.sendStopped();
  }
});

bridge.onRecEnum(async () => {
  bridge.sendDevices(await enumerateMics());
});

log('recorder page loaded');
