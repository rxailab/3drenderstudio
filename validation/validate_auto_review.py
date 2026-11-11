"""Actual Blender acceptance, intentionally overexposed opaque product."""
import copy
import json
import sys
import uuid
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import engine
from planner import default_plan
from studio_store import ROOT,DATA,get,put,atomic_json

ident=uuid.uuid4().hex
plan=default_plan();plan['exposure']=4;plan['visual_review']=True;plan['max_corrections']=2
p={'id':ident,'name':'自动曝光真实验收','slug':'auto-exposure-validation','version':1,'plan':plan,'history':[],
   'model_path':str(ROOT/'samples/matte-pump-bottle.glb'),'outputs':[],'metadata':{},'status':'new'}
put('project',p)
job=engine.submit(ident,'analyze');engine.run_job(job['id'])
assert get('job',job['id'])['status']=='completed',get('job',job['id']).get('error')
project=get('project',ident);project['plan']['shots']=project['plan']['shots'][:1];put('project',project)
with patch('ai_config.get_config',return_value={'enabled':False}):
    job=engine.submit(ident,'preview');engine.run_job(job['id'])
result=get('job',job['id']);project=get('project',ident)
assert result['status']=='completed',result.get('error')
assert result.get('adopted_version')==2,result
original=next(o for o in project['outputs'] if o['version']==1)
adopted=next(o for o in project['outputs'] if o['version']==2)
assert len(adopted['corrections'])<=3
assert adopted['review']['score']>original['review']['score']
points=[{'id':'center','x':.5,'y':.5},{'id':'background','x':.02,'y':.02}]
picked=engine.pick_output(ident,{'output_id':adopted['job_id'],'shot_id':adopted['shot_id'],'version':adopted['version'],'quality':'preview','points':points})
assert picked['hits'][0]['hit'] and not picked['hits'][1]['hit'],picked
assert engine.effective_plan(project['plan'],'hero')['exposure']==adopted['effective_plan']['exposure']
atomic_json(ROOT/'validation/auto-review-results.json',{'project_id':ident,'job':result,'original':original,'adopted':adopted,'picks':picked,'final_uses_same_effective_settings':True,'visual_ai_tested':False})
print(json.dumps({'project_id':ident,'status':result['status'],'adopted_version':result['adopted_version'],'rounds':len(adopted['corrections'])-1,'picks':picked},ensure_ascii=True))
