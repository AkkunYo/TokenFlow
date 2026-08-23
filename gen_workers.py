#!/usr/bin/env python3
"""
Worker 配置生成器

生成 workers.json 与各实例的 instances/wN/config.json。

原先由 shell 逐行拼接 JSON 字符串（逗号、引号、null 全靠手工处理），
改由 json.dump 输出，避免转义错误产出畸形配置。
被 start.sh 调用；run_local.py 直接 import 纯函数复用同一套分配语义。
"""

import os
import json
import argparse

BASE_WORKER_PORT = 9000
BASE_PROXY_PORT = 19000


def compute_workers(worker_count, use_proxies=False, is_cn_host=False,
                    base_worker_port=BASE_WORKER_PORT,
                    base_proxy_port=BASE_PROXY_PORT):
    """
    计算 Worker 列表 (纯函数，不做 IO)。

    出口分配语义：启用代理时，宿主机在海外则 Worker-1 走原生直连
    （保留一条不依赖订阅的通路），其余 Worker 各自绑定专属代理端口；
    宿主机在国内则全部走代理。
    """
    if worker_count <= 0:
        raise ValueError(f"worker_count must be positive, got {worker_count}")

    workers = []
    for i in range(worker_count):
        wid = i + 1
        proxy = None
        if use_proxies and (i > 0 or is_cn_host):
            proxy = f"http://127.0.0.1:{base_proxy_port + wid}"
        workers.append({
            "id": wid,
            "port": base_worker_port + wid,
            "proxy": proxy,
        })
    return workers


def write_workers_json(workers, out_path):
    """写出 workers.json"""
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"workers": workers}, f, indent=2)
        f.write("\n")


def write_instance_configs(workers, app_dir):
    """为每个 Worker 生成独立工作目录与 config.json"""
    for w in workers:
        wdir = os.path.join(app_dir, "instances", f"w{w['id']}")
        os.makedirs(wdir, exist_ok=True)
        cfg = {
            "port": w["port"],
            "api_keys": [],
            "cookie": "",
            "proxy": w["proxy"],
            "log_requests": False,
        }
        with open(os.path.join(wdir, "config.json"), "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
            f.write("\n")


def _parse_bool(raw):
    return str(raw).strip().lower() in ("true", "1", "yes", "on")


def main():
    parser = argparse.ArgumentParser(description="Generate gemflow worker configs")
    parser.add_argument("--workers", type=int, required=True, help="Worker count")
    parser.add_argument("--out", required=True, help="workers.json output path")
    parser.add_argument("--app-dir", default=os.environ.get("APP_DIR", "/app"),
                        help="Application directory holding instances/")
    parser.add_argument("--use-proxies", default="false",
                        help="Whether mihomo proxies are available (true/false)")
    parser.add_argument("--cn-host", default="false",
                        help="Whether the host native IP is in China (true/false)")
    parser.add_argument("--base-worker-port", type=int, default=BASE_WORKER_PORT)
    parser.add_argument("--base-proxy-port", type=int, default=BASE_PROXY_PORT)
    args = parser.parse_args()

    try:
        workers = compute_workers(
            args.workers,
            use_proxies=_parse_bool(args.use_proxies),
            is_cn_host=_parse_bool(args.cn_host),
            base_worker_port=args.base_worker_port,
            base_proxy_port=args.base_proxy_port,
        )
        write_workers_json(workers, args.out)
        write_instance_configs(workers, args.app_dir)
    except (OSError, ValueError) as e:
        print(f"[Workers] Failed to generate configs: {e}", flush=True)
        return 1

    proxied = sum(1 for w in workers if w["proxy"])
    print(f"[Workers] Generated {len(workers)} worker config(s) "
          f"({proxied} proxied, {len(workers) - proxied} direct).", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
