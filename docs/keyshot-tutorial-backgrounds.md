# KeyShot 教学场景的背景与布光

本机 KeyShot Studio 欢迎页的 Demo Scenes 栏收录 19 个工程，均有本地缓存的官方配图。另有一个随程序安装的耳机欢迎教程。下表逐项记录这 20 个场景。编号沿用本机欢迎页顺序，预设 ID 用于对应物影主视图。缩略图反映其中一个构图，多环境、多镜头工程可能保存其他外观。

| 序号 | 官方场景名 | 官方配图中可见的背景、光影 | 随场景提供的相关资源 | 物影预设 |
| --- | --- | --- | --- | --- |
| 1 | Animation - Keyboard Mockup | 键盘悬在蓝灰背景前，地面下半部变浅，按键边缘有明亮反射 | 图标与教学图层 | `blue-keyboard` |
| 2 | Caustics - Cologne | 暖木圆柱间明暗交错，玻璃香水瓶与台面出现集中的暖色亮斑 | 浴室 HDR、木色与木纹法线 | `amber-caustics` |
| 3 | Planar Light & Light Positioning - Visune | 冷灰台面与灰墙相接，明亮的斜向光带掠过产品底部 | 皮革贴图、分步教学前景图 | `silver-beam` |
| 4 | Light Layers – Cologne Bottle | 象牙白台座、金黄玻璃与弧面反光，背景有枝叶状明暗条纹 | Palm04 灰度叶影图、两张标签图 | `cologne` |
| 5 | KS101_1 Models | 黑灰摄影棚里的圆台，台面边缘有光，中心有青色光带 | 两个 startup HDR 环境 | `charcoal-stage` |
| 6 | KS101_2 Materials | 银灰渐变墙前有多级白色圆台，台阶柔和的明暗差使轮廓清楚 | startup HDR | `silver-studio` |
| 7 | KS101_3 Lights | 石膏头像从纯黑底中浮现，脸部一侧和发丝上缘受光 | 教学卡片，目录中没有单独的 HDR 图 | `black-jewelry` |
| 8 | Randomize Nodes – Wood | 高而窄的暖木走廊，敞口照亮墙底，纵向格栅形成重复阴影 | 公寓 HDR、两套木色与表面贴图 | `wood-interior` |
| 9 | Mold-Tech - Headphones | 耳机放在深灰圆台上，背景有低调的冷色光晕，阴影偏向一侧 | 现代空仓库 HDR | `charcoal-headphones` |
| 10 | Interpon & Color Mode - Lamborghini | 暗色车身与红色轮辋近景，地面粗糙、车漆与轮辐上有窄反光 | 现代空仓库 HDR、混凝土贴图 | `dark-garage` |
| 11 | Sharpen - Lounge Bench | 暖木窗框和靠垫，窗边侧光令织物与木纹可见 | 楼梯间 HDR、木地板与织物图 | `lounge-daylight` |
| 12 | Camera Keyframe Animation - Orbit Interpolation | 深暗背景、灰色岩面托起宝石，切面和金属镶边有紧凑的亮斑 | 浴室 HDR、灰泥表面贴图 | `black-orbit` |
| 13 | Camera Keyframe Animation - Path Interpolation | 白色无缝棚中的正面赛车，车轮下有柔和接触影 | 城市巷道 HDR | `white-car` |
| 14 | Coffee-Shop | 墨绿墙板与靠窗餐桌，右窗处更亮，斜向日光扫过墙和椅面 | 阴影体育馆 HDR、startup HDR、木料与织物图 | `cafe-window` |
| 15 | 3D Paint - Table | 浅灰背景和连续地面，矮桌与漆桶之间保留柔和阴影 | 楼梯间 HDR、startup HDR | `silver-table` |
| 16 | CMF NB Shoe Demo Scene | 白色运动鞋置于近纯白背景，鞋底有窄接触影，轮廓可清楚辨认 | 工业厨房 HDR、针织与网面图 | `white-shoe` |
| 17 | Physics Ball&Chain | 深蓝灰背景和较亮的台面，左右透明球的边缘有冷暖不同的高光 | 木质底座表面贴图 | `blue-night` |
| 18 | Web Viewer Bush Radio | 接近中性的浅灰背景，黑白收音机下方有很轻的接触影 | studio_small_03_4k.exr | `white-radio` |
| 19 | 3DPaint_DemoScene | 工具箱置于工业工作室，背后可见货架和木托板，暖光打亮箱盖与地面 | startup HDR、工业灯具模型表面图、木托板贴图 | `loft-atelier` |
| 欢迎 | Welcome Tour / scene 1 | 黑色耳机悬在中性灰渐变底前，背景上深下浅 | backplate.png、耳罩穿孔图、startup.hdr 的场景引用 | `welcome` |

## 香水瓶与欢迎教程的工程参数

香水瓶的独立工程和 KSP 中的 BIP 均为 6,561,218 字节。环境记录保存白色背景、开启的地面阴影、关闭的地面反射及遮蔽阴影。棕榈图是 4096 × 4096 的灰度纹理，场景另有模拟棚拍反光的实体灯。下表保留着色器中存储的功率字段值，灯具单位和归一化方式须随类型解释。

| 工程灯名 | 着色器 | power 原值 | 作用线索 |
| --- | --- | ---: | --- |
| Area Light - Top | lux_mesh_light | 2000 | 顶部主光 |
| Area Light - Highlight | lux_mesh_light | 1000 | 瓶身高光 |
| Spotlight - Textured | lux_spotlight | 850 | 图案投射 |
| Planar Light - Label Logo | lux_arealight | 80 | 灯宽原值 0.008，照亮标识 |
| Area Light - Label | lux_mesh_light | 500 | 补亮正面标签 |

耳机欢迎工程的背景图 `backplate.png` 为 1500 × 1500。背景类型原值为 2，地面阴影、反射及遮蔽阴影均为 0。沿启用的环境节点追踪，4000 × 2000 环境画布使用纯黑底色和四个白光斑。工程另存有 `startup.hdr` 字符串。以下四个光斑均启用，旋转为 0°，光刷 gamma 为 1。

| 环境光斑 | 方位 / 仰角 | 白光增益 | 几何原值 | 边缘衰减 |
| --- | --- | ---: | --- | --- |
| Pin #1 | 109.41° / 37.47° | 3 | shape 1，半径 18.72° | 0.3 |
| Pin #2 | −8.42° / −21.90° | 6 | shape 0，宽 21.00°、高 13.21° | 0.1 |
| Pin #4 | 21.39° / 37.40° | 1 | shape 1，半径 45.90° | 0.1 |
| Pin #5 | 132.76° / −18.77° | 2 | shape 1，半径 15.00° | 0.1 |

## 逐例核得的灯具和环境设置

环境栏优先列出源文件中的 `map.gain` 和 `map.rotation`。旧工程的渲染节点另存有 `multiplier` 和变换矩阵。多个环境同时存在时，只有引用可对应到具体文件的值才可确定。灯具一栏记录保存的材质或环境节点，具体镜头可再选择灯光和模型组。不同灯型的 power 原值有各自的单位选项。

| 案例 | 环境增益与旋转原值或运行时引用 | 可确认的灯光或地面 |
| --- | --- | --- |
| 1 键盘 | 0.3 / 353.5° | Planar Light Material 与 Planar Light Material 1 的 power 为 700、240，地面阴影开启 |
| 2 香水焦散 | 0.078 / 51° | Spotlight Material 110，环境光斑 Copy Pin #1 已启用 |
| 3 Visune | 1 / 360° | 2 Backdrop 1100、4 Left Backlight 1000、3 Right Highlight 600、1 Top Down 450；工程的两个 Overhead 环境光斑均开启 |
| 4 香水光层 | 1 / 0° | 五个物理灯，详见上表 |
| 5 模型课 | 两个设置为 1 和 0.83333，旋转均为 0°；与两张 startup 图的逐一绑定未定 | 两个面光的 power 为 400 和 500，聚光为 1000 |
| 6 材质课 | 渲染节点引用 startup.hdr，可见 multiplier 为 1 和 0.916667 | 两个面光 500 和 100，聚光 3000 |
| 7 灯光课 | 1 / 360°，黑色背景 | 两个同名 Area Light Material #2，power 为 50 和 120 |
| 8 木廊 | 1.5 / 240.5° | SpotLight 1000，Area Light Material #1 为 500000（lumen 标志 1） |
| 9 耳机 | 0.138 / 299.5° | Spotlight Material 650，地面展平开启 |
| 10 汽车 | 1.2 / 92° | Spotlight Material 2000，Area Light Material 45000（lumen 标志 1） |
| 11 长椅 | 1.5 / 329° | 地面阴影开启 |
| 12 珠宝 | 渲染节点的 current_texture 为内部 :1516.dhdr，multiplier 为 1；随包浴室 HDR 与该内部图的关系未定 | 地面阴影开启 |
| 13 赛车 | current_texture 指向城市巷道 HDR，multiplier 为 1；user_transform 首行为 [−0.608761, 0, 0.793353, 0] | 地面阴影开启 |
| 14 咖啡馆 | 包含 1、1.252 和 0°、305° 两组设置，随包体育馆 HDR 和 startup HDR 的逐一绑定未定 | 两个面光 10000 与 400000，lumen 标志均为 1 |
| 15 边桌 | 随包两张 HDR；各自增益与旋转未定 | Area Light Material 2000（lumen 标志 1），地面阴影开启 |
| 16 鞋 | current_texture 指向工业厨房 HDR 的节点 multiplier 为 1.6786；user_transform 首行为 [−0.165048, 0, −0.986286, 0] | 地面阴影关闭 |
| 17 物理演示 | 旧版背景模式为 2 | 文件内发现名为 Spotlight 的场景节点，功率未定；地面阴影开启 |
| 18 收音机 | 包含 0.795、0.535 和 200°、267°；单张随包 EXR 存在两个环境设定，各自的使用镜头未定 | 地面阴影开启 |
| 19 工具间 | 渲染节点引用内部 :1078.dhdr，multiplier 为 1；随包另有 startup.hdr | 地面阴影开启 |

## 物影主视图

主视图默认使用「材质入门 · 银灰柔光」。完整导入后有 58 个选项，包括白底、20 个教学预设和 37 张本机图库背景。每个教学预设对应一个参考镜位。香水叶影用工程内的 Palm04 纹理重建，耳机欢迎例使用原始背景图和重建的四光斑环境。其余教学底图依照封面构图制作或改编。匹配到的 HDR 单独用于模型照明及反射，镜头可以保留参考图中的白色、灰色或黑色背景。[背景与环境生成器](../scripts/build_studio_backdrops.py)

收音机案例官方 EXR 的 SHA-256 为 `d0f4e9a7268836a9714169e05b2a5d3965e398b733f081bc77e8b95e35b25a8f`。生成器也能读取由它转换的 Radiance RGBE 环境图。

## 来源与检验

- 案例名单取自 `%LOCALAPPDATA%\Luxion\KeyShot\startup\offline\welcome.xml` 中的 `demoscenes/demoscene`。逐项查看同目录 `imgs` 的官方缩略图。索引 SHA-256 为 `f2b30fda4cabc7b6aa119936b5a9ad033bf39976359df345aa4768160fbb493a`。欢迎页还单独列有十个模板。
- 19 个官方 KSP 的 ZIP 中央目录均通过 HTTP 206 请求核对。核对记录包括各工程的官方地址、缩略图 SHA-256、目录成员、CRC32、压缩位置及长度。
- 案例 1、3、4 和欢迎教程的完整 BIP 已核对 ZIP 成员长度及 CRC32。其余案例以 HTTP Range 抽查 BIP 压缩尾段。从可独立对齐的 DEFLATE 块读至成员结尾，用全零、全 FF 两套初始字典校验输出，仅读取两次字节完全一致且类型和长度匹配的 DAL 字段。档案中保留尾段 SHA-256 或完整 BIP 校验值。未命中的灯具不作为缺失证据。
- 已使用 REA 检查本机安装中完整的香水教学工程和耳机欢迎工程。
