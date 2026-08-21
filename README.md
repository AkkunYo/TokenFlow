# 🌟 gemflow

> **Intelligent Sticky-Session Load Balancer & Multi-Egress Routing Gateway for Gemini Web / Web2API Services.**
>
> 专为 Gemini Web2API / LLM 服务设计的**智能会话粘滞负载均衡与多出口分流网关**。兼顾 **Prompt KV 缓存加速** 与 **多线路高可用负载**。

[中文文档 (README_CN.md)](README_CN.md) | [English Documentation](README.md)

---

## ✨ Key Features

1. 🎯 **Prompt KV Cache Locality (Sticky Session)**:
   - Identifies conversational sessions via `user` field, first prompt MD5 snippet (`ctx_<md5>`), or `Authorization` token.
   - Pins subsequent turns of the same conversation to the exact same backend worker and egress IP, maximizing Google's prompt prefix cache hit rate.
2. 🔄 **Least-Connection + Strict Round-Robin Dispatch**:
   - Routes new conversations to the worker with the lowest active connections.
   - Strict Round-Robin tie-breaking ensures perfectly even traffic distribution during sequential requests.
3. 🛡️ **Automatic Failover & Retry**:
   - Transparently catches `429` (Rate Limited), `5xx` server errors, or dropouts, automatically re-routing to an alternate healthy worker within seconds while applying a 20-second cooling penalty to failed nodes.
4. 🌐 **Dynamic Multi-Egress Proxies (Mihomo Integration)**:
   - Worker 1 defaults to direct native network.
   - Workers 2..N are assigned isolated proxy egress ports (`19002..19000+N`) backed by auto-latency-tested proxy groups.
   - Graceful fallback: defaults to native direct mode if no proxy subscription is provided.
5. 🌊 **Native Zero-Buffer Streaming**:
   - Full passthrough for SSE (Server-Sent Events) and chunked transfer encoding.
6. 🔍 **Real-Time Debug Visibility**:
   - Toggle `DEBUG=true` to monitor session fingerprints, routing decisions (`STICKY` vs `LEAST_CONN`), egress nodes, and response latencies.

---

## 🏗️ Architecture

```text
                             [Client Request]
                                    │
                                    ▼
                 ┌──────────────────────────────────────┐
                 │     gemflow Gateway (:8081)          │
                 │  - Fingerprint / Context Sticky Map  │
                 │  - Least-Conn + Round-Robin Dispatch │
                 │  - Auto Failover on 429/5xx          │
                 └──────┬──────────────┬──────────────┬─┘
                        │              │              │
        ┌───────────────┘              │              └───────────────┐
        ▼                              ▼                              ▼
 ┌──────────────┐               ┌──────────────┐               ┌──────────────┐
 │ Worker 1     │ (:9001)       │ Worker 2     │ (:9002)       │ Worker N     │ (:9000+N)
 └──────┬───────┘               └──────┬───────┘               └──────┬───────┘
        │                              │                              │
        ▼ (Native Direct)              ▼ (Mihomo :19002)              ▼ (Mihomo :19000+N)
 ┌──────────────┐               ┌──────────────┐               ┌──────────────┐
 │ DIRECT Egress│               │ Proxy Node A │               │ Proxy Node B │
 └──────┬───────┘               └──────┬───────┘               └──────┬───────┘
        │                              │                              │
        └───────────────────────┬──────┴──────────────────────────────┘
                                ▼
                     [Google Gemini Upstream]
```

---

## 🚀 Quick Start

### 1. Local Python Run

```bash
# Clone and install dependencies
git clone https://github.com/your-username/gemflow.git
cd gemflow
pip install -r requirements.txt

# Start 4 workers with subscription and debug logging
python3 run_local.py --workers 4 --port 8081 --sub "https://your-subscription-url.yaml" --debug
```

### 2. Docker & Docker Compose

```bash
# Direct run with Docker
docker run -d -p 8081:8081 \
  -e WORKER_COUNT=4 \
  -e PROVIDER_URLS="https://your-subscription-url.yaml" \
  -e DEBUG=true \
  --name gemflow gemflow:latest

# Or using docker-compose
docker compose up -d
```

---

## ⚙️ Environment Variables

| Variable | Default | Description |
| :--- | :--- | :--- |
| `PORT` | `8081` | Gateway HTTP listen entrypoint |
| `WORKER_COUNT` | `1` | Number of worker instances to launch (`1` = Direct only, `N` = 1 Direct + N-1 Proxy workers) |
| `PROVIDER_URLS` | `""` | Proxy subscription URLs (supports multi-line) |
| `DEBUG` | `false` | Enable verbose logging (`true`/`1`/`yes`) |

---

## 📄 License
MIT License.
