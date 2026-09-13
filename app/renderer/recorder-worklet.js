'use strict';

// AudioWorklet: converts float32 input to int16 PCM, downsampling to the
// target rate when the AudioContext rate differs. Buffers CHUNK_SAMPLES
// (100 ms @ 16 kHz) before posting to the main script.

const TARGET_RATE = 16000;
const CHUNK_SAMPLES = 1600;

class RecProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / TARGET_RATE;
    this.pos = 0; // fractional read position in the input buffer
    this.out = new Int16Array(CHUNK_SAMPLES);
    this.outLen = 0;
    this.levelAcc = 0;
    this.levelCount = 0;
  }

  toInt16(s) {
    s = s < -1 ? -1 : s > 1 ? 1 : s;
    return s < 0 ? (s * 0x8000) | 0 : (s * 0x7fff) | 0;
  }

  flush(final) {
    if (this.outLen === 0) return;
    const chunk = this.out.slice(0, this.outLen);
    const rms = this.levelCount > 0 ? Math.sqrt(this.levelAcc / this.levelCount) : 0;
    this.levelAcc = 0;
    this.levelCount = 0;
    this.outLen = 0;
    this.port.postMessage({ pcm: chunk.buffer, rms }, [chunk.buffer]);
    void final;
  }

  process(inputs) {
    const input = inputs[0];
    const ch = input && input[0];
    if (!ch || ch.length === 0) return true;

    if (Math.abs(this.ratio - 1) < 0.001) {
      // context already at target rate: direct conversion
      for (let i = 0; i < ch.length; i++) {
        const s = ch[i];
        this.levelAcc += s * s;
        this.levelCount++;
        this.out[this.outLen++] = this.toInt16(s);
        if (this.outLen === CHUNK_SAMPLES) this.flush();
      }
    } else {
      // linear-interpolation downsample
      for (let i = 0; i < ch.length; i++) {
        const i0 = Math.floor(this.pos);
        if (i0 + 1 >= ch.length) break;
        const frac = this.pos - i0;
        const s = ch[i0] * (1 - frac) + ch[i0 + 1] * frac;
        this.levelAcc += s * s;
        this.levelCount++;
        this.out[this.outLen++] = this.toInt16(s);
        if (this.outLen === CHUNK_SAMPLES) this.flush();
        this.pos += this.ratio;
      }
      // consumed the whole buffer: keep position relative to buffer length
      this.pos = Math.max(0, this.pos - ch.length);
    }
    return true;
  }
}

registerProcessor('rec-processor', RecProcessor);
