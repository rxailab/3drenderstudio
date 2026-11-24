// The listing workspace (static/listing.js) when chosen pages still lack what they need: picking them anyway, 去填写,
// the checklist of 填产品 that ticks itself off, adding components, the moving parts and the summary's 不会生成 lines.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const {JSDOM}=require(process.env.UI_TEST_JSDOM_PATH || 'jsdom');
const html=fs.readFileSync('static/index.html','utf8'),script=fs.readFileSync('static/listing.js','utf8');
const panel=html.slice(html.indexOf('<section id="inspectorListing"'),html.indexOf('<section id="inspectorEcommerce"')).replace('<section id="inspectorListing"','<section id="inspectorListing" hidden');
const tick=()=>new Promise(resolve=>setTimeout(resolve,0));
const settle=async()=>{for(let i=0;i<8;i++)await tick();};
const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const plain=value=>JSON.parse(JSON.stringify(value ?? null));

const ORDER=['T12','T01','T03','T13','T02','T04','T08','T06','T05','T07','T09','T10','T11'];
const AUTO=['T12','T01','T03','T13','T02','T04','T08','T06','T05'];
const KINDS={T12:{zh:'海报主图'},T01:{zh:'功能标注'},T03:{zh:'主体 + 细节栏'},T13:{zh:'多姿态展示'},T02:{zh:'深色细节特写'},T04:{zh:'场景 + 参数带'},
  T08:{zh:'使用场景组图'},T06:{zh:'尺寸图'},T05:{zh:'包装清单'},T07:{zh:'模块系统'},T09:{zh:'同条件对比'},T10:{zh:'接口与连接'},T11:{zh:'同系列变体'}};
const reason=(why,...needs)=>({why,fix:needs[0] ? {step:needs[0][0],field:needs[0][1]} : null,needs:needs.map(([step,field])=>({step,field})),hard:!needs.length});
const WHY={
  T06:reason('没有填写真实尺寸：在第 3 步「填产品」的「最长边真实尺寸（mm）」里填写',['product','size']),
  T05:reason('还没有处理完成的组件：在第 3 步「填产品」的「套装组件」里点「添加组件」',['product','components']),
  T13:reason('没有识别出可以展示的活动部件（需要至少 2 个姿态）：在第 3 步「填产品」的「活动部件」里点「识别活动部件」',['product','articulation']),
  T04:reason('缺两样：一是场景图；二是参数',['product','specs'],['brand','scenes']),
  T08:reason('需要 2 张场景图，但 AI 场景图已关闭：在第 4 步「品牌与场景」的「AI 场景图」里勾选「生成 AI 场景图」',['brand','scenes']),
  T02:reason('模型是一个整体，没有可单独特写的部件'),T03:reason('模型是一个整体，没有可单独特写的部件')};
function state(changes={}) {
  const blocked=changes.blocked || WHY;
  return {ready:true,available:true,built_at:null,notes:[],task:null,order:ORDER,auto:AUTO,blocked,
    product:{title:'Ratchet Pipe Cutter',model:'PC-22',headline:'',subline:'',language:'en',size_mm:null,drafted:null,pages:null,aspect:'1:1',
      features:[{title:'Hardened wheel',detail:'',parts:[],icon:''},{title:'Quick release',detail:'',parts:[],icon:''}],specs:[],figures:[],
      scenes:{enabled:false,count:3,audience:'auto',people:'auto',hint:''},
      usecase:{enabled:false,kind:'pipe',material:'pvc_white',mode:'through',side:'+',diameter_mm:null,width_mm:null,thickness_mm:null,length_mm:null,offset_mm:[0,0,0],rotate:[0,0,0],headline:''}},
    brand:{preset:'frame',name:'',accent:null,dark:null,badges:[],legal:'',unit:'mm',logo:null,accent_used:'#d9463d',accent_source:'product',dark_used:'#16171a'},
    presets:[{id:'frame',label:{zh:'风格 frame'},note:{zh:''}}],components:[],roles:{battery:{zh:'电池'},other:{zh:'其他'}},
    pages:[],plan:AUTO.map(kind=>blocked[kind]?{kind,skipped:blocked[kind].why}:{kind,index:0}),
    kinds:KINDS,parts:['Body'],size:{mm:215,known:false},
    options:{workpieces:['pipe'],materials:['pvc_white'],modes:['through'],languages:['en','zh'],workpiece_names:{pipe:'管材'},material_names:{pvc_white:'PVC'}},
    scene_options:{audiences:[],image_ready:true,max:3,people:{auto:'自动'},aspects:['1:1']},elements:{},vision_ready:true,...changes};
}
async function setup(t,initial=state()) {
  const dom=new JSDOM(`<!doctype html><body><button id="listingOpen"></button>${panel}</body>`,{runScripts:'outside-only',pretendToBeVisual:true});
  const w=dom.window;t.after(()=>w.close());
  const h={w,calls:[],state:initial,toasts:[],respond:null};
  w.setInspectorTab=tab=>{const node=w.document.getElementById('inspectorListing');node.hidden=tab!=='listing';if(tab==='listing')w.listingActivate(true);};
  w.$=id=>w.document.getElementById(id);
  w.esc=value=>String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  w.toast=(message,error=false)=>h.toasts.push([message,error]);
  w.current={id:'a'};
  w.api=async(path,options={})=>{
    const call={path,method:options.method || 'GET',body:options.body};h.calls.push(call);
    if(h.respond){const answer=await h.respond(call);if(answer!==undefined)return answer;}
    if(call.method==='PUT' && path.endsWith('/listing/product'))h.state={...h.state,product:{...h.state.product,...call.body}};
    if(path==='/api/projects/a/articulation')return {status:'unknown',states:[],running:false};
    return JSON.parse(JSON.stringify(h.state));
  };
  w.eval(script);
  w.listingAutoDelay=0;                  // the background save of 填产品 runs at once here
  h.node=id=>w.document.getElementById(id);
  h.type=(node,value)=>{node.focus();node.value=value;node.dispatchEvent(new w.Event('input',{bubbles:true}));};
  h.change=(node,value)=>{if(value!==undefined)node.checked=value;node.dispatchEvent(new w.Event('change',{bubbles:true}));};
  h.open=async()=>{h.node('listingOpen').onclick=null;w.setInspectorTab('listing');await settle();};
  h.step=()=>w.document.querySelector('#listingSteps [aria-current="step"]')?.dataset.step;
  h.goto=async id=>{const order=['style','pages','product','brand','build'];
    for(let i=0;i<8 && h.step()!==id;i++){h.node(order.indexOf(id)<order.indexOf(h.step())?'listingBack':'listingNext').click();await settle();}
    assert.equal(h.step(),id);};
  h.card=value=>h.node('listingPanel').querySelector(`.listing-card[data-card="${value}"]`);
  h.field=(scope,key)=>h.node('listingPanel').querySelector(`[data-scope="${scope}"][data-key="${key}"]`);
  h.need=field=>h.node('listingNeeds').querySelector(`[data-need="${field}"]`);
  return h;
}

test('a page waiting for the seller can be ticked and stays on the list; one nothing here can supply cannot',async t=>{
  const h=await setup(t,state({product:{...state().product,pages:['T12']}}));await h.open();await h.goto('pages');
  const size=h.card('T06').querySelector('input');
  assert.equal(size.disabled,false);assert.equal(size.checked,false);
  h.change(size,true);await tick();
  assert.equal(h.card('T06').querySelector('input').checked,true,'shown ticked, not greyed out');
  assert.match(h.card('T06').querySelector('.listing-card-why').textContent,/^需补充：没有填写真实尺寸/);
  assert.match(h.node('listingPanel').querySelector('.listing-pick-count').textContent,/已选 2 张：1 张可以生成，1 张需补充资料/);
  const hard=h.card('T02').querySelector('input');
  assert.equal(hard.disabled,true);hard.click();await tick();
  assert.equal(h.card('T02').querySelector('input').checked,false,'a one-piece model has no close-ups to pick');
  await h.goto('product');
  const saved=h.calls.filter(call=>call.method==='PUT' && call.path.endsWith('/listing/product')).at(-1);
  assert.deepEqual(plain(saved.body.pages),['T12','T06'],'the chosen page is saved with the list');
});

test('去填写 saves, goes to the step that fills it, opens its section and puts the cursor in the field, without unticking the card',async t=>{
  const h=await setup(t,state({product:{...state().product,pages:['T12','T06','T04']}}));await h.open();await h.goto('pages');
  const button=h.card('T06').closest('.listing-card-wrap').querySelector('[data-action="fill"]');
  assert.equal(h.card('T06').contains(button),false,'the button is outside the card label');
  h.change(h.card('T12').querySelector('input'),false);await tick();          // an unsaved change goes in first
  h.node('listingPanel').querySelector('.listing-card-wrap [data-kind="T06"]').click();await settle();
  assert.equal(h.step(),'product');
  assert.deepEqual(plain(h.calls.filter(call=>call.method==='PUT').at(-1).body.pages),['T04','T06']);
  assert.equal(h.w.document.activeElement,h.field('product','size_mm'),'the size field has the cursor');
  assert.ok(h.field('product','size_mm').closest('.listing-field').classList.contains('is-needed'),'the field the chosen page needs is marked');
  assert.equal(h.field('product','size_mm').closest('.listing-field').querySelector('.listing-need-tag').hidden,false);
  // the scene part of 场景 + 参数带 is on step 4: its row goes there and opens the AI 场景图 section
  h.need('scenes').querySelector('[data-action="need-go"]').click();await settle();
  assert.equal(h.step(),'brand');
  assert.equal(h.node('listingPanel').querySelector('details[data-section="scenes"]').open,true);
  assert.equal(h.w.document.activeElement,h.field('scenes','enabled'));
});

test('the checklist of 填产品 lists what the chosen pages need and ticks a row off once the saved product has it',async t=>{
  const h=await setup(t,state({product:{...state().product,pages:['T12','T06','T04']}}));await h.open();await h.goto('product');
  const rows=[...h.node('listingNeeds').querySelectorAll('.listing-need')];
  assert.deepEqual(rows.map(row=>row.dataset.need),['specs','scenes','size'],'in the order of the pages');
  assert.deepEqual(rows.map(row=>row.dataset.done),['false','false','false']);
  assert.match(h.node('listingNeeds').textContent,/你选的图片还需要/);assert.match(h.node('listingNeeds').textContent,/还差 3 项/);
  assert.match(h.need('size').textContent,/「尺寸图」需要/);
  // the server answers the saved size: 尺寸图 can be made, 场景 + 参数带 now waits only for its scene
  h.respond=call=>{if(call.method==='PUT' && call.path.endsWith('/listing/product') && call.body.size_mm===225){
    const blocked={...WHY,T04:reason('参数已有，缺场景图',['brand','scenes'])};delete blocked.T06;
    h.state={...h.state,product:{...h.state.product,...call.body},blocked,size:{mm:225,known:true},
      plan:h.state.plan.map(row=>row.kind==='T06'?{kind:'T06',index:0}:row)};}};
  const input=h.field('product','size_mm');
  h.type(input,'22');h.type(input,'225');
  await wait(20);await settle();
  const saves=h.calls.filter(call=>call.method==='PUT' && call.path.endsWith('/listing/product'));
  assert.ok(saves.length>=1,'saved in the background');assert.equal(saves.at(-1).body.size_mm,225);
  assert.deepEqual(['size','specs','scenes'].map(field=>h.need(field).dataset.done),['true','true','false']);
  assert.match(h.need('size').textContent,/已补齐/);assert.equal(h.need('size').querySelector('button'),null);
  assert.match(h.node('listingNeeds').textContent,/还差 1 项/);
  assert.equal(h.w.document.activeElement,h.field('product','size_mm'),'the focus stays in the field');
  assert.equal(h.field('product','size_mm').value,'225','what was typed stays');
  assert.equal(h.field('product','size_mm').closest('.listing-field').classList.contains('is-needed'),false);
  assert.equal(h.w.listingUnsaved(),false);
});

test('words typed while a background save is on the way are kept and go in with the next save',async t=>{
  const h=await setup(t);await h.open();await h.goto('product');
  let release;const late=new Promise(resolve=>{release=resolve;});
  h.respond=async call=>{if(call.method==='PUT'){await late;}};
  const puts=()=>h.calls.filter(call=>call.method==='PUT');
  h.type(h.field('product','title'),'Cutter A');await wait(5);
  h.type(h.field('product','title'),'Cutter AB');await wait(5);
  assert.equal(puts().length,1,'one save at a time');
  release();await wait(20);await settle();
  assert.equal(h.field('product','title').value,'Cutter AB','the answer to the first save does not undo the second edit');
  assert.deepEqual(puts().map(call=>call.body.title),['Cutter A','Cutter AB']);
  assert.equal(h.w.listingUnsaved(),false);
});

test('the 套装组件 section shows while 包装清单 is chosen and adds components through the server, then follows their processing',async t=>{
  const h=await setup(t,state({product:{...state().product,pages:['T12','T05']}}));await h.open();await h.goto('product');
  const section=h.node('listingPanel').querySelector('details[data-section="components"]');
  assert.ok(section,'shown although no component is ready');assert.equal(section.open,true);
  assert.match(h.need('components').textContent,/套装组件/);
  h.need('components').querySelector('[data-action="need-go"]').click();await settle();
  assert.equal(h.w.document.activeElement,h.node('listingPanel').querySelector('[data-action="component-add"]'));
  let picked=0;h.node('listingComponentFiles').click=()=>{picked++;};
  h.node('listingPanel').querySelector('[data-action="component-add"]').click();await tick();
  assert.equal(picked,1,'添加组件 opens the file picker');
  const processing={id:'c1',name:'battery',role:'battery',sku:'',caption:'',quantity:1,kind:'model',status:'processing',stage:'render',progress:10,included:true,cutout:null,notes:[]};
  h.respond=call=>{if(call.method==='POST' && call.path==='/api/projects/a/components'){h.state={...h.state,components:[processing]};return {...processing};}};
  const input=h.node('listingComponentFiles');
  Object.defineProperty(input,'files',{configurable:true,value:[new h.w.File(['glTF'],'battery.glb'),new h.w.File(['png'],'charger.png')]});
  input.dispatchEvent(new h.w.Event('change',{bubbles:true}));await settle();
  const posts=h.calls.filter(call=>call.method==='POST');
  assert.deepEqual(posts.map(call=>call.path),['/api/projects/a/components','/api/projects/a/components'],'one component per file');
  assert.deepEqual(posts.map(call=>call.body.get('files').name),['battery.glb','charger.png'],'sent as the files field the server reads');
  assert.match(h.toasts.at(-1)[0],/已添加 2 个组件/);
  assert.match(h.node('listingComponents').textContent,/渲染抠图 · 10%/);
  // while it is processed the state is read again until it is ready
  h.respond=null;h.state={...h.state,components:[{...processing,status:'ready',stage:'ready',progress:100,cutout:{url:'/files/a/components/c1/cutout.png'}}]};
  const before=h.calls.filter(call=>call.path==='/api/projects/a/listing').length;
  await wait(1700);await settle();
  assert.ok(h.calls.filter(call=>call.path==='/api/projects/a/listing').length>before,'polled');
  assert.equal(h.node('listingComponents').querySelector('img').getAttribute('src'),'/files/a/components/c1/cutout.png');
  // a file of another kind is refused before anything is sent
  Object.defineProperty(input,'files',{configurable:true,value:[new h.w.File(['x'],'manual.pdf')]});
  const sent=h.calls.length;input.dispatchEvent(new h.w.Event('change',{bubbles:true}));await settle();
  assert.equal(h.calls.length,sent);assert.match(h.toasts.at(-1)[0],/不是可用的组件文件/);
});

test('the 活动部件 section shows while 多姿态展示 is chosen, offers 识别活动部件, and says where the vision model is set when there is none',async t=>{
  const h=await setup(t,state({product:{...state().product,pages:['T12','T13']},vision_ready:false}));await h.open();await h.goto('pages');
  h.card('T13').closest('.listing-card-wrap').querySelector('[data-action="fill"]').click();await settle();
  assert.equal(h.step(),'product');
  const section=h.node('listingPanel').querySelector('details[data-section="articulation"]');assert.equal(section.open,true);
  const run=h.node('listingArticulation').querySelector('[data-action="articulation-run"]');
  assert.equal(run.textContent,'识别活动部件');assert.equal(run.disabled,true);
  assert.match(h.node('listingArticulation').textContent,/「AI 设置」.*「预览图像分析」.*「视觉模型名称」/);
  assert.equal(h.w.document.activeElement,h.node('listingVisionNeeded'),'the cursor lands on what to do about it');
  const ready=await setup(t,state({product:{...state().product,pages:['T12','T13']}}));await ready.open();await ready.goto('product');
  const button=ready.node('listingArticulation').querySelector('[data-action="articulation-run"]');
  assert.equal(button.disabled,false);button.click();await settle();
  assert.ok(ready.calls.some(call=>call.method==='POST' && call.path==='/api/projects/a/articulation'));
});

test('a model without named parts says the 对应部件 picker is not there and what that means for the close-up pages',async t=>{
  const h=await setup(t);await h.open();await h.goto('product');
  assert.equal(h.node('listingPanel').querySelector('.listing-parts'),null);
  const note=h.node('listingPartsNone').textContent;
  assert.match(note,/没有「对应部件」可选/);assert.match(note,/「功能标注」只能由视觉模型/);assert.match(note,/「主体 \+ 细节栏」和「深色细节特写」/);
  const parts=await setup(t,state({parts:['Frame','Wheel']}));await parts.open();await parts.goto('product');
  assert.equal(parts.node('listingPartsNone'),null);assert.ok(parts.node('listingPanel').querySelector('.listing-parts'));
});

test('the summary lists every chosen page that will not be made, with 去填写 where the seller can fill it in',async t=>{
  const h=await setup(t,state({product:{...state().product,pages:['T12','T06','T08','T02']}}));await h.open();await h.goto('build');
  const held=[...h.node('listingPlan').querySelectorAll('.listing-summary-held')];
  assert.deepEqual(held.map(row=>row.querySelector('.listing-summary-text').textContent.match(/「(.+?)」/)[1]),['深色细节特写','使用场景组图','尺寸图']);
  assert.deepEqual(held.map(row=>!!row.querySelector('[data-action="fill"]')),[false,true,true],'the one-piece close-ups have nothing to fill in');
  assert.equal(h.node('listingBuild').disabled,false,'海报主图 can be made: the rest does not hold the build');
  held[1].querySelector('[data-action="fill"]').click();await settle();
  assert.equal(h.step(),'brand');assert.equal(h.w.document.activeElement,h.field('scenes','enabled'));
});

test('an empty 卖点 row added before a background save stays typeable after the next state from the server',async t=>{
  const h=await setup(t);await h.open();await h.goto('product');
  const errors=[];h.w.addEventListener('error',event=>errors.push(String(event.error || event.message)));
  h.node('listingPanel').querySelector('[data-action="add-feature"]').click();await settle();
  h.type(h.field('product','title'),'Ratchet Pipe Cutter Pro');           // the background save leaves the empty row out
  await wait(20);await settle();
  const saved=h.calls.filter(call=>call.method==='PUT' && call.path.endsWith('/listing/product')).at(-1);
  assert.deepEqual(plain(saved.body.features).map(row=>row.title),['Hardened wheel','Quick release']);
  assert.equal(h.w.listingUnsaved(),false);
  await h.w.listingLoad(false);await settle();                            // a component poll, 添加组件 or a component edit
  const titles=[...h.node('listingPanel').querySelectorAll('[data-scope="feature"][data-key="title"]')];
  assert.equal(titles.length,3);
  h.type(titles[2],'One-hand ratchet');await wait(20);await settle();
  assert.deepEqual(errors,[]);
  const last=h.calls.filter(call=>call.method==='PUT' && call.path.endsWith('/listing/product')).at(-1);
  assert.deepEqual(plain(last.body.features).map(row=>row.title),['Hardened wheel','Quick release','One-hand ratchet'],'what was typed is saved');
});

test('when the saved rows no longer match the form, the form is drawn again so no field points past the rows',async t=>{
  const h=await setup(t);await h.open();await h.goto('product');
  h.node('listingPanel').querySelector('[data-action="add-feature"]').click();await settle();
  h.type(h.field('product','title'),'Ratchet Pipe Cutter Pro');await wait(20);await settle();
  h.state={...h.state,product:{...h.state.product,features:[{title:'Hardened wheel',detail:'',parts:[],icon:''}]}};   // a row dropped elsewhere
  const errors=[];h.w.addEventListener('error',event=>errors.push(String(event.error || event.message)));
  await h.w.listingLoad(false);await settle();
  const titles=[...h.node('listingPanel').querySelectorAll('[data-scope="feature"][data-key="title"]')];
  assert.deepEqual(titles.map(node=>node.value),['Hardened wheel',''],'the form shows the saved row and the empty one');
  h.type(titles.at(-1),'Quick release');await wait(20);await settle();
  assert.deepEqual(errors,[]);
  const last=h.calls.filter(call=>call.method==='PUT' && call.path.endsWith('/listing/product')).at(-1);
  assert.deepEqual(plain(last.body.features).map(row=>row.title),['Hardened wheel','Quick release']);
});
