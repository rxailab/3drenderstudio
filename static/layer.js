// Text and icon layer: editable vector annotations over a rendered shot.
// A layer is plain data in picture coordinates (0..1 across the frame; sizes as a
// share of the shorter side). The same function draws it on screen and into the
// exported SVG / PNG, so what is edited is what is exported.
// Loaded before app.js; uses its helpers ($, esc, api, toast, current, ...) at call time.

// Icons are drawn here on a 24 x 24 grid with a round 1.8 stroke. Names match info_layer.ICONS.
const LAYER_ICONS = {
  check: ['M5 12.5l4.5 4.5L19 7.5'],
  star: ['M12 3.5l2.6 5.4 5.9.8-4.3 4.1 1 5.8L12 16.9 6.8 19.6l1-5.8L3.5 9.7l5.9-.8z'],
  shield: ['M12 3l7 2.8v5.4c0 4.4-2.9 8.1-7 9.8-4.1-1.7-7-5.4-7-9.8V5.8z', 'M9 12l2.2 2.2L15.2 10'],
  lock: ['M6.5 10.5h11a1 1 0 0 1 1 1v7a1 1 0 0 1-1 1h-11a1 1 0 0 1-1-1v-7a1 1 0 0 1 1-1z', 'M8.5 10.5V8a3.5 3.5 0 0 1 7 0v2.5', 'M12 14.2v2.2'],
  drawer: ['M4 5h16v14H4z', 'M4 12h16', 'M10 8.5h4', 'M10 15.5h4'],
  wheel: ['M12 4.5a7.5 7.5 0 1 0 0 15 7.5 7.5 0 0 0 0-15z', 'M12 9.8a2.2 2.2 0 1 0 0 4.4 2.2 2.2 0 0 0 0-4.4z', 'M12 4.5v5.3M12 14.2v5.3M4.5 12h5.3M14.2 12h5.3'],
  layers: ['M12 4l8.5 4.5L12 13 3.5 8.5z', 'M3.5 12.5L12 17l8.5-4.5', 'M3.5 16.2L12 20.7l8.5-4.5'],
  ruler: ['M3 8.5h18v7H3z', 'M7 8.5v2.8M10.3 8.5v4M13.7 8.5v2.8M17 8.5v4'],
  drop: ['M12 3.5c3.2 4 5.5 6.9 5.5 10a5.5 5.5 0 0 1-11 0c0-3.1 2.3-6 5.5-10z'],
  leaf: ['M5 19c0-8 5-13.5 14.5-14 .3 9.5-5 14.5-13 14.5', 'M5 19c3-5 6.5-8 10.5-10'],
  bolt: ['M13 3L5.5 13.5H11L10 21l8.5-11.5H13z'],
  sparkle: ['M12 3.5l1.8 5.2 5.2 1.8-5.2 1.8L12 17.5l-1.8-5.2L5 10.5l5.2-1.8z', 'M18.5 16v4M16.5 18h4'],
  heart: ['M12 20s-7.5-4.6-7.5-10A4.3 4.3 0 0 1 12 7.2 4.3 4.3 0 0 1 19.5 10c0 5.4-7.5 10-7.5 10z'],
  handle: ['M5 9h14', 'M7 9v6a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2V9'],
  door: ['M6 3.5h12v17H6z', 'M12 3.5v17', 'M9.8 12h.4M13.8 12h.4'],
  box: ['M12 3.5l7.5 4.2v8.6L12 20.5l-7.5-4.2V7.7z', 'M4.5 7.7L12 12l7.5-4.3', 'M12 12v8.5'],
  truck: ['M2.5 6.5h11v9h-11z', 'M13.5 9.5h4l3 3v3h-7z', 'M7 15.5a1.8 1.8 0 1 0 0 3.6 1.8 1.8 0 0 0 0-3.6z', 'M17 15.5a1.8 1.8 0 1 0 0 3.6 1.8 1.8 0 0 0 0-3.6z'],
  clock: ['M12 4a8 8 0 1 0 0 16 8 8 0 0 0 0-16z', 'M12 7.5V12l3 2'],
  weight: ['M8.5 8h7l3 11.5h-13z', 'M12 4a2 2 0 1 0 0 4 2 2 0 0 0 0-4z'],
  tool: ['M14.5 4.5a4 4 0 0 0-3.9 5L4.5 15.6a1.8 1.8 0 0 0 0 2.6l1.3 1.3a1.8 1.8 0 0 0 2.6 0l6.1-6.1a4 4 0 0 0 5-3.9l-2.5 2.5-2.4-.6-.6-2.4z'],
  sun: ['M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z', 'M12 2.5V5M12 19v2.5M2.5 12H5M19 12h2.5M5.3 5.3l1.8 1.8M16.9 16.9l1.8 1.8M5.3 18.7l1.8-1.8M16.9 7.1l1.8-1.8'],
  wave: ['M3 9c2-2 4-2 6 0s4 2 6 0 4-2 6 0', 'M3 14.5c2-2 4-2 6 0s4 2 6 0 4-2 6 0'],
  flame: ['M12 3.5c1 3-1.5 4.5-1.5 7 0 1 .6 1.8 1.5 1.8s1.5-.8 1.5-1.8c2 1.5 3.5 3.5 3.5 6a5 5 0 0 1-10 0c0-4 3-6 5-13z'],
  bulb: ['M12 3.5a6 6 0 0 0-3.5 10.9V17h7v-2.6A6 6 0 0 0 12 3.5z', 'M10 20h4'],
  screen: ['M3.5 5h17v11h-17z', 'M9 20h6', 'M12 16v4'],
  power: ['M12 3.5v8', 'M7.3 6.5a7 7 0 1 0 9.4 0'],
  bag: ['M5.5 8h13l1 12h-15z', 'M9 8V6.5a3 3 0 0 1 6 0V8'],
  eye: ['M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z', 'M12 9.3a2.7 2.7 0 1 0 0 5.4 2.7 2.7 0 0 0 0-5.4z'],
  award: ['M12 3.5a5.5 5.5 0 1 0 0 11 5.5 5.5 0 0 0 0-11z', 'M8.8 13.5L7.5 20.5l4.5-2.5 4.5 2.5-1.3-7'],
  tag: ['M3.5 3.5h8l9 9-8 8-9-9z', 'M7.8 7.8h.4'],
  info: ['M12 4a8 8 0 1 0 0 16 8 8 0 0 0 0-16z', 'M12 11v5.5', 'M11.8 7.8h.4'],
  size: ['M4 4l6 6M4 4h5M4 4v5', 'M20 20l-6-6M20 20h-5M20 20v-5'],
  gear: ['M12 5.5a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13z', 'M12 9.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5z', 'M12 2.8v2.7M12 18.5v2.7M2.8 12h2.7M18.5 12h2.7M5.5 5.5l1.9 1.9M16.6 16.6l1.9 1.9M5.5 18.5l1.9-1.9M16.6 7.4l1.9-1.9'],
  dot: ['M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z'],
  arrow: ['M4 12h15', 'M13 6l6 6-6 6'],
  plus: ['M12 5v14M5 12h14'],
  home: ['M4 11l8-7 8 7', 'M6.5 9.5V20h11V9.5'],
  cup: ['M5 6h11v7a5 5 0 0 1-5 5h-1a5 5 0 0 1-5-5z', 'M16 8h1.5a2.5 2.5 0 0 1 0 5H16', 'M5 21h11'],
};
const LAYER_FONT = "'PingFang SC','Microsoft YaHei','Noto Sans CJK SC','Source Han Sans SC','Hiragino Sans GB',system-ui,sans-serif";
const LAYER_LINE = 1.28, LAYER_MAX_ITEMS = 40;
const LAYER_SWATCHES = ['#141414', '#ffffff', '#d92d20', '#f79009', '#1570ef', '#079455'];
const LAYER_TYPE_LABELS = {text: '文字', icon: '图标', callout: '标注'};

// ------------------------------------------------------------------ looks
// A look is how a layer is dressed: a style preset and one accent colour. 'plain' is the undecorated v23 drawing and
// what a layer without a style gets, so layers saved before styles existed look as they did.
const LAYER_THEMES = {clean: '简洁', bold: '醒目', card: '卡片', dark: '深色', tag: '色块'};
const LAYER_ACCENTS = ['#e8452c', '#f79009', '#ffc53d', '#12b76a', '#1570ef', '#7a5af8', '#141414'];
const LAYER_DISPLAY_FONT = "'Noto Sans SC'";        // the bundled 900 weight; headlines and titles
const LAYER_DISPLAY_URL = '/static/vendor/fonts/noto-sans-sc/noto-sans-sc-900.woff2';
function layerLookOf(layer) { return {theme: LAYER_THEMES[layer?.theme] ? layer.theme : 'plain', accent: /^#[0-9a-f]{6}$/i.test(layer?.accent || '') ? layer.accent : LAYER_ACCENTS[0]}; }
const layerStyled = look => look.theme !== 'plain';

// ------------------------------------------------------------------ measuring and geometry
let layerMeasureCanvas;
function layerMeasureContext() {
  if (layerMeasureCanvas === undefined) {
    layerMeasureCanvas = null;
    try { if (typeof OffscreenCanvas !== 'undefined') layerMeasureCanvas = new OffscreenCanvas(8, 8).getContext('2d'); } catch (_) { layerMeasureCanvas = null; }
  }
  return layerMeasureCanvas;
}
// Width of one line in em. The estimate mirrors info_layer.text_width and is what tests and the server use.
function layerEstimate(text) {
  let width = 0;
  for (const char of text) {
    if (char.codePointAt(0) >= 0x2e80) width += 1;
    else if (char === ' ') width += .3;
    else if ((char !== char.toLowerCase() && char === char.toUpperCase()) || 'mwMW@%'.includes(char)) width += .68;
    else if ("iljtfI.,:;!|'".includes(char)) width += .32;
    else width += .56;
  }
  return width;
}
// How a piece of text is set: family, weight and tracking (in em).
function layerType(weight = 400, display = false, tracking = 0) { return {family: display ? LAYER_DISPLAY_FONT + ',' + LAYER_FONT : LAYER_FONT, weight: display ? 900 : weight, tracking}; }
function layerMeasure(text, size, type) {
  const context = layerMeasureContext(), spacing = type.tracking * size * [...text].length;
  if (!context) return layerEstimate(text) * size + spacing;
  context.font = `${type.weight} ${size}px ${type.family}`;
  return context.measureText(text).width + spacing;
}
function layerWrap(text, size, type, limit) {
  const lines = [];
  for (const paragraph of String(text).split('\n')) {
    let line = '';
    for (const token of paragraph.match(/[A-Za-z0-9_.%+\-/]+|\s+|[\s\S]/gu) || []) {
      if (!line.trim() || layerMeasure(line + token, size, type) <= limit) line += token;
      else { lines.push(line.trimEnd()); line = token.trimStart(); }
    }
    lines.push(line.trimEnd());
  }
  return lines.length ? lines : [''];
}
function layerTextType(item, look) {
  if (!layerStyled(look)) return layerType(item.weight || 400);
  const role = item.role || 'body';
  return role === 'headline' ? layerType(900, true, -.01) : role === 'kicker' ? layerType(700, false, .08) : role === 'subline' ? layerType(Math.max(500, item.weight || 500), false, .03) : layerType(item.weight || 400);
}
// Everything a drawing or a hit test needs to know about one item, in picture pixels.
function layerBox(item, W, H, look = layerLook()) {
  const unit = Math.min(W, H), size = item.size * unit;
  if (item.type === 'icon') return {x: item.x * W - size / 2, y: item.y * H - size / 2, w: size, h: size, size};
  const width = item.w * W;
  if (item.type === 'text') {
    const type = layerTextType(item, look), kicker = layerStyled(look) && item.role === 'kicker';
    const padX = kicker ? size * .75 : 0, padY = kicker ? size * .36 : 0;
    const lines = layerWrap(item.text, size, type, Math.max(size, width - padX * 2));
    const height = lines.length * size * LAYER_LINE + padY * 2;
    if (!kicker) return {x: item.x * W, y: item.y * H, w: width, h: height, size, lines, type, padX, padY};
    // A kicker is a pill that hugs its words; the item's width only says where the words may wrap and how it is aligned.
    const hug = Math.min(width, Math.max(...lines.map(line => layerMeasure(line, size, type))) + padX * 2);
    const x = item.align === 'right' ? item.x * W + width - hug : item.align === 'center' ? item.x * W + (width - hug) / 2 : item.x * W;
    return {x, y: item.y * H, w: hug, h: height, size, lines, type, padX, padY, kicker};
  }
  if (!layerStyled(look)) {
    const detailSize = size * .7, iconSize = item.icon ? size * 1.45 : 0, gap = item.icon ? size * .4 : 0, pad = size * .35, padY = pad, titleType = layerType(700), detailType = layerType(400);
    const textWidth = Math.max(size, width - iconSize - gap - pad * 2);
    const title = layerWrap(item.title, size, titleType, textWidth), detail = item.detail ? layerWrap(item.detail, detailSize, detailType, textWidth) : [];
    const textHeight = title.length * size * LAYER_LINE + detail.length * detailSize * LAYER_LINE;
    return {x: item.x * W, y: item.y * H, w: width, h: Math.max(iconSize, textHeight) + pad * 2, size, detailSize, iconSize, gap, pad, padY, title, detail, textHeight, titleType, detailType};
  }
  // A styled callout: a round badge with the icon, then the title and its detail. The same measures in every style,
  // so changing the style never moves anything.
  const detailSize = size * .68, iconSize = item.icon ? size * 1.9 : 0, gap = item.icon ? size * .55 : 0, pad = size * .5, padY = size * .42;
  const titleType = layerType(700, look.theme !== 'clean'), detailType = layerType(400);
  const textWidth = Math.max(size, width - iconSize - gap - pad * 2);
  const title = layerWrap(item.title, size, titleType, textWidth), detail = item.detail ? layerWrap(item.detail, detailSize, detailType, textWidth) : [];
  const textHeight = title.length * size * LAYER_LINE + detail.length * detailSize * LAYER_LINE;
  return {x: item.x * W, y: item.y * H, w: width, h: Math.max(iconSize, textHeight) + padY * 2, size, detailSize, iconSize, gap, pad, padY, title, detail, textHeight, titleType, detailType};
}
function layerLuminance(color) { const value = parseInt(String(color).slice(1), 16); return (.2126 * (value >> 16 & 255) + .7152 * (value >> 8 & 255) + .0722 * (value & 255)) / 255; }
function layerContrast(color) { return layerLuminance(color) > .5 ? '#000000' : '#ffffff'; }
// Type that reads on a filled shape: white, or near-black on a light fill.
function layerOn(fill) { return layerLuminance(fill) > .62 ? '#141414' : '#ffffff'; }
const layerNumber = value => String(Math.round(value * 100) / 100);

// ------------------------------------------------------------------ drawing
function layerIconMarkup(name, x, y, size, color) {
  const paths = LAYER_ICONS[name]; if (!paths) return '';
  return `<g transform="translate(${layerNumber(x)} ${layerNumber(y)}) scale(${Math.round(size / 24 * 10000) / 10000})" fill="none" stroke="${esc(color)}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${paths.map(d => `<path d="${d}"/>`).join('')}</g>`;
}
// Lines of text as one <text>. ``mark`` is a phrase drawn in ``markColor`` wherever it occurs; ``effect`` names a filter.
function layerTextMarkup(lines, x, y, size, type, color, {anchor = 'start', opacity = 1, mark = '', markColor = '', effect = ''} = {}) {
  const first = y + (LAYER_LINE - 1) * size / 2 + size * .86;
  const spans = lines.map((line, index) => {
    const start = `<tspan x="${layerNumber(x)}" y="${layerNumber(first + index * size * LAYER_LINE)}">`, at = mark ? line.indexOf(mark) : -1;
    if (at < 0) return start + esc(line) + '</tspan>';
    return start + esc(line.slice(0, at)) + '</tspan>' + `<tspan fill="${esc(markColor)}">${esc(mark)}</tspan>` + (line.length > at + mark.length ? `<tspan>${esc(line.slice(at + mark.length))}</tspan>` : '');
  }).join('');
  return `<text font-family="${type.family}" font-size="${layerNumber(size)}" font-weight="${type.weight}"${type.tracking ? ` letter-spacing="${layerNumber(type.tracking * size)}"` : ''} fill="${esc(color)}"${opacity < 1 ? ` fill-opacity="${opacity}"` : ''} text-anchor="${anchor}"${effect ? ` filter="url(#${effect})"` : ''} xml:space="preserve">${spans}</text>`;
}
function layerPlateMarkup(plate, x, y, w, h, radius, effect = '') {
  return plate ? `<rect x="${layerNumber(x)}" y="${layerNumber(y)}" width="${layerNumber(w)}" height="${layerNumber(h)}" rx="${layerNumber(radius)}" fill="${esc(plate.color)}" fill-opacity="${plate.opacity ?? .85}"${effect ? ` filter="url(#${effect})"` : ''}/>` : '';
}
// The line from a label to the spot it describes: out of the side that faces the spot, one short level run, then straight to it.
function layerLeader(box, anchorX, anchorY, unit) {
  const centreX = box.x + box.w / 2, centreY = box.y + box.h / 2;
  let start, elbow;
  if (anchorX > box.x + box.w || anchorX < box.x) {
    const right = anchorX > box.x + box.w, edge = right ? box.x + box.w : box.x, run = Math.min(Math.abs(anchorX - edge) * .45, unit * .05) * (right ? 1 : -1);
    start = [edge, centreY]; elbow = [edge + run, centreY];
  } else {
    const below = anchorY > centreY, edge = below ? box.y + box.h : box.y, run = Math.min(Math.abs(anchorY - edge) * .45, unit * .05) * (below ? 1 : -1);
    start = [centreX, edge]; elbow = [centreX, edge + run];
  }
  return `M${layerNumber(start[0])} ${layerNumber(start[1])}L${layerNumber(elbow[0])} ${layerNumber(elbow[1])}L${layerNumber(anchorX)} ${layerNumber(anchorY)}`;
}
// Soft shadows and glows, sized to the picture. Light type gets a dark shadow, dark type a faint light glow: both stay readable on a photo without a box.
function layerDefs(W, H) {
  const unit = Math.min(W, H), n = value => layerNumber(unit * value);
  return `<defs><filter id="layer-shadow" x="-20%" y="-30%" width="140%" height="160%"><feDropShadow dx="0" dy="${n(.0025)}" stdDeviation="${n(.006)}" flood-color="#000" flood-opacity=".55"/></filter>`
    + `<filter id="layer-glow" x="-20%" y="-30%" width="140%" height="160%"><feDropShadow dx="0" dy="0" stdDeviation="${n(.007)}" flood-color="#fff" flood-opacity=".8"/></filter>`
    + `<filter id="layer-lift" x="-20%" y="-30%" width="140%" height="170%"><feDropShadow dx="0" dy="${n(.005)}" stdDeviation="${n(.011)}" flood-color="#000" flood-opacity=".28"/></filter></defs>`;
}
const layerEffect = color => layerLuminance(color) > .5 ? 'layer-shadow' : 'layer-glow';
function layerItemMarkup(item, W, H, look = layerLook()) {
  const unit = Math.min(W, H), box = layerBox(item, W, H, look), color = item.color || '#141414', styled = layerStyled(look);
  let body = '';
  if (item.type === 'icon') body = layerIconMarkup(item.icon, box.x, box.y, box.size, color);
  else if (item.type === 'text') {
    const role = styled ? item.role || 'body' : 'body', anchor = item.align === 'center' ? 'middle' : item.align === 'right' ? 'end' : 'start';
    if (box.kicker) {
      body = `<rect x="${layerNumber(box.x)}" y="${layerNumber(box.y)}" width="${layerNumber(box.w)}" height="${layerNumber(box.h)}" rx="${layerNumber(Math.min(box.h / 2, box.size * 1.1))}" fill="${esc(look.accent)}"/>`
        + layerTextMarkup(box.lines, box.x + box.w / 2, box.y + box.padY, box.size, box.type, layerOn(look.accent), {anchor: 'middle'});
    } else {
      const pad = box.size * .32, x = item.align === 'center' ? box.x + box.w / 2 : item.align === 'right' ? box.x + box.w : box.x;
      body = layerPlateMarkup(item.plate, box.x - pad, box.y - pad * .6, box.w + pad * 2, box.h + pad * 1.2, box.size * .22, styled ? 'layer-lift' : '');
      if (role === 'headline' && look.theme !== 'clean' && item.align !== 'center') {
        // A bar in the accent colour stands beside the headline, on the side the text is set from.
        const bar = box.size * .11, gap = box.size * .26, barX = item.align === 'right' ? box.x + box.w + gap : box.x - gap - bar;
        body += `<rect x="${layerNumber(barX)}" y="${layerNumber(box.y + box.size * .16)}" width="${layerNumber(bar)}" height="${layerNumber(box.h - box.size * .26)}" rx="${layerNumber(bar / 2)}" fill="${esc(look.accent)}"/>`;
      }
      body += layerTextMarkup(box.lines, x, box.y, box.size, box.type, color, {anchor, mark: styled && item.mark || '', markColor: look.accent, effect: styled && !item.plate ? layerEffect(color) : ''});
    }
  } else {
    // What lies on the picture itself (line, dot, bare text) uses the item's own colour; what lies on a card uses the card's.
    const card = !styled ? item.plate : look.theme === 'card' ? {color: '#ffffff', opacity: .97} : look.theme === 'dark' ? {color: '#16171a', opacity: .86} : look.theme === 'tag' ? {color: look.accent, opacity: 1}
      : item.plate ? (layerLuminance(color) > .5 ? {color: '#16171a', opacity: .82} : {color: '#ffffff', opacity: .95}) : null;
    const ink = !styled ? color : card ? layerOn(card.color) : color;
    if (item.anchor) {
      const ax = item.anchor[0] * W, ay = item.anchor[1] * H, path = layerLeader(box, ax, ay, unit), halo = layerContrast(color), stroke = Math.max(1, unit * (styled ? .0032 : .0028));
      body += `<path d="${path}" fill="none" stroke="${halo}" stroke-opacity=".35" stroke-width="${layerNumber(stroke * 2.6)}" stroke-linecap="round" stroke-linejoin="round"/>`
        + `<path d="${path}" fill="none" stroke="${esc(color)}" stroke-width="${layerNumber(stroke)}" stroke-linecap="round" stroke-linejoin="round"/>`;
      body += styled
        ? `<circle cx="${layerNumber(ax)}" cy="${layerNumber(ay)}" r="${layerNumber(unit * .017)}" fill="${esc(look.accent)}" fill-opacity=".3"/><circle cx="${layerNumber(ax)}" cy="${layerNumber(ay)}" r="${layerNumber(unit * .0085)}" fill="${esc(look.accent)}" stroke="#ffffff" stroke-width="${layerNumber(stroke * 1.1)}"/>`
        : `<circle cx="${layerNumber(ax)}" cy="${layerNumber(ay)}" r="${layerNumber(unit * .0075)}" fill="${esc(color)}" stroke="${halo}" stroke-opacity=".6" stroke-width="${layerNumber(stroke)}"/>`;
    }
    body += layerPlateMarkup(card, box.x, box.y, box.w, box.h, styled ? box.size * .5 : box.size * .3, styled && card ? 'layer-lift' : '');
    const iconY = box.y + (box.h - box.iconSize) / 2, effect = styled && !card ? layerEffect(ink) : '';
    if (item.icon && !styled) body += layerIconMarkup(item.icon, box.x + box.pad, iconY, box.iconSize, color);
    else if (item.icon) {
      // The badge: a filled disc in the accent colour, a white one on an accent card, a ring in the plain-spoken style.
      const r = box.iconSize / 2, cx = box.x + box.pad + r, cy = iconY + r, inner = box.iconSize * .56;
      const fill = look.theme === 'clean' ? '' : look.theme === 'tag' ? '#ffffff' : look.accent, mark = look.theme === 'clean' ? ink : look.theme === 'tag' ? look.accent : layerOn(look.accent);
      body += fill ? `<circle cx="${layerNumber(cx)}" cy="${layerNumber(cy)}" r="${layerNumber(r)}" fill="${esc(fill)}"/>` : `<circle cx="${layerNumber(cx)}" cy="${layerNumber(cy)}" r="${layerNumber(r - box.size * .05)}" fill="none" stroke="${esc(ink)}" stroke-width="${layerNumber(box.size * .09)}"${effect ? ` filter="url(#${effect})"` : ''}/>`;
      body += layerIconMarkup(item.icon, cx - inner / 2, cy - inner / 2, inner, mark);
    }
    const textX = box.x + box.pad + box.iconSize + box.gap, textY = box.y + (box.h - box.textHeight) / 2;
    body += layerTextMarkup(box.title, textX, textY, box.size, box.titleType, ink, {effect});
    if (box.detail.length) body += layerTextMarkup(box.detail, textX, textY + box.title.length * box.size * LAYER_LINE, box.detailSize, box.detailType, ink, {opacity: styled ? .78 : .82, effect});
  }
  return `<g data-item="${esc(item.id)}" data-type="${item.type}">${body}</g>`;
}
function layerMarkup(items, W, H, look = layerLook()) { return (layerStyled(look) ? layerDefs(W, H) : '') + items.map(item => layerItemMarkup(item, W, H, look)).join(''); }
// A complete SVG file. With ``image`` (a URL or data URL) the render lies under the layer; ``fonts`` is CSS that carries the display font with it.
function layerSvgDocument(items, W, H, image = '', look = layerLook(), fonts = '') {
  return `<?xml version="1.0" encoding="UTF-8"?>\n<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">`
    + (fonts ? `<style>${fonts}</style>` : '')
    + (image ? `<image id="render" href="${esc(image)}" xlink:href="${esc(image)}" x="0" y="0" width="${W}" height="${H}"/>` : '')
    + `<g id="layer">${layerMarkup(items, W, H, look)}</g></svg>\n`;
}
// The display font as CSS with the file inside it, so an exported picture or SVG carries its own headline type.
let layerFontCssPromise = null;
function layerFontCss() {
  layerFontCssPromise ||= (async () => {
    const blob = await (await fetch(LAYER_DISPLAY_URL)).blob();
    const data = await new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.onerror = () => reject(new Error('font')); reader.readAsDataURL(blob); });
    return `@font-face{font-family:'Noto Sans SC';font-weight:900;font-style:normal;src:url(${data}) format('woff2');}`;
  })().catch(() => { layerFontCssPromise = null; return ''; });
  return layerFontCssPromise;
}

// ------------------------------------------------------------------ compositions
// A composition (layer.layout) is a designed canvas: the render placed in a frame, few words in a restrained type
// system, icons from open sets. Geometry comes from layer_compose.py; this draws it. Older layers keep the drawing above.
const COMP_FONT = "Inter,'PingFang SC','Microsoft YaHei','Noto Sans CJK SC','Source Han Sans SC','Hiragino Sans GB','WenQuanYi Zen Hei',system-ui,sans-serif";
const COMP_FIGURE = {unit: .4, label: .3, gap: .22}, COMP_ROW = {detail: .78, icon: 1.5};
const COMP_LAYOUTS = {split: '左右分区', callouts: '结构标注', scene: '场景叙事', cards: '卡片网格', spotlight: '单点卖点', specs: '规格清单'};
const COMP_REFERENCES = {split: '参考 01、05', callouts: '参考 02、07、11', scene: '参考 03、09', cards: '参考 04、06', spotlight: '参考 08', specs: '参考 10'};
function compType(weight = 400, tracking = 0) { return {family: COMP_FONT, weight, tracking}; }
function compTextType(item) {
  const role = item.role || 'body';
  return role === 'kicker' ? compType(item.weight || 600, .14) : role === 'headline' ? compType(item.weight || 700, -.01) : compType(item.weight || 400, role === 'caption' ? .01 : 0);
}
// A glyph from the layer's own icons, drawn on its grid: strokes keep the set's weight, fills keep their holes.
function compGlyphMarkup(glyph, x, y, size, color) {
  if (!glyph || !Array.isArray(glyph.ops)) return '';
  const grid = Number(glyph.grid) || 24, k = Math.round(size / grid * 10000) / 10000, stroke = Number(glyph.stroke) || 2;
  const paths = glyph.ops.filter(op => Array.isArray(op) && /^[MmLlHhVvCcSsQqTtAaZz0-9eE.,\s+-]+$/.test(op[1] || '')).map(([mode, d]) => mode === 's'
    ? `<path d="${d}" fill="none" stroke="${esc(color)}" stroke-width="${stroke}" stroke-linecap="round" stroke-linejoin="round"/>`
    : `<path d="${d}" fill="${esc(color)}"${mode === 'e' ? ' fill-rule="evenodd"' : ''}/>`).join('');
  return `<g transform="translate(${layerNumber(x)} ${layerNumber(y)}) scale(${k})">${paths}</g>`;
}
function compIconMarkup(item, x, y, size, color, glyphs) {
  const glyph = item.glyph && glyphs?.[item.glyph];
  return glyph ? compGlyphMarkup(glyph, x, y, size, color) : layerIconMarkup(item.icon, x, y, size, color);
}
function compLeading(item) { return Number(item.leading) || LAYER_LINE; }
// Lines of text set from a top edge, each line ``leading`` apart.
function compTextMarkup(lines, x, y, size, type, color, {anchor = 'start', leading = LAYER_LINE, opacity = 1} = {}) {
  const first = y + (leading - 1) * size / 2 + size * .86;
  const spans = lines.map((line, index) => `<tspan x="${layerNumber(x)}" y="${layerNumber(first + index * size * leading)}">${esc(line)}</tspan>`).join('');
  return `<text font-family="${type.family}" font-size="${layerNumber(size)}" font-weight="${type.weight}"${type.tracking ? ` letter-spacing="${layerNumber(type.tracking * size)}"` : ''} fill="${esc(color)}"${opacity < 1 ? ` fill-opacity="${opacity}"` : ''} text-anchor="${anchor}" xml:space="preserve">${spans}</text>`;
}
function compBox(item, W, H, glyphs) {
  const unit = Math.min(W, H), size = item.size * unit;
  if (item.type === 'panel') return {x: item.x * W, y: item.y * H, w: item.w * W, h: item.h * H, size};
  if (item.type === 'rule') {
    const x = Math.min(item.x, item.x2) * W, y = Math.min(item.y, item.y2) * H, pad = Math.max(3, unit * .006);
    return {x: x - pad, y: y - pad, w: Math.abs(item.x2 - item.x) * W + pad * 2, h: Math.abs(item.y2 - item.y) * H + pad * 2, size};
  }
  if (item.type === 'icon') return {x: item.x * W - size / 2, y: item.y * H - size / 2, w: size, h: size, size};
  const width = item.w * W;
  if (item.type === 'text') {
    const type = compTextType(item), leading = compLeading(item), lines = layerWrap(item.text, size, type, Math.max(size, width));
    return {x: item.x * W, y: item.y * H, w: width, h: lines.length * size * leading, size, lines, type, leading};
  }
  if (item.type === 'figure') {
    const valueType = compType(600, -.02), unitSize = size * COMP_FIGURE.unit, labelSize = size * COMP_FIGURE.label;
    const icon = item.glyph && glyphs?.[item.glyph] ? size * .62 : 0, iconGap = icon ? size * .28 : 0;
    const valueWidth = layerMeasure(item.value, size, valueType), unitWidth = item.unit ? layerMeasure(item.unit, unitSize, compType(500)) + size * .08 : 0;
    const label = item.label ? layerWrap(item.label, labelSize, compType(400), width) : [];
    const height = size * 1.02 + (label.length ? size * COMP_FIGURE.gap + label.length * labelSize * 1.4 : 0);
    return {x: item.x * W, y: item.y * H, w: width, h: height, size, unitSize, labelSize, icon, iconGap, valueWidth, unitWidth, label, valueType};
  }
  if (item.type === 'chips') {
    const chipSize = size * .62, labelSize = size * .5, padX = size * .55, gap = size * .28, chipH = size * 1.5;
    const label = (item.label || '') + (item.unit ? (compHasCjk(item.label) ? `（${item.unit}）` : ` (${item.unit})`) : '');
    const chipWidths = item.values.map(value => Math.max(chipH, layerMeasure(value, chipSize, compType(600)) + padX * 2));
    const rows = []; let row = [], rowWidth = 0;
    chipWidths.forEach((chip, index) => { if (row.length && rowWidth + gap + chip > width) { rows.push(row); row = []; rowWidth = 0; } row.push(index); rowWidth += (row.length > 1 ? gap : 0) + chip; });
    if (row.length) rows.push(row);
    const top = label ? labelSize * 1.6 : 0;
    return {x: item.x * W, y: item.y * H, w: width, h: top + rows.length * chipH + (rows.length - 1) * gap, size, chipSize, labelSize, padX, gap, chipH, chipWidths, rows, top, label};
  }
  // callouts in a composition: a row with its icon, or a note with a hairline to its part
  const row = item.style === 'row', icon = row && (item.glyph || item.icon) ? size * COMP_ROW.icon : 0, gap = icon ? size * .7 : 0;
  const textWidth = Math.max(size, width - icon - gap), titleType = compType(600), detailType = compType(400), detailSize = size * COMP_ROW.detail;
  const title = layerWrap(item.title, size, titleType, textWidth), detail = item.detail ? layerWrap(item.detail, detailSize, detailType, textWidth) : [];
  const textHeight = title.length * size * 1.3 + detail.length * detailSize * 1.45;
  return {x: item.x * W, y: item.y * H, w: width, h: Math.max(icon, textHeight), size, icon, gap, title, detail, detailSize, titleType, detailType, textHeight};
}
function compItemMarkup(item, W, H, layer) {
  const unit = Math.min(W, H), glyphs = layer.glyphs || {}, box = compBox(item, W, H, glyphs), color = item.color || '#1d1d1f', soft = item.soft || '#6e6e73';
  let body = '';
  if (item.type === 'panel') {
    const radius = (item.radius || 0) * unit, opacity = item.opacity ?? 1;
    if (item.fade) {
      const id = 'fade-' + item.id.replace(/[^A-Za-z0-9_-]/g, ''), [x1, y1, x2, y2] = {up: [0, 1, 0, 0], down: [0, 0, 0, 1], left: [1, 0, 0, 0], right: [0, 0, 1, 0]}[item.fade];
      body = `<defs><linearGradient id="${id}" x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}"><stop offset="0" stop-color="${esc(color)}" stop-opacity="${opacity}"/><stop offset=".55" stop-color="${esc(color)}" stop-opacity="${layerNumber(opacity * .45)}"/><stop offset="1" stop-color="${esc(color)}" stop-opacity="0"/></linearGradient></defs>`
        + `<rect x="${layerNumber(box.x)}" y="${layerNumber(box.y)}" width="${layerNumber(box.w)}" height="${layerNumber(box.h)}" fill="url(#${id})"/>`;
    } else body = `<rect x="${layerNumber(box.x)}" y="${layerNumber(box.y)}" width="${layerNumber(box.w)}" height="${layerNumber(box.h)}" rx="${layerNumber(radius)}" fill="${esc(color)}"${opacity < 1 ? ` fill-opacity="${opacity}"` : ''}/>`;
  } else if (item.type === 'rule') {
    body = `<line x1="${layerNumber(item.x * W)}" y1="${layerNumber(item.y * H)}" x2="${layerNumber(item.x2 * W)}" y2="${layerNumber(item.y2 * H)}" stroke="${esc(color)}" stroke-opacity="${item.opacity ?? 1}" stroke-width="${layerNumber(Math.max(1, (item.width || .0016) * unit))}" stroke-linecap="round"/>`;
  } else if (item.type === 'icon') {
    body = compIconMarkup(item, box.x, box.y, box.size, color, glyphs);
  } else if (item.type === 'text') {
    const anchor = item.align === 'center' ? 'middle' : item.align === 'right' ? 'end' : 'start', x = item.align === 'center' ? box.x + box.w / 2 : item.align === 'right' ? box.x + box.w : box.x;
    body = compTextMarkup(box.lines, x, box.y, box.size, box.type, color, {anchor, leading: box.leading});
  } else if (item.type === 'figure') {
    const total = box.icon + box.iconGap + box.valueWidth + box.unitWidth, align = item.align || 'left';
    const start = align === 'center' ? box.x + (box.w - total) / 2 : align === 'right' ? box.x + box.w - total : box.x, baseline = box.y + box.size * .86;
    if (box.icon) body += compIconMarkup(item, start, baseline - box.size * .74, box.icon, color, glyphs);
    const valueX = start + box.icon + box.iconGap;
    body += `<text font-family="${COMP_FONT}" font-size="${layerNumber(box.size)}" font-weight="600" letter-spacing="${layerNumber(-.02 * box.size)}" fill="${esc(color)}" xml:space="preserve"><tspan x="${layerNumber(valueX)}" y="${layerNumber(baseline)}">${esc(item.value)}</tspan>`
      + (item.unit ? `<tspan x="${layerNumber(valueX + box.valueWidth + box.size * .08)}" y="${layerNumber(baseline)}" font-size="${layerNumber(box.unitSize)}" font-weight="500" letter-spacing="0">${esc(item.unit)}</tspan>` : '') + '</text>';
    if (box.label.length) {
      const anchor = align === 'center' ? 'middle' : align === 'right' ? 'end' : 'start', x = align === 'center' ? box.x + box.w / 2 : align === 'right' ? box.x + box.w : box.x;
      body += compTextMarkup(box.label, x, box.y + box.size * (1.02 + COMP_FIGURE.gap), box.labelSize, compType(400, .02), item.soft || soft, {anchor, leading: 1.4});
    }
  } else if (item.type === 'chips') {
    if (box.label) body += compTextMarkup([box.label], box.x, box.y, box.labelSize, compType(500, .04), soft, {leading: 1.2});
    box.rows.forEach((row, rowIndex) => {
      let x = box.x; const y = box.y + box.top + rowIndex * (box.chipH + box.gap);
      row.forEach(index => {
        const width = box.chipWidths[index];
        body += `<rect x="${layerNumber(x)}" y="${layerNumber(y)}" width="${layerNumber(width)}" height="${layerNumber(box.chipH)}" rx="${layerNumber(box.chipH / 2)}" fill="${esc(item.fill || '#ffffff')}" stroke="${esc(color)}" stroke-opacity=".14" stroke-width="${layerNumber(Math.max(1, unit * .0012))}"/>`
          + `<text font-family="${COMP_FONT}" font-size="${layerNumber(box.chipSize)}" font-weight="600" fill="${esc(color)}" text-anchor="middle" xml:space="preserve"><tspan x="${layerNumber(x + width / 2)}" y="${layerNumber(y + box.chipH / 2 + box.chipSize * .35)}">${esc(item.values[index])}</tspan></text>`;
        x += width + box.gap;
      });
    });
  } else {
    const titleY = box.y + (item.style === 'row' ? Math.max(0, (box.h - box.textHeight) / 2) : 0), textX = box.x + box.icon + box.gap;
    if (item.style === 'line' && item.anchor) {
      // A hairline from the note to its part: level with the note's first line, one bend, a dot on the part.
      const ax = item.anchor[0] * W, ay = item.anchor[1] * H, ly = box.y + box.size * .66, gap = unit * .014, startX = box.x - gap;
      const elbow = Math.min(startX, Math.max(ax, (item.elbow ?? (box.x / W - .05)) * W)), stroke = Math.max(1, unit * .0018);
      body += `<path d="M${layerNumber(startX)} ${layerNumber(ly)}H${layerNumber(elbow)}L${layerNumber(ax)} ${layerNumber(ay)}" fill="none" stroke="${esc(color)}" stroke-opacity=".55" stroke-width="${layerNumber(stroke)}" stroke-linecap="round" stroke-linejoin="round"/>`
        + `<circle cx="${layerNumber(ax)}" cy="${layerNumber(ay)}" r="${layerNumber(unit * .0062)}" fill="${esc(color)}" stroke="#ffffff" stroke-opacity=".9" stroke-width="${layerNumber(unit * .0022)}"/>`;
    }
    if (box.icon) body += compIconMarkup(item, box.x, titleY + (box.textHeight > box.icon ? (box.size * 1.3 - box.icon) / 2 : (box.h - box.icon) / 2 - (box.h - box.textHeight) / 2 + Math.max(0, (box.textHeight - box.icon) / 2)), box.icon, color, glyphs);
    body += compTextMarkup(box.title, textX, titleY, box.size, box.titleType, color, {leading: 1.3});
    if (box.detail.length) body += compTextMarkup(box.detail, textX, titleY + box.title.length * box.size * 1.3, box.detailSize, box.detailType, soft, {leading: 1.45});
  }
  return `<g data-item="${esc(item.id)}" data-type="${item.type}">${body}</g>`;
}
// The canvas and the photo in its frame. ``image`` is the render's URL (or a data URL in an exported file).
function compCanvasMarkup(layer, W, H, image, background = true) {
  // Without a canvas the words are set on the picture itself, which fills the frame.
  const canvas = layer.canvas; if (!canvas) return image ? `<image href="${esc(image)}" x="0" y="0" width="${W}" height="${H}" preserveAspectRatio="none"/>` : '';
  let markup = background ? `<rect x="0" y="0" width="${W}" height="${H}" fill="${esc(canvas.color)}"/>` : '';
  const photo = canvas.photo; if (!photo || !image) return markup;
  const [c0, c1, c2, c3] = photo.crop, fx = photo.x * W, fy = photo.y * H, fw = photo.w * W, fh = photo.h * H, unit = Math.min(W, H);
  const id = 'photo-clip', radius = (photo.radius || 0) * unit;
  markup += `<defs><clipPath id="${id}"><rect x="${layerNumber(fx)}" y="${layerNumber(fy)}" width="${layerNumber(fw)}" height="${layerNumber(fh)}" rx="${layerNumber(radius)}"/></clipPath></defs>`;
  if (photo.fill) markup += `<rect x="${layerNumber(fx)}" y="${layerNumber(fy)}" width="${layerNumber(fw)}" height="${layerNumber(fh)}" rx="${layerNumber(radius)}" fill="${esc(photo.fill)}"/>`;
  // The render's own pixels sit at (0,0)-(W,H) of the crop's coordinate system.
  let inner = `<image href="${esc(image)}" x="0" y="0" width="${W}" height="${H}" preserveAspectRatio="none"/>`;
  if (photo.feather) {
    // Where the render ends inside the frame it fades into the canvas, so a studio sweep shows no seam.
    const band = Math.min(W, H) * .07, color = esc(photo.fill || canvas.color);
    inner += `<defs><linearGradient id="feather-x" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="${color}"/><stop offset="1" stop-color="${color}" stop-opacity="0"/></linearGradient><linearGradient id="feather-y" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${color}"/><stop offset="1" stop-color="${color}" stop-opacity="0"/></linearGradient></defs>`
      + `<rect x="0" y="0" width="${layerNumber(band)}" height="${H}" fill="url(#feather-x)"/><rect x="${layerNumber(W - band)}" y="0" width="${layerNumber(band)}" height="${H}" fill="url(#feather-x)" transform="rotate(180 ${layerNumber(W - band / 2)} ${H / 2})"/>`
      + `<rect x="0" y="0" width="${W}" height="${layerNumber(band)}" fill="url(#feather-y)"/><rect x="0" y="${layerNumber(H - band)}" width="${W}" height="${layerNumber(band)}" fill="url(#feather-y)" transform="rotate(180 ${W / 2} ${layerNumber(H - band / 2)})"/>`;
  }
  markup += `<g clip-path="url(#${id})"><svg x="${layerNumber(fx)}" y="${layerNumber(fy)}" width="${layerNumber(fw)}" height="${layerNumber(fh)}" viewBox="${layerNumber(c0 * W)} ${layerNumber(c1 * H)} ${layerNumber((c2 - c0) * W)} ${layerNumber((c3 - c1) * H)}" preserveAspectRatio="none" overflow="hidden">${inner}</svg></g>`;
  return markup;
}
// A whole composition: canvas, photo and items, as SVG content in picture pixels.
function compMarkup(layer, W, H, image) { return compCanvasMarkup(layer, W, H, image) + (layer.items || []).map(item => compItemMarkup(item, W, H, layer)).join(''); }
function layerComposed(layer) { return !!(layer && layer.layout && COMP_LAYOUTS[layer.layout]); }
const LAYER_LANGUAGES = ['en', 'zh'];
// The language of the next smart layout: one just picked in the panel, else the layer's own, else the last one picked here, else English.
function layerLanguage() {
  const own = layerState.content?.language, chosen = (() => { try { return localStorage.getItem('studio.layerLanguage'); } catch (_) { return null; } })();
  return [layerState.languageChoice, own, chosen].find(value => LAYER_LANGUAGES.includes(value)) || 'en';
}
function compHasCjk(text) { return /[\u3400-\u9fff]/.test(String(text || '')); }
// Small diagrams of the six layouts for the picker: the photo as a filled shape, words as lines.
const COMP_SKETCHES = {
  split: '<rect x="13" y="4" width="8" height="16" rx="1.5" fill="currentColor" opacity=".35"/><path d="M3 6h6M3 9h5M3 16h4M3 19h3" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>',
  callouts: '<rect x="3" y="4" width="10" height="16" rx="1.5" fill="currentColor" opacity=".35"/><path d="M16 7h5M16 12h5M16 17h5M10 7h5M10 12h5M10 17h5" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>',
  scene: '<rect x="2" y="3" width="20" height="18" rx="1.5" fill="currentColor" opacity=".35"/><path d="M5 7h8M5 10h5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>',
  cards: '<rect x="3" y="3" width="11" height="18" rx="1.5" fill="currentColor" opacity=".35"/><rect x="15.5" y="3" width="5.5" height="5" rx="1" fill="none" stroke="currentColor" stroke-width="1.2"/><rect x="15.5" y="9.5" width="5.5" height="5" rx="1" fill="none" stroke="currentColor" stroke-width="1.2"/><rect x="15.5" y="16" width="5.5" height="5" rx="1" fill="none" stroke="currentColor" stroke-width="1.2"/>',
  spotlight: '<path d="M8 5h8" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/><rect x="8" y="8" width="8" height="8" rx="1.5" fill="currentColor" opacity=".35"/><rect x="2" y="17.5" width="20" height="3.5" fill="currentColor"/>',
  specs: '<path d="M3 5h8" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/><rect x="7" y="8" width="10" height="7" rx="1.5" fill="currentColor" opacity=".35"/><path d="M3 18h4M10 18h4M17 18h4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/><path d="M8.5 17v3M15.5 17v3" stroke="currentColor" stroke-width="1" opacity=".5"/>',
};

// ------------------------------------------------------------------ editor state
const layerState = {key: '', items: [], theme: null, accent: null, basis: null, source: null, layout: null, canvas: null, content: null, glyphs: null, revision: 0, selected: null,
  dirty: false, saving: null, inflight: 0, timer: null, undo: [], redo: [], coalesce: '', visible: true, report: null, error: '', busy: false, drag: null, editing: null, guides: [], pressed: null,
  iconQuery: '', iconResults: null, iconBusy: false, iconError: ''};
// What is stored and undone besides the items: how a layer is dressed, or the composition it belongs to.
const LAYER_FIELDS = ['theme', 'accent', 'layout', 'canvas', 'content', 'glyphs'];
function layerFieldsOf(source) { return Object.fromEntries(LAYER_FIELDS.map(key => [key, source?.[key] ? structuredCloneSafe(source[key]) : null])); }
function layerCurrent() { return {items: layerState.items, ...layerFieldsOf(layerState)}; }
function layerItemBox(item, W, H) { return layerComposed(layerState) ? compBox(item, W, H, layerState.glyphs || {}) : layerBox(item, W, H); }
function layerEditing() { return inspectorTab === 'overlay'; }
function layerLook() { return layerLookOf(layerState); }
// What undo restores: the items and how they are dressed.
function layerSnapshot() { return JSON.stringify({items: layerState.items, ...Object.fromEntries(LAYER_FIELDS.map(key => [key, layerState[key]]))}); }
function layerRestore(snapshot) { const value = JSON.parse(snapshot); Object.assign(layerState, {items: value.items, ...Object.fromEntries(LAYER_FIELDS.map(key => [key, value[key] || null]))}); }
function layerKey() { return current ? current.id + ':' + activeShot : ''; }
function layerSelected() { return layerState.items.find(item => item.id === layerState.selected) || null; }
function layerSize() { const image = $('renderImage'); return currentOutput && !image.hidden && image.naturalWidth ? [image.naturalWidth, image.naturalHeight] : currentOutput ? [currentOutput.width, currentOutput.height] : null; }
// The editor owns its copy. The server's copy replaces it only when the shot changes, or when nothing is unsaved here and it is newer.
function layerSync() {
  const key = layerKey(), stored = current?.layers?.[activeShot] || null;
  if (key !== layerState.key) {
    if (layerState.dirty) layerFlush();
    Object.assign(layerState, {key, items: structuredCloneSafe(stored?.items || []), ...layerFieldsOf(stored), basis: stored?.basis || null, source: stored?.source || null, revision: stored?.revision || 0,
      selected: null, dirty: false, undo: [], redo: [], coalesce: '', report: null, error: '', drag: null, editing: null, guides: [], iconResults: null, iconError: '', languageChoice: null});
    if (key && !layerState.inflight) layerRevisions.set(key, layerState.revision);
  } else if (!layerState.dirty && !layerState.inflight && (stored?.revision || 0) > layerState.revision) {
    Object.assign(layerState, {items: structuredCloneSafe(stored.items || []), ...layerFieldsOf(stored), basis: stored.basis || null, source: stored.source || null, revision: stored.revision});
    layerRevisions.set(key, stored.revision);
    if (!layerSelected()) layerState.selected = null;
  }
}
function structuredCloneSafe(value) { return JSON.parse(JSON.stringify(value)); }
function layerShotBasis() {
  const shot = shots().find(item => item.id === activeShot); if (!shot) return null;
  const basis = {aspect: (dirty ? draft : current.plan).aspect || '1:1'};
  for (const key of ['azimuth', 'elevation', 'focal_length', 'projection', 'ortho_scale', 'camera_distance', 'target_offset']) if (shot[key] !== undefined) basis[key] = shot[key];
  return basis;
}
const layerCanonical = value => JSON.stringify(Object.keys(value || {}).sort().map(key => [key, value[key]]));
function layerStale() { return !!layerState.basis && layerState.items.some(item => item.anchor) && layerCanonical(layerState.basis) !== layerCanonical(layerShotBasis()); }

// Every change goes through here: one undo step, then draw and save. Changes with the same key (typing, a slider) share a step.
function layerChange(mutate, coalesce = '') {
  if (!coalesce || coalesce !== layerState.coalesce) { layerState.undo.push(layerSnapshot()); if (layerState.undo.length > 60) layerState.undo.shift(); }
  layerState.redo = []; layerState.coalesce = coalesce;
  mutate();
  layerTouch();
}
function layerTouch() {
  layerState.dirty = true; layerState.error = '';
  clearTimeout(layerState.timer); layerState.timer = setTimeout(layerFlush, 700);
  renderLayerStage(); renderLayerPanel();
}
function layerUndo(redo = false) {
  const from = redo ? layerState.redo : layerState.undo, to = redo ? layerState.undo : layerState.redo;
  if (!from.length) return;
  to.push(layerSnapshot()); layerRestore(from.pop()); layerState.coalesce = '';
  if (!layerSelected()) layerState.selected = null;
  layerTouch();
}
// Saves are queued one after another. What is sent is fixed when the save is asked for, so edits to one shot are never
// lost or mixed up when the shot or the project changes while an earlier save is still on its way.
const layerRevisions = new Map();
function layerFlush() {
  clearTimeout(layerState.timer); layerState.timer = null;
  if (!layerState.dirty || !layerState.key) return layerState.saving || Promise.resolve();
  const key = layerState.key, [projectId, shotId] = key.split(':');
  const layer = {version: 1, items: structuredCloneSafe(layerState.items), ...Object.fromEntries(LAYER_FIELDS.filter(key => layerState[key]).map(key => [key, structuredCloneSafe(layerState[key])])),
    ...(layerState.basis ? {basis: layerState.basis} : {}), source: layerState.source || 'manual'};
  layerState.dirty = false; layerState.inflight++;
  const send = async () => {
    try {
      const saved = await api(`/api/projects/${encodeURIComponent(projectId)}/layers/${encodeURIComponent(shotId)}`, {method: 'PUT', body: {layer, expected_revision: layerRevisions.get(key) || 0}});
      layerRevisions.set(key, saved.revision);
      if (current?.id === projectId) current.layers = {...(current.layers || {}), [shotId]: saved};
      if (layerState.key === key) {
        layerState.revision = saved.revision;
        // Back on this shot before its save had landed: show what was saved, not the older copy that was loaded meanwhile.
        if (!layerState.dirty && !layerState.drag && !layerState.undo.length && JSON.stringify(layerState.items) !== JSON.stringify(layer.items)) {
          Object.assign(layerState, {items: structuredCloneSafe(saved.items), ...layerFieldsOf(saved), basis: saved.basis || null, source: saved.source || null}); renderLayerStage();
        }
      }
    } catch (error) {
      if (layerState.key === key) layerState.error = error.status === 409 ? '图文已在别处修改，本次修改未保存。重新打开项目可载入最新内容。' : (error.message || '图文未能保存，下次修改时会再试。');
    } finally {
      layerState.inflight--; if (!layerState.inflight) layerState.saving = null;
      if (current) renderLayerPanel();
    }
  };
  layerState.saving = (layerState.saving || Promise.resolve()).then(send);
  return layerState.saving;
}

// ------------------------------------------------------------------ adding and editing items
function layerNewId() { let index = layerState.items.length + 1; while (layerState.items.some(item => item.id === 'e' + index)) index++; return 'e' + index; }
function layerAdd(kind) {
  if (!layerSize() || layerState.items.length >= LAYER_MAX_ITEMS) return;
  const id = layerNewId(), offset = (layerState.items.length % 5) * .03;
  const composed = layerComposed(layerState), ink = composed ? (layerState.items.find(entry => entry.role === 'headline')?.color || '#1d1d1f') : '#141414';
  const english = composed && layerLanguage() === 'en';
  const item = kind === 'title' ? {id, type: 'text', role: 'headline', x: .08 + offset, y: .06 + offset, w: .56, text: english ? 'Headline' : '标题文字', size: composed ? .056 : .08, weight: 700, align: 'left', color: ink, plate: null, ...(composed ? {leading: 1.16} : {})}
    : kind === 'text' ? {id, type: 'text', role: 'body', x: .08 + offset, y: .2 + offset, w: .36, text: english ? 'Supporting text' : '在这里写说明文字', size: composed ? .024 : .028, weight: 400, align: 'left', color: ink, plate: null, ...(composed ? {leading: 1.45} : {})}
    : kind === 'callout' ? (composed ? {id, type: 'callout', style: 'line', x: .66 + offset * .3, y: .4 + offset, w: .26, title: english ? 'Key point' : '要点', detail: english ? 'Detail' : '补充说明', icon: null, anchor: [.45, .5], size: .024, color: ink, plate: null}
      : {id, type: 'callout', x: .06 + offset, y: .4 + offset, w: .27, title: '要点', detail: '补充说明', icon: 'check', anchor: [.5, .5], size: .03, color: '#141414', plate: {color: '#ffffff', opacity: .9}})
    : {id, type: 'icon', icon: 'star', x: .12 + offset, y: .82 - offset, size: composed ? .05 : .07, color: ink};
  // A layer started by hand is dressed from the first item on; one saved before styles existed stays as it was until a style is chosen.
  layerChange(() => { if (!layerState.items.length && !layerState.theme) layerState.theme = 'bold'; layerState.items.push(item); layerState.selected = id; });
}
function layerRemove(id = layerState.selected) {
  if (!layerState.items.some(item => item.id === id)) return;
  layerChange(() => { layerState.items = layerState.items.filter(item => item.id !== id); if (layerState.selected === id) layerState.selected = null; });
}
function layerDuplicate() {
  const item = layerSelected(); if (!item || layerState.items.length >= LAYER_MAX_ITEMS) return;
  const copy = {...structuredCloneSafe(item), id: layerNewId(), x: item.x + .03, y: item.y + .03};
  layerChange(() => { layerState.items.push(copy); layerState.selected = copy.id; });
}
function layerSet(patch, coalesce = '') {
  const item = layerSelected(); if (!item) return;
  layerChange(() => { Object.assign(item, patch); layerSyncSlot(item); }, coalesce ? item.id + ':' + coalesce : '');
}
function layerSelect(id) {
  if (layerState.selected === id) return;
  layerState.selected = id; layerState.coalesce = ''; layerState.iconResults = null; layerState.iconError = ''; layerCloseInline(true);
  renderLayerStage(); renderLayerPanel();
}
function layerClear() { if (layerState.items.length) layerChange(() => { layerState.items = []; layerState.selected = null; layerState.basis = null; }); }
function layerSetLook(patch, coalesce = '') { layerChange(() => Object.assign(layerState, patch), coalesce ? 'look:' + coalesce : ''); }

async function layerSuggest(layout = null) {
  if (!current || layerState.busy || !currentOutput) return;
  const key = layerState.key, shotId = activeShot, projectId = current.id;
  layerState.busy = true; layerState.error = ''; renderLayerPanel();
  try {
    const output = {job_id: currentOutput.job_id, version: currentOutput.version, quality: currentOutput.quality};
    const result = await api(`/api/projects/${encodeURIComponent(projectId)}/layers/${encodeURIComponent(shotId)}/suggest`, {method: 'POST', body: {hint: $('layerHint').value.trim().slice(0, 400), output, language: $('layerLanguage').value, ...(layout ? {layout} : {})}});
    if (layerState.key !== key) return;
    layerState.report = result.report; layerState.languageChoice = null;     // the words now carry the language
    layerChange(() => { Object.assign(layerState, {items: result.layer.items, ...layerFieldsOf(result.layer), basis: result.layer.basis || null, source: result.layer.source || 'rules', selected: null}); });
  } catch (error) { if (layerState.key === key) layerState.error = error.message || '排版未完成，请重试。'; }
  finally { layerState.busy = false; if (current) { renderLayerStage(); renderLayerPanel(); } }
}
// The same words in another composition: laid out again on the server, no model asked, one undo step.
async function layerCompose(layout) {
  if (!current || layerState.busy || !currentOutput || !COMP_LAYOUTS[layout]) return;
  if (!layerState.content) return layerSuggest(layout);
  const key = layerState.key, shotId = activeShot, projectId = current.id;
  layerState.busy = true; layerState.error = ''; renderLayerPanel();
  try {
    const output = {job_id: currentOutput.job_id, version: currentOutput.version, quality: currentOutput.quality};
    const result = await api(`/api/projects/${encodeURIComponent(projectId)}/layers/${encodeURIComponent(shotId)}/compose`, {method: 'POST',
      body: {layout, content: layerState.content, glyphs: layerState.glyphs || {}, output, source: layerState.source || 'manual'}});
    if (layerState.key !== key) return;
    layerState.report = {...(layerState.report || {}), ...result.report};
    layerChange(() => { Object.assign(layerState, {items: result.layer.items, ...layerFieldsOf(result.layer), basis: result.layer.basis || null, selected: null}); });
  } catch (error) { if (layerState.key === key) layerState.error = error.message || '版式未能切换，请重试。'; }
  finally { layerState.busy = false; if (current) { renderLayerStage(); renderLayerPanel(); } }
}
// An edit to an item that shows part of the content is kept in the content, so it survives a change of layout.
function layerSyncSlot(item) {
  const content = layerState.content, slot = item?.slot; if (!content || !slot) return;
  const joined = text => String(text).split('\n').reduce((all, line) => all + (/[A-Za-z0-9]$/.test(all) && /^[A-Za-z0-9]/.test(line) ? ' ' : '') + line, '');
  const [group, index, field] = slot.split('.');
  if (!index) { content[group] = joined(item.text || ''); return; }
  const entry = content[group]?.[Number(index)]; if (!entry) return;
  if (item.type === 'callout') { entry.title = item.title; entry.detail = item.detail || ''; }
  else if (item.type === 'figure') { entry.value = item.value; entry.unit = item.unit || ''; entry.label = item.label || ''; }
  else if (item.type === 'chips') { entry.values = [...item.values]; entry.label = item.label || ''; entry.unit = item.unit || ''; }
  else if (item.type === 'text' && field) entry[field] = joined(item.text || '');
  if ('glyph' in item) { if (item.glyph) entry.glyph = item.glyph; else delete entry.glyph; }
}

// ------------------------------------------------------------------ on the picture
function renderLayerStage() {
  const stage = $('layerStage'); if (!stage) return;
  if (current) layerSync(); else if (layerState.dirty) layerFlush();        // leaving the project: what was edited is still saved
  const size = current && layerSize(), rect = size && imageContentRect(), editing = current && layerEditing();
  const show = !!rect && (editing || (layerState.visible && layerState.items.length > 0));
  stage.toggleAttribute('hidden', !show); $('viewer').classList.toggle('layer-editing', !!(show && editing));      // an SVG element has no .hidden property
  if (!show) { stage.innerHTML = ''; layerCloseInline(true); return; }
  const [W, H] = size, viewer = $('viewer').getBoundingClientRect(), scale = W / rect.width;
  stage.setAttribute('viewBox', `0 0 ${W} ${H}`);
  Object.assign(stage.style, {left: rect.left - viewer.left + 'px', top: rect.top - viewer.top + 'px', width: rect.width + 'px', height: rect.height + 'px'});
  const composed = layerComposed(layerState);
  // A composition without a canvas lies over the render the viewer already shows.
  let markup = composed ? compMarkup(layerCurrent(), W, H, layerState.canvas ? currentOutput?.png_url || '' : '') : layerMarkup(layerState.items, W, H);
  if (editing) {
    // A clear target for every item, also where its shape is thin or empty. Large panels lie under everything else.
    const order = composed ? [...layerState.items].sort((a, b) => Number(b.type === 'panel') - Number(a.type === 'panel')) : layerState.items;
    markup += order.map(item => { const box = layerItemBox(item, W, H); return `<rect class="layer-hit" data-item="${esc(item.id)}" x="${layerNumber(box.x)}" y="${layerNumber(box.y)}" width="${layerNumber(box.w)}" height="${layerNumber(box.h)}" fill="transparent"/>`; }).join('');
    markup += layerState.guides.map(guide => guide.axis === 'x' ? `<line class="layer-guide" x1="${guide.at * W}" y1="0" x2="${guide.at * W}" y2="${H}" stroke-width="${scale}"/>` : `<line class="layer-guide" x1="0" y1="${guide.at * H}" x2="${W}" y2="${guide.at * H}" stroke-width="${scale}"/>`).join('');
    const item = layerSelected();
    if (item) {
      const box = layerItemBox(item, W, H), handle = 5 * scale;
      markup += `<rect class="layer-outline" x="${layerNumber(box.x)}" y="${layerNumber(box.y)}" width="${layerNumber(box.w)}" height="${layerNumber(box.h)}" fill="none" stroke-width="${layerNumber(scale * 1.2)}" stroke-dasharray="${layerNumber(scale * 4)} ${layerNumber(scale * 3)}"/>`;
      if (!['icon', 'panel', 'rule'].includes(item.type)) markup += `<rect class="layer-handle" data-handle="width" x="${layerNumber(box.x + box.w - handle * .6)}" y="${layerNumber(box.y + box.h / 2 - handle * 1.4)}" width="${layerNumber(handle * 1.2)}" height="${layerNumber(handle * 2.8)}" rx="${layerNumber(handle * .5)}" stroke-width="${layerNumber(scale)}"/>`;
      if (item.type !== 'rule') markup += `<circle class="layer-handle" data-handle="size" cx="${layerNumber(box.x + box.w)}" cy="${layerNumber(box.y + box.h)}" r="${layerNumber(handle)}" stroke-width="${layerNumber(scale)}"/>`;
      if (item.anchor) markup += `<circle class="layer-handle layer-anchor" data-handle="anchor" cx="${layerNumber(item.anchor[0] * W)}" cy="${layerNumber(item.anchor[1] * H)}" r="${layerNumber(handle * 1.2)}" stroke-width="${layerNumber(scale)}"/>`;
    }
  }
  stage.innerHTML = markup;
  layerPlaceInline();
}
function layerPoint(event) {
  const rect = imageContentRect(); if (!rect) return null;
  return [(event.clientX - rect.left) / rect.width, (event.clientY - rect.top) / rect.height];
}
const layerClamp = (value, low, high) => Math.min(high, Math.max(low, value));
function layerPointerDown(event) {
  if (!layerEditing() || event.button > 0) return;
  const point = layerPoint(event); if (!point) return;
  const handle = event.target.closest?.('[data-handle]')?.dataset.handle, id = handle ? layerState.selected : event.target.closest?.('[data-item]')?.dataset.item;
  if (!id) { layerSelect(null); return; }
  // The stage is redrawn on every press, so the browser never reports a double click: a second press on the same item is one.
  const again = !handle && layerState.pressed?.id === id && event.timeStamp - layerState.pressed.at < 420;
  layerState.pressed = again ? null : {id, at: event.timeStamp};
  if (again) { event.preventDefault(); layerOpenInline(id); return; }
  layerSelect(id);
  const item = layerSelected(), size = layerSize(); if (!item || !size) return;
  const box = layerItemBox(item, size[0], size[1]);
  layerState.drag = {mode: handle || 'move', id, start: point, before: layerSnapshot(), origin: structuredCloneSafe(item), box: {x: box.x / size[0], y: box.y / size[1], w: box.w / size[0], h: box.h / size[1]}, moved: false};
  try { $('layerStage').setPointerCapture?.(event.pointerId); } catch (_) {}
  event.preventDefault();
}
// While moving, a box lines up with the middle and the margins of the picture; Alt turns that off.
function layerSnap(value, extent, axis, free) {
  if (free) return value;
  for (const [edge, at] of [[0, .05], [extent / 2, .5], [extent, .95]]) if (Math.abs(value + edge - at) < .008) { layerState.guides.push({axis, at}); return at - edge; }
  return value;
}
function layerPointerMove(event) {
  const drag = layerState.drag; if (!drag) return;
  const point = layerPoint(event), item = layerSelected(), size = layerSize(); if (!point || !item || !size) return;
  const dx = point[0] - drag.start[0], dy = point[1] - drag.start[1];
  if (!drag.moved && Math.hypot(dx * size[0], dy * size[1]) < 3 * size[0] / (imageContentRect()?.width || size[0])) return;
  drag.moved = true; layerState.guides = [];
  if (drag.mode === 'move' && item.type === 'rule') {
    // A hairline moves as a whole.
    Object.assign(item, {x: layerClamp(drag.origin.x + dx, -.3, 1.3), x2: layerClamp(drag.origin.x2 + dx, -.3, 1.3), y: layerClamp(drag.origin.y + dy, -.3, 1.3), y2: layerClamp(drag.origin.y2 + dy, -.3, 1.3)});
  } else if (drag.mode === 'move') {
    const centred = item.type === 'icon', left = drag.box.x + dx, top = drag.box.y + dy;
    const x = layerSnap(left, drag.box.w, 'x', event.altKey), y = layerSnap(top, drag.box.h, 'y', event.altKey);
    item.x = layerClamp(x + (centred ? drag.box.w / 2 : 0), -.3, 1.3); item.y = layerClamp(y + (centred ? drag.box.h / 2 : 0), -.3, 1.3);
  } else if (drag.mode === 'anchor') item.anchor = [layerClamp(point[0], 0, 1), layerClamp(point[1], 0, 1)];
  else if (drag.mode === 'width') item.w = layerClamp(point[0] - drag.box.x, .04, 1.4);
  else if (item.type === 'panel') {
    item.w = layerClamp(point[0] - drag.box.x, .02, 1.4); item.h = layerClamp(point[1] - drag.box.y, .02, 1.4);
  } else {
    const before = Math.hypot(drag.box.w, drag.box.h), now = Math.hypot(Math.max(.01, point[0] - drag.box.x), Math.max(.01, point[1] - drag.box.y)), factor = layerClamp(now / before, .2, 6);
    item.size = layerClamp(drag.origin.size * factor, .01, .4);
    if (item.type !== 'icon') item.w = layerClamp(drag.origin.w * item.size / drag.origin.size, .04, 1.4);
  }
  renderLayerStage();
}
function layerPointerUp() {
  const drag = layerState.drag; if (!drag) return;
  layerState.drag = null; layerState.guides = [];
  if (drag.moved && layerSnapshot() !== drag.before) {
    layerState.undo.push(drag.before); layerState.redo = []; layerState.coalesce = ''; layerTouch();
  } else renderLayerStage();
}
// Typing on the picture: a field laid over the item, in its own size and colour.
function layerInlineValue(item) { return item.type === 'text' ? item.text : item.type === 'figure' ? item.value : item.type === 'chips' ? item.values.join(' ') : item.title; }
function layerOpenInline(id) {
  const item = layerState.items.find(entry => entry.id === id); if (!item || ['icon', 'panel', 'rule'].includes(item.type)) return;
  layerSelect(id); layerState.editing = id;
  const field = $('layerInline'); field.value = layerInlineValue(item); field.hidden = false;
  layerPlaceInline(); field.focus(); field.select?.();
}
function layerPlaceInline() {
  const field = $('layerInline'); if (!field || field.hidden) return;
  const item = layerState.items.find(entry => entry.id === layerState.editing), size = layerSize(), rect = imageContentRect();
  if (!item || !size || !rect) { layerCloseInline(true); return; }
  const viewer = $('viewer').getBoundingClientRect(), box = layerItemBox(item, size[0], size[1]), k = rect.width / size[0];
  const offset = item.type === 'callout' ? (layerComposed(layerState) ? box.icon + box.gap : box.pad + box.iconSize + box.gap) : box.padX || 0;
  Object.assign(field.style, {left: rect.left - viewer.left + (box.x + offset) * k - 4 + 'px', top: rect.top - viewer.top + box.y * k - 3 + 'px', width: Math.max(80, (box.w - offset) * k + 8) + 'px',
    height: Math.max(26, box.h * k + 6) + 'px', fontSize: Math.max(11, box.size * k) + 'px', fontWeight: item.type === 'callout' ? 700 : item.weight || 400, textAlign: item.align || 'left'});
}
function layerInlineInput() {
  const item = layerState.items.find(entry => entry.id === layerState.editing); if (!item) return;
  const value = $('layerInline').value;
  layerChange(() => {
    if (item.type === 'text') item.text = value || ' ';
    else if (item.type === 'figure') item.value = value.replace(/\s+/g, ' ').trim().slice(0, 24) || ' ';
    else if (item.type === 'chips') { const values = value.split(/[\s,，、]+/).filter(Boolean).slice(0, 16).map(entry => entry.slice(0, 10)); if (values.length) item.values = values; }
    else item.title = value.replace(/\n/g, ' ') || ' ';
    layerSyncSlot(item);
  }, item.id + ':inline');
}
function layerCloseInline(silent = false) {
  const field = $('layerInline'); if (!field || field.hidden) return;
  field.hidden = true; layerState.editing = null; layerState.coalesce = '';
  if (!silent) { renderLayerStage(); renderLayerPanel(); }
}
function layerKeyDown(event) {
  if (!layerEditing() || !current) return;
  const target = event.target, typing = target && (target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName));
  if (typing) { if (target.id === 'layerInline' && (event.key === 'Escape' || (event.key === 'Enter' && (event.ctrlKey || event.metaKey)))) { event.preventDefault(); layerCloseInline(); } return; }
  const command = event.ctrlKey || event.metaKey, item = layerSelected();
  if (command && event.key.toLowerCase() === 'z') { event.preventDefault(); layerUndo(event.shiftKey); return; }
  if (command && event.key.toLowerCase() === 'y') { event.preventDefault(); layerUndo(true); return; }
  if (!item) return;
  if (command && event.key.toLowerCase() === 'd') { event.preventDefault(); layerDuplicate(); }
  else if (event.key === 'Delete' || event.key === 'Backspace') { event.preventDefault(); layerRemove(); }
  else if (event.key === 'Escape') layerSelect(null);
  else if (event.key === 'Enter') { event.preventDefault(); layerOpenInline(item.id); }
  else if (event.key.startsWith('Arrow')) {
    event.preventDefault();
    const step = event.shiftKey ? .02 : .004, dx = event.key === 'ArrowLeft' ? -step : event.key === 'ArrowRight' ? step : 0, dy = event.key === 'ArrowUp' ? -step : event.key === 'ArrowDown' ? step : 0;
    layerSet({x: layerClamp(item.x + dx, -.3, 1.3), y: layerClamp(item.y + dy, -.3, 1.3)}, 'nudge');
  }
}

// ------------------------------------------------------------------ icons for an item
// The items that show an icon: a lone icon, a figure and a point set as a row. A note with a hairline to its part has none.
function layerGlyphTarget() { const item = layerSelected(); return item && layerComposed(layerState) && (['icon', 'figure'].includes(item.type) || (item.type === 'callout' && item.style === 'row')) ? item : null; }
function layerGlyphConcept(item) { return String((item && (item.type === 'figure' ? item.label : item.title)) || '').replace(/\s+/g, ' ').trim().slice(0, 60); }
function layerGlyphLabel(glyph) {
  if (!glyph) return '';
  const source = glyph.source === 'drawn' ? 'AI 绘制' : glyph.source?.startsWith('iconify:') ? `在线 · ${glyph.source.slice(8)}` : glyph.source === 'lucide' ? 'Lucide' : glyph.source === 'tabler' ? 'Tabler' : glyph.source || '';
  return `${source}${glyph.name ? ' · ' + glyph.name : ''}${glyph.license && glyph.license !== 'own' ? ' · ' + glyph.license : ''}${glyph.check?.status === 'failed' ? ' · 未通过核对' : ''}`;
}
function layerUseGlyph(row) {
  const item = layerGlyphTarget(); if (!item || !row?.ops) return;
  layerChange(() => {
    const glyphs = layerState.glyphs || (layerState.glyphs = {});
    const value = {source: row.source, name: row.name, license: row.license, ops: row.ops, stroke: row.stroke || 2, grid: row.grid || 24, ...(row.model ? {model: row.model} : {}), ...(row.check ? {check: row.check} : {}), ...(row.concept ? {concept: row.concept} : {})};
    let key = Object.keys(glyphs).find(name => JSON.stringify(glyphs[name].ops) === JSON.stringify(value.ops));
    if (!key) {
      // Icons no item uses any more make room for the new one.
      const used = new Set(layerState.items.map(entry => entry.glyph).concat([...(layerState.content?.points || []), ...(layerState.content?.figures || []), ...(layerState.content?.specs || [])].map(entry => entry.glyph)).filter(Boolean));
      for (const name of Object.keys(glyphs)) if (!used.has(name) && Object.keys(glyphs).length >= 20) delete glyphs[name];
      let index = 1; while (glyphs['g' + index]) index++; key = 'g' + index; glyphs[key] = value;
    }
    item.glyph = key; layerSyncSlot(item);
  });
}
async function layerIconSearch() {
  const query = ($('layerGlyphQuery').value.trim() || layerGlyphConcept(layerGlyphTarget())).slice(0, 60); if (!query || layerState.iconBusy) return;
  layerState.iconBusy = true; layerState.iconError = ''; renderLayerPanel();
  try {
    const result = await api(`/api/icons?q=${encodeURIComponent(query)}${$('layerGlyphOnline').checked ? '&online=1' : ''}`);
    layerState.iconResults = [...(result.icons || []), ...(result.online || [])].slice(0, 40); layerState.iconQuery = query;
    if (!layerState.iconResults.length) layerState.iconError = '图标库中没有找到，可以让 AI 画一个。';
  } catch (error) { layerState.iconError = error.message || '图标未能找到，请重试。'; }
  finally { layerState.iconBusy = false; renderLayerPanel(); }
}
async function layerIconDraw() {
  const item = layerGlyphTarget(); if (!item || layerState.iconBusy) return;
  const concept = ($('layerGlyphQuery').value.trim() || layerGlyphConcept(item)).slice(0, 80); if (!concept) { layerState.iconError = '请先写出要画的图标是什么。'; renderLayerPanel(); return; }
  layerState.iconBusy = true; layerState.iconError = ''; renderLayerPanel();
  try { const result = await api('/api/icons/draw', {method: 'POST', body: {concept}}); layerUseGlyph(result.glyph); }
  catch (error) { layerState.iconError = error.message || '图标未能画出，请重试。'; }
  finally { layerState.iconBusy = false; renderLayerPanel(); }
}

// ------------------------------------------------------------------ the panel
function layerItemLabel(item) {
  if (item.type === 'icon') return '图标';
  if (item.type === 'panel') return '底板';
  if (item.type === 'rule') return '细线';
  if (item.type === 'figure') return `${item.value}${item.unit || ''}${item.label ? ' ' + item.label : ''}`;
  if (item.type === 'chips') return `${item.label || '规格'} ${item.values.join(' ')}`;
  return (item.type === 'text' ? item.text : item.title).replace(/\s+/g, ' ').trim() || '（空）';
}
const LAYER_KIND_LABELS = {...LAYER_TYPE_LABELS, figure: '数据', panel: '底板', rule: '细线', chips: '规格'};
function renderLayerPanel() {
  if (!$('inspectorOverlay') || !current) return;
  layerSync();
  const ready = !!currentOutput, item = layerSelected(), full = layerState.items.length >= LAYER_MAX_ITEMS, report = layerState.report;
  let message = '';
  if (!ready) message = '先生成这个镜头的预览，再添加图文。';
  else if (layerState.error) message = layerState.error;
  else if (layerState.busy) message = '正在提取要点并排版…';
  else if (layerStale()) message = '镜头在排版后变过，标注可能没有对准；可重新排版或拖动圆点调整。';
  else if (report) {
    const icons = Object.values(report.icons || {}), counts = {};
    for (const icon of icons) { const label = icon.source === 'drawn' ? 'AI 绘制' : icon.source?.startsWith('iconify:') ? '在线图标' : icon.source === 'lucide' ? 'Lucide' : icon.source === 'tabler' ? 'Tabler' : icon.source; counts[label] = (counts[label] || 0) + 1; }
    const iconText = icons.length ? `；图标：${Object.entries(counts).map(([label, count]) => `${label} ${count}`).join('、')}` : '';
    const layoutText = report.layout && COMP_LAYOUTS[report.layout] ? `版式「${COMP_LAYOUTS[report.layout]}」（${COMP_REFERENCES[report.layout]}）` : '';
    message = `${report.source === 'ai' ? '由 AI 提取要点' : report.source === 'manual' ? '已按你的文字重新排版' : '按部件名称提取要点'}${layoutText ? '，' + layoutText : ''}${report.anchored ? `，${report.anchored} 处指向了画面中的部件` : ''}${iconText}。${(report.notes || []).join(' ')}文案是草稿，数字只来自你写下的规格，请核对后使用。`;
  }
  const status = $('layerStatus'); status.textContent = message; status.hidden = !message; status.classList.toggle('is-summary', !!report && !layerState.error && ready && !layerStale());
  $('layerSuggest').disabled = !ready || layerState.busy || serviceDisconnected; $('layerHint').disabled = !ready || layerState.busy;
  $('layerLanguage').disabled = !ready || layerState.busy;
  if (document.activeElement !== $('layerLanguage')) $('layerLanguage').value = layerLanguage();
  $('layerSuggest').textContent = layerState.busy ? '正在排版…' : layerState.items.length ? '重新智能排版' : '智能排版关键信息';
  const composed = layerComposed(layerState);
  stableHTML($('layerLayouts'), Object.entries(COMP_LAYOUTS).map(([name, label]) => `<button type="button" class="layer-layout${layerState.layout === name ? ' selected' : ''}" data-layer-layout="${name}" aria-pressed="${layerState.layout === name}" title="${label}：${COMP_REFERENCES[name]}" ${!ready || layerState.busy || serviceDisconnected ? 'disabled' : ''}><svg viewBox="0 0 24 24" aria-hidden="true">${COMP_SKETCHES[name]}</svg><span>${label}</span></button>`).join(''));
  if (layerState.busy) $('layerSuggest').setAttribute('aria-busy', 'true'); else $('layerSuggest').removeAttribute('aria-busy');
  for (const id of ['layerAddTitle', 'layerAddText', 'layerAddCallout', 'layerAddIcon']) $(id).disabled = !ready || full;
  $('layerUndo').disabled = !layerState.undo.length; $('layerRedo').disabled = !layerState.redo.length;
  $('layerSaved').textContent = layerState.error ? '未保存' : layerState.dirty || layerState.inflight ? '保存中…' : layerState.items.length || layerState.revision ? '已保存' : '';
  stableHTML($('layerItems'), layerState.items.filter(entry => !composed || !['panel', 'rule'].includes(entry.type)).map(entry => `<li><button type="button" class="layer-item${entry.id === layerState.selected ? ' selected' : ''}" data-layer-item="${esc(entry.id)}" aria-pressed="${entry.id === layerState.selected}"><span class="layer-kind">${LAYER_KIND_LABELS[entry.type]}</span><span class="layer-name">${esc(layerItemLabel(entry))}</span></button><button type="button" class="icon-button" data-layer-remove="${esc(entry.id)}" title="删除" aria-label="删除 ${esc(layerItemLabel(entry))}">×</button></li>`).join(''));
  $('layerEmpty').hidden = layerState.items.length > 0 || !ready;
  const look = layerLook();
  $('layerLook').hidden = !layerState.items.length || composed;      // the five v24 styles dress older layers only
  stableHTML($('layerThemes'), Object.entries(LAYER_THEMES).map(([name, label]) => `<button type="button" class="button subtle" data-layer-theme="${name}" aria-pressed="${look.theme === name}">${label}</button>`).join(''));
  stableHTML($('layerAccents'), LAYER_ACCENTS.map(color => `<button type="button" class="layer-swatch${look.accent === color ? ' selected' : ''}" data-layer-accent="${color}" style="background:${color}" title="${color}" aria-label="主色 ${color}" aria-pressed="${look.accent === color}"></button>`).join(''));
  if (document.activeElement !== $('layerAccent')) $('layerAccent').value = look.accent;
  $('layerAccents').closest('.layer-row').hidden = !layerStyled(look);
  const properties = $('layerProperties'); properties.hidden = !item;
  if (item) {
    const text = ['text', 'callout'].includes(item.type);
    $('layerTextField').hidden = !text; $('layerDetailField').hidden = item.type !== 'callout'; $('layerStyleRow').hidden = item.type !== 'text';
    $('layerRoleRow').hidden = item.type !== 'text' || !layerStyled(look) || composed;
    $('layerFigureFields').hidden = item.type !== 'figure'; $('layerChipsField').hidden = item.type !== 'chips';
    if (item.type === 'figure') for (const [id, key] of [['layerFigureValue', 'value'], ['layerFigureUnit', 'unit'], ['layerFigureLabel', 'label']]) if (document.activeElement !== $(id)) $(id).value = item[key] || '';
    if (item.type === 'chips' && document.activeElement !== $('layerChipsValues')) $('layerChipsValues').value = item.values.join(' ');
    const glyphTarget = layerGlyphTarget(), glyph = glyphTarget?.glyph && layerState.glyphs?.[glyphTarget.glyph];
    $('layerGlyphField').hidden = !glyphTarget;
    if (glyphTarget) {
      $('layerGlyphCurrent').textContent = glyph ? '当前：' + layerGlyphLabel(glyph) : '当前没有图标';
      const concept = layerGlyphConcept(glyphTarget); $('layerGlyphQuery').placeholder = concept ? `留空则按“${concept}”查找` : '找图标，如“扳手”“wrench”';
      $('layerGlyphNone').hidden = glyphTarget.type === 'icon';     // a lone icon without its picture is removed instead
      $('layerGlyphSearch').disabled = layerState.iconBusy; $('layerGlyphDraw').disabled = layerState.iconBusy || serviceDisconnected;
      $('layerGlyphDraw').textContent = layerState.iconBusy ? '请稍候…' : 'AI 绘制';
      const status = $('layerGlyphStatus'); status.textContent = layerState.iconError; status.hidden = !layerState.iconError;
      stableHTML($('layerGlyphResults'), (layerState.iconResults || []).map((row, index) => `<button type="button" class="layer-icon${glyph && JSON.stringify(glyph.ops) === JSON.stringify(row.ops) ? ' selected' : ''}" data-layer-glyph="${index}" title="${esc(layerGlyphLabel(row))}" aria-label="使用图标 ${esc(layerGlyphLabel(row))}"><svg viewBox="0 0 24 24" aria-hidden="true">${compGlyphMarkup({...row, stroke: row.stroke || 2}, 0, 0, 24, 'currentColor')}</svg></button>`).join(''));
    }
    if (item.type === 'text') {
      $('layerRole').value = item.role || 'body'; $('layerMarkField').hidden = item.role !== 'headline';
      if (document.activeElement !== $('layerMark')) $('layerMark').value = item.mark || '';
      $('layerBold').disabled = layerStyled(look) && ['headline', 'kicker'].includes(item.role);
    }
    $('layerIconField').hidden = item.type === 'text' || composed || !['icon', 'callout'].includes(item.type);
    $('layerPlateField').hidden = !text || composed; $('layerPointerField').hidden = item.type !== 'callout' || (composed && item.style === 'row');
    $('layerSize').closest('.layer-size-row')?.toggleAttribute('hidden', ['panel', 'rule'].includes(item.type));
    const value = item.type === 'text' ? item.text : item.type === 'callout' ? item.title : '';
    if ($('layerText').value !== value && document.activeElement !== $('layerText')) $('layerText').value = value;
    if (item.type === 'callout' && $('layerDetail').value !== item.detail && document.activeElement !== $('layerDetail')) $('layerDetail').value = item.detail;
    $('layerSize').value = item.size; $('layerSizeValue').textContent = Math.round(item.size * 1000) / 10 + '%';
    $('layerBold').setAttribute('aria-pressed', (item.weight || 400) >= 600);
    for (const align of ['left', 'center', 'right']) $('layerAlign' + align[0].toUpperCase() + align.slice(1)).setAttribute('aria-pressed', (item.align || 'left') === align);
    stableHTML($('layerIcons'), (item.type === 'callout' ? [`<button type="button" class="layer-icon${item.icon ? '' : ' selected'}" data-layer-icon="" title="不用图标" aria-label="不用图标" aria-pressed="${!item.icon}">–</button>`] : [])
      .concat(Object.keys(LAYER_ICONS).map(name => `<button type="button" class="layer-icon${item.icon === name ? ' selected' : ''}" data-layer-icon="${name}" title="${name}" aria-label="图标 ${name}" aria-pressed="${item.icon === name}"><svg viewBox="0 0 24 24" aria-hidden="true">${layerIconMarkup(name, 0, 0, 24, 'currentColor')}</svg></button>`)).join(''));
    stableHTML($('layerColors'), LAYER_SWATCHES.map(color => `<button type="button" class="layer-swatch${item.color === color ? ' selected' : ''}" data-layer-color="${color}" style="background:${color}" title="${color}" aria-label="颜色 ${color}" aria-pressed="${item.color === color}"></button>`).join(''));
    if (document.activeElement !== $('layerColor')) $('layerColor').value = item.color;
    $('layerPlate').checked = !!item.plate; $('layerPointer').checked = !!item.anchor;
  }
  $('layerVisible').checked = layerState.visible;
  for (const id of ['layerExportPng', 'layerExportJpg', 'layerExportSvg', 'layerExportSvgLayer']) $(id).disabled = !ready || !layerState.items.length || !!layerState.exporting;
  // The Photoshop file is built from a composition's drawing; an overlay layer saved before v26 is laid out again first.
  $('layerExportPsd').disabled = !ready || !layerState.items.length || !composed || !!layerState.exporting;
  $('layerExportPsd').textContent = layerState.exporting === 'psd' ? '生成中…' : 'PSD';
  $('layerExportPsd').title = composed ? 'Photoshop 工程：每段文字是可编辑的文字图层，图标、引线与色块是矢量形状图层，照片与画布各占一层，按元素分组' : '旧版图文请先「重新智能排版」，再导出 Photoshop 工程';
  $('layerClear').disabled = !layerState.items.length;
}
function layerLeave() { layerCloseInline(true); layerState.selected = null; layerState.drag = null; layerState.guides = []; layerFlush(); }
function bindLayerControls() {
  const stage = $('layerStage'); if (!stage) return;
  stage.addEventListener('pointerdown', layerPointerDown); stage.addEventListener('pointermove', layerPointerMove);
  stage.addEventListener('pointerup', layerPointerUp); stage.addEventListener('pointercancel', layerPointerUp);
  document.addEventListener('keydown', layerKeyDown);
  $('layerInline').addEventListener('input', layerInlineInput); $('layerInline').addEventListener('blur', () => layerCloseInline());
  $('layerSuggest').onclick = () => layerSuggest();
  // A language chosen here is used for the next smart layout and remembered for the next layer.
  $('layerLanguage').onchange = () => { layerState.languageChoice = $('layerLanguage').value; try { localStorage.setItem('studio.layerLanguage', $('layerLanguage').value); } catch (_) {} };
  $('layerLayouts').onclick = event => { const button = event.target.closest('[data-layer-layout]'); if (button && !button.disabled) layerCompose(button.dataset.layerLayout); };
  for (const [id, key] of [['layerFigureValue', 'value'], ['layerFigureUnit', 'unit'], ['layerFigureLabel', 'label']])
    $(id).oninput = () => layerSet({[key]: key === 'value' ? ($(id).value.slice(0, 24) || ' ') : $(id).value.slice(0, key === 'unit' ? 12 : 40)}, 'figure-' + key);
  $('layerChipsValues').oninput = () => { const values = $('layerChipsValues').value.split(/[\s,，、]+/).filter(Boolean).slice(0, 16).map(value => value.slice(0, 10)); if (values.length) layerSet({values}, 'chips'); };
  $('layerGlyphSearch').onclick = layerIconSearch; $('layerGlyphDraw').onclick = layerIconDraw;
  $('layerGlyphQuery').onkeydown = event => { if (event.key === 'Enter') { event.preventDefault(); layerIconSearch(); } };
  $('layerGlyphResults').onclick = event => { const button = event.target.closest('[data-layer-glyph]'); if (button) layerUseGlyph(layerState.iconResults?.[Number(button.dataset.layerGlyph)]); };
  $('layerGlyphNone').onclick = () => { const item = layerGlyphTarget(); if (item) layerSet({glyph: null}); };
  $('layerAddTitle').onclick = () => layerAdd('title'); $('layerAddText').onclick = () => layerAdd('text');
  $('layerAddCallout').onclick = () => layerAdd('callout'); $('layerAddIcon').onclick = () => layerAdd('icon');
  $('layerUndo').onclick = () => layerUndo(); $('layerRedo').onclick = () => layerUndo(true);
  $('layerItems').onclick = event => {
    const remove = event.target.closest('[data-layer-remove]'), select = event.target.closest('[data-layer-item]');
    if (remove) layerRemove(remove.dataset.layerRemove); else if (select) layerSelect(select.dataset.layerItem);
  };
  $('layerText').oninput = () => { const item = layerSelected(); if (item) layerSet(item.type === 'text' ? {text: $('layerText').value || ' '} : {title: $('layerText').value.replace(/\n/g, ' ') || ' '}, 'text'); };
  $('layerDetail').oninput = () => layerSet({detail: $('layerDetail').value}, 'detail');
  $('layerSize').oninput = () => { const item = layerSelected(); if (!item) return; const size = Number($('layerSize').value); layerSet(item.type === 'icon' ? {size} : {size, w: layerClamp(item.w * size / item.size, .04, 1.4)}, 'size'); };
  $('layerBold').onclick = () => { const item = layerSelected(); if (item) layerSet({weight: (item.weight || 400) >= 600 ? 400 : 700}); };
  for (const align of ['left', 'center', 'right']) $('layerAlign' + align[0].toUpperCase() + align.slice(1)).onclick = () => layerSet({align});
  $('layerIcons').onclick = event => { const button = event.target.closest('[data-layer-icon]'); if (button) layerSet({icon: button.dataset.layerIcon || null}); };
  $('layerColors').onclick = event => { const button = event.target.closest('[data-layer-color]'); if (button) layerSet({color: button.dataset.layerColor}); };
  $('layerColor').oninput = () => layerSet({color: $('layerColor').value.toLowerCase()}, 'color');
  $('layerPlate').onchange = () => { const item = layerSelected(); if (item) layerSet({plate: $('layerPlate').checked ? (layerContrast(item.color) === '#ffffff' ? {color: '#ffffff', opacity: .9} : {color: '#111111', opacity: .62}) : null}); };
  $('layerPointer').onchange = () => { const item = layerSelected(); if (item) layerSet({anchor: $('layerPointer').checked ? [layerClamp(item.x + item.w + .12, .02, .98), layerClamp(item.y + .04, .02, .98)] : null}); };
  $('layerDuplicate').onclick = layerDuplicate; $('layerDelete').onclick = () => layerRemove();
  $('layerThemes').onclick = event => { const button = event.target.closest('[data-layer-theme]'); if (button) layerSetLook({theme: button.dataset.layerTheme}); };
  $('layerAccents').onclick = event => { const button = event.target.closest('[data-layer-accent]'); if (button) layerSetLook({accent: button.dataset.layerAccent}); };
  $('layerAccent').oninput = () => layerSetLook({accent: $('layerAccent').value.toLowerCase()}, 'accent');
  $('layerRole').onchange = () => layerSet({role: $('layerRole').value});
  $('layerMark').oninput = () => layerSet({mark: $('layerMark').value.slice(0, 60)}, 'mark');
  // The headline face arrives after the first paint: measure and draw again once it is there.
  try { document.fonts?.load(`900 16px ${LAYER_DISPLAY_FONT}`, '标题A').then(() => { if (current) renderLayerStage(); }).catch(() => {}); } catch (_) {}
  $('layerVisible').onchange = () => { layerState.visible = $('layerVisible').checked; renderLayerStage(); };
  $('layerClear').onclick = layerClear;
  $('layerExportPng').onclick = () => layerExport('png'); $('layerExportJpg').onclick = () => layerExport('jpg');
  $('layerExportSvg').onclick = () => layerExport('svg'); $('layerExportSvgLayer').onclick = () => layerExport('svg-layer');
  $('layerExportPsd').onclick = () => layerExport('psd');
  $('renderImage').addEventListener('load', renderLayerStage); window.addEventListener('resize', renderLayerStage);
}

// ------------------------------------------------------------------ export
function layerDownload(blob, extension) {
  const link = document.createElement('a'), url = URL.createObjectURL(blob);
  link.href = url; link.download = `${current.name}-${sceneShotLabel(activeShot)}-图文.${extension}`.replace(/[\\/:*?"<>|]/g, '_');
  document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 4000);
}
function layerLoadImage(url) { return new Promise((resolve, reject) => { const image = new Image(); image.onload = () => resolve(image); image.onerror = () => reject(new Error('图片未能载入，请重试。')); image.src = url; }); }
// The render with the layer drawn on it, at the render's own size. The same SVG that is on screen is painted, by the browser's own text engine.
async function layerRaster(output, items, look, type, quality) {
  const base = await layerLoadImage(output.png_url), W = base.naturalWidth, H = base.naturalHeight, fonts = layerStyled(look) ? await layerFontCss() : '';
  const canvas = document.createElement('canvas'); canvas.width = W; canvas.height = H;
  const context = canvas.getContext('2d'); context.drawImage(base, 0, 0, W, H);
  const url = URL.createObjectURL(new Blob([layerSvgDocument(items, W, H, '', look, fonts)], {type: 'image/svg+xml'}));
  try { context.drawImage(await layerLoadImage(url), 0, 0, W, H); } finally { URL.revokeObjectURL(url); }
  return new Promise((resolve, reject) => canvas.toBlob(blob => blob ? resolve(blob) : reject(new Error('图片未能生成，请重试。')), type, quality));
}
// A composition as a complete SVG file: canvas, the photo in its frame and the items; the type it needs travels with it.
function compSvgDocument(layer, W, H, image = '', fonts = '', withCanvas = true) {
  return `<?xml version="1.0" encoding="UTF-8"?>\n<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">`
    + (fonts ? `<style>${fonts}</style>` : '') + (withCanvas ? compCanvasMarkup(layer, W, H, image) : '')
    + `<g id="layer">${(layer.items || []).map(item => compItemMarkup(item, W, H, layer)).join('')}</g></svg>\n`;
}
let compFontCssPromise = null;
function compFontCss() {
  compFontCssPromise ||= (async () => {
    const blob = await (await fetch('/static/vendor/fonts/inter/inter-latin.woff2')).blob();
    const data = await new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.onerror = () => reject(new Error('font')); reader.readAsDataURL(blob); });
    return `@font-face{font-family:'Inter';font-weight:100 900;font-style:normal;src:url(${data}) format('woff2');}`;
  })().catch(() => { compFontCssPromise = null; return ''; });
  return compFontCssPromise;
}
async function layerDataUrl(url) {
  const blob = await (await fetch(url)).blob();
  return new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.onerror = () => reject(new Error('图片未能读取，请重试。')); reader.readAsDataURL(blob); });
}
// ------------------------------------------------------------------ Photoshop export
// A composition as a layered Photoshop file. The canvas colour and the photo are pixel layers; every run of words is an
// editable text layer; icons, hairlines, dots, size chips, cards and bands are vector shape layers; each item is a group.
// It is built from the same SVG that is drawn on screen, element by element, so the file matches what is shown.
let compPsdWriter = null;
function compLoadPsdWriter() {
  compPsdWriter ||= new Promise((resolve, reject) => {
    if (window.agPsd) return resolve(window.agPsd);
    const script = Object.assign(document.createElement('script'), {src: '/static/vendor/ag-psd/ag-psd.js', async: true});
    script.onload = () => window.agPsd ? resolve(window.agPsd) : reject(new Error('Photoshop 导出组件未能载入，请重试。'));
    script.onerror = () => reject(new Error('Photoshop 导出组件未能载入，请重试。'));
    document.head.append(script);
  }).catch(error => { compPsdWriter = null; throw error; });
  return compPsdWriter;
}
const COMP_PATH_NUMBER = /[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?/y;
// Commands with their numbers; arc flags may be written without separators ("a1 1 0 011 1").
function compPathCommands(d) {
  const out = [], text = String(d || ''); let position = 0, command = null, numbers = [];
  while (position < text.length) {
    const char = text[position];
    if (' ,\t\n\r'.includes(char)) { position++; continue; }
    if (/[A-Za-z]/.test(char)) { if (command) out.push([command, numbers]); command = char; numbers = []; position++; continue; }
    if ((command === 'A' || command === 'a') && [3, 4].includes(numbers.length % 7) && (char === '0' || char === '1')) { numbers.push(Number(char)); position++; continue; }
    COMP_PATH_NUMBER.lastIndex = position; const match = COMP_PATH_NUMBER.exec(text);
    if (!match) throw new Error('bad path');
    numbers.push(Number(match[0])); position = COMP_PATH_NUMBER.lastIndex;
  }
  if (command) out.push([command, numbers]);
  return out;
}
// An SVG elliptical arc as cubic segments [x1, y1, x2, y2, x, y] (endpoint parameterisation, SVG 1.1 F.6.5).
function compArcCubics(x1, y1, rx, ry, angle, large, sweep, x2, y2) {
  if (x1 === x2 && y1 === y2) return [];
  if (!rx || !ry) return [[x1, y1, x2, y2, x2, y2]];
  const phi = (angle % 360) * Math.PI / 180, cos = Math.cos(phi), sin = Math.sin(phi);
  const dx = (x1 - x2) / 2, dy = (y1 - y2) / 2, x1p = cos * dx + sin * dy, y1p = -sin * dx + cos * dy;
  rx = Math.abs(rx); ry = Math.abs(ry);
  const scale = x1p * x1p / (rx * rx) + y1p * y1p / (ry * ry);
  if (scale > 1) { rx *= Math.sqrt(scale); ry *= Math.sqrt(scale); }
  const num = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p, den = rx * rx * y1p * y1p + ry * ry * x1p * x1p;
  let factor = den ? Math.sqrt(Math.max(0, num / den)) : 0; if (!!large === !!sweep) factor = -factor;
  const cxp = factor * rx * y1p / ry, cyp = -factor * ry * x1p / rx, cx = cos * cxp - sin * cyp + (x1 + x2) / 2, cy = sin * cxp + cos * cyp + (y1 + y2) / 2;
  const angleOf = (ux, uy, vx, vy) => Math.atan2(ux * vy - uy * vx, ux * vx + uy * vy);
  const start = angleOf(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry);
  let delta = angleOf((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry);
  if (!sweep && delta > 0) delta -= 2 * Math.PI; else if (sweep && delta < 0) delta += 2 * Math.PI;
  const parts = Math.max(1, Math.ceil(Math.abs(delta) / (Math.PI / 2) - 1e-6)), step = delta / parts, k = 4 / 3 * Math.tan(step / 4);
  const at = theta => [cos * rx * Math.cos(theta) - sin * ry * Math.sin(theta) + cx, sin * rx * Math.cos(theta) + cos * ry * Math.sin(theta) + cy];
  const slope = theta => [-cos * rx * Math.sin(theta) - sin * ry * Math.cos(theta), -sin * rx * Math.sin(theta) + cos * ry * Math.cos(theta)];
  const out = [];
  for (let index = 0; index < parts; index++) {
    const a = start + step * index, b = a + step, [ax, ay] = at(a), [bx, by] = at(b), [dax, day] = slope(a), [dbx, dby] = slope(b);
    out.push([ax + k * dax, ay + k * day, bx - k * dbx, by - k * dby, bx, by]);
  }
  out[out.length - 1][4] = x2; out[out.length - 1][5] = y2;
  return out;
}
// Path data as subpaths of absolute cubic segments: [{start: [x, y], segments: [[x1, y1, x2, y2, x, y], ...], closed}].
function compPathCubics(d) {
  const subpaths = []; let path = null, x = 0, y = 0, sx = 0, sy = 0, cubic = null, quad = null;
  const begin = (px, py) => { path = {start: [px, py], segments: [], closed: false}; subpaths.push(path); x = sx = px; y = sy = py; };
  const ensure = () => { if (!path || path.closed) begin(sx, sy); };
  const curve = (x1, y1, x2, y2, px, py) => { ensure(); path.segments.push([x1, y1, x2, y2, px, py]); x = px; y = py; };
  const line = (px, py) => curve(x, y, px, py, px, py);
  for (const [command, numbers] of compPathCommands(d)) {
    const upper = command.toUpperCase(), relative = command !== upper;
    const groups = {M: 2, L: 2, H: 1, V: 1, C: 6, S: 4, Q: 4, T: 2, A: 7, Z: 0}[upper];
    if (groups === undefined) throw new Error('bad path');
    if (upper === 'Z') { if (path && !path.closed) { if (x !== path.start[0] || y !== path.start[1]) line(path.start[0], path.start[1]); path.closed = true; x = sx; y = sy; } cubic = quad = null; continue; }
    if (!numbers.length || numbers.length % groups) throw new Error('bad path');
    for (let index = 0; index < numbers.length; index += groups) {
      const n = numbers.slice(index, index + groups), ox = relative ? x : 0, oy = relative ? y : 0;
      let nextCubic = null, nextQuad = null;
      if (upper === 'M') { if (index === 0) begin(n[0] + ox, n[1] + oy); else line(n[0] + ox, n[1] + oy); }
      else if (upper === 'L') line(n[0] + ox, n[1] + oy);
      else if (upper === 'H') line(n[0] + (relative ? x : 0), y);
      else if (upper === 'V') line(x, n[0] + (relative ? y : 0));
      else if (upper === 'C') { curve(n[0] + ox, n[1] + oy, n[2] + ox, n[3] + oy, n[4] + ox, n[5] + oy); nextCubic = [n[2] + ox, n[3] + oy]; }
      else if (upper === 'S') { const [rx, ry] = cubic ? [2 * x - cubic[0], 2 * y - cubic[1]] : [x, y]; curve(rx, ry, n[0] + ox, n[1] + oy, n[2] + ox, n[3] + oy); nextCubic = [n[0] + ox, n[1] + oy]; }
      else if (upper === 'Q' || upper === 'T') {
        const [qx, qy] = upper === 'Q' ? [n[0] + ox, n[1] + oy] : quad ? [2 * x - quad[0], 2 * y - quad[1]] : [x, y];
        const [px, py] = upper === 'Q' ? [n[2] + ox, n[3] + oy] : [n[0] + ox, n[1] + oy];
        curve(x + 2 / 3 * (qx - x), y + 2 / 3 * (qy - y), px + 2 / 3 * (qx - px), py + 2 / 3 * (qy - py), px, py); nextQuad = [qx, qy];
      } else {
        ensure();
        for (const segment of compArcCubics(x, y, n[0], n[1], n[2], n[3], n[4], n[5] + ox, n[6] + oy)) path.segments.push(segment);
        x = n[5] + ox; y = n[6] + oy;
      }
      cubic = nextCubic; quad = nextQuad;
    }
  }
  return subpaths;
}
// One subpath as Photoshop knots, each [before x, y, anchor x, y, after x, y] in document pixels.
function compKnots(subpath, apply) {
  const segments = subpath.segments, anchors = [subpath.start, ...segments.map(segment => [segment[4], segment[5]])];
  if (subpath.closed && anchors.length > 1) {
    const last = anchors[anchors.length - 1];
    if (Math.abs(last[0] - subpath.start[0]) < 1e-6 && Math.abs(last[1] - subpath.start[1]) < 1e-6) anchors.pop();
  }
  return anchors.map((anchor, index) => {
    const incoming = index > 0 ? segments[index - 1] : subpath.closed ? segments[segments.length - 1] : null;
    const outgoing = index < segments.length && (index < anchors.length - 1 || subpath.closed) ? segments[index] : null;
    const [bx, by] = apply(incoming ? [incoming[2], incoming[3]] : anchor), [ax, ay] = apply(anchor), [nx, ny] = apply(outgoing ? [outgoing[0], outgoing[1]] : anchor);
    return {linked: false, points: [bx, by, ax, ay, nx, ny]};
  });
}
// The transform of an element in the document: translate, scale and matrix, as [a, b, c, d, e, f].
function compMatrix(element) {
  let matrix = [1, 0, 0, 1, 0, 0];
  const multiply = (m, n) => [m[0] * n[0] + m[2] * n[1], m[1] * n[0] + m[3] * n[1], m[0] * n[2] + m[2] * n[3], m[1] * n[2] + m[3] * n[3], m[0] * n[4] + m[2] * n[5] + m[4], m[1] * n[4] + m[3] * n[5] + m[5]];
  const chain = []; for (let node = element; node && node.getAttribute; node = node.parentNode) if (node.getAttribute('transform')) chain.unshift(node.getAttribute('transform'));
  for (const transform of chain) for (const [, name, args] of transform.matchAll(/(translate|scale|matrix)\s*\(([^)]*)\)/g)) {
    const v = args.split(/[\s,]+/).filter(Boolean).map(Number);
    matrix = multiply(matrix, name === 'translate' ? [1, 0, 0, 1, v[0] || 0, v[1] || 0] : name === 'scale' ? [v[0], 0, 0, v[1] ?? v[0], 0, 0] : v);
  }
  return matrix;
}
function compRgb(color) {
  const match = /^#([0-9a-f]{6})$/i.exec(String(color || '').trim()), value = match ? parseInt(match[1], 16) : 0;
  return {r: value >> 16 & 255, g: value >> 8 & 255, b: value & 255};
}
// The font Photoshop is told to set a run in: Inter for Latin words (as on screen), Source Han Sans for Chinese.
function compPsdFont(text, weight) {
  const w = Number(weight) || 400;
  if (compHasCjk(text)) return 'SourceHanSansSC-' + (w >= 800 ? 'Heavy' : w >= 600 ? 'Bold' : w >= 500 ? 'Medium' : 'Regular');
  return 'Inter-' + (w >= 900 ? 'Black' : w >= 800 ? 'ExtraBold' : w >= 700 ? 'Bold' : w >= 600 ? 'SemiBold' : w >= 500 ? 'Medium' : w >= 300 ? 'Regular' : 'Light');
}
function compPsdItemName(item, glyphs) {
  const words = text => String(text || '').replace(/\s+/g, ' ').trim().slice(0, 40);
  if (item.type === 'text') return `${{headline: '标题', kicker: '标签', subline: '副标题', caption: '说明'}[item.role] || '文字'} · ${words(item.text)}`;
  if (item.type === 'figure') return `数据 · ${words(`${item.value}${item.unit ? ' ' + item.unit : ''} ${item.label || ''}`)}`;
  if (item.type === 'callout') return `要点 · ${words(item.title)}`;
  if (item.type === 'chips') return `规格 · ${words(item.label || item.values.join(' '))}`;
  if (item.type === 'icon') return `图标 · ${words(glyphs?.[item.glyph]?.name || item.icon)}`;
  if (item.type === 'rule') return '细线';
  return {band: '色带', scrim: '渐变遮罩'}[item.id] || (/^card/.test(item.id) ? '卡片' : '底板');
}
// What the Photoshop file holds for a composition, without pixels: groups of text, shape and pixel nodes, each with the
// SVG markup that draws it (for its preview). Coordinates are document pixels.
function compPsdModel(layer, W, H) {
  const glyphs = layer.glyphs || {}, svg = compSvgDocument({...layer, canvas: null}, W, H, '', '', false);
  const doc = new DOMParser().parseFromString(svg, 'image/svg+xml'), defs = id => doc.getElementById(id)?.outerHTML || '';
  const serial = node => new XMLSerializer().serializeToString(node);
  const wrap = (node, matrix) => `<g transform="matrix(${matrix.map(layerNumber).join(' ')})">${serial(node)}</g>`;
  const paint = node => {
    const get = name => node.getAttribute(name), fill = get('fill'), stroke = get('stroke'), m = compMatrix(node), scale = Math.sqrt(Math.abs(m[0] * m[3] - m[1] * m[2])) || 1;
    return {fill: fill && fill !== 'none' && !fill.startsWith('url(') ? fill : fill === null && !stroke ? '#000000' : null, gradient: !!fill?.startsWith('url('),
      fillOpacity: Number(get('fill-opacity') ?? 1), evenOdd: get('fill-rule') === 'evenodd', stroke: stroke && stroke !== 'none' ? stroke : null,
      strokeWidth: Number(get('stroke-width') ?? 1) * scale, strokeOpacity: Number(get('stroke-opacity') ?? 1),
      cap: get('stroke-linecap') || 'butt', join: get('stroke-linejoin') || 'miter'};
  };
  const outline = node => {
    const n = name => Number(node.getAttribute(name)) || 0, tag = node.tagName.toLowerCase();
    if (tag === 'path') return node.getAttribute('d');
    if (tag === 'line') return `M${n('x1')} ${n('y1')}L${n('x2')} ${n('y2')}`;
    if (tag === 'circle' || tag === 'ellipse') { const rx = tag === 'circle' ? n('r') : n('rx'), ry = tag === 'circle' ? n('r') : n('ry'); return `M${n('cx') - rx} ${n('cy')}A${rx} ${ry} 0 1 0 ${n('cx') + rx} ${n('cy')}A${rx} ${ry} 0 1 0 ${n('cx') - rx} ${n('cy')}Z`; }
    if (tag === 'rect') {
      const x = n('x'), y = n('y'), w = n('width'), h = n('height'), r = Math.min(n('rx') || n('ry'), w / 2, h / 2);
      return r ? `M${x + r} ${y}H${x + w - r}A${r} ${r} 0 0 1 ${x + w} ${y + r}V${y + h - r}A${r} ${r} 0 0 1 ${x + w - r} ${y + h}H${x + r}A${r} ${r} 0 0 1 ${x} ${y + h - r}V${y + r}A${r} ${r} 0 0 1 ${x + r} ${y}Z` : `M${x} ${y}H${x + w}V${y + h}H${x}Z`;
    }
    if (tag === 'polyline' || tag === 'polygon') { const v = (node.getAttribute('points') || '').trim().split(/[\s,]+/).map(Number); return v.length >= 4 ? 'M' + v.join(' ') + (tag === 'polygon' ? 'Z' : '') : null; }
    return null;
  };
  const shape = (node, name) => {
    const style = paint(node), d = outline(node); if (!d) return null;
    const m = compMatrix(node), apply = ([px, py]) => [m[0] * px + m[2] * py + m[4], m[1] * px + m[3] * py + m[5]];
    const paths = compPathCubics(d).map(subpath => ({open: !subpath.closed, operation: 'combine', fillRule: style.evenOdd ? 'even-odd' : 'non-zero', knots: compKnots(subpath, apply)})).filter(path => path.knots.length);
    if (!paths.length) return null;
    const box = compPsdBounds(paths.flatMap(path => path.knots.flatMap(knot => knot.points)), (style.stroke ? style.strokeWidth / 2 : 0) + 2);
    if (style.gradient) {
      const id = /url\(#([^)]+)\)/.exec(node.getAttribute('fill'))?.[1];
      return {kind: 'pixel', name, box, markup: (id ? `<defs>${defs(id)}</defs>` : '') + wrap(node, m)};
    }
    return {kind: 'shape', name, paths, ...style, box, markup: wrap(node, m)};
  };
  const texts = (node, name) => {
    const spans = [...node.querySelectorAll('tspan')], base = {size: Number(node.getAttribute('font-size')) || 16, weight: node.getAttribute('font-weight') || 400,
      spacing: Number(node.getAttribute('letter-spacing')) || 0, color: node.getAttribute('fill') || '#000000', opacity: Number(node.getAttribute('fill-opacity') ?? 1), anchor: node.getAttribute('text-anchor') || 'start'};
    const run = span => ({...base, size: Number(span.getAttribute('font-size')) || base.size, weight: span.getAttribute('font-weight') || base.weight,
      spacing: span.hasAttribute('letter-spacing') ? Number(span.getAttribute('letter-spacing')) || 0 : base.spacing, x: Number(span.getAttribute('x')) || 0, y: Number(span.getAttribute('y')) || 0, text: span.textContent});
    const runs = spans.map(run), lines = new Set(runs.map(row => row.y)).size === runs.length && new Set(runs.map(row => row.x)).size === 1;
    const node_ = (rows, own) => {
      const first = rows[0], text = rows.map(row => row.text).join('\n'), copy = node.cloneNode(true);
      if (own) for (const span of [...copy.querySelectorAll('tspan')]) if (span.textContent !== first.text || Number(span.getAttribute('x')) !== first.x) span.remove();
      const leading = rows.length > 1 ? rows[1].y - rows[0].y : first.size * 1.2, type = compType(first.weight, first.size ? first.spacing / first.size : 0);
      const width = Math.max(...rows.map(row => layerMeasure(row.text, first.size, type))), left = first.anchor === 'middle' ? first.x - width / 2 : first.anchor === 'end' ? first.x - width : first.x;
      return {kind: 'text', name: `${name} · ${text.replace(/\s+/g, ' ').slice(0, 30)}`, text, x: first.x, y: first.y, size: first.size, weight: Number(first.weight), font: compPsdFont(text, first.weight),
        tracking: first.size ? Math.round(first.spacing / first.size * 1000) : 0, color: first.color, opacity: first.opacity, anchor: first.anchor, leading, lines: rows.length,
        box: compPsdBounds([left, first.y - first.size, left + width, first.y + (rows.length - 1) * leading + first.size * .4], first.size * .3 + 2), markup: serial(copy)};
    };
    return runs.length ? (lines || runs.length === 1 ? [node_(runs, false)] : runs.map(row => node_([row], true))) : [];
  };
  const groups = [];
  for (const item of layer.items || []) {
    const element = doc.querySelector(`[data-item="${CSS.escape ? CSS.escape(item.id) : item.id}"]`); if (!element) continue;
    const children = [];
    const visit = (node, label) => {
      for (const child of node.children) {
        const tag = child.tagName.toLowerCase();
        if (tag === 'defs') continue;
        if (tag === 'g') {
          // An icon: its strokes in one shape layer and its fills in another, gathered under the icon's name.
          const glyph = glyphs[item.glyph], icon = `图标 · ${glyph?.name || item.icon || ''}${glyph?.source ? `（${{lucide: 'Lucide', tabler: 'Tabler', drawn: 'AI 绘制'}[glyph.source] || glyph.source}${glyph.license && glyph.license !== 'own' ? ' · ' + glyph.license : ''}）` : ''}`;
          const parts = [...child.children].map(part => shape(part, icon)).filter(Boolean), merged = [];
          for (const part of parts) {
            const same = merged.find(other => other.kind === 'shape' && part.kind === 'shape' && ['fill', 'stroke', 'strokeWidth', 'cap', 'join', 'fillOpacity', 'strokeOpacity'].every(key => other[key] === part[key]));
            if (same) { same.paths.push(...part.paths); same.markup += part.markup; same.box = compPsdUnion(same.box, part.box); } else merged.push(part);
          }
          if (merged.length === 1) children.push(merged[0]); else if (merged.length) children.push({kind: 'group', name: icon, children: merged.map((part, index) => ({...part, name: `${icon} ${index + 1}`}))});
        } else if (tag === 'text') children.push(...texts(child, label));
        else {
          const name = tag === 'circle' ? '圆点' : item.type === 'callout' && tag === 'path' ? '引线' : item.type === 'chips' && tag === 'rect' ? '尺寸底' : item.type === 'rule' ? '细线' : compPsdItemName(item, glyphs);
          const node_ = shape(child, name); if (node_) children.push(node_);
        }
      }
    };
    visit(element, item.type === 'text' ? '文字' : item.type === 'figure' ? '数字' : item.type === 'chips' ? '尺寸' : '文字');
    if (children.length === 1 && children[0].kind !== 'group') groups.push({...children[0], name: compPsdItemName(item, glyphs)});
    else if (children.length) groups.push({kind: 'group', name: compPsdItemName(item, glyphs), children});
  }
  return groups;
}
// The rectangle [x0, y0, x1, y1] around a list of coordinates (x, y, x, y, ...), widened by ``pad``.
function compPsdBounds(values, pad = 0) {
  const xs = values.filter((_, index) => index % 2 === 0), ys = values.filter((_, index) => index % 2 === 1);
  return [Math.min(...xs) - pad, Math.min(...ys) - pad, Math.max(...xs) + pad, Math.max(...ys) + pad];
}
function compPsdUnion(a, b) { return [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[2], b[2]), Math.max(a[3], b[3])]; }
// Draws SVG markup (in document coordinates) inside ``box`` and returns its pixels cropped to what it covers: {canvas, left, top}.
async function compPsdRaster(markup, W, H, fonts, box = [0, 0, W, H]) {
  const x0 = Math.max(0, Math.floor(box[0])), y0 = Math.max(0, Math.floor(box[1])), x1 = Math.min(W, Math.ceil(box[2])), y1 = Math.min(H, Math.ceil(box[3]));
  const w = x1 - x0, h = y1 - y0; if (w < 1 || h < 1) return null;
  const url = URL.createObjectURL(new Blob([`<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="${w}" height="${h}" viewBox="${x0} ${y0} ${w} ${h}">${fonts ? `<style>${fonts}</style>` : ''}${markup}</svg>`], {type: 'image/svg+xml'}));
  const area = Object.assign(document.createElement('canvas'), {width: w, height: h}), context = area.getContext('2d');
  try { context.drawImage(await layerLoadImage(url), 0, 0, w, h); } finally { URL.revokeObjectURL(url); }
  const data = context.getImageData(0, 0, w, h).data; let left = w, top = h, right = -1, bottom = -1;
  for (let y = 0; y < h; y++) { const row = y * w * 4; for (let x = 0; x < w; x++) if (data[row + x * 4 + 3]) { if (x < left) left = x; if (x > right) right = x; if (y < top) top = y; bottom = y; } }
  if (right < 0) return null;
  const canvas = Object.assign(document.createElement('canvas'), {width: right - left + 1, height: bottom - top + 1});
  canvas.getContext('2d').drawImage(area, left, top, canvas.width, canvas.height, 0, 0, canvas.width, canvas.height);
  return {canvas, left: x0 + left, top: y0 + top};
}
// The ag-psd layer for one node of the model, with its preview pixels.
async function compPsdLayer(node, W, H, fonts) {
  if (node.kind === 'group') {
    const children = [];
    for (const child of node.children) { const built = await compPsdLayer(child, W, H, fonts); if (built) children.push(built); }     // one at a time: each preview is a canvas
    return children.length ? {name: node.name, opened: true, children} : null;
  }
  const pixels = await compPsdRaster(node.markup, W, H, fonts, node.box), base = {name: node.name, ...(pixels || {})};
  if (node.kind === 'text') {
    return {...base, opacity: node.opacity, text: {text: node.text, transform: [1, 0, 0, 1, node.x, node.y], antiAlias: 'smooth',
      style: {font: {name: node.font}, fontSize: node.size, fillColor: compRgb(node.color), tracking: node.tracking, autoLeading: node.lines < 2, ...(node.lines > 1 ? {leading: node.leading} : {})},
      paragraphStyle: {justification: node.anchor === 'middle' ? 'center' : node.anchor === 'end' ? 'right' : 'left'}}};
  }
  if (node.kind === 'shape') {
    const solid = node.fill || node.stroke;
    return {...base, opacity: node.stroke ? 1 : node.fillOpacity, vectorFill: {type: 'color', color: compRgb(solid)},
      vectorStroke: node.stroke ? {strokeEnabled: true, fillEnabled: !!node.fill, lineWidth: {units: 'Pixels', value: node.strokeWidth}, lineCapType: node.cap === 'round' ? 'round' : node.cap === 'square' ? 'square' : 'butt',
        lineJoinType: node.join === 'round' ? 'round' : node.join === 'bevel' ? 'bevel' : 'miter', lineAlignment: 'center', opacity: node.strokeOpacity, content: {type: 'color', color: compRgb(node.stroke)}} : {strokeEnabled: false, fillEnabled: true},
      vectorMask: {paths: node.paths}};
  }
  return pixels ? base : null;
}
// The whole file: canvas colour, photo, then one group per item, bottom to top; the flattened picture as its preview.
async function compPsdExport(output, layer) {
  const W = output.width, H = output.height, [agPsd, fonts, image] = await Promise.all([compLoadPsdWriter(), compFontCss(), layerDataUrl(output.png_url)]);
  const children = [];
  if (layer.canvas) {
    const background = Object.assign(document.createElement('canvas'), {width: W, height: H}), context = background.getContext('2d');
    context.fillStyle = layer.canvas.color; context.fillRect(0, 0, W, H);
    children.push({name: '画布', canvas: background, left: 0, top: 0});
  }
  const photo = await compPsdRaster(compCanvasMarkup(layer, W, H, image, false), W, H, '');
  if (photo) children.push({name: '照片', ...photo});
  for (const node of compPsdModel(layer, W, H)) { const built = await compPsdLayer(node, W, H, fonts); if (built) children.push(built); }
  const composite = Object.assign(document.createElement('canvas'), {width: W, height: H}), url = URL.createObjectURL(new Blob([compSvgDocument(layer, W, H, image, fonts)], {type: 'image/svg+xml'}));
  try { composite.getContext('2d').drawImage(await layerLoadImage(url), 0, 0, W, H); } finally { URL.revokeObjectURL(url); }
  const buffer = agPsd.writePsd({width: W, height: H, children, canvas: composite}, {invalidateTextLayers: true, generateThumbnail: true});
  return new Blob([buffer], {type: 'image/vnd.adobe.photoshop'});
}
async function compExport(kind, output, layer) {
  if (kind === 'psd') return layerDownload(await compPsdExport(output, layer), 'psd');
  const W = output.width, H = output.height, fonts = await compFontCss();
  if (kind === 'svg-layer') return layerDownload(new Blob([compSvgDocument(layer, W, H, '', fonts, false)], {type: 'image/svg+xml'}), 'svg');
  const document = compSvgDocument(layer, W, H, await layerDataUrl(output.png_url), fonts);
  if (kind === 'svg') return layerDownload(new Blob([document], {type: 'image/svg+xml'}), 'svg');
  const canvas = Object.assign(window.document.createElement('canvas'), {width: W, height: H}), url = URL.createObjectURL(new Blob([document], {type: 'image/svg+xml'}));
  try { canvas.getContext('2d').drawImage(await layerLoadImage(url), 0, 0, W, H); } finally { URL.revokeObjectURL(url); }
  const type = kind === 'jpg' ? 'image/jpeg' : 'image/png';
  layerDownload(await new Promise((resolve, reject) => canvas.toBlob(blob => blob ? resolve(blob) : reject(new Error('图片未能生成，请重试。')), type, kind === 'jpg' ? .93 : undefined)), kind);
}
async function layerExport(kind) {
  if (!current || !currentOutput || !layerState.items.length) return;
  const output = currentOutput, items = structuredCloneSafe(layerState.items), look = layerLook();
  if (layerComposed(layerState)) {
    if (layerState.exporting) return;
    layerState.exporting = kind; renderLayerPanel();
    try { await compExport(kind, output, structuredCloneSafe(layerCurrent())); if (kind === 'psd') toast('Photoshop 工程已生成。打开时如提示更新文字图层，请选「更新」。'); }
    catch (error) { toast(error.message || '导出未完成，请重试。', true); }
    finally { layerState.exporting = null; if (current) renderLayerPanel(); }
    return;
  }
  if (kind === 'psd') return;
  try {
    if (kind === 'png') return layerDownload(await layerRaster(output, items, look, 'image/png'), 'png');
    if (kind === 'jpg') return layerDownload(await layerRaster(output, items, look, 'image/jpeg', .93), 'jpg');
    let image = '';
    if (kind === 'svg') {
      const blob = await (await fetch(output.png_url)).blob();
      image = await new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.onerror = () => reject(new Error('图片未能读取，请重试。')); reader.readAsDataURL(blob); });
    }
    layerDownload(new Blob([layerSvgDocument(items, output.width, output.height, image, look, layerStyled(look) ? await layerFontCss() : '')], {type: 'image/svg+xml'}), 'svg');
  } catch (error) { toast(error.message || '导出未完成，请重试。', true); }
}
