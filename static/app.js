'use strict';
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const clone = value => JSON.parse(JSON.stringify(value));
const presets = {matte_plastic:'哑光塑料',glossy_plastic:'亮面塑料',painted_metal:'喷涂金属',brushed_metal:'拉丝金属',polished_metal:'抛光金属',glass:'玻璃',rubber:'橡胶',fabric:'织物',leather:'皮革',wood:'木材',ceramic:'陶瓷'};
const statusNames = {analyzing:'模型分析中',ready:'可开始拍摄',failed:'需要处理',queued:'等待执行',running:'正在执行',completed:'已完成',cancelled:'已取消',interrupted:'等待继续'};
const sourceNames = {rules:'规则推导',user:'用户指定',ai:'AI 分析',model:'模型自带',fallback:'占位材质',default:'默认材质'};
let current = null, draft = null, allProjects = [], activeShot = null, selectedShots = new Set(), selectedMaterial = null, cameraShot = null;
let dirty = false, busy = false, polling = false, system = null, uploadFiles = [], toastTimer, syncSignature = '', currentOutput = null;
let serviceDisconnected = false;
let projectRequest = 0, projectLoading = false, planSavePromise = null, draftBaseVersion = null;
let uploadSubmitting = false, decisionOpen = false, projectDeletionId = '', libraryRequest = 0;
const deletedProjectIds = new Set();
const confirmedRenders = new Set();
const instructionDrafts = new Map(), instructionAttempts = new Map(), instructionReceipts = new Map(), instructionGuidance = new Map();
let instructionTargetSelection = null;
const instructionJournalKey='wuying-instruction-attempts-v1', instructionJournalLimit=50;
const instructionPreflightDraftKey='wuying-instruction-preflight-drafts-v1', instructionPreflightDrafts=new Map();
let instructionPreflightPending=null;
const unfinishedInstructionPhases=new Set(['requesting','unknown','saved','preview_submitted']);
let instructionSubmitting = false, instructionProjectId = '', pendingInstructionPreview = null, reviewErrorKey = '', reviewRevision = 0;
const heroOptimizationAttempts=new Map(),heroOptimizationErrors=new Map();
const heroOptimizationJournalKey='wuying-hero-optimization-attempts-v1';
let heroOptimizationSubmitting=false;
function remember(key,value) { try {localStorage.setItem(key,value);} catch (_) {} }
function recalled(key) { try {return localStorage.getItem(key);} catch (_) {return null;} }
function forget(key) { try {localStorage.removeItem(key);} catch (_) {} }
function workerRenderReady() { return !!system?.available && !serviceDisconnected; }
function heroOptimizationReady() { return system?.runtime?.capabilities?.hero_optimize===true && !serviceDisconnected; }
function renderReadyForShot(id) { return !serviceDisconnected && (!!system?.available || !!current?.plan?.scene_checkpoints?.[id] && heroOptimizationReady()); }
function renderReady() { return renderReadyForShot(activeShot); }
function runtimeReason() { return serviceDisconnected?'本机服务连接中断，请确认应用仍在运行。':system?.runtime?.message || (!system?'正在检查本机运行环境…':!system.available?'请先安装或配置 Blender，然后查看运行环境。':''); }
function projectWritePending() { return busy || !!planSavePromise || realismBusy || materialAgentSubmitting || sceneExportSubmitting || uploadSubmitting || !!projectDeletionId; }
function navigationBlocked() { return projectWritePending() || projectLoading; }
function setText(id,text) { if($(id))$(id).textContent=text; }
function stableHTML(element,html) {
  if(element._renderedHTML===html)return;
  const focus=document.activeElement,within=element.contains(focus),attributes=within?[...focus.attributes].filter(a=>a.name==='id' || a.name.startsWith('data-')).map(a=>[a.name,a.value]):[];
  element.innerHTML=html;element._renderedHTML=html;
  if(within && attributes.length){const replacement=[...element.querySelectorAll('button,input,a,summary')].find(node=>attributes.every(([key,value])=>node.getAttribute(key)===value));replacement?.focus({preventScroll:true});}
}
function chooseAction(id,title,description,actions) {
  if(decisionOpen)return Promise.resolve('cancel');
  decisionOpen=true;
  return new Promise(resolve=>{
    let dialog=$(id);if(!dialog){dialog=document.createElement('dialog');dialog.id=id;dialog.className='action-dialog';document.body.appendChild(dialog);}
    dialog.setAttribute('aria-labelledby',id+'Title');dialog.setAttribute('aria-describedby',id+'Description');
    dialog.innerHTML=`<div class="dialog-header"><h2 id="${id}Title">${esc(title)}</h2><button type="button" class="icon-button" data-choice="cancel" aria-label="关闭">×</button></div><div id="${id}Description" class="action-dialog-summary">${description}</div><div class="dialog-footer action-dialog-actions">${actions.map(action=>`<button type="button" class="button ${action.danger?'danger':action.primary?'primary':'secondary'}" data-choice="${esc(action.value)}">${esc(action.label)}</button>`).join('')}</div>`;
    let result='cancel';dialog.onclick=event=>{const button=event.target.closest('[data-choice]');if(button){result=button.dataset.choice;dialog.close();}};
    dialog.onclose=()=>{decisionOpen=false;resolve(result);};dialog.showModal();
    dialog.querySelector('[data-choice="cancel"]')?.focus();
  });
}
async function resolveUnsavedChanges() {
  if(!dirty)return true;
  const choice=await chooseAction('unsavedDialog','如何处理当前调整？','<p>当前项目有尚未保存的摄影参数。保存后再继续，或仅放弃这次草稿。</p>',[{value:'cancel',label:'留在此项目'},{value:'discard',label:'放弃本次调整'},{value:'save',label:'保存并继续',primary:true}]);
  if(choice==='save'){try{await savePlan(true);return !dirty;}catch(error){toast(error.message,true);return false;}}
  if(choice==='discard'){syncSettings();renderProject();return true;}
  return false;
}
function renderWorkflow() {
  let title='',description='';
  if(!renderReady()){title=serviceDisconnected?'本机服务已断开':'渲染环境需要处理';description=runtimeReason();}
  else if(current && !current.analysis && ['failed','cancelled','interrupted'].includes(current.status)){
    title=current.status==='cancelled'?'模型分析已取消':current.status==='interrupted'?'模型分析已中断':'模型分析未完成';
    description='查看任务原因，处理后继续或重试。';
  }else if(current?.initial_preview?.status==='failed' && !current.initial_preview.job_id && !latestOutput('hero')){title='白底主图未能自动开始';description=current.initial_preview.error || '请查看运行环境，然后点击“预览当前镜头”重试。';}
  else if(dirty && current?.version!==draftBaseVersion){title='方案已在后台更新';description='当前调整仍保留。记录需要的参数后，放弃本次调整以载入最新方案。';}
  $('workspaceHint').hidden=!title;$('workspaceHint').dataset.tone=title?'error':'';
  setText('workflowTitle',title);setText('workflowDescription',description);
  $('runtimeHelp').hidden=renderReady();
  setText('planState',!current?'':planSavePromise?'正在保存…':dirty?'有未保存调整':'已保存');
  $('planState').hidden=!current;$('planState').classList.toggle('is-dirty',dirty);
  $('planSaveActions').hidden=!dirty;$('discardPlan').disabled=busy || !!planSavePromise;
}
function persistInstructionJournal() {
  const attempts=[...instructionAttempts.values()].filter(item=>unfinishedInstructionPhases.has(item.phase));
  // Never evict an uncertain request to make room for a new request key.
  if(attempts.length>instructionJournalLimit)return;
  try{
    if(!attempts.length){sessionStorage.removeItem(instructionJournalKey);return;}
    sessionStorage.setItem(instructionJournalKey,JSON.stringify({schema:1,attempts:attempts.map(item=>({projectId:item.projectId,text:item.text,shotId:item.shotId,shotIds:item.shotIds,body:item.body,instructionKey:item.instructionKey,previewKey:item.previewKey,phase:item.phase,confirmed:item.confirmed,version:item.version,submitted:item.submitted,...(item.kind==='confirm'?{kind:'confirm'}:{}),...(typeof item.remainingText==='string'?{remainingText:item.remainingText}:{})}))}));
  }catch(_){}
}
function persistInstructionPreflightDrafts() {
  try {
    if(!instructionPreflightDrafts.size)sessionStorage.removeItem(instructionPreflightDraftKey);
    else sessionStorage.setItem(instructionPreflightDraftKey,JSON.stringify([...instructionPreflightDrafts]));
  } catch (_) {}
}
function restoreInstructionPreflightDrafts() {
  try {
    const raw=sessionStorage.getItem(instructionPreflightDraftKey);if(!raw || raw.length>120000)return;
    const records=JSON.parse(raw);if(!Array.isArray(records) || records.length>instructionJournalLimit)return;
    for(const record of records){
      if(!Array.isArray(record) || record.length!==2 || typeof record[0]!=='string' || !/^[A-Za-z0-9_-]{1,128}$/.test(record[0]) || typeof record[1]!=='string' || !record[1].trim() || record[1].length>2000)continue;
      instructionPreflightDrafts.set(record[0],record[1]);instructionDrafts.set(record[0],record[1]);
    }
  } catch (_) {}
}
function validInstructionTargetPoints(points) {
  return Array.isArray(points) && points.length>=1 && points.length<=12 && points.every(point=>point && typeof point==='object' && !Array.isArray(point) && Object.keys(point).sort().join(',')==='id,x,y' && typeof point.id==='string' && /^[A-Za-z0-9_-]{1,64}$/.test(point.id) && ['x','y'].every(key=>typeof point[key]==='number' && Number.isFinite(point[key]) && point[key]>=0 && point[key]<=1)) && new Set(points.map(point=>point.id)).size===points.length;
}
function restoreInstructionJournal() {
  try{
    const raw=sessionStorage.getItem(instructionJournalKey);if(!raw || raw.length>250000)return;
    const journal=JSON.parse(raw);if(journal?.schema!==1 || !Array.isArray(journal.attempts) || journal.attempts.length>instructionJournalLimit)return;
    const ident=value=>typeof value==='string' && /^[A-Za-z0-9_-]{1,128}$/.test(value),positive=value=>Number.isInteger(value) && value>0;
    for(const item of journal.attempts){
      const body=item?.body,source=body?.source,confirm=item?.kind==='confirm';
      if(!item || !ident(item.projectId) || !ident(item.shotId) || !ident(item.instructionKey) || !ident(item.previewKey) || item.instructionKey===item.previewKey || typeof item.text!=='string' || !item.text.trim() || item.text.length>2000 || !unfinishedInstructionPhases.has(item.phase) || typeof item.confirmed!=='boolean' || typeof item.submitted!=='boolean')continue;
      if(item.kind!==undefined && item.kind!=='confirm')continue;
      if(!body || typeof body!=='object' || Array.isArray(body) || !positive(body.expected_version))continue;
      if(confirm){if(Object.keys(body).sort().join(',')!=='expected_version,proposal_id' || !ident(body.proposal_id))continue;}
      else if(Object.keys(body).sort().join(',')!==(Object.hasOwn(body,'target_points')?'expected_version,shot_id,source,target_points,text':'expected_version,shot_id,source,text') || body.text!==item.text || body.shot_id!==item.shotId || (source!==null && (!source || typeof source!=='object' || Array.isArray(source) || Object.keys(source).sort().join(',')!=='output_id,quality,shot_id,version' || !ident(source.output_id) || source.shot_id!==item.shotId || !['preview','standard','fine','hero'].includes(source.quality) || !positive(source.version))) || (Object.hasOwn(body,'target_points') && (!source || source.version!==body.expected_version || !validInstructionTargetPoints(body.target_points))))continue;
      if(item.remainingText!==undefined && (!item.confirmed || typeof item.remainingText!=='string' || !item.remainingText.trim() || item.remainingText.length>2000 || item.remainingText.trim()===item.text.trim()))continue;
      if(!Array.isArray(item.shotIds) || !item.shotIds.length || item.shotIds.length>100 || !item.shotIds.every(ident) || new Set(item.shotIds).size!==item.shotIds.length || (!item.confirmed && (item.shotIds.length!==1 || item.shotIds[0]!==item.shotId)) || (item.confirmed?!positive(item.version):item.version!==null) || (item.confirmed?!['saved','preview_submitted'].includes(item.phase):!['requesting','unknown'].includes(item.phase)) || (item.submitted && !item.confirmed))continue;
      const restored={projectId:item.projectId,text:item.text,shotId:item.shotId,shotIds:[...item.shotIds],body:clone(body),instructionKey:item.instructionKey,previewKey:item.previewKey,phase:item.phase==='requesting'?'unknown':item.phase,confirmed:item.confirmed,version:item.version,submitted:item.submitted,...(confirm?{kind:'confirm'}:{}),...(item.remainingText!==undefined?{remainingText:item.remainingText}:{})};
      instructionAttempts.set(restored.instructionKey,restored);instructionDrafts.set(restored.projectId,instructionAttemptText(restored));
    }
  }catch(_){}
}
function instructionAttemptText(attempt) { return typeof attempt?.remainingText==='string'?attempt.remainingText:attempt?.text; }
function clearAppliedInstruction(attempt,clearError=true) {
  if(!attempt)return;
  if(instructionPreflightDrafts.get(attempt.projectId)===attempt.text){instructionPreflightDrafts.delete(attempt.projectId);persistInstructionPreflightDrafts();}
  const matches=value=>[attempt.text,instructionAttemptText(attempt)].includes((value || '').trim()),remaining=attempt.remainingText || '';
  if(matches(instructionDrafts.get(attempt.projectId))){if(remaining)instructionDrafts.set(attempt.projectId,remaining);else instructionDrafts.delete(attempt.projectId);}
  if(current?.id===attempt.projectId && matches($('instructionInput').value)){
    $('instructionInput').value=remaining;
    if(clearError){$('instructionError').textContent='';$('instructionError').hidden=true;}
  }
}
function currentInstructionAttempt() {
  const text=$('instructionInput').value.trim();
  if(pendingInstructionPreview && (pendingInstructionPreview.projectId!==current?.id || instructionAttemptText(pendingInstructionPreview)!==text)){
    if(pendingInstructionPreview.confirmed){pendingInstructionPreview.phase='obsolete';persistInstructionJournal();}
    pendingInstructionPreview=null;
  }
  const attempt=pendingInstructionPreview || [...instructionAttempts.values()].reverse().find(item=>item.projectId===current?.id && instructionAttemptText(item)===text && unfinishedInstructionPhases.has(item.phase));
  if(!attempt)return null;
  // An unacknowledged request retains its original key and body even if a poll
  // reveals a newer version. Only the server may resolve whether it committed.
  if(attempt.confirmed && (attempt.version!==current?.version || dirty)){
    attempt.phase='obsolete';clearAppliedInstruction(attempt,false);pendingInstructionPreview=null;persistInstructionJournal();return null;
  }
  pendingInstructionPreview=attempt;return attempt;
}
const instructionFieldLabels={coat:'清漆层',exposure:'曝光',light_softness:'反光柔和度',margin:'画面留白',style:'棚拍风格',aspect:'画幅比例',azimuth:'水平角',elevation:'俯仰角',focal_length:'焦距',target_object:'构图目标',rotation:'旋转角度',translation:'位移',texture_scale:'纹理尺度',texture_rotation:'纹理方向',color:'颜色',roughness:'粗糙度',metallic:'金属度',transmission:'透射',preset:'材质预设',key_power:'主光强度',fill_power:'补光强度',rim_power:'轮廓光强度',environment:'环境光强度',key_azimuth:'主光方位角',key_elevation:'主光俯仰角',fill_azimuth:'补光方位角',fill_elevation:'补光俯仰角',rim_azimuth:'轮廓光方位角',rim_elevation:'轮廓光俯仰角',exposure_delta:'曝光修正',softness_multiplier:'柔和度倍率',time_budget:'采样预算',visual_review:'自动检查',max_corrections:'自动修正次数',frame:'动画帧'};
Object.assign(instructionFieldLabels,{projection:'投影方式',ortho_scale:'取景宽度',camera_distance:'镜头距离',target_offset:'画面平移'});
function instructionValue(value,key='') {
  if(key==='projection')return value==='perspective'?'透视':value==='orthographic'?'正交':value==null?'正交（默认）':String(value);
  if(value===undefined || value===null)return /^(key|fill|rim)_(azimuth|elevation)$/.test(key)?'沿用原始布光':'未设置';
  if(typeof value==='boolean')return value?'开启':'关闭';
  if(typeof value==='number')return String(Number(value.toPrecision(6)))+(['exposure','exposure_delta'].includes(key)?' EV':['azimuth','elevation','texture_rotation'].includes(key) || /^(key|fill|rim)_(azimuth|elevation)$/.test(key)?'°':key==='focal_length'?' mm':'');
  if(key==='style')return {white:'白底',soft:'浅色柔光',dark:'深色棚拍'}[value] || String(value);
  if(key==='preset')return presets[value] || String(value);
  if(Array.isArray(value) && value.every(item=>typeof item!=='object'))return value.map(item=>instructionValue(item)).join('、');
  if(typeof value==='object')return JSON.stringify(value);
  return String(value);
}
function instructionDiffRows(receipt) {
  const rows=[],changes=Array.isArray(receipt?.changes)?receipt.changes:[];
  const add=(path,before,after,scope)=>{
    const key=String(path).split('.').at(-1);
    rows.push({label:instructionFieldLabels[key] || path,before:instructionValue(before,key),after:instructionValue(after,key),scope});
  };
  for(const change of changes){
    if(!change || typeof change.path!=='string')continue;
    const path=change.path;
    if(path==='shots' && Array.isArray(change.before) && Array.isArray(change.after)){
      for(const shot of change.after){
        const prior=change.before.find(item=>item.id===shot.id) || {};
        for(const key of ['azimuth','elevation','focal_length','projection','ortho_scale','camera_distance','target_offset','target_object','style'])if(JSON.stringify(prior[key])!==JSON.stringify(shot[key]))add('shots.'+shot.id+'.'+key,prior[key],shot[key],'镜头：'+sceneShotLabel(shot.id));
      }
    }else if(['material_overrides','object_material_overrides'].includes(path) && Array.isArray(change.after)){
      const items=change.after,previous=Array.isArray(change.before)?change.before:[],effectivePrevious=Array.isArray(change.effective_before)?change.effective_before:[];
      let expanded=0;
      for(const material of items){
        const prior=previous.find(item=>item.name===material.name && item.object_name===material.object_name && item.material_name===material.material_name) || {};
        const effectivePrior=effectivePrevious.find(item=>item.name===material.name && item.object_name===material.object_name && item.material_name===material.material_name) || prior;
        for(const key of ['preset','color','roughness','metallic','transmission','texture_scale','texture_rotation']){
          // A preserved shader may carry a fallback preset in the stored plan;
          // it is bookkeeping, not a material replacement made by this edit.
          if(key==='preset' && material.preserve_original===true)continue;
          if(JSON.stringify(prior[key])!==JSON.stringify(material[key])){add(key,Object.hasOwn(effectivePrior,key)?effectivePrior[key]:prior[key],material[key],material.object_name?'部件：'+material.object_name:'材质：'+(material.name || material.material_name || '未命名'));expanded++;}
        }
      }
      if(!expanded)add(path,change.before,change.after,path==='object_material_overrides'?'所选部件材质':'共享材质');
    }else{
      const match=path.match(/^(?:shot_adjustments|shots)\.([^.]+)\./);
      add(path,Object.hasOwn(change,'effective_before')?change.effective_before:change.before,Object.hasOwn(change,'effective_after')?change.effective_after:change.after,match?'镜头：'+sceneShotLabel(match[1]):'整个摄影方案');
    }
  }
  return rows;
}
function instructionRowsHTML(rows) {
  return rows.map(row=>`<li><div><strong>${esc(row.label)}</strong><small>${esc(row.scope)}</small></div><p><span>${esc(row.before)}</span><span> → </span><span>${esc(row.after)}</span></p></li>`).join('');
}
function currentInstructionGuidance() {
  const guidance=instructionGuidance.get(current?.id);
  const staleResponse=guidance?.kind==='response_error' && (dirty || guidance.baseVersion!==current?.version || guidance.shotId!==activeShot || !sameInstructionSource(guidance.source,feedbackSnapshot(currentOutput)));
  if(guidance && (guidance.text!==$('instructionInput').value.trim() || staleResponse)){
    instructionGuidance.delete(current.id);
    if(guidance.kind==='response_error' && $('instructionError').textContent===guidance.message){$('instructionError').textContent='';$('instructionError').hidden=true;}
    return null;
  }
  return guidance || null;
}
const instructionResponseReasons={generic:'修改结果未通过校验',conflict:'返回的修改项互相冲突',scope:'要求与修改范围未能对应',coverage:'返回结果遗漏了部分要求',material:'材质参数与要求不一致',parameters:'修改参数格式不正确',unsupported_parameter:'返回了当前不支持的修改参数',range:'返回的参数超出可用范围',target:'返回的部件、材质或镜头无效',constraints:'修改违反了需要保留的设置',interpretation:'要求尚未转成可执行的修改',verification:'返回结果未验证要求的画面效果'};
const instructionResponseErrorMessage='本次未应用：'+instructionResponseReasons.generic+'。原要求已保留。';
const instructionResponseFields=new Set(['roughness','metallic','transmission','color','preset','exposure','light_softness','margin','aspect','key_power','fill_power','rim_power','environment','key_azimuth','key_elevation','fill_azimuth','fill_elevation','rim_azimuth','rim_elevation','azimuth','elevation','focal_length']);
for(const field of ['projection','ortho_scale','camera_distance','target_offset'])instructionResponseFields.add(field);
function instructionResponseFeedback(payload) {
  // Only app-owned enum values may become visible copy; upstream error text
  // and arbitrary schema paths never enter this recovery state.
  const issue=payload?.response_issue,kind=typeof issue?.kind==='string' && Object.hasOwn(instructionResponseReasons,issue.kind)?issue.kind:'generic';
  const field=typeof issue?.field==='string' && instructionResponseFields.has(issue.field)?issue.field:null;
  const diagnosticId=typeof payload?.diagnostic_id==='string' && /^[0-9a-f]{32}$/.test(payload.diagnostic_id)?payload.diagnostic_id:null;
  return {message:'本次未应用：'+instructionResponseReasons[kind]+'。原要求已保留。',field,diagnosticId};
}
function renderInstructionErrorDetails(guidance,attempt) {
  const panel=$('instructionErrorDisclosure'),visible=guidance?.kind==='response_error' && !instructionSubmitting && !attempt && (guidance.field || guidance.diagnosticId);
  panel.hidden=!visible;
  if(!visible){panel.open=false;panel.removeAttribute('data-error');stableHTML($('instructionErrorDetails'),'');return;}
  const key=[guidance.message,guidance.field,guidance.diagnosticId].join(':');
  if(panel.dataset.error!==key){panel.open=false;panel.dataset.error=key;}
  stableHTML($('instructionErrorDetails'),`${guidance.field?`<p>校验项目：${esc(instructionFieldLabels[guidance.field])}</p>`:''}${guidance.diagnosticId?`<p>诊断编号：${esc(guidance.diagnosticId)}</p>`:''}`);
}
function sameInstructionSource(left,right) {
  if(!left || !right)return !left && !right;
  return ['output_id','shot_id','quality','version'].every(key=>left[key]===right[key]);
}
function instructionProposalIsCurrent(guidance) {
  return guidance?.kind==='proposal' && !guidance.invalid && !dirty && guidance.baseVersion===current?.version && guidance.shotId===activeShot && sameInstructionSource(guidance.source,feedbackSnapshot(currentOutput));
}
function restoreInstructionContext() {
  const receipt=current?.instruction_result,id=current?.id;
  if(!id || instructionDrafts.has(id) || instructionGuidance.has(id) || [...instructionAttempts.values()].some(item=>item.projectId===id && unfinishedInstructionPhases.has(item.phase)))return;
  if(['applied','partially_applied','no_change'].includes(receipt?.status) && receipt.partial===true && receipt.applied_version===current.version && typeof receipt.remaining_text==='string' && receipt.remaining_text.trim() && receipt.remaining_text.length<=2000 && receipt.remaining_text.trim()!==String(receipt.text || '').trim() && Array.isArray(receipt.pending) && receipt.pending.length){
    const text=receipt.remaining_text.trim();instructionDrafts.set(id,text);instructionGuidance.set(id,{kind:'remaining',text,pending:clone(receipt.pending)});return;
  }
  if(receipt?.status!=='needs_confirmation' || receipt.base_version!==current.version || receipt.shot_id!==activeShot || typeof receipt.text!=='string' || !receipt.text.trim() || receipt.text.length>2000 || !sameInstructionSource(receipt.source_snapshot,feedbackSnapshot(currentOutput)))return;
  const attempt={text:receipt.text,shotId:receipt.shot_id,body:{expected_version:receipt.base_version}};
  if(!instructionProposalIsValid(receipt,attempt))return;
  instructionDrafts.set(id,receipt.text);
  instructionGuidance.set(id,{kind:'proposal',text:receipt.text,proposalId:receipt.proposal_id,baseVersion:receipt.base_version,shotId:receipt.shot_id,source:clone(receipt.source_snapshot || null),ready:clone(receipt.ready),pending:clone(receipt.pending),proposedChanges:clone(receipt.proposed_changes)});
}
function pendingInstructionLabel(pending) {
  const items=(Array.isArray(pending)?pending:[]).filter(item=>item && typeof item==='object'),clarifications=items.filter(item=>item.status!=='unsupported'),unsupported=items.filter(item=>item.status==='unsupported');
  const labels=[...new Set((clarifications.length?clarifications:unsupported).map(item=>typeof item.label==='string'?item.label.trim():'').filter(label=>label && label.length<=24))];
  const subject=labels.length && labels.length<=2?labels.join('、'):'有要求';
  return clarifications.length?subject+'仍需确认'+(unsupported.length?'；另有要求暂不支持':''):unsupported.length?subject+'暂不支持':'有要求仍需确认';
}
// Which agents worked on a request, from the service's fixed codes and counts.
function agentTraceLabel(trace) {
  if(!Array.isArray(trace) || !trace.length)return '';
  const planner=trace.find(step=>step?.agent==='planner'),steps=trace.filter(step=>step?.agent==='specialists' && typeof step.requirement==='string');
  const parts=[];
  if(planner)parts.push(`理解要求 ${Number(planner.calls) || 1} 轮`);
  const verifier=trace.find(step=>step?.agent==='verifier');
  if(verifier?.result==='narrowed' && Number(verifier.removed)>0)parts.push(`核对定位，去掉 ${Number(verifier.removed)} 个不属于所指部位的部件`);
  else if(verifier?.result==='confirmed' && Number(verifier.checked)>0)parts.push(`核对定位 ${Number(verifier.checked)} 组`);
  const located=steps.filter(step=>step.locator?.result==='located').length,finished=steps.filter(step=>step.finisher?.result==='chosen').length,resolved=steps.filter(step=>step.result==='resolved').length;
  if(located)parts.push(`自动定位部件 ${located} 项`);
  if(finished)parts.push(`自动确定质感 ${finished} 项`);
  if(steps.length)parts.push(`补全待定要求 ${resolved}/${steps.length} 项`);
  const labels={localised_shared_material:'共用材质只改所指部件',extended_to_bound_targets:'补齐同组部件',dropped_unbound_edit:'去掉未定位部件的修改'};
  for(const code of trace.find(step=>step?.agent==='executor')?.repairs || [])if(labels[code])parts.push(labels[code]);
  return parts.length?'自动处理：'+parts.join(' · '):'';
}
function instructionPendingDetails(pending) {
  return (Array.isArray(pending)?pending:[]).filter(item=>item && typeof item==='object').map(item=>`<li>${item.quote?`<p><strong>${esc(item.quote)}</strong></p>`:''}<p>${esc(item.reason || (item.status==='unsupported'?'当前能力暂不支持此项。':item.action==='clarify_parameters'?'请补充或重新提交该项参数要求。':'请在预览中定位相关部位，或补充要求。'))}</p></li>`).join('');
}
function automaticPartialReceipt(guidance) {
  const receipt=instructionReceipts.get(current?.id) || current?.instruction_result;
  return guidance?.kind==='remaining' && receipt?.partial===true && ['partially_applied','no_change'].includes(receipt.status) && receipt.applied_version===current?.version && typeof receipt.remaining_text==='string' && receipt.remaining_text.trim()===guidance.text && Array.isArray(receipt.pending) && receipt.pending.length?receipt:null;
}
function completedInstructionLabel(receipt) {
  const changes=Array.isArray(receipt.changes)?receipt.changes:[];
  const labels=[...new Set(changes.filter(item=>item && typeof item.path==='string').map(item=>{
    const path=item.path;
    if(/^(?:material_overrides|object_material_overrides|texture_scale|texture_rotation)(?:\.|$)/.test(path))return '材质';
    if(/(?:^|\.)(?:lighting|exposure|exposure_delta|light_softness|softness_multiplier)(?:\.|$)/.test(path))return '灯光';
    if(/^(?:shots|shot_adjustments)(?:\.|$)/.test(path))return '镜头';
    if(/^(?:margin|aspect)(?:\.|$)/.test(path))return '构图';
    if(/^(?:pose|rotation)(?:\.|$)/.test(path))return '姿态';
    return '参数调整';
  }))];
  // Report saved controls, never echo a subjective goal as verified quality.
  return labels.length && labels.length<=2?labels.join('、'):labels.length>2?labels.length+' 类调整':'可执行修改';
}
function locatableInstructionPending(guidance) {
  return (Array.isArray(guidance?.pending)?guidance.pending:[]).filter(item=>item && item.status!=='unsupported' && (item.action===undefined || item.action==='locate_part'));
}
function canLocateInstruction() {
  return !!current?.analysis && !!currentOutput && currentOutput.version===current.version && !dirty && !comparePrevious && renderReady() && system?.runtime?.capabilities?.pick!==false && !busy && !planSavePromise && !projectLoading && !realismBusy && !materialAgentSubmitting && !activeJob() && !automaticMaterialBusy() && ![...instructionAttempts.values()].some(item=>item.projectId===current.id && !item.confirmed && unfinishedInstructionPhases.has(item.phase));
}
function instructionTargetSelectionIsCurrent() {
  const selection=instructionTargetSelection;
  return !!selection && selection.projectId===current?.id && selection.version===current.version && selection.text===$('instructionInput').value.trim() && selection.shotId===activeShot && !dirty && !comparePrevious && sameInstructionSource(selection.source,feedbackSnapshot(currentOutput));
}
function instructionTargetPoints() {
  if(!instructionTargetSelectionIsCurrent() || !feedbackPoints.length || feedbackPoints.some(point=>point.pending || !point.hit || !point.material_name))return null;
  const points=feedbackPoints.map(({id,x,y})=>({id,x,y}));
  return validInstructionTargetPoints(points)?points:null;
}
function renderInstructionGuidance(guidance) {
  if(guidance?.kind==='response_error')guidance=null;
  const panel=$('instructionProposal');panel.hidden=!guidance;
  $('instructionLocate').hidden=!locatableInstructionPending(guidance).length;
  $('instructionLocate').disabled=!canLocateInstruction();
  $('instructionLocate').title=$('instructionLocate').disabled?'当前版本预览就绪后，可点选待确认部位。':'点选一个需要修改的部位，帮助识别同类部件。';
  if(!guidance){$('instructionProposalSummary').textContent='';stableHTML($('instructionReadyList'),'');stableHTML($('instructionProposalDetails'),'');panel.removeAttribute('data-guidance');return;}
  const stale=guidance.kind==='proposal' && !instructionProposalIsCurrent(guidance),applied=automaticPartialReceipt(guidance);
  $('instructionProposalSummary').textContent=applied?`${applied.status==='no_change'?'可执行项已符合要求':'已完成'+completedInstructionLabel(applied)}；还有 ${applied.pending.length} 项待处理`:stale?'方案或图片已变化，请重新分析要求。':pendingInstructionLabel(guidance.pending);
  const ready=!stale && guidance.kind==='proposal'?(guidance.ready || []).filter(item=>item.status!=='already_satisfied'):[];
  $('instructionReady').hidden=!ready.length;
  stableHTML($('instructionReadyList'),ready.map(item=>`<li>${esc(item.summary || item.label || item.quote)}</li>`).join(''));
  const key=guidance.proposalId || applied?.id || guidance.kind+':'+guidance.text;
  if(panel.dataset.guidance!==key){$('instructionProposalDisclosure').open=false;panel.dataset.guidance=key;}
  $('instructionProposalDisclosure').querySelector('summary').textContent=applied?'查看详情':'查看原因与修改明细';
  if(applied){
    const rows=instructionDiffRows(applied);
    const worked=agentTraceLabel(applied.agent_trace);
    stableHTML($('instructionProposalDetails'),`<p>待处理要求</p><ul>${instructionPendingDetails(applied.pending)}</ul>${rows.length?`<p>已完成的修改</p><ul class="instruction-diff-list">${instructionRowsHTML(rows)}</ul>`:''}${worked?`<p class="instruction-receipt-evidence">${esc(worked)}</p>`:''}`);
    return;
  }
  const rows=instructionDiffRows({changes:guidance.proposedChanges || []}),satisfied=(guidance.ready || []).filter(item=>item.status==='already_satisfied');
  stableHTML($('instructionProposalDetails'),`${guidance.pending?.length?`<ul>${instructionPendingDetails(guidance.pending)}</ul>`:''}${rows.length?`<p>待应用的参数</p><ul class="instruction-diff-list">${instructionRowsHTML(rows)}</ul>`:''}${satisfied.length?`<p>已符合要求</p><ul>${satisfied.map(item=>`<li>${esc(item.summary || item.quote)}</li>`).join('')}</ul>`:''}${guidance.details?`<p>${esc(guidance.details)}</p>`:''}`);
}
function locatePendingInstruction() {
  const guidance=currentInstructionGuidance();
  if(!locatableInstructionPending(guidance).length || !canLocateInstruction())return;
  clearFeedback(true);
  // A new example is a request to resolve the original text again, not consent
  // to apply the previous partial proposal or to edit only the clicked mesh.
  if(guidance.kind==='proposal')instructionGuidance.set(current.id,{kind:'clarification',text:guidance.text,pending:clone(guidance.pending)});
  instructionTargetSelection={projectId:current.id,text:$('instructionInput').value.trim(),version:current.version,shotId:activeShot,source:clone(feedbackSnapshot(currentOutput))};
  feedbackMode=true;realtime.mode='image';setInspectorTab('material');renderViewer();renderInstruction();
  $('feedbackAnnouncement').textContent='请在预览中点选待确认部位，帮助识别同类部件。';
  $('inspectorMaterialTab').focus({preventScroll:true});
}
async function continueInstructionTargets() {
  if(!instructionTargetPoints() || !canLocateInstruction())return;
  setInspectorTab('review');$('instructionSubmit').focus({preventScroll:true});
  await applyInstructionAndPreview({preventDefault(){}});
}
function renderInstructionReceipt(guidance=currentInstructionGuidance(),attempt=currentInstructionAttempt()) {
  const receipt=instructionReceipts.get(current?.id) || current?.instruction_result;
  const panel=$('instructionReceipt'),input=$('instructionInput').value.trim();
  const unrelatedInput=!!input && ![receipt?.text,receipt?.remaining_text].some(value=>typeof value==='string' && value.trim()===input);
  panel.hidden=!receipt || receipt.status==='needs_confirmation' || !Array.isArray(receipt.changes) || unrelatedInput || !!automaticPartialReceipt(guidance) || ['proposal','clarification','response_error'].includes(guidance?.kind) || (!!attempt && !attempt.confirmed) || (instructionSubmitting && !attempt?.confirmed);
  if(panel.hidden){$('instructionReceiptSummary').textContent='';stableHTML($('instructionReceiptDetails'),'');panel.removeAttribute('data-receipt');return;}
  const rows=instructionDiffRows(receipt),noChange=receipt.status==='no_change' || !receipt.changes.length;
  const older=Number.isInteger(receipt.applied_version) && receipt.applied_version!==current.version;
  const summary=noChange?'没有参数变化':rows.length?'已调整：'+rows.slice(0,2).map(row=>row.label+' '+row.before+' → '+row.after).join('；')+(rows.length>2?` 等 ${rows.length} 项`:''):'调整已保存';
  $('instructionReceiptSummary').textContent=older?`原调整已记录（v${receipt.applied_version}）；当前方案为 v${current.version}`:summary;
  $('instructionReceiptSummary').title=$('instructionReceiptSummary').textContent;
  if(panel.dataset.receipt!==String(receipt.id || '')){$('instructionReceiptDisclosure').open=false;panel.dataset.receipt=String(receipt.id || '');}
  const notes=Array.isArray(receipt.notes)?receipt.notes:[];
  const rowsHtml=instructionRowsHTML(rows);
  stableHTML($('instructionReceiptDetails'),`<p class="instruction-receipt-request">${esc(receipt.text || '')}</p>${receipt.summary?`<p>${esc(receipt.summary)}</p>`:''}${rows.length?`<ul class="instruction-diff-list">${rowsHtml}</ul>`:''}${receipt.partial && receipt.pending?.length?`<p>尚待确认的要求已保留在输入框。</p><ul>${instructionPendingDetails(receipt.pending)}</ul>`:''}${older?'<p>此记录对应原请求，后续方案已有变化。本次未为旧请求启动新的预览。</p>':''}${agentTraceLabel(receipt.agent_trace)?`<p class="instruction-receipt-evidence">${esc(agentTraceLabel(receipt.agent_trace))}</p>`:''}<p class="instruction-receipt-evidence">${receipt.source==='ai'?'AI 解析':receipt.source==='rules'?'内置规则':'参数调整'} · 预览图：${receipt.used_image?'已使用':'未使用'} · 参考图：${receipt.used_reference?'已使用':'未使用'}</p>${notes.length?`<ul class="instruction-receipt-notes">${notes.map(note=>`<li>${esc(typeof note==='string'?note:JSON.stringify(note))}</li>`).join('')}</ul>`:''}`);
}
function renderInstruction() {
  if(instructionProjectId!==(current?.id || '')){
    if(pendingInstructionPreview?.confirmed){pendingInstructionPreview.phase='obsolete';clearAppliedInstruction(pendingInstructionPreview);persistInstructionJournal();}
    instructionProjectId=current?.id || '';
    restoreInstructionContext();
    $('instructionInput').value=instructionDrafts.get(instructionProjectId) || '';
    $('instructionError').textContent='';$('instructionError').hidden=true;
    pendingInstructionPreview=null;
  }
  if(instructionTargetSelection && !instructionTargetSelectionIsCurrent())clearFeedback(true);
  const attempt=currentInstructionAttempt(),guidance=currentInstructionGuidance(),proposal=instructionProposalIsCurrent(guidance),targetPoints=instructionTargetPoints();
  const locked=busy || !!planSavePromise || projectLoading || realismBusy || materialAgentSubmitting || !!activeJob() || automaticMaterialBusy();
  $('instructionInput').disabled=locked || !current?.analysis || !renderReady() || comparePrevious;
  $('instructionSubmit').disabled=$('instructionInput').disabled || !$('instructionInput').value.trim() || (!!attempt && !attempt.confirmed && dirty) || (!attempt && !!instructionTargetSelection && !targetPoints);
  $('instructionSubmit').textContent=instructionSubmitting?(attempt?.confirmed?'正在提交预览…':attempt?.kind==='confirm'?'正在应用…':'正在理解要求并准备修改…'):attempt?(attempt.confirmed?'重试预览':'核对并继续'):guidance?.kind==='response_error'?'重试调整':instructionTargetSelection?'用选点继续识别':proposal?'先应用可完成项':automaticPartialReceipt(guidance)?'继续处理剩余要求':guidance && ['proposal','clarification'].includes(guidance.kind)?'重新分析要求':'应用并预览';
  if(guidance?.kind==='response_error' && !instructionSubmitting && !attempt){$('instructionError').textContent=guidance.message;$('instructionError').hidden=false;}
  let context='';
  if(!current)context='上传模型后即可调整画面。';
  else if(!current.analysis)context='模型分析完成后即可调整。';
  else if(comparePrevious)context='正在对比历史图片，请返回最新图片后再调整。';
  else if(instructionSubmitting)context='';
  else if(attempt && !attempt.confirmed)context=dirty?'上次请求尚未确认。先保存或放弃新参数，再核对同一请求。':'上次请求的结果尚未确认；重试会核对同一请求，不会重复应用。';
  else if(attempt)context=attempt.submitted?'参数已保存，预览任务已提交。正在等待更新。':`调整已保存；重试只生成「${attempt.shotIds.map(sceneShotLabel).join('、')}」的预览。`;
  else if(instructionPreflightDrafts.has(current.id) && !instructionPreflightPending)context='预览阶段的要求已保留；当前预览完成后可重新应用。';
  else if(instructionTargetSelection)context=targetPoints?`将用 ${targetPoints.length} 个选点辅助识别，继续处理原来的要求。`:'先在预览中点选一个需要修改的部位。';
  else if(proposal)context='尚未修改，确认后应用可完成项并生成预览。';
  else if(currentOutput && currentOutput.version!==current.version)context=`当前显示的是旧版图片 v${currentOutput.version}；应用调整时会先生成当前方案的预览。`;
  else if(realtimeActive() && currentOutput)context=`AI 参考已渲染图片 v${currentOutput.version}；当前视角用于新预览。`;
  $('instructionContext').textContent=context;$('instructionContext').hidden=!context;
  if(!current)$('reviewRefreshButton').hidden=true;
  renderInstructionErrorDetails(guidance,attempt);renderInstructionGuidance(guidance);renderInstructionReceipt(guidance,attempt);
}
function heroOptimizationSlot(projectId=current?.id,shotId=activeShot) { return JSON.stringify([projectId,shotId]); }
function heroOptimizationShot() {
  const shot=shots().find(item=>item.id===activeShot);
  return shot?.kind==='full' && (shot.style || draft?.style || current?.plan?.style)==='white'?shot:null;
}
function persistHeroOptimizationJournal() {
  try{
    const attempts=[...heroOptimizationAttempts.values()].slice(-50);
    if(attempts.length)sessionStorage.setItem(heroOptimizationJournalKey,JSON.stringify({schema:1,attempts}));
    else sessionStorage.removeItem(heroOptimizationJournalKey);
  }catch(_){}
}
function restoreHeroOptimizationJournal() {
  try{
    const raw=sessionStorage.getItem(heroOptimizationJournalKey);if(!raw || raw.length>60000)return;
    const data=JSON.parse(raw),ident=value=>typeof value==='string' && /^[A-Za-z0-9_-]{1,128}$/.test(value),positive=value=>Number.isInteger(value) && value>0;
    if(data?.schema!==1 || !Array.isArray(data.attempts) || data.attempts.length>50)return;
    for(const item of data.attempts){
      const body=item?.body,source=body?.source;
      if(!ident(item?.projectId) || !ident(item?.shotId) || !ident(item?.requestKey) || !body || Array.isArray(body) || Object.keys(body).sort().join(',')!=='expected_version,final_resolution,shot_id,source' || body.shot_id!==item.shotId || !positive(body.expected_version) || body.final_resolution!==2000 || !source || Array.isArray(source) || Object.keys(source).sort().join(',')!=='output_id,quality,shot_id,version' || !ident(source.output_id) || source.shot_id!==item.shotId || !positive(source.version) || !['preview','standard','fine','hero'].includes(source.quality))continue;
      heroOptimizationAttempts.set(heroOptimizationSlot(item.projectId,item.shotId),{projectId:item.projectId,shotId:item.shotId,requestKey:item.requestKey,body:clone(body),phase:item.phase==='rejected'?'rejected':'unknown'});
    }
  }catch(_){}
}
function currentHeroOptimizationAttempt() {
  const slot=heroOptimizationSlot(),attempt=heroOptimizationAttempts.get(slot);if(!attempt)return null;
  if(current?.jobs?.some(job=>job.kind==='hero_optimize' && job.request_key===attempt.requestKey)){
    heroOptimizationAttempts.delete(slot);heroOptimizationErrors.delete(slot);persistHeroOptimizationJournal();return null;
  }
  const source=latestOutput(activeShot);
  if(attempt.phase==='rejected' && (dirty || attempt.body.expected_version!==current?.version || !sameInstructionSource(attempt.body.source,feedbackSnapshot(source)))){
    heroOptimizationAttempts.delete(slot);heroOptimizationErrors.delete(slot);persistHeroOptimizationJournal();return null;
  }
  return attempt;
}
// Usage scene: the product where it is used. A strategist proposes who the picture is for from the audience
// reference pack; the picture is then built from CC0 props in Blender, or made by the image model from
// renders of the product when it needs people or a real place.
let sceneSubmitting=false,sceneSeenTask=null,sceneLibrary=null,sceneLibraryLoading=false,sceneRecommending=false,sceneChoiceProject=null;
const sceneErrors=new Map();
const sceneZoneLabels={on_top:'台面',inside:'抽屉内',beside:'旁边',behind:'后方'};
const sceneSkipLabels={not_in_catalogue:'模型库中没有合适的',no_room:'没有合适的位置',no_flat_top:'没有平整台面',no_open_compartment:'没有可打开的抽屉',download_failed:'下载失败',duplicate:'与其他道具重复',import_failed:'模型无法导入'};
const sceneMethodLabels={render:'3D 道具场景（Blender 渲染）',generate:'AI 图像生成'};
const sceneSourceLabels={ai:'由 AI 推荐',rules:'按内置规则推荐',user:'你选择的人群'};
function sceneTaskActive() { return ['queued','running'].includes(current?.scene_task?.status); }
function sceneAvailable() { return system?.runtime?.capabilities?.scene_set!==false; }
function sceneSegment(id) { return (sceneLibrary?.segments || []).find(item=>item.id===id) || null; }
function sceneReference(id) { for(const segment of sceneLibrary?.segments || []){const row=(segment.references || []).find(item=>item.id===id);if(row)return row;} return null; }
function sceneHttps(url) { return typeof url==='string' && /^https:\/\/[^\s<>"']+$/.test(url)?url:''; }
function sceneFile(url) { return typeof url==='string' && /^\/files\/[^\s<>"']+$/.test(url)?url:''; }
function sceneGeneratedOutput() { return [...(current?.outputs || [])].filter(o=>o.shot_id==='scene' && o.quality==='generated').sort((a,b)=>b.version-a.version || (b.created_at || 0)-(a.created_at || 0))[0] || null; }
async function loadSceneLibrary() {
  if(sceneLibrary || sceneLibraryLoading)return;
  sceneLibraryLoading=true;
  // A failed load leaves an empty library: scenes work without audiences, and it is tried again when a project is opened.
  try{const library=await api('/api/scene/audiences');sceneLibrary=Array.isArray(library?.segments)?library:{segments:[],failed:true};fillSceneAudience();}catch(_){sceneLibrary={segments:[],failed:true};}
  finally{sceneLibraryLoading=false;if(current)renderSceneSet();}
}
function fillSceneAudience() {
  const select=$('sceneAudienceSelect'),chosen=select.value;
  select.innerHTML='<option value="auto">自动推荐</option>'+(sceneLibrary?.segments || []).map(item=>`<option value="${esc(item.id)}">${esc(item.label)}</option>`).join('');
  select.value=[...select.options].some(option=>option.value===chosen)?chosen:'auto';
  fillSceneReferences();
}
function fillSceneReferences() {
  const segment=sceneSegment($('sceneAudienceSelect').value),select=$('sceneReferenceSelect'),chosen=select.value;
  select.innerHTML='<option value="">由 AI 在该人群中选择</option>'+(segment?.references || []).map(row=>`<option value="${esc(row.id)}">${esc(row.id+' '+row.title)}</option>`).join('');
  select.value=[...select.options].some(option=>option.value===chosen)?chosen:'';
  $('sceneReferenceRow').hidden=!segment;
}
function sceneChoices() {
  const audience=$('sceneAudienceSelect').value || 'auto',reference=audience==='auto'?'':$('sceneReferenceSelect').value;
  return {hint:$('sceneHint').value.trim().slice(0,300),audience,...(reference?{reference}:{}),method:$('sceneMethodSelect').value || 'auto'};
}
function sceneProposal() {
  // A proposal is shown only while it answers what is on screen: this plan, this hint, this audience.
  const proposal=current?.scene_proposal,choice=sceneChoices();
  if(!proposal || proposal.version!==current.version || proposal.hint!==choice.hint)return null;
  if(choice.audience==='auto'?proposal.audience!=='auto':choice.audience!==proposal.segment_id)return null;
  if(choice.reference && choice.reference!==proposal.reference_id)return null;
  return proposal;
}
function sceneReferenceHTML(id) {
  const row=sceneReference(id),url=sceneHttps(row?.url);
  if(!row)return '';
  return `<p>参考 ${esc(row.id)}：${url?`<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(row.title)}</a>`:esc(row.title)}（${esc(row.attribution)}）。${esc(row.note)}</p>`;
}
function sceneProposalHTML(proposal) {
  const segment=sceneSegment(proposal.segment_id),method=proposal.method;
  const chosen=choice=>choice==='generate' || choice==='render'?choice:method,final=chosen($('sceneMethodSelect').value);
  const why=proposal.people?'画面含人物':proposal.complex?'画面需要真实环境':'用道具即可搭出';
  const index=typeof sceneLibrary?.pack?.index_url==='string' && sceneLibrary.pack.index_url.startsWith('/references/')?sceneLibrary.pack.index_url:'';
  return `<strong>${segment?(proposal.audience==='auto'?'推荐人群：':'所选人群：')+esc(segment.label):'没有对应的人群，按通用场景处理'}</strong>`
    +`<p>${esc(proposal.reason || '')}</p>`
    +(segment?`<p>他们想知道：${esc(segment.question)}；优先画面：${esc(segment.priority)}。</p>`:'')
    +sceneReferenceHTML(proposal.reference_id)
    +(proposal.summary?`<p>计划画面：${esc(proposal.summary)}</p>`:'')
    +`<p class="scene-method">生成方式：${esc(sceneMethodLabels[final] || '')}${final===method?`（${esc(why)}）`:'（你指定）'}${proposal.method_note && final===method?' '+esc(proposal.method_note):''}</p>`
    +`<p>${esc(sceneSourceLabels[proposal.source] || '')}；可在上方改选人群、参考或生成方式${index?`，或<a href="${esc(index)}" target="_blank" rel="noopener">浏览参考库</a>`:''}。</p>`;
}
function sceneBriefHTML(brief) {
  const segment=sceneSegment(brief.segment_id);
  return `<strong>${segment?'面向：'+esc(segment.label):'通用使用场景'}</strong>`
    +`<p>${esc(sceneSourceLabels[brief.source] || '')}${brief.reason?'：'+esc(brief.reason):''}</p>`+sceneReferenceHTML(brief.reference_id);
}
function sceneGeneratedHTML(output) {
  const made=output.generated || {},check=made.check || {},status=check.status;
  const label=status==='passed'?'产品与参考图一致':status==='failed'?'产品与参考图有差异':'未自动核对';
  const issues=Array.isArray(check.issues)?check.issues.filter(item=>typeof item==='string').slice(0,5):[];
  const references=(Array.isArray(made.references)?made.references:[]).map(row=>sceneFile(row?.url)).filter(Boolean).slice(0,8);
  return `<p class="scene-check is-${esc(status || 'unchecked')}"><strong>${esc(label)}</strong>${check.note?' '+esc(check.note):''}</p>`
    +(issues.length?`<ul class="scene-issues">${issues.map(item=>`<li>${esc(item)}</li>`).join('')}</ul>`:'')
    +(references.length?`<p>发给图像模型的产品参考图（${references.length} 个角度，Blender 渲染）：</p><div class="scene-refs">${references.map((url,index)=>`<a href="${esc(url)}" target="_blank" rel="noopener"><img src="${esc(url)}" alt="产品参考角度 ${index+1}" loading="lazy"></a>`).join('')}</div>`:'');
}
function renderSceneSet() {
  const button=$('sceneButton'),status=$('sceneStatus'),details=$('sceneDetails');
  const section=button.closest('.scene-set');section.hidden=!current;if(!current)return;
  if(sceneChoiceProject!==current.id){
    sceneChoiceProject=current.id;$('sceneAudienceSelect').value='auto';$('sceneMethodSelect').value='auto';fillSceneReferences();
    if(sceneLibrary?.failed && !sceneLibraryLoading)sceneLibrary=null;
  }
  if(!sceneLibrary)loadSceneLibrary();
  const task=current.scene_task,set=current.plan?.scene_set,brief=current.plan?.scene_brief,made=brief?.method==='generate',has=!!set || made;
  const active=sceneTaskActive(),job=activeJob();
  const locked=busy || !!planSavePromise || projectLoading || realismBusy || materialAgentSubmitting || !!job || automaticMaterialBusy() || sceneSubmitting || sceneRecommending;
  const ready=sceneAvailable() && !!current.analysis && !serviceDisconnected;
  let message='';
  if(serviceDisconnected)message='连接恢复后可生成场景。';
  else if(!sceneAvailable())message='场景组件未就绪，请查看运行环境。';
  else if(!current.analysis)message='模型分析完成后即可生成场景。';
  else if(active)message=task.stage || '正在生成场景';
  else if(sceneErrors.get(current.id))message=sceneErrors.get(current.id);
  else if(task?.status==='failed' && task.base_version===current.version)message=task.error || '场景未生成，可重试。';
  else if(has)message=(set?.summary || brief?.summary || '');
  // Who the picture is for: the pack's segments, the strategist's proposal, the user's own choice.
  const audience=$('sceneAudience'),segments=sceneLibrary?.segments || [];
  audience.hidden=!segments.length;
  if(segments.length){
    const disabled=locked || active || !ready;
    for(const id of ['sceneAudienceSelect','sceneReferenceSelect','sceneMethodSelect','sceneRecommend'])$(id).disabled=disabled;
    $('sceneRecommend').textContent=sceneRecommending?'正在推荐…':'推荐人群与场景';
    if(sceneRecommending)$('sceneRecommend').setAttribute('aria-busy','true');else $('sceneRecommend').removeAttribute('aria-busy');
    const image=sceneLibrary.image || {},wantsImage=$('sceneMethodSelect').value!=='render';
    const help=!image.available && wantsImage?`图像模型不可用（${image.message || '未配置'}）；含人物的场景会改为 3D 道具场景。`:'';
    $('sceneMethodHelp').textContent=help;$('sceneMethodHelp').hidden=!help;
    const proposal=active?null:sceneProposal(),picked=sceneSegment($('sceneAudienceSelect').value);
    // A choice the last proposal does not cover is planned when the scene is generated.
    const pending=!proposal && !active && picked?`<p>将按你选择的人群「${esc(picked.label)}」策划画面；点「推荐人群与场景」可先看方案，也可以直接生成。</p>`:'';
    stableHTML($('sceneProposal'),proposal?sceneProposalHTML(proposal):pending);$('sceneProposal').hidden=!proposal && !pending;
  }
  button.disabled=locked || active || !ready;$('sceneHint').disabled=button.disabled;
  const verb=has?(made?'重新生成使用场景图':'换一组场景道具'):'生成使用场景图';
  button.textContent=sceneSubmitting?'正在提交…':active?'正在生成使用场景…':(dirty?'保存并':'')+verb;
  if(active || sceneSubmitting)button.setAttribute('aria-busy','true');else button.removeAttribute('aria-busy');
  button.title='按目标人群策划画面：用 Poly Haven（CC0）的现成模型按真实尺寸布置，或在需要人物、真实环境时由图像模型依据产品渲染图生成';
  status.textContent=message;status.hidden=!message;status.classList.toggle('is-summary',has && message===(set?.summary || brief?.summary || '') && !active);
  details.hidden=!has;
  if(has){
    $('sceneDetailsTitle').textContent=made?'场景图的来源与核对':'场景道具与来源';
    stableHTML($('sceneBrief'),brief?sceneBriefHTML(brief):'');$('sceneBrief').hidden=!brief;
    const props=set && Array.isArray(set.props)?set.props:[],skipped=set && Array.isArray(set.skipped)?set.skipped:[];
    stableHTML($('sceneProps'),props.map(prop=>{const url=typeof prop.source_url==='string' && /^https:\/\/polyhaven\.com\/a\/[A-Za-z0-9_]+$/.test(prop.source_url)?prop.source_url:'';return `<li><span>${esc(prop.name || prop.asset_id)}</span><small>${esc(sceneZoneLabels[prop.zone] || '')}${url?` · <a href="${esc(url)}" target="_blank" rel="noopener">${esc(prop.provider || 'Poly Haven')}</a>`:''} · ${esc(prop.license || 'CC0')}</small></li>`;}).join(''));
    $('sceneProps').hidden=!props.length;
    const output=made?sceneGeneratedOutput():null;
    stableHTML($('sceneGenerated'),output?sceneGeneratedHTML(output):'');$('sceneGenerated').hidden=!output;
    if(made){
      const info=output?.generated || {},stale=output && output.version!==current.version;
      $('sceneNote').textContent=output?`由图像模型 ${info.model || ''} 生成，不是 Blender 渲染，也没有可导出的 3D 工程；产品外形以上面的渲染参考图为准${info.attempts>1?'，已按核对结果重新生成过一次':''}。${stale?`这张图基于方案 v${output.version}，`:''}修改产品或镜头后请重新生成。其他镜头不变。`:'场景图尚未生成，或已被移除。';
    }else{
      const size=set.product?.max_dimension_mm,basis={user:'你填写的尺寸',ai:'模型估计',model_units:'按模型单位推断',category:'按品类常见尺寸'}[set.product?.size_basis] || '';
      $('sceneNote').textContent=`${set.source==='ai'?'由 AI 选择场景与道具':'按内置品类规则选择'}${size?`；产品最长边按 ${Math.round(size)} mm 计（${basis}）`:''}${set.open?'；已拉开抽屉':''}${skipped.length?`；未放入 ${skipped.length} 项（${[...new Set(skipped.map(row=>sceneSkipLabels[row.reason] || '未放入'))].join('、')}）`:''}。道具只出现在这个镜头里，产品本身与其他镜头不变。`;
    }
    $('sceneRemove').disabled=locked || active;
  }
  // A scene that just finished is shown: its shot becomes the active one.
  if(task?.status==='completed' && task.id!==sceneSeenTask && task.applied_version===current.version && shots().some(shot=>shot.id==='scene')){
    sceneSeenTask=task.id;
    if(activeShot!=='scene' && !dirty){activeShot='scene';cameraShot='scene';queueMicrotask(()=>{if(current)renderProject();});}
  }
}
async function recommendScene() {
  if(!current || sceneRecommending || sceneSubmitting || sceneTaskActive() || serviceDisconnected || !current.analysis)return;
  const id=current.id;sceneErrors.delete(id);sceneRecommending=true;renderSceneSet();
  try{
    if(dirty)await savePlan(true);
    if(current?.id!==id)return;
    const proposal=await api(`/api/projects/${encodeURIComponent(id)}/scene/recommend`,{method:'POST',body:sceneChoices()});
    if(current?.id===id)current.scene_proposal=proposal;
  }catch(error){sceneErrors.set(id,error.message || '未能推荐人群，请重试。');}
  finally{sceneRecommending=false;if(current)renderSceneSet();}
}
async function generateScene() {
  if(!current || sceneSubmitting || sceneRecommending || sceneTaskActive() || activeJob() || !sceneAvailable() || serviceDisconnected || !current.analysis)return;
  const id=current.id;sceneErrors.delete(id);sceneSubmitting=true;renderSceneSet();
  try{
    if(dirty)await savePlan(true);
    if(current?.id!==id)return;
    const task=await api(`/api/projects/${encodeURIComponent(id)}/scene`,{method:'POST',body:{expected_version:current.version,...sceneChoices()}});
    if(current?.id===id){current.scene_task=task;sceneSeenTask=null;}
  }catch(error){sceneErrors.set(id,error.message || '场景未能开始，请重试。');}
  finally{sceneSubmitting=false;if(current)renderProject();}
}
async function removeScene() {
  if(!current || sceneSubmitting || sceneTaskActive() || activeJob() || dirty)return;
  const id=current.id;sceneSubmitting=true;renderSceneSet();
  try{
    const project=await api(`/api/projects/${encodeURIComponent(id)}/scene/remove`,{method:'POST',body:{expected_version:current.version}});
    if(current?.id===id){current=project;if(activeShot==='scene'){activeShot='hero';cameraShot='hero';}syncSettings();toast('已移除使用场景；可用撤销恢复。');}
  }catch(error){sceneErrors.set(id,error.message || '未能移除场景。');}
  finally{sceneSubmitting=false;if(current)renderProject();}
}
function renderHeroOptimization() {
  const button=$('optimizeHeroButton'),reason=$('optimizeHeroReason'),shot=current && heroOptimizationShot();
  button.hidden=!shot;reason.hidden=true;reason.textContent='';
  button.closest('.hero-optimize').hidden=!shot;
  button.disabled=true;button.textContent='自动优化主图';button.removeAttribute('aria-busy');
  if(!shot)return;
  const source=latestOutput(shot.id),attempt=currentHeroOptimizationAttempt(),job=activeJob();
  const locked=busy || !!planSavePromise || projectLoading || realismBusy || materialAgentSubmitting || !!job || automaticMaterialBusy();
  let message='';
  if(serviceDisconnected)message='连接恢复后可继续优化。';
  else if(!heroOptimizationReady())message='自动主图优化暂不可用，请查看运行环境。';
  else if(!source?.blend_url)message='先生成预览后优化。';
  else if(!current.analysis)message='模型分析完成后即可优化。';
  else if(attempt?.phase==='unknown' && dirty)message='将核对上次提交，当前草稿保留。';
  else message=heroOptimizationErrors.get(heroOptimizationSlot()) || '';
  button.disabled=locked || !heroOptimizationReady() || !source?.blend_url || !current.analysis;
  button.textContent=heroOptimizationSubmitting?'正在提交优化…':job?.kind==='hero_optimize'?'主图优化中…':attempt?'核对并继续优化':dirty?'保存并自动优化主图':'自动优化主图';
  if(heroOptimizationSubmitting)button.setAttribute('aria-busy','true');
  reason.textContent=message;reason.hidden=!message;
  button.title=message || '自动检查、试渲染并输出 2000 px 主图';
}
async function optimizeHero() {
  const shot=heroOptimizationShot();
  if(!current || !shot || busy || planSavePromise || projectLoading || realismBusy || materialAgentSubmitting || activeJob() || automaticMaterialBusy() || !heroOptimizationReady() || !current.analysis || !latestOutput(shot.id)?.blend_url)return;
  let attempt=currentHeroOptimizationAttempt();
  const projectId=current.id,shotId=shot.id,slot=heroOptimizationSlot(projectId,shotId);
  busy=true;heroOptimizationSubmitting=true;heroOptimizationErrors.delete(slot);renderProject();
  try{
    if(!attempt){
      await savePlan(true);if(current?.id!==projectId)return;
      const source=latestOutput(shotId);
      if(!source?.blend_url)throw new Error('当前镜头没有可用场景，请先生成预览。');
      attempt={projectId,shotId,requestKey:crypto.randomUUID(),phase:'requesting',body:{expected_version:current.version,shot_id:shotId,source:clone(feedbackSnapshot(source)),final_resolution:2000}};
      heroOptimizationAttempts.set(slot,attempt);persistHeroOptimizationJournal();
    }
    attempt.phase='requesting';persistHeroOptimizationJournal();
    const job=await api(`/api/projects/${projectId}/hero-optimize`,{method:'POST',headers:{'Idempotency-Key':attempt.requestKey},body:clone(attempt.body)});
    if(!job || typeof job.id!=='string' || job.kind!=='hero_optimize' || !['queued','running','completed','failed','cancelled','interrupted'].includes(job.status) || job.project_id && job.project_id!==projectId || !job.shot_ids?.includes(shotId) || job.request_key && job.request_key!==attempt.requestKey){const error=new Error('尚未收到与本次优化一致的任务记录，请核对并继续。');error.uncertain=true;throw error;}
    heroOptimizationAttempts.delete(slot);heroOptimizationErrors.delete(slot);persistHeroOptimizationJournal();
    if(current?.id===projectId){
      current.jobs=[job,...(current.jobs || []).filter(item=>item.id!==job.id)];
      toast(['queued','running'].includes(job.status)?'主图优化已启动，完成后自动显示高清结果。':job.optimization_summary || '已核对上次主图优化任务。');
    }
  }catch(error){
    if(attempt){attempt.phase=error.status>=400 && error.status<500 && !error.uncertain?'rejected':'unknown';heroOptimizationAttempts.set(slot,attempt);persistHeroOptimizationJournal();}
    heroOptimizationErrors.set(slot,error.message || '提交结果暂未确认，请重试核对。');
  }finally{heroOptimizationSubmitting=false;busy=false;renderProject();}
}
function renderActionState() {
  document.body.classList.toggle('has-project',!!current);
  $('exitProjectButton').hidden=!current;$('exitProjectButton').disabled=projectWritePending();
  $('mobileDeleteProject').hidden=!current;$('mobileDeleteProject').disabled=navigationBlocked();
  $('mobileDeleteProject').setAttribute('aria-label',current?`删除项目「${current.name}」`:'删除当前项目');
  document.querySelectorAll('[data-delete-project]').forEach(button=>button.disabled=navigationBlocked());
  const locked=busy || !!planSavePromise || projectLoading || realismBusy || materialAgentSubmitting || !!activeJob() || automaticMaterialBusy();
  const blocked=locked || !current?.analysis;
  $('previewButton').disabled=blocked || !renderReady() || !activeShot;$('renderButton').disabled=blocked || !selectedShots.size || [...selectedShots].some(id=>!renderReadyForShot(id));
  $('previewButton').textContent=dirty?'保存并预览':'预览';$('previewButton').title=dirty?'保存当前参数并预览这个镜头':'预览当前镜头';
  $('renderButton').textContent=selectedShots.size?`渲染 ${selectedShots.size} 张 4K`:'渲染 4K';
  const reason=renderReady() && current?.analysis && !locked && !selectedShots.size?'勾选镜头后可输出 4K。':'';
  setText('renderReason',reason);if($('renderReason'))$('renderReason').hidden=!reason;
  $('renderButton').title=reason || '按勾选的镜头输出 4K 成品';
  $('savePlan').disabled=!dirty || locked || !current?.analysis;
  $('settingsForm').inert=!current?.analysis || busy || !!planSavePromise || projectLoading || realismBusy || materialAgentSubmitting || automaticMaterialBusy() || !!activeJob();
  $('uploadSubmit').disabled=uploadSubmitting || !uploadFiles.length || !!uploadValidation() || !workerRenderReady();
  document.querySelectorAll('[data-example]').forEach(button=>button.disabled=!workerRenderReady());
  renderWorkflow();renderInstruction();renderReview();renderRenderActivity();renderLatestSceneDownloads();renderHeroOptimization();renderSceneSet();renderRealtimeControls();renderEcommerce();renderLayerPanel();
}

let comparePrevious=false, viewerLatestKey='', styleFocusNotice='';
let realtime={key:'',context:'',mode:'live',status:'idle',epoch:0,instance:null,changed:false,record:null,error:'',timer:null,abort:null,preservedView:null};
let inspectorTab='review', materialAgentSubmitting=false, materialAgentProjectId='', materialAgentRenderedKey='';
const inspectorScrollPositions={review:0,material:0,plan:0};
const materialAgentRecords=new Map(), materialAgentDisclosures=new Map();
let sceneExportSource = null, sceneExportSubmitting = false, sceneExportProjectId = null, sceneExportJobsSignature = '', sceneExportOptionsSignature = '';
const latestSceneRequests = new Map(), latestSceneErrors = new Map();
let aiSettingsBusy = false, aiSettings = null;
const aiDraftEndpoints = {ai:'',vision:''};
let feedbackMode = false, feedbackSource = null, feedbackPoints = [], feedbackEpoch = 0;
let partMaterialSignature = '', partMaterialFields = new Set(), partMaterialValueFields = new Set(), realismBusy = false, reviewSeenKey = '';
const partFieldControls = {preset:['partPreset','partPresetUse'],color:['partColor','partColorUse'],roughness:['partRoughness','partRoughnessUse'],metallic:['partMetallic','partMetallicUse'],transmission:['partTransmission','partTransmissionUse']};
const editedMaterialColors = new Set();
async function api(path, options = {}) {
  if(options.body && !(options.body instanceof FormData)){options.headers={...options.headers,'Content-Type':'application/json'};options.body=JSON.stringify(options.body);}
  const res=await fetch(path,options);
  let data;
  try{data=await res.json();}catch(_){const error=new Error('本机服务返回了无法读取的结果，请重试核对。');error.status=res.status;error.uncertain=true;throw error;}
  if(!res.ok){const error=new Error(data.error || `请求失败 (${res.status})`);error.status=res.status;error.payload=data;throw error;}
  return data;
}
function toast(message, error = false) { clearTimeout(toastTimer); $('toast').textContent = message; $('toast').className = 'toast' + (error ? ' error' : ''); $('toast').hidden = false; toastTimer = setTimeout(() => $('toast').hidden = true, error ? 8000 : 4500); }
// The background high-quality pass never locks the workspace: any request pre-empts it.
function activeJob() { return current?.jobs?.find(j => ['running','queued'].includes(j.status) && !['auto_polish','auto_thumbnails'].includes(j.origin)); }
function polishJob() { return current?.jobs?.find(j => ['running','queued'].includes(j.status) && j.origin==='auto_polish'); }
function renderActivityState() {
  if(!current)return null;
  const job=activeJob(),task=(materialAgentRecords.get(current.id) || []).find(materialTaskIsActive);
  const automatic=current.auto_material,materialActive=automaticMaterialBusy();
  const stopped=['failed','cancelled','interrupted'].includes(current.status);
  const latestJob=[...(current.jobs || [])].sort((a,b)=>(b.created_at || 0)-(a.created_at || 0))[0];
  const preparingHero=!job && !materialActive && !stopped && !!current.analysis && current.initial_preview?.status==='pending'
    && (!latestJob || latestJob.kind==='analyze' && latestJob.status==='completed');
  const analyzing=!job && !materialActive && !current.analysis && current.status==='analyzing';
  if(!job && !materialActive && !preparingHero && !analyzing)return null;
  const rawStage=String(job?.stage || task?.stage || automatic?.stage || '');
  const shotIds=job?.shot_ids || task?.render_shot_ids || (task?.source?.shot_id?[task.source.shot_id]:preparingHero?['hero']:[]);
  const activeDone=Array.isArray(job?.completed) && job.completed.includes(activeShot);
  const renderingElsewhere=job?.status==='running' && job.kind!=='analyze' && !!job.current_shot && job.current_shot!==sceneShotLabel(activeShot);
  const scoped=job?.kind==='analyze' || analyzing || (!shotIds.length && materialActive) || shotIds.includes(activeShot) && !activeDone && !renderingElsewhere;
  const scope=scoped && !comparePrevious?'current':'background';
  const shotLabel=job?.current_shot || (shotIds.length===1?sceneShotLabel(shotIds[0]):'');
  const outputVisible=!!currentOutput && !$('renderImage').hidden;
  let state,title,metric='等待实际进度',progress=null;
  if(job?.status==='queued'){
    state='queued';title=job.kind==='hero_optimize'?'主图优化已排队':job.kind==='analyze'?'模型分析已排队':job.origin==='initial_preview'?'白底主图已排队':job.kind==='render'?'4K 渲染已排队':'预览已排队';
    metric='等待开始，进度将自动更新';
  }else if(job?.kind==='hero_optimize'){
    const phase=job.workflow_phase;
    const stages={inspecting:['analyzing','正在读取真实场景','核对相机、灯光、材质和产品结构'],planning:['preparing','正在规划主图优化','结合画面选择构图、布光与材质调整'],preview:['rendering','正在试渲染主图','试渲染完成后自动比较效果'],evaluating:['reviewing','正在比较优化效果','检查产品完整性与画面变化'],finalizing:['rendering','正在生成高清主图','生成图片与可编辑场景文件']};
    [state,title,metric]=stages[phase] || ['preparing','正在优化主图','等待任务阶段更新'];
    if(['preview','finalizing'].includes(phase) && typeof job.sample_progress==='number' && Number.isFinite(job.sample_progress) && job.sample_progress>=0 && job.sample_progress<=100 && !/准备|启动/.test(rawStage)){
      progress=job.sample_progress;metric=`当前镜头采样 ${progress}%`;
    }
  }else if(job?.kind==='analyze' || analyzing){
    state='analyzing';title='正在分析模型';metric='正在读取几何、部件与贴图';
  }else if(preparingHero){
    state='preparing';title='正在准备白底主图';metric='准备完成后自动开始渲染';
  }else if(!job && materialActive){
    const status=task?.status || automatic?.status;
    state=status==='rendering'?'rendering':'material';title=status==='rendering'?'正在生成材质预览':status==='queued'?'材质优化已排队':'正在处理材质';
    metric=status==='rendering'?'等待实际渲染进度':'找到并应用材质后继续预览';
  }else{
    const reviewing=/检查|诊断|评估|比较|比对|分析画面|分析预览/.test(rawStage) && !/重新|重渲染/.test(rawStage);
    state=reviewing?'reviewing':'rendering';
    title=reviewing?'正在检查渲染效果':job.origin==='initial_preview'?'正在生成白底主图':job.kind==='render'?'正在渲染 4K 成品':'正在生成预览';
    const numeric=value=>typeof value==='number' && Number.isFinite(value) && value>=0 && value<=100;
    const sample=job.sample_progress,group=job.progress,total=Array.isArray(job.shot_ids)?job.shot_ids.length:0;
    const preparing=/准备|启动|回退|重新/.test(rawStage);
    if(!reviewing && !preparing && numeric(sample)){
      progress=sample;metric=`当前镜头采样 ${sample}%`;
    }else if(!reviewing && numeric(group) && group>0 && total>1){
      progress=group;metric=`图组进度 ${group}% · 已完成 ${job.completed?.length || 0}/${total} 张`;
    }else if(reviewing)metric='采样结束后继续检查，任务尚未结束';
    else if(total>1)metric=`图组已完成 ${job.completed?.length || 0}/${total} 张`;
  }
  const stage=[shotLabel,rawStage].filter(Boolean).join(' · ') || (preparingHero?'模型分析已完成':'正在获取任务状态');
  let note=outputVisible?'当前真实图片保留，任务完成后自动更新。':'任务完成后自动显示图片，可退出项目让任务继续。';
  if(scope==='background')note=comparePrevious?'正在查看历史图片，后台任务继续执行。':`当前查看「${sceneShotLabel(activeShot)}」，后台正在处理其他镜头。`;
  if(serviceDisconnected)return {state:'disconnected',title:'连接中断，等待恢复',stage:'暂时无法获取任务状态',metric:'进度暂不可用',note:'请确认本机应用仍在运行；连接恢复后自动核对进度。',progress:null,scope,motion:'still',outputVisible};
  return {state,title,stage,metric,note,progress,scope,motion:'active',outputVisible};
}
function renderRenderActivity() {
  const panel=$('renderActivity'),viewer=$('viewer');if(!panel || !viewer)return;
  const activity=renderActivityState(),putText=(id,value)=>{if($(id).textContent!==value)$(id).textContent=value;};
  panel.hidden=!activity;
  const active=!!activity && activity.motion==='active';
  viewer.classList.toggle('is-rendering',active && activity.scope==='current');
  viewer.classList.toggle('is-rendering-empty',!!activity && !activity.outputVisible && !realtimeActive());
  viewer.setAttribute('aria-busy',String(active && activity.scope==='current'));
  const track=$('renderActivityProgress'),bar=$('renderActivityBar');
  panel.dataset.state=activity?.state || 'idle';panel.dataset.motion=activity?.motion || 'still';panel.dataset.scope=activity?.scope || 'current';
  const determinate=activity?.progress!==null && activity?.progress!==undefined;
  panel.dataset.progress=!activity || activity.state==='disconnected'?'none':determinate?'determinate':'indeterminate';
  track.hidden=panel.dataset.progress==='none';
  if(determinate){track.setAttribute('aria-valuenow',String(activity.progress));bar.style.width=activity.progress+'%';}
  else{track.removeAttribute('aria-valuenow');bar.style.removeProperty('width');}
  track.setAttribute('aria-label',activity?.metric?.startsWith('当前镜头采样')?'当前镜头采样进度':activity?.metric?.startsWith('图组进度')?'已完成镜头进度':'任务进行中，进度暂不可用');
  if(activity)track.setAttribute('aria-valuetext',activity.metric);else track.removeAttribute('aria-valuetext');
  for(const [id,key] of [['renderActivityTitle','title'],['renderActivityStage','stage'],['renderActivityMetric','metric'],['renderActivityNote','note']])putText(id,activity?.[key] || '');
  // This live region sits outside aria-busy so phase changes are announced
  // immediately, without reading every sampled percentage on every poll.
  putText('renderActivityAnnouncement',activity?.title || '');
}
function shots() { return draft?.shots || current?.plan?.shots || []; }
function latestOutput(id) { return [...(current?.outputs || [])].filter(o => o.shot_id === id && o.quality!=='thumbnail').sort((a,b) => b.version-a.version || (b.created_at || 0)-(a.created_at || 0))[0]; }
function latestThumbnail(id) { return [...(current?.outputs || [])].filter(o=>o.shot_id===id && o.quality==='thumbnail' && o.version<=current.version).sort((a,b)=>b.version-a.version || (b.created_at || 0)-(a.created_at || 0))[0]; }
function displayedShotOutput(id) { const full=latestOutput(id),small=latestThumbnail(id);return small && (!full || small.version>full.version)?small:full; }
function previousOutput(latest) {
  if(!latest)return null;
  // A new engine can re-render the same plan version. Compare with the exact
  // real snapshot recorded by that job, not an unrelated earlier version.
  const job=(current?.jobs || []).find(j=>j.id===latest.job_id);
  const ref=job?.automatic_previous_preview;
  if(ref && ref.shot_id===latest.shot_id && ref.png_url && ref.png_url!==latest.png_url){
    const exact=(current?.outputs || []).find(o=>o.job_id===ref.job_id && o.shot_id===ref.shot_id && o.version===ref.version && o.png_url===ref.png_url);
    if(exact)return exact;
  }
  return [...(current?.outputs || [])].filter(o=>o.quality!=='thumbnail' && o.shot_id===latest.shot_id && o.version<latest.version && o.png_url && o.png_url!==latest.png_url)
    .sort((a,b)=>b.version-a.version || Number(b.quality===latest.quality)-Number(a.quality===latest.quality) || (b.created_at || 0)-(a.created_at || 0))[0] || null;
}
function focusStyleShot(style,previousStyle) {
  if(style===previousStyle)return false;
  if(activeShot!=='hero'){styleFocusNotice='棚拍风格已改为'+({dark:'深色',soft:'浅色柔光',white:'白底'}[style] || style)+'，以新预览为准。';return false;}
  const target=shots().find(s=>s.id==='campaign' && !s.style) || shots().find(s=>s.id!=='hero' && !s.style);
  if(!target)return false;
  stashCamera();activeShot=target.id;cameraShot=target.id;selectedShots.add(target.id);comparePrevious=false;
  $('cameraShot').value=cameraShot;syncCamera();
  styleFocusNotice='白底主图保持白底；已切换到'+({dark:'深色',soft:'浅色柔光',white:'白底'}[style] || '')+'棚拍镜头。';
  remember('wuying-shot-'+current.id,activeShot);
  return true;
}
function outputNotice(output,latest) {
  if(comparePrevious && output)return output.version===latest.version?`正在查看优化前的真实预览（方案 v${output.version}）；返回最新可查看本轮结果。`:`正在对比上一版 v${output.version}；返回最新可查看 v${latest.version}。`;
  const running=activeJob();
  if(!running && automaticMaterialBusy())return '正在自动寻找或应用材质；当前真实图片保留，新预览完成后自动更新。';
  if(running?.shot_ids?.includes(activeShot))return `${running.kind==='hero_optimize'?'正在自动优化':'正在生成'}${sceneShotLabel(activeShot)}${running.kind==='hero_optimize'?'':'的新结果'}${output?'；暂时显示 v'+output.version+'，完成后自动更新。':'，完成后自动显示。'}`;
  if(dirty)return (styleFocusNotice?styleFocusNotice+' ':'')+'方案尚未渲染，生成预览后查看变化。';
  if(output?.quality==='generated')return output.version!==current?.version?`AI 生成的场景图，基于方案 v${output.version}；当前方案为 v${current.version}，如产品或镜头已修改，请在「使用场景」中重新生成。`:'AI 生成的场景图：由图像模型依据产品渲染参考图生成，不是 Blender 渲染。';
  if(output && output.version!==current?.version)return (styleFocusNotice?styleFocusNotice+' ':'')+`当前显示旧图 v${output.version}，方案 v${current.version} 尚未生成这个镜头。`;
  const reviewNotice=(current?.jobs || []).find(job=>job.id===output?.job_id)?.review_notice || '';
  if(reviewNotice){
    const attempts=output?.corrections || [],chosen=attempts.find(a=>a.applied);
    return (attempts.length>1?(chosen?.round?`已显示本轮第 ${chosen.round} 轮的真实结果。`:'本轮候选未优于本轮初始预览，已保留本轮初始结果。')+' ':'')+reviewNotice;
  }
  if(styleFocusNotice && activeShot!=='hero')return styleFocusNotice;
  const attempts=output?.corrections || [];
  if(attempts.length>1){const chosen=attempts.find(a=>a.applied);return chosen?.round?`已显示自动修正第 ${chosen.round} 轮的真实结果，可对比上一版。`:'自动候选未优于本轮初始预览，已保留本轮初始结果；可在诊断记录中查看比较。';}
  return '';
}
function filenameSize(bytes) { return bytes > 1024*1024 ? (bytes/1024/1024).toFixed(1)+' MB' : Math.ceil(bytes/1024)+' KB'; }
function markDirty() { if(!current)return;dirty = true;currentInstructionAttempt();renderActionState();if(currentOutput){$('previewNotice').textContent=outputNotice(currentOutput,latestOutput(activeShot));$('previewNotice').hidden=false;} }
function options(select, values, selected) { stableHTML(select,values.map(x => `<option value="${esc(x.value)}">${esc(x.label)}</option>`).join('')); if (values.some(x => x.value === selected)) select.value = selected; }
function setValue(id,value) { $(id).value = value ?? ''; }
function readNumber(id) { return Number($(id).value); }
function colorHex(color) { if (typeof color === 'string') return color; if (!Array.isArray(color)) return '#8c9aab'; return '#'+color.slice(0,3).map(c => Math.round(Math.min(1,Math.max(0,c <= .0031308 ? c*12.92 : 1.055*Math.pow(c,1/2.4)-.055))*255).toString(16).padStart(2,'0')).join(''); }
function describeSource(value) { return sourceNames[value] || value || '未记录'; }
function updateRangeLabels() { $('marginValue').textContent = $('margin').value+'%'; $('softnessValue').textContent = Number($('softness').value).toFixed(2); $('exposureValue').textContent = $('exposure').value+' EV'; $('roughnessValue').textContent = Number($('roughness').value).toFixed(2); const aspect = document.querySelector('[name=aspect]:checked')?.value || '1:1'; $('outputSize').textContent = '成品 '+({'1:1':'4096 × 4096','4:3':'4096 × 3072','3:4':'3072 × 4096'}[aspect])+' px'; }
function syncMaterial() {
  const mat = (current.analysis?.materials || []).find(m => m.name === selectedMaterial) || {};
  const override = (draft.material_overrides || []).find(m => m.name === selectedMaterial);
  setValue('materialPreset',override?.preset || ''); setValue('materialColor',colorHex(override?.color || mat.color));
  setValue('roughness',override?.roughness ?? mat.roughness ?? .45); setValue('metallic',override?.metallic ?? mat.metallic ?? 0); setValue('transmission',override?.transmission ?? mat.transmission ?? 0);
  $('materialEditing').hidden = !override;
  $('materialSource').textContent = override ? '当前：'+describeSource(override.source || 'user')+(override.license ? '；许可 '+override.license : '') : `${describeSource(mat.source)} · 材质记录置信度 ${Math.round((mat.confidence || 0)*100)}%${mat.suggested_preset ? '；建议 '+(presets[mat.suggested_preset] || mat.suggested_preset)+'（规则推测 '+Math.round((mat.suggestion_confidence || 0)*100)+'%）' : ''}`;
  updateRangeLabels();
}
function stashMaterial() {
  if (!draft || !selectedMaterial) return;
  const prior = (draft.material_overrides || []).find(m => m.name === selectedMaterial) || {};
  draft.material_overrides = (draft.material_overrides || []).filter(m => m.name !== selectedMaterial);
  if ($('materialPreset').value) {
    const override = {...prior,name:selectedMaterial,preset:$('materialPreset').value,roughness:readNumber('roughness'),metallic:readNumber('metallic'),transmission:readNumber('transmission'),source:'user'};
    // An untouched color swatch is a display value, not authorization to disconnect
    // the asset's linked base-color texture (which may contain product branding).
    if (editedMaterialColors.has(selectedMaterial)) override.color = $('materialColor').value;
    draft.material_overrides.push(override);
  }
}
const lightDirectionInputs={keyAzimuth:'key_azimuth',keyElevation:'key_elevation',fillAzimuth:'fill_azimuth',fillElevation:'fill_elevation',rimAzimuth:'rim_azimuth',rimElevation:'rim_elevation'};
function syncLightDirections() {
  const local=draft?.shot_adjustments?.[cameraShot]?.lighting || {},shared=draft?.lighting || {};
  for(const [id,key] of Object.entries(lightDirectionInputs)){
    $(id).value=Object.hasOwn(local,key)?local[key]:'';
    $(id).placeholder=Number.isFinite(shared[key])?shared[key]+'°（项目默认）':'沿用原始布光';
  }
}
function stashLightDirections() {
  if(!draft || !cameraShot)return;
  const adjustments=clone(draft.shot_adjustments || {}),item=clone(adjustments[cameraShot] || {}),lighting=clone(item.lighting || {});
  for(const [id,key] of Object.entries(lightDirectionInputs)){
    if($(id).value.trim()==='')delete lighting[key];else lighting[key]=readNumber(id);
  }
  if(Object.keys(lighting).length)item.lighting=lighting;else delete item.lighting;
  if(Object.keys(item).length)adjustments[cameraShot]=item;else delete adjustments[cameraShot];
  if(Object.keys(adjustments).length)draft.shot_adjustments=adjustments;else delete draft.shot_adjustments;
}
function syncCamera() {
  const s = shots().find(s => s.id === cameraShot); if (!s) return;
  setValue('azimuth',s.azimuth);setValue('elevation',s.elevation);setValue('focalLength',s.focal_length);setValue('detailTarget',s.target_object || '');
  setValue('cameraProjection',s.projection==='perspective'?'perspective':'orthographic');syncProjectionField();
  $('detailTarget').disabled = s.kind !== 'detail';
  syncLightDirections();
}
function syncProjectionField() { const perspective=$('cameraProjection').value==='perspective';$('focalLengthField').hidden=!perspective;$('focalLength').disabled=!perspective; }
function stashCamera() { const s = shots().find(s => s.id === cameraShot); if (!s) return; Object.assign(s,{azimuth:readNumber('azimuth'),elevation:readNumber('elevation'),focal_length:readNumber('focalLength'),projection:$('cameraProjection').value==='perspective'?'perspective':'orthographic'});if ($('detailTarget').value) s.target_object = $('detailTarget').value;else delete s.target_object;stashLightDirections(); }
function syncSettings() {
  if (!current) return;
  // Discard/save reinitializes the draft. A camera accepted only into that
  // draft must not remain as an apparently saved view after discarding it.
  if(dirty && realtime.context===current.id+':'+activeShot)disposeRealtime(false);
  draft = clone(current.plan);draftBaseVersion=current.version;dirty = false;editedMaterialColors.clear();
  document.querySelectorAll('[name=style]').forEach(el => el.checked = el.value === draft.style);document.querySelectorAll('[name=aspect]').forEach(el => el.checked = el.value === draft.aspect);
  setValue('margin',draft.margin*100);setValue('softness',draft.light_softness);setValue('exposure',draft.exposure);setValue('textureScale',draft.texture_scale);setValue('textureRotation',draft.texture_rotation);
  ['rotateX','rotateY','rotateZ'].forEach((id,i) => setValue(id,draft.rotation[i]));$('repairNormals').checked = !!draft.repair.normals;$('repairSmooth').checked = !!draft.repair.smooth;
  setValue('timeBudget',draft.time_budget);setValue('maxCorrections',draft.max_corrections);$('visualReview').checked=draft.visual_review!==false;$('autoPolish').checked=draft.auto_polish===true;$('lookMode').value=draft.look==='showcase'?'showcase':'classic';
  const materials = current.analysis?.materials || [];
  if (!materials.some(m => m.name === selectedMaterial)) selectedMaterial = materials[0]?.name || null;
  options($('materialSlot'),materials.map(m => ({value:m.name,label:m.name})),selectedMaterial);
  if (!shots().some(s => s.id === cameraShot)) cameraShot = activeShot || shots()[0]?.id;
  options($('cameraShot'),shots().map(s => ({value:s.id,label:s.label})),cameraShot);
  const obs = (current.analysis?.objects || []).map(o => ({value:o.name,label:o.name+(o.parent ? ' ↳ '+o.parent : '')}));
  options($('detailTarget'),[{value:'',label:'自动选择目标'},...obs], '');options($('poseObject'),[{value:'',label:'保留静态姿态'},...obs],draft.pose?.object || '');
  ['poseX','poseY','poseZ'].forEach((id,i) => setValue(id,draft.pose?.rotation?.[i] || 0));['moveX','moveY','moveZ'].forEach((id,i) => setValue(id,draft.pose?.translation?.[i] || 0));setValue('animationFrame',draft.pose?.frame);
  $('animationInfo').textContent = current.analysis?.animations?.length ? '模型已有动画：'+current.analysis.animations.map(a => `${a.name} (${a.frame_range.join('–')})`).join('，') : '未检测到已有动画。部件运动需要手动确认。';
  syncMaterial();syncCamera();updateRangeLabels();$('savePlan').disabled = true;
  syncSignature = `${current.id}:${current.version}:${!!current.analysis}`;
}
function gatherPlan() {
  stashMaterial();stashCamera();
  draft.visual_review=$('visualReview').checked;
  if($('autoPolish').checked || Object.hasOwn(draft,'auto_polish'))draft.auto_polish=$('autoPolish').checked;
  if($('lookMode').value==='showcase' || Object.hasOwn(draft,'look'))draft.look=$('lookMode').value;
  Object.assign(draft,{style:document.querySelector('[name=style]:checked').value,aspect:document.querySelector('[name=aspect]:checked').value,margin:readNumber('margin')/100,light_softness:readNumber('softness'),exposure:readNumber('exposure'),texture_scale:readNumber('textureScale'),texture_rotation:readNumber('textureRotation'),rotation:['rotateX','rotateY','rotateZ'].map(readNumber),repair:{normals:$('repairNormals').checked,smooth:$('repairSmooth').checked},time_budget:readNumber('timeBudget'),max_corrections:readNumber('maxCorrections')});
  draft.pose = {};
  if ($('poseObject').value) draft.pose = {object:$('poseObject').value,rotation:['poseX','poseY','poseZ'].map(readNumber),translation:['moveX','moveY','moveZ'].map(readNumber)};
  if ($('animationFrame').value !== '') draft.pose.frame = readNumber('animationFrame');
  return draft;
}
async function savePlan(silent = false) {
  if(planSavePromise)return planSavePromise;
  if(!current || !dirty)return;
  if(realismBusy || materialAgentSubmitting || projectLoading || activeJob() || automaticMaterialBusy())throw new Error('当前任务完成后才能保存摄影方案，草稿已保留。');
  if(draftBaseVersion!==current.version)throw new Error('方案已在后台更新。当前草稿仍保留，请先记录需要的调整，再点击“放弃本次调整”载入最新方案。');
  const invalid=[...$('settingsForm').querySelectorAll('input,select')].find(input=>!input.disabled && (input.validity.badInput || input.validity.rangeUnderflow || input.validity.rangeOverflow || input.validity.valueMissing || input.validity.typeMismatch || (input.type==='number' && input.id!=='animationFrame' && !input.hasAttribute('data-light-direction') && input.value.trim()==='')));
  if(invalid){setInspectorTab('plan');for(let node=invalid.parentElement;node;node=node.parentElement){if(node.tagName==='DETAILS')node.open=true;if(node.classList?.contains('tab-content'))document.querySelector('[data-tab="'+node.id.replace(/Tab$/,'')+'"]')?.click();}invalid.reportValidity();throw new Error('请检查「参数」中标出的无效数值。');}
  const id=current.id,body=clone(gatherPlan()),baseVersion=draftBaseVersion;
  planSavePromise=(async()=>{
    const result=await api(`/api/projects/${id}/plan`,{method:'POST',headers:{'X-Plan-Version':String(baseVersion)},body});
    if(current?.id!==id)return;
    current=result;syncSettings();renderProject();if(!silent)toast('摄影方案已保存；点击预览查看变化。');
  })();
  renderActionState();
  try{return await planSavePromise;}finally{planSavePromise=null;renderActionState();}
}
function renderLibrary() {
  $('projectCount').textContent = allProjects.length;
  options($('mobileProjectSelect'),[{value:'',label:allProjects.length?'选择摄影项目':'尚无摄影项目'},...allProjects.map(p=>({value:p.id,label:p.name}))],current?.id || '');
  stableHTML($('projectList'),allProjects.length ? allProjects.map(p => {const out=projectCardImage(p),status=['queued','running'].includes(p.initial_preview?.status)?'白底主图生成中':p.initial_preview?.status==='failed'?'白底主图需处理':statusNames[p.status] || p.status;return `<div class="project-row ${p.id===current?.id?'active':''}"><button type="button" class="project-item" data-project="${esc(p.id)}" ${p.id===current?.id?'aria-current="true"':''}><span class="project-thumb">${out ? `<img src="${esc(out.jpg_url || out.png_url)}" alt="" loading="lazy">` : '◇'}</span><span class="project-copy"><strong>${esc(p.name)}</strong><small>${esc(status)}${p.outputs?.length ? ' · '+p.outputs.length+' 张' : ''}</small></span></button><button type="button" class="icon-button project-delete" data-delete-project="${esc(p.id)}" aria-label="删除项目「${esc(p.name)}」" title="删除项目" ${navigationBlocked()?'disabled':''}>${deleteProjectIcon}</button></div>`;}).join('') : '<p class="muted">还没有摄影项目。<br>上传一个模型开始吧。</p>');
  renderHomeProjects();
}
// Home cards (design 01 首页 · 上传与项目): latest hero image, plain status line,
// amber progress while work continues in the background.
const projectPlaceholderIcon='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="m12 3 8 4.5v9L12 21l-8-4.5v-9L12 3Z"/><path d="m4 7.5 8 4.5 8-4.5M12 12v9"/></svg>';
const projectWarningIcon='<svg class="card-warning" role="img" aria-label="需要处理" viewBox="0 0 16 16" focusable="false"><path d="M8 2.5l6 10.5H2zM8 6.5v3M8 11.3v.2"/></svg>';
function projectCardImage(p) {
  const outputs=(p.outputs || []).filter(output=>output.quality!=='thumbnail');
  return [...outputs].reverse().find(output=>output.shot_id==='hero') || outputs.at(-1) || null;
}
function projectCardStatus(p) {
  const outputs=(p.outputs || []).filter(output=>output.quality!=='thumbnail'),finals=outputs.filter(output=>['standard','fine'].includes(output.quality)).length,polished=outputs.filter(output=>output.quality==='showcase').length,previews=outputs.length-finals-polished,shotCount=p.plan?.shots?.length || 0;
  const job=p.active_job || p.jobs?.find(item=>['running','queued'].includes(item.status)),initial=p.initial_preview?.status;
  if(job || ['queued','running'].includes(initial) || p.status==='analyzing'){
    const kind=job?.kind || (p.status==='analyzing'?'analyze':'preview'),queued=job?job.status==='queued':initial==='queued';
    const label=job?.origin==='auto_polish'?'正在生成高清图':kind==='render'?'正在渲染 4K':kind==='analyze'?'模型分析中':kind==='hero_optimize'?'正在优化主图':job?.origin==='initial_preview' || (!job && initial)?'白底主图生成中':'正在生成预览';
    const value=Number(job?.progress),progress=!queued && job?.status==='running' && Number.isFinite(value) && value>0?Math.min(100,Math.round(value)):null;
    return {tone:'active',text:queued?label.replace(/^正在/,'')+' · 已排队':label+' · 后台继续',progress};
  }
  if(['failed','interrupted','cancelled'].includes(p.status) && !p.analysis){
    return {tone:'warning',text:p.status==='failed'?'分析失败 · 可重试':p.status==='cancelled'?'分析已取消 · 可继续':'分析已中断 · 可继续'};
  }
  if(['failed','interrupted'].includes(initial) && !outputs.length)return {tone:'warning',text:'白底主图需处理 · 可继续'};
  if(finals)return {tone:'done',text:`${finals} 张 4K 成品${shotCount?` · ${shotCount} 个镜头`:''}`};
  if(polished)return {tone:'done',text:`${polished} 张高清 2K${shotCount?` · ${shotCount} 个镜头`:''}`};
  const base=statusNames[p.status] || p.status || '可开始拍摄';
  return {tone:'ready',text:previews?`${base} · ${previews} 张预览`:base};
}
function renderHomeProjects() {
  $('homeProjectCount').textContent=allProjects.length;
  stableHTML($('homeProjectGrid'),allProjects.length ? allProjects.map(p=>{
    const image=projectCardImage(p),status=projectCardStatus(p);
    const marker=status.tone==='done'?'<span class="card-dot" aria-hidden="true"></span>':status.tone==='warning'?projectWarningIcon:'';
    const progress=status.tone==='active'?`<span class="project-card-progress" aria-hidden="true" ${status.progress===null?'data-progress="indeterminate"':''}><span${status.progress===null?'':` style="width:${status.progress}%"`}></span></span>`:'';
    return `<div class="project-card"><button type="button" class="project-card-open" data-open-project="${esc(p.id)}"><span class="project-card-media">${image?`<img src="${esc(image.jpg_url || image.png_url)}" alt="${esc(p.name)}${image.shot_id==='hero'?'白底主图':'预览'}" loading="lazy">`:projectPlaceholderIcon}</span><span class="project-card-body"><span class="project-card-name">${esc(p.name)}</span><span class="project-card-status" data-tone="${status.tone}">${marker}<span>${esc(status.text)}</span></span>${progress}</span></button><button type="button" class="icon-button project-card-delete" data-delete-project="${esc(p.id)}" aria-label="删除项目「${esc(p.name)}」" title="删除项目" ${navigationBlocked()?'disabled':''}>${deleteProjectIcon}</button></div>`;
  }).join('') : '<p class="muted">还没有摄影项目。上传一个模型，或用下方示例试一次。</p>');
}
// Home upload zone: the buttons open the native pickers directly; chosen or
// dropped files continue in the existing upload dialog for review and submit.
function pickUploadFiles(folder) {
  if(navigationBlocked())return toast('当前操作正在提交，完成后可新建项目。');
  if(uploadSubmitting){$('uploadDialog').showModal();return;}
  $(folder?'modelFolder':'modelFiles').click();
}
async function droppedFiles(event) {
  const entries=[...(event.dataTransfer?.items || [])].map(item=>item.webkitGetAsEntry?.()).filter(Boolean);
  return entries.length?(await Promise.all(entries.map(entry=>readEntry(entry)))).flat():[...(event.dataTransfer?.files || [])];
}
async function continueUpload(files) {
  if(uploadSubmitting)return;
  if(files?.length)addFiles(files);
  if(!$('uploadDialog').open)await showUpload();
}
const deleteProjectIcon='<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v5M14 11v5"/></svg>';
function realtimeAvailable() { return !!window.WuyingViewport && !!current?.analysis && activeShot==='hero' && system?.viewport?.available!==false && system?.runtime?.capabilities?.viewport!==false; }
function realtimeActive() { return realtimeAvailable() && realtime.mode==='live' && !comparePrevious && !feedbackMode && !instructionTargetSelection && !(inspectorTab==='overlay' && currentOutput); }
function disposeRealtime(clearContext=true) {
  realtime.epoch++;realtime.abort?.abort();clearTimeout(realtime.timer);realtime.instance?.dispose();
  realtime.instance=null;realtime.abort=null;realtime.timer=null;realtime.key='';realtime.record=null;realtime.changed=false;realtime.status='idle';realtime.error='';realtime.preservedView=null;
  if(clearContext){realtime.context='';realtime.mode='live';}
}
function realtimeIdentity() { const output=latestOutput(activeShot);return JSON.stringify([current?.id,current?.version,activeShot,output?sceneOutputKey(output):'']); }
function realtimeStillCurrent(epoch,key) { return epoch===realtime.epoch && key===realtime.key && !!current && key===realtimeIdentity(); }
function viewportFileURL(value,projectId) {
  if(typeof value!=='string')throw new Error('实时预览缺少模型文件。');
  const url=new URL(value,window.location.href);
  if(url.origin!==window.location.origin || !url.pathname.startsWith('/files/'+encodeURIComponent(projectId)+'/viewport/') || url.username || url.password)throw new Error('实时预览文件地址无效。');
  return url.pathname+url.search;
}
async function refreshRealtimeAsset(epoch=realtime.epoch,key=realtime.key,first=false) {
  if(!realtimeStillCurrent(epoch,key))return;
  const projectId=current.id,version=current.version,shotId=activeShot;
  try{
    const record=await api(`/api/projects/${projectId}/viewport${first?'':'/'+encodeURIComponent(realtime.record.id)}`,first?{method:'POST',body:{shot_id:shotId,expected_version:version},signal:realtime.abort.signal}:{signal:realtime.abort.signal});
    if(!realtimeStillCurrent(epoch,key))return;
    if(record.project_id!==projectId || record.version!==version || record.shot_id!==shotId)throw new Error('实时预览来源已变化，请重新载入。');
    realtime.record=record;
    if(['queued','running'].includes(record.status)){
      realtime.status='preparing';renderRealtimeStage();realtime.timer=setTimeout(()=>refreshRealtimeAsset(epoch,key),1500);return;
    }
    if(record.status!=='ready' || !record.asset)throw new Error(record.error || (record.status==='stale'?'方案已更新，请重新载入实时预览。':'实时模型未能生成，请重试。'));
    realtime.status='loading';renderRealtimeStage();
    const metadata=await api(viewportFileURL(record.asset.metadata_url,projectId),{signal:realtime.abort.signal});
    if(!realtimeStillCurrent(epoch,key))return;
    const asset={...record.asset,url:viewportFileURL(record.asset.url,projectId)};
    const instance=window.WuyingViewport.create($('realtimeViewport'),{
      onChange:changed=>{if(realtimeStillCurrent(epoch,key)){realtime.changed=changed;renderRealtimeControls();}},
      onError:error=>{if(realtimeStillCurrent(epoch,key)){realtime.error=error.message;realtime.status='failed';renderRealtimeStage();}}
    });
    realtime.instance=instance;
    await instance.load(asset,metadata,clone(shots().find(shot=>shot.id===shotId)),clone(current.plan),projectId);
    if(!realtimeStillCurrent(epoch,key)){instance.dispose();return;}
    const preservedView=realtime.preservedView;realtime.preservedView=null;
    if(preservedView)instance.restoreUnappliedView(preservedView);
    else if(dirty){
      const savedShot=current.plan.shots.find(shot=>shot.id===shotId),draftShot=shots().find(shot=>shot.id===shotId),view=instance.snapshot();
      const changes={};for(const field of ['projection','azimuth','elevation','focal_length','ortho_scale','camera_distance','target_offset'])if(JSON.stringify(draftShot?.[field])!==JSON.stringify(savedShot?.[field]))changes[field]=draftShot[field];
      const framing=draft.aspect!==current.plan.aspect || draft.margin!==current.plan.margin;
      if(framing || Object.keys(changes).length){
        if(draftShot.ortho_scale===undefined && (framing || 'azimuth' in changes || 'elevation' in changes))delete view.ortho_scale;
        instance.setShot({...view,...changes},draft,{autoFit:true});
      }
    }
    realtime.status='ready';realtime.changed=!!preservedView;renderRealtimeStage();renderRenderActivity();
  }catch(error){
    if(!realtimeStillCurrent(epoch,key) || error.name==='AbortError')return;
    realtime.instance?.dispose();realtime.instance=null;realtime.status='failed';realtime.error=error.message || '实时模型载入失败，请重试。';renderRealtimeStage();renderRenderActivity();
  }
}
function ensureRealtime() {
  if(!realtimeAvailable()){if(realtime.context)disposeRealtime();return;}
  const context=current.id+':'+activeShot,key=realtimeIdentity();
  if(realtime.context!==context){disposeRealtime();realtime.context=context;}
  if(key===realtime.key)return;
  let preservedView=null;
  const previousIdentity=realtime.key?JSON.parse(realtime.key):[],identity=JSON.parse(key);
  // A newly completed image can replace geometry/materials without changing
  // the plan version. Keep an orbit made while that render ran, but never
  // carry it into another project, shot, or plan version.
  if(realtime.changed && realtime.instance && identity.slice(0,3).every((value,i)=>value===previousIdentity[i])){try{preservedView=realtime.instance.snapshot();}catch(_){}}
  disposeRealtime(false);realtime.key=key;realtime.preservedView=preservedView;realtime.status='preparing';realtime.abort=new AbortController();
  refreshRealtimeAsset(realtime.epoch,key,true);
}
function renderRealtimeControls() {
  if(!$('viewModeSwitch'))return;
  const available=realtimeAvailable(),active=realtimeActive(),ready=active && realtime.status==='ready';
  $('viewModeSwitch').hidden=!available;$('liveViewButton').setAttribute('aria-pressed',String(active));$('savedViewButton').setAttribute('aria-pressed',String(!active));
  $('savedViewButton').disabled=!currentOutput;
  $('resetViewport').hidden=!ready;$('applyViewportView').hidden=!ready || !realtime.changed;
  $('applyViewportView').disabled=projectWritePending() || projectLoading || !!activeJob() || automaticMaterialBusy();
  if(ready)$('viewportHint').textContent=realtime.changed?'视角已调整 · 点击预览将使用当前视角':'拖动旋转 · 滚轮缩放 · 右键平移';
  $('zoomImage').title=active?'全屏查看 3D':'查看原图';$('zoomImage').setAttribute('aria-label',active?'全屏查看 3D':'查看原图');
}
function renderRealtimeStage() {
  const active=realtimeActive(),ready=active && realtime.status==='ready';
  $('viewer').classList.toggle('is-realtime',active);
  $('realtimeViewport').hidden=!ready;realtime.instance?.setVisible(ready);
  $('viewportStatus').hidden=!active || ready;$('viewportHint').hidden=!ready;
  const failed=realtime.status==='failed';$('viewportStatus').dataset.state=failed?'failed':'loading';$('retryViewport').hidden=!failed;
  setText('viewportStatusTitle',failed?'实时预览未能载入':realtime.status==='loading'?'正在载入 3D 画面':'正在准备实时模型');
  setText('viewportStatusMessage',failed?realtime.error:'首次载入需要转换模型，完成后即可拖动查看。');
  if(active){
    $('renderImage').hidden=true;$('renderEmpty').hidden=true;$('feedbackMarkers').hidden=true;$('imageDownloads').hidden=true;
    const projection=realtime.instance?.camera?.isPerspectiveCamera?'透视':'正交';
    setText('renderKind',ready?'实时预览 · '+projection:'实时预览');$('renderKind').title='支持拖动查看真实模型。实时材质与光影为交互预览，最终效果以渲染图片为准。';
    setText('imageMeta','');setText('previewNotice',dirty?'有未保存的摄影参数；预览时将一并保存。':'');$('previewNotice').hidden=!dirty;
  }else {$('renderKind').removeAttribute('title');$('feedbackMarkers').hidden=false;}
  renderRealtimeControls();
}
function setViewerMode(mode) {
  realtime.mode=mode==='image'?'image':'live';
  if(mode==='live'){comparePrevious=false;feedbackMode=false;instructionTargetSelection=null;}
  renderViewer();renderActionState();
}
function applyRealtimeView(announce=true) {
  if(!realtimeActive() || realtime.status!=='ready' || !realtime.instance || !realtime.changed)return false;
  const shot=shots().find(item=>item.id===activeShot);if(!shot)return false;
  const parameters=realtime.instance.snapshot();
  stashCamera();Object.assign(shot,parameters);cameraShot=activeShot;$('cameraShot').value=cameraShot;syncCamera();
  realtime.instance.acceptView();realtime.changed=false;markDirty();renderRealtimeControls();
  if(announce)toast('当前视角已用于此镜头，点击预览即可出图。');return true;
}
function updateRealtimeFraming() {
  if(!realtimeActive() || realtime.status!=='ready' || !realtime.instance)return;
  try{
    const shot=shots().find(item=>item.id===activeShot),view=realtime.instance.snapshot();
    if(shot.ortho_scale===undefined && !realtime.changed)delete view.ortho_scale;
    realtime.instance.setShot({...shot,...view},draft,{autoFit:true});realtime.changed=true;renderRealtimeStage();
  }catch(error){toast(error.message,true);}
}
function renderViewer() {
  const shot = shots().find(s => s.id === activeShot) || shots()[0];if (!shot) return;activeShot = shot.id;
  const latest=latestOutput(activeShot),displayed=displayedShotOutput(activeShot),latestKey=displayed?current.id+':'+sceneOutputKey(displayed):current.id+':'+activeShot;
  if(viewerLatestKey!==latestKey){comparePrevious=false;viewerLatestKey=latestKey;}
  const previous=displayed?.quality==='thumbnail'?null:previousOutput(latest);
  currentOutput = displayed?.quality==='thumbnail'?null:(comparePrevious && previous?previous:latest);$('shotTitle').textContent = shot.label;
  $('comparePreview').hidden=!previous;$('comparePreview').setAttribute('aria-pressed',comparePrevious);
  const sameVersion=previous && previous.version===latest.version;
  $('comparePreview').textContent=comparePrevious?'返回最新 v'+latest.version:sameVersion?'对比优化前':'对比上一版 v'+(previous?.version || '');
  $('previewNotice').textContent=outputNotice(currentOutput,latest);$('previewNotice').hidden=!$('previewNotice').textContent;
  if (currentOutput) {
    const o=currentOutput;if ($('renderImage').getAttribute('src') !== o.png_url) $('renderImage').src=o.png_url;
    $('renderImage').hidden=false;$('renderEmpty').hidden=true;
    const old=o.version!==current.version;$('renderKind').textContent=sceneQualityLabel(o.quality,o)+(old?' · 旧版':'');$('viewer').dataset.quality=o.quality;
    $('imageMeta').textContent=`${o.width} × ${o.height} px`;
    $('imageDownloads').hidden=false;for (const [id,key] of [['downloadPng','png_url'],['downloadJpg','jpg_url']]) {$(id).hidden=!o[key];if(o[key])$(id).href=o[key]+'?download=1';}
    $('sceneExportButton').disabled=false;
  } else if(displayed?.quality==='thumbnail') {
    const thumb=displayed;if($('renderImage').getAttribute('src')!==thumb.png_url)$('renderImage').src=thumb.png_url;
    $('renderImage').hidden=false;$('renderEmpty').hidden=true;$('imageDownloads').hidden=true;
    $('renderKind').textContent='镜头缩略图'+(thumb.version!==current.version?' · 旧版':'');$('viewer').dataset.quality='thumbnail';
    $('imageMeta').textContent=`${thumb.width} × ${thumb.height} px · 低分辨率`; $('sceneExportButton').disabled=true;
  } else {
    $('renderImage').hidden=true;$('renderEmpty').hidden=false;$('imageDownloads').hidden=true;
    $('renderKind').textContent='等待预览';$('imageMeta').textContent='';
    const initial=current.initial_preview,running=activeJob(),buildingHero=activeShot==='hero' && running?.origin==='initial_preview';
    $('renderEmpty').querySelector('h2').textContent=!current.analysis && !['failed','cancelled','interrupted'].includes(current.status)?initial?.status==='pending'?'正在分析模型，完成后自动生成白底主图':'正在分析模型':buildingHero?running.status==='queued'?'白底主图已排队':'正在生成白底主图':current.analysis && activeShot==='hero' && initial?.status==='pending'?'正在准备白底主图':'当前镜头还没有预览';
  }
  renderFeedback();ensureRealtime();renderRealtimeStage();renderReview();renderRenderActivity();renderLatestSceneDownloads();renderLayerStage();
}
function sceneOutputKey(output) {
  return JSON.stringify([output.job_id || '',output.shot_id,output.version,output.quality,output.created_at || '',output.blend_url || '']);
}
function sceneQualityLabel(quality,output=null) {
  if(quality==='thumbnail')return '镜头缩略图';
  if(quality==='generated')return 'AI 生成';
  if(quality==='preview')return '快速预览';
  if(quality==='showcase')return '高清 2K';
  const fourK=Math.max(Number(output?.width)||0,Number(output?.height)||0)>=4096;
  if(quality==='hero')return fourK?'4K 主图':'高清主图';
  if(quality==='fine')return fourK?'精细 4K':'精细成品';
  if(quality==='standard')return fourK?'标准 4K':'高清成品';
  return '场景快照';
}
function sceneShotLabel(id) { return current?.plan?.shots?.find(shot=>shot.id===id)?.label || id || '镜头'; }
function sceneOutputLabel(output) {
  const stamp = output.created_at ? new Date(typeof output.created_at==='number'?output.created_at*1000:output.created_at) : null;
  const date = stamp && !Number.isNaN(stamp.getTime()) ? ' · '+stamp.toLocaleString('zh-CN',{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit',second:'2-digit'}) : '';
  return `${sceneShotLabel(output.shot_id)} · v${output.version} · ${sceneQualityLabel(output.quality,output)}${date}`;
}
function sceneSourceKey(projectId,output) {
  return JSON.stringify([projectId,output?.job_id,output?.shot_id,output?.version,output?.quality,output?.png_url,output?.blend_url,output?.blend_sha256 || '']);
}
function sceneExportMatches(record,output,format) {
  if(!output?.png_url || !output?.blend_url || record.project_id!==current?.id || record.format!==format)return false;
  return record.output_id===output.job_id && record.shot_id===output.shot_id && record.version===output.version && record.quality===output.quality && record.source_png_url===output.png_url && record.source_blend_url===output.blend_url && (!output.blend_sha256 || record.source_sha256===output.blend_sha256);
}
function matchingSceneExports(output,format) {
  return (current?.exports || []).filter(record=>sceneExportMatches(record,output,format)).sort((a,b)=>(b.created_at || 0)-(a.created_at || 0));
}
function nativeSceneUrl(url,format) {
  return typeof url==='string' && url.startsWith('/files/'+current?.id+'/') && url.toLowerCase().endsWith('.'+format) ? url : '';
}
function sceneExportBody(source,format) {
  return {format,output_id:source.job_id,shot_id:source.shot_id,quality:source.quality,version:source.version,source_png_url:source.png_url,source_blend_url:source.blend_url,...(source.blend_sha256?{source_sha256:source.blend_sha256}:{})};
}
function c4dGenerationReason(source) {
  const capability=system?.scene_exports;
  if(source?.quality==='generated')return 'AI 生成的场景图没有 3D 工程可下载。';
  if(!nativeSceneUrl(source?.blend_url,'blend'))return '此预览未保存工程，重新生成预览后即可下载。';
  if(serviceDisconnected)return '本机服务连接中断，恢复连接后可生成 C4D 文件。';
  if(capability?.c4d_status==='license_required')return '请先完成 Cinema 4D 命令行授权。';
  if(capability?.c4d_executable_found===false || capability?.c4d_status==='unavailable')return '请先配置本机 Cinema 4D 转换环境。';
  if(capability?.export_available===false || capability?.c4d_export_available===false || system?.runtime?.capabilities?.export_c4d===false)return '请先补齐 Cinema 4D 转换组件。';
  return '';
}
function setNativeSceneLink(id,url,source,format) {
  const link=$(id);
  if(url){
    link.href=url+'?download=1';link.download=`${current.name}-${sceneShotLabel(source.shot_id)}-v${source.version}.${format}`.replace(/[\\/:*?"<>|]/g,'_');
    link.removeAttribute('aria-disabled');link.removeAttribute('tabindex');
  }else{link.removeAttribute('href');link.setAttribute('aria-disabled','true');link.tabIndex=-1;}
}
function renderLatestSceneDownloads() {
  const source=current && latestOutput(activeShot),panel=$('latestSceneDownloads');
  panel.hidden=!source;
  setNativeSceneLink('downloadLatestBlend','',source,'blend');setNativeSceneLink('downloadLatestC4d','',source,'c4d');
  $('downloadLatestC4d').hidden=true;$('generateLatestC4d').hidden=!source;$('latestSceneDetails').hidden=true;
  $('generateLatestC4d').disabled=true;$('generateLatestC4d').removeAttribute('aria-busy');
  $('generateLatestC4d').textContent='生成 C4D 文件';$('generateLatestC4d').title='';
  $('latestSceneStatus').textContent='';$('latestSceneStatus').hidden=true;$('latestSceneSource').textContent='';
  if(!source)return;
  $('latestSceneSource').textContent=`${sceneShotLabel(source.shot_id)} · v${source.version} · 最新已渲染`;
  const blends=matchingSceneExports(source,'blend'),c4ds=matchingSceneExports(source,'c4d');
  const blend=blends.find(record=>record.status==='completed' && nativeSceneUrl(record.native_url,'blend'));
  setNativeSceneLink('downloadLatestBlend',blend?.native_url || nativeSceneUrl(source.blend_url,'blend'),source,'blend');
  const native=c4ds.find(record=>record.status==='completed' && nativeSceneUrl(record.native_url,'c4d'));
  const running=c4ds.find(record=>['queued','running'].includes(record.status));
  const key=sceneSourceKey(current.id,source),submitting=latestSceneRequests.has(key),reason=c4dGenerationReason(source);
  let status='';
  if(native){
    setNativeSceneLink('downloadLatestC4d',native.native_url,source,'c4d');$('downloadLatestC4d').hidden=false;$('generateLatestC4d').hidden=true;
  }else if(submitting || running){
    $('generateLatestC4d').textContent=submitting?'正在提交 C4D…':running.status==='queued'?'C4D 排队中…':'正在生成 C4D…';
    $('generateLatestC4d').setAttribute('aria-busy',String(!serviceDisconnected));
    status=serviceDisconnected?'本机服务连接中断，恢复连接后查看生成结果。':running?.stage || '';
  }else if(c4ds.length){
    $('generateLatestC4d').hidden=true;$('latestSceneDetails').hidden=false;
    status=c4ds[0].conversion_package_url || c4ds[0].package_url?'尚未生成 .c4d 文件，转换包可在详情中查看。':'C4D 文件尚未生成，查看详情后可重试。';
  }else{
    $('generateLatestC4d').disabled=!!reason || sceneExportSubmitting;
    $('generateLatestC4d').title=reason;
    $('latestSceneDetails').hidden=!reason;
    status=latestSceneErrors.get(key) || reason;
  }
  if(!status && (source.version!==current.version || dirty))status='尚未出图的调整不包含在工程内。';
  $('latestSceneStatus').textContent=status;$('latestSceneStatus').hidden=!status;
}
async function generateLatestC4d() {
  const source=current && latestOutput(activeShot);
  if(!source || sceneExportSubmitting || c4dGenerationReason(source))return;
  const projectId=current.id,key=sceneSourceKey(projectId,source),records=matchingSceneExports(source,'c4d');
  if(latestSceneRequests.has(key) || records.some(record=>['queued','running'].includes(record.status) || record.status==='completed' && nativeSceneUrl(record.native_url,'c4d')))return;
  const snapshot=clone(source);latestSceneRequests.set(key,true);latestSceneErrors.delete(key);renderLatestSceneDownloads();
  if($('sceneExportDialog').open)renderSceneExportControls();
  try{
    const record=await api(`/api/projects/${projectId}/exports`,{method:'POST',body:sceneExportBody(snapshot,'c4d')});
    if(current?.id===projectId){current.exports=[...(current.exports || []).filter(item=>item.id!==record.id),record];renderSceneExports();}
  }catch(error){latestSceneErrors.set(key,error.message);}
  finally{latestSceneRequests.delete(key);renderLatestSceneDownloads();if($('sceneExportDialog').open)renderSceneExportControls();}
}
function renderSceneExportControls() {
  const format = document.querySelector('[name=sceneExportFormat]:checked')?.value || 'blend';
  const capability = system?.scene_exports;
  const source = sceneExportSource;
  $('downloadBlend').hidden=!source?.blend_url;
  if(source?.blend_url)$('downloadBlend').href=source.blend_url+'?download=1';else $('downloadBlend').removeAttribute('href');
  const c4d = format==='c4d';
  $('sceneExportContext').textContent = source ? `已选：${sceneShotLabel(source.shot_id)} · 方案 v${source.version} · ${sceneQualityLabel(source.quality,source)}。${source.version!==current?.version || dirty?'当前方案有后续调整，导出的仍是所选图片对应的场景。':'相机、灯光与材质取自这张图片的场景快照。'}` : '请先生成一张预览或成品，再导出对应场景。';
  $('sceneExportC4dNote').hidden=!c4d;
  let capabilityMessage;
  if (c4d) {
    const statusMessage = {ready:'本机 Cinema 4D 转换已验证。',unverified:'已找到 Cinema 4D；启动与许可证尚未验证，将在导出时检查。',unavailable:'本机 Cinema 4D 转换不可用；仍可尝试生成手动转换包。',license_required:'Cinema 4D 许可证尚未就绪；仍可尝试生成手动转换包。'};
    capabilityMessage = capability ? (statusMessage[capability.c4d_status] || 'Cinema 4D 转换能力尚未验证。') : 'Cinema 4D 转换能力尚未读取，将在导出时由本机服务检查。';
    if (capability?.c4d_message) capabilityMessage+=' '+capability.c4d_message;
  } else capabilityMessage = capability?.blender_available===false ? '本机 Blender 不可用，请检查运行环境；服务将检查已有场景能否打包。' : capability?.blender_available ? '本机 Blender 可用，可打包原生场景与贴图。' : '导出时将检查所选 Blender 场景与贴图。';
  $('sceneExportCapability').textContent=capabilityMessage;
  $('sceneExportCapability').classList.toggle('needs-review',c4d && capability?.c4d_status!=='ready');
  const unavailable=capability?.export_available===false || (c4d && capability?.c4d_export_available===false);
  if(unavailable)$('sceneExportCapability').textContent=(system?.blender_available===false?'本机 Blender 不可用，请先配置 Blender。':system?.runtime?.missing_components?.length?'场景导出组件不完整，请查看运行环境并补齐组件。':'当前工程导出暂不可用，请查看运行环境。')+' 已生成的工程仍可从记录下载。';
  const directPending=source && c4d && (latestSceneRequests.has(sceneSourceKey(sceneExportProjectId,source)) || matchingSceneExports(source,'c4d').some(record=>['queued','running'].includes(record.status)));
  $('sceneExportSubmit').disabled=sceneExportSubmitting || directPending || !source || current?.id!==sceneExportProjectId || unavailable;
  $('sceneExportSource').disabled=sceneExportSubmitting || !source;
  document.querySelectorAll('[name=sceneExportFormat]').forEach(input=>input.disabled=sceneExportSubmitting);
  $('sceneExportSubmit').textContent=sceneExportSubmitting?'正在提交…':directPending?'C4D 正在生成…':!c4d?'生成 Blender 工程包':['unavailable','license_required'].includes(capability?.c4d_status)?'尝试转换 / 生成转换包':'生成 C4D 工程包';
}
function renderSceneExports() {
  renderLatestSceneDownloads();
  if (!$('sceneExportDialog').open || !current || sceneExportProjectId!==current.id) return;
  const outputs=[...(current.outputs || [])].filter(output=>output.quality!=='thumbnail').sort((a,b)=>b.version-a.version || (b.created_at || 0)-(a.created_at || 0));
  const signature=JSON.stringify(outputs.map(output=>[sceneOutputKey(output),sceneOutputLabel(output)]));
  if (signature!==sceneExportOptionsSignature) {
    const selected=sceneExportSource ? sceneOutputKey(sceneExportSource) : '';
    options($('sceneExportSource'),outputs.map(output=>({value:sceneOutputKey(output),label:sceneOutputLabel(output)})),selected);
    // Keep the chosen snapshot stable when a new render arrives during export.
    if (!outputs.some(output=>sceneOutputKey(output)===selected)) sceneExportSource=outputs[0]?clone(outputs[0]):null;
    sceneExportOptionsSignature=signature;
  }
  renderSceneExportControls();
  const records=[...(current.exports || [])].sort((a,b)=>(b.created_at || 0)-(a.created_at || 0));
  const jobsSignature=JSON.stringify(records);
  if (jobsSignature===sceneExportJobsSignature) return;
  sceneExportJobsSignature=jobsSignature;
  const statuses={queued:'等待导出',running:'正在导出',completed:'工程已完成',failed:'导出失败',interrupted:'导出已中断',cancelled:'导出已取消'};
  $('sceneExportJobs').innerHTML=records.length?records.map(record=>{
    const running=['queued','running'].includes(record.status),completed=record.status==='completed';
    const format=record.format==='c4d'?'Cinema 4D':'Blender';
    const native=nativeSceneUrl(record.native_url,record.format),packageUrl=record.conversion_package_url || record.package_url;
    const warnings=Array.isArray(record.warnings)?record.warnings:record.warnings?[record.warnings]:[];
    return `<article class="scene-export-job ${running?'is-running':''} ${['failed','interrupted'].includes(record.status)?'is-failed':''}"><div class="scene-export-job-heading"><strong>${format} <span>.${esc(record.format)}</span></strong><span class="pill">${esc(statuses[record.status] || record.status)}</span></div><p class="scene-export-job-context">${esc(sceneOutputLabel(record))}</p>${record.stage?`<p class="scene-export-stage">${running?'<span class="scene-export-activity" aria-hidden="true"></span>':''}${esc(record.stage)}</p>`:''}${record.error?`<p class="job-error">${esc(record.error)}</p>`:''}${warnings.length?`<ul class="scene-export-warnings">${warnings.map(warning=>`<li>${esc(typeof warning==='string'?warning:JSON.stringify(warning))}</li>`).join('')}</ul>`:''}<div class="scene-export-links">${completed && record.download_url?`<a class="button secondary" href="${esc(record.download_url)}" download>下载 ${format} 工程 ZIP</a>`:''}${completed && native?`<a href="${esc(native)}" download>单独下载 .${esc(record.format)}</a>`:''}${packageUrl && (!completed || packageUrl!==record.download_url)?`<a class="button secondary" href="${esc(packageUrl)}" download>下载手动转换包（非 .c4d 工程）</a>`:''}</div></article>`;
  }).join(''):'<p class="scene-export-empty">还没有导出记录。选择格式后即可生成工程包。</p>';
  $('sceneExportAnnouncement').textContent=records.length?`${records.length} 条导出记录。最近一次 ${records[0].format==='c4d'?'Cinema 4D':'Blender'}：${statuses[records[0].status] || records[0].status}。${records[0].stage || ''}`:'还没有导出记录。';
}
async function showSceneExport(source=currentOutput,format=null) {
  if (!source || !current) return toast('生成预览后即可导出对应场景。',true);
  sceneExportProjectId=current.id;sceneExportSource=clone(source);sceneExportOptionsSignature='';sceneExportJobsSignature='';
  if(format)document.querySelector(`[name=sceneExportFormat][value="${format}"]`).checked=true;
  $('sceneExportError').textContent='';$('sceneExportDialog').showModal();renderSceneExports();
  try {system=await api('/api/system');if($('sceneExportDialog').open)renderSceneExportControls();} catch(error) {$('sceneExportCapability').textContent='暂时无法读取运行环境。提交后以实际导出结果为准。';}
}
async function submitSceneExport(event) {
  event.preventDefault();
  if(sceneExportSubmitting || !sceneExportSource || current?.id!==sceneExportProjectId || $('sceneExportSubmit').disabled)return;
  const projectId=sceneExportProjectId,source=clone(sceneExportSource),format=document.querySelector('[name=sceneExportFormat]:checked').value;
  sceneExportSubmitting=true;$('sceneExportError').textContent='';renderSceneExportControls();renderLatestSceneDownloads();
  try {
    const record=await api(`/api/projects/${projectId}/exports`,{method:'POST',body:sceneExportBody(source,format)});
    if(current?.id===projectId){current.exports=[...(current.exports || []).filter(item=>item.id!==record.id),record];renderSceneExports();}
    toast('已提交场景导出，可在导出记录中查看进度。');
  } catch(error) {if(sceneExportProjectId===projectId)$('sceneExportError').textContent=error.message;}
  finally {sceneExportSubmitting=false;renderLatestSceneDownloads();if($('sceneExportDialog').open)renderSceneExportControls();}
}
function aiStatusLabel(settings=system?.ai) {
  if (!settings) return system?.ai_mode==='ai'?'AI 已配置 · 尚未验证':'内置规则模式';
  const labels={missing_key:'待填写 API Key',missing_model:'待选择模型',disabled:'AI 已关闭 · 内置规则',configured:'AI 已配置 · 尚未验证',verified:'AI 连接已验证',error:'AI 连接需要处理',blocked_model:'此型号已停用，请更换模型',unsupported_vision:'此型号不支持图像，请更换视觉模型'};
  return labels[settings.status] || settings.message || 'AI 尚未验证';
}
function aiRequestAllowed(settings) {
  if(!settings || settings.status==='blocked_model' || settings.blocked_reason)return false;
  return settings.request_allowed!==undefined?settings.request_allowed===true:!!(settings.enabled && settings.key_configured && settings.model);
}
function visionState() {
  const settings=system?.ai?.vision_config || system?.ai;
  if(!settings)return {status:system?.ai_mode==='ai'?'configured':'missing',model:'',attemptable:system?.ai_mode==='ai'};
  const attemptable=aiRequestAllowed(settings);
  const status=settings.blocked_reason || settings.status==='blocked_model'?settings.status || 'blocked_model':!settings.enabled?'disabled':!settings.key_configured?'missing_key':!settings.model?'missing_model':settings.status || 'configured';
  return {status,model:settings.model || '',attemptable};
}
function updateAiMode() { $('aiSettingsButton').title=aiStatusLabel(system?.ai); }
function applyAiPublic(settings) {
  aiSettings=settings;system=system || {};system.ai=settings;
  system.ai_mode=aiRequestAllowed(settings)?'ai':'rules';
  // The image model follows the same settings; the scene panel shows whether it can be used.
  if(sceneLibrary && settings?.image_config && typeof settings.image_config==='object')sceneLibrary.image=settings.image_config;
  updateAiMode();renderFeedback();if(current){renderReview();renderMaterialAgent();renderSceneSet();}
}
function aiEndpoint(provider,base) { return JSON.stringify([provider || '',String(base || '').trim().replace(/\/+$/,'')]); }
function aiFormEndpoint(prefix) { return aiEndpoint($(prefix+'Provider').value,$(prefix+'BaseUrl').value); }
function aiPresetOptions() { return (aiSettings?.model_presets || []).filter(item=>item && ['openai','anthropic'].includes(item.provider) && item.id && item.label && item.base_url && item.model); }
function matchingAiPreset() { return aiPresetOptions().find(item=>aiEndpoint(item.provider,item.base_url)===aiFormEndpoint('ai') && item.model===$('aiModel').value.trim()); }
function renderAiPresets() {
  $('aiModelPreset').innerHTML='<option value="">自定义 / 当前配置</option>'+aiPresetOptions().map(item=>`<option value="${esc(item.id)}">${esc(item.label)}</option>`).join('');
  $('aiModelPreset').value=matchingAiPreset()?.id || '';
  $('aiPresetHint').textContent='选择后可继续修改，点击保存后生效。';
}
function officialHaiEndpoint(base) {
  try{const url=new URL(base);return url.protocol==='https:' && url.hostname==='api.hai.network' && (!url.port || url.port==='443') && !url.username && !url.password && !url.search && !url.hash && /^\/(?:openai|anthropic|google)(?:\/v1(?:\/(?:chat\/completions|messages))?)?\/?$/.test(url.pathname);}catch(_){return false;}
}
function visionSharesMainKey() {
  return aiFormEndpoint('vision')===aiFormEndpoint('ai') || (officialHaiEndpoint($('visionBaseUrl').value.trim()) && officialHaiEndpoint($('aiBaseUrl').value.trim()));
}
function clearKeyForChangedEndpoint(prefix) {
  const endpoint=aiFormEndpoint(prefix);
  if(aiDraftEndpoints[prefix] && aiDraftEndpoints[prefix]!==endpoint){$(prefix+'ApiKey').value='';$(prefix+'ClearKey').checked=false;}
  aiDraftEndpoints[prefix]=endpoint;
}
function inheritVisionDraft() {
  for(const field of ['Provider','BaseUrl','Model'])setValue('vision'+field,$('ai'+field).value);
  clearKeyForChangedEndpoint('vision');
}
function renderAiKeyHints() {
  const changed=aiFormEndpoint('ai')!==aiEndpoint(aiSettings?.provider,aiSettings?.base_url),keyLabel=matchingAiPreset()?.key_label || '当前服务商的 API Key';
  $('aiKeyHint').textContent=$('aiClearKey').checked?'保存后将清除已保存的 Key。':changed?`服务地址或接口已更换。请填写${keyLabel}；原 Key 不会用于新地址。`:aiSettings?.key_configured?'已保存 Key。留空保留，填写新 Key 将替换。':`尚未保存 Key。请填写${keyLabel}。`;
  $('aiKeyHint').classList.toggle('needs-key',changed && !$('aiApiKey').value.trim());
  $('aiApiKey').placeholder=!changed && aiSettings?.key_configured?'留空保留已保存的 Key':`填写${keyLabel}`;
  const vision=aiSettings?.vision_config || aiSettings,visionChanged=aiFormEndpoint('vision')!==aiEndpoint(vision?.provider,vision?.base_url),shared=visionSharesMainKey();
  $('visionKeyHint').textContent=$('visionClearKey').checked?'保存后清除视觉专用 Key；同一服务的主 Key 仍可供视觉分析使用。':shared?'可使用上面的 Key；填写专用 Key 将优先使用专用 Key。':visionChanged?'视觉服务地址或接口已更换，请填写此服务商的新 Key。原 Key 不会用于新地址。':vision?.key_configured?'已配置视觉 Key，留空保留。': '请填写此视觉服务商的 API Key。';
  $('visionKeyHint').classList.toggle('needs-key',!shared && (visionChanged || !vision?.key_configured) && !$('visionApiKey').value.trim());
  $('visionApiKey').placeholder=shared?'留空使用主服务 Key':!visionChanged && vision?.key_configured?'留空保留已保存的视觉 Key':'填写此服务商的 API Key';
}
function aiDraftChanged(prefix,settings) {
  const enabled=prefix==='vision'?$('visionEnabled').checked:$('aiEnabled').checked;
  return aiFormEndpoint(prefix)!==aiEndpoint(settings?.provider,settings?.base_url) || $(prefix+'Model').value.trim()!==(settings?.model || '') || enabled!==(settings?.enabled!==false) || !!$(prefix+'ApiKey').value || $(prefix+'ClearKey').checked || (prefix==='vision' && enabled && settings?.inherits);
}
function renderAiConnection(settings) {
  for(const [prefix,config] of [['ai',settings],['vision',settings?.vision_config || settings]]){
    const changed=aiDraftChanged(prefix,config),blocked=(config?.status==='blocked_model' || !!config?.blocked_reason) && $(prefix+'Model').value.trim()===config?.model;
    const element=$(prefix==='ai'?'aiConnectionStatus':'visionConnectionStatus');
    element.textContent=blocked?(config.blocked_reason || '此型号已停用。请从常用配置选择其他模型后保存。'):changed?'新配置尚未保存，保存后可测试连接。':aiStatusLabel(config)+(config?.message?'：'+config.message:'');
    element.classList.toggle('is-error',blocked || (!changed && config?.status==='error'));
    element.classList.toggle('is-verified',!blocked && !changed && config?.status==='verified');
  }
  renderAiKeyHints();
}
function renderVisionFields(){const enabled=$('visionEnabled').checked;$('visionFields').hidden=!enabled;for(const id of ['visionProvider','visionBaseUrl','visionModel','visionApiKey','visionClearKey','visionSaveTest'])$(id).disabled=aiSettingsBusy || !enabled;$('visionBaseUrl').required=enabled;$('visionModel').required=enabled;}
function refreshAiSettingsDraft(prefix='ai') {
  clearKeyForChangedEndpoint(prefix);
  if(!$('visionEnabled').checked)inheritVisionDraft();
  $('aiModelPreset').value=matchingAiPreset()?.id || '';
  $('aiSettingsError').textContent='';renderAiConnection(aiSettings);
}
function selectAiPreset() {
  const preset=aiPresetOptions().find(item=>item.id===$('aiModelPreset').value);if(!preset)return;
  const vision=aiSettings?.vision_config;
  const replaceBlockedVision=$('visionEnabled').checked && !!vision?.blocked_reason && $('visionModel').value.trim()===vision.model;
  setValue('aiProvider',preset.provider);setValue('aiBaseUrl',preset.base_url);setValue('aiModel',preset.model);
  if(replaceBlockedVision){$('visionEnabled').checked=false;$('visionApiKey').value='';$('visionClearKey').checked=false;renderVisionFields();}
  refreshAiSettingsDraft();
  $('aiPresetHint').textContent=replaceBlockedVision?'已将停用的视觉型号改为沿用此模型，点击保存后一起生效。':'选择后可继续修改，点击保存后生效。';
}
async function showAiSettings() {
  $('aiSettingsError').textContent='';$('aiApiKey').value='';$('aiClearKey').checked=false;$('visionApiKey').value='';$('visionClearKey').checked=false;
  try {
    const settings=await api('/api/ai/settings');applyAiPublic(settings);
    setValue('aiProvider',settings.provider || 'openai');setValue('aiBaseUrl',settings.base_url || '');setValue('aiModel',settings.model || '');$('aiEnabled').checked=settings.enabled!==false;
    const vision=settings.vision_config || {}; $('visionEnabled').checked=vision.enabled===true && !vision.inherits;
    const independent=vision.inherits===false;
    setValue('visionProvider',independent?vision.provider || settings.provider || 'openai':settings.provider || 'openai');setValue('visionBaseUrl',independent?vision.base_url || '':settings.base_url || '');setValue('visionModel',independent?vision.model || '':settings.model || '');
    aiDraftEndpoints.ai=aiFormEndpoint('ai');aiDraftEndpoints.vision=aiFormEndpoint('vision');renderAiPresets();renderVisionFields();
    renderAiConnection(settings);if(!$('aiSettingsDialog').open)$('aiSettingsDialog').showModal();
  } catch(error){toast('无法读取 AI 设置：'+error.message,true);}
}
function setAiSettingsBusy(value) {
  aiSettingsBusy=value;
  for(const id of ['aiSave','aiSaveTest','aiModelPreset','aiProvider','aiBaseUrl','aiModel','aiApiKey','aiEnabled','aiClearKey','visionEnabled'])$(id).disabled=value;
  renderVisionFields();
  $('aiSaveTest').textContent=value?'正在处理…':'保存并测试连接';
}
function aiTestUnavailable(settings) {
  if(settings?.blocked_reason || settings?.status==='blocked_model')return settings.blocked_reason || '此型号已停用，请选择其他模型后保存。';
  if(!settings?.enabled)return '请先启用 AI 服务，再测试连接。';
  if(!settings?.key_configured)return '请填写当前服务地址的 API Key，再点击“保存并测试连接”。';
  if(!settings?.model)return '请选择模型或填写模型 ID 后重试。';
  return '当前配置暂不可调用，请核对服务地址、模型 ID 和 Key 后重试。';
}
async function saveAiSettings(test=false) {
  if(aiSettingsBusy || !$('aiSettingsForm').reportValidity())return;
  clearKeyForChangedEndpoint('ai');clearKeyForChangedEndpoint('vision');
  const body={provider:$('aiProvider').value,base_url:$('aiBaseUrl').value.trim(),model:$('aiModel').value.trim(),enabled:$('aiEnabled').checked};
  if($('visionEnabled').checked){body.vision_enabled=true;body.vision_provider=$('visionProvider').value;body.vision_base_url=$('visionBaseUrl').value.trim();body.vision_model=$('visionModel').value.trim();if(!body.vision_base_url || !body.vision_model){$('aiSettingsError').textContent='请填写视觉服务地址和模型名称。';return;}if($('visionClearKey').checked)body.vision_clear_key=true;else if($('visionApiKey').value.trim())body.vision_api_key=$('visionApiKey').value.trim();else if(!visionSharesMainKey() && aiFormEndpoint('vision')!==aiEndpoint(aiSettings?.vision_config?.provider,aiSettings?.vision_config?.base_url))body.vision_clear_key=true;}
  else if(aiSettings?.vision_config && !aiSettings.vision_config.inherits){body.vision_provider='';body.vision_base_url='';body.vision_model='';body.vision_enabled='';body.vision_clear_key=true;}
  if($('aiClearKey').checked)body.clear_key=true;
  else if($('aiApiKey').value.trim())body.api_key=$('aiApiKey').value.trim();
  else if(aiFormEndpoint('ai')!==aiEndpoint(aiSettings?.provider,aiSettings?.base_url))body.clear_key=true;
  setAiSettingsBusy(true);$('aiSettingsError').textContent='';let saved=false;
  try {
    const settings=await api('/api/ai/settings',{method:'PUT',body});
    saved=true;
    $('aiApiKey').value='';$('aiClearKey').checked=false;$('visionApiKey').value='';$('visionClearKey').checked=false;delete body.api_key;delete body.vision_api_key;applyAiPublic(settings);renderAiConnection(settings);
    if(test){
      const visionTest=test==='vision',testedSettings=visionTest?settings.vision_config || settings:settings;
      if(!aiRequestAllowed(testedSettings)){$('aiSettingsError').textContent='设置已保存。'+aiTestUnavailable(testedSettings);return;}
      $(visionTest?'visionConnectionStatus':'aiConnectionStatus').textContent=visionTest?'正在使用最近的真实预览测试图像分析…':'正在向已保存的服务地址发起真实连接测试…';
      const result=await api(visionTest?'/api/ai/vision/test':'/api/ai/test',{method:'POST',body:{}});
      if(result.settings){applyAiPublic(result.settings);renderAiConnection(result.settings);}
      else renderAiConnection(settings);
      if(!result.success){
        const connection=$(visionTest?'visionConnectionStatus':'aiConnectionStatus');connection.textContent=result.message || '连接测试未通过，请检查设置后重试。';connection.classList.add('is-error');connection.classList.remove('is-verified');
        $('aiSettingsError').textContent='设置已保存，但连接测试未通过：'+(result.message || '服务未返回有效响应。')+' 请核对服务地址、模型 ID 和 Key 后重试。';
      }else toast(result.message || 'AI 服务连接测试通过。');
    }else toast(aiRequestAllowed(settings)?'AI 设置已保存，可测试连接。':'AI 设置已保存。'+aiTestUnavailable(settings));
  }catch(error){
    $('aiSettingsError').textContent=(saved?'设置已保存，但连接测试未完成：':'保存未完成：')+error.message+' 请核对服务地址、模型 ID 和 Key 后重试。';
    if(saved && test){const connection=$(test==='vision'?'visionConnectionStatus':'aiConnectionStatus');connection.textContent='连接测试未完成，请检查设置后重试。';connection.classList.add('is-error');connection.classList.remove('is-verified');}
    try{const settings=await api('/api/ai/settings');applyAiPublic(settings);renderAiConnection(settings);}catch(_){}
  }finally{delete body.api_key;delete body.vision_api_key;setAiSettingsBusy(false);}
}
function feedbackSnapshot(output=currentOutput) {
  return output?{output_id:output.job_id,shot_id:output.shot_id,quality:output.quality,version:output.version}:null;
}
function clearFeedback(close=false) {
  feedbackEpoch++;feedbackPoints=[];feedbackSource=null;if(close){feedbackMode=false;instructionTargetSelection=null;}
  resetPartMaterialDraft();renderFeedback();
}
function imageContentRect(image=$('renderImage')) {
  const box=image.getBoundingClientRect();
  if(image.hidden || !image.naturalWidth || !image.naturalHeight || !box.width || !box.height)return null;
  const scale=Math.min(box.width/image.naturalWidth,box.height/image.naturalHeight);
  const width=image.naturalWidth*scale,height=image.naturalHeight*scale;
  return {left:box.left+(box.width-width)/2,top:box.top+(box.height-height)/2,width,height};
}
function pointFromClient(clientX,clientY,rect=imageContentRect()) {
  if(!rect || clientX<rect.left || clientX>rect.left+rect.width || clientY<rect.top || clientY>rect.top+rect.height)return null;
  return {x:(clientX-rect.left)/rect.width,y:(clientY-rect.top)/rect.height};
}
function renderFeedbackMarkers() {
  const rect=imageContentRect(),viewer=$('viewer').getBoundingClientRect();
  $('feedbackMarkers').innerHTML=feedbackMode && rect?feedbackPoints.map((point,index)=>`<button type="button" class="feedback-marker ${point.pending?'pending':point.hit?'':'miss'}" data-remove-point="${esc(point.id)}" style="left:${rect.left-viewer.left+rect.width*point.x}px;top:${rect.top-viewer.top+rect.height*point.y}px" aria-label="移除位置 ${index+1}" title="移除位置 ${index+1}">${index+1}</button>`).join(''):'';
}
function renderFeedback() {
  const canSelect=!!currentOutput && !!current?.analysis && !comparePrevious && system?.runtime?.capabilities?.pick!==false;
  const interactionBusy=busy || !!planSavePromise || projectLoading || realismBusy || materialAgentSubmitting || !!activeJob() || automaticMaterialBusy() || !renderReady();
  if((instructionTargetSelection && !instructionTargetSelectionIsCurrent()) || (feedbackSource && (feedbackSource.projectId!==current?.id || feedbackSource.key!==sceneOutputKey(currentOutput || {})))){
    feedbackEpoch++;feedbackPoints=[];feedbackSource=null;feedbackMode=false;instructionTargetSelection=null;resetPartMaterialDraft();
  }
  const targeting=!!instructionTargetSelection;
  $('instructionTargetControls').hidden=!targeting;
  $('manualPartControls').hidden=targeting;$('materialAgentPanel').hidden=targeting;
  $('materialIntro').textContent=targeting?'点选一个需要修改的部位，帮助识别同类部件。':'在画面中点击部件，查看并修改它的材质。';
  $('feedbackTitle').textContent=targeting?'定位参考部位':'所选部件与材质';
  $('instructionTargetRequest').textContent=targeting?instructionTargetSelection.text:'';
  $('instructionTargetContinue').disabled=!targeting || !canLocateInstruction() || !instructionTargetPoints();
  $('instructionTargetCancel').disabled=interactionBusy;
  $('feedbackToggle').disabled=!canSelect || interactionBusy;
  $('feedbackToggle').setAttribute('aria-pressed',feedbackMode);
  $('feedbackToggle').title=feedbackMode?'结束点选':'选择部件 / 材质';$('feedbackToggle').setAttribute('aria-label',$('feedbackToggle').title);
  $('feedbackPanel').hidden=!feedbackMode;$('viewer').classList.toggle('feedback-selecting',feedbackMode);
  $('feedbackContext').textContent=currentOutput?`${sceneShotLabel(currentOutput.shot_id)} · 方案 v${currentOutput.version} · ${feedbackPoints.length}/12 个位置`:'';
  $('feedbackSelection').innerHTML=feedbackPoints.length?feedbackPoints.map((point,index)=>`<li><span class="feedback-number">${index+1}</span><span>${point.pending?'正在定位实际部件…':point.error?esc(point.error):point.hit?`<strong>${esc(point.object_name || '部件')}</strong><small>材质：${esc(point.material_name || '未分配材质')}</small>`:esc(point.message || '没有命中产品，请移除后重新点选')}<small>位置 ${Math.round(point.x*100)}%, ${Math.round(point.y*100)}%</small></span><button type="button" class="text-button" data-remove-point="${esc(point.id)}" aria-label="移除位置 ${index+1}">移除</button></li>`).join(''):'<li class="feedback-empty">尚未选择部件。</li>';
  $('feedbackClear').disabled=!feedbackPoints.length || busy;
  $('feedbackAddPoint').disabled=!canSelect || interactionBusy;
  renderPartMaterial();renderFeedbackMarkers();renderMaterialAgent();
}
function resetPartMaterialDraft() {
  partMaterialSignature='';partMaterialFields.clear();partMaterialValueFields.clear();
  for(const [,use] of Object.values(partFieldControls))$(use).checked=false;
  $('partMaterialError').textContent='';
}
function uniqueMaterialSelections() {
  const seen=new Set();
  return feedbackPoints.filter(point=>{
    if(!point.hit || point.pending)return false;
    const pair=JSON.stringify([point.object_name,point.material_name]);
    if(seen.has(pair))return false;seen.add(pair);return true;
  });
}
function commonMaterialValue(materials,field) {
  if(!materials.length || materials.some(material=>material?.[field]===undefined || material?.[field]===null))return {value:null,label:'未提供可读值'};
  const value=materials[0][field],same=materials.every(material=>typeof value==='number'?Math.abs(material[field]-value)<.00001:material[field]===value);
  return same?{value,label:typeof value==='number'?Number(value.toFixed(3)).toString():String(value)}:{value:null,label:'混合值'};
}
function partSelectionBlocked() {
  return !!instructionTargetSelection || comparePrevious || busy || !!planSavePromise || projectLoading || realismBusy || materialAgentSubmitting || !renderReady() || system?.runtime?.capabilities?.pick===false || !!activeJob() || automaticMaterialBusy() || dirty || !currentOutput || currentOutput.version!==current?.version || !feedbackPoints.length || feedbackPoints.some(point=>point.pending || !point.hit || !point.material_name);
}
function renderPartMaterial() {
  const selected=uniqueMaterialSelections(),materials=selected.map(point=>point.material_info || {});
  const signature=JSON.stringify(feedbackPoints.map(point=>[point.id,point.pending,point.hit,point.material_info]));
  if(signature!==partMaterialSignature){
    partMaterialSignature=signature;partMaterialFields.clear();partMaterialValueFields.clear();
    for(const [field,[id,use]] of Object.entries(partFieldControls)){
      $(use).checked=false;const common=commonMaterialValue(materials,field);
      $(id).value=field==='preset'?'':field==='color'?(common.value || '#808080'):(common.value ?? '');
    }
  }
  $('partMaterialInfo').textContent=!selected.length?'':`已选 ${selected.length} 个材质槽`;
  for(const [field,labelId] of [['color','partColorValue'],['roughness','partRoughnessValue'],['metallic','partMetallicValue'],['transmission','partTransmissionValue']]){
    const common=commonMaterialValue(materials,field),linked=materials.some(material=>material.linked?.[field]);
    $(labelId).textContent=selected.length?`当前 ${common.label}${linked?' · 由贴图驱动':''}`:'';$(labelId).title=linked?'该参数由节点或贴图驱动，所列为输入默认值':'';
  }
  $('partIorValue').textContent=commonMaterialValue(materials,'ior').label;
  const warnings=[...new Set(materials.flatMap(material=>material.warnings || []))];
  const textures=[...new Set(materials.flatMap(material=>(material.textures || []).map(texture=>typeof texture==='string'?texture:texture.name)).filter(Boolean))];
  $('partMaterialResources').innerHTML=(textures.length?`<p>已关联贴图：${textures.map(esc).join('、')}</p>`:'')+warnings.map(warning=>`<p>${esc(warning)}</p>`).join('');
  let note='';
  if(dirty)note='「参数」有未保存的调整，请先保存并预览，再编辑部件。';
  else if(currentOutput && currentOutput.version!==current?.version)note='当前图片属于旧方案，请先生成最新预览。';
  else if(feedbackPoints.some(point=>point.pending))note='正在读取实际表面与材质，请稍候。';
  else if(feedbackPoints.some(point=>!point.hit || !point.material_name))note='请移除未命中产品或没有可编辑材质的点。';
  else if(!selected.length)note=system?.runtime?.capabilities?.pick===false?'本机部件定位组件缺失，请查看运行环境。':'';
  const unsupported=materials.some(material=>material.editable===false || material.supported===false);
  const explicitPreset=partMaterialFields.has('preset') && !!presets[$('partPreset').value];
  if(unsupported && !partSelectionBlocked())note='所选材质含复杂或不支持的着色器，不能直接覆盖参数。请明确选择内置材质预设后再修改。';
  $('partMaterialState').textContent=note;
  const blocked=partSelectionBlocked();
  for(const [id,use] of Object.values(partFieldControls)){$(id).disabled=blocked;$(use).disabled=blocked;}
  $('partMaterialSubmit').disabled=blocked || !partMaterialFields.size || (unsupported && !explicitPreset);
  $('partMaterialSubmit').textContent=busy?'正在应用…':'应用并预览';
  const overrides=current?.plan?.object_material_overrides || [];
  $('partMaterialReset').disabled=blocked || !selected.some(point=>overrides.some(item=>item.object_name===point.object_name && item.material_name===point.material_name));
}
function gatherPartMaterialPatch() {
  const patch={};
  for(const field of partMaterialFields){
    const input=$(partFieldControls[field][0]);
    if(field==='preset'){
      if(!presets[input.value])throw new Error('请选择要应用的材质预设，或取消勾选。');
      patch[field]=input.value;
    }else if(field==='color'){
      if(commonMaterialValue(uniqueMaterialSelections().map(point=>point.material_info || {}),'color').value===null && !partMaterialValueFields.has('color'))throw new Error('所选部件颜色为混合或未知值，请先选择要应用的目标颜色。');
      if(!/^#[0-9a-f]{6}$/i.test(input.value))throw new Error('请选择有效颜色。');
      patch[field]=input.value;
    }else{
      if(input.value.trim()==='')throw new Error('请填写要修改的数值，混合值不会自动覆盖。');
      const value=Number(input.value);if(!Number.isFinite(value) || value<0 || value>1)throw new Error('材质数值必须在 0–1 之间。');
      patch[field]=value;
    }
  }
  return patch;
}
async function submitPartMaterial(reset=false) {
  if(partSelectionBlocked() || !feedbackSource || (!reset && $('partMaterialSubmit').disabled))return;
  let patch={};try{if(!reset)patch=gatherPartMaterialPatch();}catch(error){$('partMaterialError').textContent=error.message;return;}
  if(!reset && !Object.keys(patch).length)return;
  const source=clone(feedbackSource),points=feedbackPoints.map(({id,x,y})=>({id,x,y}));
  if(source.projectId!==current.id || source.key!==sceneOutputKey(currentOutput))return toast('图片已改变，请重新选择部件。',true);
  busy=true;$('partMaterialError').textContent='';renderProject();
  try{
    const result=await api(`/api/projects/${source.projectId}/materials/selection/apply`,{method:'POST',body:{...source.snapshot,points,...(reset?{reset:true}:{patch})}});
    if(current?.id===source.projectId){current=result;syncSettings();clearFeedback(true);}
    toast(reset?'所选部件局部材质已恢复，正在重新预览。':'部件材质已保存，正在真实重新渲染当前镜头。');
  }catch(error){$('partMaterialError').textContent=error.message;}
  finally{busy=false;renderProject();}
}
async function requestRealismReview(event) {
  event?.preventDefault();
  if(realismBusy || materialAgentSubmitting || busy || planSavePromise || projectLoading || !currentOutput || activeJob() || automaticMaterialBusy() || serviceDisconnected)return;
  if(realtimeActive()){realtime.mode='image';renderViewer();}
  const projectId=current.id,source=feedbackSnapshot(),key=sceneOutputKey(currentOutput);
  realismBusy=true;reviewRevision++;reviewErrorKey=key;$('reviewError').textContent='';$('reviewError').hidden=true;renderProject();
  try{
    const result=await api(`/api/projects/${projectId}/review`,{method:'POST',body:{...source,diagnostic_only:true}});
    if(current?.id===projectId){current=result;renderProject();}
    toast('图片检查已更新。');
  }catch(error){
    if(current?.id===projectId && sceneOutputKey(currentOutput || {})===key){$('reviewError').textContent=error.message;$('reviewError').hidden=false;}
    else toast('先前图片检查失败：'+error.message,true);
  }finally{realismBusy=false;renderProject();}
}
async function addFeedbackPoint(point) {
  if(comparePrevious || !feedbackMode || !currentOutput || busy || planSavePromise || projectLoading || realismBusy || materialAgentSubmitting || activeJob() || automaticMaterialBusy() || system?.runtime?.capabilities?.pick===false)return;
  if(feedbackPoints.length>=12)return toast('一次最多选择 12 个位置，可移除不需要的位置。',true);
  if(!point || !Number.isFinite(point.x) || !Number.isFinite(point.y) || point.x<0 || point.x>1 || point.y<0 || point.y>1)return toast('请选择图片范围内的位置。',true);
  if(!feedbackSource)feedbackSource={projectId:current.id,key:sceneOutputKey(currentOutput),snapshot:feedbackSnapshot()};
  const epoch=feedbackEpoch,source=clone(feedbackSource),id=crypto.randomUUID();
  feedbackPoints.push({...point,id,pending:true});renderFeedback();
  try{
    const response=await api(`/api/projects/${source.projectId}/feedback/pick`,{method:'POST',body:{...source.snapshot,points:[{...point,id}]}});
    if(epoch!==feedbackEpoch || feedbackSource?.key!==source.key)return;
    const target=feedbackPoints.find(entry=>entry.id===id),hit=response.hits?.find(entry=>entry.id===id);
    if(target){Object.assign(target,hit || {hit:false,message:'未收到场景定位结果'}, {pending:false});$('feedbackAnnouncement').textContent=target.hit?`已选择 ${target.object_name || '产品部件'}`:target.message || '没有命中产品';}
  }catch(error){if(epoch===feedbackEpoch){const target=feedbackPoints.find(entry=>entry.id===id);if(target)Object.assign(target,{pending:false,hit:false,error:error.message});$('feedbackAnnouncement').textContent='位置定位失败：'+error.message;}}
  finally{if(epoch===feedbackEpoch){renderFeedback();renderInstruction();}}
}
function renderReview() {
  const key=currentOutput?sceneOutputKey(currentOutput):'';
  if(reviewErrorKey && reviewErrorKey!==key){reviewErrorKey='';$('reviewError').textContent='';$('reviewError').hidden=true;}
  $('reviewRefreshButton').hidden=!currentOutput;
  $('reviewRefreshButton').disabled=realismBusy || materialAgentSubmitting || busy || !!planSavePromise || projectLoading || !!activeJob() || automaticMaterialBusy() || !currentOutput || serviceDisconnected;
  $('reviewRefreshButton').textContent=realismBusy?'正在检查…':'重新检查';
  $('reviewRefreshButton').title='检查此镜头的已渲染图片';
  $('reviewImageSource').hidden=!realtimeActive() || !currentOutput;
  $('reviewImageSource').textContent=currentOutput?'依据：已渲染图片 v'+currentOutput.version+' · 查看图片':'';
  const review=currentOutput?.review,corrections=currentOutput?.corrections;
  $('reviewPanel').hidden=!review && !corrections?.length;
  $('reviewEmptyState').hidden=!!review || !!corrections?.length;
  $('reviewEmptyState').textContent=currentOutput?'暂无诊断，可重新检查当前图片。':'预览后显示诊断。';
  if($('reviewPanel').hidden)return;
  const description=review?.realism?.summary || review?.summary || review?.message || ({passed:'本轮检查通过',manual_review:'有问题待处理',unavailable:'检查暂不可用'}[review?.status]) || '已有检查记录';
  $('reviewSummary').textContent=String(description);
  const issues=Array.isArray(review?.issues)?review.issues:[];
  const realism=review?.realism;
  const causes=Array.isArray(realism?.causes)?realism.causes:[];
  const priorities=Array.isArray(realism?.priority_actions)?realism.priority_actions:[];
  const realismHtml=realism?`<section class="realism-report"><h3>诊断依据</h3>${causes.length?`<div class="realism-causes">${causes.map(cause=>`<article><div><strong>${esc(cause.aspect || '待检查因素')}</strong>${typeof cause.confidence==='number'?`<small>判断置信度 ${Math.round(Math.max(0,Math.min(1,cause.confidence))*100)}%</small>`:''}</div><p><span>画面证据</span>${esc(cause.evidence || '没有提供明确画面证据')}</p><p><span>改进建议</span>${esc(cause.suggestion || '请进一步核对模型与参考信息')}</p>${cause.requires_user?'<small class="realism-manual">可能需要手动调整模型</small>':''}</article>`).join('')}</div>`:''}${priorities.length?`<h4>优先处理</h4><ol>${priorities.map(action=>`<li>${esc(typeof action==='string'?action:JSON.stringify(action))}</li>`).join('')}</ol>`:''}</section>`:'';
  const attempts=Array.isArray(corrections)?corrections:[],chosen=attempts.find(round=>round.applied);
  const detailsKey=JSON.stringify([review,corrections,currentOutput?.effective_plan]);
  if(reviewSeenKey!==detailsKey){reviewSeenKey=detailsKey;$('reviewDetails').innerHTML=`<p>${review?.source==='ai'?'AI 视觉检查':review?.source==='rules'?'基础规则检查':'检查来源未记录'}${chosen?' · 本轮最佳：第 '+Number(chosen.round)+' 轮（0 为初始预览）':''}</p>${realismHtml}${issues.length?`<ul>${issues.map(issue=>`<li>${esc(typeof issue==='string'?issue:issue.description || issue.message || JSON.stringify(issue))}</li>`).join('')}</ul>`:''}${attempts.length?`<div class="review-attempts">${attempts.map(round=>`<div>${round.png_url?`<a href="${esc(round.png_url)}" target="_blank" rel="noopener"><img src="${esc(round.png_url)}" alt="第 ${Number(round.round)} 轮真实预览" loading="lazy"></a>`:''}<span>第 ${Number(round.round)} 轮${round.applied?(currentOutput?.review_applied?' · 已应用至当前版本':' · 本轮最佳'):''}</span><small>曝光 ${esc(round.exposure)} EV · 柔和度 ${esc(round.light_softness)}</small>${round.decision_reason?`<small class="review-decision">${esc(round.decision_reason)}</small>`:''}</div>`).join('')}</div>`:''}<details><summary>完整检查与修正记录</summary><pre>${esc(JSON.stringify({review,corrections,effective_plan:currentOutput?.effective_plan},null,2))}</pre></details>`;
  }
}

function renderShots() {
  selectedShots=new Set([...selectedShots].filter(id=>shots().some(shot=>shot.id===id)));
  $('shotCount').textContent=`${shots().length} 个镜头`;
  $('shotHint').textContent='点图查看 · 勾选输出 4K';
  stableHTML($('shotList'),shots().map((s,i)=>{
    const o=displayedShotOutput(s.id),active=s.id===activeShot;
    const generating=(current.jobs || []).some(job=>job.origin==='auto_thumbnails' && ['queued','running'].includes(job.status) && (job.shot_ids || []).includes(s.id) && !(job.completed || []).includes(s.id));
    return `<div class="shot-card ${active?'active':''} ${selectedShots.has(s.id)?'selected':''}"><button type="button" data-shot="${esc(s.id)}" aria-pressed="${active}" aria-label="查看${esc(s.label)}${active?'（当前画面）':''}">${o?`<img src="${esc(o.jpg_url || o.png_url)}" alt="${esc(s.label)}${o.quality==='generated'?'（AI 生成）':'的真实渲染'}" loading="lazy">`:''}</button><label title="将${esc(s.label)}加入 4K 输出"><input type="checkbox" data-select-shot="${esc(s.id)}" aria-label="将${esc(s.label)}加入 4K 输出" ${selectedShots.has(s.id)?'checked':''}></label><strong>${String(i+1).padStart(2,'0')} ${esc(s.label)}</strong><small>${o?`${sceneQualityLabel(o.quality,o)} · v${o.version}${o.version!==current.version?' · 旧版':''}`:generating?'缩略图生成中':'未出图'}</small></div>`;
  }).join(''));
  $('selectAllShots').checked=!!shots().length && selectedShots.size===shots().length;$('selectAllShots').indeterminate=selectedShots.size>0 && selectedShots.size<shots().length;
}
function renderJob() {
  const logWasOpen = !!$('jobPanel').querySelector('.job-log[open]');
  const candidatesWereOpen=!!$('jobPanel').querySelector('.hero-optimization-candidates[open]');
  const jobs=[...(current.jobs || [])].sort((a,b)=>(b.created_at || 0)-(a.created_at || 0));const job=activeJob() || polishJob() || jobs.find(item=>item.origin!=='auto_polish' || item.status==='failed') || jobs[0];
  $('jobPanel').hidden=!job;if (!job) return;
  const running=['running','queued'].includes(job.status),autoRunning=!running && automaticMaterialBusy();const done=job.status==='completed' && !autoRunning;const cancelled=['cancelled','interrupted'].includes(job.status);
  const total=job.shot_ids?.length || 1;const completed=job.completed?.length || 0;
  const hero=job.kind==='hero_optimize',sample=hero && !['preview','finalizing'].includes(job.workflow_phase)?null:job.sample_progress;
  const automaticTask=(materialAgentRecords.get(current.id) || []).find(materialTaskIsActive);
  let stage=autoRunning?(automaticTask?.stage || current.auto_material?.stage || '正在自动优化材质'):job.stage || statusNames[job.status];if(job.kind==='analyze')stage='模型分析 · '+stage;else if(job.origin==='initial_preview')stage='白底主图 · '+stage;else if(job.origin==='auto_polish')stage=running?'后台生成高清图':job.status==='failed'?'高清图未生成':stage;
  $('jobPanel').className='job-panel'+(job.status==='failed'?' failed':'')+(done?' is-complete':'');
  const summary=hero?job.optimization_summary || (done && job.optimization_decision==='kept_original'?'试渲染未达到采用条件，已保留原图。':done && job.optimization_decision==='adopted'?'已采用优化后的高清主图。':''):'';
  const candidates=hero && Array.isArray(job.hero_candidates || job.candidates)?(job.hero_candidates || job.candidates).slice(0,12):[];
  const candidatesHTML=candidates.length?`<details class="hero-optimization-candidates"><summary>查看试渲染记录</summary><div class="review-attempts">${candidates.map((candidate,index)=>{const url=typeof candidate.png_url==='string' && candidate.png_url.startsWith('/files/'+current.id+'/') && /\.(png|jpg)$/i.test(candidate.png_url)?candidate.png_url:'';return `<div>${url?`<a href="${esc(url)}" target="_blank" rel="noopener"><img src="${esc(url)}" alt="${esc(candidate.label || '第 '+(index+1)+' 轮试渲染')}" loading="lazy"></a>`:''}<span>${esc(candidate.label || '第 '+(index+1)+' 轮试渲染')}</span>${candidate.reason?`<small>${esc(candidate.reason)}</small>`:''}</div>`;}).join('')}</div></details>`:'';
  const progressText=job.origin==='auto_polish'?(running?`可继续操作，新的要求会立即接管${typeof sample==='number'?' · 采样 '+sample+'%':''}`:'当前预览保留'):hero?(running?'自动优化进行中，完成后显示结果':done?'主图优化已结束':'已完成图片保留'):`图组进度 ${completed}/${total} 张${running && typeof sample==='number' ? ' · 当前镜头采样 '+sample+'%' : ''}`;
  $('jobPanel').innerHTML=`<div class="job-top"><div><strong>${esc(stage)}</strong><small>${autoRunning?'当前图片暂时保留，自动应用并重渲染后更新':`${job.current_shot?esc(job.current_shot)+' · ':''}${job.kind==='analyze'?'读取几何、部件与贴图':progressText}${done && !hero?' · 执行完成':''}${cancelled && !hero?' · 已完成图片保留':''}`}</small></div>${running?`<button type="button" class="button subtle" data-cancel="${esc(job.id)}">取消任务</button>`:autoRunning && (automaticTask?.id || current.auto_material?.task_id)?`<button type="button" class="button subtle" data-cancel-auto-material="${esc(automaticTask?.id || current.auto_material.task_id)}">取消优化</button>`:!done && !autoRunning?`<button type="button" class="button secondary" data-retry="${esc(job.id)}">继续 / 重试</button>`:''}</div>${running && !hero?`<div class="progress-track" role="progressbar" aria-label="已完成镜头进度" aria-valuenow="${Number(job.progress)||0}" aria-valuemin="0" aria-valuemax="100"><span style="width:${Math.min(100,Math.max(0,Number(job.progress)||0))}%"></span></div>`:''}${summary?`<p class="hero-optimization-summary" role="status">${esc(summary)}</p>`:''}${candidatesHTML}${job.error?`<p class="job-error">${esc(job.error)}</p>`:''}<details class="job-log"><summary>日志</summary><pre>${esc((job.log_tail || []).join('\n'))}</pre></details>`;
  if (logWasOpen) $('jobPanel').querySelector('.job-log').open = true;
  if(candidatesWereOpen && $('jobPanel').querySelector('.hero-optimization-candidates'))$('jobPanel').querySelector('.hero-optimization-candidates').open=true;
}
// What the first image assumed about placeholder materials, with the evidence for each.
function lookdevSummaryHTML() {
  const record=current?.plan?.lookdev;if(!record?.summary)return '';
  const rows=(Array.isArray(record.decisions)?record.decisions:[]).slice(0,60).map(item=>`<li><b>${esc(item.object || item.material || '')}</b> → ${esc(item.label || '')}<small>${esc(item.reason || '')}</small></li>`).join('');
  return `<dt>质感推断</dt><dd>${esc(record.summary)}${rows?`<details class="lookdev-record"><summary>查看依据</summary><ul>${rows}</ul><p>模型只带纯色占位材质时，按材质与部件名称补全质感，颜色保持不变。可在「材质」中逐项修改，或直接输入要求。</p></details>`:''}</dd>`;
}
function renderInspection() {
  const openSections = [...$('inspectionContent').querySelectorAll('details')].map(d => d.open);
  const a=current.analysis;const warnings=[...new Set([...(a?.warnings || []),...(current.plan?.pending || []),...(currentOutput?.checks?.manual_review || [])])];
  $('warningBadge').textContent=warnings.length?` · ${warnings.length} 项待确认`:'';
  if (!a) {$('inspectionContent').innerHTML='<p>模型检查尚未完成，请查看任务状态。</p>';return;}
  const f=a.features || {};const dim=current.plan.dimensions_mm?current.plan.dimensions_mm.join(' × ')+' mm（用户指定）':a.dimensions?.unit==='m'?a.dimensions.value.map(n=>(n*1000).toFixed(1)).join(' × ')+' mm（模型声明，需确认）':'未知；使用归一化工作尺度';
  const features=[f.elongated?'细长物体':null,f.flat?'扁平物体':null,f.multipart?'多部件':'单网格',f.has_animation?'已有动画':null,f.has_armature?'已有绑定':null].filter(Boolean);
  $('inspectionContent').innerHTML=`<dl><dt>模型特征</dt><dd>${esc(features.join(' / '))}</dd><dt>真实尺寸</dt><dd>${esc(dim)}</dd><dt>方案来源</dt><dd>${esc(describeSource(current.plan.source))}</dd>${current.plan.auto_framing?.reason?`<dt>智能取景</dt><dd>${esc(current.plan.auto_framing.reason)}</dd>`:''}${lookdevSummaryHTML()}<dt>材质说明</dt><dd>${esc(current.metadata?.material_note || '未补充')}</dd></dl>${warnings.map(w=>`<p class="warning">• ${esc(w)}</p>`).join('')}<h3>部件层级</h3><table><thead><tr><th>部件</th><th>父部件</th><th>UV / 面数</th></tr></thead><tbody>${(a.objects || []).map(o=>`<tr><td>${esc(o.name)}</td><td>${esc(o.parent || '根节点')}</td><td>${esc(Array.isArray(o.uv_layers)?o.uv_layers.join(', ') || '无 UV':o.uv_layers || '—')} / ${esc(o.faces ?? o.polygons ?? '—')}</td></tr>`).join('')}</tbody></table>${currentOutput?`<details><summary>当前图片的检查结果</summary><pre>${esc(JSON.stringify({checks:currentOutput.checks,image_metrics:currentOutput.metrics},null,2))}</pre></details>`:''}<h3>操作记录</h3>${current.history?.length?current.history.slice().reverse().slice(0,8).map(h=>`<p>v${h.version} · ${esc(h.reason)} · ${new Date(h.at*1000).toLocaleString('zh-CN')}</p>`).join(''):'<p>初始方案；尚未修改参数。</p>'}<details><summary>查看可复现拍摄方案 JSON</summary><pre>${esc(JSON.stringify(current.plan,null,2))}</pre></details>`;
  $('inspectionContent').querySelectorAll('details').forEach((d,i) => d.open = !!openSections[i]);
}
function renderProject() {
  if(!current){
    disposeRealtime();renderRealtimeStage();
    $('welcomePanel').hidden=false;$('projectPanel').hidden=true;$('emptySettings').hidden=false;$('settingsForm').hidden=true;
    $('projectTitle').textContent='新建项目';$('undoButton').disabled=true;
    $('bundleButton').classList.add('disabled');$('bundleButton').setAttribute('aria-disabled','true');$('bundleButton').href='#';$('bundleButton').tabIndex=-1;
    $('jobPanel').hidden=true;$('renderImage').hidden=true;$('renderImage').removeAttribute('src');$('imageDownloads').hidden=true;
    renderActionState();return;
  }
  $('welcomePanel').hidden=true;$('projectPanel').hidden=false;$('emptySettings').hidden=true;$('settingsForm').hidden=false;
  $('projectTitle').textContent=current.name;
  $('undoButton').disabled=busy || !!planSavePromise || projectLoading || realismBusy || dirty || !current.history?.length || !!activeJob() || automaticMaterialBusy();
  $('undoButton').title=dirty?'先保存或放弃本次调整，再回退已保存版本':'回退上一版已保存方案';
  $('bundleButton').classList.toggle('disabled',!current.outputs?.length);$('bundleButton').setAttribute('aria-disabled',!current.outputs?.length);$('bundleButton').href=current.outputs?.length?`/api/projects/${current.id}/bundle`:'#';$('bundleButton').tabIndex=current.outputs?.length?0:-1;
  if(!dirty && !planSavePromise && syncSignature!==`${current.id}:${current.version}:${!!current.analysis}`)syncSettings();
  updateAiMode();renderViewer();renderShots();renderJob();renderInspection();renderSceneExports();renderActionState();refreshEcommerce();
}
async function refreshList() { const request=++libraryRequest,projects=await api('/api/projects');if(request!==libraryRequest)return;allProjects=projects.filter(project=>!deletedProjectIds.has(project.id));allProjects.sort((a,b)=>(b.created_at || 0)-(a.created_at || 0));renderLibrary(); }
function leaveProjectView() {
  if(current && $('instructionInput').value)instructionDrafts.set(current.id,$('instructionInput').value);
  // Exiting is navigation. Unacknowledged and saved requests keep their keys.
  pendingInstructionPreview=null;current=null;draft=null;dirty=false;draftBaseVersion=null;projectLoading=false;
  activeShot=null;selectedShots=new Set();selectedMaterial=null;cameraShot=null;currentOutput=null;syncSignature='';
  styleFocusNotice='';comparePrevious=false;viewerLatestKey='';editedMaterialColors.clear();
  ecommerceView=null;ecommerceViewKey='';ecommerceError='';ecommerceRequest++;
  sceneExportSource=null;sceneExportProjectId=null;sceneExportJobsSignature='';sceneExportOptionsSignature='';
  for(const id of ['sceneExportDialog','imageDialog'])if($(id).open)$(id).close();
  clearFeedback(true);forget('wuying-project');renderProject();renderLibrary();$('uploadMain').focus();
}
async function exitProject() {
  if(!current && !projectLoading)return true;
  if(projectWritePending()){toast('当前操作正在提交，完成后即可退出项目。');return false;}
  const requestId=++projectRequest;
  if(!(await resolveUnsavedChanges()) || requestId!==projectRequest){if(requestId===projectRequest){projectLoading=false;renderProject();}return false;}
  const background=!!activeJob() || automaticMaterialBusy() || current?.exports?.some(job=>['queued','running'].includes(job.status));
  leaveProjectView();toast(background?'已退出项目，任务会在后台继续。':'已退出项目。');return true;
}
function forgetDeletedProject(id) {
  deletedProjectIds.add(id);libraryRequest++;
  instructionDrafts.delete(id);instructionReceipts.delete(id);instructionGuidance.delete(id);materialAgentRecords.delete(id);
  for(const [key,attempt] of instructionAttempts)if(attempt.projectId===id)instructionAttempts.delete(key);
  for(const key of materialAgentDisclosures.keys())if(key.startsWith(id+':'))materialAgentDisclosures.delete(key);
  for(const key of confirmedRenders){try{if(JSON.parse(key)[0]===id)confirmedRenders.delete(key);}catch(_){}}
  if(pendingInstructionPreview?.projectId===id)pendingInstructionPreview=null;
  for(const [key,attempt] of heroOptimizationAttempts)if(attempt.projectId===id)heroOptimizationAttempts.delete(key);
  for(const key of heroOptimizationErrors.keys()){try{if(JSON.parse(key)[0]===id)heroOptimizationErrors.delete(key);}catch(_){}}
  persistHeroOptimizationJournal();
  if(recalled('wuying-project')===id)forget('wuying-project');forget('wuying-shot-'+id);persistInstructionJournal();
  allProjects=allProjects.filter(project=>project.id!==id);
}
async function deleteProject(id) {
  const project=current?.id===id?current:allProjects.find(item=>item.id===id);
  if(!project || deletedProjectIds.has(id))return false;
  if(navigationBlocked()){toast('当前操作正在提交，完成后可删除项目。');return false;}
  const choice=await chooseAction('deleteProjectDialog','删除项目？',`<p>将永久删除 <strong>${esc(project.name)}</strong> 及本项目保存的模型、渲染结果、工程和历史记录，无法恢复。</p>${current?.id===id && dirty?'<p>当前未保存的调整也会丢弃。</p>':''}`,[{value:'cancel',label:'取消'},{value:'delete',label:'删除项目',danger:true}]);
  if(choice!=='delete' || navigationBlocked())return false;
  projectDeletionId=id;busy=true;renderActionState();
  try{
    const result=await api('/api/projects/'+encodeURIComponent(id),{method:'DELETE'});
    if(result?.deleted!==true || result.id!==id)throw new Error('服务未返回删除确认，请刷新项目列表后核对。');
    if(current?.id===id){projectRequest++;leaveProjectView();}
    forgetDeletedProject(id);renderLibrary();
    try{await refreshList();}catch(error){toast('项目已删除，但列表暂未刷新：'+error.message,true);return true;}
    toast(result.warning || `已删除「${project.name}」。`);return true;
  }catch(error){toast('未能删除项目：'+error.message,true);return false;}
  finally{projectDeletionId='';busy=false;renderProject();renderLibrary();}
}
async function openProject(id) {
  if(!id || deletedProjectIds.has(id) || (current?.id===id && !projectLoading))return true;
  if(projectWritePending()){toast('当前操作正在提交，完成后即可切换项目。');return false;}
  const requestId=++projectRequest;
  if(!(await resolveUnsavedChanges()) || requestId!==projectRequest)return false;
  projectLoading=true;renderActionState();
  try{
    const project=await api('/api/projects/'+encodeURIComponent(id));
    if(requestId!==projectRequest)return false;
    current=project;const savedShot=recalled('wuying-shot-'+id);
    activeShot=current.plan.shots.some(s=>s.id===savedShot)?savedShot:current.initial_preview && current.plan.shots.some(s=>s.id==='hero')?'hero':current.plan.style==='dark' && current.plan.shots.some(s=>s.id==='campaign')?'campaign':current.plan.shots[0]?.id;
    styleFocusNotice='';comparePrevious=false;viewerLatestKey='';selectedShots=new Set(activeShot?[activeShot]:[]);selectedMaterial=null;cameraShot=null;syncSignature='';
    clearFeedback(true);syncSettings();renderProject();setInspectorTab('review');renderLibrary();remember('wuying-project',id);await refreshMaterialTasks(id);return true;
  }catch(error){if(requestId===projectRequest)toast('无法打开项目：'+error.message,true);return false;}
  finally{if(requestId===projectRequest){projectLoading=false;renderProject();scheduleThumbnails();}}
}
const thumbnailAttempts=new Map();
let thumbnailSubmitting=false;
function scheduleThumbnails() {
  if(!current?.analysis || !system?.runtime?.capabilities?.thumbnails || thumbnailSubmitting || busy || projectLoading || planSavePromise || dirty || activeJob() || polishJob() || automaticMaterialBusy() || serviceDisconnected)return;
  if(!latestOutput('hero'))return;
  if(!shots().some(shot=>{const full=latestOutput(shot.id),small=latestThumbnail(shot.id);return shot.id!=='hero' && (!full || full.version<current.version) && small?.version!==current.version;}))return;
  const projectId=current.id,version=current.version,key=projectId+':'+version;
  const jobs=(current.jobs || []).filter(job=>job.origin==='auto_thumbnails' && job.version===version).sort((a,b)=>(b.created_at || 0)-(a.created_at || 0));
  const last=jobs[0],state=thumbnailAttempts.get(key) || {count:0,lastAt:0,lastYield:null};
  if(jobs.some(job=>['queued','running'].includes(job.status)))return;
  if(last && !(last.status==='cancelled' && last.yielded===true))return;
  if(last && state.lastYield===last.id)return;
  if(state.blocked || state.count>=5 || Date.now()-state.lastAt<5000)return;
  state.count++;state.lastAt=Date.now();if(last)state.lastYield=last.id;thumbnailAttempts.set(key,state);thumbnailSubmitting=true;
  api(`/api/projects/${encodeURIComponent(projectId)}/thumbnails`,{method:'POST',body:{expected_version:version}})
    .catch(error=>{if(error.uncertain || !error.status || error.status===409 || error.status>=500)state.lastYield=null;else state.blocked=true;})
    .finally(()=>{thumbnailSubmitting=false;});
}
async function poll() {
  if(polling || busy || planSavePromise || projectLoading || realismBusy || materialAgentSubmitting)return;
  polling=true;const pollingProject=current?.id,pollingEpoch=projectRequest;
  try{
    if(current){
      const id=current.id,requestId=projectRequest,oldVersion=current.version,reviewEpoch=reviewRevision;
      const project=await api('/api/projects/'+encodeURIComponent(id));
      if(current?.id===id && requestId===projectRequest && !busy && !planSavePromise && !projectLoading && !realismBusy && !materialAgentSubmitting && reviewEpoch===reviewRevision && current.version===oldVersion){
        const priorOutputs=current.outputs?.length,priorStyle=current.plan.style;
        current=project;if(!dirty && project.plan.style!==priorStyle)styleFocusNotice='棚拍风格已更新，生成预览后查看变化。';
        allProjects=allProjects.map(item=>item.id===project.id?project:item);renderLibrary();
        renderProject();await refreshMaterialTasks(id);
        if(priorOutputs!==project.outputs?.length){try{system=await api('/api/system');}catch(_){}await refreshList();}
      }
    }
    if(!current)await refreshList();
    if(serviceDisconnected || !current || system?.available===false){system=await api('/api/system');serviceDisconnected=false;updateSystemStatus();renderActionState();}
  }catch(error){if(pollingEpoch===projectRequest && pollingProject===current?.id){serviceDisconnected=true;updateSystemStatus();renderActionState();}}
  finally{polling=false;}
  await resumeInstructionPreflight();
  scheduleThumbnails();
}
async function resumeInstructionPreflight() {
  const pending=instructionPreflightPending;if(!pending || busy || projectLoading || planSavePromise)return;
  // Never resume against another project, shot, edited text or plan version.
  if(current?.id!==pending.projectId || activeShot!==pending.shotId || current.version!==pending.version || $('instructionInput').value.trim()!==pending.text || dirty || (realtimeActive() && realtime.changed)){
    instructionPreflightPending=null;
    if(current?.id===pending.projectId){$('instructionError').textContent='项目、视角或要求已变化，未自动应用；原要求仍可编辑。';$('instructionError').hidden=false;}
    return;
  }
  const job=(current.jobs || []).find(item=>item.id===pending.jobId);
  if(!job || ['failed','cancelled','interrupted'].includes(job.status)){
    instructionPreflightPending=null;$('instructionError').textContent='当前方案预览未完成，调整要求已保留。请检查任务后重新应用。';$('instructionError').hidden=false;return;
  }
  if(job.status!=='completed' || activeJob() || automaticMaterialBusy())return;
  if(!currentOutput || currentOutput.job_id!==pending.jobId || currentOutput.version!==pending.version || currentOutput.shot_id!==pending.shotId){
    instructionPreflightPending=null;$('instructionError').textContent='预览结果与当前镜头不一致，调整要求已保留。请检查图片后重新应用。';$('instructionError').hidden=false;return;
  }
  instructionPreflightPending=null;
  await applyInstructionAndPreview({preventDefault(){}});
}
function updateSystemStatus() {
  $('systemLabel').textContent=serviceDisconnected?'本机服务连接中断':system?.available?'本机 Blender 已就绪':system?.runtime?.missing_components?.length?'渲染组件缺失':!system?'正在检查运行环境':'未找到 Blender';
  document.querySelector('.system-status .status-dot').style.background=renderReady()?'var(--green)':'var(--amber)';
  $('systemInfo').title=runtimeReason() || '查看本机运行环境';
}
async function confirmFinalRender(ids) {
  const missing=ids.filter(id=>dirty || !(current.outputs || []).some(output=>output.quality!=='thumbnail' && output.shot_id===id && output.version===current.version));
  const quality=$('quality').value,aspect=document.querySelector('[name=aspect]:checked')?.value || '1:1';
  const signature=JSON.stringify([current.id,current.version,dirty,ids,quality,aspect,$('timeBudget').value]);
  if(!missing.length && quality!=='fine' && ids.length===1)return true;
  if(confirmedRenders.has(signature))return true;
  const size={'1:1':'4096 × 4096','4:3':'4096 × 3072','3:4':'3072 × 4096'}[aspect];
  const description=`<p>本次将生成 <strong>${ids.length} 张 ${quality==='fine'?'精细':'标准'} 4K</strong>，每张 ${size} px。</p><p>${ids.map(id=>esc(sceneShotLabel(id))).join('、')}</p>${missing.length?`<p class="warning">${missing.length} 个镜头尚未验证当前方案的预览。建议先检查角度、材质与构图。</p>`:''}${dirty?'<p>开始后会先保存当前草稿，再按新方案出图。</p>':''}<p>采样预算每张最多 ${Math.ceil(readNumber('timeBudget')/60)} 分钟；导入与写入另需时间，实际耗时取决于设备和模型。</p>`;
  const actions=[{value:'cancel',label:'继续调整'}];if(missing.includes(activeShot))actions.push({value:'preview',label:'先预览当前镜头'});actions.push({value:'render',label:`开始 ${ids.length} 张 4K`,primary:true});
  const choice=await chooseAction('renderConfirmDialog','确认本次 4K 输出',description,actions);
  if(choice==='preview'){await run('preview');return false;}
  if(choice==='render'){confirmedRenders.add(signature);return true;}
  return false;
}
async function run(kind) {
  if(!current || busy || planSavePromise || projectLoading || realismBusy || materialAgentSubmitting || activeJob() || automaticMaterialBusy())return;
  if(!current.analysis)return toast('模型分析完成后才能生成图片。',true);
  const projectId=current.id,ids=kind==='preview'?[activeShot]:[...selectedShots];
  if(!ids.length || !ids[0])return toast('请先勾选至少一个 4K 输出镜头。',true);
  if(ids.some(id=>!renderReadyForShot(id)))return toast(runtimeReason(),true);
  // A user-initiated render captures the visible camera once; dragging never
  // writes a plan, creates a version, or starts an offline render by itself.
  if(ids.includes(activeShot) && realtimeActive() && realtime.changed){try{applyRealtimeView(false);}catch(error){return toast(error.message,true);}}
  if(kind!=='preview' && !(await confirmFinalRender(ids)))return;
  if(current?.id!==projectId || busy || activeJob())return;
  const quality=kind==='preview'?'preview':$('quality').value;
  busy=true;renderProject();
  try{
    await savePlan(true);if(current?.id!==projectId)return;
    await api(`/api/projects/${projectId}/jobs`,{method:'POST',headers:{'Idempotency-Key':crypto.randomUUID()},body:{kind,quality,shot_ids:ids,expected_version:current.version}});
    const project=await api('/api/projects/'+projectId);if(current?.id===projectId)current=project;
    toast(kind==='preview'?`正在生成「${sceneShotLabel(ids[0])}」的预览。`:`已提交 ${ids.length} 张 4K 渲染。`);
  }catch(error){toast(error.message,true);}finally{busy=false;renderProject();}
}
function instructionReceiptIsValid(receipt,attempt) {
  if(!receipt || typeof receipt.id!=='string' || receipt.text!==attempt.text || !Array.isArray(receipt.changes) || !Number.isInteger(receipt.base_version) || !Number.isInteger(receipt.applied_version) || receipt.shot_id!==attempt.shotId || !['applied','partially_applied','no_change'].includes(receipt.status))return false;
  const partial=receipt.partial===true && Array.isArray(receipt.pending) && !!receipt.pending.length && typeof receipt.remaining_text==='string' && !!receipt.remaining_text.trim() && receipt.remaining_text.length<=2000 && receipt.remaining_text.trim()!==attempt.text.trim();
  if(attempt.kind==='confirm')return receipt.proposal_id===attempt.body.proposal_id && partial && ['applied','no_change'].includes(receipt.status);
  if(receipt.partial===true)return partial && receipt.base_version===attempt.body.expected_version && (receipt.status==='partially_applied'?!!receipt.changes.length && receipt.applied_version===receipt.base_version+1:receipt.status==='no_change' && !receipt.changes.length && receipt.applied_version===receipt.base_version);
  return receipt.status!=='partially_applied';
}
function instructionProposalIsValid(receipt,attempt) {
  return attempt.kind!=='confirm' && receipt?.status==='needs_confirmation' && typeof receipt.id==='string' && typeof receipt.proposal_id==='string' && /^[A-Za-z0-9_-]{1,128}$/.test(receipt.proposal_id) && receipt.text===attempt.text && receipt.shot_id===attempt.shotId && receipt.base_version===attempt.body.expected_version && receipt.applied_version===receipt.base_version && Array.isArray(receipt.changes) && !receipt.changes.length && Array.isArray(receipt.proposed_changes) && !!receipt.proposed_changes.length && Array.isArray(receipt.ready) && !!receipt.ready.length && receipt.ready.every(item=>item && typeof item==='object' && typeof item.summary==='string' && item.summary.trim()) && receipt.ready.some(item=>item.status!=='already_satisfied') && Array.isArray(receipt.pending) && !!receipt.pending.length && typeof receipt.remaining_text==='string' && !!receipt.remaining_text.trim() && receipt.remaining_text.length<=2000;
}
function instructionPreviewShots(receipt,project,capturedShot) {
  const requested=receipt.preview_shot_ids,known=new Set((project.plan?.shots || []).map(shot=>shot.id));
  return Array.isArray(requested) && requested.length && requested.length<=known.size && new Set(requested).size===requested.length && requested.every(id=>typeof id==='string' && known.has(id))?[...requested]:[capturedShot];
}
async function applyInstructionAndPreview(event) {
  event.preventDefault();let attempt=currentInstructionAttempt();const text=$('instructionInput').value.trim();
  if(!text || !current?.analysis || comparePrevious || busy || planSavePromise || projectLoading || realismBusy || materialAgentSubmitting || activeJob() || automaticMaterialBusy())return;
  if(!renderReady())return toast(runtimeReason(),true);
  if(attempt && !attempt.confirmed && dirty)return;
  const targetPoints=instructionTargetPoints();
  if(!attempt && instructionTargetSelection && !targetPoints)return;
  const projectId=current.id,capturedShot=activeShot,capturedSource=currentOutput?clone(feedbackSnapshot(currentOutput)):null,guidance=currentInstructionGuidance(),confirm=instructionProposalIsCurrent(guidance);
  if(!attempt && !confirm && realtimeActive() && realtime.changed){try{applyRealtimeView(false);}catch(error){return toast(error.message,true);}}
  if(guidance?.kind==='response_error')instructionGuidance.delete(projectId);
  instructionDrafts.set(projectId,$('instructionInput').value);
  busy=true;instructionSubmitting=true;$('instructionError').textContent='';$('instructionError').hidden=true;renderProject();
  try{
    if(!attempt){
      if([...instructionAttempts.values()].filter(item=>unfinishedInstructionPhases.has(item.phase)).length>=instructionJournalLimit)throw new Error('待核对的调整已达到上限，请先核对已有请求，再提交新调整。');
      if(!confirm){await savePlan(true);if(current?.id!==projectId)return;}
      if(!current.plan.shots.some(shot=>shot.id===capturedShot))throw new Error('当前镜头已改变，请重新选择后提交。');
      if(!confirm && capturedSource && capturedSource.version!==current.version){
        // A previous image cannot supply trustworthy part IDs for the new
        // scene. Generate a fresh view instead of sending an evidence-free
        // material request; keep the user's text until they can retry.
        instructionPreflightDrafts.set(projectId,text);persistInstructionPreflightDrafts();
        const preview=await api(`/api/projects/${projectId}/jobs`,{method:'POST',headers:{'Idempotency-Key':crypto.randomUUID()},body:{kind:'preview',quality:'preview',shot_ids:[capturedShot],expected_version:current.version}});
        if(current?.id===projectId && preview?.id)current.jobs=[preview,...(current.jobs || []).filter(item=>item.id!==preview.id)];
        if(!preview?.id)throw new Error('预览任务状态未确认，调整要求已保留。');
        instructionPreflightPending={projectId,shotId:capturedShot,version:current.version,text,jobId:preview.id};
        $('instructionError').textContent='当前图片属于旧方案，正在生成当前方案的预览，完成后自动继续调整。刷新页面后可重新应用已保留的要求。';
        $('instructionError').hidden=false;
        return;
      }
      const body=confirm?{proposal_id:guidance.proposalId,expected_version:guidance.baseVersion}:{text,expected_version:current.version,shot_id:capturedShot,source:capturedSource,...(targetPoints?{target_points:clone(targetPoints)}:{})};
      attempt={projectId,text,shotId:capturedShot,shotIds:[capturedShot],body,instructionKey:crypto.randomUUID(),previewKey:crypto.randomUUID(),phase:'requesting',confirmed:false,version:null,submitted:false,...(confirm?{kind:'confirm'}:{})};
      instructionAttempts.set(attempt.instructionKey,attempt);pendingInstructionPreview=attempt;persistInstructionJournal();
    }
    if(!attempt.confirmed){
      attempt.phase='requesting';
      const updated=await api(`/api/projects/${projectId}/instruct${attempt.kind==='confirm'?'/confirm':''}`,{method:'POST',headers:{'Idempotency-Key':attempt.instructionKey},body:clone(attempt.body)});
      const receipt=updated.instruction_result;
      if(updated.id===projectId && Number.isInteger(updated.version) && instructionProposalIsValid(receipt,attempt)){
        attempt.phase='proposed';pendingInstructionPreview=null;
        instructionGuidance.set(projectId,{kind:'proposal',text:attempt.text,proposalId:receipt.proposal_id,baseVersion:receipt.base_version,shotId:attempt.shotId,source:clone(attempt.body.source),ready:clone(receipt.ready),pending:clone(receipt.pending),proposedChanges:clone(receipt.proposed_changes)});
        if(current?.id===projectId){current=updated;syncSettings();if(instructionTargetSelection)clearFeedback(true);}
        return;
      }
      if(updated.id!==projectId || !Number.isInteger(updated.version) || !instructionReceiptIsValid(receipt,attempt)){const error=new Error('服务未返回与本次请求一致的执行回执，保存状态暂未确认。');error.uncertain=true;throw error;}
      attempt.confirmed=true;attempt.version=receipt.applied_version;attempt.receipt=clone(receipt);attempt.shotIds=instructionPreviewShots(receipt,updated,attempt.shotId);attempt.phase='saved';
      instructionGuidance.delete(projectId);
      if(receipt.partial){
        attempt.remainingText=receipt.remaining_text.trim();
        instructionGuidance.set(projectId,{kind:'remaining',text:attempt.remainingText,pending:clone(receipt.pending || [])});
        clearAppliedInstruction(attempt);
      }
      instructionReceipts.set(projectId,clone(receipt));
      if(current?.id!==projectId)return;
      current=updated;syncSettings();if(instructionTargetSelection)clearFeedback(true);
      if(receipt.applied_version!==updated.version){
        attempt.phase='obsolete';clearAppliedInstruction(attempt);pendingInstructionPreview=null;
        toast('已找回原执行记录；当前方案已有后续修改。');return;
      }
      if(receipt.status==='no_change' || !receipt.changes.length){
        attempt.phase='completed';clearAppliedInstruction(attempt);pendingInstructionPreview=null;
        toast(receipt.partial?'可执行项已符合要求；待处理要求已保留。':'没有参数变化。');return;
      }
      renderInstruction();
    }
    if(current?.id!==projectId || current.version!==attempt.version){attempt.phase='obsolete';clearAppliedInstruction(attempt,false);pendingInstructionPreview=null;throw new Error('方案已更新，请按当前方案重新预览。');}
    if(!attempt.shotIds.length || !attempt.shotIds.every(id=>(current.plan.shots || []).some(shot=>shot.id===id)))throw new Error('本次预览的镜头已不可用。请确认当前方案后手动预览。');
    const job=await api(`/api/projects/${projectId}/jobs`,{method:'POST',headers:{'Idempotency-Key':attempt.previewKey},body:{kind:'preview',quality:'preview',shot_ids:attempt.shotIds,expected_version:attempt.version}});
    attempt.submitted=true;attempt.phase='preview_submitted';
    if(current?.id===projectId && job?.id)current.jobs=[job,...(current.jobs || []).filter(item=>item.id!==job.id)];
    const refreshed=await api('/api/projects/'+projectId);
    if(current?.id===projectId){current=refreshed;clearAppliedInstruction(attempt);}
    attempt.phase='completed';pendingInstructionPreview=null;
    toast(attempt.remainingText?'可完成项已应用，正在生成预览；待确认要求已保留。':'调整已应用，正在生成预览。');
  }catch(error){
    const responseInvalid=!!attempt && !attempt.confirmed && attempt.kind!=='confirm' && Number.isInteger(error.status) && error.payload?.error_code==='instruction_response_invalid' && error.payload?.applied===false;
    if(attempt && !attempt.confirmed){
      const uncertain=!responseInvalid && (error.uncertain || error.payload?.error_code==='instruction_in_progress' || !Number.isInteger(error.status) || error.status>=500 || error.status===408);
      attempt.phase=uncertain?'unknown':'rejected';
      if(!uncertain)pendingInstructionPreview=null;
    }
    const prefix=attempt?.submitted?'调整已保存，预览任务已提交；状态暂未刷新：':attempt?.confirmed?'调整已保存，预览未启动：':attempt?.phase==='unknown'?'保存状态暂未确认；请核对同一请求：':'';
    const unresolved=!responseInvalid && error.payload?.error_code==='instruction_targets_unresolved' && Array.isArray(error.payload.pending) && error.payload.pending.length;
    const stale=error.payload?.error_code==='instruction_proposal_stale';
    const responseFeedback=responseInvalid?instructionResponseFeedback(error.payload):null;
    if(responseInvalid)instructionGuidance.set(projectId,{kind:'response_error',text,baseVersion:attempt.body.expected_version,shotId:attempt.shotId,source:clone(attempt.body.source || null),...responseFeedback});
    else if(unresolved)instructionGuidance.set(projectId,{kind:'clarification',text,pending:clone(error.payload.pending),details:[error.message,agentTraceLabel(error.payload.agent_trace)].filter(Boolean).join(' ')});
    if(stale && instructionGuidance.has(projectId))instructionGuidance.get(projectId).invalid=true;
    if(current?.id===projectId){$('instructionError').textContent=responseInvalid?responseFeedback.message:unresolved?'':stale?'方案已更新，请重新分析要求。':prefix+error.message;$('instructionError').hidden=!!unresolved;}
    else toast(responseInvalid?responseFeedback.message:prefix+error.message,true);
    if(error.status===409 && (attempt?.confirmed || stale)){
      try{const refreshed=await api('/api/projects/'+projectId);if(current?.id===projectId)current=refreshed;}catch(_){}
    }
  }finally{busy=false;instructionSubmitting=false;persistInstructionJournal();renderProject();}
}
function uploadValidation() {
  if(!uploadFiles.length)return '';
  const allowed=new Set(['glb','obj','mtl','zip','png','jpg','jpeg','webp','tga','bmp','tif','tiff','exr','hdr','json','txt']);
  const extension=file=>file.name.split('.').pop().toLowerCase();
  const unsupported=uploadFiles.find(file=>!allowed.has(extension(file)));
  if(unsupported)return `不支持 ${unsupported.name}。请移除后再上传模型与配套资源。`;
  if(uploadFiles.reduce((sum,file)=>sum+file.size,0)>=512*1024*1024)return '文件总量需小于 512 MB，请减少资源或压缩贴图。';
  const modelCount=uploadFiles.filter(file=>['glb','obj'].includes(extension(file))).length;
  if(modelCount>1)return '每个项目只能包含一个 GLB 或 OBJ，请移除多余模型。';
  if(!modelCount && !uploadFiles.some(file=>extension(file)==='zip'))return '还缺少模型文件。请添加一个 GLB / OBJ，或包含模型的 ZIP。';
  return '';
}
function addFiles(files) {
  if(uploadSubmitting)return;
  const pathOf=file=>file.webkitRelativePath || file._relativePath || file.name;
  for(const file of files){const index=uploadFiles.findIndex(item=>pathOf(item)===pathOf(file));if(index>=0)uploadFiles[index]=file;else uploadFiles.push(file);}
  $('chosenFiles').innerHTML=uploadFiles.map((file,index)=>`<div><span>${esc(pathOf(file))}</span><span>${filenameSize(file.size)} <button type="button" data-remove-file="${index}" aria-label="移除${esc(file.name)}">×</button></span></div>`).join('')+(uploadFiles.length?`<p class="field-help">已选 ${uploadFiles.length} 个文件 · 合计 ${filenameSize(uploadFiles.reduce((sum,file)=>sum+file.size,0))} · 上限 512 MB</p>`:'');
  $('uploadError').textContent=uploadValidation() || (uploadFiles.length && !workerRenderReady()?runtimeReason():'');
  $('uploadSubmit').disabled=!uploadFiles.length || !!uploadValidation() || !workerRenderReady();
}
async function readEntry(entry,path='') {
  if (entry.isFile) return new Promise((resolve,reject)=>entry.file(f=>{f._relativePath=path+f.name;resolve([f]);},reject));
  if (entry.isDirectory) {const reader=entry.createReader();let entries=[],batch;do {batch=await new Promise((resolve,reject)=>reader.readEntries(resolve,reject));entries.push(...batch);}while(batch.length);return (await Promise.all(entries.map(e=>readEntry(e,path+entry.name+'/')))).flat();}return [];
}
async function showUpload() {if(navigationBlocked())return toast('当前操作正在提交，完成后可新建项目。');if(!(await resolveUnsavedChanges()))return;ensureEcommerceCatalog().catch(()=>{});$('uploadError').textContent=uploadValidation() || (!workerRenderReady()?runtimeReason():'');$('uploadDialog').showModal();renderActionState();}
const inspectorTabs=['review','material','plan','overlay','ecommerce'];
function setInspectorTab(tab) {
  if(!inspectorTabs.includes(tab))return;
  if(tab!==inspectorTab){inspectorScrollPositions[inspectorTab]=$('inspectorBody').scrollTop || 0;$('inspectorBody').scrollTop=inspectorScrollPositions[tab] || 0;}
  const previousTab=inspectorTab;inspectorTab=tab;
  for(const name of inspectorTabs){
    const button=$('inspector'+name[0].toUpperCase()+name.slice(1)+'Tab');
    button.setAttribute('aria-selected',name===tab);button.tabIndex=name===tab?0:-1;
    $('inspector'+name[0].toUpperCase()+name.slice(1)).hidden=name!==tab;
  }
  if(tab==='material' && currentOutput && !feedbackMode){feedbackMode=true;renderFeedback();}
  if(tab==='ecommerce'){renderEcommerce();refreshEcommerce(true);}
  // The text and icon layer is edited on the rendered picture, so its tab shows the picture rather than the live 3D view.
  if(previousTab==='overlay' && tab!=='overlay')layerLeave();
  if((tab==='overlay')!==(previousTab==='overlay') && current){renderViewer();renderLayerPanel();}
}
function storeMaterialTask(projectId,task) {
  materialAgentRecords.set(projectId,[task,...(materialAgentRecords.get(projectId) || []).filter(item=>item.id!==task.id)]);
}
async function refreshMaterialTasks(projectId) {
  try{
    const tasks=await api(`/api/projects/${projectId}/material-agent/tasks`);
    if(deletedProjectIds.has(projectId))return;
    materialAgentRecords.set(projectId,Array.isArray(tasks)?tasks:[]);
    if(current?.id===projectId)renderMaterialAgent();
  }catch(error){if(current?.id===projectId)$('materialAgentError').textContent='无法读取材质搜索任务：'+error.message;}
}
function materialTaskIsActive(task) {return ['queued','running','rendering'].includes(task.status);}
function automaticMaterialBusy() {
  return ['queued','running','rendering','searching','downloading','applying'].includes(current?.auto_material?.status) || (materialAgentRecords.get(current?.id) || []).some(materialTaskIsActive);
}
function materialSourceLink(url) {
  try{const parsed=new URL(url);return parsed.protocol==='https:' && ['polyhaven.com','www.polyhaven.com','creativecommons.org'].includes(parsed.hostname)?parsed.href:null;}catch(_){return null;}
}
function renderAutomaticMaterialStatus(records) {
  const panel=$('autoMaterialStatus');
  const automatic=current?.auto_material,running=activeJob(),task=records.find(t=>t.origin==='automatic' && materialTaskIsActive(t));
  const inProgress=!!task || ['queued','running','rendering','searching','downloading','applying'].includes(automatic?.status);
  panel.hidden=!automatic && !records.length;
  if(inProgress)panel.open=true;else if(panel.dataset.running==='true')panel.open=false;
  panel.dataset.running=String(inProgress);
  const newPreview=running?.kind==='preview' && !String(running.request_key || '').startsWith('material-agent:') && !records.some(t=>t.job_id===running.id) && running.id!==automatic?.origin_job_id;
  let stage,notice='自动定位部件、寻找适用的 CC0 表面贴图并重新渲染；保留原色和标识。';
  if(newPreview && current?.plan?.visual_review!==false && current?.plan?.max_corrections!==0 && visionState().attemptable && visionState().status!=='error')stage='当前预览完成后自动检查材质';
  else if(task)stage=task.stage;
  else if(automatic && !newPreview){
    stage=automatic.stage || ({completed:'自动材质优化已完成',unchanged:'本轮保留现有材质',disabled:'自动材质优化已关闭',reverted:'已恢复优化前的材质与图片',needs_attention:'自动材质优化已结束，仍有待检查项',failed:'本轮材质优化未完成',skipped:'本轮保留现有材质',cancelled:'自动材质优化已取消',interrupted:'自动材质优化已中断'}[automatic.status] || '自动检查材质');
    const unique=new Set([stage.trim()]);
    notice=[automatic.error,automatic.notice,...(Array.isArray(automatic.notes)?automatic.notes:[])].filter(value=>{
      if(typeof value!=='string' || !value.trim() || unique.has(value.trim()))return false;unique.add(value.trim());return true;
    }).join('；');
  }
  else if(current?.plan?.visual_review===false || current?.plan?.max_corrections===0){stage='自动材质优化已关闭';notice='可在「参数 → 出图质量」中开启自动检查，并设置至少一轮自动修正。';}
  else if(!visionState().attemptable || visionState().status==='error'){stage='等待可用的 AI 视觉检查';notice=visionState().status==='error'?'视觉模型最近一次调用失败，下次检查会重试；无法分析时保留现有材质。':'视觉模型未配置时保留现有材质，基础渲染照常完成。';}
  else{stage=activeJob()?'预览完成后自动检查材质':'下一次生成预览时自动检查材质';}
  $('autoMaterialStage').textContent=stage || '自动检查材质';$('autoMaterialNotice').textContent=notice;$('autoMaterialNotice').hidden=!notice;
}
function renderMaterialAgent() {
  if(materialAgentProjectId!==current?.id){materialAgentProjectId=current?.id || '';materialAgentRenderedKey='';$('materialAgentPrompt').value='';$('materialAgentColor').checked=false;$('materialAgentError').textContent='';}
  const records=materialAgentRecords.get(current?.id) || [],running=records.some(materialTaskIsActive),selected=uniqueMaterialSelections();
  renderAutomaticMaterialStatus(records);renderRenderActivity();
  const blocked=partSelectionBlocked() || materialAgentSubmitting || running;
  $('materialAgentSubmit').disabled=blocked;
  $('materialAgentSubmit').textContent=materialAgentSubmitting?'正在提交…':running?'材质助手正在处理…':'寻找材质并应用';
  $('materialAgentPrompt').disabled=materialAgentSubmitting || running;
  $('materialAgentColor').disabled=materialAgentSubmitting || running;
  $('materialAgentHint').textContent=running?'按实际执行显示进度；下载后应用到所选部件，并重新渲染当前镜头。':dirty?'请先保存方案并生成新预览。':currentOutput && currentOutput.version!==current?.version?'这张图片已过期，请先生成最新预览。':selected.length?`已选 ${selected.length} 个部件材质。搜索 CC0 资源，下载并应用；默认保留原色和图案。`:'先在预览中点选一个或多个部件。仅对所选部件寻找并应用材质。';
    const statuses={queued:'等待执行',running:'正在寻找与应用',rendering:'真实预览中',completed:'预览已完成',failed:'需要处理',cancelled:'已取消',interrupted:'已中断'};
  const renderKey=JSON.stringify([current?.id,records]);
  if(materialAgentRenderedKey===renderKey)return;materialAgentRenderedKey=renderKey;
  const disclosure=(key)=>`data-material-detail="${esc(key)}" data-material-project="${esc(current?.id)}" ${materialAgentDisclosures.get(current?.id+':'+key)?'open':''}`;
  const taskHtml=task=>{
    const source=task.selection,sourceUrl=materialSourceLink(source?.url),licenseUrl=materialSourceLink(source?.license_url);
    return `<article class="material-agent-task ${['failed','interrupted'].includes(task.status)?'is-failed':''}"><div><strong>${task.origin==='automatic'?'自动 · ':''}${esc(statuses[task.status] || task.status)}</strong>${materialTaskIsActive(task)?`<button type="button" class="text-button" data-cancel-material="${esc(task.id)}">取消</button>`:''}</div><p>${esc(task.stage || '')}</p>${source?`<p>${sourceUrl?`<a href="${esc(sourceUrl)}" target="_blank" rel="noopener noreferrer">${esc(source.name || source.asset_id)}</a>`:esc(source.name || source.asset_id)} · ${licenseUrl?`<a href="${esc(licenseUrl)}" target="_blank" rel="noopener noreferrer">${esc(source.license || '查看许可')}</a>`:esc(source.license || '')}</p>`:''}${task.error?`<p class="error-text">${esc(task.error)}</p>`:''}<details ${disclosure(task.id)}><summary>来源与执行记录</summary><p>${esc(task.prompt)}</p>${source?.reason?`<p>${esc(source.reason)}</p>`:''}<p>${task.use_base_color?'已授权使用资源颜色':'保留原色与图案'}${task.applied_version?' · 方案 v'+Number(task.applied_version):''}</p></details></article>`;
  };
  const latest=records.find(materialTaskIsActive) || records[0],older=records.filter(task=>task.id!==latest?.id);
  $('materialAgentTasks').innerHTML=(latest?taskHtml(latest):'')+(older.length?`<details class="material-agent-history" ${disclosure('history')}><summary>之前的材质任务（${older.length}）</summary>${older.map(taskHtml).join('')}</details>`:'');
}
async function submitMaterialAgent(event) {
  event.preventDefault();if(instructionTargetSelection || $('materialAgentSubmit').disabled || !feedbackSource || !currentOutput)return;
  const prompt=$('materialAgentPrompt').value.trim();if(!prompt)return;
  const source=clone(feedbackSource);
  if(source.projectId!==current?.id || source.key!==sceneOutputKey(currentOutput))return toast('图片已改变，请重新选择部件。',true);
  const body={...source.snapshot,points:feedbackPoints.map(({id,x,y})=>({id,x,y})),prompt,use_base_color:$('materialAgentColor').checked};
  materialAgentSubmitting=true;$('materialAgentError').textContent='';renderProject();
  try{
    const task=await api(`/api/projects/${source.projectId}/material-agent/tasks`,{method:'POST',body});
    storeMaterialTask(source.projectId,task);
    if(current?.id===source.projectId)toast('材质助手已启动，将寻找资源、下载并生成真实预览。');
  }catch(error){if(current?.id===source.projectId)$('materialAgentError').textContent=error.message;}
  finally{materialAgentSubmitting=false;renderProject();}
}
// 导出 panel: marketplace choice, checks of the real renders, and the sized export.
let ecommerceCatalog=null,ecommerceCatalogPromise=null,ecommerceView=null,ecommerceViewKey='',ecommerceRequest=0,ecommerceSubmitting=false,ecommerceError='';
const ecommerceSeen=new Map();
function ecommerceSize(bytes) { return bytes>=1048576?String(Math.round(bytes/104857.6)/10)+' MB':Math.max(1,Math.round(bytes/1024))+' KB'; }
function ecommercePlatformLine(platform) {
  const white={required:'主图须纯白底',recommended:'建议纯白底',optional:'背景不限'}[platform.white];
  return [platform.aspects.join(' / '),`导出 ${platform.target_px} px`,white,platform.fill_min?`产品占比 ≥ ${Math.round(platform.fill_min*100)}%`:null,`≤ ${ecommerceSize(platform.max_bytes)}`].filter(Boolean).join(' · ');
}
function renderEcommercePlatformOptions() {
  if(!ecommerceCatalog)return;
  const groups=[...new Set(ecommerceCatalog.platforms.map(platform=>platform.group))];
  const html=groups.map(group=>`<optgroup label="${esc(group)}">${ecommerceCatalog.platforms.filter(platform=>platform.group===group).map(platform=>`<option value="${esc(platform.id)}">${esc(platform.name)}</option>`).join('')}</optgroup>`).join('');
  stableHTML($('ecommercePlatform'),html);
  const chosen=$('uploadPlatform').value || recalled('wuying-platform') || '';
  stableHTML($('uploadPlatform'),'<option value="">暂不指定</option>'+html);
  $('uploadPlatform').value=ecommerceCatalog.platforms.some(platform=>platform.id===chosen)?chosen:'';
}
function ensureEcommerceCatalog() {
  if(ecommerceCatalog)return Promise.resolve(ecommerceCatalog);
  if(!ecommerceCatalogPromise)ecommerceCatalogPromise=api('/api/ecommerce/platforms').then(catalog=>{ecommerceCatalog=catalog;renderEcommercePlatformOptions();return catalog;}).catch(error=>{ecommerceCatalogPromise=null;throw error;});
  return ecommerceCatalogPromise;
}
function ecommerceKey() {
  if(!current)return '';
  const outputs=current.outputs || [];
  return [current.id,current.version,outputs.length,outputs.at(-1)?.created_at || 0,JSON.stringify(current.ecommerce || {})].join(':');
}
function ecommerceControls() {
  return {platform:$('ecommercePlatform').value || ecommerceView?.settings?.platform || 'general',background:$('ecommerceBackground').value || 'main_white',shadow:$('ecommerceShadow').checked,transparent:$('ecommerceTransparent').checked};
}
async function refreshEcommerce(force=false) {
  if(!current || inspectorTab!=='ecommerce')return;
  const key=ecommerceKey();
  if(!force && key===ecommerceViewKey)return;
  const request=++ecommerceRequest,id=current.id;ecommerceViewKey=key;
  try{
    await ensureEcommerceCatalog();
    const view=await api(`/api/projects/${id}/ecommerce`);
    if(request!==ecommerceRequest || current?.id!==id)return;
    ecommerceView={...view,project_id:id};ecommerceError='';
  }catch(error){if(request!==ecommerceRequest || current?.id!==id)return;ecommerceError=error.message;}
  renderEcommerce();
}
async function saveEcommerceSettings() {
  if(!current)return;
  const id=current.id,request=++ecommerceRequest;
  try{
    const view=await api(`/api/projects/${id}/ecommerce`,{method:'PUT',body:ecommerceControls()});
    if(request!==ecommerceRequest || current?.id!==id)return;
    ecommerceView={...view,project_id:id};current.ecommerce=view.settings;ecommerceViewKey=ecommerceKey();ecommerceError='';
  }catch(error){if(request!==ecommerceRequest || current?.id!==id)return;ecommerceError=error.message;}
  renderEcommerce();
}
function ecommerceLatestExport() { return (current?.ecommerce_exports || [])[0] || null; }
function ecommerceExportRunning() { return (current?.ecommerce_exports || []).some(task=>['queued','running'].includes(task.status)); }
function renderEcommerceResult() {
  const task=ecommerceLatestExport(),box=$('ecommerceResult');
  if(task){const before=ecommerceSeen.get(task.id);if(before && before!==task.status && task.status==='completed')toast('平台套图已导出，可以下载。');ecommerceSeen.set(task.id,task.status);}
  box.hidden=!task || task.status==='cancelled';if(box.hidden)return;
  const running=['queued','running'].includes(task.status),files=Array.isArray(task.files)?task.files:[];
  const own=url=>typeof url==='string' && url.startsWith('/files/'+current.id+'/ecommerce/')?url:'';
  const tiles=files.map(file=>{const url=own(file.url),picture=url && /\.jpg$/i.test(url),issue=!!file.issues?.length;
    return `<li data-background="${esc(file.background || '')}" data-issue="${issue}"><a href="${esc(url || '#')}" target="_blank" rel="noopener" title="${esc(file.name)}">${picture?`<img src="${esc(url)}" alt="${esc(file.label)}" loading="lazy">`:`<img alt="${esc(file.label)}" ${url?`src="${esc(url)}" loading="lazy"`:''}>`}<span>${esc(file.label)}</span><small>${Number(file.width)} × ${Number(file.height)} · ${esc(ecommerceSize(Number(file.bytes) || 0))}</small></a></li>`;}).join('');
  const issues=files.flatMap(file=>(file.issues || []).map(issue=>`<li>${esc(file.label)}：${esc(issue)}</li>`)).concat((task.warnings || []).map(warning=>`<li>${esc(warning)}</li>`)).join('');
  const download=own(task.download_url);
  stableHTML(box,`<div class="ecommerce-result-head"><strong>${esc(task.platform_name || '')}${task.status==='completed'?` · ${files.length} 个文件`:''}</strong>${running?`<button type="button" class="button subtle" data-cancel-ecommerce="${esc(task.id)}">取消导出</button>`:task.status==='completed' && download?`<a class="button secondary" href="${esc(download)}?download=1" download>下载 ZIP</a>`:''}</div>${running?`<div class="progress-track" role="progressbar" aria-label="套图导出进度" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Number(task.progress) || 0}"><span style="width:${Math.min(100,Math.max(0,Number(task.progress) || 0))}%"></span></div>`:''}<p>${esc(task.stage || '')}${task.project_version && task.project_version!==current.version && !running?` · 来自方案 v${Number(task.project_version)}`:''}</p>${task.error?`<p class="error-text">${esc(task.error)}</p>`:''}${tiles?`<ul class="ecommerce-files">${tiles}</ul>`:''}${issues?`<ul class="ecommerce-issues">${issues}</ul>`:''}`);
}
function renderEcommerce() {
  if(!$('inspectorEcommerce'))return;
  const view=current && ecommerceView?.project_id===current.id?ecommerceView:null;
  const locked=!view || ecommerceSubmitting || busy || projectLoading || serviceDisconnected;
  $('ecommercePlatform').disabled=locked;
  $('ecommerceBackground').disabled=locked || !view.matte_available;
  $('ecommerceShadow').disabled=locked || !view.matte_available;$('ecommerceTransparent').disabled=locked || !view.matte_available;
  $('ecommerceError').textContent=ecommerceError;$('ecommerceError').hidden=!ecommerceError;
  renderEcommerceResult();
  if(!view){
    setText('ecommercePlatformSummary',current && !ecommerceError?'正在读取平台要求…':'');
    $('ecommercePlatformBasis').hidden=true;stableHTML($('ecommerceChecks'),'');setText('ecommerceCheckShot','');setText('ecommerceShotsSummary','');
    $('ecommerceAdapt').hidden=true;$('ecommerceExport').disabled=true;setText('ecommerceExportHint','');return;
  }
  const platform=view.platform,settings=view.settings;
  if(document.activeElement!==$('ecommercePlatform'))$('ecommercePlatform').value=platform.id;
  if(document.activeElement!==$('ecommerceBackground'))$('ecommerceBackground').value=view.matte_available?settings.background:'original';
  $('ecommerceBackground').title=view.matte_available?'纯白底与透明底只替换产品以外的背景，并自动居中、按平台占比构图':'纯白底与透明底需要完整的 Blender 运行环境';
  $('ecommerceShadow').checked=settings.shadow;$('ecommerceTransparent').checked=settings.transparent && view.matte_available;
  setText('ecommercePlatformSummary',ecommercePlatformLine(platform));
  $('ecommercePlatformBasis').hidden=false;
  stableHTML($('ecommercePlatformNotes'),`<ul>${platform.notes.map(note=>`<li>${esc(note)}</li>`).join('')}</ul>${platform.sources.length?`<p>来源：${platform.sources.map((url,index)=>`<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">平台规则 ${index+1}</a>`).join('、')}</p>`:''}<p>${esc(ecommerceCatalog?.disclaimer || '')}</p>`);
  const main=view.shots.find(shot=>shot.main);
  const shot=view.shots.find(item=>item.shot_id===activeShot) || main || view.shots[0];
  setText('ecommerceCheckShot',shot?shot.label:'');
  // One line per rule; the sentence appears only where the user has to act.
  stableHTML($('ecommerceChecks'),(shot?.items || []).map(item=>`<li data-status="${esc(item.status)}" title="${esc(item.detail)}"><span class="mark" aria-hidden="true"></span><span class="name">${esc(item.label)}</span><span class="value">${esc(item.value || '')}</span>${['action','warn'].includes(item.status)?`<span class="detail">${esc(item.detail)}</span>`:`<span class="sr-only">${esc(item.detail)}</span>`}</li>`).join(''));
  const small=view.shots.filter(item=>item.items.some(check=>check.id==='resolution' && check.status==='action')).length,counts=view.counts;
  setText('ecommerceShotsSummary',`已出图 ${counts.rendered} / ${counts.shots} 个镜头`);
  const patch=view.plan_recommendation || {},changes=[patch.aspect?`画幅 ${patch.aspect}`:null,typeof patch.margin==='number'?`留白 ${Math.round(patch.margin*100)}%`:null].filter(Boolean);
  $('ecommerceAdapt').hidden=!changes.length;$('ecommerceAdapt').disabled=locked || dirty || !!planSavePromise || !!activeJob() || automaticMaterialBusy() || !renderReady();
  setText('ecommerceAdapt',changes.length?`按平台重新构图（${changes.join('，')}）`:'');
  $('ecommerceAdapt').title='把平台建议的画幅与留白写入方案并重新预览；不执行也能导出，导出时会自动构图';
  const running=ecommerceExportRunning(),rendering=!!activeJob();
  $('ecommerceExport').disabled=locked || running || rendering || !counts.rendered;
  setText('ecommerceExport',ecommerceSubmitting || running?'正在导出…':'导出平台套图');
  // Say something under the button only when the export is blocked or will miss a rule.
  const hint=!counts.rendered?'生成预览后即可导出。':rendering?'渲染完成后可导出。':small?`${small} 个镜头只有预览尺寸，低于平台最低要求。`:'';
  setText('ecommerceExportHint',hint);$('ecommerceExportHint').hidden=!hint;$('ecommerceExportHint').dataset.tone=small && counts.rendered && !rendering?'warn':'';
}
async function submitEcommerceExport() {
  if(!current || ecommerceSubmitting || ecommerceExportRunning())return;
  const id=current.id;ecommerceSubmitting=true;ecommerceError='';renderEcommerce();
  try{
    const task=await api(`/api/projects/${id}/ecommerce/exports`,{method:'POST',body:ecommerceControls()});
    if(current?.id===id){ecommerceSeen.set(task.id,task.status);current.ecommerce_exports=[task,...(current.ecommerce_exports || []).filter(item=>item.id!==task.id)];toast(task.status==='completed'?'这组设置已经导出过，可直接下载。':'正在导出平台套图。');}
  }catch(error){if(current?.id===id)ecommerceError=error.message;}
  finally{ecommerceSubmitting=false;renderEcommerce();}
}
async function cancelEcommerceExport(taskId) {
  if(!current)return;
  const id=current.id;
  try{const task=await api(`/api/ecommerce/exports/${encodeURIComponent(taskId)}/cancel`,{method:'POST'});if(current?.id===id){current.ecommerce_exports=(current.ecommerce_exports || []).map(item=>item.id===task.id?task:item);toast('已取消导出，渲染图片不受影响。');}}
  catch(error){toast(error.message,true);}
  renderEcommerce();
}
async function adaptEcommercePlan() {
  if(!current || busy || planSavePromise || projectLoading || activeJob() || automaticMaterialBusy())return;
  if(dirty)return toast('请先保存或放弃当前未保存的调整。',true);
  const id=current.id;busy=true;renderProject();
  try{
    const project=await api(`/api/projects/${id}/ecommerce/adapt`,{method:'POST',body:{...ecommerceControls(),expected_version:current.version}});
    if(current?.id===id){current=project;syncSettings();}
  }catch(error){toast(error.message,true);busy=false;renderProject();return;}
  busy=false;renderProject();
  if(current?.id===id){toast('已按平台调整画幅与留白。');await run('preview');}
}
function installEvents() {
  // Validate after revealing the affected advanced panel, including folded inputs.
  $('settingsForm').noValidate=true;
  document.querySelectorAll('[data-inspector]').forEach(button=>{button.onclick=()=>setInspectorTab(button.dataset.inspector);button.onkeydown=event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();const tabs=[...document.querySelectorAll('[data-inspector]')].map(tab=>tab.dataset.inspector),index=tabs.indexOf(inspectorTab);const next=event.key==='Home'?tabs[0]:event.key==='End'?tabs[tabs.length-1]:tabs[(index+(event.key==='ArrowRight'?1:tabs.length-1))%tabs.length];setInspectorTab(next);document.querySelector('[data-inspector="'+next+'"]').focus();};});
  $('comparePreview').onclick=()=>{realtime.mode='image';comparePrevious=!comparePrevious;feedbackMode=false;renderViewer();renderInspection();renderInstruction();};
  $('materialAgentForm').onsubmit=submitMaterialAgent;
  $('materialAgentTasks').addEventListener('toggle',event=>{const target=event.target;if(target.tagName==='DETAILS' && target.dataset.materialDetail)materialAgentDisclosures.set(target.dataset.materialProject+':'+target.dataset.materialDetail,target.open);},true);
  $('materialAgentTasks').onclick=async event=>{const button=event.target.closest('[data-cancel-material]');if(!button || !current)return;button.disabled=true;try{const projectId=current.id,task=await api(`/api/projects/${projectId}/material-agent/tasks/${button.dataset.cancelMaterial}/cancel`,{method:'POST'});storeMaterialTask(projectId,task);if(current?.id===projectId)renderMaterialAgent();}catch(error){$('materialAgentError').textContent=error.message;button.disabled=false;}};
  setInspectorTab('review');
  $('aiSettingsButton').onclick=showAiSettings;
  $('aiSettingsForm').onsubmit=e=>{e.preventDefault();saveAiSettings(false);};$('aiSaveTest').onclick=()=>saveAiSettings(true);$('visionSaveTest').onclick=()=>saveAiSettings('vision');
  $('aiModelPreset').onchange=selectAiPreset;
  $('aiEnabled').onchange=()=>refreshAiSettingsDraft();
  $('visionEnabled').onchange=()=>{renderVisionFields();refreshAiSettingsDraft('vision');};
  for(const prefix of ['ai','vision']){
    $(prefix+'Provider').onchange=()=>refreshAiSettingsDraft(prefix);
    for(const field of ['BaseUrl','Model'])$(prefix+field).oninput=()=>refreshAiSettingsDraft(prefix);
    $(prefix+'ClearKey').onchange=()=>{if($(prefix+'ClearKey').checked)$(prefix+'ApiKey').value='';refreshAiSettingsDraft(prefix);};
    $(prefix+'ApiKey').oninput=()=>{if($(prefix+'ApiKey').value)$(prefix+'ClearKey').checked=false;$('aiSettingsError').textContent='';renderAiConnection(aiSettings);};
  }
  $('aiSettingsDialog').addEventListener('close',()=>{$('aiApiKey').value='';$('aiClearKey').checked=false;$('visionApiKey').value='';$('visionClearKey').checked=false;});
  $('feedbackToggle').onclick=()=>{realtime.mode='image';feedbackMode=!feedbackMode;if(!feedbackMode)clearFeedback(true);else setInspectorTab('material');renderViewer();renderInstruction();};
  $('feedbackClear').onclick=()=>{clearFeedback(false);renderInstruction();};
  $('instructionTargetContinue').onclick=continueInstructionTargets;
  $('instructionTargetCancel').onclick=()=>{clearFeedback(true);setInspectorTab('review');renderInstruction();$('instructionInput').focus({preventScroll:true});};
  for(const [field,[id,use]] of Object.entries(partFieldControls)){
    $(id).addEventListener(field==='preset'?'change':'input',()=>{
      const selected=field!=='preset' || !!$(id).value;
      partMaterialValueFields.add(field);
      $(use).checked=selected;if(selected)partMaterialFields.add(field);else partMaterialFields.delete(field);
      $('partMaterialError').textContent='';renderPartMaterial();
    });
    $(use).onchange=()=>{if($(use).checked)partMaterialFields.add(field);else partMaterialFields.delete(field);$('partMaterialError').textContent='';renderPartMaterial();};
  }
  $('partMaterialForm').onsubmit=event=>{event.preventDefault();submitPartMaterial(false);};$('partMaterialReset').onclick=()=>submitPartMaterial(true);
  $('reviewRefreshButton').onclick=requestRealismReview;
  $('feedbackAddPoint').onclick=()=>addFeedbackPoint({x:readNumber('feedbackX')/100,y:readNumber('feedbackY')/100});
  $('renderImage').onclick=e=>{if(realtimeActive() || comparePrevious || busy || planSavePromise || projectLoading || realismBusy || materialAgentSubmitting || activeJob() || automaticMaterialBusy() || !currentOutput || system?.runtime?.capabilities?.pick===false)return;const point=pointFromClient(e.clientX,e.clientY);if(point){feedbackMode=true;setInspectorTab('material');addFeedbackPoint(point);}};
  $('renderImage').addEventListener('load',renderFeedbackMarkers);window.addEventListener('resize',renderFeedbackMarkers);bindLayerControls();
  const removePoint=e=>{const button=e.target.closest('[data-remove-point]');if(button && !busy){feedbackPoints=feedbackPoints.filter(point=>point.id!==button.dataset.removePoint);renderFeedback();renderInstruction();}};
  $('feedbackSelection').onclick=removePoint;$('feedbackMarkers').onclick=removePoint;
  $('newProject').onclick=showUpload;$('uploadMain').onclick=()=>pickUploadFiles(false);$('homeBrowseFolder').onclick=()=>pickUploadFiles(true);
  $('exitProjectButton').onclick=exitProject;
  document.querySelector('.brand').onclick=event=>{event.preventDefault();exitProject();};
  $('mobileDeleteProject').innerHTML='<span>删除项目</span>';
  // Menus are plain <details>; close them on an outside click, a chosen item or Escape.
  document.addEventListener('click',event=>{for(const menu of document.querySelectorAll('details.menu[open]'))if(!menu.contains(event.target) || event.target.closest('.menu-item:not([data-keep-open])'))menu.open=false;});
  document.addEventListener('keydown',event=>{if(event.key!=='Escape')return;for(const menu of document.querySelectorAll('details.menu[open]')){menu.open=false;menu.querySelector('summary')?.focus();}});
  $('mobileDeleteProject').onclick=()=>{if(current)deleteProject(current.id);};
  $('mobileNewProject').onclick=showUpload;
  $('mobileProjectSelect').onchange=async()=>{const id=$('mobileProjectSelect').value;if(id)await openProject(id);$('mobileProjectSelect').value=current?.id || '';};
  document.querySelectorAll('.close-dialog').forEach(b=>b.onclick=()=>{const dialog=b.closest('dialog');if(dialog.id==='uploadDialog' && uploadSubmitting)return;dialog.close();});
  $('uploadDialog').addEventListener('cancel',event=>{if(uploadSubmitting)event.preventDefault();});
  $('sceneExportButton').onclick=()=>showSceneExport();
  $('generateLatestC4d').onclick=generateLatestC4d;
  $('latestSceneDetails').onclick=()=>showSceneExport(latestOutput(activeShot),'c4d');
  $('sceneExportForm').onsubmit=submitSceneExport;
  $('sceneExportSource').onchange=()=>{const output=current?.outputs?.find(item=>sceneOutputKey(item)===$('sceneExportSource').value);sceneExportSource=output?clone(output):null;$('sceneExportError').textContent='';renderSceneExportControls();};
  document.querySelectorAll('[name=sceneExportFormat]').forEach(input=>input.onchange=()=>{$('sceneExportError').textContent='';renderSceneExportControls();});
  $('browseFiles').onclick=e=>{e.stopPropagation();$('modelFiles').click();};$('browseFolder').onclick=e=>{e.stopPropagation();$('modelFolder').click();};$('dropzone').onclick=e=>{if(e.target===$('dropzone') || !e.target.closest('button'))$('modelFiles').click();};$('dropzone').onkeydown=e=>{if(e.target===$('dropzone') && (e.key==='Enter' || e.key===' ')){e.preventDefault();$('modelFiles').click();}};
  $('modelFiles').onchange=e=>{const files=[...e.target.files];e.target.value='';continueUpload(files);};$('modelFolder').onchange=e=>{const files=[...e.target.files];e.target.value='';continueUpload(files);};
  for (const event of ['dragenter','dragover']) $('dropzone').addEventListener(event,e=>{e.preventDefault();$('dropzone').classList.add('dragging');});
  $('dropzone').ondragleave=()=>$('dropzone').classList.remove('dragging');$('dropzone').ondrop=async e=>{e.preventDefault();$('dropzone').classList.remove('dragging');try{addFiles(await droppedFiles(e));}catch(error){$('uploadError').textContent=error.message;}};
  for (const event of ['dragenter','dragover']) $('homeDropzone').addEventListener(event,e=>{if(![...(e.dataTransfer?.types || [])].includes('Files'))return;e.preventDefault();$('homeDropzone').classList.add('dragging');});
  $('homeDropzone').ondragleave=e=>{if(!$('homeDropzone').contains(e.relatedTarget))$('homeDropzone').classList.remove('dragging');};
  $('homeDropzone').ondrop=async e=>{e.preventDefault();$('homeDropzone').classList.remove('dragging');if(navigationBlocked())return toast('当前操作正在提交，完成后可新建项目。');try{await continueUpload(await droppedFiles(e));}catch(error){toast(error.message,true);}};
  $('chosenFiles').onclick=e=>{const b=e.target.closest('[data-remove-file]');if(b){uploadFiles.splice(Number(b.dataset.removeFile),1);addFiles([]);}};
  $('uploadForm').onsubmit=async event=>{
    event.preventDefault();if(uploadSubmitting || !uploadFiles.length)return;
    const validation=uploadValidation();if(validation || !workerRenderReady()){$('uploadError').textContent=validation || runtimeReason();return;}
    const form=new FormData($('uploadForm'));uploadFiles.forEach(file=>form.append('files',file));form.append('paths',JSON.stringify(uploadFiles.map(file=>file.webkitRelativePath || file._relativePath || file.name)));
    uploadSubmitting=true;$('uploadForm').inert=true;$('uploadSubmit').disabled=true;$('uploadSubmit').textContent='正在上传并创建项目…';$('uploadError').textContent='';
    let created;
    try{created=await api('/api/projects',{method:'POST',body:form});}
    catch(error){$('uploadError').textContent=error.message;}
    finally{uploadSubmitting=false;$('uploadForm').inert=false;$('uploadSubmit').textContent='上传并生成主图';renderActionState();}
    if(created){if(form.get('platform'))remember('wuying-platform',form.get('platform'));else forget('wuying-platform');$('uploadDialog').close();uploadFiles=[];$('uploadForm').reset();$('uploadPlatform').value=recalled('wuying-platform') || '';addFiles([]);try{await refreshList();}catch(error){toast('项目已创建，但项目列表暂未刷新：'+error.message,true);}await openProject(created.id);toast('模型已上传，分析完成后会自动生成白底主图。');}
  };
  $('projectList').onclick=e=>{const removal=e.target.closest('[data-delete-project]');if(removal){deleteProject(removal.dataset.deleteProject);return;}const b=e.target.closest('[data-project]');if(b)openProject(b.dataset.project);};
  $('homeProjectGrid').onclick=e=>{const removal=e.target.closest('[data-delete-project]');if(removal){deleteProject(removal.dataset.deleteProject);return;}const card=e.target.closest('[data-open-project]');if(card)openProject(card.dataset.openProject);};
  $('exampleList').onclick=async e=>{const b=e.target.closest('[data-example]');if(!b || !workerRenderReady() || navigationBlocked())return;if(!(await resolveUnsavedChanges()))return;b.disabled=true;try{const p=await api('/api/examples/'+b.dataset.example+'/import',{method:'POST'});await refreshList();await openProject(p.id);toast('验收模型已导入，正在进行真实分析。');}catch(error){toast(error.message,true);}finally{b.disabled=false;}};
  $('optimizeHeroButton').onclick=optimizeHero;
  $('sceneButton').onclick=generateScene;$('sceneRemove').onclick=removeScene;$('sceneRecommend').onclick=recommendScene;
  $('sceneAudienceSelect').onchange=()=>{fillSceneReferences();if(current)renderSceneSet();};
  for(const id of ['sceneReferenceSelect','sceneMethodSelect'])$(id).onchange=()=>{if(current)renderSceneSet();};
  $('sceneHint').addEventListener('input',()=>{if(current)renderSceneSet();});
  $('previewButton').onclick=()=>run('preview');$('renderButton').onclick=()=>run('render');
  $('settingsForm').onsubmit=async e=>{e.preventDefault();try{await savePlan();}catch(error){toast(error.message,true);}};
  $('settingsForm').addEventListener('input',e=>{if(['materialSlot','cameraShot','quality','materialOrigin','materialLicense','externalColor','externalRoughness','externalNormal'].includes(e.target.id))return;if(e.target.id==='materialColor' && selectedMaterial)editedMaterialColors.add(selectedMaterial);markDirty();updateRangeLabels();if(e.target.id==='margin' && draft){draft.margin=readNumber('margin')/100;updateRealtimeFraming();}});
  $('settingsForm').addEventListener('change',e=>{if(e.target.matches('[name=aspect]')){updateRangeLabels();if(draft){draft.aspect=e.target.value;updateRealtimeFraming();}}if(e.target.matches('[name=style]')){focusStyleShot(e.target.value,draft.style);draft.style=e.target.value;renderProject();}});
  for(const id of ['azimuth','elevation','focalLength','cameraProjection'])$(id).addEventListener('change',()=>{syncProjectionField();stashCamera();if(realtime.instance && realtime.status==='ready' && cameraShot===activeShot){realtime.instance.setShot(clone(shots().find(s=>s.id===activeShot)),draft);realtime.changed=false;renderRealtimeStage();}});
  $('materialSlot').onchange=()=>{stashMaterial();selectedMaterial=$('materialSlot').value;syncMaterial();};
  $('materialPreset').onchange=()=>{const p=$('materialPreset').value;const defaults={matte_plastic:[.5,0,0],glossy_plastic:[.17,0,0],painted_metal:[.28,.7,0],brushed_metal:[.3,1,0],polished_metal:[.09,1,0],glass:[.04,0,1],rubber:[.8,0,0],fabric:[.85,0,0],leather:[.5,0,0],wood:[.45,0,0],ceramic:[.2,0,0]};if(defaults[p])['roughness','metallic','transmission'].forEach((id,i)=>setValue(id,defaults[p][i]));if(!p){editedMaterialColors.delete(selectedMaterial);stashMaterial();syncMaterial();}$('materialEditing').hidden=!p;markDirty();updateRangeLabels();};
  $('cameraShot').onchange=()=>{stashCamera();cameraShot=$('cameraShot').value;activeShot=cameraShot;comparePrevious=false;styleFocusNotice='';remember('wuying-shot-'+current.id,activeShot);syncCamera();renderViewer();renderShots();renderInspection();renderActionState();};
  document.querySelectorAll('[data-tab]').forEach(b=>{b.tabIndex=b.getAttribute('aria-selected')==='true'?0:-1;b.onclick=()=>{document.querySelectorAll('[data-tab]').forEach(x=>{x.setAttribute('aria-selected',x===b);x.tabIndex=x===b?0:-1;});for(const key of ['materials','camera','structure'])$(key+'Tab').hidden=key!==b.dataset.tab;};});
  for(const selector of ['.settings-tabs']){const list=document.querySelector(selector);if(!list)continue;list.addEventListener('keydown',event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;const tabs=[...list.querySelectorAll('[role=tab]')].filter(tab=>!tab.hidden),index=tabs.indexOf(event.target);if(index<0)return;event.preventDefault();const next=event.key==='Home'?0:event.key==='End'?tabs.length-1:(index+(event.key==='ArrowRight'?1:tabs.length-1))%tabs.length;tabs[next].click();tabs[next].focus();});}
  $('shotList').onclick=e=>{const b=e.target.closest('[data-shot]');if(b){stashCamera();activeShot=b.dataset.shot;comparePrevious=false;styleFocusNotice='';remember('wuying-shot-'+current.id,activeShot);cameraShot=activeShot;$('cameraShot').value=cameraShot;syncCamera();renderViewer();renderShots();renderInspection();renderActionState();}};
  $('shotList').onchange=e=>{if(e.target.dataset.selectShot){if(e.target.checked)selectedShots.add(e.target.dataset.selectShot);else selectedShots.delete(e.target.dataset.selectShot);renderProject();}};
  $('selectAllShots').onchange=()=>{selectedShots=$('selectAllShots').checked?new Set(shots().map(s=>s.id)):new Set();renderProject();};
  if($('selectCurrentShot'))$('selectCurrentShot').onclick=()=>{selectedShots=new Set(activeShot?[activeShot]:[]);renderProject();};
  if($('discardPlan'))$('discardPlan').onclick=()=>{if(busy || planSavePromise)return;syncSettings();renderProject();toast('已放弃本次草稿，恢复当前保存的方案。');};
  $('bundleButton').onclick=event=>{if($('bundleButton').getAttribute('aria-disabled')==='true')event.preventDefault();};
  $('jobPanel').onclick=async e=>{const autoCancel=e.target.closest('[data-cancel-auto-material]');if(autoCancel){try{await api(`/api/projects/${current.id}/material-agent/tasks/${autoCancel.dataset.cancelAutoMaterial}/cancel`,{method:'POST'});await poll();toast('自动优化已取消，已完成图片保留。');}catch(error){toast(error.message,true);}return;}const cancel=e.target.closest('[data-cancel]'),retry=e.target.closest('[data-retry]');if(!cancel && !retry)return;try{const id=cancel?.dataset.cancel || retry.dataset.retry;await api(`/api/jobs/${id}/${cancel?'cancel':'retry'}`,{method:'POST'});await poll();toast(cancel?'已发送取消请求，已完成图片保留。':'正在继续原任务的未完成镜头。');}catch(error){toast(error.message,true);}};
  $('undoButton').onclick=async()=>{
    if(!current || dirty || navigationBlocked() || activeJob() || automaticMaterialBusy())return;
    const projectId=current.id;busy=true;renderProject();
    try{const project=await api(`/api/projects/${projectId}/undo`,{method:'POST'});if(current?.id===projectId){current=project;syncSettings();}toast('已回退上一版已保存方案；现有图片保留，重新预览查看变化。');}
    catch(error){toast(error.message,true);}finally{busy=false;renderProject();}
  };
  $('instructionForm').onsubmit=applyInstructionAndPreview;
  $('ecommercePlatform').onchange=saveEcommerceSettings;$('ecommerceShadow').onchange=saveEcommerceSettings;$('ecommerceTransparent').onchange=saveEcommerceSettings;
  $('ecommerceBackground').onchange=saveEcommerceSettings;
  $('ecommerceExport').onclick=submitEcommerceExport;$('ecommerceAdapt').onclick=adaptEcommercePlan;
  $('ecommerceResult').onclick=event=>{const button=event.target.closest('[data-cancel-ecommerce]');if(button)cancelEcommerceExport(button.dataset.cancelEcommerce);};
  $('instructionLocate').onclick=locatePendingInstruction;
  $('instructionInput').addEventListener('input',()=>{if(current){const value=$('instructionInput').value;instructionDrafts.set(current.id,value);if(instructionPreflightDrafts.has(current.id)){if(value.trim())instructionPreflightDrafts.set(current.id,value);else instructionPreflightDrafts.delete(current.id);persistInstructionPreflightDrafts();}}currentInstructionAttempt();$('instructionError').textContent='';$('instructionError').hidden=true;renderInstruction();});
  $('toggleGrid').onclick=()=>{$('compositionGrid').hidden=!$('compositionGrid').hidden;$('toggleGrid').setAttribute('aria-pressed',!$('compositionGrid').hidden);};
  $('liveViewButton').onclick=()=>setViewerMode('live');$('savedViewButton').onclick=()=>setViewerMode('image');$('reviewImageSource').onclick=()=>setViewerMode('image');
  $('applyViewportView').onclick=()=>{if($('applyViewportView').disabled)return;try{applyRealtimeView();}catch(error){toast(error.message,true);}};
  $('resetViewport').onclick=()=>{realtime.instance?.reset();realtime.changed=false;renderRealtimeControls();};
  $('retryViewport').onclick=()=>{disposeRealtime(false);ensureRealtime();renderRealtimeStage();};
  window.addEventListener('wuying-viewport-ready',()=>{if(current)renderViewer();});
  $('zoomImage').onclick=async()=>{if(realtimeActive()){try{if(document.fullscreenElement)await document.exitFullscreen();else if($('viewer').requestFullscreen)await $('viewer').requestFullscreen();else toast('当前浏览器不支持全屏，请放大浏览器窗口查看。');}catch(_){toast('无法进入全屏，请放大浏览器窗口查看。',true);}}else if(currentOutput){$('fullImage').src=currentOutput.png_url;$('imageDialog').showModal();}else toast('生成预览后即可查看原图。');};
  $('systemInfo').onclick=async()=>{
    try{system=await api('/api/system');serviceDisconnected=false;}catch(error){serviceDisconnected=true;}
    updateSystemStatus();renderActionState();
    const missing=system?.runtime?.missing_components || [];
    $('systemDetails').innerHTML=`<p><strong>${renderReady()?'本机渲染已就绪':'运行环境需要处理'}</strong></p><p>${esc(runtimeReason() || '可以开始模型分析与渲染。')}</p>${missing.length?`<p>请从完整项目中补齐以下组件：</p><ul>${missing.map(path=>`<li><code>${esc(path)}</code></li>`).join('')}</ul>`:''}${!system?.blender_available && !system?.blender?'<p>安装 Blender 并配置其可执行文件路径后，重新启动应用。</p>':''}<p>${system?.ai_mode==='ai'?'AI 服务已配置；是否可用以连接测试结果为准。':'AI 未配置：基础渲染和手动材质编辑不需要 AI Key。'}</p><details><summary>查看技术信息</summary><pre>${esc(JSON.stringify(system,null,2))}</pre></details>`;
    $('infoDialog').showModal();
  };
  if($('runtimeHelp'))$('runtimeHelp').onclick=()=>$('systemInfo').click();
  $('importMaterial').onclick=async()=>{
    if(navigationBlocked() || activeJob() || automaticMaterialBusy())return;
    if(!current || !selectedMaterial)return toast('请先选择一个材质槽。',true);
    if(!$('materialOrigin').value.trim() || !$('materialLicense').value.trim())return toast('请填写贴图来源和使用许可。',true);
    if(!['externalColor','externalRoughness','externalNormal'].some(id=>$(id).files[0]))return toast('请至少选择一张 PBR 贴图。',true);
    const projectId=current.id,form=new FormData();form.set('name',selectedMaterial);form.set('preset',$('materialPreset').value || 'matte_plastic');form.set('source',$('materialOrigin').value);form.set('license',$('materialLicense').value);
    for(const [id,channel]of [['externalColor','base_color'],['externalRoughness','roughness'],['externalNormal','normal']])if($(id).files[0])form.set(channel,$(id).files[0]);
    busy=true;renderProject();
    try{await savePlan(true);const project=await api(`/api/projects/${projectId}/materials/import`,{method:'POST',body:form});if(current?.id===projectId){current=project;syncSettings();}for(const id of ['externalColor','externalRoughness','externalNormal'])$(id).value='';toast('贴图已保存，点击预览查看材质变化。');}
    catch(error){toast(error.message,true);}finally{busy=false;renderProject();}
  };
  window.addEventListener('beforeunload',e=>{if(dirty || uploadSubmitting || planSavePromise){e.preventDefault();e.returnValue='';}});
}
async function start() {
  restoreInstructionPreflightDrafts();restoreInstructionJournal();
  restoreHeroOptimizationJournal();
  const presetOptions=Object.entries(presets).map(([k,v])=>`<option value="${k}">${v}</option>`).join('');
  $('materialPreset').insertAdjacentHTML('beforeend',presetOptions);$('partPreset').insertAdjacentHTML('beforeend',presetOptions);installEvents();setInspectorTab('review');renderActionState();
  const results=await Promise.allSettled([api('/api/system'),refreshList(),api('/api/examples')]);
  if(results[0].status==='fulfilled'){system=results[0].value;}else{serviceDisconnected=true;toast(results[0].reason.message,true);}updateSystemStatus();renderActionState();
  if(results[1].status==='rejected')toast(results[1].reason.message,true);
  if(results[2].status==='fulfilled'){$('exampleArea').hidden=!results[2].value.length;$('exampleList').innerHTML=results[2].value.map(e=>`<button type="button" class="example-chip" data-example="${esc(e.id)}" title="${esc(e.description)}" ${workerRenderReady()?'':'disabled'}>${esc(e.name)}</button>`).join('');}
  const saved=recalled('wuying-project');if(saved && allProjects.some(p=>p.id===saved))await openProject(saved);
  setInterval(poll,2000);
}
start().catch(e=>toast(e.message,true));
