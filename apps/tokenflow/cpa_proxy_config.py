#!/usr/bin/env python3
"""Build a private CPA runtime config with per-key NVIDIA SOCKS proxies."""

import argparse
import copy
import os
import tempfile
from urllib.parse import urlparse

import yaml


DEFAULT_PROVIDER_NAMES = ("nvidia",)
DEFAULT_PROXY_HOST = "127.0.0.1"
DEFAULT_PROXY_BASE_PORT = 19000


def build_proxy_urls(host, base_port, count):
    """Build the SOCKS listener pool exposed by Mihomo."""
    host = str(host).strip()
    if not host or any(char.isspace() for char in host):
        raise ValueError("proxy host must be a non-empty hostname or IP address")
    if "://" in host or "/" in host:
        raise ValueError("proxy host must not include a scheme or path")

    try:
        base_port = int(base_port)
        count = int(count)
    except (TypeError, ValueError) as exc:
        raise ValueError("proxy base port and count must be integers") from exc

    if count <= 0:
        raise ValueError(f"proxy count must be positive, got {count}")
    if base_port < 0 or base_port + count > 65535:
        raise ValueError(
            f"proxy port range must be within 1..65535, got "
            f"{base_port + 1}..{base_port + count}"
        )

    return [
        f"socks5://{host}:{base_port + index}"
        for index in range(1, count + 1)
    ]


def _normalize_provider_names(provider_names):
    names = provider_names or DEFAULT_PROVIDER_NAMES
    return {
        str(name).strip().lower()
        for name in names
        if str(name).strip()
    }


def _is_nvidia_provider(provider, provider_names):
    name = str(provider.get("name", "")).strip().lower()
    if name in provider_names:
        return True

    base_url = str(provider.get("base-url", "")).strip()
    hostname = (urlparse(base_url).hostname or "").lower()
    return hostname == "nvidia.com" or hostname.endswith(".nvidia.com")


def assign_nvidia_socks_proxies(config, proxy_urls, provider_names=None):
    """
    Return a deep-copied CPA config with NVIDIA keys assigned round-robin.

    Existing non-empty per-key proxy settings are always preserved. Only keys
    without a proxy participate in round-robin assignment.
    """
    if not isinstance(config, dict):
        raise ValueError("CPA config root must be a YAML mapping")

    proxy_pool = [str(url).strip() for url in proxy_urls if str(url).strip()]
    if not proxy_pool:
        raise ValueError("at least one SOCKS proxy URL is required")
    for proxy_url in proxy_pool:
        parsed = urlparse(proxy_url)
        if parsed.scheme.lower() != "socks5" or not parsed.hostname or not parsed.port:
            raise ValueError(f"invalid SOCKS5 proxy URL: {proxy_url}")

    names = _normalize_provider_names(provider_names)
    rendered = copy.deepcopy(config)
    providers = rendered.get("openai-compatibility", [])
    if providers is None:
        providers = []
    if not isinstance(providers, list):
        raise ValueError("openai-compatibility must be a YAML list")

    stats = {
        "matched_providers": 0,
        "eligible_keys": 0,
        "assigned_keys": 0,
        "preserved_keys": 0,
    }
    rotation_slot = 0

    for provider in providers:
        if not isinstance(provider, dict) or not _is_nvidia_provider(provider, names):
            continue

        stats["matched_providers"] += 1
        entries = provider.get("api-key-entries", [])
        if entries is None:
            entries = []
        if not isinstance(entries, list):
            raise ValueError(
                "NVIDIA provider api-key-entries must be a YAML list"
            )

        for entry in entries:
            if not isinstance(entry, dict):
                continue
            api_key = entry.get("api-key")
            if not isinstance(api_key, str) or not api_key.strip():
                continue

            stats["eligible_keys"] += 1

            existing = entry.get("proxy-url")
            if (
                isinstance(existing, str)
                and existing.strip()
            ):
                stats["preserved_keys"] += 1
                continue

            proxy_url = proxy_pool[rotation_slot % len(proxy_pool)]
            rotation_slot += 1
            entry["proxy-url"] = proxy_url
            stats["assigned_keys"] += 1

    return rendered, stats


def write_runtime_config(source_path, output_path, proxy_urls,
                         provider_names=None):
    """Safely write a mode-0600 derived config without modifying the source."""
    source_real = os.path.realpath(source_path)
    output_real = os.path.realpath(output_path)
    if source_real == output_real:
        raise ValueError("runtime output path must differ from source config path")

    with open(source_path, "r", encoding="utf-8") as handle:
        source = yaml.safe_load(handle)
    if source is None:
        source = {}

    rendered, stats = assign_nvidia_socks_proxies(
        source,
        proxy_urls,
        provider_names=provider_names,
    )

    output_dir = os.path.dirname(output_real) or "."
    os.makedirs(output_dir, exist_ok=True)
    descriptor, temporary_path = tempfile.mkstemp(
        prefix=".cpa-runtime-",
        suffix=".yaml",
        dir=output_dir,
        text=True,
    )
    try:
        os.chmod(temporary_path, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            yaml.safe_dump(
                rendered,
                handle,
                allow_unicode=True,
                sort_keys=False,
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, output_real)
        os.chmod(output_real, 0o600)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            os.unlink(temporary_path)
        except OSError:
            pass
        raise

    return stats


def _parse_provider_names(raw):
    return [
        name.strip()
        for name in str(raw or "").split(",")
        if name.strip()
    ]


def main():
    parser = argparse.ArgumentParser(
        description="Assign Mihomo SOCKS listeners to NVIDIA CPA API keys"
    )
    parser.add_argument("--input", required=True, help="Source CPA YAML config")
    parser.add_argument("--output", required=True, help="Derived runtime YAML config")
    parser.add_argument("--proxy-host", default=DEFAULT_PROXY_HOST)
    parser.add_argument(
        "--proxy-base-port",
        type=int,
        default=DEFAULT_PROXY_BASE_PORT,
    )
    parser.add_argument("--proxy-count", type=int, required=True)
    parser.add_argument(
        "--provider-names",
        default=",".join(DEFAULT_PROVIDER_NAMES),
        help="Comma-separated provider names treated as NVIDIA",
    )
    args = parser.parse_args()

    try:
        proxy_urls = build_proxy_urls(
            args.proxy_host,
            args.proxy_base_port,
            args.proxy_count,
        )
        stats = write_runtime_config(
            args.input,
            args.output,
            proxy_urls,
            provider_names=_parse_provider_names(args.provider_names),
        )
    except Exception as exc:
        print(f"[NVIDIA Proxy] Failed to prepare runtime config: {exc}", flush=True)
        return 1

    print(
        "[NVIDIA Proxy] Runtime config ready: "
        f"{stats['matched_providers']} provider(s), "
        f"{stats['assigned_keys']} assigned key(s), "
        f"{stats['preserved_keys']} preserved explicit proxy setting(s).",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
