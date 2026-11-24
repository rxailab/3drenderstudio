// The listing workspace's settings (static/listing.js) against a stand-in server: the mounting control, brands shared
// between projects, confirmations before removing, the AI cost line, the sub-tabs and what screen readers hear.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const {JSDOM}=require(process.env.UI_TEST_JSDOM_PATH || 'jsdom');
const html=fs.readFileSync('static/index.html','utf8'),script=fs.readFileSync('static/listing.js','utf8');
const panel=html.slice(html.indexOf('<section id="inspectorListing"'),html.indexOf('<section id="inspectorEcommerce"')).replace('<section id="inspectorListing"','<section id="inspectorListing" hidden');
const tick=()=>new Promise(resolve=>setTimeout(resolve,0));
const settle=async()=>{for(let i=0;i<5;i++)await tick();};
const plain=value=>JSON.parse(JSON.stringify(value ?? null));

function state(changes={}) {
  return {ready:true,available:true,built_at:null,notes:[],task:null,order:['T12','T04'],
    product:{title:'Wall Cabinet',model:'WC-1',headline:'',subline:'',language:'en',size_mm:null,drafted:null,pages:null,features:[],specs:[],figures:[],aspect:'1:1',
      scenes:{enabled:true,count:3,audience:'auto',people:'auto',hint:''},
      usecase:{enabled:false,kind:'pipe',material:'pvc_white',mode:'through',side:'+',diameter_mm:null,width_mm:null,thickness_mm:null,length_mm:null,offset_mm:[0,0,0],rotate:[0,0,0],headline:''}},
    brand:{preset:'frame',name:'NORTHFORGE',accent:null,dark:null,badges:[],legal:'',unit:'mm',logo:'/files/a/listing/brand/logo-1.png',accent_used:'#d9463d',accent_source:'product',dark_used:'#16171a',ground:'#ffffff'},
    presets:[{id:'frame',label:{zh:'品牌边框',en:'frame'},note:{zh:'说明',en:''},ground:'light'}],
    components:[],roles:{other:{zh:'其他',en:'Item'}},pages:[],plan:[{kind:'T12',index:0},{kind:'T04',index:0}],
    kinds:{T12:{zh:'海报',en:'Poster'},T04:{zh:'场景参数',en:'Scene'}},parts:[],size:{mm:850,known:true},
    options:{workpieces:['pipe'],materials:['pvc_white'],modes:['through','under','front','manual'],languages:['en','zh'],workpiece_names:{pipe:'管材'},material_names:{pvc_white:'PVC'}},
    scene_options:{audiences:[],image_ready:true,max:3,people:{auto:'自动'},aspects:['1:1']},
    mounting:{setting:'',detected:'wall',reason:'判断为壁挂：名称含 Wall-Mounted',used:'wall'},
    estimate:{new_scenes:3,kept_scenes:0,review_pages:2,usd:[0.42,0.5],basis:{image_call:0.075,tries:1.5,review_page:[0.04,0.08]}},
    spend:{usd:1.234,calls:12,unpriced:1,since:1795430000},elements:{},...changes};
}
const brands={projects:[{id:'b',project_name:'Tool Chest',name:'NORTHFORGE',preset:'tech',accent:'#0a84ff',logo:true,at:5}],
  default:null,last:{project_id:'b',project_name:'Tool Chest',name:'NORTHFORGE',preset:'tech',accent:'#0a84ff',logo:true,at:5}};
async function setup(t,initial=state()) {
  const dom=new JSDOM(`<!doctype html><body><button id="listingOpen"></button><div id="toast" hidden></div><div id="listingStage" hidden><div id="listingStageHead"></div><div id="listingStageNotice"></div><div id="listingStageBody"></div></div>${panel}</body>`,{runScripts:'outside-only',pretendToBeVisual:true});
  const w=dom.window;t.after(()=>w.close());
  const h={w,calls:[],state:initial,brands:JSON.parse(JSON.stringify(brands)),toasts:[],asked:[],answer:'yes'};
  w.setInspectorTab=tab=>{const node=w.document.getElementById('inspectorListing'),was=!node.hidden;node.hidden=tab!=='listing';
    if(was && tab!=='listing')w.listingDeactivate();if(tab==='listing')w.listingActivate(true);};
  w.$=id=>w.document.getElementById(id);
  w.esc=value=>String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  w.toast=(message,error=false)=>h.toasts.push([message,error]);
  w.chooseAction=async(id,title,description,actions)=>{h.asked.push({id,title,description,actions:actions.map(action=>action.label)});return h.answer;};
  w.current={id:'a'};
  w.api=async(path,options={})=>{
    const call={path,method:options.method || 'GET',body:options.body};h.calls.push(call);
    if(path.endsWith('/listing/brands'))return JSON.parse(JSON.stringify(h.brands));
    if(path.endsWith('/listing/brand/default')){h.brands.default=call.body.clear?null:{project_id:'a',project_name:'Wall Cabinet',name:h.state.brand.name,logo:true};return JSON.parse(JSON.stringify(h.brands));}
    if(path.endsWith('/listing/brand/copy'))h.state={...h.state,brand:{...h.state.brand,name:'COPIED',preset:'tech'}};
    if(path.endsWith('/listing/mounting'))h.state={...h.state,mounting:{...h.state.mounting,setting:call.body.mounting,used:call.body.mounting || h.state.mounting.detected}};
    if(call.method==='DELETE' && path.endsWith('/listing/logo'))h.state={...h.state,brand:{...h.state.brand,logo:null}};
    if(call.method==='PUT' && path.endsWith('/listing/brand'))h.state={...h.state,brand:{...h.state.brand,...call.body}};
    return JSON.parse(JSON.stringify(h.state));
  };
  w.eval(script);
  h.node=id=>w.document.getElementById(id);
  h.open=async()=>{h.node('listingOpen').click();await settle();};
  h.tab=async name=>{w.document.querySelector(`[data-listing-tab="${name}"]`).click();await settle();};
  h.change=(node,value)=>{if(value!==undefined){if(node.type==='checkbox' || node.type==='radio')node.checked=value;else node.value=value;}node.dispatchEvent(new w.Event('change',{bubbles:true}));};
  h.click=async selector=>{h.node('listingPanel').querySelector(selector).click();await settle();};
  return h;
}

test('the scene settings show how the product is installed, why, and the three-way override that remakes the scenes',async t=>{
  const h=await setup(t);await h.open();await h.tab('usecase');
  const box=h.node('listingPanel').querySelector('.listing-mounting');
  assert.equal(box.querySelector('legend').textContent,'安装方式');
  assert.equal(h.node('listingMountingReason').textContent,'判断为壁挂：名称含 Wall-Mounted');
  const radios=[...box.querySelectorAll('input[type="radio"][name="listingMounting"]')];
  assert.deepEqual(radios.map(node=>[node.value,node.parentElement.textContent.trim(),node.checked]),[['','自动（壁挂）',true],['wall','壁挂',false],['free','落地·台面',false]]);
  assert.match(box.textContent,/下次生成套图会重新生成场景图/);
  assert.equal(box.getAttribute('aria-describedby'),'listingMountingReason listingMountingNote');
  h.change(radios[2],true);await settle();
  const sent=h.calls.find(call=>call.path.endsWith('/listing/mounting'));
  assert.deepEqual(plain(sent),{path:'/api/projects/a/listing/mounting',method:'PUT',body:{mounting:'free'}});
  assert.match(h.node('listingMountingReason').textContent,/已手动设为落地·台面。自动识别的结果：判断为壁挂/);
  assert.equal(h.node('listingPanel').querySelector('input[name="listingMounting"]:checked').value,'free');
  assert.match(h.toasts.at(-1)[0],/重新生成场景图/);
});

test('the earlier scene pictures are shown only on the scene sub-tab',async t=>{
  const h=await setup(t);await h.open();
  const history=h.node('sceneHistory');history.dataset.count='2';h.w.listingSyncSceneHistory();
  assert.equal(history.hidden,true,'not on the product tab');
  await h.tab('usecase');assert.equal(history.hidden,false);
  await h.tab('brand');assert.equal(history.hidden,true);
  history.dataset.count='0';await h.tab('usecase');assert.equal(history.hidden,true,'nothing to show');
});

test('the sub-tabs control the one panel, which is labelled by the selected tab, and only it is in the tab order',async t=>{
  const h=await setup(t);await h.open();
  const tabs=[...h.w.document.querySelectorAll('[data-listing-tab]')];
  for(const tab of tabs){assert.equal(tab.getAttribute('aria-controls'),'listingPanel');assert.ok(tab.id);}
  assert.equal(h.node('listingPanel').getAttribute('aria-labelledby'),'listingTabProduct');
  await h.tab('brand');
  assert.equal(h.node('listingPanel').getAttribute('aria-labelledby'),'listingTabBrand');
  assert.deepEqual(tabs.map(tab=>tab.tabIndex),[-1,-1,0,-1,-1]);
});

test('the brand can be copied from another project after a confirmation, and set as the default for new projects',async t=>{
  const h=await setup(t);await h.open();await h.tab('brand');
  const share=h.node('listingShare');
  assert.match(share.textContent,/新项目沿用最近保存的品牌「NORTHFORGE」（来自「Tool Chest」）/);
  const select=h.node('listingBrandSource');
  assert.deepEqual([...select.options].map(option=>option.value),['','b']);assert.match(select.options[1].textContent,/Tool Chest · NORTHFORGE（含标志）/);
  await h.click('[data-action="brand-copy"]');
  assert.deepEqual(h.toasts.at(-1),['请先选择要复制的项目',true]);
  select.value='b';h.answer='cancel';await h.click('[data-action="brand-copy"]');
  assert.equal(h.calls.some(call=>call.path.endsWith('/brand/copy')),false,'cancelled: nothing replaced');
  assert.match(h.asked.at(-1).description,/「Tool Chest」的品牌/);
  h.node('listingBrandSource').value='b';h.answer='yes';await h.click('[data-action="brand-copy"]');
  assert.deepEqual(plain(h.calls.find(call=>call.path.endsWith('/brand/copy'))),{path:'/api/projects/a/listing/brand/copy',method:'POST',body:{source:'b'}});
  assert.equal(h.node('listingPanel').querySelector('[data-key="name"]').value,'COPIED');
  // typed and not saved: the default is the brand as saved, so it is saved first
  const name=h.node('listingPanel').querySelector('[data-scope="brand"][data-key="name"]');name.value='TYPED';name.dispatchEvent(new h.w.Event('input',{bubbles:true}));
  await h.click('[data-action="brand-default"]');
  const writes=h.calls.filter(call=>call.method!=='GET').map(call=>call.path);
  assert.deepEqual(writes.slice(-2),['/api/projects/a/listing/brand','/api/projects/a/listing/brand/default']);
  assert.match(h.node('listingShare').textContent,/新项目默认使用「TYPED」/);
  await h.click('[data-action="brand-default-clear"]');
  assert.deepEqual(plain(h.calls.at(-1)),{path:'/api/projects/a/listing/brand/default',method:'POST',body:{clear:true}});
  assert.match(h.node('listingShare').textContent,/沿用最近保存的品牌/);
});

test('removing the logo asks first',async t=>{
  const h=await setup(t);await h.open();await h.tab('brand');
  h.answer='cancel';await h.click('[data-action="logo-remove"]');
  assert.equal(h.calls.some(call=>call.method==='DELETE'),false);
  assert.equal(h.asked.at(-1).title,'移除品牌标志？');assert.deepEqual(plain(h.asked.at(-1).actions),['取消','移除标志']);
  h.answer='yes';await h.click('[data-action="logo-remove"]');
  assert.deepEqual(plain(h.calls.find(call=>call.method==='DELETE')),{path:'/api/projects/a/listing/logo',method:'DELETE'});
  assert.equal(h.node('listingPanel').querySelector('.listing-logo img'),null);
});

test('the next build shows its estimated AI cost and the project its logged spend, both marked as list-price estimates',async t=>{
  const h=await setup(t);await h.open();
  const cost=h.node('listingCost');
  assert.equal(cost.hidden,false);
  assert.equal(cost.textContent,'预估 AI 费用约 US$0.42–0.50（新场景图 3 张、视觉审核 2 页，按官方原价估算）');
  assert.match(cost.title,/1\.5 次图像模型调用/);assert.match(cost.title,/以服务商账单为准/);
  assert.equal(h.node('listingBuild').getAttribute('aria-describedby'),'listingCost');
  assert.match(h.node('listingSpend').textContent,/^本项目已记录的 AI 花费约 US\$1\.23（12 次调用，其中 1 次的模型价格未知，未计入；按官方原价估算/);
  const free=await setup(t,state({estimate:{new_scenes:0,kept_scenes:3,review_pages:0,usd:[0,0]},spend:{usd:0,calls:0,unpriced:0,since:null}}));await free.open();
  assert.equal(free.node('listingCost').textContent,'预估 AI 费用很低：没有新场景图，也不做视觉审核（沿用 3 张场景图）');
  assert.equal(free.node('listingSpend').hidden,true,'nothing recorded for this project yet');
  const running=await setup(t,state({task:{id:'t1',status:'running',stage:'scenes',progress:30,detail:'生成第 1 / 3 张场景图'}}));await running.open();
  assert.equal(running.node('listingCost').hidden,true,'not shown while a build runs');
});

test('screen readers hear the stage of a build, not every percentage',async t=>{
  const h=await setup(t,state({task:{id:'t1',status:'running',stage:'scenes',progress:30,detail:'生成第 1 / 3 张场景图'}}));await h.open();
  assert.equal(h.node('listingStatus').getAttribute('role'),null,'the line with the percentage is not a live region');
  const announce=h.node('listingAnnounce');assert.equal(announce.getAttribute('role'),'status');
  assert.equal(announce.textContent,'正在生成场景图');
  let changes=0;new h.w.MutationObserver(records=>{changes+=records.length;}).observe(announce,{childList:true,characterData:true,subtree:true});
  h.state={...h.state,task:{...h.state.task,progress:41,detail:'生成第 2 / 3 张场景图'}};await h.w.listingLoad(false);await settle();
  assert.match(h.node('listingStatus').textContent,/41%/);assert.equal(changes,0,'same stage: nothing new is announced');
  h.state={...h.state,task:{...h.state.task,stage:'compose',progress:85,detail:'排版 2 张图片'}};await h.w.listingLoad(false);await settle();
  assert.equal(announce.textContent,'正在排版和审核');assert.ok(changes>0);
});
