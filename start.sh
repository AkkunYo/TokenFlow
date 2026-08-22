#!/usr/bin/env bash
# gemflow 统一容器启动入口 (独立版)
set -eo pipefail

APP_DIR="${APP_DIR:-/app}"
cd "$APP_DIR"

WORKER_COUNT="${WORKER_COUNT:-1}"
PROVIDER_URLS="${PROVIDER_URLS:-}"
PORT="${PORT:-8081}"
DEBUG="${DEBUG:-false}"
BUILD_VERSION="${BUILD_VERSION:-unknown}"
BUILD_TIME="${BUILD_TIME:-unknown}"
AUTO_UPDATE_UPSTREAM="${AUTO_UPDATE_UPSTREAM:-true}"
UPSTREAM_URL="${UPSTREAM_URL:-https://raw.githubusercontent.com/Sophomoresty/gemini-web2api/refs/heads/main/gemini_web2api.py}"
UPSTREAM_MIRROR_URL="${UPSTREAM_MIRROR_URL:-https://ghfast.top/https://raw.githubusercontent.com/Sophomoresty/gemini-web2api/refs/heads/main/gemini_web2api.py}"
WORKERS_JSON="$APP_DIR/workers.json"
MIHOMO_CONFIG="$APP_DIR/mihomo.yaml"
BASE_WORKER_PORT=9000
BASE_PROXY_PORT=19000

echo "=================================================="
echo "          Starting gemflow Gateway Engine        "
echo "=================================================="
echo "-> Image Version       : $BUILD_VERSION"
echo "-> Image Build Time    : $BUILD_TIME"
echo "-> Unified Listen Port : $PORT"
echo "-> Target Worker Count : $WORKER_COUNT"
echo "-> Auto Update Upstream: $AUTO_UPDATE_UPSTREAM"
echo "-> Debug Logging Mode  : $DEBUG"
echo "=================================================="

# 0. 自动拉取/更新最新版 gemini_web2api.py
TARGET_SCRIPT="$APP_DIR/gemini_web2api.py"
if [ "$AUTO_UPDATE_UPSTREAM" = "true" ] || [ "$AUTO_UPDATE_UPSTREAM" = "1" ]; then
    echo "[Upstream] Checking and downloading latest gemini_web2api.py..."
    TMP_SCRIPT="/tmp/gemini_web2api_latest.py"
    DOWNLOADED=false

    # 优先从主源下载，失败则尝试镜像加速源
    if curl -fsSL --connect-timeout 8 --max-time 20 "$UPSTREAM_URL" -o "$TMP_SCRIPT" && [ -s "$TMP_SCRIPT" ]; then
        DOWNLOADED=true
    elif [ -n "$UPSTREAM_MIRROR_URL" ] && curl -fsSL --connect-timeout 8 --max-time 20 "$UPSTREAM_MIRROR_URL" -o "$TMP_SCRIPT" && [ -s "$TMP_SCRIPT" ]; then
        DOWNLOADED=true
    fi

    if [ "$DOWNLOADED" = "true" ]; then
        # 简单验证下载的内容包含 python 关键字
        if grep -q "import" "$TMP_SCRIPT" || grep -q "def " "$TMP_SCRIPT"; then
            cp "$TMP_SCRIPT" "$TARGET_SCRIPT"
            chmod +x "$TARGET_SCRIPT"
            echo "[Upstream] Successfully updated gemini_web2api.py to latest version."
        else
            echo "[Upstream] Downloaded file invalid. Keeping existing script."
        fi
        rm -f "$TMP_SCRIPT"
    else
        if [ -f "$TARGET_SCRIPT" ]; then
            echo "[Upstream] Notice: Network failed to fetch latest upstream. Using existing cached gemini_web2api.py."
        else
            echo "[Upstream] Error: Failed to fetch gemini_web2api.py and no local copy exists."
        fi
    fi
fi

# 1. 检查并准备订阅与 Mihomo 代理配置
USE_PROXIES=false
if [ -n "$PROVIDER_URLS" ]; then
    echo "[Mihomo] Generating proxy configuration for $WORKER_COUNT workers..."
    cp "$APP_DIR/mihomo.template.yaml" "$MIHOMO_CONFIG"

    # 注入 proxy-providers
    echo "proxy-providers:" >> "$MIHOMO_CONFIG"
    P_INDEX=1
    echo "$PROVIDER_URLS" | while IFS= read -r url || [ -n "$url" ]; do
        url=$(echo "$url" | tr -d '\r' | xargs)
        if [ -n "$url" ]; then
            cat <<EOF >> "$MIHOMO_CONFIG"
  sub-$P_INDEX:
    type: http
    url: "$url"
    interval: 3600
    path: ./sub-$P_INDEX.yaml
    health-check:
      enable: true
      interval: 180
      url: https://www.gstatic.com/generate_204
EOF
            P_INDEX=$((P_INDEX + 1))
        fi
    done

    # 注入 listeners (为所有 Worker 1..N 创建专属端口 19001..19000+N)
    echo "listeners:" >> "$MIHOMO_CONFIG"
    for ((i=0; i<WORKER_COUNT; i++)); do
        PROXY_PORT=$((BASE_PROXY_PORT + i + 1))
        cat <<EOF >> "$MIHOMO_CONFIG"
  - name: mixed-$PROXY_PORT
    type: mixed
    port: $PROXY_PORT
    proxy: 🚀 节点选择
EOF
    done

    echo "[Mihomo] Starting mihomo daemon..."
    mihomo -d "$APP_DIR" -f "$MIHOMO_CONFIG" > /tmp/mihomo.log 2>&1 &
    MIHOMO_PID=$!
    sleep 3

    if kill -0 "$MIHOMO_PID" 2>/dev/null; then
        echo "[Mihomo] Started successfully (PID $MIHOMO_PID)."
        USE_PROXIES=true
    else
        echo "[Mihomo] Warning: mihomo failed to start. Falling back to direct native routing."
    fi
else
    echo "[Info] Running in DIRECT mode (No subscription provided)."
fi

# 2. 生成 workers.json
echo "{\"workers\": [" > "$WORKERS_JSON"
for ((i=0; i<WORKER_COUNT; i++)); do
    W_ID=$((i + 1))
    W_PORT=$((BASE_WORKER_PORT + W_ID))

    if [ "$USE_PROXIES" = "true" ]; then
        PROXY_PORT=$((BASE_PROXY_PORT + W_ID))
        PROXY_URL="\"http://127.0.0.1:$PROXY_PORT\""
    else
        PROXY_URL="null"
    fi

    COMMA=","
    if [ "$i" -eq $((WORKER_COUNT - 1)) ]; then
        COMMA=""
    fi
    echo "  {\"id\": $W_ID, \"port\": $W_PORT, \"proxy\": $PROXY_URL}$COMMA" >> "$WORKERS_JSON"
done
echo "]}" >> "$WORKERS_JSON"

echo "[LB] Generated workers config ($WORKERS_JSON):"
cat "$WORKERS_JSON"

# 3. 启动所有 gemini_web2api 实例
for ((i=0; i<WORKER_COUNT; i++)); do
    W_ID=$((i + 1))
    W_PORT=$((BASE_WORKER_PORT + W_ID))
    W_DIR="$APP_DIR/instances/w$W_ID"
    mkdir -p "$W_DIR"

    # 生成 config.json
    W_PROXY=""
    if [ "$USE_PROXIES" = "true" ]; then
        PROXY_PORT=$((BASE_PROXY_PORT + W_ID))
        W_PROXY="http://127.0.0.1:$PROXY_PORT"
    fi

    cat <<EOF > "$W_DIR/config.json"
{
  "port": $W_PORT,
  "api_keys": [],
  "cookie": "",
  "proxy": $([ -n "$W_PROXY" ] && echo "\"$W_PROXY\"" || echo "null"),
  "log_requests": false
}
EOF

    echo "[Worker-$W_ID] Starting gemini_web2api on port $W_PORT (proxy: ${W_PROXY:-DIRECT})..."
    (
        cd "$W_DIR"
        while true; do
            python3 "$APP_DIR/gemini_web2api.py" --port "$W_PORT" --config "$W_DIR/config.json" > "$APP_DIR/worker_$W_ID.log" 2>&1 || true
            sleep 1
        done
    ) &
done

# 4. 启动轻量负载网关 lb_gateway.py
echo "[LB] Starting gemflow Load Balancer Gateway on port $PORT..."
EXTRA_ARGS=""
if [ "$DEBUG" = "true" ] || [ "$DEBUG" = "1" ]; then
    EXTRA_ARGS="--debug"
fi

python3 "$APP_DIR/lb_gateway.py" --port "$PORT" --config "$WORKERS_JSON" $EXTRA_ARGS
