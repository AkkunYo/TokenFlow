import os
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestMonorepoLayout(unittest.TestCase):
    def test_owned_files_live_under_their_component(self):
        required = (
            "services/gemflow/lb_gateway.py",
            "services/gemflow/gen_workers.py",
            "services/gemflow/run_local.py",
            "services/gemflow/start.sh",
            "services/gemflow/Dockerfile",
            "services/gemflow/tests/test_gateway.py",
            "services/gemflow/tests/test_gen_workers.py",
            "packages/egress/assign_worker_nodes.py",
            "packages/egress/mihomo_config.py",
            "packages/egress/mihomo.template.yaml",
            "packages/egress/vpngate_provider.py",
            "packages/egress/tests/test_node_assign.py",
            "apps/tokenflow/cpa_proxy_config.py",
            "apps/tokenflow/start.sh",
            "apps/tokenflow/config.example.yaml",
            "apps/tokenflow/tokenflow.sh",
            "apps/tokenflow/tests/test_cpa_proxy_config.py",
            "scripts/test_all.sh",
        )
        for relative in required:
            self.assertTrue(
                os.path.exists(os.path.join(ROOT, relative)),
                relative,
            )

    def test_root_contains_no_component_source_copies(self):
        duplicates = (
            "assign_worker_nodes.py",
            "cpa_proxy_config.py",
            "gen_workers.py",
            "lb_gateway.py",
            "mihomo_config.py",
            "mihomo.template.yaml",
            "run_local.py",
            "start.sh",
            "tokenflow.sh",
            "vpngate_provider.py",
            "tests/test_cpa_proxy_config.py",
            "tests/test_gateway.py",
            "tests/test_gen_workers.py",
            "tests/test_node_assign.py",
        )
        for relative in duplicates:
            self.assertFalse(
                os.path.exists(os.path.join(ROOT, relative)),
                relative,
            )

    def test_build_and_ci_use_component_paths(self):
        with open(os.path.join(ROOT, "Dockerfile"), encoding="utf-8") as handle:
            dockerfile = handle.read()
        with open(
            os.path.join(ROOT, ".github", "workflows", "docker-image.yml"),
            encoding="utf-8",
        ) as handle:
            workflow = handle.read()
        with open(os.path.join(ROOT, "install.sh"), encoding="utf-8") as handle:
            installer = handle.read()

        for source in (
            "services/gemflow/",
            "packages/egress/",
            "apps/tokenflow/",
        ):
            self.assertIn(source, dockerfile)
            self.assertIn(source, installer)

        self.assertIn("bash scripts/test_all.sh", workflow)
        self.assertIn("apps/tokenflow/cpa_proxy_config.py", workflow)


if __name__ == "__main__":
    unittest.main()
