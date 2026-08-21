# gemflow Project Context for Claude / Agents

This file provides architectural context and operational details for Claude or other AI coding agents working on this project.

## Project Summary
- **Name**: `gemflow`
- **Location**: `/Users/zhangky/IdeaProjects/gemflow`
- **Purpose**: Intelligent sticky-session load balancing gateway and multi-egress proxy router for Gemini Web2API / LLM endpoints.
- **Key Advantages**:
  1. Preserves Gemini Prompt KV cache locality by pinning conversational turns via prompt fingerprinting.
  2. Balances new sessions across multiple worker instances and egress proxy IPs (Mihomo integration).
  3. Automatic failover & cooling penalty on HTTP 429/5xx.

## Key Files & Layout
- `lb_gateway.py`: Pure Python HTTP load balancer gateway (`port: 8081`). No heavy external web framework dependencies.
- `run_local.py`: Local orchestrator for macOS/Linux/Windows. Configures Mihomo listeners, spawns workers, and launches the gateway.
- `start.sh`: Entrypoint for Docker container.
- `Dockerfile` & `docker-compose.yml`: Container builds with pre-compiled Mihomo binary.
- `mihomo.template.yaml`: Base template dynamically populated with `proxy-providers` and `listeners`.
- `config.json.example`: Upstream Gemini-Web2API worker configuration sample.
- `requirements.txt`: Python dependencies (`httpx`, `pyyaml`).

## Upstream & Acknowledgments
- **Core Upstream Worker**: `gemini-web2api` (Handles Gemini Web session to OpenAI-compatible API mapping).
- **Proxy Kernel**: `Mihomo` / MetaCubeX (Handles multi-port listeners and egress node routing).

## Port & Topology Conventions
- **Gateway Entrypoint**: `8081`
- **Workers Base Port**: `9000` (Worker 1 on `9001`, Worker N on `9000 + N`)
- **Proxy Base Port**: `19000` (Worker 1 = `DIRECT`, Worker N = `http://127.0.0.1:19000 + N`)
- **Mihomo External Controller**: `127.0.0.1:9090`

## Routing Rules
1. `STICKY`: Triggered when `session_id` (`usr_<user>`, `ctx_<prompt_md5>`, or `auth_<token_md5>`) matches an active worker within `SESSION_TTL = 1800`s and worker is not in cooling down penalty.
2. `LEAST_CONN`: Triggered for new sessions. Picks candidate with minimum `active_connections + penalty`, broken evenly with strict Round-Robin cursor (`RR_INDEX`).
3. `FAILOVER`: On HTTP 429/5xx or disconnect, failed worker gets 20s cooling penalty (`FAIL_PENALTY_SEC = 20`), and request transparently fails over to next candidate.
