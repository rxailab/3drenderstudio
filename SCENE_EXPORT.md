# Blender 与 Cinema 4D 场景工程导出

生成预览或成品后，在图片下方点击「导出场景工程」，选择已渲染的镜头、版本与画质，再选择 Blender 或 Cinema 4D。导出使用该图片的已保存场景，不重新渲染，不会把未生成预览的界面调整混入工程。

## Blender

下载工程ZIP后打开 `scene.blend`。原生工程保留原始模型层级、材质节点、相机、灯光、姿态、现有绑定/动画及渲染配置，贴图已打包。导出器实际重新打开工程，校验模型、相机和灯光记录；原来的工程文件保持不变。工程ZIP另包含交换用scene.json、FBX（若导出成功）、纹理和来源快照，便于其他工具继续处理。

已用实际塑料瓶、金属盘和带贴图手提包工程验证。本次浏览器也已实际下载工程ZIP，下载后CRC校验通过。

## Cinema 4D

原生转换使用官方 `c4dpy` 与 `SaveDocument(..., FORMAT_C4DEXPORT)`，保存后必须通过 Cinema 4D `LoadDocument` 重新打开和结构检查，应用才显示“工程已完成”和 `.c4d` 下载链接。FBX 不会改名伪装为 `.c4d`。

在 `.env` 中可设置：

```ini
C4DPY_PATH=C:\Program Files\Maxon Cinema 4D R24\c4dpy.exe
C4D_EXPORT_TIMEOUT=120
```

要求对应 Cinema 4D 安装和可用的 **c4dpy 授权**。GUI应用可用不一定代表命令行授权已经初始化。本机实际探测 R22 和 R24 均返回 `No License Model defined`，因此此次尚未生成或验证任何原生 `.c4d`。请按 Maxon 官方方式在本机完成对应软件的授权，再重新导出；应用不会收集账号密码或修改授权设置。

授权不可用或没有安装 c4dpy 时，应用仍会生成「手动转换包」，并把原生导出标为失败。该ZIP是可用的转换材料，不是已生成的C4D工程：

1. 解压ZIP，保留 `scene.json`、`scene.blend`、`scene.fbx`（如果存在）、`tex/` 与 `convert_scene.py` 的相对目录。
2. 在有授权的 Cinema 4D 中打开脚本管理器，载入 `convert_scene.py` 并执行。
3. 脚本优先读取同目录 `scene.json`；如未自动找到，选择该文件。
4. 转换会创建新文档，保存真正的 `scene.c4d` 并重新打开检查，不改动当前已有文档。已有同名文件时会要求另存。

原生C4D转换保留可编辑的静态网格、部件层级、UV、材质分配、相机和灯光；坐标从 Blender Z向上转换为C4D Y向上，米转换为厘米，当前姿态的修改器会求值为多边形。动画、约束、骨骼绑定以及程序材质节点图不会在C4D中重建，完整信息仍保留在 `.blend`。

Blender Principled BSDF、Cycles光能、世界环境和色彩管理与C4D的渲染系统不同，转换使用标准材质、位图和灯光近似。需要在C4D中检查粗糙度、金属反射、透射、纹理方向和灯光强度；不能保证打开后的渲染与Blender逐像素一致。

## 验收与实现

- `blender/export_scene.py`：真实保存、重开验证、纹理提取及交换场景。
- `c4d/convert_scene.py`：原生C4D文档构造、保存和重开检查；当前原生运行验收受授权限制。
- `scene_exports.py`：独立导出队列、镜头版本绑定、文件指纹去重、日志、重启中断状态、原生文件和转换包分开报告。
- `validation/scene-export-integration.json`：通过运行中应用实际导出和下载的记录。
- `validation/c4d/runtime_probe.json`：本机R22/R24授权阻塞证据。
- 13项服务端测试通过；C4D转换的8项坐标、UV、层级与输入验证测试通过，不等同于原生C4D运行通过。

官方参考：[c4dpy 运行及授权要求](https://developers.maxon.net/docs/py/26_107/manuals/introduction/python_c4dpy.html)、[Cinema 4D 文档保存与加载API](https://developers.maxon.net/docs/py/2024_5_0/modules/c4d.documents/index.html)。
