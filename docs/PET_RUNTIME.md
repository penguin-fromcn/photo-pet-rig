# 桌面运行时（可选）

这个仓库除了“照片 → 分层 rig 素材”，还带一个**可选的 Windows 桌面运行时**：
把 `rig.json` 和同画布的分层 PNG 真正变成一个透明、置顶、可拖动、会走路、会掉落的桌宠。

> 运行时**不调用任何大模型 API**。渲染、物理、状态机、托盘全部本地完成。
> 它直接使用你的 PNG 像素，不重绘、不换脸、不做生成式美化。

![运行时渲染预览](pet_demo.png)

上图由仓库自带的**合成 mannequin** 实际渲染，IDLE/WALK 两帧；不是真人照片。

---

## 1. 安装

要求 **Windows 10/11 + Python 3.10+**（推荐 3.11/3.12）。

```powershell
git clone <your-fork-url> photo-pet-rig
cd photo-pet-rig
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[pet]"
```

只做素材、不运行桌面窗口时，`pip install -e .` 就够了。

> PySide6 采用 LGPLv3/GPLv3/商业许可；分发打包后的程序前，请自行确认 Qt/PySide6
> 的许可义务。仓库本身不重新分发 Qt 二进制。

---

## 2. 30 秒跑起来（不需要你的照片）

```powershell
photopet pet --assets examples/demo_character
```

会弹出一个透明、无边框、置顶的合成小人。右键角色打开菜单，托盘图标也可以操作。

不显示窗口、只做一次自检（适合 CI/排障）：

```powershell
photopet pet --assets examples/demo_character --smoke-test 3 --no-tray --smoke-report smoke.json
```

`--diagnostics` 只打印坐标、屏幕、素材和配置，不打开窗口：

```powershell
photopet pet --diagnostics --assets examples/demo_character
```

---

## 3. 换成你自己的角色

### 3.1 先按主流程生成素材

```powershell
photopet inspect "C:\photos\me.jpg" -o inspect
photopet cutout "C:\photos\me.jpg" -o cutout.png
# 写 project.json 后：
photopet validate project.json
photopet build project.json --out "C:\Users\<you>\Desktop"
```

产物是一个 `<slug>_pet_assets/` 文件夹，里面有：

```text
character_original.png
head.png / torso.png / ... / foot_right.png
rig.json
preview.png / preview_poses.png
qa_report.json
```

### 3.2 启动

```powershell
photopet pet --assets "C:\Users\<you>\Desktop\<slug>_pet_assets"
```

不要把 `photo`、`project.json`、个人素材提交进 Git；运行时只读你指定的目录。

### 3.3 素材不合格时会发生什么

- **`rig.json`/某个图层缺失、尺寸不一致、JSON 损坏**：启动时给出具体错误，
  并在允许时用 `character_original.png` **降级为整图模式**。
- 整图模式仍支持拖动、重力、反弹、点击和托盘，但**不能**做真实的分肢动画。
  程序不会拿整张照片旋转来冒充“四肢摆动”。
- 合成示例的 14 层都完整，所以默认跑的是真正的分层骨骼模式。

---

## 4. 鼠标与托盘

| 操作 | 行为 |
|---|---|
| 左键单击 | 短跳 + 压缩回弹；可能显示本地文字气泡 |
| 左键双击 | 更高的跳跃/更明显的反应 |
| 按住左键拖动 | 进入 DRAG；暂停重力和自主走路，保留抓取偏移 |
| 松开左键 | 按最近约 120 ms 的鼠标轨迹计算释放速度，进入 FALL |
| 右键 | 设置菜单：暂停/恢复、设置、打招呼、回到主屏底部、开机启动、诊断、退出 |
| 托盘图标 | 显示/隐藏、暂停/恢复、设置、退出 |
| 设置窗口 | 角色高度、帧率、走路速度、重力、反弹、动画开关、气泡开关 |

透明区域默认**鼠标穿透**（点击桌面图标不会被透明窗口挡住）；只有角色像素附近
才会接住鼠标。窗口不会主动抢键盘焦点。

---

## 5. 状态机与优先级

状态定义在 `photopet/pet/behavior_controller.py`：

| 状态 | 进入条件 | 退出条件 |
|---|---|---|
| `IDLE` | 默认/休息结束 | 计时到点后随机进入 WALK；用户操作可抢占 |
| `WALK` | 站立随机时间后选择方向 | 走够随机时长 → IDLE；到边缘转向；用户操作抢占 |
| `DRAG` | 左键在角色上按下并移动超过阈值 | 松开左键 → FALL |
| `FALL` | 空中且向上速度耗尽/被抛起 | 接触地面 → LAND |
| `LAND` | 触地且有可感知速度 | 弹跳衰减到阈值 → IDLE/WALK |
| `CLICK_REACT` | 单击/双击（未拖动） | 动画结束 → 回到原行为状态 |
| `PAUSED` | 托盘/菜单暂停 | 恢复后回到 IDLE/WALK |

优先级（高 → 低）：

1. 退出程序
2. 用户拖拽
3. 用户主动点击反馈
4. 空中物理（FALL/LAND）
5. 自主走路
6. 待机

`PAUSED` 是一个“行为闸门”：暂停自主走路/点击反馈，但不阻止用户拖拽和退出。
点击有冷却时间，避免连续重复触发；拖动中不会触发点击动画。

---

## 6. 物理与动画

### 物理

- 固定子步长积分（默认 1/120 s），避免不同帧率下轨迹漂移。
- 重力、空气阻力、地面摩擦、水平/垂直反弹系数均可配置。
- 释放速度取最近一小段鼠标轨迹的**加权最小二乘**速度，并限制最大值；
  松手后不会把速度清零。
- 地面使用当前显示器 `availableGeometry().bottom()`，不会掉到任务栏下面。
- 横向边界是虚拟桌面；拖拽跨屏时重新选择角色所在显示器并重新夹取。

### 动画

- `rig.json` 定义 15 个骨骼、pivot、父子关系和角度范围。
- 每个图层是**完整画布**的透明 PNG；渲染器对每层做“绕 pivot 旋转 + 统一缩放”，
  所以缩放时不会丢失对齐关系。
- 走路是参数化循环：大腿、小腿、脚、上臂、前臂、躯干、头、身体起伏分别有配置项。
- 站立呼吸、落地压缩/回弹、拖拽惯性摆动、点击跳跃都是独立叠加量。
- 所有参数在 `config.json` 的 `animation` 段，不在代码里散落硬编码。

> **交叉手臂素材的现实限制**：如果源照片里手臂是抱胸的，上臂和前臂的可见像素
> 被衣服/另一只手臂遮住，旋转超过几度就会露缝。示例 rig 的
> `recommended_angle_deg` 就是保守值；想做大摆臂，需要提供手臂分开的照片或
> 重做切件。这不是渲染器能“补”出来的。

---

## 7. 配置文件

默认从可执行文件/仓库根目录读 `config.json`；不存在时用内置默认值并在退出/设置时
写回。也可以显式指定：

```powershell
photopet pet --assets ... --config "C:\path\my_pet_config.json"
```

常用字段：

| 段 | 字段 | 说明 |
|---|---|---|
| `assets` | `directory` / `rig_file` / `character_png` | 素材目录和文件名 |
| `window` | `display_height_px` / `padding_px` / `click_through_transparent` | 显示尺寸和透明区域穿透 |
| `render` | `fps` / `idle_fps` / `max_dpr` | 帧率、静止降帧、最大设备像素比 |
| `physics` | `gravity_px_s2` / `restitution` / `wall_restitution` | 重力/落地/撞墙反弹 |
| `interaction` | `drag_threshold_px` / `release_velocity_window_s` | 点击与拖拽的判定 |
| `behavior` | `walk_speed_px_s` / `idle_min_s` / `edge_margin_px` | 自主行为 |
| `animation` | `walk_*` / `idle_*` / `land_*` / `click_*` | 动画幅度与时长 |
| `speech` | `enabled` / `lines` / `duration_ms` | 本地气泡台词，不联网 |
| `startup` | `autostart` | 开机启动，默认 `false` |

仓库里的 [`examples/pet_config.example.json`](../examples/pet_config.example.json)
是一份完整示例，可以直接复制成自己的配置。

---

## 8. 代码结构

```text
photopet/pet/
├─ app.py                 # 入口、诊断、日志、smoke test、配置/素材路径解析
├─ pet_window.py          # 透明置顶窗口、Win32 hit-test、托盘、菜单
├─ character_renderer.py  # 全分辨率图层绘制、统一缩放、阴影
├─ rig_loader.py          # rig.json 校验、分层/整图降级
├─ animation_controller.py# 各状态姿态和插值
├─ physics.py             # 固定步长重力/碰撞/摩擦
├─ behavior_controller.py # IDLE/WALK/PAUSED 与计时器
├─ interaction_controller.py # 点击/拖拽/释放速度
├─ settings.py            # dataclass 配置、读写、校验
├─ screen_utils.py        # 多屏、DPI、工作区、虚拟桌面
├─ speech_bubble.py       # 本地文字气泡
└─ settings_dialog.py     # Qt 设置窗口
```

素材构建器在 `photopet/` 顶层（`project.py`、`cutout.py`、`patches.py`、
`validate.py`、`preview.py`、`cli.py`），两者互不依赖：只装核心依赖也能构建素材。

---

## 9. 性能、DPI 和多显示器

- 默认 30 FPS；静止时降到 15 FPS，停止走动后计时器可暂停，避免空转。
- 所有 PNG 只加载一次，之后每帧只做缓存层的 QPainter 旋转/缩放。
- 全分辨率图层绘制，然后一次性缩放到窗口 DPR；避免预缩放导致的关节接缝。
- 鼠标穿透通过 `WM_NCHITTEST` 按 alpha 命中，透明区域返回 `HTTRANSPARENT`。
- 窗口使用当前显示器的 `devicePixelRatio`；移动/缩放/换屏时重新计算布局。
- 地面取当前显示器工作区底部；横向边界取虚拟桌面，任务栏不会遮住脚。

---

## 10. 打包成 Windows exe（可选）

```powershell
python -m pip install pyinstaller
powershell -ExecutionPolicy Bypass -File packaging\build_pet.ps1 -Python .venv\Scripts\python.exe
```

产物：

```text
dist\pet\PhotoDesktopPet\
├─ PhotoDesktopPet.exe
├─ config.json
└─ _internal\
   └─ assets\            # 合成 demo，随包分发
```

自检：

```powershell
dist\pet\PhotoDesktopPet\PhotoDesktopPet.exe --smoke-test 3 --no-tray --smoke-report smoke.json
```

打包版把 `examples/demo_character` 放到 `_internal/assets`，`packaging/pet_config.json`
会作为 exe 同目录的 `config.json`。换成自己的角色时，把素材文件夹放在 exe 旁边并改
`config.json` 的 `assets.directory` 即可（或直接用源码版 `--assets` 参数）。

> PyInstaller 打包会复制 Qt 运行库，体积通常 100 MB 以上；这是 onedir 正常现象。
> 仓库不提交 `dist/`、`build/`、exe 或 Qt 二进制。

---

## 11. 隐私与安全

- 运行时**不联网**，不调用云模型，不上传照片。
- 文字气泡台词完全来自 `config.json`。
- 日志默认写 `logs/pet.log`；诊断报告包含窗口/屏幕坐标，不包含照片内容。
- 仓库默认 `.gitignore` 排除 `inputs/`、`outputs/`、`private/`、`*_pet_assets/`、
  `*.zip`；不要强行 `git add -f` 个人素材。
- 请不要把别人的照片做成冒充、骚扰或未经同意的桌宠。

---

## 12. 已知限制

- 需要**分层素材 + rig.json** 才能做真实四肢动画；单张整图只能降级。
- 单视图、正视/大半身照片效果最好；侧身、遮挡、长裙、交叉手臂会限制角度。
- 交叉手臂素材的上臂/前臂只能小角度摆动；大摆臂需要分开手臂的素材。
- 走路循环是程序化的小幅摆动，不是逐帧真人动画；没有跑步、表情和口型。
- 透明区域穿透在 Windows 上通过 Win32 hit-test 实现；极端 DPI 缩放/远程桌面下
  仍建议先用 `--diagnostics` 检查坐标。
- 非 Windows 平台可以构建素材，但桌面透明置顶/穿透/托盘行为未做同等验证。
- 运行时依赖 PySide6；不安装 `[pet]` 时只能用构建器，不能开窗口。

---

## 13. 验证

```powershell
python -m pip install -e ".[dev]"
python -m pytest -q
```

运行时测试覆盖：物理子步/反弹、状态机优先级、释放速度、分层/降级加载、
DPI 渲染、配置校验、CLI 参数和合成 demo 的启动路径。真实窗口的
透明/焦点/命中测试需要 Windows 桌面会话，`--smoke-test` 会生成报告和截图。
本次公开版本的实测记录见 [`PET_VALIDATION.md`](PET_VALIDATION.md)。
