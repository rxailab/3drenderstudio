// Run: node validation/frontend-review-actions-tests.js
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const html=fs.readFileSync('static/index.html','utf8'),source=fs.readFileSync('static/app.js','utf8');
const ids=[...html.matchAll(/\bid="([^"]+)"/g)].map(match=>match[1]);
assert.equal(new Set(ids).size,ids.length);
for(const match of source.matchAll(/\$\('([^']+)'\)/g))assert(ids.includes(match[1]),'Missing element '+match[1]);
for(const obsolete of ['reviewActionsPanel','reviewCauseChoices','reviewApplyButton','reviewProposeButton'])assert(!ids.includes(obsolete),'Manual diagnostic action removed: '+obsolete);
assert(!html.includes('需要你确认'));assert(!html.includes('选择要处理的建议'));
const elements=Object.fromEntries(ids.map(id=>[id,{value:'',checked:false,disabled:false,hidden:false,textContent:'',innerHTML:'',style:{},classList:{toggle(){}},setAttribute(){}}]));
let calls=[],response;
const context=vm.createContext({console,document:{getElementById:id=>elements[id]},FormData:class{},clearTimeout(){},setTimeout(){},fetch:async(path,options)=>{calls.push({path,body:JSON.parse(options.body)});return {ok:true,json:async()=>response};}});
vm.runInContext(source.replace(/start\(\)\.catch\(e=>toast\(e.message,true\)\);\s*$/,''),context);
const run=code=>vm.runInContext(code,context);
run(`current={id:'p',version:4,jobs:[],plan:{shots:[{id:'hero'}]}};currentOutput={job_id:'render',shot_id:'hero',quality:'preview',version:4,review:{checked_at:123}};renderReview=()=>{};renderProject=()=>{};setInspectorTab=()=>{};`);
elements.realismQuestion.value='台面反光更明显';
async function main(){
 response={id:'p',version:4,jobs:[{id:'j',status:'queued'}],plan:{shots:[{id:'hero'}]}};
 await run('requestRealismReview({preventDefault(){}})');
 assert.equal(calls[0].path,'/api/projects/p/review');assert.equal(calls[0].body.question,'台面反光更明显');
 assert.equal(calls[0].body.output_id,'render');assert.equal(calls[0].body.version,4);
 assert(elements.toast.textContent.includes('正在执行自动优化'));
 run('current.jobs=[]');response={id:'p',version:4,jobs:[],plan:{shots:[{id:'hero'}]}};
 await run('requestRealismReview({preventDefault(){}})');assert(elements.toast.textContent.includes('没有启动自动修改'));
 run('current.jobs=[{status:"running"}]');await run('requestRealismReview({preventDefault(){}})');assert.equal(calls.length,2,'No duplicate requests while rendering');
 run('current.jobs=[];comparePrevious=true');await run('requestRealismReview({preventDefault(){}})');assert.equal(calls.length,2,'Even a same-version historical comparison cannot trigger an edit');
 assert.equal(run('partSelectionBlocked()'),true,'Historical comparison cannot edit materials');
 console.log('Automatic diagnostic UI passed: no manual selection or approval, natural-language request, real job-aware status and duplicate guard.');
}
main().catch(e=>{console.error(e);process.exitCode=1});
