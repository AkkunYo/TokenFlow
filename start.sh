#!/usr/bin/env bash
# gemflow 统一容器启动入口 (独立版)
#
# 本脚本同时被下游 cpa 项目以「保活循环」方式调用，故须遵守以下契约：
#   1. 文件名与可执行位保持不变 (/app/start.sh)
#   2. 尊重 PORT 环境变量
#   3. 不占用 /app/config.yaml (由 cpa 写入自己的配置)
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
MIHOMO_CONTROLLER="${MIHOMO_CONTROLLER:-127.0.0.1:9090}"
BASE_WORKER_PORT=9000
BASE_PROXY_PORT=19000

# ---------------------------------------------------------------------------
# 子进程生命周期管理
#
# 本脚本可能被外层保活循环反复调用。若退出时不回收自己拉起的后台进程，
# 它们会被 init 收养并继续持有 9090 / 19001+ / 9001+ 等端口，
# 导致下一轮启动全面端口冲突：mihomo 起不来则静默回退直连，
# Worker 起不来则请求全部 502，且日志中没有明显报错。
#
# 因此：入口先清理上一轮残留，退出时通过 trap 主动回收。
# 保活循环是子 shell，杀它不会连带杀掉其中的 python 孙进程，
# 故按完整脚本路径 pkill 补杀 —— 路径前缀限定了作用域，
# 不会误伤同容器内的 cliproxy 等其他进程。
# ---------------------------------------------------------------------------
CHILD_PIDS=""

cleanup() {
    for p in $CHILD_PIDS; do
        kill "$p" 2>/dev/null || true
    done
    pkill -f "$APP_DIR/gemini_web2api.py" 2>/dev/null || true
    pkill -f "$APP_DIR/lb_gateway.py" 2>/dev/null || true
    pkill -x mihomo 2>/dev/null || true
}

trap cleanup EXIT INT TERM

echo "[Init] Reclaiming any leftover processes from a previous run..."
cleanup
CHILD_PIDS=""

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

    # 镜像加速源优先，与 Dockerfile 构建期顺序保持一致：
    # 国内网络下可省掉一次主源连接超时的等待
    if [ -n "$UPSTREAM_MIRROR_URL" ] && curl -fsSL --connect-timeout 8 --max-time 20 "$UPSTREAM_MIRROR_URL" -o "$TMP_SCRIPT" && [ -s "$TMP_SCRIPT" ]; then
        DOWNLOADED=true
    elif curl -fsSL --connect-timeout 8 --max-time 20 "$UPSTREAM_URL" -o "$TMP_SCRIPT" && [ -s "$TMP_SCRIPT" ]; then
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
IS_CN_HOST=false

# 预检宿主机直连 IP 归属地与网络环境
echo "[Network] Inspecting host native network environment..."
GEO_COUNTRY=$(curl -fsSL --connect-timeout 2 -m 4 "http://ip-api.com/line?fields=countryCode" 2>/dev/null || curl -fsSL --connect-timeout 2 -m 4 "https://ipinfo.io/country" 2>/dev/null || true)
GEO_COUNTRY=$(echo "$GEO_COUNTRY" | tr -d '\r\n ' | tr '[:lower:]' '[:upper:]')

if [ "$GEO_COUNTRY" = "CN" ]; then
    IS_CN_HOST=true
    echo "[Network] Detected host native IP in China ($GEO_COUNTRY). Worker-1 will be routed through proxy."
elif [ -n "$GEO_COUNTRY" ]; then
    echo "[Network] Detected host native IP overseas ($GEO_COUNTRY). Worker-1 will use DIRECT connection."
else
    # 兜底测试 Google 直连连通性
    if ! curl -fsSL --connect-timeout 2 -m 3 "https://www.gstatic.com/generate_204" >/dev/null 2>&1; then
        IS_CN_HOST=true
        echo "[Network] Direct Google connection blocked. Worker-1 will be routed through proxy."
    else
        echo "[Network] Direct Google connection available. Worker-1 will use DIRECT connection."
    fi
fi

if [ -n "$PROVIDER_URLS" ]; then
    echo "[Mihomo] Generating proxy configuration for $WORKER_COUNT workers..."

    # 保留既有 sub-*.yaml 订阅缓存：mihomo 拉取成功后会自行覆盖，
    # 网络不通时这份缓存是唯一的离线兜底，清空会导致节点列表为空。

    # 由 mihomo_config.py 统一渲染 Worker 专属策略组 / providers / listeners
    if python3 "$APP_DIR/mihomo_config.py" \
        --template "$APP_DIR/mihomo.template.yaml" \
        --out "$MIHOMO_CONFIG" \
        --workers "$WORKER_COUNT" \
        --base-proxy-port "$BASE_PROXY_PORT"; then

        echo "[Mihomo] Starting mihomo daemon..."
        mihomo -d "$APP_DIR" -f "$MIHOMO_CONFIG" > /tmp/mihomo.log 2>&1 &
        MIHOMO_PID=$!
        CHILD_PIDS="$CHILD_PIDS $MIHOMO_PID"

        # 轮询 external-controller 判定就绪：配置正常时秒级通过，
        # 订阅拉取慢时最多等 30s，进程提前退出则立即放弃。
        # 固定 sleep 既可能不够（拿到空节点列表）又可能白等。
        for _ in $(seq 1 30); do
            if curl -fsS --max-time 1 "http://$MIHOMO_CONTROLLER/version" >/dev/null 2>&1; then
                USE_PROXIES=true
                break
            fi
            kill -0 "$MIHOMO_PID" 2>/dev/null || break
            sleep 1
        done

        if [ "$USE_PROXIES" = "true" ]; then
            echo "[Mihomo] Started successfully (PID $MIHOMO_PID, controller ready)."
        else
            echo "[Mihomo] Warning: not ready. Falling back to direct native routing."
            echo "[Mihomo] --- last 20 lines of /tmp/mihomo.log ---"
            tail -20 /tmp/mihomo.log 2>/dev/null || true
            echo "[Mihomo] --- end of log ---"
        fi
    else
        echo "[Mihomo] Warning: failed to render config. Falling back to direct native routing."
    fi
else
    echo "[Info] Running in DIRECT mode (No subscription provided)."
fi

# 2. 生成 workers.json 与各实例 config.json (交由 Python json.dump，避免手工拼接转义出错)
if ! python3 "$APP_DIR/gen_workers.py" \
    --workers "$WORKER_COUNT" \
    --out "$WORKERS_JSON" \
    --app-dir "$APP_DIR" \
    --use-proxies "$USE_PROXIES" \
    --cn-host "$IS_CN_HOST" \
    --base-worker-port "$BASE_WORKER_PORT" \
    --base-proxy-port "$BASE_PROXY_PORT"; then
    echo "[Workers] Fatal: failed to generate worker configuration."
    exit 1
fi

# 2.1 为各 Worker 策略组分配互不相同的出口节点
if [ "$USE_PROXIES" = "true" ]; then
    SKIP_IDS=""
    if [ "$IS_CN_HOST" != "true" ]; then
        SKIP_IDS="1"
    fi
    python3 "$APP_DIR/assign_worker_nodes.py" \
        --workers "$WORKER_COUNT" \
        --skip "$SKIP_IDS" \
        --max-wait 45 || echo "[NodeAssign] Skipped due to error; workers share the auto-select egress."
fi

# 3. 启动所有 gemini_web2api 实例
# 出口分配已固化在 workers.json 中，此处直接读取，避免 bash 侧重复推导
WORKER_LINES=$(python3 -c '
import json, sys
with open(sys.argv[1], encoding="utf-8") as f:
    data = json.load(f)
for w in data["workers"]:
    print(w["id"], w["port"], w.get("proxy") or "DIRECT")
' "$WORKERS_JSON")

while read -r W_ID W_PORT W_PROXY_DESC; do
    [ -n "$W_ID" ] || continue
    W_DIR="$APP_DIR/instances/w$W_ID"

    echo "[Worker-$W_ID] Starting gemini_web2api on port $W_PORT (proxy: $W_PROXY_DESC)..."
    (
        cd "$W_DIR"
        FAIL=0
        while true; do
            # 追加写而非覆盖：崩溃重启的瞬间清空日志会让根因永久丢失
            python3 "$APP_DIR/gemini_web2api.py" --port "$W_PORT" \
                --config "$W_DIR/config.json" >> "$APP_DIR/worker_$W_ID.log" 2>&1 || true
            FAIL=$((FAIL + 1))
            # 指数退避封顶 60s：Cookie 失效等持续性故障下避免每秒空转刷盘
            if [ "$FAIL" -gt 6 ]; then BACKOFF=60; else BACKOFF=$((FAIL * 5)); fi
            echo "[Worker-$W_ID] exited (#$FAIL), retry in ${BACKOFF}s" | tee -a "$APP_DIR/worker_$W_ID.log"
            sleep "$BACKOFF"
        done
    ) &
    CHILD_PIDS="$CHILD_PIDS $!"
done <<< "$WORKER_LINES"

# 4. 启动轻量负载网关 lb_gateway.py
echo "[LB] Starting gemflow Load Balancer Gateway on port $PORT..."
EXTRA_ARGS=""
if [ "$DEBUG" = "true" ] || [ "$DEBUG" = "1" ]; then
    EXTRA_ARGS="--debug"
fi

# 网关同样进入保活循环并置于后台，脚本末尾以 wait 挂起。
# 若让网关作为前台阻塞进程，它一旦退出整个 start.sh 就退出，
# 外层保活循环重新执行时会与尚未回收的 mihomo / Worker 抢端口。
(
    FAIL=0
    while true; do
        python3 "$APP_DIR/lb_gateway.py" --port "$PORT" --config "$WORKERS_JSON" $EXTRA_ARGS || true
        FAIL=$((FAIL + 1))
        if [ "$FAIL" -gt 6 ]; then BACKOFF=60; else BACKOFF=$((FAIL * 5)); fi
        echo "[LB] Gateway exited (#$FAIL), retry in ${BACKOFF}s"
        sleep "$BACKOFF"
    done
) &
CHILD_PIDS="$CHILD_PIDS $!"

# 挂起等待所有保活循环。单个组件崩溃由各自循环内部重启，
# 不再连带整个脚本退出，从根上避免「重启 -> 端口冲突」循环。
wait || true
