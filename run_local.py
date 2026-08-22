#!/usr/bin/env python3
"""
gemflow - 本地一键启动与多实例负载编排脚本 (macOS / Linux / Windows)
支持:
1. 自动生成多 worker 配置 (workers.json) 与多实例工作目录
2. 自动配置与拉起 Mihomo 多端口代理 (若指定 --sub 订阅链接)
3. 批量拉起 gemini_web2api 实例并常驻保活
4. 启动轻量智能粘滞负载网关 (lb_gateway.py)
"""

import sys
import os
import time
import json
import signal
import shutil
import argparse
import subprocess
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WORKERS_JSON = os.path.join(BASE_DIR, "workers.json")
MIHOMO_CONFIG = os.path.join(BASE_DIR, "mihomo.yaml")
TEMPLATE_YAML = os.path.join(BASE_DIR, "mihomo.template.yaml")
SUB_FILE = os.path.join(BASE_DIR, "provider_urls.txt")
UPSTREAM_PY_URL = "https://raw.githubusercontent.com/Sophomoresty/gemini-web2api/refs/heads/main/gemini_web2api.py"
UPSTREAM_MIRROR_PY_URL = "https://ghfast.top/https://raw.githubusercontent.com/Sophomoresty/gemini-web2api/refs/heads/main/gemini_web2api.py"

BASE_WORKER_PORT = 9000
BASE_PROXY_PORT = 19000

PROCESSES = []


def fetch_latest_upstream(target_path, force=False):
    """自动拉取 upstream gemini_web2api.py 保证最新"""
    if os.path.exists(target_path) and not force:
        return True

    print("[gemflow] Fetching latest `gemini_web2api.py` from upstream repository...")
    urls = [
        os.environ.get("UPSTREAM_URL", UPSTREAM_PY_URL),
        os.environ.get("UPSTREAM_MIRROR_URL", UPSTREAM_MIRROR_PY_URL),
    ]

    for u in urls:
        if not u:
            continue
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "gemflow-launcher"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    code = resp.read().decode("utf-8")
                    if "import" in code and "def " in code:
                        with open(target_path, "w", encoding="utf-8") as f:
                            f.write(code)
                        print(f"[gemflow] Successfully fetched latest upstream script -> {target_path}")
                        return True
        except Exception as e:
            print(f"[gemflow] Fetch failed via {u}: {e}")

    if os.path.exists(target_path):
        print(f"[gemflow] Using existing cached `{target_path}`.")
        return True

    return False


def terminate_all(signum=None, frame=None):
    print("\n[gemflow] Shutting down all services...")
    for p in PROCESSES:
        try:
            if p.poll() is None:
                p.terminate()
        except Exception:
            pass
    time.sleep(1)
    for p in PROCESSES:
        try:
            if p.poll() is None:
                p.kill()
        except Exception:
            pass
    print("[gemflow] All processes stopped.")
    sys.exit(0)


def generate_mihomo_config(worker_count, sub_urls):
    if not os.path.exists(TEMPLATE_YAML):
        print(f"[Warning] Template {TEMPLATE_YAML} not found. Skipping proxy setup.")
        return False

    with open(TEMPLATE_YAML, "r", encoding="utf-8") as f:
        content = f.read()

    providers_block = "\nproxy-providers:\n"
    for idx, url in enumerate(sub_urls, start=1):
        url = url.strip()
        if not url:
            continue
        providers_block += f"""  sub-{idx}:
    type: http
    url: "{url}"
    interval: 3600
    path: ./sub-{idx}.yaml
    health-check:
      enable: true
      interval: 180
      url: https://www.gstatic.com/generate_204
"""

    listeners_block = "\nlisteners:\n"
    for i in range(worker_count):
        proxy_port = BASE_PROXY_PORT + i + 1
        listeners_block += f"""  - name: mixed-{proxy_port}
    type: mixed
    port: {proxy_port}
    proxy: 🚀 节点选择
"""

    full_yaml = content + providers_block + listeners_block
    with open(MIHOMO_CONFIG, "w", encoding="utf-8") as f:
        f.write(full_yaml)

    return True


def main():
    parser = argparse.ArgumentParser(description="gemflow - Gemini Multi-Instance Load Balancer")
    parser.add_argument("--workers", "-w", type=int, default=1, help="Number of worker instances (default: 1)")
    parser.add_argument("--port", "-p", type=int, default=8081, help="LB Gateway entryport (default: 8081)")
    parser.add_argument("--sub", "-s", type=str, default="", help="Subscription URL or path to subscription file")
    parser.add_argument("--update", action="store_true", help="Force update gemini_web2api.py from upstream repository")
    parser.add_argument("--debug", action="store_true", help="Enable verbose debug logs")
    args = parser.parse_args()

    signal.signal(signal.SIGINT, terminate_all)
    signal.signal(signal.SIGTERM, terminate_all)

    # 1. 收集订阅
    sub_urls = []
    if args.sub:
        if os.path.isfile(args.sub):
            with open(args.sub, "r", encoding="utf-8") as f:
                sub_urls = [line.strip() for line in f if line.strip() and not line.startswith("#")]
        else:
            sub_urls = [args.sub.strip()]
    elif os.path.exists(SUB_FILE):
        with open(SUB_FILE, "r", encoding="utf-8") as f:
            sub_urls = [line.strip() for line in f if line.strip() and not line.startswith("#")]

    env_subs = os.environ.get("PROVIDER_URLS", "")
    if env_subs:
        sub_urls.extend([line.strip() for line in env_subs.splitlines() if line.strip()])

    use_proxies = False
    if sub_urls:
        print(f"[gemflow] Configuring Mihomo for {args.workers} workers using {len(sub_urls)} subscription source(s)...")
        if generate_mihomo_config(args.workers, sub_urls):
            # 检查 mihomo 二进制是否在 PATH 中
            mihomo_bin = shutil.which("mihomo") or shutil.which("clash-meta")
            if mihomo_bin:
                try:
                    p = subprocess.Popen([mihomo_bin, "-d", BASE_DIR, "-f", MIHOMO_CONFIG],
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    PROCESSES.append(p)
                    time.sleep(2)
                    if p.poll() is None:
                        use_proxies = True
                        print("[Mihomo] Started successfully.")
                    else:
                        print("[Mihomo] Warning: process exited early.")
                except Exception as e:
                    print(f"[Mihomo] Failed to spawn: {e}")
            else:
                print("[Mihomo] Binary 'mihomo' not found in PATH. Defaulting to DIRECT connections.")

    # 2. 生成 workers.json
    workers = []
    for i in range(args.workers):
        wid = i + 1
        wport = BASE_WORKER_PORT + wid
        proxy = None
        if use_proxies:
            proxy = f"http://127.0.0.1:{BASE_PROXY_PORT + wid}"
        workers.append({"id": wid, "port": wport, "proxy": proxy})

    with open(WORKERS_JSON, "w", encoding="utf-8") as f:
        json.dump({"workers": workers}, f, indent=2)

    print(f"[gemflow] Created {WORKERS_JSON} with {len(workers)} worker(s):")
    for w in workers:
        print(f"  -> Worker-{w['id']}: Port {w['port']} [Egress: {w['proxy'] or 'DIRECT'}]")

    # 3. 检查/拉取并启动 gemini_web2api 实例
    web2api_script = os.path.join(BASE_DIR, "gemini_web2api.py")
    fetch_latest_upstream(web2api_script, force=args.update)

    if not os.path.exists(web2api_script):
        print(f"\n[Notice] {web2api_script} not found in root.")
        print("  Place your `gemini_web2api.py` into this folder to automatically launch workers,")
        print("  or start your upstream servers independently on the ports listed above.")
    else:
        for w in workers:
            wid = w["id"]
            wport = w["port"]
            wdir = os.path.join(BASE_DIR, "instances", f"w{wid}")
            os.makedirs(wdir, exist_ok=True)

            w_cfg = {
                "port": wport,
                "api_keys": [],
                "cookie": "",
                "proxy": w["proxy"],
                "log_requests": False
            }
            with open(os.path.join(wdir, "config.json"), "w", encoding="utf-8") as f:
                json.dump(w_cfg, f, indent=2)

            log_file = open(os.path.join(BASE_DIR, f"worker_{wid}.log"), "w")
            p = subprocess.Popen([sys.executable, web2api_script, "--port", str(wport), "--config", os.path.join(wdir, "config.json")],
                                 cwd=wdir, stdout=log_file, stderr=log_file)
            PROCESSES.append(p)
            print(f"[Worker-{wid}] Started PID {p.pid} on port {wport}")

    # 4. 启动网关并展示访问信息
    print("\n" + "=" * 54)
    print(f"  🌟 gemflow Gateway Started: http://127.0.0.1:{args.port}")
    print("=" * 54)
    print("  API Entrypoint : http://127.0.0.1:" + str(args.port) + "/v1/chat/completions")
    print("  Models List    : http://127.0.0.1:" + str(args.port) + "/v1/models")
    print(f"  Debug Mode     : {'ENABLED' if (args.debug or os.environ.get('DEBUG', '').lower() in ('true', '1', 'yes')) else 'DISABLED'}")
    print("=" * 54 + "\n")

    gateway_script = os.path.join(BASE_DIR, "lb_gateway.py")
    cmd = [sys.executable, gateway_script, "--port", str(args.port), "--config", WORKERS_JSON]
    if args.debug or os.environ.get("DEBUG", "").lower() in ("true", "1", "yes"):
        cmd.append("--debug")

    p_gw = subprocess.Popen(cmd)
    PROCESSES.append(p_gw)
    p_gw.wait()


if __name__ == "__main__":
    main()
