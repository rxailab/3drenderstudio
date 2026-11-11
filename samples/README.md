# 验收样例资产

全部资产由 `scripts/generate_samples.py` 在 Blender 中实际建模并导出，许可为 CC0-1.0。MORA 为虚构样例标签。样例几何是人工规则生成的测试产品，不声称是扫描实物。

| 文件 | 验证内容 |
| --- | --- |
| matte-pump-bottle.glb | 细长哑光塑料、多部件、保留文字 |
| brushed-metal-tray.glb | 扁平金属、单网格、凹槽 |
| thick-glass-tumbler.glb | 透明材料、空心、真实壁厚 |
| canvas-leather-tote.glb | 帆布、皮革、金属、纹理、缝线、层级 |
| canvas-tote-resources.zip | OBJ、MTL、子目录相对纹理路径 |
| sideways-missing-texture.zip | 故意缺失贴图、朝向待修正；建议 X 轴 -90°，具体以导入轴转换后的预览为准 |

这些模型可以通过应用的样例入口或正常上传入口导入；ZIP 保留资源目录结构。GLB 单位依 glTF 规范为米，OBJ 没有可靠实际单位。自动按可摄影尺度归一化不会写回原文件。

材质中的金属各向异性可能因 glTF 导出器能力丢失。需要方向性拉丝时，在应用材质编辑中选择内置拉丝金属并调节方向；不可将仅金属度与粗糙度误称为已验证拉丝方向。

本目录只提供真实模型资产。渲染是否通过以应用输出和验收报告为准。
