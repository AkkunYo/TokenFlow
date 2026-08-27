# 🌟 TokenFlow 统一智能 AI 聚合网关

<div align="center">

[![CI](https://github.com/AkkunYo/TokenFlow/actions/workflows/docker-image.yml/badge.svg)](https://github.com/AkkunYo/TokenFlow/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Docker Architecture](https://img.shields.io/badge/Docker-amd64%20%7C%20arm64-blue)](https://github.com/AkkunYo/TokenFlow)
[![Python Version](https://img.shields.io/badge/Python-3.9%2B-brightgreen)](https://www.python.org/)
[![Node.js](https://img.shields.io/badge/Node.js-20%2B-green)](https://nodejs.org/)

**集成 CLIProxyAPI、gemflow 粘滞负载网关 与 Cursor Agent API Proxy 的多功能统一高可用聚合网关。**

[中文文档 (README_CN.md)](README_CN.md) | [English Documentation](README.md)

</div>

---

## ✨ 核心特性矩阵

| 维度 | 核心技术组件 | 效果与价值 | 实现细节 |
| :--- | :--- | :--- | :--- |
| 🎯 **统一入口** | `CLIProxyAPI (Port 18317)` | 单一 OpenAI 兼容入口，多上游自动故障转移与额度路由 | Go 核心常驻进程，支持热重载与 Web 管理面板 |
| ⚡ **Prompt KV 缓存加速** | `gemflow (Port 8081)` | 提取对话指纹 (`user` + Prompt MD5)，锁定相同实例与出口，降低首字延迟 ~72% | 内存 LRU 会话池 + 最少连接调度 (`Least-Connection`) |
| 🌐 **多出口分流与 Web 监控** | `Mihomo + Zashboard (Port 9090/ui)` | 为每个 Worker 实例分配独立专属代理端口 (`19001..`)，提供现代 Web 控制面板监控节点状态与切换 | 启动后通过 Controller API 自动绑定健康低延迟节点，支持可视化 Web UI |
| 🟩 **NVIDIA 逐 Key SOCKS 分流** | `CLIProxyAPI + Mihomo` | NVIDIA OpenAI-compatible provider 的缺失代理 key 按顺序轮询独立 SOCKS5 出口 | 生成 `0600` 权限运行时配置，不修改只读源文件；已有 `proxy-url` 永不覆盖 |
| 💻 **Cursor 订阅转换** | `cursor-agent-api (Port 4646)` | 将 Cursor Pro / Business 订阅包装为标准 OpenAI 兼容接口，供 OpenClaw 等工具无缝调用 | 无头进程拉起 Cursor CLI (`agent`)，流式解析标准 SSE |
| 🛡️ **双模式保活自愈** | `Systemd (宿主机) / Supervisor Loop (Docker)` | 针对宿主机与容器环境使用差异化守护策略，杜绝僵尸进程与端口冲突 | Linux 一键脚本注册 systemd 单元；Docker 内置进程生命周期捕获 |

---

## 🏗️ 架构拓扑

```text
                                    [客户端请求]
                                         │
                                         ▼
                     ┌───────────────────────────────────────┐
                     │    TokenFlow 主入口网关 (Port 18317)    │
                     │             (CLIProxyAPI)             │
                     └───┬───────────────────────────────┬───┘
                         │                               │
          ┌──────────────┘                               └──────────────┐
          ▼                                                             ▼
 ┌──────────────────────────────────────┐                    ┌──────────────────────┐
 │    gemflow 粘滞分流网关 (Port 8081)   │                    │  Cursor 代理 (Port 4646)│
 │  - 上下文/会话指纹粘滞 (KV Cache)     │                    │  - Cursor CLI Agent  │
 │  - 最少连接优先 + 429 毫秒级故障自愈   │                    │  - OpenAI SSE 流式输出│
 └───┬──────────┬──────────┬────────────┘                    └──────────────────────┘
     │          │          │
     ▼          ▼          ▼
 ┌────────┐ ┌────────┐ ┌────────┐
 │Worker 1│ │Worker 2│ │Worker N│
 └───┬────┘ └───┬────┘ └───┬────┘
     │          │          │
     ▼          ▼          ▼
 [原生直连]  [代理节点 A] [代理节点 B] (Mihomo 独立多出口)
```

---

## 🚀 快速部署与使用

### 方案 1：Linux 宿主机一键全自动安装 (推荐)

一键脚本自动检测系统架构 (`amd64`/`arm64`)，安装 Node.js、Mihomo、CLIProxyAPI、Cursor CLI，并配置好 **systemd 系统服务**守护。

```bash
# 执行一键安装脚本 (需 root 权限)
curl -fsSL https://raw.githubusercontent.com/AkkunYo/TokenFlow/main/install.sh | bash
```

**便捷管理命令：**
```bash
tokenflow start     # 启动服务
tokenflow stop      # 停止服务
tokenflow restart   # 重启服务
tokenflow status    # 查看运行状态与监听端口
tokenflow logs      # 实时查看聚合日志
tokenflow config    # 快速编辑配置文件
```

---

### 方案 2：Docker / Docker Compose 部署

#### 1. 命令行直接运行
```bash
docker run -d \
  --name tokenflow \
  -p 18317:18317 \
  -p 8081:8081 \
  -p 4646:4646 \
  -e WORKER_COUNT=4 \
  -e PROVIDER_URLS="https://example.com/sub?token=xxx" \
  -e CPA_CONFIG="$(cat config.yaml)" \
  registry.cn-hangzhou.aliyuncs.com/zkyml/tokenflow:latest
```

#### 2. Docker Compose 编排
```yaml
services:
  tokenflow:
    image: registry.cn-hangzhou.aliyuncs.com/zkyml/tokenflow:latest
    container_name: tokenflow
    restart: unless-stopped
    ports:
      - "18317:18317"
      - "8081:8081"
      - "4646:4646"
    environment:
      - WORKER_COUNT=4
      - PROVIDER_URLS=https://example.com/sub?token=xxx
      - ENABLE_NVIDIA_PROXY=true
      - NVIDIA_PROVIDER_NAMES=nvidia
    volumes:
      - ./auth-dir:/home/user/app/auth-dir
      - ./config.yaml:/app/config.yaml:ro
```

```bash
docker compose up -d
```

### NVIDIA API keys 的 SOCKS5 出口

在 `config.yaml` 的 `openai-compatibility` 中配置 NVIDIA provider，并把 keys 放入 `api-key-entries`。TokenFlow 会识别名称为 `nvidia` 或 `base-url` 属于 `*.nvidia.com` 的 provider，为每个 key 轮询分配：

```text
socks5://127.0.0.1:19001
socks5://127.0.0.1:19002
...
```

默认出口数量等于 `WORKER_COUNT`。可用环境变量：

- `ENABLE_NVIDIA_PROXY=true`：启用自动分配。
- `NVIDIA_PROVIDER_NAMES=nvidia`：额外的 provider 名称，多个名称用逗号分隔。
- `NVIDIA_PROXY_PORT_COUNT`：使用前 N 个 Mihomo 出口，不能大于 `WORKER_COUNT`。

原始 `config.yaml` 不会被修改。已有非空 `proxy-url` 永远保留，只有缺失或空白值才参与轮询补全。派生配置写入 `/app/tmp/cpa-config.runtime.yaml`，权限为 `0600`。当 Mihomo 没有可用订阅时，NVIDIA 请求会失败而不会回退到直连。

---

## ⚙️ 端口与服务规划

| 服务模块 | 端口 | 协议 / 格式 | 说明 |
| :--- | :--- | :--- | :--- |
| **CLIProxyAPI (主入口)** | `18317` | HTTP / OpenAI 兼容 | 聚合所有上游模型，提供统一鉴权与管理面板 |
| **gemflow (Gemini 网关)** | `8081` | HTTP / OpenAI 兼容 | 多实例 Gemini-Web2API 粘滞网关 |
| **Cursor Proxy (Cursor 代理)** | `4646` | HTTP / OpenAI 兼容 | Cursor CLI 转换出的 OpenAI 接口 |
| **Mihomo & Zashboard (Web 面板)** | `9090` | Web UI / REST API | 访问 `http://<ip>:9090/ui` 即可直观查看代理节点与测速控制 |

---

## 📄 开源许可证

MIT License.
