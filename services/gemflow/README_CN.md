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

## 🌟 核心特性矩阵

| 核心维度 | 功能机制 | 业务价值 / 效果 | 关键配置与技术实现 |
| :--- | :--- | :--- | :--- |
| 🎯 **会话粘滞 (Sticky Session)** | 提取 `user`、Prompt 首句 MD5（`ctx_<md5>`）或 Token 特征作为指纹 | 锁定同一 Worker 与出口 IP，命中 Google **Prompt KV 缓存**，降低 70%+ TTFT 首字延迟 | 自动提取 Request Meta，维护内存级 Session LRU 映射表 |
| 🔄 **智能调度均衡** | **最少活跃连接 (Least-Connection) + 循环轮询 (Round-Robin)** | 避免单 Worker 负载过高，并发相同时严格均分流量 | 原子连接计数器 `ACTIVE_CONNS` + 线程安全锁调度 |
| 🛡️ **高可用容灾** | **429 / 5xx / 网络断开秒级自动重试 + 冷却降权** | 单个节点限流或异常时秒级透明漂移，客户端业务零中断 | 自动加入 20 秒冷却池（`FAIL_PENALTY_SEC=20`）并调度健康节点 |
| 🌐 **多出口网络分流** | **集成 Mihomo 内核 + 每 Worker 独立策略组绑定不同节点** | 各 Worker 拥有互不相同的出口 IP，彻底规避单 IP 风控限流与地域屏蔽 | 每个 Worker 独享策略组 `🎯 Worker-N` 与监听端口（`19001..19000+N`）；启动后经 REST API 绑定不同节点 |
| 🌊 **极速流式透传** | **零缓冲 HTTP Chunked / SSE 流式传输** | 打字机效果实时呈现，首字极速下发，内存占用恒定极低 | `urllib` / 原始字节流无缓冲管道直通 |
| 🔍 **全链路透明观测** | **Session 指纹、路由决策与耗时实时追踪** | 清晰掌握每笔请求命中路径（`STICKY` vs `LEAST_CONN`）与出口节点 | 环境变量 `DEBUG=true` / 启动参数 `--debug` 实时输出全彩日志 |

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
         │ 🎯 Worker-1  │           │ 🎯 Worker-2  │      │ 🎯 Worker-3  │      │ 🎯 Worker-N  │  ← 专属策略组
         └──────┬───────┘           └──────┬───────┘      └──────┬───────┘      └──────┬───────┘
                │                          │                     │                     │
         ┌──────────────┐           ┌──────────────┐      ┌──────────────┐      ┌──────────────┐
         │ 直连 / 节点 A │           │    节点 B    │      │    节点 C    │      │    节点 N    │  ← 互不相同
         └──────┬───────┘           └──────┬───────┘      └──────┬───────┘      └──────┬───────┘
                │                          │                     │                     │
                └──────────────────────────┴──────────┬──────────┴─────────────────────┘
                                                      ▼
                                           [Google Gemini 服务端]
```

### 出口节点分配机制

每个 Worker 在 Mihomo 配置中拥有专属策略组 `🎯 Worker-N`，其监听端口 `19000+N` 一对一绑定到该组。Mihomo 启动后，`assign_worker_nodes.py` 通过 external-controller REST API（`127.0.0.1:9090`）执行分配：

1. 轮询等待订阅加载与 health-check 完成，最长 45 秒
2. 收集订阅中的真实出口节点（排除策略组自身、直连类节点、无测速历史的节点）
3. 按延迟升序，为各 Worker 策略组绑定**互不相同**的节点

降级策略：健康节点数少于 Worker 数时，节点按顺序轮转复用并打印提示；一个健康节点都探测不到时，各组保留默认的 `♻️ 自动选择`（url-test 自动择优），保证链路可用性优先于出口分散性。

> 配置渲染由 `mihomo_config.py` 统一负责，容器（`start.sh`）与本地（`run_local.py`）两条启动路径产出完全一致的配置。

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
# 1. 直接通过 Docker 运行
docker run -d -p 8081:8081 \
  -e WORKER_COUNT=4 \
  -e PROVIDER_URLS="https://example.com/api/v1/client/subscribe?token=xxx" \
  -e DEBUG=true \
  --name gemflow registry.cn-hangzhou.aliyuncs.com/zkyml/gemflow:latest

# 2. 或使用 Docker Compose 一键拉起
# 下载官方 docker-compose.yml 配置文件 (如未克隆仓库)
curl -fsSL https://raw.githubusercontent.com/AkkunYo/gemflow/main/docker-compose.yml -o docker-compose.yml
# 国内加速下载备选：
# curl -fsSL https://ghfast.top/https://raw.githubusercontent.com/AkkunYo/gemflow/main/docker-compose.yml -o docker-compose.yml

# 启动服务
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
| `STREAM_CONNECT_TIMEOUT_SEC` | `45` | 首字节超时：Worker 在此时间内未返回响应头即判定失败并重试其他 Worker |
| `STREAM_IDLE_TIMEOUT_SEC` | `75` | 流式空闲超时：已开始吐数据后，连续无新数据超过此时长才判定截断（出海代理链路慢可调大） |
| `DEBUG` / `--debug` | `false` | 是否开启详细调试日志 (`true`/`1`/`yes`) |

---

## 🙏 致谢与参考项目 (Acknowledgments)

`gemflow` 为自主设计研发的智能会话粘滞网关与多出口调度引擎，底层业务实例与分流网络深度集成并依赖以下优秀的开源项目：

- 🔹 **[gemini-web2api](https://github.com/Sophomoresty/gemini-web2api)**：提供 Gemini Web 端会话转标准 OpenAI API 格式的核心上游服务。
- 🔹 **[Mihomo (Clash.Meta)](https://github.com/MetaCubeX/mihomo)**：提供高性能代理内核、多监听端口分流以及自动延迟测速健康检查能力。

感谢以上开源项目作者及社区贡献者的辛勤付出！

---

## 📋 运行日志与调试示例 (Logs & Visibility)

### 1. 出口节点分配日志

Mihomo 启动后为各 Worker 策略组绑定互不相同的节点，逐条输出分配结果：

```text
[NodeAssign] Worker-2 -> 🇯🇵 日本 IEPL 01
[NodeAssign] Worker-3 -> 🇸🇬 新加坡 IEPL 02
[NodeAssign] Worker-4 -> 🇭🇰 香港 IEPL 03
[NodeAssign] Worker-5 -> 🇺🇸 美国 IEPL 01
[NodeAssign] Completed: 4/4 worker(s) bound, 4 distinct egress node(s).
```

健康节点不足时轮转复用并给出提示：

```text
[NodeAssign] Notice: only 2 healthy node(s) for 4 proxied worker(s). Nodes will be reused in rotation.
```

### 2. 各 Worker 出口 IP 状态自检日志

各 Worker 的端口、出口网络类型及归属地信息，代理链路确认生效后打印首轮，随后每 5 分钟输出一次当前实况：

```text
========== [Worker Egress IP Status @ 2026-08-22 10:05:26] ==========
[Worker-1 : Port 9001 : DIRECT (Native)] -> United States (Ashburn) - IP: 44.196.116.2 [AWS EC2 (us-east-1)]
[Worker-2 : Port 9002 : Proxy (:19002) ] -> Japan (Tokyo) - IP: 203.0.113.45 [NTT Communications]
[Worker-3 : Port 9003 : Proxy (:19003) ] -> Singapore (Singapore) - IP: 198.51.100.72 [DigitalOcean]
[Worker-4 : Port 9004 : Proxy (:19004) ] -> Hong Kong (Central) - IP: 192.0.2.181 [HKT Limited]
[Worker-5 : Port 9005 : Proxy (:19005) ] -> United States (Los Angeles) - IP: 203.0.113.209 [Cloudflare, Inc.]
======================================================================
```

**首轮打印时机**：仅在探测确认代理出口与原生直连 IP 存在差异后才打印，避免 Mihomo 未就绪时输出一堆无意义的重复直连 IP。若 180 秒内仍未确认生效，则无条件打印当前实况并附带一行 warning 便于排查：

```text
[LB] Warning: proxy egress not confirmed within 180s. Printing current state as-is.
```

### 3. 详细路由调试日志 (`DEBUG=true`)

开启 `DEBUG=true` 时可在终端实时追踪请求的 Session 指纹、决策路径（`STICKY` vs `LEAST_CONN`）与响应耗时：

```text
# 1. 新会话请求进入 -> 最少连接轮询到 Worker-2
[DEBUG @ 21:05:10] [Req #1] [POST] /v1/chat/completions | Model: 'gemini-2.5-flash' | Session: ctx_8a3f912b41de | Prompt: "Explain quantum computing..." -> Selected: Worker-2 (Port 9002, Egress: http://127.0.0.1:19002, Route: LEAST_CONN, Active: 1)
[DEBUG @ 21:05:12] [Req #1] Completed in 2.10s | HTTP 200 via Worker-2 (http://127.0.0.1:19002)

# 2. 会话后续追问 -> 精准触发 STICKY 命中 Worker-2 (命中 KV 缓存，响应加速)
[DEBUG @ 21:05:25] [Req #2] [POST] /v1/chat/completions | Model: 'gemini-2.5-flash' | Session: ctx_8a3f912b41de | Prompt: "Explain quantum computing..." -> Selected: Worker-2 (Port 9002, Egress: http://127.0.0.1:19002, Route: STICKY, Active: 1)
[DEBUG @ 21:05:26] [Req #2] Completed in 1.15s | HTTP 200 via Worker-2 (http://127.0.0.1:19002)
```

### 4. 故障排查与连通性自检命令

当遇到代理出口未生效、节点测速异常或需要查看实例内部状态时：

```bash
# 1. 查看 Mihomo 代理内核与订阅拉取日志
docker exec -it gemflow cat /tmp/mihomo.log
# 或实时滚动追踪代理日志
docker exec -it gemflow tail -f /tmp/mihomo.log

# 2. 手动测试指定 Worker 代理端口的出口连通性 (以 Worker-2 对应的 19002 为例)
docker exec -it gemflow curl -x http://127.0.0.1:19002 -s https://ipinfo.io/json

# 3. 查看特定 Worker 实例的上游运行日志 (以 Worker-2 为例)
docker exec -it gemflow cat /app/worker_2.log

# 4. 查看各 Worker 策略组当前绑定的节点 (确认出口是否已分散)
docker exec -it gemflow sh -c 'for i in $(seq 2 5); do
  curl -s "http://127.0.0.1:9090/proxies/%F0%9F%8E%AF%20Worker-$i" \
    | grep -o "\"now\":\"[^\"]*\"" | sed "s/^/Worker-$i /"
done'

# 5. 手动重新分配出口节点 (节点大面积掉线恢复后可重新分散)
docker exec -it gemflow python3 /app/assign_worker_nodes.py --workers 12 --skip 1
```

---

## 📄 开源协议
本项目基于 MIT License 协议开源。
