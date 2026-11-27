const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM,VirtualConsole} = require(process.env.UI_TEST_JSDOM_PATH || 'jsdom');

const root = path.resolve(__dirname,'..');
const html = fs.readFileSync(path.join(root,'static/index.html'),'utf8').replace(/<script[\s\S]*?<\/script>/g,'');
const script = fs.readFileSync(path.join(root,'static/angle-pads.js'),'utf8');

async function setup() {
  const dom = new JSDOM(html,{runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:new VirtualConsole()});
  const {window} = dom,events = [];
  if (window.document.readyState === 'loading') await new Promise(done => window.document.addEventListener('DOMContentLoaded',done));
  window.document.getElementById('settingsForm').addEventListener('input',e=>events.push('input:'+e.target.id));
  window.document.getElementById('settingsForm').addEventListener('change',e=>events.push('change:'+e.target.id));
  window.eval(script);
  const node = id => window.document.getElementById(id);
  const click = target => target.dispatchEvent(new window.MouseEvent('click',{bubbles:true}));
  const key = (target,name) => target.dispatchEvent(new window.KeyboardEvent('keydown',{key:name,bubbles:true}));
  return {window,node,click,key,events};
}

test('the panel has no nested tabs and keeps every plan field',async () => {
  const h = await setup();
  assert.equal(h.window.document.querySelector('[data-tab]'),null);
  for (const id of ['azimuth','elevation','cameraProjection','focalLength','detailTarget','cameraShot','keyAzimuth','keyElevation','fillAzimuth',
    'fillElevation','rimAzimuth','rimElevation','materialSlot','materialPreset','rotateX','poseObject','quality','savePlan'])assert.ok(h.node(id),id);
  assert.equal(h.node('azimuth').closest('details').id,'cameraSection');
  assert.equal(h.node('keyAzimuth').closest('details').id,'lightSection');
});

test('a camera preset writes both angles and tells the app once',async () => {
  const h = await setup();
  h.click(h.window.document.querySelector('#cameraPresets [data-view="90,10"]'));
  assert.equal(h.node('azimuth').value,'90');assert.equal(h.node('elevation').value,'10');
  assert.deepEqual(h.events.filter(e=>e.startsWith('change')),['change:azimuth']);
  assert.equal(h.window.document.querySelector('#cameraPresets [data-view="90,10"]').getAttribute('aria-pressed'),'true');
  assert.match(h.node('cameraSummary').textContent,/^90° \/ 10° · 正交$/);
});

test('arrow keys turn and lift the camera within its limits',async () => {
  const h = await setup();
  h.node('azimuth').value='178';h.node('elevation').value='88';
  h.key(h.node('orbitTop'),'ArrowRight');assert.equal(h.node('azimuth').value,'-177');
  h.key(h.node('orbitSide'),'ArrowUp');assert.equal(h.node('elevation').value,'90');
  h.node('elevation').value='-78';h.key(h.node('orbitSide'),'ArrowDown');assert.equal(h.node('elevation').value,'-80');
});

test('the projection switch drives the hidden field and the summary',async () => {
  const h = await setup();
  h.click(h.window.document.querySelector('[data-projection="perspective"]'));
  assert.equal(h.node('cameraProjection').value,'perspective');
  assert.ok(h.events.includes('change:cameraProjection'));
  assert.match(h.node('cameraSummary').textContent,/透视 70mm/);
});

test('a lighting look fills all three lights and 默认 clears them',async () => {
  const h = await setup();
  assert.equal(h.node('lightSummary').textContent,'默认布光');
  h.click(h.window.document.querySelector('#lightLooks [data-look="three"]'));
  assert.deepEqual(['keyAzimuth','keyElevation','fillAzimuth','fillElevation','rimAzimuth','rimElevation'].map(id=>h.node(id).value),['-35','45','40','30','145','50']);
  assert.equal(h.node('lightSummary').textContent,'已调 主光·补光·轮廓光');
  assert.equal(h.events.filter(e=>e.startsWith('change')).length,0);
  h.click(h.window.document.querySelector('[data-light-reset="fill"]'));
  assert.equal(h.node('fillAzimuth').value,'');assert.equal(h.node('fillElevation').value,'');
  assert.equal(h.node('lightSummary').textContent,'已调 主光·轮廓光');
  h.click(h.window.document.querySelector('#lightLooks [data-look=""]'));
  assert.equal(h.node('keyAzimuth').value,'');assert.equal(h.node('lightSummary').textContent,'默认布光');
});

test('an unset light starts from the project default when nudged',async () => {
  const h = await setup();
  h.node('keyAzimuth').dataset.shared='140';h.window.anglePads.sync();
  const handle = h.window.document.querySelector('.light-handle.light-key');
  assert.ok(handle.classList.contains('unset'));
  h.key(handle,'ArrowRight');
  assert.equal(h.node('keyAzimuth').value,'145');assert.equal(h.node('keyElevation').value,'45');
  h.key(handle,'Delete');
  assert.equal(h.node('keyAzimuth').value,'');assert.equal(h.node('keyElevation').value,'');
});
