# QWEN-TTS-BRIDGE (OpenClaw Edition)

[English](#english) | [中文说明](#中文安装引导)

---

## English

### Overview
This is a microservice designed to provide high-quality, real-time speech synthesis for **OpenClaw** using the **Qwen3 Realtime TTS** engine. It handles channel-specific audio encoding, dynamic voice personality selection, and native platform delivery (e.g., Feishu voice bubbles).

### Key Features
- **Channel-Aware Routing**: Optimized formats for Telegram (OGG/Opus) and Feishu (Native Bubbles).
- **Voice Personality**: Automatically selects voices (Companion, Playful, Professional) based on text intent.
- **Opus Optimization**: Native conversion to 32kbps Opus for stable voice delivery.
- **Robust Fallbacks**: Graceful degradation to text or standard audio if synthesis or upload fails.
- **Security**: Secured via Bearer token authentication.

### Requirements
- **Python**: 3.10+
- **System**: `ffmpeg` (Required for Opus conversion and voice bubbles).

### Installation
1. **Clone and Install**:
   ```bash
   git clone https://github.com/KaikiDeishuuu/QWEN-TTS-BRIDGE.git
   cd QWEN-TTS-BRIDGE
   python3 -m venv venv_bridge
   source venv_bridge/bin/activate
   pip install -r tts_bridge/requirements.txt
   ```
2. **Configure Environment (`.env`)**:
   ```bash
   DASHSCOPE_API_KEY=your_key
   INTERNAL_TTS_TOKEN=your_secure_token
   # Optional for Feishu voice bubbles
   FEISHU_APP_ID=your_id
   FEISHU_APP_SECRET=your_secret
   ```
3. **Run**:
   ```bash
   uvicorn tts_bridge.server:app --host 127.0.0.1 --port 5200
   ```

---

## 中文安装引导

### 项目简介
本项目是专为 **OpenClaw** 设计的 TTS 桥接服务，采用 **Qwen3 Realtime TTS** 引擎。它能将文本实时合成为语音，并针对不同平台（Telegram、飞书）进行特定的优化处理。

### 核心特性
- **平台自适应路由**：自动为 Telegram 生成带有波形的 OGG 语音条，并支持飞书的原生语音气泡。
- **智能音色匹配**：根据回答内容自动切换音色（温暖伴侣、活泼、专业）。
- **原生 Opus 优化**：针对飞书和 TG 优化为 32kbps Opus 格式，确保在各类客户端上的播放稳定性。
- **强健的回退机制**：如果语音合成或上传失败，会自动通过 JSON 告知 OpenClaw 回退到文本。
- **安全性**：基于 Bearer Token 的 API 鉴权。

### 安装步骤
1. **源码下载与环境配置**：
   ```bash
   git clone https://github.com/KaikiDeishuuu/QWEN-TTS-BRIDGE.git
   cd QWEN-TTS-BRIDGE
   python3 -m venv venv_bridge
   source venv_bridge/bin/activate
   pip install -r tts_bridge/requirements.txt
   ```
2. **系统依赖**：
   确保您的服务器已安装 `ffmpeg`（用于语音转码）：
   ```bash
   sudo apt update && sudo apt install -y ffmpeg
   ```
3. **配置 `.env` 文件**：
   在项目根目录下创建 `.env`，填入您的 API 密钥：
   ```env
   DASHSCOPE_API_KEY=您的阿里云API_KEY
   INTERNAL_TTS_TOKEN=自定义的内部通信Token
   # 若需启用飞书语音气泡需配置：
   FEISHU_APP_ID=飞书应用ID
   FEISHU_APP_SECRET=飞书应用密钥
   ```
4. **启动服务**：
   ```bash
   uvicorn tts_bridge.server:app --host 127.0.0.1 --port 5200
   ```

### 部署建议
推荐使用 `systemd` 管理服务（参考 `deploy/tts-bridge.service`），并确保服务绑定在 `127.0.0.1` 以保证内部安全。

---

## License
MIT License. See [LICENSE](LICENSE) for details.
