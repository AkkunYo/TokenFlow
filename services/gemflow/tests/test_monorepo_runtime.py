import os
import sys
import unittest


SERVICE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(SERVICE_DIR))
EGRESS_DIR = os.path.join(ROOT, "packages", "egress")

sys.path.insert(0, SERVICE_DIR)
sys.path.insert(0, EGRESS_DIR)

import run_local


class TestMonorepoRuntimePaths(unittest.TestCase):
    def test_run_local_uses_egress_template_from_monorepo(self):
        expected = os.path.realpath(
            os.path.join(EGRESS_DIR, "mihomo.template.yaml")
        )
        self.assertEqual(os.path.realpath(run_local.TEMPLATE_YAML), expected)
        self.assertTrue(os.path.exists(run_local.TEMPLATE_YAML))


if __name__ == "__main__":
    unittest.main()
