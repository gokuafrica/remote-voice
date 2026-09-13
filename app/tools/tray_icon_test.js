'use strict';

// Renders the tray icons to PNG on disk, prints an ASCII preview of the glyph
// shape, and asserts geometry/coverage/color invariants. Pure Node, no deps:
//   node tools/tray_icon_test.js [outDir]

const fs = require('fs');
const path = require('path');
const zlib = require('zlib');
const { MASTER, COLORS, G, renderIcon, downsample } = require('../main/trayIconDraw');

const OUT_DIR = process.argv[2] || path.join(process.env.TEMP || '.', 'opencode', 'tray-icons');
const SIZES = [16, 20, 32, 48];

// --- minimal PNG encoder (RGBA, filter 0) ---
const CRC_TABLE = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    t[n] = c >>> 0;
  }
  return t;
})();
function crc32(buf) {
  let c = 0xffffffff;
  for (let i = 0; i < buf.length; i++) c = CRC_TABLE[(c ^ buf[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}
function chunk(type, data) {
  const len = Buffer.alloc(4); len.writeUInt32BE(data.length);
  const body = Buffer.concat([Buffer.from(type, 'ascii'), data]);
  const crc = Buffer.alloc(4); crc.writeUInt32BE(crc32(body));
  return Buffer.concat([len, body, crc]);
}
function writePng(file, rgba, w, h) {
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(w, 0); ihdr.writeUInt32BE(h, 4);
  ihdr[8] = 8; ihdr[9] = 6; ihdr[10] = 0; ihdr[11] = 0; ihdr[12] = 0;
  const raw = Buffer.alloc(h * (1 + w * 4));
  for (let y = 0; y < h; y++) {
    raw[y * (1 + w * 4)] = 0;
    Buffer.from(rgba.buffer, rgba.byteOffset + y * w * 4, w * 4)
      .copy(raw, y * (1 + w * 4) + 1);
  }
  const png = Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', ihdr),
    chunk('IDAT', zlib.deflateSync(raw, { level: 9 })),
    chunk('IEND', Buffer.alloc(0)),
  ]);
  fs.writeFileSync(file, png);
}

// --- helpers ---
function px(img, size, x, y) {
  const i = (y * size + x) * 4;
  return [img[i], img[i + 1], img[i + 2], img[i + 3]];
}
function isWhiteish(p, t = 200) {
  return p[0] >= t && p[1] >= t && p[2] >= t && p[3] >= 200;
}
// Estimate glyph coverage t of a pixel assumed to be mix(stateColor, white, t).
// Only channels with a meaningful span are used (a saturated channel like the
// R of #ff4444 carries no information).
function glyphT(p, color) {
  let sum = 0, n = 0;
  for (let k = 0; k < 3; k++) {
    const span = 255 - color[k];
    if (span >= 64) { sum += (p[k] - color[k]) / span; n++; }
  }
  return n ? sum / n : 1;
}
function isGlyph(p, color, minT = 0.6) {
  return p[3] >= 200 && glyphT(p, color) >= minT;
}
function coverageIn(img, size, x0, y0, x1, y1, color) {
  let white = 0, total = 0;
  for (let y = y0; y < y1; y++) {
    for (let x = x0; x < x1; x++) {
      total++;
      if (isGlyph(px(img, size, x, y), color)) white++;
    }
  }
  return white / total;
}
function ascii(img, size) {
  const lines = [];
  for (let y = 0; y < size; y++) {
    let line = '';
    for (let x = 0; x < size; x++) {
      const p = px(img, size, x, y);
      line += p[3] === 0 ? ' ' : isWhiteish(p, 190) ? '#' : '.';
    }
    lines.push(line);
  }
  return lines;
}

let failures = 0;
function check(name, cond, detail) {
  if (cond) console.log(`  ok   ${name}`);
  else { failures++; console.error(`  FAIL ${name} ${detail || ''}`); }
}

fs.mkdirSync(OUT_DIR, { recursive: true });
const GLYPH_T = {};

for (const [state, hexColor] of Object.entries(COLORS)) {
  console.log(`\n== ${state} (#${hexColor.slice(0, 3).map((v) => v.toString(16).padStart(2, '0')).join('')}) ==`);
  const master = renderIcon(state);
  const bySize = {};
  for (const s of SIZES) {
    bySize[s] = downsample(master.data, MASTER, s);
    writePng(path.join(OUT_DIR, `${state}-${s}.png`), bySize[s], s, s);
  }
  writePng(path.join(OUT_DIR, `${state}-256.png`), master.data, MASTER, MASTER);

  // 1. circle background color is exactly the state hex (sample point is
  //    inside the circle but clear of the glyph)
  const bgX = 128 + 90, bgY = 128;
  const c = px(master.data, MASTER, bgX, bgY);
  check('circle color matches state hex',
    c[0] === hexColor[0] && c[1] === hexColor[1] && c[2] === hexColor[2] && c[3] === 255,
    `got ${c}`);

  // 2. corners are fully transparent
  const corner = px(master.data, MASTER, 4, 4);
  check('outside circle transparent', corner[3] === 0, `got ${corner}`);

  // 3. margin: points just inside the circle edge at N/E/S/W are pure state
  //    color (no glyph pixels near the rim => nothing clipped)
  const r = 118 - 4;
  const rim = [
    px(master.data, MASTER, 128, 128 - r),
    px(master.data, MASTER, 128 + r, 128),
    px(master.data, MASTER, 128, 128 + r),
    px(master.data, MASTER, 128 - r, 128),
  ];
  check('rim margin untouched by glyph',
    rim.every((p) => p[0] === hexColor[0] && p[1] === hexColor[1] && p[2] === hexColor[2] && p[3] === 255),
    `got ${rim.map((p) => p.join(',')).join(' | ')}`);

  // 4. glyph regions populated at 256
  const capCov = coverageIn(master.data, MASTER, 104, 52, 152, 124, hexColor);
  const cradleCov = coverageIn(master.data, MASTER, 70, 108, 186, 168, hexColor);
  const footCov = coverageIn(master.data, MASTER, 100, 180, 156, 194, hexColor);
  check(`capsule coverage >= 0.35 (${capCov.toFixed(2)})`, capCov >= 0.35);
  check(`cradle coverage >= 0.05 (${cradleCov.toFixed(2)})`, cradleCov >= 0.05);
  check(`foot coverage >= 0.30 (${footCov.toFixed(2)})`, footCov >= 0.30);

  // 5. grille slots actually cut into the head (at 256)
  const slot = px(master.data, MASTER, 128, 73);
  const head = px(master.data, MASTER, 128, 60);
  check('grille slot cut (slot px != head white)', !(slot[0] === 255 && slot[1] === 255 && slot[2] === 255)
    && head[0] === 255 && head[1] === 255 && head[2] === 255, `slot ${slot} head ${head}`);

  // 6. idle halo darkens pixels just outside the capsule edge
  if (state === 'idle') {
    const halo = px(master.data, MASTER, 96, 90);
    check('idle halo present (darker than #888)', halo[0] < 0x88 && halo[1] < 0x88, `got ${halo}`);
  }

  // 7. every size: glyph present, margins respected
  for (const s of SIZES) {
    const img = bySize[s];
    const white = img.reduce((n, v, idx) => (idx % 4 === 0 && isGlyph([
      img[idx], img[idx + 1], img[idx + 2], img[idx + 3]], hexColor) ? n + 1 : n), 0);
    check(`${s}px has white glyph pixels (${white})`, white >= 4);
    const bg = px(img, s, Math.round(s / 2) + Math.round((90 * s) / 256), Math.round(s / 2));
    check(`${s}px circle color matches state hex`,
      Math.abs(bg[0] - hexColor[0]) <= 8 && Math.abs(bg[1] - hexColor[1]) <= 8 && Math.abs(bg[2] - hexColor[2]) <= 8 && bg[3] === 255,
      `got ${bg}`);
    // 1px-inside-edge points must not be glyph
    const m = [
      px(img, s, Math.round(s / 2), 1), px(img, s, s - 2, Math.round(s / 2)),
      px(img, s, Math.round(s / 2), s - 2), px(img, s, 1, Math.round(s / 2)),
    ];
    check(`${s}px rim not glyph`, m.every((p) => !isGlyph(p, hexColor, 0.5)), `got ${m.map((p) => p.join(',')).join(' | ')}`);
  }

  // 8. soft glyph-coverage profile identical across states (only color differs)
  GLYPH_T[state] = Array.from({ length: 1024 }, (_, i) => {
    const a = bySize[32][i * 4 + 3];
    return a === 0 ? null // fully transparent corner: no color info
      : glyphT([bySize[32][i * 4], bySize[32][i * 4 + 1], bySize[32][i * 4 + 2], a], hexColor);
  });
}

let maxTdiff = 0, maxTdiffIdle = 0;
for (const state of Object.keys(GLYPH_T)) {
  for (let i = 0; i < 1024; i++) {
    if (GLYPH_T[state][i] === null) continue;
    const d = Math.abs(GLYPH_T[state][i] - GLYPH_T.recording[i]);
    if (state === 'idle') maxTdiffIdle = Math.max(maxTdiffIdle, d); // halo shifts edge colors
    else maxTdiff = Math.max(maxTdiff, d);
  }
}
check(`glyph coverage identical across recording/processing/success (max delta ${maxTdiff.toFixed(3)})`,
  maxTdiff <= 0.03);
check(`idle glyph coverage matches within halo tolerance (max delta ${maxTdiffIdle.toFixed(3)})`,
  maxTdiffIdle <= 0.25);

console.log('\n--- ASCII preview (idle @48px, #=white glyph, .=circle) ---');
const prev = downsample(renderIcon('idle').data, MASTER, 48);
console.log(ascii(prev, 48).join('\n'));

console.log(failures === 0
  ? `\nALL CHECKS PASSED — PNGs written to ${OUT_DIR}`
  : `\n${failures} CHECK(S) FAILED`);
process.exit(failures === 0 ? 0 : 1);
