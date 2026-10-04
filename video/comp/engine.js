// Shotgun demo video: deterministic motion engine. Every frame is a pure function of t
// (global seconds). No CSS animation/transition, no timers, no Date, no unseeded random.
'use strict';

const FPS = 30;
const W = 1920, H = 1080;
const ACCENT = '#39ff9c', FG = '#c9d4df', DIM = '#5c6b7a', LINE = '#1c2530', BG = '#07090c', PANEL = '#0c1015';

// ── easing (the brief's global presets) ─────────────────────────────────────
function bezier(x1, y1, x2, y2) {
  const cx = 3 * x1, bx = 3 * (x2 - x1) - cx, ax = 1 - cx - bx;
  const cy = 3 * y1, by = 3 * (y2 - y1) - cy, ay = 1 - cy - by;
  const sx = (t) => ((ax * t + bx) * t + cx) * t;
  const sy = (t) => ((ay * t + by) * t + cy) * t;
  const dx = (t) => (3 * ax * t + 2 * bx) * t + cx;
  return (x) => {
    if (x <= 0) return 0;
    if (x >= 1) return 1;
    let t = x;
    for (let i = 0; i < 8; i++) {
      const d = dx(t);
      if (Math.abs(d) < 1e-6) break;
      t -= (sx(t) - x) / d;
    }
    let lo = 0, hi = 1;
    for (let i = 0; i < 30 && Math.abs(sx(t) - x) > 1e-6; i++) {
      if (sx(t) < x) lo = t; else hi = t;
      t = (lo + hi) / 2;
    }
    return sy(t);
  };
}
const OUT = bezier(0.16, 1, 0.3, 1);
const IN = bezier(0.7, 0, 0.84, 0);
const IO = bezier(0.65, 0, 0.35, 1);
const LIN = (x) => Math.min(1, Math.max(0, x));

const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
const lerp = (a, b, p) => a + (b - a) * p;
const prog = (t, t0, dur, ease = LIN) => ease(clamp((t - t0) / dur));
const frameOf = (t) => Math.round(t * FPS);

// Seeded PRNG (mulberry32), keyed by element and frame.
function rng(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let x = Math.imul(a ^ (a >>> 15), 1 | a);
    x = (x + Math.imul(x ^ (x >>> 7), 61 | x)) ^ x;
    return ((x ^ (x >>> 14)) >>> 0) / 4294967296;
  };
}

// ── DOM helpers ─────────────────────────────────────────────────────────────
function el(tag, attrs = {}, parent = null, text = null) {
  const svgTags = new Set(['svg', 'line', 'circle', 'rect', 'path', 'polyline', 'g', 'defs', 'pattern', 'filter', 'feGaussianBlur', 'feMerge', 'feMergeNode']);
  const e = svgTags.has(tag) ? document.createElementNS('http://www.w3.org/2000/svg', tag) : document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') e.setAttribute('class', v);
    else if (k === 'style' && typeof v === 'object') Object.assign(e.style, v);
    else e.setAttribute(k, v);
  }
  if (text != null) e.textContent = text;
  if (parent) parent.appendChild(e);
  return e;
}
function setStyle(e, s) { for (const k in s) if (e.style[k] !== s[k]) e.style[k] = s[k]; }
function setText(e, s) { if (e.textContent !== s) e.textContent = s; }
// Elements hidden by CSS (scenes, captions) carry e._disp = 'block'.
function show(e, on) { const d = on ? (e._disp || '') : 'none'; if (e.style.display !== d) e.style.display = d; }

// Images: swap src per frame, and let seek() await the decode.
const pending = [];
function setImg(img, src) {
  if (img.getAttribute('src') !== src) {
    img.setAttribute('src', src);
    pending.push(img.decode().catch(() => { throw new Error('image failed: ' + src); }));
  }
}
const pad5 = (n) => String(n).padStart(5, '0');

// ── presets ─────────────────────────────────────────────────────────────────
// word_in: 0→240 ms opacity 0→1, y +28→0, blur 6→0 (OUT). word_out: 0→180 ms 1→0, y 0→-16 (IN).
function wordStyle(t, tin, tout) {
  const p = prog(t, tin, 0.24, OUT);
  let o = p, y = 28 * (1 - p), b = 6 * (1 - p);
  if (tout != null && t >= tout) {
    const q = prog(t, tout, 0.18, IN);
    o = p * (1 - q); y = -16 * q; b = 0;
  }
  return { opacity: o.toFixed(3), transform: `translateY(${y.toFixed(2)}px)`, filter: b > 0.05 ? `blur(${b.toFixed(2)}px)` : 'none' };
}

// Blinking block cursor, 530 ms on/off from its own start.
const blinkOn = (t, t0, period = 0.53) => Math.floor((t - t0) / period) % 2 === 0;

// A headline: words (with optional accent) laid out in lines, each word_in on its own time.
class Headline {
  constructor(parent, words, breaksAfter = [], style = {}) {
    this.root = el('div', { class: 'headline' }, parent);
    Object.assign(this.root.style, style);
    this.words = [];
    let line = el('div', {}, this.root);
    words.forEach((w, i) => {
      const span = el('span', { class: 'w' + (w.accent ? ' accent' : '') }, line, w.text);
      let cursor = null;
      if (w.accent) cursor = el('span', { class: 'cursor' }, span, '█');
      this.words.push({ ...w, span, cursor });
      if (breaksAfter.includes(w.text) && i < words.length - 1) line = el('div', {}, this.root);
      else if (i < words.length - 1) line.appendChild(document.createTextNode(' '));
    });
  }
  render(t, exitT) {
    for (const w of this.words) {
      setStyle(w.span, wordStyle(t, w.t, exitT));
      if (w.cursor) setStyle(w.cursor, { visibility: t >= w.t && blinkOn(t, w.t) ? 'visible' : 'hidden' });
    }
  }
  visible(t, exitT) { return t >= this.words[0].t - 0.01 && (exitT == null || t < exitT + 0.2); }
}

// type_line: 30 ms per character; the cursor follows, and blinks 400 ms after the line ends.
function typed(text, t, t0, msPerChar = 30) {
  const n = clamp(Math.floor((t - t0) * 1000 / msPerChar) + 1, 0, text.length);
  const done = t0 + text.length * msPerChar / 1000;
  const cursor = t >= t0 && t < done + 0.4 && (t < done || blinkOn(t, done, 0.2));
  return { text: t < t0 ? '' : text.slice(0, n), cursor, finished: t >= done };
}
class TypeLine {
  constructor(parent, cls = '', style = {}, tag = 'div') {
    this.root = el(tag, { class: cls }, parent);
    Object.assign(this.root.style, style);
    this.txt = el('span', {}, this.root);
    this.cur = el('span', { style: { color: ACCENT } }, this.root, '█');
  }
  render(text, t, t0, ms = 30) {
    const s = typed(text, t, t0, ms);
    setText(this.txt, s.text);
    setStyle(this.cur, { visibility: s.cursor ? 'visible' : 'hidden' });
    return s;
  }
}

// callout: the leader draws 0→280 ms (OUT), the dot scales 0→1 over 0→160 ms (OUT),
// the label types at 22 ms/char from 200 ms. Exit: reverse in 200 ms.
class Callout {
  constructor(svg, parent, text) {
    this.text = text;
    this.line = el('line', { stroke: FG, 'stroke-width': 1.5, 'stroke-linecap': 'square' }, svg);
    this.dot = el('circle', { r: 6, fill: FG }, svg);
    this.label = el('div', { class: 'callout-label' }, parent);
  }
  render(t, t0, exitT, target, off) {
    const on = t >= t0 && (exitT == null || t < exitT + 0.2);
    for (const e of [this.line, this.dot, this.label]) show(e, on);
    if (!on) return;
    const e = exitT != null && t >= exitT ? prog(t, exitT, 0.2, IN) : 0;
    const draw = prog(t, t0, 0.28, OUT) * (1 - e);
    const dot = prog(t, t0, 0.16, OUT) * (1 - e);
    const [tx, ty] = target;
    const lx = tx + off[0], ly = ty + off[1];
    // the leader runs from the label to the target
    this.line.setAttribute('x1', lx); this.line.setAttribute('y1', ly);
    this.line.setAttribute('x2', lerp(lx, tx, draw)); this.line.setAttribute('y2', lerp(ly, ty, draw));
    this.dot.setAttribute('cx', tx); this.dot.setAttribute('cy', ty);
    this.dot.setAttribute('r', (6 * dot).toFixed(2));
    const n = Math.floor(clamp((t - t0 - 0.2) * 1000 / 22 + 1, 0, this.text.length) * (1 - e));
    setText(this.label, this.text.slice(0, n));
    show(this.label, n > 0);
    const right = off[0] >= 0;
    setStyle(this.label, right
      ? { left: `${lx + 6}px`, right: 'auto', top: `${ly}px` }
      : { left: 'auto', right: `${W - lx + 6}px`, top: `${ly}px` });
  }
}

// tag_done: [DONE] pops scale 0.8→1.0 and opacity 0→1 over 160 ms (OUT).
function tagStyle(t, t0) {
  const p = prog(t, t0, 0.16, OUT);
  return { opacity: (t >= t0 ? p : 0).toFixed(3), transform: `scale(${lerp(0.8, 1, p).toFixed(3)})`, display: t >= t0 ? 'inline-block' : 'none' };
}

// push_in: scale 1.00→1.08 over the shot (IO), about an origin near the subject.
function pushIn(t, t0, t1, amount = 0.08) { return 1 + amount * prog(t, t0, t1 - t0, IO); }
// A point under a scale s about origin o (frame coordinates).
const scaled = (p, o, s) => [o[0] + (p[0] - o[0]) * s, o[1] + (p[1] - o[1]) * s];

// ── the Lit Seat mark, built: outline → windshield → driver seat → passenger fill → glow ──
class Mark {
  constructor(parent, size) {
    this.svg = el('svg', { viewBox: '0 0 120 120', width: size, height: size, fill: 'none', style: { position: 'absolute', overflow: 'visible' } }, parent);
    this.outline = el('rect', { x: 8, y: 8, width: 104, height: 104, rx: 14, stroke: FG, 'stroke-width': 6, pathLength: 100, 'stroke-dasharray': 100 }, this.svg);
    this.wind = el('path', { d: 'M26 30 H94', stroke: DIM, 'stroke-width': 4, 'stroke-linecap': 'round', pathLength: 100, 'stroke-dasharray': 100 }, this.svg);
    this.driver = el('rect', { x: 27, y: 46, width: 27, height: 48, rx: 6, stroke: FG, 'stroke-width': 5, pathLength: 100, 'stroke-dasharray': 100 }, this.svg);
    this.pass = el('rect', { x: 66, y: 46, width: 27, height: 48, rx: 6, fill: ACCENT }, this.svg);
  }
  // mark_build (ms): outline 0-500 (IO) → windshield 400-650 → driver 550-850 →
  // passenger fill 850-1050 (OUT) → glow 0→18→8 px at 1050-1500. speed < 1 = faster.
  render(t, t0, speed = 1) {
    const ms = (t - t0) * 1000 / speed;
    const p = (a, b, ease = IO) => ease(clamp((ms - a) / (b - a)));
    this.outline.setAttribute('stroke-dashoffset', (100 * (1 - p(0, 500))).toFixed(2));
    this.wind.setAttribute('stroke-dashoffset', (100 * (1 - p(400, 650))).toFixed(2));
    this.driver.setAttribute('stroke-dashoffset', (100 * (1 - p(550, 850))).toFixed(2));
    this.pass.setAttribute('opacity', p(850, 1050, OUT).toFixed(3));
    let glow = 0;
    if (ms >= 1050) glow = ms < 1275 ? lerp(0, 18, IO(clamp((ms - 1050) / 225))) : lerp(18, 8, IO(clamp((ms - 1275) / 225)));
    this.svg.style.filter = glow > 0.1 ? `drop-shadow(0 0 ${glow.toFixed(1)}px rgba(57,255,156,0.55))` : 'none';
    this.svg.style.opacity = ms < 0 ? '0' : '1';
  }
  place(cx, cy, size) {
    setStyle(this.svg, { left: `${(cx - size / 2).toFixed(2)}px`, top: `${(cy - size / 2).toFixed(2)}px`, width: `${size.toFixed(2)}px`, height: `${size.toFixed(2)}px` });
  }
}

// A thin green scanline (2 px).
function scanline(parent) {
  return el('div', { style: { position: 'absolute', left: '0', width: '1920px', height: '2px', background: ACCENT, display: 'none' } }, parent);
}

// Grain: 6 seeded noise tiles at half resolution, one picked per frame by a seeded hash.
const GRAIN = [];
function makeGrain(canvas) {
  const w = 960, h = 540;
  for (let v = 0; v < 6; v++) {
    const c = document.createElement('canvas');
    c.width = w; c.height = h;
    const ctx = c.getContext('2d');
    const img = ctx.createImageData(w, h);
    const r = rng(9001 + v * 7919);
    for (let i = 0; i < w * h; i++) {
      const g = Math.floor(r() * 255);
      img.data[i * 4] = g; img.data[i * 4 + 1] = g; img.data[i * 4 + 2] = g; img.data[i * 4 + 3] = 255;
    }
    ctx.putImageData(img, 0, 0);
    GRAIN.push(c);
  }
  canvas.width = w; canvas.height = h;
}
function drawGrain(canvas, f) {
  const pick = Math.floor(rng(f * 2654435761)() * GRAIN.length);
  canvas.getContext('2d').drawImage(GRAIN[pick], 0, 0);
}
