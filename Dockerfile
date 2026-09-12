FROM python:3.11-slim

WORKDIR /app

USER root

# 1. 安装基础运行依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    procps \
    gzip \
    tar \
    && rm -rf /var/lib/apt/lists/*

# 2. 下载并安装 Mihomo (Clash Meta) 二进制内核
ARG MIHOMO_VERSION=1.19.30
RUN ARCH=$(uname -m) && \
    if [ "$ARCH" = "x86_64" ]; then MIHOMO_ARCH="amd64-compatible"; \
    elif [ "$ARCH" = "aarch64" ]; then MIHOMO_ARCH="arm64"; \
    else MIHOMO_ARCH="amd64"; fi && \
    curl -fsSL "https://github.com/MetaCubeX/mihomo/releases/download/v${MIHOMO_VERSION}/mihomo-linux-${MIHOMO_ARCH}-v${MIHOMO_VERSION}.gz" -o /tmp/mihomo.gz && \
    gzip -d /tmp/mihomo.gz && \
    mv /tmp/mihomo /usr/local/bin/mihomo && \
    chmod +x /usr/local/bin/mihomo

# 3. 下载并安装最新版 CLIProxyAPI (支持 amd64 与 arm64 架构自适应)
RUN python3 - <<'EOF'
import urllib.request, tarfile, glob, os, time, platform

arch_map = {"x86_64": "amd64", "aarch64": "aarch64", "arm64": "aarch64"}
machine = platform.machine().lower()
target_arch = arch_map.get(machine, "amd64")

req = urllib.request.Request(f"https://github.com/router-for-me/CLIProxyAPI/releases/latest?t={int(time.time())}", headers={"User-Agent": "Mozilla/5.0"})
with urllib.request.urlopen(req) as resp:
    tag = resp.geturl().rstrip("/").split("/")[-1]
ver = tag.lstrip("v")
download_url = f"https://github.com/router-for-me/CLIProxyAPI/releases/download/{tag}/CLIProxyAPI_{ver}_linux_{target_arch}.tar.gz"
print(f"Downloading CLIProxyAPI ({target_arch}) from:", download_url)

dl_req = urllib.request.Request(download_url, headers={"User-Agent": "Mozilla/5.0"})
with urllib.request.urlopen(dl_req) as resp, open("/tmp/cliproxy.tar.gz", "wb") as f:
    f.write(resp.read())
with tarfile.open("/tmp/cliproxy.tar.gz", "r:gz") as tar:
    tar.extractall("/tmp/cliproxy_out")
for f in glob.glob("/tmp/cliproxy_out/**/cli-proxy-api", recursive=True) + glob.glob("/tmp/cliproxy_out/**/CLIProxyAPI", recursive=True):
    os.rename(f, "/usr/local/bin/cliproxy")
    break
os.chmod("/usr/local/bin/cliproxy", 0o755)
os.remove("/tmp/cliproxy.tar.gz")
EOF

# 4. 下载并配置 Zashboard Web 面板静态资源到 /app/ui
RUN python3 - <<'EOF'
import urllib.request, zipfile, io, os, shutil

url = "https://github.com/Zephyruso/zashboard/releases/latest/download/dist.zip"
print("Downloading latest Zashboard UI from:", url)
req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
try:
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = resp.read()
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        z.extractall("/tmp/zashboard_extracted")
    os.makedirs("/app/ui", exist_ok=True)
    extracted_dist = "/tmp/zashboard_extracted/dist"
    src_dir = extracted_dist if os.path.exists(extracted_dist) else "/tmp/zashboard_extracted"
    for item in os.listdir(src_dir):
        s = os.path.join(src_dir, item)
        d = os.path.join("/app/ui", item)
        if os.path.isdir(s):
            shutil.copytree(s, d, dirs_exist_ok=True)
        else:
            shutil.copy2(s, d)
    print("Zashboard UI extracted successfully to /app/ui")
except Exception as e:
    print(f"Warning: Failed to pre-download Zashboard UI during build ({e}). Will fallback at runtime.")
EOF

# 5. 安装 Python 依赖
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r /app/requirements.txt

# 6. 构建期预下载 gemini_web2api.py 做本地固化兜底
ARG UPSTREAM_URL="https://raw.githubusercontent.com/Sophomoresty/gemini-web2api/refs/heads/main/gemini_web2api.py"
ARG UPSTREAM_MIRROR_URL="https://ghfast.top/https://raw.githubusercontent.com/Sophomoresty/gemini-web2api/refs/heads/main/gemini_web2api.py"
RUN (curl -fsSL "${UPSTREAM_MIRROR_URL}" -o /app/gemini_web2api.py || \
     curl -fsSL "${UPSTREAM_URL}" -o /app/gemini_web2api.py || true) && \
    chmod +x /app/gemini_web2api.py 2>/dev/null || true

# 7. 创建 UID 1000 非 root 用户并授权工作区
RUN if ! id -u user >/dev/null 2>&1; then useradd -m -u 1000 user; fi && \
    mkdir -p /home/user/app /app/auth-dir /app/ui && \
    chown -R user:user /app /home/user

# 8. 按 Monorepo 所有权复制源码，运行时平铺以保持兼容
COPY --chown=user:user services/gemflow/lb_gateway.py services/gemflow/gen_workers.py services/gemflow/run_local.py /app/
COPY --chown=user:user services/gemflow/config.json.example /app/config.json.example
COPY --chown=user:user packages/egress/assign_worker_nodes.py packages/egress/mihomo_config.py packages/egress/vpngate_provider.py /app/
COPY --chown=user:user packages/egress/mihomo.template.yaml /app/mihomo.template.yaml
COPY --chown=user:user apps/tokenflow/cpa_proxy_config.py apps/tokenflow/start.sh apps/tokenflow/tokenflow.sh /app/
COPY --chown=user:user apps/tokenflow/config.example.yaml /app/config.example.yaml
RUN chmod +x /app/start.sh /app/vpngate_provider.py /app/run_local.py /app/cpa_proxy_config.py 2>/dev/null || true

USER user

# 暴露端口: 18317 (CLIProxyAPI 主入口), 8081 (gemflow 网关), 9090 (Mihomo API & Zashboard 面板)
EXPOSE 18317 8081 9090

CMD ["/app/start.sh"]
