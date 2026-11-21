// The listing workspace ("套图"): what the product page's images are made from, and the images themselves.
// It edits three things kept on the project (the product record, the brand kit, the components) and asks
// the server to build the set. Every picture shown here is a file the server rendered; nothing is drawn
// in the browser. Uses $, api, esc, toast and `current` from app.js.
let listingState=null,listingEdit=null,listingTab='product',listingTimer=null,listingBusy=false,listingProject=null;
const listingDirty={product:false,brand:false};
const listingStageNames={queued:'排队中',analyse:'分析模型',pose:'检查摆放朝向',render:'渲染抠图',cutout:'抠图',ready:'已完成'};
const listingModeNames={through:'穿过工具的开口',under:'垫在工具下方',front:'顶在工具前端',manual:'手动放置（工具中心）'};
const listingClone=value=>JSON.parse(JSON.stringify(value));
function listingOpenFor() { return listingProject && current && current.id===listingProject?listingProject:null; }
function listingComponentsBusy() { return (listingState?.components || []).some(row=>['queued','processing'].includes(row.status)); }
function listingTaskActive() { return ['queued','running'].includes(listingState?.task?.status); }
function listingNumber(value) { const text=String(value ?? '').trim();if(!text)return null;const number=Number(text);return Number.isFinite(number)?number:text; }

// The workspace lives in the inspector's 套图 tab: it is shown, loaded and kept up to date while that tab is open.
function listingUnsaved() { return !!(listingOpenFor() && (listingDirty.product || listingDirty.brand)); }
function listingVisible() { const panel=$('inspectorListing');return !!panel && !panel.hidden; }
function listingOpen() {
  if(!current)return;
  if(typeof setInspectorTab==='function')setInspectorTab('listing');else listingActivate();
}
// Called whenever the 套图 tab is shown (and when another project is opened with it showing).
let listingSwitching=null;
async function listingActivate(refresh=true) {
  if(!current || !listingVisible())return;
  if(listingProject===current.id && (listingState || !refresh)){if(refresh)listingLoad(false).catch(()=>{});return;}
  const target=current.id;
  if(listingSwitching===target)return;            // already switching to this project (a redraw and the tab both asked)
  listingSwitching=target;
  try{if(listingProject && listingProject!==target)await listingSavePending().catch(error=>toast('上一个项目的套图设置没有保存：'+error.message,true));}
  finally{listingSwitching=null;}
  if(!current || current.id!==target)return;
  listingProject=current.id;listingState=null;listingEdit=null;listingTab='product';listingDirty.product=listingDirty.brand=false;
  $('listingPanel').innerHTML='<p class="field-help">正在读取…</p>';$('listingPages').innerHTML='';$('listingPlan').innerHTML='';$('listingNotes').innerHTML='';
  try{await listingLoad(true);}catch(error){$('listingPanel').innerHTML=`<p class="field-error">${esc(error.message)}</p>`;}
}
// Called when the user leaves the tab: words still being typed are saved, polling stops.
function listingDeactivate() {
  clearTimeout(listingTimer);clearTimeout(listingArticulationTimer);
  if(listingOpenFor())listingSavePending().catch(error=>toast(error.message,true));
}
async function listingLoad(reset) {
  const id=listingOpenFor();if(!id)return;
  const state=await api(`/api/projects/${id}/listing`);
  if(id!==listingOpenFor())return;
  listingAdopt(state,reset);
}
// Take the server's state. Words the user is still typing are kept unless `reset` says to drop them.
function listingAdopt(state,reset) {
  const first=!listingEdit || reset;listingState=state;
  if(first || !listingDirty.product)listingEdit={...(listingEdit || {}),product:listingClone(state.product)};
  if(first || !listingDirty.brand)listingEdit={...listingEdit,brand:{preset:state.brand.preset,name:state.brand.name,accent:state.brand.accent,dark:state.brand.dark,
    badges:[...state.brand.badges],legal:state.brand.legal,unit:state.brand.unit,fonts:state.brand.fonts,badge_style:state.brand.badge_style,texture:state.brand.texture}};
  if(first)listingRenderPanel();else if(listingTab==='components')listingRenderComponents();
  listingRenderMain();listingSchedule();
}
function listingSchedule() {
  clearTimeout(listingTimer);
  if(listingVisible() && (listingTaskActive() || listingComponentsBusy()))listingTimer=setTimeout(()=>listingLoad(false).catch(()=>listingSchedule()),1500);
}

// ---- left side ----
function listingField(label,html,help='') { return `<label class="listing-field"><span class="field-label">${esc(label)}</span>${html}${help?`<span class="field-help">${esc(help)}</span>`:''}</label>`; }
function listingInput(scope,key,value,attrs='') { return `<input type="text" data-scope="${scope}" data-key="${esc(key)}" value="${esc(value ?? '')}" ${attrs}>`; }
function listingRenderPanel() {
  for(const tab of document.querySelectorAll('[data-listing-tab]'))tab.setAttribute('aria-selected',String(tab.dataset.listingTab===listingTab));
  if(!listingState || !listingEdit)return;
  if(listingTab==='product')listingRenderProduct();
  else if(listingTab==='components')listingRenderComponents();
  else if(listingTab==='brand')listingRenderBrand();
  else if(listingTab==='articulation')listingRenderArticulation();
  else listingRenderUsecase();
}
function listingPartsHTML(index,chosen) {
  const parts=listingState.parts || [];
  if(!parts.length)return '';
  return `<details class="listing-parts"><summary>对应部件（${chosen.length}）</summary><div class="listing-parts-list">${parts.map(name=>
    `<label><input type="checkbox" data-scope="feature-part" data-index="${index}" value="${esc(name)}" ${chosen.includes(name)?'checked':''}> ${esc(name)}</label>`).join('')}</div>
    <span class="field-help">选中部件后，功能标注会画出引线，并可生成该部件的特写。</span></details>`;
}
function listingRenderProduct() {
  const p=listingEdit.product,size=listingState.size,drafted=p.drafted;
  const rows=(list,scope,cells)=>list.map((row,index)=>`<div class="listing-row" data-row="${scope}">${cells(row,index)}<button type="button" class="icon-button" data-action="remove-${scope}" data-index="${index}" aria-label="删除这一条" title="删除">×</button></div>`).join('');
  $('listingPanel').innerHTML=`
    <div class="listing-source">
      <label class="field-label" for="listingSource">产品介绍</label>
      <textarea id="listingSource" data-scope="product" data-key="source" rows="7" maxlength="8000" placeholder="把商品详情、规格表或产品说明整段粘贴到这里，点「提取到各栏」，由文本模型分出名称、型号、尺寸、卖点、规格和指标。">${esc(p.source || '')}</textarea>
      <div class="listing-source-actions"><button type="button" class="button primary" data-action="extract">提取到各栏</button><button type="button" class="button subtle" data-action="draft" title="没有介绍时，根据模型部件名称起草卖点">起草文案</button></div>
      <p class="field-help">${drafted?`上次${drafted.source==='extracted'?'从介绍中提取':drafted.source==='ai'?'由 AI 起草':'按部件名称起草'}。`:''}提取只采用介绍里写到的内容，含有介绍里没有的数字的条目会被略去；提取到的栏目会替换原内容，其余保留。${(drafted?.notes || []).map(esc).join(' ')}</p>
    </div>
    <div class="listing-grid2">${listingField('产品名称',listingInput('product','title',p.title,'maxlength="60"'))}${listingField('型号',listingInput('product','model',p.model,'maxlength="40" placeholder="如 AG-20X"'))}</div>
    <div class="listing-grid2">${listingField('图片文字语言',`<select data-scope="product" data-key="language"><option value="en" ${p.language==='en'?'selected':''}>English</option><option value="zh" ${p.language==='zh'?'selected':''}>中文</option></select>`)}
    ${listingField('最长边真实尺寸（mm）',listingInput('product','size_mm',p.size_mm ?? '',`inputmode="decimal" placeholder="按模型估计约 ${esc(Math.round(size.mm))}"`),size.known?'':'填写后才会画出产品尺寸线，套装里的大小比例也才准确。')}</div>
    ${listingField('主标题',listingInput('product','headline',p.headline,'maxlength="60" placeholder="一句话说清最重要的卖点"'))}
    ${listingField('副标题',listingInput('product','subline',p.subline,'maxlength="120"'))}
    <h3 class="listing-heading">卖点 <button type="button" class="button subtle" data-action="add-feature">添加</button></h3>
    ${rows(p.features,'feature',(row,index)=>`<div class="listing-feature"><input type="text" data-scope="feature" data-index="${index}" data-key="title" value="${esc(row.title)}" maxlength="40" placeholder="卖点标题" aria-label="卖点标题">
      <input type="text" data-scope="feature" data-index="${index}" data-key="detail" value="${esc(row.detail || '')}" maxlength="90" placeholder="一行说明（可不填）" aria-label="卖点说明">${listingPartsHTML(index,row.parts || [])}</div>`) || '<p class="field-help">还没有卖点。可点「起草文案」，或手动添加。</p>'}
    <h3 class="listing-heading">规格 <button type="button" class="button subtle" data-action="add-spec">添加</button></h3>
    ${rows(p.specs,'spec',(row,index)=>`<input type="text" data-scope="spec" data-index="${index}" data-key="label" value="${esc(row.label)}" maxlength="30" placeholder="名称，如 Voltage" aria-label="规格名称">
      <input type="text" data-scope="spec" data-index="${index}" data-key="value" value="${esc(row.value)}" maxlength="30" placeholder="数值，如 20 V" aria-label="规格数值">`)}
    <h3 class="listing-heading">大数字指标（最多 3 个） <button type="button" class="button subtle" data-action="add-figure" ${p.figures.length>=3?'disabled':''}>添加</button></h3>
    ${rows(p.figures,'figure',(row,index)=>`<input type="text" class="short" data-scope="figure" data-index="${index}" data-key="value" value="${esc(row.value)}" maxlength="12" placeholder="8,500" aria-label="指标数值">
      <input type="text" class="short" data-scope="figure" data-index="${index}" data-key="unit" value="${esc(row.unit || '')}" maxlength="8" placeholder="rpm" aria-label="指标单位">
      <input type="text" data-scope="figure" data-index="${index}" data-key="label" value="${esc(row.label)}" maxlength="30" placeholder="名称" aria-label="指标名称">`)}
    <div class="listing-save"><button type="button" class="button secondary" data-action="save-product">保存产品信息</button></div>`;
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
function listingRenderComponents() {
  const rows=listingState.components || [],focused=document.activeElement?.closest?.('#listingPanel')?document.activeElement:null;
  // While the user types in a component's field, only statuses change under them; the list is rebuilt when they are not typing.
  if(focused && focused.dataset.scope==='component' && $('listingPanel').querySelector('.listing-components')){
    for(const row of rows){const node=$('listingPanel').querySelector(`[data-component="${row.id}"] .listing-component-status`);if(node)node.textContent=`${listingStageNames[row.stage] || '处理中'} · ${Math.round(row.progress || 0)}%`;}
    return;
  }
  $('listingPanel').innerHTML=`<p class="field-help">随箱附带的电池、充电器、工具箱等组件，在新建项目时与主模型一起上传。模型组件会自动分析、摆正，在主图里与产品一起渲染；这里可以修改名称、型号、数量和朝向。</p>
    <div class="listing-components">${rows.map(listingComponentHTML).join('') || '<p class="field-help">这个项目没有组件。新建项目时可在上传窗口的「套装组件」里添加。</p>'}</div>`;
}
function listingRenderBrand() {
  const b=listingEdit.brand,used=listingState.brand;
  $('listingPanel').innerHTML=`
    <fieldset class="listing-presets"><legend class="field-label">风格</legend>${listingState.presets.map(preset=>`<label class="listing-preset" data-ground="${esc(preset.ground)}">
      <input type="radio" name="listingPreset" data-scope="brand" data-key="preset" value="${esc(preset.id)}" ${b.preset===preset.id?'checked':''}>
      <strong>${esc(preset.label.zh)}</strong><span>${esc(preset.note.zh)}</span></label>`).join('')}</fieldset>
    ${listingFontPicker(b)}
    ${listingField('品牌名称',listingInput('brand','name',b.name,'maxlength="40" placeholder="显示在页眉，可不填"'))}
    <div class="listing-grid2">
      <label class="listing-field"><span class="field-label">品牌色</span><span class="listing-color"><input type="color" data-scope="brand" data-key="accent" value="${esc(b.accent || used.accent_used)}" ${b.accent?'':'disabled'} aria-label="品牌色">
        <label class="listing-check"><input type="checkbox" data-scope="brand-auto" data-key="accent" ${b.accent?'':'checked'}> 跟随产品颜色</label></span>
        <span class="field-help">${b.accent?'':'当前 '+esc(used.accent_used)+(used.accent_source==='product'?'（取自产品）':'（默认）')}</span></label>
      <label class="listing-field"><span class="field-label">深色</span><span class="listing-color"><input type="color" data-scope="brand" data-key="dark" value="${esc(b.dark || used.dark_used)}" ${b.dark?'':'disabled'} aria-label="深色">
        <label class="listing-check"><input type="checkbox" data-scope="brand-auto" data-key="dark" ${b.dark?'':'checked'}> 默认</label></span></label>
    </div>
    ${listingField('徽标文字（最多 3 个，用逗号分隔）',listingInput('brand','badges',(b.badges || []).join(', '),'placeholder="如 20V, PRO"'),'印在主图和海报的空白角落，如 2 YEAR WARRANTY、PRO。')}
    <div class="listing-grid2">
      ${listingField('徽标样式',`<select data-scope="brand" data-key="badge_style">${((listingState.elements||{}).badge_styles||[]).map(row=>`<option value="${esc(row.id)}" ${(b.badge_style||'auto')===row.id?'selected':''}>${esc(row.label.zh)}</option>`).join('')}</select>`,'徽标印在主图和海报的空白角落。')}
      ${listingField('背景底纹',`<select data-scope="brand" data-key="texture">${((listingState.elements||{}).textures||[]).map(row=>`<option value="${esc(row.id)}" ${(b.texture||'none')===row.id?'selected':''}>${esc(row.label.zh)}</option>`).join('')}</select>`)}
    </div>
    ${listingField('页脚说明文字',listingInput('brand','legal',b.legal,'maxlength="200" placeholder="如 *Maximum initial battery voltage…"'))}
    ${listingField('尺寸单位',`<select data-scope="brand" data-key="unit"><option value="mm" ${b.unit==='mm'?'selected':''}>毫米 mm</option><option value="in" ${b.unit==='in'?'selected':''}>英寸 in</option></select>`)}
    <div class="listing-logo"><span class="field-label">标志图片</span>${used.logo?`<img src="${esc(used.logo)}" alt="品牌标志"><button type="button" class="button subtle" data-action="logo-remove">移除</button>`:'<span class="field-help">未上传；页眉显示品牌名称文字。</span>'}
      <input id="listingLogoFile" type="file" accept=".png,.jpg,.jpeg,.webp" aria-label="上传标志图片"></div>
    <p class="field-help">风格只决定排版方式；系统不内置任何品牌的标志或名称，请只使用你自己的品牌素材。</p>
    <div class="listing-save"><button type="button" class="button secondary" data-action="save-brand">保存品牌风格</button></div>`;
}
// The font library's pairings, each previewed in its own faces (served by /brandkit/fonts.css).
function listingFontPicker(b) {
  const pairs=listingState.font_pairings||[];
  if(!pairs.length)return '';
  if(!document.getElementById('brandkitFontsCss')){const link=document.createElement('link');link.id='brandkitFontsCss';link.rel='stylesheet';link.href='/brandkit/fonts.css';document.head.appendChild(link);}
  const current=b.fonts||pairs[0].id;
  return `<fieldset class="listing-presets listing-fonts"><legend class="field-label">字体搭配</legend>${pairs.map(pair=>`<label class="listing-preset listing-font">
      <input type="radio" name="listingFonts" data-scope="brand" data-key="fonts" value="${esc(pair.id)}" ${current===pair.id?'checked':''}>
      <span class="listing-font-display" style="font-family:'${esc(pair.display)}',sans-serif">POWER 12V</span>
      <span class="listing-font-cjk" style="font-family:'${esc(pair.cjk)}',sans-serif">强劲动力</span>
      <strong>${esc(pair.label.zh)}</strong><span>${esc(pair.note.zh)}</span>
      <span class="listing-font-names">${esc(pair.display)} · ${esc(pair.text)} · ${esc(pair.cjk)}</span></label>`).join('')}</fieldset>`;
}
function listingRenderUsecase() {
  const u=listingEdit.product.usecase,o=listingState.options,round=['pipe','rod'].includes(u.kind);
  const select=(key,values,names)=>`<select data-scope="usecase" data-key="${key}">${values.map(value=>`<option value="${esc(value)}" ${u[key]===value?'selected':''}>${esc(names[value] || value)}</option>`).join('')}</select>`;
  const number=(key,placeholder)=>`<input type="text" inputmode="decimal" data-scope="usecase" data-key="${key}" value="${esc(u[key] ?? '')}" placeholder="${esc(placeholder)}">`;
  const triple=(key,labels)=>`<span class="listing-grid3">${labels.map((label,index)=>`<input type="text" inputmode="decimal" data-scope="usecase-triple" data-key="${key}" data-index="${index}" value="${esc(u[key][index])}" aria-label="${esc(label)}" title="${esc(label)}">`).join('')}</span>`;
  const sc=listingEdit.product.scenes || {enabled:true,count:3,audience:'auto',people:'auto',hint:''},so=listingState.scene_options || {audiences:[],people:{},max:3};
  $('listingPanel').innerHTML=`
    <h3 class="listing-heading">AI 场景图</h3>
    <p class="field-help">生成套图时，按目标人群策划使用场景，由图像模型围绕锁定的产品渲染生成（产品外形不会被改动），再排进海报主图、场景参数图和使用场景组图。${so.image_ready?'':'<strong>图像模型未配置</strong>：可在 AI 设置里开启；未开启时海报用纯色版，需要场景的页面会跳过。'}</p>
    <label class="listing-check listing-toggle"><input type="checkbox" data-scope="scenes" data-key="enabled" ${sc.enabled?'checked':''}> 生成 AI 场景图</label>
    <div class="listing-grid2">${listingField('场景图数量',`<select data-scope="scenes" data-key="count">${Array.from({length:so.max || 3},(_,i)=>i+1).map(n=>`<option value="${n}" ${sc.count===n?'selected':''}>${n} 张</option>`).join('')}</select>`,'第 1 张是使用中的画面，第 2 张是产品放在使用环境里，第 3 张换一类买家的场景。')}
      ${listingField('目标人群',`<select data-scope="scenes" data-key="audience"><option value="auto">自动推荐</option>${(so.audiences || []).map(row=>`<option value="${esc(row.id)}" ${sc.audience===row.id?'selected':''}>${esc(row.label)}</option>`).join('')}</select>`)}</div>
    ${listingField('画面人物',`<select data-scope="scenes" data-key="people">${Object.entries(so.people || {}).map(([key,label])=>`<option value="${esc(key)}" ${sc.people===key?'selected':''}>${esc(label)}</option>`).join('')}</select>`)}
    ${listingField('场景说明（可选）',listingInput('scenes','hint',sc.hint,'maxlength="300" placeholder="如：车间里的工具柜、户外露营"'),'设置不变时，重新生成套图会沿用已生成的场景图。')}
    ${listingField('图片比例',`<select data-scope="product" data-key="aspect">${(so.aspects || ['1:1','4:5']).map(value=>`<option value="${esc(value)}" ${listingEdit.product.aspect===value?'selected':''}>${value==='1:1'?'1:1 方图 1500×1500':'4:5 竖图 1200×1500'}</option>`).join('')}</select>`)}
    <h3 class="listing-heading">工件使用图（3D 渲染）</h3>
    <p class="field-help">把产品和它加工的工件放在一张图里，例如割管器套在一根白色塑料管上。工件由程序按尺寸生成，位置由模型上量到的开口决定，也可以手动调整。这张图会作为一张场景图，排进使用场景组图。</p>
    <label class="listing-check listing-toggle"><input type="checkbox" data-scope="usecase" data-key="enabled" ${u.enabled?'checked':''}> 渲染工件使用图</label>
    <div class="listing-grid2">${listingField('工件',select('kind',o.workpieces,o.workpiece_names))}${listingField('材质',select('material',o.materials,o.material_names))}</div>
    ${listingField('位置',select('mode',o.modes,listingModeNames),u.mode==='through'?'在模型上寻找被工具三面包住的开口，工件从中穿过；找不到时放在工具中心。':u.mode==='front'?'工件立在工具长轴的一端；方向不对时改选另一端。':'')}
    ${u.mode==='front'?listingField('哪一端',select('side',['+','-'],{'+':'正向一端','-':'反向一端'})):''}
    <div class="listing-grid3">${round?listingField('直径 mm',number('diameter_mm','自动')):listingField('厚度 mm',number('thickness_mm','自动'))}
      ${round?'':listingField('宽度 mm',number('width_mm','自动'))}${listingField('长度 mm',number('length_mm','自动'))}</div>
    ${listingField('位置微调 X / Y / Z（mm）',triple('offset_mm',['X 偏移','Y 偏移','Z 偏移']))}
    ${listingField('工件旋转 X / Y / Z（度）',triple('rotate',['绕 X 旋转','绕 Y 旋转','绕 Z 旋转']))}
    ${listingField('这张图的标题',listingInput('usecase','headline',u.headline,'maxlength="60" placeholder="留空则写「In use · 工件名称」"'))}
    <p class="field-help">图上的工件尺寸是你在这里选的尺寸；产品尺寸线只在填写了真实尺寸时画出。</p>
    <div class="listing-save"><button type="button" class="button secondary" data-action="save-product">保存场景设置</button></div>`;
}

// ---- moving parts: what the pose agent (articulation.py) found, and asking it again ----
let listingArticulation=null,listingArticulationTimer=null;
const listingArticulationStatus={ready:'有活动部件',none:'没有活动部件',failed:'识别失败',unknown:'尚未识别'};
const listingRoleNames={rest:'静止',open:'打开',working:'使用中'};
async function listingRenderArticulation(reload=true) {
  const id=listingOpenFor();if(!id)return;
  clearTimeout(listingArticulationTimer);
  if(reload || !listingArticulation || listingArticulation.project!==id){
    if(!listingArticulation || listingArticulation.project!==id)$('listingPanel').innerHTML='<p class="field-help">正在读取…</p>';
    let data;
    try{data=await api(`/api/projects/${id}/articulation`);}
    catch(error){if(listingTab==='articulation' && listingOpenFor()===id)$('listingPanel').innerHTML=`<p class="field-error">${esc(error.message)}</p>`;return;}
    const was=listingArticulation && listingArticulation.project===id && listingArticulation.data.running;
    listingArticulation={project:id,data};
    if(was && !data.running)listingLoad(false).catch(()=>{});      // the planned pages may change with the result
  }
  if(listingTab!=='articulation' || listingOpenFor()!==id)return;
  const a=listingArticulation.data,running=!!a.running;
  const status=running?'识别中':(listingArticulationStatus[a.status] || a.status || '尚未识别');
  const states=(a.states || []).map((row,index)=>`<li><span class="listing-pose-num">${String(index+1).padStart(2,'0')}</span><strong>${esc(row.label_zh || row.id)}</strong> <span>${esc(row.label_en || '')}</span>${row.role?`<span class="listing-pose-role">${esc(listingRoleNames[row.role] || row.role)}</span>`:''}</li>`).join('');
  const usage=((a.usage || {}).zh || []).map(rule=>`<li>${esc(rule)}</li>`).join('');
  const notes=(a.notes || []).map(note=>`<li>${esc(note)}</li>`).join('');
  const picture=(url,alt)=>url?`<a href="${esc(url)}" target="_blank" rel="noopener"><img src="${esc(url)}?t=${Math.round((a.at || 0)*1000)}" alt="${esc(alt)}" loading="lazy"></a>`:'';
  $('listingPanel').innerHTML=`
    <p class="field-help">钳子、卡尺、卷尺、美工刀、工具箱这类产品有活动部件。视觉模型看模型的部件图，判断哪些部件会动、怎么动，再由 Blender 摆出各个姿态并检查。识别出 2 个以上姿态时，套图会多一张「多姿态展示」，场景图也会按正确的用法摆放产品。每个模型文件只识别一次。</p>
    <div class="listing-articulation-status" data-status="${esc(running?'running':a.status || 'unknown')}" role="status"><span class="field-label">状态</span><strong>${esc(status)}</strong>${a.product_kind?`<span>${esc(a.product_kind)}</span>`:''}</div>
    ${a.reasoning?`<p class="listing-articulation-reason">${esc(a.reasoning)}</p>`:''}
    ${states?`<h3 class="listing-heading">姿态</h3><ol class="listing-poses">${states}</ol>`:''}
    ${usage?`<h3 class="listing-heading">正确用法</h3><ul class="listing-usage">${usage}</ul>`:''}
    ${notes?`<h3 class="listing-heading">说明</h3><ul class="listing-usage">${notes}</ul>`:''}
    ${a.parts_sheet || a.previews?`<div class="listing-articulation-pictures">${picture(a.parts_sheet,'部件编号图')}${picture(a.previews,'各姿态预览')}</div>`:''}
    <div class="listing-save"><button type="button" class="button secondary" data-action="articulation-run" ${running?'disabled':''}>${running?'识别中…':'重新识别'}</button></div>`;
  if(running && listingVisible())listingArticulationTimer=setTimeout(()=>listingRenderArticulation(true),2000);
}

// ---- right side ----
function listingRenderMain() {
  const state=listingState;if(!state)return;
  const task=state.task,active=listingTaskActive(),pages=state.pages || [],kinds=state.kinds;
  const explicit=Array.isArray(listingEdit?.product?.pages),planned=new Map(state.plan.map(row=>[row.kind,row]));
  $('listingPlan').innerHTML=`<span class="field-label">图片清单</span>`+(state.order || Object.keys(kinds)).map(kind=>{
    const label=kinds[kind],row=planned.get(kind),on=explicit?listingEdit.product.pages.includes(kind):!!row && !row.skipped;
    return `<label class="listing-kind${row?.skipped?' is-skipped':''}" title="${esc(row?.skipped?'暂时无法生成：'+row.skipped:label.en)}"><input type="checkbox" data-scope="page" value="${esc(kind)}" ${on?'checked':''} ${active?'disabled':''}> ${esc(label.zh)}</label>`;
  }).join('')+(explicit?`<button type="button" class="button subtle" data-action="pages-auto" ${active?'disabled':''}>恢复自动</button>`:'');
  const build=$('listingBuild');build.disabled=active || listingBusy || !state.ready || !state.available || state.layout_available===false;
  build.textContent=pages.length?'重新生成套图':'生成套图';
  // A rebuild reuses the headlines and reviews of the same request (v40); this one asks for new ones.
  const rewrite=$('listingRewrite');if(rewrite){rewrite.hidden=!pages.length || active;rewrite.disabled=build.disabled;}
  $('listingCancel').hidden=!active;
  const download=$('listingDownload');download.hidden=!pages.length || active;download.href=`/api/projects/${encodeURIComponent(listingProject)}/listing/download`;
  const progress=$('listingProgress');progress.hidden=!active;
  if(active){const value=Math.max(0,Math.min(100,Math.round(task.progress || 0)));$('listingProgressBar').style.width=value+'%';progress.setAttribute('aria-valuenow',String(value));}
  $('listingStatus').textContent=!state.available?'渲染组件不可用，无法生成套图。':state.layout_available===false?'没有找到 Chrome 或 Edge 浏览器，无法排版品牌模板。':!state.ready?'模型分析完成后才能生成套图。'
    :active?`${task.detail || (task.status==='queued'?'排队中':'正在生成')} · ${Math.round(task.progress || 0)}%`
    :task?.status==='failed'?`上次生成失败：${task.error || ''}`:task?.status==='cancelled'?'上次生成已取消。'
    :pages.length?`共 ${pages.length} 张，${new Date((state.built_at || 0)*1000).toLocaleString('zh-CN',{hour12:false})} 生成。`:'还没有生成套图。';
  const skipped=state.plan.filter(row=>row.skipped).map(row=>`「${kinds[row.kind].zh}」暂时无法生成：${row.skipped}。`);
  $('listingNotes').innerHTML=[...(state.notes || []),...skipped,...(listingComponentsBusy()?['有组件还在处理，完成后再生成会把它们放进套图。']:[])].map(note=>`<li>${esc(note)}</li>`).join('');
  const stamp=Math.round((state.built_at || 0)*1000),scenes=state.scenes || [];
  $('listingPages').innerHTML=(scenes.length?`<section class="listing-scenes" aria-label="本次使用的场景图"><span class="field-label">场景图（图像模型生成，产品由遮罩锁定）</span><div>${scenes.map((row,index)=>
    `<a href="${esc(row.url)}" target="_blank" rel="noopener" title="${esc(row.title || '')}"><img src="${esc(row.url)}" alt="场景图 ${index+1}：${esc(row.title || '')}" loading="lazy"></a>`).join('')}</div></section>`:'')+pages.map((page,index)=>`<figure class="listing-page">
    <a href="${esc(page.url)}" target="_blank" rel="noopener" title="查看原图"><img src="${esc(page.url)}?t=${stamp}" alt="套图第 ${index+1} 张：${esc(kinds[page.kind]?.zh || page.kind)}" loading="lazy"></a>
    <figcaption><span>${String(index+1).padStart(2,'0')} · ${esc(kinds[page.kind]?.zh || page.kind)}${page.checks?(page.checks.passed?' · 版式通过':' · 版式需检查'):''}${listingReviewLabel(page.review)}</span><span>${page.width}×${page.height}</span><a href="${esc(page.url)}?download=1" download="${esc(page.file)}">下载</a>${page.svg?`<a href="${esc(page.svg)}" download="${esc(page.file.replace(/\.png$/,'.svg'))}" title="文字和图层都可编辑，可用 Illustrator、Figma、Inkscape 打开">可编辑 SVG</a>`:''}</figcaption>
    ${(page.notes || []).map(note=>`<p class="field-help">${esc(note)}</p>`).join('')}</figure>`).join('');
}

function listingReviewLabel(review) {
  // What the vision model's art director made of the page; a page nobody looked at is never shown as approved.
  if(!review)return '';
  const score=Number.isFinite(review.score)?` ${review.score}`:'';
  return {passed:` · 审核通过${score}`,revised:` · 审核后修改${score}`,failed:` · 审核未通过${score}`,not_verified:' · 未经视觉审核'}[review.status] || '';
}

// ---- reading the forms ----
function listingOnInput(event) {
  const node=event.target,scope=node.dataset.scope,key=node.dataset.key;if(!scope || !listingEdit)return;
  const p=listingEdit.product,index=Number(node.dataset.index);
  if(scope==='product'){p[key]=key==='size_mm'?listingNumber(node.value):node.value;listingDirty.product=true;}
  else if(scope==='feature'){p.features[index][key]=node.value;listingDirty.product=true;}
  else if(scope==='feature-part'){const parts=new Set(p.features[index].parts || []);if(node.checked)parts.add(node.value);else parts.delete(node.value);p.features[index].parts=[...parts];
    node.closest('details').querySelector('summary').textContent=`对应部件（${parts.size}）`;listingDirty.product=true;}
  else if(scope==='spec'){p.specs[index][key]=node.value;listingDirty.product=true;}
  else if(scope==='figure'){p.figures[index][key]=node.value;listingDirty.product=true;}
  else if(scope==='usecase'){p.usecase[key]=node.type==='checkbox'?node.checked:['diameter_mm','width_mm','thickness_mm','length_mm'].includes(key)?listingNumber(node.value):node.value;listingDirty.product=true;
    if(event.type==='change' && ['kind','mode'].includes(key))listingRenderUsecase();}
  else if(scope==='scenes'){p.scenes={...p.scenes,[key]:node.type==='checkbox'?node.checked:key==='count'?Number(node.value):node.value};listingDirty.product=true;}
  else if(scope==='usecase-triple'){p.usecase[key][index]=listingNumber(node.value) ?? 0;listingDirty.product=true;}
  else if(scope==='brand'){listingEdit.brand[key]=key==='badges'?node.value.split(/[,，;；\n]+/).map(text=>text.trim()).filter(Boolean):node.value;listingDirty.brand=true;}
  else if(scope==='brand-auto'){listingEdit.brand[key]=node.checked?null:listingState.brand[key+'_used'];listingDirty.brand=true;listingRenderBrand();}
  else if(scope==='page' && event.type==='change'){
    const chosen=new Set(Array.isArray(p.pages)?p.pages:listingState.plan.filter(row=>!row.skipped).map(row=>row.kind));
    if(node.checked)chosen.add(node.value);else chosen.delete(node.value);
    p.pages=(listingState.order || Object.keys(listingState.kinds)).filter(kind=>chosen.has(kind));listingDirty.product=true;listingRenderMain();
  }
}
async function listingOnChange(event) {
  const node=event.target;
  if(node.dataset.scope==='component'){
    const key=node.dataset.key,value=node.type==='checkbox'?node.checked:key==='quantity'?Number(node.value):key==='size_mm'?listingNumber(node.value):node.value;
    try{await api(`/api/projects/${listingProject}/components/${node.dataset.id}`,{method:'PUT',body:{[key]:value}});await listingLoad(false);}
    catch(error){toast(error.message,true);listingLoad(false).catch(()=>{});}
  }else if(node.id==='listingLogoFile' && node.files.length){
    const form=new FormData();form.append('file',node.files[0]);
    try{listingAdopt(await api(`/api/projects/${listingProject}/listing/logo`,{method:'POST',body:form}),false);listingRenderBrand();toast('标志已上传');}catch(error){toast(error.message,true);}
  }else listingOnInput(event);
}
async function listingSaveProduct(quiet) {
  const p=listingEdit.product;
  const body={title:p.title,model:p.model,headline:p.headline,subline:p.subline,source:p.source || '',language:p.language,size_mm:p.size_mm,features:p.features.filter(row=>row.title.trim()),
    specs:p.specs.filter(row=>row.label.trim() && row.value.trim()),figures:p.figures.filter(row=>row.value.trim() && row.label.trim()),pages:p.pages,usecase:p.usecase,scenes:p.scenes,aspect:p.aspect};
  const state=await api(`/api/projects/${listingProject}/listing/product`,{method:'PUT',body});
  listingDirty.product=false;listingAdopt(state,false);if(!quiet){listingRenderPanel();toast('已保存');}
}
async function listingSaveBrand(quiet) {
  const b=listingEdit.brand;
  const state=await api(`/api/projects/${listingProject}/listing/brand`,{method:'PUT',body:{preset:b.preset,name:b.name,accent:b.accent,dark:b.dark,badges:b.badges,legal:b.legal,unit:b.unit,fonts:b.fonts,badge_style:b.badge_style,texture:b.texture}});
  listingDirty.brand=false;listingAdopt(state,false);if(!quiet){listingRenderPanel();toast('已保存');}
}
async function listingSavePending() { if(listingDirty.product)await listingSaveProduct(true);if(listingDirty.brand)await listingSaveBrand(true); }
async function listingBuildNow(fresh=false) {
  if(listingBusy)return;listingBusy=true;listingRenderMain();
  try{
    await listingSavePending();
    await api(`/api/projects/${listingProject}/listing/build`,{method:'POST',body:fresh?{fresh:true}:{}});
    await listingLoad(false);
  }catch(error){toast(error.message,true);}
  finally{listingBusy=false;listingRenderMain();}
}
async function listingOnClick(event) {
  const tab=event.target.closest('[data-listing-tab]');
  if(tab){listingTab=tab.dataset.listingTab;listingRenderPanel();return;}
  const button=event.target.closest('[data-action]');if(!button || !listingEdit)return;
  const action=button.dataset.action,p=listingEdit.product,index=Number(button.dataset.index),id=button.dataset.id;
  try{
    if(action==='add-feature'){if(p.features.length<8){p.features.push({title:'',detail:'',parts:[],icon:''});listingDirty.product=true;listingRenderProduct();}}
    else if(action==='add-spec'){if(p.specs.length<10){p.specs.push({label:'',value:''});listingDirty.product=true;listingRenderProduct();}}
    else if(action==='add-figure'){if(p.figures.length<3){p.figures.push({value:'',unit:'',label:''});listingDirty.product=true;listingRenderProduct();}}
    else if(action.startsWith('remove-')){p[{feature:'features',spec:'specs',figure:'figures'}[action.slice(7)]].splice(index,1);listingDirty.product=true;listingRenderProduct();}
    else if(action==='save-product')await listingSaveProduct(false);
    else if(action==='save-brand')await listingSaveBrand(false);
    else if(action==='pages-auto'){p.pages=null;listingDirty.product=true;await listingSaveProduct(true);}
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
      button.disabled=true;button.setAttribute('aria-busy','true');
      const id=listingProject;
      try{await listingSavePending();
        const state=await api(`/api/projects/${id}/listing/draft`,{method:'POST',body:{hint:($('listingSource')?.value || '').trim().slice(0,400),language:p.language}});
        if(listingOpenFor()!==id)return;
        listingDirty.product=false;listingAdopt(state,true);toast(state.product.drafted?.source==='ai'?'已由 AI 起草':'已按部件名称起草');}
      finally{button.disabled=false;button.removeAttribute('aria-busy');}
    }
    else if(action==='component-turn'){await api(`/api/projects/${listingProject}/components/${id}`,{method:'PUT',body:{turn:button.dataset.turn}});await listingLoad(false);}
    else if(action==='component-refresh'){await api(`/api/projects/${listingProject}/components/${id}/refresh`,{method:'POST'});await listingLoad(false);}
    else if(action==='component-remove'){await api(`/api/projects/${listingProject}/components/${id}`,{method:'DELETE'});await listingLoad(false);}
    else if(action==='articulation-run'){
      button.disabled=true;button.textContent='识别中…';
      const id=listingProject,data=await api(`/api/projects/${id}/articulation`,{method:'POST',body:{}});
      if(listingOpenFor()!==id)return;
      listingArticulation={project:id,data};listingRenderArticulation(false);toast('已开始重新识别活动部件');
    }
    else if(action==='logo-remove'){listingAdopt(await api(`/api/projects/${listingProject}/listing/logo`,{method:'DELETE'}),false);listingRenderBrand();}
  }catch(error){toast(error.message,true);}
}
function listingInit() {
  const panel=$('inspectorListing');if(!panel)return;
  if($('listingOpen'))$('listingOpen').onclick=()=>listingOpen();
  panel.addEventListener('click',listingOnClick);
  panel.addEventListener('input',listingOnInput);
  panel.addEventListener('change',event=>{listingOnChange(event);});
  $('listingBuild').onclick=()=>listingBuildNow();
  if($('listingRewrite'))$('listingRewrite').onclick=()=>listingBuildNow(true);
  $('listingCancel').onclick=async()=>{const task=listingState?.task;if(!task)return;try{await api(`/api/projects/${listingProject}/listing/${task.id}/cancel`,{method:'POST'});await listingLoad(false);}catch(error){toast(error.message,true);}};
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',listingInit);else listingInit();
