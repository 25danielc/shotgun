// Shotgun demo video, cut 2: the scenes. Times come from window.TL (tools/build.py, global s).
// Cut 2 keeps the agent in the car: real in-car footage is the main picture, and the
// dashboard, the call and the camera run in windows side by side.
'use strict';

const BEAT = 60 / TL.music.bpm;
const stage = document.getElementById('stage');
const SCENES = [];
function scene(id, start, end, build) {
  const root = el('div', { class: 'scene', id }, stage);
  root._disp = 'block';
  const s = { id, start, end, root, render: () => {} };
  build(s, root);
  SCENES.push(s);
  return s;
}

// ── footage and replay frame lookup ─────────────────────────────────────────
const ASSET = '../work';
const seq = (dir, n, s, offset = 0) => `${ASSET}/seq/${dir}/${pad5(clamp(Math.round((s - offset) * FPS) + 1, 1, n))}.jpg`;
const introSrc = (t) => `${ASSET}/seq/intro/${pad5(clamp(Math.floor(t * FPS) + 1, 1, 283))}.jpg`;
const carSrc = (s) => seq('car', 492, s, 3.0); // carplay_on.mov from 3.0 s
const driveSrc = (s) => seq('drive', 2129, s); // IMG_0353, upright
const selfieSrc = (s) => seq('selfie', 165, s); // IMG_0354, portrait
const dash2Src = (s) => seq('dash2', 1402, s); // the 1:58 AM dash recording (drive #16)
const hms = (s) => { const [h, m, x] = s.split(':').map(Number); return h * 3600 + m * 60 + x; };
const SEG = { departure: [hms('22:24:58'), 1021], arrival: [hms('22:31:50'), 181] };
function dashSrc(sec) {
  // the replay frames: 0.2 s of drive time apart (tools/replay_dashboard.py, SPEED 6 at 30 fps)
  const seg = sec >= SEG.arrival[0] - 1 ? 'arrival' : 'departure';
  const [s0, n] = SEG[seg];
  const i = clamp(Math.round((sec - s0) / 0.2), 0, n - 1);
  return `${ASSET}/dash/${seg}/${pad5(i)}.jpg`;
}
// [[beat offset, "HH:MM:SS"], ...] → drive time at a beat offset (piecewise linear).
function replayAt(map, b) {
  const pts = map.map(([k, s]) => [k, hms(s)]);
  if (b <= pts[0][0]) return pts[0][1];
  for (let i = 1; i < pts.length; i++) {
    if (b <= pts[i][0]) return lerp(pts[i - 1][1], pts[i][1], (b - pts[i - 1][0]) / (pts[i][0] - pts[i - 1][0]));
  }
  return pts[pts.length - 1][1];
}
// The source time of a cut list [{t, src}, ...] at global t (the footage runs in real time).
function cutSrc(cuts, t) {
  let cur = cuts[0];
  for (const c of cuts) if (t >= c.t) cur = c;
  return cur.src + (t - cur.t);
}

// A camera over a layer: point p (layer px) is placed at frame point f, scaled by s.
class Cam {
  constructor(parent, w = 1920, h = 1080) {
    this.wrap = el('div', { class: 'layer', style: { transformOrigin: '0 0', width: `${w}px`, height: `${h}px` } }, parent);
    this.cam = { p: [960, 540], f: [960, 540], s: 1 };
  }
  set(p, f, s) {
    this.cam = { p, f, s };
    const tx = f[0] - p[0] * s, ty = f[1] - p[1] * s;
    setStyle(this.wrap, { transform: `translate(${tx.toFixed(2)}px, ${ty.toFixed(2)}px) scale(${s.toFixed(4)})` });
  }
  map(q) { const c = this.cam; return [c.f[0] + (q[0] - c.p[0]) * c.s, c.f[1] + (q[1] - c.p[1]) * c.s]; }
}
// The dash recording is 1620x1080: shown 1920 wide (1280 tall, centred), so its pixels scale by 1920/1620.
const D2K = 1920 / 1620;
const d2 = (p) => [p[0] * D2K, p[1] * D2K - 100];
function dash2Layer(parent) {
  const cam = new Cam(parent);
  const img = el('img', { style: { position: 'absolute', left: '0', top: '-100px', width: '1920px', height: '1280px', filter: TL.grade.dash2 } }, cam.wrap);
  return { cam, img };
}

// A dashboard view: the 2x replay frame shown at 1920x1080 under a camera.
class Dash {
  constructor(parent) {
    this.wrap = el('div', { class: 'layer', style: { transformOrigin: '0 0' } }, parent);
    this.img = el('img', { style: { position: 'absolute', left: '0', top: '0', width: '1920px', height: '1080px' } }, this.wrap);
    this.cam = { cx: 960, cy: 540, s: 1, fx: 960, fy: 540 };
  }
  frame(sec) { setImg(this.img, dashSrc(sec)); }
  set(cam) {
    this.cam = cam;
    const tx = cam.fx - cam.cx * cam.s, ty = cam.fy - cam.cy * cam.s;
    setStyle(this.wrap, { transform: `translate(${tx.toFixed(2)}px, ${ty.toFixed(2)}px) scale(${cam.s.toFixed(4)})` });
  }
  map(p) { const c = this.cam; return [c.fx + (p[0] - c.cx) * c.s, c.fy + (p[1] - c.cy) * c.s]; }
}
// punch: from the full dashboard to the window over 420 ms (IO), then push_in.
function punchCam(t, t0, t1, rect, at = [960, 560], room = [1600, 760], push = 0.04) {
  const [x, y, w, h] = rect;
  const fit = Math.min(room[0] / w, room[1] / h);
  const p = prog(t, t0, 0.42, IO);
  const s = lerp(1, fit, p) * (1 + push * prog(t, t0 + 0.42, Math.max(0.01, t1 - t0 - 0.42), IO));
  return { cx: lerp(960, x + w / 2, p), cy: lerp(540, y + h / 2, p), s, fx: lerp(960, at[0], p), fy: lerp(540, at[1], p) };
}

// A window in the house style holding live footage (picture-in-picture).
class VideoWin {
  constructor(parent, rect, title, grade = 'none') {
    const [x, y, w, h] = rect;
    this.root = el('div', { class: 'win', style: { left: `${x}px`, top: `${y}px`, width: `${w}px`, height: `${h}px`, overflow: 'visible' } }, parent);
    const clip = el('div', { style: { position: 'absolute', inset: '0', overflow: 'hidden' } }, this.root);
    this.img = el('img', { style: { width: '100%', height: '100%', objectFit: 'cover', filter: grade } }, clip);
    el('div', { class: 'title' }, this.root, title);
    this.rec = el('div', { class: 'mono22', style: { position: 'absolute', right: '14px', top: '10px', color: ACCENT, fontSize: '20px', letterSpacing: '0.1em', background: 'rgba(7,9,12,0.7)', padding: '2px 8px' } }, clip, '● LIVE');
  }
  // pop in: scale 0.92→1 and fade (OUT, 240 ms); out: fade (IN, 180 ms)
  render(t, t0, exitT, src) {
    const on = t >= t0 && (exitT == null || t < exitT + 0.18);
    show(this.root, on);
    if (!on) return;
    setImg(this.img, src);
    const p = prog(t, t0, 0.24, OUT), q = exitT != null && t >= exitT ? prog(t, exitT, 0.18, IN) : 0;
    setStyle(this.root, { opacity: (p * (1 - q)).toFixed(3), transform: `scale(${lerp(0.92, 1, p).toFixed(4)})` });
    setStyle(this.rec, { visibility: blinkOn(t, t0, 0.53) ? 'visible' : 'hidden' });
  }
}

// The call card (abstract Shotgun call state; no iOS UI): mark, name, state, timer, waveform.
class CallCard {
  constructor(parent, title, rect) {
    const [x, y, w, h] = rect;
    this.root = el('div', { class: 'win', style: { left: `${x}px`, top: `${y}px`, width: `${w}px`, height: `${h}px` } }, parent);
    el('div', { class: 'title' }, this.root, title);
    const mark = el('div', { style: { position: 'absolute', left: '36px', top: '40px', width: '88px', height: '88px' } }, this.root);
    this.mark = new Mark(mark, 88);
    this.mark.place(44, 44, 88);
    el('div', { style: { position: 'absolute', left: '150px', top: '44px', fontWeight: 800, fontSize: '52px', letterSpacing: '-0.01em' } }, this.root, 'SHOTGUN');
    this.state = el('div', { class: 'mono22', style: { position: 'absolute', left: '154px', top: '108px', color: DIM, letterSpacing: '0.12em' } }, this.root, 'ON CALL');
    this.timer = el('div', { style: { position: 'absolute', right: '32px', top: '50px', fontWeight: 500, fontSize: '36px', color: FG } }, this.root, '00:00');
    this.bars = [];
    this.n = 48;
    const bw = (w - 72) / this.n;
    for (let i = 0; i < this.n; i++) {
      this.bars.push(el('div', { style: { position: 'absolute', left: `${36 + i * bw}px`, width: `${Math.max(2, bw - 4)}px`, background: FG, top: '0', height: '2px' } }, this.root));
    }
    this.waveTop = 160; this.waveH = h - 160 - 56;
    this.who = el('div', { class: 'mono22', style: { position: 'absolute', left: '36px', bottom: '18px', color: DIM, letterSpacing: '0.12em' } }, this.root, '');
  }
  render(t, clipsSorted, callStartSrc = 0) {
    this.mark.render(t, -10);
    const f = frameOf(t);
    const mid = this.waveTop + this.waveH / 2;
    for (let i = 0; i < this.n; i++) {
      const v = ENV[f - (this.n - 1 - i)] || 0;
      const hh = Math.max(2, v * this.waveH);
      setStyle(this.bars[i], { top: `${(mid - hh / 2).toFixed(1)}px`, height: `${hh.toFixed(1)}px`, opacity: (0.35 + 0.65 * (i / this.n)).toFixed(2) });
    }
    // the real time into the call of whatever was said last (cuts show as jumps)
    let shown = null, speaking = null;
    for (const c of clipsSorted) {
      if (t >= c.t) {
        shown = Math.min(c.out, c.in + (t - c.t)) - callStartSrc;
        if (t < c.t + (c.out - c.in)) speaking = c.speaker;
      }
    }
    if (shown != null) {
      const s = Math.max(0, Math.floor(shown));
      setText(this.timer, `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`);
    }
    setText(this.who, speaking === 'SHOTGUN' ? 'SHOTGUN SPEAKING' : speaking ? 'YOU' : '');
  }
}

// A typed call panel: "> you" lines, "SHOTGUN:" lines, chips with [DONE], accent-bordered tags.
class CallPanel {
  constructor(parent, rect, title, lines, big = 31) {
    const [x, y, w, h] = rect;
    this.root = el('div', { class: 'win', style: { left: `${x}px`, top: `${y}px`, width: `${w}px`, height: `${h}px` } }, parent);
    el('div', { class: 'title' }, this.root, title);
    const list = el('div', { style: { position: 'absolute', left: '28px', top: '34px', right: '24px' } }, this.root);
    this.rows = lines.map((L) => {
      const row = el('div', { style: { marginBottom: '16px', fontWeight: 500, fontSize: L.kind ? '28px' : `${big}px`, lineHeight: 1.3, display: 'none' } }, list);
      if (L.kind === 'tag') return { L, row, tag: el('span', { class: 'tagbox' }, row, L.text) };
      if (L.kind === 'chip') {
        const chip = el('span', { class: 'chip' }, row);
        return { L, row, tl: new TypeLine(chip, '', {}, 'span'), done: L.done ? el('span', { class: 'tag', style: { marginLeft: '14px', fontSize: '28px' } }, row, '[DONE]') : null };
      }
      el('span', { style: { color: L.who === 'shotgun' ? ACCENT : DIM } }, row, L.who === 'shotgun' ? 'SHOTGUN: ' : '> ');
      const tl = new TypeLine(row, '', {}, 'span');
      if (L.who === 'shotgun') tl.txt.style.color = FG;
      return { L, row, tl };
    });
  }
  render(t) {
    for (const r of this.rows) {
      show(r.row, t >= r.L.t);
      if (r.tag) setStyle(r.tag, tagStyle(t, r.L.t));
      if (r.tl) r.tl.render(r.L.text, t, r.L.t);
      if (r.done) setStyle(r.done, tagStyle(t, r.L.t + r.L.text.length * 0.03 + 0.1));
    }
  }
}

// ── captions (every spoken line, from window.CAPS) ──────────────────────────
const capBox = el('div', { id: 'captions' }, stage);
capBox._disp = 'block';
function renderCaptions(t) {
  const c = CAPS.find((c) => t >= c.start && t < c.end);
  show(capBox, !!c);
  if (!c) return;
  const html = (c.speaker ? `<span class="who">${c.speaker}:</span> ` : '') + c.text.replace(/&/g, '&amp;').replace(/</g, '&lt;');
  if (capBox.innerHTML !== html) capBox.innerHTML = html;
}

// ═════════════════════════════════════════════════════════════════════════════
// INTRO (with the three phone agents popping out from his fingertips) + A · SWIPE
const I0 = TL.intro, A = TL.A_swipe;
scene('intro', 0, A.end, (s, root) => {
  const wrap = el('div', { class: 'layer' }, root);
  const img = el('img', { class: 'fill' }, wrap);
  const svg = el('svg', { class: 'over' }, wrap);
  const cards = I0.logos.map((L) => {
    const line = el('line', { stroke: FG, 'stroke-width': 2, 'stroke-linecap': 'round' }, svg);
    const dot = el('circle', { r: 7, fill: ACCENT }, svg);
    const card = el('div', { class: 'win', style: { left: `${L.to[0] - 160}px`, top: `${L.to[1] - 92}px`, width: '320px', height: '184px', transformOrigin: '50% 50%' } }, wrap);
    const logoBox = el('div', { style: { position: 'absolute', left: '0', right: '0', top: '26px', height: '74px', display: 'flex', alignItems: 'center', justifyContent: 'center' } }, card);
    const logo = el('img', { src: L.logo, style: { maxHeight: L.id === 'instinct' ? '58px' : '70px', maxWidth: '250px' } }, logoBox);
    pending.push(logo.decode());
    el('div', { style: { position: 'absolute', left: '0', right: '0', top: '112px', textAlign: 'center', fontWeight: 800, fontSize: '30px' } }, card, L.name);
    el('div', { style: { position: 'absolute', left: '0', right: '0', top: '150px', textAlign: 'center', fontWeight: 500, fontSize: '18px', letterSpacing: '0.14em', color: DIM } }, card, `BY ${L.by}`);
    return { L, line, dot, card };
  });
  const lt = I0.lower_third;
  const box = el('div', { style: { position: 'absolute', left: '96px', top: '760px', background: 'rgba(7,9,12,0.8)', padding: '10px 16px', fontWeight: 500, fontSize: '28px', letterSpacing: '0.08em', textTransform: 'uppercase', whiteSpace: 'nowrap' } }, wrap);
  const line = new TypeLine(box);
  const incoming = el('div', { class: 'bg', style: { display: 'none' } }, root);
  const seam = scanline(root);
  s.render = (t) => {
    setImg(img, introSrc(Math.min(t, I0.out - I0.in - 1 / FPS)));
    line.render(`┌─ ${lt.text} ─┐`, t, lt.t);
    setStyle(box, wordStyle(t, lt.t, lt.exit));
    show(box, t >= lt.t && t < lt.exit + 0.2);
    for (const c of cards) {
      // the line draws from the fingertip (OUT, 280 ms), the card pops 0.6→1 (OUT, 240 ms) and
      // stays; the line fades once the card has landed; all dim, then leave together
      const L = c.L;
      const draw = prog(t, L.t, 0.28, OUT);
      const gone = prog(t, L.t + 0.9, 0.3, LIN);
      const [x1, y1] = L.from, [x2, y2] = L.to;
      c.line.setAttribute('x1', x1); c.line.setAttribute('y1', y1);
      c.line.setAttribute('x2', lerp(x1, x2, draw)); c.line.setAttribute('y2', lerp(y1, y2, draw));
      c.line.setAttribute('opacity', t >= L.t ? (1 - gone).toFixed(3) : '0');
      c.dot.setAttribute('cx', x1); c.dot.setAttribute('cy', y1);
      c.dot.setAttribute('r', (7 * prog(t, L.t, 0.16, OUT) * (1 - gone)).toFixed(2));
      const p = prog(t, L.t + 0.2, 0.24, OUT);
      const dim = prog(t, I0.logos_dim, 0.3, IO);
      const out = prog(t, I0.logos_exit, 0.18, IN);
      show(c.card, t >= L.t + 0.2 && t < I0.logos_exit + 0.2);
      setStyle(c.card, { opacity: (p * lerp(1, 0.45, dim) * (1 - out)).toFixed(3), transform: `translateY(${(-16 * out).toFixed(1)}px) scale(${lerp(0.6, 1, p).toFixed(4)})` });
    }
    // swipe_down: outgoing y 0→-100%, blur 0→40 px (IN, 380 ms); incoming +100%→0 (OUT, from 120 ms)
    const p = prog(t, A.start, 0.38, IN);
    setStyle(wrap, { transform: `translateY(${(-100 * p).toFixed(2)}%)`, filter: p > 0.01 ? `blur(${(40 * p).toFixed(1)}px)` : 'none' });
    const q = prog(t, A.start + 0.12, 0.26, OUT);
    const inY = 1080 * (1 - q);
    show(incoming, t >= A.start + 0.12);
    setStyle(incoming, { transform: `translateY(${inY.toFixed(1)}px)` });
    show(seam, t >= A.start && t < A.end);
    setStyle(seam, { top: `${(t >= A.start + 0.12 ? inY : 1080 * (1 - p)).toFixed(1) - 1}px` });
  };
});

// B · THESIS
const B = TL.B_thesis;
scene('thesis', B.start, B.end, (s, root) => {
  el('div', { class: 'bg' }, root);
  const holder = el('div', { class: 'layer' }, root);
  const mark = new Mark(holder, B.mark_size);
  const head = new Headline(root, B.headline, B.headline_breaks_after);
  s.render = (t) => {
    mark.render(t, B.start);
    const p = prog(t, B.mark_slide, 0.5, IO);
    const size = lerp(B.mark_size, B.mark_small, p);
    mark.place(lerp(960, W - 96 - B.mark_small / 2, p), lerp(540, 80 + B.mark_small / 2, p), size);
    head.render(t, B.exit);
  };
});

// C · PROBLEM
const C = TL.C_problem;
scene('problem', C.start, C.end, (s, root) => {
  el('div', { class: 'bg' }, root);
  const dots = el('svg', { class: 'over' }, root);
  const pat = el('pattern', { id: 'dots', width: 32, height: 32, patternUnits: 'userSpaceOnUse' }, el('defs', {}, dots));
  el('circle', { cx: 16, cy: 16, r: 1.6, fill: LINE }, pat);
  el('rect', { x: 0, y: 0, width: W, height: H, fill: 'url(#dots)' }, dots);
  const one = el('div', { class: 'layer' }, root);
  const h1 = new Headline(one, C.headline1, C.headline1_breaks_after);
  const sub = el('div', { class: 'callout-label dim', style: { left: '100px', top: '336px', fontSize: '40px', color: FG } }, one);
  const two = el('div', { class: 'layer' }, root);
  const h2 = new Headline(two, C.headline2, C.headline2_breaks_after);
  const wipe = scanline(root);
  s.render = (t) => {
    h1.render(t);
    const n = clamp(Math.floor((t - C.callout1.t) * 1000 / 22) + 1, 0, C.callout1.text.length);
    setText(sub, t >= C.callout1.t ? C.callout1.text.slice(0, n) : '');
    // scan_wipe: a 2 px green line sweeps top→bottom over 300 ms (LIN), revealing the next shot
    const p = prog(t, C.wipe, 0.3, LIN), y = 1080 * p;
    show(wipe, t >= C.wipe && t < C.wipe + 0.3);
    setStyle(wipe, { top: `${(y - 1).toFixed(1)}px` });
    setStyle(one, { clipPath: t >= C.wipe ? `inset(${y.toFixed(1)}px 0 0 0)` : 'none' });
    show(one, t < C.wipe + 0.3);
    show(two, t >= C.wipe);
    setStyle(two, { clipPath: t < C.wipe + 0.3 ? `inset(0 0 ${(1080 - y).toFixed(1)}px 0)` : 'none' });
    h2.render(t, C.exit);
  };
});

// D · PLUG IN: the dash recording (phone goes in, CarPlay comes up) + the selfie in a window
const D = TL.D_plugin;
scene('plugin', D.start, D.end, (s, root) => {
  el('div', { class: 'bg' }, root);
  const { cam, img } = dash2Layer(root);
  const svg = el('svg', { class: 'over' }, root);
  const sw = D.selfie;
  const selfie = new VideoWin(root, sw.rect, sw.title);
  const head = new Headline(root, D.headline, []);
  const call = new Callout(svg, root, D.callout.text);
  s.render = (t) => {
    setImg(img, dash2Src(cutSrc(D.dash2, t)));
    const k = D.dash2_cam.scale * (1 + D.dash2_cam.push * prog(t, D.start, D.end - D.start, IO));
    cam.set(d2(D.dash2_cam.origin), [1180, 560], k);
    selfie.render(t, sw.t - 0.1, sw.exit, selfieSrc(clamp(sw.in + (t - sw.t), sw.in, sw.out)));
    head.render(t);
    call.render(t, D.callout.t, null, cam.map(d2(D.callout.target)), D.callout.label_off);
  };
});

// E · IT CALLS YOU (the sharp close-up take)
function carScene(id, S, origin) {
  return scene(id, S.start, S.end, (s, root) => {
    const wrap = el('div', { class: 'layer', style: { transformOrigin: `${origin[0]}px ${origin[1]}px` } }, root);
    const img = el('img', { class: 'fill' }, wrap);
    const svg = el('svg', { class: 'over' }, root);
    const head = new Headline(root, S.headline, S.headline_breaks_after || []);
    const callouts = (S.callouts || []).map((c) => ({ c, v: new Callout(svg, root, c.text) }));
    s.render = (t) => {
      setImg(img, carSrc(S.src_in + (t - S.start)));
      const k = pushIn(t, S.start, S.end);
      setStyle(wrap, { transform: `scale(${k.toFixed(4)})` });
      head.render(t, S.exit);
      for (const { c, v } of callouts) v.render(t, c.t, c.exit ?? null, scaled(c.target, origin, k), c.label_off);
    };
  });
}
carScene('ring', TL.E_ring, [940, 455]);

// E2 · SAY WHERE: the real drive #16 exchange over the dash recording, with the call panel
const E2 = TL.E2_where;
scene('where', E2.start, E2.end, (s, root) => {
  el('div', { class: 'bg' }, root);
  const { cam, img } = dash2Layer(root);
  const head = new Headline(root, E2.headline, []);
  const panel = new CallPanel(root, E2.panel.rect, E2.panel.title, E2.panel_lines, 31);
  s.render = (t) => {
    // the on-call screen, at the in-call time of whatever was said last (its timer jumps at cuts)
    let inCall = E2.clips[0].in;
    for (const c of E2.clips) if (t >= c.t) inCall = Math.min(c.out, c.in + (t - c.t));
    setImg(img, dash2Src(E2.dash2_call_start + inCall));
    const k = E2.dash2_cam.scale * (1 + E2.dash2_cam.push * prog(t, E2.start, E2.end - E2.start, IO));
    cam.set(d2(E2.dash2_cam.origin), [790, 600], k);
    head.render(t, E2.exit);
    panel.render(t);
  };
});

// F · TALK: the driving footage, with the call card and the call panel running in windows
const F = TL.F_talk;
const fClips = F.clips.map((c) => ({ ...c }));
scene('talk', F.start, F.end, (s, root) => {
  const img = el('img', { class: 'fill', style: { filter: TL.grade.drive } }, root);
  const head = new Headline(root, F.headline, F.headline_breaks_after);
  const card = new CallCard(root, F.card.title, F.card.rect);
  const panel = new CallPanel(root, F.panel.rect, F.panel.title, F.panel_lines, 31);
  s.render = (t) => {
    setImg(img, driveSrc(cutSrc(F.broll, t)));
    head.render(t, F.exit);
    const p = prog(t, F.start + 0.1, 0.24, OUT);
    setStyle(card.root, { opacity: p.toFixed(3), transform: `scale(${lerp(0.92, 1, p).toFixed(4)})` });
    setStyle(panel.root, { opacity: p.toFixed(3), transform: `scale(${lerp(0.92, 1, p).toFixed(4)})` });
    card.render(t, fClips, F.call_started_src);
    panel.render(t);
  };
});

// G · SUBAGENTS WORK WHILE YOU DRIVE: the replayed dashboard, with the drive running in a window
const G = TL.G_subagents;
scene('subagents', G.start, G.end, (s, root) => {
  const broll = el('img', { class: 'fill', style: { filter: TL.grade.drive } }, root);
  const dashL = el('div', { class: 'layer' }, root);
  el('div', { class: 'bg' }, dashL);
  const dash = new Dash(dashL);
  const svg = el('svg', { class: 'over' }, root);
  const head = new Headline(root, G.headline, G.headline_breaks_after);
  const prov = el('div', { class: 'callout-label dim', style: { left: '96px', top: '1000px', fontSize: '30px', transform: 'translateY(-100%)' } }, root, 'DRIVE #8 · OCT 3, 22:25 · REAL DATA, TIME-COMPRESSED');
  const pip = new VideoWin(root, G.pip.rect, G.pip.title, TL.grade.drive);
  const wins = G.windows.map((w, i) => ({
    w, end: i + 1 < G.windows.length ? G.windows[i + 1].t : G.end,
    callouts: (w.callouts || []).map((c) => ({ c, v: new Callout(svg, root, c.text) })),
  }));
  // JOBS: the step checklist, ticking with [DONE] (values from job #189)
  const steps = [['issue_filed', 'issue #10'], ['action_running', 'Claude Code'], ['pr_opened', 'PR #11'], ['tests', 'passed'], ['approval_check', 'pre-approved'], ['merged', '']];
  const list = el('div', { style: { position: 'absolute', left: '230px', top: '690px', width: '1080px', display: 'none', columnCount: 2, columnGap: '30px' } }, root);
  const ticks = steps.map(([name, detail]) => {
    const row = el('div', { style: { fontWeight: 500, fontSize: '30px', lineHeight: '46px', whiteSpace: 'nowrap' } }, list);
    const tag = el('span', { class: 'tag' }, row, '[DONE]');
    el('span', { style: { marginLeft: '16px' } }, row, name);
    if (detail) el('span', { style: { marginLeft: '12px', color: DIM } }, row, detail);
    return { row, tag };
  });
  s.render = (t) => {
    const cur = wins.find((x) => t >= x.w.t && t < x.end);
    show(dashL, !!cur);
    show(broll, !cur);
    show(prov, !!cur);
    if (!cur) setImg(broll, driveSrc(G.broll_full.src + (t - G.broll_full.t)));
    else {
      const b = (t - cur.w.t) / BEAT;
      dash.frame(replayAt(cur.w.replay, b));
      dash.set(punchCam(t, cur.w.t, cur.end, cur.w.rect, cur.w.at, cur.w.room, cur.w.push_in ? 0.1 : 0.04));
    }
    pip.render(t, G.windows[0].t, null, driveSrc(G.pip.src + (t - G.windows[0].t)));
    head.render(t, G.exit);
    show(head.root, t < G.exit + 0.2);
    for (const x of wins) {
      for (const { c, v } of x.callouts) {
        if (cur !== x) { v.render(-1, 0, null, [0, 0], [0, 0]); continue; }
        v.render(t, x.w.t + c.t_beat * BEAT, null, dash.map(c.target), c.label_off);
      }
    }
    const jobs = wins[0];
    show(list, cur === jobs);
    if (cur === jobs) {
      ticks.forEach((tk, i) => {
        const at = jobs.w.t + jobs.w.done_ticks_beats[i] * BEAT;
        setStyle(tk.row, { opacity: t >= at ? '1' : '0' });
        setStyle(tk.tag, tagStyle(t, at));
      });
    }
  };
});

// H · ONE CALL BEFORE YOU PARK: the countdown (driving in a window), then the arrival call
// over the driving footage
const Hh = TL.H_arrival;
const hClip = [{ t: Hh.audio.t, in: Hh.audio.in, out: Hh.audio.out, speaker: 'SHOTGUN' }];
scene('arrival', Hh.start, Hh.end, (s, root) => {
  const broll = el('img', { class: 'fill', style: { filter: TL.grade.drive } }, root);
  const dashL = el('div', { class: 'layer' }, root);
  el('div', { class: 'bg' }, dashL);
  const dash = new Dash(dashL);
  const svg = el('svg', { class: 'over' }, root);
  const cd = Hh.countdown;
  const call = new Callout(svg, root, cd.callout.text);
  const pip = new VideoWin(root, Hh.pip.rect, Hh.pip.title, TL.grade.drive);
  const card = new CallCard(root, Hh.card.title, Hh.card.rect);
  const ov = el('div', { style: { position: 'absolute', right: '32px', bottom: '16px', display: 'flex', gap: '14px', alignItems: 'center' } }, card.root);
  const ovText = el('span', { class: 'tagbox', style: { fontSize: '26px' } }, ov, Hh.overlay.text);
  const ovDone = el('span', { class: 'tag', style: { fontSize: '26px' } }, ov, '[DONE]');
  const h1 = new Headline(root, Hh.headline1, [], { fontSize: '100px' });
  const h2 = new Headline(root, Hh.headline2, [], { fontSize: '100px' });
  s.render = (t) => {
    const onCall = t >= Hh.audio.t;
    show(dashL, !onCall);
    show(broll, onCall);
    show(card.root, onCall);
    if (!onCall) {
      dash.frame(replayAt(cd.replay, (t - cd.t) / BEAT));
      dash.set(punchCam(t, cd.t, cd.until, cd.rect, cd.at, cd.room, 0.05));
      call.render(t, cd.t + cd.callout.t_beat * BEAT, cd.until - 0.2, dash.map(cd.callout.target), cd.callout.label_off);
      pip.render(t, Hh.start, Hh.audio.t - 0.18, driveSrc(Hh.pip.src + (t - Hh.start)));
    } else {
      call.render(-1, 0, null, [0, 0], [0, 0]);
      pip.render(-1, 0, null, '');
      setImg(broll, driveSrc(Hh.broll_full.src + (t - Hh.audio.t)));
      const p = prog(t, Hh.audio.t, 0.24, OUT);
      setStyle(card.root, { opacity: p.toFixed(3), transform: `scale(${lerp(0.92, 1, p).toFixed(4)})` });
      card.render(t, hClip, 0);
      setStyle(ovText, tagStyle(t, Hh.overlay.t));
      setStyle(ovDone, tagStyle(t, Hh.overlay.t + 0.12));
    }
    h1.render(t, Hh.exit1);
    show(h1.root, t < Hh.exit1 + 0.2);
    h2.render(t, Hh.exit2);
  };
});

// I · PARKED (the real recap text for drive #8, app/recap.py) over the parked car's screen
const Ip = TL.I_parked;
scene('parked', Ip.start, Ip.end, (s, root) => {
  el('div', { class: 'bg' }, root);
  const { cam, img } = dash2Layer(root);
  const shade = el('div', { class: 'bg', style: { opacity: '0.72' } }, root);
  const svg = el('svg', { class: 'over' }, root);
  const panel = el('div', { class: 'win', style: { left: '260px', top: '400px', width: '1400px', height: '330px', transformOrigin: '50% 50%' } }, root);
  el('div', { class: 'title' }, panel, '┌─ NTFY · PUSH ─┐');
  el('div', { style: { position: 'absolute', left: '44px', top: '46px', fontWeight: 800, fontSize: '38px' } }, panel, Ip.recap_title);
  Ip.recap_lines.forEach((line, i) => {
    el('div', { style: { position: 'absolute', left: '44px', top: `${126 + i * 66}px`, fontWeight: 500, fontSize: '30px', color: FG, whiteSpace: 'nowrap' } }, panel, line);
  });
  const head = new Headline(root, Ip.headline, []);
  const call = new Callout(svg, root, Ip.callout.text);
  s.render = (t) => {
    setImg(img, dash2Src(Ip.bg_dash2_src + (t - Ip.start)));
    cam.set(d2([705, 365]), [960, 540], 1.4);
    const p = prog(t, Ip.start, 0.4, OUT);
    setStyle(panel, { transform: `scale(${lerp(0.96, 1, p).toFixed(4)})`, opacity: p.toFixed(3) });
    head.render(t);
    call.render(t, Ip.callout.t, null, [1640, 470], [-10, 360]);
  };
});

// J · HOW IT WORKS
const J = TL.J_how;
scene('how', J.start, J.end, (s, root) => {
  el('div', { class: 'bg' }, root);
  const head = new Headline(root, J.headline, J.headline_breaks_after);
  const svg = el('svg', { class: 'over' }, root);
  const L = { // design layout, frame px: [x, y, w, h]
    car: [96, 420, 430, 124], api: [630, 420, 430, 124], voice: [1164, 420, 600, 124],
    neon: [630, 640, 430, 124], subs: [1164, 640, 600, 124], ntfy: [96, 860, 430, 124], eta: [1164, 860, 600, 124],
  };
  const mid = (id, side) => {
    const [x, y, w, h] = L[id];
    return { l: [x, y + h / 2], r: [x + w, y + h / 2], t: [x + w / 2, y], b: [x + w / 2, y + h] }[side];
  };
  const EDGES = {
    'car>api': [mid('car', 'r'), mid('api', 'l')],
    'api>voice': [[1060, 468], [1164, 468]],
    'voice>api': [[1164, 496], [1060, 496]],
    'api>neon': [mid('api', 'b'), mid('neon', 't')],
    'neon>subs': [[1060, 688], [1164, 688]],
    'subs>neon': [[1164, 716], [1060, 716]],
    'neon>eta': [[845, 764], [845, 922], [1164, 922]],
    'eta>voice': [[1764, 922], [1800, 922], [1800, 482], [1764, 482]],
    'api>ntfy': [[630, 530], [580, 530], [580, 922], [526, 922]],
  };
  const nodes = J.nodes.map((n) => {
    const [x, y, w, h] = L[n.id];
    const box = el('rect', { x, y, width: w, height: h, fill: PANEL, stroke: DIM, 'stroke-width': 1.5, pathLength: 100, 'stroke-dasharray': 100 }, svg);
    const label = el('div', { style: { position: 'absolute', left: `${x + 22}px`, top: `${y + (n.sub ? 20 : 42)}px`, fontWeight: 800, fontSize: '29px', letterSpacing: '0', whiteSpace: 'nowrap' } }, root);
    const sub = n.sub ? el('div', { style: { position: 'absolute', left: `${x + 22}px`, top: `${y + 70}px`, fontWeight: 500, fontSize: '22px', letterSpacing: '0.02em', color: DIM, whiteSpace: 'nowrap' } }, root) : null;
    return { n, box, label, sub };
  });
  const edges = Object.entries(EDGES).map(([k, pts]) => {
    const [a, b] = k.split('>');
    const line = el('polyline', { points: pts.map((p) => p.join(',')).join(' '), fill: 'none', stroke: DIM, 'stroke-width': 1.5, pathLength: 100, 'stroke-dasharray': 100 }, svg);
    const headDot = el('circle', { r: 4, fill: DIM }, svg);
    const t0 = Math.max(J.nodes.find((n) => n.id === a).t, J.nodes.find((n) => n.id === b).t) + 0.1;
    return { k, pts, line, head: headDot, t0 };
  });
  const packets = J.packets.map(([a, b, t0]) => ({ e: edges.find((x) => x.k === `${a}>${b}`), t0, sq: el('rect', { width: 8, height: 8, fill: ACCENT }, svg) }));
  const along = (pts, p) => {
    const segs = []; let total = 0;
    for (let i = 1; i < pts.length; i++) { const d = Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]); segs.push(d); total += d; }
    let at = p * total;
    for (let i = 0; i < segs.length; i++) {
      if (at <= segs[i]) { const q = at / segs[i]; return [lerp(pts[i][0], pts[i + 1][0], q), lerp(pts[i][1], pts[i + 1][1], q)]; }
      at -= segs[i];
    }
    return pts[pts.length - 1];
  };
  s.render = (t) => {
    head.render(t, J.exit);
    const out = J.exit != null && t >= J.exit ? prog(t, J.exit, 0.18, IN) : 0;
    setStyle(svg, { opacity: (1 - out).toFixed(3) });
    for (const x of nodes) {
      x.box.setAttribute('stroke-dashoffset', (100 * (1 - prog(t, x.n.t, 0.28, OUT))).toFixed(2));
      x.box.setAttribute('fill-opacity', prog(t, x.n.t + 0.1, 0.2).toFixed(2));
      const n = clamp(Math.floor((t - x.n.t - 0.12) * 1000 / 22) + 1, 0, x.n.label.length);
      setText(x.label, t >= x.n.t + 0.12 ? x.n.label.slice(0, n) : '');
      if (x.sub) setText(x.sub, t >= x.n.t + 0.3 ? x.n.sub.slice(0, clamp(Math.floor((t - x.n.t - 0.3) * 1000 / 12) + 1, 0, x.n.sub.length)) : '');
      for (const e of [x.label, x.sub]) if (e) setStyle(e, { opacity: (1 - out).toFixed(3) });
    }
    for (const e of edges) {
      const p = prog(t, e.t0, 0.28, OUT);
      e.line.setAttribute('stroke-dashoffset', (100 * (1 - p)).toFixed(2));
      const end = e.pts[e.pts.length - 1];
      e.head.setAttribute('cx', end[0]); e.head.setAttribute('cy', end[1]);
      e.head.setAttribute('opacity', p >= 1 ? '1' : '0');
    }
    for (const pk of packets) {
      // green packets travel each edge in 600 ms (LIN), in the order of the real flow
      const p = (t - pk.t0) / 0.6;
      const on = p >= 0 && p <= 1;
      pk.sq.setAttribute('opacity', on ? '1' : '0');
      if (on) { const [x, y] = along(pk.e.pts, p); pk.sq.setAttribute('x', x - 4); pk.sq.setAttribute('y', y - 4); }
    }
  };
});

// K · SAFE BY DESIGN
const K = TL.K_safe;
scene('safe', K.start, K.end, (s, root) => {
  el('div', { class: 'bg' }, root);
  const label = el('div', { class: 'callout-label dim', style: { left: '96px', top: '96px', fontSize: '26px' } }, root, `┌─ ${K.label} ─┐`);
  const lines = K.lines.map((L, i) => {
    const row = el('div', { style: { position: 'absolute', left: '96px', top: `${300 + i * 170}px`, fontWeight: 800, fontSize: '56px', letterSpacing: '-0.01em', whiteSpace: 'nowrap' } }, root);
    const tl = new TypeLine(row, '', {}, 'span');
    const done = el('span', { class: 'tag', style: { marginLeft: '28px', fontSize: '40px', fontWeight: 500, display: 'none' } }, row, '[DONE]');
    return { L, tl, done };
  });
  s.render = (t) => {
    setStyle(label, { opacity: prog(t, K.start, 0.24, OUT).toFixed(3) });
    for (const x of lines) {
      x.tl.render('> ' + x.L.text, t, x.L.t);
      setStyle(x.done, tagStyle(t, x.L.t + (x.L.text.length + 2) * 0.03 + 0.15));
    }
  };
});

// L · STACK FLICKER (constant black, only the text changes; no full-frame flashes)
const Ls = TL.L_stack;
scene('stack', Ls.start, Ls.end, (s, root) => {
  el('div', { class: 'bg' }, root);
  const word = el('div', { style: { position: 'absolute', left: '0', right: '0', top: '470px', textAlign: 'center', fontWeight: 800, fontSize: '120px', letterSpacing: '-0.01em', lineHeight: 1 } }, root);
  s.render = (t) => {
    const i = clamp(Math.floor((t - Ls.start) / (Ls.per_label_beats * BEAT)), 0, Ls.labels.length - 1);
    setText(word, Ls.labels[i]);
  };
});

// M · END CARD
const M = TL.M_end;
scene('end', M.start, TL.duration + 1, (s, root) => {
  el('div', { class: 'bg' }, root);
  const holder = el('div', { class: 'layer' }, root);
  const mark = new Mark(holder, 200);
  mark.place(960, 380, 200);
  const word = el('div', { style: { position: 'absolute', left: '0', right: '0', top: '530px', textAlign: 'center', fontWeight: 800, fontSize: '120px', letterSpacing: '-0.01em', lineHeight: 1 } }, root);
  const letters = [...M.wordmark].map((ch) => el('span', { style: { display: 'inline-block' } }, word, ch));
  const tag = new TypeLine(root, '', { position: 'absolute', left: '0', right: '0', top: '690px', textAlign: 'center', fontWeight: 500, fontSize: '36px' });
  const small = el('div', { style: { position: 'absolute', left: '0', right: '0', top: '790px', textAlign: 'center', fontWeight: 500, fontSize: '28px', letterSpacing: '0.06em', color: DIM } }, root, M.small);
  s.render = (t) => {
    mark.render(t, M.start, M.mark_speed);
    letters.forEach((sp, i) => setStyle(sp, wordStyle(t, M.wordmark_t + i * 0.04)));
    const st = tag.render(M.tagline, t, M.tagline_t);
    if (st.finished) setStyle(tag.cur, { visibility: blinkOn(t, M.tagline_t) ? 'visible' : 'hidden' });
    setStyle(small, wordStyle(t, M.small_t));
  };
});

// ── seek ────────────────────────────────────────────────────────────────────
const grain = el('canvas', { id: 'grain' }, stage);
stage.appendChild(capBox);
stage.appendChild(grain);
makeGrain(grain);
window.DURATION = TL.duration;
window.seek = async (t) => {
  for (const s of SCENES) {
    const on = t >= s.start && t < s.end;
    show(s.root, on);
    if (on) s.render(t);
  }
  renderCaptions(t);
  drawGrain(grain, frameOf(t));
  await document.fonts.ready;
  const waits = pending.splice(0);
  await Promise.all(waits);
  return true;
};
