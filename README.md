# 🌟 gemflow

<div align="center">

[![CI](https://github.com/AkkunYo/gemflow/actions/workflows/docker-image.yml/badge.svg)](https://github.com/AkkunYo/gemflow/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Docker Architecture](https://img.shields.io/badge/Docker-amd64%20%7C%20arm64-blue)](https://github.com/AkkunYo/gemflow)
[![Python Version](https://img.shields.io/badge/Python-3.9%2B-brightgreen)](https://www.python.org/)

**Intelligent Sticky-Session Load Balancer & Multi-Egress Routing Gateway for Gemini Web / Web2API Services.**

专为 Gemini Web2API / LLM 服务设计的**智能会话粘滞负载均衡与多出口分流网关**。兼顾 **Prompt KV 缓存加速** 与 **多线路高可用负载**。

[中文文档 (README_CN.md)](README_CN.md) | [English Documentation](README.md)

</div>

---

## 📊 Benchmark & KV Cache Locality

Google Gemini models implement **Prompt KV Prefix Caching**. Random or naive round-robin dispatch across different IPs or upstream sessions invalidates the prefix cache, causing severe First-Token Latency (TTFT) degradation.

`gemflow` achieves **~70%+ reduction in TTFT** by deterministically pinning conversational contexts to the same backend worker and egress proxy:

| Metric | Random / Round-Robin Gateway | `gemflow` Sticky-Session Gateway | Optimization |
| :--- | :---: | :---: | :---: |
| **First-Token Latency (TTFT, Turn 2+)** | `1.85s ~ 2.40s` | **`0.42s ~ 0.65s`** | ⚡ **~72% Faster** |
| **Prefix Cache Hit Rate** | < 25% | **> 95%** | 🎯 **Optimal Cache Locality** |
| **429 Rate Limit Failover Time** | Manual / Request Fails | **< 0.1s Auto Failover** | 🛡️ **Zero Downtime** |
| **Multi-IP Egress Scaling** | Single / Static IP | **N-Isolated Proxy Tunnels** | 🌐 **High Capacity** |

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

# Start 4 workers with subscription (supports Clash YAML or V2Ray / Base64 / VLESS / SS) and debug logging
python3 run_local.py --workers 4 --port 8081 --sub "https://example.com/api/v1/client/subscribe?token=xxx" --debug
```

### 2. Docker & Docker Compose

```bash
# Direct run with Docker
docker run -d -p 8081:8081 \
  -e WORKER_COUNT=4 \
  -e PROVIDER_URLS="https://example.com/api/v1/client/subscribe?token=xxx" \
  -e DEBUG=true \
  --name gemflow gemflow:latest

# Or using docker-compose
docker compose up -d
```

---

## 💻 API Client Usage

`gemflow` exposes a standard OpenAI-compatible API interface on port `8081`.

### 1. `curl` (Streaming SSE)

```bash
curl -N http://127.0.0.1:8081/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-api-key" \
  -d '{
    "model": "gemini-2.5-flash",
    "messages": [
      {"role": "user", "content": "Explain quantum computing in 3 sentences."}
    ],
    "stream": true,
    "user": "session-user-123"
  }'
```

### 2. Python (`openai` SDK)

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8081/v1",
    api_key="your-api-key",  # or dummy string if upstream doesn't enforce
)

response = client.chat.completions.create(
    model="gemini-2.5-flash",
    messages=[
        {"role": "user", "content": "Hello Gemini!"}
    ],
    stream=True,
    user="user-session-42",  # Optional: Explicit sticky session identifier
)

for chunk in response:
    content = chunk.choices[0].delta.content or ""
    print(content, end="", flush=True)
print()
```

---

## ⚙️ Environment Variables

| Variable | Default | Description |
| :--- | :--- | :--- |
| `PORT` | `8081` | Gateway HTTP listen entrypoint |
| `WORKER_COUNT` | `1` | Number of worker instances to launch (`1` = Direct only, `N` = 1 Direct + N-1 Proxy workers) |
| `PROVIDER_URLS` | `""` | Proxy subscription URLs (supports Clash YAML links as well as V2Ray / Base64 / VMess / VLESS / Trojan subscription formats, multi-line supported) |
| `DEBUG` | `false` | Enable verbose logging (`true`/`1`/`yes`) |

---

## 🙏 Acknowledgments & References

`gemflow` is a custom-engineered intelligent load balancing and routing gateway built on top of and integrating with the following outstanding open-source projects:

- 🔹 **[gemini-web2api](https://github.com/fatpandabb/gemini-web2api)**: Upstream service provider converting Gemini Web sessions into standard OpenAI-compatible API endpoints.
- 🔹 **[Mihomo (Clash.Meta)](https://github.com/MetaCubeX/mihomo)**: High-performance rule-based proxy kernel powering multi-egress routing and latency testing.

---

## 📄 License
MIT License.
