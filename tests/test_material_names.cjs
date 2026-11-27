const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require(process.env.UI_TEST_JSDOM_PATH || 'jsdom');

const read = file => fs.readFileSync(path.join(__dirname, '..', file), 'utf8');

test('all bundled materials have Chinese card names and paths, with bilingual search and stable selection', async t => {
  const dom = new JSDOM(read('static/index.html'), {runScripts:'outside-only', pretendToBeVisual:true});
  t.after(() => dom.window.close());
  const win = dom.window, doc = win.document;
  const scripts = [...doc.scripts].map(script => script.getAttribute('src') || '');
  assert.ok(scripts.findIndex(s=>s.startsWith('/static/material-labels-zh.js')) >= 0);
  assert.ok(scripts.findIndex(s=>s.startsWith('/static/material-labels-zh.js')) < scripts.findIndex(s=>s.startsWith('/static/material-browser.js')));
  win.eval(read('static/material-labels-zh.js'));
  win.eval(read('static/material-browser.js'));
  const materials = JSON.parse(read('materials/keyshot/catalog.json'));
  const categories = [...new Set(materials.map(item=>item.category))];
  const labels = win.WuyingMaterialLabels;
  for (const material of materials) {
    assert.match(labels.names[material.name] || '', /\p{Script=Han}/u, material.name);
    for (const part of material.subcategory.split('/').filter(Boolean))
      assert.match(labels.subcategories[part] || '', /\p{Script=Han}/u, part);
  }
  const labelsInLibrary = materials.map(item=>labels.names[item.name]);
  assert.equal(new Set(labelsInLibrary).size, materials.length, 'visually distinct variants need distinct names');
  const instance={renderer:{domElement:doc.createElement('canvas')},previewLibraryMaterial(){}};
  const browser=win.createMaterialBrowser({state:()=>({key:'test',visible:true,active:true,instance}),
    load:async()=>({materials,categories}),onError:message=>assert.fail(message)});
  browser.refresh();
  await new Promise(resolve=>setImmediate(resolve));
  const search = doc.querySelector('#localMaterialSearch'), tiles=()=>[...doc.querySelectorAll('.local-material-tile')];
  function find(query) {search.value=query;search.dispatchEvent(new win.Event('input'));return tiles();}
  const aluminum=materials.find(item=>item.name==='Aluminum Brushed');
  for (const query of ['拉丝铝','Aluminum Brushed']) {
    const tile=find(query).find(button=>button.dataset.id===aluminum.id);
    assert.equal(tile.textContent,'拉丝铝');
    assert.equal(tile.title,'金属 / 铝 / 纹理 / 拉丝铝');
    tile.click();
    assert.match(doc.querySelector('#localMaterialHint').textContent,/已选 拉丝铝/);
  }
  assert.equal(find('745657')[0].textContent,'淡丁香紫（745657）');
  assert.ok(find('三平面投影').length>0);
});
