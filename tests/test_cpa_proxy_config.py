import copy
import io
import os
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cpa_proxy_config


def _config(*providers):
    return {"port": 18317, "openai-compatibility": list(providers)}


def _provider(name="nvidia", base_url="https://integrate.api.nvidia.com/v1",
              entries=None):
    return {
        "name": name,
        "base-url": base_url,
        "api-key-entries": entries or [],
        "models": [{"name": "meta/llama", "alias": ""}],
    }


class TestAssignNvidiaSocksProxies(unittest.TestCase):
    def test_assigns_each_nvidia_key_round_robin(self):
        source = _config(_provider(entries=[
            {"api-key": "nvapi-1"},
            {"api-key": "nvapi-2"},
            {"api-key": "nvapi-3"},
        ]))

        rendered, stats = cpa_proxy_config.assign_nvidia_socks_proxies(
            source,
            ["socks5://127.0.0.1:19001", "socks5://127.0.0.1:19002"],
        )

        entries = rendered["openai-compatibility"][0]["api-key-entries"]
        self.assertEqual(
            [entry["proxy-url"] for entry in entries],
            [
                "socks5://127.0.0.1:19001",
                "socks5://127.0.0.1:19002",
                "socks5://127.0.0.1:19001",
            ],
        )
        self.assertEqual(stats["matched_providers"], 1)
        self.assertEqual(stats["eligible_keys"], 3)
        self.assertEqual(stats["assigned_keys"], 3)
        self.assertEqual(stats["preserved_keys"], 0)

    def test_scales_round_robin_assignment_to_hundreds_of_keys(self):
        entries = [{"api-key": f"nvapi-test-{index}"} for index in range(855)]
        source = _config(_provider(entries=entries))
        proxy_urls = cpa_proxy_config.build_proxy_urls("127.0.0.1", 19000, 4)

        rendered, stats = cpa_proxy_config.assign_nvidia_socks_proxies(
            source,
            proxy_urls,
        )

        assigned = rendered["openai-compatibility"][0]["api-key-entries"]
        self.assertEqual(stats["assigned_keys"], 855)
        self.assertEqual(assigned[0]["proxy-url"], proxy_urls[0])
        self.assertEqual(assigned[854]["proxy-url"], proxy_urls[2])

    def test_detects_nvidia_from_base_url_when_name_is_custom(self):
        source = _config(_provider(
            name="third-party-gpu",
            base_url="https://integrate.api.nvidia.com/v1",
            entries=[{"api-key": "nvapi-1"}],
        ))

        rendered, stats = cpa_proxy_config.assign_nvidia_socks_proxies(
            source, ["socks5://127.0.0.1:19001"]
        )

        entry = rendered["openai-compatibility"][0]["api-key-entries"][0]
        self.assertEqual(entry["proxy-url"], "socks5://127.0.0.1:19001")
        self.assertEqual(stats["matched_providers"], 1)

    def test_leaves_non_nvidia_providers_unchanged(self):
        source = _config(_provider(
            name="gemflow",
            base_url="http://127.0.0.1:8081/v1",
            entries=[{"api-key": "internal"}],
        ))
        before = copy.deepcopy(source)

        rendered, stats = cpa_proxy_config.assign_nvidia_socks_proxies(
            source, ["socks5://127.0.0.1:19001"]
        )

        self.assertEqual(rendered, before)
        self.assertEqual(stats["matched_providers"], 0)
        self.assertEqual(stats["assigned_keys"], 0)

    def test_preserves_explicit_proxy_and_keeps_stable_rotation_slots(self):
        source = _config(_provider(entries=[
            {"api-key": "nvapi-1", "proxy-url": "socks5://custom:1080"},
            {"api-key": "nvapi-2"},
            {"api-key": "nvapi-3"},
        ]))

        rendered, stats = cpa_proxy_config.assign_nvidia_socks_proxies(
            source,
            ["socks5://127.0.0.1:19001", "socks5://127.0.0.1:19002"],
        )

        entries = rendered["openai-compatibility"][0]["api-key-entries"]
        self.assertEqual(entries[0]["proxy-url"], "socks5://custom:1080")
        self.assertEqual(entries[1]["proxy-url"], "socks5://127.0.0.1:19002")
        self.assertEqual(entries[2]["proxy-url"], "socks5://127.0.0.1:19001")
        self.assertEqual(stats["assigned_keys"], 2)
        self.assertEqual(stats["preserved_keys"], 1)

    def test_can_override_existing_proxy_explicitly(self):
        source = _config(_provider(entries=[
            {"api-key": "nvapi-1", "proxy-url": "socks5://custom:1080"},
        ]))

        rendered, stats = cpa_proxy_config.assign_nvidia_socks_proxies(
            source,
            ["socks5://127.0.0.1:19001"],
            override_existing=True,
        )

        entry = rendered["openai-compatibility"][0]["api-key-entries"][0]
        self.assertEqual(entry["proxy-url"], "socks5://127.0.0.1:19001")
        self.assertEqual(stats["assigned_keys"], 1)
        self.assertEqual(stats["preserved_keys"], 0)

    def test_does_not_mutate_source_config(self):
        source = _config(_provider(entries=[{"api-key": "nvapi-1"}]))
        before = copy.deepcopy(source)

        cpa_proxy_config.assign_nvidia_socks_proxies(
            source, ["socks5://127.0.0.1:19001"]
        )

        self.assertEqual(source, before)

    def test_rejects_empty_proxy_pool(self):
        with self.assertRaises(ValueError):
            cpa_proxy_config.assign_nvidia_socks_proxies(
                _config(_provider(entries=[{"api-key": "nvapi-1"}])),
                [],
            )

    def test_rejects_invalid_config_shapes_and_proxy_urls(self):
        with self.assertRaises(ValueError):
            cpa_proxy_config.assign_nvidia_socks_proxies(
                [], ["socks5://127.0.0.1:19001"]
            )
        with self.assertRaises(ValueError):
            cpa_proxy_config.assign_nvidia_socks_proxies(
                {"openai-compatibility": {}},
                ["socks5://127.0.0.1:19001"],
            )
        with self.assertRaises(ValueError):
            cpa_proxy_config.assign_nvidia_socks_proxies(
                _config(_provider(entries=[{"api-key": "nvapi-1"}])),
                ["http://127.0.0.1:19001"],
            )
        with self.assertRaises(ValueError):
            cpa_proxy_config.assign_nvidia_socks_proxies(
                _config(_provider(entries="not-a-list")),
                ["socks5://127.0.0.1:19001"],
            )

    def test_handles_empty_and_malformed_provider_entries(self):
        source = {
            "openai-compatibility": [
                None,
                _provider(entries=None),
                _provider(entries=[
                    "not-a-mapping",
                    {},
                    {"api-key": "  "},
                    {"api-key": "nvapi-valid"},
                ]),
            ]
        }

        rendered, stats = cpa_proxy_config.assign_nvidia_socks_proxies(
            source,
            ["socks5://127.0.0.1:19001"],
        )

        entries = rendered["openai-compatibility"][2]["api-key-entries"]
        self.assertEqual(entries[-1]["proxy-url"], "socks5://127.0.0.1:19001")
        self.assertEqual(stats["matched_providers"], 2)
        self.assertEqual(stats["eligible_keys"], 1)


class TestRuntimeConfig(unittest.TestCase):
    def test_build_proxy_urls_validates_and_builds_socks_pool(self):
        self.assertEqual(
            cpa_proxy_config.build_proxy_urls("127.0.0.1", 19000, 3),
            [
                "socks5://127.0.0.1:19001",
                "socks5://127.0.0.1:19002",
                "socks5://127.0.0.1:19003",
            ],
        )
        with self.assertRaises(ValueError):
            cpa_proxy_config.build_proxy_urls("127.0.0.1", 19000, 0)

    def test_build_proxy_urls_rejects_invalid_host_and_port_ranges(self):
        for host in ("", "bad host", "http://127.0.0.1", "host/path"):
            with self.assertRaises(ValueError):
                cpa_proxy_config.build_proxy_urls(host, 19000, 1)
        with self.assertRaises(ValueError):
            cpa_proxy_config.build_proxy_urls("127.0.0.1", "invalid", 1)
        with self.assertRaises(ValueError):
            cpa_proxy_config.build_proxy_urls("127.0.0.1", 65535, 1)

    def test_writes_private_runtime_copy_without_changing_source(self):
        source_data = _config(_provider(entries=[{"api-key": "nvapi-secret"}]))

        with tempfile.TemporaryDirectory() as directory:
            source_path = os.path.join(directory, "config.yaml")
            output_path = os.path.join(directory, "runtime", "config.yaml")
            with open(source_path, "w", encoding="utf-8") as handle:
                yaml.safe_dump(source_data, handle, sort_keys=False)

            stats = cpa_proxy_config.write_runtime_config(
                source_path,
                output_path,
                ["socks5://127.0.0.1:19001"],
            )

            with open(source_path, encoding="utf-8") as handle:
                self.assertNotIn("proxy-url", handle.read())
            with open(output_path, encoding="utf-8") as handle:
                rendered = yaml.safe_load(handle)

            entry = rendered["openai-compatibility"][0]["api-key-entries"][0]
            self.assertEqual(entry["proxy-url"], "socks5://127.0.0.1:19001")
            self.assertEqual(stat.S_IMODE(os.stat(output_path).st_mode), 0o600)
            self.assertEqual(stats["assigned_keys"], 1)

    def test_rejects_overwriting_source_and_accepts_empty_yaml(self):
        with tempfile.TemporaryDirectory() as directory:
            source_path = os.path.join(directory, "config.yaml")
            output_path = os.path.join(directory, "runtime.yaml")
            with open(source_path, "w", encoding="utf-8") as handle:
                handle.write("")

            with self.assertRaises(ValueError):
                cpa_proxy_config.write_runtime_config(
                    source_path,
                    source_path,
                    ["socks5://127.0.0.1:19001"],
                )

            stats = cpa_proxy_config.write_runtime_config(
                source_path,
                output_path,
                ["socks5://127.0.0.1:19001"],
            )
            self.assertEqual(stats["matched_providers"], 0)

    def test_cli_success_and_failure_do_not_print_api_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            source_path = os.path.join(directory, "config.yaml")
            output_path = os.path.join(directory, "runtime.yaml")
            secret = "nvapi-secret-value"
            with open(source_path, "w", encoding="utf-8") as handle:
                yaml.safe_dump(
                    _config(_provider(entries=[{"api-key": secret}])),
                    handle,
                    sort_keys=False,
                )

            output = io.StringIO()
            with mock.patch.object(sys, "argv", [
                "cpa_proxy_config.py",
                "--input", source_path,
                "--output", output_path,
                "--proxy-count", "2",
                "--provider-names", "nvidia,custom-nvidia",
            ]), redirect_stdout(output):
                self.assertEqual(cpa_proxy_config.main(), 0)
            self.assertIn("1 assigned key(s)", output.getvalue())
            self.assertNotIn(secret, output.getvalue())

            output = io.StringIO()
            with mock.patch.object(sys, "argv", [
                "cpa_proxy_config.py",
                "--input", source_path,
                "--output", output_path,
                "--proxy-count", "0",
            ]), redirect_stdout(output):
                self.assertEqual(cpa_proxy_config.main(), 1)
            self.assertIn("Failed to prepare runtime config", output.getvalue())


class TestStartupIntegration(unittest.TestCase):
    def test_startup_uses_derived_config_and_installer_packages_module(self):
        project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(project_dir, "start.sh"), encoding="utf-8") as handle:
            startup = handle.read()
        with open(os.path.join(project_dir, "install.sh"), encoding="utf-8") as handle:
            installer = handle.read()

        self.assertIn('python3 "$APP_DIR/cpa_proxy_config.py"', startup)
        self.assertIn('CPA_EFFECTIVE_CONFIG_FILE="$CPA_RUNTIME_CONFIG_FILE"', startup)
        self.assertIn(
            'cliproxy --config "$CPA_EFFECTIVE_CONFIG_FILE" run',
            startup,
        )
        self.assertIn("cpa_proxy_config.py", installer)


if __name__ == "__main__":
    unittest.main()
