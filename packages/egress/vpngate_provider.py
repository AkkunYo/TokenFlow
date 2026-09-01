#!/usr/bin/env python3
"""
VPNGate to Mihomo (Clash Meta) Proxy-Provider / Node Converter
抓取官方 vpngate.net 免费节点，转换生成 Mihomo 兼容的标准 proxy 节点与 provider yaml。
"""
from __future__ import annotations

import base64
import csv
import io
import json
import logging
import os
import re
import sys
import urllib.request
from typing import Any, Dict, List

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [VPNGate] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("vpngate_converter")

VPNGATE_API_URLS = [
    "https://www.vpngate.net/api/iphone/",
    "http://www.vpngate.net/api/iphone/",
]


def fetch_vpngate_csv() -> str:
    for url in VPNGATE_API_URLS:
        try:
            logger.info("Fetching VPNGate server list from: %s", url)
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                },
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = resp.read().decode("utf-8", errors="ignore")
                if "OpenVPN_ConfigData_Base64" in data:
                    logger.info("Successfully fetched VPNGate data (%d bytes)", len(data))
                    return data
        except Exception as e:
            logger.warning("Failed to fetch from %s: %s", url, e)
    return ""


def parse_vpngate_nodes(csv_raw: str, max_nodes: int = 30) -> List[Dict[str, Any]]:
    nodes: List[Dict[str, Any]] = []
    if not csv_raw:
        return nodes

    lines = [
        line
        for line in csv_raw.splitlines()
        if line and not line.startswith("*")
    ]
    if not lines:
        return nodes

    reader = csv.reader(io.StringIO("\n".join(lines)))
    header = next(reader, None)
    if not header:
        return nodes

    # 排序优选：按 Ping 升序、Score 降序
    rows = []
    for r in reader:
        if len(r) > 14 and r[14]:
            try:
                ping = int(r[3]) if r[3].isdigit() else 999
                score = int(r[2]) if r[2].isdigit() else 0
                rows.append((ping, -score, r))
            except Exception:
                continue

    rows.sort(key=lambda x: (x[0], x[1]))

    seen_ips = set()
    for _, _, r in rows:
        ip = r[1].strip()
        if not ip or ip in seen_ips:
            continue

        country = r[5].strip() or r[6].strip() or "Global"
        country_short = r[6].strip().upper() or "UN"
        hostname = r[0].strip()
        ovpn_b64 = r[14].strip()

        try:
            ovpn_text = base64.b64decode(ovpn_b64).decode("utf-8", errors="ignore")
        except Exception:
            continue

        remote_m = re.search(r"^remote\s+([\w\.-]+)\s+(\d+)", ovpn_text, re.MULTILINE)
        if not remote_m:
            continue
        server = remote_m.group(1)
        port = int(remote_m.group(2))

        proto_m = re.search(r"^proto\s+(\w+)", ovpn_text, re.MULTILINE)
        proto = (proto_m.group(1) if proto_m else "tcp").lower()
        if "tcp" in proto:
            proto_type = "tcp"
        else:
            proto_type = "udp"

        cipher_m = re.search(r"^cipher\s+(\S+)", ovpn_text, re.MULTILINE)
        cipher = cipher_m.group(1) if cipher_m else "AES-128-CBC"

        auth_m = re.search(r"^auth\s+(\S+)", ovpn_text, re.MULTILINE)
        auth = auth_m.group(1) if auth_m else "SHA1"

        ca_m = re.search(r"<ca>(.*?)</ca>", ovpn_text, re.DOTALL)
        cert_m = re.search(r"<cert>(.*?)</cert>", ovpn_text, re.DOTALL)
        key_m = re.search(r"<key>(.*?)</key>", ovpn_text, re.DOTALL)

        ca_str = ca_m.group(1).strip() if ca_m else ""
        cert_str = cert_m.group(1).strip() if cert_m else ""
        key_str = key_m.group(1).strip() if key_m else ""

        if not ca_str:
            continue

        node_name = f"🌐 {country_short}-{ip}-{port}"
        node_cfg: Dict[str, Any] = {
            "name": node_name,
            "type": "openvpn",
            "server": server,
            "port": port,
            "ip": ip,
            "proto": proto_type,
            "cipher": cipher,
            "auth": auth,
            "auth-user-pass": {
                "user": "vpn",
                "pass": "vpn",
            },
            "ca": ca_str,
            "cert": cert_str,
            "key": key_str,
        }

        nodes.append(node_cfg)
        seen_ips.add(ip)
        if len(nodes) >= max_nodes:
            break

    logger.info("Parsed %d valid openvpn nodes from VPNGate", len(nodes))
    return nodes


def generate_vpngate_provider_yaml(output_path: str, max_nodes: int = 30) -> bool:
    csv_raw = fetch_vpngate_csv()
    if not csv_raw:
        return False
    nodes = parse_vpngate_nodes(csv_raw, max_nodes=max_nodes)
    if not nodes:
        return False

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    try:
        import yaml  # type: ignore
        with open(output_path, "w", encoding="utf-8") as f:
            yaml.safe_dump({"proxies": nodes}, f, allow_unicode=True, sort_keys=False)
        logger.info("Wrote %d nodes to %s", len(nodes), output_path)
        return True
    except ImportError:
        # 兜底 json/yaml 序列化
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("proxies:\n")
            for node in nodes:
                f.write(f"  - name: \"{node['name']}\"\n")
                f.write(f"    type: {node['type']}\n")
                f.write(f"    server: \"{node['server']}\"\n")
                f.write(f"    port: {node['port']}\n")
                f.write(f"    proto: {node['proto']}\n")
                f.write(f"    cipher: {node['cipher']}\n")
                f.write(f"    auth: {node['auth']}\n")
                f.write("    auth-user-pass:\n")
                f.write("      user: \"vpn\"\n")
                f.write("      pass: \"vpn\"\n")
                f.write("    ca: |\n")
                for line in node["ca"].splitlines():
                    f.write(f"      {line}\n")
                if node.get("cert"):
                    f.write("    cert: |\n")
                    for line in node["cert"].splitlines():
                        f.write(f"      {line}\n")
                if node.get("key"):
                    f.write("    key: |\n")
                    for line in node["key"].splitlines():
                        f.write(f"      {line}\n")
        logger.info("Fallback wrote %d nodes to %s", len(nodes), output_path)
        return True


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/vpngate_provider.yaml"
    success = generate_vpngate_provider_yaml(out)
    sys.exit(0 if success else 1)
