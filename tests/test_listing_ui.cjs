// The listing workspace (static/listing.js) against a stand-in server: what it shows, what it sends, what it keeps, walked
// through its five steps (选风格, 选图片, 填产品, 品牌与场景, 生成).
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const {JSDOM}=require(process.env.UI_TEST_JSDOM_PATH || 'jsdom');
const html=fs.readFileSync('static/index.html','utf8'),script=fs.readFileSync('static/listing.js','utf8');
// The workspace is the inspector's 套图 tab; here it starts hidden, as if another tab were showing.
const panel=html.slice(html.indexOf('<section id="inspectorListing"'),html.indexOf('<section id="inspectorEcommerce"')).replace('<section id="inspectorListing"','<section id="inspectorListing" hidden');
const tick=()=>new Promise(resolve=>setTimeout(resolve,0));
const settle=async()=>{for(let i=0;i<6;i++)await tick();};
// Values made inside the page belong to its own realm: compare what they hold, not whose Object made them.
const plain=value=>JSON.parse(JSON.stringify(value ?? null));


const ORDER=['T12','T01','T03','T13','T02','T04','T08','T06','T05','T07','T09','T10','T11'];
const AUTO=['T12','T01','T03','T13','T02','T04','T08','T06','T05'];
const KINDS={T12:{zh:'海报主图',en:'Hero poster'},T01:{zh:'功能标注',en:'Callouts'},T03:{zh:'主体 + 细节栏',en:'Product + details'},T13:{zh:'多姿态展示',en:'Poses'},
  T02:{zh:'深色细节特写',en:'Close-ups'},T04:{zh:'场景 + 参数带',en:'Scene + key figures'},T08:{zh:'使用场景组图',en:'Usage scenes'},T06:{zh:'尺寸图',en:'Dimensions'},
  T05:{zh:'包装清单',en:'In the box'},T07:{zh:'模块系统',en:'Modules'},T09:{zh:'同条件对比',en:'Comparison'},T10:{zh:'接口与连接',en:'Connections'},T11:{zh:'同系列变体',en:'Variants'}};
function state(changes={}) {
  return {ready:true,available:true,built_at:null,notes:[],task:null,order:ORDER,auto:AUTO,
    product:{title:'Ratchet Pipe Cutter',model:'PC-22',headline:'',subline:'',language:'en',size_mm:null,drafted:null,pages:null,aspect:'1:1',
      features:[{title:'Hardened wheel',detail:'Replaceable',parts:['Cutting_Wheel'],icon:''}],specs:[{label:'Capacity',value:'6 - 22 mm'}],figures:[],
      scenes:{enabled:true,count:3,audience:'auto',people:'auto',hint:''},
      usecase:{enabled:true,kind:'pipe',material:'pvc_white',mode:'through',side:'+',diameter_mm:null,width_mm:null,thickness_mm:null,length_mm:null,offset_mm:[0,0,0],rotate:[0,0,0],headline:''}},
    brand:{preset:'frame',name:'NORTHFORGE',accent:null,dark:null,badges:['PRO'],legal:'',unit:'mm',logo:null,accent_used:'#d9463d',accent_source:'product',dark_used:'#16171a',ground:'#ffffff'},
    presets:['frame','flat','tech','clean','promo'].map(id=>({id,label:{zh:'风格 '+id,en:id},note:{zh:'说明 '+id,en:''},ground:'light'})),
    components:[],roles:{battery:{zh:'电池',en:'Battery'},charger:{zh:'充电器',en:'Charger'},other:{zh:'其他',en:'Item'}},
    pages:[],plan:[{kind:'T12',index:0},{kind:'T01',index:0},{kind:'T08',index:0},{kind:'T06',skipped:'没有填写真实尺寸'}],
    kinds:KINDS,parts:['Frame','Cutting_Wheel','Knob'],size:{mm:215,known:false},
    options:{workpieces:['pipe','rod','board','sheet','block'],materials:['pvc_white','copper','steel','wood'],modes:['through','under','front','manual'],languages:['en','zh'],
      workpiece_names:{pipe:'管材',rod:'圆棒',board:'板材',sheet:'薄板',block:'块料'},material_names:{pvc_white:'PVC',copper:'铜',steel:'钢',wood:'木'}},
    scene_options:{audiences:[],image_ready:true,max:3,people:{auto:'自动'},aspects:['1:1','4:5']},
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
  h.change=(node,value)=>{if(value!==undefined){if(node.type==='checkbox' || node.type==='radio')node.checked=value;else node.value=value;}node.dispatchEvent(new w.Event('change',{bubbles:true}));};
  h.key=(node,key)=>node.dispatchEvent(new w.KeyboardEvent('keydown',{key,bubbles:true,cancelable:true}));
  h.open=async()=>{h.node('listingOpen').click();await settle();};
  h.leave=async()=>{w.setInspectorTab('review');await settle();};
  h.step=()=>w.document.querySelector('#listingSteps [aria-current="step"]')?.dataset.step;
  h.next=async()=>{h.node('listingNext').click();await settle();};
  // Walks forward with 下一步 (or back with 上一步) until the step is shown, as a seller would.
  h.goto=async id=>{const order=['style','pages','product','brand','build'];
    for(let i=0;i<8 && h.step()!==id;i++){h.node(order.indexOf(id)<order.indexOf(h.step())?'listingBack':'listingNext').click();await settle();}
    assert.equal(h.step(),id);};
  h.field=(scope,key)=>h.node('listingPanel').querySelector(`[data-scope="${scope}"][data-key="${key}"]`);
  h.card=value=>h.node('listingPanel').querySelector(`.listing-card[data-card="${value}"]`);
  h.writes=()=>h.calls.filter(call=>call.method!=='GET');
  return h;
}

test('a new set starts at the first step: the five styles as single-choice cards, nothing else on screen',async t=>{
  const h=await setup(t);await h.open();
  assert.equal(h.node('inspectorListing').hidden,false);
  assert.deepEqual(h.calls.map(call=>call.path),['/api/projects/a/listing']);
  assert.equal(h.node('listingStepCount').textContent,'1 / 5 · 选风格');assert.equal(h.node('listingStepTitle').textContent,'1. 选风格');
  assert.equal(h.node('listingMain').hidden,true,'the build bar waits for the last step');
  assert.equal(h.field('product','title'),null,'the product form is a later step');
  const radios=[...h.node('listingPanel').querySelectorAll('input[type="radio"][name="listingPreset"]')];
  assert.deepEqual(radios.map(node=>node.value),['frame','flat','tech','clean','promo']);
  assert.deepEqual(radios.map(node=>node.checked),[true,false,false,false,false]);
  const card=h.card('tech');
  assert.equal(card.querySelector('img').getAttribute('src'),'/static/listing-thumbs/style-tech.jpg');
  assert.equal(card.querySelector('.listing-card-name').textContent,'风格 tech');assert.equal(card.querySelector('.listing-card-note').textContent,'说明 tech');
  assert.equal(h.w.document.getElementById(radios[2].getAttribute('aria-labelledby')).textContent,'风格 tech','each card is named by its caption');
  // one choice: picking another style unpicks the first
  radios[2].click();await tick();
  assert.deepEqual(radios.map(node=>node.checked),[false,false,true,false,false]);
  assert.equal(h.node('listingBack').hidden,true);assert.equal(h.node('listingNext').hidden,false);
  const dots=[...h.node('listingSteps').querySelectorAll('button')];
  assert.equal(dots.length,5);assert.equal(dots[0].getAttribute('aria-current'),'step');
  assert.deepEqual(dots.map(node=>node.disabled),[false,true,true,true,true],'steps not reached yet cannot be jumped to');
});

test('the steps go in order, 下一步 saves what was chosen, and visited steps can be revisited from the dots',async t=>{
  const h=await setup(t);await h.open();
  h.card('promo').querySelector('input').click();await tick();
  await h.next();
  assert.deepEqual(plain(h.writes().map(call=>[call.method,call.path,call.body.preset])),[['PUT','/api/projects/a/listing/brand','promo']]);
  assert.equal(h.step(),'pages');assert.equal(h.node('listingStepCount').textContent,'2 / 5 · 选图片');
  assert.equal(h.w.document.activeElement,h.node('listingStepTitle'),'the new step is announced by moving to its title');
  h.change(h.card('T08').querySelector('input'),false);await tick();
  await h.next();
  const product=h.writes().at(-1);assert.equal(product.path,'/api/projects/a/listing/product');assert.deepEqual(plain(product.body.pages),['T12','T01','T06']);
  assert.equal(h.step(),'product');assert.equal(h.field('product','title').value,'Ratchet Pipe Cutter');
  await h.next();
  assert.equal(h.writes().length,2,'nothing changed on the product step: nothing is sent');
  assert.equal(h.step(),'brand');assert.equal(h.node('listingSkip').hidden,false);
  h.node('listingSkip').click();await settle();
  assert.equal(h.step(),'build');assert.equal(h.node('listingMain').hidden,false);assert.equal(h.node('listingPanel').hidden,true);
  assert.equal(h.node('listingNext').hidden,true,'the last step has one primary action: 生成套图');
  const rows=[...h.node('listingPlan').querySelectorAll('.listing-summary-row')];
  assert.deepEqual(rows.map(row=>row.querySelector('.listing-summary-label').textContent),['风格','图片','产品','品牌与场景']);
  assert.match(rows[0].textContent,/风格 promo/);assert.match(rows[1].textContent,/2 张：海报主图、功能标注；另有 1 张暂时无法生成/);
  assert.match(rows[2].textContent,/Ratchet Pipe Cutter · PC-22 · 未填真实尺寸 · 1 条卖点/);assert.match(rows[3].textContent,/NORTHFORGE · 场景图 3 张/);
  rows[2].querySelector('[data-action="step"]').click();await settle();
  assert.equal(h.step(),'product','修改 goes back to its step');
  const dots=[...h.node('listingSteps').querySelectorAll('button')];
  assert.deepEqual(dots.map(node=>node.disabled),[false,false,false,false,false]);
  dots[0].click();await settle();assert.equal(h.step(),'style');
  h.node('listingNext').click();await settle();assert.equal(h.step(),'pages');
  h.node('listingBack').click();await settle();assert.equal(h.step(),'style');
});

test('a step whose save fails stays on screen with the error, and the edits are kept',async t=>{
  const h=await setup(t);await h.open();await h.goto('product');
  h.type(h.field('product','title'),'New name');
  h.respond=call=>{if(call.method==='PUT')throw new Error('产品名称太长');};
  await h.next();
  assert.equal(h.step(),'product');assert.deepEqual(h.toasts.at(-1),['产品名称太长',true]);
  assert.equal(h.field('product','title').value,'New name');assert.equal(h.w.listingUnsaved(),true);
  h.respond=null;await h.next();assert.equal(h.step(),'brand');assert.equal(h.w.listingUnsaved(),false);
});

test('the page cards: a thumbnail each, the kinds that cannot be made now disabled with the reason, and 恢复自动',async t=>{
  const h=await setup(t);await h.open();await h.goto('pages');
  const cards=[...h.node('listingPanel').querySelectorAll('.listing-card')];
  assert.deepEqual(cards.map(node=>node.dataset.card),AUTO,'only the kinds the app can build are offered');
  assert.deepEqual(cards.map(node=>node.querySelector('img').getAttribute('src')),AUTO.map(kind=>`/static/listing-thumbs/page-${kind}.jpg`));
  assert.deepEqual(cards.map(node=>node.querySelector('.listing-card-name').textContent),AUTO.map(kind=>KINDS[kind].zh));
  assert.match(h.card('T06').querySelector('.listing-card-note').textContent,/外形尺寸/);
  assert.deepEqual(cards.map(node=>node.querySelector('input').checked),[true,true,false,false,false,false,true,false,false]);
  const blocked=h.card('T06').querySelector('input');
  assert.equal(blocked.disabled,true);assert.ok(h.card('T06').classList.contains('is-disabled'));
  assert.match(h.card('T06').querySelector('.listing-card-why').textContent,/暂时无法生成：没有填写真实尺寸/);
  assert.match(blocked.getAttribute('aria-describedby'),/-why/,'the reason is read out with the card');
  assert.match(h.node('listingPanel').textContent,/已选 3 张（自动挑选）/);
  assert.equal(h.node('listingPanel').querySelector('[data-action="pages-auto"]'),null);
  // turning one off makes an explicit list; the page that cannot be made yet stays on it
  h.change(h.card('T08').querySelector('input'),false);await tick();
  assert.deepEqual(['T12','T01','T08','T06'].map(kind=>h.card(kind).querySelector('input').checked),[true,true,false,false]);
  h.change(h.card('T03').querySelector('input'),true);await tick();
  assert.match(h.node('listingPanel').textContent,/已选 3 张/);
  const auto=h.node('listingPanel').querySelector('[data-action="pages-auto"]');assert.ok(auto,'an explicit list offers the way back');
  await h.goto('build');
  assert.deepEqual(plain(h.writes().find(call=>call.path.endsWith('/listing/product')).body.pages),['T12','T01','T03','T06']);
  await h.goto('pages');
  h.node('listingPanel').querySelector('[data-action="pages-auto"]').click();await settle();
  assert.equal(h.writes().at(-1).body.pages,null,'恢复自动 hands the list back to the planner');
  assert.equal(h.node('listingPanel').querySelector('[data-action="pages-auto"]'),null);
  // nothing chosen: 下一步 says so instead of moving on
  for(const kind of ['T12','T01','T08'])h.change(h.card(kind).querySelector('input'),false);
  await h.next();assert.equal(h.step(),'pages');assert.deepEqual(h.toasts.at(-1),['请至少选一张能生成的图片',true]);
});

test('arrow keys move within a card group; on the style cards they also pick',async t=>{
  const h=await setup(t);await h.open();
  const radio=value=>h.card(value).querySelector('input');
  radio('frame').focus();h.key(radio('frame'),'ArrowRight');await tick();
  assert.equal(h.w.document.activeElement,radio('flat'));assert.equal(radio('flat').checked,true);
  h.key(radio('flat'),'End');await tick();assert.equal(h.w.document.activeElement,radio('promo'));assert.equal(radio('promo').checked,true);
  h.key(radio('promo'),'ArrowRight');await tick();assert.equal(h.w.document.activeElement,radio('frame'),'wraps round');
  assert.equal(radio('frame').checked,true);
  await h.goto('pages');
  const box=kind=>h.card(kind).querySelector('input');
  box('T08').focus();h.key(box('T08'),'ArrowRight');await tick();
  assert.equal(h.w.document.activeElement,box('T05'),'the disabled 尺寸图 card is passed over');
  assert.equal(box('T05').checked,false,'moving does not tick a page');
  h.key(box('T05'),'ArrowLeft');await tick();assert.equal(h.w.document.activeElement,box('T08'));
  h.key(box('T08'),'Home');await tick();assert.equal(h.w.document.activeElement,box('T12'));
});

test('a missing preview picture leaves a neutral block with the name',async t=>{
  const h=await setup(t);await h.open();
  const card=h.card('clean'),img=card.querySelector('img');
  assert.equal(card.querySelector('.listing-card-ph').textContent,'风格 clean');
  img.dispatchEvent(new h.w.Event('error'));
  assert.equal(img.hidden,true);assert.ok(card.querySelector('.listing-card-thumb').classList.contains('is-missing'));
});

test('the product step shows the essentials first and the rest behind 更多信息; components and moving parts only when their page is chosen',async t=>{
  const h=await setup(t);await h.open();await h.goto('product');
  const panel=h.node('listingPanel'),inClosed=node=>!!node.closest('details:not([open])');
  for(const key of ['title','model','size_mm','language'])assert.equal(inClosed(h.field('product',key)),false,key);
  assert.equal(inClosed(panel.querySelector('[data-scope="feature"][data-key="title"]')),false,'selling points are essentials');
  for(const key of ['headline','subline'])assert.equal(h.field('product',key).closest('details').dataset.section,'more',key);
  assert.equal(panel.querySelector('details[data-section="more"]').open,false);
  assert.match(panel.querySelector('[data-sum="more"]').textContent,/已填 1 项/);
  assert.equal(inClosed(h.node('listingSource')),true,'a product with a name does not open the paste box');
  assert.match(h.field('product','size_mm').placeholder,/按模型估计约 215/);assert.match(panel.textContent,/填写后才会画出产品尺寸线/);
  assert.match(panel.querySelector('.listing-parts summary').textContent,/对应部件（1）/);
  assert.equal(h.node('listingComponents'),null,'no 包装清单 page: no components');assert.equal(h.node('listingArticulation'),null,'no 多姿态展示 page: no moving parts');
  assert.equal(h.calls.some(call=>call.path.endsWith('/articulation')),false);
  // choosing the two pages brings their settings into this step
  await h.goto('pages');h.change(h.card('T05').querySelector('input'),true);h.change(h.card('T13').querySelector('input'),true);await h.goto('product');
  assert.ok(h.node('listingComponents'));assert.ok(h.node('listingArticulation'));
  assert.ok(h.calls.some(call=>call.method==='GET' && call.path==='/api/projects/a/articulation'));
  // and turning them off again takes them away
  await h.goto('pages');h.change(h.card('T05').querySelector('input'),false);await h.goto('product');
  assert.equal(h.node('listingComponents'),null);assert.ok(h.node('listingArticulation'));
  // an empty product opens the paste box first
  const empty=await setup(t,state({product:{...state().product,title:'',features:[]}}));await empty.open();await empty.goto('product');
  assert.equal(empty.node('listingSource').closest('details').open,true);
});

test('words typed are saved on the way to the last step, and a running build shows its percentage, locks the steps and can be cancelled',async t=>{
  const h=await setup(t);await h.open();await h.goto('product');
  h.type(h.field('product','headline'),'One-hand clean cuts');h.type(h.field('product','size_mm'),'225');
  h.node('listingPanel').querySelector('[data-action="add-spec"]').click();await tick();
  const rows=h.node('listingPanel').querySelectorAll('[data-scope="spec"][data-key="label"]');assert.equal(rows.length,2);
  assert.equal(h.w.document.activeElement,rows[1],'the new row is ready to type in');
  assert.equal(h.node('listingPanel').querySelector('details[data-section="more"]').open,true);
  h.type(rows[1],'Wheel');h.type(h.node('listingPanel').querySelectorAll('[data-scope="spec"][data-key="value"]')[1],'Hardened steel');
  assert.equal(h.field('product','headline').value,'One-hand clean cuts','adding a row keeps what was typed');
  const part=h.node('listingPanel').querySelector('[data-scope="feature-part"][value="Knob"]');h.change(part,true);
  await h.goto('build');
  h.node('listingBuild').click();await settle();
  const sent=h.writes();
  assert.deepEqual(sent.map(call=>[call.method,call.path]),[['PUT','/api/projects/a/listing/product'],['POST','/api/projects/a/listing/build']]);
  assert.equal(sent[0].body.headline,'One-hand clean cuts');assert.equal(sent[0].body.size_mm,225);
  assert.deepEqual(plain(sent[0].body.specs),[{label:'Capacity',value:'6 - 22 mm'},{label:'Wheel',value:'Hardened steel'}]);
  assert.deepEqual(plain(sent[0].body.features[0].parts),['Cutting_Wheel','Knob']);
  assert.equal(h.node('listingStatus').textContent,'渲染第 1 / 4 张素材 · 12%');
  assert.equal(h.node('listingProgress').hidden,false);assert.equal(h.node('listingProgress').getAttribute('aria-valuenow'),'12');
  assert.equal(h.node('listingBuild').disabled,true);assert.equal(h.node('listingCancel').hidden,false);
  assert.ok([...h.node('listingSteps').querySelectorAll('button:not([aria-current])')].every(node=>node.disabled),'the steps wait while the set is made');
  assert.ok([...h.node('listingPlan').querySelectorAll('[data-action="step"]')].every(node=>node.disabled));
  assert.equal(h.node('listingBack').disabled,true);
  h.node('listingCancel').click();await tick();
  assert.deepEqual(h.calls.at(-2).path,'/api/projects/a/listing/t1/cancel');
});

test('a build already running opens on the last step with the steps locked',async t=>{
  const h=await setup(t,state({task:{id:'t1',status:'running',stage:'scenes',progress:30,detail:'生成第 1 / 3 张场景图'}}));await h.open();
  assert.equal(h.step(),'build');assert.equal(h.node('listingMain').hidden,false);
  assert.ok([...h.node('listingSteps').querySelectorAll('button:not([aria-current])')].every(node=>node.disabled));
  await h.w.listingGoStep('style');assert.equal(h.step(),'build');
});

test('built images are shown with their notes and downloads, the set opens on its summary, and nothing a user typed is run as markup',async t=>{
  const pages=[{id:'p1',kind:'T12',file:'01-T12.png',url:'/files/a/listing/build-1/01-T12.png',width:2000,height:2000,notes:[]},
    {id:'p2',kind:'T01',file:'02-T01.png',url:'/files/a/listing/build-1/02-T01.png',width:2000,height:2000,notes:['有 1 个卖点没有对应到部件 <img src=x onerror=alert(1)>']}];
  const initial=state({pages,built_at:1700000000,notes:['品牌色取自产品 <script>alert(1)</script>'],task:{id:'t0',status:'completed',progress:100}});
  initial.product.title='<img src=x onerror=alert(2)>';
  const h=await setup(t,initial);await h.open();
  assert.equal(h.step(),'build');assert.equal(h.node('listingStepCount').textContent,'5 / 5 · 生成');
  assert.equal(h.node('listingEditSteps').hidden,false);assert.equal(h.node('listingBack').hidden,true);
  const figures=[...h.node('listingPages').querySelectorAll('.listing-page')];assert.equal(figures.length,2);
  assert.match(figures[0].querySelector('img').getAttribute('src'),/^\/files\/a\/listing\/build-1\/01-T12\.png\?t=1700000000000$/);
  assert.equal(figures[0].querySelector('figcaption a').getAttribute('download'),'01-T12.png');
  assert.match(figures[1].textContent,/02 · 功能标注/);assert.match(figures[1].textContent,/有 1 个卖点没有对应到部件 <img src=x onerror=alert\(1\)>/);
  assert.equal(h.node('inspectorListing').querySelectorAll('script').length,0);assert.equal(h.node('inspectorListing').querySelectorAll('img[onerror]').length,0);
  assert.match(h.node('listingPlan').textContent,/<img src=x onerror=alert\(2\)>/);
  assert.equal(h.node('listingBuild').textContent,'重新生成套图');
  assert.equal(h.node('listingDownload').hidden,false);assert.equal(h.node('listingDownload').getAttribute('href'),'/api/projects/a/listing/download');
  assert.match(h.node('listingStatus').textContent,/^共 2 张/);
  // neither page was reviewed: only the draft can be downloaded, and its name says what it holds
  assert.equal(h.node('listingDownloadReviewed').hidden,true);assert.match(h.node('listingDownload').textContent,/草稿/);
  // 修改设置 walks the steps again from the first
  h.node('listingEditSteps').click();await settle();
  assert.equal(h.step(),'style');assert.ok([...h.node('listingSteps').querySelectorAll('button')].every(node=>!node.disabled),'every step is open to jump to');
  assert.equal(h.field('product','title'),null);
  await h.goto('product');assert.equal(h.field('product','title').value,'<img src=x onerror=alert(2)>');
});

test('pages that passed the checks and the review get their own download, counted',async t=>{
  const ok={checks:{passed:true,issues:[]},review:{status:'passed',score:86}};
  const pages=[{id:'p1',kind:'T12',file:'01-T12.png',url:'/files/a/listing/build-1/01-T12.png',width:2000,height:2000,notes:[],...ok},
    {id:'p2',kind:'T01',file:'02-T01.png',url:'/files/a/listing/build-1/02-T01.png',width:2000,height:2000,notes:[],checks:{passed:true,issues:[]},review:{status:'failed',score:61}}];
  const h=await setup(t,state({pages,built_at:1700000000,task:{id:'t0',status:'completed',progress:100}}));await h.open();
  const reviewed=h.node('listingDownloadReviewed');
  assert.equal(reviewed.hidden,false);assert.equal(reviewed.getAttribute('href'),'/api/projects/a/listing/download?reviewed=1');
  assert.match(reviewed.textContent,/1\/2/);assert.match(h.node('listingDownload').textContent,/草稿/);
});

test('a rebuild reuses the kept headlines; asking for new ones is its own button and sends fresh',async t=>{
  const empty=await setup(t);await empty.open();
  assert.equal(empty.node('listingRewrite').hidden,true,'nothing built yet: there are no headlines to rewrite');
  const pages=[{id:'p1',kind:'T12',file:'01-T12.png',url:'/files/a/listing/build-1/01-T12.png',width:2000,height:2000,notes:[]}];
  const h=await setup(t,state({pages,built_at:1700000000,task:{id:'t0',status:'completed',progress:100}}));await h.open();
  assert.equal(h.node('listingRewrite').hidden,false);assert.equal(h.node('listingRewrite').disabled,false);
  h.node('listingBuild').click();await settle();
  assert.deepEqual(plain(h.calls.find(call=>call.method==='POST').body),{},'the plain rebuild asks for nothing new');
  assert.equal(h.node('listingRewrite').hidden,true,'hidden while a build runs');
  const again=await setup(t,state({pages,built_at:1700000000,task:{id:'t0',status:'completed',progress:100}}));await again.open();
  again.node('listingRewrite').click();await settle();
  assert.deepEqual(plain(again.calls.find(call=>call.method==='POST').body),{fresh:true});
});

test('components uploaded with the project are edited, turned and removed through the server, and their progress is shown',async t=>{
  const row={id:'c1',name:'Battery',role:'battery',sku:'BP-205',caption:'',quantity:2,kind:'model',status:'processing',stage:'render',progress:64,error:'',size_mm:117.5,size_source:'model',
    included:true,cutout:null,notes:['尺寸按模型文件的单位估计为最长边 117.5 mm；填写真实尺寸后，套装图里的大小比例才准确。']};
  const plan=[...state().plan,{kind:'T05',skipped:'还没有处理完成的组件'}];
  const h=await setup(t,state({components:[row],plan}));await h.open();await h.goto('product');
  const section=h.node('listingPanel').querySelector('details[data-section="components"]');
  assert.equal(section.open,true,'the 包装清单 page waits for them: the box is open');
  assert.match(section.querySelector('[data-sum="components"]').textContent,/1 个，1 个未就绪/);
  const card=h.node('listingPanel').querySelector('[data-component="c1"]');
  assert.match(card.querySelector('.listing-component-status').textContent,/渲染抠图 · 64%/);assert.match(card.textContent,/按模型文件的单位估计/);
  assert.equal(card.querySelector('[data-turn="flip"]').disabled,true,'a component being processed is not turned');
  assert.match(h.node('listingNotes').textContent,/有组件还在处理/);
  h.state={...h.state,components:[{...row,status:'ready',stage:'ready',progress:100,cutout:{url:'/files/a/components/c1/cutout-1.png',width:900,height:700}}]};
  await h.w.listingLoad(false);
  const ready=h.node('listingPanel').querySelector('[data-component="c1"]');
  assert.equal(ready.querySelector('img').getAttribute('src'),'/files/a/components/c1/cutout-1.png');
  assert.match(section.querySelector('[data-sum="components"]').textContent,/^1 个$/);
  h.change(ready.querySelector('[data-key="quantity"]'),'3');await settle();
  h.change(h.node('listingPanel').querySelector('[data-component="c1"] [data-key="included"]'),false);await settle();
  h.node('listingPanel').querySelector('[data-component="c1"] [data-turn="flip"]').click();await settle();
  h.node('listingPanel').querySelector('[data-component="c1"] [data-action="component-refresh"]').click();await settle();
  h.answer='cancel';h.node('listingPanel').querySelector('[data-component="c1"] [data-action="component-remove"]').click();await settle();
  assert.equal(h.calls.some(call=>call.method==='DELETE'),false,'nothing is removed without a yes');
  assert.match(h.asked.at(-1).description,/「Battery」/);assert.deepEqual(plain(h.asked.at(-1).actions),['取消','删除组件']);
  h.answer='yes';h.node('listingPanel').querySelector('[data-component="c1"] [data-action="component-remove"]').click();await settle();
  const sent=h.writes().map(call=>[call.method,call.path,call.body]);
  assert.deepEqual(plain(sent),[['PUT','/api/projects/a/components/c1',{quantity:3}],['PUT','/api/projects/a/components/c1',{included:false}],
    ['PUT','/api/projects/a/components/c1',{turn:'flip'}],['POST','/api/projects/a/components/c1/refresh',null],['DELETE','/api/projects/a/components/c1',null]]);
  assert.equal(h.node('listingPanel').querySelector('form'),null,'components come with the project, not from here');
  assert.match(h.node('listingPanel').textContent,/在新建项目时与主模型一起上传/);
});

test('the brand step is optional, each part a collapsed section with its summary; the colour follows the product until one is set',async t=>{
  const h=await setup(t);await h.open();
  h.card('tech').querySelector('input').click();await tick();
  await h.goto('brand');
  const sections=[...h.node('listingPanel').querySelectorAll('details.listing-section')];
  assert.deepEqual(sections.map(node=>node.dataset.section),['identity','colors','marks','share','scenes','usecase']);
  assert.ok(sections.every(node=>!node.open),'everything starts folded');
  assert.equal(h.node('listingPanel').querySelector('[data-sum="identity"]').textContent,'NORTHFORGE · 无标志');
  assert.equal(h.node('listingPanel').querySelector('[data-sum="colors"]').textContent,'品牌色跟随产品');
  const colour=h.field('brand','accent');assert.equal(colour.disabled,true);assert.equal(colour.value,'#d9463d');
  assert.match(h.node('listingPanel').textContent,/当前 #d9463d（取自产品）/);
  sections[1].open=true;sections[1].dispatchEvent(new h.w.Event('toggle'));
  const auto=h.node('listingPanel').querySelector('[data-scope="brand-auto"][data-key="accent"]');auto.focus();h.change(auto,false);await tick();
  assert.equal(h.field('brand','accent').disabled,false);
  assert.equal(h.node('listingPanel').querySelector('details[data-section="colors"]').open,true,'an opened section stays open when the step is drawn again');
  assert.equal(h.w.document.activeElement,h.node('listingPanel').querySelector('[data-scope="brand-auto"][data-key="accent"]'),'and the focus stays on the control');
  h.type(h.field('brand','accent'),'#0a84ff');h.type(h.field('brand','badges'),'20V， PRO , ');h.type(h.field('brand','name'),'NEWFORGE');
  assert.equal(h.node('listingPanel').querySelector('[data-sum="identity"]').textContent,'NEWFORGE · 无标志','the summary follows what is typed');
  assert.equal(h.node('listingPanel').querySelector('[data-sum="marks"]').textContent,'徽标 20V、PRO · 毫米');
  h.node('listingSkip').click();await settle();
  const saved=h.writes().filter(call=>call.method==='PUT' && call.path.endsWith('/listing/brand')).at(-1).body;
  assert.deepEqual(plain(saved),{preset:'tech',name:'NEWFORGE',accent:'#0a84ff',dark:null,badges:['20V','PRO'],legal:'',unit:'mm'});
  assert.equal(h.step(),'build');
});

test('the use case is set by workpiece, place and size, with only the fields that apply',async t=>{
  const h=await setup(t);await h.open();await h.goto('brand');
  assert.equal(h.node('listingPanel').querySelector('[data-sum="usecase"]').textContent,'开启 · 管材');
  assert.equal(h.field('usecase','enabled').checked,true);
  assert.deepEqual([...h.field('usecase','mode').options].map(option=>option.textContent),['穿过工具的开口','垫在工具下方','顶在工具前端','手动放置（工具中心）']);
  assert.ok(h.field('usecase','diameter_mm'));assert.equal(h.field('usecase','thickness_mm'),null);assert.equal(h.field('usecase','side'),null);
  h.type(h.field('usecase','diameter_mm'),'22');
  h.field('usecase','kind').focus();h.change(h.field('usecase','kind'),'board');await tick();
  assert.equal(h.field('usecase','diameter_mm'),null);assert.ok(h.field('usecase','thickness_mm'));assert.ok(h.field('usecase','width_mm'));
  assert.equal(h.w.document.activeElement,h.field('usecase','kind'),'the redraw keeps the focus on the select');
  h.change(h.field('usecase','mode'),'front');await tick();
  assert.ok(h.field('usecase','side'),'which end is asked only where the workpiece stands at an end');
  const offsets=h.node('listingPanel').querySelectorAll('[data-scope="usecase-triple"][data-key="offset_mm"]');h.type(offsets[2],'-12.5');
  h.type(h.field('usecase','headline'),'Drills hardwood');
  await h.next();
  const use=h.writes().find(call=>call.method==='PUT').body.usecase;
  assert.equal(use.kind,'board');assert.equal(use.mode,'front');assert.equal(use.diameter_mm,22);assert.deepEqual(plain(use.offset_mm),[0,0,-12.5]);assert.equal(use.headline,'Drills hardwood');
});

test('a draft fills the form from the server, and unsaved words are saved when the tab is left',async t=>{
  const h=await setup(t);await h.open();await h.goto('product');
  h.node('listingSource').value='6-22 mm';
  h.respond=call=>{if(call.path.endsWith('/listing/draft')){h.state={...h.state,product:{...h.state.product,headline:'Clean, square cuts',drafted:{source:'rules',at:1,notes:[]}}};}};
  h.node('listingPanel').querySelector('[data-action="draft"]').click();await settle();
  const draft=h.calls.find(call=>call.path.endsWith('/listing/draft'));
  assert.deepEqual(plain(draft.body),{hint:'6-22 mm',language:'en'});
  assert.equal(h.step(),'product','a draft keeps the step');
  assert.equal(h.field('product','headline').value,'Clean, square cuts');assert.match(h.node('listingPanel').textContent,/按部件名称起草/);
  h.respond=null;h.type(h.field('product','subline'),'Typed and not yet saved');
  await h.leave();
  const last=h.writes().filter(call=>call.method==='PUT').at(-1);
  assert.equal(last.path,'/api/projects/a/listing/product');assert.equal(last.body.subline,'Typed and not yet saved');
});

test('a server that refuses is reported and the workspace stays usable',async t=>{
  const h=await setup(t,state({available:false}));await h.open();
  assert.equal(h.node('listingBuild').disabled,true);assert.equal(h.node('listingStatus').textContent,'渲染组件不可用，无法生成套图。');
  const failing=await setup(t,state({task:{id:'t9',status:'failed',error:'渲染失败：显存不足',progress:40}}));await failing.open();
  assert.equal(failing.node('listingStatus').textContent,'上次生成失败：渲染失败：显存不足');assert.equal(failing.node('listingBuild').disabled,false);
  failing.respond=call=>{if(call.method==='POST')throw new Error('这个项目的套图正在生成，请等待完成或先取消。');};
  await failing.goto('build');
  failing.node('listingBuild').click();await settle();
  assert.deepEqual(failing.toasts.at(-1),['这个项目的套图正在生成，请等待完成或先取消。',true]);assert.equal(failing.node('listingBuild').disabled,false);
});

test('a pasted description is sent for extraction, the fields fill from the answer and the text is kept',async t=>{
  const h=await setup(t);await h.open();await h.goto('product');
  h.node('listingPanel').querySelector('[data-action="extract"]').click();await tick();
  assert.deepEqual(h.toasts.at(-1),['请先粘贴一段产品介绍',true]);assert.equal(h.calls.some(call=>call.path.endsWith('/listing/extract')),false);   // nothing to send
  const text='Ratchet Pipe Cutter PC-22. Cuts 6 - 22 mm copper and PVC.';
  h.type(h.node('listingSource'),text);
  h.respond=call=>{if(call.path.endsWith('/listing/extract')){h.state={...h.state,product:{...h.state.product,source:call.body.text,headline:'Cuts 6 - 22 mm pipe',
    specs:[{label:'Capacity',value:'6 - 22 mm'}],drafted:{source:'extracted',at:1,notes:['以下内容含有介绍里没有的数字，已略去：指标「Weight」。']}}};}};
  h.node('listingPanel').querySelector('[data-action="extract"]').click();await settle();
  const sent=h.writes();
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
  await h.leave();h.w.setInspectorTab('listing');await settle();
  assert.equal(h.calls.filter(call=>call.path==='/api/projects/a/listing').length,2);          // shown again: fresh state
  h.w.current={id:'b'};h.w.listingActivate(false);await settle();
  assert.equal(h.calls.at(-1).path,'/api/projects/b/listing');                                  // another project: its own record
  assert.equal(h.step(),'style','another project starts at its own first step');
});

test('extracting saves brand edits first, and an answer that arrives after another project opened is not applied to it',async t=>{
  const h=await setup(t);await h.open();await h.goto('product');
  // a brand edit still waiting to be saved (as after a save that failed on the way here)
  h.w.listingOnInputOnce({type:'input',target:{type:'text',value:'NEWBRAND',dataset:{scope:'brand',key:'name'}}});assert.equal(h.w.listingUnsaved(),true);   // a reload would ask first
  h.type(h.node('listingSource'),'Cuts 6 - 22 mm pipe.');
  let release;const late=new Promise(resolve=>{release=resolve;});
  h.respond=async call=>{if(call.path.endsWith('/listing/extract')){await late;return {...JSON.parse(JSON.stringify(h.state)),product:{...h.state.product,title:'From A'}};}};
  h.node('listingPanel').querySelector('[data-action="extract"]').click();await settle();
  assert.ok(h.calls.some(call=>call.method==='PUT' && call.path==='/api/projects/a/listing/brand' && call.body.name==='NEWBRAND'));   // not dropped by the reset
  h.respond=null;h.w.current={id:'b'};h.w.listingActivate(false);h.w.listingActivate(true);await settle();
  assert.equal(h.calls.filter(call=>call.path==='/api/projects/b/listing').length,1);           // two callers, one switch
  release();await settle();
  assert.equal(h.field('product','title'),null,'project b opens at its first step');
  await h.goto('product');assert.notEqual(h.field('product','title').value,'From A');            // A's answer stays with A
  assert.equal(h.w.listingUnsaved(),false);
});

test('the moving parts sit in the product step while 多姿态展示 is chosen, show what the pose agent found and ask it again',async t=>{
  const plan=[...state().plan,{kind:'T13',skipped:'没有识别出可以展示的活动部件'}];
  const h=await setup(t,state({plan}));await h.open();
  let found={status:'ready',running:false,product_kind:'digital caliper',reasoning:'The slider runs along the beam.',at:5,
    states:[{id:'closed',label_zh:'闭合',label_en:'Closed',role:'rest'},{id:'open',label_zh:'全开',label_en:'Fully open',role:'open'}],
    usage:{zh:['卡爪垂直夹住工件两面'],en:['Jaws square on the work']},notes:['姿态检查未通过：滑块方向反了'],
    parts_sheet:'/api/projects/a/articulation/files/0123456789abcdef/parts.png',previews:'/api/projects/a/articulation/files/0123456789abcdef/states2.png'};
  h.respond=call=>{if(call.path==='/api/projects/a/articulation'){if(call.method==='POST')found={...found,running:true};return JSON.parse(JSON.stringify(found));}};
  await h.goto('pages');
  assert.equal(h.card('T13').querySelector('input').disabled,true);assert.match(h.card('T13').textContent,/没有识别出可以展示的活动部件/);
  await h.goto('product');
  const section=h.node('listingPanel').querySelector('details[data-section="articulation"]');
  assert.equal(section.open,true,'the poses page waits for it: the box is open');
  assert.equal(section.querySelector('[data-sum="articulation"]').textContent,'有活动部件 · 2 个姿态');
  const box=h.node('listingArticulation'),text=box.textContent;
  assert.match(text,/有活动部件/);assert.match(text,/digital caliper/);assert.match(text,/The slider runs along the beam/);
  assert.deepEqual([...box.querySelectorAll('.listing-poses strong')].map(node=>node.textContent),['闭合','全开']);
  assert.match(text,/卡爪垂直夹住工件两面/);assert.doesNotMatch(text,/Jaws square/);                 // the Chinese rules
  assert.match(text,/滑块方向反了/);
  assert.deepEqual([...box.querySelectorAll('.listing-articulation-pictures img')].map(node=>node.getAttribute('src').split('?')[0]),[found.parts_sheet,found.previews]);
  box.querySelector('[data-action="articulation-run"]').click();await settle();
  assert.ok(h.calls.some(call=>call.method==='POST' && call.path==='/api/projects/a/articulation'));
  assert.match(h.node('listingArticulation').textContent,/识别中/);
  assert.equal(h.node('listingArticulation').querySelector('[data-action="articulation-run"]').disabled,true);
  found={...found,running:false,status:'none',states:[]};await h.leave();                          // leaving stops the polling
  for(const [status,label] of [['none','没有活动部件'],['failed','识别失败']]){
    found={...found,status};h.w.setInspectorTab('listing');await settle();
    assert.match(h.node('listingArticulation').textContent,new RegExp(label));await h.leave();
  }
});
