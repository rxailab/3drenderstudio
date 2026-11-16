const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/app.js','utf8');
const state = source.slice(source.indexOf('function tileProgress('),source.indexOf('function renderRenderActivity()'));
function harness() {
  const context={current:{id:'a',jobs:[],analysis:{},status:'ready',initial_preview:{status:'completed'}},materialAgentRecords:new Map(),materialTaskIsActive:()=>false,activeJob:()=>null,polishJob:()=>null,automaticMaterialBusy:()=>false,sceneTaskActive:()=>false,sceneSubmitting:false,instructionSubmitting:false,instructionProjectId:'a',instructionProgress:null,activeShot:'hero',comparePrevious:false,currentOutput:null,$:()=>({hidden:true}),sceneShotLabel:id=>id,serviceDisconnected:false};
  vm.createContext(context);vm.runInContext(state,context);return context;
}
test('shows the real background thumbnail sample, without treating it as whole-job progress',()=>{
  const c=harness();c.current.jobs=[{status:'running',origin:'auto_thumbnails',kind:'preview',shot_ids:['side','back'],current_shot:'side',sample_progress:42,stage:'渲染中'}];
  const activity=c.renderActivityState();assert.equal(activity.actor,'Blender 渲染');assert.match(activity.title,/缩略图/);assert.match(activity.metric,/采样 42%/);assert.equal(activity.scope,'background');
});
test('shows actual scene and instruction stages without inventing percentages',()=>{
  const c=harness();c.current.scene_task={status:'running',stage:'下载道具'};c.sceneTaskActive=()=>true;
  let activity=c.renderActivityState();assert.equal(activity.actor,'场景生成');assert.match(activity.stage,/下载道具/);assert.equal(activity.progress,null);
  c.sceneTaskActive=()=>false;c.instructionSubmitting=true;c.instructionProgress={agent:'材质 Agent',stage:'核对材质'};
  activity=c.renderActivityState();assert.equal(activity.actor,'材质 Agent');assert.match(activity.stage,/核对材质/);assert.equal(activity.progress,null);
  c.instructionProgress=null;activity=c.renderActivityState();assert.equal(activity.actor,'调整请求');
  c.serviceDisconnected=true;activity=c.renderActivityState();assert.equal(activity.state,'disconnected');assert.equal(activity.progress,null);
});
test('instruction polling uses the reported stage and ignores a late response after completion',async()=>{
  const polling=source.slice(source.indexOf('let instructionProgress=null'),source.indexOf('function instructionPendingDetails('));
  const functions=polling.slice(0,polling.indexOf('\n}'))+'\n}';
  let resolve, scheduled;
  const c={current:{id:'a'},instructionSubmitting:true,api:()=>new Promise(r=>{resolve=r;}),renderRenderActivity:()=>{},setTimeout:f=>(scheduled=f,1),clearTimeout:()=>{scheduled=null;}};
  vm.createContext(c);vm.runInContext(functions,c);c.startInstructionProgress('a');
  resolve({activity:{agent:'规划 Agent',stage:'规划修改'}});await new Promise(setImmediate);
  assert.equal(vm.runInContext('instructionProgress.agent',c),'规划 Agent');assert.ok(scheduled);
  scheduled();c.stopInstructionProgress();resolve({activity:{agent:'过期',stage:'过期'}});await new Promise(setImmediate);
  assert.equal(vm.runInContext('instructionProgress',c),null);
});
