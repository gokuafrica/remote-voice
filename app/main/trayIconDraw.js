'use strict';

// Procedurally drawn tray icon: state-colored circle + white studio-mic glyph
// (capsule head with grille slots, U/Y cradle, stem, base foot).
//
// Rendered once at 256x256 with signed-distance-field antialiasing, then
// area-averaged down to the size actually needed. No Electron imports here so
// the drawing can be unit-tested / rendered to PNG with plain Node.

const MASTER = 256;
const AA = 1.5; // edge smoothing width in master units

const COLORS = {
  idle: [0x88, 0x88, 0x88, 0xff],
  recording: [0xff, 0x44, 0x44, 0xff],
  processing: [0x44, 0x88, 0xff, 0xff],
  success: [0x44, 0xcc, 0x44, 0xff],
};

const CX = 128;
const CIRCLE_R = 118; // background circle

// Glyph bounding box: y 46..194, x 74..182 => height 148 (~61% of the 236px
// circle diameter), always clear of the circle edge (min margin ~38 units).
const G = {
  capsule: { x: CX, ySeg1: 75, ySeg2: 99, r: 29 }, // stadium head: y 46..128, x 99..157
  slots: [ // grille hints (cut out of the head)
    { x1: 112, x2: 144, y1: 70, y2: 77 },
    { x1: 112, x2: 144, y1: 87, y2: 94 },
  ],
  bracket: { cx: CX, cy: 116, r: 46, halfStroke: 8, armTop: 87 }, // Y/U cradle
  stem: { x: CX, y1: 128, y2: 174, halfW: 8 },
  foot: { cx: CX, cy: 187, halfW: 28, halfH: 7, r: 7 },
};

const HALO = 12; // dark outline spread for the light-gray idle state
const HALO_ALPHA = 0.38;

function clamp(v, lo, hi) {
  return v < lo ? lo : v > hi ? hi : v;
}

function cov(d) {
  return clamp((AA - d) / (2 * AA), 0, 1);
}

function sdCircle(px, py, cx, cy, r) {
  return Math.hypot(px - cx, py - cy) - r;
}

function sdSegment(px, py, ax, ay, bx, by, r) {
  const abx = bx - ax, aby = by - ay;
  const len2 = abx * abx + aby * aby;
  const t = clamp(((px - ax) * abx + (py - ay) * aby) / len2, 0, 1);
  return Math.hypot(px - (ax + abx * t), py - (ay + aby * t)) - r;
}

function sdRoundRect(px, py, cx, cy, halfW, halfH, r) {
  const qx = Math.abs(px - cx) - (halfW - r);
  const qy = Math.abs(py - cy) - (halfH - r);
  const out = Math.hypot(Math.max(qx, 0), Math.max(qy, 0));
  return Math.min(Math.max(qx, qy), 0) + out - r;
}

function glyphDistance(px, py) {
  const c = G.capsule;
  let d = sdSegment(px, py, c.x, c.ySeg1, c.x, c.ySeg2, c.r); // capsule head
  const b = G.bracket;
  const annulus = Math.abs(Math.hypot(px - b.cx, py - b.cy) - b.r) - b.halfStroke;
  d = Math.min(d, Math.max(annulus, b.cy - py)); // lower half arc
  d = Math.min(d, sdSegment(px, py, b.cx - b.r, b.cy, b.cx - b.r, b.armTop, b.halfStroke));
  d = Math.min(d, sdSegment(px, py, b.cx + b.r, b.cy, b.cx + b.r, b.armTop, b.halfStroke));
  const s = G.stem;
  d = Math.min(d, sdSegment(px, py, s.x, s.y1, s.x, s.y2, s.halfW));
  const f = G.foot;
  d = Math.min(d, sdRoundRect(px, py, f.cx, f.cy, f.halfW, f.halfH, f.r));
  for (const slot of G.slots) { // grille: subtract slots from the head
    d = Math.max(d, -sdRoundRect(px, py, (slot.x1 + slot.x2) / 2, (slot.y1 + slot.y2) / 2,
      (slot.x2 - slot.x1) / 2, (slot.y2 - slot.y1) / 2, 2));
  }
  return d;
}

// Returns { size, data: Uint8Array RGBA (straight alpha) }
function renderIcon(colorName) {
  const color = COLORS[colorName] || COLORS.idle;
  const data = new Uint8Array(MASTER * MASTER * 4);
  const dark = [0x28, 0x28, 0x28];
  const withHalo = (colorName || 'idle') === 'idle';

  for (let y = 0; y < MASTER; y++) {
    for (let x = 0; x < MASTER; x++) {
      const px = x + 0.5, py = y + 0.5;
      const circleCov = cov(sdCircle(px, py, CX, CX, CIRCLE_R));
      const i = (y * MASTER + x) * 4;
      if (circleCov <= 0) continue;
      const d = glyphDistance(px, py);
      const glyphCov = cov(d);
      let r = color[0], g = color[1], b = color[2];
      if (withHalo && d > 0) {
        const haloA = HALO_ALPHA * clamp((HALO - d) / HALO, 0, 1);
        const k = haloA * (1 - glyphCov) * circleCov;
        r = r + (dark[0] - r) * k;
        g = g + (dark[1] - g) * k;
        b = b + (dark[2] - b) * k;
      }
      r = r + (255 - r) * glyphCov;
      g = g + (255 - g) * glyphCov;
      b = b + (255 - b) * glyphCov;
      data[i] = Math.round(r);
      data[i + 1] = Math.round(g);
      data[i + 2] = Math.round(b);
      data[i + 3] = Math.round(255 * circleCov);
    }
  }
  return { size: MASTER, data };
}

// Area-average (box) downsample with fractional edge coverage.
function downsample(src, srcSize, dstSize) {
  const out = new Uint8Array(dstSize * dstSize * 4);
  const scale = srcSize / dstSize;
  for (let dy = 0; dy < dstSize; dy++) {
    for (let dx = 0; dx < dstSize; dx++) {
      const x0 = dx * scale, x1 = (dx + 1) * scale;
      const y0 = dy * scale, y1 = (dy + 1) * scale;
      let sr = 0, sg = 0, sb = 0, sa = 0, area = 0;
      const ix0 = Math.floor(x0), ix1 = Math.min(Math.ceil(x1), srcSize);
      const iy0 = Math.floor(y0), iy1 = Math.min(Math.ceil(y1), srcSize);
      for (let sy = iy0; sy < iy1; sy++) {
        const fy = Math.min(y1, sy + 1) - Math.max(y0, sy);
        if (fy <= 0) continue;
        for (let sx = ix0; sx < ix1; sx++) {
          const fx = Math.min(x1, sx + 1) - Math.max(x0, sx);
          if (fx <= 0) continue;
          const w = fx * fy;
          const i = (sy * srcSize + sx) * 4;
          const a = src[i + 3];
          sa += a * w;
          sr += src[i] * a * w;
          sg += src[i + 1] * a * w;
          sb += src[i + 2] * a * w;
          area += w;
        }
      }
      const o = (dy * dstSize + dx) * 4;
      if (sa > 0) {
        out[o] = Math.round(sr / sa);
        out[o + 1] = Math.round(sg / sa);
        out[o + 2] = Math.round(sb / sa);
      }
      out[o + 3] = Math.round(sa / area);
    }
  }
  return out;
}

// BGRA premultiplied buffer, the format nativeImage.createFromBitmap/addRepresentation
// expects on Windows.
function rgbaToBgraPremultiplied(rgba) {
  const n = rgba.length;
  const out = new Uint8Array(n);
  for (let i = 0; i < n; i += 4) {
    const a = rgba[i + 3];
    out[i] = Math.round((rgba[i + 2] * a) / 255);
    out[i + 1] = Math.round((rgba[i + 1] * a) / 255);
    out[i + 2] = Math.round((rgba[i] * a) / 255);
    out[i + 3] = a;
  }
  return out;
}

module.exports = { MASTER, COLORS, G, renderIcon, downsample, rgbaToBgraPremultiplied };
