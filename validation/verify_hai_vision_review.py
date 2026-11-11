"""Exercise the real structured vision review using a real saved Blender result."""
import copy
import json
import sys
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import ai_config,engine
from studio_store import DATA,get

project=get('project','fb50b3a68d194f28af84b5d7b598599f')
output=copy.deepcopy(next(o for o in reversed(project['outputs']) if o['shot_id']=='hero' and o['quality']=='preview'))
result=json.loads((DATA/output['result_url'][7:]).read_text(encoding='utf-8'))
config={**ai_config.get_config(),'provider':'openai','base_url':'https://api.hai.network/openai',
        'model':'gpt-5.5-2026-04-23','enabled':True,'inherits':False}
with patch.object(ai_config,'get_vision_config',return_value=config), patch.object(ai_config,'record_vision_result'):
    review=engine.diagnose_render(output,result,output['effective_plan'],project['analysis'],
        question='检查台面金属反光与红色柜体表面反光，并说明能安全自动改善的部分。')
assert review.get('source')=='ai',review.get('needs_user')
assert review.get('model')==config['model'] and isinstance(review.get('realism',{}).get('causes'),list)
report={'passed':True,'model':review['model'],'provider':review['provider'],'project':project['id'],
        'actual_preview':output['png_url'],'summary':review['realism']['summary'],
        'causes':review['realism']['causes'],'score':review['score'],'patch':review['patch'],
        'material_requests':review.get('material_requests',[])}
(ROOT/'validation/hai-vision-structured-results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False))
