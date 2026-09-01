import os
import sys
import json
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gen_workers


class TestComputeWorkers(unittest.TestCase):
    def test_direct_only_mode(self):
        workers = gen_workers.compute_workers(3, use_proxies=False)
        self.assertEqual([w["proxy"] for w in workers], [None, None, None])
        self.assertEqual([w["id"] for w in workers], [1, 2, 3])
        self.assertEqual([w["port"] for w in workers], [9001, 9002, 9003])

    def test_overseas_host_keeps_worker1_direct(self):
        """海外宿主机：Worker-1 保留原生直连，其余走代理"""
        workers = gen_workers.compute_workers(3, use_proxies=True, is_cn_host=False)
        self.assertIsNone(workers[0]["proxy"])
        self.assertEqual(workers[1]["proxy"], "http://127.0.0.1:19002")
        self.assertEqual(workers[2]["proxy"], "http://127.0.0.1:19003")

    def test_cn_host_routes_all_through_proxy(self):
        """国内宿主机：包括 Worker-1 在内全部走代理"""
        workers = gen_workers.compute_workers(3, use_proxies=True, is_cn_host=True)
        self.assertEqual([w["proxy"] for w in workers],
                         ["http://127.0.0.1:19001", "http://127.0.0.1:19002",
                          "http://127.0.0.1:19003"])

    def test_single_worker_overseas_has_no_proxy_even_with_proxies_enabled(self):
        workers = gen_workers.compute_workers(1, use_proxies=True, is_cn_host=False)
        self.assertIsNone(workers[0]["proxy"])

    def test_custom_base_ports(self):
        workers = gen_workers.compute_workers(
            2, base_worker_port=5000, base_proxy_port=6000,
            use_proxies=True, is_cn_host=True)
        self.assertEqual(workers[0]["port"], 5001)
        self.assertEqual(workers[0]["proxy"], "http://127.0.0.1:6001")

    def test_rejects_non_positive_count(self):
        with self.assertRaises(ValueError):
            gen_workers.compute_workers(0)
        with self.assertRaises(ValueError):
            gen_workers.compute_workers(-1)


class TestWriteWorkersJson(unittest.TestCase):
    def test_writes_valid_json_matching_lb_gateway_schema(self):
        workers = gen_workers.compute_workers(2, use_proxies=True, is_cn_host=True)
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "workers.json")
            gen_workers.write_workers_json(workers, out)
            with open(out, encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(data["workers"], workers)

    def test_survives_special_characters_that_broke_shell_concat(self):
        """
        原 shell 实现靠字符串拼接生成 JSON，proxy URL 含引号/反斜杠会破坏结构。
        json.dump 不受此限制。
        """
        workers = [{"id": 1, "port": 9001, "proxy": 'http://x/a"b\\c?q=1&r=2'}]
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "workers.json")
            gen_workers.write_workers_json(workers, out)
            with open(out, encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(data["workers"][0]["proxy"], workers[0]["proxy"])


class TestWriteInstanceConfigs(unittest.TestCase):
    def test_creates_one_config_per_worker(self):
        workers = gen_workers.compute_workers(3, use_proxies=True, is_cn_host=False)
        with tempfile.TemporaryDirectory() as d:
            gen_workers.write_instance_configs(workers, d)
            for w in workers:
                cfg_path = os.path.join(d, "instances", f"w{w['id']}", "config.json")
                self.assertTrue(os.path.exists(cfg_path))
                with open(cfg_path, encoding="utf-8") as f:
                    cfg = json.load(f)
                self.assertEqual(cfg["port"], w["port"])
                self.assertEqual(cfg["proxy"], w["proxy"])
                self.assertEqual(cfg["cookie"], "")
                self.assertEqual(cfg["api_keys"], [])
                self.assertFalse(cfg["log_requests"])


class TestParseBool(unittest.TestCase):
    def test_recognizes_common_truthy_strings(self):
        for v in ("true", "True", "1", "yes", "on", " TRUE "):
            self.assertTrue(gen_workers._parse_bool(v))

    def test_rejects_falsy_or_garbage_strings(self):
        for v in ("false", "0", "no", "", "off", "banana"):
            self.assertFalse(gen_workers._parse_bool(v))


if __name__ == "__main__":
    unittest.main()
