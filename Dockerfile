FROM python:3.11-slim

WORKDIR /app

# 安装基础依赖与 curl
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    gzip \
    tar \
    && rm -rf /var/lib/apt/lists/*

# 下载并安装 Mihomo (Clash Meta) 二进制
RUN ARCH=$(uname -m) && \
    if [ "$ARCH" = "x86_64" ]; then MIHOMO_ARCH="amd64-compatible"; \
    elif [ "$ARCH" = "aarch64" ]; then MIHOMO_ARCH="arm64"; \
    else MIHOMO_ARCH="amd64"; fi && \
    curl -fsSL "https://github.com/MetaCubeX/mihomo/releases/download/v1.19.22/mihomo-linux-${MIHOMO_ARCH}-v1.19.22.gz" -o mihomo.gz && \
    gzip -d mihomo.gz && \
    mv mihomo /usr/local/bin/mihomo && \
    chmod +x /usr/local/bin/mihomo

# 安装 Python 依赖
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# 复制项目代码与启动脚本
COPY . /app/
RUN chmod +x /app/start.sh

# 环境变量默认值
ENV PORT=8081 \
    WORKER_COUNT=1 \
    PROVIDER_URLS="" \
    DEBUG="false"

EXPOSE 8081

CMD ["/app/start.sh"]
