// The listing workspace (static/listing.js) against a stand-in server: what it shows, what it sends, what it keeps.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const {JSDOM}=require(process.env.UI_TEST_JSDOM_PATH || 'jsdom');
const html=fs.readFileSync('static/index.html','utf8'),script=fs.readFileSync('static/listing.js','utf8');
// The workspace is the inspector's 套图 tab; here it starts hidden, as if another tab were showing.
const panel=html.slice(html.indexOf('<section id="inspectorListing"'),html.indexOf('<section id="inspectorEcommerce"')).replace('<section id="inspectorListing"','<section id="inspectorListing" hidden');
const tick=()=>new Promise(resolve=>setTimeout(resolve,0));
// Values made inside the page belong to its own realm: compare what they hold, not whose Object made them.
const plain=value=>JSON.parse(JSON.stringify(value ?? null));

function state(changes={}) {
  return {ready:true,available:true,built_at:null,notes:[],task:null,order:['main','includes','callouts','usecase','feature','band','specs','benefits'],
    product:{title:'Ratchet Pipe Cutter',model:'PC-22',headline:'',subline:'',language:'en',size_mm:null,drafted:null,pages:null,
      features:[{title:'Hardened wheel',detail:'Replaceable',parts:['Cutting_Wheel'],icon:''}],specs:[{label:'Capacity',value:'6 - 22 mm'}],figures:[],
      usecase:{enabled:true,kind:'pipe',material:'pvc_white',mode:'through',side:'+',diameter_mm:null,width_mm:null,thickness_mm:null,length_mm:null,offset_mm:[0,0,0],rotate:[0,0,0],headline:''}},
    brand:{preset:'frame',name:'NORTHFORGE',accent:null,dark:null,badges:['PRO'],legal:'',unit:'mm',logo:null,accent_used:'#d9463d',accent_source:'product',dark_used:'#16171a',ground:'#ffffff'},
    presets:['frame','flat','tech','clean','promo'].map(id=>({id,label:{zh:'风格 '+id,en:id},note:{zh:'说明',en:''},ground:'light'})),
    components:[],roles:{battery:{zh:'电池',en:'Battery'},charger:{zh:'充电器',en:'Charger'},other:{zh:'其他',en:'Item'}},
    pages:[],plan:[{kind:'main',index:0},{kind:'callouts',index:0},{kind:'usecase',index:0},{kind:'includes',skipped:'还没有处理完成的组件'}],
    kinds:{main:{zh:'主图',en:'Main image'},includes:{zh:'套装清单',en:'Kit includes'},callouts:{zh:'功能标注',en:'Feature callouts'},usecase:{zh:'使用场景标注',en:'Use case'},
      feature:{zh:'特写卖点',en:'Feature close-up'},band:{zh:'标题横幅',en:'Headline band'},specs:{zh:'尺寸规格',en:'Specifications'},benefits:{zh:'卖点列表',en:'Benefits'}},
    parts:['Frame','Cutting_Wheel','Knob'],size:{mm:215,known:false},
    options:{workpieces:['pipe','rod','board','sheet','block'],materials:['pvc_white','copper','steel','wood'],modes:['through','under','front','manual'],languages:['en','zh'],
      workpiece_names:{pipe:'管材',rod:'圆棒',board:'板材',sheet:'薄板',block:'块料'},material_names:{pvc_white:'PVC',copper:'铜',steel:'钢',wood:'木'}},
    elements:{},...changes};
}
async function setup(t,initial=state()) {
  const dom=new JSDOM(`<!doctype html><body><button id="listingOpen"></button><div id="toast" hidden></div>${panel}</body>`,{runScripts:'outside-only',pretendToBeVisual:true});
  const w=dom.window;t.after(()=>w.close());
  const h={w,calls:[],state:initial,toasts:[],respond:null,asked:[],answer:'yes'};
  // app.js's decision dialog: what was asked, answered as the test says
  w.chooseAction=async(id,title,description,actions)=>{h.asked.push({id,title,description,actions:actions.map(action=>action.label)});return h.answer;};
  // The inspector's tab switch as app.js does it: showing the tab activates the workspace, leaving it deactivates it.
  w.setInspectorTab=tab=>{const node=w.document.getElementById('inspectorListing'),was=!node.hidden;node.hidden=tab!=='listing';
    if(was && tab!=='listing')w.listingDeactivate();if(tab==='listing')w.listingActivate(true);};
  w.$=id=>w.document.getElementById(id);
  w.esc=value=>String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  w.toast=(message,error=false)=>h.toasts.push([message,error]);
  w.current={id:'a'};
  w.api=async(path,options={})=>{
    const call={path,method:options.method || 'GET',body:options.body};h.calls.push(call);
    if(h.respond){const answer=await h.respond(call);if(answer!==undefined)return answer;}
    if(call.method==='PUT' && path.endsWith('/listing/product'))h.state={...h.state,product:{...h.state.product,...call.body}};
    if(call.method==='PUT' && path.endsWith('/listing/brand'))h.state={...h.state,brand:{...h.state.brand,...call.body,accent_used:call.body.accent || '#d9463d'}};
    if(call.method==='POST' && path.endsWith('/listing/build')){h.state={...h.state,task:{id:'t1',status:'running',stage:'pictures',progress:12,detail:'渲染第 1 / 4 张素材'}};return h.state.task;}
    return JSON.parse(JSON.stringify(h.state));
  };
  w.eval(script);
  h.node=id=>w.document.getElementById(id);
  h.type=(node,value)=>{node.value=value;node.dispatchEvent(new w.Event('input',{bubbles:true}));};
  h.change=(node,value)=>{if(value!==undefined){if(node.type==='checkbox')node.checked=value;else node.value=value;}node.dispatchEvent(new w.Event('change',{bubbles:true}));};
  h.open=async()=>{h.node('listingOpen').click();await tick();await tick();};
  h.leave=async()=>{w.setInspectorTab('review');await tick();await tick();};
  h.tab=async name=>{w.document.querySelector(`[data-listing-tab="${name}"]`).click();await tick();};
  h.field=(scope,key)=>h.node('listingPanel').querySelector(`[data-scope="${scope}"][data-key="${key}"]`);
  return h;
}

test('opening shows the product record, the planned images in order and what cannot be made yet',async t=>{
  const h=await setup(t);await h.open();
  assert.equal(h.node('inspectorListing').hidden,false);
  assert.deepEqual(h.calls.map(call=>call.path),['/api/projects/a/listing']);
  assert.equal(h.field('product','title').value,'Ratchet Pipe Cutter');assert.equal(h.field('product','model').value,'PC-22');
  assert.match(h.field('product','size_mm').placeholder,/按模型估计约 215/);
  assert.match(h.node('listingPanel').textContent,/填写后才会画出产品尺寸线/);
  const kinds=[...h.node('listingPlan').querySelectorAll('.listing-kind')];
  assert.deepEqual(kinds.map(node=>node.textContent.trim()),['主图','套装清单','功能标注','使用场景标注','特写卖点','标题横幅','尺寸规格','卖点列表']);
  assert.deepEqual(kinds.map(node=>node.querySelector('input').checked),[true,false,true,true,false,false,false,false]);
  assert.match(kinds[1].title,/还没有处理完成的组件/);
  assert.match(h.node('listingNotes').textContent,/「套装清单」暂时无法生成：还没有处理完成的组件/);
  assert.equal(h.node('listingBuild').textContent,'生成套图');assert.equal(h.node('listingBuild').disabled,false);
  assert.equal(h.node('listingDownload').hidden,true);assert.equal(h.node('listingStatus').textContent,'还没有生成套图。');
  assert.match(h.node('listingPanel').querySelector('.listing-parts summary').textContent,/对应部件（1）/);
});

test('words typed are saved before a build, and a running build shows its percentage and can be cancelled',async t=>{
  const h=await setup(t);await h.open();
  h.type(h.field('product','headline'),'One-hand clean cuts');h.type(h.field('product','size_mm'),'225');
  h.node('listingPanel').querySelector('[data-action="add-spec"]').click();await tick();
  const rows=h.node('listingPanel').querySelectorAll('[data-scope="spec"][data-key="label"]');assert.equal(rows.length,2);
  h.type(rows[1],'Wheel');h.type(h.node('listingPanel').querySelectorAll('[data-scope="spec"][data-key="value"]')[1],'Hardened steel');
  assert.equal(h.field('product','headline').value,'One-hand clean cuts','adding a row keeps what was typed');
  const part=h.node('listingPanel').querySelector('[data-scope="feature-part"][value="Knob"]');h.change(part,true);
  h.node('listingBuild').click();await tick();await tick();await tick();
  const sent=h.calls.filter(call=>call.method!=='GET');
  assert.deepEqual(sent.map(call=>[call.method,call.path]),[['PUT','/api/projects/a/listing/product'],['POST','/api/projects/a/listing/build']]);
  assert.equal(sent[0].body.headline,'One-hand clean cuts');assert.equal(sent[0].body.size_mm,225);
  assert.deepEqual(plain(sent[0].body.specs),[{label:'Capacity',value:'6 - 22 mm'},{label:'Wheel',value:'Hardened steel'}]);
  assert.deepEqual(plain(sent[0].body.features[0].parts),['Cutting_Wheel','Knob']);
  assert.equal(h.node('listingStatus').textContent,'渲染第 1 / 4 张素材 · 12%');
  assert.equal(h.node('listingProgress').hidden,false);assert.equal(h.node('listingProgress').getAttribute('aria-valuenow'),'12');
  assert.equal(h.node('listingBuild').disabled,true);assert.equal(h.node('listingCancel').hidden,false);
  assert.ok([...h.node('listingPlan').querySelectorAll('input')].every(node=>node.disabled),'the list is fixed while it is being made');
  h.node('listingCancel').click();await tick();
  assert.deepEqual(h.calls.at(-2).path,'/api/projects/a/listing/t1/cancel');
});

test('built images are shown with their notes and downloads, and nothing a user typed is run as markup',async t=>{
  const pages=[{id:'p1',kind:'main',file:'01-main.png',url:'/files/a/listing/build-1/01-main.png',width:2000,height:2000,notes:[]},
    {id:'p2',kind:'callouts',file:'02-callouts.png',url:'/files/a/listing/build-1/02-callouts.png',width:2000,height:2000,notes:['有 1 个卖点没有对应到部件 <img src=x onerror=alert(1)>']}];
  const initial=state({pages,built_at:1700000000,notes:['品牌色取自产品 <script>alert(1)</script>'],task:{id:'t0',status:'completed',progress:100}});
  initial.product.title='<img src=x onerror=alert(2)>';
  const h=await setup(t,initial);await h.open();
  const figures=[...h.node('listingPages').querySelectorAll('.listing-page')];assert.equal(figures.length,2);
  assert.match(figures[0].querySelector('img').getAttribute('src'),/^\/files\/a\/listing\/build-1\/01-main\.png\?t=1700000000000$/);
  assert.equal(figures[0].querySelector('figcaption a').getAttribute('download'),'01-main.png');
  assert.match(figures[1].textContent,/02 · 功能标注/);assert.match(figures[1].textContent,/有 1 个卖点没有对应到部件 <img src=x onerror=alert\(1\)>/);
  assert.equal(h.node('inspectorListing').querySelectorAll('script').length,0);assert.equal(h.node('inspectorListing').querySelectorAll('img[onerror]').length,0);
  assert.equal(h.field('product','title').value,'<img src=x onerror=alert(2)>');
  assert.equal(h.node('listingBuild').textContent,'重新生成套图');
  assert.equal(h.node('listingDownload').hidden,false);assert.equal(h.node('listingDownload').getAttribute('href'),'/api/projects/a/listing/download');
  assert.match(h.node('listingStatus').textContent,/^共 2 张/);
  // neither page was reviewed: only the draft can be downloaded, and its name says what it holds
  assert.equal(h.node('listingDownloadReviewed').hidden,true);assert.match(h.node('listingDownload').textContent,/草稿/);
});

test('pages that passed the checks and the review get their own download, counted',async t=>{
  const ok={checks:{passed:true,issues:[]},review:{status:'passed',score:86}};
  const pages=[{id:'p1',kind:'main',file:'01-main.png',url:'/files/a/listing/build-1/01-main.png',width:2000,height:2000,notes:[],...ok},
    {id:'p2',kind:'callouts',file:'02-callouts.png',url:'/files/a/listing/build-1/02-callouts.png',width:2000,height:2000,notes:[],checks:{passed:true,issues:[]},review:{status:'failed',score:61}}];
  const h=await setup(t,state({pages,built_at:1700000000,task:{id:'t0',status:'completed',progress:100}}));await h.open();
  const reviewed=h.node('listingDownloadReviewed');
  assert.equal(reviewed.hidden,false);assert.equal(reviewed.getAttribute('href'),'/api/projects/a/listing/download?reviewed=1');
  assert.match(reviewed.textContent,/1\/2/);assert.match(h.node('listingDownload').textContent,/草稿/);
});

test('a rebuild reuses the kept headlines; asking for new ones is its own button and sends fresh',async t=>{
  const empty=await setup(t);await empty.open();
  assert.equal(empty.node('listingRewrite').hidden,true,'nothing built yet: there are no headlines to rewrite');
  const pages=[{id:'p1',kind:'main',file:'01-main.png',url:'/files/a/listing/build-1/01-main.png',width:2000,height:2000,notes:[]}];
  const h=await setup(t,state({pages,built_at:1700000000,task:{id:'t0',status:'completed',progress:100}}));await h.open();
  assert.equal(h.node('listingRewrite').hidden,false);assert.equal(h.node('listingRewrite').disabled,false);
  h.node('listingBuild').click();await tick();await tick();await tick();
  assert.deepEqual(plain(h.calls.find(call=>call.method==='POST').body),{},'the plain rebuild asks for nothing new');
  assert.equal(h.node('listingRewrite').hidden,true,'hidden while a build runs');
  const again=await setup(t,state({pages,built_at:1700000000,task:{id:'t0',status:'completed',progress:100}}));await again.open();
  again.node('listingRewrite').click();await tick();await tick();await tick();
  assert.deepEqual(plain(again.calls.find(call=>call.method==='POST').body),{fresh:true});
});

test('choosing which images to make is an explicit list that can be handed back to the planner',async t=>{
  const h=await setup(t);await h.open();
  const box=kind=>h.node('listingPlan').querySelector(`input[value="${kind}"]`);
  h.change(box('usecase'),false);await tick();
  assert.deepEqual([...h.node('listingPlan').querySelectorAll('input:checked')].map(node=>node.value),['main','callouts']);
  h.change(box('specs'),true);await tick();
  const auto=h.node('listingPlan').querySelector('[data-action="pages-auto"]');assert.ok(auto,'an explicit list offers the way back');
  h.node('listingBuild').click();await tick();await tick();await tick();
  assert.deepEqual(plain(h.calls.find(call=>call.method==='PUT').body.pages),['main','callouts','specs']);
});

test('components uploaded with the project are edited, turned and removed through the server, and their progress is shown',async t=>{
  const row={id:'c1',name:'Battery',role:'battery',sku:'BP-205',caption:'',quantity:2,kind:'model',status:'processing',stage:'render',progress:64,error:'',size_mm:117.5,size_source:'model',
    included:true,cutout:null,notes:['尺寸按模型文件的单位估计为最长边 117.5 mm；填写真实尺寸后，套装图里的大小比例才准确。']};
  const h=await setup(t,state({components:[row]}));await h.open();await h.tab('components');
  const card=h.node('listingPanel').querySelector('[data-component="c1"]');
  assert.match(card.querySelector('.listing-component-status').textContent,/渲染抠图 · 64%/);assert.match(card.textContent,/按模型文件的单位估计/);
  assert.equal(card.querySelector('[data-turn="flip"]').disabled,true,'a component being processed is not turned');
  assert.match(h.node('listingNotes').textContent,/有组件还在处理/);
  h.state={...h.state,components:[{...row,status:'ready',stage:'ready',progress:100,cutout:{url:'/files/a/components/c1/cutout-1.png',width:900,height:700}}]};
  await h.w.listingLoad(false);
  const ready=h.node('listingPanel').querySelector('[data-component="c1"]');
  assert.equal(ready.querySelector('img').getAttribute('src'),'/files/a/components/c1/cutout-1.png');
  h.change(ready.querySelector('[data-key="quantity"]'),'3');await tick();await tick();
  h.change(h.node('listingPanel').querySelector('[data-component="c1"] [data-key="included"]'),false);await tick();await tick();
  h.node('listingPanel').querySelector('[data-component="c1"] [data-turn="flip"]').click();await tick();await tick();
  h.node('listingPanel').querySelector('[data-component="c1"] [data-action="component-refresh"]').click();await tick();await tick();
  h.answer='cancel';h.node('listingPanel').querySelector('[data-component="c1"] [data-action="component-remove"]').click();await tick();await tick();
  assert.equal(h.calls.some(call=>call.method==='DELETE'),false,'nothing is removed without a yes');
  assert.match(h.asked.at(-1).description,/「Battery」/);assert.deepEqual(plain(h.asked.at(-1).actions),['取消','删除组件']);
  h.answer='yes';h.node('listingPanel').querySelector('[data-component="c1"] [data-action="component-remove"]').click();await tick();await tick();await tick();
  const sent=h.calls.filter(call=>call.method!=='GET').map(call=>[call.method,call.path,call.body]);
  assert.deepEqual(plain(sent),[['PUT','/api/projects/a/components/c1',{quantity:3}],['PUT','/api/projects/a/components/c1',{included:false}],
    ['PUT','/api/projects/a/components/c1',{turn:'flip'}],['POST','/api/projects/a/components/c1/refresh',null],['DELETE','/api/projects/a/components/c1',null]]);
  assert.equal(h.node('listingPanel').querySelector('form'),null,'components come with the project, not from here');
  assert.match(h.node('listingPanel').textContent,/在新建项目时与主模型一起上传/);
});

test('the brand follows the product colour until the user sets one, and a preset is one choice of five',async t=>{
  const h=await setup(t);await h.open();await h.tab('brand');
  const radios=[...h.node('listingPanel').querySelectorAll('input[name="listingPreset"]')];
  assert.deepEqual(radios.map(node=>node.value),['frame','flat','tech','clean','promo']);assert.equal(radios.find(node=>node.checked).value,'frame');
  const colour=h.field('brand','accent');assert.equal(colour.disabled,true);assert.equal(colour.value,'#d9463d');
  assert.match(h.node('listingPanel').textContent,/当前 #d9463d（取自产品）/);
  assert.match(h.node('listingPanel').textContent,/不内置任何品牌的标志或名称/);
  h.change(h.node('listingPanel').querySelector('[data-scope="brand-auto"][data-key="accent"]'),false);await tick();
  assert.equal(h.field('brand','accent').disabled,false);
  h.type(h.field('brand','accent'),'#0a84ff');h.type(h.field('brand','badges'),'20V， PRO , ');
  radios[2].checked=true;h.change(h.node('listingPanel').querySelector('input[name="listingPreset"][value="tech"]'),'tech');
  h.node('listingPanel').querySelector('[data-action="save-brand"]').click();await tick();await tick();
  const saved=h.calls.find(call=>call.method==='PUT' && call.path.endsWith('/listing/brand')).body;
  assert.deepEqual(plain(saved),{preset:'tech',name:'NORTHFORGE',accent:'#0a84ff',dark:null,badges:['20V','PRO'],legal:'',unit:'mm'});
  assert.deepEqual(h.toasts.at(-1),['已保存',false]);
});

test('the use case is set by workpiece, place and size, with only the fields that apply',async t=>{
  const h=await setup(t);await h.open();await h.tab('usecase');
  assert.equal(h.field('usecase','enabled').checked,true);
  assert.deepEqual([...h.field('usecase','mode').options].map(option=>option.textContent),['穿过工具的开口','垫在工具下方','顶在工具前端','手动放置（工具中心）']);
  assert.ok(h.field('usecase','diameter_mm'));assert.equal(h.field('usecase','thickness_mm'),null);assert.equal(h.field('usecase','side'),null);
  h.type(h.field('usecase','diameter_mm'),'22');
  h.change(h.field('usecase','kind'),'board');await tick();
  assert.equal(h.field('usecase','diameter_mm'),null);assert.ok(h.field('usecase','thickness_mm'));assert.ok(h.field('usecase','width_mm'));
  h.change(h.field('usecase','mode'),'front');await tick();
  assert.ok(h.field('usecase','side'),'which end is asked only where the workpiece stands at an end');
  const offsets=h.node('listingPanel').querySelectorAll('[data-scope="usecase-triple"][data-key="offset_mm"]');h.type(offsets[2],'-12.5');
  h.type(h.field('usecase','headline'),'Drills hardwood');
  h.node('listingPanel').querySelector('[data-action="save-product"]').click();await tick();await tick();
  const use=h.calls.find(call=>call.method==='PUT').body.usecase;
  assert.equal(use.kind,'board');assert.equal(use.mode,'front');assert.equal(use.diameter_mm,22);assert.deepEqual(plain(use.offset_mm),[0,0,-12.5]);assert.equal(use.headline,'Drills hardwood');
});

test('a draft fills the form from the server, and unsaved words are saved when the tab is left',async t=>{
  const h=await setup(t);await h.open();
  h.node('listingSource').value='6-22 mm';
  h.respond=call=>{if(call.path.endsWith('/listing/draft')){h.state={...h.state,product:{...h.state.product,headline:'Clean, square cuts',drafted:{source:'rules',at:1,notes:[]}}};}};
  h.node('listingPanel').querySelector('[data-action="draft"]').click();await tick();await tick();await tick();
  const draft=h.calls.find(call=>call.path.endsWith('/listing/draft'));
  assert.deepEqual(plain(draft.body),{hint:'6-22 mm',language:'en'});
  assert.equal(h.field('product','headline').value,'Clean, square cuts');assert.match(h.node('listingPanel').textContent,/按部件名称起草/);
  h.respond=null;h.type(h.field('product','subline'),'Typed and not yet saved');
  await h.leave();
  const last=h.calls.filter(call=>call.method==='PUT').at(-1);
  assert.equal(last.path,'/api/projects/a/listing/product');assert.equal(last.body.subline,'Typed and not yet saved');
});

test('a server that refuses is reported and the workspace stays usable',async t=>{
  const h=await setup(t,state({available:false}));await h.open();
  assert.equal(h.node('listingBuild').disabled,true);assert.equal(h.node('listingStatus').textContent,'渲染组件不可用，无法生成套图。');
  const failing=await setup(t,state({task:{id:'t9',status:'failed',error:'渲染失败：显存不足',progress:40}}));await failing.open();
  assert.equal(failing.node('listingStatus').textContent,'上次生成失败：渲染失败：显存不足');assert.equal(failing.node('listingBuild').disabled,false);
  failing.respond=call=>{if(call.method==='POST')throw new Error('这个项目的套图正在生成，请等待完成或先取消。');};
  failing.node('listingBuild').click();await tick();await tick();await tick();
  assert.deepEqual(failing.toasts.at(-1),['这个项目的套图正在生成，请等待完成或先取消。',true]);assert.equal(failing.node('listingBuild').disabled,false);
});

test('a pasted description is sent for extraction, the fields fill from the answer and the text is kept',async t=>{
  const h=await setup(t);await h.open();
  h.node('listingPanel').querySelector('[data-action="extract"]').click();await tick();
  assert.deepEqual(h.toasts.at(-1),['请先粘贴一段产品介绍',true]);assert.equal(h.calls.some(call=>call.path.endsWith('/listing/extract')),false);   // nothing to send
  const text='Ratchet Pipe Cutter PC-22. Cuts 6 - 22 mm copper and PVC.';
  h.type(h.node('listingSource'),text);
  h.respond=call=>{if(call.path.endsWith('/listing/extract')){h.state={...h.state,product:{...h.state.product,source:call.body.text,headline:'Cuts 6 - 22 mm pipe',
    specs:[{label:'Capacity',value:'6 - 22 mm'}],drafted:{source:'extracted',at:1,notes:['以下内容含有介绍里没有的数字，已略去：指标「Weight」。']}}};}};
  h.node('listingPanel').querySelector('[data-action="extract"]').click();await tick();await tick();await tick();await tick();
  const sent=h.calls.filter(call=>call.method!=='GET');
  assert.equal(sent[0].path,'/api/projects/a/listing/product');assert.equal(sent[0].body.source,text);    // the typed text is saved first
  assert.deepEqual(plain(sent[1]),{path:'/api/projects/a/listing/extract',method:'POST',body:{text}});
  assert.equal(h.field('product','headline').value,'Cuts 6 - 22 mm pipe');assert.equal(h.node('listingSource').value,text);
  assert.match(h.node('listingPanel').textContent,/从介绍中提取/);assert.match(h.node('listingPanel').textContent,/已略去：指标「Weight」/);
  assert.deepEqual(h.toasts.at(-1),['已从介绍中提取',false]);
});

test('the tab loads once per project, polls only while shown and reloads when shown again',async t=>{
  const h=await setup(t);await h.open();
  assert.equal(h.calls.length,1);
  h.w.listingActivate(false);await tick();assert.equal(h.calls.length,1);                     // an ordinary redraw does not reload
  await h.leave();h.w.setInspectorTab('listing');await tick();await tick();
  assert.equal(h.calls.filter(call=>call.path==='/api/projects/a/listing').length,2);          // shown again: fresh state
  h.w.current={id:'b'};h.w.listingActivate(false);await tick();await tick();
  assert.equal(h.calls.at(-1).path,'/api/projects/b/listing');                                  // another project: its own record
});

test('extracting saves brand edits first, and an answer that arrives after another project opened is not applied to it',async t=>{
  const h=await setup(t);await h.open();
  await h.tab('brand');h.type(h.field('brand','name'),'NEWBRAND');assert.equal(h.w.listingUnsaved(),true);   // a reload would ask first
  await h.tab('product');h.type(h.node('listingSource'),'Cuts 6 - 22 mm pipe.');
  let release;const late=new Promise(resolve=>{release=resolve;});
  h.respond=async call=>{if(call.path.endsWith('/listing/extract')){await late;return {...JSON.parse(JSON.stringify(h.state)),product:{...h.state.product,title:'From A'}};}};
  h.node('listingPanel').querySelector('[data-action="extract"]').click();await tick();await tick();await tick();
  assert.ok(h.calls.some(call=>call.method==='PUT' && call.path==='/api/projects/a/listing/brand' && call.body.name==='NEWBRAND'));   // not dropped by the reset
  h.respond=null;h.w.current={id:'b'};h.w.listingActivate(false);h.w.listingActivate(true);await tick();await tick();await tick();
  assert.equal(h.calls.filter(call=>call.path==='/api/projects/b/listing').length,1);           // two callers, one switch
  release();await tick();await tick();await tick();
  assert.notEqual(h.field('product','title').value,'From A');                                    // A's answer stays with A
  assert.equal(h.w.listingUnsaved(),false);
});

test('the moving-parts tab shows what the pose agent found and asks it again',async t=>{
  const h=await setup(t);await h.open();
  let found={status:'ready',running:false,product_kind:'digital caliper',reasoning:'The slider runs along the beam.',at:5,
    states:[{id:'closed',label_zh:'闭合',label_en:'Closed',role:'rest'},{id:'open',label_zh:'全开',label_en:'Fully open',role:'open'}],
    usage:{zh:['卡爪垂直夹住工件两面'],en:['Jaws square on the work']},notes:['姿态检查未通过：滑块方向反了'],
    parts_sheet:'/api/projects/a/articulation/files/0123456789abcdef/parts.png',previews:'/api/projects/a/articulation/files/0123456789abcdef/states2.png'};
  h.respond=call=>{if(call.path==='/api/projects/a/articulation'){if(call.method==='POST')found={...found,running:true};return JSON.parse(JSON.stringify(found));}};
  await h.tab('articulation');await tick();await tick();
  const panel=h.node('listingPanel'),text=panel.textContent;
  assert.equal(h.w.document.querySelector('[data-listing-tab="articulation"]').getAttribute('aria-selected'),'true');
  assert.match(text,/有活动部件/);assert.match(text,/digital caliper/);assert.match(text,/The slider runs along the beam/);
  assert.deepEqual([...panel.querySelectorAll('.listing-poses strong')].map(node=>node.textContent),['闭合','全开']);
  assert.match(text,/卡爪垂直夹住工件两面/);assert.doesNotMatch(text,/Jaws square/);                 // the Chinese rules
  assert.match(text,/滑块方向反了/);
  assert.deepEqual([...panel.querySelectorAll('.listing-articulation-pictures img')].map(node=>node.getAttribute('src').split('?')[0]),[found.parts_sheet,found.previews]);
  panel.querySelector('[data-action="articulation-run"]').click();await tick();await tick();await tick();
  assert.ok(h.calls.some(call=>call.method==='POST' && call.path==='/api/projects/a/articulation'));
  assert.match(h.node('listingPanel').textContent,/识别中/);
  assert.equal(h.node('listingPanel').querySelector('[data-action="articulation-run"]').disabled,true);
  found={...found,running:false,status:'none',states:[]};await h.leave();                          // leaving stops the polling
  for(const [status,label] of [['none','没有活动部件'],['failed','识别失败']]){
    found={...found,status};h.w.setInspectorTab('listing');await tick();await tick();await h.tab('articulation');await tick();await tick();
    assert.match(h.node('listingPanel').textContent,new RegExp(label));await h.leave();
  }
});
