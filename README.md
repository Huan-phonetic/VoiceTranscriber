# VoiceDecoder

语音备忘录本地转写工具，使用 faster-whisper，支持 GPU 加速和思源笔记自动嵌入。

## 功能

- 扫描指定录音目录，列出未处理的音频文件
- 使用 [faster-whisper](https://github.com/SYSTRAN/faster-whisper) 本地转写，支持 CUDA GPU 加速
- 转写进度条实时显示音频时间进度
- 批准后将音频和文字自动推送到思源笔记对应日期的日记页

## 依赖安装

```bash
# GPU 版 PyTorch（CUDA 12.6，RTX 30/40 系）
pip install torch --index-url https://download.pytorch.org/whl/cu126

# 仅 CPU
pip install torch

# 其余依赖
pip install faster-whisper requests
```

> ffmpeg 需单独安装（用于解码音频）：
> - Windows: `winget install ffmpeg` 或 [官网下载](https://ffmpeg.org/download.html)

## 使用方法

1. 双击 `voice.bat` 启动
2. 首次运行弹出「设置」对话框，填写：
   - **录音文件目录**：存放录音的文件夹（可点击「浏览…」选择）
   - **思源资产目录**：SiYuan 的 assets 文件夹路径
   - **思源 API 地址**：例如 `http://127.0.0.1:6806`
   - **思源 Token**：思源笔记 → 设置 → 关于 → API Token
   - **日记本 Notebook ID**：通过 `/api/notebook/lsNotebooks` 查询
3. 选择文件后点击「开始转写」，进度条实时更新
4. 转写完成后检查文字，点击「✓ 批准 → 思源」保存

## 模型选择建议

| 模型 | 显存（float16） | 适用场景 |
|------|----------------|----------|
| medium | ~1.5 GB | 日常普通话，速度快 |
| **large-v3** | **~3 GB** | **专业词汇、混合语言，推荐** |
| turbo | ~1.6 GB | large-v3 的快速蒸馏版 |

## 快捷键

| 快捷键 | 操作 |
|--------|------|
| `Ctrl+Enter` | 批准并推送思源 |
| `Ctrl+→` | 跳过 |
| `Ctrl+←` | 上一个 |

## 配置文件

`config.json` 由程序自动生成，不需要手动创建。参考 `config.example.json`。
