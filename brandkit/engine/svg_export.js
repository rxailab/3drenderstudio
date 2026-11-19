/* Editable layered SVG export of a laid-out brand-kit page (loaded only by brandkit_render.export_svg).
 *
 * After the page is laid out and measured (TPL._measure, wrapped below), walk #canvas in paint order and write one
 * self-contained SVG into <script type="image/svg+xml" id="svg-result">:
 *   - every element is its own <g id data-name inkscape:label> (named from data-qa, else its class / words),
 *   - boxes become rects / circles / paths with their solid, linear- and radial-gradient fills, borders, box-shadows
 *     (rings as strokes, soft shadows through a blur filter), overflow clips, CSS masks, filters, opacity and blending,
 *   - pictures become <image> layers with the file embedded as a data: URI,
 *   - the page's own SVG (leaders, nodes, rings, icons) is copied with its computed paint inlined,
 *   - text stays text: one <text> per element, one <tspan> per rendered line and style run, at the exact positions
 *     the browser broke and placed the lines; fonts are declared with @font-face pointing at fonts/<file>.
 * Coordinates are measured with every CSS transform switched off; the transforms are put back as group transforms.
 */
(function () {
  'use strict';
  const NS = 'http://www.w3.org/2000/svg';
  const OPTS = window.SVG_EXPORT_OPTIONS || {};
  const SVGX = window.SVGX = {};

  /* ---------------- small helpers ---------------- */
  const esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  const n = v => { const r = Math.round(v * 100) / 100; return Object.is(r, -0) ? '0' : String(r); };
  const px = v => parseFloat(v) || 0;
  const attrs = o => Object.entries(o).filter(([, v]) => v != null && v !== '').map(([k, v]) => ` ${k}="${esc(v)}"`).join('');

  /* split at a separator outside parentheses (and quotes) */
  function splitTop(s, sep) {
    const out = []; let depth = 0, cur = '', q = null;
    for (const ch of String(s)) {
      if (q) { cur += ch; if (ch === q) q = null; continue; }
      if (ch === '"' || ch === "'") { q = ch; cur += ch; continue; }
      if (ch === '(') depth++;
      if (ch === ')') depth--;
      if (depth === 0 && (sep === ' ' ? /\s/.test(ch) : ch === sep)) { if (cur.trim()) out.push(cur.trim()); cur = ''; continue; }
      cur += ch;
    }
    if (cur.trim()) out.push(cur.trim());
    return out;
  }

  /* ---------------- colours ---------------- */
  let probe = null;
  const colorCache = new Map();
  function parseColor(s) {
    if (s == null) return null;
    s = String(s).trim();
    if (colorCache.has(s)) return colorCache.get(s);
    let c = null, m;
    if (s === 'transparent' || s === 'none' || s === '') c = { r: 0, g: 0, b: 0, a: 0 };
    else if ((m = /^rgba?\(([^)]*)\)$/i.exec(s))) {
      const p = m[1].split(/[\s,\/]+/).filter(Boolean).map(v => v.endsWith('%') ? parseFloat(v) * 2.55 : parseFloat(v));
      c = { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? (m[1].split(/[\s,\/]+/).filter(Boolean)[3].endsWith('%') ? p[3] / 255 : p[3]) : 1 };
    } else if ((m = /^color\(srgb\s+([^)]*)\)$/i.exec(s))) {
      const [rgb, alpha] = m[1].split('/');
      const p = rgb.trim().split(/\s+/).map(v => v.endsWith('%') ? parseFloat(v) / 100 : parseFloat(v));
      c = { r: p[0] * 255, g: p[1] * 255, b: p[2] * 255, a: alpha != null ? (alpha.trim().endsWith('%') ? parseFloat(alpha) / 100 : parseFloat(alpha)) : 1 };
    } else if ((m = /^#([0-9a-f]{3,8})$/i.exec(s))) {
      let h = m[1]; if (h.length <= 4) h = h.split('').map(x => x + x).join('');
      c = { r: parseInt(h.slice(0, 2), 16), g: parseInt(h.slice(2, 4), 16), b: parseInt(h.slice(4, 6), 16), a: h.length === 8 ? parseInt(h.slice(6, 8), 16) / 255 : 1 };
    } else {
      // anything else (a name, color-mix(), another space): let the browser resolve it
      if (!probe) { probe = document.createElement('i'); probe.style.display = 'none'; document.body.appendChild(probe); }
      probe.style.color = ''; probe.style.color = s;
      const got = getComputedStyle(probe).color;
      c = got && got !== s && probe.style.color ? parseColor(got) : null;
    }
    if (c) for (const k of ['r', 'g', 'b']) c[k] = Math.max(0, Math.min(255, isNaN(c[k]) ? 0 : c[k]));
    if (c && isNaN(c.a)) c.a = 1;
    colorCache.set(s, c);
    return c;
  }
  const hex = c => '#' + [c.r, c.g, c.b].map(v => Math.round(v).toString(16).padStart(2, '0')).join('');
  /* a paint as attributes: fill="#rrggbb" fill-opacity="a" */
  function paint(name, color, extraAlpha = 1) {
    const c = typeof color === 'string' ? parseColor(color) : color;
    if (!c || c.a * extraAlpha <= 0) return { [name]: 'none' };
    const a = c.a * extraAlpha;
    return { [name]: hex(c), [name + '-opacity']: a < 0.999 ? n3(a) : null };
  }
  const n3 = v => String(Math.round(v * 1000) / 1000);

  /* ---------------- document state for one export ---------------- */
  let defs = [], defIds = new Set(), uid = 0, names = new Map(), C = null;
  const newId = p => 'sx-' + p + (++uid);
  function layerId(base) {
    let id = String(base || 'layer').replace(/[^A-Za-z0-9_.-]+/g, '-').replace(/^-+|-+$/g, '') || 'layer';
    if (!/^[A-Za-z_]/.test(id)) id = 'l-' + id;
    const k = names.get(id) || 0; names.set(id, k + 1);
    return k ? id + '-' + (k + 1) : id;
  }
  function group(name, label, extra, inner) {
    if (!inner) return '';
    const id = layerId(name);
    return `<g${attrs({ id, 'data-name': label || name, 'inkscape:label': label || name, ...extra })}>${inner}</g>`;
  }

  /* ---------------- pictures as data: URIs ---------------- */
  const dataCache = new Map();
  function sniff(bytes, url) {
    if (bytes.startsWith('\x89PNG')) return 'image/png';
    if (bytes.startsWith('\xFF\xD8')) return 'image/jpeg';
    if (bytes.startsWith('GIF8')) return 'image/gif';
    if (bytes.startsWith('RIFF') && bytes.slice(8, 12) === 'WEBP') return 'image/webp';
    if (/^\s*</.test(bytes.slice(0, 200))) return 'image/svg+xml';
    const ext = (/\.([a-z0-9]+)(?:[?#]|$)/i.exec(url) || [])[1] || '';
    return { jpg: 'image/jpeg', jpeg: 'image/jpeg', webp: 'image/webp', gif: 'image/gif', svg: 'image/svg+xml' }[ext.toLowerCase()] || 'image/png';
  }
  function dataURI(url, imgEl) {
    if (!url || url.startsWith('data:')) return url;
    if (dataCache.has(url)) return dataCache.get(url);
    let out = null;
    try {   // the file's own bytes (file:// needs --allow-file-access-from-files); synchronous so the export stays one pass
      const x = new XMLHttpRequest();
      x.open('GET', url, false);
      x.overrideMimeType('text/plain; charset=x-user-defined');
      x.send();
      if ((x.status === 200 || x.status === 0) && x.responseText) {
        const s = x.responseText, parts = [];
        for (let i = 0; i < s.length; i += 0x8000) {
          const codes = new Array(Math.min(0x8000, s.length - i));
          for (let j = 0; j < codes.length; j++) codes[j] = s.charCodeAt(i + j) & 0xff;
          parts.push(String.fromCharCode.apply(null, codes));
        }
        const bin = parts.join('');
        out = 'data:' + sniff(bin.slice(0, 16), url) + ';base64,' + btoa(bin);
      }
    } catch (e) { out = null; }
    if (!out) {   // a loaded picture drawn into a canvas (re-encoded as PNG)
      const img = imgEl && imgEl.complete ? imgEl : [...document.images].find(i => i.complete && (i.currentSrc || i.src) === url);
      if (img && img.naturalWidth) {
        try {
          const c = document.createElement('canvas'); c.width = img.naturalWidth; c.height = img.naturalHeight;
          c.getContext('2d').drawImage(img, 0, 0); out = c.toDataURL('image/png');
        } catch (e) { out = null; }
      }
    }
    dataCache.set(url, out || url);   // left as a link: the renderer embeds it from disk
    return out || url;
  }

  /* ---------------- geometry: rounded boxes ---------------- */
  function radii(cs, w, h) {
    const corner = v => { const p = String(v).split(/\s+/); const a = p[0], b = p[1] || p[0];
      return [a.endsWith('%') ? px(a) / 100 * w : px(a), b.endsWith('%') ? px(b) / 100 * h : px(b)]; };
    const r = [corner(cs.borderTopLeftRadius), corner(cs.borderTopRightRadius), corner(cs.borderBottomRightRadius), corner(cs.borderBottomLeftRadius)];
    let f = 1;
    const fit = (len, sum) => { if (sum > len && sum > 0) f = Math.min(f, len / sum); };
    fit(w, r[0][0] + r[1][0]); fit(w, r[3][0] + r[2][0]); fit(h, r[0][1] + r[3][1]); fit(h, r[1][1] + r[2][1]);
    return r.map(([a, b]) => [a * f, b * f]);
  }
  const zeroR = r => r.every(([a, b]) => a < 0.01 || b < 0.01);
  /* grow (d > 0) or shrink a box's radii, as CSS does for spread and borders */
  const adjustR = (r, dx, dy = dx) => r.map(([a, b]) => (a > 0 && b > 0 ? [Math.max(0, a + dx), Math.max(0, b + dy)] : [0, 0]));
  function shapePath(x, y, w, h, r) {
    const [tl, tr, br, bl] = r;
    return `M${n(x + tl[0])} ${n(y)}H${n(x + w - tr[0])}` + (tr[0] ? `A${n(tr[0])} ${n(tr[1])} 0 0 1 ${n(x + w)} ${n(y + tr[1])}` : '') +
      `V${n(y + h - br[1])}` + (br[0] ? `A${n(br[0])} ${n(br[1])} 0 0 1 ${n(x + w - br[0])} ${n(y + h)}` : '') +
      `H${n(x + bl[0])}` + (bl[0] ? `A${n(bl[0])} ${n(bl[1])} 0 0 1 ${n(x)} ${n(y + h - bl[1])}` : '') +
      `V${n(y + tl[1])}` + (tl[0] ? `A${n(tl[0])} ${n(tl[1])} 0 0 1 ${n(x + tl[0])} ${n(y)}` : '') + 'Z';
  }
  /* the simplest SVG element for a (rounded) box: rect, circle, ellipse or path */
  function shape(x, y, w, h, r, extra = {}) {
    if (w <= 0 || h <= 0) return '';
    if (!r || zeroR(r)) return `<rect${attrs({ x: n(x), y: n(y), width: n(w), height: n(h), ...extra })}/>`;
    const same = r.every(([a, b]) => Math.abs(a - r[0][0]) < 0.01 && Math.abs(b - r[0][1]) < 0.01);
    if (same) {
      const [rx, ry] = r[0];
      if (rx >= w / 2 - 0.01 && ry >= h / 2 - 0.01) {
        if (Math.abs(w - h) < 0.01) return `<circle${attrs({ cx: n(x + w / 2), cy: n(y + h / 2), r: n(w / 2), ...extra })}/>`;
        return `<ellipse${attrs({ cx: n(x + w / 2), cy: n(y + h / 2), rx: n(w / 2), ry: n(h / 2), ...extra })}/>`;
      }
      return `<rect${attrs({ x: n(x), y: n(y), width: n(w), height: n(h), rx: n(rx), ry: Math.abs(rx - ry) > 0.01 ? n(ry) : null, ...extra })}/>`;
    }
    return `<path${attrs({ d: shapePath(x, y, w, h, r), ...extra })}/>`;
  }
  function clipDef(inner) { const id = newId('clip'); defs.push(`<clipPath id="${id}">${inner}</clipPath>`); return `url(#${id})`; }

  /* ---------------- gradients ---------------- */
  function evalLen(tok, L) {
    tok = tok.trim();
    if (/^calc\(/i.test(tok)) {
      const expr = tok.slice(5, -1).replace(/(-?[\d.]+)%/g, (_, v) => `(${parseFloat(v) * L / 100})`).replace(/(-?[\d.]+)px/g, '($1)');
      try { return Function('"use strict";return (' + expr + ')')(); } catch (e) { return 0; }
    }
    if (tok.endsWith('%')) return parseFloat(tok) * L / 100;
    return parseFloat(tok) || 0;
  }
  const isLen = t => /^(-?[\d.]+(px|%)?|calc\(.*\))$/i.test(t);
  /* colour stops with their positions in px along a line of length L (CSS defaulting rules) */
  function stops(parts, L) {
    const out = [];
    for (const p of parts) {
      const toks = splitTop(p, ' ');
      const colorTok = toks.filter(t => !isLen(t)).join(' ');
      const lens = toks.filter(isLen).map(t => evalLen(t, L));
      if (!colorTok) continue;              // an interpolation hint: ignored
      const c = parseColor(colorTok) || { r: 0, g: 0, b: 0, a: 0 };
      if (!lens.length) out.push({ c, p: null });
      for (const l of lens) out.push({ c, p: l });
    }
    if (!out.length) return out;
    if (out[0].p == null) out[0].p = 0;
    if (out[out.length - 1].p == null) out[out.length - 1].p = L;
    let maxSoFar = -Infinity;
    for (let i = 0; i < out.length; i++) {
      if (out[i].p == null) {
        let j = i; while (out[j].p == null) j++;
        const a = out[i - 1].p, b = out[j].p;
        for (let k = i; k < j; k++) out[k].p = a + (b - a) * (k - i + 1) / (j - i + 1);
      }
      maxSoFar = Math.max(maxSoFar, out[i].p); out[i].p = maxSoFar;
    }
    return out;
  }
  /* CSS interpolates premultiplied; SVG does not: transparent ends take their neighbour's colour and partly
     transparent spans between different colours get extra samples */
  function svgStops(list, p0, p1, toMask) {
    const span = p1 - p0 || 1, out = [];
    const emit = (p, c) => out.push(`<stop offset="${n3(Math.max(0, Math.min(1, (p - p0) / span)))}"${attrs(toMask
      ? { 'stop-color': '#ffffff', 'stop-opacity': c.a < 0.999 ? n3(c.a) : null }
      : { 'stop-color': hex(c), 'stop-opacity': c.a < 0.999 ? n3(c.a) : null })}/>`);
    if (list.length === 1) { emit(p0, list[0].c); emit(p1, list[0].c); return out.join(''); }
    for (let i = 0; i + 1 < list.length; i++) {
      const A = list[i], B = list[i + 1];
      let ca = { ...A.c }, cb = { ...B.c };
      if (ca.a === 0) Object.assign(ca, { r: cb.r, g: cb.g, b: cb.b });
      if (cb.a === 0) Object.assign(cb, { r: ca.r, g: ca.g, b: ca.b });
      emit(A.p, ca);
      const differ = Math.abs(ca.a - cb.a) > 0.01 && (Math.abs(ca.r - cb.r) + Math.abs(ca.g - cb.g) + Math.abs(ca.b - cb.b) > 3) && B.p > A.p;
      if (differ && !toMask) for (let k = 1; k < 8; k++) {
        const t = k / 8, a = ca.a * (1 - t) + cb.a * t;
        const mix = ch => (ca[ch] * ca.a * (1 - t) + cb[ch] * cb.a * t) / (a || 1);
        emit(A.p + (B.p - A.p) * t, { r: mix('r'), g: mix('g'), b: mix('b'), a });
      }
      emit(B.p, cb);
    }
    return out.join('');
  }
  /* a CSS gradient over the box (bx,by,w,h) as an SVG gradient; returns 'url(#id)' or a plain colour */
  function gradient(str, bx, by, w, h, toMask) {
    const m = /^(repeating-)?(linear|radial)-gradient\(([\s\S]*)\)$/i.exec(str.trim());
    if (!m) return null;
    const repeating = !!m[1], kind = m[2].toLowerCase();
    let parts = splitTop(m[3], ',');
    const id = newId(kind[0] + 'g');
    if (kind === 'linear') {
      let dx = 0, dy = 1;   // to bottom
      const first = parts[0].trim();
      if (/^to\s/i.test(first)) {
        const w_ = first.toLowerCase();
        const sx = /right/.test(w_) ? 1 : /left/.test(w_) ? -1 : 0, sy = /bottom/.test(w_) ? 1 : /top/.test(w_) ? -1 : 0;
        if (sx && sy) { dx = sx * h; dy = sy * w; } else { dx = sx; dy = sy; }
        parts = parts.slice(1);
      } else if (/^-?[\d.]+(deg|rad|turn|grad)$/i.test(first)) {
        let a = parseFloat(first);
        if (/rad$/i.test(first)) a = a * 180 / Math.PI; else if (/turn$/i.test(first)) a *= 360; else if (/grad$/i.test(first)) a *= 0.9;
        const t = a * Math.PI / 180; dx = Math.sin(t); dy = -Math.cos(t);
        parts = parts.slice(1);
      }
      const len = Math.hypot(dx, dy) || 1; dx /= len; dy /= len;
      const L = Math.abs(w * dx) + Math.abs(h * dy);
      const list = stops(parts, L);
      if (!list.length) return null;
      const cx = bx + w / 2, cy = by + h / 2, sx0 = cx - dx * L / 2, sy0 = cy - dy * L / 2;
      let p0 = repeating ? list[0].p : Math.min(0, list[0].p), p1 = repeating ? list[list.length - 1].p : Math.max(L, list[list.length - 1].p);
      if (p1 - p0 < 1e-6) { p0 = 0; p1 = L || 1; }
      defs.push(`<linearGradient${attrs({ id, gradientUnits: 'userSpaceOnUse', x1: n(sx0 + dx * p0), y1: n(sy0 + dy * p0), x2: n(sx0 + dx * p1), y2: n(sy0 + dy * p1),
        spreadMethod: repeating ? 'repeat' : null })}>${svgStops(list, p0, p1, toMask)}</linearGradient>`);
      return `url(#${id})`;
    }
    // radial
    let shapeKind = 'ellipse', size = 'farthest-corner', pos = ['50%', '50%'], sizeLens = null;
    const head = splitTop(parts[0], ' ');
    if (head.length && (/^(circle|ellipse|closest-side|closest-corner|farthest-side|farthest-corner|at)$/i.test(head[0]) || isLen(head[0]))) {
      parts = parts.slice(1);
      const at = head.findIndex(t => t.toLowerCase() === 'at');
      const before = at >= 0 ? head.slice(0, at) : head, after = at >= 0 ? head.slice(at + 1) : [];
      for (const t of before) {
        if (/^(circle|ellipse)$/i.test(t)) shapeKind = t.toLowerCase();
        else if (/^(closest|farthest)-/i.test(t)) size = t.toLowerCase();
        else if (isLen(t)) (sizeLens = sizeLens || []).push(t);
      }
      if (sizeLens && sizeLens.length === 1 && !before.some(t => /^ellipse$/i.test(t))) shapeKind = 'circle';
      if (after.length) {
        let a = after.slice(0, 2);
        if (a.length === 1) a = /^(top|bottom)$/i.test(a[0]) ? ['center', a[0]] : [a[0], 'center'];
        if (/^(top|bottom)$/i.test(a[0]) || /^(left|right)$/i.test(a[1])) a = [a[1], a[0]];
        pos = a;
      }
    }
    const kw = { left: '0%', center: '50%', right: '100%', top: '0%', bottom: '100%' };
    const cx = evalLen(kw[pos[0]] || pos[0], w), cy = evalLen(kw[pos[1]] || pos[1], h);
    const l = cx, r = w - cx, t = cy, b = h - cy;
    const corners = [[l, t], [r, t], [r, b], [l, b]].map(([a, c]) => [a, c, Math.hypot(a, c)]);
    let rx, ry;
    if (sizeLens) {
      rx = evalLen(sizeLens[0], w); ry = shapeKind === 'circle' ? rx : evalLen(sizeLens[1] || sizeLens[0], h);
    } else if (shapeKind === 'circle') {
      rx = ry = size === 'closest-side' ? Math.min(l, r, t, b) : size === 'farthest-side' ? Math.max(l, r, t, b)
        : size === 'closest-corner' ? Math.min(...corners.map(c => c[2])) : Math.max(...corners.map(c => c[2]));
    } else {
      const closest = size.startsWith('closest');
      const sxr = closest ? Math.min(l, r) : Math.max(l, r), syr = closest ? Math.min(t, b) : Math.max(t, b);
      if (size.endsWith('side')) { rx = sxr; ry = syr; } else {
        const c = corners.slice().sort((p, q) => p[2] - q[2])[closest ? 0 : 3];
        const ratio = sxr > 0 ? syr / sxr : 1;
        rx = ratio > 0 ? Math.hypot(c[0], c[1] / ratio) : c[0]; ry = rx * ratio;
      }
    }
    if (!(rx > 0) || !(ry > 0)) return null;
    const list = stops(parts, rx).map(s => ({ ...s, p: Math.max(0, s.p) }));
    if (!list.length) return null;
    const p1 = Math.max(rx, list[list.length - 1].p);
    const X = bx + cx, Y = by + cy;
    defs.push(`<radialGradient${attrs({ id, gradientUnits: 'userSpaceOnUse', cx: n(X), cy: n(Y), r: n(p1), fx: n(X), fy: n(Y),
      gradientTransform: Math.abs(ry / rx - 1) > 1e-4 ? `translate(${n(X)} ${n(Y)}) scale(1 ${n3(ry / rx)}) translate(${n(-X)} ${n(-Y)})` : null })}>` +
      `${svgStops(list, 0, p1, toMask)}</radialGradient>`);
    return `url(#${id})`;
  }

  /* ---------------- shadows ---------------- */
  function parseShadows(str, allowSpread) {
    if (!str || str === 'none') return [];
    return splitTop(str, ',').map(one => {
      const toks = splitTop(one, ' ');
      const inset = toks.includes('inset');
      const lens = toks.filter(t => /^-?[\d.]+(px)?$/.test(t)).map(px);
      const color = toks.filter(t => t !== 'inset' && !/^-?[\d.]+(px)?$/.test(t)).join(' ') || 'black';
      return { inset, x: lens[0] || 0, y: lens[1] || 0, blur: lens[2] || 0, spread: allowSpread ? lens[3] || 0 : 0, color: parseColor(color) };
    }).filter(s => s.color && s.color.a > 0);
  }
  function blurFilter(sigma, region) {
    const id = newId('blur');
    defs.push(`<filter${attrs({ id, filterUnits: 'userSpaceOnUse', x: n(region[0]), y: n(region[1]), width: n(region[2]), height: n(region[3]),
      'color-interpolation-filters': 'sRGB' })}><feGaussianBlur stdDeviation="${n(sigma)}"/></filter>`);
    return `url(#${id})`;
  }
  /* box-shadows of a w×h box with radii r: outer ones (under the background) and inset ones (over it) */
  function boxShadows(cs, w, h, r, inset) {
    let out = '';
    const list = parseShadows(cs.boxShadow, true).filter(s => s.inset === inset).reverse();   // the first listed is on top
    for (const s of list) {
      const fillA = paint('fill', s.color);
      if (!s.inset && !s.blur && !s.x && !s.y && s.spread > 0) {          // a ring around the box: one stroke
        const d = s.spread / 2;
        out += shape(-d, -d, w + 2 * d, h + 2 * d, adjustR(r, d), { fill: 'none', ...paint('stroke', s.color), 'stroke-width': n(s.spread), 'data-name': 'ring' });
        continue;
      }
      if (s.inset && !s.blur && !s.x && !s.y && s.spread > 0) {           // a ring inside the edge
        const d = s.spread / 2;
        out += shape(d, d, w - 2 * d, h - 2 * d, adjustR(r, -d), { fill: 'none', ...paint('stroke', s.color), 'stroke-width': n(s.spread), 'data-name': 'inner ring' });
        continue;
      }
      const sg = s.blur / 2, pad = 3 * sg + 2;
      if (!s.inset) {
        const ex = s.spread;
        const body = shape(s.x - ex, s.y - ex, w + 2 * ex, h + 2 * ex, adjustR(r, ex), fillA);
        // the shadow shows only outside the box
        const region = [Math.min(0, s.x - ex) - pad, Math.min(0, s.y - ex) - pad, w + Math.abs(s.x) + 2 * Math.abs(ex) + 2 * pad, h + Math.abs(s.y) + 2 * Math.abs(ex) + 2 * pad];
        const mid = newId('mask');
        defs.push(`<mask id="${mid}" maskUnits="userSpaceOnUse" x="${n(region[0])}" y="${n(region[1])}" width="${n(region[2])}" height="${n(region[3])}">` +
          `<rect x="${n(region[0])}" y="${n(region[1])}" width="${n(region[2])}" height="${n(region[3])}" fill="#fff"/>${shape(0, 0, w, h, r, { fill: '#000' })}</mask>`);
        out += `<g${attrs({ 'data-name': 'shadow', mask: `url(#${mid})` })}><g${attrs({ filter: sg > 0 ? blurFilter(sg, region) : null })}>${body}</g></g>`;
      } else {
        // inset: the box minus the box moved by the offset and shrunk by the spread, kept inside the box
        const ins = s.spread;
        const big = 3 * sg + Math.abs(s.x) + Math.abs(s.y) + 4;
        const d = `M${n(-big)} ${n(-big)}H${n(w + big)}V${n(h + big)}H${n(-big)}Z` + shapePath(s.x + ins, s.y + ins, Math.max(0, w - 2 * ins), Math.max(0, h - 2 * ins), adjustR(r, -ins));
        const clip = clipDef(shape(0, 0, w, h, r));
        out += `<g${attrs({ 'data-name': 'inner shadow', 'clip-path': clip })}><path${attrs({ d, 'fill-rule': 'evenodd', ...fillA,
          filter: sg > 0 ? blurFilter(sg, [-big - pad, -big - pad, w + 2 * big + 2 * pad, h + 2 * big + 2 * pad]) : null })}/></g>`;
      }
    }
    return out;
  }

  /* ---------------- CSS filters -> SVG filters ---------------- */
  function cssFilter(value, box) {
    if (!value || value === 'none') return null;
    const fns = splitTop(value, ' ');
    if (fns.length === 1) {
      const u = /^url\(\s*["']?#([^"')]+)["']?\s*\)$/.exec(fns[0]);
      if (u) return copyDef(u[1]) ? `url(#${u[1]})` : null;
    }
    let prims = '', last = 'SourceGraphic', k = 0, grow = 0;
    const res = () => 'f' + (++k);
    for (const f of fns) {
      const m = /^([a-z-]+)\(([\s\S]*)\)$/i.exec(f); if (!m) continue;
      const name = m[1].toLowerCase(), arg = m[2].trim();
      const v = arg === '' ? 1 : arg.endsWith('%') ? parseFloat(arg) / 100 : parseFloat(arg);
      const r = res();
      if (name === 'blur') { prims += `<feGaussianBlur in="${last}" stdDeviation="${n(px(arg))}" result="${r}"/>`; grow += 3 * px(arg); }
      else if (name === 'brightness') prims += `<feComponentTransfer in="${last}" result="${r}"><feFuncR type="linear" slope="${n3(v)}"/><feFuncG type="linear" slope="${n3(v)}"/><feFuncB type="linear" slope="${n3(v)}"/></feComponentTransfer>`;
      else if (name === 'contrast') { const ic = n3(-0.5 * v + 0.5); prims += `<feComponentTransfer in="${last}" result="${r}"><feFuncR type="linear" slope="${n3(v)}" intercept="${ic}"/><feFuncG type="linear" slope="${n3(v)}" intercept="${ic}"/><feFuncB type="linear" slope="${n3(v)}" intercept="${ic}"/></feComponentTransfer>`; }
      else if (name === 'saturate') prims += `<feColorMatrix in="${last}" type="saturate" values="${n3(v)}" result="${r}"/>`;
      else if (name === 'grayscale') prims += `<feColorMatrix in="${last}" type="saturate" values="${n3(1 - Math.min(1, v))}" result="${r}"/>`;
      else if (name === 'hue-rotate') prims += `<feColorMatrix in="${last}" type="hueRotate" values="${n3(parseFloat(arg) || 0)}" result="${r}"/>`;
      else if (name === 'opacity') prims += `<feComponentTransfer in="${last}" result="${r}"><feFuncA type="linear" slope="${n3(v)}"/></feComponentTransfer>`;
      else if (name === 'invert') prims += `<feComponentTransfer in="${last}" result="${r}"><feFuncR type="table" tableValues="${n3(v)} ${n3(1 - v)}"/><feFuncG type="table" tableValues="${n3(v)} ${n3(1 - v)}"/><feFuncB type="table" tableValues="${n3(v)} ${n3(1 - v)}"/></feComponentTransfer>`;
      else if (name === 'drop-shadow') {
        const s = parseShadows(arg, false)[0]; if (!s) { k--; continue; }
        const c = s.color;
        prims += `<feGaussianBlur in="${last}" stdDeviation="${n(s.blur / 2)}" result="${r}b"/><feOffset in="${r}b" dx="${n(s.x)}" dy="${n(s.y)}" result="${r}o"/>` +
          `<feFlood flood-color="${hex(c)}"${c.a < 1 ? ` flood-opacity="${n3(c.a)}"` : ''} result="${r}c"/><feComposite in="${r}c" in2="${r}o" operator="in" result="${r}s"/>` +
          `<feMerge result="${r}"><feMergeNode in="${r}s"/><feMergeNode in="${last}"/></feMerge>`;
        grow += 1.5 * s.blur + Math.max(Math.abs(s.x), Math.abs(s.y));
      } else { k--; continue; }
      last = r;
    }
    if (!prims) return null;
    const id = newId('filter'), g = grow + 4;
    defs.push(`<filter${attrs({ id, filterUnits: 'userSpaceOnUse', x: n(box[0] - g), y: n(box[1] - g), width: n(box[2] + 2 * g), height: n(box[3] + 2 * g),
      'color-interpolation-filters': 'sRGB' })}>${prims}</filter>`);
    return `url(#${id})`;
  }
  function textShadowFilter(cs, box) {
    const list = parseShadows(cs.textShadow, false); if (!list.length) return null;
    return cssFilter(list.map(s => `drop-shadow(rgba(${s.color.r},${s.color.g},${s.color.b},${s.color.a}) ${s.x}px ${s.y}px ${s.blur}px)`).join(' '), box);
  }

  /* ---------------- copying the page's own SVG ---------------- */
  const SKIP_SVG = new Set(['defs', 'filter', 'clippath', 'mask', 'marker', 'lineargradient', 'radialgradient', 'pattern', 'symbol', 'style', 'title', 'desc', 'metadata', 'script']);
  const SHAPES = new Set(['path', 'rect', 'circle', 'ellipse', 'line', 'polyline', 'polygon', 'text', 'tspan', 'use', 'image', 'g']);
  const PAINT_PROPS = ['fill', 'stroke'];
  const PLAIN_PROPS = { 'fill-rule': 'nonzero', 'stroke-width': '1px', 'stroke-dasharray': 'none', 'stroke-dashoffset': '0px', 'stroke-linecap': 'butt',
    'stroke-linejoin': 'miter', 'stroke-miterlimit': '4', 'clip-rule': 'nonzero', 'paint-order': 'normal', 'vector-effect': 'none' };
  function urlRef(v) { const m = /url\(\s*["']?#([^"')]+)["']?\s*\)/.exec(v || ''); return m ? m[1] : null; }
  function copyDef(id) {
    if (defIds.has(id)) return true;
    const el = document.getElementById(id); if (!el) return false;
    defIds.add(id);
    defs.push(cloneSvg(el, true));
    return true;
  }
  function cloneSvg(node, asDef) {
    if (node.nodeType === 3) return esc(node.data);
    if (node.nodeType !== 1) return '';
    const tag = node.localName, low = tag.toLowerCase();
    if (low === 'script' || low === 'style') return '';
    if (!asDef && SKIP_SVG.has(low)) return '';
    const cs = getComputedStyle(node);
    const graphic = SHAPES.has(low);
    if (graphic && (cs.display === 'none' || cs.visibility === 'hidden')) return '';
    const a = {};
    for (const at of node.attributes) {
      if (at.name === 'class' || at.name === 'style' || at.name.startsWith('on')) continue;
      let v = at.value;
      if ((at.name === 'href' || at.name === 'xlink:href') && low === 'image') { a['xlink:href'] = dataURI(v); continue; }
      if (at.name === 'href' || at.name === 'xlink:href') { const r = urlRef(`url(${v})`); if (r) copyDef(r); a['xlink:href'] = v; continue; }
      const r = urlRef(v); if (r) copyDef(r);
      a[at.name] = v;
    }
    if (graphic && low !== 'use') {
      let opacityExtra = {};
      for (const p of PAINT_PROPS) {
        const v = cs.getPropertyValue(p);
        if (!v) continue;
        const ref = urlRef(v);
        if (ref) { copyDef(ref); a[p] = `url(#${ref})`; continue; }
        if (v === 'none') { a[p] = 'none'; continue; }
        const c = parseColor(v); if (!c) continue;
        const own = parseFloat(cs.getPropertyValue(p + '-opacity')); const o = (isNaN(own) ? 1 : own) * c.a;
        a[p] = hex(c); opacityExtra[p + '-opacity'] = o < 0.999 ? n3(o) : null;
        delete a[p + '-opacity'];
      }
      Object.assign(a, Object.fromEntries(Object.entries(opacityExtra).filter(([, v]) => v != null)));
      for (const [p, def] of Object.entries(PLAIN_PROPS)) {
        const v = cs.getPropertyValue(p);
        if (v && v !== def) a[p] = p === 'stroke-width' || p === 'stroke-dashoffset' ? String(px(v)) : v.replace(/px/g, '');
        else delete a[p];
      }
      for (const p of ['marker-start', 'marker-mid', 'marker-end', 'clip-path', 'mask']) {
        const v = cs.getPropertyValue(p); const ref = urlRef(v);
        if (ref && copyDef(ref)) a[p] = `url(#${ref})`;
      }
      const op = parseFloat(cs.opacity); if (op < 1) a.opacity = n3(op); else delete a.opacity;
      const f = cs.filter;
      if (f && f !== 'none') {
        let bb = [0, 0, 0, 0]; try { const b = node.getBBox(); bb = [b.x, b.y, b.width, b.height]; } catch (e) {}
        const ff = cssFilter(f, bb); if (ff) a.filter = ff; else delete a.filter;
      }
      const blend = cs.mixBlendMode; if (blend && blend !== 'normal') a.style = `mix-blend-mode:${blend}`;
      if (low === 'text' || low === 'tspan') {
        a['font-family'] = cs.fontFamily; a['font-size'] = cs.fontSize; a['font-weight'] = cs.fontWeight;
        if (cs.fontStyle !== 'normal') a['font-style'] = cs.fontStyle;
        if (cs.letterSpacing !== 'normal') a['letter-spacing'] = cs.letterSpacing;
        if (cs.textAnchor && cs.textAnchor !== 'start') a['text-anchor'] = cs.textAnchor;
      }
      const qa = node.getAttribute('data-qa-line') || node.getAttribute('data-qa');
      const cls = node.getAttribute('class');
      if (!asDef && (qa || cls)) { const nm = qa || cls.split(/\s+/)[0]; a.id = a.id || layerId(nm); a['data-name'] = nm; a['inkscape:label'] = nm; }
    }
    const kids = [...node.childNodes].map(c => cloneSvg(c, asDef || low === 'text' || low === 'tspan')).join('');
    return `<${tag}${attrs(a)}>${kids}</${tag}>`;
  }
  /* an <svg> element placed in the page: its children in a group mapped from its viewBox */
  function emitSvgContent(svg, w, h) {
    let t = '';
    const vb = svg.viewBox && svg.viewBox.baseVal;
    if (vb && vb.width > 0 && vb.height > 0) {
      const par = svg.preserveAspectRatio.baseVal;
      let sx = w / vb.width, sy = h / vb.height, tx = 0, ty = 0;
      if (par.align !== 1) {   // 1 = none
        const s = par.meetOrSlice === 2 ? Math.max(sx, sy) : Math.min(sx, sy);
        const ax = [0, 0, 0.5, 1, 0, 0.5, 1, 0, 0.5, 1][par.align] || 0.5, ay = [0, 0, 0, 0, 0.5, 0.5, 0.5, 1, 1, 1][par.align] || 0.5;
        tx = (w - vb.width * s) * ax; ty = (h - vb.height * s) * ay; sx = sy = s;
      }
      t = `translate(${n(tx)} ${n(ty)}) scale(${n3(sx)} ${n3(sy)}) translate(${n(-vb.x)} ${n(-vb.y)})`;
      if (Math.abs(sx - 1) < 1e-4 && Math.abs(sy - 1) < 1e-4 && !tx && !ty && !vb.x && !vb.y) t = '';
    }
    const inner = [...svg.childNodes].map(c => cloneSvg(c, false)).join('');
    return t ? `<g transform="${t}">${inner}</g>` : inner;
  }

  /* ---------------- text ---------------- */
  /* where the baseline sits below the top of a text fragment's rect, for a face: measured with a zero-size
     inline-block (whose bottom is the baseline) beside sample text in an off-page probe of the same font */
  const baselineCache = new Map();
  let bprobe = null;
  function baselineOffset(cs) {
    const props = ['fontStyle', 'fontWeight', 'fontStretch', 'fontSize', 'fontFamily', 'fontVariantNumeric', 'fontFeatureSettings', 'fontKerning', 'fontVariationSettings'];
    const key = props.map(p => cs[p]).join('|') + '|' + document.documentElement.lang;
    if (baselineCache.has(key)) return baselineCache.get(key);
    if (!bprobe) {
      bprobe = document.createElement('div');
      bprobe.style.cssText = 'position:absolute;left:0;top:0;visibility:hidden;white-space:nowrap;line-height:normal;letter-spacing:0;text-transform:none';
      (document.getElementById('canvas') || document.body).appendChild(bprobe);
    }
    for (const p of props) bprobe.style[p] = cs[p];
    bprobe.textContent = 'Hg';
    const mark = document.createElement('span'); mark.style.cssText = 'display:inline-block;width:0;height:0;vertical-align:baseline';
    bprobe.appendChild(mark);
    const r = document.createRange(); r.selectNodeContents(bprobe.firstChild);
    const q = r.getClientRects()[0];
    const v = q ? mark.getBoundingClientRect().bottom - q.top : px(cs.fontSize) * 0.8;
    baselineCache.set(key, v); return v;
  }
  function transformCase(s, tt, prevChar) {
    if (tt === 'uppercase') return s.toUpperCase();
    if (tt === 'lowercase') return s.toLowerCase();
    if (tt === 'capitalize') return !prevChar || /\s/.test(prevChar) ? s.toUpperCase() : s;
    return s;
  }
  const isReplaced = e => /^(img|svg|canvas|video|iframe|input|textarea|select|object)$/i.test(e.localName);
  /* an inline child whose text belongs to the parent's <text> (a .hi word, a .pill) */
  function inlineChild(e) {
    if (e.nodeType !== 1 || e.namespaceURI === NS || isReplaced(e)) return false;
    const cs = getComputedStyle(e);
    if (!cs.display.startsWith('inline') || (cs.position !== 'static' && cs.position !== 'relative')) return false;
    if (cs.transform && cs.transform !== 'none') return false;
    return [...e.children].every(inlineChild);
  }
  function textNodesOf(el) {
    const out = [];
    const walk = node => { for (const c of node.childNodes) { if (c.nodeType === 3) out.push(c); else if (c.nodeType === 1 && inlineChild(c)) walk(c); } };
    walk(el); return out;
  }
  const FONT_PROPS = e => ({
    'font-family': e.fontFamily, 'font-size': e.fontSize, 'font-weight': e.fontWeight,
    'font-style': e.fontStyle !== 'normal' ? e.fontStyle : null,
    'font-stretch': e.fontStretch !== '100%' && e.fontStretch !== 'normal' ? e.fontStretch : null,
    'letter-spacing': e.letterSpacing !== 'normal' && px(e.letterSpacing) !== 0 ? e.letterSpacing : null,
    'word-spacing': e.wordSpacing !== 'normal' && px(e.wordSpacing) !== 0 ? e.wordSpacing : null,
    'font-variant-numeric': e.fontVariantNumeric !== 'normal' ? e.fontVariantNumeric : null,
    'font-feature-settings': e.fontFeatureSettings !== 'normal' ? e.fontFeatureSettings : null,
    'font-kerning': e.fontKerning !== 'auto' ? e.fontKerning : null,
    // the same width and weight as variable-font axes, for editors that ignore font-stretch or snap odd weights
    'font-variation-settings': variation(e),
  });
  function variation(e) {
    if (e.fontVariationSettings && e.fontVariationSettings !== 'normal') return e.fontVariationSettings;
    const w = parseFloat(e.fontWeight) || 400, st = parseFloat(e.fontStretch) || 100;
    if (w % 100 === 0 && Math.abs(st - 100) < 0.01) return null;
    return `"wght" ${w}` + (Math.abs(st - 100) >= 0.01 ? `, "wdth" ${st}` : '');
  }
  const styleStr = o => Object.entries(o).filter(([, v]) => v != null && v !== '').map(([k, v]) => `${k}:${v}`).join(';');
  /* opacity of the inline elements between a text node and the text element */
  function inlineAlpha(node, el) { let a = 1; for (let e = node.parentNode; e && e !== el; e = e.parentNode) a *= parseFloat(getComputedStyle(e).opacity); return a; }

  /* the element's text as one <text>, a tspan per line and style run; plus the inline boxes (pills) under it */
  function emitText(el, ox, oy) {
    const nodes = textNodesOf(el).filter(t => /\S/.test(t.data));
    if (!nodes.length) return '';
    const base = getComputedStyle(el);
    const range = document.createRange();
    const glyphs = [];
    for (const t of nodes) {
      const st = getComputedStyle(t.parentNode);
      const tt = st.textTransform;
      const data = t.data;
      let i = 0, prev = '';
      while (i < data.length) {
        const cp = data.codePointAt(i), len = cp > 0xffff ? 2 : 1, ch = data.substr(i, len);
        range.setStart(t, i); range.setEnd(t, i + len);
        const rects = [...range.getClientRects()].filter(q => q.width > 0.01 || !/\s/.test(ch));
        const q = rects[0];
        if (q && ch !== '\n' && q.height > 0) glyphs.push({ ch: transformCase(ch, tt, prev), raw: ch, x: q.left - C.left - ox, r: q.right - C.left - ox, top: q.top - C.top - oy, bottom: q.bottom - C.top - oy, node: t, st, space: /\s/.test(ch) });
        prev = ch; i += len;
      }
    }
    if (!glyphs.length) return '';
    // lines: a glyph left of the previous one, or wholly below the current line, starts a new one
    const lines = []; let cur = null;
    for (const g of glyphs) {
      if (!cur || g.x < cur.lastR - 1.5 && g.top > cur.top - 0.5 * (cur.bottom - cur.top) || g.top >= cur.bottom - 0.5) {
        cur = { glyphs: [], top: g.top, bottom: g.bottom, lastR: g.r }; lines.push(cur);
      }
      cur.glyphs.push(g); cur.lastR = Math.max(cur.lastR, g.r); cur.top = Math.min(cur.top, g.top); cur.bottom = Math.max(cur.bottom, g.bottom);
    }
    const baseFont = FONT_PROPS(base);
    let spans = '';
    for (const line of lines) {
      let gs = line.glyphs;
      while (gs.length && gs[0].space) gs = gs.slice(1);
      while (gs.length && gs[gs.length - 1].space) gs = gs.slice(0, -1);
      // runs of one text node (one style)
      let run = null; const runs = [];
      for (const g of gs) { if (!run || run.node !== g.node) { run = { node: g.node, st: g.st, glyphs: [] }; runs.push(run); } run.glyphs.push(g); }
      for (const r of runs) {
        const g0 = r.glyphs[0], st = r.st;
        const tops = r.glyphs.map(g => g.top).sort((a, b) => a - b);
        const baseline = tops[tops.length >> 1] + baselineOffset(st);
        const font = FONT_PROPS(st), diff = {};
        for (const [k, v] of Object.entries(font)) if (v !== baseFont[k]) diff[k] = v == null ? (k === 'font-stretch' ? '100%' : k.includes('spacing') ? '0' : 'normal') : v;
        const alpha = inlineAlpha(r.node, el);
        const fill = paint('fill', st.color, alpha);
        const sameFill = st.color === base.color && alpha === 1;
        const textStr = r.glyphs.map(g => g.ch).join('');
        spans += `<tspan${attrs({ x: n(g0.x), y: n(baseline), ...(sameFill ? {} : fill), style: styleStr(diff) || null })}>${esc(textStr)}</tspan>`;
      }
    }
    // inline boxes with a background or border (pills) under the text
    let boxes = '';
    const inl = [];
    const walk = node => { for (const c of node.children) if (inlineChild(c)) { inl.push(c); walk(c); } };
    walk(el);
    for (const e of inl) {
      const cs = getComputedStyle(e);
      for (const q of e.getClientRects()) {
        const b = decorations(e, cs, q.width, q.height, inlineAlpha(e, el) * parseFloat(cs.opacity));
        if (b) boxes += group(e.className ? String(e.className).split(/\s+/)[0] : 'box', null, { transform: `translate(${n(q.left - C.left - ox)} ${n(q.top - C.top - oy)})` }, b);
      }
    }
    const words = el.textContent.trim().replace(/\s+/g, ' ');
    // text-shadow: a blurred, tinted <use> of the text under it (the text itself stays unfiltered, so it rasterises
    // like the page and editing it updates its shadow)
    const tid = layerId('text-' + (el.getAttribute('data-qa') || 'run'));
    const text = `<text${attrs({ id: tid, 'xml:space': 'preserve', ...paint('fill', base.color), style: styleStr(baseFont), 'data-name': words.slice(0, 60) })}>${spans}</text>`;
    let shadows = '';
    const bw = el.offsetWidth || 0, bh = el.offsetHeight || 0;
    for (const sh of parseShadows(base.textShadow, false).reverse()) {
      const id = newId('tshadow'), sg = sh.blur / 2, g = 3 * sg + 4 + Math.max(bh, 0) * 0.5;
      defs.push(`<filter${attrs({ id, filterUnits: 'userSpaceOnUse', x: n(-g), y: n(-g), width: n(bw + 2 * g), height: n(bh + 2 * g), 'color-interpolation-filters': 'sRGB' })}>` +
        `<feFlood flood-color="${hex(sh.color)}"${sh.color.a < 1 ? ` flood-opacity="${n3(sh.color.a)}"` : ''}/><feComposite in2="SourceAlpha" operator="in"/>` +
        (sg > 0 ? `<feGaussianBlur stdDeviation="${n(sg)}"/>` : '') + '</filter>');
      shadows += `<use${attrs({ 'xlink:href': '#' + tid, transform: sh.x || sh.y ? `translate(${n(sh.x)} ${n(sh.y)})` : null, filter: `url(#${id})`, 'data-name': 'text shadow' })}/>`;
    }
    return boxes + shadows + text;
  }

  /* background, borders and shadows of a box (local coordinates 0,0,w,h) */
  function decorations(el, cs, w, h, alpha = 1) {
    if (w <= 0 || h <= 0) return '';
    const r = radii(cs, w, h);
    let out = boxShadows(cs, w, h, r, false);
    const bt = px(cs.borderTopWidth), br = px(cs.borderRightWidth), bb = px(cs.borderBottomWidth), bl = px(cs.borderLeftWidth);
    const clip = cs.backgroundClip;
    const cb = clip === 'padding-box' || clip === 'content-box' ? [bl, bt, w - bl - br, h - bt - bb] : [0, 0, w, h];
    const cr = clip === 'padding-box' || clip === 'content-box' ? adjustR(r, -Math.max(bl, bt)) : r;
    const bg = parseColor(cs.backgroundColor);
    if (bg && bg.a > 0) out += shape(cb[0], cb[1], cb[2], cb[3], cr, { ...paint('fill', bg), 'data-name': 'background' });
    if (cs.backgroundImage && cs.backgroundImage !== 'none') {
      const layers = splitTop(cs.backgroundImage, ',').reverse();
      const pb = [bl, bt, w - bl - br, h - bt - bb];   // background-origin: padding-box
      for (const layer of layers) {
        if (/gradient\(/i.test(layer)) {
          const g = gradient(layer, pb[0], pb[1], pb[2], pb[3], false);
          if (g) out += shape(cb[0], cb[1], cb[2], cb[3], cr, { fill: g, 'data-name': 'gradient' });
        } else {
          const u = /^url\(\s*["']?([^"')]+)["']?\s*\)$/.exec(layer);
          if (u) out += backgroundImage(u[1], cs, pb, shape(cb[0], cb[1], cb[2], cb[3], cr));
        }
      }
    }
    out += boxShadows(cs, w, h, r, true);
    // borders
    const sides = [['top', bt], ['right', br], ['bottom', bb], ['left', bl]].map(([s, wd]) => ({ s, wd, style: cs['border' + s[0].toUpperCase() + s.slice(1) + 'Style'], color: cs['border' + s[0].toUpperCase() + s.slice(1) + 'Color'] }))
      .filter(x => x.wd > 0 && x.style !== 'none' && x.style !== 'hidden');
    if (sides.length === 4 && sides.every(x => x.wd === bt && x.style === sides[0].style && x.color === sides[0].color)) {
      const d = bt / 2, dash = sides[0].style === 'dashed' ? `${n(bt * 3)} ${n(bt * 3)}` : sides[0].style === 'dotted' ? `${n(bt)} ${n(bt)}` : null;
      out += shape(d, d, w - bt, h - bt, adjustR(r, -d), { fill: 'none', ...paint('stroke', sides[0].color), 'stroke-width': n(bt), 'stroke-dasharray': dash, 'data-name': 'border' });
    } else {
      for (const x of sides) {
        const box = { top: [0, 0, w, x.wd], bottom: [0, h - x.wd, w, x.wd], left: [0, 0, x.wd, h], right: [w - x.wd, 0, x.wd, h] }[x.s];
        if (x.style === 'dotted' || x.style === 'dashed') {
          const horiz = x.s === 'top' || x.s === 'bottom';
          const [x1, y1, x2, y2] = horiz ? [box[0], box[1] + x.wd / 2, box[0] + box[2], box[1] + x.wd / 2] : [box[0] + x.wd / 2, box[1], box[0] + x.wd / 2, box[1] + box[3]];
          const dash = x.style === 'dotted' ? `${n(x.wd)} ${n(x.wd)}` : `${n(x.wd * 3)} ${n(x.wd * 3)}`;
          out += `<line${attrs({ x1: n(x1), y1: n(y1), x2: n(x2), y2: n(y2), ...paint('stroke', x.color), 'stroke-width': n(x.wd), 'stroke-dasharray': dash, 'data-name': 'border-' + x.s })}/>`;
        } else out += shape(box[0], box[1], box[2], box[3], null, { ...paint('fill', x.color), 'data-name': 'border-' + x.s });
      }
    }
    const ow = px(cs.outlineWidth);
    if (ow > 0 && cs.outlineStyle !== 'none') {
      const off = px(cs.outlineOffset) + ow / 2;
      out += shape(-off, -off, w + 2 * off, h + 2 * off, adjustR(r, off), { fill: 'none', ...paint('stroke', cs.outlineColor), 'stroke-width': n(ow), 'data-name': 'outline' });
    }
    return out && alpha < 1 ? `<g opacity="${n3(alpha)}">${out}</g>` : out;
  }
  function backgroundImage(url, cs, box, clipShape) {
    const img = new Image(); img.src = url;
    const iw = img.naturalWidth || 0, ih = img.naturalHeight || 0;
    let w = box[2], h = box[3];
    const size = cs.backgroundSize.split(',')[0].trim();
    if (iw && ih) {
      if (size === 'cover' || size === 'contain') {
        const s = size === 'cover' ? Math.max(box[2] / iw, box[3] / ih) : Math.min(box[2] / iw, box[3] / ih); w = iw * s; h = ih * s;
      } else {
        const p = size.split(/\s+/);
        if (p[0] === 'auto' && (!p[1] || p[1] === 'auto')) { w = iw; h = ih; }
        else { w = p[0] === 'auto' ? null : evalLen(p[0], box[2]); h = !p[1] || p[1] === 'auto' ? null : evalLen(p[1], box[3]);
          if (w == null) w = h * iw / ih; if (h == null) h = w * ih / iw; }
      }
    }
    const pos = (cs.backgroundPosition.split(',')[0] || '0% 0%').trim().split(/\s+/);
    const x = box[0] + evalLen(pos[0] || '0%', 0) + (pos[0] && pos[0].endsWith('%') ? (box[2] - w) * parseFloat(pos[0]) / 100 : 0);
    const y = box[1] + evalLen(pos[1] || '0%', 0) + (pos[1] && pos[1].endsWith('%') ? (box[3] - h) * parseFloat(pos[1]) / 100 : 0);
    return `<g clip-path="${clipDef(clipShape)}"><image${attrs({ x: n(x), y: n(y), width: n(w), height: n(h), preserveAspectRatio: 'none', 'xlink:href': dataURI(url) })}/></g>`;
  }

  /* CSS mask-image (gradients) -> SVG masks; returns the list of mask urls (intersected when more than one) */
  function masks(cs, w, h) {
    const img = cs.maskImage && cs.maskImage !== 'none' ? cs.maskImage : cs.webkitMaskImage;
    if (!img || img === 'none') return [];
    const layers = splitTop(img, ',').filter(l => /gradient\(/i.test(l));
    if (!layers.length) return [];
    const comp = String(cs.maskComposite || cs.webkitMaskComposite || 'add').split(',')[0].trim();
    const make = list => {
      const id = newId('mask');
      defs.push(`<mask id="${id}" maskUnits="userSpaceOnUse" x="0" y="0" width="${n(w)}" height="${n(h)}">` +
        list.map(l => { const g = gradient(l, 0, 0, w, h, true); return g ? `<rect width="${n(w)}" height="${n(h)}" fill="${g}"/>` : ''; }).join('') + '</mask>');
      return `url(#${id})`;
    };
    if (layers.length > 1 && /intersect|source-in|destination-in/.test(comp)) return layers.map(l => make([l]));
    return [make(layers)];
  }
  function clipPathCss(cs, w, h) {
    const v = cs.clipPath; if (!v || v === 'none') return null;
    const ref = urlRef(v); if (ref) return copyDef(ref) ? `url(#${ref})` : null;
    const m = /^inset\(([^)]*)\)/.exec(v);
    if (m) {
      const [box, round] = m[1].split(/\sround\s/);
      const p = box.trim().split(/\s+/); const t = evalLen(p[0], h), r = evalLen(p[1] || p[0], w), b = evalLen(p[2] || p[0], h), l = evalLen(p[3] || p[1] || p[0], w);
      const rr = round ? px(round) : 0;
      return clipDef(shape(l, t, w - l - r, h - t - b, rr ? [[rr, rr], [rr, rr], [rr, rr], [rr, rr]] : null));
    }
    const c = /^circle\(([^)]*)\)/.exec(v);
    if (c) {
      const [rad, at] = c[1].split(/\sat\s/); const rr = evalLen(rad.trim() || '50%', Math.hypot(w, h) / Math.SQRT2);
      const pp = (at || '50% 50%').trim().split(/\s+/);
      return clipDef(`<circle cx="${n(evalLen(pp[0], w))}" cy="${n(evalLen(pp[1] || '50%', h))}" r="${n(rr)}"/>`);
    }
    return null;
  }

  /* ---------------- elements ---------------- */
  function layerName(el) {
    const qa = el.getAttribute('data-qa');
    if (qa) return qa;
    const cls = el.getAttribute('class');
    if (el.localName === 'img') return 'image';
    if (cls) return String(cls).trim().split(/\s+/)[0];
    if (el.id) return el.id;
    return el.localName;
  }
  function stackKey(el, cs, i) {
    const positioned = cs.position !== 'static';
    const z = cs.zIndex === 'auto' ? null : parseInt(cs.zIndex, 10);
    if (positioned && z != null && z < 0) return [0, z, i];
    if (!positioned && !(parseFloat(cs.opacity) < 1) && cs.transform === 'none' && cs.filter === 'none') return [1, 0, i];
    if (positioned && z != null && z > 0) return [3, z, i];
    return [2, 0, i];
  }
  const xforms = new Map();   // element -> {m, ox, oy}

  function emitElement(el, parentX, parentY, snap = true) {
    if (el.nodeType !== 1) return '';
    const tag = el.localName.toLowerCase();
    if (/^(script|style|template|link|meta|br|wbr|noscript)$/.test(tag)) return '';
    const cs = getComputedStyle(el);
    if (cs.display === 'none') return '';
    const op = parseFloat(cs.opacity);
    if (op <= 0) return '';
    const rect = el.getBoundingClientRect();
    const x = rect.left - C.left, y = rect.top - C.top, w = rect.width, h = rect.height;
    const isCanvas = el.id === 'canvas';

    let body = '';
    if (tag === 'svg') {
      body = emitSvgContent(el, w, h);
      if (cs.overflow !== 'visible' && body) body = `<g clip-path="${clipDef(`<rect width="${n(w)}" height="${n(h)}"/>`)}">${body}</g>`;
    } else {
      if (cs.visibility !== 'hidden') body += decorations(el, cs, w, h);
      if (tag === 'img') {
        const src = el.currentSrc || el.getAttribute('src');
        if (src && w > 0 && h > 0) {
          const r = radii(cs, w, h);
          // the browser draws an untransformed picture on whole pixels: do the same so it resamples alike
          let ix = 0, iy = 0, iw = w, ih = h;
          if (snap && !xforms.has(el)) { ix = Math.round(x) - x; iy = Math.round(y) - y; iw = Math.round(x + w) - Math.round(x); ih = Math.round(y + h) - Math.round(y); }
          const im = `<image${attrs({ x: n(ix), y: n(iy), width: n(iw), height: n(ih), preserveAspectRatio: 'none', 'xlink:href': dataURI(src, el) })}/>`;
          body += zeroR(r) ? im : `<g clip-path="${clipDef(shape(0, 0, w, h, r))}">${im}</g>`;
        }
      }
      // content: in-flow blocks, then the element's own text, then positioned children (by z-index)
      const kids = [...el.children].filter(c => !inlineChild(c) || !textNodesOf(el).some(t => /\S/.test(t.data)));
      const keyed = kids.map((c, i) => ({ c, k: stackKey(c, getComputedStyle(c), i) }))
        .sort((a, b) => a.k[0] - b.k[0] || a.k[1] - b.k[1] || a.k[2] - b.k[2]);
      let content = '';
      for (const { c, k } of keyed) if (k[0] <= 1) content += emitElement(c, x, y, snap && !xforms.has(el));
      if (cs.visibility !== 'hidden') content += emitText(el, x, y);
      for (const { c, k } of keyed) if (k[0] > 1) content += emitElement(c, x, y, snap && !xforms.has(el));
      if (content && !isCanvas && (cs.overflowX !== 'visible' || cs.overflowY !== 'visible') && tag !== 'img') {
        const bt = px(cs.borderTopWidth), br = px(cs.borderRightWidth), bb = px(cs.borderBottomWidth), bl = px(cs.borderLeftWidth);
        content = `<g clip-path="${clipDef(shape(bl, bt, w - bl - br, h - bt - bb, adjustR(radii(cs, w, h), -Math.max(bl, bt))))}">${content}</g>`;
      }
      body += content;
    }
    if (!body) return '';

    // effects, in CSS order: filter, clip-path, mask, opacity (one group applies them in the same order)
    const fx = {};
    let transform = isCanvas ? '' : (Math.abs(x - parentX) > 1e-3 || Math.abs(y - parentY) > 1e-3 ? `translate(${n(x - parentX)} ${n(y - parentY)})` : '');
    const xf = xforms.get(el);
    if (xf) transform += ` translate(${n(xf.ox)} ${n(xf.oy)}) matrix(${[xf.m.a, xf.m.b, xf.m.c, xf.m.d, xf.m.e, xf.m.f].map(v => Math.round(v * 1e5) / 1e5).join(' ')}) translate(${n(-xf.ox)} ${n(-xf.oy)})`;
    if (transform.trim()) fx.transform = transform.trim();
    const filt = cssFilter(cs.filter, [0, 0, w, h]); if (filt) fx.filter = filt;
    const cp = clipPathCss(cs, w, h); if (cp) fx['clip-path'] = cp;
    const ms = masks(cs, w, h);
    if (ms.length) fx.mask = ms[0];
    if (op < 1) fx.opacity = n3(op);
    if (cs.mixBlendMode && cs.mixBlendMode !== 'normal') fx.style = `mix-blend-mode:${cs.mixBlendMode}`;
    if (cs.isolation === 'isolate') fx.style = (fx.style ? fx.style + ';' : '') + 'isolation:isolate';
    let inner = body;
    for (const m of ms.slice(1)) inner = `<g mask="${m}">${inner}</g>`;
    const name = layerName(el);
    let label = name;
    if (!el.getAttribute('data-qa')) { const words = (el.textContent || '').trim().replace(/\s+/g, ' '); if (words && tag !== 'svg' && words.length < 80 && el.children.length < 3) label = name + ': ' + words.slice(0, 40); }
    return group(isCanvas ? 'page' : name, isCanvas ? 'page' : label, fx, inner);
  }

  function fontFaces() {
    if (OPTS.fontCss) return OPTS.fontCss;
    const zh = (window.JOB && window.JOB.lang) === 'zh';
    const rules = [["Archivo", "Archivo-Variable.ttf", "font-weight:100 900;font-stretch:62% 125%"],
      ["Barlow Condensed", "BarlowCondensed-SemiBold.ttf", "font-weight:600"], ["Barlow Condensed", "BarlowCondensed-Bold.ttf", "font-weight:700"],
      ["Barlow Condensed", "BarlowCondensed-ExtraBold.ttf", "font-weight:800"], ["Inter", "Inter-Variable.ttf", "font-weight:100 900"]]
      .concat(zh ? [["Noto Sans SC", "NotoSansSC-Variable.ttf", "font-weight:100 900"]] : []);
    return rules.map(([f, file, d]) => `@font-face{font-family:'${f}';src:url('fonts/${file}') format('truetype');${d}}`).join('\n');
  }

  SVGX.build = function () {
    const canvas = document.getElementById('canvas');
    defs = []; defIds = new Set(); uid = 0; names = new Map(); xforms.clear();
    // switch every transform off while measuring (transforms do not move layout); put them back as group transforms
    const touched = [];
    for (const el of [canvas, ...canvas.querySelectorAll('*')]) {
      if (el.namespaceURI === NS && el.localName !== 'svg') continue;
      const cs = getComputedStyle(el);
      const t = cs.transform, extra = ['rotate', 'scale', 'translate'].some(p => cs[p] && cs[p] !== 'none');
      if ((t && t !== 'none') || extra) {
        let m = new DOMMatrix(t && t !== 'none' ? t : undefined);
        if (extra) {   // individual transform properties apply before transform: translate, rotate, scale, then transform
          let pre = new DOMMatrix();
          if (cs.translate && cs.translate !== 'none') { const p = cs.translate.split(/\s+/).map(px); pre = pre.translate(p[0] || 0, p[1] || 0); }
          if (cs.rotate && cs.rotate !== 'none') { const deg = /(-?[\d.]+)deg/.exec(cs.rotate); if (deg) pre = pre.rotate(parseFloat(deg[1])); }
          if (cs.scale && cs.scale !== 'none') { const p = cs.scale.split(/\s+/).map(parseFloat); pre = pre.scale(p[0], p.length > 1 ? p[1] : p[0]); }
          m = pre.multiply(m);
        }
        const o = cs.transformOrigin.split(/\s+/).map(px);
        xforms.set(el, { m, ox: o[0] || 0, oy: o[1] || 0 });
        touched.push([el, ['transform', 'rotate', 'scale', 'translate'].map(p => [p, el.style.getPropertyValue(p), el.style.getPropertyPriority(p)])]);
        for (const p of ['transform', 'rotate', 'scale', 'translate']) el.style.setProperty(p, 'none', 'important');
      }
    }
    let body;
    try {
      C = canvas.getBoundingClientRect();
      body = emitElement(canvas, 0, 0);
    } finally {
      if (bprobe) { bprobe.remove(); bprobe = null; }
      for (const [el, saved] of touched) for (const [p, v, pr] of saved) { if (v) el.style.setProperty(p, v, pr); else el.style.removeProperty(p); }
    }
    const W = Math.round(C.width), H = Math.round(C.height);
    const job = window.JOB || {};
    const lang = job.lang || 'en';
    return `<?xml version="1.0" encoding="UTF-8"?>\n` +
      `<svg xmlns="${NS}" xmlns:xlink="http://www.w3.org/1999/xlink" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" ` +
      `width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" xml:lang="${esc(lang)}" lang="${esc(lang)}"` +
      ` data-template="${esc(job.template_id || '')}" style="text-rendering:geometricPrecision">\n` +
      `<title>${esc([job.template_id, job.variant].filter(Boolean).join(' '))}</title>\n` +
      `<style>\n${fontFaces()}\n</style>\n<defs>${defs.join('\n')}</defs>\n${body}\n</svg>\n`;
  };

  SVGX.run = function () {
    let out;
    try { out = SVGX.build(); } catch (e) { out = null; SVGX.error = String(e && e.stack || e); }
    const s = document.createElement('script'); s.type = 'image/svg+xml'; s.id = 'svg-result';
    s.textContent = out || ('<!-- svg export failed: ' + String(SVGX.error).replace(/--/g, '- -') + ' -->');
    document.body.appendChild(s);
  };

  // Run after the runtime's QA measurement (layout done, fonts and pictures loaded). Nothing else changes.
  if (window.TPL && typeof window.TPL._measure === 'function') {
    const measure = window.TPL._measure;
    window.TPL._measure = function () { try { return measure.apply(this, arguments); } finally { SVGX.run(); } };
  }
})();
