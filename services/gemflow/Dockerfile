FROM python:3.11-slim

WORKDIR /app

# 安装基础依赖与 curl、procps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    gzip \
    tar \
    procps \
    && rm -rf /var/lib/apt/lists/*

# 下载并安装 Mihomo (Clash Meta) 二进制
# v1.19.30 起内置 OpenVPN 出站支持 (v1.19.22 及以前不含该协议)
ARG MIHOMO_VERSION=1.19.30
RUN ARCH=$(uname -m) && \
    if [ "$ARCH" = "x86_64" ]; then MIHOMO_ARCH="amd64-compatible"; \
    elif [ "$ARCH" = "aarch64" ]; then MIHOMO_ARCH="arm64"; \
    else MIHOMO_ARCH="amd64"; fi && \
    curl -fsSL "https://github.com/MetaCubeX/mihomo/releases/download/v${MIHOMO_VERSION}/mihomo-linux-${MIHOMO_ARCH}-v${MIHOMO_VERSION}.gz" -o mihomo.gz && \
    gzip -d mihomo.gz && \
    mv mihomo /usr/local/bin/mihomo && \
    chmod +x /usr/local/bin/mihomo

# 安装 Python 依赖 (预装 gemflow 及 gemini-web2api 常用依赖)
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# 复制项目代码与启动脚本
COPY . /app/
RUN chmod +x /app/start.sh

# 构建参数与元信息
ARG BUILD_VERSION="dev"
ARG BUILD_TIME=""

# 环境变量默认值
ENV PORT=8081 \
    WORKER_COUNT=1 \
    PROVIDER_URLS="" \
    AUTO_UPDATE_UPSTREAM="true" \
    UPSTREAM_URL="https://raw.githubusercontent.com/Sophomoresty/gemini-web2api/refs/heads/main/gemini_web2api.py" \
    UPSTREAM_MIRROR_URL="https://ghfast.top/https://raw.githubusercontent.com/Sophomoresty/gemini-web2api/refs/heads/main/gemini_web2api.py" \
    DEBUG="false" \
    BUILD_VERSION=${BUILD_VERSION} \
    BUILD_TIME=${BUILD_TIME}

# 构建期预下载 gemini_web2api.py 做本地固化兜底 (使用 BUILD_TIME 避免构建缓存滞后)
RUN echo "Build version: ${BUILD_VERSION}, Build time: ${BUILD_TIME}" && \
    (curl -fsSL "${UPSTREAM_MIRROR_URL}" -o /app/gemini_web2api.py || \
     curl -fsSL "${UPSTREAM_URL}" -o /app/gemini_web2api.py || true) && \
    chmod +x /app/gemini_web2api.py 2>/dev/null || true

EXPOSE 8081

CMD ["/app/start.sh"]
