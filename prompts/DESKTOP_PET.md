# 端到端提示词：现有照片 → 分层 rig → 真正在桌面运行的桌宠

把本文件全文交给一个**能看图片、能执行终端命令**的 AI 编码代理（Claude Code、
Codex、DeepSeek Harness 等），替换下面的输入，然后让它实际执行。不要只让它给建议。

```text
PHOTO_PATH: C:\absolute\path\to\your\full-body-photo.jpg
OUTPUT_DIR: C:\Users\<you>\Desktop
RIGHTS: I own this photo / I have permission to process and use it.
RUNTIME: Windows 10/11
```

## 0. 成本与隐私约束（必须遵守）

- 运行时**不调用任何大模型 API**。渲染、物理、走路、点击、托盘全部本地完成。
- 素材处理优先使用仓库里的确定性 Python builder；**不要**生成或重绘人物的脸、
  手、衣服，也不要用生成式模型“美化”人物。
- 默认只做**一次**视觉分析（看网格图和参考裁剪）；不要反复上传整张照片、
  不要对每一帧调用视觉模型、不要循环调用付费服务。
- `rembg` 是可选项。首次使用会下载模型；先征得用户同意，或让用户提供透明底 PNG。
- 不把用户的照片、alpha、`project.json` 或输出提交到 Git；参考 `.gitignore`。
- 如果照片不适合（严重遮挡、侧身、长裙盖腿、运动模糊、多人重叠），先说明限制，
  不要用假肢或假手硬补。

## 1. 检查环境

```powershell
python --version
python -m pip install -e ".[pet]"
photopet --version
```

要求 Python 3.10+，Windows 10/11。`[pet]` 会安装 PySide6；不安装它也能只构建素材，
但无法运行桌面窗口。

## 2. 看照片，先判断能不能做

```powershell
photopet inspect "<PHOTO_PATH>" -o inspect
```

打开 `inspect/grid.png`、`inspect/crops/*_grid.png`，确认：

- 是否正面/大半身；
- 头、肩、肘、髋、膝、踝是否至少部分可见；
- 左右手臂和腿是否被衣服合并；
- 手是否被完全遮挡；
- 脸部是否清晰、无遮挡。

把判断写进最终报告。**不要跳过这一步直接切图。**

## 3. 准备透明底

优先让用户提供透明 PNG。没有时：

```powershell
photopet cutout "<PHOTO_PATH>" -o cutout.png
```

使用前检查 alpha；背景残留、头发缺口、手指缺失都要指出。不要用生成式模型修补。

## 4. 写 `project.json`

严格按 [`PROJECT_SCHEMA.md`](PROJECT_SCHEMA.md) 和
[`AGENT.md`](AGENT.md) 的坐标规则，用**原图绝对像素坐标**写：

- 15 个骨骼 pivot；
- 14 个身体部件的可见区域多边形；
- 脸部保护多边形；
- 被完全遮挡的手/肢体：保留图层文件名，但多边形留空或标为不可见，
  **不要伪造**。

```powershell
photopet validate project.json
```

## 5. 构建并检查

```powershell
photopet build project.json --out "<OUTPUT_DIR>"
```

检查输出文件夹里的：

- `qa_report.json.summary` 四项必须为 true；
- `preview.png` 的静止外观；
- `preview_poses.png` 的小角度姿态；
- `rig.json` 的 `recommended_angle_deg` 和 `limitations`。

不合格最多迭代 3 次；每次说明改了什么、为什么。不要隐藏失败。

## 6. 启动真实桌宠

```powershell
photopet pet --assets "<OUTPUT_DIR>\<slug>_pet_assets" --config pet_config.json
```

或者直接用打包好的 exe。确认：

- 背景透明，没有黑/白矩形；
- 可以拖动、抛出、落地回弹；
- 会自己走、到屏幕边缘转向；
- 单击/双击有不同反馈，右键有菜单，托盘可暂停/退出；
- 有分层素材时能看到四肢交替；没有分层素材时程序应明确降级，而不是拿整张图旋转冒充。

建议先跑一次无交互自检：

```powershell
photopet pet --assets "<OUTPUT_DIR>\<slug>_pet_assets" --smoke-test 3 --smoke-report smoke.json
```

## 7. 交付格式

最终回复必须包含：

1. 素材是否适合、能实现/不能实现哪些动作；
2. 输出文件夹和 `rig.json` 的绝对路径；
3. 实际运行的命令；
4. `qa_report.json` 的关键结果；
5. 运行截图或 `preview_poses.png` 的路径；
6. 已知限制（尤其遮挡、手、补片、角度上限）。
