// Run: node validation/frontend-material-tests.js (no server or AI required).
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const html=fs.readFileSync('static/index.html','utf8'),source=fs.readFileSync('static/app.js','utf8');
const ids=[...html.matchAll(/\bid="([^"]+)"/g)].map(match=>match[1]);
assert.equal(new Set(ids).size,ids.length);
for(const match of source.matchAll(/\$\('([^']+)'\)/g))assert(ids.includes(match[1]),'Missing element '+match[1]);
const elements=Object.fromEntries(ids.map(id=>[id,{value:'',checked:false,disabled:false,hidden:false,textContent:'',innerHTML:'',style:{},classList:{toggle(){}},setAttribute(){},getBoundingClientRect(){return {left:0,top:0,width:1000,height:500};},reportValidity(){return true;}}]));
elements.renderImage.hidden=true;
let calls=[],serverProject;
const context=vm.createContext({console,document:{getElementById:id=>elements[id]},FormData:class{},clearTimeout(){},setTimeout(){},crypto:{randomUUID:()=>String(Math.random())},fetch:async(path,options)=>{calls.push({path,body:JSON.parse(options.body)});return {ok:true,json:async()=>serverProject};}});
vm.runInContext(source.replace(/start\(\)\.catch\(e=>toast\(e.message,true\)\);\s*$/,''),context);
const run=code=>vm.runInContext(code,context),plain=value=>JSON.parse(JSON.stringify(value));
run(`current={id:'p',version:2,jobs:[],analysis:{},plan:{shots:[{id:'hero',label:'主图'}],object_material_overrides:[]}};
currentOutput={job_id:'j',shot_id:'hero',version:2,quality:'preview'};
feedbackSource={projectId:'p',key:sceneOutputKey(currentOutput),snapshot:feedbackSnapshot()};feedbackMode=true;
system={ai_mode:'rules'};
feedbackPoints=[{id:'a',x:.4,y:.5,hit:true,object_name:'Door',material_name:'red',material_info:{color:'#a52a2a',roughness:.3,metallic:.5,transmission:0,ior:1.45,linked:{color:true},textures:[{name:'Brand label'}],warnings:[]}},
{id:'b',x:.7,y:.5,hit:true,object_name:'Side',material_name:'red',material_info:{color:'#8b2020',roughness:.7,metallic:.5,transmission:0,ior:1.45,linked:{},textures:[],warnings:[]}}];renderFeedback();`);
assert.equal(elements.partRoughness.value,'','Mixed values are not silently replaced with zero');
assert(elements.partRoughnessValue.textContent.includes('混合值'));
assert(elements.partColorValue.textContent.includes('节点/贴图驱动'));
assert(elements.partMaterialResources.innerHTML.includes('Brand label'));
assert.equal(elements.partMetallic.value,.5);
assert.equal(elements.partMaterialSubmit.disabled,true,'Reading parameters cannot submit an implicit modification');
assert.equal(elements.feedbackSubmit.disabled,true,'Language edits still require AI');

run("partMaterialFields.add('roughness');");elements.partRoughness.value='0.6';elements.partRoughnessUse.checked=true;
run('renderPartMaterial()');
assert.equal(elements.partMaterialSubmit.disabled,false,'Direct edit is available without AI');
assert.deepEqual(plain(run('gatherPartMaterialPatch()')),{roughness:.6},'Color/label must not be overwritten by reading a swatch');
run('renderPartMaterial()');
assert.equal(elements.partRoughness.value,'0.6','Periodic rendering preserves the edited value');
elements.partRoughness.value='';assert.throws(()=>run('gatherPartMaterialPatch()'),/混合值/);
elements.partRoughness.value='1.5';assert.throws(()=>run('gatherPartMaterialPatch()'),/0–1/);
elements.partRoughness.value='0.6';
run("partMaterialFields.add('color');");
assert.throws(()=>run('gatherPartMaterialPatch()'),/目标颜色/,'A mixed color placeholder is never an implicit new color');
run("partMaterialValueFields.add('color');");elements.partColor.value='#1d3178';
assert.deepEqual(plain(run('gatherPartMaterialPatch()')),{roughness:.6,color:'#1d3178'});
run("feedbackPoints.push({...feedbackPoints[0],id:'c',x:.42});renderPartMaterial();");
assert.equal(run('uniqueMaterialSelections().length'),2,'Same mesh/slot selected twice is counted once');
assert.equal(run('partMaterialFields.size'),0,'Changing target set clears unsubmitted field choices');
run('currentOutput.version=1;renderPartMaterial();');
assert.equal(elements.partRoughness.disabled,true,'Old version permits inspection only');
run('currentOutput.version=2;dirty=true;renderPartMaterial();');assert.equal(elements.partMaterialSubmit.disabled,true);
run('dirty=false;');
run("feedbackPoints[0].material_info.editable=false;renderPartMaterial();partMaterialFields.add('roughness');");elements.partRoughness.value='0.5';run('renderPartMaterial()');
assert.equal(elements.partMaterialSubmit.disabled,true,'Complex shaders require an explicit preset');
run("partMaterialFields.add('preset');");elements.partPreset.value='matte_plastic';run('renderPartMaterial()');
assert.equal(elements.partMaterialSubmit.disabled,false);
run('delete feedbackPoints[0].material_info.editable;renderPartMaterial();');

async function main(){
  run("feedbackPoints=feedbackPoints.slice(0,2);renderPartMaterial();partMaterialFields.add('roughness');");elements.partRoughness.value='0.62';
  run('renderPartMaterial()');
  serverProject={id:'p',version:3,jobs:[{id:'new-job',status:'queued'}],analysis:{},plan:{shots:[{id:'hero'}],object_material_overrides:[]}};
  run('renderProject=()=>{};syncSettings=()=>{};');
  await run('submitPartMaterial(false)');
  assert.equal(calls[0].path,'/api/projects/p/materials/selection/apply');
  assert.equal(calls[0].body.version,2);
  assert.deepEqual(calls[0].body.patch,{roughness:.62});
  assert.deepEqual(Object.keys(calls[0].body.points[0]).sort(),['id','x','y'],'Never send client object names as edit authority');
  assert.equal(run('current.version'),3);assert.equal(run('feedbackPoints.length'),0);
  // A diagnostic question is a read-only review request, not a plan edit.
  run("current.jobs=[];currentOutput={job_id:'j2',shot_id:'hero',version:3,quality:'preview'};");
  elements.realismQuestion.value='为什么渲染不够逼真？';calls=[];
  serverProject={...serverProject,jobs:[]};
  await run('requestRealismReview({preventDefault(){}})');
  assert.equal(calls[0].path,'/api/projects/p/review');assert.equal(calls[0].body.question,'为什么渲染不够逼真？');
  assert(!('patch' in calls[0].body));
  run(`currentOutput.review={source:'ai',mode:'diagnostic_only',realism:{summary:'反光缺乏层次',causes:[{aspect:'金属',evidence:'<script>bad</script>',confidence:.8,suggestion:'增加轮廓光',requires_user:true}],priority_actions:['核对粗糙度']}};renderReview();`);
  assert(elements.reviewDetails.innerHTML.includes('&lt;script&gt;'));
  assert(!elements.reviewDetails.innerHTML.includes('<script>'));
  assert(elements.reviewDetails.innerHTML.includes('核对粗糙度'));
  console.log('Material UI checks passed: mixed/current values, texture guards, explicit patches, no-key editing, snapshot binding and read-only realism questions.');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
