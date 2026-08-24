# 🌟 TokenFlow

<div align="center">

[![CI](https://github.com/AkkunYo/TokenFlow/actions/workflows/docker-image.yml/badge.svg)](https://github.com/AkkunYo/TokenFlow/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Docker Architecture](https://img.shields.io/badge/Docker-amd64%20%7C%20arm64-blue)](https://github.com/AkkunYo/TokenFlow)
[![Python Version](https://img.shields.io/badge/Python-3.9%2B-brightgreen)](https://www.python.org/)
[![Node.js](https://img.shields.io/badge/Node.js-20%2B-green)](https://nodejs.org/)

**Unified AI Gateway with Multi-Instance Sticky Load Balancing, Multi-Egress Routing, and CLI API Bridging.**

集成了 **CLIProxyAPI (CPA)**、**Gemini-Web2API (gemflow 粘滞负载网关)** 与 **Cursor Agent API Proxy** 的多功能高可用聚合网关。

[中文文档 (README_CN.md)](README_CN.md) | [English Documentation](README.md)

</div>

---

## ✨ Features Matrix

| Dimension | Core Technology | Capability & Benefit | Implementation |
| :--- | :--- | :--- | :--- |
| 🎯 **Unified Entrypoint** | `CLIProxyAPI (Port 18317)` | Single unified OpenAI-compatible endpoint aggregating upstream providers | CLIProxyAPI daemon with automatic restart supervisor |
| ⚡ **Prompt KV Cache Locality** | `gemflow (Port 8081)` | Sticky session routing via user / MD5 fingerprint cutting TTFT by ~72% | In-memory session affinity + Least-Connection scheduling |
| 🌐 **Multi-Egress & Web Dashboard** | `Mihomo + Zashboard (:9090/ui)` | Dedicated per-worker proxy listeners (`19001..`) + modern Web UI for node monitoring | Dynamic policy groups + integrated visual dashboard at `:9090/ui` |
| 💻 **Cursor CLI Bridge** | `cursor-agent-api (Port 4646)` | Converts Cursor Pro/Business subscription into standard OpenAI API format | Headless `agent` process spawning & SSE streaming |
| 🛡️ **Dual-Mode Self-Healing** | `Systemd (Host) / Shell Loop (Docker)` | Differentiates host OS service supervisor from lightweight container loop | `install.sh` systemd unit vs `start.sh` background PID tracking |

---

## 🏗️ Architecture

```text
                                    [Client Request]
                                           │
                                           ▼
                       ┌───────────────────────────────────────┐
                       │    TokenFlow Main Gateway (:18317)    │
                       │           (CLIProxyAPI)               │
                       └───┬───────────────────────────────┬───┘
                           │                               │
            ┌──────────────┘                               └──────────────┐
            ▼                                                             ▼
 ┌──────────────────────────────────────┐                      ┌──────────────────────┐
 │    gemflow Sticky Gateway (:8081)    │                      │  Cursor Proxy (:4646)│
 │  - Context / Session Affinity        │                      │  - Cursor CLI Agent  │
 │  - Least-Conn + 429/5xx Failover     │                      │  - OpenAI Stream     │
 └───┬──────────┬──────────┬────────────┘                      └──────────────────────┘
     │          │          │
     ▼          ▼          ▼
 ┌────────┐ ┌────────┐ ┌────────┐
 │Worker 1│ │Worker 2│ │Worker N│
 └───┬────┘ └───┬────┘ └───┬────┘
     │          │          │
     ▼          ▼          ▼
 [Direct]   [Proxy A]  [Proxy B] (Mihomo Multi-Egress)
```

---

## 🚀 Quick Start

### 1. One-Click Host Installation (Linux Systemd)

```bash
# Run one-line installer as root / sudo
curl -fsSL https://raw.githubusercontent.com/AkkunYo/TokenFlow/main/install.sh | bash

# Service management
tokenflow start
tokenflow status
tokenflow logs
```

### 2. Docker & Docker Compose

```bash
# Docker Run
docker run -d \
  --name tokenflow \
  -p 18317:18317 \
  -p 8081:8081 \
  -p 4646:4646 \
  -e WORKER_COUNT=4 \
  -e PROVIDER_URLS="https://example.com/sub?token=xxx" \
  registry.cn-hangzhou.aliyuncs.com/zkyml/tokenflow:latest
```

```bash
# Docker Compose
docker compose up -d
```

| Service Module | Port | Protocol / Description |
| :--- | :--- | :--- |
| **CLIProxyAPI (Main Gateway)** | `18317` | HTTP / OpenAI compatible unified entrypoint |
| **gemflow (Gemini Gateway)** | `8081` | HTTP / OpenAI compatible sticky load balancer |
| **Cursor Proxy (Cursor Agent)** | `4646` | HTTP / OpenAI compatible proxy |
| **Mihomo & Zashboard** | `9090` | Web Dashboard (`http://<ip>:9090/ui`) & Controller API |

---

## 📄 License

MIT License.
