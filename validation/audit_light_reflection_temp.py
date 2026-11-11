import bpy, mathutils
from pathlib import Path
from mathutils import Vector
path=Path(r'D:/模型渲染/data/fb50b3a68d194f28af84b5d7b598599f/runs/81bd61bfccdc48718efb7edffbf712ae/08_G07C_G02M_combo_hero_v12/08_G07C_G02M_combo_hero_v12.blend')
bpy.ops.wm.open_mainfile(filepath=str(path),load_ui=False,use_scripts=False)
ob=bpy.data.objects['G-不锈钢台面_2']; camera=bpy.context.scene.camera
points=[ob.matrix_world@Vector(v) for v in ob.bound_box]
hi=max(p.z for p in points)
pt=Vector((sum(p.x for p in points)/8,sum(p.y for p in points)/8,hi))
view=(camera.location-pt).normalized(); norm=Vector((0,0,1)); ray=(2*view.dot(norm)*norm-view).normalized()
print('AUDIT point',tuple(pt),'view',tuple(view),'reflected direction',tuple(ray))
for name in ('Key softbox','Fill softbox','Edge softbox','Top reflection'):
 lamp=bpy.data.objects[name]; world=lamp.matrix_world; n=world.to_quaternion()@Vector((0,0,1)); denom=ray.dot(n)
 if abs(denom)<1e-6: print('AUDIT',name,'parallel');continue
 t=(lamp.location-pt).dot(n)/denom
 hit=world.inverted()@(pt+t*ray); d=lamp.data; sx=d.size/2;sy=(d.size_y if d.shape in ('RECTANGLE','ELLIPSE') else d.size)/2
 print('AUDIT',name,'t',round(t,2),'hitxy',round(hit.x,2),round(hit.y,2),'halfsizes',round(sx,2),round(sy,2),'within bounds',t>0 and abs(hit.x)<=sx and abs(hit.y)<=sy)
