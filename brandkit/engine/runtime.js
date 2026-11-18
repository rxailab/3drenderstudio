/* Shared runtime for ecommerce templates.
 * - Builds nothing by itself; templates call the helpers below.
 * - After layout, `TPL.finish()` measures the real rendered DOM and writes a
 *   JSON QA record into <script id="qa-result">, which the Python renderer
 *   reads back through `chrome --dump-dom`.
 * Coordinates are CSS px in the current canvas (1 CSS px == 1 output px).
 */
(function () {
  const TPL = {};
  window.TPL = TPL;

  TPL.job = window.JOB;
  TPL.W = TPL.job.canvas.width;
  TPL.H = TPL.job.canvas.height;
  TPL.root = null;
  TPL.leaders = []; // {id, label, x1,y1,x2,y2,...}
  TPL.notes = [];

  TPL.init = function () {
    const r = document.getElementById('canvas');
    r.style.width = TPL.W + 'px';
    r.style.height = TPL.H + 'px';
    r.setAttribute('lang', TPL.job.lang || 'en');
    document.documentElement.setAttribute('lang', TPL.job.lang || 'en');
    TPL.root = r;
    // map palette tokens to CSS vars
    const pal = TPL.job.palette || {};
    for (const k of Object.keys(pal)) r.style.setProperty('--' + k, pal[k]);
    return r;
  };

  TPL.asset = function (id) {
    const a = (TPL.job.assets || {})[id];
    if (!a) throw new Error('missing asset ' + id);
    return a;
  };

  /* el(tag, {attrs}, parent, text) */
  TPL.el = function (tag, attrs, parent, text) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (k === 'style') Object.assign(e.style, v);
      else if (k === 'class') e.className = v;
      else e.setAttribute(k, v);
    }
    if (text != null) e.textContent = text;
    (parent || TPL.root).appendChild(e);
    return e;
  };

  TPL.box = function (e, x, y, w, h) {
    e.style.position = 'absolute';
    e.style.left = x + 'px';
    e.style.top = y + 'px';
    if (w != null) e.style.width = w + 'px';
    if (h != null) e.style.height = h + 'px';
    return e;
  };

  /* Fit an asset (natural w,h) into a box, preserving aspect ratio (contain). */
  TPL.contain = function (a, bx, by, bw, bh, alignX = 0.5, alignY = 0.5) {
    const s = Math.min(bw / a.width, bh / a.height);
    const w = a.width * s, h = a.height * s;
    return { x: bx + (bw - w) * alignX, y: by + (bh - h) * alignY, w, h, scale: s };
  };
  /* Cover crop for explicit detail crops: returns image rect bigger than box. */
  TPL.cover = function (a, bx, by, bw, bh, focusX = 0.5, focusY = 0.5) {
    const s = Math.max(bw / a.width, bh / a.height);
    const w = a.width * s, h = a.height * s;
    return { x: bx - (w - bw) * focusX, y: by - (h - bh) * focusY, w, h, scale: s };
  };

  /* Place an <img>; qa attrs record crop policy + natural size. */
  TPL.img = function (id, assetId, r, opts = {}) {
    const a = TPL.asset(assetId);
    const i = TPL.el('img', {
      src: a.file, 'data-qa': id, 'data-kind': opts.kind || 'product_image',
      'data-asset': assetId, 'data-nw': a.width, 'data-nh': a.height,
      'data-crop': opts.crop || 'contain', 'data-allow': (opts.allow || []).join(','),
      'data-bleed': opts.bleed ? '1' : '', class: opts.class || ''
    }, opts.parent);
    TPL.box(i, r.x, r.y, r.w, r.h);
    if (opts.blend) i.style.mixBlendMode = opts.blend;
    return i;
  };

  /* Clip frame for explicit detail crops: frame box + cover image inside. */
  TPL.detailFrame = function (id, assetId, bx, by, bw, bh, opts = {}) {
    const f = TPL.el('div', { class: 'detail-frame ' + (opts.class || ''), 'data-qa': id + '_frame',
      'data-kind': 'shape', 'data-allow': (opts.allow || []).join(','), 'data-bleed': opts.bleed ? '1' : '' }, opts.parent);
    TPL.box(f, bx, by, bw, bh);
    const a = TPL.asset(assetId);
    const r = TPL.cover(a, 0, 0, bw, bh, opts.fx ?? 0.5, opts.fy ?? 0.5);
    const i = TPL.el('img', { src: a.file, 'data-qa': id, 'data-kind': 'detail_image', 'data-asset': assetId,
      'data-nw': a.width, 'data-nh': a.height, 'data-crop': 'explicit_detail_crop',
      'data-allow': [id + '_frame'].concat(opts.allow || []).join(','), 'data-inframe': '1' }, f);
    TPL.box(i, r.x, r.y, r.w, r.h);
    return { frame: f, img: i, scale: r.scale };
  };

  /* Text block with fixed width; height grows. tier: headline|important|secondary|meta */
  TPL.text = function (id, str, x, y, w, cls, opts = {}) {
    const e = TPL.el('div', { class: 'txt ' + cls, 'data-qa': id, 'data-kind': 'text',
      'data-tier': opts.tier || 'secondary', 'data-allow': (opts.allow || []).join(','),
      'data-maxlines': opts.maxLines || '' }, opts.parent, str);
    TPL.box(e, x, y, w, null);
    if (opts.align) e.style.textAlign = opts.align;
    return e;
  };
  TPL.rect = function (e) { const r = e.getBoundingClientRect(); return { x: r.left, y: r.top, w: r.width, h: r.height }; };

  /* SVG overlay for leader lines */
  TPL.svg = function () {
    if (TPL._svg) return TPL._svg;
    const s = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    s.setAttribute('width', TPL.W); s.setAttribute('height', TPL.H);
    s.setAttribute('viewBox', `0 0 ${TPL.W} ${TPL.H}`);
    s.style.position = 'absolute'; s.style.left = 0; s.style.top = 0; s.style.pointerEvents = 'none';
    s.setAttribute('class', 'overlay');
    TPL.root.appendChild(s);
    TPL._svg = s; return s;
  };
  TPL.svgEl = function (tag, attrs) {
    const e = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
    TPL.svg().appendChild(e); return e;
  };
  /* polyline leader; pts = [[x,y],...]; label id is allowed to touch. */
  TPL.leader = function (id, pts, labelId, anchorId) {
    const d = pts.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' ');
    TPL.svgEl('path', { d, class: 'leader', 'data-qa-line': id });
    TPL.leaders.push({ id, pts, label: labelId, anchor: anchorId });
  };
  TPL.anchorDot = function (id, x, y) {
    TPL.svgEl('circle', { cx: x, cy: y, r: 13, class: 'anchor-ring' });
    TPL.svgEl('circle', { cx: x, cy: y, r: 6, class: 'anchor-dot' });
  };

  TPL.watermark = function () {
    const d = TPL.job.demo; if (!d) return;
    const band = TPL.el('div', { class: 'demo-band', 'data-qa': 'demo_band', 'data-kind': 'shape', 'data-exempt': '1' });
    TPL.box(band, 0, TPL.H - 40, TPL.W, 40);
    TPL.el('span', {}, band, d.band_text);
    const wm = TPL.el('div', { class: 'demo-wm', 'data-exempt': '1' }, null, 'INTERNAL DEMO · NOT FOR PUBLICATION');
    wm.style.width = TPL.W + 'px'; wm.style.top = (TPL.H / 2 - 40) + 'px';
  };

  /* ---------- geometry helpers for leader routing ---------- */
  TPL.segIntersect = function (p1, p2, p3, p4) {
    const d = (a, b, c) => (c[0] - a[0]) * (b[1] - a[1]) - (b[0] - a[0]) * (c[1] - a[1]);
    const d1 = d(p3, p4, p1), d2 = d(p3, p4, p2), d3 = d(p1, p2, p3), d4 = d(p1, p2, p4);
    return ((d1 > 0 && d2 < 0) || (d1 < 0 && d2 > 0)) && ((d3 > 0 && d4 < 0) || (d3 < 0 && d4 > 0));
  };
  TPL.segRect = function (a, b, r, pad = 0) {
    const x0 = r.x - pad, y0 = r.y - pad, x1 = r.x + r.w + pad, y1 = r.y + r.h + pad;
    const inside = p => p[0] > x0 && p[0] < x1 && p[1] > y0 && p[1] < y1;
    if (inside(a) || inside(b)) return true;
    const c = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]];
    for (let i = 0; i < 4; i++) if (TPL.segIntersect(a, b, c[i], c[(i + 1) % 4])) return true;
    return false;
  };
  TPL.permutations = function (arr) {
    if (arr.length <= 1) return [arr.slice()];
    const out = [];
    arr.forEach((v, i) => { for (const p of TPL.permutations(arr.slice(0, i).concat(arr.slice(i + 1)))) out.push([v].concat(p)); });
    return out;
  };

  /* ---------- font verification (canvas width comparison) ---------- */
  function fontInUse(family, weight, sample) {
    const c = document.createElement('canvas').getContext('2d');
    const res = [];
    for (const fb of ['monospace', 'serif']) {
      c.font = `${weight} 64px ${fb}`; const w0 = c.measureText(sample).width;
      c.font = `${weight} 64px "${family}", ${fb}`; const w1 = c.measureText(sample).width;
      res.push(Math.abs(w1 - w0) > 0.5);
    }
    return res[0] && res[1];
  }

  /* ---------- QA measurement ---------- */
  TPL.finish = function () {
    const go = () => { try { TPL._measure(); } catch (e) { const s = document.createElement('script'); s.type = 'application/json'; s.id = 'qa-result'; s.textContent = JSON.stringify({issues: [{check: 'js_error', detail: String(e && e.stack || e)}]}); document.body.appendChild(s); } };
    if (document.readyState === 'complete') go(); else window.addEventListener('load', go);
  };
  TPL._measure = function () {
    const W = TPL.W, H = TPL.H, issues = [], els = [];
    const nodes = [...document.querySelectorAll('[data-qa]')];
    for (const n of nodes) {
      const r = TPL.rect(n);
      const cs = getComputedStyle(n);
      const rec = { id: n.dataset.qa, kind: n.dataset.kind, x: +r.x.toFixed(1), y: +r.y.toFixed(1), w: +r.w.toFixed(1), h: +r.h.toFixed(1),
        allow: (n.dataset.allow || '').split(',').filter(Boolean), bleed: !!n.dataset.bleed, exempt: !!n.dataset.exempt, inframe: !!n.dataset.inframe };
      if (rec.kind === 'text') {
        // use the union of rendered line boxes as the real text bounds
        const rg0 = document.createRange(); rg0.selectNodeContents(n);
        const qs = [...rg0.getClientRects()].filter(q => q.width > 0);
        if (qs.length) {
          const x0 = Math.min(...qs.map(q => q.left)), y0 = r.y, x1 = Math.max(...qs.map(q => q.right)), y1 = r.y + r.h;
          rec.box = [rec.x, rec.y, rec.w, rec.h];
          rec.x = +x0.toFixed(1); rec.y = +y0.toFixed(1); rec.w = +(x1 - x0).toFixed(1); rec.h = +(y1 - y0).toFixed(1);
        }
        rec.text = n.textContent; rec.font_size = parseFloat(cs.fontSize); rec.font_family = cs.fontFamily; rec.font_weight = cs.fontWeight;
        rec.tier = n.dataset.tier;
        const range = document.createRange(); range.selectNodeContents(n);
        // count visual lines: merge line boxes whose vertical extents overlap (mixed font sizes on one line)
        const qsl = [...range.getClientRects()].filter(q => q.height > 0).sort((a, b) => a.top - b.top);
        let lines = 0, curBottom = -1e9;
        for (const q of qsl) { const mid = (q.top + q.bottom) / 2; if (mid > curBottom) { lines++; curBottom = q.bottom; } else curBottom = Math.max(curBottom, q.bottom); }
        rec.lines = lines;
        rec.equiv_px_at_360 = +(rec.font_size * 360 / W).toFixed(2);
        rec.overflow_x = n.scrollWidth > n.clientWidth + 1;
        if (rec.overflow_x) issues.push({ check: 'text_overflow', id: rec.id, detail: `scrollWidth ${n.scrollWidth} > ${n.clientWidth}` });
        const ml = +n.dataset.maxlines; if (ml && rec.lines > ml) issues.push({ check: 'text_max_lines', id: rec.id, detail: `${rec.lines} lines > ${ml}` });
        const minEq = { headline: 14, important: 12, secondary: 10, meta: 0 }[rec.tier] ?? 10;
        if (rec.equiv_px_at_360 < minEq) issues.push({ check: 'min_size_at_360', id: rec.id, detail: `${rec.equiv_px_at_360}px < ${minEq}px` });
      }
      if (n.dataset.feather) rec.feather = JSON.parse(n.dataset.feather);   // project change: feathered frame edges, for the renderer's part-cut check
      if (n.tagName === 'IMG') {
        const nw = +n.dataset.nw, nh = +n.dataset.nh;
        rec.asset = n.dataset.asset; rec.crop = n.dataset.crop; rec.natural = [nw, nh];
        const lw_ = n.offsetWidth, lh_ = n.offsetHeight;
        rec.layout = [parseFloat(n.style.left) || 0, parseFloat(n.style.top) || 0, lw_, lh_];
        rec.rotate = +(n.dataset.rotate || 0);
        rec.blend = n.style.mixBlendMode || '';
        rec.scale = +(lw_ / nw).toFixed(3);
        rec.aspect_error = +Math.abs((lw_ / lh_) / (nw / nh) - 1).toFixed(4);
        rec.loaded = n.complete && n.naturalWidth === nw && n.naturalHeight === nh;
        if (!rec.loaded) issues.push({ check: 'image_loaded', id: rec.id, detail: `natural ${n.naturalWidth}x${n.naturalHeight} vs manifest ${nw}x${nh}` });
        if (rec.aspect_error > 0.005) issues.push({ check: 'product_distortion', id: rec.id, detail: `aspect error ${rec.aspect_error}` });
        if (rec.scale > 1.5) issues.push({ check: 'upscale', id: rec.id, severity: 'fail', detail: `scale ${rec.scale}` });
        else if (rec.scale > 1.25) issues.push({ check: 'upscale', id: rec.id, severity: 'warn', detail: `scale ${rec.scale}` });
        if (rec.crop === 'contain' && !rec.bleed && rec.rotate) {
          const q = n.dataset.quad ? JSON.parse(n.dataset.quad) : ((TPL.layoutInfo || {}).hero_content_quad || []);   // per-image quad (multi-rotated layouts) or the single hero quad
          if (q.some(p => p[0] < -0.5 || p[1] < -0.5 || p[0] > W + 0.5 || p[1] > H + 0.5)) issues.push({ check: 'product_cropped', id: rec.id, detail: 'rotated content quad exceeds canvas' });
        } else if (rec.crop === 'contain' && !rec.bleed) {
          // visible-portion check: contained product must be fully inside canvas
          if (r.x < -0.5 || r.y < -0.5 || r.x + r.w > W + 0.5 || r.y + r.h > H + 0.5) issues.push({ check: 'product_cropped', id: rec.id, detail: 'contain image exceeds canvas' });
        }
      }
      if (!rec.bleed && !rec.exempt && !rec.inframe && rec.kind !== 'product_image') {
        if (r.x < -0.5 || r.y < -0.5 || r.x + r.w > W + 0.5 || r.y + r.h > H + 0.5) issues.push({ check: 'out_of_canvas', id: rec.id, detail: JSON.stringify([rec.x, rec.y, rec.w, rec.h]) });
      }
      els.push(rec);
    }
    // safe-area for text
    const safe = TPL.job.min_margin ?? 60;   // hard minimum; templates aim for job.safe_margin
    for (const e of els) if (e.kind === 'text' && !e.exempt) {
      if (e.x < safe - 1 || e.y < safe - 1 || e.x + e.w > W - safe + 1 || e.y + e.h > H - safe - (TPL.job.demo ? 40 : 0) + 1) issues.push({ check: 'safe_margin', id: e.id, detail: `text box outside ${safe}px safe area` });
    }
    // pairwise overlap (text vs everything; detail frames vs each other)
    const byId = Object.fromEntries(els.map(e => [e.id, e]));
    const inter = (a, b) => Math.max(0, Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x)) * Math.max(0, Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y));
    const overlaps = [];
    for (let i = 0; i < els.length; i++) for (let j = i + 1; j < els.length; j++) {
      const a = els[i], b = els[j];
      if (a.exempt || b.exempt || a.inframe || b.inframe) continue;
      const relevant = a.kind === 'text' || b.kind === 'text' || (a.kind === 'shape' && b.kind === 'shape');
      // text over a blended product photo: bounding boxes always intersect; real test is pixel-level (renderer)
      if ((a.kind === 'product_image' && b.kind === 'text') || (b.kind === 'product_image' && a.kind === 'text')) { overlaps.push({ a: a.id, b: b.id, area: Math.round(inter(a, b)), allowed: 'pixel_check' }); continue; }
      if (!relevant) continue;
      const area = inter(a, b); if (area < 1) continue;
      const allowed = a.allow.includes(b.id) || b.allow.includes(a.id);
      overlaps.push({ a: a.id, b: b.id, area: Math.round(area), allowed });
      if (!allowed) issues.push({ check: 'overlap', id: `${a.id}×${b.id}`, detail: `${Math.round(area)}px²` });
    }
    // leader lines must not cross text other than their own label, nor each other
    const texts = els.filter(e => e.kind === 'text' && !e.exempt);
    for (const L of TPL.leaders) {
      for (let k = 0; k + 1 < L.pts.length; k++) {
        for (const t of texts) {
          if (t.id.startsWith(L.label)) continue;
          if (TPL.segRect(L.pts[k], L.pts[k + 1], t, 4)) issues.push({ check: 'leader_crosses_text', id: `${L.id}×${t.id}` });
        }
      }
    }
    for (let i = 0; i < TPL.leaders.length; i++) for (let j = i + 1; j < TPL.leaders.length; j++) {
      const A = TPL.leaders[i].pts, B = TPL.leaders[j].pts;
      for (let a = 0; a + 1 < A.length; a++) for (let b = 0; b + 1 < B.length; b++)
        if (TPL.segIntersect(A[a], A[a + 1], B[b], B[b + 1])) issues.push({ check: 'leader_crossing', id: `${TPL.leaders[i].id}×${TPL.leaders[j].id}` });
    }
    const fams = [['Noto Sans Display', 700, 'Hamburgefonstiv'], ['Noto Sans', 400, 'Hamburgefonstiv'], ['Noto Sans', 700, 'Hamburgefonstiv'], ['Noto Sans CJK SC', 700, 'Hamburg控制部件'], ['Noto Sans CJK SC', 400, 'Hamburg控制部件']];
    const fonts = fams.map(([f, w, s]) => ({ family: f, weight: w, available: fontInUse(f, w, s) }));
    for (const f of fonts) if (!f.available) issues.push({ check: 'font_missing', id: f.family + ' ' + f.weight });
    const out = { template: TPL.job.template_id, variant: TPL.job.variant, canvas: [W, H], lang: TPL.job.lang, elements: els, leaders: TPL.leaders, overlaps, issues, fonts, notes: TPL.notes, layout: TPL.layoutInfo || {} };
    const s = document.createElement('script'); s.type = 'application/json'; s.id = 'qa-result';
    s.textContent = JSON.stringify(out);
    document.body.appendChild(s);
  };
})();
