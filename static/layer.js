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

// ------------------------------------------------------------------ editor state
const layerState = {key: '', items: [], theme: null, accent: null, basis: null, source: null, revision: 0, selected: null, dirty: false, saving: null, inflight: 0, timer: null,
  undo: [], redo: [], coalesce: '', visible: true, report: null, error: '', busy: false, drag: null, editing: null, guides: [], pressed: null};
function layerEditing() { return inspectorTab === 'overlay'; }
function layerLook() { return layerLookOf(layerState); }
// What undo restores: the items and how they are dressed.
function layerSnapshot() { return JSON.stringify({items: layerState.items, theme: layerState.theme, accent: layerState.accent}); }
function layerRestore(snapshot) { const value = JSON.parse(snapshot); Object.assign(layerState, {items: value.items, theme: value.theme || null, accent: value.accent || null}); }
function layerKey() { return current ? current.id + ':' + activeShot : ''; }
function layerSelected() { return layerState.items.find(item => item.id === layerState.selected) || null; }
function layerSize() { const image = $('renderImage'); return currentOutput && !image.hidden && image.naturalWidth ? [image.naturalWidth, image.naturalHeight] : currentOutput ? [currentOutput.width, currentOutput.height] : null; }
// The editor owns its copy. The server's copy replaces it only when the shot changes, or when nothing is unsaved here and it is newer.
function layerSync() {
  const key = layerKey(), stored = current?.layers?.[activeShot] || null;
  if (key !== layerState.key) {
    if (layerState.dirty) layerFlush();
    Object.assign(layerState, {key, items: structuredCloneSafe(stored?.items || []), theme: stored?.theme || null, accent: stored?.accent || null, basis: stored?.basis || null, source: stored?.source || null, revision: stored?.revision || 0,
      selected: null, dirty: false, undo: [], redo: [], coalesce: '', report: null, error: '', drag: null, editing: null, guides: []});
    if (key && !layerState.inflight) layerRevisions.set(key, layerState.revision);
  } else if (!layerState.dirty && !layerState.inflight && (stored?.revision || 0) > layerState.revision) {
    Object.assign(layerState, {items: structuredCloneSafe(stored.items || []), theme: stored.theme || null, accent: stored.accent || null, basis: stored.basis || null, source: stored.source || null, revision: stored.revision});
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
  const layer = {version: 1, items: structuredCloneSafe(layerState.items), ...(layerState.theme ? {theme: layerState.theme} : {}), ...(layerState.accent ? {accent: layerState.accent} : {}), ...(layerState.basis ? {basis: layerState.basis} : {}), source: layerState.source || 'manual'};
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
          Object.assign(layerState, {items: structuredCloneSafe(saved.items), theme: saved.theme || null, accent: saved.accent || null, basis: saved.basis || null, source: saved.source || null}); renderLayerStage();
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
  const item = kind === 'title' ? {id, type: 'text', role: 'headline', x: .08 + offset, y: .06 + offset, w: .56, text: '标题文字', size: .08, weight: 700, align: 'left', color: '#141414', plate: null}
    : kind === 'text' ? {id, type: 'text', role: 'body', x: .08 + offset, y: .2 + offset, w: .36, text: '在这里写说明文字', size: .028, weight: 400, align: 'left', color: '#141414', plate: null}
    : kind === 'callout' ? {id, type: 'callout', x: .06 + offset, y: .4 + offset, w: .27, title: '要点', detail: '补充说明', icon: 'check', anchor: [.5, .5], size: .03, color: '#141414', plate: {color: '#ffffff', opacity: .9}}
    : {id, type: 'icon', icon: 'star', x: .12 + offset, y: .82 - offset, size: .07, color: '#141414'};
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
  layerChange(() => Object.assign(item, patch), coalesce ? item.id + ':' + coalesce : '');
}
function layerSelect(id) {
  if (layerState.selected === id) return;
  layerState.selected = id; layerState.coalesce = ''; layerCloseInline(true);
  renderLayerStage(); renderLayerPanel();
}
function layerClear() { if (layerState.items.length) layerChange(() => { layerState.items = []; layerState.selected = null; layerState.basis = null; }); }
function layerSetLook(patch, coalesce = '') { layerChange(() => Object.assign(layerState, patch), coalesce ? 'look:' + coalesce : ''); }

async function layerSuggest() {
  if (!current || layerState.busy || !currentOutput) return;
  const key = layerState.key, shotId = activeShot, projectId = current.id;
  layerState.busy = true; layerState.error = ''; renderLayerPanel();
  try {
    const output = {job_id: currentOutput.job_id, version: currentOutput.version, quality: currentOutput.quality};
    const result = await api(`/api/projects/${encodeURIComponent(projectId)}/layers/${encodeURIComponent(shotId)}/suggest`, {method: 'POST', body: {hint: $('layerHint').value.trim().slice(0, 400), output}});
    if (layerState.key !== key) return;
    layerState.report = result.report;
    layerChange(() => { Object.assign(layerState, {items: result.layer.items, theme: result.layer.theme || null, accent: result.layer.accent || null, basis: result.layer.basis || null, source: result.layer.source || 'rules', selected: null}); });
  } catch (error) { if (layerState.key === key) layerState.error = error.message || '排版未完成，请重试。'; }
  finally { layerState.busy = false; if (current) { renderLayerStage(); renderLayerPanel(); } }
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
  let markup = layerMarkup(layerState.items, W, H);
  if (editing) {
    // A clear target for every item, also where its shape is thin or empty.
    markup += layerState.items.map(item => { const box = layerBox(item, W, H); return `<rect class="layer-hit" data-item="${esc(item.id)}" x="${layerNumber(box.x)}" y="${layerNumber(box.y)}" width="${layerNumber(box.w)}" height="${layerNumber(box.h)}" fill="transparent"/>`; }).join('');
    markup += layerState.guides.map(guide => guide.axis === 'x' ? `<line class="layer-guide" x1="${guide.at * W}" y1="0" x2="${guide.at * W}" y2="${H}" stroke-width="${scale}"/>` : `<line class="layer-guide" x1="0" y1="${guide.at * H}" x2="${W}" y2="${guide.at * H}" stroke-width="${scale}"/>`).join('');
    const item = layerSelected();
    if (item) {
      const box = layerBox(item, W, H), handle = 5 * scale;
      markup += `<rect class="layer-outline" x="${layerNumber(box.x)}" y="${layerNumber(box.y)}" width="${layerNumber(box.w)}" height="${layerNumber(box.h)}" fill="none" stroke-width="${layerNumber(scale * 1.2)}" stroke-dasharray="${layerNumber(scale * 4)} ${layerNumber(scale * 3)}"/>`;
      if (item.type !== 'icon') markup += `<rect class="layer-handle" data-handle="width" x="${layerNumber(box.x + box.w - handle * .6)}" y="${layerNumber(box.y + box.h / 2 - handle * 1.4)}" width="${layerNumber(handle * 1.2)}" height="${layerNumber(handle * 2.8)}" rx="${layerNumber(handle * .5)}" stroke-width="${layerNumber(scale)}"/>`;
      markup += `<circle class="layer-handle" data-handle="size" cx="${layerNumber(box.x + box.w)}" cy="${layerNumber(box.y + box.h)}" r="${layerNumber(handle)}" stroke-width="${layerNumber(scale)}"/>`;
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
  const box = layerBox(item, size[0], size[1]);
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
  if (drag.mode === 'move') {
    const centred = item.type === 'icon', left = drag.box.x + dx, top = drag.box.y + dy;
    const x = layerSnap(left, drag.box.w, 'x', event.altKey), y = layerSnap(top, drag.box.h, 'y', event.altKey);
    item.x = layerClamp(x + (centred ? drag.box.w / 2 : 0), -.3, 1.3); item.y = layerClamp(y + (centred ? drag.box.h / 2 : 0), -.3, 1.3);
  } else if (drag.mode === 'anchor') item.anchor = [layerClamp(point[0], 0, 1), layerClamp(point[1], 0, 1)];
  else if (drag.mode === 'width') item.w = layerClamp(point[0] - drag.box.x, .04, 1.4);
  else {
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
function layerOpenInline(id) {
  const item = layerState.items.find(entry => entry.id === id); if (!item || item.type === 'icon') return;
  layerSelect(id); layerState.editing = id;
  const field = $('layerInline'); field.value = item.type === 'text' ? item.text : item.title; field.hidden = false;
  layerPlaceInline(); field.focus(); field.select?.();
}
function layerPlaceInline() {
  const field = $('layerInline'); if (!field || field.hidden) return;
  const item = layerState.items.find(entry => entry.id === layerState.editing), size = layerSize(), rect = imageContentRect();
  if (!item || !size || !rect) { layerCloseInline(true); return; }
  const viewer = $('viewer').getBoundingClientRect(), box = layerBox(item, size[0], size[1]), k = rect.width / size[0];
  const offset = item.type === 'callout' ? box.pad + box.iconSize + box.gap : box.padX || 0;
  Object.assign(field.style, {left: rect.left - viewer.left + (box.x + offset) * k - 4 + 'px', top: rect.top - viewer.top + box.y * k - 3 + 'px', width: Math.max(80, (box.w - offset) * k + 8) + 'px',
    height: Math.max(26, box.h * k + 6) + 'px', fontSize: Math.max(11, box.size * k) + 'px', fontWeight: item.type === 'callout' ? 700 : item.weight || 400, textAlign: item.align || 'left'});
}
function layerInlineInput() {
  const item = layerState.items.find(entry => entry.id === layerState.editing); if (!item) return;
  const value = $('layerInline').value;
  layerChange(() => { if (item.type === 'text') item.text = value || ' '; else item.title = value.replace(/\n/g, ' ') || ' '; }, item.id + ':inline');
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

// ------------------------------------------------------------------ the panel
function layerItemLabel(item) { return item.type === 'icon' ? '图标' : (item.type === 'text' ? item.text : item.title).replace(/\s+/g, ' ').trim() || '（空）'; }
function renderLayerPanel() {
  if (!$('inspectorOverlay') || !current) return;
  layerSync();
  const ready = !!currentOutput, item = layerSelected(), full = layerState.items.length >= LAYER_MAX_ITEMS, report = layerState.report;
  let message = '';
  if (!ready) message = '先生成这个镜头的预览，再添加图文。';
  else if (layerState.error) message = layerState.error;
  else if (layerState.busy) message = '正在提取要点并排版…';
  else if (layerStale()) message = '镜头在排版后变过，标注可能没有对准；可重新排版或拖动圆点调整。';
  else if (report) message = `${report.source === 'ai' ? '由 AI 提取要点' : '按部件名称提取要点'}${report.anchored ? `，${report.anchored} 处指向了画面中的部件` : ''}。${(report.notes || []).join(' ')}文案是草稿，请核对后使用。`;
  const status = $('layerStatus'); status.textContent = message; status.hidden = !message; status.classList.toggle('is-summary', !!report && !layerState.error && ready && !layerStale());
  $('layerSuggest').disabled = !ready || layerState.busy || serviceDisconnected; $('layerHint').disabled = !ready || layerState.busy;
  $('layerSuggest').textContent = layerState.busy ? '正在排版…' : layerState.items.length ? '重新智能排版' : '智能排版关键信息';
  if (layerState.busy) $('layerSuggest').setAttribute('aria-busy', 'true'); else $('layerSuggest').removeAttribute('aria-busy');
  for (const id of ['layerAddTitle', 'layerAddText', 'layerAddCallout', 'layerAddIcon']) $(id).disabled = !ready || full;
  $('layerUndo').disabled = !layerState.undo.length; $('layerRedo').disabled = !layerState.redo.length;
  $('layerSaved').textContent = layerState.error ? '未保存' : layerState.dirty || layerState.inflight ? '保存中…' : layerState.items.length || layerState.revision ? '已保存' : '';
  stableHTML($('layerItems'), layerState.items.map(entry => `<li><button type="button" class="layer-item${entry.id === layerState.selected ? ' selected' : ''}" data-layer-item="${esc(entry.id)}" aria-pressed="${entry.id === layerState.selected}"><span class="layer-kind">${LAYER_TYPE_LABELS[entry.type]}</span><span class="layer-name">${esc(layerItemLabel(entry))}</span></button><button type="button" class="icon-button" data-layer-remove="${esc(entry.id)}" title="删除" aria-label="删除 ${esc(layerItemLabel(entry))}">×</button></li>`).join(''));
  $('layerEmpty').hidden = layerState.items.length > 0 || !ready;
  const look = layerLook();
  $('layerLook').hidden = !layerState.items.length;
  stableHTML($('layerThemes'), Object.entries(LAYER_THEMES).map(([name, label]) => `<button type="button" class="button subtle" data-layer-theme="${name}" aria-pressed="${look.theme === name}">${label}</button>`).join(''));
  stableHTML($('layerAccents'), LAYER_ACCENTS.map(color => `<button type="button" class="layer-swatch${look.accent === color ? ' selected' : ''}" data-layer-accent="${color}" style="background:${color}" title="${color}" aria-label="主色 ${color}" aria-pressed="${look.accent === color}"></button>`).join(''));
  if (document.activeElement !== $('layerAccent')) $('layerAccent').value = look.accent;
  $('layerAccents').closest('.layer-row').hidden = !layerStyled(look);
  const properties = $('layerProperties'); properties.hidden = !item;
  if (item) {
    const text = item.type !== 'icon';
    $('layerTextField').hidden = !text; $('layerDetailField').hidden = item.type !== 'callout'; $('layerStyleRow').hidden = item.type !== 'text';
    $('layerRoleRow').hidden = item.type !== 'text' || !layerStyled(look);
    if (item.type === 'text') {
      $('layerRole').value = item.role || 'body'; $('layerMarkField').hidden = item.role !== 'headline';
      if (document.activeElement !== $('layerMark')) $('layerMark').value = item.mark || '';
      $('layerBold').disabled = layerStyled(look) && ['headline', 'kicker'].includes(item.role);
    }
    $('layerIconField').hidden = item.type === 'text'; $('layerPlateField').hidden = !text; $('layerPointerField').hidden = item.type !== 'callout';
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
  for (const id of ['layerExportPng', 'layerExportJpg', 'layerExportSvg', 'layerExportSvgLayer']) $(id).disabled = !ready || !layerState.items.length;
  $('layerClear').disabled = !layerState.items.length;
}
function layerLeave() { layerCloseInline(true); layerState.selected = null; layerState.drag = null; layerState.guides = []; layerFlush(); }
function bindLayerControls() {
  const stage = $('layerStage'); if (!stage) return;
  stage.addEventListener('pointerdown', layerPointerDown); stage.addEventListener('pointermove', layerPointerMove);
  stage.addEventListener('pointerup', layerPointerUp); stage.addEventListener('pointercancel', layerPointerUp);
  document.addEventListener('keydown', layerKeyDown);
  $('layerInline').addEventListener('input', layerInlineInput); $('layerInline').addEventListener('blur', () => layerCloseInline());
  $('layerSuggest').onclick = layerSuggest;
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
async function layerExport(kind) {
  if (!current || !currentOutput || !layerState.items.length) return;
  const output = currentOutput, items = structuredCloneSafe(layerState.items), look = layerLook();
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
