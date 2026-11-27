"""Apply Three's ACES sRGB transfer to the product, preserving the photographed background."""
import bpy
import json


def restore_legacy(scene):
    state=json.loads(scene['wuying_viewport_legacy_composite'])
    tree=scene.node_tree
    for node in list(tree.nodes):
        if node.get('wuying_viewport_composite_node'):
            tree.nodes.remove(node)
    if state['connection']:
        source,port,destination=state['connection']
        tree.links.new(tree.nodes[source].outputs[port],tree.nodes[destination].inputs[0])
    scene.use_nodes=state['enabled']
    del scene['wuying_viewport_legacy_composite']


def make_compositor(scene,plate,exposure):
    modern=hasattr(scene,'compositing_node_group')
    if modern:
        tree=bpy.data.node_groups.new('Viewport photographic composite','CompositorNodeTree')
        tree.interface.new_socket(name='Image',in_out='OUTPUT',socket_type='NodeSocketColor')
        scene.compositing_node_group=tree
        output=tree.nodes.new('NodeGroupOutput')
    else:
        was_enabled=scene.use_nodes
        scene.use_nodes=True
        tree=scene.node_tree
        existing=set(tree.nodes)
        output=tree.nodes.get('Composite') or tree.nodes.new('CompositorNodeComposite')
        previous=next(iter(output.inputs[0].links),None)
        connection=[previous.from_node.name,previous.from_socket.name,output.name] if previous else None
        scene['wuying_viewport_legacy_composite']=json.dumps(dict(enabled=was_enabled,connection=connection))
    scene.render.use_compositing=True
    scene.render.film_transparent=True
    scene.view_settings.view_transform='Standard'
    scene.view_settings.look='None'
    scene.view_settings.gamma=1
    scene.view_settings.exposure=0
    layers=tree.nodes.new('CompositorNodeRLayers')
    straight=tree.nodes.new('CompositorNodePremulKey')
    if modern:
        straight.inputs['Type'].default_value='To Straight'
    else:
        straight.mapping='PREMUL_TO_STRAIGHT'
    tree.links.new(layers.outputs['Image'],straight.inputs[0])
    separate=tree.nodes.new('CompositorNodeSeparateColor')
    tree.links.new(straight.outputs[0],separate.inputs[0])

    def math_node(op,a,b):
        node=tree.nodes.new('ShaderNodeMath' if modern else 'CompositorNodeMath')
        node.operation=op
        for socket,arg in zip(node.inputs,(a,b)):
            if isinstance(arg,(float,int)):
                socket.default_value=arg
            else:
                tree.links.new(arg,socket)
        return node.outputs[0]

    def matrix(rgb,rows):
        result=[]
        for row in rows:
            parts=[math_node('MULTIPLY',ch,scale) for ch,scale in zip(rgb,row)]
            result.append(math_node('ADD',math_node('ADD',parts[0],parts[1]),parts[2]))
        return result

    # ACESFilmicToneMapping and RRTAndODTFit, as used by the actual WebGL
    # renderer. The plate lands after display mapping just as in the viewport.
    scale=2**float(exposure)/.6
    rgb=matrix(separate.outputs[:3],[[v*scale for v in row] for row in (
        (.59719,.35458,.04823),(.07600,.90834,.01566),(.02840,.13383,.83777))])
    fitted=[]
    for v in rgb:
        a=math_node('SUBTRACT',math_node('MULTIPLY',v,math_node('ADD',v,.0245786)),.000090537)
        b=math_node('ADD',math_node('MULTIPLY',v,math_node('ADD',math_node('MULTIPLY',v,.983729),.432951)),.238081)
        fitted.append(math_node('DIVIDE',a,b))
    rgb=matrix(fitted,((1.60475,-.53108,-.07367),(-.10208,1.10813,-.00605),(-.00327,-.07276,1.07602)))
    combine=tree.nodes.new('CompositorNodeCombineColor')
    for value,socket in zip(rgb,combine.inputs):
        tree.links.new(math_node('MINIMUM',math_node('MAXIMUM',value,0),1),socket)
    tree.links.new(layers.outputs['Alpha'],combine.inputs['Alpha'])
    over=tree.nodes.new('CompositorNodeAlphaOver')
    if modern:
        background,foreground=over.inputs['Background'],over.inputs['Foreground']
        over.inputs['Straight Alpha'].default_value=True
    else:
        background,foreground=over.inputs[1],over.inputs[2]
        over.use_premultiply=True
    background.default_value=(1,1,1,1)
    if plate:
        im=tree.nodes.new('CompositorNodeImage')
        im.image=plate
        size=tree.nodes.new('CompositorNodeScale')
        if modern:
            size.inputs['Type'].default_value='Render Size'
            size.inputs['Frame Type'].default_value='Crop'
        else:
            size.space='RENDER_SIZE'
            size.frame_method='CROP'
        tree.links.new(im.outputs['Image'],size.inputs['Image'])
        tree.links.new(size.outputs[0],background)
    tree.links.new(combine.outputs[0],foreground)
    tree.links.new(over.outputs[0],output.inputs[0])
    if not modern:
        for node in set(tree.nodes)-existing:
            node['wuying_viewport_composite_node']=True
