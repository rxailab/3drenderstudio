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

async function listingOpen() {
  if(!current)return;
  listingProject=current.id;listingTab='product';listingDirty.product=listingDirty.brand=false;
  $('listingPanel').innerHTML='<p class="field-help">正在读取…</p>';$('listingPages').innerHTML='';
  if(!$('listingDialog').open)$('listingDialog').showModal();
  try{await listingLoad(true);}catch(error){$('listingPanel').innerHTML=`<p class="field-error">${esc(error.message)}</p>`;}
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
    badges:[...state.brand.badges],legal:state.brand.legal,unit:state.brand.unit}};
  if(first)listingRenderPanel();else if(listingTab==='components')listingRenderComponents();
  listingRenderMain();listingSchedule();
}
function listingSchedule() {
  clearTimeout(listingTimer);
  if($('listingDialog').open && (listingTaskActive() || listingComponentsBusy()))listingTimer=setTimeout(()=>listingLoad(false).catch(()=>listingSchedule()),1500);
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
    <div class="listing-grid2">${listingField('产品名称',listingInput('product','title',p.title,'maxlength="60"'))}${listingField('型号',listingInput('product','model',p.model,'maxlength="40" placeholder="如 AG-20X"'))}</div>
    <div class="listing-grid2">${listingField('图片文字语言',`<select data-scope="product" data-key="language"><option value="en" ${p.language==='en'?'selected':''}>English</option><option value="zh" ${p.language==='zh'?'selected':''}>中文</option></select>`)}
    ${listingField('最长边真实尺寸（mm）',listingInput('product','size_mm',p.size_mm ?? '',`inputmode="decimal" placeholder="按模型估计约 ${esc(Math.round(size.mm))}"`),size.known?'':'填写后才会画出产品尺寸线，套装里的大小比例也才准确。')}</div>
    ${listingField('主标题',listingInput('product','headline',p.headline,'maxlength="60" placeholder="一句话说清最重要的卖点"'))}
    ${listingField('副标题',listingInput('product','subline',p.subline,'maxlength="120"'))}
    <div class="listing-draft"><input id="listingHint" type="text" maxlength="400" placeholder="可写下要强调的卖点和规格，如：20V 无刷电机，115 mm 磨片"><button type="button" class="button subtle" data-action="draft">起草文案</button></div>
    <p class="field-help">${drafted?`上次起草：${drafted.source==='ai'?'由 AI 根据模型与说明撰写':'按部件名称规则生成'}。`:''}起草只填写空着的内容；数字只采用你写下的。${(drafted?.notes || []).map(esc).join(' ')}</p>
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
  $('listingPanel').innerHTML=`<p class="field-help">产品之外随箱附带的东西：电池、充电器、工具箱……每个组件上传一个模型（OBJ / GLB / ZIP），或一张白底 / 透明背景的图片。模型会自动分析、摆正并渲染成抠图。</p>
    <form id="listingComponentForm" class="listing-upload">
      <input id="listingComponentFiles" type="file" multiple accept=".glb,.obj,.mtl,.zip,.png,.jpg,.jpeg,.webp" aria-label="选择组件文件">
      <div class="listing-grid3"><input id="listingComponentName" type="text" maxlength="60" placeholder="名称（可不填）" aria-label="新组件名称">
      <input id="listingComponentSku" type="text" maxlength="40" placeholder="型号" aria-label="新组件型号">
      <input id="listingComponentQuantity" type="text" inputmode="numeric" placeholder="数量" aria-label="新组件数量"></div>
      <button type="submit" class="button secondary">添加组件</button>
    </form>
    <div class="listing-components">${rows.map(listingComponentHTML).join('') || '<p class="field-help">还没有组件。</p>'}</div>`;
}
function listingRenderBrand() {
  const b=listingEdit.brand,used=listingState.brand;
  $('listingPanel').innerHTML=`
    <fieldset class="listing-presets"><legend class="field-label">风格</legend>${listingState.presets.map(preset=>`<label class="listing-preset" data-ground="${esc(preset.ground)}">
      <input type="radio" name="listingPreset" data-scope="brand" data-key="preset" value="${esc(preset.id)}" ${b.preset===preset.id?'checked':''}>
      <strong>${esc(preset.label.zh)}</strong><span>${esc(preset.note.zh)}</span></label>`).join('')}</fieldset>
    ${listingField('品牌名称',listingInput('brand','name',b.name,'maxlength="40" placeholder="显示在页眉，可不填"'))}
    <div class="listing-grid2">
      <label class="listing-field"><span class="field-label">品牌色</span><span class="listing-color"><input type="color" data-scope="brand" data-key="accent" value="${esc(b.accent || used.accent_used)}" ${b.accent?'':'disabled'} aria-label="品牌色">
        <label class="listing-check"><input type="checkbox" data-scope="brand-auto" data-key="accent" ${b.accent?'':'checked'}> 跟随产品颜色</label></span>
        <span class="field-help">${b.accent?'':'当前 '+esc(used.accent_used)+(used.accent_source==='product'?'（取自产品）':'（默认）')}</span></label>
      <label class="listing-field"><span class="field-label">深色</span><span class="listing-color"><input type="color" data-scope="brand" data-key="dark" value="${esc(b.dark || used.dark_used)}" ${b.dark?'':'disabled'} aria-label="深色">
        <label class="listing-check"><input type="checkbox" data-scope="brand-auto" data-key="dark" ${b.dark?'':'checked'}> 默认</label></span></label>
    </div>
    ${listingField('徽标文字（最多 3 个，用逗号分隔）',listingInput('brand','badges',(b.badges || []).join(', '),'placeholder="如 20V, PRO"'),'第一个显示在没有标题的页眉里。')}
    ${listingField('页脚说明文字',listingInput('brand','legal',b.legal,'maxlength="200" placeholder="如 *Maximum initial battery voltage…"'))}
    ${listingField('尺寸单位',`<select data-scope="brand" data-key="unit"><option value="mm" ${b.unit==='mm'?'selected':''}>毫米 mm</option><option value="in" ${b.unit==='in'?'selected':''}>英寸 in</option></select>`)}
    <div class="listing-logo"><span class="field-label">标志图片</span>${used.logo?`<img src="${esc(used.logo)}" alt="品牌标志"><button type="button" class="button subtle" data-action="logo-remove">移除</button>`:'<span class="field-help">未上传；页眉显示品牌名称文字。</span>'}
      <input id="listingLogoFile" type="file" accept=".png,.jpg,.jpeg,.webp" aria-label="上传标志图片"></div>
    <p class="field-help">风格只决定排版方式；系统不内置任何品牌的标志或名称，请只使用你自己的品牌素材。</p>
    <div class="listing-save"><button type="button" class="button secondary" data-action="save-brand">保存品牌风格</button></div>`;
}
function listingRenderUsecase() {
  const u=listingEdit.product.usecase,o=listingState.options,round=['pipe','rod'].includes(u.kind);
  const select=(key,values,names)=>`<select data-scope="usecase" data-key="${key}">${values.map(value=>`<option value="${esc(value)}" ${u[key]===value?'selected':''}>${esc(names[value] || value)}</option>`).join('')}</select>`;
  const number=(key,placeholder)=>`<input type="text" inputmode="decimal" data-scope="usecase" data-key="${key}" value="${esc(u[key] ?? '')}" placeholder="${esc(placeholder)}">`;
  const triple=(key,labels)=>`<span class="listing-grid3">${labels.map((label,index)=>`<input type="text" inputmode="decimal" data-scope="usecase-triple" data-key="${key}" data-index="${index}" value="${esc(u[key][index])}" aria-label="${esc(label)}" title="${esc(label)}">`).join('')}</span>`;
  $('listingPanel').innerHTML=`
    <p class="field-help">把产品和它加工的工件放在一张图里，标出尺寸和功能，例如割管器套在一根白色塑料管上。工件由程序按尺寸生成，位置由模型上量到的开口决定，也可以手动调整。</p>
    <label class="listing-check listing-toggle"><input type="checkbox" data-scope="usecase" data-key="enabled" ${u.enabled?'checked':''}> 生成使用场景标注图</label>
    <div class="listing-grid2">${listingField('工件',select('kind',o.workpieces,o.workpiece_names))}${listingField('材质',select('material',o.materials,o.material_names))}</div>
    ${listingField('位置',select('mode',o.modes,listingModeNames),u.mode==='through'?'在模型上寻找被工具三面包住的开口，工件从中穿过；找不到时放在工具中心。':u.mode==='front'?'工件立在工具长轴的一端；方向不对时改选另一端。':'')}
    ${u.mode==='front'?listingField('哪一端',select('side',['+','-'],{'+':'正向一端','-':'反向一端'})):''}
    <div class="listing-grid3">${round?listingField('直径 mm',number('diameter_mm','自动')):listingField('厚度 mm',number('thickness_mm','自动'))}
      ${round?'':listingField('宽度 mm',number('width_mm','自动'))}${listingField('长度 mm',number('length_mm','自动'))}</div>
    ${listingField('位置微调 X / Y / Z（mm）',triple('offset_mm',['X 偏移','Y 偏移','Z 偏移']))}
    ${listingField('工件旋转 X / Y / Z（度）',triple('rotate',['绕 X 旋转','绕 Y 旋转','绕 Z 旋转']))}
    ${listingField('这张图的标题',listingInput('usecase','headline',u.headline,'maxlength="60" placeholder="留空则写「In use · 工件名称」"'))}
    <p class="field-help">图上的工件尺寸是你在这里选的尺寸；产品尺寸线只在填写了真实尺寸时画出。</p>
    <div class="listing-save"><button type="button" class="button secondary" data-action="save-product">保存使用场景</button></div>`;
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
  const build=$('listingBuild');build.disabled=active || listingBusy || !state.ready || !state.available;
  build.textContent=pages.length?'重新生成套图':'生成套图';
  $('listingCancel').hidden=!active;
  const download=$('listingDownload');download.hidden=!pages.length || active;download.href=`/api/projects/${encodeURIComponent(listingProject)}/listing/download`;
  const progress=$('listingProgress');progress.hidden=!active;
  if(active){const value=Math.max(0,Math.min(100,Math.round(task.progress || 0)));$('listingProgressBar').style.width=value+'%';progress.setAttribute('aria-valuenow',String(value));}
  $('listingStatus').textContent=!state.available?'渲染组件不可用，无法生成套图。':!state.ready?'模型分析完成后才能生成套图。'
    :active?`${task.detail || (task.status==='queued'?'排队中':'正在生成')} · ${Math.round(task.progress || 0)}%`
    :task?.status==='failed'?`上次生成失败：${task.error || ''}`:task?.status==='cancelled'?'上次生成已取消。'
    :pages.length?`共 ${pages.length} 张，${new Date((state.built_at || 0)*1000).toLocaleString('zh-CN',{hour12:false})} 生成。`:'还没有生成套图。';
  const skipped=state.plan.filter(row=>row.skipped).map(row=>`「${kinds[row.kind].zh}」暂时无法生成：${row.skipped}。`);
  $('listingNotes').innerHTML=[...(state.notes || []),...skipped,...(listingComponentsBusy()?['有组件还在处理，完成后再生成会把它们放进套图。']:[])].map(note=>`<li>${esc(note)}</li>`).join('');
  const stamp=Math.round((state.built_at || 0)*1000);
  $('listingPages').innerHTML=pages.map((page,index)=>`<figure class="listing-page">
    <a href="${esc(page.url)}" target="_blank" rel="noopener" title="查看原图"><img src="${esc(page.url)}?t=${stamp}" alt="套图第 ${index+1} 张：${esc(kinds[page.kind]?.zh || page.kind)}" loading="lazy"></a>
    <figcaption><span>${String(index+1).padStart(2,'0')} · ${esc(kinds[page.kind]?.zh || page.kind)}</span><span>${page.width}×${page.height}</span><a href="${esc(page.url)}?download=1" download="${esc(page.file)}">下载</a></figcaption>
    ${(page.notes || []).map(note=>`<p class="field-help">${esc(note)}</p>`).join('')}</figure>`).join('');
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
  const body={title:p.title,model:p.model,headline:p.headline,subline:p.subline,language:p.language,size_mm:p.size_mm,features:p.features.filter(row=>row.title.trim()),
    specs:p.specs.filter(row=>row.label.trim() && row.value.trim()),figures:p.figures.filter(row=>row.value.trim() && row.label.trim()),pages:p.pages,usecase:p.usecase};
  const state=await api(`/api/projects/${listingProject}/listing/product`,{method:'PUT',body});
  listingDirty.product=false;listingAdopt(state,false);if(!quiet){listingRenderPanel();toast('已保存');}
}
async function listingSaveBrand(quiet) {
  const b=listingEdit.brand;
  const state=await api(`/api/projects/${listingProject}/listing/brand`,{method:'PUT',body:{preset:b.preset,name:b.name,accent:b.accent,dark:b.dark,badges:b.badges,legal:b.legal,unit:b.unit}});
  listingDirty.brand=false;listingAdopt(state,false);if(!quiet){listingRenderPanel();toast('已保存');}
}
async function listingSavePending() { if(listingDirty.product)await listingSaveProduct(true);if(listingDirty.brand)await listingSaveBrand(true); }
async function listingBuildNow() {
  if(listingBusy)return;listingBusy=true;listingRenderMain();
  try{
    await listingSavePending();
    await api(`/api/projects/${listingProject}/listing/build`,{method:'POST',body:{}});
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
    else if(action==='draft'){
      button.disabled=true;button.setAttribute('aria-busy','true');
      try{if(listingDirty.product)await listingSaveProduct(true);
        const state=await api(`/api/projects/${listingProject}/listing/draft`,{method:'POST',body:{hint:$('listingHint').value.trim(),language:p.language}});
        listingDirty.product=false;listingAdopt(state,true);toast(state.product.drafted?.source==='ai'?'已由 AI 起草':'已按部件名称起草');}
      finally{button.disabled=false;button.removeAttribute('aria-busy');}
    }
    else if(action==='component-turn'){await api(`/api/projects/${listingProject}/components/${id}`,{method:'PUT',body:{turn:button.dataset.turn}});await listingLoad(false);}
    else if(action==='component-refresh'){await api(`/api/projects/${listingProject}/components/${id}/refresh`,{method:'POST'});await listingLoad(false);}
    else if(action==='component-remove'){await api(`/api/projects/${listingProject}/components/${id}`,{method:'DELETE'});await listingLoad(false);}
    else if(action==='logo-remove'){listingAdopt(await api(`/api/projects/${listingProject}/listing/logo`,{method:'DELETE'}),false);listingRenderBrand();}
  }catch(error){toast(error.message,true);}
}
async function listingAddComponent(event) {
  event.preventDefault();
  const files=[...$('listingComponentFiles').files];if(!files.length)return toast('请选择组件的模型或图片文件',true);
  const form=new FormData();for(const file of files)form.append('files',file);
  form.append('paths',JSON.stringify(files.map(file=>file.webkitRelativePath || file.name)));
  for(const [key,id] of [['name','listingComponentName'],['sku','listingComponentSku'],['quantity','listingComponentQuantity']])if($(id).value.trim())form.append(key,$(id).value.trim());
  const submit=event.target.querySelector('button[type="submit"]');submit.disabled=true;
  try{await api(`/api/projects/${listingProject}/components`,{method:'POST',body:form});event.target.reset();await listingLoad(false);toast('组件已添加，正在处理');}
  catch(error){toast(error.message,true);}finally{submit.disabled=false;}
}
function listingInit() {
  const dialog=$('listingDialog');if(!dialog)return;
  $('listingOpen').onclick=()=>listingOpen();
  dialog.addEventListener('click',listingOnClick);
  dialog.addEventListener('input',listingOnInput);
  dialog.addEventListener('change',event=>{listingOnChange(event);});
  dialog.addEventListener('submit',event=>{if(event.target.id==='listingComponentForm')listingAddComponent(event);});
  dialog.addEventListener('close',()=>{clearTimeout(listingTimer);if(listingOpenFor())listingSavePending().catch(error=>toast(error.message,true));});
  $('listingBuild').onclick=()=>listingBuildNow();
  $('listingCancel').onclick=async()=>{const task=listingState?.task;if(!task)return;try{await api(`/api/projects/${listingProject}/listing/${task.id}/cancel`,{method:'POST'});await listingLoad(false);}catch(error){toast(error.message,true);}};
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',listingInit);else listingInit();
