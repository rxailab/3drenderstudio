/**
 * UI state regressions using the actual HTML and app.js.
 * Validated with Node.js v24.22.1 and jsdom 31.0.0 (npm install --no-save jsdom@31.0.0).
 * Use a Node.js version supported by the chosen jsdom release.
 * Run: node --test tests/test_ui_state.cjs
 * UI_TEST_JSDOM_PATH may point at an existing jsdom installation.
 * No browser, Blender process, external service, or user files are accessed.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { JSDOM } = require(process.env.UI_TEST_JSDOM_PATH || 'jsdom');
const root = path.resolve(__dirname,'..');
const html = fs.readFileSync(path.join(root,'static/index.html'),'utf8');
const script = fs.readFileSync(path.join(root,'static/app.js'),'utf8').replace(/start\(\)\.catch\(e=>toast\(e.message,true\)\);\s*$/, '');
const copy = value => JSON.parse(JSON.stringify(value));
function project(id='a') {
  return {id,name:'测试项目 '+id,status:'ready',version:1,created_at:1,metadata:{},history:[],outputs:[],jobs:[],exports:[],
    analysis:{materials:[{name:'BodyMaterial',color:[.2,.3,.4,1],roughness:.43,metallic:.03,transmission:0,source:'model',confidence:1}],objects:[{name:'Body',faces:100,uv_layers:['UVMap']}],warnings:[],features:{},animations:[]},
    plan:{style:'soft',aspect:'1:1',margin:.12,rotation:[0,0,0],exposure:0,light_softness:1,lighting:{key_power:1,fill_power:1,rim_power:1,environment:1},texture_scale:1,texture_rotation:0,material_overrides:[],object_material_overrides:[],repair:{normals:false,smooth:false},pose:{},time_budget:1200,max_corrections:2,visual_review:true,pending:[],source:'rules',shots:[
      {id:'hero',label:'白底主图',kind:'full',azimuth:35,elevation:18,focal_length:70,style:'white'},
      {id:'campaign',label:'棚拍宣传',kind:'full',azimuth:-38,elevation:22,focal_length:60},
      {id:'side',label:'侧面结构',kind:'full',azimuth:90,elevation:12,focal_length:70}]}};
}
function marketplaces() {
  const base={aspects:['1:1'],aspect:'1:1',min_px:1000,target_px:2000,max_px:10000,max_bytes:10485760,white:'required',fill_min:.85,fill_target:.88,notes:['主图必须为纯白底。'],sources:['https://example.test/rule']};
  return {reviewed:'2026-11',default:'general',disclaimer:'以商家后台提示为准。',platforms:[
    {...base,id:'general',name:'通用白底主图',group:'通用',white:'recommended',fill_min:null,fill_target:.85,min_px:800,sources:[]},
    {...base,id:'amazon',name:'Amazon 亚马逊',group:'跨境'},
    {...base,id:'taobao',name:'淘宝 / 天猫',group:'国内',aspects:['1:1','3:4'],white:'optional',fill_min:null,target_px:1440,min_px:800,max_bytes:3145728}]};
}
// Mirrors the server's overview shape; statuses follow the size of the newest render.
function marketplaceView(h,p) {
  const settings={platform:'general',background:'main_white',shadow:true,transparent:false,...(p.ecommerce || {})};
  const platform=h.catalog.platforms.find(item=>item.id===settings.platform);
  const shots=p.plan.shots.map((shot,index)=>{
    const output=[...p.outputs].filter(item=>item.shot_id===shot.id).sort((a,b)=>b.version-a.version)[0];
    if(!output)return {shot_id:shot.id,label:shot.label,kind:shot.kind,main:index===0,output:null,status:'missing',items:[{id:'render',status:'action',action:'preview',label:'尚未出图',detail:'这个镜头还没有渲染图片。'}]};
    const large=Math.max(output.width,output.height)>=platform.min_px;
    const items=[large?{id:'resolution',status:'pass',label:'分辨率',value:`${platform.target_px} × ${platform.target_px}`,detail:`将导出 ${platform.target_px} × ${platform.target_px} px。`}:{id:'resolution',status:'action',action:'render',label:'分辨率',value:`${Math.max(output.width,output.height)} px，需渲染 4K`,detail:'当前只有预览图，渲染 4K 后即可导出。'},
      {id:'background',status:'auto',label:'纯白背景',value:'导出时处理',detail:'导出时自动处理为纯白 RGB 255。'}];
    return {shot_id:shot.id,label:shot.label,kind:shot.kind,main:index===0,output:{version:output.version,quality:output.quality,width:output.width,height:output.height},export:{aspect:'1:1',cut:index===0,reframe:true,size:[platform.target_px,platform.target_px]},status:large?'ready':'action',items};
  });
  const rendered=shots.filter(shot=>shot.output);
  return {platform,settings,matte_available:h.matteAvailable!==false,main_shot_id:shots[0].shot_id,shots,step:!rendered.length?'preview':rendered[0].status==='ready'?'export':'render',
    counts:{shots:shots.length,rendered:rendered.length,exportable:rendered.filter(shot=>shot.status==='ready').length},plan_recommendation:h.planRecommendation || {}};
}
function environment(ready=true) {
  return {available:ready,blender_available:true,blender:'/qa/blender',runtime:{available:ready,message:ready?'':'缺少 blender/worker.py，请恢复完整安装包。',missing_components:ready?[]:['blender/worker.py'],capabilities:{render:ready,pick:ready,export_blend:ready,export_c4d:false}},ai_mode:'rules',ai:{enabled:false,key_configured:false,model:'',status:'disabled'},scene_exports:{blender_available:true,export_available:ready,c4d_export_available:false}};
}
function deferred() {let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}
async function setup(t,{ready=true,projects=[project('a'),project('b')],journal=null,heroJournal=null}={}) {
  const dom=new JSDOM(html,{url:'http://127.0.0.1:8765',runScripts:'outside-only',pretendToBeVisual:true});
  if(journal!==null)dom.window.sessionStorage.setItem('wuying-instruction-attempts-v1',typeof journal==='string'?journal:JSON.stringify(journal));
  if(heroJournal!==null)dom.window.sessionStorage.setItem('wuying-hero-optimization-attempts-v1',typeof heroJournal==='string'?heroJournal:JSON.stringify(heroJournal));
  const uiErrors=[];dom.window.addEventListener('error',event=>uiErrors.push(event.message));
  const w=dom.window,context=dom.getInternalVMContext(),records=new Map(projects.map(p=>[p.id,copy(p)])),calls=[];
  // jsdom does not implement the native dialog open/close methods. The app's
  // dialog markup, handlers, focus calls, and promises still execute unchanged.
  w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
  w.HTMLDialogElement.prototype.close=function(){this.open=false;this.dispatchEvent(new w.Event('close'));};
  w.setInterval=()=>1;w.setTimeout=()=>1;w.clearTimeout=()=>{};
  const h={dom,w,records,calls,instructions:new Map(),afterInstruction:null,system:environment(ready),intercept:null,run:code=>vm.runInContext(code,context),node:id=>w.document.getElementById(id)};
  h.catalog=marketplaces();h.ecommerceView=p=>marketplaceView(h,p);
  const response=(data,status=200)=>({ok:status>=200&&status<300,status,json:async()=>copy(data)});
  h.response=response;
  w.fetch=async(url,options={})=>{
    const call={url,method:options.method||'GET',headers:options.headers||{},body:typeof options.body==='string'?JSON.parse(options.body):options.body};calls.push(call);
    if(h.intercept){const intercepted=await h.intercept(call);if(intercepted!==undefined)return intercepted;}
    if(url==='/api/system')return response(h.system);
    if(url==='/api/projects' && call.method==='GET')return response([...records.values()]);
    if(url==='/api/examples')return response([]);
    if(url==='/api/ecommerce/platforms')return response(h.catalog);
    const exportCancel=url.match(/^\/api\/ecommerce\/exports\/([^/]+)\/cancel$/);
    if(exportCancel){for(const p of records.values()){const task=(p.ecommerce_exports || []).find(item=>item.id===exportCancel[1]);if(task){task.status='cancelled';return response(task);}}return response({error:'记录不存在'},404);}
    if(/\/material-agent\/tasks$/.test(url))return response([]);
    if(url==='/api/ai/settings')return response(h.system.ai);
    const match=url.match(/^\/api\/projects\/([^/]+)(?:\/(.*))?$/);
    if(match){
      const p=records.get(match[1]),action=match[2];
      if(!p)return response({error:'记录不存在'},404);
      if(!action && call.method==='DELETE'){records.delete(p.id);return response({deleted:true,id:p.id,cleanup_pending:false});}
      if(!action)return response(p);
      if(action==='ecommerce'){
        if(call.method==='PUT')p.ecommerce={...(p.ecommerce || {}),...call.body};
        return response(h.ecommerceView(p));
      }
      if(action==='ecommerce/exports'){
        const preset=h.catalog.platforms.find(item=>item.id===call.body.platform);if(!preset)return response({error:'未知的销售平台'},400);
        const task={id:'ecom-'+calls.length,project_id:p.id,platform:preset.id,platform_name:preset.name,options:copy(call.body),status:'queued',stage:'等待导出',progress:0,error:null,warnings:[],files:[],download_url:null,project_version:p.version,created_at:calls.length};
        p.ecommerce_exports=[task,...(p.ecommerce_exports || [])];return response(task,202);
      }
      if(action==='ecommerce/adapt'){
        if(call.body.expected_version!==p.version)return response({error:'方案已在后台更新'},409);
        p.history.push({version:p.version,plan:copy(p.plan),at:1,reason:'按平台调整画幅与留白'});p.plan.margin=.06;p.version++;p.ecommerce={platform:call.body.platform};return response(p);
      }
      if(action==='plan'){
        if(Number(call.headers['X-Plan-Version'])!==p.version)return response({error:'方案版本已更新'},409);
        p.history.push({version:p.version,plan:copy(p.plan),at:1,reason:'手动修改'});p.plan=copy(call.body);p.version++;return response(p);
      }
      if(action==='instruct'){
        const key=call.headers['Idempotency-Key'],stored=h.instructions.get(p.id+':'+key);
        if(stored){
          if(JSON.stringify(call.body)!==stored.body)return response({error:'同一请求的内容不一致'},409);
          return response({...p,instruction_result:{...stored.receipt,replayed:true}});
        }
        if(!key || call.body.expected_version!==p.version || !p.plan.shots.some(shot=>shot.id===call.body.shot_id))return response({error:'指令上下文版本不匹配'},409);
        const base=p.version,before=p.plan.exposure;
        p.history.push({version:p.version,plan:copy(p.plan),at:1,reason:call.body.text});p.plan.exposure+=.5;p.version++;
        const receipt={id:'instruction-'+calls.length,text:call.body.text,source:'rules',changes:[{path:'exposure',before,after:p.plan.exposure}],summary:'曝光已按测试规则增加',notes:[],shot_id:call.body.shot_id,used_image:false,used_reference:false,base_version:base,applied_version:p.version,replayed:false,status:'applied'};
        p.instruction_result=copy(receipt);h.instructions.set(p.id+':'+key,{body:JSON.stringify(call.body),receipt:copy(receipt)});
        if(h.afterInstruction){const alternative=await h.afterInstruction(call,p,receipt);if(alternative!==undefined)return alternative;}
        return response(p);
      }
      if(action==='review'){
        if(call.body.diagnostic_only!==true)throw new Error('Manual review must be diagnostic-only');
        const output=p.outputs.find(item=>item.job_id===call.body.output_id && item.shot_id===call.body.shot_id && item.version===call.body.version && item.quality===call.body.quality);
        if(!output)return response({error:'图片不存在'},404);
        output.review={mode:'diagnostic_only',source:'rules',summary:'测试诊断结论',status:'passed',issues:[]};return response(p);
      }
      if(action==='jobs'){
        const key=call.headers['Idempotency-Key'],existing=p.jobs.find(job=>job.request_key===key);if(existing)return response(existing);
        if(call.body.expected_version!==undefined && call.body.expected_version!==p.version)return response({error:'预览版本已经变化'},409);
        const job={id:'job-'+calls.length,status:'queued',kind:call.body.kind,shot_ids:call.body.shot_ids,quality:call.body.quality,version:p.version,created_at:2,stage:'排队中',request_key:key};p.jobs.unshift(job);return response(job,202);
      }
      if(action==='hero-optimize'){
        const key=call.headers['Idempotency-Key'],existing=p.jobs.find(job=>job.kind==='hero_optimize' && job.request_key===key);
        if(existing)return response(existing,202);
        if(!key || call.body.expected_version!==p.version || call.body.final_resolution!==2000 || !p.outputs.some(output=>output.job_id===call.body.source?.output_id && output.shot_id===call.body.shot_id && output.version===call.body.source?.version && output.quality===call.body.source?.quality))return response({error:'优化来源或版本已变化'},409);
        const job={id:'hero-'+calls.length,project_id:p.id,kind:'hero_optimize',status:'queued',shot_ids:[call.body.shot_id],quality:'hero',version:p.version,created_at:50,stage:'主图优化已排队',workflow_phase:'inspecting',request_key:key,completed:[]};p.jobs.unshift(job);return response(job,202);
      }
    }
    throw new Error('Unexpected test API: '+call.method+' '+url);
  };
  vm.runInContext(script,context,{filename:'app.js'});await h.run('start()');
  t.after(()=>{dom.window.close();assert.deepEqual(uiErrors,[],'UI event handlers should not throw');});
  return h;
}
function input(h,id,value){h.node(id).value=value;h.node(id).dispatchEvent(new h.w.Event('input',{bubbles:true}));}
async function tick(){await new Promise(resolve=>setImmediate(resolve));}
function installRealtime(h,options={}) {
  const prior=h.intercept,instances=[],assetCalls=[];
  const defaultView={projection:'orthographic',azimuth:35,elevation:18,ortho_scale:4,target_offset:[0,0,0]};
  const metadata={schema:'wuying-viewport-v1',coordinate_system:'gltf-y-up',bounds:{min:[-1,0,-1],max:[1,2,1],center:[0,1,0],diagonal:Math.sqrt(12)},camera:defaultView,render:{aspect:1}};
  h.intercept=async call=>{
    if(options.intercept){const result=await options.intercept(call);if(result!==undefined)return result;}
    if(/\/viewport(?:\/[^/]+)?$/.test(call.url)){
      assetCalls.push(call);const id=call.url.split('/')[3],p=h.records.get(id);
      return h.response({id:'asset-'+id,status:'ready',project_id:id,version:p.version,shot_id:'hero',source_version:p.version,asset:{url:`/files/${id}/viewport/asset/scene.glb`,metadata_url:`/files/${id}/viewport/asset/scene.json`},warnings:[]});
    }
    if(call.url.includes('/viewport/') && call.url.endsWith('.json'))return h.response(metadata);
    if(prior)return prior(call);
  };
  h.w.WuyingViewport={create:(host,callbacks)=>{
    if(options.createError)throw new Error(options.createError);
    const instance={camera:{isOrthographicCamera:true,isPerspectiveCamera:false},view:copy(defaultView),visible:false,disposed:false,loads:0,resets:0,
      load:async()=>{instance.loads++;if(options.load)await options.load(instance);},
      setVisible:value=>{instance.visible=value;},dispose:()=>{instance.disposed=true;},snapshot:()=>copy(instance.view),
      acceptView:()=>callbacks.onChange(false),setShot:(shot,plan)=>{instance.view=copy(shot);instance.lastPlan=copy(plan);instance.camera.isPerspectiveCamera=shot.projection==='perspective';},
      restoreUnappliedView:view=>{instance.view=copy(view);callbacks.onChange(true);},
      reset:()=>{instance.resets++;instance.view=copy(defaultView);callbacks.onChange(false);},
      orbit:view=>{Object.assign(instance.view,view);callbacks.onChange(true);}};
    instances.push(instance);return instance;
  }};
  h.w.dispatchEvent(new h.w.Event('wuying-viewport-ready'));
  return {instances,assetCalls,metadata};
}
async function settleRealtime(h) {for(let i=0;i<10 && !['ready','failed'].includes(h.run('realtime.status'));i++)await tick();}

test('realtime hero defaults to an actual async model stage and leaves photo-only shots unchanged',async t=>{
  const h=await setup(t);await h.run("openProject('a')");const rt=installRealtime(h);await settleRealtime(h);
  assert.equal(h.node('realtimeViewport').hidden,false);assert.equal(h.node('renderImage').hidden,true);assert.equal(h.node('viewportStatus').hidden,true);
  assert.equal(h.node('viewModeSwitch').hidden,false);assert.equal(h.node('savedViewButton').disabled,true);
  assert.equal(h.node('cameraProjection').value,'orthographic');assert.equal(h.node('focalLength').disabled,true);
  assert.deepEqual(rt.assetCalls[0].body,{shot_id:'hero',expected_version:1});
  h.node('shotList').querySelector('[data-shot="side"]').click();
  assert.equal(rt.instances[0].disposed,true);assert.equal(h.node('viewModeSwitch').hidden,true);assert.equal(h.node('renderEmpty').hidden,false);
});
test('dragging and periodic project refreshes neither save nor recreate the camera, including offline rendering',async t=>{
  const h=await setup(t);await h.run("openProject('a')");const rt=installRealtime(h);await settleRealtime(h);
  rt.instances[0].orbit({azimuth:111,ortho_scale:2.5});
  for(let i=0;i<5;i++)h.run('renderProject()');
  assert.equal(rt.instances.length,1);assert.equal(rt.assetCalls.length,1);assert.equal(h.run('dirty'),false);
  assert.equal(h.calls.filter(call=>call.url.endsWith('/plan')).length,0);assert.equal(h.node('applyViewportView').hidden,false);
  h.records.get('a').jobs=[{id:'offline',status:'running',kind:'render',shot_ids:['hero'],quality:'fine',version:1,stage:'采样中'}];
  await h.run('poll()');assert.equal(rt.instances[0].visible,true);assert.equal(h.node('applyViewportView').disabled,true);
  assert.equal(h.node('viewer').classList.contains('is-realtime'),true);assert.equal(h.node('viewer').classList.contains('is-rendering-empty'),false);
  rt.instances[0].orbit({azimuth:-66});assert.equal(rt.instances[0].view.azimuth,-66);
});
test('preview captures the visible orbit, pan and zoom once into the versioned shot before rendering',async t=>{
  const h=await setup(t);await h.run("openProject('a')");const rt=installRealtime(h);await settleRealtime(h);
  rt.instances[0].orbit({azimuth:-110,elevation:22,ortho_scale:3.125,target_offset:[.1,-.2,.05]});
  await h.run("run('preview')");
  const saved=h.calls.find(call=>call.url.endsWith('/plan')),job=h.calls.find(call=>call.url.endsWith('/jobs'));
  const hero=saved.body.shots.find(shot=>shot.id==='hero');
  assert.equal(hero.projection,'orthographic');assert.equal(hero.azimuth,-110);assert.equal(hero.ortho_scale,3.125);assert.deepEqual(hero.target_offset,[.1,-.2,.05]);
  assert.equal(saved.headers['X-Plan-Version'],'1');assert.equal(job.body.expected_version,2);assert.deepEqual(job.body.shot_ids,['hero']);
  assert.ok(h.calls.indexOf(saved)<h.calls.indexOf(job));assert.equal(h.calls.filter(call=>call.url.endsWith('/plan')).length,1);
});
test('live image switching preserves photo provenance and part picking always returns to the saved image',async t=>{
  const h=await setup(t,{projects:[previewProject()]});await h.run("openProject('a')");const rt=installRealtime(h);await settleRealtime(h);
  assert.equal(h.node('imageDownloads').hidden,true);assert.equal(h.node('reviewImageSource').hidden,false);
  const source=h.run('currentOutput.png_url');h.node('feedbackToggle').click();
  assert.equal(h.node('renderImage').hidden,false);assert.equal(h.node('renderImage').getAttribute('src'),source);assert.equal(rt.instances[0].visible,false);
  assert.equal(h.node('feedbackMarkers').hidden,false);assert.equal(h.node('savedViewButton').getAttribute('aria-pressed'),'true');
  h.node('liveViewButton').click();assert.equal(h.node('renderImage').hidden,true);
  await h.run('requestRealismReview()');
  assert.equal(h.node('renderImage').hidden,false);assert.equal(rt.instances[0].visible,false);
  const review=h.calls.find(call=>call.url.endsWith('/review'));assert.ok(review.body.output_id);assert.equal(review.body.diagnostic_only,true);
});
test('late viewport responses cannot cross projects or override a chosen image mode',async t=>{
  const h=await setup(t,{projects:[previewProject(),project('b')]});await h.run("openProject('a')");const gate=deferred();
  const rt=installRealtime(h,{intercept:call=>call.url==='/api/projects/a/viewport'?gate.promise:undefined});
  await h.run("openProject('b')");await settleRealtime(h);
  gate.resolve(h.response({id:'late-a',project_id:'a',version:1,shot_id:'hero',status:'ready',asset:{url:'/files/a/viewport/asset/scene.glb',metadata_url:'/files/a/viewport/asset/scene.json'}}));await tick();
  assert.equal(h.run('current.id'),'b');assert.equal(h.run('realtime.record.project_id'),'b');assert.equal(rt.instances.length,1);
  await h.run("openProject('a')");h.node('savedViewButton').click();await settleRealtime(h);
  assert.equal(h.node('renderImage').hidden,false);assert.equal(h.node('realtimeViewport').hidden,true);assert.equal(h.run('realtime.mode'),'image');
});
test('WebGL initialization errors keep the saved image accessible and show an explicit retry',async t=>{
  const h=await setup(t,{projects:[previewProject()]});await h.run("openProject('a')");installRealtime(h,{createError:'浏览器未能开启 3D 加速'});await settleRealtime(h);
  assert.equal(h.run('realtime.status'),'failed');assert.equal(h.node('retryViewport').hidden,false);assert.match(h.node('viewportStatusMessage').textContent,/3D 加速/);
  h.node('savedViewButton').click();assert.equal(h.node('renderImage').hidden,false);assert.equal(h.node('viewportStatus').hidden,true);
});
test('unsafe asset URLs are rejected before fetching and retry can recover without changing a plan',async t=>{
  const h=await setup(t);await h.run("openProject('a')");let invalid=true;
  const rt=installRealtime(h,{intercept:call=>invalid && call.url==='/api/projects/a/viewport'?h.response({id:'bad',status:'ready',project_id:'a',version:1,shot_id:'hero',asset:{url:'https://elsewhere.invalid/model.glb',metadata_url:'https://elsewhere.invalid/scene.json'}}):undefined});
  await settleRealtime(h);assert.equal(h.run('realtime.status'),'failed');assert.equal(rt.instances.length,0);assert.ok(!h.calls.some(call=>call.url.includes('elsewhere.invalid')));
  invalid=false;h.node('retryViewport').click();await settleRealtime(h);assert.equal(h.run('realtime.status'),'ready');assert.equal(rt.instances.length,1);assert.equal(h.run('current.version'),1);
});
test('late GLB loads are disposed after project exit',async t=>{
  const h=await setup(t);await h.run("openProject('a')");const gate=deferred(),rt=installRealtime(h,{load:()=>gate.promise});
  for(let i=0;i<5 && !rt.instances.length;i++)await tick();assert.equal(rt.instances.length,1);
  h.run('leaveProjectView()');gate.resolve();await tick();assert.equal(rt.instances[0].disposed,true);assert.equal(h.run('realtime.instance'),null);
  assert.equal(h.node('realtimeViewport').hidden,true);
});
test('AI apply first previews the saved live camera when the photo becomes stale',async t=>{
  const h=await setup(t,{projects:[previewProject()]});await h.run("openProject('a')");const rt=installRealtime(h);await settleRealtime(h);
  rt.instances[0].orbit({azimuth:150,ortho_scale:2.4,target_offset:[.08,0,.01]});input(h,'instructionInput','灯光再亮一些');
  assert.match(h.node('instructionContext').textContent,/AI 参考已渲染图片/);
  await h.run('applyInstructionAndPreview({preventDefault(){}})');
  const saved=h.calls.find(call=>call.url.endsWith('/plan')),instruction=h.calls.find(call=>call.url.endsWith('/instruct')),job=h.calls.find(call=>call.url.endsWith('/jobs'));
  assert.equal(saved.body.shots[0].azimuth,150);assert.equal(saved.body.shots[0].ortho_scale,2.4);
  assert.equal(instruction,undefined);assert.equal(job.body.expected_version,2);
  assert.equal(h.node('instructionInput').value,'灯光再亮一些');assert.match(h.node('instructionError').textContent,/当前方案的预览/);
});
test('asset-only completion preserves an unsubmitted orbit but a new plan version starts from its own camera',async t=>{
  const h=await setup(t,{projects:[previewProject()]});await h.run("openProject('a')");const rt=installRealtime(h);await settleRealtime(h);
  rt.instances[0].orbit({azimuth:123,ortho_scale:1.75,target_offset:[.1,0,0]});
  const p=h.records.get('a'),fresh={...p.outputs[0],job_id:'completed-new-image',created_at:999,png_url:'/files/a/new.png'};p.outputs.push(fresh);
  await h.run('poll()');await settleRealtime(h);
  assert.equal(rt.instances.length,2);assert.equal(rt.instances[0].disposed,true);assert.equal(rt.instances[1].view.azimuth,123);assert.equal(h.run('realtime.changed'),true);assert.equal(h.run('dirty'),false);
  p.version++;p.plan.shots[0].azimuth=40;await h.run('poll()');await settleRealtime(h);
  assert.equal(rt.instances.length,3);assert.equal(h.run('realtime.changed'),false);assert.notEqual(rt.instances[2].view.azimuth,123);
});
test('unavailable backend viewport capability keeps the actual saved picture and starts no conversion',async t=>{
  const h=await setup(t,{projects:[previewProject()]});await h.run("openProject('a')");h.run('system.viewport={available:false}');const rt=installRealtime(h);await tick();
  assert.equal(rt.assetCalls.length,0);assert.equal(h.node('viewModeSwitch').hidden,true);assert.equal(h.node('renderImage').hidden,false);
});
test('aspect and margin edits update live framing, while perspective distance and pan survive saving',async t=>{
  const h=await setup(t);await h.run("openProject('a')");const rt=installRealtime(h);await settleRealtime(h);
  const aspect=h.w.document.querySelector('[name="aspect"][value="4:5"]') || [...h.w.document.querySelectorAll('[name="aspect"]')].find(node=>node.value!=='1:1');
  aspect.checked=true;aspect.dispatchEvent(new h.w.Event('input',{bubbles:true}));aspect.dispatchEvent(new h.w.Event('change',{bubbles:true}));
  assert.equal(rt.instances[0].lastPlan.aspect,aspect.value);input(h,'margin','18');assert.equal(rt.instances[0].lastPlan.margin,.18);
  rt.instances[0].orbit({projection:'perspective',azimuth:42,elevation:17,camera_distance:8,focal_length:85,target_offset:[0,.1,.02]});h.node('applyViewportView').click();
  assert.equal(h.node('cameraProjection').value,'perspective');assert.equal(h.node('focalLength').disabled,false);await h.run('savePlan(true)');
  const shot=h.records.get('a').plan.shots[0];assert.equal(shot.camera_distance,8);assert.equal(shot.focal_length,85);assert.deepEqual(shot.target_offset,[0,.1,.02]);
  await settleRealtime(h);
});
test('camera change receipts and safe response fields use understandable projection labels',async t=>{
  const h=await setup(t);await h.run("openProject('a')");
  const rows=h.run("instructionDiffRows({changes:[{path:'shots',before:[{id:'hero',projection:'perspective'}],after:[{id:'hero',projection:'orthographic',ortho_scale:2,target_offset:[.1,0,0]}]}]})");
  assert.ok(rows.some(row=>row.label==='投影方式' && row.before==='透视' && row.after==='正交'));
  assert.ok(rows.some(row=>row.label==='取景宽度'));assert.ok(rows.some(row=>row.label==='画面平移'));
  assert.equal(h.run("instructionResponseFeedback({response_issue:{kind:'range',field:'ortho_scale'}}).field"),'ortho_scale');
});
test('discarding an accepted live camera draft restores the saved scene instead of leaving a stale view',async t=>{
  const h=await setup(t);await h.run("openProject('a')");const rt=installRealtime(h);await settleRealtime(h);
  rt.instances[0].orbit({azimuth:160,ortho_scale:1.5});h.node('applyViewportView').click();
  assert.equal(h.run('draft.shots[0].azimuth'),160);assert.equal(h.run('dirty'),true);
  h.node('discardPlan').click();await settleRealtime(h);
  assert.equal(h.run('dirty'),false);assert.equal(h.run('draft.shots[0].azimuth'),35);assert.equal(rt.instances[0].disposed,true);assert.equal(rt.instances.at(-1).view.azimuth,35);
  assert.equal(h.calls.filter(call=>call.url.endsWith('/plan')).length,0);
});
test('camera edits made during first asset loading take effect when the model arrives',async t=>{
  const h=await setup(t);await h.run("openProject('a')");const gate=deferred(),rt=installRealtime(h,{load:()=>gate.promise});
  for(let i=0;i<5 && !rt.instances.length;i++)await tick();
  input(h,'azimuth','110');h.node('azimuth').dispatchEvent(new h.w.Event('change',{bubbles:true}));
  gate.resolve();await settleRealtime(h);
  assert.equal(rt.instances[0].view.azimuth,110);assert.equal(h.run('dirty'),true);assert.equal(h.calls.filter(call=>call.url.endsWith('/plan')).length,0);
});

function aiConfiguration(overrides={}) {
  const main={provider:'anthropic',base_url:'https://api.anthropic.com',model:'custom-vision-model',enabled:true,key_configured:true,status:'verified',message:'连接已验证',request_allowed:true,blocked_reason:'',...overrides};
  return {...main,model_presets:[
    {id:'qwen-flash-cn',label:'千问 3.8 Flash · 日常推荐',provider:'openai',base_url:'https://dashscope.aliyuncs.com/compatible-mode/v1',model:'qwen3.8-flash',key_label:'阿里云百炼 API Key',vision:true},
    {id:'same-service',label:'当前服务的另一模型',provider:main.provider,base_url:main.base_url,model:'another-vision-model',key_label:'当前服务商的 API Key',vision:true}
  ],vision_config:{...main,inherits:true},...overrides};
}
function selectPreset(h,id='qwen-flash-cn') {h.node('aiModelPreset').value=id;h.node('aiModelPreset').dispatchEvent(new h.w.Event('change'));}

test('AI settings keep the saved custom model and inherit it for visual analysis',async t=>{
  const h=await setup(t);h.system.ai=aiConfiguration();await h.run('showAiSettings()');
  assert.equal(h.node('aiModel').value,'custom-vision-model');assert.equal(h.node('aiModelPreset').value,'');
  assert.equal(h.node('visionEnabled').checked,false);assert.equal(h.node('visionFields').hidden,true);
  for(const field of ['Provider','BaseUrl','Model'])assert.equal(h.node('vision'+field).value,h.node('ai'+field).value);
  assert.equal(h.node('aiModelPreset').options.length,3);assert.equal(h.node('aiApiKey').value,'');assert.equal(h.node('visionApiKey').value,'');
  assert.equal(h.calls.filter(call=>call.method!=='GET').length,0);
  assert.doesNotMatch(html+script,/gpt-5\.5-2026-04-23/,'the UI must not embed the prohibited model as a default or fallback');
});

test('selecting a preset changes only the draft and discards keys entered for the old endpoint',async t=>{
  const h=await setup(t);h.system.ai=aiConfiguration();await h.run('showAiSettings()');
  input(h,'aiApiKey','old-endpoint-draft-secret');const saved=JSON.stringify(h.run('system.ai'));
  selectPreset(h);
  assert.equal(h.node('aiProvider').value,'openai');assert.equal(h.node('aiModel').value,'qwen3.8-flash');
  assert.equal(h.node('aiBaseUrl').value,'https://dashscope.aliyuncs.com/compatible-mode/v1');assert.equal(h.node('aiApiKey').value,'');
  assert.match(h.node('aiKeyHint').textContent,/原 Key 不会用于新地址/);assert.match(h.node('aiKeyHint').textContent,/阿里云百炼/);
  assert.match(h.node('aiConnectionStatus').textContent,/尚未保存/);assert.equal(h.node('aiConnectionStatus').classList.contains('is-verified'),false);
  assert.equal(JSON.stringify(h.run('system.ai')),saved);assert.equal(h.calls.filter(call=>call.method!=='GET').length,0);
  input(h,'aiApiKey','new-endpoint-draft-secret');input(h,'aiModel','my-custom-model');assert.equal(h.node('aiModelPreset').value,'');assert.equal(h.node('aiApiKey').value,'new-endpoint-draft-secret');
  input(h,'aiBaseUrl','https://another-provider.example/v1');assert.equal(h.node('aiApiKey').value,'');
  h.node('aiSettingsDialog').close();await h.run('showAiSettings()');assert.equal(h.node('aiModel').value,'custom-vision-model');assert.equal(h.node('aiApiKey').value,'');
});

test('switching a model on the same endpoint retains a newly entered key',async t=>{
  const h=await setup(t);h.system.ai=aiConfiguration();await h.run('showAiSettings()');
  input(h,'aiApiKey','same-service-new-secret');selectPreset(h,'same-service');
  assert.equal(h.node('aiApiKey').value,'same-service-new-secret');assert.equal(h.node('aiModel').value,'another-vision-model');
  assert.equal(h.node('visionModel').value,'another-vision-model');assert.doesNotMatch(h.node('aiKeyHint').textContent,/服务地址或接口已更换/);
});

test('saving a changed endpoint without a new key clears credentials and skips connection calls',async t=>{
  const h=await setup(t);h.system.ai=aiConfiguration();await h.run('showAiSettings()');selectPreset(h);
  h.intercept=call=>{if(call.url==='/api/ai/settings' && call.method==='PUT'){
    assert.equal(call.body.clear_key,true);assert.equal(call.body.api_key,undefined);
    h.system.ai=aiConfiguration({provider:'openai',base_url:call.body.base_url,model:call.body.model,key_configured:false,request_allowed:false,status:'missing_key',message:'请填写当前服务商的 API Key'});return h.response(h.system.ai);
  }};
  await h.run('saveAiSettings(true)');
  assert.equal(h.calls.filter(call=>call.url==='/api/ai/test').length,0);assert.equal(h.run('system.ai_mode'),'rules');
  assert.match(h.node('aiSettingsError').textContent,/设置已保存.*API Key/);assert.equal(h.node('aiSaveTest').disabled,false);assert.equal(h.node('aiModelPreset').disabled,false);
});

test('save and test sends only the new key and reports a saved but unverified connection accurately',async t=>{
  const h=await setup(t);h.system.ai=aiConfiguration();await h.run('showAiSettings()');input(h,'aiApiKey','old-draft-secret');selectPreset(h);input(h,'aiApiKey','new-provider-secret');
  h.intercept=call=>{
    if(call.url==='/api/ai/settings' && call.method==='PUT'){
      assert.equal(call.body.api_key,'new-provider-secret');assert.equal(call.body.clear_key,undefined);assert.doesNotMatch(JSON.stringify(call.body),/old-draft-secret/);
      h.system.ai=aiConfiguration({provider:'openai',base_url:call.body.base_url,model:call.body.model,status:'configured',message:'尚未测试'});return h.response(h.system.ai);
    }
    if(call.url==='/api/ai/test'){h.system.ai.status='error';h.system.ai.message='模型无访问权限';return h.response({success:false,message:'模型无访问权限'});}
  };
  await h.run('saveAiSettings(true)');
  assert.equal(h.calls.filter(call=>call.url==='/api/ai/test').length,1);assert.equal(h.node('aiApiKey').value,'');assert.equal(h.run('system.ai.model'),'qwen3.8-flash');
  assert.match(h.node('aiSettingsError').textContent,/设置已保存.*模型无访问权限.*重试/);assert.equal(h.node('aiSaveTest').disabled,false);
  assert.equal(h.node('aiConnectionStatus').textContent,'模型无访问权限');assert.equal(h.node('aiConnectionStatus').classList.contains('is-error'),true);
});

test('a blocked saved model remains visible for replacement and cannot trigger an AI test',async t=>{
  const h=await setup(t);h.system.ai=aiConfiguration({model:'gpt-5.5-2026-04-23',status:'blocked_model',request_allowed:false,blocked_reason:'此型号已停用，请选择其他模型。'});
  await h.run('showAiSettings()');assert.equal(h.node('aiModel').value,'gpt-5.5-2026-04-23');assert.equal(h.node('aiConnectionStatus').classList.contains('is-error'),true);
  assert.match(h.node('aiConnectionStatus').textContent,/已停用.*选择其他模型/);assert.equal(h.run('visionState().attemptable'),false);assert.equal(h.run('system.ai_mode'),'rules');
  await h.run('saveAiSettings(true)');assert.equal(h.calls.filter(call=>call.url==='/api/ai/test').length,0);assert.match(h.node('aiSettingsError').textContent,/已停用/);
  selectPreset(h);assert.match(h.node('aiConnectionStatus').textContent,/尚未保存/);assert.equal(h.node('aiConnectionStatus').classList.contains('is-error'),false);
});

test('vision endpoint changes discard entered keys and returning to inheritance removes the independent configuration',async t=>{
  const h=await setup(t);h.system.ai=aiConfiguration({vision_config:{provider:'openai',base_url:'https://vision.example/v1',model:'vision-custom',enabled:true,key_configured:true,status:'verified',request_allowed:true,inherits:false}});
  await h.run('showAiSettings()');assert.equal(h.node('visionEnabled').checked,true);assert.equal(h.node('visionModel').value,'vision-custom');
  input(h,'visionApiKey','old-vision-secret');input(h,'visionBaseUrl','https://other-vision.example/v1');assert.equal(h.node('visionApiKey').value,'');assert.match(h.node('visionKeyHint').textContent,/新 Key/);
  h.node('visionEnabled').checked=false;h.node('visionEnabled').dispatchEvent(new h.w.Event('change'));assert.equal(h.node('visionFields').hidden,true);assert.equal(h.node('visionModel').value,h.node('aiModel').value);
  h.intercept=call=>{if(call.url==='/api/ai/settings' && call.method==='PUT'){
    for(const field of ['vision_provider','vision_base_url','vision_model','vision_enabled'])assert.equal(call.body[field],'');
    assert.equal(call.body.vision_clear_key,true);assert.equal(call.body.vision_api_key,undefined);h.system.ai=aiConfiguration();return h.response(h.system.ai);
  }};
  await h.run('saveAiSettings(false)');assert.equal(h.node('visionApiKey').value,'');assert.equal(h.run('system.ai.vision_config.inherits'),true);
});

test('a failed settings save preserves the edited model and provides a retry action',async t=>{
  const h=await setup(t);h.system.ai=aiConfiguration();await h.run('showAiSettings()');selectPreset(h);input(h,'aiModel','unavailable-custom-model');
  h.intercept=call=>call.url==='/api/ai/settings' && call.method==='PUT'?h.response({error:'服务地址格式无效'},400):undefined;
  await h.run('saveAiSettings(false)');
  assert.equal(h.node('aiModel').value,'unavailable-custom-model');assert.equal(h.run('system.ai.model'),'custom-vision-model');assert.match(h.node('aiSettingsError').textContent,/保存未完成.*服务地址格式无效.*重试/);
  assert.equal(h.node('aiSave').disabled,false);assert.equal(h.calls.filter(call=>call.url==='/api/ai/test').length,0);
});

test('choosing a preset also replaces a still-blocked independent vision draft without overwriting a new valid draft',async t=>{
  const h=await setup(t),oldVision={provider:'openai',base_url:'https://api.hai.network/openai',model:'gpt-5.5-2026-04-23',enabled:true,key_configured:true,status:'blocked_model',request_allowed:false,blocked_reason:'已停用，请选择其他型号。',inherits:false};
  h.system.ai=aiConfiguration({vision_config:oldVision});await h.run('showAiSettings()');
  selectPreset(h);assert.equal(h.node('visionEnabled').checked,false);assert.equal(h.node('visionModel').value,'qwen3.8-flash');
  assert.match(h.node('aiPresetHint').textContent,/沿用此模型.*保存后/);assert.equal(h.system.ai.vision_config.model,oldVision.model);
  assert.equal(h.calls.filter(call=>call.method!=='GET').length,0);
  h.node('aiSettingsDialog').close();await h.run('showAiSettings()');assert.equal(h.node('visionEnabled').checked,true);assert.equal(h.node('visionModel').value,oldVision.model);
  input(h,'visionModel','new-valid-vision-draft');selectPreset(h);assert.equal(h.node('visionEnabled').checked,true);assert.equal(h.node('visionModel').value,'new-valid-vision-draft');
  h.node('aiSettingsDialog').close();await h.run('showAiSettings()');selectPreset(h);
  h.intercept=call=>{if(call.url==='/api/ai/settings'&&call.method==='PUT'){
    assert.equal(call.body.vision_model,'');assert.equal(call.body.vision_provider,'');assert.equal(call.body.vision_clear_key,true);
    h.system.ai=aiConfiguration({provider:call.body.provider,base_url:call.body.base_url,model:call.body.model,key_configured:false,request_allowed:false});return h.response(h.system.ai);
  }};
  await h.run('saveAiSettings(false)');assert.equal(h.run('system.ai.vision_config.inherits'),true);
});

test('initial screen explains the next step and disables project actions',async t=>{
  const h=await setup(t);
  assert.equal(h.node('inspectorReviewTab').getAttribute('aria-selected'),'true');
  assert.equal(h.node('previewButton').disabled,true);assert.equal(h.node('renderButton').disabled,true);
  assert.equal(h.node('renderReason').hidden,true);
});

test('preview renders the viewed shot even when no 4K shots are selected',async t=>{
  const h=await setup(t);await h.run("openProject('a')");
  assert.equal(h.run('selectedShots.size'),1);
  h.node('selectAllShots').checked=false;h.node('selectAllShots').dispatchEvent(new h.w.Event('change'));
  assert.equal(h.node('previewButton').disabled,false);assert.equal(h.node('renderButton').disabled,true);
  await h.run("run('preview')");
  const job=h.calls.find(call=>call.url==='/api/projects/a/jobs');assert.deepEqual(job.body.shot_ids,['hero']);assert.equal(job.body.quality,'preview');
});

test('camera editing follows the visible shot and keeps 4K selection independent',async t=>{
  const h=await setup(t);await h.run("openProject('a')");
  h.node('cameraShot').value='campaign';h.node('cameraShot').dispatchEvent(new h.w.Event('change'));
  assert.equal(h.node('shotTitle').textContent,'棚拍宣传');assert.equal(h.run('activeShot'),'campaign');
  assert.deepEqual(Array.from(h.run('selectedShots')),['hero']);
  assert.equal(h.node('previewScope'),null);
});

test('switching projects lets the user stay or discard only the current draft',async t=>{
  const h=await setup(t);await h.run("openProject('a')");input(h,'margin','24');
  const stay=h.run("openProject('b')");await tick();
  h.node('unsavedDialog').querySelector('[data-choice="cancel"]').click();await stay;
  assert.equal(h.run('current.id'),'a');assert.equal(h.run('dirty'),true);assert.equal(h.node('margin').value,'24');
  const discard=h.run("openProject('b')");await tick();h.node('unsavedDialog').querySelector('[data-choice="discard"]').click();await discard;
  assert.equal(h.run('current.id'),'b');assert.equal(h.run('dirty'),false);
  assert.equal(h.calls.filter(call=>call.url.endsWith('/undo')).length,0);
  assert.equal(h.records.get('a').plan.margin,.12);
});

test('save-and-switch persists the draft with its base version before navigation',async t=>{
  const h=await setup(t);await h.run("openProject('a')");input(h,'margin','22');
  const switching=h.run("openProject('b')");await tick();h.node('unsavedDialog').querySelector('[data-choice="save"]').click();await switching;
  assert.equal(h.run('current.id'),'b');assert.equal(h.records.get('a').plan.margin,.22);
  const save=h.calls.find(call=>call.url==='/api/projects/a/plan');assert.equal(save.headers['X-Plan-Version'],'1');
});

test('new project dialog resolves unsaved changes instead of silently clearing them',async t=>{
  const h=await setup(t);await h.run("openProject('a')");input(h,'margin','20');
  const opening=h.run('showUpload()');await tick();assert.equal(h.node('unsavedDialog').open,true);assert.equal(h.node('uploadDialog').open,false);
  h.node('unsavedDialog').querySelector('[data-choice="cancel"]').click();await opening;
  assert.equal(h.run('dirty'),true);assert.equal(h.node('uploadDialog').open,false);
});

test('rapid project switching ignores a late response from an earlier click',async t=>{
  const h=await setup(t);const gate=deferred();
  h.intercept=call=>call.url==='/api/projects/a'?gate.promise:undefined;
  const first=h.run("openProject('a')");await tick();const second=h.run("openProject('b')");await second;
  gate.resolve(h.response(h.records.get('a')));await first;
  assert.equal(h.run('current.id'),'b');assert.equal(h.node('projectTitle').textContent,'测试项目 b');
});

test('duplicate saves share one request and lock the editing form',async t=>{
  const h=await setup(t);await h.run("openProject('a')");input(h,'margin','25');const gate=deferred();
  h.intercept=call=>call.url==='/api/projects/a/plan'?gate.promise:undefined;
  const first=h.run('savePlan(true)'),second=h.run('savePlan(true)');await tick();
  assert.equal(h.calls.filter(call=>call.url==='/api/projects/a/plan').length,1);assert.equal(h.node('settingsForm').inert,true);
  const result=copy(h.records.get('a'));result.version=2;result.plan.margin=.25;gate.resolve(h.response(result));await Promise.all([first,second]);
  assert.equal(h.run('dirty'),false);assert.equal(h.run('current.version'),2);
});

test('a stale polling response cannot overwrite a newly saved plan',async t=>{
  const h=await setup(t);await h.run("openProject('a')");const gate=deferred(),old=copy(h.records.get('a'));let once=true;
  h.intercept=call=>{if(call.url==='/api/projects/a'&&once){once=false;return gate.promise;}};
  const polling=h.run('poll()');await tick();input(h,'margin','23');await h.run('savePlan(true)');
  gate.resolve(h.response(old));await polling;
  assert.equal(h.run('current.version'),2);assert.equal(h.node('margin').value,'23');
});

test('background plan changes preserve the draft and reject overwriting a newer version',async t=>{
  const h=await setup(t);await h.run("openProject('a')");input(h,'margin','27');h.records.get('a').version=2;h.records.get('a').plan.margin=.15;
  await h.run('poll()');assert.equal(h.node('margin').value,'27');assert.equal(h.run('dirty'),true);
  await assert.rejects(h.run('savePlan(true)'),/后台更新/);
  assert.equal(h.calls.filter(call=>call.url==='/api/projects/a/plan').length,0);
  h.node('discardPlan').click();assert.equal(h.node('margin').value,'15');assert.equal(h.run('dirty'),false);
});

test('4K confirmation shows exact scope and cancel starts no render',async t=>{
  const h=await setup(t);await h.run("openProject('a')");h.node('selectAllShots').checked=true;h.node('selectAllShots').dispatchEvent(new h.w.Event('change'));
  const rendering=h.run("run('render')");await tick();
  assert.match(h.node('renderConfirmDialog').textContent,/3 张 标准 4K/);assert.match(h.node('renderConfirmDialog').textContent,/4096 × 4096/);
  h.node('renderConfirmDialog').querySelector('[data-choice="cancel"]').click();await rendering;
  assert.equal(h.calls.filter(call=>call.url.endsWith('/jobs')).length,0);
});

test('missing runtime blocks jobs, explains why, and leaves environment help reachable',async t=>{
  const h=await setup(t,{ready:false});await h.run("openProject('a')");
  assert.equal(h.node('previewButton').disabled,true);assert.equal(h.node('renderButton').disabled,true);
  assert.match(h.node('workflowDescription').textContent,/blender\/worker.py/);assert.equal(h.node('renderReason').hidden,true);assert.equal(h.node('runtimeHelp').hidden,false);
  await h.run("run('preview')");assert.equal(h.calls.filter(call=>call.url.endsWith('/jobs')).length,0);
  h.node('runtimeHelp').click();await tick();assert.equal(h.node('infoDialog').open,true);
});

test('upload preflight rejects missing models, multiple models, and oversized files',async t=>{
  const h=await setup(t);
  h.w.testFiles=[new h.w.File(['x'],'texture.png')];h.run('addFiles(testFiles)');assert.match(h.node('uploadError').textContent,/缺少模型/);
  h.w.testFiles=[new h.w.File(['x'],'part.obj')];h.run('addFiles(testFiles)');assert.equal(h.node('uploadSubmit').disabled,false);
  h.w.testFiles=[new h.w.File(['x'],'other.glb')];h.run('addFiles(testFiles)');assert.match(h.node('uploadError').textContent,/一个 GLB 或 OBJ/);
  h.run("uploadFiles=[{name:'big.glb',size:512*1024*1024}];addFiles([])");assert.match(h.node('uploadError').textContent,/512 MB/);assert.equal(h.node('uploadSubmit').disabled,true);
});

test('stable polling keeps focus on the selected shot checkbox',async t=>{
  const h=await setup(t);await h.run("openProject('a')");const checkbox=h.w.document.querySelector('[data-select-shot="hero"]');checkbox.focus();
  await h.run('poll()');assert.equal(h.w.document.activeElement,checkbox);
});

test('tabs support arrow keys and only the active tab is in the tab order',async t=>{
  const h=await setup(t);await h.run("openProject('a')");const tab=h.node('inspectorPlanTab');tab.focus();tab.dispatchEvent(new h.w.KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true}));
  const active=h.w.document.querySelector('[data-inspector][aria-selected="true"]');assert.equal(active.tabIndex,0);assert.equal(h.w.document.activeElement,active);
  const other=[...h.w.document.querySelectorAll('[data-inspector]')].filter(node=>node!==active);assert.ok(other.every(node=>node.tabIndex===-1));
});

test('confirmed 4K output submits only the checked shots at the chosen quality',async t=>{
  const h=await setup(t);await h.run("openProject('a')");h.run("selectedShots=new Set(['campaign','side']);renderProject()");
  h.node('quality').value='fine';const rendering=h.run("run('render')");await tick();h.node('renderConfirmDialog').querySelector('[data-choice="render"]').click();await rendering;
  const job=h.calls.find(call=>call.url==='/api/projects/a/jobs');assert.deepEqual(job.body.shot_ids,['campaign','side']);assert.equal(job.body.quality,'fine');
});

test('sidebar follows analysis cancellation without waiting for an output image',async t=>{
  const a=project('a');a.analysis=null;a.status='analyzing';const h=await setup(t,{projects:[a]});await h.run("openProject('a')");
  h.records.get('a').status='cancelled';h.records.get('a').jobs=[{id:'analysis-a',kind:'analyze',status:'cancelled',created_at:1,stage:'已取消'}];
  await h.run('poll()');assert.match(h.node('projectList').textContent,/已取消/);assert.match(h.node('workflowTitle').textContent,/已取消/);
});

test('diagnostic and material submission locks prevent parallel mutations',async t=>{
  const a=project('a');a.outputs=[{job_id:'render-a',shot_id:'hero',version:1,quality:'preview',width:512,height:512,png_url:'/qa/synthetic-preview.png'}];
  const h=await setup(t,{projects:[a]});await h.run("openProject('a')");
  h.run("feedbackMode=true;feedbackSource={projectId:current.id,key:sceneOutputKey(currentOutput),snapshot:feedbackSnapshot()};feedbackPoints=[{id:'p1',x:.5,y:.5,hit:true,object_name:'Body',material_name:'BodyMaterial',material_info:{color:'#ffffff',roughness:.4,metallic:0,transmission:0}}];system.ai_mode='ai';realismBusy=true;renderProject();");
  assert.equal(h.node('partMaterialSubmit').disabled,true);assert.equal(h.node('previewButton').disabled,true);
  const before=h.calls.length;await h.run('submitPartMaterial(false)');await h.run("run('preview')");assert.equal(h.calls.length,before);
  h.run('realismBusy=false;materialAgentSubmitting=true;renderProject()');
  assert.equal(h.node('previewButton').disabled,true);assert.equal(h.node('partMaterialSubmit').disabled,true);assert.equal(h.node('settingsForm').inert,true);
  await h.run("run('preview')");assert.equal(h.calls.length,before);
});

test('saving invalid advanced numbers opens the relevant panel and preserves the draft',async t=>{
  const h=await setup(t);await h.run("openProject('a')");input(h,'azimuth','500');
  await assert.rejects(h.run('savePlan(true)'),/无效数值/);
  assert.equal(h.node('cameraTab').hidden,false);assert.equal(h.node('azimuth').closest('details').open,true);assert.equal(h.run('dirty'),true);
  assert.equal(h.calls.filter(call=>call.url.endsWith('/plan')).length,0);
});

test('runtime recovery restores upload and preview without reloading the page',async t=>{
  const h=await setup(t,{ready:false});await h.run("openProject('a')");assert.equal(h.node('previewButton').disabled,true);
  h.system=environment(true);await h.run('poll()');assert.equal(h.node('previewButton').disabled,false);assert.equal(h.node('runtimeHelp').hidden,true);
});

function previewProject(id='a') {
  const p=project(id);p.outputs=[{job_id:'render-'+id,shot_id:'hero',version:1,quality:'preview',width:512,height:512,png_url:'/qa/synthetic-preview-'+id+'.png'}];return p;
}
test('thumbnail shows current shot without becoming a full image, diagnostic source or export',async t=>{
  const p=previewProject();p.version=2;
  p.outputs.push({job_id:'full-old',shot_id:'campaign',version:1,quality:'preview',width:512,height:512,png_url:'/qa/old-full.png'},
    {job_id:'small',shot_id:'campaign',version:2,quality:'thumbnail',width:256,height:256,png_url:'/qa/small.png'});
  const h=await setup(t,{projects:[p]});await h.run("openProject('a')");
  assert.equal(h.run("latestOutput('campaign').job_id"),'full-old');
  assert.equal(h.run("displayedShotOutput('campaign').job_id"),'small');
  h.node('shotList').querySelector('[data-shot="campaign"]').click();
  assert.equal(h.node('renderImage').getAttribute('src'),'/qa/small.png');
  assert.equal(h.run('currentOutput'),null);assert.equal(h.node('feedbackToggle').disabled,true);
  assert.equal(h.node('imageDownloads').hidden,true);assert.match(h.node('renderKind').textContent,/缩略图/);
  h.run("current.outputs.push({job_id:'full-new',shot_id:'campaign',version:2,quality:'preview',width:512,height:512,png_url:'/qa/full-new.png',created_at:0});renderProject()");
  assert.equal(h.node('renderImage').getAttribute('src'),'/qa/full-new.png','normal preview wins at the same version');
});
test('thumbnail scheduling resumes only preempted work and never blocks normal tasks',async t=>{
  const p=previewProject(),h=await setup(t,{projects:[p]});h.run('system.runtime.capabilities.thumbnails=true');
  let count=0;h.intercept=call=>{if(call.url.endsWith('/thumbnails')){count++;return h.response({job:{id:'thumb-'+count}},202);}};
  await h.run("openProject('a')");await tick();assert.equal(count,1);
  let jobs=h.records.get('a').jobs;jobs.unshift({id:'thumb-1',origin:'auto_thumbnails',kind:'preview',quality:'thumbnail',status:'running',version:1,shot_ids:['campaign','side'],completed:[],created_at:1});
  await h.run('poll()');assert.equal(h.run('activeJob()'),undefined);assert.match(h.node('shotList').textContent,/缩略图生成中/);
  jobs[0].status='cancelled';jobs[0].yielded=true;h.run("thumbnailAttempts.get('a:1').lastAt=0");
  await h.run('poll()');await tick();assert.equal(count,2);
  await h.run('poll()');await tick();assert.equal(count,2,'same yielded task must not be submitted twice');
  jobs.unshift({id:'thumb-2',origin:'auto_thumbnails',status:'cancelled',version:1,created_at:2});
  h.run("thumbnailAttempts.get('a:1').lastAt=0");await h.run('poll()');await tick();assert.equal(count,2,'manual cancellation must not restart automatically');
});
test('thumbnail scheduling retries a temporary submission error without an unbounded loop',async t=>{
  const h=await setup(t,{projects:[previewProject()]});h.run('system.runtime.capabilities.thumbnails=true');let count=0;
  h.intercept=call=>{if(call.url.endsWith('/thumbnails')){count++;return count===1?h.response({error:'暂不可用'},503):h.response({job:null});}};
  await h.run("openProject('a')");await tick();assert.equal(count,1);
  h.run("thumbnailAttempts.get('a:1').lastAt=0");await h.run('poll()');await tick();assert.equal(count,2);
});
function failFirstPreview(h) {
  let failed=false;
  h.intercept=call=>{if(call.url.endsWith('/jobs')&&!failed){failed=true;return h.response({error:'测试预览启动失败'},503);}};
}

const responseContractText='上表面是反光的拉丝不锈钢，箱体是红色金属有光泽';
const responseContractMessage='本次未应用：修改结果未通过校验。原要求已保留。';
function responseContractFailure(h,status=422,extra={}) {
  return h.response({error:'模型内部 decision_conflict：请明确调整目标。',error_code:'instruction_response_invalid',retryable:true,applied:false,...extra},status);
}

test('a rejected model response preserves the exact request and offers regeneration in the existing form',async t=>{
  const h=await setup(t,{projects:[previewProject()]});await h.run("openProject('a')");const before=copy(h.records.get('a'));
  input(h,'instructionInput',responseContractText);h.intercept=call=>call.url.endsWith('/instruct')?responseContractFailure(h):undefined;
  await h.run('applyInstructionAndPreview({preventDefault(){}})');await h.run('poll()');
  assert.deepEqual(h.records.get('a'),before);assert.equal(h.node('instructionInput').value,responseContractText);
  assert.equal(h.node('instructionError').textContent,responseContractMessage);assert.equal(h.node('instructionError').hidden,false);
  assert.equal(h.node('instructionSubmit').textContent,'重试调整');assert.equal(h.node('instructionSubmit').disabled,false);
  assert.equal(h.node('instructionProposal').hidden,true);assert.equal(h.node('instructionLocate').hidden,true);assert.equal(h.node('instructionReceipt').hidden,true);
  assert.equal(h.run('currentInstructionAttempt()'),null);assert.equal(h.w.sessionStorage.getItem('wuying-instruction-attempts-v1'),null);
  assert.equal(h.calls.filter(call=>call.url.endsWith('/instruct')).length,1);assert.equal(h.calls.filter(call=>call.url.endsWith('/jobs')).length,0);
});

test('a rejected instruction shows a curated reason with collapsed field and diagnostic details',async t=>{
  const h=await setup(t,{projects:[previewProject()]});await h.run("openProject('a')");
  const text='柜体光泽不够，灯光再亮点',diagnosticId='a'.repeat(32);input(h,'instructionInput',text);
  h.intercept=call=>call.url.endsWith('/instruct')?responseContractFailure(h,422,{response_issue:{kind:'material',field:'roughness',reason:'private model text'},diagnostic_id:diagnosticId}):undefined;
  await h.run('applyInstructionAndPreview({preventDefault(){}})');
  assert.equal(h.node('instructionError').textContent,'本次未应用：材质参数与要求不一致。原要求已保留。');
  assert.equal(h.node('instructionErrorDisclosure').hidden,false);assert.equal(h.node('instructionErrorDisclosure').open,false);
  assert.match(h.node('instructionErrorDetails').textContent,/校验项目：粗糙度/);assert.match(h.node('instructionErrorDetails').textContent,new RegExp(diagnosticId));
  assert.doesNotMatch(h.node('instructionForm').textContent,/private model|decision_conflict|请明确调整目标/);
  assert.equal(h.node('instructionInput').value,text);assert.notEqual(h.node('instructionInput').getAttribute('aria-invalid'),'true');
  h.node('instructionErrorDisclosure').open=true;await h.run('poll()');assert.equal(h.node('instructionErrorDisclosure').open,true);
  input(h,'instructionInput','柜体光泽再明显一点');assert.equal(h.node('instructionErrorDisclosure').hidden,true);assert.equal(h.node('instructionErrorDetails').textContent,'');
  assert.equal(h.calls.filter(call=>call.url.endsWith('/jobs')).length,0);
});

test('a scope-mapping failure reports the system mismatch without asking to rewrite or pick a part',async t=>{
  const h=await setup(t,{projects:[previewProject()]});await h.run("openProject('a')");input(h,'instructionInput','柜体光泽不够，灯光再亮点');
  h.intercept=call=>call.url.endsWith('/instruct')?responseContractFailure(h,422,{response_issue:{kind:'scope'}}):undefined;
  await h.run('applyInstructionAndPreview({preventDefault(){}})');
  assert.equal(h.node('instructionError').textContent,'本次未应用：要求与修改范围未能对应。原要求已保留。');
  assert.equal(h.node('instructionSubmit').textContent,'重试调整');assert.equal(h.node('instructionLocate').hidden,true);
  assert.equal(h.node('instructionErrorDisclosure').hidden,true);assert.equal(h.node('instructionInput').value,'柜体光泽不够，灯光再亮点');
});

test('malformed error detail values and upstream text stay hidden during safe retry',async t=>{
  const h=await setup(t,{projects:[previewProject()]});await h.run("openProject('a')");input(h,'instructionInput',responseContractText);
  h.intercept=call=>call.url.endsWith('/instruct')?responseContractFailure(h,422,{response_issue:{kind:'__proto__',field:'<img src=x onerror=private()>'},diagnostic_id:'../private.json'}):undefined;
  await h.run('applyInstructionAndPreview({preventDefault(){}})');
  assert.equal(h.node('instructionError').textContent,responseContractMessage);assert.equal(h.node('instructionErrorDisclosure').hidden,true);
  assert.equal(h.node('instructionErrorDetails').textContent,'');assert.equal(h.node('instructionForm').querySelector('img'),null);
  assert.equal(h.node('instructionSubmit').textContent,'重试调整');
});

test('regenerating a definitely rejected instruction uses a fresh key and commits only the successful attempt',async t=>{
  const h=await setup(t,{projects:[previewProject()]});await h.run("openProject('a')");input(h,'instructionInput',responseContractText);
  let fail=true;h.intercept=call=>call.url.endsWith('/instruct') && fail?responseContractFailure(h):undefined;
  await h.run('applyInstructionAndPreview({preventDefault(){}})');fail=false;await h.run('applyInstructionAndPreview({preventDefault(){}})');
  const calls=h.calls.filter(call=>call.url.endsWith('/instruct'));
  assert.equal(calls.length,2);assert.deepEqual(calls[0].body,calls[1].body);assert.notEqual(calls[0].headers['Idempotency-Key'],calls[1].headers['Idempotency-Key']);
  assert.equal(h.records.get('a').version,2);assert.equal(h.records.get('a').plan.exposure,.5);assert.equal(h.records.get('a').history.length,1);
  assert.equal(h.calls.filter(call=>call.url.endsWith('/jobs')).length,1);assert.equal(h.node('instructionInput').value,'');assert.equal(h.node('instructionError').hidden,true);
});

test('an explicit precommit rejection stays retryable even with a server status while unknown saves reuse the original key',async t=>{
  for(const applied of [false,undefined]){
    const h=await setup(t,{projects:[previewProject()]});await h.run("openProject('a')");input(h,'instructionInput','亮一点');
    let fail=true;h.intercept=call=>call.url.endsWith('/instruct') && fail?responseContractFailure(h,503,{applied}):undefined;
    await h.run('applyInstructionAndPreview({preventDefault(){}})');assert.equal(h.node('instructionSubmit').textContent,applied===false?'重试调整':'核对并继续');
    fail=false;await h.run('applyInstructionAndPreview({preventDefault(){}})');const calls=h.calls.filter(call=>call.url.endsWith('/instruct'));
    assert.equal(calls.length,2);assert.deepEqual(calls[0].body,calls[1].body);
    assert.equal(calls[0].headers['Idempotency-Key']===calls[1].headers['Idempotency-Key'],applied!==false);
    assert.equal(h.records.get('a').plan.exposure,.5);assert.equal(h.records.get('a').history.length,1);
  }
});

test('a model contract failure replaces old target guidance and never invents a point-picking action',async t=>{
  const h=await setup(t,{projects:[previewProject()]});await h.run("openProject('a')");input(h,'instructionInput',responseContractText);
  h.intercept=call=>call.url.endsWith('/instruct')?h.response({error:'请核对部位。',error_code:'instruction_targets_unresolved',pending:[{label:'上表面',quote:responseContractText,action:'locate_part'}]},400):undefined;
  await h.run('applyInstructionAndPreview({preventDefault(){}})');assert.equal(h.node('instructionLocate').hidden,false);
  h.intercept=call=>call.url.endsWith('/instruct')?responseContractFailure(h,422,{pending:[{label:'上表面',action:'locate_part'}],remaining_text:'错误残留'}):undefined;
  await h.run('applyInstructionAndPreview({preventDefault(){}})');assert.equal(h.node('instructionProposal').hidden,true);assert.equal(h.node('instructionLocate').hidden,true);
  h.node('instructionLocate').click();assert.equal(h.run('instructionTargetSelection'),null);assert.equal(h.run('feedbackMode'),false);
  assert.equal(h.node('instructionInput').value,responseContractText);assert.equal(h.node('instructionError').textContent,responseContractMessage);
});

test('valid target examples survive a protocol rejection and are reused only with the same source and request',async t=>{
  const h=await setup(t,{projects:[previewProject()]});await h.run("openProject('a')");await prepareInstructionTargetRecovery(h);await h.run('addFeedbackPoint({x:.23,y:.85})');
  const prior=h.intercept;let fail=true;h.intercept=call=>call.url.endsWith('/instruct') && call.body.target_points && fail?responseContractFailure(h):prior(call);
  await h.run('continueInstructionTargets()');assert.equal(h.run('feedbackPoints.length'),1);assert.equal(h.node('instructionSubmit').textContent,'重试调整');assert.equal(h.node('instructionLocate').hidden,true);
  const selection=h.run('JSON.stringify(instructionTargetSelection)');assert.ok(selection);assert.equal(h.node('instructionInput').value,'四个轮子都要是黑色橡胶');
  fail=false;await h.run('applyInstructionAndPreview({preventDefault(){}})');const calls=h.calls.filter(call=>call.url.endsWith('/instruct') && call.body.target_points);
  assert.equal(calls.length,2);assert.deepEqual(calls[0].body,calls[1].body);assert.notEqual(calls[0].headers['Idempotency-Key'],calls[1].headers['Idempotency-Key']);
  assert.equal(h.calls.filter(call=>call.url.endsWith('/feedback/pick')).length,1);assert.equal(h.records.get('a').version,2);assert.equal(h.run('instructionTargetSelection'),null);
});

test('editing the text, source, version, camera or draft invalidates a model-response retry state',async t=>{
  for(const change of ['text','image','version','shot','draft']){
    const h=await setup(t,{projects:[previewProject()]});await h.run("openProject('a')");input(h,'instructionInput',responseContractText);
    h.intercept=call=>call.url.endsWith('/instruct')?responseContractFailure(h):undefined;await h.run('applyInstructionAndPreview({preventDefault(){}})');
    if(change==='text')input(h,'instructionInput','箱体改成灰色金属');
    if(change==='image')h.run("current.outputs[0].job_id='replacement-preview';renderProject()");
    if(change==='version')h.run('current.version++;renderProject()');
    if(change==='shot')h.run("activeShot='side';renderProject()");
    if(change==='draft')input(h,'margin','24');
    assert.equal(h.run('instructionGuidance.has(current.id)'),false,change);assert.equal(h.node('instructionError').hidden,true,change);
    assert.equal(h.node('instructionSubmit').textContent,'应用并预览',change);assert.equal(h.node('instructionProposal').hidden,true,change);
    assert.equal(h.calls.filter(call=>call.url.endsWith('/instruct')).length,1,change);
  }
});

test('project navigation restores a valid rejection without changing the request or starting work',async t=>{
  const h=await setup(t,{projects:[previewProject(),project('b')]});await h.run("openProject('a')");input(h,'instructionInput',responseContractText);
  h.intercept=call=>call.url.endsWith('/instruct')?responseContractFailure(h):undefined;await h.run('applyInstructionAndPreview({preventDefault(){}})');
  await h.run("openProject('b')");assert.equal(h.node('instructionError').hidden,true);await h.run("openProject('a')");
  assert.equal(h.node('instructionInput').value,responseContractText);assert.equal(h.node('instructionSubmit').textContent,'重试调整');assert.equal(h.node('instructionError').textContent,responseContractMessage);
  assert.equal(h.calls.filter(call=>call.url.endsWith('/instruct')).length,1);assert.equal(h.calls.filter(call=>call.url.endsWith('/jobs')).length,0);
});

test('confirmed modifications always retry preview only even when a preview failure uses the response-error code',async t=>{
  const h=await setup(t);await h.run("openProject('a')");input(h,'instructionInput','亮一点');let fail=true;
  h.intercept=call=>call.url.endsWith('/jobs') && fail?responseContractFailure(h):undefined;await h.run('applyInstructionAndPreview({preventDefault(){}})');
  assert.equal(h.node('instructionSubmit').textContent,'重试预览');assert.match(h.node('instructionError').textContent,/调整已保存，预览未启动/);
  fail=false;await h.run('applyInstructionAndPreview({preventDefault(){}})');
  assert.equal(h.calls.filter(call=>call.url.endsWith('/instruct')).length,1);const jobs=h.calls.filter(call=>call.url.endsWith('/jobs'));
  assert.equal(jobs.length,2);assert.equal(jobs[0].headers['Idempotency-Key'],jobs[1].headers['Idempotency-Key']);assert.equal(h.records.get('a').plan.exposure,.5);assert.equal(h.records.get('a').history.length,1);
});

test('a rejection of remaining requirements never restores an already-applied relative instruction',async t=>{
  const h=await setup(t,{projects:[previewProject()]});installPartialFlow(h);await h.run("openProject('a')");await proposePartial(h);await h.run('applyInstructionAndPreview({preventDefault(){}})');
  h.records.get('a').jobs=[];h.records.get('a').outputs=[{...h.records.get('a').outputs[0],version:2,job_id:'fresh-after-partial'}];await h.run('poll()');assert.equal(h.node('instructionInput').value,remainingInstructionText);
  h.intercept=call=>call.url.endsWith('/instruct')?responseContractFailure(h):undefined;await h.run('applyInstructionAndPreview({preventDefault(){}})');
  assert.equal(h.node('instructionInput').value,remainingInstructionText);assert.equal(h.node('instructionSubmit').textContent,'重试调整');assert.equal(h.node('instructionProposal').hidden,true);
  await h.run('applyInstructionAndPreview({preventDefault(){}})');const calls=h.calls.filter(call=>call.url.endsWith('/instruct'));
  assert.equal(calls.length,3);assert.equal(calls[0].body.text,partialInstructionText);assert.equal(calls[1].body.text,remainingInstructionText);assert.equal(calls[2].body.text,remainingInstructionText);
  assert.notEqual(calls[1].headers['Idempotency-Key'],calls[2].headers['Idempotency-Key']);assert.equal(h.records.get('a').plan.exposure,.5);assert.equal(h.records.get('a').version,2);assert.equal(h.records.get('a').history.length,1);
  assert.equal(h.calls.filter(call=>call.url.endsWith('/instruct/confirm')).length,1);assert.equal(h.calls.filter(call=>call.url.endsWith('/jobs')).length,1);
});

test('the workspace has one global command form and no duplicate diagnostic input',async t=>{
  const h=await setup(t);
  assert.equal(h.node('instructionForm').closest('[role="tabpanel"]').id,'inspectorReview');
  assert.equal(h.node('instructionSubmit').textContent,'应用并预览');
  for(const id of ['instructionChips','instructionMode','realismQuestion','realismAskForm','emptyPreview','renderSelection','previewScope','imageStamp','visionModelStatus','reviewRealismSummary'])assert.equal(h.node(id),null,`${id} should be removed`);
  assert.equal(h.w.document.querySelector('.workflow'),null);
  assert.equal(h.node('reviewRefreshButton').hidden,true);
  assert.equal(h.w.document.body.classList.contains('has-project'),false);
  await h.run("openProject('a')");assert.equal(h.w.document.body.classList.contains('has-project'),true);
  assert.equal(h.node('workspaceHint').hidden,true);assert.equal(h.node('planState').textContent,'已保存');
});

test('applying a command saves parameters, queues one current-shot preview, then clears the text',async t=>{
  const h=await setup(t);await h.run("openProject('a')");input(h,'instructionInput','亮一点');
  await h.run('applyInstructionAndPreview({preventDefault(){}})');
  const actions=h.calls.filter(call=>call.method==='POST');assert.deepEqual(actions.map(call=>call.url),['/api/projects/a/instruct','/api/projects/a/jobs']);
  assert.equal(actions[0].body.text,'亮一点');assert.deepEqual(actions[1].body.shot_ids,['hero']);
  assert.equal(h.records.get('a').plan.exposure,.5);assert.equal(h.node('instructionInput').value,'');assert.equal(h.node('instructionError').hidden,true);
});

test('a rejected instruction preserves the text and never starts a preview',async t=>{
  const h=await setup(t);await h.run("openProject('a')");input(h,'instructionInput','亮一点');
  h.intercept=call=>call.url.endsWith('/instruct')?h.response({error:'测试指令未识别'},400):undefined;
  await h.run('applyInstructionAndPreview({preventDefault(){}})');
  assert.equal(h.node('instructionInput').value,'亮一点');assert.equal(h.node('instructionError').hidden,false);assert.match(h.node('instructionError').textContent,/未识别/);
  assert.equal(h.calls.filter(call=>call.url.endsWith('/jobs')).length,0);assert.equal(h.records.get('a').version,1);
});

test('retrying a failed preview does not apply the same relative command twice',async t=>{
  const h=await setup(t);await h.run("openProject('a')");input(h,'instructionInput','亮一点');failFirstPreview(h);
  await h.run('applyInstructionAndPreview({preventDefault(){}})');
  assert.equal(h.records.get('a').version,2);assert.equal(h.records.get('a').plan.exposure,.5);assert.equal(h.node('instructionInput').value,'亮一点');assert.equal(h.node('instructionSubmit').textContent,'重试预览');
  await h.run('applyInstructionAndPreview({preventDefault(){}})');
  assert.equal(h.calls.filter(call=>call.url.endsWith('/instruct')).length,1);assert.equal(h.records.get('a').plan.exposure,.5);
  const jobs=h.calls.filter(call=>call.url.endsWith('/jobs'));assert.equal(jobs.length,2);assert.equal(jobs[0].headers['Idempotency-Key'],jobs[1].headers['Idempotency-Key']);
  assert.equal(h.node('instructionInput').value,'');assert.equal(h.run('pendingInstructionPreview'),null);
});

test('changing the requested command invalidates preview-only retry and keeps the new text',async t=>{
  const h=await setup(t);await h.run("openProject('a')");input(h,'instructionInput','亮一点');failFirstPreview(h);await h.run('applyInstructionAndPreview({preventDefault(){}})');
  input(h,'instructionInput','增加留白');assert.equal(h.node('instructionInput').value,'增加留白');assert.equal(h.node('instructionSubmit').textContent,'应用并预览');
  await h.run('applyInstructionAndPreview({preventDefault(){}})');assert.equal(h.calls.filter(call=>call.url.endsWith('/instruct')).length,2);
});

test('switching projects cannot turn an already-applied failed-preview command into a fresh command',async t=>{
  const h=await setup(t);await h.run("openProject('a')");input(h,'instructionInput','亮一点');failFirstPreview(h);await h.run('applyInstructionAndPreview({preventDefault(){}})');
  await h.run("openProject('b')");assert.equal(h.node('instructionInput').value,'');assert.equal(h.run('pendingInstructionPreview'),null);
  await h.run("openProject('a')");assert.equal(h.node('instructionInput').value,'');await h.run('applyInstructionAndPreview({preventDefault(){}})');
  assert.equal(h.calls.filter(call=>call.url.endsWith('/instruct')).length,1);assert.equal(h.records.get('a').plan.exposure,.5);
});

test('a newer saved version clears an already-applied retry command instead of reapplying it',async t=>{
  const h=await setup(t);await h.run("openProject('a')");input(h,'instructionInput','亮一点');failFirstPreview(h);await h.run('applyInstructionAndPreview({preventDefault(){}})');
  h.records.get('a').version=3;h.records.get('a').plan.margin=.2;await h.run('poll()');
  assert.equal(h.run('pendingInstructionPreview'),null);assert.equal(h.node('instructionInput').value,'');
  await h.run('applyInstructionAndPreview({preventDefault(){}})');assert.equal(h.calls.filter(call=>call.url.endsWith('/instruct')).length,1);
});

test('rechecking the displayed image explicitly requests diagnostic-only and preserves the plan',async t=>{
  const p=previewProject(),h=await setup(t,{projects:[p]});await h.run("openProject('a')");const before=copy(h.records.get('a').plan);
  await h.run('requestRealismReview({preventDefault(){}})');
  const actions=h.calls.filter(call=>call.method==='POST');assert.deepEqual(actions.map(call=>call.url),['/api/projects/a/review']);assert.equal(actions[0].body.diagnostic_only,true);
  assert.equal(actions[0].body.output_id,'render-a');assert.equal('question' in actions[0].body,false);
  assert.deepEqual(h.records.get('a').plan,before);assert.equal(h.records.get('a').version,1);assert.equal(h.records.get('a').jobs.length,0);
  assert.equal(h.node('reviewSummary').textContent,'测试诊断结论');assert.equal(h.node('reviewRefreshButton').textContent,'重新检查');
});

test('read-only diagnosis preserves a local unsaved parameter draft',async t=>{
  const h=await setup(t,{projects:[previewProject()]});await h.run("openProject('a')");input(h,'margin','29');
  assert.equal(h.node('reviewRefreshButton').disabled,false);await h.run('requestRealismReview({preventDefault(){}})');
  assert.equal(h.run('dirty'),true);assert.equal(h.node('margin').value,'29');assert.equal(h.records.get('a').plan.margin,.12);
  assert.equal(h.calls.filter(call=>call.url.endsWith('/plan')||call.url.endsWith('/jobs')).length,0);
});

test('a late polling response cannot erase the new diagnostic report even without a version change',async t=>{
  const h=await setup(t,{projects:[previewProject()]});await h.run("openProject('a')");const old=copy(h.records.get('a')),gate=deferred();let once=true;
  h.intercept=call=>{if(call.url==='/api/projects/a'&&once){once=false;return gate.promise;}};
  const polling=h.run('poll()');await tick();await h.run('requestRealismReview({preventDefault(){}})');gate.resolve(h.response(old));await polling;
  assert.equal(h.node('reviewSummary').textContent,'测试诊断结论');assert.equal(h.run('current.version'),1);
});

test('historical comparison is read-only for global commands but can diagnose that displayed image',async t=>{
  const p=previewProject();p.version=2;p.outputs.push({...p.outputs[0],job_id:'render-a-new',version:2,png_url:'/qa/synthetic-new.png'});
  const h=await setup(t,{projects:[p]});await h.run("openProject('a')");input(h,'instructionInput','亮一点');h.node('comparePreview').click();
  assert.equal(h.run('comparePrevious'),true);assert.equal(h.node('instructionInput').disabled,true);assert.equal(h.node('instructionSubmit').disabled,true);
  await h.run('applyInstructionAndPreview({preventDefault(){}})');assert.equal(h.calls.filter(call=>call.url.endsWith('/instruct')).length,0);
  await h.run('requestRealismReview({preventDefault(){}})');const review=h.calls.find(call=>call.url.endsWith('/review'));assert.equal(review.body.version,1);assert.equal(review.body.diagnostic_only,true);
});

test('automatic optimization records stay contextual and selected materials have one manual editor',async t=>{
  const h=await setup(t,{projects:[previewProject()]});await h.run("openProject('a')");
  assert.equal(h.node('autoMaterialStatus').hidden,true);assert.equal(h.node('partLanguageTab'),null);
  assert.equal(h.node('feedbackForm'),null);assert.equal(h.node('feedbackPanel').querySelector('textarea'),null);
  assert.equal(h.run('typeof submitFeedback'),'undefined');
  h.run("current.auto_material={status:'running',stage:'查找资源'};renderProject()");assert.equal(h.node('autoMaterialStatus').hidden,false);assert.equal(h.node('autoMaterialStatus').open,true);
  h.run("current.auto_material.status='completed';renderProject()");assert.equal(h.node('autoMaterialStatus').open,false);
  h.run("feedbackPoints=[{id:'p1',x:.5,y:.5,hit:true,object_name:'Body',material_name:'BodyMaterial',material_info:{color:'#ffffff'}}];renderPartMaterial()");
  assert.equal(h.node('partMaterialPanel').hidden,false);assert.match(h.node('partMaterialInfo').textContent,/已选 1 个/);
  assert.match(h.node('materialIntro').textContent,/点击部件/);
});

test('selected material parameters still submit an explicit local patch without a language request',async t=>{
  const h=await setup(t,{projects:[previewProject()]});await h.run("openProject('a')");
  h.run("feedbackMode=true;feedbackSource={projectId:current.id,key:sceneOutputKey(currentOutput),snapshot:feedbackSnapshot()};feedbackPoints=[{id:'surface-1',x:.25,y:.7,hit:true,object_name:'Body',material_name:'BodyMaterial',material_info:{color:'#ffffff',roughness:.4,metallic:0,transmission:0}}];setInspectorTab('material');renderProject()");
  h.intercept=call=>{if(call.url.endsWith('/materials/selection/apply')){
    const p=h.records.get('a');p.version++;p.plan.object_material_overrides=[{object_name:'Body',material_name:'BodyMaterial',...call.body.patch}];return h.response(p);
  }};
  input(h,'partRoughness','.27');assert.equal(h.node('partRoughnessUse').checked,true);assert.equal(h.node('partMaterialSubmit').disabled,false);
  h.node('partMaterialForm').dispatchEvent(new h.w.Event('submit',{bubbles:true,cancelable:true}));await tick();
  const writes=h.calls.filter(call=>call.method==='POST');assert.equal(writes.length,1);
  assert.equal(writes[0].url,'/api/projects/a/materials/selection/apply');assert.deepEqual(writes[0].body.patch,{roughness:.27});
  assert.deepEqual(writes[0].body.points,[{id:'surface-1',x:.25,y:.7}]);assert.equal(writes[0].body.text,undefined);
  assert.equal(h.records.get('a').version,2);assert.equal(h.run('feedbackPoints.length'),0);
});

test('language edits use the main instruction flow without reinterpreting manual selected-point scope',async t=>{
  const h=await setup(t,{projects:[previewProject()]});await h.run("openProject('a')");
  h.run("feedbackMode=true;feedbackSource={projectId:current.id,key:sceneOutputKey(currentOutput),snapshot:feedbackSnapshot()};feedbackPoints=[{id:'surface-1',x:.25,y:.7,hit:true,object_name:'Body',material_name:'BodyMaterial',material_info:{color:'#ffffff',roughness:.4}}];setInspectorTab('material');renderProject()");
  h.node('inspectorReviewTab').click();input(h,'instructionInput','让正脸更通透一些');
  await h.run('applyInstructionAndPreview({preventDefault(){}})');
  const requests=h.calls.filter(call=>call.url.endsWith('/instruct'));assert.equal(requests.length,1);
  assert.equal(requests[0].body.text,'让正脸更通透一些');assert.equal(requests[0].body.protocol,undefined,'use the production semantic default');
  assert.equal(requests[0].body.target_points,undefined,'manual local selections do not authorize a larger target group');
  assert.equal(h.calls.filter(call=>/\/feedback$|\/materials\/selection\/apply$/.test(call.url)).length,0);
  assert.equal(h.calls.filter(call=>call.url.endsWith('/jobs')).length,1);
  assert.equal(h.run('instructionTargetSelection'),null);assert.equal(h.node('instructionInput').value,'');
});

test('existing Blender download follows the selected historical snapshot even when new export is unavailable',async t=>{
  const p=previewProject();p.outputs[0].blend_url='/files/a/v1/scene.blend';p.version=2;
  p.outputs.push({...p.outputs[0],job_id:'render-a-new',version:2,png_url:'/qa/synthetic-new.png',blend_url:'/files/a/v2/scene.blend'});
  const h=await setup(t,{ready:false,projects:[p]});await h.run("openProject('a')");await h.run('showSceneExport()');
  assert.equal(h.node('downloadBlend').closest('dialog').id,'sceneExportDialog');assert.equal(h.node('sceneExportSubmit').disabled,true);
  assert.equal(h.node('downloadBlend').hidden,false);assert.equal(h.node('downloadBlend').getAttribute('href'),'/files/a/v2/scene.blend?download=1');
  h.node('sceneExportSource').value=h.run('sceneOutputKey(current.outputs[0])');h.node('sceneExportSource').dispatchEvent(new h.w.Event('change'));
  assert.equal(h.node('downloadBlend').getAttribute('href'),'/files/a/v1/scene.blend?download=1');
  h.run('delete sceneExportSource.blend_url;renderSceneExportControls()');assert.equal(h.node('downloadBlend').hidden,true);assert.equal(h.node('downloadBlend').hasAttribute('href'),false);
});

function nativeSnapshot(overrides={}) {
  return {job_id:'native-new',shot_id:'hero',version:2,quality:'preview',width:512,height:512,created_at:30,
    png_url:'/files/a/runs/native-new/hero.png',blend_url:'/files/a/runs/native-new/hero.blend',blend_sha256:'a'.repeat(64),...overrides};
}
function nativeExport(source,format='c4d',overrides={}) {
  return {id:format+'-complete',project_id:'a',format,status:'completed',created_at:40,
    output_id:source.job_id,shot_id:source.shot_id,version:source.version,quality:source.quality,
    source_png_url:source.png_url,source_blend_url:source.blend_url,source_sha256:source.blend_sha256 || 'a'.repeat(64),
    native_url:'/files/a/exports/'+format+'-complete/scene.'+format,download_url:'/files/a/exports/'+format+'-complete/project.zip',...overrides};
}
function enableNativeC4d(h) {
  Object.assign(h.system.scene_exports,{export_available:true,c4d_export_available:true,c4d_executable_found:true,c4d_status:'ready'});
  h.system.runtime.capabilities.export_c4d=true;
  h.run('system='+JSON.stringify(h.system)+';renderProject()');
}

test('latest native downloads follow the newest rendered shot even while comparing an older image',async t=>{
  const p=project(),latest=nativeSnapshot(),previous=nativeSnapshot({job_id:'native-before',created_at:20,png_url:'/files/a/runs/native-before/hero.png',blend_url:'/files/a/runs/native-before/hero.blend'});
  const side=nativeSnapshot({job_id:'native-side',shot_id:'side',png_url:'/files/a/runs/native-side/side.png',blend_url:'/files/a/runs/native-side/side.blend'});
  p.version=3;p.outputs=[previous,side,latest,nativeSnapshot({job_id:'old-v1',version:1,created_at:99,blend_url:'/files/a/runs/old-v1/hero.blend'})];
  p.jobs=[{id:latest.job_id,status:'completed',automatic_previous_preview:copy(previous)}];
  const h=await setup(t,{projects:[p]});enableNativeC4d(h);await h.run("openProject('a')");
  assert.equal(h.node('downloadLatestBlend').getAttribute('href'),latest.blend_url+'?download=1');
  assert.match(h.node('latestSceneSource').textContent,/白底主图.*v2.*最新已渲染/);assert.match(h.node('latestSceneStatus').textContent,/尚未出图/);
  h.node('comparePreview').click();assert.equal(h.node('renderImage').getAttribute('src'),previous.png_url);
  assert.equal(h.node('downloadLatestBlend').getAttribute('href'),latest.blend_url+'?download=1','comparison must not change the latest scene download');
  h.node('cameraShot').value='side';h.node('cameraShot').dispatchEvent(new h.w.Event('change'));
  assert.equal(h.node('downloadLatestBlend').getAttribute('href'),side.blend_url+'?download=1');assert.match(h.node('latestSceneSource').textContent,/侧面结构/);
  h.node('cameraShot').value='campaign';h.node('cameraShot').dispatchEvent(new h.w.Event('change'));
  assert.equal(h.node('latestSceneDownloads').hidden,true);assert.equal(h.node('downloadLatestBlend').hasAttribute('href'),false);assert.equal(h.node('downloadLatestC4d').hasAttribute('href'),false);
  assert.equal(h.calls.filter(call=>call.method!=='GET').length,0,'direct files do not require a new render, save, export, or archive request');
});

test('native downloads reuse only completed files matching all snapshot source fields and the available hash',async t=>{
  const p=project(),source=nativeSnapshot();p.version=2;p.outputs=[source];
  const invalid=[
    {id:'wrong-job',output_id:'another-job'},{id:'wrong-shot',shot_id:'side'},{id:'wrong-version',version:1},{id:'wrong-quality',quality:'fine'},
    {id:'wrong-project',project_id:'b'},{id:'wrong-png',source_png_url:'/files/a/runs/other/hero.png'},
    {id:'wrong-blend',source_blend_url:'/files/a/runs/other/hero.blend'},{id:'wrong-hash',source_sha256:'b'.repeat(64)},
    {id:'legacy',source_png_url:undefined,source_blend_url:undefined},
    {id:'zip-is-not-native',native_url:'/files/a/exports/fallback.zip'},
    {id:'other-project-file',native_url:'/files/b/exports/scene.c4d'}
  ].map((overrides,index)=>nativeExport(source,'c4d',{created_at:60+index,...overrides}));
  const blend=nativeExport(source,'blend'),c4d=nativeExport(source,'c4d');
  p.exports=[...invalid,blend,c4d,nativeExport(source,'c4d',{id:'older-complete',created_at:39,native_url:'/files/a/exports/older/scene.c4d'})];
  const h=await setup(t,{ready:false,projects:[p]});await h.run("openProject('a')");
  assert.equal(h.node('downloadLatestBlend').getAttribute('href'),blend.native_url+'?download=1');
  assert.equal(h.node('downloadLatestC4d').getAttribute('href'),c4d.native_url+'?download=1');assert.equal(h.node('downloadLatestC4d').hidden,false);
  assert.equal(h.node('generateLatestC4d').hidden,true,'existing native files remain downloadable without a working converter');
  h.run('current.exports='+JSON.stringify(invalid)+';renderProject()');
  assert.equal(h.node('downloadLatestBlend').getAttribute('href'),source.blend_url+'?download=1');
  assert.equal(h.node('downloadLatestC4d').hidden,true);assert.equal(h.node('downloadLatestC4d').hasAttribute('href'),false);
  h.run('current.exports='+JSON.stringify([nativeExport(source,'c4d',{status:'running'})])+';renderProject()');
  assert.equal(h.node('downloadLatestC4d').hidden,true);assert.equal(h.node('generateLatestC4d').disabled,true);assert.match(h.node('generateLatestC4d').textContent,/正在生成/);
  h.run('delete current.outputs[0].blend_sha256;current.exports='+JSON.stringify([c4d])+';renderProject()');
  assert.equal(h.node('downloadLatestC4d').getAttribute('href'),c4d.native_url+'?download=1','legacy outputs without a hash still require both exact public source URLs');
  h.run('delete current.exports[0].source_png_url;renderProject()');assert.equal(h.node('downloadLatestC4d').hasAttribute('href'),false);
});

test('C4D generation captures the latest snapshot, suppresses duplicate submits, and becomes a download after completion',async t=>{
  const p=project(),source=nativeSnapshot();p.version=2;p.outputs=[nativeSnapshot({job_id:'before',created_at:10,png_url:'/files/a/runs/before/hero.png',blend_url:'/files/a/runs/before/hero.blend'}),source];
  p.jobs=[{id:source.job_id,status:'completed',automatic_previous_preview:copy(p.outputs[0])}];
  const h=await setup(t,{projects:[p]});enableNativeC4d(h);await h.run("openProject('a')");h.node('comparePreview').click();
  const gate=deferred();let automaticDownloads=0,firstRequest=true;h.w.HTMLAnchorElement.prototype.click=function(){automaticDownloads++;};
  h.intercept=call=>{if(call.url!=='/api/projects/a/exports')return;if(firstRequest){firstRequest=false;return h.response({error:'转换服务暂时不可用，请重试'},503);}return gate.promise;};
  await h.run('generateLatestC4d()');assert.equal(h.node('generateLatestC4d').disabled,false);assert.match(h.node('latestSceneStatus').textContent,/暂时不可用/);assert.equal(h.run('current.exports.length'),0);
  const submission=h.run('generateLatestC4d()');await tick();await h.run('generateLatestC4d()');
  const requests=()=>h.calls.filter(call=>call.url.endsWith('/exports') && call.method==='POST');
  assert.equal(requests().length,2);assert.deepEqual(requests()[1].body,{format:'c4d',output_id:source.job_id,shot_id:'hero',quality:'preview',version:2,source_png_url:source.png_url,source_blend_url:source.blend_url,source_sha256:source.blend_sha256});
  assert.equal(h.node('generateLatestC4d').disabled,true);assert.match(h.node('generateLatestC4d').textContent,/正在提交/);
  const queued=nativeExport(source,'c4d',{status:'queued',stage:'等待转换',native_url:null,download_url:null});
  h.records.get('a').exports=[copy(queued)];gate.resolve(h.response(queued,202));await submission;
  assert.match(h.node('generateLatestC4d').textContent,/排队/);await h.run('generateLatestC4d()');assert.equal(requests().length,2);
  h.records.get('a').exports[0].status='running';h.records.get('a').exports[0].stage='正在保存原生工程';await h.run('poll()');
  assert.match(h.node('generateLatestC4d').textContent,/正在生成/);assert.equal(h.node('downloadLatestC4d').hasAttribute('href'),false);
  h.records.get('a').exports=[nativeExport(source)];await h.run('poll()');
  assert.equal(h.node('generateLatestC4d').hidden,true);assert.equal(h.node('downloadLatestC4d').getAttribute('href'),nativeExport(source).native_url+'?download=1');
  assert.equal(automaticDownloads,0,'completion only exposes the link; it never opens a file automatically');assert.equal(requests().length,2);
  assert.equal(h.calls.filter(call=>call.method!=='GET' && !call.url.endsWith('/exports')).length,0);
});

test('late C4D results cannot leak into another shot, another project, or an exited workspace',async t=>{
  const a=project(),source=nativeSnapshot(),side=nativeSnapshot({job_id:'side',shot_id:'side',png_url:'/files/a/runs/side/side.png',blend_url:'/files/a/runs/side/side.blend'}),b=project('b');
  a.version=2;a.outputs=[source,side];b.outputs=[nativeSnapshot({job_id:'b-hero',version:1,png_url:'/files/b/runs/hero/hero.png',blend_url:'/files/b/runs/hero/hero.blend'})];
  const h=await setup(t,{projects:[a,b]});enableNativeC4d(h);await h.run("openProject('a')");
  const gate=deferred();let downloads=0;h.w.HTMLAnchorElement.prototype.click=function(){downloads++;};
  h.intercept=call=>call.url==='/api/projects/a/exports'?gate.promise:undefined;
  const submission=h.run('generateLatestC4d()');await tick();
  h.node('cameraShot').value='side';h.node('cameraShot').dispatchEvent(new h.w.Event('change'));
  assert.equal(h.node('downloadLatestBlend').getAttribute('href'),side.blend_url+'?download=1');assert.equal(h.node('generateLatestC4d').disabled,false);assert.doesNotMatch(h.node('generateLatestC4d').textContent,/提交/);
  await h.run("openProject('b')");gate.resolve(h.response(nativeExport(source)));await submission;
  assert.equal(h.run('current.id'),'b');assert.equal(h.run('current.exports.length'),0);assert.equal(h.node('downloadLatestBlend').getAttribute('href'),b.outputs[0].blend_url+'?download=1');assert.equal(h.node('downloadLatestC4d').hasAttribute('href'),false);assert.equal(downloads,0);
  await h.run('exitProject()');assert.equal(h.node('latestSceneDownloads').hidden,true);assert.equal(h.node('downloadLatestBlend').hasAttribute('href'),false);assert.equal(h.node('downloadLatestC4d').hasAttribute('href'),false);assert.equal(h.node('latestSceneStatus').textContent,'');
});

test('C4D fallback packages are details only and missing conversion prerequisites do not create failing tasks',async t=>{
  const p=project(),source=nativeSnapshot();p.version=2;p.outputs=[nativeSnapshot({job_id:'old',version:1,created_at:10,png_url:'/files/a/runs/old/hero.png',blend_url:'/files/a/runs/old/hero.blend'}),source];
  const fallback=nativeExport(source,'c4d',{status:'failed',native_url:null,download_url:null,conversion_package_url:'/files/a/exports/manual/conversion-kit.zip',error:'Cinema 4D 尚未授权'});p.exports=[fallback];
  const h=await setup(t,{projects:[p]});await h.run("openProject('a')");h.node('comparePreview').click();
  assert.equal(h.node('downloadLatestC4d').hidden,true);assert.equal(h.node('downloadLatestC4d').hasAttribute('href'),false);assert.equal(h.node('generateLatestC4d').hidden,true);assert.equal(h.node('latestSceneDetails').hidden,false);
  h.node('latestSceneDetails').click();await tick();
  assert.equal(h.node('sceneExportDialog').open,true);assert.equal(h.run('sceneExportSource.job_id'),source.job_id);assert.equal(h.w.document.querySelector('[name=sceneExportFormat]:checked').value,'c4d');
  const fallbackLink=[...h.node('sceneExportJobs').querySelectorAll('a')].find(link=>link.getAttribute('href')===fallback.conversion_package_url);
  assert.ok(fallbackLink);assert.match(fallbackLink.textContent,/非 \.c4d/);assert.doesNotMatch(h.node('sceneExportJobs').textContent,/单独下载 \.c4d/);
  h.node('sceneExportDialog').close();h.run('current.exports=[]');
  for(const capability of [
    {export_available:true,c4d_export_available:true,c4d_executable_found:false,c4d_status:'unavailable'},
    {export_available:true,c4d_export_available:true,c4d_executable_found:true,c4d_status:'license_required'},
    {export_available:true,c4d_export_available:false,c4d_executable_found:true,c4d_status:'ready'}
  ]){
    h.run('system.scene_exports='+JSON.stringify(capability)+';system.runtime.capabilities.export_c4d=true;renderProject()');
    assert.equal(h.node('generateLatestC4d').disabled,true);assert.ok(h.node('generateLatestC4d').title);assert.equal(h.node('latestSceneDetails').hidden,false);
    await h.run('generateLatestC4d()');assert.equal(h.node('downloadLatestBlend').getAttribute('href'),source.blend_url+'?download=1');
  }
  assert.equal(h.calls.filter(call=>call.method!=='GET').length,0);
});

test('instruction captures the viewed shot and source while later viewing changes stay untouched',async t=>{
  const p=previewProject();p.outputs.push({...p.outputs[0],job_id:'render-side',shot_id:'side',png_url:'/qa/side.png'});
  const h=await setup(t,{projects:[p]});await h.run("openProject('a')");const gate=deferred();h.afterInstruction=()=>gate.promise;
  input(h,'instructionInput','反光柔和一点，颜色和角度不变');const applying=h.run('applyInstructionAndPreview({preventDefault(){}})');await tick();
  h.w.document.querySelector('[data-shot="side"]').click();assert.equal(h.run('activeShot'),'side');
  gate.resolve(h.response(h.records.get('a')));await applying;
  const instruction=h.calls.find(call=>call.url.endsWith('/instruct')),job=h.calls.find(call=>call.url.endsWith('/jobs'));
  assert.equal(instruction.body.shot_id,'hero');assert.deepEqual(instruction.body.source,{output_id:'render-a',shot_id:'hero',quality:'preview',version:1});
  assert.deepEqual(job.body.shot_ids,['hero']);assert.equal(job.body.expected_version,2);assert.equal(h.run('activeShot'),'side');assert.equal(h.node('shotTitle').textContent,'侧面结构');
});

test('saving a local draft generates a fresh preview before applying the instruction',async t=>{
  const h=await setup(t,{projects:[previewProject()]});await h.run("openProject('a')");input(h,'margin','24');input(h,'instructionInput','亮一点');
  await h.run('applyInstructionAndPreview({preventDefault(){}})');const instruction=h.calls.find(call=>call.url.endsWith('/instruct'));
  assert.equal(instruction,undefined);assert.equal(h.records.get('a').plan.margin,.24);
  assert.equal(h.calls.find(call=>call.url.endsWith('/jobs')).body.expected_version,2);
  assert.equal(h.node('instructionInput').value,'亮一点');assert.match(h.node('instructionError').textContent,/重新应用/);
});

test('stale preview completes before the original adjustment resumes exactly once',async t=>{
  const h=await setup(t,{projects:[previewProject()]});await h.run("openProject('a')");input(h,'margin','24');input(h,'instructionInput','轮子的刹车改成反光金属');
  await h.run('applyInstructionAndPreview({preventDefault(){}})');
  const p=h.records.get('a'),job=p.jobs[0];assert.equal(h.calls.filter(call=>call.url.endsWith('/instruct')).length,0);
  job.status='completed';p.outputs.push({...p.outputs[0],job_id:job.id,version:2,created_at:999,png_url:'/qa/new.png'});
  await h.run('poll()');
  const calls=h.calls.filter(call=>call.url.endsWith('/instruct'));assert.equal(calls.length,1);assert.equal(calls[0].body.source.output_id,job.id);assert.equal(calls[0].body.expected_version,2);
  await h.run('poll()');assert.equal(h.calls.filter(call=>call.url.endsWith('/instruct')).length,1);
});

test('failed preflight retains the instruction and does not apply it',async t=>{
  const h=await setup(t,{projects:[previewProject()]});await h.run("openProject('a')");input(h,'margin','24');input(h,'instructionInput','轮子的刹车改成反光金属');
  await h.run('applyInstructionAndPreview({preventDefault(){}})');h.records.get('a').jobs[0].status='failed';await h.run('poll()');
  assert.equal(h.calls.filter(call=>call.url.endsWith('/instruct')).length,0);assert.equal(h.node('instructionInput').value,'轮子的刹车改成反光金属');assert.match(h.node('instructionError').textContent,/预览未完成/);
});
test('preflight never resumes across changed project, shot, text, version or output',async t=>{
  for(const change of ['project','shot','text','version','output','cancelled']){
    const h=await setup(t,{projects:[previewProject(),project('b')]});await h.run("openProject('a')");input(h,'margin','24');input(h,'instructionInput','轮子的刹车改成反光金属');
    await h.run('applyInstructionAndPreview({preventDefault(){}})');const p=h.records.get('a'),job=p.jobs[0];job.status=change==='cancelled'?'cancelled':'completed';
    p.outputs.push({...p.outputs[0],job_id:change==='output'?'another-job':job.id,version:2,created_at:999,png_url:'/qa/new.png'});
    if(change==='project')await h.run("openProject('b')");
    if(change==='shot')h.run("activeShot='campaign';renderProject()");
    if(change==='text')input(h,'instructionInput','新的要求');
    if(change==='version')p.version=3;
    await h.run('poll()');assert.equal(h.calls.filter(call=>call.url.endsWith('/instruct')).length,0,change);
  }
});
test('preflight draft survives reload without automatically replaying a request',async t=>{
  const first=await setup(t,{projects:[previewProject()]});await first.run("openProject('a')");input(first,'margin','24');input(first,'instructionInput','轮子的刹车改成反光金属');await first.run('applyInstructionAndPreview({preventDefault(){}})');
  const saved=first.w.sessionStorage.getItem('wuying-instruction-preflight-drafts-v1');assert.ok(saved);
  const restored=await setup(t,{projects:[copy(first.records.get('a'))]});restored.w.sessionStorage.setItem('wuying-instruction-preflight-drafts-v1',saved);restored.run('restoreInstructionPreflightDrafts()');await restored.run("openProject('a')");
  assert.equal(restored.node('instructionInput').value,'轮子的刹车改成反光金属');await restored.run('poll()');assert.equal(restored.calls.filter(call=>call.method==='POST').length,0);
});
test('a changed live camera prevents preflight from applying to a different view',async t=>{
  const h=await setup(t,{projects:[previewProject()]});await h.run("openProject('a')");const rt=installRealtime(h);await settleRealtime(h);
  input(h,'margin','24');input(h,'instructionInput','轮子的刹车改成反光金属');await h.run('applyInstructionAndPreview({preventDefault(){}})');
  await settleRealtime(h);
  const p=h.records.get('a'),job=p.jobs[0];job.status='completed';p.outputs.push({...p.outputs[0],job_id:job.id,version:2,created_at:999,png_url:'/qa/new.png'});
  rt.instances.at(-1).orbit({azimuth:90});await h.run('poll()');assert.equal(h.calls.filter(call=>call.url.endsWith('/instruct')).length,0);await settleRealtime(h);
});

test('a lost instruction response reuses its key and exact body after polling the committed version',async t=>{
  const h=await setup(t,{projects:[previewProject()]});await h.run("openProject('a')");input(h,'instructionInput','亮一点');let dropped=false;
  h.afterInstruction=()=>{if(!dropped){dropped=true;throw new TypeError('测试响应连接中断');}};
  await h.run('applyInstructionAndPreview({preventDefault(){}})');assert.equal(h.records.get('a').version,2);assert.equal(h.node('instructionSubmit').textContent,'核对并继续');
  await h.run('poll()');assert.equal(h.run('current.version'),2);await h.run('applyInstructionAndPreview({preventDefault(){}})');
  const calls=h.calls.filter(call=>call.url.endsWith('/instruct'));assert.equal(calls.length,2);assert.equal(calls[0].headers['Idempotency-Key'],calls[1].headers['Idempotency-Key']);assert.deepEqual(calls[0].body,calls[1].body);
  assert.equal(h.records.get('a').plan.exposure,.5);assert.equal(h.records.get('a').version,2);assert.equal(h.instructions.size,1);
  const job=h.calls.find(call=>call.url.endsWith('/jobs'));assert.notEqual(job.headers['Idempotency-Key'],calls[0].headers['Idempotency-Key']);assert.equal(job.body.expected_version,2);
});

test('an HTTP timeout after commit is reconciled with the original instruction key',async t=>{
  const h=await setup(t);await h.run("openProject('a')");input(h,'instructionInput','亮一点');let timedOut=false;
  h.afterInstruction=()=>{if(!timedOut){timedOut=true;return h.response({error:'上游响应超时'},504);}};
  await h.run('applyInstructionAndPreview({preventDefault(){}})');await h.run('applyInstructionAndPreview({preventDefault(){}})');
  const calls=h.calls.filter(call=>call.url.endsWith('/instruct'));assert.equal(calls.length,2);assert.equal(calls[0].headers['Idempotency-Key'],calls[1].headers['Idempotency-Key']);assert.equal(h.records.get('a').plan.exposure,.5);
});

test('replaying an applied instruction on a newer plan shows its original receipt and queues nothing',async t=>{
  const h=await setup(t);await h.run("openProject('a')");input(h,'instructionInput','亮一点');h.afterInstruction=()=>{throw new TypeError('测试响应丢失');};
  await h.run('applyInstructionAndPreview({preventDefault(){}})');h.records.get('a').version=3;h.records.get('a').plan.margin=.3;
  await h.run('applyInstructionAndPreview({preventDefault(){}})');
  assert.equal(h.calls.filter(call=>call.url.endsWith('/jobs')).length,0);assert.equal(h.records.get('a').plan.exposure,.5);assert.equal(h.records.get('a').plan.margin,.3);
  assert.equal(h.node('instructionInput').value,'');assert.match(h.node('instructionReceiptSummary').textContent,/v2.*v3/);
});

test('an obsolete replay receipt cannot erase newer text entered while that response is pending',async t=>{
  const h=await setup(t);await h.run("openProject('a')");input(h,'instructionInput','亮一点');h.afterInstruction=()=>{throw new TypeError('测试响应丢失');};await h.run('applyInstructionAndPreview({preventDefault(){}})');
  const stored=[...h.instructions.values()][0],gate=deferred();h.records.get('a').version=3;
  h.intercept=call=>call.url.endsWith('/instruct')?gate.promise:undefined;
  const applying=h.run('applyInstructionAndPreview({preventDefault(){}})');await tick();input(h,'instructionInput','调整补光');
  gate.resolve(h.response({...h.records.get('a'),instruction_result:{...stored.receipt,replayed:true}}));await applying;
  assert.equal(h.node('instructionInput').value,'调整补光');assert.equal(h.calls.filter(call=>call.url.endsWith('/jobs')).length,0);
});

test('a no-change receipt does not create a version or preview job',async t=>{
  const h=await setup(t);await h.run("openProject('a')");input(h,'instructionInput','保持当前效果');
  h.intercept=call=>call.url.endsWith('/instruct')?h.response({...h.records.get('a'),instruction_result:{id:'no-change-1',text:call.body.text,shot_id:call.body.shot_id,status:'no_change',changes:[],summary:'当前参数已符合要求',source:'rules',notes:[],used_image:false,used_reference:false,base_version:1,applied_version:1,replayed:false}}):undefined;
  await h.run('applyInstructionAndPreview({preventDefault(){}})');assert.equal(h.records.get('a').version,1);assert.equal(h.calls.filter(call=>call.url.endsWith('/jobs')).length,0);assert.equal(h.node('instructionInput').value,'');assert.equal(h.node('instructionReceiptSummary').textContent,'没有参数变化');
});

test('a preview version conflict preserves the actionable error and cannot render another version',async t=>{
  const h=await setup(t);await h.run("openProject('a')");input(h,'instructionInput','亮一点');
  h.intercept=call=>{if(call.url.endsWith('/jobs')){h.records.get('a').version=3;return h.response({error:'预览版本已经变化，请重新检查'},409);}};
  await h.run('applyInstructionAndPreview({preventDefault(){}})');
  assert.equal(h.calls.find(call=>call.url.endsWith('/jobs')).body.expected_version,2);assert.equal(h.records.get('a').jobs.length,0);assert.equal(h.run('current.version'),3);
  assert.equal(h.node('instructionError').hidden,false);assert.match(h.node('instructionError').textContent,/预览版本已经变化/);
});

test('receipts show escaped actual fields, scope and evidence without leaking across projects',async t=>{
  const p=project('a');p.instruction_result={id:'receipt-a',text:'<img src=x onerror=alert(1)>',source:'ai',summary:'<script>alert(2)</script>',notes:['<svg onload=alert(3)>'],changes:[{path:'shot_adjustments.hero.exposure_delta',before:0,after:.35}],used_image:true,used_reference:true,shot_id:'hero',base_version:1,applied_version:1};
  const h=await setup(t,{projects:[p,project('b')]});await h.run("openProject('a')");
  assert.equal(h.node('instructionReceipt').hidden,false);assert.equal(h.node('instructionReceiptDisclosure').open,false);
  assert.match(h.node('instructionReceiptSummary').textContent,/曝光修正.*0 EV.*0.35 EV/);assert.match(h.node('instructionReceiptDetails').textContent,/镜头：白底主图/);assert.match(h.node('instructionReceiptDetails').textContent,/预览图：已使用.*参考图：已使用/);
  assert.equal(h.node('instructionReceipt').querySelector('img,script,svg'),null);assert.match(h.node('instructionReceiptDetails').textContent,/<script>/);
  await h.run("openProject('b')");assert.equal(h.node('instructionReceipt').hidden,true);assert.equal(h.node('instructionReceiptDetails').textContent,'');
  await h.run("openProject('a')");assert.match(h.node('instructionReceiptDetails').textContent,/镜头：白底主图/);
});

test('an unknown instruction survives a project round-trip with its original request identity',async t=>{
  const h=await setup(t);await h.run("openProject('a')");input(h,'instructionInput','亮一点');h.afterInstruction=()=>{throw new TypeError('测试响应丢失');};await h.run('applyInstructionAndPreview({preventDefault(){}})');
  await h.run("openProject('b')");assert.equal(h.node('instructionInput').value,'');await h.run("openProject('a')");assert.equal(h.node('instructionInput').value,'亮一点');
  await h.run('applyInstructionAndPreview({preventDefault(){}})');const calls=h.calls.filter(call=>call.url.endsWith('/instruct'));
  assert.equal(calls[0].headers['Idempotency-Key'],calls[1].headers['Idempotency-Key']);assert.equal(h.records.get('a').plan.exposure,.5);
});

test('restored session attempts require an explicit retry and retain both original keys',async t=>{
  const first=await setup(t);await first.run("openProject('a')");input(first,'instructionInput','亮一点');first.afterInstruction=()=>{throw new TypeError('测试响应丢失');};await first.run('applyInstructionAndPreview({preventDefault(){}})');
  const journal=first.w.sessionStorage.getItem('wuying-instruction-attempts-v1');assert.ok(journal);
  const restored=await setup(t,{projects:[copy(first.records.get('a'))],journal});restored.instructions=new Map(first.instructions);
  await restored.run("openProject('a')");assert.equal(restored.calls.filter(call=>call.method==='POST').length,0);assert.equal(restored.node('instructionSubmit').textContent,'核对并继续');
  await restored.run('applyInstructionAndPreview({preventDefault(){}})');
  const original=first.calls.find(call=>call.url.endsWith('/instruct')),retry=restored.calls.find(call=>call.url.endsWith('/instruct'));assert.equal(original.headers['Idempotency-Key'],retry.headers['Idempotency-Key']);assert.deepEqual(original.body,retry.body);
  assert.equal(restored.calls.find(call=>call.url.endsWith('/jobs')).headers['Idempotency-Key'],JSON.parse(journal).attempts[0].previewKey);
  assert.equal(restored.w.sessionStorage.getItem('wuying-instruction-attempts-v1'),null);
});

function journalAttempt(index=0) {
  const text='待核对指令 '+index;
  return {projectId:'a',text,shotId:'hero',shotIds:['hero'],body:{text,expected_version:1,shot_id:'hero',source:null},instructionKey:'instruction-'+index,previewKey:'preview-'+index,phase:'unknown',confirmed:false,version:null,submitted:false};
}

test('corrupt or tampered recovery records never send an arbitrary source or automatic request',async t=>{
  const bad=journalAttempt();bad.body.source={url:'file:///private.env'};
  const h=await setup(t,{journal:{schema:1,attempts:[bad]}});await h.run("openProject('a')");assert.equal(h.node('instructionInput').value,'');assert.equal(h.run('instructionAttempts.size'),0);assert.equal(h.calls.filter(call=>call.method==='POST').length,0);
  const corrupt=await setup(t,{journal:'{invalid JSON'});assert.equal(corrupt.run('instructionAttempts.size'),0);assert.equal(corrupt.calls.filter(call=>call.method==='POST').length,0);
});

test('the recovery cap blocks new attempts instead of evicting uncertain request keys',async t=>{
  const attempts=Array.from({length:50},(_,index)=>journalAttempt(index)),h=await setup(t,{journal:{schema:1,attempts}});await h.run("openProject('a')");input(h,'instructionInput','一条新的调整');
  await h.run('applyInstructionAndPreview({preventDefault(){}})');assert.equal(h.calls.filter(call=>call.method==='POST').length,0);assert.match(h.node('instructionError').textContent,/达到上限/);
  const kept=JSON.parse(h.w.sessionStorage.getItem('wuying-instruction-attempts-v1')).attempts;assert.equal(kept.length,50);assert.deepEqual(kept.map(item=>item.instructionKey),attempts.map(item=>item.instructionKey));
});

test('session storage failure does not block an otherwise valid adjustment',async t=>{
  const h=await setup(t);await h.run("openProject('a')");h.w.Storage.prototype.setItem=()=>{throw new Error('Storage unavailable');};input(h,'instructionInput','亮一点');
  await h.run('applyInstructionAndPreview({preventDefault(){}})');assert.equal(h.records.get('a').plan.exposure,.5);assert.equal(h.calls.filter(call=>call.url.endsWith('/jobs')).length,1);
});

test('automatic correction decision reasons remain escaped inside the existing folded record',async t=>{
  const p=previewProject();p.outputs[0].corrections=[{round:0,applied:true,exposure:0,light_softness:1,decision_reason:'原始预览，作为本轮比较基准。'},{round:1,exposure:.2,light_softness:1.2,decision_reason:'新增裁切 <script>alert(1)</script>'}];
  const h=await setup(t,{projects:[p]});await h.run("openProject('a')");assert.equal(h.node('reviewDetails').closest('details').open,false);
  const reasons=h.node('reviewDetails').querySelectorAll('.review-decision');assert.equal(reasons.length,2);assert.match(reasons[1].textContent,/新增裁切/);assert.equal(h.node('reviewDetails').querySelector('script'),null);
});

test('server-selected affected shots drive previews without changing the captured context or visible shot',async t=>{
  const h=await setup(t,{projects:[previewProject()]});await h.run("openProject('a')");input(h,'instructionInput','把宣传图改成深色，主图保持白底');
  h.afterInstruction=(_call,p,receipt)=>{receipt.preview_shot_ids=['campaign','side'];receipt.notes=['白底主图保持不变，预览已调整的宣传镜头。'];p.instruction_result=copy(receipt);};
  await h.run('applyInstructionAndPreview({preventDefault(){}})');
  const instruction=h.calls.find(call=>call.url.endsWith('/instruct')),preview=h.calls.find(call=>call.url.endsWith('/jobs'));
  assert.equal(instruction.body.shot_id,'hero');assert.equal(instruction.body.source.shot_id,'hero');assert.deepEqual(preview.body.shot_ids,['campaign','side']);assert.equal(h.run('activeShot'),'hero');
  assert.match(h.node('instructionReceiptDetails').textContent,/白底主图保持不变/);
});

test('invalid server preview targets fall back to the captured known shot',async t=>{
  const h=await setup(t);await h.run("openProject('a')");input(h,'instructionInput','亮一点');
  h.afterInstruction=(_call,p,receipt)=>{receipt.preview_shot_ids=['campaign','not-a-current-shot'];p.instruction_result=copy(receipt);};
  await h.run('applyInstructionAndPreview({preventDefault(){}})');
  assert.deepEqual(h.calls.find(call=>call.url.endsWith('/jobs')).body.shot_ids,['hero']);
});

test('a restored saved instruction retries its redirected preview without applying the command again',async t=>{
  const first=await setup(t);await first.run("openProject('a')");input(first,'instructionInput','宣传图用深色');
  first.afterInstruction=(_call,p,receipt)=>{receipt.preview_shot_ids=['campaign'];p.instruction_result=copy(receipt);};failFirstPreview(first);
  await first.run('applyInstructionAndPreview({preventDefault(){}})');
  const journal=first.w.sessionStorage.getItem('wuying-instruction-attempts-v1'),saved=JSON.parse(journal).attempts[0];assert.deepEqual(saved.shotIds,['campaign']);
  const restored=await setup(t,{projects:[copy(first.records.get('a'))],journal});await restored.run("openProject('a')");
  assert.equal(restored.node('instructionSubmit').textContent,'重试预览');await restored.run('applyInstructionAndPreview({preventDefault(){}})');
  assert.equal(restored.calls.filter(call=>call.url.endsWith('/instruct')).length,0);
  const preview=restored.calls.find(call=>call.url.endsWith('/jobs'));assert.deepEqual(preview.body.shot_ids,['campaign']);assert.equal(preview.headers['Idempotency-Key'],saved.previewKey);assert.equal(preview.body.expected_version,2);
});

test('a retryable in-progress conflict retains the original instruction key until the receipt is confirmed',async t=>{
  const h=await setup(t);await h.run("openProject('a')");input(h,'instructionInput','亮一点');h.afterInstruction=()=>{throw new TypeError('测试响应丢失');};
  await h.run('applyInstructionAndPreview({preventDefault(){}})');
  h.intercept=call=>call.url.endsWith('/instruct')?h.response({error:'原请求仍在处理，请稍后核对。',error_code:'instruction_in_progress',retryable:true},409):undefined;
  await h.run('applyInstructionAndPreview({preventDefault(){}})');
  assert.equal(h.node('instructionSubmit').textContent,'核对并继续');assert.equal(h.calls.filter(call=>call.url.endsWith('/jobs')).length,0);
  h.intercept=null;await h.run('applyInstructionAndPreview({preventDefault(){}})');
  const instructions=h.calls.filter(call=>call.url.endsWith('/instruct'));
  assert.equal(instructions.length,3);assert.equal(new Set(instructions.map(call=>call.headers['Idempotency-Key'])).size,1);
  assert.ok(instructions.every(call=>JSON.stringify(call.body)===JSON.stringify(instructions[0].body)));assert.equal(h.records.get('a').plan.exposure,.5);assert.equal(h.calls.filter(call=>call.url.endsWith('/jobs')).length,1);
});

test('project deletion is a separate accessible list action and cancellation sends no request',async t=>{
  const a=project('a');a.name='<img src=x onerror=alert(1)>';
  const h=await setup(t,{projects:[a,project('b')]});
  const buttons=h.node('projectList').querySelectorAll('[data-delete-project]');
  assert.equal(buttons.length,2);assert.equal(buttons[0].closest('[data-project]'),null);
  assert.equal(buttons[0].type,'button');assert.match(buttons[0].getAttribute('aria-label'),/删除项目/);
  const removing=h.run("deleteProject('a')");await tick();
  assert.match(h.node('deleteProjectDialog').textContent,/无法恢复/);assert.match(h.node('deleteProjectDialog').textContent,/<img/);
  assert.equal(h.node('deleteProjectDialog').querySelector('img,script'),null);
  h.node('deleteProjectDialog').querySelector('[data-choice="cancel"]').click();assert.equal(await removing,false);
  assert.equal(h.calls.filter(call=>call.method==='DELETE').length,0);assert.equal(h.run('current'),null);
});

test('deleting a different project preserves the selected project and its unsaved draft',async t=>{
  const h=await setup(t);await h.run("openProject('a')");input(h,'margin','26');input(h,'instructionInput','保留这条输入');
  const removing=h.run("deleteProject('b')");await tick();h.node('deleteProjectDialog').querySelector('[data-choice="delete"]').click();
  assert.equal(await removing,true);assert.equal(h.run('current.id'),'a');assert.equal(h.run('dirty'),true);assert.equal(h.node('margin').value,'26');
  assert.equal(h.node('instructionInput').value,'保留这条输入');assert.equal(h.records.has('b'),false);
  assert.equal(h.node('projectList').querySelector('[data-project="b"]'),null);assert.equal(h.node('projectCount').textContent,'1');
  assert.deepEqual(h.calls.filter(call=>call.method==='DELETE').map(call=>call.url),['/api/projects/b']);
  assert.equal(h.calls.filter(call=>call.url==='/api/projects/b' && call.method==='GET').length,0);
});

test('deleting the current project returns home and removes only that project recovery state',async t=>{
  const attemptA=journalAttempt(1),attemptB=journalAttempt(2);attemptB.projectId='b';
  const h=await setup(t,{projects:[previewProject(),project('b')],journal:{schema:1,attempts:[attemptA,attemptB]}});await h.run("openProject('a')");
  h.run("instructionReceipts.set('a',{id:'a'});instructionReceipts.set('b',{id:'b'});materialAgentRecords.set('a',[]);materialAgentDisclosures.set('a:task',true);materialAgentDisclosures.set('b:task',true);remember('wuying-shot-a','hero')");
  input(h,'margin','24');const removing=h.run("deleteProject('a')");await tick();assert.match(h.node('deleteProjectDialog').textContent,/未保存的调整也会丢弃/);
  h.node('deleteProjectDialog').querySelector('[data-choice="delete"]').click();assert.equal(await removing,true);
  assert.equal(h.run('current'),null);assert.equal(h.run('dirty'),false);assert.equal(h.run('draft'),null);assert.equal(h.run('currentOutput'),null);
  assert.equal(h.node('welcomePanel').hidden,false);assert.equal(h.node('projectPanel').hidden,true);assert.equal(h.node('exitProjectButton').hidden,true);
  assert.equal(h.node('mobileDeleteProject').hidden,true);assert.equal(h.node('renderImage').hasAttribute('src'),false);assert.equal(h.node('instructionReceipt').hidden,true);
  assert.equal(h.w.localStorage.getItem('wuying-project'),null);assert.equal(h.w.localStorage.getItem('wuying-shot-a'),null);
  assert.equal(h.run("instructionDrafts.has('a') || instructionReceipts.has('a') || materialAgentRecords.has('a') || materialAgentDisclosures.has('a:task')"),false);
  assert.equal(h.run("instructionReceipts.has('b') && materialAgentDisclosures.has('b:task')"),true);
  assert.deepEqual(JSON.parse(h.w.sessionStorage.getItem('wuying-instruction-attempts-v1')).attempts.map(item=>item.projectId),['b']);
  assert.equal(h.calls.filter(call=>call.url.endsWith('/plan')).length,0);
});

test('a refused or missing project deletion preserves the project, draft and recovery journal',async t=>{
  for(const status of [409,404]){
    const h=await setup(t,{journal:{schema:1,attempts:[journalAttempt()]}});await h.run("openProject('a')");input(h,'margin','28');
    h.intercept=call=>call.method==='DELETE'?h.response({error:status===409?'项目仍有任务运行，请先取消或等待。':'项目记录不存在。'},status):undefined;
    const journal=h.w.sessionStorage.getItem('wuying-instruction-attempts-v1'),removing=h.run("deleteProject('a')");await tick();
    h.node('deleteProjectDialog').querySelector('[data-choice="delete"]').click();assert.equal(await removing,false);
    assert.equal(h.run('current.id'),'a');assert.equal(h.run('dirty'),true);assert.equal(h.node('margin').value,'28');assert.equal(h.records.has('a'),true);
    assert.equal(h.w.sessionStorage.getItem('wuying-instruction-attempts-v1'),journal);assert.equal(h.node('exitProjectButton').disabled,false);
    assert.match(h.node('toast').textContent,status===409?/先取消或等待/:/记录不存在/);
  }
});

test('project deletion locks duplicate actions and shows deferred cleanup feedback honestly',async t=>{
  const h=await setup(t);await h.run("openProject('a')");const gate=deferred();
  h.intercept=call=>call.method==='DELETE'?gate.promise:undefined;
  const removing=h.run("deleteProject('a')");await tick();h.node('deleteProjectDialog').querySelector('[data-choice="delete"]').click();await tick();
  assert.equal(h.node('exitProjectButton').disabled,true);assert.equal(h.node('projectList').querySelector('[data-delete-project="b"]').disabled,true);
  assert.equal(await h.run("deleteProject('a')"),false);assert.equal(await h.run('exitProject()'),false);
  h.records.delete('a');gate.resolve(h.response({deleted:true,id:'a',cleanup_pending:true,warning:'项目已移除，剩余临时文件将在下次启动时清理。'}));assert.equal(await removing,true);
  assert.equal(h.calls.filter(call=>call.method==='DELETE').length,1);assert.match(h.node('toast').textContent,/剩余临时文件/);
});

test('late list, material and poll responses cannot resurrect a deleted project',async t=>{
  const h=await setup(t);await h.run("openProject('a')");const listGate=deferred(),pollGate=deferred(),materialGate=deferred(),old=copy(h.records.get('a')),oldList=[...h.records.values()].map(copy);
  let listFirst=true,pollFirst=true;
  h.intercept=call=>{if(call.url==='/api/projects' && listFirst){listFirst=false;return listGate.promise;}if(call.url==='/api/projects/a' && call.method==='GET' && pollFirst){pollFirst=false;return pollGate.promise;}if(call.url==='/api/projects/a/material-agent/tasks')return materialGate.promise;};
  const refreshing=h.run('refreshList()'),polling=h.run('poll()'),materials=h.run("refreshMaterialTasks('a')");await tick();
  const removing=h.run("deleteProject('a')");await tick();h.node('deleteProjectDialog').querySelector('[data-choice="delete"]').click();await removing;
  listGate.resolve(h.response(oldList));pollGate.resolve(h.response(old));materialGate.resolve(h.response([{id:'old-task',status:'completed'}]));await Promise.all([refreshing,polling,materials]);
  assert.equal(h.run('current'),null);assert.equal(h.node('projectList').querySelector('[data-project="a"]'),null);assert.equal(h.run("materialAgentRecords.has('a')"),false);
});

test('exiting returns to upload without deleting the project or cancelling its background work',async t=>{
  const a=project('a');a.jobs=[{id:'active-a',kind:'preview',status:'running',shot_ids:['hero'],stage:'渲染中',created_at:2}];
  const h=await setup(t,{projects:[a]});await h.run("openProject('a')");assert.equal(h.node('exitProjectButton').hidden,false);assert.equal(h.node('exitProjectButton').disabled,false);
  assert.equal(await h.run('exitProject()'),true);assert.equal(h.run('current'),null);assert.equal(h.node('welcomePanel').hidden,false);
  assert.equal(h.node('projectPanel').hidden,true);assert.equal(h.node('previewButton').disabled,true);assert.equal(h.w.localStorage.getItem('wuying-project'),null);
  assert.equal(h.records.has('a'),true);assert.equal(h.records.get('a').jobs[0].status,'running');assert.equal(h.calls.filter(call=>call.method!=='GET').length,0);
  assert.match(h.node('toast').textContent,/后台继续/);assert.equal(h.w.document.activeElement.id,'uploadMain');
  await h.run("openProject('a')");assert.equal(h.run('current.id'),'a');assert.equal(h.node('jobPanel').hidden,false);
});

test('exiting lets a parameter draft stay, discard or save before returning home',async t=>{
  const h=await setup(t);await h.run("openProject('a')");input(h,'margin','29');
  const staying=h.run('exitProject()');await tick();h.node('unsavedDialog').querySelector('[data-choice="cancel"]').click();assert.equal(await staying,false);
  assert.equal(h.run('current.id'),'a');assert.equal(h.run('dirty'),true);
  const discarding=h.run('exitProject()');await tick();h.node('unsavedDialog').querySelector('[data-choice="discard"]').click();assert.equal(await discarding,true);
  assert.equal(h.run('current'),null);assert.equal(h.records.get('a').plan.margin,.12);assert.equal(h.calls.filter(call=>call.url.endsWith('/plan')).length,0);
  await h.run("openProject('a')");input(h,'margin','31');const saving=h.run('exitProject()');await tick();h.node('unsavedDialog').querySelector('[data-choice="save"]').click();assert.equal(await saving,true);
  assert.equal(h.run('current'),null);assert.equal(h.records.get('a').plan.margin,.31);assert.equal(h.records.get('a').version,2);
});

test('late project open and poll results cannot reopen an exited project',async t=>{
  const h=await setup(t);await h.run("openProject('a')");const pollGate=deferred(),openGate=deferred(),old=copy(h.records.get('a'));
  h.intercept=call=>call.url==='/api/projects/a'?pollGate.promise:call.url==='/api/projects/b'?openGate.promise:undefined;
  const polling=h.run('poll()');await tick();const opening=h.run("openProject('b')");await tick();assert.equal(h.run('projectLoading'),true);
  assert.equal(await h.run('exitProject()'),true);assert.equal(h.run('projectLoading'),false);
  pollGate.resolve(h.response(old));openGate.resolve(h.response(h.records.get('b')));await Promise.all([polling,opening]);
  assert.equal(h.run('current'),null);assert.equal(h.node('welcomePanel').hidden,false);assert.equal(h.w.localStorage.getItem('wuying-project'),null);
});

test('an uncertain instruction survives exit and reopening with its exact request key',async t=>{
  const h=await setup(t);await h.run("openProject('a')");input(h,'instructionInput','亮一点');h.afterInstruction=()=>{throw new TypeError('测试响应丢失');};await h.run('applyInstructionAndPreview({preventDefault(){}})');
  const original=h.calls.find(call=>call.url.endsWith('/instruct')),journal=h.w.sessionStorage.getItem('wuying-instruction-attempts-v1');
  await h.run('exitProject()');assert.equal(h.w.sessionStorage.getItem('wuying-instruction-attempts-v1'),journal);assert.equal(h.node('instructionInput').value,'');
  await h.run("openProject('a')");assert.equal(h.node('instructionInput').value,'亮一点');assert.equal(h.node('instructionSubmit').textContent,'核对并继续');
  await h.run('applyInstructionAndPreview({preventDefault(){}})');const retry=h.calls.filter(call=>call.url.endsWith('/instruct')).at(-1);
  assert.equal(retry.headers['Idempotency-Key'],original.headers['Idempotency-Key']);assert.deepEqual(retry.body,original.body);assert.equal(h.records.get('a').plan.exposure,.5);
});

test('a saved instruction retains preview-only retry after exiting and reopening',async t=>{
  const h=await setup(t);await h.run("openProject('a')");input(h,'instructionInput','亮一点');failFirstPreview(h);
  await h.run('applyInstructionAndPreview({preventDefault(){}})');const journal=h.w.sessionStorage.getItem('wuying-instruction-attempts-v1');
  await h.run('exitProject()');assert.equal(h.w.sessionStorage.getItem('wuying-instruction-attempts-v1'),journal);
  await h.run("openProject('a')");assert.equal(h.node('instructionSubmit').textContent,'重试预览');await h.run('applyInstructionAndPreview({preventDefault(){}})');
  assert.equal(h.calls.filter(call=>call.url.endsWith('/instruct')).length,1);assert.equal(h.records.get('a').plan.exposure,.5);
});

const partialInstructionText='四个轮子改成黑色橡胶，灯光亮一点';
const remainingInstructionText='四个轮子改成黑色橡胶';
function installPartialFlow(h,state=null) {
  const flow=state || {proposals:new Map(),initial:new Map(),confirmed:new Map(),sequence:0,afterConfirm:null,beforeConfirm:null,beforeJob:null};
  h.intercept=async call=>{
    const match=call.url.match(/^\/api\/projects\/([^/]+)\/instruct(\/confirm)?$/),p=match?h.records.get(match[1]):null;
    if(call.url.endsWith('/jobs') && flow.beforeJob){const result=await flow.beforeJob(call);if(result!==undefined)return result;}
    if(!p)return;
    if(!match[2]){
      if(call.body.text!==partialInstructionText)return;
      const key=call.headers['Idempotency-Key'],saved=flow.initial.get(key);
      if(saved)return h.response({...p,instruction_result:{...copy(saved),replayed:true}});
      const id='proposal-'+(++flow.sequence),receipt={id:'receipt-'+id,status:'needs_confirmation',proposal_id:id,text:call.body.text,shot_id:call.body.shot_id,
        base_version:p.version,applied_version:p.version,changes:[],proposed_changes:[{path:'exposure',before:p.plan.exposure,after:p.plan.exposure+.5}],
        ready:[{id:'lighting',quote:'灯光亮一点',summary:'灯光：曝光增加 0.5 EV',status:'ready'}],
        pending:[{id:'wheels',label:'四个轮子',quote:remainingInstructionText,reason:'仍需核对 pasted__pCylinder19；<svg onload=alert(1)> 不是确认部位。'}],
        remaining_text:remainingInstructionText,preview_shot_ids:[],source:'ai',used_image:true,used_reference:false,notes:[],source_snapshot:call.body.source?{quality:call.body.source.quality,version:call.body.source.version,shot_id:call.body.source.shot_id,output_id:call.body.source.output_id}:null};
      flow.initial.set(key,copy(receipt));flow.proposals.set(id,copy(receipt));p.instruction_result=copy(receipt);return h.response({...p,instruction_result:receipt});
    }
    if(flow.beforeConfirm){const result=await flow.beforeConfirm(call);if(result!==undefined)return result;}
    const proposal=flow.proposals.get(call.body.proposal_id),saved=flow.confirmed.get(call.body.proposal_id);
    if(saved)return h.response({...p,instruction_result:{...copy(saved),replayed:true}});
    if(!proposal || call.body.expected_version!==p.version)return h.response({error:'方案已变化，确认未应用。',error_code:'instruction_proposal_stale'},409);
    const before=p.plan.exposure;p.history.push({version:p.version,plan:copy(p.plan),at:1,reason:proposal.text});p.version++;p.plan.exposure+=.5;
    const receipt={...copy(proposal),id:'applied-'+proposal.proposal_id,status:'applied',partial:true,applied_version:p.version,changes:[{path:'exposure',before,after:p.plan.exposure}],preview_shot_ids:[proposal.shot_id]};
    delete receipt.proposed_changes;p.instruction_result=copy(receipt);flow.confirmed.set(proposal.proposal_id,copy(receipt));
    if(flow.afterConfirm){const result=await flow.afterConfirm(call,p,receipt);if(result!==undefined)return result;}
    return h.response(p);
  };
  return flow;
}
async function proposePartial(h) {input(h,'instructionInput',partialInstructionText);await h.run('applyInstructionAndPreview({preventDefault(){}})');}

function installAutomaticPartialFlow(h,state=null) {
  const flow=state || {receipts:new Map(),afterApply:null,beforeJob:null,noChange:false,remainingReply:null};
  h.intercept=async call=>{
    if(call.url.endsWith('/jobs') && flow.beforeJob){const result=await flow.beforeJob(call);if(result!==undefined)return result;}
    if(call.url.endsWith('/instruct/confirm'))throw new Error('Automatic partial execution must not call confirmation');
    const match=call.url.match(/^\/api\/projects\/([^/]+)\/instruct$/),p=match?h.records.get(match[1]):null;
    if(!p)return;
    if(call.body.text!==partialInstructionText)return flow.remainingReply?flow.remainingReply(call,p):undefined;
    const key=call.headers['Idempotency-Key'],saved=flow.receipts.get(key);
    if(saved){assert.equal(JSON.stringify(call.body),saved.body);return h.response({...p,instruction_result:{...copy(saved.receipt),replayed:true}});}
    assert.equal(call.body.expected_version,p.version);
    const base=p.version,before=p.plan.exposure;
    if(!flow.noChange){p.history.push({version:base,plan:copy(p.plan),at:1,reason:call.body.text});p.version++;p.plan.exposure+=.5;}
    const receipt={id:'auto-partial-'+key,status:flow.noChange?'no_change':'partially_applied',partial:true,text:call.body.text,
      shot_id:call.body.shot_id,base_version:base,applied_version:p.version,
      changes:flow.noChange?[]:[{path:'exposure',before,after:p.plan.exposure}],
      ready:[{id:'lighting',label:'灯光',quote:'灯光亮一点',summary:'灯光：灯光',status:flow.noChange?'already_satisfied':'ready'}],
      pending:[{id:'wheels',label:'四个轮子',quote:remainingInstructionText,status:'needs_clarification',action:'locate_part',reason:'需要核验完整轮胎表面。<script>bad()</script>'}],
      remaining_text:remainingInstructionText,preview_shot_ids:flow.noChange?[]:[call.body.shot_id],source:'ai',notes:[],used_image:true,used_reference:false,
      source_snapshot:call.body.source?copy(call.body.source):null};
    p.instruction_result=copy(receipt);flow.receipts.set(key,{body:JSON.stringify(call.body),receipt:copy(receipt)});
    if(flow.afterApply){const result=await flow.afterApply(call,p,receipt);if(result!==undefined)return result;}
    return h.response(p);
  };
  return flow;
}

test('automatic partial execution applies and previews once, leaving one compact remaining-work panel',async t=>{
  const h=await setup(t,{projects:[previewProject()]});installAutomaticPartialFlow(h);await h.run("openProject('a')");
  h.run("instructionReceipts.set('a',{id:'old',status:'applied',text:'以前的材质修改',changes:[{path:'margin',before:.1,after:.2}],applied_version:1});renderInstruction()");
  await proposePartial(h);
  assert.deepEqual(h.calls.filter(call=>call.method==='POST').map(call=>call.url),['/api/projects/a/instruct','/api/projects/a/jobs']);
  assert.equal(h.records.get('a').version,2);assert.equal(h.records.get('a').plan.exposure,.5);assert.equal(h.records.get('a').history.length,1);
  assert.equal(h.node('instructionInput').value,remainingInstructionText);assert.equal(h.node('instructionSubmit').textContent,'继续处理剩余要求');
  assert.equal(h.node('instructionProposalSummary').textContent,'已完成灯光；还有 1 项待处理');
  assert.equal(h.node('instructionReady').hidden,true);assert.equal(h.node('instructionReadyList').textContent,'');
  assert.equal(h.node('instructionProposalDisclosure').open,false);assert.equal(h.node('instructionReceipt').hidden,true);
  assert.match(h.node('instructionProposalDetails').textContent,/待处理要求.*四个轮子.*已完成的修改.*曝光/s);
  assert.doesNotMatch(h.node('instructionProposalDetails').textContent,/待应用的参数|灯光：灯光|以前的材质修改/);
  assert.equal(h.node('instructionProposal').querySelector('script'),null);
  assert.equal(h.node('instructionLocate').disabled,true);assert.equal(h.w.sessionStorage.getItem('wuying-instruction-attempts-v1'),null);
});

test('a lost automatic partial response replays the same request and never reapplies its lighting',async t=>{
  const h=await setup(t,{projects:[previewProject()]});const flow=installAutomaticPartialFlow(h);await h.run("openProject('a')");
  flow.afterApply=()=>{throw new TypeError('测试回执丢失');};await proposePartial(h);
  const first=h.calls.find(call=>call.url.endsWith('/instruct'));
  assert.equal(h.node('instructionSubmit').textContent,'核对并继续');assert.equal(h.node('instructionInput').value,partialInstructionText);
  assert.equal(h.calls.filter(call=>call.url.endsWith('/jobs')).length,0);await h.run('poll()');
  await h.run('applyInstructionAndPreview({preventDefault(){}})');
  const requests=h.calls.filter(call=>call.url.endsWith('/instruct'));
  assert.equal(requests.length,2);assert.equal(requests[1].headers['Idempotency-Key'],first.headers['Idempotency-Key']);assert.deepEqual(requests[1].body,first.body);
  assert.equal(h.records.get('a').version,2);assert.equal(h.records.get('a').history.length,1);assert.equal(h.records.get('a').plan.exposure,.5);
  assert.equal(h.calls.filter(call=>call.url.endsWith('/jobs')).length,1);assert.equal(h.node('instructionInput').value,remainingInstructionText);
});

test('automatic partial preview failure survives reload and retries only its original preview key',async t=>{
  const first=await setup(t,{projects:[previewProject()]});const flow=installAutomaticPartialFlow(first);await first.run("openProject('a')");
  flow.beforeJob=()=>first.response({error:'测试预览未开始'},503);await proposePartial(first);
  const journal=first.w.sessionStorage.getItem('wuying-instruction-attempts-v1'),saved=JSON.parse(journal).attempts[0];
  assert.equal(saved.kind,undefined);assert.equal(saved.remainingText,remainingInstructionText);assert.equal(saved.confirmed,true);
  assert.equal(first.node('instructionSubmit').textContent,'重试预览');assert.equal(first.node('instructionInput').value,remainingInstructionText);
  const restored=await setup(t,{projects:[copy(first.records.get('a'))],journal});await restored.run("openProject('a')");
  assert.equal(restored.node('instructionInput').value,remainingInstructionText);assert.equal(restored.node('instructionSubmit').textContent,'重试预览');
  await restored.run('applyInstructionAndPreview({preventDefault(){}})');
  assert.equal(restored.calls.filter(call=>call.url.includes('/instruct')).length,0);
  const job=restored.calls.find(call=>call.url.endsWith('/jobs'));assert.equal(job.headers['Idempotency-Key'],saved.previewKey);assert.equal(job.body.expected_version,2);
  assert.equal(restored.records.get('a').plan.exposure,.5);assert.equal(restored.node('instructionInput').value,remainingInstructionText);
});

test('an automatic partial receipt restores remaining work after reopening without applying again',async t=>{
  const first=await setup(t,{projects:[previewProject()]});installAutomaticPartialFlow(first);await first.run("openProject('a')");await proposePartial(first);
  const saved=copy(first.records.get('a'));saved.jobs=[];saved.outputs=[{...saved.outputs[0],version:2,job_id:'render-after-auto-partial'}];
  const restored=await setup(t,{projects:[saved,project('b')]});await restored.run("openProject('a')");
  assert.equal(restored.node('instructionInput').value,remainingInstructionText);assert.equal(restored.node('instructionLocate').disabled,false);
  assert.equal(restored.node('instructionProposalSummary').textContent,'已完成灯光；还有 1 项待处理');
  assert.equal(restored.calls.filter(call=>call.method==='POST').length,0);
  await restored.run("openProject('b')");assert.equal(restored.node('instructionProposal').hidden,true);
  await restored.run("openProject('a')");assert.equal(restored.node('instructionInput').value,remainingInstructionText);
  assert.equal(restored.calls.filter(call=>call.method==='POST').length,0);
});

test('automatic partial work already satisfied keeps only pending requirements and starts no preview',async t=>{
  const h=await setup(t,{projects:[previewProject()]});const flow=installAutomaticPartialFlow(h);flow.noChange=true;
  await h.run("openProject('a')");await proposePartial(h);
  assert.equal(h.records.get('a').version,1);assert.equal(h.records.get('a').history.length,0);
  assert.equal(h.node('instructionInput').value,remainingInstructionText);assert.equal(h.calls.filter(call=>call.url.endsWith('/jobs')).length,0);
  assert.equal(h.node('instructionProposalSummary').textContent,'可执行项已符合要求；还有 1 项待处理');
  assert.equal(h.node('instructionReceipt').hidden,true);assert.equal(h.node('instructionReady').hidden,true);
  assert.doesNotMatch(h.node('instructionProposalDetails').textContent,/已完成的修改|待应用的参数/);
  const restored=await setup(t,{projects:[copy(h.records.get('a'))]});await restored.run("openProject('a')");
  assert.equal(restored.node('instructionInput').value,remainingInstructionText);assert.equal(restored.calls.filter(call=>call.method==='POST').length,0);
});

test('retrying remaining automatic work excludes completed lighting and hides stale success on protocol failure',async t=>{
  const h=await setup(t,{projects:[previewProject()]});const flow=installAutomaticPartialFlow(h);await h.run("openProject('a')");await proposePartial(h);
  h.records.get('a').jobs=[];h.records.get('a').outputs=[{...h.records.get('a').outputs[0],version:2,job_id:'fresh-after-partial'}];await h.run('poll()');
  flow.remainingReply=call=>{
    assert.equal(call.body.text,remainingInstructionText);assert.equal(call.body.expected_version,2);
    return h.response({error:'这次返回的修改结果未通过校验，当前方案未修改。可以直接重试，无需改写要求。',error_code:'instruction_response_invalid',retryable:true,applied:false},422);
  };
  await h.run('applyInstructionAndPreview({preventDefault(){}})');
  const requests=h.calls.filter(call=>call.url.endsWith('/instruct'));assert.equal(requests.length,2);assert.notEqual(requests[0].headers['Idempotency-Key'],requests[1].headers['Idempotency-Key']);
  assert.equal(h.records.get('a').plan.exposure,.5);assert.equal(h.records.get('a').version,2);
  assert.equal(h.node('instructionInput').value,remainingInstructionText);assert.equal(h.node('instructionSubmit').textContent,'重试调整');
  assert.equal(h.node('instructionProposal').hidden,true);assert.equal(h.node('instructionReceipt').hidden,true);
});

test('automatic pending targets are picked only from a current preview and never resubmit completed clauses',async t=>{
  const h=await setup(t,{projects:[previewProject()]});installAutomaticPartialFlow(h);await h.run("openProject('a')");await proposePartial(h);
  assert.equal(h.node('instructionLocate').disabled,true);
  const p=h.records.get('a');p.jobs=[];p.outputs=[{...p.outputs[0],version:2,job_id:'current-after-partial'}];await h.run('poll()');
  assert.equal(h.node('instructionLocate').disabled,false);const before=h.calls.length;h.node('instructionLocate').click();
  assert.equal(h.calls.length,before);assert.equal(h.run('instructionTargetSelection.text'),remainingInstructionText);
  assert.equal(h.run('instructionTargetSelection.version'),2);assert.equal(h.run('instructionTargetSelection.source.output_id'),'current-after-partial');
  assert.equal(h.node('instructionInput').value,remainingInstructionText);
});

test('malformed automatic partial receipts stay uncertain and never trigger preview or drop original text',async t=>{
  for(const missing of ['pending','remaining_text','partial']){
    const h=await setup(t,{projects:[previewProject()]});const flow=installAutomaticPartialFlow(h);await h.run("openProject('a')");
    flow.afterApply=(call,p,receipt)=>{const broken=copy(receipt);delete broken[missing];return h.response({...p,instruction_result:broken});};
    await proposePartial(h);
    assert.equal(h.node('instructionSubmit').textContent,'核对并继续',missing);assert.equal(h.node('instructionInput').value,partialInstructionText,missing);
    assert.equal(h.calls.filter(call=>call.url.endsWith('/jobs')).length,0,missing);assert.equal(h.run('pendingInstructionPreview.confirmed'),false,missing);
  }
});

test('an old automatic partial replay keeps remaining text but never previews a later version',async t=>{
  const h=await setup(t,{projects:[previewProject()]});const flow=installAutomaticPartialFlow(h);await h.run("openProject('a')");
  flow.afterApply=()=>{throw new TypeError('测试回执丢失');};await proposePartial(h);
  h.records.get('a').version=3;h.records.get('a').plan.margin=.3;await h.run('applyInstructionAndPreview({preventDefault(){}})');
  assert.equal(h.node('instructionInput').value,remainingInstructionText);assert.equal(h.records.get('a').plan.exposure,.5);assert.equal(h.records.get('a').plan.margin,.3);
  assert.equal(h.calls.filter(call=>call.url.endsWith('/jobs')).length,0);assert.match(h.node('instructionReceiptSummary').textContent,/v2.*v3/);
});

test('light change details use server verified effective values instead of unset sparse overrides',async t=>{
  const h=await setup(t);await h.run("openProject('a')");
  const rows=h.run("instructionDiffRows({changes:[{path:'shot_adjustments.hero.lighting.key_power',before:null,after:1.3,effective_before:1,effective_after:1.3}]})");
  assert.equal(rows[0].before,'1');assert.equal(rows[0].after,'1.3');assert.equal(rows[0].scope,'镜头：白底主图');
  const plain=h.run("instructionDiffRows({changes:[{path:'shot_adjustments.hero.lighting.key_power',before:null,after:1.3}]})");assert.equal(plain[0].before,'未设置');
  assert.equal(h.run("completedInstructionLabel({ready:[{label:'让整个物体更好看',status:'ready'}],changes:[{path:'shot_adjustments.hero.lighting.key_power',before:null,after:1.3}]})"),'灯光');
});

test('material receipt uses exact measured before values without inventing changes to other fields or parts',async t=>{
  const h=await setup(t);await h.run("openProject('a')");
  const change={path:'object_material_overrides',
    before:[{object_name:'Body',material_name:'Paint',color:'#cc0000',metallic:.1,roughness:.4},{object_name:'Other',material_name:'Paint',roughness:.5}],
    after:[{object_name:'Body',material_name:'Paint',color:'#cc0000',metallic:.1,roughness:.22},{object_name:'Other',material_name:'Paint',roughness:.5}],
    effective_before:[{object_name:'Body',material_name:'Paint',roughness:.35,metallic:.3},{object_name:'Other',material_name:'Paint',roughness:.9}]};
  const rows=h.run('instructionDiffRows('+JSON.stringify({changes:[change]})+')');
  assert.equal(rows.length,1);assert.equal(rows[0].label,'粗糙度');assert.equal(rows[0].before,'0.35');assert.equal(rows[0].after,'0.22');assert.equal(rows[0].scope,'部件：Body');
  delete change.effective_before;const fallback=h.run('instructionDiffRows('+JSON.stringify({changes:[change]})+')');assert.equal(fallback[0].before,'0.4');
});

test('a preserved original shader never reports its unused preset as a material replacement',async t=>{
  const h=await setup(t);await h.run("openProject('a')");
  for(const path of ['material_overrides','object_material_overrides']){
    const identity=path==='material_overrides'?{name:'Paint'}:{object_name:'Body',material_name:'Paint'};
    const rows=h.run('instructionDiffRows('+JSON.stringify({changes:[{path,before:[],after:[{...identity,preset:'matte_plastic',preserve_original:true,roughness:.22}],effective_before:[{...identity,roughness:.35}]}]})+')');
    assert.equal(rows.length,1);assert.equal(rows[0].label,'粗糙度');assert.equal(rows[0].before,'0.35');assert.equal(rows[0].after,'0.22');
  }
  const explicit=h.run("instructionDiffRows({changes:[{path:'object_material_overrides',before:[],after:[{object_name:'Body',material_name:'Paint',preset:'rubber',preserve_original:false}]}]})");
  assert.equal(explicit.length,1);assert.equal(explicit[0].label,'材质预设');
});

test('a new typed requirement hides the previous partial result instead of mixing two operations',async t=>{
  const h=await setup(t,{projects:[previewProject()]});installAutomaticPartialFlow(h);await h.run("openProject('a')");await proposePartial(h);
  h.records.get('a').jobs=[];await h.run('poll()');input(h,'instructionInput','把背景改为浅灰色');
  assert.equal(h.node('instructionProposal').hidden,true);assert.equal(h.node('instructionReceipt').hidden,true);
  assert.equal(h.node('instructionSubmit').textContent,'应用并预览');assert.equal(h.node('instructionLocate').hidden,true);
  assert.equal(h.calls.filter(call=>call.url.endsWith('/instruct')).length,1);
});

test('a partial proposal shows a short pending notice and real ready items without saving or rendering',async t=>{
  const h=await setup(t,{projects:[previewProject()]});installPartialFlow(h);await h.run("openProject('a')");await proposePartial(h);await h.run('poll()');
  assert.equal(h.records.get('a').version,1);assert.equal(h.records.get('a').plan.exposure,0);assert.equal(h.records.get('a').history.length,0);
  assert.deepEqual(h.calls.filter(call=>call.method==='POST').map(call=>call.url),['/api/projects/a/instruct']);
  assert.equal(h.node('instructionInput').value,partialInstructionText);assert.equal(h.node('instructionSubmit').textContent,'先应用可完成项');
  assert.equal(h.node('instructionProposalSummary').textContent,'四个轮子仍需确认');assert.equal(h.node('instructionError').hidden,true);
  assert.equal(h.node('instructionReadyList').textContent,'灯光：曝光增加 0.5 EV');assert.equal(h.node('instructionProposalDisclosure').open,false);
  assert.match(h.node('instructionProposalDetails').textContent,/pasted__pCylinder19/);assert.match(h.node('instructionProposalDetails').textContent,/曝光.*0 EV.*0.5 EV/s);
  assert.equal(h.node('instructionProposal').querySelector('svg,script'),null);assert.equal(h.node('instructionReceipt').hidden,true);
  assert.equal(h.w.sessionStorage.getItem('wuying-instruction-attempts-v1'),null);
});

test('locating a pending part only opens the existing picker and never guesses or applies a target',async t=>{
  const h=await setup(t,{projects:[previewProject()]});installPartialFlow(h);await h.run("openProject('a')");await proposePartial(h);
  h.run("feedbackPoints=[{id:'old',x:.5,y:.5,hit:true,object_name:'Body'}]");const before=h.calls.length;
  h.node('instructionLocate').click();
  assert.equal(h.run('feedbackMode'),true);assert.equal(h.run('inspectorTab'),'material');assert.equal(h.run('feedbackPoints.length'),0);
  assert.equal(h.node('feedbackPanel').hidden,false);assert.equal(h.node('instructionInput').value,partialInstructionText);assert.equal(h.node('feedbackText'),null);
  assert.equal(h.calls.length,before);assert.equal(h.records.get('a').version,1);assert.match(h.node('feedbackAnnouncement').textContent,/点选待确认部位/);
});

test('only explicit partial confirmation applies changes and leaves the unresolved original clause',async t=>{
  const h=await setup(t,{projects:[previewProject()]});installPartialFlow(h);await h.run("openProject('a')");await proposePartial(h);
  await h.run('applyInstructionAndPreview({preventDefault(){}})');
  const initial=h.calls.find(call=>call.url.endsWith('/instruct')),confirmation=h.calls.find(call=>call.url.endsWith('/instruct/confirm'));
  assert.deepEqual(confirmation.body,{proposal_id:'proposal-1',expected_version:1});assert.notEqual(confirmation.headers['Idempotency-Key'],initial.headers['Idempotency-Key']);
  assert.equal(h.records.get('a').version,2);assert.equal(h.records.get('a').plan.exposure,.5);assert.equal(h.records.get('a').history.length,1);
  assert.equal(h.node('instructionInput').value,remainingInstructionText);assert.equal(h.node('instructionSubmit').textContent,'应用并预览');
  assert.equal(h.node('instructionReady').hidden,true);assert.equal(h.node('instructionProposalSummary').textContent,'四个轮子仍需确认');
  assert.equal(h.node('instructionLocate').disabled,true,'old preview cannot be used to locate the unresolved part');
  assert.deepEqual(h.calls.filter(call=>call.url.endsWith('/jobs')).map(call=>call.body.expected_version),[2]);
  assert.equal(h.calls.filter(call=>call.url.endsWith('/instruct')).length,1);assert.match(h.node('instructionReceiptDetails').textContent,/尚待确认的要求已保留/);
});

test('editing the request discards its proposal before another instruction can be sent',async t=>{
  const h=await setup(t,{projects:[previewProject()]});installPartialFlow(h);await h.run("openProject('a')");await proposePartial(h);
  input(h,'instructionInput','只调整灯光');assert.equal(h.node('instructionProposal').hidden,true);assert.equal(h.node('instructionSubmit').textContent,'应用并预览');
  input(h,'instructionInput',partialInstructionText);assert.equal(h.node('instructionProposal').hidden,true,'retyping the old text cannot revive its discarded proposal');
  await h.run('applyInstructionAndPreview({preventDefault(){}})');
  assert.equal(h.calls.filter(call=>call.url.endsWith('/instruct')).length,2);assert.equal(h.calls.filter(call=>call.url.endsWith('/instruct/confirm')).length,0);assert.equal(h.records.get('a').version,1);
});

test('a proposal cannot be confirmed after the plan or displayed source has changed',async t=>{
  for(const change of ['plan','source','shot']){
    const h=await setup(t,{projects:[previewProject()]});installPartialFlow(h);await h.run("openProject('a')");await proposePartial(h);
    if(change==='plan'){h.records.get('a').version=2;h.records.get('a').plan.margin=.3;await h.run('poll()');}
    if(change==='source'){h.records.get('a').outputs.unshift({...h.records.get('a').outputs[0],job_id:'new-render',created_at:10});await h.run('poll()');}
    if(change==='shot')h.run("activeShot='campaign';renderProject()");
    assert.equal(h.node('instructionSubmit').textContent,'重新分析要求',change);assert.equal(h.node('instructionReady').hidden,true,change);
    await h.run('applyInstructionAndPreview({preventDefault(){}})');
    assert.equal(h.calls.filter(call=>call.url.endsWith('/instruct/confirm')).length,0,change);assert.equal(h.calls.filter(call=>call.url.endsWith('/instruct')).length,change==='plan'?1:2,change);
    if(change==='plan')assert.equal(h.calls.filter(call=>call.url.endsWith('/jobs')).length,1);
    assert.equal(h.records.get('a').plan.exposure,0,change);
  }
});

test('a rejected stale confirmation refreshes context and requires reanalysis without a render',async t=>{
  const h=await setup(t,{projects:[previewProject()]});const flow=installPartialFlow(h);await h.run("openProject('a')");await proposePartial(h);
  flow.beforeConfirm=()=>{h.records.get('a').version=2;return h.response({error:'来源采样已变化，确认未应用。',error_code:'instruction_proposal_stale'},409);};
  await h.run('applyInstructionAndPreview({preventDefault(){}})');
  assert.equal(h.run('current.version'),2);assert.equal(h.node('instructionSubmit').textContent,'重新分析要求');assert.equal(h.node('instructionInput').value,partialInstructionText);
  assert.equal(h.calls.filter(call=>call.url.endsWith('/jobs')).length,0);assert.equal(h.records.get('a').plan.exposure,0);assert.equal(h.w.sessionStorage.getItem('wuying-instruction-attempts-v1'),null);
});

test('an unapplied proposal stays scoped through project switching and exiting',async t=>{
  const h=await setup(t,{projects:[previewProject(),project('b')]});installPartialFlow(h);await h.run("openProject('a')");await proposePartial(h);
  await h.run("openProject('b')");assert.equal(h.node('instructionProposal').hidden,true);assert.equal(h.node('instructionInput').value,'');
  await h.run("openProject('a')");assert.equal(h.node('instructionSubmit').textContent,'先应用可完成项');assert.equal(h.node('instructionInput').value,partialInstructionText);
  await h.run('exitProject()');assert.equal(h.node('instructionProposal').hidden,true);assert.equal(h.node('instructionLocate').hidden,true);
  await h.run("openProject('a')");assert.equal(h.node('instructionSubmit').textContent,'先应用可完成项');
  assert.equal(h.calls.filter(call=>call.method==='POST').length,1);assert.equal(h.records.get('a').plan.exposure,0);
});

test('deleting a project removes its pending proposal and cannot expose its draft on home',async t=>{
  const h=await setup(t,{projects:[previewProject()]});installPartialFlow(h);await h.run("openProject('a')");await proposePartial(h);
  const removing=h.run("deleteProject('a')");await tick();h.node('deleteProjectDialog').querySelector('[data-choice="delete"]').click();assert.equal(await removing,true);
  assert.equal(h.run("instructionGuidance.has('a') || instructionDrafts.has('a')"),false);assert.equal(h.node('instructionProposal').hidden,true);assert.equal(h.node('instructionInput').value,'');
  assert.equal(h.calls.filter(call=>call.url.endsWith('/instruct/confirm')).length,0);
});

test('a lost confirmation response restores its exact endpoint, body and key without replaying relative changes',async t=>{
  const first=await setup(t,{projects:[previewProject()]});const flow=installPartialFlow(first);await first.run("openProject('a')");await proposePartial(first);
  flow.afterConfirm=()=>{throw new TypeError('测试确认回执丢失');};await first.run('applyInstructionAndPreview({preventDefault(){}})');
  const journal=first.w.sessionStorage.getItem('wuying-instruction-attempts-v1'),saved=JSON.parse(journal).attempts[0];
  assert.equal(saved.kind,'confirm');assert.deepEqual(saved.body,{proposal_id:'proposal-1',expected_version:1});assert.equal(saved.confirmed,false);
  const restored=await setup(t,{projects:[copy(first.records.get('a'))],journal});installPartialFlow(restored,flow);await restored.run("openProject('a')");
  assert.equal(restored.calls.filter(call=>call.method==='POST').length,0);assert.equal(restored.node('instructionSubmit').textContent,'核对并继续');
  await restored.run('applyInstructionAndPreview({preventDefault(){}})');
  const retry=restored.calls.find(call=>call.url.endsWith('/instruct/confirm'));assert.equal(retry.headers['Idempotency-Key'],saved.instructionKey);assert.deepEqual(retry.body,saved.body);
  assert.equal(restored.calls.filter(call=>call.url.endsWith('/instruct')).length,0);assert.equal(restored.records.get('a').plan.exposure,.5);assert.equal(restored.records.get('a').version,2);
  assert.equal(restored.node('instructionInput').value,remainingInstructionText);assert.equal(restored.w.sessionStorage.getItem('wuying-instruction-attempts-v1'),null);
});

test('a malformed partial remainder cannot turn the completed full request into another adjustment',async t=>{
  const h=await setup(t,{projects:[previewProject()]});const flow=installPartialFlow(h);await h.run("openProject('a')");await proposePartial(h);
  flow.afterConfirm=(_call,p,receipt)=>h.response({...p,instruction_result:{...receipt,remaining_text:partialInstructionText}});
  await h.run('applyInstructionAndPreview({preventDefault(){}})');
  assert.equal(h.node('instructionSubmit').textContent,'核对并继续');assert.equal(h.calls.filter(call=>call.url.endsWith('/jobs')).length,0);
  await h.run('applyInstructionAndPreview({preventDefault(){}})');
  const confirmations=h.calls.filter(call=>call.url.endsWith('/instruct/confirm'));assert.equal(confirmations.length,2);assert.equal(confirmations[0].headers['Idempotency-Key'],confirmations[1].headers['Idempotency-Key']);
  assert.equal(h.records.get('a').plan.exposure,.5);assert.equal(h.records.get('a').version,2);assert.equal(h.node('instructionInput').value,remainingInstructionText);assert.equal(h.calls.filter(call=>call.url.endsWith('/instruct')).length,1);
});

test('partial preview-only retry survives exit and session restoration while keeping only the remaining clause',async t=>{
  const first=await setup(t,{projects:[previewProject()]});const flow=installPartialFlow(first);await first.run("openProject('a')");await proposePartial(first);
  flow.beforeJob=()=>first.response({error:'测试预览未能启动'},503);await first.run('applyInstructionAndPreview({preventDefault(){}})');
  assert.equal(first.node('instructionSubmit').textContent,'重试预览');assert.equal(first.node('instructionInput').value,remainingInstructionText);
  const journal=first.w.sessionStorage.getItem('wuying-instruction-attempts-v1'),saved=JSON.parse(journal).attempts[0];assert.equal(saved.remainingText,remainingInstructionText);
  await first.run('exitProject()');await first.run("openProject('a')");assert.equal(first.node('instructionSubmit').textContent,'重试预览');assert.equal(first.node('instructionInput').value,remainingInstructionText);
  const restored=await setup(t,{projects:[copy(first.records.get('a'))],journal});await restored.run("openProject('a')");await restored.run('applyInstructionAndPreview({preventDefault(){}})');
  assert.equal(restored.calls.filter(call=>call.url.includes('/instruct')).length,0);const preview=restored.calls.find(call=>call.url.endsWith('/jobs'));
  assert.equal(preview.headers['Idempotency-Key'],saved.previewKey);assert.equal(preview.body.expected_version,2);assert.equal(restored.records.get('a').plan.exposure,.5);assert.equal(restored.node('instructionInput').value,remainingInstructionText);
});

test('switching projects after partial application never restores the already-completed lighting clause',async t=>{
  const h=await setup(t,{projects:[previewProject(),project('b')]});const flow=installPartialFlow(h);await h.run("openProject('a')");await proposePartial(h);
  flow.beforeJob=()=>h.response({error:'预览启动失败'},503);await h.run('applyInstructionAndPreview({preventDefault(){}})');
  await h.run("openProject('b')");await h.run("openProject('a')");
  assert.equal(h.node('instructionInput').value,remainingInstructionText);assert.equal(h.node('instructionSubmit').textContent,'应用并预览');
  assert.equal(h.records.get('a').plan.exposure,.5);assert.equal(h.calls.filter(call=>call.url.endsWith('/instruct/confirm')).length,1);
});

test('an older confirmed partial receipt retains unresolved text and never queues a preview of another version',async t=>{
  const h=await setup(t,{projects:[previewProject()]});const flow=installPartialFlow(h);await h.run("openProject('a')");await proposePartial(h);
  flow.afterConfirm=()=>{throw new TypeError('测试确认回执丢失');};await h.run('applyInstructionAndPreview({preventDefault(){}})');h.records.get('a').version=3;h.records.get('a').plan.margin=.3;
  await h.run('applyInstructionAndPreview({preventDefault(){}})');
  assert.equal(h.node('instructionInput').value,remainingInstructionText);assert.equal(h.records.get('a').plan.exposure,.5);assert.equal(h.records.get('a').plan.margin,.3);
  assert.equal(h.calls.filter(call=>call.url.endsWith('/jobs')).length,0);assert.match(h.node('instructionReceiptSummary').textContent,/v2.*v3/);
});

test('fully unresolved targets use a compact clarification with escaped optional diagnostics',async t=>{
  const h=await setup(t,{projects:[previewProject()]});await h.run("openProject('a')");input(h,'instructionInput',remainingInstructionText);
  h.intercept=call=>call.url.endsWith('/instruct')?h.response({error:'pasted__pCylinder19 的材质不能确定。<script>bad()</script>',error_code:'instruction_targets_unresolved',pending:[{label:'四个轮子',quote:remainingInstructionText,reason:'请定位实际轮胎本体。'}],remaining_text:remainingInstructionText},400):undefined;
  await h.run('applyInstructionAndPreview({preventDefault(){}})');
  assert.equal(h.node('instructionError').hidden,true);assert.equal(h.node('instructionProposalSummary').textContent,'四个轮子仍需确认');assert.equal(h.node('instructionReady').hidden,true);
  assert.equal(h.node('instructionSubmit').textContent,'重新分析要求');assert.equal(h.node('instructionLocate').disabled,false);assert.equal(h.node('instructionProposalDisclosure').open,false);
  assert.match(h.node('instructionProposalDetails').textContent,/pasted__pCylinder19/);assert.equal(h.node('instructionProposal').querySelector('script'),null);
  assert.equal(h.records.get('a').version,1);assert.equal(h.calls.filter(call=>call.url.endsWith('/jobs')).length,0);
});

test('a same-version saved proposal is restored after reload and confirms without another model request',async t=>{
  const first=await setup(t,{projects:[previewProject()]});const flow=installPartialFlow(first);await first.run("openProject('a')");await proposePartial(first);
  const restored=await setup(t,{projects:[copy(first.records.get('a'))]});installPartialFlow(restored,flow);await restored.run("openProject('a')");
  assert.equal(restored.node('instructionInput').value,partialInstructionText);assert.equal(restored.node('instructionSubmit').textContent,'先应用可完成项');
  assert.equal(restored.calls.filter(call=>call.method==='POST').length,0);await restored.run('applyInstructionAndPreview({preventDefault(){}})');
  assert.equal(restored.calls.filter(call=>call.url.endsWith('/instruct')).length,0);assert.equal(restored.calls.filter(call=>call.url.endsWith('/instruct/confirm')).length,1);assert.equal(restored.records.get('a').plan.exposure,.5);
});

test('saved proposals never restore over changed context, an existing draft, or an uncertain request',async t=>{
  const first=await setup(t,{projects:[previewProject()]});installPartialFlow(first);await first.run("openProject('a')");await proposePartial(first);
  for(const guard of ['version','source','draft','uncertain']){
    const p=copy(first.records.get('a'));if(guard==='version')p.version=2;if(guard==='source')p.outputs[0].job_id='different-output';
    const h=await setup(t,{projects:[p],journal:guard==='uncertain'?{schema:1,attempts:[journalAttempt()]}:null});
    if(guard==='draft')h.run("instructionDrafts.set('a','另一个新要求')");await h.run("openProject('a')");
    assert.equal(h.node('instructionProposal').hidden,true,guard);assert.notEqual(h.node('instructionSubmit').textContent,'先应用可完成项',guard);
    assert.equal(h.node('instructionInput').value,guard==='draft'?'另一个新要求':guard==='uncertain'?'待核对指令 0':'',guard);assert.equal(h.calls.filter(call=>call.method==='POST').length,0,guard);
  }
});

test('reloading a completed partial edit restores only its current remaining clause and respects newer drafts',async t=>{
  const first=await setup(t,{projects:[previewProject()]});installPartialFlow(first);await first.run("openProject('a')");await proposePartial(first);await first.run('applyInstructionAndPreview({preventDefault(){}})');
  const saved=copy(first.records.get('a'));saved.jobs=[];saved.outputs=[{...saved.outputs[0],version:2,job_id:'render-after-partial'}];
  for(const guard of ['current','newer','draft','full-text','uncertain']){
    const p=copy(saved);if(guard==='newer')p.version=3;if(guard==='full-text')p.instruction_result.remaining_text=partialInstructionText;
    const h=await setup(t,{projects:[p],journal:guard==='uncertain'?{schema:1,attempts:[journalAttempt()]}:null});
    if(guard==='draft')h.run("instructionDrafts.set('a','另一个要求')");await h.run("openProject('a')");
    assert.equal(h.node('instructionInput').value,guard==='current'?remainingInstructionText:guard==='draft'?'另一个要求':guard==='uncertain'?'待核对指令 0':'',guard);
    assert.notEqual(h.node('instructionInput').value,partialInstructionText,guard);assert.equal(h.calls.filter(call=>call.method==='POST').length,0,guard);
    if(guard==='current'){assert.equal(h.node('instructionProposalSummary').textContent,'四个轮子仍需确认');assert.equal(h.node('instructionLocate').disabled,false);}
  }
});

test('unsupported requirements hide part location while mixed clarification still opens only the picker',async t=>{
  const h=await setup(t,{projects:[previewProject()]});const flow=installPartialFlow(h);await h.run("openProject('a')");await proposePartial(h);
  h.run("instructionGuidance.get('a').pending=[{label:'真实倒角',quote:'增加真实倒角',status:'unsupported',reason:'当前工具暂不支持几何倒角。'}];renderInstruction()");
  assert.equal(h.node('instructionProposalSummary').textContent,'真实倒角暂不支持');assert.equal(h.node('instructionLocate').hidden,true);
  const before=h.calls.length;h.run('locatePendingInstruction()');assert.equal(h.run('feedbackMode'),false);assert.equal(h.calls.length,before);
  h.run("instructionGuidance.get('a').pending.push({label:'四个轮子',quote:'轮子改成黑色橡胶',status:'needs_clarification'});renderInstruction()");
  assert.equal(h.node('instructionLocate').hidden,false);assert.equal(h.node('instructionProposalSummary').textContent,'四个轮子仍需确认；另有要求暂不支持');
  h.node('instructionLocate').click();assert.equal(h.run('feedbackMode'),true);assert.equal(h.run('feedbackPoints.length'),0);assert.equal(h.calls.length,before);assert.equal(flow.confirmed.size,0);
});

test('unresolved photography parameters never send the user to material picking',async t=>{
  const h=await setup(t,{projects:[previewProject()]});await h.run("openProject('a')");
  h.intercept=call=>call.url.endsWith('/instruct')?h.response({error:'镜头参数未完整返回。',error_code:'instruction_targets_unresolved',
    pending:[{id:'camera',label:'镜头',quote:'焦距设为85mm',status:'needs_clarification',action:'clarify_parameters'}],remaining_text:'焦距设为85mm'},400):undefined;
  input(h,'instructionInput','焦距设为85mm');await h.run('applyInstructionAndPreview({preventDefault(){}})');
  assert.equal(h.node('instructionProposalSummary').textContent,'镜头仍需确认');assert.equal(h.node('instructionLocate').hidden,true);
  assert.match(h.node('instructionProposalDetails').textContent,/补充或重新提交该项参数要求/);
  assert.doesNotMatch(h.node('instructionProposalDetails').textContent,/定位相关部位/);
  const before=h.calls.length;h.run('locatePendingInstruction()');assert.equal(h.run('feedbackMode'),false);assert.notEqual(h.run('inspectorTab'),'material');assert.equal(h.calls.length,before);
  assert.equal(h.node('instructionInput').value,'焦距设为85mm');assert.equal(h.records.get('a').version,1);assert.equal(h.records.get('a').history.length,0);
});

test('mixed parameter and part clarification offers location only for the actual part',async t=>{
  const h=await setup(t,{projects:[previewProject()]});const flow=installPartialFlow(h);await h.run("openProject('a')");await proposePartial(h);
  h.run("instructionGuidance.get('a').pending=[{id:'camera',label:'镜头',quote:'焦距设为85mm',status:'needs_clarification',action:'clarify_parameters'},{id:'wheels',label:'四个轮子',quote:'四个轮子改成黑色橡胶',status:'needs_clarification',action:'locate_part'}];renderInstruction()");
  assert.equal(h.node('instructionLocate').hidden,false);
  assert.equal(h.run("locatableInstructionPending(currentInstructionGuidance()).length"),1);
  assert.equal(h.run("locatableInstructionPending(currentInstructionGuidance())[0].id"),'wheels');
  const before=h.calls.length;h.node('instructionLocate').click();
  assert.equal(h.run('feedbackMode'),true);assert.equal(h.run('inspectorTab'),'material');assert.equal(h.run('feedbackPoints.length'),0);
  assert.equal(h.calls.length,before);assert.equal(flow.confirmed.size,0);assert.equal(h.records.get('a').version,1);
});

async function prepareInstructionTargetRecovery(h,text='四个轮子都要是黑色橡胶') {
  const previous=h.intercept;
  h.intercept=call=>{
    if(call.url.endsWith('/feedback/pick'))return h.response({hits:call.body.points.map(point=>({...point,hit:true,object_name:'pasted__pCylinder22',material_name:'default',material_info:{editable:true}}))});
    if(call.url.endsWith('/instruct') && !call.body.target_points)return h.response({error:'需要定位轮胎表面。',error_code:'instruction_targets_unresolved',pending:[{id:'wheels',label:'四个轮子',quote:text,status:'needs_clarification',action:'locate_part',reason:'点选轮胎，帮助区分支架与轮胎。'}],remaining_text:text},400);
    return previous?.(call);
  };
  input(h,'instructionInput',text);await h.run('applyInstructionAndPreview({preventDefault(){}})');h.node('instructionLocate').click();
}

test('target recovery carries the original request through the normal instruction path with only real coordinates',async t=>{
  const h=await setup(t,{projects:[previewProject()]});await h.run("openProject('a')");await prepareInstructionTargetRecovery(h);
  assert.equal(h.node('instructionError').hidden,true);assert.equal(h.node('instructionTargetControls').hidden,false);assert.equal(h.node('manualPartControls').hidden,true);assert.equal(h.node('materialAgentPanel').hidden,true);
  assert.equal(h.node('instructionTargetRequest').textContent,'四个轮子都要是黑色橡胶');assert.match(h.node('materialIntro').textContent,/点选一个.*帮助识别同类部件/);
  assert.equal(h.node('instructionTargetContinue').disabled,true);assert.equal(h.node('instructionSubmit').disabled,true);assert.equal(h.records.get('a').version,1);
  await h.run('addFeedbackPoint({x:.23,y:.85})');
  assert.equal(h.node('instructionTargetContinue').disabled,false);assert.equal(h.node('partMaterialSubmit').disabled,true);assert.equal(h.node('feedbackSubmit'),null);assert.equal(h.node('materialAgentSubmit').disabled,true);
  const before=h.calls.length;await h.run('submitPartMaterial(false)');await h.run('submitMaterialAgent({preventDefault(){}})');assert.equal(h.calls.length,before,'an example cannot silently become a local edit');
  await h.run('continueInstructionTargets()');
  const calls=h.calls.filter(call=>call.url.endsWith('/instruct')),body=calls[1].body;
  assert.equal(calls.length,2);assert.equal(body.text,'四个轮子都要是黑色橡胶');assert.deepEqual(body.source,calls[0].body.source);assert.equal(body.expected_version,1);
  assert.equal(body.target_points.length,1);assert.deepEqual(Object.keys(body.target_points[0]).sort(),['id','x','y']);assert.equal(body.target_points[0].x,.23);assert.equal(body.target_points[0].y,.85);
  assert.equal(h.calls.filter(call=>/\/feedback$|\/materials\/selection\/apply$/.test(call.url)).length,0);assert.equal(h.run('inspectorTab'),'review');assert.equal(h.run('instructionTargetSelection'),null);assert.equal(h.node('instructionInput').value,'');
});

test('pending or missed target examples cannot submit a material instruction',async t=>{
  const h=await setup(t,{projects:[previewProject()]});await h.run("openProject('a')");await prepareInstructionTargetRecovery(h);
  const prior=h.intercept,gate=deferred();h.intercept=call=>call.url.endsWith('/feedback/pick')?gate.promise:prior(call);
  const picking=h.run('addFeedbackPoint({x:.01,y:.01})');await tick();assert.equal(h.node('instructionTargetContinue').disabled,true);
  const before=h.calls.length;await h.run('continueInstructionTargets()');assert.equal(h.calls.length,before);
  const point=h.calls.find(call=>call.url.endsWith('/feedback/pick')).body.points[0];gate.resolve(h.response({hits:[{...point,hit:false,message:'背景'}]}));await picking;
  assert.equal(h.node('instructionTargetContinue').disabled,true);await h.run('applyInstructionAndPreview({preventDefault(){}})');assert.equal(h.calls.length,before);assert.equal(h.records.get('a').version,1);
});

test('clearing examples keeps recovery active and cancellation restores ordinary local editing without losing the request',async t=>{
  const h=await setup(t,{projects:[previewProject()]});await h.run("openProject('a')");await prepareInstructionTargetRecovery(h);await h.run('addFeedbackPoint({x:.23,y:.85})');
  h.node('feedbackClear').click();assert.equal(h.run('feedbackPoints.length'),0);assert.equal(h.node('instructionTargetControls').hidden,false);assert.equal(h.node('instructionTargetContinue').disabled,true);
  assert.equal(h.node('instructionInput').value,'四个轮子都要是黑色橡胶');const before=h.calls.length;h.node('instructionTargetCancel').click();
  assert.equal(h.calls.length,before);assert.equal(h.run('instructionTargetSelection'),null);assert.equal(h.node('instructionTargetControls').hidden,true);assert.equal(h.node('manualPartControls').hidden,false);assert.equal(h.node('materialAgentPanel').hidden,false);assert.equal(h.run('inspectorTab'),'review');assert.equal(h.w.document.activeElement.id,'instructionInput');assert.equal(h.node('instructionInput').value,'四个轮子都要是黑色橡胶');
});

test('changed text, image, version, camera or parameter draft invalidates target examples without erasing the request',async t=>{
  for(const change of ['text','image','version','shot','draft']){
    const h=await setup(t,{projects:[previewProject()]});await h.run("openProject('a')");await prepareInstructionTargetRecovery(h);await h.run('addFeedbackPoint({x:.23,y:.85})');const before=h.calls.length;
    if(change==='text')input(h,'instructionInput','四个轮子都要是灰色橡胶');
    if(change==='image')h.run("current.outputs[0].job_id='new-image';renderProject()");
    if(change==='version')h.run('current.version++;renderProject()');
    if(change==='shot')h.run("activeShot='side';renderProject()");
    if(change==='draft')input(h,'margin','24');
    assert.equal(h.run('instructionTargetSelection'),null,change);assert.equal(h.run('feedbackPoints.length'),0,change);assert.equal(h.node('instructionTargetControls').hidden,true,change);
    assert.equal(h.node('instructionInput').value,change==='text'?'四个轮子都要是灰色橡胶':'四个轮子都要是黑色橡胶',change);
    await h.run('continueInstructionTargets()');assert.equal(h.calls.length,before,change);
  }
});

test('late target picks cannot restore examples after editing the request or exiting the project',async t=>{
  for(const change of ['text','exit']){
    const h=await setup(t,{projects:[previewProject()]});await h.run("openProject('a')");await prepareInstructionTargetRecovery(h);
    const prior=h.intercept,gate=deferred();h.intercept=call=>call.url.endsWith('/feedback/pick')?gate.promise:prior(call);
    const picking=h.run('addFeedbackPoint({x:.23,y:.85})');await tick();const point=h.calls.find(call=>call.url.endsWith('/feedback/pick')).body.points[0];
    if(change==='text')input(h,'instructionInput','轮胎改成灰色橡胶');else await h.run('exitProject()');
    gate.resolve(h.response({hits:[{...point,hit:true,object_name:'Wheel',material_name:'rubber'}]}));await picking;
    assert.equal(h.run('instructionTargetSelection'),null,change);assert.equal(h.run('feedbackPoints.length'),0,change);assert.equal(h.node('instructionTargetControls').hidden,true,change);
    if(change==='exit'){await h.run("openProject('a')");assert.equal(h.node('instructionInput').value,'四个轮子都要是黑色橡胶');assert.equal(h.run('instructionTargetSelection'),null);}
  }
});

test('failed seeded recognition retains examples and the exact remaining request for a retry',async t=>{
  const h=await setup(t,{projects:[previewProject()]});await h.run("openProject('a')");await prepareInstructionTargetRecovery(h);await h.run('addFeedbackPoint({x:.23,y:.85})');
  const prior=h.intercept;let fail=true;h.intercept=call=>call.url.endsWith('/instruct') && call.body.target_points && fail?h.response({error:'其余轮胎仍需定位。',error_code:'instruction_targets_unresolved',pending:[{id:'wheels',label:'四个轮子',quote:'四个轮子都要是黑色橡胶',status:'needs_clarification',action:'locate_part'}]},400):prior(call);
  await h.run('continueInstructionTargets()');assert.equal(h.records.get('a').version,1);assert.equal(h.node('instructionInput').value,'四个轮子都要是黑色橡胶');assert.equal(h.run('feedbackPoints.length'),1);assert.equal(h.node('instructionSubmit').textContent,'用选点继续识别');assert.equal(h.node('instructionError').hidden,true);
  fail=false;await h.run('applyInstructionAndPreview({preventDefault(){}})');const calls=h.calls.filter(call=>call.url.endsWith('/instruct') && call.body.target_points);
  assert.equal(calls.length,2);assert.deepEqual(calls[0].body,calls[1].body);assert.notEqual(calls[0].headers['Idempotency-Key'],calls[1].headers['Idempotency-Key'],'a rejected request can be re-analyzed');assert.equal(h.records.get('a').version,2);
});

test('a lost seeded-instruction response restores the same coordinates, source and request key without another pick',async t=>{
  const first=await setup(t,{projects:[previewProject()]});await first.run("openProject('a')");await prepareInstructionTargetRecovery(first);await first.run('addFeedbackPoint({x:.23,y:.85})');first.afterInstruction=()=>{throw new TypeError('测试响应丢失');};
  await first.run('continueInstructionTargets()');const original=first.calls.find(call=>call.url.endsWith('/instruct') && call.body.target_points),journal=first.w.sessionStorage.getItem('wuying-instruction-attempts-v1');
  assert.equal(first.node('instructionSubmit').textContent,'核对并继续');assert.equal(first.node('instructionLocate').disabled,true);
  const restored=await setup(t,{projects:[copy(first.records.get('a'))],journal});restored.instructions=new Map(first.instructions);await restored.run("openProject('a')");assert.equal(restored.calls.filter(call=>call.method==='POST').length,0);
  await restored.run('applyInstructionAndPreview({preventDefault(){}})');const retry=restored.calls.find(call=>call.url.endsWith('/instruct'));
  assert.deepEqual(retry.body,original.body);assert.equal(retry.headers['Idempotency-Key'],original.headers['Idempotency-Key']);assert.equal(restored.calls.filter(call=>call.url.endsWith('/feedback/pick')).length,0);assert.equal(restored.records.get('a').version,2);assert.equal(restored.w.sessionStorage.getItem('wuying-instruction-attempts-v1'),null);
});

test('invalid target-point journal records never become actionable requests',async t=>{
  const valid=journalAttempt();valid.body.source={output_id:'render-a',shot_id:'hero',quality:'preview',version:1};valid.body.target_points=[{id:'point-a',x:.23,y:.85}];
  const mutations=[item=>delete item.body.source,item=>item.body.source.version=2,item=>item.body.target_points=[],item=>item.body.target_points=Array.from({length:13},(_,index)=>({id:'point-'+index,x:.2,y:.8})),item=>item.body.target_points[0].x=1.01,item=>item.body.target_points[0].x='0.2',item=>item.body.target_points[0].x=null,item=>item.body.target_points[0].id='p'.repeat(65),item=>item.body.target_points[0].object_name='untrusted-wheel',item=>item.body.target_points.push(copy(item.body.target_points[0]))];
  const invalid=mutations.map((mutate,index)=>{const item=copy(valid);item.instructionKey+='-'+index;item.previewKey+='-'+index;mutate(item);return item;});
  const h=await setup(t,{projects:[previewProject()],journal:{schema:1,attempts:invalid}});await h.run("openProject('a')");assert.equal(h.run('instructionAttempts.size'),0);assert.equal(h.node('instructionInput').value,'');assert.equal(h.calls.filter(call=>call.method==='POST').length,0);
});

test('adding target examples to a partial proposal re-analyzes the original request instead of confirming the old subset',async t=>{
  const h=await setup(t,{projects:[previewProject()]});installPartialFlow(h);await h.run("openProject('a')");await proposePartial(h);const prior=h.intercept;
  h.intercept=call=>call.url.endsWith('/feedback/pick')?h.response({hits:call.body.points.map(point=>({...point,hit:true,object_name:'Wheel',material_name:'rubber'}))}):prior(call);
  h.node('instructionLocate').click();await h.run('addFeedbackPoint({x:.23,y:.85})');await h.run('continueInstructionTargets()');
  const calls=h.calls.filter(call=>call.url.endsWith('/instruct'));assert.equal(calls.length,2);assert.equal(calls[1].body.text,partialInstructionText);assert.equal(calls[1].body.target_points.length,1);assert.equal(h.calls.filter(call=>call.url.endsWith('/instruct/confirm')).length,0);assert.equal(h.records.get('a').version,1);assert.equal(h.node('instructionSubmit').textContent,'先应用可完成项');assert.equal(h.run('instructionTargetSelection'),null);
});

test('material recognition reports one honest waiting state while preserving the request until a result arrives',async t=>{
  const h=await setup(t,{projects:[previewProject()]});await h.run("openProject('a')");input(h,'instructionInput','四个轮子都要是黑色橡胶');const gate=deferred();h.intercept=call=>call.url.endsWith('/instruct')?gate.promise:undefined;
  const submitting=h.run('applyInstructionAndPreview({preventDefault(){}})');await tick();assert.equal(h.node('instructionSubmit').textContent,'正在理解要求并准备修改…');assert.equal(h.node('instructionInput').value,'四个轮子都要是黑色橡胶');assert.equal(h.records.get('a').version,1);assert.equal(h.calls.filter(call=>call.url.endsWith('/jobs')).length,0);
  gate.resolve(h.response({error:'需要定位。',error_code:'instruction_targets_unresolved',pending:[{label:'四个轮子',quote:'四个轮子都要是黑色橡胶',status:'needs_clarification',action:'locate_part'}]},400));await submitting;
  assert.equal(h.node('instructionSubmit').textContent,'重新分析要求');assert.equal(h.node('instructionLocate').disabled,false);assert.equal(h.node('instructionError').hidden,true);
});

test('a submitted seeded request blocks cancellation and exit until its result is resolved',async t=>{
  const h=await setup(t,{projects:[previewProject()]});await h.run("openProject('a')");await prepareInstructionTargetRecovery(h);await h.run('addFeedbackPoint({x:.23,y:.85})');
  const prior=h.intercept,gate=deferred();h.intercept=call=>call.url.endsWith('/instruct') && call.body.target_points?gate.promise:prior(call);
  const submitting=h.run('continueInstructionTargets()');await tick();
  assert.equal(h.node('instructionTargetCancel').disabled,true);assert.equal(h.node('feedbackToggle').disabled,true);assert.equal(h.node('exitProjectButton').disabled,true);assert.equal(h.node('instructionInput').disabled,true);
  h.node('instructionTargetCancel').click();assert.equal(h.run('feedbackPoints.length'),1);assert.equal(await h.run('exitProject()'),false);assert.equal(h.run('current.id'),'a');
  gate.resolve(h.response({error:'定位证据尚不完整。',error_code:'instruction_targets_unresolved',pending:[{label:'四个轮子',quote:'四个轮子都要是黑色橡胶',status:'needs_clarification',action:'locate_part'}]},400));await submitting;
  assert.equal(h.node('exitProjectButton').disabled,false);assert.equal(h.node('instructionInput').disabled,false);assert.equal(h.records.get('a').version,1);assert.equal(h.node('instructionInput').value,'四个轮子都要是黑色橡胶');
});

test('exit waits for submitted changes but background-only work keeps it enabled',async t=>{
  const h=await setup(t);await h.run("openProject('a')");input(h,'instructionInput','亮一点');const gate=deferred();
  h.intercept=call=>call.url.endsWith('/instruct')?gate.promise:undefined;
  const applying=h.run('applyInstructionAndPreview({preventDefault(){}})');await tick();assert.equal(h.node('exitProjectButton').disabled,true);
  assert.equal(await h.run('exitProject()'),false);assert.equal(h.run('current.id'),'a');
  gate.resolve(h.response({error:'请重试'},503));await applying;assert.equal(h.node('exitProjectButton').disabled,false);
});

test('home polling updates background project progress without opening a project',async t=>{
  const h=await setup(t);h.records.get('a').initial_preview={status:'running',job_id:'initial-a'};
  await h.run('poll()');assert.equal(h.run('current'),null);assert.match(h.node('projectList').querySelector('[data-project="a"]').textContent,/白底主图生成中/);
  assert.equal(h.calls.filter(call=>call.method!=='GET').length,0);
});

test('initial white hero progress and completion use backend state without a frontend job submission',async t=>{
  const a=project('a');a.status='analyzing';a.analysis=null;a.initial_preview={status:'pending'};a.jobs=[{id:'analyze-a',kind:'analyze',status:'running',stage:'读取模型',created_at:1}];
  const h=await setup(t,{projects:[a]});await h.run("openProject('a')");assert.match(h.node('renderEmpty').textContent,/完成后自动生成白底主图/);
  const record=h.records.get('a');record.analysis=project().analysis;record.status='ready';record.initial_preview={status:'queued',job_id:'initial-a'};
  record.jobs=[{id:'initial-a',kind:'preview',origin:'initial_preview',status:'queued',shot_ids:['hero'],stage:'等待执行',created_at:2},...record.jobs.map(job=>({...job,status:'completed'}))];
  await h.run('poll()');assert.match(h.node('renderEmpty').textContent,/白底主图已排队/);assert.match(h.node('jobPanel').textContent,/白底主图/);
  record.initial_preview.status='running';record.jobs[0].status='running';record.jobs[0].stage='正在采样';await h.run('poll()');assert.match(h.node('renderEmpty').textContent,/正在生成白底主图/);
  record.initial_preview.status='completed';record.jobs[0].status='completed';record.outputs=copy(previewProject().outputs);await h.run('poll()');assert.equal(h.node('renderImage').hidden,false);assert.equal(h.node('renderEmpty').hidden,true);
  assert.equal(h.calls.filter(call=>call.url.endsWith('/jobs') && call.method==='POST').length,0);assert.equal(h.node('uploadSubmit').textContent,'上传并生成主图');
});

test('initial render scheduling errors remain actionable and mobile delete targets the selected project',async t=>{
  const a=project('a');a.initial_preview={status:'failed',error:'白底主图未排队：请检查渲染环境。'};
  const h=await setup(t,{projects:[a,project('b')]});await h.run("openProject('a')");assert.equal(h.node('workspaceHint').hidden,false);assert.match(h.node('workflowDescription').textContent,/检查渲染环境/);
  assert.equal(h.node('mobileDeleteProject').hidden,false);h.node('mobileDeleteProject').click();await tick();
  assert.match(h.node('deleteProjectDialog').textContent,/测试项目 a/);h.node('deleteProjectDialog').querySelector('[data-choice="cancel"]').click();await tick();
  assert.equal(h.calls.filter(call=>call.method==='DELETE').length,0);assert.equal(h.run('current.id'),'a');
  h.w.document.querySelector('.brand').click();await tick();assert.equal(h.run('current'),null);
});

test('upload opens the automatic white hero flow and does not submit a second rendering job',async t=>{
  const h=await setup(t,{projects:[]}),uploaded=project('uploaded');uploaded.status='analyzing';uploaded.analysis=null;uploaded.initial_preview={status:'pending'};uploaded.plan.style='dark';
  uploaded.jobs=[{id:'upload-analysis',kind:'analyze',status:'queued',stage:'等待分析',created_at:2}];
  h.intercept=call=>{if(call.url==='/api/projects' && call.method==='POST'){assert.ok(call.body instanceof h.w.FormData);h.records.set(uploaded.id,copy(uploaded));return h.response(uploaded,201);}};
  await h.run('showUpload()');h.w.modelUpload=[new h.w.File(['test model'],'product.glb')];h.run('addFiles(modelUpload)');
  await h.node('uploadForm').onsubmit({preventDefault(){}});
  assert.equal(h.node('uploadDialog').open,false);assert.equal(h.run('current.id'),'uploaded');assert.equal(h.run('activeShot'),'hero');
  assert.equal(h.run('current.plan.style'),'dark');assert.equal(h.node('shotTitle').textContent,'白底主图');assert.match(h.node('renderEmpty').textContent,/完成后自动生成白底主图/);
  assert.match(h.node('toast').textContent,/分析完成后会自动生成白底主图/);assert.equal(h.node('uploadSubmit').textContent,'上传并生成主图');
  assert.deepEqual(h.calls.filter(call=>call.method==='POST').map(call=>call.url),['/api/projects']);
});

test('an interrupted analysis never presents a pending initial hero as still being prepared',async t=>{
  for(const status of ['failed','cancelled','interrupted']){
    const a=project('a');a.analysis=null;a.status=status;a.initial_preview={status:'pending'};
    a.jobs=[{id:'analysis-a',kind:'analyze',status,stage:'分析已停止',created_at:1}];
    const h=await setup(t,{projects:[a]});await h.run("openProject('a')");
    assert.equal(h.node('renderEmpty').querySelector('h2').textContent,'当前镜头还没有预览');
    assert.match(h.node('workflowTitle').textContent,/模型分析/);assert.equal(h.node('jobPanel').querySelector('[data-retry]').dataset.retry,'analysis-a');
    assert.equal(h.calls.filter(call=>call.method==='POST').length,0);
  }
});

function activityJob(overrides={}) {
  return {id:'activity-a',kind:'preview',status:'running',quality:'preview',shot_ids:['hero'],current_shot:'白底主图',
    stage:'正在采样',progress:0,sample_progress:0,completed:[],version:1,created_at:3,...overrides};
}

test('render activity follows queued, real sampling and review states without replacing the existing image',async t=>{
  const a=previewProject();a.jobs=[activityJob({status:'queued',stage:'等待执行',sample_progress:77})];
  const h=await setup(t,{projects:[a]});await h.run("openProject('a')");
  const panel=h.node('renderActivity'),track=h.node('renderActivityProgress'),bar=h.node('renderActivityBar'),image=h.node('renderImage'),src=image.getAttribute('src');
  assert.equal(panel.hidden,false);assert.equal(panel.dataset.state,'queued');
  assert.notEqual(panel.dataset.progress,'determinate');assert.equal(track.hasAttribute('aria-valuenow'),false,'queued jobs cannot reuse a previous sampling number');
  assert.equal(image.hidden,false);assert.equal(h.node('viewer').getAttribute('aria-busy'),'true');
  const job=h.records.get('a').jobs[0];job.status='running';job.stage='正在采样';job.sample_progress=0;
  await h.run('poll()');assert.equal(panel.dataset.state,'rendering');assert.equal(panel.dataset.progress,'determinate');assert.equal(track.getAttribute('aria-valuenow'),'0');
  job.sample_progress=42.5;await h.run('poll()');
  assert.equal(track.getAttribute('aria-valuenow'),'42.5');assert.match(h.node('renderActivityMetric').textContent,/采样/);assert.match(h.node('renderActivityMetric').textContent,/42\.5/);
  assert.strictEqual(h.node('renderActivityBar'),bar,'polling must preserve the animation node');assert.equal(image.getAttribute('src'),src);
  job.sample_progress=100;await h.run('poll()');
  assert.equal(panel.hidden,false);assert.equal(panel.dataset.state,'rendering');assert.equal(track.getAttribute('aria-valuenow'),'100');
  assert.doesNotMatch(h.node('renderActivityTitle').textContent,/已完成|渲染完成/,'100% sampling is not task completion');
  job.stage='检查预览曝光与逼真度';await h.run('poll()');
  assert.equal(panel.dataset.state,'reviewing');assert.notEqual(panel.dataset.progress,'determinate');assert.equal(track.hasAttribute('aria-valuenow'),false);
  assert.equal(image.getAttribute('src'),src);assert.equal(h.calls.filter(call=>call.method!=='GET').length,0);
});

test('unknown sampling remains indeterminate and does not invent elapsed-time progress',async t=>{
  const a=previewProject();a.jobs=[activityJob({sample_progress:null,progress:0})];
  const h=await setup(t,{projects:[a]});await h.run("openProject('a')");
  assert.equal(h.node('renderActivity').dataset.progress,'indeterminate');assert.equal(h.node('renderActivityProgress').hasAttribute('aria-valuenow'),false);
  assert.doesNotMatch(h.node('renderActivityMetric').textContent,/采样.*\d+%/);
  for(let repeat=0;repeat<3;repeat++)await h.run('poll()');
  assert.equal(h.node('renderActivity').dataset.progress,'indeterminate');assert.equal(h.node('renderActivityProgress').hasAttribute('aria-valuenow'),false);
  assert.equal(h.calls.filter(call=>call.method!=='GET').length,0);
});

test('a disconnected polling cycle pauses render activity and reconnects using the latest backend state',async t=>{
  const a=previewProject();a.jobs=[activityJob({sample_progress:38})];
  const h=await setup(t,{projects:[a]});await h.run("openProject('a')");const src=h.node('renderImage').getAttribute('src');
  assert.equal(h.node('renderActivity').dataset.motion,'active');
  h.intercept=call=>{if(call.url==='/api/projects/a')throw new TypeError('模拟本机连接中断');};
  await h.run('poll()');
  assert.equal(h.node('renderActivity').dataset.state,'disconnected');assert.equal(h.node('renderActivity').dataset.motion,'still');assert.equal(h.node('renderActivity').dataset.progress,'none');
  assert.match(h.node('renderActivityTitle').textContent,/连接中断/);assert.equal(h.node('renderActivityProgress').hasAttribute('aria-valuenow'),false);assert.equal(h.node('viewer').getAttribute('aria-busy'),'false');
  assert.equal(h.node('renderImage').getAttribute('src'),src);
  h.intercept=null;h.records.get('a').jobs[0].sample_progress=64;await h.run('poll()');
  assert.equal(h.node('renderActivity').dataset.state,'rendering');assert.equal(h.node('renderActivity').dataset.motion,'active');assert.equal(h.node('renderActivityProgress').getAttribute('aria-valuenow'),'64');
  assert.equal(h.calls.filter(call=>call.method!=='GET').length,0);
});

test('terminal render states remove activity immediately while preserving results and recovery controls',async t=>{
  for(const status of ['completed','failed','cancelled','interrupted']){
    const a=previewProject();a.jobs=[activityJob({sample_progress:96})];
    const h=await setup(t,{projects:[a]});await h.run("openProject('a')");
    const record=h.records.get('a');record.jobs[0].status=status;record.jobs[0].stage=status==='completed'?'已完成':'任务已停止';
    if(status==='failed')record.jobs[0].error='合成测试渲染失败';
    if(status==='completed')record.outputs=[{...record.outputs[0],job_id:'activity-a',png_url:'/qa/new-activity-result.png',created_at:4}];
    await h.run('poll()');
    assert.equal(h.node('renderActivity').hidden,true,status);assert.equal(h.node('viewer').getAttribute('aria-busy'),'false',status);assert.equal(h.node('renderImage').hidden,false,status);
    assert.equal(h.node('renderImage').getAttribute('src'),status==='completed'?'/qa/new-activity-result.png':'/qa/synthetic-preview-a.png',status);
    if(status!=='completed')assert.ok(h.node('jobPanel').querySelector('[data-retry]'),status+' keeps its existing recovery action');
    assert.equal(h.calls.filter(call=>call.method!=='GET').length,0,status);
  }
});

test('activity identifies the actually running shot and never leaks across project navigation or a late poll',async t=>{
  const a=previewProject();a.jobs=[activityJob({kind:'render',quality:'standard',shot_ids:['hero','campaign'],current_shot:'棚拍宣传',completed:['hero'],progress:50,sample_progress:28})];
  const h=await setup(t,{projects:[a,previewProject('b')]});await h.run("openProject('a')");
  assert.equal(h.node('renderActivity').dataset.scope,'background');assert.equal(h.node('viewer').getAttribute('aria-busy'),'false');assert.match(h.node('renderActivity').textContent,/棚拍宣传/);
  assert.equal(h.node('renderImage').getAttribute('src'),'/qa/synthetic-preview-a.png');
  h.records.get('a').jobs[0].completed=[];h.records.get('a').jobs[0].progress=0;await h.run('poll()');
  assert.equal(h.node('renderActivity').dataset.scope,'background','a pending viewed shot is not the shot currently being rendered');assert.equal(h.node('viewer').getAttribute('aria-busy'),'false');
  h.node('shotList').querySelector('[data-shot="campaign"]').click();
  assert.equal(h.node('renderActivity').dataset.scope,'current');assert.equal(h.node('viewer').getAttribute('aria-busy'),'true');
  const gate=deferred();h.intercept=call=>call.url==='/api/projects/a'?gate.promise:undefined;
  const polling=h.run('poll()');await tick();await h.run("openProject('b')");
  assert.equal(h.node('renderActivity').hidden,true);assert.equal(h.node('viewer').getAttribute('aria-busy'),'false');
  gate.resolve(h.response(h.records.get('a')));await polling;
  assert.equal(h.run('current.id'),'b');assert.equal(h.node('renderActivity').hidden,true);assert.equal(h.node('renderImage').getAttribute('src'),'/qa/synthetic-preview-b.png');
  h.intercept=null;await h.run("openProject('a')");assert.equal(h.node('renderActivity').hidden,false);
  await h.run('exitProject()');assert.equal(h.run('current'),null);assert.equal(h.node('renderActivity').hidden,true);assert.equal(h.node('viewer').getAttribute('aria-busy'),'false');
  assert.equal(h.calls.filter(call=>call.method!=='GET').length,0);
});

const lightDirectionInputs={keyAzimuth:'key_azimuth',keyElevation:'key_elevation',fillAzimuth:'fill_azimuth',fillElevation:'fill_elevation',rimAzimuth:'rim_azimuth',rimElevation:'rim_elevation'};

test('legacy plans keep blank light directions and saving an unrelated edit does not invent angles',async t=>{
  const a=project();a.plan.shot_adjustments={hero:{exposure_delta:.25,lighting:{key_power:1.6}},campaign:{lighting:{fill_power:1.2}}};
  const lighting=copy(a.plan.lighting),adjustments=copy(a.plan.shot_adjustments);
  const h=await setup(t,{projects:[a]});await h.run("openProject('a')");
  for(const [id,key] of Object.entries(lightDirectionInputs)){
    assert.equal(h.node(id).value,'',id+' stays inherited');assert.equal(h.node(id).dataset.lightDirection,key);
  }
  input(h,'margin','20');await h.run('savePlan(true)');
  const saved=h.records.get('a').plan;assert.deepEqual(saved.lighting,lighting);assert.deepEqual(saved.shot_adjustments,adjustments);
  assert.equal(saved.margin,.2);assert.equal(h.calls.filter(call=>call.url.endsWith('/plan')).length,1);
});

test('light directions follow the edited shot and clearing a local angle restores inheritance without losing power',async t=>{
  const a=project();a.plan.lighting={...a.plan.lighting,key_azimuth:-55,key_elevation:48};
  a.plan.shot_adjustments={hero:{exposure_delta:.2,lighting:{key_power:1.7,key_azimuth:-30,key_elevation:42}},campaign:{lighting:{fill_power:1.25,fill_azimuth:80}}};
  const globals=copy(a.plan.lighting),h=await setup(t,{projects:[a]});await h.run("openProject('a')");
  assert.equal(h.node('keyAzimuth').value,'-30');assert.equal(h.node('keyElevation').value,'42');assert.equal(h.node('fillAzimuth').value,'');
  input(h,'keyAzimuth','-25');input(h,'keyElevation','35');input(h,'fillAzimuth','20');
  h.node('cameraShot').value='campaign';h.node('cameraShot').dispatchEvent(new h.w.Event('change'));
  assert.equal(h.node('keyAzimuth').value,'');assert.equal(h.node('keyElevation').value,'');assert.equal(h.node('fillAzimuth').value,'80');
  input(h,'rimAzimuth','145');input(h,'rimElevation','30');
  h.node('cameraShot').value='hero';h.node('cameraShot').dispatchEvent(new h.w.Event('change'));
  assert.equal(h.node('keyAzimuth').value,'-25');assert.equal(h.node('keyElevation').value,'35');assert.equal(h.node('fillAzimuth').value,'20');assert.equal(h.node('rimAzimuth').value,'');
  await h.run('savePlan(true)');
  let saved=h.records.get('a').plan;
  assert.deepEqual(saved.lighting,globals);assert.deepEqual(saved.shot_adjustments.hero,{exposure_delta:.2,lighting:{key_power:1.7,key_azimuth:-25,key_elevation:35,fill_azimuth:20}});
  assert.deepEqual(saved.shot_adjustments.campaign,{lighting:{fill_power:1.25,fill_azimuth:80,rim_azimuth:145,rim_elevation:30}});assert.equal(saved.shot_adjustments.side,undefined);
  input(h,'keyAzimuth','');await h.run('savePlan(true)');saved=h.records.get('a').plan;
  assert.equal(Object.hasOwn(saved.shot_adjustments.hero.lighting,'key_azimuth'),false);assert.equal(saved.lighting.key_azimuth,-55);assert.equal(saved.shot_adjustments.hero.lighting.key_power,1.7);assert.equal(saved.shot_adjustments.hero.exposure_delta,.2);
  assert.equal(saved.shot_adjustments.campaign.lighting.rim_azimuth,145);assert.equal(h.node('keyAzimuth').value,'');
});

test('invalid light direction ranges stay local while valid boundary values save',async t=>{
  for(const [id,value] of [['keyAzimuth','181'],['fillAzimuth','-181'],['keyElevation','-1'],['rimElevation','86']]){
    const h=await setup(t);await h.run("openProject('a')");input(h,id,value);
    await assert.rejects(h.run('savePlan(true)'),/检查|无效|角度/);
    assert.equal(h.records.get('a').version,1,id);assert.equal(h.calls.filter(call=>call.url.endsWith('/plan')).length,0,id);assert.equal(h.run('dirty'),true,id);
  }
  const h=await setup(t);await h.run("openProject('a')");
  for(const [id,value] of [['keyAzimuth','-180'],['fillAzimuth','180'],['keyElevation','0'],['rimElevation','85']])input(h,id,value);
  await h.run('savePlan(true)');
  assert.deepEqual(h.records.get('a').plan.shot_adjustments.hero.lighting,{key_azimuth:-180,key_elevation:0,fill_azimuth:180,rim_elevation:85});
});

test('light direction receipts show degrees and original-rig inheritance instead of a fabricated multiplier',async t=>{
  const h=await setup(t);await h.run("openProject('a')");
  const rows=h.run("instructionDiffRows({changes:[{path:'shot_adjustments.hero.lighting.key_azimuth',before:null,after:-30},{path:'shot_adjustments.hero.lighting.key_elevation',before:0,after:35},{path:'shot_adjustments.campaign.lighting.fill_azimuth',before:null,after:30,effective_before:45,effective_after:30},{path:'lighting.rim_elevation',after:85}]})");
  assert.match(rows[0].label,/主光.*方位/);assert.equal(rows[0].before,'沿用原始布光');assert.equal(rows[0].after,'-30°');assert.equal(rows[0].scope,'镜头：白底主图');
  assert.match(rows[1].label,/主光.*俯仰/);assert.equal(rows[1].before,'0°');assert.equal(rows[1].after,'35°');
  assert.match(rows[2].label,/补光.*方位/);assert.equal(rows[2].before,'45°');assert.equal(rows[2].after,'30°');assert.equal(rows[2].scope,'镜头：棚拍宣传');
  assert.match(rows[3].label,/轮廓光.*俯仰/);assert.equal(rows[3].before,'沿用原始布光');assert.equal(rows[3].after,'85°');
  const rendered=h.run("instructionRowsHTML(instructionDiffRows({changes:[{path:'shot_adjustments.hero.lighting.key_azimuth',before:null,after:-30}]}))");
  assert.match(rendered,/沿用原始布光/);assert.match(rendered,/ → /);assert.match(rendered,/-30°/);
});

test('analysis and material processing are named honestly and task-only refreshes update the activity',async t=>{
  const a=project();a.analysis=null;a.status='analyzing';a.initial_preview={status:'pending'};
  a.jobs=[activityJob({kind:'analyze',current_shot:null,shot_ids:[],stage:'读取模型',sample_progress:88})];
  const h=await setup(t,{projects:[a]});await h.run("openProject('a')");
  assert.equal(h.node('renderActivity').dataset.state,'analyzing');assert.match(h.node('renderActivityTitle').textContent,/分析|解析/);assert.doesNotMatch(h.node('renderActivityTitle').textContent,/正在渲染/);
  assert.notEqual(h.node('renderActivity').dataset.progress,'determinate');
  const record=h.records.get('a');record.analysis=project().analysis;record.status='ready';record.jobs[0].status='completed';record.initial_preview={status:'completed'};record.outputs=copy(previewProject().outputs);await h.run('poll()');
  assert.equal(h.node('renderActivity').hidden,true);
  let tasks=[{id:'material-a',origin:'automatic',status:'running',stage:'正在寻找可用材质',prompt:'保留原色'}];
  h.intercept=call=>call.url==='/api/projects/a/material-agent/tasks'?h.response(tasks):undefined;
  await h.run("refreshMaterialTasks('a')");
  assert.equal(h.node('renderActivity').hidden,false);assert.equal(h.node('renderActivity').dataset.state,'material');assert.doesNotMatch(h.node('renderActivityTitle').textContent,/正在渲染/);assert.match(h.node('renderActivityStage').textContent,/材质/);
  tasks=[{...tasks[0],status:'rendering',stage:'正在生成材质预览'}];await h.run("refreshMaterialTasks('a')");
  assert.match(h.node('renderActivityTitle').textContent,/渲染|生成.*预览/);
  tasks=[{...tasks[0],status:'completed',stage:'处理完成'}];await h.run("refreshMaterialTasks('a')");
  assert.equal(h.node('renderActivity').hidden,true);
  h.run('busy=true;renderActionState()');assert.equal(h.node('renderActivity').hidden,true,'generic saving/submitting is not a rendering task');h.run('busy=false;renderActionState()');
  assert.equal(h.calls.filter(call=>call.method!=='GET').length,0);
});

function heroOptimizationProject(id='a') {
  const p=project(id);
  p.outputs=[nativeSnapshot({job_id:'hero-source-'+id,version:1,png_url:'/files/'+id+'/runs/source/hero.png',blend_url:'/files/'+id+'/runs/source/hero.blend'})];
  return p;
}
function enableHeroOptimization(h) {
  h.system.runtime.capabilities.hero_optimize=true;
  h.run('system='+JSON.stringify(h.system)+';renderProject()');
}

test('one-click hero optimization saves a draft and submits the latest real source once without another confirmation',async t=>{
  const p=heroOptimizationProject();p.outputs.push({...p.outputs[0],job_id:'hero-new',created_at:40,png_url:'/files/a/runs/new/hero.png',blend_url:'/files/a/runs/new/hero.blend'});
  const h=await setup(t,{projects:[p]});enableHeroOptimization(h);await h.run("openProject('a')");
  input(h,'instructionInput','保留这段尚未提交的要求');input(h,'exposure','0.4');
  assert.equal(h.node('optimizeHeroButton').hidden,false);assert.equal(h.node('optimizeHeroButton').disabled,false);assert.equal(h.node('renderButton').classList.contains('primary'),true);assert.equal(h.node('optimizeHeroButton').classList.contains('primary'),false);assert.equal(h.node('optimizeHeroButton').closest('[role="tabpanel"]').id,'inspectorReview');
  const pending=h.run('optimizeHero()');await tick();await h.run('optimizeHero()');await pending;
  const writes=h.calls.filter(call=>call.method==='POST');assert.deepEqual(writes.map(call=>call.url),['/api/projects/a/plan','/api/projects/a/hero-optimize']);
  const request=writes[1];assert.equal(request.body.expected_version,2);assert.equal(request.body.final_resolution,2000);assert.equal(request.body.shot_id,'hero');
  assert.deepEqual(request.body.source,{output_id:'hero-new',shot_id:'hero',quality:'preview',version:1});assert.ok(request.headers['Idempotency-Key']);
  assert.equal(h.node('instructionInput').value,'保留这段尚未提交的要求');assert.equal(h.node('renderConfirmDialog'),null);assert.equal(h.node('renderActivity').dataset.state,'queued');
  assert.equal(h.node('optimizeHeroButton').disabled,true);assert.equal(h.node('exitProjectButton').disabled,false,'accepted work may continue in the background');
});

test('hero optimization availability is scoped to white full shots and independent of the legacy render worker',async t=>{
  const p=heroOptimizationProject();const h=await setup(t,{ready:false,projects:[p]});enableHeroOptimization(h);await h.run("openProject('a')");
  assert.equal(h.node('optimizeHeroButton').disabled,false);assert.equal(h.node('previewButton').disabled,true);
  h.run("activeShot='campaign';cameraShot='campaign';syncCamera();renderProject()");assert.equal(h.node('optimizeHeroButton').hidden,true);assert.equal(h.node('renderButton').classList.contains('primary'),true);
  h.run("draft.shots.find(s=>s.id==='campaign').style='white';renderProject()");assert.equal(h.node('optimizeHeroButton').hidden,false);assert.equal(h.node('optimizeHeroButton').disabled,true);assert.match(h.node('optimizeHeroReason').textContent,/先生成预览/);
  h.run("draft.shots.find(s=>s.id==='campaign').kind='detail';renderProject()");assert.equal(h.node('optimizeHeroButton').hidden,true);
  h.run("activeShot='hero';cameraShot='hero';syncCamera();serviceDisconnected=true;renderProject()");assert.equal(h.node('optimizeHeroButton').disabled,true);assert.match(h.node('optimizeHeroReason').textContent,/连接/);
  h.run('serviceDisconnected=false;system.runtime.capabilities.hero_optimize=false;renderActionState()');assert.equal(h.node('optimizeHeroButton').disabled,true);
  assert.equal(h.calls.filter(call=>call.method!=='GET').length,0);
});

test('hero workflow stages never reuse sampling during inspection or comparison and preserve the current image',async t=>{
  const p=heroOptimizationProject();p.jobs=[activityJob({id:'hero-work',kind:'hero_optimize',workflow_phase:'inspecting',stage:'核对场景',sample_progress:88})];
  const h=await setup(t,{projects:[p]});enableHeroOptimization(h);await h.run("openProject('a')");
  const image=h.node('renderImage').getAttribute('src'),job=h.records.get('a').jobs[0];
  for(const [phase,title,determinate] of [['inspecting',/读取真实场景/,false],['planning',/规划主图/,false],['preview',/试渲染主图/,true],['evaluating',/比较优化效果/,false],['finalizing',/生成高清主图/,true]]){
    Object.assign(job,{workflow_phase:phase,stage:'当前阶段工作',sample_progress:phase==='preview'?0:88});await h.run('poll()');
    assert.match(h.node('renderActivityTitle').textContent,title);assert.equal(h.node('renderActivity').dataset.progress,determinate?'determinate':'indeterminate');
    assert.equal(h.node('renderActivityProgress').hasAttribute('aria-valuenow'),determinate);assert.equal(h.node('renderImage').getAttribute('src'),image);
    assert.equal(h.node('jobPanel').querySelector('[aria-label="已完成镜头进度"]'),null,'a single optimization does not invent image-group progress');
  }
  job.sample_progress=100;await h.run('poll()');assert.equal(h.node('renderActivity').hidden,false,'100% sampling still requires saving and validation');
  h.run('serviceDisconnected=true;renderActionState()');assert.equal(h.node('renderActivity').dataset.motion,'still');assert.equal(h.node('renderActivity').dataset.progress,'none');
  h.run('serviceDisconnected=false;renderActionState()');assert.equal(h.node('renderActivity').dataset.motion,'active');
  for(const status of ['completed','failed','cancelled','interrupted']){job.status=status;await h.run('poll()');assert.equal(h.node('renderActivity').hidden,true);assert.equal(h.node('renderImage').getAttribute('src'),image);}
});

test('unknown hero submissions persist their exact source and request key for retry and refresh recovery',async t=>{
  const p=heroOptimizationProject(),h=await setup(t,{projects:[p]});enableHeroOptimization(h);await h.run("openProject('a')");
  let failed=false;h.intercept=call=>{if(call.url.endsWith('/hero-optimize') && !failed){failed=true;throw new TypeError('连接中断，提交结果未知');}};
  await h.run('optimizeHero()');assert.match(h.node('optimizeHeroButton').textContent,/核对/);assert.match(h.node('optimizeHeroReason').textContent,/连接中断/);
  const first=h.calls.find(call=>call.url.endsWith('/hero-optimize')),journal=h.w.sessionStorage.getItem('wuying-hero-optimization-attempts-v1');assert.ok(journal);
  const restored=await setup(t,{projects:[p],heroJournal:journal});enableHeroOptimization(restored);await restored.run("openProject('a')");
  await restored.run('optimizeHero()');const replay=restored.calls.find(call=>call.url.endsWith('/hero-optimize'));
  assert.deepEqual(replay.body,first.body);assert.equal(replay.headers['Idempotency-Key'],first.headers['Idempotency-Key']);assert.equal(restored.w.sessionStorage.getItem('wuying-hero-optimization-attempts-v1'),null);
  const accepted=await setup(t,{projects:[p]});enableHeroOptimization(accepted);await accepted.run("openProject('a')");
  accepted.intercept=call=>{if(call.url.endsWith('/hero-optimize')){accepted.records.get('a').jobs=[activityJob({kind:'hero_optimize',id:'accepted-before-loss',request_key:call.headers['Idempotency-Key'],status:'queued'})];throw new TypeError('回复丢失');}};
  await accepted.run('optimizeHero()');accepted.intercept=null;await accepted.run('poll()');assert.equal(accepted.w.sessionStorage.getItem('wuying-hero-optimization-attempts-v1'),null);
  assert.equal(accepted.node('optimizeHeroButton').disabled,true);assert.equal(accepted.calls.filter(call=>call.url.endsWith('/hero-optimize')).length,1);
});

test('hero errors permit retry while malformed journals and late cross-project replies cannot start or replace unrelated work',async t=>{
  const p=heroOptimizationProject(),h=await setup(t,{projects:[p,heroOptimizationProject('b')],heroJournal:{schema:1,attempts:[{projectId:'a',shotId:'hero',requestKey:'bad',body:{expected_version:1,shot_id:'hero',source:{output_id:'source',shot_id:'hero',quality:'preview',version:1},final_resolution:99999}}]}});enableHeroOptimization(h);await h.run("openProject('a')");
  assert.equal(h.run('heroOptimizationAttempts.size'),0);
  let failures=0;h.intercept=call=>call.url.endsWith('/hero-optimize') && failures++===0?h.response({error:'灯光组件未就绪'},503):undefined;
  await h.run('optimizeHero()');assert.equal(h.node('optimizeHeroButton').disabled,false);const first=h.calls.find(call=>call.url.endsWith('/hero-optimize'));
  await h.run('optimizeHero()');const requests=h.calls.filter(call=>call.url.endsWith('/hero-optimize'));assert.equal(requests[1].headers['Idempotency-Key'],first.headers['Idempotency-Key']);assert.deepEqual(requests[1].body,first.body);
  const later=await setup(t,{projects:[p,heroOptimizationProject('b')]});enableHeroOptimization(later);await later.run("openProject('a')");
  const response=deferred();let captured;later.intercept=call=>{if(call.url.endsWith('/hero-optimize')){captured=call;return response.promise;}};
  const pending=later.run('optimizeHero()');await tick();assert.equal(later.node('exitProjectButton').disabled,true);
  later.run('leaveProjectView()');later.run('current='+JSON.stringify(heroOptimizationProject('b'))+";activeShot='hero';syncSettings();renderProject()");
  response.resolve(later.response(activityJob({id:'late-hero',project_id:'a',kind:'hero_optimize',status:'queued',request_key:captured.headers['Idempotency-Key']}),202));await pending;
  assert.equal(later.run('current.id'),'b');assert.equal(later.run('current.jobs.length'),0);assert.equal(later.node('renderImage').getAttribute('src'),'/files/b/runs/source/hero.png');assert.equal(later.node('renderActivity').hidden,true);
  assert.equal(later.calls.filter(call=>call.url.includes('/exports') || call.url.includes('/bundle')).length,0);
});

test('adopted hero outputs show actual high resolution and reuse existing comparison and native downloads',async t=>{
  const p=heroOptimizationProject(),source=copy(p.outputs[0]);p.version=2;p.history=[{version:1,plan:copy(p.plan),at:1,reason:'主图优化'}];
  const result={...source,job_id:'hero-adopted',quality:'hero',version:2,width:2000,height:2000,created_at:80,png_url:'/files/a/runs/adopted/hero.png',blend_url:'/files/a/runs/adopted/hero.blend'};
  p.outputs.push(result);p.jobs=[activityJob({id:result.job_id,kind:'hero_optimize',status:'completed',workflow_phase:'finalizing',optimization_decision:'adopted',optimization_summary:'已改善正面照明与台面反射，输出高清主图。',automatic_previous_preview:source,hero_candidates:[{round:1,label:'布光试渲染',png_url:'/files/a/runs/candidate/hero.png'}]})];
  const h=await setup(t,{projects:[p]});enableHeroOptimization(h);await h.run("openProject('a')");
  assert.equal(h.node('renderKind').textContent,'高清主图');assert.equal(h.node('imageMeta').textContent,'2000 × 2000 px');assert.doesNotMatch(h.node('shotList').querySelector('[data-shot="hero"]').parentElement.querySelector('small').textContent,/4K/);
  assert.equal(h.node('downloadPng').getAttribute('href'),result.png_url+'?download=1');assert.equal(h.node('downloadLatestBlend').getAttribute('href'),result.blend_url+'?download=1');assert.equal(h.node('undoButton').disabled,false);
  assert.match(h.node('jobPanel').querySelector('.hero-optimization-summary').textContent,/正面照明/);assert.equal(h.node('jobPanel').querySelector('.hero-optimization-candidates').open,false);
  h.node('comparePreview').click();assert.equal(h.node('renderImage').getAttribute('src'),source.png_url);assert.equal(h.node('downloadLatestBlend').getAttribute('href'),result.blend_url+'?download=1');
  assert.equal(h.run('current.outputs.length'),2);assert.equal(h.run("current.outputs.some(o=>o.png_url.includes('candidate'))"),false);
  h.run('comparePrevious=false;current.outputs[1].width=4096;current.outputs[1].height=4096;renderProject()');assert.equal(h.node('renderKind').textContent,'4K 主图');
});

test('kept-original and failed hero tasks preserve the real source and expose only the existing recovery controls',async t=>{
  const p=heroOptimizationProject();p.jobs=[activityJob({id:'hero-kept',kind:'hero_optimize',status:'completed',optimization_decision:'kept_original',optimization_summary:'候选改善不足，已保留原图。',hero_candidates:[{round:1,label:'未采用的试渲染',png_url:'/files/a/runs/trial/hero.png'}]})];
  const h=await setup(t,{projects:[p]});enableHeroOptimization(h);await h.run("openProject('a')");
  assert.equal(h.node('renderImage').getAttribute('src'),p.outputs[0].png_url);assert.equal(h.node('downloadLatestBlend').getAttribute('href'),p.outputs[0].blend_url+'?download=1');assert.equal(h.run('current.outputs.length'),1);
  assert.match(h.node('jobPanel').textContent,/保留原图/);assert.doesNotMatch(h.node('jobPanel').textContent,/已采用优化/);assert.equal(h.node('jobPanel').querySelector('[data-retry]'),null);
  const saved=h.records.get('a');saved.jobs[0].status='failed';saved.jobs[0].optimization_summary='';saved.jobs[0].error='试渲染中断';await h.run('poll()');
  assert.ok(h.node('jobPanel').querySelector('[data-retry="hero-kept"]'));assert.equal(h.node('renderActivity').hidden,true);
  h.intercept=call=>{if(call.url==='/api/jobs/hero-kept/retry'){saved.jobs[0].status='queued';return h.response(saved.jobs[0],202);}};
  h.node('jobPanel').querySelector('[data-retry]').click();await tick();assert.equal(h.calls.filter(call=>call.url==='/api/jobs/hero-kept/retry').length,1);assert.equal(h.calls.filter(call=>call.url.endsWith('/hero-optimize')).length,0);
  assert.equal(h.node('renderActivity').dataset.state,'queued');
});

test('native checkpoints enable only their own follow-up preview and render without enabling unsupported uploads',async t=>{
  const p=heroOptimizationProject();p.plan.scene_checkpoints={hero:{blend_url:p.outputs[0].blend_url}};
  const h=await setup(t,{ready:false,projects:[p]});enableHeroOptimization(h);await h.run("openProject('a')");
  assert.equal(h.node('previewButton').disabled,false);assert.equal(h.node('renderButton').disabled,false);
  h.run("uploadFiles=[new File(['x'],'new.obj')];addFiles([])");assert.equal(h.node('uploadSubmit').disabled,true);
  h.run("selectedShots.add('side');renderActionState()");assert.equal(h.node('renderButton').disabled,true);
  h.run("selectedShots=new Set(['hero']);activeShot='side';cameraShot='side';syncCamera();renderProject()");assert.equal(h.node('previewButton').disabled,true);assert.equal(h.node('renderButton').disabled,false,'selected hero can still render while another view is open');
  h.run("activeShot='hero';cameraShot='hero';syncCamera();renderProject()");await h.run("run('preview')");
  const request=h.calls.find(call=>call.url.endsWith('/jobs'));assert.ok(request);assert.deepEqual(request.body.shot_ids,['hero']);assert.equal(request.body.kind,'preview');
  assert.equal(h.calls.filter(call=>call.url.endsWith('/hero-optimize')).length,0);
});

test('home cards follow the v18 design: latest hero image, finished, active and interrupted states',async t=>{
  const done=project('a');done.name='MORA 按压瓶';
  done.outputs=[{job_id:'j1',shot_id:'side',quality:'preview',version:1,jpg_url:'/files/a/side.jpg',png_url:'/files/a/side.png'},
    {job_id:'j2',shot_id:'hero',quality:'standard',version:1,jpg_url:'/files/a/hero.jpg',png_url:'/files/a/hero.png'},
    {job_id:'j3',shot_id:'campaign',quality:'preview',version:1,jpg_url:'/files/a/campaign.jpg',png_url:'/files/a/campaign.png'}];
  const rendering=project('b');rendering.active_job={id:'r',kind:'render',quality:'standard',status:'running',progress:40,origin:null};
  const stopped=project('c');stopped.status='interrupted';stopped.analysis=null;
  const fresh=project('d');fresh.name='<b>坐垫</b>';
  const h=await setup(t,{projects:[done,rendering,stopped,fresh]});
  const grid=h.node('homeProjectGrid'),card=id=>grid.querySelector(`[data-open-project="${id}"]`).closest('.project-card');
  assert.equal(h.node('homeProjectCount').textContent,'4');assert.equal(grid.querySelectorAll('.project-card').length,4);
  assert.equal(card('a').querySelector('img').getAttribute('src'),'/files/a/hero.jpg','the latest hero image represents the project');
  assert.match(card('a').querySelector('.project-card-status').textContent,/1 张 4K 成品 · 3 个镜头/);
  assert.equal(card('a').querySelector('.project-card-status').dataset.tone,'done');assert.ok(card('a').querySelector('.card-dot'));
  assert.match(card('b').textContent,/正在渲染 4K · 后台继续/);assert.equal(card('b').querySelector('.project-card-progress > span').style.width,'40%');
  assert.match(card('c').textContent,/分析已中断 · 可继续/);assert.ok(card('c').querySelector('svg.card-warning[aria-label="需要处理"]'));
  assert.equal(card('d').querySelector('img'),null);assert.equal(card('d').querySelector('b'),null,'project names are escaped');assert.match(card('d').textContent,/可开始拍摄/);
});

test('home cards open projects and delete through the shared confirmation',async t=>{
  const h=await setup(t);
  const removing=h.run("deleteProject('b')");await tick();
  assert.ok(h.node('homeProjectGrid').querySelector('[data-delete-project="b"]').getAttribute('aria-label').includes('删除项目'));
  h.node('deleteProjectDialog').querySelector('[data-choice="delete"]').click();assert.equal(await removing,true);
  assert.equal(h.node('homeProjectGrid').querySelector('[data-open-project="b"]'),null);assert.equal(h.node('homeProjectCount').textContent,'1');
  h.node('homeProjectGrid').querySelector('[data-open-project="a"]').click();await tick();await tick();
  assert.equal(h.run('current?.id'),'a');assert.equal(h.w.document.body.classList.contains('has-project'),true);assert.equal(h.node('welcomePanel').hidden,true);
});

test('home upload buttons open the native pickers and chosen files continue in the upload dialog',async t=>{
  const h=await setup(t);let picked='';
  h.node('modelFiles').click=()=>{picked='files';};h.node('modelFolder').click=()=>{picked='folder';};
  h.node('uploadMain').click();assert.equal(picked,'files');assert.equal(h.node('uploadDialog').open,false,'the picker opens before the dialog');
  h.node('homeBrowseFolder').click();assert.equal(picked,'folder');
  await h.run("continueUpload([new File(['glb'],'bottle.glb')])");
  assert.equal(h.node('uploadDialog').open,true);assert.match(h.node('chosenFiles').textContent,/bottle\.glb/);assert.equal(h.node('uploadSubmit').disabled,false);
});

function rendered(id='a',size=4096) {
  const p=project(id),quality=size>512?'standard':'preview';
  p.outputs.push({job_id:'r1',shot_id:'hero',shot_label:'白底主图',quality,version:1,created_at:5,width:size,height:size,png_url:`/files/${id}/hero.png`,jpg_url:`/files/${id}/hero.jpg`,blend_url:`/files/${id}/hero.blend`});
  return p;
}
const ecommerceCalls=h=>h.calls.filter(call=>/ecommerce/.test(call.url));

test('电商出图 is the fourth inspector tab and asks the server only once it is opened',async t=>{
  const h=await setup(t,{projects:[rendered('a',512)]});await h.run("openProject('a')");
  assert.deepEqual([...h.w.document.querySelectorAll('[data-inspector]')].map(tab=>tab.dataset.inspector),['review','material','plan','ecommerce']);
  assert.deepEqual(ecommerceCalls(h),[],'opening a project does not load marketplace data');
  h.node('inspectorEcommerceTab').click();await tick();await tick();
  assert.equal(h.node('inspectorEcommerce').hidden,false);assert.equal(h.node('inspectorReview').hidden,true);
  assert.deepEqual(ecommerceCalls(h).map(call=>call.method+' '+call.url),['GET /api/ecommerce/platforms','GET /api/projects/a/ecommerce']);
  assert.equal(h.node('ecommercePlatform').value,'general');
  assert.deepEqual([...h.node('ecommercePlatform').querySelectorAll('optgroup')].map(group=>group.label),['通用','跨境','国内']);
  assert.match(h.node('ecommercePlatformSummary').textContent,/导出 2000 px · 建议纯白底/);
  assert.equal(h.node('ecommerceCheckShot').textContent,'白底主图');
  const checks=[...h.node('ecommerceChecks').children];
  assert.deepEqual(checks.map(item=>item.dataset.status),['action','auto']);
  assert.equal(checks[0].querySelector('.value').textContent,'512 px，需渲染 4K');assert.ok(checks[0].querySelector('.detail'),'a rule that needs the user shows its sentence');
  assert.equal(checks[1].querySelector('.value').textContent,'导出时处理');assert.equal(checks[1].querySelector('.detail'),null,'a passing or automatic rule stays on one line');
  assert.match(checks[1].title,/自动处理为纯白/);
  assert.equal(h.node('ecommerceExport').disabled,false,'a preview can still be exported');
  assert.equal(h.node('ecommerceExportHint').dataset.tone,'warn');assert.match(h.node('ecommerceExportHint').textContent,/只有预览尺寸/);
  await h.run('poll()');assert.equal(ecommerceCalls(h).length,2,'an unchanged project is not measured again');
});

test('the last inspector tab is reachable from the keyboard',async t=>{
  const h=await setup(t);await h.run("openProject('a')");const first=h.node('inspectorReviewTab');first.focus();
  first.dispatchEvent(new h.w.KeyboardEvent('keydown',{key:'End',bubbles:true}));await tick();
  assert.equal(h.node('inspectorEcommerceTab').getAttribute('aria-selected'),'true');assert.equal(h.w.document.activeElement,h.node('inspectorEcommerceTab'));
  h.node('inspectorEcommerceTab').dispatchEvent(new h.w.KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true}));
  assert.equal(h.node('inspectorReviewTab').getAttribute('aria-selected'),'true');
  h.node('inspectorReviewTab').dispatchEvent(new h.w.KeyboardEvent('keydown',{key:'ArrowLeft',bubbles:true}));
  assert.equal(h.node('inspectorEcommerceTab').getAttribute('aria-selected'),'true');
  await tick();await tick();await tick();
});

test('marketplace and export options are saved with the project and drive the checks',async t=>{
  const h=await setup(t,{projects:[rendered('a')]});await h.run("openProject('a')");h.node('inspectorEcommerceTab').click();await tick();await tick();
  assert.equal(h.node('ecommerceExportHint').hidden,true,'nothing to say when the export is ready');
  h.node('ecommercePlatform').value='amazon';h.node('ecommercePlatform').dispatchEvent(new h.w.Event('change'));await tick();
  const saved=h.calls.at(-1);assert.equal(saved.method+' '+saved.url,'PUT /api/projects/a/ecommerce');
  assert.deepEqual(saved.body,{platform:'amazon',background:'main_white',shadow:true,transparent:false});
  assert.match(h.node('ecommercePlatformSummary').textContent,/主图须纯白底 · 产品占比 ≥ 85% · ≤ 10 MB/);
  assert.equal(h.node('ecommercePlatformNotes').querySelector('a').getAttribute('rel'),'noopener noreferrer');
  h.node('ecommerceTransparent').checked=true;h.node('ecommerceTransparent').dispatchEvent(new h.w.Event('change'));await tick();
  assert.equal(h.calls.at(-1).body.transparent,true);assert.equal(h.node('ecommerceTransparent').checked,true);
  h.node('ecommerceBackground').value='all_white';h.node('ecommerceBackground').dispatchEvent(new h.w.Event('change'));await tick();
  assert.equal(h.calls.at(-1).body.background,'all_white');assert.equal(h.records.get('a').ecommerce.background,'all_white');
});

test('without the matte runtime only the keep-as-rendered export is offered',async t=>{
  const h=await setup(t,{projects:[rendered('a')]});h.matteAvailable=false;await h.run("openProject('a')");h.node('inspectorEcommerceTab').click();await tick();await tick();
  assert.equal(h.node('ecommerceBackground').disabled,true);assert.equal(h.node('ecommerceBackground').value,'original');
  h.node('ecommerceExport').click();await tick();assert.equal(h.calls.at(-1).body.background,'original');
  assert.equal(h.node('ecommerceTransparent').disabled,true);assert.equal(h.node('ecommerceShadow').disabled,true);
});

test('export submits the chosen settings once and then shows the verified files',async t=>{
  const h=await setup(t,{projects:[rendered('a')]});await h.run("openProject('a')");h.node('inspectorEcommerceTab').click();await tick();await tick();
  h.node('ecommerceExport').click();h.node('ecommerceExport').click();await tick();
  const posts=h.calls.filter(call=>call.method==='POST' && call.url==='/api/projects/a/ecommerce/exports');
  assert.equal(posts.length,1);assert.deepEqual(posts[0].body,{platform:'general',background:'main_white',shadow:true,transparent:false});
  assert.equal(h.node('ecommerceResult').hidden,false);assert.equal(h.node('ecommerceExport').disabled,true);assert.equal(h.node('ecommerceExport').textContent,'正在导出…');
  assert.ok(h.node('ecommerceResult').querySelector('[data-cancel-ecommerce]'));
  const task=h.records.get('a').ecommerce_exports[0];
  Object.assign(task,{status:'completed',stage:'已导出 2 个文件，其中 1 个需要留意',progress:100,download_url:'/files/a/ecommerce/exports/x/pack.zip',files:[
    {name:'01_hero_main_2000x2000.jpg',label:'白底主图',role:'main',width:2000,height:2000,bytes:412000,background:'white',pure_white:true,fill:.88,issues:[],url:'/files/a/ecommerce/exports/x/package/01_hero_main_2000x2000.jpg'},
    {name:'x.png',label:'<img src=x onerror=alert(1)>',role:'transparent',width:4096,height:4096,bytes:5242880,background:'transparent',issues:['图片来自旧方案 v1'],url:'/files/other/ecommerce/x.png'}]});
  await h.run('poll()');
  const result=h.node('ecommerceResult');
  assert.equal(result.querySelector('a.button').getAttribute('href'),'/files/a/ecommerce/exports/x/pack.zip?download=1');
  const tiles=[...result.querySelectorAll('.ecommerce-files li')];assert.equal(tiles.length,2);
  assert.match(tiles[0].textContent,/2000 × 2000 · 402 KB/);assert.equal(tiles[0].querySelector('a').getAttribute('href'),'/files/a/ecommerce/exports/x/package/01_hero_main_2000x2000.jpg');
  assert.equal(tiles[1].querySelector('a').getAttribute('href'),'#','a file outside this project is never linked');
  assert.equal(result.querySelectorAll('img[onerror]').length,0);assert.equal(tiles[1].dataset.issue,'true');
  assert.match(result.querySelector('.ecommerce-issues').textContent,/图片来自旧方案 v1/);
  assert.equal(h.node('ecommerceExport').disabled,false);assert.equal(h.node('ecommerceExport').textContent,'导出平台套图');
  assert.match(h.node('toast').textContent,/平台套图已导出/);
});

test('a running export can be cancelled and a running render blocks a new export',async t=>{
  const h=await setup(t,{projects:[rendered('a')]});await h.run("openProject('a')");h.node('inspectorEcommerceTab').click();await tick();await tick();
  h.node('ecommerceExport').click();await tick();
  h.node('ecommerceResult').querySelector('[data-cancel-ecommerce]').click();await tick();
  assert.match(h.calls.at(-1).url,/^\/api\/ecommerce\/exports\/ecom-\d+\/cancel$/);assert.equal(h.node('ecommerceResult').hidden,true);
  assert.equal(h.node('ecommerceExport').disabled,false);
  h.records.get('a').jobs.unshift({id:'j1',status:'running',kind:'render',shot_ids:['hero'],quality:'standard',version:1,created_at:9,stage:'渲染中'});
  await h.run('poll()');
  assert.equal(h.node('ecommerceExport').disabled,true);assert.match(h.node('ecommerceExportHint').textContent,/渲染完成后可导出/);
});

test('re-framing for a marketplace is one undoable plan change followed by one preview',async t=>{
  const h=await setup(t,{projects:[rendered('a',512)]});h.planRecommendation={margin:.06};
  await h.run("openProject('a')");h.node('inspectorEcommerceTab').click();await tick();await tick();
  assert.equal(h.node('ecommerceAdapt').hidden,false);assert.match(h.node('ecommerceAdapt').textContent,/留白 6%/);
  h.planRecommendation={};
  await h.run('adaptEcommercePlan()');
  const adapt=h.calls.find(call=>call.url==='/api/projects/a/ecommerce/adapt');
  assert.equal(adapt.body.expected_version,1);assert.equal(adapt.body.platform,'general');
  const preview=h.calls.find(call=>call.url==='/api/projects/a/jobs');
  assert.deepEqual([preview.body.kind,preview.body.shot_ids,preview.body.expected_version],['preview',['hero'],2]);
  assert.equal(h.records.get('a').plan.margin,.06);assert.equal(h.node('margin').value,'6');
  await tick();assert.equal(h.node('ecommerceAdapt').hidden,true);
});

test('the upload dialog offers marketplaces and remembers the last choice',async t=>{
  const h=await setup(t);await h.run("continueUpload([new File(['glb'],'bottle.glb')])");await tick();
  const select=h.node('uploadPlatform');
  assert.equal(select.name,'platform');assert.equal(select.value,'');
  assert.deepEqual([...select.options].map(option=>option.value),['','general','amazon','taobao']);
  select.value='amazon';
  h.intercept=async call=>call.method==='POST' && call.url==='/api/projects'?h.response({...project('n'),status:'analyzing',analysis:null},201):undefined;
  h.records.set('n',{...project('n'),status:'analyzing',analysis:null});
  h.node('uploadForm').dispatchEvent(new h.w.Event('submit',{cancelable:true}));await tick();await tick();await tick();
  const upload=h.calls.find(call=>call.method==='POST' && call.url==='/api/projects');
  assert.equal(upload.body.get('platform'),'amazon');
  assert.equal(h.w.localStorage.getItem('wuying-platform'),'amazon');assert.equal(h.node('uploadPlatform').value,'amazon');
});


function polishJob(overrides={}) {
  return {id:'polish-a',kind:'render',origin:'auto_polish',status:'running',quality:'showcase',shot_ids:['hero'],current_shot:'白底主图',
    stage:'Cycles 正在渲染',progress:0,sample_progress:30,completed:[],version:1,created_at:9,...overrides};
}

test('a background high-quality pass never locks the workspace and says so',async t=>{
  const a=previewProject();a.plan.look='showcase';a.plan.auto_polish=true;a.jobs=[polishJob()];
  const h=await setup(t,{projects:[a]});await h.run("openProject('a')");
  assert.equal(h.run('!!activeJob()'),false,'the pass is not a blocking job');assert.equal(h.run('polishJob().id'),'polish-a');
  assert.equal(h.node('settingsForm').inert,false);assert.equal(h.node('undoButton').disabled,true,'nothing to undo yet, not locked by the pass');
  assert.equal(h.node('instructionInput').disabled,false);
  const panel=h.node('jobPanel');assert.equal(panel.hidden,false);
  assert.match(panel.textContent,/后台生成高清图/);assert.match(panel.textContent,/可继续操作/);assert.match(panel.textContent,/采样 30%/);
  assert.ok(panel.querySelector('[data-cancel="polish-a"]'),'the pass can still be cancelled by hand');
  assert.equal(h.node('autoPolish').checked,true);
  assert.equal(h.calls.filter(call=>call.method!=='GET').length,0);
});

test('a finished or pre-empted pass leaves no stale status and its image is labelled 2K',async t=>{
  const a=previewProject();a.plan.look='showcase';a.plan.auto_polish=true;
  a.jobs=[{id:'preview-a',kind:'preview',status:'completed',quality:'preview',shot_ids:['hero'],completed:['hero'],version:1,created_at:3,stage:'已完成',progress:100},
          polishJob({status:'cancelled',yielded:true,stage:'已取消'})];
  const h=await setup(t,{projects:[a]});await h.run("openProject('a')");
  assert.doesNotMatch(h.node('jobPanel').textContent,/高清图/,'a pre-empted pass is not presented as an interrupted task');
  assert.equal(h.node('jobPanel').querySelector('[data-retry]'),null);
  const record=h.records.get('a');record.jobs[1]=polishJob({status:'completed',stage:'已完成',progress:100});
  record.outputs.push({job_id:'polish-a',shot_id:'hero',version:1,quality:'showcase',width:2048,height:2048,created_at:10,png_url:'/qa/synthetic-2k-a.png'});
  await h.run('poll()');
  assert.equal(h.run('currentOutput.quality'),'showcase','the newest image of the same plan is shown');
  assert.match(h.node('renderKind').textContent,/高清 2K/);
  assert.equal(h.run("sceneQualityLabel('showcase',{width:2048,height:2048})"),'高清 2K');
});

test('unchecking the background pass is saved with the plan and old plans stay untouched',async t=>{
  const a=previewProject();a.plan.look='showcase';a.plan.auto_polish=true;
  const h=await setup(t,{projects:[a,previewProject('b')]});await h.run("openProject('a')");
  h.node('autoPolish').checked=false;await h.run('gatherPlan()');
  assert.equal(h.run('draft.auto_polish'),false);
  await h.run("openProject('b')");
  assert.equal(h.node('autoPolish').checked,false);await h.run('gatherPlan()');
  assert.equal(h.run("Object.hasOwn(draft,'auto_polish')"),false,'a plan that never had the setting does not gain it by being opened');
});

test('the receipt says which agents worked on a request, from fixed codes only',async t=>{
  const p=project('a');p.instruction_result={id:'receipt-agents',text:'柜体是红色喷漆金属带反光',source:'ai',summary:'柜体改为带清漆层的红色喷漆金属。',notes:[],
    changes:[{path:'object_material_overrides.Body/BodyMaterial.coat',before:null,after:.75}],used_image:true,used_reference:false,status:'applied',shot_id:'hero',base_version:1,applied_version:1,
    agent_trace:[{agent:'planner',calls:2,ready:1,pending:1},
      {agent:'specialists',requirement:'r2',locator:{result:'located',targets:4,groups:1,confidence:.94},result:'resolved'},
      {agent:'specialists',requirement:'r3',locator:{result:'located',targets:1,groups:1,confidence:.8},finisher:{result:'chosen',fields:['roughness']},result:'rejected',reason:'<img src=x onerror=alert(1)>'},
      {agent:'specialists',calls:3},{agent:'executor',repairs:['localised_shared_material','<script>alert(2)</script>']}]};
  const h=await setup(t,{projects:[p]});await h.run("openProject('a')");
  const details=h.node('instructionReceiptDetails');
  assert.match(details.textContent,/自动处理：理解要求 2 轮 · 自动定位部件 2 项 · 自动确定质感 1 项 · 补全待定要求 1\/2 项 · 共用材质只改所指部件/);
  assert.equal(details.querySelector('img,script'),null);assert.doesNotMatch(details.textContent,/alert/);
  assert.equal(h.run("agentTraceLabel([])"),'');assert.equal(h.run("agentTraceLabel('x')"),'');
  assert.equal(h.run("agentTraceLabel([{agent:'planner',calls:1,ready:1,pending:0}])"),'自动处理：理解要求 1 轮');
  assert.equal(h.run("agentTraceLabel([{agent:'planner',calls:1,ready:1,pending:0},{agent:'verifier',checked:1,narrowed:1,removed:6,result:'narrowed'},{agent:'executor',repairs:['dropped_unbound_edit']}])"),
    '自动处理：理解要求 1 轮 · 核对定位，去掉 6 个不属于所指部位的部件 · 去掉未定位部件的修改');
  assert.equal(h.run("agentTraceLabel([{agent:'planner',calls:1,ready:2,pending:0},{agent:'verifier',checked:3,narrowed:0,removed:0,result:'confirmed'}])"),'自动处理：理解要求 1 轮 · 核对定位 3 组');
  assert.equal(h.run("agentTraceLabel([{agent:'planner',calls:1,ready:2,pending:0},{agent:'verifier',result:'unavailable',reason:'transport'}])"),'自动处理：理解要求 1 轮');
});

test('a requirement the agents could not finish shows their specific reason and what was tried',async t=>{
  const h=await setup(t,{projects:[previewProject()]});await h.run("openProject('a')");
  h.intercept=call=>call.url.endsWith('/instruct')?h.response({error:'还有要求暂未形成可执行的修改，原文已保留。',error_code:'instruction_targets_unresolved',remaining_text:call.body.text,
    pending:[{id:'r1',quote:'把抽屉滑轨换成黑色',label:'抽屉滑轨',status:'needs_clarification',action:'locate_part',reason:'已自动查找，模型的部件中没有找到这项要求所指的部位；可以补充它的位置或换一种说法。'}],
    agent_trace:[{agent:'planner',calls:1,ready:0,pending:1},{agent:'specialists',requirement:'r1',locator:{result:'unresolved',reason:'not_in_catalogue'},result:'pending'},{agent:'specialists',calls:1}]},400):undefined;
  h.node('instructionInput').value='把抽屉滑轨换成黑色';await h.run("applyInstructionAndPreview({preventDefault(){}})");await tick();await tick();
  const details=h.node('instructionProposalDetails').textContent;
  assert.match(details,/已自动查找，模型的部件中没有找到/);assert.match(details,/自动处理：理解要求 1 轮 · 补全待定要求 0\/1 项/);
  assert.equal(h.node('instructionInput').value,'把抽屉滑轨换成黑色','the request stays editable');
});

test('look-dev decisions are listed with their evidence in the inspection record',async t=>{
  const a=previewProject();a.plan.look='showcase';
  a.plan.lookdev={version:1,source:'names',summary:'亮面金属 ×2、亮面塑料 ×1',decisions:[
    {scope:'material',material:'亮光金属_6',finish:'bright_metal',label:'亮面金属',reason:'材质名「亮光金属_6」含「亮光金属」',parts:38},
    {scope:'part',object:'<img src=x onerror=alert(1)>',material:'default',finish:'bright_metal',label:'亮面金属',reason:'部件名含「key」'}]};
  const h=await setup(t,{projects:[a,previewProject('b')]});await h.run("openProject('a')");
  const content=h.node('inspectionContent');
  assert.match(content.textContent,/质感推断/);assert.match(content.textContent,/亮面金属 ×2、亮面塑料 ×1/);
  const record=content.querySelector('.lookdev-record');assert.ok(record);assert.equal(record.querySelectorAll('li').length,2);
  assert.match(record.textContent,/材质名「亮光金属_6」含「亮光金属」/);assert.match(record.textContent,/颜色保持不变/);
  assert.equal(content.querySelector('img'),null,'part names from the model file are never markup');
  await h.run("openProject('b')");assert.doesNotMatch(h.node('inspectionContent').textContent,/质感推断/);
});

test('home cards name the background pass and never call a 2K image 4K',async t=>{
  const working=previewProject('a');working.active_job={id:'polish-a',kind:'render',quality:'showcase',status:'running',progress:0,origin:'auto_polish'};
  const polished=previewProject('b');polished.outputs.push({job_id:'polish-b',shot_id:'hero',version:1,quality:'showcase',width:2048,height:2048,created_at:9,png_url:'/qa/synthetic-2k-b.png'});
  const h=await setup(t,{projects:[working,polished]});
  const cards=[...h.node('homeProjectGrid').querySelectorAll('.project-card')].map(card=>card.textContent);
  const text=cards.join('\n');
  assert.match(text,/正在生成高清图 · 后台继续/);assert.match(text,/1 张高清 2K/);assert.doesNotMatch(text,/4K/);
});

function sceneSet(overrides={}) {
  return {version:1,shot_id:'scene',setting:'workshop',source:'rules',summary:'工具柜 / 工具箱：台面 2 件、抽屉内 1 件',units_per_meter:2.7,seed:1,
    product:{max_dimension_mm:740,size_basis:'model_units'},open:{objects:['Drawer'],translation:[0,-.4,0]},skipped:[{concept:'work gloves',reason:'not_in_catalogue'}],
    props:[{id:'p1',asset_id:'adjustable_wrench',name:'Adjustable Wrench',file:'/qa/props/adjustable_wrench/a.gltf',zone:'on_top',pose:'flat',license:'CC0 1.0',provider:'Poly Haven',source_url:'https://polyhaven.com/a/adjustable_wrench'},
           {id:'p2',asset_id:'x',name:'<img src=x onerror=alert(1)>',file:'/qa/props/x/x.gltf',zone:'inside',pose:'flat',license:'CC0 1.0',provider:'Poly Haven',source_url:'javascript:alert(1)'}],...overrides};
}
// The server's scene routes: one task per request, a new plan version with the scene shot when it finishes.
function sceneServer(h) {
  h.intercept=call=>{
    const match=call.url.match(/^\/api\/projects\/([^/]+)\/scene(\/remove)?$/);if(!match)return undefined;
    const p=h.records.get(match[1]);
    if(call.body.expected_version!==p.version)return h.response({error:'方案已在后台更新'},409);
    if(match[2]){
      p.history.push({version:p.version,plan:copy(p.plan),at:1,reason:'移除使用场景'});delete p.plan.scene_set;p.plan.shots=p.plan.shots.filter(shot=>shot.id!=='scene');p.version++;return h.response(p);
    }
    p.scene_task={id:'scene-'+h.calls.length,project_id:p.id,status:'queued',stage:'等待布置场景',base_version:p.version,hint:call.body.hint};return h.response(p.scene_task,202);
  };
}
function finishScene(h,id='a') {
  const p=h.records.get(id);p.history.push({version:p.version,plan:copy(p.plan),at:1,reason:'生成使用场景'});
  p.plan.shots.push({id:'scene',label:'使用场景',kind:'full',style:'soft',projection:'perspective',azimuth:35,elevation:36,focal_length:55,camera_distance:5.2,target_offset:[0,0,0]});
  p.plan.scene_set=sceneSet();p.version++;p.scene_task={...p.scene_task,status:'completed',stage:'已完成',applied_version:p.version};
}

test('a usage scene is requested once with the plan version, then its shot is shown with the props and their licence',async t=>{
  const h=await setup(t,{projects:[previewProject()]});await h.run("openProject('a')");sceneServer(h);
  assert.equal(h.node('sceneButton').disabled,false);assert.equal(h.node('sceneDetails').hidden,true);assert.match(h.node('sceneButton').textContent,/生成使用场景图/);
  h.node('sceneHint').value='  车间里的工具柜  ';
  const pending=h.run('generateScene()');await h.run('generateScene()');await pending;
  const requests=h.calls.filter(call=>call.url==='/api/projects/a/scene' && call.method==='POST');
  assert.equal(requests.length,1,'a second click while submitting does not start another scene');
  assert.deepEqual(requests[0].body,{expected_version:1,hint:'车间里的工具柜'});
  assert.equal(h.node('sceneButton').disabled,true);assert.equal(h.node('sceneButton').getAttribute('aria-busy'),'true');assert.match(h.node('sceneStatus').textContent,/等待布置场景/);
  await h.run('generateScene()');assert.equal(h.calls.filter(call=>call.url==='/api/projects/a/scene' && call.method==='POST').length,1,'a running task is not submitted again');
  finishScene(h);await h.run('poll()');await tick();
  assert.equal(h.run('activeShot'),'scene','the finished scene is the picture on screen');assert.equal(h.run('current.version'),2);
  assert.equal(h.node('sceneDetails').hidden,false);assert.match(h.node('sceneButton').textContent,/换一组场景道具/);assert.equal(h.node('sceneButton').disabled,false);
  assert.match(h.node('sceneStatus').textContent,/台面 2 件/);assert.equal(h.node('sceneStatus').classList.contains('is-summary'),true,'a finished scene is described, not warned about');
  const rows=[...h.node('sceneProps').querySelectorAll('li')];assert.equal(rows.length,2);
  assert.match(rows[0].textContent,/Adjustable Wrench/);assert.match(rows[0].textContent,/台面/);assert.match(rows[0].textContent,/CC0/);
  assert.equal(rows[0].querySelector('a').href,'https://polyhaven.com/a/adjustable_wrench');assert.equal(rows[0].querySelector('a').rel,'noopener');
  assert.equal(rows[1].querySelector('a'),null,'only Poly Haven asset pages become links');assert.equal(h.node('sceneProps').querySelector('img'),null,'names are text, never markup');
  assert.match(rows[1].textContent,/抽屉内/);
  const note=h.node('sceneNote').textContent;assert.match(note,/内置品类规则/);assert.match(note,/740 mm/);assert.match(note,/已拉开抽屉/);assert.match(note,/未放入 1 项/);assert.match(note,/只出现在这个镜头里/);
});

test('removing a usage scene is one versioned change and returns to the main image',async t=>{
  const a=previewProject();const h=await setup(t,{projects:[a]});await h.run("openProject('a')");sceneServer(h);
  await h.run('generateScene()');finishScene(h);await h.run('poll()');await tick();assert.equal(h.run('activeShot'),'scene');
  await h.run('removeScene()');
  const removal=h.calls.filter(call=>call.url==='/api/projects/a/scene/remove');assert.equal(removal.length,1);assert.deepEqual(removal[0].body,{expected_version:2});
  assert.equal(h.run('activeShot'),'hero');assert.equal(h.run('current.version'),3);assert.equal(h.node('sceneDetails').hidden,true);
  assert.equal(h.run("shots().some(shot=>shot.id==='scene')"),false);assert.match(h.node('sceneButton').textContent,/生成使用场景图/);
  assert.equal(h.node('undoButton').disabled,false,'the scene can be brought back with undo');
});

test('the scene control explains why it is unavailable and reports a refused or failed request',async t=>{
  const h=await setup(t,{projects:[previewProject()]});await h.run("openProject('a')");
  h.run('system.runtime.capabilities.scene_set=false;renderProject()');
  assert.equal(h.node('sceneButton').disabled,true);assert.equal(h.node('sceneHint').disabled,true);assert.match(h.node('sceneStatus').textContent,/场景组件未就绪/);
  await h.run('generateScene()');assert.equal(h.calls.some(call=>/\/scene$/.test(call.url)),false);
  h.run('delete system.runtime.capabilities.scene_set;serviceDisconnected=true;renderProject()');
  assert.equal(h.node('sceneButton').disabled,true);assert.match(h.node('sceneStatus').textContent,/连接恢复后/);
  h.run('serviceDisconnected=false;renderProject()');assert.equal(h.node('sceneButton').disabled,false);
  h.intercept=call=>/\/scene$/.test(call.url)?h.response({error:'模型库暂时无法访问，请稍后重试。'},503):undefined;
  await h.run('generateScene()');
  assert.match(h.node('sceneStatus').textContent,/模型库暂时无法访问/);assert.equal(h.node('sceneStatus').classList.contains('is-summary'),false);assert.equal(h.node('sceneButton').disabled,false,'the request can be tried again');
  h.intercept=null;h.records.get('a').scene_task={id:'scene-f',status:'failed',stage:'未完成',error:'没有找到可用的抽屉或台面。',base_version:1};
  h.run('sceneErrors.clear()');await h.run('poll()');
  assert.match(h.node('sceneStatus').textContent,/没有找到可用的抽屉或台面/);assert.equal(h.run('activeShot'),'hero');
});

test('a render in progress locks the scene control and unsaved edits are saved before a scene is made',async t=>{
  const a=previewProject();a.jobs=[{id:'run',kind:'render',status:'running',quality:'final',shot_ids:['hero'],completed:[],version:1,created_at:5,stage:'Cycles 正在渲染',progress:20}];
  const h=await setup(t,{projects:[a,previewProject('b')]});await h.run("openProject('a')");
  assert.equal(h.node('sceneButton').disabled,true,'one Blender job at a time');
  await h.run("openProject('b')");sceneServer(h);
  h.run("draft.exposure=.4;dirty=true;renderProject()");assert.match(h.node('sceneButton').textContent,/保存并生成使用场景图/);
  await h.run('generateScene()');
  const writes=h.calls.filter(call=>call.method!=='GET').map(call=>call.url);
  assert.deepEqual(writes,['/api/projects/b/plan','/api/projects/b/scene'],'the edit is saved first, then the scene is requested for the saved version');
  assert.equal(h.calls.find(call=>call.url==='/api/projects/b/scene').body.expected_version,2);
});
