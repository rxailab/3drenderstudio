# 本地接口与可复现性

## v22 使用场景图

**生成。** `POST /api/projects/{id}/scene`，请求体 `{"hint"?: 不超过 300 字的说明, "expected_version"?: 当前方案版本, "seed"?: 1–999999999 的整数}`，返回 `202` 与任务 `{id,project_id,status,stage,error,base_version,created_at}`。任务在后台线程中执行：识别产品与环境 → 检索并下载道具 → 准备地面与背景 → 启动一次无界面的 Blender 计算布置。成功后一次性保存为一个新的方案版本（历史记录的原因是“生成使用场景”），并自动提交镜头 `scene` 的预览任务。`status` 为 `queued`、`running`、`completed`、`failed`、`cancelled`；完成后带 `applied_version`、`job_id`（预览任务）和 `result`：

```json
{"summary":"工具柜 / 工具箱：台面 5 件、旁边 2 件、抽屉内 2 件","source":"rules","setting":"workshop",
 "placed":9,"opened":true,"size_basis":"model_units","max_dimension_mm":737.6,
 "props":[{"id":"p1","asset_id":"adjustable_wrench","name":"Adjustable Wrench","zone":"on_top",
           "source_url":"https://polyhaven.com/a/adjustable_wrench","license":"CC0 1.0"}],
 "skipped":[{"concept":"work gloves","reason":"not_in_catalogue"}]}
```

`skipped[].reason` 为 `not_in_catalogue`、`duplicate`、`download_failed`、`import_failed`、`no_flat_top`、`no_open_compartment`、`no_room`。项目已有排队或运行中的渲染任务、已有进行中的场景任务、或 `expected_version` 与当前版本不一致时返回 `409`；模型尚未分析完成或找不到 Blender 时返回 `400`。后台高清图会像其他操作一样先让路。任务执行期间方案被别的操作修改时，任务以 `failed` 结束，方案不变。服务重启时未完成的任务标记为 `failed`。

`GET /api/projects/{id}/scene` 返回 `{task, scene_set, available}`；项目详情也带 `scene_task`（最近一次任务）。`POST /api/projects/{id}/scene/{task_id}/cancel` 取消进行中的任务，方案不变。`POST /api/projects/{id}/scene/remove`（请求体可带 `expected_version`）把场景镜头和 `scene_set` 从方案中移除，保存为一个新版本并返回项目详情；两者都可用 `/undo` 恢复。

**方案字段。** 场景保存在 `plan.scene_set`，对应的镜头是 `plan.shots` 中 `id` 为 `"scene"` 的一项（55 mm 透视镜头，`camera_distance` 与 `target_offset` 由布置时算出，之后可像其他镜头一样修改）：

```json
{"version":1,"shot_id":"scene","setting":"workshop","source":"rules","summary":"…","seed":7,
 "product":{"type":"工具柜 / 工具箱","max_dimension_mm":737.6,"size_basis":"model_units"},
 "units_per_meter":2.7115,
 "props":[{"id":"p1","asset_id":"adjustable_wrench","name":"Adjustable Wrench","provider":"Poly Haven",
           "license":"CC0 1.0","source_url":"https://polyhaven.com/a/adjustable_wrench",
           "file":"<data>/<项目>/props/models/adjustable_wrench/adjustable_wrench.gltf",
           "zone":"on_top","pose":"flat","matrix":[16 个数，行主序],"size_mm":[68.3,30.6,254.1],"concept":"adjustable wrench"}],
 "open":{"objects":["抽屉…"],"translation":[0,-0.583,0],"cavity_area":0.625,"cavity_depth":0.199},
 "surface":{"asset_id":"scuffed_cement","textures":{"base_color":"…","roughness":"…","normal":"…"},"tile_m":1.62,"color":[0.55,0.53,0.5]},
 "wall":{"asset_id":"concrete_wall_008","textures":{…},"tile_m":2.71,"color":[0.6,0.6,0.58],"distance_m":1.18},
 "basis":{"rotation":[0,0,0],"pose":{},"dimensions_mm":null},
 "skipped":[]}
```

`setting` 为 `workshop`、`kitchen`、`bathroom`、`living`、`office`、`garden`、`studio`；`source` 为 `rules` 或 `ai`；`zone` 为 `on_top`、`inside`、`beside`、`behind`；`size_basis` 为 `user`、`ai`、`model_units`、`category`。`units_per_meter` 是产品归一化后（最长边 2 个单位）每米对应的单位数，道具按它缩放到真实比例。`matrix` 与 `open.translation` 都在这个归一化空间中。

`scene_set` 通过 `POST /plan` 提交时按同一份约定校验：只接受上述字段；道具最多 12 件；`file` 与贴图必须是已存在于该项目 `props/` 目录内的本地文件，不接受 URL。渲染时只有 `shot_id` 指向的镜头会布置道具；`basis` 与方案当前的 `rotation`、`pose`、`dimensions_mm` 不一致时（布置之后改过朝向或姿态），该镜头按没有场景渲染，并在输出的 `warnings` 中说明需要重新生成。

**运行能力。** `GET /api/system` 的 `runtime.capabilities.scene_set` 表示布置组件（`blender/scene_dress.py`、`blender/scene_layout.py`、`scene_set.py`、`scene_props.py`）是否齐全；为 `false` 时含场景镜头的渲染请求会被拒绝并说明缺少的文件。

**网络访问。** 只请求 `https://api.polyhaven.com/assets?t=models|textures`、`https://api.polyhaven.com/files/{asset}` 和 `https://dl.polyhaven.org/file/ph-assets/Models|Textures/…`，不跟随重定向。目录在 `data/props_cache/` 中缓存 6 小时，网络不可用时使用已有的旧目录；模型与贴图按清单中的大小和 MD5 校验后缓存，再复制进项目。道具模型取 1K 贴图版本，地面与墙面取 2K。详见 [SCENE_IMAGES_UPDATE.md](SCENE_IMAGES_UPDATE.md)。

## v21 展示级首图、后台高清图与指令智能体

**方案字段。** `plan.look` 为 `"classic"`（缺省，旧项目保持不变）或 `"showcase"`（新上传的项目）。`plan.auto_polish` 为布尔值：预览完成后是否在后台补渲一张高清 2K。`plan.lookdev` 是服务端写入的只读记录 `{version,source,summary,decisions:[{scope,material,object?,finish,label,reason,parts?,twin_of?}]}`，说明分析完成时为占位材质推断了哪些质感及依据。材质覆盖（`material_overrides`、`object_material_overrides`）新增可选的 `coat`（0–1，清漆层）；质感推断写入的条目带 `source:"lookdev"`、`lookdev_finish`、`lookdev_reason`。通过 `POST /api/projects/{id}/plan` 把旧项目的 `look` 改为 `showcase` 且方案尚无任何材质覆盖时，服务端按同样规则补一次质感推断。

**画质 `showcase`。** `POST /api/projects/{id}/jobs` 的 `kind:"render"` 可使用 `quality:"showcase"`：长边 2048 px、160 采样。输出记录的 `quality` 为 `"showcase"`，可作为 `/instruct`、`/hero-optimize`、定位与导出接口的来源图片。`kind:"preview"` 不接受该画质。

**后台高清图。** `look` 为 `showcase` 且 `auto_polish` 为真的项目，在某个镜头的预览任务完成、自动检查与自动材质链路都结束后，服务端自动为该镜头提交一个 `kind:"render", quality:"showcase"` 的任务，带 `origin:"auto_polish"`；同一方案版本、同一镜头只成功一次。项目详情的 `polish` 为 `{status,job_id,version,shot_id,error}`。这个任务不占用项目：任何新的 `/jobs` 提交，以及 `/plan`、`/undo`、`/instruct`、`/instruct/confirm`、`/feedback`、`/review/propose`、`/review/apply`、`/hero-optimize`、`/materials/import`、`/materials/selection/apply`、`/material-agent/tasks`、`/ecommerce/adapt` 的 POST 请求都会先取消它（任务记录带 `yielded:true`，状态为 `cancelled`），然后照常执行；之后下一次预览完成时会重新提交，最多 3 次。用户通过 `/api/jobs/{id}/cancel` 取消的不会重新提交。客户端不应把 `origin:"auto_polish"` 的活动任务当作需要等待的任务。

**指令回执。** `instruction_result` 新增 `agent_trace`，只含固定代码与计数：

```json
[{"agent":"planner","calls":2,"ready":1,"pending":1},
 {"agent":"verifier","checked":2,"narrowed":1,"removed":6,"result":"narrowed"},
 {"agent":"specialists","requirement":"r2","locator":{"result":"located","targets":4,"groups":1,"confidence":0.94},"result":"resolved"},
 {"agent":"specialists","calls":1},
 {"agent":"executor","repairs":["localised_shared_material"]}]
```

`verifier.result` 为 `narrowed`（去掉了 `removed` 个部件）、`confirmed`、`disputed`（认为整组都不对，未改动）、`rejected` 或 `unavailable`。`specialists` 的 `result` 为 `resolved`、`pending`、`rejected`（附校验代码 `reason`）或 `unavailable`；`locator.result` 为 `located`、`unresolved`（`reason`：`not_in_catalogue`、`ambiguous`、`hidden_or_too_small`、`not_a_part`）或 `rejected`；`finisher.result` 为 `chosen` 或 `rejected`。`executor.repairs` 为 `localised_shared_material`、`extended_to_bound_targets`、`dropped_unbound_edit`。全部要求仍未能执行时，错误响应（`error_code:"instruction_targets_unresolved"`）同样带 `agent_trace`，`pending[].reason` 为智能体给出的具体原因。

`target_bindings[]` 中，用户写明数量时仍为 `expected_instances`（必须与实测的 `instance_count` 相等）；模型自行估计的数量记为 `inferred_instances`，只作记录，不参与校验。

**环境变量。** `AI_AGENT_TEAM=0` 关闭核对与专职智能体（规划器的两轮调用不受影响）；`AI_AGENT_VERIFY=0` 只关闭核对；`AI_AGENT_MAX_CALLS`（默认 6，上限 12）限制每条指令中专职智能体的模型调用次数。详见 [AUTOMATION_UPDATE.md](AUTOMATION_UPDATE.md)。

## v19 电商平台检查与套图导出

`GET /api/system` 的 `runtime.capabilities` 增加 `matte`：本机 Blender 与 `blender/render_matte.py` 均可用时为 `true`，纯白底与透明底处理依赖它。

`GET /api/ecommerce/platforms` 返回平台预设目录：`{reviewed,default,disclaimer,platforms:[…]}`。每个预设包含 `id,name,group,aspects,aspect,min_px,target_px,max_px,max_bytes,white,fill_min,fill_target,notes,sources,confidence`，部分平台另有 `white_slot`（独立白底图槽位）。`white` 取 `required / recommended / optional`。

`GET /api/projects/{id}/ecommerce[?platform=amazon]` 按平台检查项目中每个镜头最新的真实渲染图，不修改项目。返回 `platform,settings,matte_available,main_shot_id,step,counts,plan_recommendation,shots`。`shots[].items[]` 为逐项结论 `{id,label,value,status,detail,action?}`，其中 `value` 是用于一行显示的简短结果（如 `2000 × 2000`、`76% → 88%`），`detail` 是完整说明：

| `status` | 含义 |
| --- | --- |
| `pass` | 已满足平台要求 |
| `auto` | 导出时自动处理（纯白背景、居中构图、补白或裁切到平台画幅） |
| `warn` | 可导出但需留意（图片来自旧方案版本） |
| `action` | 需要用户操作；`action` 为 `preview`、`render` 或 `background` |
| `info` | 平台建议，不影响导出 |

每个镜头取最新方案版本中像素最多的一张图；产品占比来自该图渲染结果中的真实顶点投影。`PUT` 同一地址保存 `{platform,background,shadow,transparent}` 并返回更新后的检查；`background` 取 `main_white / all_white / original`。

```http
POST /api/projects/{id}/ecommerce/adapt
{"platform":"amazon","expected_version":4}
```

把平台建议的画幅与留白写入摄影方案，作为一个可回退的新版本（历史记录原因为「按「平台」调整画幅与留白」）。已接受的画幅不会被改动；没有差异时不产生新版本。有渲染任务排队或执行时返回 409。

```http
POST /api/projects/{id}/ecommerce/exports
{"platform":"amazon","background":"main_white","shadow":true,"transparent":false,"shot_ids":["hero","side"]}
```

创建后台导出任务并返回 HTTP 202。`shot_ids` 省略时导出全部已出图镜头。任务记录字段：`id,project_id,platform,platform_name,options,status,stage,progress,error,warnings,files,download_url,created_at,finished_at,project_version`。`status` 为 `queued / running / completed / failed / cancelled / interrupted`。相同来源图片与相同设置的已完成任务会被直接复用。

`files[]` 中每个文件给出 `name,label,role,width,height,bytes,background,shadow,framing,fill,pure_white,issues,url`。`role` 为 `main / angle / detail / white_slot / transparent`；`pure_white` 与尺寸、字节数均来自对已写入文件的重新读取；`issues` 列出仍不满足平台要求之处（例如只有预览分辨率）。`download_url` 指向包含全部文件、`manifest.json` 与 `README.txt` 的 ZIP。

`GET /api/ecommerce/exports/{task_id}` 读取任务，`POST /api/ecommerce/exports/{task_id}/cancel` 取消并终止正在进行的蒙版渲染。`GET /api/projects/{id}` 附带 `ecommerce`（已保存的平台与导出设置）与 `ecommerce_exports`（最近三次任务）。每个项目保留最近若干次导出，更早的记录和文件自动清理；删除项目前需等待或取消进行中的导出。

`POST /api/projects` 的表单新增可选字段 `platform`：新项目的画幅与留白直接按该平台设置；未知平台在保存任何文件前返回 400。

## v17 实时模型与相机回放

`GET /api/system` 增加 `viewport:{available,format,projection,interactive}`。实时模型准备独立于 Cycles 出图任务，依赖本机 Blender 与 `blender/export_viewport.py`，不调用 LLM。

```http
POST /api/projects/{id}/viewport
Content-Type: application/json
```

```json
{"shot_id":"hero","expected_version":4}
```

返回项目绑定的任务记录：`id,status,project_id,version,shot_id,source_kind,source_version,asset,error,warnings`。排队／运行返回 HTTP 202，已就绪缓存返回 HTTP 200。仅接受正文中的镜头与版本，不接受客户端指定的源文件路径、Blender 脚本或资源地址。

`GET /api/projects/{id}/viewport/{task_id}` 返回该任务状态，并使用 `Cache-Control:no-store`。状态为 `queued/running/ready/failed/stale`；当前方案版本改变后，旧任务返回 `stale` 且不提供资产地址。任务 ID 不能跨项目读取。准备期间持有项目操作预约，避免与删除冲突。

`ready` 的 `asset` 包含 `url`（独立 GLB）与 `metadata_url`。元数据协议为 `wuying-viewport-v1`，坐标系为 glTF Y-up，包含产品边界、实测相机、灯光与环境信息、材质近似说明、来源与资产摘要。GLB 只包含独立产品几何与可转换材质，贴图嵌入文件；缓存身份覆盖项目、版本、计划、分析、场景源、资源与导出器版本。原始模型与已保存工程不会在导出时被覆盖。

镜头字段通过原有 `/plan` 版本化接口保存：

| 字段 | 含义 |
| --- | --- |
| `projection` | `orthographic` 或 `perspective`；缺省按正交执行新视图和新渲染。 |
| `ortho_scale` | 正交画面横向宽度，使用当前 Blender 场景世界单位；显式值不被自动取景覆盖。 |
| `target_offset` | 三元数组，Blender Z-up 坐标；相对产品包围盒中心、按包围盒对角线归一化的平移。 |
| `azimuth/elevation` | 沿用已有方位角与俯仰角，单位度。 |
| `camera_distance` | 显式透视镜头距离，使用场景世界单位。 |
| `focal_length` | 显式透视时的焦距；正交缩放由 `ortho_scale` 控制。 |

新应用的相机使用 `sensor_fit=HORIZONTAL`；相机参数在渲染和保存后的工程中核验。预览、4K、原生场景回放以及自动主图优化遵守镜头投影选择。历史图片与工程按原文件保留，不能把历史透视图标成新的正交出图证明。

浏览器拖动只改变当前视图；使用当前视角或提交出图时，把一份视角快照并入草稿，再沿用版本校验和渲染队列。实时画布不是原有图片选点或 AI 诊断的图像来源。

## v10 默认自动部分执行

`POST /api/projects/{id}/instruct` 可选 `partial_mode` 为 `"apply_ready"` 或 `"review"`，缺省等同 `"apply_ready"`。此字段属于客户端请求并进入请求摘要；模型不能改变它。新版界面普通提交使用缺省模式。

有已经核验的独立修改与未完成项时，默认直接提交可执行子集，返回 `instruction_result.status:"partially_applied"`、`partial:true`、`ready`、`pending`、`remaining_text`、实际 `changes` 与 `preview_shot_ids`。`base_version` 为执行前版本，`applied_version` 为提交后的版本；不返回新 `proposal_id`，不保存待确认提案。客户端使用既有预览接口与预览去重 key 生成当前镜头预览，不进行第二次确认或第二轮 AI 规划。

`ready` 全部经过核验为已满足、没有实际参数变化时，可返回 `status:"no_change"`、`partial:true` 和剩余要求；此时版本不变，`changes` 与 `preview_shot_ids` 为空。全部未完成则沿用结构化未完成错误，不产生空成功操作。

并列部位共享材质谓语时，服务器生成确定性的完整 `quote`，并附原文 `source_quote`。可执行与剩余文本只允许由相应子句及原有保留条件组成；这种确定性展开之外的模型自创文字不会成为可执行范围。各部位独立核验，共享原句不代表共享材质槽授权。

执行回执的 `changes` 保留准确存储差异 `path/before/after`。镜头灯光、曝光和柔光覆盖项可附 `effective_before/effective_after`，表示由 `engine.effective_plan` 计算的有效数值，供界面显示。历史与计划的 `parameter_diff` 继续使用原始存储差异。

自动部分执行在一次持久提交中保存计划、历史、版本与回执；重复请求返回原回执，不再次执行。实际完成的子句进入 `last_instruction.text`，完整原要求放在 `original_text`，未完成子句保留在 `pending` 和 `remaining_text`。版本、来源、参考图、资料、部件目录和保留条件保护继续生效。

显式 `partial_mode:"review"` 及已保存的旧提案使用原确认接口；以下 v6 的提案准备与确认说明对应这一兼容路径。加载项目不执行历史提案。

## v9 修改返回协议与可重试错误

客户端的指令、确认与预览请求结构不变。模型端统一使用逐项返回结构：`{requirement_results:[{id,status,summary,patch,target_bindings?}],notes:[],summary?:string}`，不再要求冗余的总体状态和总体修改。服务器验证全部原始编号、逐项参数、目标绑定、保护条件和实际差异后，派生内部执行状态。旧返回仅在总体修改与逐项已核验修改相容、且没有额外目标或字段时去重；冲突进入与其他纠错共享的两次请求上限。

两次回复仍无法满足返回协议时，`POST /api/projects/{id}/instruct` 返回 HTTP 422：

```json
{
  "error": "这次返回的修改结果未通过校验，当前方案未修改。可以直接重试，无需改写要求。",
  "error_code": "instruction_response_invalid",
  "retryable": true,
  "applied": false
}
```

可选 `diagnostic_id` 为 32 位小写十六进制标识。这类错误不附带部件待确认或定位动作，也不改变项目方案、版本、历史、执行回执或渲染任务。客户端结束已拒绝的尝试，用户主动重试使用新 key；状态未知的网络重试仍复用原 key。真实部件未确认继续使用已有 `instruction_targets_unresolved`；已有提案与确认流程保持原有约束。

项目归档新增至多 10 个 `instruction-errors/{diagnostic_id}.json`。每个记录限 32 KiB，保存原要求、来源版本、错误代码与字段路径、最多两次回复的字段名／逐项状态结构；不保存完整模型输出、参数值、提示词、图片或凭据。导出只读取由服务清洗后的记录，不打包任意目录文件。

## v8 三维部件检查与示例选点

`POST /api/projects/{id}/instruct` 增加可选 `target_points:[{id,x,y}]`，仅允许 1–12 个不同 ID（长度 1–64）及有限的归一化坐标。必须同时提供当前版本的完整 `source`；不接受客户端对象名或材质名。服务器从该来源重新拾取，核对每个点的真实对象/材质槽，仅作为 `target_seeds` 提供给识别流程，不作为限定修改范围的旧 `selections`。

当前版本预览具备 `.blend`、Blender 与新增检查组件时，未直接点名对象的材质赋值要求会自动执行只读场景检查。部件证据可为 `method:"scene_part_catalog"`，含源快照、目标编号、实际世界边界、连通几何边界及至多四页独立双视角部件图。原 `camera_ray_samples` 机制继续兼容。私有路径、源文件指纹和完整几何校验数据不进入模型公开索引。

材质绑定通过时可带 `instance_count` 和 `instance_basis:"scene_geometry_components"`。前者表示所有实际被修改表面的空间分组数，不是网格对象数。材质槽执行覆盖仍需逐项通过，不能用正确数量掩盖遗漏的网格表面。执行回执的 `target_verification` 增加 `scene_geometry_catalog`；`part_inspection` 可记录目标数、未确认数与是否超限，不能当作视觉效果合格声明。

格式纠错与目标纠错共享最多两次 AI 调用。未通过的部位返回既有 `instruction_targets_unresolved`、`pending`、`remaining_text`、`action:"locate_part"`，单项要求同样可进入恢复操作。候选提案、确认、执行回执和网络重试均保留原来的版本与幂等约束；缓存检查图变化使旧提案失效。读取场景及生成部件图不变更计划、历史、版本或普通渲染任务。

`GET /api/system` 增加 `runtime.capabilities.inspect_parts`，表示 Blender 与 `blender/inspect_parts.py` 可用。该能力独立于原来的渲染、拾取和导出组件。

## v7 摄影要求与待处理动作

API 请求和明确确认格式沿用 v6。摄影要求中的泛化目标与明确参数分别处理：前者要求实际的相关摄影变化，但不强迫无意义修改镜头；后者按用户指定值、方向和保留条件核验。可解析的缺失参数按同一个原始有效计划补齐，不重复叠加相对变化。模型调用预算仍最多两次，确认接口不调用 AI。

`pending` 可增加 `action:"clarify_parameters"` 或 `action:"locate_part"`。此字段会进入提案、确认和重试回执，以及结构化错误。参数缺项不提供部件定位入口；旧回执缺省仍沿用兼容行为。`quote`、`execution_text`、`remaining_text` 的原文与保护条件验证不变，客户端不能借此提交任意候选或改写原始要求。

## v6 复合要求与明确确认

`POST /api/projects/{id}/instruct` 保持原请求结构。完整可执行要求仍返回 `applied`，无变化仍返回 `no_change`。当逐项校验得到可执行内容和待确认内容时，返回项目详情及 `instruction_result.status="needs_confirmation"`；这次不修改方案、历史或版本，也不排队预览。

提案回执增加以下字段：

| 字段 | 含义 |
| --- | --- |
| `proposal_id` | 服务端保存的提案身份；候选方案和证据摘要不公开。 |
| `ready` | 已核验事项：`id,quote,summary`，可含 `status:"already_satisfied"`。`quote` 来自原始要求。 |
| `pending` | 未完成事项：`id,quote,reason,status`，仍保留原句；`status` 为 `needs_clarification` 或 `unsupported`。 |
| `proposed_changes` | 服务端基于保存候选计算的真实 `path,before,after` 差异。 |
| `changes` | 此时固定为空数组；尚未应用。 |
| `remaining_text` | 未完成的原始要求及仍适用的保留约束。 |
| `base_version,applied_version` | 此时均为当前方案版本。 |
| `preview_shot_ids` | 此时固定为空数组。 |

用户明确确认后，客户端以新的 `Idempotency-Key` 调用：

```http
POST /api/projects/{id}/instruct/confirm
Content-Type: application/json
Idempotency-Key: confirmation-request-key
```

```json
{"proposal_id":"server-proposal-id","expected_version":4}
```

该接口只接受这两个正文属性，不接受候选参数、任意修改或替换后的要求。服务直接读取已校验候选，复核当前版本、计划、分析、来源图片及目标证据，再原子保存一个历史版本与执行回执；确认阶段不调用 AI。成功为 `status:"applied",partial:true`，保留 `proposal_id,pending,remaining_text`，`changes` 为实际应用的差异，`preview_shot_ids` 指定后续严格版本预览。浏览器成功后将输入替换为剩余要求。

同一确认请求键重复提交返回原回执；同一提案换用另一个请求键也不能重复应用。方案或证据发生变化时返回 409，客户端需按最新状态重新分析。初次提案和确认的响应不确定时，都必须沿用各自原正文和原请求键重试；预览失败只重试已保存版本的预览。已完成提案的回执用于恢复结果，不能重新覆盖之后的方案。

所有操作未完成时继续占用现有指令预约；运行任务和项目删除沿用原生命周期互斥。内部 `_instruction_proposals` 不进入项目列表或详情响应。全部未能执行时不保存空提案，返回简短错误及适用的 `pending`。

## v5 模型选择与部件证据

`GET /api/ai/settings` 和系统信息中的 `ai` 增加以下公开字段，均不包含 Key：

| 字段 | 含义 |
| --- | --- |
| `request_allowed` | 启用、凭据、型号与本地停用策略均允许发起请求；不表示已真实连通。 |
| `blocked_reason` / `blocked_code` | 已停用型号或不支持视觉的具体原因；旧连接成功记录不能覆盖此状态。 |
| `model_presets` | 常用配置列表：`id`、`label`、`provider`、`base_url`、`model`、`key_label`、`vision`、`note`，以及附日期和条件的参考 `price`。选择预设本身不会写入或调用服务。 |
| `vision_config.request_allowed` | 对当前实际视觉路由单独判断；未独立配置时继承主模型。 |

`gpt-5.5-2026-04-23` 与 GPT-5.5 常见别名在构造图片请求前、发送网络请求前均会被本地拒绝。请求体不能携带与配置不同的 `model`。旧配置可以读取并更新，没有自动降级／升级到另一收费模型的行为。切到其他平台时需要对应 Key；保留原来已明确支持的 HAI 官方路由共享规则。

全局指令 API 不接受客户端提交“可信目标表”。服务端根据项目内已验证的当前图片记录读取 `result_url`，从真实 `product_samples` 生成有界的 `target_evidence`：最多 80 个对象／材质组合，每个最多 3 个真实命中点。预览、版本与采样摘要一起进入原有 AI 解析请求；原有有界格式纠错策略保持不变。

AI 的 `target_bindings` 需要把本次原文部位对应到这张表中的编号。绑定结果、实际局部修改目标和请求范围必须一致；已知数量不完整、未知编号、跨对象共享材质扩散或过时采样均不能提交。完整要求必须覆盖所有必需子项；v6 允许把已校验子项保存为待确认提案，明确确认前不落地部分修改。存在可用的 `.blend` 和拾取运行组件时，按每批最多 20 点复核实际命中；否则明确使用已有渲染采样。

执行回执可包含 `target_bindings` 与 `target_verification`，后者为 `render_samples` 或 `raycast`。写入前再次检查方案版本与采样摘要。编号和采样不能证明隐藏部件的语义或保证材质的最终视觉效果。

模型费用、候选与验证边界见 [MODEL_AND_PARTS_UPDATE.md](MODEL_AND_PARTS_UPDATE.md)。

基础地址：`http://127.0.0.1:8765`。所有 JSON 使用 UTF-8。错误返回 `{ "error": "原因" }` 和非 2xx 状态码。

| 操作 | 接口 |
| --- | --- |
| 检查 Blender / AI / 设备 | `GET /api/system` |
| 项目列表 / 项目详情 | `GET /api/projects` / `GET /api/projects/{id}` |
| 上传 | `POST /api/projects`，multipart `files`、可选 `paths` JSON 数组、`name`、`dimensions` 毫米三元数组、`material_note`、`reference` |
| 修改方案 | `POST /api/projects/{id}/plan`，JSON 局部顶层更新 |
| 回退修改 | `POST /api/projects/{id}/undo` |
| 智能画面调整 | `POST /api/projects/{id}/instruct`，`text,expected_version,shot_id,source`，附 `Idempotency-Key`；旧 `{"text":"增加留白"}` 保持兼容 |
| 确认可完成项 | `POST /api/projects/{id}/instruct/confirm`，`proposal_id,expected_version`，附独立 `Idempotency-Key` |
| 删除项目 | `DELETE /api/projects/{id}`，成功返回 `deleted,id,cleanup_pending,warning?`；活动任务或操作未结束时返回 409 |
| AI 服务设置 | `GET /api/ai/settings` / `PUT /api/ai/settings`；字段 `provider,base_url,model,enabled,api_key`；独立视觉字段为 `vision_provider,vision_base_url,vision_model,vision_enabled,vision_api_key`；空 Key 保留，分别用 `clear_key:true`、`vision_clear_key:true` 清除 |
| 真实连接测试 | `POST /api/ai/test`，仅测试已保存配置；返回 `success,message,settings`，无原始模型响应或密钥 |
| 真实视觉测试 | `POST /api/ai/vision/test`，使用最近的真实预览测试已保存视觉配置；无预览时明确失败，返回安全的可见内容描述和独立状态 |
| 图片表面多点定位 | `POST /api/projects/{id}/feedback/pick`；`output_id,shot_id,quality,version,points:[{id,x,y}]`，x/y 为图像内 0–1 坐标 |
| 局部视觉修改并预览 | `POST /api/projects/{id}/feedback`；同上加 `text`；再次验证真实表面，更新版本并提交该镜头的真实预览任务 |
| 直接修改 / 恢复所选材质 | `POST /api/projects/{id}/materials/selection/apply`；同上定位字段加 `patch` 或 `reset:true`，返回更新后的项目详情 |
| 已生成图片的逼真度诊断 | `POST /api/projects/{id}/review`；`output_id,shot_id,quality,version,question?,diagnostic_only?`；新UI显式传true，只更新诊断 |
| 创建任务 | `POST /api/projects/{id}/jobs`，`{"kind":"preview","quality":"preview","shot_ids":["hero"],"expected_version":2}`；新客户端绑定方案版本 |
| 任务状态 | `GET /api/jobs/{id}` |
| 取消 / 重试继续 | `POST /api/jobs/{id}/cancel` / `POST /api/jobs/{id}/retry` |
| 项目归档下载（含历史结果） | `GET /api/projects/{id}/bundle?blend=1&quality=all` |
| 样例 | `GET /api/examples`、`POST /api/examples/{id}/import` |
| 外部材质 | `POST /api/projects/{id}/materials/import`，multipart `name`、`preset`、`source`、`license` 和 `base_color/roughness/metallic/normal/transmission` 贴图 |
| 场景工程导出 | `POST /api/projects/{id}/exports`，JSON `format:blend/c4d`、`output_id:渲染任务ID`、`shot_id`、`quality`、`version` |
| 场景导出状态/取消 | `GET /api/exports/{id}` / `POST /api/exports/{id}/cancel` |

项目详情的 `exports` 包含独立场景导出任务。只有原生文件实际保存并重开成功时才返回 completed/native_url。C4D因授权失败时返回 failed/error，并可附带 `conversion_package_url`（兼容别名 `package_url`）下载手动转换资料；此包不是原生C4D工程。

正式任务使用 `kind=render`，`quality=standard` 或 `fine`（v21 另有 2K 的 `showcase`，见上文）。`shot_ids` 省略时使用全部镜头，显式空数组返回 400。`kind=render` 未提供正式质量时默认 `standard`。提交可附带 `Idempotency-Key`，一个项目同时只允许一个活动任务。预览和正式任务使用同一计划；正式档位不把已有预览放大，而是重新原生渲染。

方案结构由 `planner.default_plan` 和 `planner.validate` 定义：风格、比例、留白、旋转、曝光、光源柔和度、纹理尺度/旋转、材质槽覆盖、部件局部覆盖 `object_material_overrides`、逐镜头调整 `shot_adjustments`、可回退修复、姿态、时间预算、自动检查 `visual_review`、修正次数、镜头数组和信息来源。每个镜头包含稳定 ID、名称、完整图/细节图类型、方位角、俯仰角、焦距和可选细节部件目标。当前状态操作支持给定部件旋转/位移与已有动画帧，不猜测机械运动。

## 智能调整与执行回执 v3

新版主输入使用以下请求。`source` 可为 `null`；客户端只能提供输出身份，不能提供任意图片路径或 URL。

```json
{
  "text": "反光柔和一点，颜色和角度不变",
  "expected_version": 4,
  "shot_id": "campaign",
  "source": {"output_id": "saved-job-id", "shot_id": "campaign", "quality": "preview", "version": 4}
}
```

通过 `Idempotency-Key` 绑定一次用户意图。服务端在推理前后检查方案版本与活动渲染／材质任务；模型请求期间不持有全局锁。当前图必须属于本项目且有可验证的 PNG；只有与当前方案同版本的来源图才进入视觉请求。旧图会在回执中说明未作为当前画面。明确提出参考图要求时，仅从该项目 `references/` 中加载已上传、可读的参考图；缺图返回错误。

新请求携带当前镜头有效参数、项目资料、最多五条当前方案谱系中的有效人工意图与简短诊断。相机修改只能引用当前镜头；曝光、柔光、灯光转换为逐镜头覆盖。留白、比例、产品朝向、全局纹理等共享参数变化会附作用范围说明。白底主图仍保持白底；必要时返回棚拍宣传镜头作为背景风格的预览目标。

返回项目详情，另包含 `instruction_result`：

- `id,status,text,source,summary,notes`：执行身份、结果与说明；`status` 为 `applied`、`no_change`，或 v6 的 `needs_confirmation`。
- `changes`：服务端基于实际候选与原方案计算的 `path,before,after`，不是模型自行声称的结果。
- `shot_id,preview_shot_ids,source_snapshot`：请求的镜头、应预览的镜头与来源图片。
- `used_image,used_reference`：是否实际把图片交给本次推理。
- `base_version,applied_version,replayed`：版本与重复请求状态。
- `protections,protected_fields,protected_material_fields`：本次明确保留要求，供执行校验及紧随的自动优化使用。

回执与计划在同一项目记录中提交；`GET` 可读取最新回执。相同键、相同请求返回原执行回执，不重复修改；成功重放返回当前项目详情与原回执，并标明 `replayed:true`，不是原项目响应快照。相同键、不同请求返回 409。已撤销或后来修改过的方案不因旧请求重放而再次应用。前端看到原回执的 `applied_version` 不再等于当前版本时，不为该旧请求排新预览。`no_change` 不增加方案版本，不启动任务；不支持或需要澄清的要求返回 400 并保持输入。

若同一键、同一正文的原请求仍在处理中，返回 HTTP 409，并包含 `error_code:"instruction_in_progress"`、`retryable:true`。这不表示原请求已失败；客户端应保留原正文与请求键，稍后用同一请求核对。其他版本冲突、不同正文复用同一键或无关并发请求，不带此可重试标记。成功回执持久保存；处理中的预约与现有存储锁沿用单服务进程模型，不是跨进程分布式锁。

预览仍是单独的 `/jobs` 请求，使用另一稳定幂等键和 `expected_version=applied_version`。严格模式若找到同键原任务，先核对请求版本、类型、质量与镜头；全部匹配就返回原任务，即使项目已有后续版本，也不创建新任务。没有命中原任务时，才检查当前方案版本与其他活动任务；冲突返回 409。未传 `expected_version` 的旧集成保持原有兼容行为。

## 上传后首图与项目管理 v4

新建项目包含 `initial_preview:{"status":"pending"}`。后台模型分析成功并提交分析结果后，自动创建只含 `hero` 的 `preview` 任务，镜头保持白底；首次任务使用分析后的当前方案和版本。任务带 `origin:"initial_preview"`。不需要客户端再提交一次 `/jobs`，也不自动发起正式 4K。

主图可用后，后台可为当前版本尚无图片的其他镜头安排真实渲染缩略图。客户端打开项目时也可调用 `POST /api/projects/{id}/thumbnails`，请求体为 `{"expected_version":版本号}`；已安排任务时返回 202 和 `job`，不需要排队时返回 200 和空 `job`，版本冲突返回 409。缩略图为长边 256 像素的 `thumbnail` 画质，不作为正式预览、诊断或成品导出来源。

`initial_preview` 随任务保存 `status,job_id,analysis_job_id,version,shot_id,quality,error`。分析失败、取消或中断不会创建主图；保留 pending 意图，分析显式重试成功后继续。首图本身失败、取消或中断后，不自动重排，沿用已有任务重试入口。排队失败的原因写入 `initial_preview.error` 和分析任务的 `initial_preview_notice`。固定请求键与持久任务记录避免重复回调产生多个首图；启动时只补偿分析成功但首次任务尚未入队的窗口。没有 `initial_preview` 标记的旧项目不参加该自动流程。

删除接口按精确项目 ID 清理项目记录、对应 `job/export/material_task` 及该项目的资源目录；诊断方案、历史、图片记录与智能回执随项目记录一并删除。不存在的项目返回 404；活动分析、渲染、导出、材质任务、尚未退出的工作线程或同步项目操作存在时返回 409，不删除项目。客户端应在一次明确确认后调用，成功后再清除该项目的本地选择与恢复记录。

成功删除返回 `{"deleted":true,"id":"项目ID","cleanup_pending":false}`。若项目已移除、但部分文件因占用未能完成清理，返回 `cleanup_pending:true` 与 `warning`；待清理文件不通过 `/files` 提供访问，下次应用启动会重试。目录迁移前持久保存准备记录；普通异常回滚会恢复尚未提交的目录迁移，启动恢复依据项目记录是否仍存在决定恢复或继续清理。原目录与暂存目录同时存在时保留两份数据与恢复记录。

“退出项目”是客户端导航，不调用删除或取消接口。它清除当前打开的项目，返回上传首页；已有后台任务继续，重新打开项目可查看结果。未保存草稿沿用保存／放弃／取消选择，尚未确认的指令身份保留以供重试。

## 部件材质接口

定位、编辑与诊断都用 `output_id`（渲染任务ID）、`shot_id`、`quality`、`version` 四项精确绑定已有图片。材质编辑仅允许当前方案版本，项目不可有排队或运行中的渲染任务；服务器根据坐标重新运行已保存场景的射线查询，不接受客户端提供的部件名称作为修改依据。

`feedback/pick` 的命中项包含 `object_name`、`material_name`、`slot_index` 和 `material_info`。后者包含可读的 `color,roughness,metallic,transmission,ior`，以及 `linked,textures,warnings,editable,supported,source,value_basis`。`source=rendered_scene` 表示来自保存场景。颜色以sRGB十六进制返回；贴图/节点驱动的通道值是输入默认值，不能当作点击位置的最终纹理颜色。未知值为 `null`，复杂着色器以 `editable:false` 和警告标记。

直接编辑请求示例：

```json
{
  "output_id": "渲染任务ID",
  "shot_id": "hero",
  "quality": "preview",
  "version": 3,
  "points": [{"id": "p1", "x": 0.42, "y": 0.56}],
  "patch": {"roughness": 0.5}
}
```

`patch` 仅允许 `preset,color,roughness,metallic,transmission`；后三项范围为0–1，预设必须属于内置预设。只改参数时保留未改的节点连接；显式改色会替换该通道连接。明确选择预设会替换着色器。复杂着色器在直接编辑与自然语言编辑中均须明确选择预设后才能替换，不能悄悄按普通Principled参数覆盖。

恢复请求使用 `reset:true` 代替 `patch`；它只删除所选网格/材质槽的局部覆盖，保留全局材质设置。成功编辑或恢复会记录 `material_edits`、递增版本并提交当前镜头的真实预览。无需外部AI即可调用直接编辑；若启用自动视觉检查且已配置Key，后续预览检查仍会调用AI。

## 图片逼真度诊断

**新版UI的重新检查：** 请求显式包含 `"diagnostic_only": true`。此字段只接受布尔值，true时只记录诊断，绝不修改摄影方案、增加版本、排队预览或启动自动材质任务。未传或false保留下述旧调用行为。渲染后自动检查的流程不受此字段影响。

`POST /review` 可分析历史版本图片。可选 `question` 默认为「为什么现在渲染得不够逼真？」，非空且不超过2000字。服务器读取该图真实PNG、成功的 `result.json` 和对应 `effective_plan` 或原始 `request.json`，文件或快照缺失时明确报错。

结果保存在匹配输出项的 `review`，包含 `source`（`ai` 或 `rules`）、`issues,patch,score,needs_user,status,checked_at,question`，以及自动材质需求 `material_requests`。真实AI成功时记录 `provider,model`；调用失败时设置 `ai_unavailable:true`，保留有限的基础测量结果并记录失败原因。逼真度报告结构为：

```json
{
  "realism": {
    "summary": "基于该图证据的概述",
    "causes": [{
      "aspect": "检查因素",
      "evidence": "具体可见区域、现象或实际测量；推测明确标记",
      "confidence": 0.8,
      "suggestion": "可实现的调整或需要人工确认的资料",
      "requires_user": true
    }],
    "priority_actions": ["优先处理事项"]
  }
}
```

诊断记录包含 `mode:diagnostic_only`、`parameter_changes_applied:false`。只有兼容调用（请求未传 diagnostic_only 或为false）才可能对当前方案版本的真实金属反光问题另行排队自动预览；新UI的true请求严格禁止此后续分支。历史图片、关闭自动修正或没有安全动作时只诊断。自动预览检查按配置上限尝试曝光、柔光和受限反射布光并比较真实渲染；图组完成后另由自动材质流程处理已校验的 `material_requests`。无Key时仅报告实际曝光/构图测量及未检查范围，不声称具有材质视觉理解。

## 诊断回复、生成方案与应用

`POST /api/projects/{id}/review/propose` 接收当前输出的 `output_id,shot_id,quality,version`，以及该诊断的 `review_checked_at`。`selected_causes` 为选中 `review.realism.causes` 的零起始索引；`answers` 为 `{index,status,text}` 数组，index引用`review.needs_user`，status支持`confirmed`、`answered`、`skipped`；`instruction`为不超过2000字的补充要求。确认仅认可问题，不授权猜测颜色或几何；answered需要实际文字。

返回`{proposal}`，含`id,source,base_version,status,summary,changes,needs_user,manual_actions,answers,can_apply`。`changes`列出参数path/before/after。生成方案保存回答与候选参数，不增加方案版本、不启动渲染；无可执行变化时can_apply为false。方案保存在项目的`review_proposals`中，重复同一请求复用结果。

`POST /api/projects/{id}/review/apply`仅接收`{"proposal_id":"已保存方案ID"}`。服务端验证图片、诊断指纹、计划指纹、当前版本和活动任务，再应用已保存候选方案，记录历史、增加版本并排队当前镜头真实预览。返回更新后的项目。浏览器不能用此接口直接注入候选参数；重复应用同一方案不再次改版或排队。诊断或计划变化后，未应用方案失效。

可批准的灯光参数为`lighting`倍率：`key_power,fill_power,rim_power`范围0.1–3，`environment`范围0–3，默认1。`shot_adjustments[shot_id].lighting`为该镜头的绝对倍率覆盖，优先于全局；曝光和柔光仍分别写入该镜头`exposure_delta`与`softness_multiplier`。倍率在材质适配布光完成后施加到真实灯光能量/物理环境强度，白底相机背景单独保持白色。自动逐镜头修正可使用曝光、光源柔和度，以及经过条件限制的金属反光布光倍率；执行时遵守本次指令的保留字段，并通过候选比较后决定是否采用。同类材质微表面由下述独立流程处理。

## 联网材质代理

### 预览完成后的自动流程

真实预览任务完成后，服务器自动检查所选图组的AI诊断并排队材质任务，浏览器无需另发查找请求。只有 `source=ai` 的结构化需求可以触发，规则回退报告不触发。需求格式为：

```json
{
  "material_requests": [{
    "query": "fine cotton fabric",
    "targets": [{"object_name": "Bag body", "material_name": "Woven canvas"}],
    "reason": "包身缺少细密织物表面变化，现有材质名称明确为帆布；建议试用同类微表面。",
    "confidence": 0.5
  }]
}
```

最多2项需求，每项1–12个不重复的真实网格/材质对。`query` 是2–160字符的英文材质检索词，不接受URL、文件路径、代码或额外可执行字段。材质类别必须与服务器提供的明确依据一致；目标必须出现在当前图片的真实表面采样中，应用前再用保存场景的射线定位验证。默认猜测的材质类别、复杂着色器或已有外部资源的目标不可自动替换。

`confidence` 范围0–1，表示微表面不足对画面问题的可能贡献，用于记录与处理优先级，不设最低触发分数。符合已知材质类别、真实目标与格式约束的需求可以进行有次数上限的可回退试用，不能把推测写成已验证缺陷。目录选材还需另一次AI判断，匹配置信度至少0.6；AI不可用或退回关键词模式时自动任务停止。对于有可处理目标的新诊断，`material_requests` 必须显式返回，遗漏触发一次结构纠正；历史报告或无可处理目标时缺省按空数组处理。

自动任务记录 `origin:automatic`、`auto_query`、`expected_targets`、`chain`、原方案 `baseline_plan` 和原图组 `baseline_outputs`。`chain` 保存发起预览任务、已尝试目标、原选中镜头及当前轮次。自动应用固定 `use_base_color:false`，只下载/写入粗糙度与OpenGL法线通道，不改金属度、颜色、标签、预设或几何。每轮应用增加方案版本，并真实重渲染原选中图组。

材质链最多2轮，由发起任务的 `plan.max_corrections` 限制在0–2；它与逐镜头曝光修正分别计数。`visual_review:false` 或次数0时不启动。重复完成回调、同链已处理目标、已有外部贴图不会重复应用。下载前后都校验图片版本、方案指纹及活动任务；用户后续修改优先。

项目详情的 `auto_material` 提供 `origin_job_id,round,max_rounds,status,stage,error,updated_at`，可带 `task_id,notice,notes,comparison`。活动状态来自真实材质任务；结束状态还包括 `unchanged,disabled,skipped,reverted,needs_attention`。用同一服务商、模型和评估版本的完整前后图组AI平均评分比较，只有有效评分可比较、平均分提高超过0.02且每张图的已有可靠测量未发现明确退化时自动采纳。持平、下降、提高不超过0.02、评分不可比较、新增完整产品裁切、适用表面高光剪切明显增加或原有可靠测量缺失时，尝试恢复经文件校验的原图和原方案，新候选仍保留。新渲染失败时也尝试恢复；方案已被用户改动时不覆盖。任务记录真实的采用或恢复原因，不以下载完成宣称优化成功。`decision:reverted`、`restored_version`、`auto_finalized` 保存采纳/恢复结果，重启时恢复任务关联和未完成的比较。

### 可选的高级手动接口

`POST /api/projects/{id}/material-agent/tasks` 接收 `{output_id,shot_id,quality,version,points:[{id,x,y}],prompt,use_base_color:false}`，x/y为图片内归一化坐标。返回任务对象和202；相同当前图片、选点、要求和颜色选项的活动或完成请求会复用，避免重复应用。每个项目最多一个活动材质任务。

`GET /api/projects/{id}/material-agent/tasks` 返回该项目最近30个任务；`GET .../tasks/{task_id}` 返回单个任务；`POST .../tasks/{task_id}/cancel` 取消查找下载或关联的真实渲染。任务状态为`queued/running/rendering/completed/failed/cancelled/interrupted`，`stage`是实际步骤，包含`source,targets,selection,downloaded_files,error`；应用后增加`job_id,applied_version`。`completed`仅由真实渲染完成触发。取消不会回退已经应用的材质，重启会明确标记中断或恢复任务关联。

手动入口可在AI不可用时采用明确材质关键词匹配，且展示规则来源；自动入口不采用此回退。手动默认可应用粗糙度、OpenGL法线及可用金属度通道，`use_base_color`默认false；只有显式true才替换颜色。

两种流程的资源来源都限定Poly Haven，记录CC0许可和官方资源链接，联网请求使用独立User-Agent。只允许官方API和贴图主机、HTTPS、无重定向，下载1K PNG/JPG通道并核对官方大小、可用MD5、SHA256和解码尺寸。每张上限16MB。模型返回只可选择目录内实际ID，不能指定下载URL或执行代码。

局部覆盖`object_material_overrides`可记录`textures,source,license,resource,texture_scale,texture_rotation`。`resource`保存`provider,asset_id,name,url,license_url,resolution,downloaded_at,manifest_path`。API同时约束全局/局部贴图必须在当前项目materials目录。工作场景记录实际应用与跳过通道，.blend打包图片；整组下载包含当前材质资源、manifest及材质任务记录。

每个任务都有独立 `runs/{job_id}/`，保存 `request.json`、`result.json`、Blender 日志和渲染文件。`request.json` 是执行层收到的完整快照。对单个请求可直接运行：

```powershell
& 'D:\blender\blender.exe' -b --factory-startup --disable-autoexec --python-exit-code 1 --python blender/worker.py -- '<request.json 的绝对路径>'
```

工程文件是方便在 Blender 中继续编辑的副本；源模型始终在 `originals/` 中。手动修改或删除 `data/` 会破坏任务可追溯性，请在应用关闭后备份整个目录。此服务仅面向可信的本机用户，未设计多用户认证或公网部署。


## 操作保护与运行能力（2026-11-09）

`GET /api/system` 的 `available` 表示 Blender 与 `blender/worker.py` 同时就绪；`blender_available` 单独表示发现可执行文件。`runtime` 还包含 `message`、`missing_components`、`samples_available` 和分别表示渲染、部件定位、Blender工程导出、C4D工程导出的 `capabilities.render/pick/export_blend/export_c4d`。缺少可选导出组件不妨碍已具备的渲染能力。

前端保存方案时发送 `X-Plan-Version: <草稿开始编辑时的版本>`。也支持 JSON `{"plan": {...}, "expected_version": 3}`。版本与服务端不一致则返回409，不修改方案或历史。没有版本参数的旧客户端仍兼容，但不能获得此项冲突保护。

外部材质上传在写入时合并最新摄影方案。同一材质槽在上传期间发生改变则返回409，本次贴图不应用；无关摄影参数的变化会保留。

新任务、上传、重试和场景导出在创建记录或资源前检查相应执行组件。分析任务取消/中断同步更新项目状态，重试恢复 analyzing；取消已分析项目的预览不清除 ready 状态。

默认 `/bundle` 是项目归档，不读取前端勾选的镜头集合；若要筛选镜头、版本或画质，应设计显式过滤接口。
