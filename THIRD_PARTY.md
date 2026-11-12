# 第三方开源组件

安装脚本会下载或安装以下组件；源码、模型和运行环境均被 `.gitignore` 排除。

- [SadTalker](https://github.com/OpenTalker/SadTalker)，Apache-2.0，用于将单张照片和语音转换为口播视频。
- [Piper](https://github.com/OHF-Voice/piper1-gpl)，GPL-3.0-or-later，用于本地文字转语音，本项目通过独立命令行进程调用。
- [Piper huayan 中文声音](https://huggingface.co/rhasspy/piper-voices/tree/main/zh/zh_CN/huayan/medium)。其模型卡将原始训练数据集许可标为 Unknown；商业发布前应自行确认使用权，或替换为许可明确的声音模型。
- [FFmpeg](https://ffmpeg.org/)，用于竖屏合成、转场、混音、编码和 ASS 字幕渲染。
- [HyperFrames](https://github.com/heygen-com/hyperframes)，Apache-2.0，可选后期包装引擎（HTML → MP4），通过本机 `npx` 调用。
- PyTorch、GFPGAN、facexlib、BasicSR 及其余 Python 依赖分别遵循各自的上游许可证。

可选商业云服务（需自行申请密钥，人像/文案会离开本机）：

- [Fish Audio](https://fish.audio/) — 云端 TTS
- [HeyGen](https://www.heygen.com/) — 云端口型 / talking photo

请只处理本人或已获明确授权的肖像、声音、文案和音乐。

