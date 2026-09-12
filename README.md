# 🌟 TokenFlow

<div align="center">

[![CI](https://github.com/AkkunYo/TokenFlow/actions/workflows/docker-image.yml/badge.svg)](https://github.com/AkkunYo/TokenFlow/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Docker Architecture](https://img.shields.io/badge/Docker-amd64%20%7C%20arm64-blue)](https://github.com/AkkunYo/TokenFlow)
[![Python Version](https://img.shields.io/badge/Python-3.9%2B-brightgreen)](https://www.python.org/)

**Unified AI Gateway with Multi-Instance Sticky Load Balancing and Multi-Egress Routing.**

集成 **CLIProxyAPI (CPA)**、**Gemini-Web2API (gemflow 粘滞负载网关)** 与 **Mihomo 多出口路由** 的高可用聚合网关。

[中文文档 (README_CN.md)](README_CN.md) | [English Documentation](README.md)

</div>

---

## ✨ Features Matrix

| Dimension | Core Technology | Capability & Benefit | Implementation |
| :--- | :--- | :--- | :--- |
| 🎯 **Unified Entrypoint** | `CLIProxyAPI (Port 18317)` | Single unified OpenAI-compatible endpoint aggregating upstream providers | CLIProxyAPI daemon with automatic restart supervisor |
| ⚡ **Prompt KV Cache Locality** | `gemflow (Port 8081)` | Sticky session routing via user / MD5 fingerprint cutting TTFT by ~72% | In-memory session affinity + Least-Connection scheduling |
| 🌐 **Multi-Egress & Web Dashboard** | `Mihomo + Zashboard (:9090/ui)` | Dedicated per-worker proxy listeners (`19001..`) + modern Web UI for node monitoring | Dynamic policy groups + integrated visual dashboard at `:9090/ui` |
| 🟩 **Per-Key NVIDIA SOCKS Routing** | `CLIProxyAPI + Mihomo` | Round-robins NVIDIA keys missing a proxy across dedicated SOCKS5 egress listeners | Writes a mode-`0600` runtime config, preserves the read-only source, and never replaces an existing `proxy-url` |
| 🛡️ **Dual-Mode Self-Healing** | `Systemd (Host) / Shell Loop (Docker)` | Differentiates host OS service supervisor from lightweight container loop | `install.sh` systemd unit vs `start.sh` background PID tracking |

---

## 📦 Monorepo Structure

```text
apps/tokenflow/       CPA, NVIDIA and product startup
services/gemflow/     Gemini workers and sticky load balancing
packages/egress/      Mihomo configuration, providers and node assignment
scripts/              Repository-wide verification
```

The repository publishes both the final `tokenflow` image and the standalone
`gemflow` image. Source ownership is separated in Git, while the TokenFlow
container keeps the existing flat `/app` runtime layout for compatibility.

Run every component test with:

```bash
bash scripts/test_all.sh
```

See [docs/architecture.md](docs/architecture.md) for dependency boundaries.

---

## 🏗️ Architecture

```text
                         [Client Request]
                                │
                                ▼
              [TokenFlow / CLIProxyAPI :18317]
                    │                     │
                    ▼                     ▼
       [gemflow Sticky Gateway]    [NVIDIA Provider Keys]
                :8081                per-key proxy-url
                    │                     │
                    ▼                     │
             [Workers 1..N]               │
                    │                     │
                    └──────────┬──────────┘
                               ▼
              [Mihomo SOCKS5 :19001..19000+N]
                               │
                               ▼
                       [Upstream Networks]
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
  -p 9090:9090 \
  -e WORKER_COUNT=4 \
  -e PROVIDER_URLS="https://example.com/sub?token=xxx" \
  ghcr.io/akkunyo/tokenflow:latest
```

```bash
# Docker Compose
docker compose up -d
```

### SOCKS5 egress for NVIDIA API keys

Define an NVIDIA provider under `openai-compatibility` and place its credentials in `api-key-entries`. TokenFlow recognizes providers named `nvidia` or using a `*.nvidia.com` base URL, then assigns each key to:

```text
socks5://127.0.0.1:19001
socks5://127.0.0.1:19002
...
```

The pool size defaults to `WORKER_COUNT`. Configuration options:

- `ENABLE_NVIDIA_PROXY=true` enables automatic assignment.
- `NVIDIA_PROVIDER_NAMES=nvidia` accepts additional comma-separated provider names.
- `NVIDIA_PROXY_PORT_COUNT` uses the first N Mihomo listeners and cannot exceed `WORKER_COUNT`.

The source `config.yaml` remains unchanged. Existing non-empty `proxy-url` values are always preserved; only missing or blank values participate in round-robin assignment. The derived config is written to `/app/tmp/cpa-config.runtime.yaml` with mode `0600`. If Mihomo has no usable provider source, NVIDIA requests fail closed instead of falling back to a direct connection.

| Service Module | Port | Protocol / Description |
| :--- | :--- | :--- |
| **CLIProxyAPI (Main Gateway)** | `18317` | HTTP / OpenAI compatible unified entrypoint |
| **gemflow (Gemini Gateway)** | `8081` | HTTP / OpenAI compatible sticky load balancer |
| **Mihomo & Zashboard** | `9090` | Web Dashboard (`http://<ip>:9090/ui`) & Controller API |

---

## 📄 License

MIT License.
