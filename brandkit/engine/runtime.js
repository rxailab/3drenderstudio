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
    // the brand's ground texture, under everything; else the preset's own (job.style.texture: the blueprint's drafting grid)
    const own = TPL.job.style && TPL.job.style.texture;
    if (TPL.job.texture) TPL.texture(TPL.job.texture);
    // a template that draws its own drafting grid (T06 ground_style 'blueprint') gets no second one under it
    else if (own && own.kind && TPL.job.ground_style !== 'blueprint') TPL.texture(own.kind, { opacity: own.opacity, ink: own.ink });
    TPL.applyStyle(r);                                      // the brand preset: type, corners, leader lines, frame marks
    return r;
  };

  /* The brand preset (job.style, from brand_kit.PRESETS): how headlines and labels are set, how round cards and chips
     are, how leader lines are drawn and what marks frame the page. A job without it (and the default preset, which IS the
     templates' own look) changes nothing. Type is set by text tier, so every template follows without naming its classes;
     Chinese keeps its own faces and takes the weight only. */
  TPL.applyStyle = function (r) {
    const st = TPL.job.style; if (!st) return;
    r.classList.add('styled', 'preset-' + st.preset, 'ground-' + (st.ground || 'light'));
    r.dataset.preset = st.preset;
    const set = (k, v) => r.style.setProperty(k, String(v));
    const rules = [];
    const type = (prefix, tier, t) => {
      if (!t) return;
      set(`--${prefix}-weight`, t.weight); set(`--${prefix}-stretch`, t.stretch + '%');
      set(`--${prefix}-case`, t.case === 'upper' ? 'uppercase' : 'none'); set(`--${prefix}-slant`, t.italic ? 'italic' : 'normal');
      set(`--${prefix}-tracking`, (t.tracking || 0) + 'em');
      rules.push(`#canvas.styled:not([lang=zh]) .txt[data-tier="${tier}"]{font-weight:var(--${prefix}-weight);font-stretch:var(--${prefix}-stretch);` +
        `text-transform:var(--${prefix}-case);font-style:var(--${prefix}-slant);letter-spacing:var(--${prefix}-tracking)}`);
      rules.push(`#canvas.styled[lang=zh] .txt[data-tier="${tier}"]{font-weight:var(--${prefix}-weight)}`);
    };
    type('hl', 'headline', st.headline); type('lb', 'important', st.label);
    if (st.card && st.card.radius != null) {
      set('--card-radius', st.card.radius + 'px');
      rules.push('#canvas.styled .tile,#canvas.styled .tbl,#canvas.styled .stack,#canvas.styled .slot,#canvas.styled .badge,' +
        '#canvas.styled .panel,#canvas.styled .card{border-radius:var(--card-radius)}');
    }
    if (st.chip && st.chip.radius != null) {
      set('--chip-radius', Math.min(st.chip.radius, 999) + 'px');
      rules.push('#canvas.styled .chip,#canvas.styled .t4chip{border-radius:var(--chip-radius)}');
    }
    if (st.leader) {
      set('--leader-w', st.leader.width);
      set('--leader', st.leader.ink === 'ink' ? 'var(--text)' : st.leader.ink === 'soft' ? 'var(--muted)' : 'var(--accent)');
    }
    // a white panel on a page turned dark keeps its own dark inks
    rules.push('#canvas.ground-dark .tbl,#canvas.ground-accent .tbl,#canvas.ground-blueprint .tbl{--text:#1E2226;--muted:#5E666D;--ink:#1A1A1A;--ink-2:#3A3A3A;--line:#8E969D}');
    if (rules.length) { const css = document.createElement('style'); css.id = 'preset-style'; css.textContent = rules.join('\n'); document.head.appendChild(css); }
    TPL.frameMarks(st.frame || {});
  };

  /* The preset's frame, drawn in the page's outer margin where no template puts anything: a slanted accent band (promo),
     an accent line with a tick (tech). Under everything a template adds later. */
  TPL.frameMarks = function (frame) {
    const W = TPL.W, H = TPL.H, u = Math.round(H / 100);
    const mark = (id, style) => { const e = TPL.el('div', { class: 'frame-mark', 'data-qa': id, 'data-kind': 'shape', 'data-exempt': '1' });
      Object.assign(e.style, { position: 'absolute', background: 'var(--accent)', pointerEvents: 'none', zIndex: 40 }, style); return e; };
    if (frame.header === 'slash') {
      mark('frame_header', { left: '0px', top: '0px', width: Math.round(W * 0.42) + 'px', height: (u * 1.6) + 'px', clipPath: `polygon(0 0,100% 0,calc(100% - ${u * 2.4}px) 100%,0 100%)` });
      mark('frame_header_tail', { left: Math.round(W * 0.44) + 'px', top: '0px', width: (u * 5) + 'px', height: (u * 1.6) + 'px', clipPath: `polygon(${u * 2.4}px 0,100% 0,calc(100% - ${u * 2.4}px) 100%,0 100%)` });
    } else if (frame.header === 'band') {
      mark('frame_header', { left: '0px', top: '0px', width: W + 'px', height: (u * 1.2) + 'px' });
    }
    if (frame.footer === 'line') {
      mark('frame_footer', { left: '0px', top: (H - u * 0.6) + 'px', width: W + 'px', height: Math.max(3, Math.round(u * 0.4)) + 'px' });
      mark('frame_footer_tick', { left: Math.round(W * 0.85) + 'px', top: (H - u * 1.8) + 'px', width: (u * 9) + 'px', height: (u * 1.2) + 'px' });
    } else if (frame.footer === 'band') {
      mark('frame_footer', { left: '0px', top: (H - u * 1.2) + 'px', width: W + 'px', height: (u * 1.2) + 'px' });
    }
  };

  /* The brand's own marks (job.brand_marks): its logo in the first free corner, on a small white plate so it reads on
     any ground, and its legal line along the bottom edge. Neither may cover text, a panel or the product: a mark with no
     room is left out and the page says so (qa.notes), so the seller learns of it before the set is exported. */
  TPL.brandMarks = function () {
    const marks = TPL.job.brand_marks; if (!marks || TPL._marked) return; TPL._marked = true;
    const W = TPL.W, H = TPL.H, m = TPL.job.min_margin || 60;
    if (marks.logo && marks.logo.file) {
      const a = marks.logo, pad = Math.round(H * 0.008), edge = Math.round(m * 0.6);
      // as large as a corner allows: 6% of the page's height, then smaller, nearer the edge
      let at = null, w = 0, h = 0, bw = 0, bh = 0;
      for (const share of [0.06, 0.05, 0.042]) {
        h = Math.round(H * share); w = Math.round(h * a.width / Math.max(1, a.height));
        const most = Math.round(W * 0.24); if (w > most) { h = Math.round(h * most / w); w = most; }
        bw = w + 2 * pad; bh = h + 2 * pad;
        for (const g of [m, edge]) {
          at = TPL.freeSpot(bw, bh, [[W - g - bw, g], [g, g], [W - g - bw, H - g - bh], [g, H - g - bh]], 12, { overPhotos: true });
          if (at) break;
        }
        if (at) break;
      }
      if (!at) TPL.notes.push('brand logo left out: no free corner');
      else {
        const plate = TPL.el('div', { class: 'brand-logo', 'data-qa': 'brand_logo', 'data-kind': 'shape', 'data-exempt': '1' });
        TPL.box(plate, at[0], at[1], bw, bh);
        Object.assign(plate.style, { background: '#FFFFFF', borderRadius: Math.round(pad * 0.8) + 'px', zIndex: 45 });
        const picture = TPL.el('img', { src: a.file, 'data-qa': 'brand_logo_image', 'data-kind': 'logo', 'data-exempt': '1', 'data-nw': a.width, 'data-nh': a.height, alt: '' }, plate);
        TPL.box(picture, pad, pad, w, h);
      }
    }
    const legal = String(marks.legal || '').trim();
    if (legal) {
      const size = Math.max(16, Math.round(H * 0.0135)), h = Math.round(size * 1.5), y = H - Math.round(m * 0.5) - Math.round(h / 2), w = W - 2 * m;
      const line = TPL.el('div', { class: 'txt brand-legal', 'data-qa': 'brand_legal', 'data-kind': 'text', 'data-tier': 'meta', 'data-maxlines': '1', 'data-exempt': '1' }, null, legal);
      TPL.box(line, m, y, w, null);
      Object.assign(line.style, { fontFamily: (TPL.job.lang === 'zh' ? 'var(--f-cjk)' : 'var(--f-text)'), fontSize: size + 'px', lineHeight: h + 'px', fontWeight: 500,
        whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', letterSpacing: '.01em', zIndex: 45, pointerEvents: 'none' });
      const fits = line.scrollWidth <= w + 1;
      line.remove();                                                       // measured; placed only if the strip is free
      const free = fits && TPL.freeSpot(w, h, [[m, y]], 4, { overPhotos: true });
      if (!free) TPL.notes.push(fits ? 'legal line left out: the bottom edge is taken' : 'legal line left out: too long for one line');
      else {
        // What the line stands on, read under its own width: one plain ground takes ink that reads on it; a photograph,
        // or a line crossing from a dark panel onto a light one, gets a dark strip of its own (no single ink reads on both).
        TPL.root.appendChild(line);
        const span = document.createRange(); span.selectNodeContents(line); const q = span.getBoundingClientRect(), c = TPL.root.getBoundingClientRect();
        const kinds = new Set(); for (let i = 0; i <= 8; i++) kinds.add(TPL.groundAt(q.left - c.left + q.width * i / 8, y + h / 2));
        const strip = kinds.has('photo') || kinds.size > 1, dark = strip || kinds.has('dark');
        if (strip) { const band = TPL.el('div', { class: 'brand-legal-strip', 'data-qa': 'brand_legal_strip', 'data-kind': 'shape', 'data-exempt': '1' });
          TPL.box(band, 0, y - Math.round(h * 0.35), W, H - y + Math.round(h * 0.35)); Object.assign(band.style, { background: 'rgba(18,17,16,.72)', zIndex: 44, pointerEvents: 'none' });
          TPL.root.appendChild(line); }
        line.style.color = dark ? 'rgba(244,239,227,.92)' : 'var(--muted)';
        // the preset's footer tick gives way to the words when they reach it
        const tick = TPL.root.querySelector('[data-qa="frame_footer_tick"]');
        if (tick) { const range = document.createRange(); range.selectNodeContents(line); const q = range.getBoundingClientRect(), k = tick.getBoundingClientRect();
          if (q.left < k.right && q.right > k.left && q.top < k.bottom && q.bottom > k.top) tick.remove(); }
      }
    }
  };
  /* What lies under a point of the page, for a mark that must read on it: 'photo' (a picture or a gradient), 'dark' or
     'light' (the topmost plain fill there, else the page's own ground). */
  TPL.groundAt = function (x, y) {
    const c = TPL.root.getBoundingClientRect();
    for (const n of document.elementsFromPoint(c.left + x, c.top + y)) {
      if (!TPL.root.contains(n) || n === TPL.root) continue;
      if (n.classList.contains('brand-legal') || n.classList.contains('frame-mark') || n.closest('.brand-logo')) continue;
      if (n.tagName === 'IMG') return 'photo';
      const cs = getComputedStyle(n), bg = cs.backgroundColor.match(/[\d.]+/g);
      if (bg && (bg.length < 4 || +bg[3] > 0.5)) return (0.2126 * bg[0] + 0.7152 * bg[1] + 0.0722 * bg[2]) / 255 < 0.4 ? 'dark' : 'light';
      if (cs.backgroundImage && cs.backgroundImage !== 'none') return 'photo';
    }
    return TPL.onDarkGround() ? 'dark' : 'light';
  };
  /* Whether the page's own ground is dark (its --bg), for marks that must read on it. */
  TPL.onDarkGround = function () {
    const probe = TPL.el('div', { 'data-exempt': '1' }); probe.style.color = 'var(--bg)';
    const rgb = getComputedStyle(probe).color.match(/[\d.]+/g) || [255, 255, 255]; probe.remove();
    return (0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]) / 255 < 0.4;
  };

  /* Run the layout once the web fonts it measures with have loaded (project change: the faces now come from
     brandkit/vendor/fonts.css; laying out before they load would measure the fallback font). */
  TPL.ready = function (fn) {
    const zh = (TPL.job.lang || 'en') === 'zh';
    const probe = (TPL.job.fonts && TPL.job.fonts.probe) || [['Archivo', 800], ['Barlow Condensed', 800], ['Inter', 400], ['Inter', 700]]
      .concat(zh ? [['Noto Sans SC', 700]] : []);
    // every weight a role is drawn in, so a variable face is loaded once for all of them
    const faces = [];
    for (const [f] of probe) for (const x of [400, 500, 600, 700, 800, 900]) faces.push(`${x} 40px "${f}"`);
    let done = false;
    const go = () => { if (done) return; done = true; fn(); };
    if (!document.fonts || !document.fonts.load) return go();
    Promise.all(faces.map(f => document.fonts.load(f, zh ? 'Ag永' : 'Ag').catch(() => null))).then(go, go);
    setTimeout(go, 4000);
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
      'data-maxlines': opts.maxLines || '' }, opts.parent);
    TPL.markup(e, str);
    TPL.box(e, x, y, w, null);
    if (opts.align) e.style.textAlign = opts.align;
    return e;
  };
  /* Text with two marks (project change): [[words]] set in the accent (span.hi), {words} as a pill (span.pill).
     Everything else is plain text; nothing is parsed as HTML. */
  TPL.markup = function (e, str) {
    e.textContent = '';
    const text = str == null ? '' : String(str);
    const re = /\[\[([^\]]+)\]\]|\{([^}]+)\}/g; let last = 0, m;
    while ((m = re.exec(text))) {
      if (m.index > last) e.appendChild(document.createTextNode(text.slice(last, m.index)));
      const span = document.createElement('span'); span.className = m[1] != null ? 'hi' : 'pill'; span.textContent = m[1] != null ? m[1] : m[2];
      e.appendChild(span); last = re.lastIndex;
    }
    if (last < text.length) e.appendChild(document.createTextNode(text.slice(last)));
    return e;
  };
  TPL.plain = s => String(s == null ? '' : s).replace(/\[\[([^\]]+)\]\]/g, '$1').replace(/\{([^}]+)\}/g, '$1');

  /* ---------- graphic primitives (project change: the Amazon-listing vocabulary) ---------- */
  /* A gradient scrim under text on a photo: dir 'up' darkens towards the bottom edge, 'down' towards the top. */
  TPL.scrim = function (id, x, y, w, h, dir = 'up', alpha = 0.62, parent) {
    const e = TPL.el('div', { class: 'scrim', 'data-qa': id, 'data-kind': 'shape', 'data-exempt': '1' }, parent);
    TPL.box(e, x, y, w, h);
    const a = Math.max(0, Math.min(0.9, alpha));
    e.style.background = `linear-gradient(to ${dir === 'up' ? 'top' : 'bottom'}, rgba(12,10,9,${a}) 0%, rgba(12,10,9,${(a * 0.55).toFixed(3)}) 45%, rgba(12,10,9,0) 100%)`;
    return e;
  };
  /* An inline line icon from job.icons (SVG markup fetched by the renderer), drawn in currentColor. */
  TPL.icon = function (name, x, y, size, color, parent) {
    const markup = (TPL.job.icons || {})[name];
    if (!markup) return null;
    const holder = TPL.el('div', { class: 'icon', 'data-exempt': '1' }, parent);
    TPL.box(holder, x, y, size, size);
    holder.innerHTML = markup;
    const svg = holder.querySelector('svg');
    if (svg) { svg.setAttribute('width', size); svg.setAttribute('height', size); svg.style.display = 'block'; svg.querySelectorAll('[stroke-width]').forEach(n => n.setAttribute('stroke-width', '1.75')); }
    if (color) holder.style.color = color;
    return holder;
  };
  /* A spec chip: an icon cell then the value (number large, unit small) over a small uppercase label.
     Returns {w, h, els}; place with the returned move(). */
  TPL.chip = function (id, x, y, spec, opts = {}) {
    const h = opts.h || 104, iconW = spec.icon && (TPL.job.icons || {})[spec.icon] ? h : 0, pad = 22;
    const box = TPL.el('div', { class: 'chip' + (opts.dark ? ' chip-dark' : ''), 'data-qa': id, 'data-kind': 'shape', 'data-allow': [id + '_v', id + '_l'].join(',') }, opts.parent);
    if (iconW) { const cell = TPL.el('div', { class: 'chip-icon', 'data-exempt': '1' }, box); TPL.box(cell, 0, 0, iconW, h); TPL.icon(spec.icon, iconW * 0.22, h * 0.22, iconW * 0.56, null, cell); }
    const v = TPL.el('div', { class: 'txt chip-value', 'data-qa': id + '_v', 'data-kind': 'text', 'data-tier': 'important', 'data-maxlines': '1', 'data-allow': id }, box);
    v.textContent = spec.value != null ? String(spec.value) : String(spec.text || '');
    if (spec.unit) { const u = document.createElement('span'); u.className = 'chip-unit'; u.textContent = spec.unit; v.appendChild(u); }
    const l = TPL.el('div', { class: 'txt chip-label', 'data-qa': id + '_l', 'data-kind': 'text', 'data-tier': 'secondary', 'data-maxlines': '1', 'data-allow': id }, box, spec.label || '');
    TPL.box(v, iconW + pad, 12, null, null); TPL.box(l, iconW + pad, null, null, null);
    v.style.whiteSpace = 'nowrap'; l.style.whiteSpace = 'nowrap';
    const w = iconW + pad * 2 + Math.max(v.getBoundingClientRect().width, l.getBoundingClientRect().width);
    l.style.top = (h - 12 - l.getBoundingClientRect().height) + 'px';
    TPL.box(box, x, y, Math.ceil(w), h);
    return { w: Math.ceil(w), h, el: box, move(nx, ny) { box.style.left = nx + 'px'; box.style.top = ny + 'px'; } };
  };
  /* ---------- common elements (project change): brand badges and ground textures ---------- */
  /* A brand badge: a sticker-like mark (seal, burst, shield, ribbon, tag or pill) carrying one short brand text such as
     "2 YEAR WARRANTY" or "PRO". Drawn as its own inline SVG with the text as a page text element over it, so the editable
     export keeps the text live. Returns {w, h, el} or null when the text is empty. */
  TPL.BADGE_STYLES = ['seal', 'burst', 'shield', 'ribbon', 'tag', 'pill'];
  TPL.badgeSize = function (style) {
    return ({ seal: [200, 200], burst: [218, 218], shield: [180, 210], ribbon: [300, 92], tag: [290, 88], pill: [290, 74] })[style] || [200, 200];
  };
  /* The width a one-line badge (ribbon, tag, pill) needs for ``text`` at height h: the text at 44% of h plus the ends. */
  TPL.badgeWidth = function (text, style, h) {
    const c = document.createElement('canvas').getContext('2d'), zh = (TPL.job.lang || 'en') === 'zh';
    const fam = getComputedStyle(TPL.root).getPropertyValue(zh ? '--f-cjk' : '--f-cond') || 'sans-serif';
    c.font = `800 ${Math.round(h * 0.44)}px ${fam}`;
    const tw = c.measureText(zh ? String(text) : String(text).toUpperCase()).width * 1.06;
    const ends = style === 'ribbon' ? 2 * (h * 0.32 + 18) : style === 'tag' ? h / 2 + 40 : h * 0.84 + 8;
    return Math.round(Math.min(560, Math.max(h * 2.2, tw + ends)));
  };
  TPL.badge = function (id, text, x, y, style, opts = {}) {
    text = String(text || '').trim();
    if (!text) return null;
    style = TPL.BADGE_STYLES.includes(style) ? style : 'seal';
    let [w, h] = opts.size || TPL.badgeSize(style);
    if (!opts.size && ['ribbon', 'tag', 'pill'].includes(style)) w = TPL.badgeWidth(text, style, h);
    const ns = 'http://www.w3.org/2000/svg', box = TPL.el('div', { class: 'badge-el badge-' + style, 'data-qa': id, 'data-kind': 'shape', 'data-allow': id + '_t' }, opts.parent);
    TPL.box(box, x, y, w, h);
    const g = document.createElementNS(ns, 'svg'); g.setAttribute('width', w); g.setAttribute('height', h); g.setAttribute('viewBox', `0 0 ${w} ${h}`);
    g.style.position = 'absolute'; g.style.left = 0; g.style.top = 0; g.style.overflow = 'visible'; box.appendChild(g);
    const add = (tag, attrs) => { const e = document.createElementNS(ns, tag); for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); g.appendChild(e); return e; };
    const fill = opts.fill || 'var(--accent)', ink = opts.ink || 'var(--accent-ink,#fff)', cx = w / 2, cy = h / 2;
    let inner = [w * 0.16, h * 0.2, w * 0.68, h * 0.6];          // where the text goes: x, y, w, h
    if (style === 'seal') {
      add('circle', { cx, cy, r: w / 2 - 2, fill });
      add('circle', { cx, cy, r: w / 2 - 14, fill: 'none', stroke: ink, 'stroke-width': 3, 'stroke-dasharray': '2 7', 'stroke-linecap': 'round', opacity: 0.85 });
      inner = [w * 0.15, h * 0.2, w * 0.7, h * 0.6];
    } else if (style === 'burst') {
      const n = 18, pts = [];
      for (let i = 0; i < n * 2; i++) { const a = Math.PI * i / n - Math.PI / 2, r = i % 2 ? w / 2 * 0.84 : w / 2 - 1; pts.push((cx + r * Math.cos(a)).toFixed(1) + ',' + (cy + r * Math.sin(a)).toFixed(1)); }
      add('polygon', { points: pts.join(' '), fill });
      inner = [w * 0.19, h * 0.24, w * 0.62, h * 0.52];
    } else if (style === 'shield') {
      const d = `M${cx} 3 L${w - 4} ${h * 0.16} L${w - 4} ${h * 0.5} C${w - 4} ${h * 0.76} ${cx + w * 0.2} ${h * 0.9} ${cx} ${h - 3} C${cx - w * 0.2} ${h * 0.9} 4 ${h * 0.76} 4 ${h * 0.5} L4 ${h * 0.16} Z`;
      add('path', { d, fill: opts.dark || 'var(--ink,#141414)', stroke: fill, 'stroke-width': 6, 'stroke-linejoin': 'round' });
      inner = [w * 0.16, h * 0.24, w * 0.68, h * 0.46];
      box.dataset.ink = 'light';
    } else if (style === 'ribbon') {
      const k = h * 0.32;
      add('path', { d: `M0 0 H${w} L${w - k} ${cy} L${w} ${h} H0 L${k} ${cy} Z`, fill });
      inner = [k + 10, h * 0.14, w - 2 * k - 20, h * 0.72];
    } else if (style === 'tag') {
      const k = h / 2;
      add('path', { d: `M${k} 0 H${w - 10} Q${w} 0 ${w} 10 V${h - 10} Q${w} ${h} ${w - 10} ${h} H${k} L0 ${cy} Z`, fill });
      add('circle', { cx: k * 0.72, cy, r: 8, fill: ink, opacity: 0.9 });
      inner = [k + 8, h * 0.14, w - k - 26, h * 0.72];
    } else {
      add('rect', { x: 2, y: 2, width: w - 4, height: h - 4, rx: (h - 4) / 2, fill: 'none', stroke: fill, 'stroke-width': 5 });
      inner = [h * 0.42, h * 0.14, w - h * 0.84, h * 0.72];
      box.dataset.ink = 'accent';
    }
    const t = TPL.el('div', { class: 'txt badge-el-t', 'data-qa': id + '_t', 'data-kind': 'text', 'data-tier': 'secondary', 'data-maxlines': '3', 'data-allow': id }, box, text);
    const color = box.dataset.ink === 'light' ? 'var(--cream,#fff)' : box.dataset.ink === 'accent' ? 'var(--accent)' : ink;
    Object.assign(t.style, { position: 'absolute', left: inner[0] + 'px', top: inner[1] + 'px', width: inner[2] + 'px', textAlign: 'center', color,
      fontFamily: 'var(--f-cond)', fontWeight: 800, textTransform: 'uppercase', lineHeight: 1, letterSpacing: '.02em', textWrap: 'balance' });
    // the largest size at which the text fits the inner box in at most two lines
    const zh = (TPL.job.lang || 'en') === 'zh';
    if (zh) { t.style.fontFamily = 'var(--f-cjk)'; t.style.fontWeight = 900; t.style.letterSpacing = '0'; }
    const oneLine = ['pill', 'ribbon', 'tag'].includes(style);
    if (oneLine) t.style.whiteSpace = 'nowrap';
    let fs = Math.round(Math.min(oneLine ? h * 0.44 : inner[3] * 0.42, 72));
    // every word must fit the width on its own line (no word is broken), and the lines the height
    const probe = document.createElement('span'); probe.style.whiteSpace = 'nowrap';
    const widest = () => Math.max(...(oneLine ? [t.textContent] : zh ? [...t.textContent.replace(/\s+/g, '')] : t.textContent.split(/\s+/)).map(word => { probe.textContent = word; t.appendChild(probe); const pw = probe.getBoundingClientRect().width; probe.remove(); return pw; }));
    const fits = () => widest() <= inner[2] && t.getBoundingClientRect().height <= inner[3] + 1;
    t.style.fontSize = fs + 'px';
    while (!fits() && fs > 24) { fs -= 2; t.style.fontSize = fs + 'px'; }
    if (!fits()) { box.remove(); TPL.notes.push(`badge "${text}" does not fit a ${style} badge`); return null; }
    t.style.top = (inner[1] + (inner[3] - t.getBoundingClientRect().height) / 2) + 'px';     // centred in the shape
    if (opts.rotate) box.style.transform = `rotate(${opts.rotate}deg)`;
    return { w, h, el: box };
  };
  /* Where a badge of size w x h can go without covering text, panels or the product: the first of ``spots`` ([x, y]
     top-left corners) whose box, grown by ``pad``, meets none of the page's elements nor the product's outline. */
  TPL.freeSpot = function (w, h, spots, pad = 24, opts = {}) {
    const rects = [];
    for (const n of TPL.root.querySelectorAll('[data-qa]')) {
      const kind = n.dataset.kind || (n.tagName === 'IMG' ? 'product_image' : '');
      // words count wherever they stand: a figure in a block that the overlap check leaves alone is still not to be covered
      const words = opts.overPhotos && kind === 'text' && !/^brand_/.test(n.dataset.qa || '');
      if (!words && (n.dataset.exempt === '1' || n.closest('[data-exempt="1"]'))) continue;
      if (opts.overPhotos) {
        // A mark may sit on a full-bleed photograph (a scene), only not on the product in it: the photograph's frame
        // is no obstacle, the product's box inside it is (the middle of the frame when the box is not known).
        if (kind === 'shape' && n.dataset.bleed === '1' && n.classList.contains('detail-frame')) continue;
        // nor is a band or panel that is itself a ground for text (a fifth of the page or more): what stands on it is
        { const q = n.getBoundingClientRect(); if (kind === 'shape' && q.width * q.height > 0.2 * TPL.W * TPL.H) continue; }
        const frame = kind === 'detail_image' ? n.parentNode : null;
        if (frame && frame.dataset && frame.dataset.bleed === '1') {
          const c = TPL.root.getBoundingClientRect(), i = n.getBoundingClientRect(), f = frame.getBoundingClientRect();
          const pb = ((TPL.job.assets || {})[n.dataset.asset] || {}).product_box;
          const box = Array.isArray(pb) && pb.length === 4
            ? { x: i.left + pb[0] * i.width, y: i.top + pb[1] * i.height, w: (pb[2] - pb[0]) * i.width, h: (pb[3] - pb[1]) * i.height }
            : { x: f.left + f.width * 0.2, y: f.top + f.height * 0.2, w: f.width * 0.6, h: f.height * 0.6 };
          const x0 = Math.max(box.x, f.left), y0 = Math.max(box.y, f.top), x1 = Math.min(box.x + box.w, f.right), y1 = Math.min(box.y + box.h, f.bottom);
          if (x1 > x0 && y1 > y0) rects.push({ x: x0 - c.left, y: y0 - c.top, w: x1 - x0, h: y1 - y0 });
          continue;
        }
      }
      if (kind === 'product_image' && n.dataset.bleed) continue;
      const r = n.getBoundingClientRect(), c = TPL.root.getBoundingClientRect();
      if (r.width < 1 || r.height < 1) continue;
      if (kind === 'product_image') {
        const q = n.dataset.quad ? JSON.parse(n.dataset.quad) : (n.dataset.qa === 'hero' && (TPL.layoutInfo || {}).hero_content_quad) || null;
        if (q && q.length) { rects.push({ poly: q }); continue; }
        const bb = n.dataset.qa === 'hero' && (TPL.layoutInfo || {}).hero_bbox;
        if (bb) { rects.push({ x: bb[0], y: bb[1], w: bb[2], h: bb[3] }); continue; }
      }
      rects.push({ x: r.left - c.left, y: r.top - c.top, w: r.width, h: r.height });
    }
    const svg = TPL._svg;
    if (svg) for (const p of svg.querySelectorAll('path,rect,circle,line')) { const b = p.getBBox(); if (b.width + b.height > 2) rects.push({ x: b.x - 4, y: b.y - 4, w: b.width + 8, h: b.height + 8 }); }
    const inPoly = (x, y, q) => { let o = false; for (let i = 0, j = q.length - 1; i < q.length; j = i++) { const [xi, yi] = q[i], [xj, yj] = q[j]; if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) o = !o; } return o; };
    for (const [x, y] of spots) {
      const a = { x: x - pad, y: y - pad, w: w + 2 * pad, h: h + 2 * pad };
      if (x < 0 || y < 0 || x + w > TPL.W || y + h > TPL.H) continue;
      const hit = rects.some(r => {
        if (r.poly) {
          for (let i = 0; i <= 6; i++) for (let j = 0; j <= 6; j++) if (inPoly(a.x + a.w * i / 6, a.y + a.h * j / 6, r.poly)) return true;
          return r.poly.some(([px, py]) => px > a.x && px < a.x + a.w && py > a.y && py < a.y + a.h);
        }
        return r.x < a.x + a.w && r.x + r.w > a.x && r.y < a.y + a.h && r.y + r.h > a.y;
      });
      if (!hit) return [x, y];
    }
    return null;
  };
  /* The brand's badges (job.badges: [{text, style}]) set in the first free spots of ``area`` ({x, y, w, h}), corners first;
     a badge with no free spot is left out with a note. ``style`` 'auto' takes the page's own choice. */
  TPL.placeBadges = function (area, opts = {}) {
    const list = (TPL.job.badges || []).filter(b => b && String(b.text || '').trim()).slice(0, opts.max || 3);
    const placed = [];
    list.forEach((b, i) => {
      const style = b.style && b.style !== 'auto' ? b.style : (opts.style || 'seal');
      const [w0, h0] = TPL.badgeSize(style), g = 24;
      // at its own size, then smaller: a crowded page still has a corner for a smaller sticker
      let at = null, w = 0, h = 0, scale = 1;
      for (scale of [1, 0.8]) {
        h = Math.round(h0 * scale);
        w = ['ribbon', 'tag', 'pill'].includes(style) ? TPL.badgeWidth(b.text, style, h) : Math.round(w0 * scale);
        const L = area.x, T_ = area.y, R = area.x + area.w - w, B = area.y + area.h - h;
        const spots = [[R, T_], [L, T_], [R, B], [L, B], [R, T_ + h + g], [L, T_ + h + g], [R, B - h - g], [L, B - h - g]];
        // then anywhere in the area, nearest the right edge and the top first (a sticker reads as added, not as copy)
        const scan = [];
        for (let y = T_; y <= B; y += 32) for (let x = R; x >= L; x -= 32) scan.push([x, y]);
        scan.sort((a, b2) => ((R - a[0]) + (a[1] - T_) * 0.8) - ((R - b2[0]) + (b2[1] - T_) * 0.8));
        spots.push(...scan);
        at = TPL.freeSpot(w, h, spots, opts.pad ?? 20, opts);
        if (at) break;
      }
      if (!at) { TPL.notes.push(`no free spot for badge "${b.text}"`); return; }
      const made = TPL.badge('brand_badge' + (i + 1), b.text, at[0], at[1], style, { rotate: style === 'seal' || style === 'burst' ? -8 : 0, ...(scale < 1 ? { size: [w, h] } : {}) });
      if (made) placed.push(made);
      // on a photograph the badge lies over the scene by design (its spot was checked against the words and the product)
      if (made && opts.overPhotos) {
        const box = TPL.root.querySelector(`[data-qa="brand_badge${i + 1}"]`);
        for (const n of box ? [box, ...box.querySelectorAll('[data-qa]')] : []) n.dataset.exempt = '1';
      }
    });
    return placed;
  };
  /* A faint repeating texture over the ground (job.texture: dots, grid, diagonal, carbon or drafting), under everything
     else. 'drafting' is a draughtsman's sheet: a fine 24px grid with a heavier line every fifth cell. */
  TPL.texture = function (kind, opts = {}) {
    if (!['dots', 'grid', 'diagonal', 'carbon', 'drafting'].includes(kind)) return null;
    const ns = 'http://www.w3.org/2000/svg', s = document.createElementNS(ns, 'svg');
    const x = opts.x || 0, y = opts.y || 0, w = opts.w || TPL.W, h = opts.h || TPL.H, id = 'tx_' + kind;
    s.setAttribute('width', w); s.setAttribute('height', h); s.setAttribute('viewBox', `0 0 ${w} ${h}`);
    s.setAttribute('data-qa', 'texture'); s.setAttribute('data-exempt', '1'); s.setAttribute('data-kind', 'shape');
    Object.assign(s.style, { position: 'absolute', left: x + 'px', top: y + 'px', pointerEvents: 'none' });
    const ink = opts.ink || 'var(--text,#141414)', op = opts.opacity ?? 0.07;
    const cell = { dots: 28, grid: 48, diagonal: 22, carbon: 16, drafting: 120 }[kind];
    const fine = [24, 48, 72, 96].map(v => `M${v} 0V${cell}M0 ${v}H${cell}`).join('');
    const shapes = {
      dots: `<circle cx="${cell / 2}" cy="${cell / 2}" r="2.2" fill="${ink}"/>`,
      grid: `<path d="M${cell} 0V${cell}M0 ${cell}H${cell}" stroke="${ink}" stroke-width="1.5" fill="none"/>`,
      diagonal: `<path d="M0 ${cell}L${cell} 0" stroke="${ink}" stroke-width="2" fill="none"/>`,
      carbon: `<rect width="${cell / 2}" height="${cell / 2}" fill="${ink}"/><rect x="${cell / 2}" y="${cell / 2}" width="${cell / 2}" height="${cell / 2}" fill="${ink}"/>`,
      drafting: `<path d="${fine}" stroke="${ink}" stroke-width="1" fill="none" opacity=".55"/><path d="M${cell} 0V${cell}M0 ${cell}H${cell}" stroke="${ink}" stroke-width="2" fill="none"/>`
    }[kind];
    s.innerHTML = `<defs><pattern id="${id}" width="${cell}" height="${cell}" patternUnits="userSpaceOnUse">${shapes}</pattern></defs><rect width="${w}" height="${h}" fill="url(#${id})" opacity="${op}"/>`;
    TPL.root.insertBefore(s, opts.before || TPL.root.firstChild);
    return s;
  };
  /* A detail frame cut to a circle, with an accent ring and a soft shadow. */
  TPL.circleInset = function (id, assetId, cx, cy, r, opts = {}) {
    const f = TPL.detailFrame(id, assetId, cx - r, cy - r, 2 * r, 2 * r, { ...opts, class: 'circle-inset ' + (opts.class || '') });
    return f;
  };
  /* An orthogonal leader from a label's port to an anchor: one horizontal run then one vertical (or the reverse),
     whichever is shorter; ends in a square node on the anchor. */
  TPL.elbowLeader = function (id, port, anchor, labelId, anchorId, prefer) {
    const hv = [port, [anchor[0], port[1]], anchor], vh = [port, [port[0], anchor[1]], anchor];
    const straight = Math.abs(port[1] - anchor[1]) < 2 || Math.abs(port[0] - anchor[0]) < 2;
    // The brand preset's leader (job.style.leader): 'elbow' is the right-angle run above; 'straight' goes directly;
    // 'angled' runs level from the label for a third of the way, then slants to the point.
    const look = (TPL.job.style || {}).leader || {};
    let pts = straight ? [port, anchor] : (prefer === 'vh' ? vh : hv);
    if (!straight && look.route === 'straight') pts = [port, anchor];
    else if (!straight && look.route === 'angled') pts = [port, [port[0] + (anchor[0] - port[0]) * 0.34, port[1]], anchor];
    TPL.leader(id, pts, labelId, anchorId);
    Object.assign(TPL.leaders[TPL.leaders.length - 1], { route: straight ? 'straight' : (look.route || 'elbow'), end: look.end || 'square' });
    const [ax, ay] = anchor, from = pts[pts.length - 2];
    if (look.end === 'ring') { TPL.svgEl('circle', { cx: ax, cy: ay, r: 12, class: 'anchor-ring' }); TPL.svgEl('circle', { cx: ax, cy: ay, r: 5, class: 'anchor-dot' }); }
    else if (look.end === 'dot') TPL.svgEl('circle', { cx: ax, cy: ay, r: 8, class: 'anchor-dot' });
    else if (look.end === 'arrow') {
      const t = Math.atan2(ay - from[1], ax - from[0]), L = 26, w = 11, bx = ax - L * Math.cos(t), by = ay - L * Math.sin(t);
      TPL.svgEl('path', { d: `M${ax.toFixed(1)} ${ay.toFixed(1)}L${(bx - w * Math.sin(t)).toFixed(1)} ${(by + w * Math.cos(t)).toFixed(1)}L${(bx + w * Math.sin(t)).toFixed(1)} ${(by - w * Math.cos(t)).toFixed(1)}Z`, class: 'anchor-dot' });
    } else TPL.svgEl('rect', { x: ax - 9, y: ay - 9, width: 18, height: 18, class: 'node-square' });
    TPL.svgEl('circle', { cx: port[0], cy: port[1], r: 5, class: 'anchor-dot' });
    return pts;
  };
  /* A stat block: short accent rule, big number with its unit, small label. */
  TPL.stat = function (id, x, y, w, spec, opts = {}) {
    const holder = TPL.el('div', { class: 'stat' + (opts.dark ? ' on-dark' : ''), 'data-exempt': '1' }, opts.parent);
    TPL.box(holder, x, y, w, null);
    TPL.el('div', { class: 'stat-rule' }, holder);
    const v = TPL.el('div', { class: 'txt stat-value', 'data-qa': id + '_v', 'data-kind': 'text', 'data-tier': 'headline', 'data-maxlines': '1' }, holder);
    v.textContent = String(spec.value ?? spec.text ?? '');
    if (spec.unit) { const u = document.createElement('span'); u.className = 'stat-unit'; u.textContent = spec.unit; v.appendChild(u); }
    TPL.el('div', { class: 'txt stat-label', 'data-qa': id + '_l', 'data-kind': 'text', 'data-tier': 'secondary', 'data-maxlines': '2' }, holder, spec.label || '');
    return holder;
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
    // the brand's logo and legal line go on now that the page is laid out; the measurement below waits for the logo to load
    try { TPL.brandMarks(); } catch (e) { TPL.notes.push('brand marks failed: ' + String(e && e.message || e)); }
    const go = () => { try { TPL._measure(); } catch (e) { const s = document.createElement('script'); s.type = 'application/json'; s.id = 'qa-result'; s.textContent = JSON.stringify({issues: [{check: 'js_error', detail: String(e && e.stack || e)}]}); document.body.appendChild(s); } };
    // The layout may run after the load event (it waits for the fonts): measure once every picture it placed has loaded.
    const pictures = () => Promise.all([...document.images].map(i => i.complete ? null
      : new Promise(done => { i.addEventListener('load', done, { once: true }); i.addEventListener('error', done, { once: true }); })));
    let measured = false;
    const once = () => { if (!measured) { measured = true; go(); } };
    const settle = () => { pictures().then(once, once); setTimeout(once, 3000); };
    if (document.readyState === 'complete') settle(); else window.addEventListener('load', settle);
  };
  TPL._measure = function () {
    const W = TPL.W, H = TPL.H, issues = [], els = [];
    const nodes = [...document.querySelectorAll('[data-qa]')];
    for (const n of nodes) {
      const r = TPL.rect(n);
      const cs = getComputedStyle(n);
      const rec = { id: n.dataset.qa, kind: n.dataset.kind, x: +r.x.toFixed(1), y: +r.y.toFixed(1), w: +r.w.toFixed(1), h: +r.h.toFixed(1),
        allow: (n.dataset.allow || '').split(',').filter(Boolean), bleed: !!n.dataset.bleed, exempt: !!n.dataset.exempt, inframe: !!n.dataset.inframe };
      if (rec.kind === 'shape') { rec.radius = parseFloat(cs.borderTopLeftRadius) || 0; rec.fill = cs.backgroundColor; }
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
        // how the type is set and inked, so a change of brand preset can be seen in the record
        rec.text_transform = cs.textTransform; rec.font_stretch = cs.fontStretch; rec.font_style = cs.fontStyle; rec.color = cs.color;
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
        if (rec.equiv_px_at_360 < minEq && rec.id !== 'brand_legal') issues.push({ check: 'min_size_at_360', id: rec.id, detail: `${rec.equiv_px_at_360}px < ${minEq}px` });     // the legal line is small print by nature
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
    // project change: the faces the pages are designed in (brandkit/vendor/fonts.css), and a hard language check.
    const zhPage = (TPL.job.lang || 'en') === 'zh';
    const fams = (TPL.job.fonts && TPL.job.fonts.probe) || [['Archivo', 800, 'Hamburgefonstiv'], ['Barlow Condensed', 800, 'Hamburgefonstiv'], ['Inter', 400, 'Hamburgefonstiv'], ['Inter', 700, 'Hamburgefonstiv']]
      .concat(zhPage ? [['Noto Sans SC', 700, 'Hamburg控制部件'], ['Noto Sans SC', 400, 'Hamburg控制部件']] : []);
    if (!zhPage) for (const e of els) if (e.kind === 'text' && /[\u3400-\u9fff]/.test(e.text || '')) issues.push({ check: 'lang_mismatch', id: e.id, detail: 'Chinese characters on an English page' });
    const fonts = fams.map(([f, w, s]) => ({ family: f, weight: w, available: fontInUse(f, w, s) }));
    for (const f of fonts) if (!f.available) issues.push({ check: 'font_missing', id: f.family + ' ' + f.weight });
    const out = { template: TPL.job.template_id, variant: TPL.job.variant, canvas: [W, H], lang: TPL.job.lang, elements: els, leaders: TPL.leaders, overlaps, issues, fonts, notes: TPL.notes, layout: TPL.layoutInfo || {},
      preset: (TPL.job.style || {}).preset || null, ground: getComputedStyle(TPL.root).getPropertyValue('--bg').trim() };
    const s = document.createElement('script'); s.type = 'application/json'; s.id = 'qa-result';
    s.textContent = JSON.stringify(out);
    document.body.appendChild(s);
  };
})();
