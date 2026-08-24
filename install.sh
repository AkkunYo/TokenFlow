#!/usr/bin/env bash
# ==============================================================================
#                 TokenFlow Linux 宿主机一键全自动安装脚本
# ==============================================================================
set -e

COLOR_GREEN="\033[32m"
COLOR_RED="\033[31m"
COLOR_YELLOW="\033[33m"
COLOR_CYAN="\033[36m"
COLOR_RESET="\033[0m"

log_info() { echo -e "${COLOR_GREEN}[TokenFlow INFO]${COLOR_RESET} $1"; }
log_warn() { echo -e "${COLOR_YELLOW}[TokenFlow WARN]${COLOR_RESET} $1"; }
log_err()  { echo -e "${COLOR_RED}[TokenFlow ERROR]${COLOR_RESET} $1"; }

INSTALL_DIR="${INSTALL_DIR:-/opt/tokenflow}"
BIN_DIR="${BIN_DIR:-/usr/local/bin}"
SYSTEMD_SERVICE_FILE="/etc/systemd/system/tokenflow.service"

echo -e "${COLOR_CYAN}"
echo "========================================================"
echo "          TokenFlow All-in-One Installer                "
echo "========================================================"
echo -e "${COLOR_RESET}"

# 1. 检查 root 权限
if [ "$(id -u)" -ne 0 ]; then
    log_err "Please run this installer as root (or use sudo)."
    exit 1
fi

# 2. 检测系统架构
ARCH=$(uname -m)
case "$ARCH" in
    x86_64|amd64)
        CLI_ARCH="amd64"
        MIHOMO_ARCH="amd64-compatible"
        ;;
    aarch64|arm64)
        CLI_ARCH="aarch64"
        MIHOMO_ARCH="arm64"
        ;;
    *)
        log_err "Unsupported architecture: $ARCH"
        exit 1
        ;;
esac
log_info "Detected system architecture: $ARCH (Mapping: CLIProxy=$CLI_ARCH, Mihomo=$MIHOMO_ARCH)"

# 3. 安装系统依赖工具
log_info "Installing required packages (curl, git, tar, gzip, python3, pip, nodejs)..."
if command -v apt-get >/dev/null 2>&1; then
    apt-get update -y
    apt-get install -y curl git tar gzip python3 python3-pip python3-venv procps
    if ! command -v node >/dev/null 2>&1; then
        log_info "Setting up Node.js 20 repository..."
        curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
        apt-get install -y nodejs
    fi
elif command -v dnf >/dev/null 2>&1; then
    dnf install -y curl git tar gzip python3 python3-pip procps-ng
    if ! command -v node >/dev/null 2>&1; then
        curl -fsSL https://rpm.nodesource.com/setup_20.x | bash -
        dnf install -y nodejs
    fi
elif command -v yum >/dev/null 2>&1; then
    yum install -y curl git tar gzip python3 python3-pip procps-ng
    if ! command -v node >/dev/null 2>&1; then
        curl -fsSL https://rpm.nodesource.com/setup_20.x | bash -
        yum install -y nodejs
    fi
elif command -v pacman >/dev/null 2>&1; then
    pacman -Sy --noconfirm curl git tar gzip python python-pip nodejs npm procps-ng
fi

# 4. 创建安装目录
mkdir -p "$INSTALL_DIR" "$INSTALL_DIR/bin" "$INSTALL_DIR/instances" "$INSTALL_DIR/auth-dir"

# 5. 下载并安装 Mihomo (Clash.Meta) 内核
log_info "Downloading and installing Mihomo kernel..."
MIHOMO_URL="https://github.com/MetaCubeX/mihomo/releases/download/v1.19.22/mihomo-linux-${MIHOMO_ARCH}-v1.19.22.gz"
curl -fsSL "$MIHOMO_URL" -o /tmp/mihomo.gz
gzip -df /tmp/mihomo.gz
mv /tmp/mihomo "$BIN_DIR/mihomo"
chmod +x "$BIN_DIR/mihomo"

# 6. 下载并安装最新版 CLIProxyAPI
log_info "Fetching latest CLIProxyAPI binary..."
python3 - <<EOF
import urllib.request, tarfile, glob, os, time

download_url = "https://github.com/router-for-me/CLIProxyAPI/releases/latest"
req = urllib.request.Request(download_url, headers={"User-Agent": "Mozilla/5.0"})
with urllib.request.urlopen(req) as resp:
    tag = resp.geturl().rstrip("/").split("/")[-1]
ver = tag.lstrip("v")
target_url = f"https://github.com/router-for-me/CLIProxyAPI/releases/download/{tag}/CLIProxyAPI_{ver}_linux_${CLI_ARCH}.tar.gz"
print("Downloading from:", target_url)

dl_req = urllib.request.Request(target_url, headers={"User-Agent": "Mozilla/5.0"})
with urllib.request.urlopen(dl_req) as resp, open("/tmp/cliproxy.tar.gz", "wb") as f:
    f.write(resp.read())
with tarfile.open("/tmp/cliproxy.tar.gz", "r:gz") as tar:
    tar.extractall("/tmp/cliproxy_out")
for f in glob.glob("/tmp/cliproxy_out/**/cli-proxy-api", recursive=True) + glob.glob("/tmp/cliproxy_out/**/CLIProxyAPI", recursive=True):
    os.rename(f, "$BIN_DIR/cliproxy")
    break
if os.path.exists("$BIN_DIR/cliproxy"):
    os.chmod("$BIN_DIR/cliproxy", 0o755)
EOF
rm -rf /tmp/cliproxy*

# 7. 安装 Cursor CLI 与 cursor-agent-api-proxy
log_info "Installing Cursor CLI and cursor-agent-api-proxy..."
curl https://cursor.com/install -fsS | bash || true
if [ -f /root/.local/bin/agent ]; then
    ln -sf /root/.local/bin/agent "$BIN_DIR/agent"
fi
if [ -f /root/.local/bin/cursor-agent ]; then
    ln -sf /root/.local/bin/cursor-agent "$BIN_DIR/cursor-agent"
fi
npm install -g cursor-agent-api-proxy || true

# 8. 同步 gemflow / TokenFlow 核心运行时代码
log_info "Setting up Python runtime dependencies in $INSTALL_DIR..."
cat <<'EOF' > "$INSTALL_DIR/requirements.txt"
httpx>=0.25.0
pyyaml>=6.0
curl_cffi
websockets
certifi
requests>=2.31.0
aiohttp>=3.9.0
EOF

pip3 install --no-cache-dir -r "$INSTALL_DIR/requirements.txt" || pip install --break-system-packages -r "$INSTALL_DIR/requirements.txt" || true

# 9. 部署源码、配置文件、转换模块与管理工具
for pyfile in lb_gateway.py gen_workers.py assign_worker_nodes.py mihomo_config.py run_local.py vpngate_provider.py; do
    if [ -f "./$pyfile" ]; then
        cp "./$pyfile" "$INSTALL_DIR/$pyfile"
        chmod +x "$INSTALL_DIR/$pyfile"
    fi
done

if [ -f "./mihomo.template.yaml" ]; then
    cp ./mihomo.template.yaml "$INSTALL_DIR/mihomo.template.yaml"
fi

if [ -f "./config.example.yaml" ]; then
    cp ./config.example.yaml "$INSTALL_DIR/config.example.yaml"
fi
if [ ! -f "$INSTALL_DIR/config.yaml" ] && [ -f "$INSTALL_DIR/config.example.yaml" ]; then
    cp "$INSTALL_DIR/config.example.yaml" "$INSTALL_DIR/config.yaml"
fi

if [ -f "./tokenflow.sh" ]; then
    cp ./tokenflow.sh "$BIN_DIR/tokenflow"
    chmod +x "$BIN_DIR/tokenflow"
fi

# 10. 配置 systemd 守护进程 (宿主机标准守护模式)
log_info "Configuring systemd service: $SYSTEMD_SERVICE_FILE..."
cat <<EOF > "$SYSTEMD_SERVICE_FILE"
[Unit]
Description=TokenFlow Unified Intelligent Gateway Service
After=network.target network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$INSTALL_DIR
Environment=APP_DIR=$INSTALL_DIR
Environment=CPA_CONFIG_FILE=$INSTALL_DIR/config.yaml
Environment=PORT=8081
Environment=CPA_PORT=18317
Environment=CURSOR_PORT=4646
ExecStart=$INSTALL_DIR/start.sh
Restart=always
RestartSec=3s
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
EOF

if [ -f "./start.sh" ]; then
    cp ./start.sh "$INSTALL_DIR/start.sh"
    chmod +x "$INSTALL_DIR/start.sh"
fi

systemctl daemon-reload
systemctl enable tokenflow

echo ""
echo -e "${COLOR_GREEN}========================================================"
echo "          🎉 TokenFlow Installed Successfully!          "
echo "========================================================"
echo -e "${COLOR_RESET}"
echo "Quick Management Commands:"
echo "  - Start service   : tokenflow start (or systemctl start tokenflow)"
echo "  - Stop service    : tokenflow stop  (or systemctl stop tokenflow)"
echo "  - Service Status  : tokenflow status"
echo "  - Live Logs       : tokenflow logs"
echo "  - Edit Config     : tokenflow config"
echo ""
echo "Config File Location: $INSTALL_DIR/config.yaml"
echo "Main Port (CPA)     : 18317"
echo "Gemflow Port        : 8081"
echo "Cursor Proxy Port   : 4646"
echo ""
