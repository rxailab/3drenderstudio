const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('static/app.js','utf8');
const part=source.slice(source.indexOf('function sceneHistoryHTML('),source.indexOf('async function loadSceneLibrary('));
const context={esc:s=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;'),sceneOutputLabel:o=>`scene v${o.version} ${o.job_id}`,sceneOutputKey:o=>o.job_id};
vm.createContext(context);vm.runInContext(part,context);
test('each historical image keeps its own downloads and rendering source',()=>{
  const project={id:'p',outputs:[
    {shot_id:'scene',quality:'preview',version:1,job_id:'old',created_at:1,png_url:'/files/p/old/a.png',jpg_url:'/files/p/old/a.jpg',blend_url:'/files/p/old/a.blend'},
    {shot_id:'scene',quality:'generated',version:2,job_id:'new',created_at:2,png_url:'/files/p/new/a.png'},
    {shot_id:'scene',quality:'thumbnail',version:3,png_url:'/files/p/thumb/a.png'},
    {shot_id:'hero',quality:'preview',version:1,png_url:'/files/p/hero/a.png'},
    {shot_id:'scene',quality:'preview',version:3,png_url:'/files/other/a.png'}]};
  const result=context.sceneHistoryHTML(project);
  assert.equal(result.count,2);assert.match(result.html,/AI 生成 · 最新/);assert.match(result.html,/Blender 渲染/);
  for(const path of ['/files/p/old/a.png','/files/p/old/a.jpg','/files/p/old/a.blend','/files/p/new/a.png'])assert.ok(result.html.includes(path));
  assert.ok(!result.html.includes('/files/other/'));assert.ok(!result.html.includes('/thumb/'));
  assert.ok(result.html.indexOf('/files/p/new/a.png')<result.html.indexOf('/files/p/old/a.png'));
});
test('when current scene exists each image has a distinct preview selection',()=>{
  const p={id:'p',plan:{shots:[{id:'scene'}]},outputs:[{shot_id:'scene',quality:'generated',version:2,job_id:'first',png_url:'/files/p/a.png'},{shot_id:'scene',quality:'generated',version:2,job_id:'second',png_url:'/files/p/b.png'}]};
  const html=context.sceneHistoryHTML(p).html;
  assert.match(html,/data-scene-history="first"/);assert.match(html,/data-scene-history="second"/);
});
