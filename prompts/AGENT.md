# AI Agent 提示词 — 真人照片 → pivot 骨骼桌面宠物素材

你是一名**能看图、能编辑文件、能运行终端**的视觉编码 agent，当前位于
[`photo-pet-rig`](../README.md) 仓库中。你的任务是把**一张真人全身照片**做成经过校验、
可用 pivot 绑定的 2D 桌宠分层素材，并实际交付到用户桌面。

如果当前环境不能看图、不能运行命令，直接说明并停止，不要假装已经完成。

---

## 可直接粘贴的任务块

把下面占位符替换后，连同本文件一起发给 agent：

```text
PHOTO_PATH: <用户全身照片的绝对路径>
OUTPUT_DIR: <可选；默认用户桌面>
RIGHTS: I own this photo / I have permission to process and use it.

请严格按照 prompts/AGENT.md 执行，阅读 prompts/PROJECT_SCHEMA.md 后再写 project.json。
必须实际运行本仓库的 builder 生成文件，不能只给建议或示意图。
必须逐像素保留源照片中的脸部；禁止用生成式模型重画、替换、美颜或改变人物身份。
手或身体部位不可见时，使用全透明图层并如实报告限制。
把最终文件夹和 ZIP 交付到桌面，报告准确路径和 QA 结果。
```

---

## 不可违反的规则

1. **上传的照片是唯一身份来源。** `head.png` 中可见脸部的 RGB 必须与源图逐字节一致。
   不得重绘、美颜、换脸、改变五官、发型、肤色、口罩、兜帽或表情。
2. **不得对人物做生成式重绘。** 构建流程只做：裁取原始像素、清理 alpha 边缘、在关节处
   复制少量父层像素、用邻近真实布料生成合成补片。不得调用图像生成模型补手、补身体、
   补衣服或补脸。
3. **不得凭空创造被遮挡的解剖结构。** 如果双手完全不可见，把两个 hand 部件设为
   `"empty": true, "polygons": []`。如果某条肢体完全在画面外或被完全遮挡，也留空并
   报告。补片不是“恢复的真相”，只是防空洞的合成层。
4. **不要为了标注而缩放照片。** 所有坐标都是原图（按 EXIF 方向修正后）的绝对像素坐标。
   builder 不支持把单个图层单独居中或重新缩放。
5. **不得掩盖 QA 失败。** 脸部一致性、图层重组、补片隐藏、静态空洞检查只要有一项失败，
   就修正 project 或如实报告，不能删除 QA、降低检查标准或伪造结果。
6. **不要把用户的照片、alpha、project 或输出提交到 Git。** 这些是个人数据，必须留在
   `private/`、`inputs/`、`outputs/`、`*_pet_assets/` 等被忽略的位置。`examples/` 只能放
   合成或明确获准公开的素材。
7. **取得授权。** 只处理用户拥有或已获得许可的照片。

---

## 第 0 步 — 检查仓库与安装

至少阅读：

- `README.md`
- `prompts/PROJECT_SCHEMA.md`
- `examples/mannequin/project.json`
- `photopet/project.py`（校验规则）
- `photopet/builder.py`（构建与 QA）

安装：

```bash
python -m pip install -e .
# 可选：自动第一遍抠图；首次使用会下载模型权重
python -m pip install -e ".[rembg]"
```

确认命令可用：

```bash
photopet --version
```

如果 `photopet` 不在 PATH，后续所有命令改用 `python -m photopet`。

---

## 第 1 步 — 照片适合性判断

先看照片，给出简短结论，再决定是否值得继续拆层。

### 适合

- 正面或接近正面的全身/大半身；
- 头、肩、肘、髋、膝、踝、脚至少部分可见；
- 四肢没有被宽大衣服完全合并成一个轮廓；
- 手臂、躯干、腿之间有足够区分度，能画多边形；
- 脸部可见且清晰度足够；
- 没有严重运动模糊、多人重叠或大面积遮挡。

### 不适合或只能受限输出

- 双臂交叉、双手完全不可见 → 仍可做正面切件，但**不能做自然摆臂**，只能小角度微动；
- 只有侧身/背面 → 只能做单视图，不能恢复另一侧；
- 长裙/长外套完全遮住腿 → 只能做上半身或近似下半身，不能伪造腿；
- 脸部被遮住或转动过大 → 身份校验和表情动画受限；
- 多人、杂物、复杂背景 → 先清理 alpha，或建议换照片；
- 脚被裁掉 → 脚部 pivot 和落地锚点只能是近似值。

如果照片根本不适合（例如完全看不到关节），停止并请用户换一张。
如果部分适合，就构建真实存在的部分，并明确报告缺失部分。

---

## 第 2 步 — 生成标注参考

```bash
photopet inspect "$PHOTO_PATH" -o inspect
```

打开 `inspect/grid.png` 和带网格的裁剪图。网格数字是**原图绝对坐标**。
如果查看器显示的尺寸与 `inspect` 报告不同，以 `inspect` 读取到的方向修正后尺寸为准。

---

## 第 3 步 — 透明底

```bash
photopet cutout "$PHOTO_PATH" -o cutout.png
```

把 `cutout.png` 分别放在深色和浅色棋盘背景上检查。

- 有第二个人或杂物时，可尝试 `--keep-largest`，但要检查是否误删了真实分离的肢体/脚。
- 只允许通过 mask 修洞和边缘，**不得重绘脸部**。
- 自动抠图质量不够时，换 matting 工具或手工 alpha，保存为灰度 PNG 或 RGBA 透明图。
- 把最终 alpha 写进 project 的 `alpha` 字段。

---

## 第 4 步 — 写 `project.json`

以 `examples/mannequin/project.json` 为结构模板，逐字段阅读
`prompts/PROJECT_SCHEMA.md`。

硬性要求：

- `schema` 必须是 `photopet/project@1`；
- 15 个标准骨骼全部存在；
- 14 个标准部件全部存在；
- 画布等于原图方向修正后的尺寸；
- `face.polygon` 只覆盖可见脸/口罩区域；
- 14 个部件的多边形合计覆盖至少 **85%** 前景 alpha，目标 99%+。

### Pivot 放在可见关节缝

| 关节 | Pivot 位置 |
|---|---|
| 颈 | 头部底部 / 躯干顶部 |
| 肩 | 袖子与躯干交界处 |
| 肘 | 上臂袖与前臂袖的可见弯折处 |
| 腕 | 袖口/手腕末端；有手时也是 hand 的 pivot |
| 髋 | 裤腿/腿与躯干交界处 |
| 膝 | 可见膝盖缝 |
| 踝 | 鞋口/裤脚交界处 |

骨骼层级必须保持：

```text
root
├─ torso
│  ├─ head
│  ├─ upper_arm_left → forearm_left → hand_left
│  └─ upper_arm_right → forearm_right → hand_right
├─ thigh_left → shin_left → foot_left
└─ thigh_right → shin_right → foot_right
```

面对镜头时，**人物左侧在画面右边**。在 JSON 中始终保持这个命名约定。

### 多边形规则

- 多边形画的是**该部件可见区域**，不是理想化全身解剖。
- 一个部件被遮挡分成几块时，用多个 polygon。
- `z` 大的赢重叠。默认值已符合标准遮挡顺序，除非照片要求，否则不要乱改。
- **头**：包含可见头发/兜帽/脸，到脖子为止；不要切断眼睛、鼻子、嘴。
- **脸**：只包住可见脸/口罩，不能包含头发、兜帽、背景或衣服。
- **躯干**：衣服主体、肩、下摆；不要画一个包住全身的巨型 torso。
- **上臂/前臂**：沿可见袖子/袖口；不要把前臂画进遮挡它的交叉手臂里。
- **腿**：在可见膝缝处分段；脚在可见踝缝处分段。
- **手**：有真实手部像素才画；没有就设 `"empty": true, "polygons": []`。
- **torso** 设 `"fallback": true`。

### 交叉手臂与严重遮挡

builder 仍能切层和生成关节补片，但无法恢复交叉手臂后面的胸口表面。
把手臂旋转范围设得很小，并在 `notes` 中写明“不支持自然摆臂”。不得伪造胸口或手。

### 脸部保护

`face.polygon` 是身份契约：

- 多边形内所有像素 alpha 强制为 255；
- 这些像素强制归入 `head`；
- builder 校验 `head.png` 脸部 RGB 与源图逐字节一致。

如果校验失败，修 project；不要关掉检查。

---

## 第 5 步 — 先 validate，再 build

```bash
photopet validate project.json
```

修完所有错误后：

```bash
photopet build project.json --out "<OUTPUT_DIR>" --force
```

省略 `--out` 时默认用户桌面。输出：

```text
<Desktop>/<slug>_pet_assets/
<Desktop>/<slug>_pet_assets.zip
```

---

## 第 6 步 — 必须检查真实输出

打开并检查：

- `preview.png` — 主透明底合成；
- `preview_layers.png` — 同画布的全部图层；
- `preview_poses.png` — 静止 + 小角度 pivot 测试；
- `preview_rig.png` — 关节示意；
- `rig.json` — pivot、父节点、z、角度；
- `qa_report.json` — 机器校验。

至少确认：

1. `summary.exact_layer_reassembly` 为 `true`；
2. `summary.face_pixels_unchanged` 为 `true`；
3. `summary.patches_hidden_at_rest` 为 `true`；
4. `summary.zero_enclosed_holes_at_rest` 为 `true`；
5. torso 的 bbox 不是整个人物（说明 torso 多边形没吞掉四肢）；
6. 头/颈、肩、髋、膝的切线看起来像关节，不像随机切块；
7. 没有背景条纹、白边/黑边、鞋底或地面碎块；
8. `preview_poses.png` 在小角度下没有明显缺块。腿间/臂间的窄负空间在某些角度可能看起来
   像封闭区域，要**目视判断**，不要只看一个像素计数。

### 迭代

出问题时只修最小原因，然后重建：

| 现象 | 优先检查 |
|---|---|
| torso 图层覆盖整个人 | torso 多边形吞掉了手臂/腿，缩小它 |
| 某部件没有像素 | 多边形画偏了，或 alpha 不对 |
| 白边 | alpha 边缘带了背景；改 alpha 或 `--keep-largest` |
| 静止时看得到补片 | 构建应报错；检查补片裁剪和 z |
| 脸部一致性 false | face 多边形包含了头发/背景，或 head 切线穿过脸 |
| 手是空/漂的 | 隐藏手应 empty；不要伪造 |
| 小角度下肩/膝有洞 | pivot 放到可见关节缝，或增大补片半径/overlap |
| 出现第二个人 | 清理 alpha 或换照片，不要靠切 layer 掩盖 |

不得为了通过而删除 QA 输出或降低检查标准。

---

## 第 7 步 — 向用户报告

用用户的语言报告：

- 最终文件夹路径和 ZIP 路径；
- 原图尺寸和输出画布；
- 可见身体图层数量、补片数量；
- 重组、脸部一致性、补片隐藏、静态空洞的 QA 数值；
- 能做什么（idle、小幅走路、点击反馈、拖拽/落地）；
- 不能做什么（隐藏手、交叉手臂、侧身、表情、大角度旋转）；
- 哪些 pivot/切线是估计值；
- 重新构建的准确命令。

禁止声称：

- 手/肢体缺失时还说“完整绑定”；
- “任何角度都没有洞”；
- 交叉/隐藏手臂的照片能“自然摆臂”；
- 没有 QA 数值还说“脸部完全保留”；
- 用生成式模型替换人物后说“原图还原”。

---

## 最终检查清单

- [ ] 已阅读 `prompts/PROJECT_SCHEMA.md`。
- [ ] 已检查照片并说明适合性/限制。
- [ ] 已生成并目视检查 alpha。
- [ ] 使用原始画布坐标，没有为标注缩放。
- [ ] 15 个骨骼、14 个部件全部填写。
- [ ] face 多边形在可见脸部内，head 切线没有穿过五官。
- [ ] 隐藏的手是 empty，没有伪造。
- [ ] `photopet validate` 通过。
- [ ] `photopet build` 成功。
- [ ] 已读 `qa_report.json`，静态门槛全部为 true。
- [ ] 已看 `preview.png`、`preview_layers.png`、`preview_poses.png`、`preview_rig.png`。
- [ ] 已交付文件夹和 ZIP 到桌面。
- [ ] 已如实说明限制。
- [ ] 没有把用户照片或输出提交到 Git。
