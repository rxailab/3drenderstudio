import json
from pathlib import Path
from PIL import Image,ImageDraw
root=Path(__file__).resolve().parents[1]
results=json.loads((root/'acceptance_results.json').read_text(encoding='utf-8'))
corrected=root/'validation/final-label-results.json'
if corrected.exists():
    project=json.loads(corrected.read_text(encoding='utf-8'))['project']
    results[0]['outputs']=[o for o in project['outputs'] if o['quality']=='preview' and o['shot_id']=='hero']
sheet=Image.new('RGB',(1536,1100),'#e9edf2');draw=ImageDraw.Draw(sheet)
for i,entry in enumerate(results):
    if not entry.get('outputs'):continue
    path=root/'data'/entry['outputs'][-1]['png_url'][7:]
    with Image.open(path) as im:
        im=im.convert('RGB');im.thumbnail((500,500));x=(i%3)*512+(512-im.width)//2;y=(i//3)*550
        sheet.paste(im,(x,y));draw.text(((i%3)*512+14,y+514),entry['example']['id'],fill='#182b46')
(root/'validation').mkdir(exist_ok=True)
sheet.save(root/'validation/contact-sheet.png')
