// The listing workspace ("套图"): what the product page's images are made from, and the images themselves.
// It edits three things kept on the project (the product record, the brand kit, the components) and asks
// the server to build the set. Every picture shown here is a file the server rendered; nothing is drawn
// in the browser. Uses $, api, esc, toast and `current` from app.js.
// The settings are walked through as five steps, one on screen at a time (style, pages, product, brand and
// scenes, build), so a seller is never faced with every control at once.
let listingState=null,listingEdit=null,listingTimer=null,listingBusy=false,listingProject=null;
const listingDirty={product:false,brand:false};
// Every product edit bumps the revision: a save that returns after more was typed leaves the product unsaved (and kept).
let listingProductRev=0;
function listingTouch() { listingDirty.product=true;listingProductRev++; }
const listingStageNames={queued:'排队中',analyse:'分析模型',pose:'检查摆放朝向',render:'渲染抠图',cutout:'抠图',ready:'已完成'};
const listingModeNames={through:'穿过工具的开口',under:'垫在工具下方',front:'顶在工具前端',manual:'手动放置（工具中心）'};
const listingClone=value=>JSON.parse(JSON.stringify(value));
function listingOpenFor() { return listingProject && current && current.id===listingProject?listingProject:null; }
function listingComponentsBusy() { return (listingState?.components || []).some(row=>['queued','processing'].includes(row.status)); }
function listingTaskActive() { return ['queued','running'].includes(listingState?.task?.status); }
function listingNumber(value) { const text=String(value ?? '').trim();if(!text)return null;const number=Number(text);return Number.isFinite(number)?number:text; }
const listingQuote=value=>String(value ?? '').replace(/["\\]/g,'\\$&');

// ---- the steps ----
const listingSteps=[
  {id:'style',name:'选风格',lead:'选一种版式风格，整套图片都按它排版。'},
  {id:'pages',name:'选图片',lead:'点选要生成的图片，可以多选。标着「需补充」的也能选，点「去填写」补齐资料；灰色的这个产品做不了，下面写着原因。'},
  {id:'product',name:'填产品',lead:'先填最要紧的几项；标题、规格等放在「更多信息」里，可以以后再补。'},
  {id:'brand',name:'品牌与场景',lead:'可选。品牌名称、颜色、标志和场景图都有默认设置，不改可以直接跳过。'},
  {id:'build',name:'生成',lead:'核对一下设置，然后生成整套图片。'}];
// The page kinds that bring their own settings into the 填产品 step: the components for the in-the-box page and the
// moving parts for the poses page. Each appears whenever its page is on the list (ready or still waiting for them).
const LISTING_BOX='T05',LISTING_POSES='T13';
// One short line on what each page shows, under its preview on the 选图片 cards.
const listingKindNotes={T12:'产品大图配主标题，作为第一张',T01:'用引线标出各个部件的卖点',T03:'产品主体旁排出几处细节',T13:'同一件产品打开、合上等不同状态',
  T02:'深色底上的部件近景',T04:'场景图配一条核心参数',T08:'几张使用场景拼成一页',T06:'标出长、宽、高等外形尺寸',T05:'盒子里的所有物品一览'};
const listingThumbURL=name=>`/static/listing-thumbs/${name}.jpg`;
let listingStep='style',listingVisited=new Set(['style']),listingStepFor=null,listingStepMoving=false;
const listingOpenSections=new Set();          // which collapsed sections the seller opened, kept across redraws
function listingStepIndex(id=listingStep) { return Math.max(0,listingSteps.findIndex(step=>step.id===id)); }
function listingLocked() { return listingTaskActive(); }

// The workspace lives in the inspector's 套图 tab: it is shown, loaded and kept up to date while that tab is open.
function listingUnsaved() { return !!(listingOpenFor() && (listingDirty.product || listingDirty.brand)); }
function listingVisible() { const panel=$('inspectorListing');return !!panel && !panel.hidden; }
function listingOpen() {
  if(!current)return;
  if(typeof setInspectorTab==='function')setInspectorTab('listing');else listingActivate();
}
// Called whenever the 套图 tab is shown (and when another project is opened with it showing).
let listingSwitching=null,listingSeen='';
function listingProjectSeen() { return current?`${current.id}|${current.version}|${current.status}|${current.analysis?1:0}`:''; }
async function listingActivate(refresh=true) {
  if(!current || !listingVisible())return;
  if(listingProject===current.id && (listingState || !refresh)){
    // The project changed under the panel (its analysis finished, a plan edit): what the panel shows is read again, or
    // 生成套图 stays disabled after an upload until the seller switches tabs.
    const seen=listingProjectSeen();
    if(refresh || (listingState && seen!==listingSeen)){listingSeen=seen;listingLoad(false).catch(()=>{});}
    if(refresh && listingStep==='product' && $('listingArticulation'))listingRenderArticulation(true);
    return;
  }
  listingSeen=listingProjectSeen();
  const target=current.id;
  if(listingSwitching===target)return;            // already switching to this project (a redraw and the tab both asked)
  listingSwitching=target;
  try{if(listingProject && listingProject!==target)await listingSavePending().catch(error=>toast('上一个项目的套图设置没有保存：'+error.message,true));}
  finally{listingSwitching=null;}
  if(!current || current.id!==target)return;
  listingProject=current.id;listingState=null;listingEdit=null;listingDirty.product=listingDirty.brand=false;
  listingDetail=null;listingStepFor=null;listingStep='style';listingVisited=new Set(['style']);listingOpenSections.clear();listingNeedsSeen.clear();clearTimeout(listingAutoTimer);listingSetArticulationPickMode(false);
  $('listingPanel').hidden=false;$('listingMain').hidden=true;const side=$('listingPanel').closest('.listing-side');if(side)side.hidden=false;
  $('listingPanel').innerHTML='<p class="field-help">正在读取…</p>';listingSetHTML($('listingPages'),'');$('listingPlan').innerHTML='';listingSetHTML($('listingNotes'),'');listingSyncStage();
  try{await listingLoad(true);}catch(error){$('listingPanel').innerHTML=`<p class="field-error">${esc(error.message)}</p>`;}
}
// Called when the user leaves the tab: words still being typed are saved, polling stops.
function listingDeactivate() {
  clearTimeout(listingTimer);clearTimeout(listingArticulationTimer);clearTimeout(listingAutoTimer);
  listingSetArticulationPickMode(false);
  document.body.classList.remove('listing-stage-on');const stage=listingStageNode();if(stage)stage.hidden=true;   // the model's view comes back
  if(listingOpenFor())listingSavePending().catch(error=>toast(error.message,true));
}
async function listingLoad(reset) {
  const id=listingOpenFor();if(!id)return;
  const state=await api(`/api/projects/${id}/listing`);
  if(id!==listingOpenFor())return;
  listingAdopt(state,reset);
}
// Take the server's state. Words the user is still typing are kept unless `reset` says to drop them; `keep` keeps the
// product as typed (the background save of 填产品: its rows may still hold an empty line the server drops).
// Rows the seller added but has not filled in yet (an empty 卖点 just added) are left out of the saved product, so the
// server's copy lacks them. Adopting that copy while the form is not redrawn keeps them where the form shows them: the
// saved rows take the places of the filled ones. When the filled rows no longer match, the empty rows follow the saved
// ones and the form is drawn again.
const listingBlank={features:row=>!String(row?.title || '').trim(),specs:row=>!(String(row?.label || '').trim() && String(row?.value || '').trim()),
  figures:row=>!(String(row?.value || '').trim() && String(row?.label || '').trim())};
function listingKeepBlankRows(local,server) {
  let aligned=true;
  for(const [key,blank] of Object.entries(listingBlank)){
    const mine=Array.isArray(local?.[key])?local[key]:[],theirs=Array.isArray(server[key])?server[key]:[];
    if(!mine.some(blank))continue;
    if(mine.filter(row=>!blank(row)).length!==theirs.length){aligned=false;server[key]=[...theirs,...mine.filter(blank)];continue;}
    let next=0;server[key]=mine.map(row=>blank(row)?row:theirs[next++]);
  }
  return aligned;
}
function listingAdopt(state,reset,keep=false) {
  const first=!listingEdit || reset;listingState=state;let redraw=false;
  if(!keep && (first || !listingDirty.product)){
    const product=listingClone(state.product);
    if(!first && listingEdit?.product)redraw=!listingKeepBlankRows(listingEdit.product,product);
    listingEdit={...(listingEdit || {}),product};
  }
  if(first || !listingDirty.brand)listingEdit={...listingEdit,brand:{preset:state.brand.preset,name:state.brand.name,accent:state.brand.accent,dark:state.brand.dark,
    badges:[...state.brand.badges],legal:state.brand.legal,unit:state.brand.unit,fonts:state.brand.fonts,badge_style:state.brand.badge_style,texture:state.brand.texture}};
  if(listingStepFor!==listingProject){
    // A project that already has a set opens on the summary next to it; a new one starts at the first step.
    listingStepFor=listingProject;
    const built=listingHasResults() || listingTaskActive();
    listingStep=built?'build':'style';listingVisited=new Set(built?listingSteps.map(step=>step.id):['style']);
  }
  if(listingTaskActive() && listingStep!=='build'){listingStep='build';listingVisited.add('build');listingRenderPanel();}   // a build runs: the steps wait
  else if(first)listingRenderPanel();
  else if(redraw && listingStep==='product')listingKeepFocus(listingRenderProduct);   // the form's rows no longer match: drawn again
  else listingRefreshStep();
  listingRenderMain();listingSchedule();
}
function listingSchedule() {
  clearTimeout(listingTimer);
  if(listingVisible() && (listingTaskActive() || listingComponentsBusy()))listingTimer=setTimeout(()=>listingLoad(false).catch(()=>listingSchedule()),1500);
}
// What a poll may change on the step shown without redrawing what the seller is typing into.
function listingRefreshStep() {
  if(listingStep==='pages')listingRenderKinds();
  else if(listingStep==='product'){listingRenderComponents();listingRenderNeeds();}
}
// The progress line, the step dots and the Back / Next buttons.
function listingSyncStepper() {
  const index=listingStepIndex(),step=listingSteps[index],locked=listingLocked(),moving=listingStepMoving;
  const count=$('listingStepCount');if(count)count.textContent=`${index+1} / ${listingSteps.length} · ${step.name}`;
  const title=$('listingStepTitle');if(title)title.textContent=`${index+1}. ${step.name}`;
  const lead=$('listingStepLead');if(lead)lead.textContent=step.lead;
  listingSetHTML($('listingSteps'),listingSteps.map((row,at)=>{
    const here=row.id===listingStep,seen=listingVisited.has(row.id);
    return `<li><button type="button" class="listing-step-dot${seen?' is-seen':''}" data-action="step" data-step="${row.id}" ${here?'aria-current="step"':''}
      aria-label="第 ${at+1} 步：${esc(row.name)}${here?'（当前）':seen?'':'（还没到）'}" title="${esc(row.name)}" ${!here && (!seen || locked)?'disabled':''}>${at+1}</button></li>`;}).join(''));
  const back=$('listingBack'),next=$('listingNext'),skip=$('listingSkip'),edit=$('listingEditSteps');
  const last=listingStep==='build',summary=last && listingHasResults();
  if(back){back.hidden=index===0 || summary;back.disabled=locked || moving;}
  if(edit){edit.hidden=!summary;edit.disabled=locked || moving;}
  if(skip){skip.hidden=listingStep!=='brand';skip.disabled=moving;}
  if(next){next.hidden=last;next.disabled=moving || !listingEdit;next.textContent=index===listingSteps.length-2?'下一步：核对并生成':'下一步';}
}
// Moving between steps saves what was typed first (the same save paths as before); a failed save keeps the step.
async function listingGoStep(id,focus=true) {
  if(!listingEdit || listingStepMoving || !listingSteps.some(step=>step.id===id))return;
  if(listingLocked() && id!=='build')return;
  // past 选图片 only with at least one page chosen (one that can be made, or one still waiting for what the seller fills
  // in on the next steps): the dots, 跳过 and 修改 go through here as well as 下一步
  const target=listingSteps.findIndex(step=>step.id===id),pages=listingSteps.findIndex(step=>step.id==='pages');
  if(pages>=0 && target>pages && !listingPicked().length){toast('请至少选一张图片',true);if(listingStep!=='pages')listingGoStep('pages');return;}
  const project=listingProject;
  clearTimeout(listingAutoTimer);
  listingStepMoving=true;listingSyncStepper();
  try{await listingSavePending();}
  catch(error){toast(error.message,true);return;}
  finally{listingStepMoving=false;listingSyncStepper();}
  if(listingOpenFor()!==project)return;
  if(id!==listingStep)listingNeedsSeen.clear();      // the checklist of 填产品 starts from what is missing when it is entered
  listingStep=id;listingVisited.add(id);listingRenderPanel();listingRenderMain();
  if(focus)$('listingStepTitle')?.focus();
}
function listingNextStep() {
  if(listingStep==='pages' && !listingPicked().length){toast('请至少选一张图片',true);return;}
  const next=listingSteps[listingStepIndex()+1];if(next)return listingGoStep(next.id);
}
function listingBackStep() { const back=listingSteps[listingStepIndex()-1];if(back)return listingGoStep(back.id); }

// ---- the step on screen ----
// `mark`: the fields (listingNeedWords) this one supplies; it is marked while a chosen page still waits for them
function listingField(label,html,help='',mark='') {
  return `<label class="listing-field"${mark?` data-need-mark="${esc(mark)}"`:''}><span class="field-label">${esc(label)}${mark?listingNeedTag():''}</span>${html}${help?`<span class="field-help">${esc(help)}</span>`:''}</label>`;
}
function listingNeedTag() { return '<span class="listing-need-tag" hidden>所选图片需要</span>'; }
function listingInput(scope,key,value,attrs='') { return `<input type="text" data-scope="${scope}" data-key="${esc(key)}" value="${esc(value ?? '')}" ${attrs}>`; }
// A collapsed section with a one-line summary of what it holds; it stays open across redraws once opened.
function listingSection(id,title,summary,body,open=false,mark='') {
  return `<details class="listing-section" data-section="${id}" ${mark?`data-need-mark="${esc(mark)}"`:''} ${listingOpenSections.has(id) || open?'open':''}><summary><span class="listing-section-name">${esc(title)}${mark?listingNeedTag():''}</span>
    <span class="listing-section-sum" data-sum="${id}">${esc(summary)}</span></summary><div class="listing-section-body">${body}</div></details>`;
}
function listingRenderPanel() {
  const panel=$('listingPanel'),main=$('listingMain');
  if(main)main.hidden=listingStep!=='build';
  panel.hidden=listingStep==='build';
  const side=panel.closest('.listing-side');if(side)side.hidden=listingStep==='build';   // the summary step has nothing on this side
  listingSyncStepper();listingSyncSceneHistory();
  if(!listingState || !listingEdit)return;
  if(listingStep!=='product')clearTimeout(listingArticulationTimer);
  if(listingStep==='style')listingRenderStyle();
  else if(listingStep==='pages')listingRenderPages();
  else if(listingStep==='product')listingRenderProduct(true);     // entering the step reads the moving parts again
  else if(listingStep==='brand')listingRenderBrand();
  else panel.innerHTML='';
}
// Redraws a step and puts the focus back on the control it was on (a redraw after a change must not drop it).
function listingKeepFocus(render) {
  const node=document.activeElement,panel=$('listingPanel');
  let selector=null;
  if(node && panel && panel.contains(node)){
    const data=node.dataset || {};
    if(node.id)selector='#'+node.id;
    else if(data.scope)selector=`[data-scope="${listingQuote(data.scope)}"]${data.key?`[data-key="${listingQuote(data.key)}"]`:''}${data.index!==undefined?`[data-index="${listingQuote(data.index)}"]`:''}${data.id?`[data-id="${listingQuote(data.id)}"]`:''}${['radio','checkbox'].includes(node.type) && node.hasAttribute('value')?`[value="${listingQuote(node.value)}"]`:''}`;
    else if(data.action)selector=`[data-action="${listingQuote(data.action)}"]`;
  }
  render();
  if(selector)panel.querySelector(selector)?.focus();
}
// A choice card: a large preview, the name and one line under it. The preview falls back to a neutral block with the
// name when its picture is missing (listingInit hides a picture that fails to load).
// `fill`: the page waits for something the seller can fill in, so it can still be picked; the reason says what.
function listingCardHTML({type,name='',scope,key='',value,checked,disabled=false,thumb,title,note,why='',fill=false}) {
  const id=`listingCard-${scope}-${value}`;
  return `<label class="listing-card${disabled?' is-disabled':''}${fill?' is-fill':''}" data-card="${esc(value)}">
    <input type="${type}" ${name?`name="${esc(name)}"`:''} data-scope="${esc(scope)}" ${key?`data-key="${esc(key)}"`:''} value="${esc(value)}" ${checked?'checked':''} ${disabled?'disabled':''}
      aria-labelledby="${esc(id)}-name" aria-describedby="${esc(id)}-note${why?` ${esc(id)}-why`:''}">
    <span class="listing-card-thumb"><span class="listing-card-ph" aria-hidden="true">${esc(title)}</span><img src="${esc(listingThumbURL(thumb))}" alt="" loading="lazy" data-thumb></span>
    <span class="listing-card-mark" aria-hidden="true"></span>
    <span class="listing-card-name" id="${esc(id)}-name">${esc(title)}</span>
    <span class="listing-card-note" id="${esc(id)}-note">${esc(note)}</span>
    ${why?`<span class="listing-card-why" id="${esc(id)}-why">${fill?'需补充：':'暂时无法生成：'}${esc(why)}</span>`:''}</label>`;
}
// ① 选风格: one of the brand kit's presets.
function listingRenderStyle() {
  const b=listingEdit.brand;
  $('listingPanel').innerHTML=`<fieldset class="listing-cards listing-cards-wide" data-arrows><legend class="sr-only">套图风格</legend>
    ${(listingState.presets || []).map(preset=>listingCardHTML({type:'radio',name:'listingPreset',scope:'brand',key:'preset',value:preset.id,checked:b.preset===preset.id,
      thumb:`style-${preset.id}`,title:preset.label.zh,note:preset.note.zh})).join('')}</fieldset>
    <p class="field-help">风格只决定排版方式；系统不内置任何品牌的标志或名称，请只使用你自己的品牌素材。</p>`;
}
// ② 选图片. The kinds offered are the ones the app can build (state.auto). A kind that waits for something the seller can
// fill in ("需补充") can be picked and stays on the list until it is there; a kind nothing in this app can supply ("hard")
// is shown disabled with the reason.
function listingKindList() {
  const s=listingState,base=s.auto || s.order || Object.keys(s.kinds || {});
  const explicit=Array.isArray(listingEdit?.product?.pages)?listingEdit.product.pages:[];
  return [...base,...explicit.filter(kind=>!base.includes(kind))];
}
function listingChosen() { const p=listingEdit.product;return new Set(Array.isArray(p.pages)?p.pages:(listingState.plan || []).map(row=>row.kind)); }
// state.blocked holds {why, fix:{step, field}, needs, hard}; a plain reason (a plan row, an older server) has no fix to offer
function listingReason(value) {
  if(value && typeof value==='object')return {why:String(value.why || ''),fix:value.fix || null,needs:Array.isArray(value.needs)?value.needs:value.fix?[value.fix]:[],hard:!!value.hard};
  return {why:String(value || ''),fix:null,needs:[],hard:true};     // as the server reads it (reason_public): nothing to fill in
}
// why a kind cannot be made now: for every kind (state.blocked), so a kind off the seller's own list says so before it is picked
function listingSkipped() {
  return new Map([...(listingState.plan || []).filter(row=>row.skipped).map(row=>[row.kind,listingReason(row.skipped)]),
    ...Object.entries(listingState.blocked || {}).map(([kind,value])=>[kind,listingReason(value)])]);
}
function listingMakeable() { const chosen=listingChosen(),skipped=listingSkipped();return listingKindList().filter(kind=>chosen.has(kind) && !skipped.has(kind)); }
// the pages chosen that wait for the seller ("需补充"), and every page chosen that is not ruled out
function listingFillable() { const chosen=listingChosen(),skipped=listingSkipped();return listingKindList().filter(kind=>chosen.has(kind) && skipped.has(kind) && !skipped.get(kind).hard); }
function listingPicked() { const skipped=listingSkipped();return listingKindList().filter(kind=>listingChosen().has(kind) && !skipped.get(kind)?.hard); }
function listingKindName(kind) { return listingState.kinds?.[kind]?.zh || kind; }
function listingKindsHTML() {
  const chosen=listingChosen(),skipped=listingSkipped(),kinds=listingState.kinds || {},active=listingTaskActive(),explicit=Array.isArray(listingEdit.product.pages);
  const cards=listingKindList().map(kind=>{const reason=skipped.get(kind),hard=!!reason?.hard,fill=!!reason && !hard;
    const card=listingCardHTML({type:'checkbox',scope:'page',value:kind,checked:chosen.has(kind) && !hard,disabled:active || hard,thumb:`page-${kind}`,
      title:kinds[kind]?.zh || kind,note:listingKindNotes[kind] || kinds[kind]?.en || '',why:reason?.why || '',fill});
    // 去填写 sits outside the card's <label>, so pressing it does not tick or untick the page
    return fill && reason.fix?`<div class="listing-card-wrap">${card}<button type="button" class="button subtle listing-card-fill" data-action="fill" data-kind="${esc(kind)}"
      aria-label="去填写「${esc(kinds[kind]?.zh || kind)}」需要的资料" ${active?'disabled':''}>去填写</button></div>`:card;}).join('');
  const make=listingMakeable().length,fill=listingFillable().length;
  return `<p class="listing-pick-count" role="status">已选 ${make+fill} 张${fill?`：${make} 张可以生成，${fill} 张需补充资料`:''}${explicit?'':'（自动挑选）'}</p>
    <div class="listing-cards" role="group" aria-label="要生成的图片" data-arrows>${cards}</div>
    <div class="listing-pick-actions">${explicit?`<button type="button" class="button subtle" data-action="pages-auto" ${active?'disabled':''}>恢复自动</button>
      <span class="field-help">恢复后按产品资料自动挑选：能生成的都生成，需补充的补齐后自动加入。</span>`
      :'<span class="field-help">现在是自动挑选：能生成的都会生成；标着「需补充」的不会生成，补齐资料后自动加入。改动任意一张后，改为按你选的清单生成。</span>'}</div>`;
}
function listingRenderPages() { $('listingPanel').innerHTML='<div id="listingKinds"></div>';listingRenderKinds(); }
function listingRenderKinds() {
  const node=$('listingKinds');if(!node || !listingState)return;
  const focused=document.activeElement && node.contains(document.activeElement)?document.activeElement:null;
  const value=focused?.value,action=focused?.dataset?.action,kind=focused?.dataset?.kind;
  listingSetHTML(node,listingKindsHTML());
  if(focused && !node.contains(focused))(value?node.querySelector(`input[value="${listingQuote(value)}"]`)
    :node.querySelector(`[data-action="${listingQuote(action)}"]${kind?`[data-kind="${listingQuote(kind)}"]`:''}`))?.focus();
}
function listingPartsHTML(index,chosen) {
  const parts=listingState.parts || [];
  if(parts.length<2)return '';            // one part is the whole product: picking it points at nothing (listingPartsNote says so)
  return `<details class="listing-parts"><summary>对应部件（${chosen.length}）</summary><div class="listing-parts-list">${parts.map(name=>
    `<label title="${esc(name)}"><input type="checkbox" data-scope="feature-part" data-index="${index}" value="${esc(name)}" ${chosen.includes(name)?'checked':''}><span>${esc(name)}</span></label>`).join('')}</div>
    <span class="field-help">选中部件后，功能标注会画出引线，并可生成该部件的特写。</span></details>`;
}
// ③ 填产品: the essentials first, the rest behind 更多信息; the components and the moving parts only when their page is chosen.
function listingRenderProduct(reload=false) {
  const p=listingEdit.product,size=listingState.size,drafted=p.drafted,chosen=listingChosen();
  const rows=(list,scope,cells)=>list.map((row,index)=>`<div class="listing-row" data-row="${scope}">${cells(row,index)}<button type="button" class="icon-button" data-action="remove-${scope}" data-index="${index}" aria-label="删除这一条" title="删除">×</button></div>`).join('');
  const source=`<div class="listing-source">
      <label class="field-label" for="listingSource">产品介绍</label>
      <textarea id="listingSource" data-scope="product" data-key="source" rows="5" maxlength="8000" placeholder="把商品详情、规格表或产品说明整段粘贴到这里，点「提取到各栏」，由文本模型分出名称、型号、尺寸、卖点、规格和指标。">${esc(p.source || '')}</textarea>
      <div class="listing-source-actions"><button type="button" class="button primary" data-action="extract">提取到各栏</button><button type="button" class="button accent" data-action="draft" title="没有介绍时，根据模型部件名称起草卖点">起草文案</button></div>
      <p class="field-help">${drafted?`上次${drafted.source==='extracted'?'从介绍中提取':drafted.source==='ai'?'由 AI 起草':'按部件名称起草'}。`:''}提取只采用介绍里写到的内容，含有介绍里没有的数字的条目会被略去；提取到的栏目会替换原内容，其余保留。${(drafted?.notes || []).map(esc).join(' ')}</p>
    </div>`;
  const filled=[p.headline,p.subline].filter(text=>String(text || '').trim()).length+p.specs.length+p.figures.length;
  const more=`${listingField('主标题',listingInput('product','headline',p.headline,'maxlength="60" placeholder="一句话说清最重要的卖点"'))}
    ${listingField('副标题',listingInput('product','subline',p.subline,'maxlength="120"'))}
    <h4 class="listing-heading">规格 <button type="button" class="button subtle" data-action="add-spec">添加</button></h4>
    ${rows(p.specs,'spec',(row,index)=>`<input type="text" data-scope="spec" data-index="${index}" data-key="label" value="${esc(row.label)}" maxlength="30" placeholder="名称，如 Voltage" aria-label="规格名称">
      <input type="text" data-scope="spec" data-index="${index}" data-key="value" value="${esc(row.value)}" maxlength="30" placeholder="数值，如 20 V" aria-label="规格数值">`)}
    <h4 class="listing-heading">大数字指标（最多 3 个） <button type="button" class="button subtle" data-action="add-figure" ${p.figures.length>=3?'disabled':''}>添加</button></h4>
    ${rows(p.figures,'figure',(row,index)=>`<input type="text" class="short" data-scope="figure" data-index="${index}" data-key="value" value="${esc(row.value)}" maxlength="12" placeholder="8,500" aria-label="指标数值">
      <input type="text" class="short" data-scope="figure" data-index="${index}" data-key="unit" value="${esc(row.unit || '')}" maxlength="8" placeholder="rpm" aria-label="指标单位">
      <input type="text" data-scope="figure" data-index="${index}" data-key="label" value="${esc(row.label)}" maxlength="30" placeholder="名称" aria-label="指标名称">`)}`;
  const box=chosen.has(LISTING_BOX),poses=chosen.has(LISTING_POSES),comps=listingState.components || [];
  const notReady=comps.filter(row=>row.status!=='ready').length;
  // the file input stays outside the box the poll redraws, so files chosen while it redraws still arrive
  const boxBody='<input id="listingComponentFiles" type="file" multiple accept=".glb,.obj,.stl,.zip,.png,.jpg,.jpeg,.webp" hidden aria-label="选择组件文件"><div id="listingComponents"></div>';
  $('listingPanel').innerHTML=`
    ${listingSection('source','粘贴产品介绍 / AI 起草文案',p.source?'已粘贴介绍':'粘贴介绍后提取，或直接起草',source,true)}
    <div class="listing-grid2">${listingField('产品名称',listingInput('product','title',p.title,'maxlength="60"'))}${listingField('型号',listingInput('product','model',p.model,'maxlength="40" placeholder="如 AG-20X"'))}</div>
    <div class="listing-grid2">${listingField('最长边真实尺寸（mm）',listingInput('product','size_mm',p.size_mm ?? '',`inputmode="decimal" placeholder="按模型估计约 ${esc(Math.round(size.mm))}"`),size.known?'':'填写后才会画出产品尺寸线，套装里的大小比例也才准确。','size specs')}
    ${listingField('图片文字语言',`<select data-scope="product" data-key="language"><option value="en" ${p.language==='en'?'selected':''}>English</option><option value="zh" ${p.language==='zh'?'selected':''}>中文</option></select>`)}</div>
    <h4 class="listing-heading" data-need-mark="features feature_parts"><span>卖点${listingNeedTag()}</span><span class="listing-heading-actions"><button type="button" class="button accent" data-action="draft" title="根据模型部件或上方介绍起草卖点">起草文案</button><button type="button" class="button subtle" data-action="add-feature">添加</button></span></h4>
    ${listingPartsNote()}
    ${rows(p.features,'feature',(row,index)=>`<div class="listing-feature"><input type="text" data-scope="feature" data-index="${index}" data-key="title" value="${esc(row.title)}" maxlength="40" placeholder="卖点标题" aria-label="卖点标题">
      <input type="text" data-scope="feature" data-index="${index}" data-key="detail" value="${esc(row.detail || '')}" maxlength="90" placeholder="一行说明（可不填）" aria-label="卖点说明">${listingPartsHTML(index,row.parts || [])}</div>`) || '<p class="field-help">还没有卖点。可在上方「粘贴产品介绍」框粘贴并点「提取到各栏」，或直接点右侧「起草文案」/「添加」。</p>'}
    ${listingSection('more','更多信息',filled?`已填 ${filled} 项`:'主标题、副标题、规格、大数字指标',more,false,'specs')}
    ${box?listingSection('components','套装组件',comps.length?`${comps.length} 个${notReady?`，${notReady} 个未就绪`:''}`:'没有组件',boxBody,listingSkipped().has(LISTING_BOX),'components'):''}
    ${poses?listingSection('articulation','活动部件',listingArticulationSummary(),'<div id="listingArticulation"></div>',listingSkipped().has(LISTING_POSES),'articulation'):''}`;
  $('listingPanel').insertAdjacentHTML('afterbegin','<section id="listingNeeds" class="listing-needs" aria-labelledby="listingNeedsTitle" hidden></section>');
  listingRenderNeeds();
  if(box)listingRenderComponents();
  if(poses)listingRenderArticulation(reload || !listingArticulation || listingArticulation.project!==listingOpenFor());
}
// ---- what the chosen pages still need: the checklist on top of 填产品, the marks on its fields, and 去填写 ----
// field (from state.blocked[kind].needs) -> what the seller fills in, in the words of the form
const listingNeedWords={features:'卖点',feature_parts:'卖点的「对应部件」',size:'最长边真实尺寸',specs:'参数：真实尺寸、规格或大数字指标（任填一项）',
  components:'套装组件',articulation:'活动部件',scenes:'场景图'};
const listingNeedsSeen=new Map();         // field -> {step, kinds}: every row shown since 填产品 was entered, so a filled one ticks off
// {field: {step, kinds:[...]}} for the chosen pages still waiting for the seller
function listingNeedsNow() {
  const now=new Map(),skipped=listingSkipped();
  for(const kind of listingFillable())for(const need of skipped.get(kind).needs){
    const row=now.get(need.field) || {step:need.step,kinds:[]};if(!row.kinds.includes(kind))row.kinds.push(kind);now.set(need.field,row);}
  return now;
}
function listingNeedsHTML() {
  const now=listingNeedsNow();
  for(const [field,row] of now)listingNeedsSeen.set(field,{step:row.step,kinds:[...new Set([...(listingNeedsSeen.get(field)?.kinds || []),...row.kinds])]});
  if(!listingNeedsSeen.size)return '';
  const left=[...listingNeedsSeen.keys()].filter(field=>now.has(field)).length;
  const items=[...listingNeedsSeen].map(([field,row])=>{
    const done=!now.has(field),names=(now.get(field)?.kinds || row.kinds).map(kind=>`「${listingKindName(kind)}」`).join('');
    const go=row.step==='brand'?'去第 4 步填写':'去填写';
    return `<li class="listing-need" data-need="${esc(field)}" data-done="${done}"><span class="listing-need-mark" aria-hidden="true"></span>
      <span class="listing-need-text"><strong>${esc(listingNeedWords[field] || field)}</strong><span>${esc(names)}${done?'':'需要'}</span></span>
      <span class="listing-need-state">${done?'已补齐':'待填写'}</span>
      ${done?'':`<button type="button" class="button subtle" data-action="need-go" data-step="${esc(row.step)}" data-field="${esc(field)}" aria-label="${esc(go)}：${esc(listingNeedWords[field] || field)}">${go}</button>`}</li>`;}).join('');
  return `<h4 class="listing-needs-title" id="listingNeedsTitle">你选的图片还需要<span>${left?`还差 ${left} 项`:'都已补齐'}</span></h4>
    <ul class="listing-needs-list">${items}</ul>
    <p class="field-help">填写后会自动保存，补齐的项目会打勾；没补齐的图片不会生成，其余照常生成。</p>`;
}
// Redrawn after each background save; only this box changes, so what is being typed and the focus stay where they are.
function listingRenderNeeds() {
  const box=$('listingNeeds');if(!box || !listingState || !listingEdit)return;
  const focused=document.activeElement && box.contains(document.activeElement)?document.activeElement.dataset.field:null;
  const html=listingNeedsHTML();box.hidden=!html;listingSetHTML(box,html);
  if(focused)(box.querySelector(`[data-field="${listingQuote(focused)}"]`) || $('listingNeedsTitle'))?.focus();
  const now=listingNeedsNow();
  for(const node of $('listingPanel').querySelectorAll('[data-need-mark]')){
    const on=node.dataset.needMark.split(' ').some(field=>now.has(field));
    node.classList.toggle('is-needed',on);
    const tag=node.querySelector('.listing-need-tag');if(tag)tag.hidden=!on;
  }
}
// What the 对应部件 picker means for this model: without two named parts there is nothing to pick.
function listingPartsNote() {
  if((listingState.parts || []).length>=2)return '';
  return `<p class="listing-parts-none field-help" id="listingPartsNone">这个模型没有分出可以单独选的部件，所以卖点下没有「对应部件」可选。因此「功能标注」只能由视觉模型（「AI 设置」→「预览图像分析」）在主图上找出卖点的位置；
    「主体 + 细节栏」和「深色细节特写」要靠部件特写，这个模型做不了。</p>`;
}
// 去填写: save, go to the step, open the section and put the cursor where the missing thing is typed.
let listingFocusPending=null;
async function listingGoFill(fix) {
  if(!fix || !listingEdit)return;
  await listingGoStep(fix.step,false);
  if(listingStep!==fix.step)return;            // the save failed or the move was refused: it said why
  listingFocusNeed(fix.field);
}
function listingFocusNeed(field) {
  const panel=$('listingPanel'),open=id=>{listingOpenSections.add(id);const node=panel.querySelector(`details[data-section="${id}"]`);if(node)node.open=true;};
  const first=(...selectors)=>{for(const selector of selectors){const node=[...panel.querySelectorAll(selector)].find(item=>!item.disabled);if(node)return node;}return null;};
  let node=null;
  if(field==='features')node=[...panel.querySelectorAll('[data-scope="feature"][data-key="title"]')].find(item=>!item.value.trim()) || first('[data-action="add-feature"]');
  else if(field==='feature_parts'){
    const picker=[...panel.querySelectorAll('details.listing-parts')].find(item=>!item.querySelector('input:checked')) || panel.querySelector('details.listing-parts');
    if(picker){picker.open=true;node=picker.querySelector('input');}
    else node=$('listingPartsNone') || first('[data-scope="feature"][data-key="title"]');
    if(node && node.id==='listingPartsNone')node.tabIndex=-1;
  }
  else if(field==='size')node=first('[data-scope="product"][data-key="size_mm"]');
  else if(field==='specs'){open('more');node=first('[data-scope="product"][data-key="size_mm"]');}
  else if(field==='components'){open('components');node=first('[data-action="component-add"]');}
  else if(field==='articulation'){open('articulation');node=first('[data-action="articulation-run"]');if(!node){listingFocusPending='articulation-run';node=panel.querySelector('details[data-section="articulation"] summary');}}
  else if(field==='scenes'){
    // without the image model the workpiece render is the scene this app can add
    const usecase=listingState.scene_options && listingState.scene_options.image_ready===false;
    open(usecase?'usecase':'scenes');node=first(usecase?'[data-scope="usecase"][data-key="enabled"]':'[data-scope="scenes"][data-key="enabled"]');
  }
  node?.scrollIntoView?.({block:'center'});node?.focus();
}
// On 填产品 the product is saved in the background shortly after each change, so the checklist can tick off.
let listingAutoTimer=null;
var listingAutoDelay=800;            // a var: the UI tests shorten it through window
let listingAutoSaving=false;
function listingAutoSave() {
  clearTimeout(listingAutoTimer);
  if(listingStep!=='product' || !listingDirty.product)return;
  listingAutoTimer=setTimeout(async()=>{
    if(listingAutoSaving)return listingAutoSave();     // one save at a time: answers never arrive out of order
    const id=listingOpenFor();
    if(!id || listingStep!=='product' || !listingDirty.product || listingStepMoving || listingBusy)return;
    const rev=listingProductRev;listingAutoSaving=true;
    try{
      const state=await api(`/api/projects/${id}/listing/product`,{method:'PUT',body:listingProductBody()});
      if(listingOpenFor()!==id)return;
      if(rev===listingProductRev)listingDirty.product=false;
      listingAdopt(state,false,true);
    }catch(error){return;/* kept unsaved: 下一步 saves it again and says what is wrong */}
    finally{listingAutoSaving=false;}
    listingAutoSave();                // typed while it was saving: that goes in next
  },listingAutoDelay);
}
// The earlier usage-scene pictures (app.js fills #sceneHistory) belong with the scene settings: shown on that step only.
function listingSyncSceneHistory() {
  const node=$('sceneHistory');if(!node)return;
  node.hidden=listingStep!=='brand' || !Number(node.dataset.count || 0);
}
// Opening an earlier scene picture shows it in the viewer, which the built set may be covering.
function listingShowModel() { if(listingStageShown()){listingStageOff=true;listingRenderMain();} }
// Asks before anything is removed or replaced: the app's decision dialog when it is there, the browser's otherwise.
async function listingConfirm(id,title,text,label,danger=true) {
  if(typeof chooseAction==='function')
    return await chooseAction(id,title,`<p>${esc(text)}</p>`,[{value:'cancel',label:'取消'},{value:'yes',label,danger,primary:!danger}])==='yes';
  return typeof window.confirm==='function'?!!window.confirm(text):true;
}
function listingComponentHTML(row) {
  const roles=listingState.roles,busy=['queued','processing'].includes(row.status);
  const status=row.status==='ready'?'':row.status==='failed'?`<p class="field-error">处理失败：${esc(row.error || '')}</p>`
    :`<p class="listing-component-status" role="status">${esc(listingStageNames[row.stage] || '处理中')} · ${Math.round(row.progress || 0)}%</p>`;
  const picture=row.cutout?.url?`<img src="${esc(row.cutout.url)}" alt="${esc(row.name)} 抠图" loading="lazy">`:`<span class="listing-thumb-empty">${busy?'处理中':'无图'}</span>`;
  const field=(key,value,attrs)=>`<input type="text" data-scope="component" data-id="${esc(row.id)}" data-key="${key}" value="${esc(value ?? '')}" ${attrs}>`;
  return `<article class="listing-component" data-component="${esc(row.id)}">
    <div class="listing-thumb">${picture}</div>
    <div class="listing-component-body">
      <div class="listing-grid2">${field('name',row.name,'maxlength="60" aria-label="组件名称" placeholder="名称"')}
        <select data-scope="component" data-id="${esc(row.id)}" data-key="role" aria-label="组件类型">${Object.entries(roles).map(([key,label])=>`<option value="${key}" ${row.role===key?'selected':''}>${esc(label.zh)}</option>`).join('')}</select></div>
      <div class="listing-grid3">${field('sku',row.sku,'maxlength="40" aria-label="型号" placeholder="型号"')}${field('quantity',row.quantity,'inputmode="numeric" aria-label="数量" placeholder="数量"')}
        ${field('size_mm',row.size_mm ?? '',`inputmode="decimal" aria-label="最长边尺寸（毫米）" placeholder="最长边 mm" title="${row.size_source==='user'?'你填写的尺寸':'按模型文件单位估计，建议填写真实尺寸'}"`)}</div>
      ${field('caption',row.caption,'maxlength="80" aria-label="一行说明" placeholder="一行说明，如 20V 5.0Ah Li-ion pack"')}
      ${status}${(row.notes || []).map(note=>`<p class="field-help">${esc(note)}</p>`).join('')}
      <div class="listing-component-actions">
        <label class="listing-check"><input type="checkbox" data-scope="component" data-id="${esc(row.id)}" data-key="included" ${row.included!==false?'checked':''}> 放进套图</label>
        ${row.kind==='model'?`<button type="button" class="button subtle" data-action="component-turn" data-id="${esc(row.id)}" data-turn="flip" ${busy?'disabled':''}>上下翻转</button>
        <button type="button" class="button subtle" data-action="component-turn" data-id="${esc(row.id)}" data-turn="left" ${busy?'disabled':''}>左转 90°</button>
        <button type="button" class="button subtle" data-action="component-turn" data-id="${esc(row.id)}" data-turn="forward" ${busy?'disabled':''}>前翻 90°</button>`:''}
        <button type="button" class="button subtle" data-action="component-refresh" data-id="${esc(row.id)}" ${busy?'disabled':''}>重新处理</button>
        <button type="button" class="button subtle danger" data-action="component-remove" data-id="${esc(row.id)}">删除</button>
      </div>
    </div></article>`;
}
// The components fill their own box inside the 填产品 step (shown while the in-the-box page is chosen). New ones are
// uploaded here as in the new-project dialog (POST /components, one file per component; at most 24 per project).
const listingComponentLimit=24;
let listingUploading=false;
async function listingUploadComponents(files) {
  const id=listingOpenFor();if(!id || !files.length || listingUploading)return;
  const allowed=/\.(glb|obj|zip|png|jpe?g|webp)$/i,wrong=files.find(file=>!allowed.test(file.name));
  if(wrong){toast(`「${wrong.name}」不是可用的组件文件：请上传 GLB、OBJ、ZIP 或 PNG / JPG / WEBP 图片`,true);return;}
  if((listingState?.components || []).length+files.length>listingComponentLimit){toast(`一个项目最多 ${listingComponentLimit} 个组件`,true);return;}
  listingUploading=true;listingRenderComponents();
  let added=0;
  try{
    for(const file of files){
      const form=new FormData();form.append('files',file);
      await api(`/api/projects/${id}/components`,{method:'POST',body:form});added++;
    }
  }catch(error){toast(error.message,true);}
  finally{
    listingUploading=false;
    if(listingOpenFor()===id){
      if(added)toast(`已添加 ${added} 个组件，正在处理`);
      await listingLoad(false).catch(()=>{});            // the processing is followed by the poll (listingSchedule)
      listingRenderComponents();$('listingPanel')?.querySelector('[data-action="component-add"]')?.focus();
    }
  }
}
function listingRenderComponents() {
  const box=$('listingComponents');if(!box || !listingState)return;
  const rows=listingState.components || [],focused=document.activeElement && box.contains(document.activeElement)?document.activeElement:null;
  // While the user types in a component's field, only statuses change under them; the list is rebuilt when they are not typing.
  if(focused && focused.dataset.scope==='component' && box.querySelector('.listing-components')){
    for(const row of rows){const node=box.querySelector(`[data-component="${row.id}"] .listing-component-status`);if(node)node.textContent=`${listingStageNames[row.stage] || '处理中'} · ${Math.round(row.progress || 0)}%`;}
    return;
  }
  const full=rows.length>=listingComponentLimit,uploading=listingUploading;
  box.innerHTML=`<p class="field-help">随箱附带的电池、充电器、工具箱等组件：每个组件上传一个模型（GLB、OBJ，带贴图的请打包成 ZIP）或一张白底 / 透明背景的图片，一个文件算一个组件。模型组件会自动分析、摆正，在主图里与产品一起渲染；这里可以修改名称、型号、数量和朝向。</p>
    <div class="listing-component-add"><button type="button" class="button secondary" data-action="component-add" ${full || uploading?'disabled':''} ${uploading?'aria-busy="true"':''}>${uploading?'正在上传…':'添加组件'}</button>
      <span class="field-help">${full?`已有 ${rows.length} 个组件，达到上限`:`最多 ${listingComponentLimit} 个；上传后自动处理，处理完成就能放进包装清单。`}</span></div>
    <div class="listing-components">${rows.map(listingComponentHTML).join('') || '<p class="field-help">这个项目还没有组件。点「添加组件」上传。</p>'}</div>`;
  const notReady=rows.filter(row=>row.status!=='ready').length,sum=$('listingPanel').querySelector('[data-sum="components"]');
  if(sum)sum.textContent=rows.length?`${rows.length} 个${notReady?`，${notReady} 个未就绪`:''}`:'没有组件';
}
// Where the saved brand marks will appear in the planned set, and which of them no page will show (state.brand_usage).
function listingUsageLine(what) {
  const usage=(listingState && listingState.brand_usage) || null,kinds=(listingState && listingState.kinds) || {};
  if(!usage)return '';
  const names=list=>list.map(kind=>(kinds[kind] || {}).zh || kind).join('、');
  if(what==='badges'){
    if(!(usage.badges || []).length)return '';
    return ' 已保存：'+usage.badges.map(row=>`「${row.text}」→ ${row.pages.length?names(row.pages):'没有页面会显示'}`).join('；')+'。';
  }
  const saved=what==='logo'?!!(listingState.brand || {}).logo:!!((listingState.brand || {}).legal || '').trim();
  if(!saved)return what==='logo'?'上传后印在海报、卖点标注图和场景页的空白角落。':'';
  const pages=usage[what] || [];
  return pages.length?` 将出现在：${names(pages)}（页面上没有空位时会省略，并在生成后提示）。`:' 当前的图片清单里没有会显示它的页面。';
}
// The one-line summaries of the 品牌与场景 sections, kept in step with what is typed.
function listingBrandSums() {
  const b=listingEdit.brand,used=listingState.brand,p=listingEdit.product,u=p.usecase,m=listingState.mounting;
  const sc=p.scenes || {enabled:true,count:3},pairs=listingState.font_pairings || [];
  const font=pairs.find(pair=>pair.id===listingFontsUsed(b,pairs));
  return {identity:[b.name || '未填品牌名称',used.logo?'有标志':'无标志'].join(' · '),
    colors:[b.accent?`品牌色 ${b.accent}`:'品牌色跟随产品',font?font.label.zh:''].filter(Boolean).join(' · '),
    marks:[(b.badges || []).length?`徽标 ${(b.badges || []).join('、')}`:'无徽标',b.legal?'有页脚说明':'',b.unit==='in'?'英寸':'毫米'].filter(Boolean).join(' · '),
    share:'复制其他项目的品牌，或设为新项目的默认品牌',
    mounting:m?(m.setting?`手动：${listingMountWords[m.setting]}`:`自动（${listingMountWords[m.detected] || '落地·台面'}）`):'',
    scenes:sc.enabled?`${sc.count} 张 · ${p.aspect==='4:5'?'4:5 竖图':'1:1 方图'}`:`关闭 · ${p.aspect==='4:5'?'4:5 竖图':'1:1 方图'}`,
    usecase:u.enabled?`开启 · ${(listingState.options.workpiece_names || {})[u.kind] || u.kind}`:'关闭'};
}
function listingSyncSums() {
  if(listingStep!=='brand' || !listingState)return;
  for(const [key,text] of Object.entries(listingBrandSums())){const node=$('listingPanel').querySelector(`[data-sum="${key}"]`);if(node)node.textContent=text;}
}
// ④ 品牌与场景: optional; every part is a collapsed section with its summary, and 跳过 goes straight to the summary.
function listingRenderBrand() {
  const b=listingEdit.brand,used=listingState.brand,sums=listingBrandSums();
  const identity=`${listingField('品牌名称',listingInput('brand','name',b.name,'maxlength="40" placeholder="显示在页眉，可不填"'))}
    <div class="listing-logo"><span class="field-label">标志图片</span>${used.logo?`<img src="${esc(used.logo)}" alt="品牌标志"><button type="button" class="button subtle" data-action="logo-remove">移除</button>`:'<span class="field-help">未上传；页眉显示品牌名称文字。</span>'}<span class="field-help">${esc(listingUsageLine('logo'))}</span>
      <input id="listingLogoFile" type="file" accept=".png,.jpg,.jpeg,.webp" aria-label="上传标志图片"></div>`;
  const colors=`<div class="listing-grid2">
      <label class="listing-field"><span class="field-label">品牌色</span><span class="listing-color"><input type="color" data-scope="brand" data-key="accent" value="${esc(b.accent || used.accent_used)}" ${b.accent?'':'disabled'} aria-label="品牌色">
        <label class="listing-check"><input type="checkbox" data-scope="brand-auto" data-key="accent" ${b.accent?'':'checked'}> 跟随产品颜色</label></span>
        <span class="field-help">${b.accent?'':'当前 '+esc(used.accent_used)+(used.accent_source==='product'?'（取自产品）':'（默认）')}</span></label>
      <label class="listing-field"><span class="field-label">深色</span><span class="listing-color"><input type="color" data-scope="brand" data-key="dark" value="${esc(b.dark || used.dark_used)}" ${b.dark?'':'disabled'} aria-label="深色">
        <label class="listing-check"><input type="checkbox" data-scope="brand-auto" data-key="dark" ${b.dark?'':'checked'}> 默认</label></span></label>
    </div>${listingFontPicker(b)}`;
  const marks=`${listingField('徽标文字（最多 3 个，用逗号分隔）',listingInput('brand','badges',(b.badges || []).join(', '),'placeholder="如 20V, PRO"'),'第 1、3 个印在海报上，第 2 个印在卖点标注图上（只填 1 个时两页都用它）。'+listingUsageLine('badges'))}
    <div class="listing-grid2">
      ${listingField('徽标样式',`<select data-scope="brand" data-key="badge_style">${((listingState.elements||{}).badge_styles||[]).map(row=>`<option value="${esc(row.id)}" ${(b.badge_style||'auto')===row.id?'selected':''}>${esc(row.label.zh)}</option>`).join('')}</select>`,'徽标印在主图和海报的空白角落。')}
      ${listingField('背景底纹',`<select data-scope="brand" data-key="texture">${((listingState.elements||{}).textures||[]).map(row=>`<option value="${esc(row.id)}" ${(b.texture||'none')===row.id?'selected':''}>${esc(row.label.zh)}</option>`).join('')}</select>`)}
    </div>
    ${listingField('页脚说明文字',listingInput('brand','legal',b.legal,'maxlength="200" placeholder="如 *Maximum initial battery voltage…"'),'一行小字，印在写明规格和卖点的页面底边。'+listingUsageLine('legal'))}
    ${listingField('尺寸单位',`<select data-scope="brand" data-key="unit"><option value="mm" ${b.unit==='mm'?'selected':''}>毫米 mm</option><option value="in" ${b.unit==='in'?'selected':''}>英寸 in</option></select>`)}`;
  $('listingPanel').innerHTML=`
    <h4 class="listing-group-title">品牌</h4>
    ${listingSection('identity','名称与标志',sums.identity,identity)}
    ${listingSection('colors','颜色与字体',sums.colors,colors)}
    ${listingSection('marks','徽标与说明文字',sums.marks,marks)}
    ${listingSection('share','多个项目共用',sums.share,`<div id="listingShare">${listingShareHTML()}</div>`)}
    <h4 class="listing-group-title">场景</h4>
    ${listingState.mounting?listingSection('mounting','安装方式',sums.mounting,listingMountingHTML()):''}
    ${listingSection('scenes','AI 场景图',sums.scenes,listingScenesHTML())}
    ${listingSection('usecase','工件使用图',sums.usecase,listingUsecaseHTML())}`;
  if(!listingShare || listingShare.project!==listingOpenFor())listingLoadShare();
}
// ---- brands shared between projects: copy another project's brand, or set this one as the default for new projects ----
let listingShare=null;          // {project, data:{projects, default, last}} from /listing/brands, read when the brand step opens
function listingShareHTML() {
  const id=listingOpenFor(),share=listingShare && listingShare.project===id?listingShare.data:null;
  if(!share)return '<p class="field-help">正在读取其他项目的品牌…</p>';
  const brandName=row=>row.name?`「${row.name}」`:'未命名品牌';
  const own=share.default && share.default.project_id===id;
  const next=share.default?`新项目默认使用${brandName(share.default)}（来自「${share.default.project_name}」，已设为默认）。`
    :share.last?`新项目沿用最近保存的品牌${brandName(share.last)}（来自「${share.last.project_name}」）；设为默认后就固定使用它。`
    :'新项目还没有可沿用的品牌；保存品牌后，新项目会自动沿用。';
  const projects=Array.isArray(share.projects)?share.projects:[];
  const options=projects.map(row=>`<option value="${esc(row.id)}">${esc(row.project_name)} · ${esc(row.name || '未命名品牌')}${row.logo?'（含标志）':''}</option>`).join('');
  return `<p class="field-help" id="listingShareNext">${esc(next)}</p>
    ${projects.length || (share.default && !own)?`<div class="listing-share-row"><label class="field-label" for="listingBrandSource">从其他项目复制品牌</label>
      <select id="listingBrandSource"><option value="">选择项目…</option>${share.default && !own?'<option value="default">默认品牌</option>':''}${options}</select>
      <button type="button" class="button subtle" data-action="brand-copy">复制到这个项目</button></div>
      <p class="field-help">复制会替换这个项目当前的品牌设置，标志图片一并复制过来。</p>`:'<p class="field-help">其他项目还没有保存过品牌。</p>'}
    <div class="listing-share-row"><button type="button" class="button subtle" data-action="brand-default" aria-describedby="listingShareNext">${own?'用当前设置更新默认品牌':'设为默认品牌'}</button>
      ${own?'<button type="button" class="button subtle" data-action="brand-default-clear">取消默认</button>':''}</div>`;
}
function listingRenderShare() { const node=$('listingShare');if(node)node.innerHTML=listingShareHTML(); }
async function listingLoadShare() {
  const id=listingOpenFor();if(!id)return;
  try{const data=await api(`/api/projects/${id}/listing/brands`);if(id!==listingOpenFor())return;listingShare={project:id,data};}
  catch(error){if(id===listingOpenFor() && $('listingShare'))$('listingShare').innerHTML=`<p class="field-error">${esc(error.message)}</p>`;return;}
  listingRenderShare();
}
// The pairing the pages are set in: the seller's own, else the chosen style's (brand_kit.fonts_of; the server's word
// for Chinese pages, which keep their default face).
function listingFontsUsed(b,pairs) { return b.fonts || (listingState.presets || []).find(row=>row.id===b.preset)?.fonts || listingState.brand?.fonts_used || pairs[0]?.id; }
// The font library's pairings, each previewed in its own faces (served by /brandkit/fonts.css).
function listingFontPicker(b) {
  const pairs=listingState.font_pairings||[];
  if(!pairs.length)return '';
  if(!document.getElementById('brandkitFontsCss')){const link=document.createElement('link');link.id='brandkitFontsCss';link.rel='stylesheet';link.href='/brandkit/fonts.css';document.head.appendChild(link);}
  const current=listingFontsUsed(b,pairs);
  return `<fieldset class="listing-presets listing-fonts"><legend class="field-label">字体搭配</legend>${pairs.map(pair=>`<label class="listing-preset listing-font">
      <input type="radio" name="listingFonts" data-scope="brand" data-key="fonts" value="${esc(pair.id)}" ${current===pair.id?'checked':''}>
      <span class="listing-font-display" style="font-family:'${esc(pair.display)}',sans-serif">POWER 12V</span>
      <span class="listing-font-cjk" style="font-family:'${esc(pair.cjk)}',sans-serif">强劲动力</span>
      <strong>${esc(pair.label.zh)}</strong><span>${esc(pair.note.zh)}</span>
      <span class="listing-font-names">${esc(pair.display)} · ${esc(pair.text)} · ${esc(pair.cjk)}</span></label>`).join('')}</fieldset>`;
}
// How the product is installed (mounting.py): what was read from its name and parts, and the seller's override.
const listingMountWords={wall:'壁挂',free:'落地·台面'};
function listingMountingHTML() {
  const m=listingState.mounting;if(!m)return '';
  const choice=(value,label)=>`<label class="listing-check"><input type="radio" name="listingMounting" data-scope="mounting" value="${value}" ${m.setting===value?'checked':''} ${listingBusy?'disabled':''}> ${esc(label)}</label>`;
  const why=m.setting?`已手动设为${listingMountWords[m.setting]}。自动识别的结果：${m.reason}`:m.reason;
  return `<fieldset class="listing-mounting" aria-describedby="listingMountingReason listingMountingNote"><legend class="field-label">安装方式</legend>
    <p class="listing-mounting-reason" id="listingMountingReason">${esc(why)}</p>
    <div class="listing-mounting-choices">${choice('',`自动（${listingMountWords[m.detected] || '落地·台面'}）`)}${choice('wall','壁挂')}${choice('free','落地·台面')}</div>
    <p class="field-help" id="listingMountingNote">壁挂产品在场景图里挂在墙上、正面朝镜头。安装方式是场景图的生成依据之一：改动后，下次生成套图会重新生成场景图（会产生图像模型费用）。</p></fieldset>`;
}
async function listingSaveMounting(value) {
  const id=listingOpenFor();if(!id)return;
  if(listingBusy){
    // not saved while another action runs: the choice shown goes back to the saved one, not left looking saved
    toast('正在处理上一个操作，请稍后再改安装方式',true);if(listingStep==='brand')listingRenderBrand();return;
  }
  listingBusy=true;
  try{const state=await api(`/api/projects/${id}/listing/mounting`,{method:'PUT',body:{mounting:value}});
    if(id!==listingOpenFor())return;
    listingAdopt(state,false);toast(value?`已设为${listingMountWords[value]}；下次生成会重新生成场景图`:'已改回自动识别');}
  catch(error){toast(error.message,true);}
  finally{listingBusy=false;if(listingStep==='brand' && listingOpenFor()===id){listingRenderBrand();$('listingPanel').querySelector(`[data-scope="mounting"][value="${listingState?.mounting?.setting || ''}"]`)?.focus();}listingRenderMain();}
}
function listingScenesHTML() {
  const sc=listingEdit.product.scenes || {enabled:true,count:3,audience:'auto',people:'auto',hint:''},so=listingState.scene_options || {audiences:[],people:{},max:3};
  return `<p class="field-help">生成套图时，按目标人群策划使用场景，由图像模型围绕锁定的产品渲染生成（产品外形不会被改动），再排进海报主图、场景参数图和使用场景组图。${so.image_ready?'':'<strong>图像模型未配置</strong>：它只能在服务器的 .env 文件里配置（AI_IMAGE_BASE_URL、AI_IMAGE_MODEL），「AI 设置」里不能设置。没有它时海报用纯色版；需要场景的页面可以用下面的「工件使用图」（算 1 张场景图），其余跳过。'}</p>
    <label class="listing-check listing-toggle"><input type="checkbox" data-scope="scenes" data-key="enabled" ${sc.enabled?'checked':''}> 生成 AI 场景图</label>
    <div class="listing-grid2">${listingField('场景图数量',`<select data-scope="scenes" data-key="count">${Array.from({length:so.max || 3},(_,i)=>i+1).map(n=>`<option value="${n}" ${sc.count===n?'selected':''}>${n} 张</option>`).join('')}</select>`,'第 1 张是使用中的画面，第 2 张是产品放在使用环境里，第 3 张换一类买家的场景。')}
      ${listingField('目标人群',`<select data-scope="scenes" data-key="audience"><option value="auto">自动推荐</option>${(so.audiences || []).map(row=>`<option value="${esc(row.id)}" ${sc.audience===row.id?'selected':''}>${esc(row.label)}</option>`).join('')}</select>`)}</div>
    ${listingField('画面人物',`<select data-scope="scenes" data-key="people">${Object.entries(so.people || {}).map(([key,label])=>`<option value="${esc(key)}" ${sc.people===key?'selected':''}>${esc(label)}</option>`).join('')}</select>`)}
    ${listingField('场景说明（可选）',listingInput('scenes','hint',sc.hint,'maxlength="300" placeholder="如：车间里的工具柜、户外露营"'),'设置不变时，重新生成套图会沿用已生成的场景图。')}
    ${listingField('图片比例',`<select data-scope="product" data-key="aspect">${(so.aspects || ['1:1','4:5']).map(value=>`<option value="${esc(value)}" ${listingEdit.product.aspect===value?'selected':''}>${value==='1:1'?'1:1 方图 1500×1500':'4:5 竖图 1200×1500'}</option>`).join('')}</select>`)}`;
}
function listingUsecaseHTML() {
  const u=listingEdit.product.usecase,o=listingState.options,round=['pipe','rod'].includes(u.kind);
  const select=(key,values,names)=>`<select data-scope="usecase" data-key="${key}">${values.map(value=>`<option value="${esc(value)}" ${u[key]===value?'selected':''}>${esc(names[value] || value)}</option>`).join('')}</select>`;
  const number=(key,placeholder)=>`<input type="text" inputmode="decimal" data-scope="usecase" data-key="${key}" value="${esc(u[key] ?? '')}" placeholder="${esc(placeholder)}">`;
  const triple=(key,labels)=>`<span class="listing-grid3">${labels.map((label,index)=>`<input type="text" inputmode="decimal" data-scope="usecase-triple" data-key="${key}" data-index="${index}" value="${esc(u[key][index])}" aria-label="${esc(label)}" title="${esc(label)}">`).join('')}</span>`;
  return `<p class="field-help">把产品和它加工的工件放在一张图里，例如割管器套在一根白色塑料管上。工件由程序按尺寸生成，位置由模型上量到的开口决定，也可以手动调整。这张图会作为一张场景图，排进使用场景组图。</p>
    <label class="listing-check listing-toggle"><input type="checkbox" data-scope="usecase" data-key="enabled" ${u.enabled?'checked':''}> 渲染工件使用图</label>
    <div class="listing-grid2">${listingField('工件',select('kind',o.workpieces,o.workpiece_names))}${listingField('材质',select('material',o.materials,o.material_names))}</div>
    ${listingField('位置',select('mode',o.modes,listingModeNames),u.mode==='through'?'在模型上寻找被工具三面包住的开口，工件从中穿过；找不到时放在工具中心。':u.mode==='front'?'工件立在工具长轴的一端；方向不对时改选另一端。':'')}
    ${u.mode==='front'?listingField('哪一端',select('side',['+','-'],{'+':'正向一端','-':'反向一端'})):''}
    <div class="listing-grid3">${round?listingField('直径 mm',number('diameter_mm','自动')):listingField('厚度 mm',number('thickness_mm','自动'))}
      ${round?'':listingField('宽度 mm',number('width_mm','自动'))}${listingField('长度 mm',number('length_mm','自动'))}</div>
    ${listingField('位置微调 X / Y / Z（mm）',triple('offset_mm',['X 偏移','Y 偏移','Z 偏移']))}
    ${listingField('工件旋转 X / Y / Z（度）',triple('rotate',['绕 X 旋转','绕 Y 旋转','绕 Z 旋转']))}
    ${listingField('这张图的标题',listingInput('usecase','headline',u.headline,'maxlength="60" placeholder="留空则写「In use · 工件名称」"'))}
    <p class="field-help">图上的工件尺寸是你在这里选的尺寸；产品尺寸线只在填写了真实尺寸时画出。</p>`;
}

// ---- moving parts: what the pose agent (articulation.py) found, user click-to-pick acceleration, and asking it again ----
let listingArticulation=null,listingArticulationTimer=null,listingArticulationPickMode=false,listingArticulationPoints=[];
const listingArticulationPicks=new Map();
const listingArticulationStatus={ready:'有活动部件',none:'没有活动部件',failed:'识别失败',unknown:'尚未识别'};
const listingRoleNames={rest:'静止',open:'打开',working:'使用中'};
function listingArticulationPickSet(id) {
  if(!id)return new Set();
  if(!listingArticulationPicks.has(id))listingArticulationPicks.set(id,new Set());
  return listingArticulationPicks.get(id);
}
function listingArticulationPicking() {
  return !!listingArticulationPickMode && listingVisible() && listingStep==='product';
}
function listingRenderArticulationMarkers() {
  const markers=$('feedbackMarkers'),viewer=$('viewer');
  if(!markers || !viewer)return;
  if(!listingArticulationPicking()){markers.innerHTML='';return;}
  const rect=typeof imageContentRect==='function'?imageContentRect():null,vrect=viewer.getBoundingClientRect();
  markers.innerHTML=rect?listingArticulationPoints.map((pt,idx)=>`<span class="feedback-marker ${pt.pending?'pending':pt.hit?'':'miss'}" style="left:${rect.left-vrect.left+rect.width*pt.x}px;top:${rect.top-vrect.top+rect.height*pt.y}px" title="${esc(pt.name || (pt.pending?'定位中…':'未命中'))}">${idx+1}</span>`).join(''):'';
}
function listingSyncRealtimePick() {
  if(typeof syncRealtimeArticulationPick!=='function')return;
  const id=listingOpenFor(),on=listingArticulationPicking();
  syncRealtimeArticulationPick(on,on?listingArticulationPickSet(id):new Set());
}
function listingArticulationTogglePart(name) {
  if(!listingArticulationPicking() || !name)return;
  const id=listingOpenFor();if(!id)return;
  const set=listingArticulationPickSet(id);
  if(set.has(name)){set.delete(name);toast(`已取消选中：${name}`);}
  else{set.add(name);toast(`已选中活动部件：${name}`);}
  listingSyncRealtimePick();
  listingRenderArticulation(false);
}
function listingSetArticulationPickMode(on) {
  const next=!!on && !!listingOpenFor();
  listingArticulationPickMode=next;
  if(!next)listingArticulationPoints=[];
  else{
    listingStageOff=true;listingSyncStage();
    // Prefer interactive 3D preview picking whenever 3D viewport is available; fall back to 2D image picking otherwise.
    if(typeof realtimeAvailable==='function' && realtimeAvailable()){
      if(typeof realtimeActive==='function' && !realtimeActive() && typeof setViewerMode==='function')setViewerMode('live');
    }
  }
  const viewer=$('viewer');
  if(viewer)viewer.classList.toggle('feedback-selecting',next || (typeof feedbackMode!=='undefined' && !!feedbackMode));
  listingSyncRealtimePick();
  if(typeof renderFeedbackMarkers==='function')renderFeedbackMarkers();
  else listingRenderArticulationMarkers();
}
function listingArticulationPickClick(event) {
  if(!listingArticulationPicking())return false;
  const id=listingOpenFor();
  if(!id || !currentOutput || typeof pointFromClient!=='function' || typeof feedbackSnapshot!=='function'){
    toast('当前没有可点选的渲染图，请先生成预览或在右侧部件列表中勾选。',true);return true;
  }
  const point=pointFromClient(event.clientX,event.clientY);
  if(!point)return true;
  const snap=feedbackSnapshot(),pid='art-'+Date.now()+'-'+Math.random().toString(16).slice(2,6);
  const entry={id:pid,x:point.x,y:point.y,pending:true,hit:false,name:''};
  listingArticulationPoints.push(entry);
  listingRenderArticulationMarkers();
  api(`/api/projects/${encodeURIComponent(id)}/feedback/pick`,{method:'POST',body:{...snap,points:[{id:pid,x:point.x,y:point.y}]}})
    .then(response=>{
      const hit=response?.hits?.find(item=>item.id===pid);
      entry.pending=false;
      if(hit?.hit && hit.object_name){
        entry.hit=true;entry.name=hit.object_name;
        const set=listingArticulationPickSet(id);
        set.add(hit.object_name);
        toast(`已点选活动部件：${hit.object_name}`);
        listingRenderArticulation(false);
      }else{
        entry.hit=false;
        toast(hit?.message || '未点中模型部件，请点击会活动的门/抽屉/盖子。',true);
      }
      listingRenderArticulationMarkers();
    })
    .catch(error=>{
      entry.pending=false;entry.hit=false;
      toast('部件定位失败：'+error.message,true);
      listingRenderArticulationMarkers();
    });
  return true;
}
function listingArticulationSummary() {
  const a=listingArticulation && listingArticulation.project===listingOpenFor()?listingArticulation.data:null;
  if(!a)return '正在读取…';
  if(a.running)return '识别中';
  return (listingArticulationStatus[a.status] || a.status || '尚未识别')+((a.states || []).length?` · ${a.states.length} 个姿态`:'');
}
// Fills the 活动部件 box of the 填产品 step (shown while the poses page is chosen).
async function listingRenderArticulation(reload=true) {
  const id=listingOpenFor();if(!id)return;
  clearTimeout(listingArticulationTimer);
  if(reload || !listingArticulation || listingArticulation.project!==id){
    if((!listingArticulation || listingArticulation.project!==id) && $('listingArticulation'))$('listingArticulation').innerHTML='<p class="field-help">正在读取…</p>';
    let data;
    try{data=await api(`/api/projects/${id}/articulation`);}
    catch(error){if(listingOpenFor()===id && $('listingArticulation'))$('listingArticulation').innerHTML=`<p class="field-error">${esc(error.message)}</p>`;return;}
    const was=listingArticulation && listingArticulation.project===id && listingArticulation.data.running;
    listingArticulation={project:id,data};
    if(!listingArticulationPicks.has(id) && Array.isArray(data.selected_parts) && data.selected_parts.length){
      listingArticulationPicks.set(id,new Set(data.selected_parts));
    }
    if(was && !data.running)listingLoad(false).catch(()=>{});      // the planned pages may change with the result
  }
  const box=$('listingArticulation');
  if(!box || listingOpenFor()!==id)return;
  // state.vision_ready false: the pose agent has no model to ask, so the button would only record that (undefined: not known)
  const a=listingArticulation.data,running=!!a.running,noVision=listingState?.vision_ready===false && !running;
  const status=running?'识别中':(listingArticulationStatus[a.status] || a.status || '尚未识别');
  const picked=listingArticulationPickSet(id),pickedList=[...picked];
  const choices=Array.isArray(a.part_choices)?a.part_choices:[];
  const canPick3D=typeof realtimeAvailable==='function' && realtimeAvailable();
  const canPickImage=typeof currentOutput!=='undefined' && !!currentOutput && (typeof system==='undefined' || system?.runtime?.capabilities?.pick!==false);
  const canPick=canPick3D || canPickImage;
  const pickBtnLabel=listingArticulationPickMode
    ?(canPick3D?'正在 3D 预览中点选（点击结束）':'正在图上点选（点击结束）')
    :(canPick3D?'在 3D 预览里点选活动部件':'在图上点选活动部件');
  const pickBtnTitle=canPick3D?'开启后直接在左侧 3D 模型上点击会活动的部件（拖动仍可旋转视角）':canPickImage?'开启后在左侧产品图上点击会活动的部件':'请等待 3D 模型加载完成或先生成预览图';
  const pickHelp=canPick3D?'请直接在左侧 3D 预览里点击会活动的部件（可拖动旋转查看背面/侧面，选中的部件会高亮为琥珀色，再次点击可取消），选好后点击下方识别按钮。':'请在左侧产品渲染图上逐一点击会活动的部件（例如左门、右门、抽屉或盖子），选好后点击下方识别按钮。';
  const states=(a.states || []).map((row,index)=>`<li><span class="listing-pose-num">${String(index+1).padStart(2,'0')}</span><strong>${esc(row.label_zh || row.id)}</strong> <span>${esc(row.label_en || '')}</span>${row.role?`<span class="listing-pose-role">${esc(listingRoleNames[row.role] || row.role)}</span>`:''}</li>`).join('');
  const usage=((a.usage || {}).zh || []).map(rule=>`<li>${esc(rule)}</li>`).join('');
  const notes=(a.notes || []).map(note=>`<li>${esc(note)}</li>`).join('');
  const picture=(url,alt)=>url?`<a href="${esc(url)}" target="_blank" rel="noopener"><img src="${esc(url)}?t=${Math.round((a.at || 0)*1000)}" alt="${esc(alt)}" loading="lazy"></a>`:'';
  const pickedChips=pickedList.map(name=>`<button type="button" class="listing-pick-chip is-picked" data-action="articulation-unpick" data-part="${esc(name)}" title="点击移除">${esc(name)} <span aria-hidden="true">×</span></button>`).join('');
  const choiceChips=choices.map(row=>{
    const on=picked.has(row.name);
    return `<button type="button" class="listing-pick-chip${on?' is-picked':''}" data-action="articulation-pick-part" data-part="${esc(row.name)}" aria-pressed="${on}">${row.id?`<span>${row.id}</span> `:''}${esc(row.name)}</button>`;
  }).join('');
  const runLabel=running?'识别中…':pickedList.length?`按已选 ${pickedList.length} 个部件识别`:(a.status==='unknown' || !a.status?'识别活动部件':'重新识别活动部件');
  box.innerHTML=`
    <p class="field-help">钳子、卡尺、卷尺、美工刀、工具箱这类产品有活动部件。可在左侧产品图上直接点选会活动的部件（如左门、右门、抽屉）来加速识别并避免误判，再由 Blender 摆出姿态。</p>
    <div class="listing-articulation-status" data-status="${esc(running?'running':a.status || 'unknown')}" role="status"><span class="field-label">状态</span><strong>${esc(status)}</strong>${a.product_kind?`<span>${esc(a.product_kind)}</span>`:''}</div>
    <div class="listing-pick-box">
      <div class="listing-pick-head">
        <button type="button" class="button ${listingArticulationPickMode?'accent':'secondary'}" data-action="articulation-pick-toggle" aria-pressed="${listingArticulationPickMode}" ${running || !canPick?'disabled':''} title="${esc(pickBtnTitle)}">${esc(pickBtnLabel)}</button>
        ${pickedList.length?`<button type="button" class="text-button" data-action="articulation-pick-clear" ${running?'disabled':''}>清空已选</button>`:''}
      </div>
      ${listingArticulationPickMode?`<p class="field-help">${esc(pickHelp)}</p>`:''}
      ${pickedChips?`<div class="listing-pick-chips" aria-label="已选活动部件">${pickedChips}</div>`:''}
      ${choices.length?`<details class="listing-parts"><summary>或直接勾选主要部件（${choices.length}）</summary><div class="listing-pick-chips">${choiceChips}</div></details>`:''}
    </div>
    ${a.reasoning?`<p class="listing-articulation-reason">${esc(a.reasoning)}</p>`:''}
    ${states?`<h4 class="listing-heading">姿态</h4><ol class="listing-poses">${states}</ol>`:''}
    ${usage?`<h4 class="listing-heading">正确用法</h4><ul class="listing-usage">${usage}</ul>`:''}
    ${notes?`<h4 class="listing-heading">说明</h4><ul class="listing-usage">${notes}</ul>`:''}
    ${a.parts_sheet || a.previews?`<div class="listing-articulation-pictures">${picture(a.parts_sheet,'部件编号图')}${picture(a.previews,'各姿态预览')}</div>`:''}
    ${noVision?`<p class="listing-need-note" id="listingVisionNeeded" tabindex="-1">识别活动部件要用能看图的视觉模型，现在没有配置：在「AI 设置」里，主模型能看图就直接用它；不能的话，在「预览图像分析」里勾选「使用独立视觉模型」，填写「视觉模型名称」后保存。</p>`:''}
    <div class="listing-save"><button type="button" class="button ${pickedList.length?'primary':'secondary'}" data-action="articulation-run" ${running || noVision?'disabled':''} ${noVision?'aria-describedby="listingVisionNeeded"':''}>${esc(runLabel)}</button></div>`;
  const sum=$('listingPanel').querySelector('[data-sum="articulation"]');if(sum)sum.textContent=listingArticulationSummary();
  if(listingFocusPending==='articulation-run'){listingFocusPending=null;(box.querySelector('[data-action="articulation-run"]:not(:disabled)') || box.querySelector('#listingVisionNeeded'))?.focus();}
  if(running && listingVisible())listingArticulationTimer=setTimeout(()=>listingRenderArticulation(true),2000);
}

// ---- the built set ----
// The built set is shown large in the main stage (#listingStage) while this tab is open; without a stage (or when the
// user asked to see the model) it is shown in the inspector as before. One renderer feeds both.
let listingStageOff=false,listingDetail=null;
const listingStatusWords={passed:'通过',failed:'未通过',unreviewed:'未审核'};
const listingSceneWords={passed:'通过核对',failed:'未通过核对',pending:'未完成核对',unchecked:'未核对'};
const listingLevelWords={critical:'需要处理',error:'未完成'};          // read out before the notes that need doing something
// A page may be uploaded only when the layout checks and the visual review both passed (a page the review fixed and then
// passed counts); the reviewed download holds exactly these pages.
function listingPageReady(page) { return !!(page && page.checks && page.checks.passed && page.review && ['passed','revised'].includes(page.review.status)); }
function listingPageStatus(page) {
  if(listingPageReady(page))return 'passed';
  if(page?.review?.status==='failed' || (page?.checks && !page.checks.passed))return 'failed';
  return 'unreviewed';
}
function listingReviewRan(pages) { return pages.some(page=>page.review && !['not_verified',undefined,null].includes(page.review.status)); }
function listingScore(page) { return Number.isFinite(page?.review?.score)?page.review.score:null; }
function listingStageNode() { return $('listingStage'); }
function listingHasResults() { return !!((listingState?.pages || []).length || (listingState?.scenes || []).length); }
function listingStageShown() { return !!(listingStageNode() && listingVisible() && listingOpenFor() && listingHasResults() && !listingStageOff); }
function listingSyncStage() {
  const stage=listingStageNode(),on=listingStageShown();
  document.body.classList.toggle('listing-stage-on',on);
  if(stage)stage.hidden=!on;
}
// Rewritten only when something changed: the 1.5 s polling during a build must not reset scrolling or focus.
const listingHTMLCache=new WeakMap();
function listingSetHTML(node,html) { if(node && listingHTMLCache.get(node)!==html){node.innerHTML=html;listingHTMLCache.set(node,html);} }
const listingCJK=/[㐀-鿿]/;
// The server sorts the notes (state.notes_view, page.note_view); this is the same split for a server that does not.
function listingNoteFallback(notes,skipped=[]) {
  const seller=new Map(),tech=new Map(),held=new Map();
  for(const [name,reason] of skipped){const key=String(reason).replace(/。$/,'');held.set(key,[...(held.get(key) || []),name]);}
  for(const note of notes || []){const text=String(note || '').trim();if(!text)continue;const bucket=listingCJK.test(text)?seller:tech;bucket.set(text,(bucket.get(text) || 0)+1);}
  for(const [reason,names] of held)seller.set(`${names.map(name=>`「${name}」`).join('、')}暂时无法生成：${reason}。`,1);
  return {seller:[...seller].map(([text,count])=>({text,level:'warn',count})),tech:[...tech].map(([text,count])=>({text,count}))};
}
function listingPageNotes(page) { return page.note_view || listingNoteFallback(page.notes); }
function listingNoteItem(row) { return `<li data-level="${esc(row.level || 'warn')}">${['critical','error'].includes(row.level)?`<span class="sr-only">${listingLevelWords[row.level]}：</span>`:''}${esc(row.text)}${row.count>1?` <span class="listing-count" title="出现 ${row.count} 次">×${row.count}</span>`:''}</li>`; }
function listingTechHTML(rows) {
  if(!rows.length)return '';
  return `<details class="listing-tech"><summary>技术记录（${rows.length}）</summary><ul>${rows.map(row=>`<li>${esc(row.text)}${row.count>1?` ×${row.count}`:''}</li>`).join('')}</ul>
    <p class="field-help">模板排版时的内部记录，供调试用，一般不需要处理。</p></details>`;
}
function listingDegradedText(degraded) {
  if(!degraded)return '';
  const what=(degraded.words || []).length?degraded.words.join('、'):'部分步骤';
  return degraded.reason==='quota'?`AI 额度用尽：${what}未完成。`:`AI 服务拒绝了请求（密钥或权限）：${what}未完成。`;
}
function listingDegradedHTML(degraded,active) {
  if(!degraded || active)return '';
  return `<div class="listing-degraded" role="status"><p><strong>${esc(listingDegradedText(degraded))}</strong>
    充值或检查密钥后点「补全」：已经做好的素材、场景图、翻译和审核结果都会沿用，只补做没完成的部分（会产生 AI 费用）。</p>
    <button type="button" class="button secondary" data-action="fill-in" ${listingBusy || !listingState?.ready || !listingState?.available?'disabled':''}>补全</button></div>`;
}
function listingBadge(status,label,extra='') { return `<span class="listing-badge" data-status="${esc(status)}">${esc(label)}${extra}</span>`; }
function listingPageName(page,index) { const kinds=listingState.kinds || {};return `${String(index+1).padStart(2,'0')} · ${kinds[page.kind]?.zh || page.kind}`; }
function listingPageCard(page,index,stamp) {
  const status=listingPageStatus(page),score=listingScore(page),notes=listingPageNotes(page),first=notes.seller[0];
  const name=listingPageName(page,index),said=`${listingStatusWords[status]}${score!==null?`，${score} 分`:''}`;
  return `<figure class="listing-page" data-status="${status}" data-page="${esc(page.id)}">
    <button type="button" class="listing-page-open" data-action="page-open" data-page="${esc(page.id)}" aria-label="查看第 ${index+1} 张：${esc(listingState.kinds?.[page.kind]?.zh || page.kind)}（${esc(said)}）">
      <img src="${esc(page.url)}?t=${stamp}" alt="套图第 ${index+1} 张：${esc(listingState.kinds?.[page.kind]?.zh || page.kind)}" loading="lazy"></button>
    <figcaption><span class="listing-page-name">${esc(name)}</span>${listingBadge(status,listingStatusWords[status],score!==null?` · ${score}`:'')}
      <span class="listing-page-size">${page.width}×${page.height}</span><a href="${esc(page.url)}?download=1" download="${esc(page.file)}">下载</a>${page.svg?`<a href="${esc(page.svg)}" download="${esc(page.file.replace(/\.png$/,'.svg'))}" title="文字和图层都可编辑，可用 Illustrator、Figma、Inkscape 打开">可编辑 SVG</a>`:''}</figcaption>
    ${first?`<p class="listing-page-note" data-level="${esc(first.level || 'warn')}">${esc(first.text)}${notes.seller.length>1?`<span class="listing-count">另有 ${notes.seller.length-1} 条</span>`:''}</p>`:''}</figure>`;
}
function listingSceneCard(row,active) {
  const check=listingSceneWords[row.check]?row.check:'unchecked',number=(row.index ?? 0)+1;
  const placed=row.placed==='composite'?listingBadge('neutral','模型合成'):row.placed==='painted'?listingBadge('neutral','AI 绘制'):'';
  // Only said when it holds: the render set into a photograph, or the render put back over the painted product.
  const lock=row.placed==='composite'?'产品是模型的渲染图，合成进背景照片，尺寸和细节与模型一致。':row.restored?'产品已换回模型的渲染图，比例和细节与模型一致。':'';
  const off=row.usable===false?'<span class="field-help">没有通过核对，不会放上页面。</span>':'';
  const why=[...(row.flags || []),...(row.issues || [])].slice(0,3);
  return `<figure class="listing-scene" data-check="${check}">
    <a href="${esc(row.url)}" target="_blank" rel="noopener" title="查看原图"><img src="${esc(row.url)}" alt="场景图 ${number}：${esc(row.title || '')}" loading="lazy"></a>
    <figcaption><span class="listing-badges">${listingBadge(check,listingSceneWords[check])}${placed}</span>
      ${row.title?`<span class="listing-scene-title">${esc(row.title)}</span>`:''}
      ${lock?`<span class="field-help">${esc(lock)}</span>`:''}${check==='pending'?'<span class="field-help">核对没能完成（额度或超时），下次生成时会补做核对，不重新画图。</span>':''}
      ${why.length?`<span class="field-help">${why.map(esc).join('；')}</span>`:''}${off}
      <button type="button" class="button subtle" data-action="scene-redraw" data-index="${esc(row.index ?? 0)}" data-url="${esc(row.url)}" ${active || listingBusy?'disabled':''}>重画这张</button></figcaption></figure>`;
}
function listingResultsHTML(stamp,active) {
  const pages=listingState.pages || [],scenes=listingState.scenes || [];
  const passed=scenes.filter(row=>row.check==='passed').length;
  // the pages first: they are what gets uploaded; the scenes they were made from follow
  return (pages.length?`<section class="listing-page-section" aria-label="套图图片"><h3 class="listing-section-title">套图 <span>${pages.length} 张</span></h3>
    <div class="listing-page-grid">${pages.map((page,index)=>listingPageCard(page,index,stamp)).join('')}</div></section>`:'')
    +(scenes.length?`<section class="listing-scenes" aria-label="本次使用的场景图"><h3 class="listing-section-title">场景图 <span>${scenes.length} 张 · 通过核对 ${passed} 张</span></h3>
    <div class="listing-scene-grid">${scenes.map(row=>listingSceneCard(row,active)).join('')}</div></section>`:'');
}
function listingDetailHTML(page,stamp) {
  const pages=listingState.pages || [],index=pages.indexOf(page),status=listingPageStatus(page),score=listingScore(page),review=page.review || null;
  const notes=listingPageNotes(page),vetoes=review?.veto_words || review?.vetoes || [],issues=review?.issues || [];
  const layout=page.checks && !page.checks.passed?(page.checks.issue_words || page.checks.issues || []):[];
  const reviewed=!review || review.status==='not_verified'?'<p class="field-help">这张图没有经过视觉审核。</p>'
    :`<p>${review.summary?esc(review.summary):'（审核没有给出总结）'}</p>${review.status==='revised' && Number.isFinite(review.first_score)?`<p class="field-help">按审核意见修改过：${review.first_score} → ${score} 分。</p>`:''}`;
  return `<article class="listing-detail" aria-labelledby="listingDetailTitle">
    <header class="listing-detail-head">
      <button type="button" class="button subtle" data-action="page-close">← 返回全部</button>
      <h3 id="listingDetailTitle" tabindex="-1">${esc(listingPageName(page,index))}</h3>${listingBadge(status,listingStatusWords[status],score!==null?` · ${score} 分`:'')}
      <span class="listing-detail-nav"><button type="button" class="button subtle" data-action="page-step" data-step="-1" ${index<=0?'disabled':''} aria-label="上一张">‹</button>
      <span>${index+1} / ${pages.length}</span><button type="button" class="button subtle" data-action="page-step" data-step="1" ${index>=pages.length-1?'disabled':''} aria-label="下一张">›</button></span>
    </header>
    <div class="listing-detail-body">
      <a class="listing-detail-picture" href="${esc(page.url)}" target="_blank" rel="noopener" title="在新标签页打开原图"><img src="${esc(page.url)}?t=${stamp}" alt="${esc(listingPageName(page,index))} 原图"></a>
      <div class="listing-detail-info">
        <h4>视觉审核</h4>${reviewed}
        ${vetoes.length?`<h4>否决项</h4><ul class="listing-detail-list" data-level="error">${vetoes.map(text=>`<li>${esc(text)}</li>`).join('')}</ul>`:''}
        ${issues.length?`<h4>问题</h4><ul class="listing-detail-list">${issues.map(row=>`<li>${row.region?`<strong>${esc(row.region)}</strong>：`:''}${esc(row.evidence || '')}</li>`).join('')}</ul>`:''}
        ${layout.length?`<h4>版式检查</h4><ul class="listing-detail-list" data-level="error">${layout.map(text=>`<li>${esc(text)}</li>`).join('')}</ul>`:''}
        ${notes.seller.length?`<h4>提示</h4><ul class="listing-notes listing-detail-notes">${notes.seller.map(listingNoteItem).join('')}</ul>`:''}
        ${listingTechHTML(notes.tech)}
        <p class="listing-detail-files">${page.width}×${page.height} · <a href="${esc(page.url)}?download=1" download="${esc(page.file)}">下载 PNG</a>${page.svg?` · <a href="${esc(page.svg)}" download="${esc(page.file.replace(/\.png$/,'.svg'))}">可编辑 SVG</a>`:''}</p>
      </div></div></article>`;
}
function listingDownloadsHTML(href,pages,ready,ran) {
  // the same two exports as the inspector bar, with the reviewed one first when a review ran
  if(!pages.length)return '';
  const reviewed=ready?`<a class="button ${ran?'primary':'secondary'}" href="${esc(href)}?reviewed=1">下载已通过审核的 ${ready}/${pages.length} 张</a>`:'';
  return reviewed+`<a class="button ${ran && ready?'subtle':'secondary'}" href="${esc(href)}">${ready===pages.length?'下载全部':'下载草稿（全部）'}</a>`;
}
const listingStageSaid={queued:'套图排队中',pictures:'正在渲染素材',scenes:'正在生成场景图',compose:'正在排版和审核',done:'套图已生成'};
const listingUSD=value=>'US$'+(Number(value) || 0).toFixed(2);
// What the next build will roughly cost (state.estimate) and what this project's logged AI requests cost (state.spend).
function listingCostText(estimate) {
  if(!estimate)return '';
  const [low,high]=estimate.usd || [0,0],parts=[];
  if(estimate.new_scenes)parts.push(`新场景图 ${estimate.new_scenes} 张`);
  if(estimate.kept_scenes)parts.push(`沿用 ${estimate.kept_scenes} 张`);
  if(estimate.review_pages)parts.push(`视觉审核 ${estimate.review_pages} 页`);
  if(!estimate.new_scenes && !estimate.review_pages)return `预估 AI 费用很低：没有新场景图，也不做视觉审核${estimate.kept_scenes?`（沿用 ${estimate.kept_scenes} 张场景图）`:''}`;
  return `预估 AI 费用约 ${listingUSD(low)}${high>low?'–'+listingUSD(high).slice(3):''}（${parts.join('、')}，按官方原价估算）`;
}
function listingRenderCost(state,active) {
  const cost=$('listingCost'),spend=$('listingSpend'),estimate=state.estimate,basis=estimate?.basis || {};
  // estimated by the server from the saved settings: unsaved scene or page changes are said, not silently ignored
  if(cost){const text=active || !state.ready?'':listingCostText(estimate)+(listingDirty.product?'（有未保存的改动，保存后按新设置重新估算）':'');
    cost.hidden=!text;cost.textContent=text;
    cost.title=text?`估算：每张新场景图约 ${basis.tries || 1.5} 次图像模型调用（1536 px medium 每次约 $${basis.image_call || 0.075}），每页视觉审核约 $${(basis.review_page || [0.04,0.08]).join('–')}；沿用的场景图、标题和审核结果不再收费。实际费用以服务商账单为准。`:'';}
  if(spend){const s=state.spend;spend.hidden=!(s && s.calls);
    spend.textContent=s && s.calls?`本项目已记录的 AI 花费约 ${listingUSD(s.usd)}（${s.calls} 次调用${s.unpriced?`，其中 ${s.unpriced} 次的模型价格未知，未计入`:''}；按官方原价估算，${s.since?new Date(s.since*1000).toLocaleDateString('zh-CN')+' 起':'从本版本起'}记录）。`:'';}
  if(state.spend){
    if(typeof current!=='undefined' && current && current.id===listingProject)current.ai_spend=state.spend;
    if(typeof renderProjectSpend==='function')renderProjectSpend(state.spend);
  }
}
// ⑤ 生成: what the set will be made from, each line with a way back to its step.
function listingSummaryHTML(active) {
  const s=listingState,p=listingEdit.product,b=listingEdit.brand,kinds=s.kinds || {};
  const preset=(s.presets || []).find(row=>row.id===b.preset),skipped=listingSkipped(),chosen=listingChosen();
  const make=listingMakeable(),held=listingKindList().filter(kind=>chosen.has(kind) && skipped.has(kind));
  const sc=p.scenes || {enabled:true,count:3},m=s.mounting;
  const rows=[
    ['style','风格',preset?preset.label.zh:b.preset],
    ['pages','图片',`${make.length} 张：${make.map(kind=>kinds[kind]?.zh || kind).join('、') || '没有能生成的图片'}${held.length?`；另有 ${held.length} 张缺资料，补齐前不会生成`:''}`],
    ['product','产品',[p.title || '未填产品名称',p.model,p.size_mm?`最长边 ${p.size_mm} mm`:'未填真实尺寸',`${(p.features || []).filter(row=>String(row.title || '').trim()).length} 条卖点`].filter(Boolean).join(' · ')],
    ['brand','品牌与场景',[b.name || '未填品牌名称',sc.enabled?`场景图 ${sc.count} 张`:'不生成场景图',m?`安装：${m.setting?listingMountWords[m.setting]:'自动'}`:''].filter(Boolean).join(' · ')]];
  // each chosen page that will not be made says so under the pages line, with the way to what it waits for
  const missing=held.map(kind=>{const reason=skipped.get(kind),fill=!reason.hard && reason.fix;
    return `<li class="listing-summary-held"><span class="listing-summary-label">不会生成</span><span class="listing-summary-text">「${esc(kinds[kind]?.zh || kind)}」：${esc(reason.why)}</span>
    ${fill?`<button type="button" class="button subtle" data-action="fill" data-kind="${esc(kind)}" aria-label="去填写「${esc(kinds[kind]?.zh || kind)}」需要的资料" ${active?'disabled':''}>去填写</button>`:''}</li>`;}).join('');
  return rows.map(([step,label,text])=>`<li class="listing-summary-row"><span class="listing-summary-label">${esc(label)}</span><span class="listing-summary-text">${esc(text)}</span>
    <button type="button" class="button subtle" data-action="step" data-step="${step}" aria-label="修改${esc(label)}" ${active?'disabled':''}>修改</button></li>${step==='pages'?missing:''}`).join('');
}
// What the build button says when nothing chosen can be made: the pages waiting and what each needs, or that none is chosen.
function listingNothingText() {
  const skipped=listingSkipped(),fill=listingFillable();
  if(!fill.length)return '图片清单里没有现在能生成的图片：请在「选图片」里至少选一张';
  return '选的图片都还缺资料，现在没有能生成的：'+fill.map(kind=>`「${listingKindName(kind)}」${(skipped.get(kind).needs || []).map(need=>listingNeedWords[need.field] || need.field).join('、') || '缺资料'}`).join('；')+'。补齐后再生成。';
}
function listingRenderMain() {
  const state=listingState;if(!state)return;
  const task=state.task,active=listingTaskActive(),pages=state.pages || [],kinds=state.kinds;
  listingSyncStepper();
  if(listingEdit)listingSetHTML($('listingPlan'),listingSummaryHTML(active));
  const ready=pages.filter(listingPageReady).length,ran=listingReviewRan(pages),unready=pages.length-ready;
  const build=$('listingBuild'),none=!listingMakeable().length;build.disabled=active || listingBusy || !state.ready || !state.available || state.layout_available===false || none;
  build.title=none?listingNothingText():'';
  build.textContent=pages.length?'重新生成套图':'生成套图';
  // Once a reviewed set can be downloaded, that download is the one primary action of the bar.
  build.className='button '+(pages.length && ran && ready?'secondary':'primary');
  // A rebuild reuses the headlines and reviews of the same request (v40); this one asks for new ones.
  const rewrite=$('listingRewrite');if(rewrite){rewrite.hidden=!pages.length || active;rewrite.disabled=build.disabled;}
  $('listingCancel').hidden=!active;
  const download=$('listingDownload');download.hidden=!pages.length || active;download.href=`/api/projects/${encodeURIComponent(listingProject)}/listing/download`;
  // The reviewed export holds only pages that passed the layout checks and the visual review; the draft holds everything.
  // Both carry each page's status (review-status.json, 审核状态.txt), so a failed page never travels without it.
  const reviewed=$('listingDownloadReviewed');
  if(reviewed){reviewed.hidden=!pages.length || active || !ready;reviewed.href=download.href+'?reviewed=1';reviewed.textContent=`下载已通过审核的 ${ready}/${pages.length} 张（ZIP）`;
    reviewed.className='button '+(ran?'primary':'secondary');}
  download.textContent=ready===pages.length?'下载全部（ZIP）':ran?`下载草稿（全部 ${pages.length} 张，含 ${unready} 张未通过或未审核）`:`下载草稿（全部 ${pages.length} 张，未经审核）`;
  download.className='button '+(ran && ready?'subtle':'secondary');
  const progress=$('listingProgress');progress.hidden=!active;
  if(active){const value=Math.max(0,Math.min(100,Math.round(task.progress || 0)));$('listingProgressBar').style.width=value+'%';progress.setAttribute('aria-valuenow',String(value));}
  const degraded=state.degraded || null;
  $('listingStatus').textContent=!state.available?'渲染组件不可用，无法生成套图。':state.layout_available===false?'没有找到 Chrome 或 Edge 浏览器，无法排版品牌模板。':!state.ready?'模型分析完成后才能生成套图。'
    :none && !active?listingNothingText()
    :active?`${task.detail || (task.status==='queued'?'排队中':'正在生成')} · ${Math.round(task.progress || 0)}%`
    :task?.status==='failed'?`上次生成失败：${task.error || ''}`:task?.status==='cancelled'?'上次生成已取消。'
    :pages.length?`共 ${pages.length} 张，${new Date((state.built_at || 0)*1000).toLocaleString('zh-CN',{hour12:false})} 生成${degraded?'，部分未完成':''}。`:'还没有生成套图。';
  // The line above changes every poll (its percentage); screen readers hear only when the stage or the outcome changes.
  const said=active?(listingStageSaid[task.stage] || '正在生成套图'):$('listingStatus').textContent;
  const announce=$('listingAnnounce');if(announce && announce.textContent!==said)announce.textContent=said;
  listingRenderCost(state,active);
  const filler=$('listingDegraded');if(filler)listingSetHTML(filler,listingDegradedHTML(degraded,active));
  const view=state.notes_view || listingNoteFallback(state.notes,state.plan.filter(row=>row.skipped).map(row=>[kinds[row.kind]?.zh || row.kind,row.skipped]));
  const busy=listingComponentsBusy()?[{text:'有组件还在处理，完成后再生成会把它们放进套图。',level:'warn',count:1}]:[];
  const needsRealSize=Array.isArray(listingEdit?.product?.pages) && listingEdit.product.pages.includes('T06') && !(listingEdit.product.size_mm>0) && !(current?.plan?.dimensions_mm?.length===3) && !state.plan.some(row=>row.kind==='T06' && row.skipped);
  const sizeNote=needsRealSize?[{text:`「${kinds.T06?.zh || '尺寸图'}」需要填写产品的真实尺寸（mm），请先在「产品信息」中填写最长边真实尺寸。`,level:'warn',count:1}]:[];
  listingSetHTML($('listingNotes'),[...view.seller,...sizeNote,...busy].map(listingNoteItem).join('')+(view.tech.length?`<li class="listing-tech-row">${listingTechHTML(view.tech)}</li>`:''));
  const stamp=Math.round((state.built_at || 0)*1000);
  listingSyncStage();
  const stage=listingStageNode(),open=listingDetail && pages.find(page=>page.id===listingDetail);
  if(!open)listingDetail=null;
  const shown=open?listingDetailHTML(open,stamp):listingResultsHTML(stamp,active);
  if(listingStageShown()){
    const counts={passed:0,failed:0,unreviewed:0};for(const page of pages)counts[listingPageStatus(page)]++;
    listingSetHTML($('listingStageHead'),`<div class="listing-stage-title"><h2>套图${pages.length?` · ${pages.length} 张`:''}</h2>
      ${pages.length?`<p>${listingBadge('passed',`通过 ${counts.passed}`)}${listingBadge('failed',`未通过 ${counts.failed}`)}${listingBadge('unreviewed',`未审核 ${counts.unreviewed}`)}</p>`:''}</div>
      <div class="listing-stage-actions">${active?'':listingDownloadsHTML(download.href,pages,ready,ran)}<button type="button" class="button subtle" data-action="stage-model">返回模型</button></div>`);
    listingSetHTML($('listingStageNotice'),listingDegradedHTML(degraded,active));
    listingSetHTML($('listingStageBody'),shown);
    listingSetHTML($('listingPages'),'<p class="field-help">套图显示在左侧主画面：点任意一张查看大图、审核结果和提示。</p>');
  }else{
    listingSetHTML($('listingStageBody'),'');           // a hidden copy would duplicate the detail's ids and buttons
    const back=stage && listingHasResults() && listingStageOff?'<p><button type="button" class="button subtle" data-action="stage-set">在主画面查看套图</button></p>':'';
    listingSetHTML($('listingPages'),back+shown);
  }
}
function listingFocusDetail() { const title=$('listingDetailTitle');if(title)title.focus(); }
// The actions of the results (in the stage or the inspector). Returns true when it handled the click.
async function listingResultsAction(button) {
  const action=button.dataset.action,pages=listingState?.pages || [];
  if(action==='page-open'){listingDetail=button.dataset.page;listingRenderMain();listingFocusDetail();return true;}
  if(action==='page-close'){const was=listingDetail;listingDetail=null;listingRenderMain();
    [...document.querySelectorAll('.listing-page-open')].find(node=>node.dataset.page===was)?.focus();return true;}
  if(action==='page-step'){const index=pages.findIndex(page=>page.id===listingDetail),next=pages[index+Number(button.dataset.step)];
    if(next){listingDetail=next.id;listingRenderMain();listingFocusDetail();}return true;}
  if(action==='stage-model'){listingStageOff=true;listingRenderMain();return true;}
  if(action==='stage-set'){listingStageOff=false;listingRenderMain();return true;}
  if(action==='fill-in'){await listingBuildNow();return true;}
  if(action==='scene-redraw'){
    const ask='重画这张场景图？其余场景图和已经做好的部分都会沿用，只重新生成这一张，然后重新排版（会产生图像模型费用）。';
    if(!await listingConfirm('sceneRedrawDialog','重画这张场景图？',ask,'重画这张',false))return true;
    if(listingBusy)return true;listingBusy=true;listingRenderMain();
    try{
      await listingSavePending();
      await api(`/api/projects/${listingProject}/listing/scenes/${Number(button.dataset.index)}/redraw`,{method:'POST',body:{url:button.dataset.url}});
      toast('已开始重画这张场景图');await listingLoad(false);
    }catch(error){toast(error.message,true);}
    finally{listingBusy=false;listingRenderMain();}
    return true;
  }
  return false;
}
function listingOnStageKey(event) {
  if(!listingDetail || event.defaultPrevented)return;
  if(event.target.closest?.('input, textarea, select'))return;
  // on the step controls these keys belong to the steps, not to a page left open in the stage
  if(event.target.closest?.('#listingSteps, .listing-step-head, .listing-side, .listing-step-nav'))return;
  if(event.key==='Escape'){event.preventDefault();listingResultsAction({dataset:{action:'page-close'}});}
  else if(event.key==='ArrowLeft' || event.key==='ArrowRight'){event.preventDefault();listingResultsAction({dataset:{action:'page-step',step:event.key==='ArrowLeft'?'-1':'1'}});}
}


// ---- reading the forms ----
function listingOnInput(event) {
  listingOnInputOnce(event);
  if(listingState && listingDirty.product)listingRenderCost(listingState,listingTaskActive());   // the estimate says it is stale
  listingSyncSums();listingAutoSave();
}
function listingOnInputOnce(event) {
  const node=event.target,scope=node.dataset.scope,key=node.dataset.key;if(!scope || !listingEdit)return;
  const p=listingEdit.product,index=Number(node.dataset.index);
  if(scope==='product'){p[key]=key==='size_mm'?listingNumber(node.value):node.value;listingTouch();}
  else if(['feature','feature-part','spec','figure'].includes(scope) && !p[{feature:'features','feature-part':'features',spec:'specs',figure:'figures'}[scope]]?.[index])return;   // a row the form still shows but the product lost
  else if(scope==='feature'){p.features[index][key]=node.value;listingTouch();}
  else if(scope==='feature-part'){const parts=new Set(p.features[index].parts || []);if(node.checked)parts.add(node.value);else parts.delete(node.value);p.features[index].parts=[...parts];
    node.closest('details').querySelector('summary').textContent=`对应部件（${parts.size}）`;listingTouch();}
  else if(scope==='spec'){p.specs[index][key]=node.value;listingTouch();}
  else if(scope==='figure'){p.figures[index][key]=node.value;listingTouch();}
  else if(scope==='usecase'){p.usecase[key]=node.type==='checkbox'?node.checked:['diameter_mm','width_mm','thickness_mm','length_mm'].includes(key)?listingNumber(node.value):node.value;listingTouch();
    if(event.type==='change' && ['kind','mode'].includes(key))listingKeepFocus(listingRenderBrand);}
  else if(scope==='scenes'){p.scenes={...p.scenes,[key]:node.type==='checkbox'?node.checked:key==='count'?Number(node.value):node.value};listingTouch();}
  else if(scope==='usecase-triple'){p.usecase[key][index]=listingNumber(node.value) ?? 0;listingTouch();}
  else if(scope==='brand'){
    if(node.type==='radio' && !node.checked)return;
    listingEdit.brand[key]=key==='badges'?node.value.split(/[,，;；\n]+/).map(text=>text.trim()).filter(Boolean):node.value;listingDirty.brand=true;}
  else if(scope==='brand-auto'){listingEdit.brand[key]=node.checked?null:listingState.brand[key+'_used'];listingDirty.brand=true;listingKeepFocus(listingRenderBrand);}
  else if(scope==='page' && event.type==='change'){
    // From the automatic plan, the first change turns it into an explicit list. A page still waiting for the seller
    // ("需补充") stays on it and is made once what it needs is there; one nothing here can supply is left off.
    const chosen=listingChosen(),skipped=listingSkipped();
    if(node.checked)chosen.add(node.value);else chosen.delete(node.value);
    for(const kind of [...chosen])if(skipped.get(kind)?.hard)chosen.delete(kind);
    p.pages=(listingState.order || Object.keys(listingState.kinds)).filter(kind=>chosen.has(kind));
    for(const kind of chosen)if(!p.pages.includes(kind))p.pages.push(kind);
    listingTouch();listingRenderKinds();listingRenderMain();
    if(node.checked && node.value==='T06' && !(p.size_mm>0) && !(current?.plan?.dimensions_mm?.length===3)){
      toast('「尺寸图」需要填写产品的真实尺寸（mm），请在「产品信息」中填写最长边真实尺寸后再生成。',true);
    }
  }
}
async function listingOnChange(event) {
  const node=event.target;
  if(node.dataset.scope==='mounting'){if(node.checked)await listingSaveMounting(node.value);}
  else if(node.id==='listingComponentFiles'){const files=[...(node.files || [])];node.value='';await listingUploadComponents(files);}
  else if(node.dataset.scope==='component'){
    const key=node.dataset.key,value=node.type==='checkbox'?node.checked:key==='quantity'?Number(node.value):key==='size_mm'?listingNumber(node.value):node.value;
    try{await api(`/api/projects/${listingProject}/components/${node.dataset.id}`,{method:'PUT',body:{[key]:value}});await listingLoad(false);}
    catch(error){toast(error.message,true);listingLoad(false).catch(()=>{});}
  }else if(node.id==='listingLogoFile' && node.files.length){
    const form=new FormData();form.append('file',node.files[0]);
    try{listingAdopt(await api(`/api/projects/${listingProject}/listing/logo`,{method:'POST',body:form}),false);listingShare=null;if(listingStep==='brand')listingKeepFocus(listingRenderBrand);toast('标志已上传');}catch(error){toast(error.message,true);}
  }else listingOnInput(event);
}
function listingProductBody() {
  const p=listingEdit.product;
  return {title:p.title,model:p.model,headline:p.headline,subline:p.subline,source:p.source || '',language:p.language,size_mm:p.size_mm,features:p.features.filter(row=>row.title.trim()),
    specs:p.specs.filter(row=>row.label.trim() && row.value.trim()),figures:p.figures.filter(row=>row.value.trim() && row.label.trim()),pages:p.pages,usecase:p.usecase,scenes:p.scenes,aspect:p.aspect};
}
async function listingSaveProduct(quiet) {
  const rev=listingProductRev;
  const state=await api(`/api/projects/${listingProject}/listing/product`,{method:'PUT',body:listingProductBody()});
  if(rev===listingProductRev)listingDirty.product=false;          // typed while it was saving: still unsaved, and kept
  listingAdopt(state,false);if(!quiet){listingRenderPanel();toast('已保存');}
}
async function listingSaveBrand(quiet) {
  const b=listingEdit.brand;
  const state=await api(`/api/projects/${listingProject}/listing/brand`,{method:'PUT',body:{preset:b.preset,name:b.name,accent:b.accent,dark:b.dark,badges:b.badges,legal:b.legal,unit:b.unit,fonts:b.fonts,badge_style:b.badge_style,texture:b.texture}});
  listingDirty.brand=false;listingShare=null;listingAdopt(state,false);if(!quiet){listingRenderPanel();toast('已保存');}
}
async function listingSavePending() { if(listingDirty.product)await listingSaveProduct(true);if(listingDirty.brand)await listingSaveBrand(true); }
async function listingBuildNow(fresh=false) {
  if(listingBusy)return;listingBusy=true;listingRenderMain();
  try{
    await listingSavePending();
    const plan=listingState?.plan || [],kinds=listingState?.kinds || {};
    const runnable=plan.filter(row=>!row.skipped),skipped=plan.filter(row=>row.skipped);
    const missingSize=skipped.find(row=>row.kind==='T06' || String(row.skipped || '').includes('真实尺寸'));
    if(!runnable.length){
      if(missingSize){
        toast(`无法生成：「${kinds[missingSize.kind]?.zh || '尺寸图'}」需要填写产品的真实尺寸（mm），请先在「产品信息」中填写最长边真实尺寸。`,true);
        listingTab='product';listingRenderPanel();
        document.querySelector('#listingPanel [data-scope="product"][data-key="size_mm"]')?.focus();
      }else if(skipped.length){
        toast('无法生成：'+skipped.map(row=>`「${kinds[row.kind]?.zh || row.kind}」${row.skipped}`).join('；')+'。',true);
      }else{
        toast('请至少勾选一张可以生成的套图页面。',true);
      }
      return;
    }
    if(missingSize && Array.isArray(listingEdit?.product?.pages) && listingEdit.product.pages.includes(missingSize.kind)){
      toast(`提示：未填写产品真实尺寸，本次将跳过「${kinds[missingSize.kind]?.zh || '尺寸图'}」；如需生成请在「产品信息」中填写最长边真实尺寸（mm）。`);
    }
    await api(`/api/projects/${listingProject}/listing/build`,{method:'POST',body:fresh?{fresh:true}:{}});
    await listingLoad(false);
  }catch(error){toast(error.message,true);}
  finally{listingBusy=false;listingRenderMain();}
}
// Arrow keys move along a group of choice cards (and pick, for the single-choice style cards), wrapping at the ends.
function listingOnCardKey(event) {
  const input=event.target;if(!input?.matches?.('.listing-card input'))return;
  const group=input.closest('[data-arrows]'),moves={ArrowRight:1,ArrowDown:1,ArrowLeft:-1,ArrowUp:-1,Home:'first',End:'last'};
  if(!group || !(event.key in moves))return;
  const inputs=[...group.querySelectorAll('.listing-card input')].filter(node=>!node.disabled),index=inputs.indexOf(input);
  if(index<0)return;
  event.preventDefault();
  const move=moves[event.key],next=move==='first'?inputs[0]:move==='last'?inputs[inputs.length-1]:inputs[(index+move+inputs.length)%inputs.length];
  next.focus();
  if(next.type==='radio' && !next.checked)next.click();
}
async function listingOnClick(event) {
  const button=event.target.closest('[data-action]');if(!button || !listingEdit)return;
  if(await listingResultsAction(button))return;
  const action=button.dataset.action,p=listingEdit.product,index=Number(button.dataset.index),id=button.dataset.id;
  try{
    if(action==='step'){if(!button.disabled)await listingGoStep(button.dataset.step);}
    else if(action==='add-feature'){if(p.features.length<8){p.features.push({title:'',detail:'',parts:[],icon:''});listingTouch();listingRenderProduct();
      [...$('listingPanel').querySelectorAll('[data-scope="feature"][data-key="title"]')].pop()?.focus();}}
    else if(action==='add-spec'){if(p.specs.length<10){p.specs.push({label:'',value:''});listingTouch();listingOpenSections.add('more');listingRenderProduct();
      [...$('listingPanel').querySelectorAll('[data-scope="spec"][data-key="label"]')].pop()?.focus();}}
    else if(action==='add-figure'){if(p.figures.length<3){p.figures.push({value:'',unit:'',label:''});listingTouch();listingOpenSections.add('more');listingRenderProduct();
      [...$('listingPanel').querySelectorAll('[data-scope="figure"][data-key="value"]')].pop()?.focus();}}
    else if(action.startsWith('remove-')){p[{feature:'features',spec:'specs',figure:'figures'}[action.slice(7)]].splice(index,1);listingTouch();listingRenderProduct();}
    else if(action==='pages-auto'){p.pages=null;listingTouch();await listingSaveProduct(true);listingRenderKinds();
      $('listingKinds')?.querySelector('.listing-card input:not(:disabled)')?.focus();}
    else if(action==='extract'){
      const text=($('listingSource')?.value || '').trim();
      if(!text){toast('请先粘贴一段产品介绍',true);$('listingSource')?.focus();return;}
      button.disabled=true;button.setAttribute('aria-busy','true');button.textContent='正在提取…';
      const id=listingProject;
      try{await listingSavePending();
        const state=await api(`/api/projects/${id}/listing/extract`,{method:'POST',body:{text}});
        if(listingOpenFor()!==id)return;                 // another project was opened while the model was reading
        listingDirty.product=false;listingAdopt(state,true);toast('已从介绍中提取');}
      finally{button.disabled=false;button.removeAttribute('aria-busy');button.textContent='提取到各栏';}
    }
    else if(action==='draft'){
      button.disabled=true;button.setAttribute('aria-busy','true');button.textContent='正在起草…';
      const id=listingProject;
      try{await listingSavePending();
        const state=await api(`/api/projects/${id}/listing/draft`,{method:'POST',body:{hint:($('listingSource')?.value || '').trim().slice(0,400),language:p.language}});
        if(listingOpenFor()!==id)return;
        listingDirty.product=false;listingAdopt(state,true);
        const count=(state.product?.features || []).length;
        toast(count?`已${state.product.drafted?.source==='ai'?'由 AI ':''}起草 ${count} 条卖点`:'未识别出可起草的卖点，请粘贴产品介绍或手动添加',!count);}
      finally{button.disabled=false;button.removeAttribute('aria-busy');button.textContent='起草文案';}
    }
    else if(action==='component-turn'){await api(`/api/projects/${listingProject}/components/${id}`,{method:'PUT',body:{turn:button.dataset.turn}});await listingLoad(false);}
    else if(action==='component-refresh'){await api(`/api/projects/${listingProject}/components/${id}/refresh`,{method:'POST'});await listingLoad(false);}
    else if(action==='component-remove'){
      const row=(listingState.components || []).find(item=>item.id===id),name=row?.name?`「${row.name}」`:'这个组件';
      if(!await listingConfirm('componentRemoveDialog','删除组件？',`删除${name}后，它的文件和抠图会一并删除，套装清单和主图里不再出现它。删除后无法恢复，需要时请重新上传。`,'删除组件'))return;
      await api(`/api/projects/${listingProject}/components/${id}`,{method:'DELETE'});await listingLoad(false);}
    else if(action==='articulation-pick-toggle'){
      listingSetArticulationPickMode(!listingArticulationPickMode);
      listingRenderArticulation(false);
      if(listingArticulationPickMode)toast((typeof realtimeAvailable==='function' && realtimeAvailable())?'请直接在左侧 3D 模型上点击会活动的部件（拖动仍可旋转视角）':'请在左侧产品图上点击会活动的部件，点完后点击下方开始识别');
    }
    else if(action==='articulation-pick-part'){
      const part=button.dataset.part,set=listingArticulationPickSet(listingProject);
      if(part){if(set.has(part))set.delete(part);else set.add(part);listingSyncRealtimePick();listingRenderArticulation(false);}
    }
    else if(action==='articulation-unpick'){
      const part=button.dataset.part,set=listingArticulationPickSet(listingProject);
      if(part){set.delete(part);listingSyncRealtimePick();listingRenderArticulation(false);}
    }
    else if(action==='articulation-pick-clear'){
      listingArticulationPickSet(listingProject).clear();listingArticulationPoints=[];
      listingSyncRealtimePick();listingRenderArticulation(false);listingRenderArticulationMarkers();
    }
    else if(action==='articulation-run'){
      button.disabled=true;button.textContent='识别中…';
      listingSetArticulationPickMode(false);
      const id=listingProject,selected=[...listingArticulationPickSet(id)];
      const data=await api(`/api/projects/${id}/articulation`,{method:'POST',body:selected.length?{selected_parts:selected}:{}});
      if(listingOpenFor()!==id)return;
      listingArticulation={project:id,data};listingRenderArticulation(false);
      toast(selected.length?`已按所选 ${selected.length} 个部件开始快速识别`:'已开始重新识别活动部件');
    }
    else if(action==='logo-remove'){
      if(!await listingConfirm('logoRemoveDialog','移除品牌标志？','移除后，下次生成的套图不再印标志，页眉改为显示品牌名称文字。需要时可以重新上传。','移除标志'))return;
      listingAdopt(await api(`/api/projects/${listingProject}/listing/logo`,{method:'DELETE'}),false);listingShare=null;if(listingStep==='brand')listingKeepFocus(listingRenderBrand);}
    else if(action==='brand-copy'){
      const source=$('listingBrandSource')?.value || '',share=listingShare?.data;
      if(!source){toast('请先选择要复制的项目',true);$('listingBrandSource')?.focus();return;}
      const row=source==='default'?share?.default:(share?.projects || []).find(item=>item.id===source);
      const from=source==='default'?'默认品牌':`「${row?.project_name || '所选项目'}」的品牌`;
      if(!await listingConfirm('brandCopyDialog','复制品牌？',`用${from}替换这个项目的品牌风格、名称、颜色、徽标、说明文字和标志。这个项目当前的品牌设置不会保留。`,'复制并替换',false))return;
      const id=listingProject;
      const state=await api(`/api/projects/${id}/listing/brand/copy`,{method:'POST',body:{source}});
      if(listingOpenFor()!==id)return;
      listingDirty.brand=false;listingShare=null;listingAdopt(state,false);if(listingStep==='brand')listingKeepFocus(listingRenderBrand);toast('品牌已复制到这个项目');
    }
    else if(action==='brand-default' || action==='brand-default-clear'){
      const id=listingProject,clear=action==='brand-default-clear';
      if(!clear)await listingSavePending();                 // the default is the brand as saved: typed changes go in first
      const data=await api(`/api/projects/${id}/listing/brand/default`,{method:'POST',body:clear?{clear:true}:{}});
      if(listingOpenFor()!==id)return;
      listingShare={project:id,data};listingRenderShare();toast(clear?'已取消默认品牌：新项目沿用最近保存的品牌':'已设为默认品牌：之后新建的项目会使用它');
    }
    else if(action==='fill')await listingGoFill(listingSkipped().get(button.dataset.kind)?.fix);
    else if(action==='need-go')await listingGoFill({step:button.dataset.step,field:button.dataset.field});
    else if(action==='component-add'){if(!listingBusy)$('listingComponentFiles')?.click();}
  }catch(error){toast(error.message,true);}
  listingAutoSave();                     // a row added or removed on 填产品 is saved like typing
}
function listingInit() {
  const panel=$('inspectorListing');if(!panel)return;
  if($('listingOpen'))$('listingOpen').onclick=()=>listingOpen();
  panel.addEventListener('click',listingOnClick);
  panel.addEventListener('input',listingOnInput);
  panel.addEventListener('change',event=>{listingOnChange(event);});
  panel.addEventListener('keydown',listingOnCardKey);
  panel.addEventListener('keydown',listingOnStageKey);
  // A collapsed section the seller opened stays open when its step is drawn again (toggle does not bubble: captured).
  panel.addEventListener('toggle',event=>{const id=event.target?.dataset?.section;if(id){if(event.target.open)listingOpenSections.add(id);else listingOpenSections.delete(id);}},true);
  // A missing preview picture leaves the neutral block with the page's name under it (error does not bubble: captured).
  panel.addEventListener('error',event=>{const img=event.target;if(img?.matches?.('img[data-thumb]')){img.hidden=true;img.closest('.listing-card-thumb')?.classList.add('is-missing');}},true);
  // The set shown in the main stage: its own clicks and keys (it sits outside the inspector).
  const stage=listingStageNode();
  if(stage){stage.addEventListener('click',event=>{const button=event.target.closest('[data-action]');if(button && listingState)listingResultsAction(button);});
    stage.addEventListener('keydown',listingOnStageKey);}
  $('listingBuild').onclick=()=>listingBuildNow();
  if($('listingRewrite'))$('listingRewrite').onclick=()=>listingBuildNow(true);
  $('listingCancel').onclick=async()=>{const task=listingState?.task;if(!task)return;try{await api(`/api/projects/${listingProject}/listing/${task.id}/cancel`,{method:'POST'});await listingLoad(false);}catch(error){toast(error.message,true);}};
  // Back / Next / Skip / 修改设置: each move saves what was typed before the next step is drawn (listingGoStep).
  if($('listingNext'))$('listingNext').onclick=()=>listingNextStep();
  if($('listingBack'))$('listingBack').onclick=()=>listingBackStep();
  if($('listingSkip'))$('listingSkip').onclick=()=>listingGoStep('build');
  if($('listingEditSteps'))$('listingEditSteps').onclick=()=>listingGoStep('style');
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',listingInit);else listingInit();
