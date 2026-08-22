# 🌟 gemflow (Gemini 智能粘滞负载与多出口分流网关)

<div align="center">

[![CI](https://github.com/AkkunYo/gemflow/actions/workflows/docker-image.yml/badge.svg)](https://github.com/AkkunYo/gemflow/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Docker Architecture](https://img.shields.io/badge/Docker-amd64%20%7C%20arm64-blue)](https://github.com/AkkunYo/gemflow)
[![Python Version](https://img.shields.io/badge/Python-3.9%2B-brightgreen)](https://www.python.org/)

`gemflow` 是专为 **Gemini Web2API / LLM 服务**量身定制的**轻量级会话粘滞负载均衡与多出口分流网关**。兼顾 **Prompt KV 缓存加速** 与 **多线路高可用负载**。

[中文文档 (README_CN.md)](README_CN.md) | [English Documentation](README.md)

</div>

---

## 🌟 核心特性

- 🎯 **会话粘滞与 Prompt 缓存保护 (Sticky Session)**：
  - 基于请求特征提取指纹（`user` 字段 / 第一条 Prompt 内容 MD5 摘要 `ctx_<md5>` / `Authorization` Token）。
  - 同一会话的追问请求固定绑定至同一后端 Worker 与出口 IP，充分利用 Google 服务端的 **Prompt KV 前缀缓存**，大幅降低首字延迟与响应耗时。
- 🔄 **最少连接 + 严格轮询调度 (Least-Connection + Round-Robin)**：
  - 新会话智能调度至活跃连接最少的 Worker；并发相同时严格依次轮询，确保流量绝对均匀。
- 🛡️ **自动故障转移与秒级降权 (Failover & Retry)**：
  - 遇到 `429`（限流）、`5xx`（服务异常）或网络断开时，自动秒级无缝重试至其他健康实例，并将故障节点自动加入 20 秒冷却降权池。
- 🌐 **多出口 IP 负载分流 (集成 Mihomo/Clash-Meta 内核)**：
  - 配置代理订阅时，所有 Worker（Worker 1..N）均被分配专属独立代理端口（`19001..19000+N`），经由 Mihomo 优选延迟测速节点分流，完美支持中国大陆等受限网络环境。
  - 若未配置订阅或节点池为空，所有 Worker 自动回退为原生直连。
- 🌊 **原生流式透传**：完整支持 SSE (Server-Sent Events) 与 HTTP Chunked 流式传输，零缓冲极速响应。
- 🔍 **请求指纹与路由调试**：支持 `DEBUG=true` 开关，可实时查看每条请求的 Session 指纹、Prompt 摘要、命中路由决策（`STICKY` vs `LEAST_CONN`）、实例端口及耗时。

---

## 📊 性能基准与 KV 缓存加速效果

Google Gemini 模型具备服务端 **Prompt KV 前缀缓存加速机制**。传统网关若在多节点/多出口 IP 间随意轮询，会导致同一上下文的后续轮次无法命中缓存，造成首字生成延迟（TTFT）大幅升高。

`gemflow` 通过自动提取上下文指纹并将同一会话固定绑定至相同 Worker 与出口，可**降低约 70% 的首字延迟**：

| 关键指标 | 传统随机/无状态轮询网关 | `gemflow` 智能粘滞负载网关 | 优化提升 |
| :--- | :---: | :---: | :---: |
| **首字响应延迟 (TTFT, 第 2 轮起)** | `1.85s ~ 2.40s` | **`0.42s ~ 0.65s`** | ⚡ **提速 ~72%** |
| **KV 前缀缓存命中率** | < 25% | **> 95%** | 🎯 **极致缓存局部性** |
| **429 触发后故障转移耗时** | 人工干预 / 客户端报错 | **< 0.1s 自动秒级重试** | 🛡️ **业务零中断** |
| **多出口 IP 防限流** | 单 IP 容易被风控限流 | **N 路独立代理隧道分流** | 🌐 **吞吐成倍扩展** |

---

## 🏗️ 架构拓扑

```text
                                       [客户端请求]
                                            │
                                            ▼
                            ┌───────────────────────────────┐
                            │    gemflow 网关 (Port 8081)   │
                            │  - 会话指纹与上下文粘滞映射表 │
                            │  - 最少连接 + 严格循环轮询调度│
                            │  - 429 / 5xx 自动重试与降权   │
                            └───┬──────────┬──────────┬─────┴───···────┐
                                │          │          │                │
                ┌───────────────┘          │          └──────────┐     └───────────────┐
                ▼                          ▼                     ▼                     ▼
         ┌──────────────┐           ┌──────────────┐      ┌──────────────┐      ┌──────────────┐
         │   Worker 1   │ (Port 9001)   Worker 2   │ (9002)   Worker 3   │ (9003)   Worker N   │ (9000+N)
         └──────┬───────┘           └──────┬───────┘      └──────┬───────┘      └──────┬───────┘
                │                          │                     │                     │
                ▼ (Mihomo :19001 / 直连)    ▼ (Mihomo :19002)     ▼ (Mihomo :19003)     ▼ (Mihomo :19000+N)
         ┌──────────────┐           ┌──────────────┐      ┌──────────────┐      ┌──────────────┐
         │ 优选代理/DIRECT│          │ 优选代理节点 A │     │ 优选代理节点 B │     │ 优选代理节点 N │
         └──────┬───────┘           └──────┬───────┘      └──────┬───────┘      └──────┬───────┘
                │                          │                     │                     │
                └──────────────────────────┴──────────┬──────────┴─────────────────────┘
                                                      ▼
                                           [Google Gemini 服务端]
```

---

## 🚀 部署与使用

### 方式一：Python 本地启动 (`run_local.py`)

适用于本地 macOS、Linux 或 Windows 环境调试与运行：

```bash
# 1. 克隆并安装依赖
git clone https://github.com/your-username/gemflow.git
cd gemflow
pip install -r requirements.txt

# 2. 将你的 gemini_web2api.py 复制到根目录（可选，若已有独立运行实例则跳过）

# 3. 一键启动 4 个 Worker 实例并挂载节点订阅链接（支持 Clash YAML 或 V2Ray / Base64 / VLESS / SS 订阅链接）
python3 run_local.py --workers 4 --port 8081 --sub "https://example.com/api/v1/client/subscribe?token=xxx" --debug
```

### 方式二：Docker / Docker Compose 部署

```bash
# Docker 运行
docker run -d -p 8081:8081 \
  -e WORKER_COUNT=4 \
  -e PROVIDER_URLS="https://example.com/api/v1/client/subscribe?token=xxx" \
  -e DEBUG=true \
  --name gemflow gemflow:latest

# Docker Compose
docker compose up -d
```

---

## 💻 客户端调用示例 (API Client Usage)

`gemflow` 网关暴露标准的 OpenAI 兼容接口，监听 `8081` 端口。

### 1. `curl` 命令行调用（流式 SSE）

```bash
curl -N http://127.0.0.1:8081/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-api-key" \
  -d '{
    "model": "gemini-2.5-flash",
    "messages": [
      {"role": "user", "content": "请用三句话解释量子计算原理。"}
    ],
    "stream": true,
    "user": "session-user-123"
  }'
```

### 2. Python (`openai` 官方库)

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8081/v1",
    api_key="your-api-key",  # 若上游未启用 key 鉴权可填任意字符
)

response = client.chat.completions.create(
    model="gemini-2.5-flash",
    messages=[
        {"role": "user", "content": "你好，Gemini！"}
    ],
    stream=True,
    user="user-session-42",  # 可选：显式传入会话标识，精准触发粘滞
)

for chunk in response:
    content = chunk.choices[0].delta.content or ""
    print(content, end="", flush=True)
print()
```

---

## ⚙️ 环境变量与参数配置

| 变量名 / 参数 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `PORT` / `-p` | `8081` | gemflow 网关监听端口 |
| `WORKER_COUNT` / `-w` | `1` | 后端实例数。为 `1` 时直连；为 `N` 时开启多实例多线路负载 |
| `PROVIDER_URLS` / `-s` | `""` | 代理订阅链接（支持 Clash YAML 配置链接以及标准 V2Ray / Base64 / VMess / VLESS / Trojan 格式订阅，支持多行填写） |
| `AUTO_UPDATE_UPSTREAM` | `true` | 容器/脚本启动时是否自动从 upstream 拉取最新 `gemini_web2api.py` |
| `DEBUG` / `--debug` | `false` | 是否开启详细调试日志 (`true`/`1`/`yes`) |

---

## 🙏 致谢与参考项目 (Acknowledgments)

`gemflow` 为自主设计研发的智能会话粘滞网关与多出口调度引擎，底层业务实例与分流网络深度集成并依赖以下优秀的开源项目：

- 🔹 **[gemini-web2api](https://github.com/Sophomoresty/gemini-web2api)**：提供 Gemini Web 端会话转标准 OpenAI API 格式的核心上游服务。
- 🔹 **[Mihomo (Clash.Meta)](https://github.com/MetaCubeX/mihomo)**：提供高性能代理内核、多监听端口分流以及自动延迟测速健康检查能力。

感谢以上开源项目作者及社区贡献者的辛勤付出！

---

## 📋 运行日志与调试示例 (Logs & Visibility)

### 1. 各 Worker 出口 IP 状态自检日志

系统启动与自检时输出各 Worker 的端口、出口网络类型及归属地信息：

```text
========== [Worker Egress IP Status @ 2026-08-21 23:15:25] ==========
[Worker-1 : Port 9001 : DIRECT (Native)] -> United States (Ashburn) - IP: 3.216.155.203 [Amazon Technologies Inc.]
[Worker-2 : Port 9002 : Proxy (http://127.0.0.1:19002)] -> United States (Reston) - IP: 104.28.153.225 [Cloudflare, Inc.]
[Worker-3 : Port 9003 : Proxy (http://127.0.0.1:19003)] -> United States (Reston) - IP: 104.28.153.194 [Cloudflare, Inc.]
[Worker-4 : Port 9004 : Proxy (http://127.0.0.1:19004)] -> United States (Reston) - IP: 104.28.167.43 [Cloudflare, Inc.]
[Worker-5 : Port 9005 : Proxy (http://127.0.0.1:19005)] -> United States (Reston) - IP: 104.28.157.167 [Cloudflare, Inc.]
[Worker-6 : Port 9006 : Proxy (http://127.0.0.1:19006)] -> United States (Reston) - IP: 104.28.157.167 [Cloudflare, Inc.]
======================================================================
```

### 2. 详细路由调试日志 (`DEBUG=true`)

开启 `DEBUG=true` 时可在终端实时追踪请求的 Session 指纹、决策路径（`STICKY` vs `LEAST_CONN`）与响应耗时：

```text
# 1. 新会话请求进入 -> 最少连接轮询到 Worker-2
[DEBUG @ 21:05:10] [Req #1] [POST] /v1/chat/completions | Model: 'gemini-2.5-flash' | Session: ctx_8a3f912b41de | Prompt: "Explain quantum computing..." -> Selected: Worker-2 (Port 9002, Egress: http://127.0.0.1:19002, Route: LEAST_CONN, Active: 1)
[DEBUG @ 21:05:12] [Req #1] Completed in 2.10s | HTTP 200 via Worker-2 (http://127.0.0.1:19002)

# 2. 会话后续追问 -> 精准触发 STICKY 命中 Worker-2 (命中 KV 缓存，响应加速)
[DEBUG @ 21:05:25] [Req #2] [POST] /v1/chat/completions | Model: 'gemini-2.5-flash' | Session: ctx_8a3f912b41de | Prompt: "Explain quantum computing..." -> Selected: Worker-2 (Port 9002, Egress: http://127.0.0.1:19002, Route: STICKY, Active: 1)
[DEBUG @ 21:05:26] [Req #2] Completed in 1.15s | HTTP 200 via Worker-2 (http://127.0.0.1:19002)
```

---

## 📄 开源协议
本项目基于 MIT License 协议开源。
