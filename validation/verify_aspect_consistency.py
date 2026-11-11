"""Compare executed preview/final settings and downloaded outputs."""
import json
from pathlib import Path
import requests
from PIL import Image,ImageChops,ImageStat

folder=Path(__file__).resolve().parent
path=folder/'aspect-results.json'
report=json.loads(path.read_text(encoding='utf-8'))
assert report.get('passed'), 'Wait for aspect validation to finish first'
comparisons=[]
for aspect in ('4:3','3:4'):
    entries=[x for x in report['checks'] if x['aspect']==aspect]
    preview=next(x for x in entries if x['quality']=='preview')
    final=next(x for x in entries if x['quality']=='standard')
    settings=[]
    for row in (preview,final):
        response=requests.get('http://127.0.0.1:8765'+row['output']['result_url'],timeout=30)
        response.raise_for_status();settings.append(response.json())
    matches={key:settings[0][key]==settings[1][key] for key in ('camera','studio','operations')}
    assert all(matches.values()),matches
    with Image.open(preview['download_checks']['png']['file']) as a,Image.open(final['download_checks']['png']['file']) as b:
        a=a.convert('RGB');b=b.convert('RGB').resize(a.size,Image.Resampling.LANCZOS)
        error=sum(ImageStat.Stat(ImageChops.difference(a,b)).mean)/3/255
    comparisons.append({'aspect':aspect,'matching_settings':matches,'normalized_mean_absolute_srgb_difference':error,
                        'preview_render_seconds':settings[0]['render_seconds'],'standard_render_seconds':settings[1]['render_seconds']})
report['preview_final_consistency']=comparisons
report['cpu_probe']=json.loads((folder/'cpu-probe/result.json').read_text(encoding='utf-8'))
path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(comparisons,ensure_ascii=False,indent=2))
