"""Build the local viewport backplates and downsample the matching lighting panoramas."""
import argparse
import json
import re
import struct
import subprocess
from tempfile import TemporaryDirectory
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import cv2
import numpy as np
from PIL import Image, ImageFilter, ImageOps


ROOT = Path(__file__).resolve().parents[1]
BASE = '/static/studio-backdrops/'
SIZE = (1728, 1152)
# Camera backgrounds and reflection environments are independent in the demos.
# Values here are relative to Wuying's product lighting rig.
CASES = [
    # id, display name, source scene, environment, key/fill/rim/ambient, yaw
    ('cologne', '香水 · 象牙棕榈光影', 'Light Layers – Cologne Bottle', '', (1.05,.85,1.3,.85,-10)),
    ('blue-keyboard', '键盘 · 雾蓝悬浮', 'Animation - Keyboard Mockup', '', (.92,.87,1.22,.85,12)),
    ('amber-caustics', '古龙水 · 琥珀暖木', 'Caustics - Cologne', 'Aversis_Bathroom_3k.hdz', (1.32,.48,1.04,.78,-32)),
    ('silver-beam', '面光源 · 银灰斜光', 'Planar Light & Light Positioning - Visune', '', (1.26,.55,1.02,.76,34)),
    ('charcoal-stage', '模型入门 · 炭灰展台', 'KS101_1 Models', 'startup.hdr', (1.15,.45,1.18,.55,-18)),
    ('silver-studio', '材质入门 · 银灰柔光', 'KS101_2 Materials', 'startup.hdr', (.91,1.12,.91,1.1,8)),
    ('black-jewelry', '灯光入门 · 黑棚轮廓', 'KS101_3 Lights', '', (1.14,.18,1.76,.3,42)),
    ('wood-interior', '木饰面 · 暖木室内', 'Randomize Nodes – Wood', 'Dosch-Apartment_2k.hdz', (1.05,.71,.88,.95,-38)),
    ('charcoal-headphones', '耳机 · 石墨聚光', 'Mold-Tech - Headphones', 'hdrmaps-empty-modern-storehouse-3K.hdz', (1.05,.38,1.44,.55,38)),
    ('dark-garage', '跑车 · 暗调车库', 'Interpon & Color Mode - Lamborghini', 'hdrmaps-empty-modern-storehouse-3K.hdz', (1.12,.34,1.58,.62,-22)),
    ('lounge-daylight', '长椅 · 窗边木台', 'Sharpen - Lounge Bench', 'Dosch-Stairwell_2k.hdz', (1.22,.69,1.03,.9,25)),
    ('black-orbit', '珠宝 · 黑绒聚光', 'Camera Keyframe Animation - Orbit Interpolation', 'Aversis_Bathroom_3k.hdz', (1.18,.25,1.59,.45,-34)),
    ('white-car', '赛车 · 暖白无缝棚', 'Camera Keyframe Animation - Path Interpolation', 'Aversis_City-alley-old_3k.hdz', (.99,.92,1.13,.97,16)),
    ('cafe-window', '咖啡店 · 深绿窗光', 'Coffee-Shop', 'hdrmaps-sports-hall-in-the-shadow-3K.hdz', (1.24,.6,.92,.82,-36)),
    ('silver-table', '边桌 · 柔灰渐变', '3D Paint - Table', 'Dosch-Stairwell_2k.hdz', (.87,1.04,.96,.96,-18)),
    ('white-shoe', '运动鞋 · 纯白柔光', 'CMF NB Shoe Demo Scene', 'Aversis_Industrial-kitchen_3k.hdz', (.92,1.17,.75,1,23)),
    ('blue-night', '摆球 · 冷蓝夜棚', 'Physics Ball&Chain', '', (.84,.39,1.55,.61,-15)),
    ('white-radio', '收音机 · 雾白棚', 'Web Viewer Bush Radio', 'studio_small_03_4k.exr', (.96,.98,1.04,.95,-10)),
    ('loft-atelier', '工作室 · 暖灰侧光', '3DPaint_DemoScene', 'startup.hdr', (1.13,.66,1.05,.82,42)),
    ('welcome', '欢迎教程 · 暖灰渐变', 'Welcome Tour', '', (1,1,1,1,0)),
]
PLATE_NAMES = {
    'white solid vignette':'白色暗角','studio_backdrop_white':'白色渐层','studio_backdrop_grey':'中灰渐层',
    'studio_backdrop_black':'黑色渐层','studio backdrop':'无缝摄影棚','concrete wall 1':'清水混凝土',
    'concrete wall 2':'浅灰水泥墙','corridor 1':'车站通道','corridor 2':'蓝光走廊','corridor 3':'暗灰长廊',
    'garage 1':'地下车库','garage 2':'冷光停车场','hdri-locations_industrial_1':'工业厂房 1',
    'hdri-locations_industrial_2':'工业厂房 2','kitchen counter':'厨房石材台面','tunnel':'拱顶隧道',
    'warehouse':'日光仓库','wood table':'浅木桌面','bench':'户外长椅','desert road':'沙漠公路',
    'dosch-design_dirt-lot':'城市空地','hdri-locations_forestroad':'林间公路',
    'hdri-locations_iceland':'冰岛荒原','hdri-locations_urban':'城市街角','outdoor 1':'雪线山坡',
    'parking lot':'桥下停车场','road 1':'沙漠双黄线','road 2':'山路落日','road 3':'田野公路',
    'steps':'台阶光影','underpass':'高架桥下',
}
GROUPS = {'Studio':'摄影棚', 'Interior':'室内', 'Outdoor':'户外'}
# These plates use only the gradients and light shapes in studio_plate().
# Their bundled radiance is always built from the generated plate itself.
BUILTIN_CASES = {'blue-keyboard', 'silver-beam', 'charcoal-stage', 'silver-studio',
                 'black-jewelry', 'charcoal-headphones', 'white-car', 'silver-table',
                 'white-shoe', 'blue-night', 'white-radio'}
# Values read from the demo BIPs. Where a scene saves several environments
# this is the one selected for this viewport interpretation.
ENV_SETTINGS = {
    'blue-keyboard':(.3,353.5), 'silver-beam':(1,360), 'cologne':(1,0), 'black-jewelry':(1,360),
    'amber-caustics':(.078,51), 'charcoal-stage':(1,0), 'wood-interior':(1.5,240.5),
    'charcoal-headphones':(.138,299.5), 'dark-garage':(1.2,92),
    'lounge-daylight':(1.5,329), 'cafe-window':(1.252,305), 'white-radio':(.795,200),
    # These older projects store a Y-axis transform: atan2(m02, m00).
    'white-car':(1,127.5), 'white-shoe':(1.6786,260.5),
}


def rgb_hex(values):
    return '#' + ''.join(f'{int(v):02x}' for v in np.clip(values,0,255))


def panorama(path):
    raw = path.read_bytes()
    if path.suffix.lower() == '.hdz':
        if not raw.startswith(b'hdzlux'):
            raise ValueError(f'Unrecognized HDZ image {path}')
        width, height = struct.unpack_from('<II',raw,16)
        scan = b'\x02\x02'+struct.pack('>H',width)
        start = raw.index(scan,24,128)
        header = f'#?RADIANCE\nFORMAT=32-bit_rle_rgbe\n\n-Y {height} +X {width}\n'.encode()
        raw = header + raw[start:]
    image = cv2.imdecode(np.frombuffer(raw,np.uint8), cv2.IMREAD_UNCHANGED)
    if image is None or image.ndim != 3:
        raise ValueError(f'Cannot decode environment {path}')
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    return rgb


def save_lighting(path, output):
    rgb = cv2.resize(panorama(path),(128,64),interpolation=cv2.INTER_AREA)
    luminance = rgb @ np.array([.2126,.7152,.0722])
    # Limit only the very brightest emitters after downsampling, keeping the
    # direction and dynamic range of the windows and panels.
    rgb *= .48 / max(np.percentile(luminance,65),.01)
    peak = rgb.max(axis=2,keepdims=True)
    rgb *= np.minimum(1,32/np.maximum(peak,.001))
    slug = re.sub('[^a-z0-9]+','-', path.stem.lower()).strip('-')
    name = f'environments/{slug}.bin'
    (output/name).write_bytes(rgb.astype('<f4').tobytes())
    return BASE+name


def welcome_lighting(output):
    """Rebuild the selected welcome environment from its four enabled white pins."""
    u,v=np.meshgrid((np.arange(512)+.5)/512,(np.arange(256)+.5)/256)
    az,el=(u-.5)*2*np.pi,(.5-v)*np.pi
    directions=np.stack((np.cos(el)*np.cos(az),np.sin(el),np.cos(el)*np.sin(az)),axis=-1)
    energy=np.zeros_like(u)
    # azimuth, elevation, gain, disk radius / rectangle width, height, falloff
    pins=[(109.41,37.47,3,18.72,None,.3),(-8.42,-21.90,6,21,13.20755,.1),
          (21.39,37.40,1,45.9,None,.1),(132.76,-18.77,2,15,None,.1)]
    for azimuth,elevation,gain,size,height,falloff in pins:
        a,b=np.deg2rad([azimuth,elevation])
        normal=np.array([np.cos(b)*np.cos(a),np.sin(b),np.cos(b)*np.sin(a)])
        front=directions @ normal
        if height is None:
            distance=np.arccos(np.clip(front,-1,1))/np.deg2rad(size)
        else:
            right=np.array([-np.sin(a),0,np.cos(a)])
            up=np.cross(right,normal)
            dx=np.abs(np.arctan2(directions @ right,front))/np.deg2rad(size/2)
            dy=np.abs(np.arctan2(directions @ up,front))/np.deg2rad(height/2)
            distance=np.maximum(dx,dy)
        energy+=gain*(1-smooth(distance-1+falloff/2,falloff))
    sky=cv2.resize(energy,(128,64),interpolation=cv2.INTER_AREA)
    name='environments/welcome-pins.bin'
    np.repeat(sky[:,:,None],3,axis=2).astype('<f4').tofile(output/name)
    return BASE+name


def plate_path(library, prefix):
    matches=[p for p in library.rglob('*.jpg') if p.stem.startswith(prefix)]
    if not matches:
        raise FileNotFoundError(f'Missing backplate: {prefix}')
    return min(matches,key=lambda p:('16x9' not in p.stem,p.as_posix()))


def color_grade(photo, multiplier, bias=(0,0,0)):
    return np.asarray(photo,dtype=np.float32)*np.array(multiplier)+np.array(bias)


def window_room(library, x, y):
    room=panorama(library.parent/'Environments'/'Interior'/'Dosch-Apartment_2k.hdz')
    h,w=room.shape[:2]
    xx=(x-.5)*1.46
    yy=(.5-y)*.97-.12
    longitude=np.arctan2(xx,1)+.78
    latitude=np.arctan2(yy,np.sqrt(1+xx**2))
    u=((longitude/(2*np.pi)+.5)*w).astype('float32')
    v=((.5-latitude/np.pi)*h).astype('float32')
    view=cv2.remap(room,u,v,cv2.INTER_LINEAR,borderMode=cv2.BORDER_WRAP)
    view*=.53/max(np.percentile(view,58),.03)
    return 255*np.power(view/(1+view),1/2.2)


def smooth(edge, width):
    t = np.clip(edge/width+.5,0,1)
    return t*t*(3-2*t)


def studio_plate(ident, library, gobo):
    w,h=SIZE
    x,y=np.meshgrid(np.linspace(0,1,w,dtype=np.float32),np.linspace(0,1,h,dtype=np.float32))
    def gradient(dark, light, field):
        return np.asarray(dark)+(np.asarray(light)-dark)*np.clip(field,0,1)[:,:,None]
    def spot(cx,cy,sx,sy):
        return np.exp(-((x-cx)/sx)**2-((y-cy)/sy)**2)
    def source(prefix):
        with Image.open(plate_path(library,prefix)) as im:
            return ImageOps.fit(im.convert('RGB'), SIZE,method=Image.Resampling.LANCZOS)
    if ident=='cologne':
        if gobo is None:
            raise FileNotFoundError('Missing palm gobo in the Cologne demo')
        # Enlarge the photographed palm gobo; the crop shows individual leaves
        # and soft shadows on the cyc. The curved glass edges have no product baked in.
        palm=ImageOps.fit(gobo.convert('L'),(w,h),centering=(.74,.26))
        palm=palm.resize((w*2,h*2),Image.Resampling.BILINEAR).crop((w//4,h//4,w//4+w,h//4+h))
        shade=1-np.asarray(palm.filter(ImageFilter.GaussianBlur(13)),np.float32)/255
        wall=1-smooth(y-.64,.46)
        field=.44+.30*spot(.84,.20,.62,.64)+.20*y+.14*spot(.42,.82,.39,.29)-.41*shade*wall
        for edge in [x-(.79-.51*np.sqrt(y+.045)), x-(.06+.21*y-.09*(y-.55)**2)]:
            field+=.25*np.exp(-(edge/.0022)**2)-.065*np.exp(-((edge-.009)/.013)**2)
        a=gradient((134,127,105),(251,244,218),field)
    elif ident=='wood-interior':
        a=window_room(library,x,y)
    elif ident in ('lounge-daylight','loft-atelier','dark-garage'):
        prefix={'lounge-daylight':'Wood Table','loft-atelier':'hdri-locations_industrial_1','dark-garage':'Garage 2'}[ident]
        src=source(prefix)
        if ident=='dark-garage':
            a=color_grade(src,(.72,.70,.67),(3,3,4))
        elif ident=='loft-atelier':
            a=color_grade(src,(.94,.81,.69),(12,12,14))
        else:
            shadow=smooth(np.sin(7.7*(x+.50*y)+.35)-.56,.37)
            level=.87-.17*shadow+.09*x
            a=color_grade(src,(1,.88,.74))*level[:,:,None]+(18,12,9)
    elif ident=='amber-caustics':
        # The installed texture library includes the 1K color map from the
        # same Wood003 set used at 2K in the Caustics demo.
        with Image.open(library.parent/'Textures'/'Wood'/'Wood003_1K_Color.jpg') as im:
            plank=ImageOps.fit(im.rotate(-18,resample=Image.Resampling.BICUBIC,expand=True),
                               (w+240,h+240),method=Image.Resampling.LANCZOS)
            wood=np.asarray(plank.crop((120,120,120+w,120+h)).convert('L'),dtype=np.float32)/255
        rays=.25*smooth(np.sin((x-.37*y)*10)-.3,.38)
        caustic=.13*np.exp(-((np.hypot((x-.72)*.94,(y-.75)*1.14)-.27)/.023)**2)*spot(.59,.68,.23,.28)
        a=gradient((51,28,19),(240,173,108),.25+.52*wood+.24*spot(.32,.18,.65,.68)-rays+caustic)
    elif ident=='cafe-window':
        a=np.asarray(source('Kitchen Counter'),dtype=np.float32)
        # Preserve the photographed copperware and counter while tinting the
        # neutral tile glaze and projecting broad window light across it.
        saturation=a.max(axis=2)-a.min(axis=2)
        tile=(1-smooth(saturation-38,25))*(1-smooth(y-.58,.12))
        a*=1-tile[:,:,None]*np.array((.67,.44,.55))
        light=smooth(x+.43*y-.48,.04)-smooth(x+.43*y-.81,.04)
        a=a*(.77+.21*light[:,:,None])+np.array((9,5,1))
    elif ident=='silver-beam':
        beam=np.exp(-((y-.46-.29*x)/.080)**4)
        a=gradient((111,109,102),(253,251,242),.39+.30*spot(.73,.17,.51,.76)+.32*beam+.08*y)
    elif ident in ('black-jewelry','black-orbit'):
        if ident=='black-jewelry':
            a=np.zeros((h,w,3),dtype=np.uint8)
        else:
            with Image.open(library.parent/'Textures'/'Color Maps'/'black_granite.jpg') as im:
                grain=np.asarray(ImageOps.fit(im.convert('L'),(w,h)),dtype=np.float32)/255
            floor=smooth(y-.67,.30)*(.30+.68*spot(.56,.87,.50,.29))
            a=gradient((5,6,8),(121,120,127),.035+floor*(.14+.75*grain))
    elif ident in ('charcoal-stage','charcoal-headphones'):
        side=.69 if ident=='charcoal-headphones' else .32
        a=gradient((16,18,20),(107,115,116),.46*spot(side,.49,.42,.45)+.24*spot(.52,.81,.44,.18))
        if ident=='charcoal-stage':
            line=np.exp(-((y-.73-.036*(x-.52)**2)/.014)**2)*np.exp(-((x-.54)/.36)**4)
            a+=line[:,:,None]*np.array((0,47,46))
    elif ident in ('blue-keyboard','blue-night'):
        if ident=='blue-night':
            a=gradient((16,26,41),(142,170,192),.53*spot(.46,.75,.85,.27)+.27*y)
        else:
            a=gradient((51,78,119),(164,217,245),.20*y+.62*smooth(y-.58-.11*x,.38)+.14*spot(.23,.85,.44,.24))
    elif ident in ('silver-table','silver-studio'):
        side=.78 if ident=='silver-table' else .36
        a=gradient((148,147,147),(245,243,238),.24+.45*spot(side,.49,.63,.72)+.24*y)
    elif ident in ('white-car','white-shoe','white-radio'):
        base={'white-car':(230,225,219),'white-shoe':(255,255,255),'white-radio':(228,228,232)}[ident]
        a=gradient(base,(255,255,255),.41+.44*spot(.53,.42,.61,.77))
    else:
        raise ValueError(ident)
    return Image.fromarray(np.clip(np.rint(a),0,255).astype('uint8'))


def save_plate(output, ident, image):
    path=f'images/{ident}.webp'
    thumb=f'thumbs/{ident}.webp'
    image=image.convert('RGB')
    image.thumbnail((1920,1920),Image.Resampling.LANCZOS)
    image.save(output/path,quality=87,method=4)
    ImageOps.fit(image,(240,144),method=Image.Resampling.LANCZOS).save(output/thumb,quality=84)
    return {'image':BASE+path,'thumbnail':BASE+thumb}


def photometry(image):
    sample=np.asarray(image.convert('RGB').resize((48,32)),dtype=float)
    sky=sample[:18].mean(axis=(0,1))
    floor=sample[23:].mean(axis=(0,1))
    yaw=np.clip((sample[:18,24:].mean()-sample[:18,:24].mean())*.48,-36,36)
    return {'floorColor':rgb_hex(floor),'environmentColor':rgb_hex(.82*sky+30)},int(yaw)


def demo_image(package, select):
    if package and package.is_file():
        with ZipFile(package) as archive:
            member=next((name for name in archive.namelist() if select(name)),None)
            if member:
                with Image.open(BytesIO(archive.read(member))) as image:
                    return image.convert('RGB')
    return None


def build(installation, output, external_envs=None, *, builtin=False):
    if not builtin and not (installation/'Backplates').is_dir() and not (installation/'Environments').is_dir():
        raise ValueError('请选择含有 Backplates 或 Environments 的 KeyShot 资源目录')
    if builtin:
        output=output/'builtin'
    for subdir in ['images','thumbs','environments']:
        (output/subdir).mkdir(parents=True,exist_ok=True)
    library=installation/'Backplates' if not builtin else None
    envs={p.name:p for p in (installation/'Environments').rglob('*') if p.is_file()} if not builtin else {}
    if not builtin and external_envs and external_envs.is_dir():
        envs.update({p.name:p for p in external_envs.iterdir() if p.is_file()})
        # The Radio panorama is also archived in RGBE so that a build of
        # OpenCV without OpenEXR can reproduce its viewport texture.
        if 'studio_small_03_4k.hdr' in envs:
            envs['studio_small_03_4k.exr']=envs['studio_small_03_4k.hdr']
    gobo=welcome=None
    if not builtin:
        package=next((installation/'Scenes'/'Demos').glob('Light+Layers*.ksp'),None)
        gobo=demo_image(package,lambda name:'palm' in name.lower())
        welcome=demo_image(installation/'bin'/'welcome_tour.ksp',lambda name:name=='backplate.png')
    previous={}
    if not builtin and (output/'catalog.json').is_file():
        previous={p['id']:p for p in json.loads((output/'catalog.json').read_text(encoding='utf8'))['presets']}

    def available(preset):
        return all((output/url[len(BASE):]).is_file() for field in ('image','thumbnail','environmentMap','radianceMap')
                   if (url:=preset.get(field)))

    presets=[dict(id='white',name='白底',group='摄影棚',image=None,thumbnail=None,
                  environmentColor='#ffffff',floorColor='#ffffff',lighting=lighting((1,1,1,1,0)))]
    env_urls={}
    for ident,name,example,env,lights in CASES:
        if builtin and ident not in BUILTIN_CASES:
            continue
        try:
            plate=welcome if ident=='welcome' else studio_plate(ident,library,gobo)
            if plate is None:
                raise FileNotFoundError('Missing welcome backplate')
        except FileNotFoundError as error:
            print(f'{name}: {error}')
            continue
        colors,_=photometry(plate)
        preset=dict(id=ident,name=name,group='教学案例',examples=[example],lighting=lighting(lights),
                    **save_plate(output,ident,plate),**colors)
        if not builtin and env in envs:
            if env not in env_urls:
                env_urls[env]=save_lighting(envs[env],output)
            preset.update(environmentMap=env_urls[env],environmentSource=env)
        elif not builtin and env:
            old=previous.get(ident,{})
            if old.get('environmentMap') and available(old):
                preset.update(environmentMap=old['environmentMap'],environmentSource=env)
            else:
                print(f'{name}: {env} unavailable, using lighting generated from its plate')
        if ident in ENV_SETTINGS:
            preset['environmentGain'],preset['environmentRotation']=ENV_SETTINGS[ident]
        if ident in ('welcome','white-shoe'):
            preset['groundShadow']=0
        if ident=='welcome':
            preset.update(environmentMap=welcome_lighting(output),environmentSource='scene 1.bip / HDRI pins',
                          environmentGain=1,environmentRotation=0)
        presets.append(preset)
    for folder,group in ([] if builtin else GROUPS.items()):
        for path in sorted((library/folder).glob('*.jpg')):
            ident='plate-'+re.sub('[^a-z0-9]+','-',path.stem.lower()).strip('-')
            name=next((label for prefix,label in PLATE_NAMES.items() if path.stem.lower().startswith(prefix)),path.stem)
            ratio=re.search(r'(\d+)x(\d+)$',path.stem)
            if ratio:
                name+=' · '+'∶'.join(ratio.groups())
            with Image.open(path) as im:
                colors,yaw=photometry(im)
                powers={'Studio':(1,.90,1.04,.94),'Interior':(1.08,.81,1.02,.88),'Outdoor':(1.14,.92,.92,.98)}
                presets.append(dict(id=ident,name=name,group=group,source=path.relative_to(installation).as_posix(),
                                    lighting=lighting((*powers[folder],yaw)),**colors,**save_plate(output,ident,im)))
    assert len({p['id'] for p in presets})==len(presets)
    with TemporaryDirectory() as tmp:
        work=Path(tmp)
        for preset in presets:
            if preset['image'] and not preset.get('environmentMap'):
                with Image.open(output/'images'/Path(preset['image']).name) as im:
                    photo=im.convert('RGBA').resize((128,64),Image.Resampling.BOX)
                    (work/(preset['id']+'.rgba')).write_bytes(photo.tobytes())
            preset['radianceMap']=BASE+'radiance/'+preset['id']+'.bin'
        (work/'presets.json').write_text(json.dumps(presets),encoding='utf8')
        subprocess.run(['node',str(ROOT/'scripts'/'build_studio_radiance.mjs'),str(output),str(work)],check=True)
    if builtin:
        for preset in presets:
            for field in ('image','thumbnail','radianceMap'):
                if preset.get(field):
                    preset[field]=preset[field].replace(BASE,BASE+'builtin/',1)
    else:
        current={p['id'] for p in presets}
        presets.extend(p for ident,p in previous.items() if ident not in current and available(p))
    data={'version':2,'default':'silver-studio','presets':presets}
    temp=output/'catalog.json.tmp'
    temp.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    temp.replace(output/'catalog.json')
    return presets


def lighting(values):
    return dict(zip(('key','fill','rim','ambient','yaw'),values))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    source=parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--installation',type=Path,help='Local KeyShot resource directory containing Backplates and Environments')
    source.add_argument('--builtin',action='store_true',help='Rebuild the bundled procedural plates and lighting without reading KeyShot assets')
    parser.add_argument('--output',type=Path,default=ROOT/'static'/'studio-backdrops')
    parser.add_argument('--external-envs',type=Path,default=ROOT/'KeyShot提取'/'环境',
                        help='Additional source panoramas from the demo packages')
    args=parser.parse_args()
    print(len(build(args.installation,args.output,args.external_envs,builtin=args.builtin)), 'viewport presets')
