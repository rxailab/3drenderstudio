import bpy,math
from pathlib import Path
bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
bpy.ops.import_scene.gltf(filepath=str(Path(__file__).resolve().parents[1]/'matte-pump-bottle.glb'))
for o in bpy.context.scene.objects:
 if o.type=='MESH' and ('MORA' in o.name or 'substrate' in o.name):
  pts=[o.matrix_world@v.co for v in o.data.vertices]
  cent=[o.matrix_world@p.center for p in o.data.polygons]
  print(o.name,'verts',len(pts),'radius min max',min(math.hypot(v.x,v.y)/.65 for v in pts),max(math.hypot(v.x,v.y)/.65 for v in pts),'centers',min(math.hypot(v.x,v.y)/.65 for v in cent),max(math.hypot(v.x,v.y)/.65 for v in cent))
