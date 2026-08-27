#!/usr/bin/env bash
# ==============================================================================
#                 TokenFlow Unified Startup & Supervisor
# ==============================================================================
set -eo pipefail

APP_DIR="${APP_DIR:-/app}"
cd "$APP_DIR"

CPA_CONFIG_FILE="${CPA_CONFIG_FILE:-$APP_DIR/config.yaml}"
CPA_RUNTIME_CONFIG_FILE="${CPA_RUNTIME_CONFIG_FILE:-$APP_DIR/tmp/cpa-config.runtime.yaml}"
CPA_EFFECTIVE_CONFIG_FILE="$CPA_CONFIG_FILE"
ENABLE_GEMFLOW="${ENABLE_GEMFLOW:-true}"
ENABLE_CURSOR="${ENABLE_CURSOR:-true}"
ENABLE_CPA="${ENABLE_CPA:-true}"
ENABLE_VPNGATE="${ENABLE_VPNGATE:-true}"
ENABLE_ZASHBOARD="${ENABLE_ZASHBOARD:-true}"
ENABLE_NVIDIA_PROXY="${ENABLE_NVIDIA_PROXY:-true}"

CPA_PORT="${CPA_PORT:-18317}"
GEMFLOW_PORT="${PORT:-8081}"
CURSOR_PORT="${CURSOR_PORT:-4646}"
MIHOMO_CONTROLLER_PORT="${MIHOMO_CONTROLLER_PORT:-9090}"
MIHOMO_SECRET="${MIHOMO_SECRET:-}"
WORKER_COUNT="${WORKER_COUNT:-1}"

# 专用代理端口划分
# 19001..19000+N 供 gemflow Workers 独享
# NVIDIA API keys 轮询复用上述 Mihomo listeners，但在 CPA 内使用 socks5://
# 19081 供 Cursor Agent 专用出口
CURSOR_PROXY_PORT="${CURSOR_PROXY_PORT:-19081}"
NVIDIA_PROXY_HOST="${NVIDIA_PROXY_HOST:-127.0.0.1}"
NVIDIA_PROXY_BASE_PORT="${NVIDIA_PROXY_BASE_PORT:-19000}"
NVIDIA_PROXY_PORT_COUNT="${NVIDIA_PROXY_PORT_COUNT:-$WORKER_COUNT}"
NVIDIA_PROVIDER_NAMES="${NVIDIA_PROVIDER_NAMES:-nvidia}"

CHILD_PIDS=""

cleanup() {
    echo ""
    echo "[TokenFlow] Initiating graceful shutdown..."
    for p in $CHILD_PIDS; do
        kill "$p" 2>/dev/null || true
    done
    pkill -f "cursor-agent-api" 2>/dev/null || true
    pkill -f "$APP_DIR/gemini_web2api.py" 2>/dev/null || true
    pkill -f "$APP_DIR/lb_gateway.py" 2>/dev/null || true
    pkill -x mihomo 2>/dev/null || true
    pkill -x cliproxy 2>/dev/null || true
    echo "[TokenFlow] All background services stopped."
}

trap cleanup EXIT INT TERM

echo "=================================================="
echo "          Starting TokenFlow Engine               "
echo "=================================================="
echo "-> Main Gateway Port (CPA) : $CPA_PORT (Aggregator Direct)"
echo "-> Gemflow Gateway Port    : $GEMFLOW_PORT (Dedicated Multi-Egress: 19001..)"
echo "-> NVIDIA CPA Keys         : SOCKS5 Round-Robin (${NVIDIA_PROXY_PORT_COUNT} Egress Port(s))"
echo "-> Cursor Proxy Port       : $CURSOR_PORT (Dedicated Egress: 127.0.0.1:$CURSOR_PROXY_PORT)"
echo "-> Mihomo Web UI (Zashboard): 0.0.0.0:$MIHOMO_CONTROLLER_PORT/ui"
echo "=================================================="

# 0. 准备与配置 Zashboard Web UI 静态资源
if [ "$ENABLE_ZASHBOARD" = "true" ] || [ "$ENABLE_ZASHBOARD" = "1" ]; then
    UI_DIR="${UI_DIR:-$APP_DIR/ui}"
    if [ ! -f "$UI_DIR/index.html" ]; then
        echo "[Zashboard] Downloading latest Zashboard web interface into $UI_DIR..."
        mkdir -p "$UI_DIR"
        python3 - <<'EOF' || true
import urllib.request, zipfile, io, os, shutil
url = "https://github.com/Zephyruso/zashboard/releases/latest/download/dist.zip"
try:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = resp.read()
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        z.extractall("/tmp/zashboard_extracted")
    ui_dir = os.environ.get("UI_DIR", "/app/ui")
    extracted_dist = "/tmp/zashboard_extracted/dist"
    src = extracted_dist if os.path.exists(extracted_dist) else "/tmp/zashboard_extracted"
    for item in os.listdir(src):
        s = os.path.join(src, item)
        d = os.path.join(ui_dir, item)
        if os.path.isdir(s):
            shutil.copytree(s, d, dirs_exist_ok=True)
        else:
            shutil.copy2(s, d)
    print("[Zashboard] Web UI assets ready.")
except Exception as e:
    print(f"[Zashboard] Failed to download UI: {e}")
EOF
    fi

    # 注入 external-ui 与 external-controller 到 mihomo.template.yaml (若存在)
    if [ -f "$APP_DIR/mihomo.template.yaml" ]; then
        if ! grep -q "external-ui" "$APP_DIR/mihomo.template.yaml"; then
            sed -i.bak '/external-controller/a\
external-ui: ui\
secret: "'"$MIHOMO_SECRET"'"
' "$APP_DIR/mihomo.template.yaml" 2>/dev/null || true
            sed -i.bak "s|external-controller: 127.0.0.1:.*|external-controller: 0.0.0.0:$MIHOMO_CONTROLLER_PORT|" "$APP_DIR/mihomo.template.yaml" 2>/dev/null || true
            rm -f "$APP_DIR/mihomo.template.yaml.bak" 2>/dev/null || true
        fi
    fi
fi

# 0.1 自动检查并集成 VPNGate 免费节点源 (当未配置自定义订阅时自动生效)
if [ -z "$PROVIDER_URLS" ] && [ "$ENABLE_VPNGATE" = "true" ]; then
    echo "[VPNGate] No custom subscription provided. Fetching free clean egress nodes from VPNGate..."
    VPNGATE_FILE="$APP_DIR/sub-vpngate.yaml"
    python3 "$APP_DIR/vpngate_provider.py" "$VPNGATE_FILE" || true
    if [ -s "$VPNGATE_FILE" ]; then
        export PROVIDER_URLS="file://$VPNGATE_FILE"
        echo "[VPNGate] Successfully loaded VPNGate proxy pool."
    fi
fi

# 1. 准备 CLIProxyAPI 配置文件
if [ "$ENABLE_CPA" = "true" ] || [ "$ENABLE_CPA" = "1" ]; then
    if [ -n "$CPA_CONFIG" ]; then
        echo "[Config] Loading CPA_CONFIG from environment into $CPA_CONFIG_FILE..."
        printf '%s\n' "$CPA_CONFIG" > "$CPA_CONFIG_FILE"
        chmod 600 "$CPA_CONFIG_FILE"
    elif [ ! -f "$CPA_CONFIG_FILE" ]; then
        if [ -f "$APP_DIR/config.example.yaml" ]; then
            echo "[Config] Notice: No CPA_CONFIG supplied. Initializing from config.example.yaml..."
            cp "$APP_DIR/config.example.yaml" "$CPA_CONFIG_FILE"
        else
            echo "[Config] Fatal: $CPA_CONFIG_FILE not found and no CPA_CONFIG environment provided."
            exit 1
        fi
    fi
fi

# 1.1 为 CPA 内 NVIDIA API keys 生成逐 key SOCKS5 运行时配置
# 原始 config.yaml 可能由只读卷挂载，始终写入独立的 mode-0600 派生文件。
if [ "$ENABLE_CPA" = "true" ] || [ "$ENABLE_CPA" = "1" ]; then
    if [ "$ENABLE_NVIDIA_PROXY" = "true" ] || [ "$ENABLE_NVIDIA_PROXY" = "1" ]; then
        if [ -z "${PROVIDER_URLS:-}" ]; then
            echo "[NVIDIA Proxy] Warning: no Mihomo provider source is available; proxied NVIDIA requests will fail closed."
        fi
        if [ ! -f "$APP_DIR/cpa_proxy_config.py" ]; then
            echo "[NVIDIA Proxy] Fatal: $APP_DIR/cpa_proxy_config.py not found."
            exit 1
        else
            case "$WORKER_COUNT" in
                ''|*[!0-9]*)
                    echo "[NVIDIA Proxy] Fatal: WORKER_COUNT must be a positive integer."
                    exit 1
                    ;;
            esac
            case "$NVIDIA_PROXY_PORT_COUNT" in
                ''|*[!0-9]*)
                    echo "[NVIDIA Proxy] Fatal: NVIDIA_PROXY_PORT_COUNT must be a positive integer."
                    exit 1
                    ;;
            esac
            if [ "$NVIDIA_PROXY_PORT_COUNT" -le 0 ] || [ "$NVIDIA_PROXY_PORT_COUNT" -gt "$WORKER_COUNT" ]; then
                echo "[NVIDIA Proxy] Fatal: proxy port count must be between 1 and WORKER_COUNT ($WORKER_COUNT)."
                exit 1
            fi

            if python3 "$APP_DIR/cpa_proxy_config.py" \
                --input "$CPA_CONFIG_FILE" \
                --output "$CPA_RUNTIME_CONFIG_FILE" \
                --proxy-host "$NVIDIA_PROXY_HOST" \
                --proxy-base-port "$NVIDIA_PROXY_BASE_PORT" \
                --proxy-count "$NVIDIA_PROXY_PORT_COUNT" \
                --provider-names "$NVIDIA_PROVIDER_NAMES"; then
                CPA_EFFECTIVE_CONFIG_FILE="$CPA_RUNTIME_CONFIG_FILE"
            else
                echo "[NVIDIA Proxy] Fatal: refusing to start CPA with an incomplete proxy configuration."
                exit 1
            fi
        fi
    fi
fi

# 2. 启动 gemflow 多出口粘滞网关引擎 (Port 8081)
if [ "$ENABLE_GEMFLOW" = "true" ] || [ "$ENABLE_GEMFLOW" = "1" ]; then
    echo "[Gemflow] Launching gemflow engine on port $GEMFLOW_PORT..."
    (
        export PORT="$GEMFLOW_PORT"
        FAIL=0
        while true; do
            if [ -f "$APP_DIR/run_local.py" ]; then
                python3 "$APP_DIR/run_local.py" --workers "$WORKER_COUNT" --port "$GEMFLOW_PORT" ${PROVIDER_URLS:+--sub "$PROVIDER_URLS"} ${DEBUG:+--debug} || true
            elif [ -f "$APP_DIR/lb_gateway.py" ] && [ -f "$APP_DIR/gen_workers.py" ]; then
                python3 "$APP_DIR/gen_workers.py" --workers "$WORKER_COUNT" --out "$APP_DIR/workers.json" --app-dir "$APP_DIR" || true
                python3 "$APP_DIR/lb_gateway.py" --port "$GEMFLOW_PORT" --config "$APP_DIR/workers.json" || true
            else
                echo "[Gemflow] Notice: gemflow modules not detected in $APP_DIR. Skipping."
                break
            fi
            FAIL=$((FAIL + 1))
            if [ "$FAIL" -gt 6 ]; then BACKOFF=60; else BACKOFF=$((FAIL * 5)); fi
            echo "[Gemflow] Engine exited (#$FAIL), restarting in ${BACKOFF}s..."
            sleep "$BACKOFF"
        done
    ) &
    CHILD_PIDS="$CHILD_PIDS $!"
fi

# 3. 启动 Cursor CLI API Proxy 引擎 (Port 4646，走专用代理端口 19081)
if [ "$ENABLE_CURSOR" = "true" ] || [ "$ENABLE_CURSOR" = "1" ]; then
    echo "[Cursor] Launching cursor-agent-api on port $CURSOR_PORT (Proxy: http://127.0.0.1:$CURSOR_PROXY_PORT)..."
    (
        FAIL=0
        while true; do
            export PORT="$CURSOR_PORT"
            export HTTP_PROXY="http://127.0.0.1:$CURSOR_PROXY_PORT"
            export HTTPS_PROXY="http://127.0.0.1:$CURSOR_PROXY_PORT"
            export ALL_PROXY="http://127.0.0.1:$CURSOR_PROXY_PORT"
            export NO_PROXY="127.0.0.1,localhost"

            if command -v cursor-agent-api >/dev/null 2>&1; then
                cursor-agent-api run || true
            elif [ -f "$APP_DIR/cursor_proxy/dist/index.js" ]; then
                node "$APP_DIR/cursor_proxy/dist/index.js" || true
            else
                echo "[Cursor] Notice: cursor-agent-api command not found. Install via: npm i -g cursor-agent-api-proxy"
                break
            fi
            FAIL=$((FAIL + 1))
            if [ "$FAIL" -gt 6 ]; then BACKOFF=60; else BACKOFF=$((FAIL * 5)); fi
            echo "[Cursor] Service exited (#$FAIL), restarting in ${BACKOFF}s..."
            sleep "$BACKOFF"
        done
    ) &
    CHILD_PIDS="$CHILD_PIDS $!"
fi

# 4. 启动主入口 CLIProxyAPI 网关
# 聚合网关本身不继承全局代理；NVIDIA 凭据通过配置内逐 key proxy-url 出站。
if [ "$ENABLE_CPA" = "true" ] || [ "$ENABLE_CPA" = "1" ]; then
    echo "[CPA] Starting CLIProxyAPI frontend aggregator on port $CPA_PORT..."
    (
        FAIL=0
        while true; do
            unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy || true

            if command -v cliproxy >/dev/null 2>&1; then
                # 注意: Go flag 包在首个位置参数处停止解析，--config 必须置于 run 之前
                cliproxy --config "$CPA_EFFECTIVE_CONFIG_FILE" run || true
            else
                echo "[CPA] Fatal: cliproxy binary not found in PATH."
                break
            fi
            FAIL=$((FAIL + 1))
            if [ "$FAIL" -gt 6 ]; then BACKOFF=30; else BACKOFF=$((FAIL * 3)); fi
            echo "[CPA] Gateway exited (#$FAIL), restarting in ${BACKOFF}s..."
            sleep "$BACKOFF"
        done
    ) &
    CHILD_PIDS="$CHILD_PIDS $!"
fi

# 挂起主守护进程，等待子组件运行
wait
