# Vendor 项目搭建说明

本仓库的工作流会用到/参考 `vendor/` 目录下的外部项目。

## 1) `vendor/bilibili-video-downloader`

这是一个 Tauri（Rust + Node/pnpm）图形界面下载器项目。

当前 hot_collect 工作流里为了自动化和可控性，**下载字幕/音频使用的是 `BBDown`**（见 `tools/video_assets.py`）。  
如果你希望对齐该项目的下载策略、字幕处理逻辑，或后续将其后端能力抽成 CLI/服务，可在此基础上二次开发。

按上游 README 构建：

```bash
cd vendor/bilibili-video-downloader
pnpm install
pnpm tauri build
```

## 2) `vendor/ms-ra-forwarder`

该项目是 Microsoft “朗读”相关的转发服务（**Text-to-Speech**）。

如果你要部署它（例如用于文本转语音），按上游 README 可使用 Docker：

```bash
docker pull wxxxcxx/ms-ra-forwarder:latest
docker run --name ms-ra-forwarder -d -p 3000:3000 wxxxcxx/ms-ra-forwarder
```

浏览器打开 `http://localhost:3000` 检查服务是否可用。

注意：它不是 Speech-to-Text（语音转文字）服务。当前工作流的“语音转文字”使用的是讯飞“极速语音转写”（HTTP 任务式，见 `tools/asr.py`）。

## 3) `vendor/BBDown`

BBDown 是命令行式 B 站下载器，本工作流使用它来下载字幕或音频。

推荐安装方式（任选其一）：

```bash
dotnet tool install --global BBDown
```

验证：

```bash
BBDown -h
```

如果你不想安装 dotnet，也可以直接下载 Release 二进制（macOS arm64 示例）并放到项目内：

```bash
mkdir -p vendor/bin
curl -L -o /tmp/BBDown_osx-arm64.zip https://github.com/nilaoda/BBDown/releases/download/1.6.3/BBDown_1.6.3_20240814_osx-arm64.zip
unzip -o /tmp/BBDown_osx-arm64.zip -d vendor/bin/BBDown
chmod +x vendor/bin/BBDown/BBDown
```

然后在 `.env` 里配置 `BBDOWN_BIN=vendor/bin/BBDown/BBDown`。
