// The 2-second poll in static/app.js against a stand-in fetch: slim library rows, If-None-Match, no re-render on 304.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('static/app.js','utf8');
const between=(start,end)=>source.slice(source.indexOf(start),source.indexOf(end));
const code=[between('const receivedTags=new WeakMap();','function toast('),between('let receivedList=null;','function leaveProjectView()'),
  between('async function poll()','async function resumeInstructionPreflight()'),between('function projectCardStatus(p)','function renderHomeProjects()')].join('\n');

function harness(answers) {
  const c={calls:[],renders:{library:0,project:0,tasks:0},statusNames:{ready:'可开始拍摄'},deletedProjectIds:new Set(),libraryRequest:0,projectRequest:0,reviewRevision:0,
    polling:false,busy:false,planSavePromise:null,projectLoading:false,realismBusy:false,materialAgentSubmitting:false,dirty:false,styleFocusNotice:'',
    current:null,allProjects:[],system:{available:true},serviceDisconnected:false,
    updateSystemStatus(){},renderActionState(){},resumeInstructionPreflight:async()=>{},scheduleThumbnails(){},
    api:async path=>({available:true,path}),
    Error,JSON,Number,Math,Object,WeakMap};
  c.renderLibrary=()=>c.renders.library++;c.renderProject=()=>c.renders.project++;c.refreshMaterialTasks=async()=>c.renders.tasks++;
  c.fetch=async(path,options={})=>{
    const sent=options.headers?.['If-None-Match'] || null;c.calls.push([path,sent]);
    const [etag,body]=answers[path]();
    if(sent && sent===etag)return {status:304,ok:false,headers:{get:()=>etag},json:async()=>{throw new Error('empty');}};
    return {status:200,ok:true,headers:{get:name=>name==='ETag'?etag:null},json:async()=>JSON.parse(JSON.stringify(body))};
  };
  vm.createContext(c);vm.runInContext(code,c);return c;
}

test('the home poll asks for the slim list and keeps the library untouched while it is unchanged',async()=>{
  let tag='"l1"',rows=[{id:'a',name:'A',status:'ready',created_at:1,outputs:[]},{id:'b',name:'B',status:'ready',created_at:2,outputs:[]}];
  const c=harness({'/api/projects?fields=summary':()=>[tag,rows]});
  await c.poll();
  assert.deepEqual(c.calls,[['/api/projects?fields=summary',null]]);
  assert.deepEqual(c.allProjects.map(p=>p.id),['b','a']);assert.equal(c.renders.library,1);
  const held=c.allProjects;await c.poll();
  assert.deepEqual(c.calls.at(-1),['/api/projects?fields=summary','"l1"']);
  assert.equal(c.allProjects,held);assert.equal(c.renders.library,1);
  tag='"l2"';rows=[...rows,{id:'c',name:'C',status:'ready',created_at:3,outputs:[]}];await c.poll();
  assert.deepEqual(c.allProjects.map(p=>p.id),['c','b','a']);assert.equal(c.renders.library,2);
  // A local change to the list (a delete) means the next refresh is unconditional.
  c.allProjects=c.allProjects.filter(p=>p.id!=='c');await c.poll();
  assert.deepEqual(c.calls.at(-1),['/api/projects?fields=summary',null]);
});

test('an open project is re-rendered only when the server says it changed, and progress keeps moving',async()=>{
  let tag='"p1"',project={id:'a',name:'A',version:2,plan:{style:'clean',shots:[]},outputs:[],jobs:[{id:'j',status:'running',progress:10}]};
  const c=harness({'/api/projects/a':()=>[tag,project]});
  c.current={id:'a',version:2,plan:{style:'clean'},outputs:[]};c.allProjects=[{id:'a'}];
  await c.poll();
  assert.deepEqual(c.calls,[['/api/projects/a',null]]);assert.equal(c.current.jobs[0].progress,10);
  assert.deepEqual([c.renders.project,c.renders.library,c.renders.tasks],[1,1,1]);
  const held=c.current;await c.poll();
  assert.deepEqual(c.calls.at(-1),['/api/projects/a','"p1"']);
  assert.equal(c.current,held);assert.deepEqual([c.renders.project,c.renders.library,c.renders.tasks],[1,1,2]);
  tag='"p2"';project={...project,jobs:[{id:'j',status:'running',progress:55}]};await c.poll();
  assert.equal(c.current.jobs[0].progress,55);assert.equal(c.renders.project,2);
  // Any other code that replaces current (a save, an open) drops the tag: the next poll fetches in full.
  c.current={...c.current};await c.poll();
  assert.deepEqual(c.calls.at(-1),['/api/projects/a',null]);
});

test('home cards read the slim fields the same way as a full record',()=>{
  const c=harness({});
  const slim={status:'failed',analyzed:false,shot_count:3,outputs:[]},full={status:'failed',analysis:null,plan:{shots:[{},{},{}]},outputs:[]};
  assert.deepEqual(c.projectCardStatus(slim),c.projectCardStatus(full));
  const done={status:'ready',outputs:[{quality:'standard'},{quality:'thumbnail'}]};
  assert.deepEqual(c.projectCardStatus({...done,analyzed:true,shot_count:3}),c.projectCardStatus({...done,analysis:{},plan:{shots:[{},{},{}]}}));
  assert.equal(c.projectCardStatus({...done,analyzed:true,shot_count:3}).text,'1 张 4K 成品 · 3 个镜头');
});
