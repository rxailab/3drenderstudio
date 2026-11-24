// The built set as the listing workspace shows it (static/listing.js): large in the main stage while the 套图 tab is open,
// each page with its status, a page's own detail view, the notes sorted for the seller, the scenes' checks, the quota line.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const {JSDOM}=require(process.env.UI_TEST_JSDOM_PATH || 'jsdom');
const html=fs.readFileSync('static/index.html','utf8'),script=fs.readFileSync('static/listing.js','utf8');
const panel=html.slice(html.indexOf('<section id="inspectorListing"'),html.indexOf('<section id="inspectorEcommerce"')).replace('<section id="inspectorListing"','<section id="inspectorListing" hidden');
const stage=html.slice(html.indexOf('<section id="listingStage"'),html.indexOf('<div id="viewer"'));
const tick=()=>new Promise(resolve=>setTimeout(resolve,0));
const plain=value=>JSON.parse(JSON.stringify(value ?? null));

const kinds={T12:{zh:'海报主图',en:'Hero poster'},T01:{zh:'功能标注',en:'Callouts'},T06:{zh:'尺寸图',en:'Dimensions'},T08:{zh:'使用场景组图',en:'Usage scenes'}};
const page=(id,kind,file,extra={})=>({id,kind,file,url:`/files/a/listing/build-1/${file}`,svg:null,width:1500,height:1500,notes:[],checks:{passed:true,issues:[]},...extra});
const pages=[
  page('p1','T12','01-T12.png',{review:{status:'passed',score:86,summary:'Clear hero.',issues:[],vetoes:[],veto_words:[]},
    note_view:{seller:[],tech:[{text:'band: tagline dropped (would wrap)',count:1}]}}),
  page('p2','T01','02-T01.png',{review:{status:'failed',score:61,summary:'A pointer misses its part.',issues:[{region:'left label',evidence:'points at the frame'}],vetoes:['wrong_pointer'],veto_words:['引线指错位置']},
    note_view:{seller:[{text:'视觉审核未通过（61 分，否决项 引线指错位置）。',level:'error',count:1},{text:'有卖点的文字太长，标注里放不下：请把卖点标题或说明改短。',level:'warn',count:1}],
      tech:[{text:'d3: value pill longer than its line',count:2}]}}),
  page('p3','T06','03-T06.png',{review:{status:'not_verified'},note_view:{seller:[],tech:[]}})];
const scenes=[{index:0,url:'/files/a/listing/scenes-k/scene1/scene1.png',title:'On the bench',role:'work',check:'passed',placed:'composite',restored:false,locked:true,usable:true,issues:[],flags:[]},
  {index:1,url:'/files/a/listing/scenes-k/scene2/scene2.png',title:'In the van',role:'work',check:'failed',placed:'painted',restored:false,locked:false,usable:false,issues:['handle redrawn'],flags:['部件不符']},
  {index:2,url:'/files/a/listing/scenes-k/scene3/scene3.png',title:'Wall',role:'place',check:'pending',placed:'painted',restored:true,locked:true,usable:false,issues:[],flags:[]}];

function state(changes={}) {
  return {ready:true,available:true,layout_available:true,built_at:1700000000,notes:[],task:{id:'t0',status:'completed',progress:100},order:['T12','T01','T06','T08'],
    product:{title:'Cart',model:'',headline:'',subline:'',language:'en',size_mm:null,drafted:null,pages:null,features:[],specs:[],figures:[],
      usecase:{enabled:false,kind:'pipe',material:'pvc_white',mode:'through',side:'+',offset_mm:[0,0,0],rotate:[0,0,0],headline:''}},
    brand:{preset:'frame',name:'',accent:null,dark:null,badges:[],legal:'',unit:'mm',logo:null,accent_used:'#d9463d',accent_source:'product',dark_used:'#16171a'},
    presets:[],components:[],roles:{},pages,scenes,kinds,plan:[{kind:'T12',index:0},{kind:'T01',index:0},{kind:'T06',index:0},{kind:'T08',skipped:'通过核对的场景图不到 2 张'}],
    notes_view:{seller:[{text:'第 2 张场景图没有生成：图像服务的额度已用尽（HTTP 402）',level:'critical',count:1},{text:'「使用场景组图」暂时无法生成：通过核对的场景图不到 2 张。',level:'error',count:1},
      {text:'品牌色取自产品本身的颜色 #d9463d。',level:'info',count:4}],tech:[{text:'headline needed 3 lines at 96px',count:1}]},
    degraded:null,parts:[],size:{mm:900,known:true},options:{workpieces:[],materials:[],modes:[],languages:['en','zh'],workpiece_names:{},material_names:{}},elements:{},...changes};
}
async function setup(t,initial=state()) {
  const dom=new JSDOM(`<!doctype html><body><div class="stage">${stage}<div id="viewer" class="viewer"></div></div><button id="listingOpen"></button>${panel}</body>`,{runScripts:'outside-only',pretendToBeVisual:true});
  const w=dom.window;t.after(()=>w.close());
  const h={w,calls:[],state:initial,toasts:[],asked:[],answer:true};
  w.setInspectorTab=tab=>{const node=w.document.getElementById('inspectorListing'),was=!node.hidden;node.hidden=tab!=='listing';
    if(was && tab!=='listing')w.listingDeactivate();if(tab==='listing')w.listingActivate(true);};
  w.$=id=>w.document.getElementById(id);
  w.esc=value=>String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  w.toast=(message,error=false)=>h.toasts.push([message,error]);
  w.confirm=text=>{h.asked.push(text);return h.answer;};
  w.current={id:'a'};
  w.api=async(path,options={})=>{
    const call={path,method:options.method || 'GET',body:options.body};h.calls.push(call);
    if(call.method==='POST' && (path.endsWith('/listing/build') || path.endsWith('/redraw'))){h.state={...h.state,task:{id:'t1',status:'running',stage:'scenes',progress:30,detail:'生成第 2 / 3 张场景图'}};return h.state.task;}
    return JSON.parse(JSON.stringify(h.state));
  };
  w.eval(script);
  h.node=id=>w.document.getElementById(id);
  h.open=async()=>{h.node('listingOpen').click();await tick();await tick();};
  h.key=(node,key)=>node.dispatchEvent(new w.KeyboardEvent('keydown',{key,bubbles:true}));
  h.cards=()=>[...h.node('listingStageBody').querySelectorAll('.listing-page')];
  return h;
}

test('a built set fills the main stage with a large grid, each page with its status and score',async t=>{
  const h=await setup(t);await h.open();
  assert.equal(h.w.document.body.classList.contains('listing-stage-on'),true);assert.equal(h.node('listingStage').hidden,false);
  const cards=h.cards();assert.equal(cards.length,3);
  assert.deepEqual(cards.map(node=>node.querySelector('.listing-badge').textContent),['通过 · 86','未通过 · 61','未审核']);
  assert.deepEqual(cards.map(node=>node.querySelector('.listing-badge').dataset.status),['passed','failed','unreviewed']);
  assert.match(cards[1].querySelector('.listing-page-open').getAttribute('aria-label'),/功能标注（未通过，61 分）/);
  assert.match(cards[1].querySelector('.listing-page-note').textContent,/视觉审核未通过.*另有 1 条/);
  assert.match(h.node('listingStageHead').textContent,/通过 1.*未通过 1.*未审核 1/);
  assert.equal(h.node('listingPages').querySelectorAll('.listing-page').length,0,'the inspector points at the stage, it does not repeat the set');
  assert.match(h.node('listingPages').textContent,/套图显示在左侧主画面/);
  // leaving the tab gives the stage back to the model
  h.w.setInspectorTab('review');await tick();
  assert.equal(h.w.document.body.classList.contains('listing-stage-on'),false);assert.equal(h.node('listingStage').hidden,true);
});

test('with a set built the inspector opens on the summary step, and the stage keeps the set while the settings are walked again',async t=>{
  const h=await setup(t);await h.open();
  const step=()=>h.w.document.querySelector('#listingSteps [aria-current="step"]')?.dataset.step;
  assert.equal(step(),'build');assert.equal(h.node('listingStepCount').textContent,'5 / 5 · 生成');
  assert.equal(h.node('listingMain').hidden,false);assert.equal(h.node('listingEditSteps').hidden,false);
  assert.equal(h.node('listingPlan').querySelectorAll('[data-action="step"]').length,4,'each line of the summary leads back to its step');
  h.node('listingEditSteps').click();for(let i=0;i<6;i++)await tick();
  assert.equal(step(),'style');assert.equal(h.node('listingMain').hidden,true);
  assert.equal(h.node('listingStage').hidden,false,'the set stays on the stage');assert.equal(h.cards().length,3);
  h.node('listingNext').click();for(let i=0;i<6;i++)await tick();
  assert.equal(step(),'pages');assert.equal(h.node('listingStage').hidden,false);
  // a build that starts (here from the stage) brings the inspector back to the summary and locks the steps
  h.node('listingStageBody').querySelectorAll('[data-action="scene-redraw"]')[0].click();for(let i=0;i<6;i++)await tick();
  assert.equal(step(),'build');assert.ok([...h.node('listingSteps').querySelectorAll('button:not([aria-current])')].every(node=>node.disabled));
});

test('a page opens in a detail view with the full picture, the review, its vetoes and its notes, and the keyboard moves through it',async t=>{
  const h=await setup(t);await h.open();
  h.cards()[1].querySelector('.listing-page-open').click();await tick();
  const detail=h.node('listingStageBody').querySelector('.listing-detail');assert.ok(detail);
  assert.equal(h.w.document.activeElement,h.node('listingDetailTitle'));
  assert.equal(detail.querySelector('.listing-detail-picture img').getAttribute('src'),'/files/a/listing/build-1/02-T01.png?t=1700000000000');
  const text=detail.textContent;
  assert.match(text,/02 · 功能标注/);assert.match(text,/A pointer misses its part/);assert.match(text,/引线指错位置/);assert.match(text,/left label：points at the frame/);
  assert.match(text,/有卖点的文字太长/);
  const tech=detail.querySelector('details.listing-tech');assert.ok(tech);assert.equal(tech.open,false,'the template log is folded away');
  assert.match(tech.querySelector('summary').textContent,/技术记录（1）/);assert.match(tech.textContent,/value pill longer than its line ×2/);
  assert.doesNotMatch(detail.querySelector('.listing-detail-notes').textContent,/value pill/);
  h.key(detail,'ArrowRight');await tick();
  assert.match(h.node('listingStageBody').querySelector('.listing-detail h3').textContent,/03 · 尺寸图/);
  assert.match(h.node('listingStageBody').textContent,/没有经过视觉审核/);
  // the same keys on the step controls belong to the steps: the open page stays as it is
  for(const node of [h.node('listingSteps'),h.node('listingNext')]){h.key(node,'ArrowRight');h.key(node,'Escape');}await tick();
  assert.match(h.node('listingStageBody').querySelector('.listing-detail h3').textContent,/03 · 尺寸图/);
  h.key(h.node('listingStageBody'),'Escape');await tick();
  assert.equal(h.node('listingStageBody').querySelector('.listing-detail'),null);
  assert.equal(h.w.document.activeElement?.dataset.page,'p3','focus goes back to the page that was open');
});

test('the model can be brought back while the tab stays open, and the set returns on request',async t=>{
  const h=await setup(t);await h.open();
  h.node('listingStageHead').querySelector('[data-action="stage-model"]').click();await tick();
  assert.equal(h.node('listingStage').hidden,true);assert.equal(h.w.document.body.classList.contains('listing-stage-on'),false);
  assert.equal(h.node('listingPages').querySelectorAll('.listing-page').length,3,'the set is still in the inspector');
  h.node('listingPages').querySelector('[data-action="stage-set"]').click();await tick();await tick();
  assert.equal(h.node('listingStage').hidden,false);
});

test('set notes are grouped, ordered by severity, and the technical ones are folded away',async t=>{
  const h=await setup(t);await h.open();
  const rows=[...h.node('listingNotes').children];
  assert.deepEqual(rows.slice(0,3).map(node=>node.dataset.level),['critical','error','info']);
  assert.match(rows[2].textContent,/×4/);
  const tech=h.node('listingNotes').querySelector('details.listing-tech');assert.ok(tech);assert.equal(tech.open,false);assert.match(tech.textContent,/headline needed 3 lines/);
  // a server without the sorted view: English notes still go under 技术记录, not among the seller's
  const old=await setup(t,state({notes_view:undefined,notes:['品牌色取自产品。','d3: value pill longer than its line','品牌色取自产品。']}));await old.open();
  const shown=[...old.node('listingNotes').querySelectorAll(':scope > li:not(.listing-tech-row)')].map(node=>node.textContent);
  assert.ok(shown.some(text=>/品牌色取自产品。 ×2/.test(text)));assert.ok(shown.every(text=>!/value pill/.test(text)));
  assert.match(old.node('listingNotes').querySelector('details').textContent,/value pill/);
});

test('a build cut short by the AI quota says so in amber and 补全 starts the build again',async t=>{
  const h=await setup(t,state({degraded:{reason:'quota',affected:['translation','scenes','review'],words:['文字翻译','场景图','视觉审核']}}));await h.open();
  const line=h.node('listingDegraded').querySelector('.listing-degraded');assert.ok(line);
  assert.match(line.textContent,/AI 额度用尽：文字翻译、场景图、视觉审核未完成。/);assert.match(line.textContent,/沿用/);
  assert.match(h.node('listingStatus').textContent,/部分未完成/);
  assert.ok(h.node('listingStageNotice').querySelector('.listing-degraded'),'the stage says it too');
  line.querySelector('[data-action="fill-in"]').click();await tick();await tick();await tick();
  const sent=h.calls.find(call=>call.method==='POST');
  assert.deepEqual(plain(sent),{path:'/api/projects/a/listing/build',method:'POST',body:{}});
  assert.equal(h.node('listingDegraded').querySelector('.listing-degraded'),null,'hidden while the build runs');
});

test('scenes carry their check and how the product got in; only a locked product is called locked; one can be drawn again',async t=>{
  const h=await setup(t);await h.open();
  const cards=[...h.node('listingStageBody').querySelectorAll('.listing-scene')];
  assert.deepEqual(cards.map(node=>node.querySelector('.listing-badge').textContent),['通过核对','未通过核对','未完成核对']);
  assert.deepEqual(cards.map(node=>node.querySelectorAll('.listing-badge')[1].textContent),['模型合成','AI 绘制','AI 绘制']);
  assert.match(cards[0].textContent,/模型的渲染图，合成进背景照片/);assert.doesNotMatch(cards[1].textContent,/渲染图/);assert.match(cards[2].textContent,/换回模型的渲染图/);
  assert.doesNotMatch(h.node('listingStageBody').textContent,/遮罩锁定/);
  assert.match(cards[1].textContent,/部件不符/);assert.match(cards[1].textContent,/不会放上页面/);assert.match(cards[2].textContent,/下次生成时会补做核对/);
  h.answer=false;cards[1].querySelector('[data-action="scene-redraw"]').click();await tick();await tick();
  assert.equal(h.calls.some(call=>call.method==='POST'),false,'declined: nothing is paid for');assert.match(h.asked[0],/只重新生成这一张/);
  h.answer=true;h.node('listingStageBody').querySelectorAll('[data-action="scene-redraw"]')[1].click();await tick();await tick();await tick();
  const sent=h.calls.find(call=>call.method==='POST');
  assert.deepEqual(plain(sent),{path:'/api/projects/a/listing/scenes/1/redraw',method:'POST',body:{url:scenes[1].url}});
  assert.ok(h.node('listingStageBody').querySelector('[data-action="scene-redraw"]').disabled,'a running build: no second redraw');
});

test('when a review ran the reviewed set is the primary download and the draft says what it holds',async t=>{
  const h=await setup(t);await h.open();
  const reviewed=h.node('listingDownloadReviewed'),draft=h.node('listingDownload');
  assert.equal(reviewed.hidden,false);assert.ok(reviewed.classList.contains('primary'));assert.match(reviewed.textContent,/1\/3/);
  assert.ok(draft.classList.contains('subtle'));assert.match(draft.textContent,/全部 3 张，含 2 张未通过或未审核/);
  assert.ok(h.node('listingBuild').classList.contains('secondary'),'one primary action');
  const head=[...h.node('listingStageHead').querySelectorAll('a')];
  assert.deepEqual(head.map(node=>node.getAttribute('href')),['/api/projects/a/listing/download?reviewed=1','/api/projects/a/listing/download']);
  assert.ok(head[0].classList.contains('primary'));
  const none=await setup(t,state({pages:pages.map(row=>({...row,review:{status:'not_verified'}})),scenes:[]}));await none.open();
  assert.equal(none.node('listingDownloadReviewed').hidden,true);assert.ok(none.node('listingDownload').classList.contains('secondary'));
  assert.match(none.node('listingDownload').textContent,/未经审核/);assert.ok(none.node('listingBuild').classList.contains('primary'));
});
