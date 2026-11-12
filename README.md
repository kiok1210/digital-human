# 数字人口播 · 自动剪辑工作流

一张照片 + 一段文案，本地离线生成 9:16 竖屏数字人口播成片。多镜头自动切分、转场、大字幕与背景音乐全部自动完成。

照片、文案与音频**只在本机处理**，不上传任何服务器。

## 快速开始

```
setup.bat      # 首次运行：安装本地运行环境与模型（约 2GB，耗时较长）
start.bat      # 启动工作流，自动打开 http://127.0.0.1:7860
数字人-start.bat  # 先启动 Fish Speech，再启动工作流；Fish 不可用时回退 Piper
```

`start.bat` 若检测到环境缺失，会自动先跑一次安装。

`数字人-start.bat` 会按顺序检查 Fish Speech → 等待健康检查 → 启动创作台。已经运行的服务会跳过，
不会重复占用端口。加 `-RequireFish` 可在 Fish 没有就绪时停止，而不是回退 Piper；加
`-NoBrowser` 可只启动服务而不打开浏览器。

## 工作流两种模式

在页面「配音与剪辑设置 → 镜头数量」中选择：

| 镜头数量 | 使用的管线 | 说明 |
| --- | --- | --- |
| 1 | `generate_video()` | 单镜头口播，适合短文案；可选混入背景音乐 |
| 2–6 | `generate_edited_video()` | 按标点把文案切成多段，逐段生成后自动剪辑 |

多镜头管线的处理顺序：

1. 文案按 `。！？` 分句，均分到指定镜头数
2. 每个镜头独立执行配音 → 口型驱动 → 竖屏底板
3. 可选插入封面卡
4. 后期拼接（FFmpeg xfade，或 HyperFrames HTML 包装）
5. 可选混入背景音乐

## 引擎融合（本地默认 · 云端可选）

页面「配音 / 口型 / 后期」三组下拉可切换。未配置密钥时云端选项会显示「未配置」并禁用。

| 层 | 默认（本地） | 可选 |
| --- | --- | --- |
| 配音 | Piper | Fish Speech 本地服务 / Fish Audio 云端（`FISH_API_KEY`） |
| 口型 | SadTalker | HeyGen（`HEYGEN_API_KEY`，会上传人像与音频） |
| 后期 | FFmpeg | HyperFrames（需本机 Node.js 22+ / `npx`） |

配置方式：复制 `.env.example` 为 `.env` 后填入密钥，再重启 `start.bat`。

推荐组合：

- **全本地**：Piper + SadTalker + FFmpeg（隐私最好）
- **全本地高质量配音**：Fish Speech + SadTalker + FFmpeg
- **质量优先配音**：Fish + SadTalker + HyperFrames
- **全云口播**：Fish + HeyGen + HyperFrames（最快成片观感，内容出本机）

HyperFrames 负责封面大字、动能字幕与镜头叠化包装；口型仍由 SadTalker / HeyGen 生成。

## 本地 Fish Speech

本机已独立安装官方 Fish Speech 1.5.1 与 1.5 模型，桌面的「数字人-start」会先启动 Fish，
通过健康检查后再启动创作台。当前电脑使用 CPU 推理；短句通常需要约 1–3 分钟，首次启动还需
约 2 分钟加载与预热。S2-Pro 仍可通过 `.env` 指向局域网高显存主机，官方建议至少 24GB 显存。

```bash
git clone https://github.com/fishaudio/fish-speech.git
cd fish-speech
conda create -n fish-speech python=3.12 -y
conda activate fish-speech
pip install -e .[cu126]        # CPU 试验改成 .[cpu]
hf download fishaudio/s2-pro --local-dir checkpoints/s2-pro
python tools/api_server.py \
  --llama-checkpoint-path checkpoints/s2-pro \
  --decoder-checkpoint-path checkpoints/s2-pro/codec.pth \
  --half --listen 0.0.0.0:8080
```

健康检查返回 `{"status":"ok"}` 后，刷新本项目页面，配音引擎会出现
「Fish Speech（本地）」。同机无需修改；局域网部署时在 `.env` 设置：

```dotenv
FISH_LOCAL_URL=http://192.168.1.50:8080/v1/tts
DOUYIN_TTS_ENGINE=fish_local
```

一键启动支持两种目录配置：

```dotenv
# 原生 Windows 环境
FISH_SPEECH_DIR=D:\AI\fish-speech
FISH_PYTHON=D:\AI\fish-speech\.venv\Scripts\python.exe

# 或 WSL 环境
FISH_WSL_PATH=/home/yourname/fish-speech
FISH_WSL_DISTRO=Ubuntu-24.04
```

如需克隆音色，直接在页面的「Fish Speech 音色工作台」上传 5–10 秒参考录音并填写完全一致的
逐字稿。页面会自动保存到 Fish Speech 的 `references/`，无需手工填写 `reference_id`。文案框
支持插入停顿、强调、语气加重、轻声等标签；1.5 模型会把这些标签转换成合适的停顿和标点。

## 目录结构

```
app.py                    HTTP 服务与任务队列
.env.example              云端密钥与默认引擎模板
pipeline/
  config.py               路径、工具发现、环境自检
  acceleration.py         GPU 发现、编码自检与 CPU 回退
  providers.py            本地/云端引擎探测与选择
  cloud.py                Fish Audio / HeyGen 适配器
  hyperframes_post.py     HyperFrames 后期包装
  proc.py                 子进程执行、时长探测
  text.py                 文案规范化与 ASS 动态字幕
  editing.py              封面卡、转场拼接、BGM 混音（FFmpeg）
  runner.py               单镜管线 + 自动剪辑主管线
web/                      stdlib 前端（HTML/CSS/JS）
scripts/setup.ps1         安装脚本
assets/bgm/               放可商用背景音乐，页面可直接选曲
```

## 背景音乐

把音频文件放进 `assets/bgm/`（支持 `.mp3 .m4a .wav .aac .flac .ogg`），刷新页面即可在选择列表中看到；也可以直接在页面里上传本地音乐。

## 成片规格

- 分辨率 1080×1920（9:16），25 fps，H.264 + AAC
- 字幕：微软雅黑，弹出式缩放动画，对齐底部安全区
- 转场：叠化 / 柔化溶解 / 左滑 / 上滑 / 圆形展开 / 平滑左移

## 环境说明

安装脚本会优先使用 micromamba；若网络不可达，会用系统 Python 3.9–3.12 创建项目独立虚拟环境，
再从国内镜像安装依赖，不会把依赖写进系统 Python。关键产物：

- `.runtime/` 内的 micromamba 环境或 `venv/` 虚拟环境
- `vendor/SadTalker/` 源码与 `checkpoints/` 模型
- `vendor/bin/ffmpeg.exe`（系统 Python 路径下由 pip wheel 提供）
- `models/piper/` 中文语音

`ffprobe` 为可选项，缺失时 `pipeline/proc.py` 会解析 ffmpeg 输出获取时长。

## 生成耗时参考

CPU（无独立显卡）：每个镜头约 15 分钟，3 镜头成片约 45 分钟。
显卡（≥4GB 显存）会自动启用 CUDA，速度提升显著。

## 使用须知

- 仅上传本人或已获授权的人像
- 背景音乐请使用无版权或已获授权的素材
- 生成内容请按发布平台规则标注 AI 生成

## GPU 自动发现与可选加速

创作台会自动发现 NVIDIA、AMD Radeon 和 Intel GPU，并分别检查 AI 模型与 H.264 硬件编码能力。
在「配音与剪辑设置 → GPU 加速」中可以选择：

- **自动选择（推荐）**：只启用通过自检的 GPU 后端。
- **仅使用 CPU**：禁用 SadTalker GPU 和 FFmpeg 硬件编码。
- **GPU 优先**：口型模型优先使用兼容的 CUDA/ROCm，剪辑优先使用 NVENC、AMD AMF 或 Intel QSV；运行失败会回退 CPU。

RX 580 / RX 588 在 Windows 下通常可以通过 AMD AMF 加速视频编码，但 SadTalker 的模型推理仍使用
CPU；在 Linux 且安装兼容的 ROCm PyTorch 时才会启用 AMD 模型推理。NVIDIA 需要 CUDA 版 PyTorch，
且为了避免口型阶段爆显存，模型 GPU 的安全阈值为 4GB。可以在 `.env` 设置默认选项：

```env
DOUYIN_ACCELERATION=auto
```
