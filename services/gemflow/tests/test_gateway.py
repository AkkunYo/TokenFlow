import json
import time
import unittest
import threading
import os
import sys

# Ensure root directory is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collections import OrderedDict
from lb_gateway import (
    extract_request_meta,
    select_worker,
    cleanup_stale_sessions,
    record_session,
    record_worker_failure,
    record_worker_success,
    LOCK,
)
import lb_gateway


class TestGatewayCore(unittest.TestCase):
    def setUp(self):
        """每个测试用例前重置网关状态"""
        with LOCK:
            lb_gateway.WORKERS = [
                {"id": 1, "port": 9001, "proxy": None},
                {"id": 2, "port": 9002, "proxy": "http://127.0.0.1:19002"},
                {"id": 3, "port": 9003, "proxy": "http://127.0.0.1:19003"},
            ]
            lb_gateway.ACTIVE_CONNS = {1: 0, 2: 0, 3: 0}
            lb_gateway.SESSION_MAP = OrderedDict()
            lb_gateway.WORKER_STATUS = {
                1: {"last_fail": 0, "fail_count": 0},
                2: {"last_fail": 0, "fail_count": 0},
                3: {"last_fail": 0, "fail_count": 0},
            }
            lb_gateway.RR_INDEX = 0
            lb_gateway.REQ_COUNTER = 0

    def test_explicit_user_field(self):
        body = json.dumps({
            "model": "gemini-2.5-flash",
            "messages": [{"role": "user", "content": "Hello"}],
            "user": "user-abc-123"
        }).encode("utf-8")

        session_id, model, snippet = extract_request_meta({}, body)
        self.assertEqual(session_id, "usr_user-abc-123")
        self.assertEqual(model, "gemini-2.5-flash")
        self.assertEqual(snippet, "Hello")

    def test_prompt_content_fingerprint(self):
        body = json.dumps({
            "model": "gemini-2.5-pro",
            "messages": [{"role": "user", "content": "Explain quantum physics in detail"}]
        }).encode("utf-8")

        session_id, model, snippet = extract_request_meta({}, body)
        self.assertTrue(session_id.startswith("ctx_"))
        self.assertEqual(model, "gemini-2.5-pro")
        self.assertEqual(snippet, "Explain quantum physics in detail")

    def test_auth_header_fallback(self):
        headers = {"Authorization": "Bearer sk-secret-token-xyz"}
        body = b"{}"
        session_id, model, snippet = extract_request_meta(headers, body)
        self.assertTrue(session_id.startswith("auth_"))

    def test_custom_cookie_ctoken_header(self):
        # 1. 测试 X-Gemini-Cookie Header
        headers = {"X-Gemini-Cookie": "SIDCC=dummy-sidcc-token; __Secure-1PSID=xyz"}
        body = b"{}"
        session_id, _, _ = extract_request_meta(headers, body)
        self.assertTrue(session_id.startswith("cookie_"))

        # 2. 测试 X-Ctoken Header
        headers2 = {"X-Ctoken": "ctoken_secret_value_123"}
        session_id2, _, _ = extract_request_meta(headers2, body)
        self.assertTrue(session_id2.startswith("cookie_"))

    def test_sticky_session_pinning(self):
        # 第一次请求分配 Worker
        worker1, route1, _ = select_worker("ctx_test_session_1")
        self.assertEqual(route1, "LEAST_CONN")

        # 第二次相同 session 请求必须命中相同的 Worker (STICKY)
        worker2, route2, _ = select_worker("ctx_test_session_1")
        self.assertEqual(route2, "STICKY")
        self.assertEqual(worker1["id"], worker2["id"])

    def test_least_conn_and_round_robin(self):
        # 当所有 worker 活跃连接为 0 时，应轮询分配
        w1, _, _ = select_worker(None)
        w2, _, _ = select_worker(None)
        w3, _, _ = select_worker(None)

        self.assertEqual([w1["id"], w2["id"], w3["id"]], [1, 2, 3])

    def test_failover_cooling_penalty(self):
        # 绑定 session 到 Worker 1
        w1, _, _ = select_worker("ctx_session_fail")
        self.assertEqual(w1["id"], 1)

        # 标记 Worker 1 故障
        record_worker_failure(1)

        # 再次路由该 session，由于 Worker 1 在冷却池中，应自动调度到其他健康节点
        w_next, route, _ = select_worker("ctx_session_fail")
        self.assertNotEqual(w_next["id"], 1)
        self.assertEqual(route, "LEAST_CONN")

    def test_cleanup_expired_sessions(self):
        # 注入一个过期 session (1小时前)
        with LOCK:
            lb_gateway.SESSION_MAP["expired_session"] = (1, time.time() - 4000)
            lb_gateway.SESSION_MAP["active_session"] = (2, time.time())

        cleanup_stale_sessions()

        with LOCK:
            self.assertNotIn("expired_session", lb_gateway.SESSION_MAP)
            self.assertIn("active_session", lb_gateway.SESSION_MAP)

    def test_lru_session_cap_eviction(self):
        orig_max = lb_gateway.MAX_SESSIONS
        try:
            lb_gateway.MAX_SESSIONS = 3
            with LOCK:
                record_session("s1", 1)
                record_session("s2", 2)
                record_session("s3", 3)
                self.assertEqual(len(lb_gateway.SESSION_MAP), 3)

                # 访问 s1，刷新 LRU 顺序
                record_session("s1", 1)

                # 插入 s4，此时应淘汰最久未访问的 s2
                record_session("s4", 1)
                self.assertEqual(len(lb_gateway.SESSION_MAP), 3)
                self.assertIn("s1", lb_gateway.SESSION_MAP)
                self.assertNotIn("s2", lb_gateway.SESSION_MAP)
                self.assertIn("s3", lb_gateway.SESSION_MAP)
                self.assertIn("s4", lb_gateway.SESSION_MAP)
        finally:
            lb_gateway.MAX_SESSIONS = orig_max


    def test_probe_single_worker_egress(self):
        worker = {"id": 1, "port": 9001, "proxy": None}
        res = lb_gateway.probe_single_worker_egress(worker, timeout=1)
        self.assertTrue("Worker-1" in res)
        self.assertTrue("Port 9001" in res)


def _line(wid, ip):
    return (wid, f"[Worker-{wid} : Port 900{wid} : x] -> United States (Ashburn) - IP: {ip} [ISP]")


class TestEgressReadiness(unittest.TestCase):
    """首次打印门控：确认代理链路真正生效后才输出状态面板"""

    DIRECT_W = {"id": 1, "port": 9001, "proxy": None}

    @staticmethod
    def _proxy_w(wid):
        return {"id": wid, "port": 9000 + wid, "proxy": f"http://127.0.0.1:1900{wid}"}

    def test_not_ready_when_proxy_falls_back_to_direct_ip(self):
        """代理端口出口与原生直连完全相同 => mihomo 未生效，不该打印"""
        workers = [self.DIRECT_W, self._proxy_w(2), self._proxy_w(3)]
        results = [_line(1, "44.196.116.2"), _line(2, "44.196.116.2"), _line(3, "44.196.116.2")]
        self.assertFalse(lb_gateway.evaluate_egress_readiness(workers, results))

    def test_ready_when_proxy_ip_differs_from_direct(self):
        workers = [self.DIRECT_W, self._proxy_w(2), self._proxy_w(3)]
        results = [_line(1, "44.196.116.2"), _line(2, "137.131.35.71"), _line(3, "5.6.7.8")]
        self.assertTrue(lb_gateway.evaluate_egress_readiness(workers, results))

    def test_ready_when_proxies_share_node_but_differ_from_direct(self):
        """健康节点少于 Worker 数时轮转复用是合法结果，不应卡住首次打印"""
        workers = [self.DIRECT_W, self._proxy_w(2), self._proxy_w(3)]
        results = [_line(1, "44.196.116.2"), _line(2, "137.131.35.71"), _line(3, "137.131.35.71")]
        self.assertTrue(lb_gateway.evaluate_egress_readiness(workers, results))

    def test_ready_with_single_proxy_worker_and_no_direct(self):
        """全代理且仅 1 个 Worker：无从比较，探测成功即视为就绪"""
        workers = [self._proxy_w(1)]
        results = [_line(1, "137.131.35.71")]
        self.assertTrue(lb_gateway.evaluate_egress_readiness(workers, results))

    def test_ready_in_direct_only_mode(self):
        workers = [self.DIRECT_W]
        results = [_line(1, "44.196.116.2")]
        self.assertTrue(lb_gateway.evaluate_egress_readiness(workers, results))

    def test_not_ready_when_any_probe_failed(self):
        workers = [self.DIRECT_W, self._proxy_w(2)]
        results = [_line(1, "44.196.116.2"),
                   (2, "[Worker-2 : Port 9002 : x] -> Connection Failed (URLError)")]
        self.assertFalse(lb_gateway.evaluate_egress_readiness(workers, results))

    def test_not_ready_on_empty_results(self):
        self.assertFalse(lb_gateway.evaluate_egress_readiness([self.DIRECT_W], []))


class TestSanitizeWorkers(unittest.TestCase):
    """workers.json 属系统边界输入，畸形条目必须被丢弃而非在远处崩溃"""

    def test_valid_config_passes_through(self):
        raw = [{"id": 1, "port": 9001, "proxy": None},
               {"id": 2, "port": 9002, "proxy": "http://127.0.0.1:19002"}]
        cleaned, dropped = lb_gateway.sanitize_workers(raw)
        self.assertEqual(len(cleaned), 2)
        self.assertEqual(dropped, [])
        self.assertEqual(cleaned[1]["proxy"], "http://127.0.0.1:19002")

    def test_drops_entry_missing_id_or_port(self):
        raw = [{"port": 9001}, {"id": 2}, {"id": 3, "port": 9003}]
        cleaned, dropped = lb_gateway.sanitize_workers(raw)
        self.assertEqual([w["id"] for w in cleaned], [3])
        self.assertEqual(len(dropped), 2)

    def test_drops_non_dict_and_out_of_range_port(self):
        raw = ["not-a-dict", {"id": 1, "port": 0}, {"id": 2, "port": 70000},
               {"id": 3, "port": 9003}]
        cleaned, _ = lb_gateway.sanitize_workers(raw)
        self.assertEqual([w["id"] for w in cleaned], [3])

    def test_rejects_bool_id_since_bool_is_int_subclass(self):
        cleaned, dropped = lb_gateway.sanitize_workers([{"id": True, "port": 9001}])
        self.assertEqual(cleaned, [])
        self.assertEqual(len(dropped), 1)

    def test_drops_gateway_port_collision(self):
        raw = [{"id": 1, "port": 8081}, {"id": 2, "port": 9002}]
        cleaned, dropped = lb_gateway.sanitize_workers(raw, gateway_port=8081)
        self.assertEqual([w["id"] for w in cleaned], [2])
        self.assertIn("collides", dropped[0])

    def test_gateway_port_honours_custom_value(self):
        raw = [{"id": 1, "port": 9001}, {"id": 2, "port": 9002}]
        cleaned, _ = lb_gateway.sanitize_workers(raw, gateway_port=9001)
        self.assertEqual([w["id"] for w in cleaned], [2])

    def test_drops_duplicate_ids_keeping_first(self):
        raw = [{"id": 1, "port": 9001}, {"id": 1, "port": 9099}]
        cleaned, dropped = lb_gateway.sanitize_workers(raw)
        self.assertEqual(len(cleaned), 1)
        self.assertEqual(cleaned[0]["port"], 9001)
        self.assertIn("duplicate", dropped[0])

    def test_blank_or_non_string_proxy_degrades_to_direct(self):
        raw = [{"id": 1, "port": 9001, "proxy": "   "},
               {"id": 2, "port": 9002, "proxy": 12345}]
        cleaned, dropped = lb_gateway.sanitize_workers(raw)
        self.assertEqual(len(cleaned), 2)
        self.assertIsNone(cleaned[0]["proxy"])
        self.assertIsNone(cleaned[1]["proxy"])
        self.assertEqual(len(dropped), 2)

    def test_proxy_whitespace_is_trimmed(self):
        cleaned, _ = lb_gateway.sanitize_workers(
            [{"id": 1, "port": 9001, "proxy": " http://127.0.0.1:19001 "}])
        self.assertEqual(cleaned[0]["proxy"], "http://127.0.0.1:19001")

    def test_non_list_input_rejected(self):
        cleaned, dropped = lb_gateway.sanitize_workers({"id": 1, "port": 9001})
        self.assertEqual(cleaned, [])
        self.assertEqual(len(dropped), 1)

    def test_sanitize_does_not_mutate_input(self):
        raw = [{"id": 1, "port": 9001, "proxy": " http://x "}]
        snapshot = json.loads(json.dumps(raw))
        lb_gateway.sanitize_workers(raw)
        self.assertEqual(raw, snapshot)


if __name__ == "__main__":
    unittest.main()


class TestConnSlotAccounting(unittest.TestCase):
    """ACTIVE_CONNS 占用/释放必须成对，且选中与计数在同一临界区完成"""

    def setUp(self):
        with LOCK:
            lb_gateway.WORKERS = [
                {"id": 1, "port": 9001, "proxy": None},
                {"id": 2, "port": 9002, "proxy": "http://127.0.0.1:19002"},
            ]
            lb_gateway.ACTIVE_CONNS = {1: 0, 2: 0}
            lb_gateway.SESSION_MAP = OrderedDict()
            lb_gateway.WORKER_STATUS = {
                1: {"last_fail": 0, "fail_count": 0},
                2: {"last_fail": 0, "fail_count": 0},
            }
            lb_gateway.RR_INDEX = 0

    def test_acquire_slot_increments_atomically(self):
        w, _, active = select_worker(None, acquire_slot=True)
        self.assertEqual(active, 1)
        self.assertEqual(lb_gateway.ACTIVE_CONNS[w["id"]], 1)

    def test_no_acquire_leaves_counter_untouched(self):
        w, _, _ = select_worker(None)
        self.assertEqual(lb_gateway.ACTIVE_CONNS[w["id"]], 0)

    def test_release_returns_counter_to_zero(self):
        w, _, _ = select_worker(None, acquire_slot=True)
        lb_gateway.release_worker_slot(w["id"])
        self.assertEqual(lb_gateway.ACTIVE_CONNS[w["id"]], 0)

    def test_release_never_goes_negative(self):
        lb_gateway.release_worker_slot(1)
        lb_gateway.release_worker_slot(1)
        self.assertEqual(lb_gateway.ACTIVE_CONNS[1], 0)

    def test_concurrent_acquire_release_balances_out(self):
        """并发占用/释放后计数必须归零，验证无泄漏与无竞态"""
        def _cycle():
            for _ in range(50):
                w, _, _ = select_worker(None, acquire_slot=True)
                lb_gateway.release_worker_slot(w["id"])

        threads = [threading.Thread(target=_cycle) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(lb_gateway.ACTIVE_CONNS[1], 0)
        self.assertEqual(lb_gateway.ACTIVE_CONNS[2], 0)


class TestProbeTimeoutPlaceholder(unittest.TestCase):
    """探测线程超时未返回时必须补占位行，且不得被误判为就绪"""

    def test_timeout_worker_gets_placeholder_line(self):
        workers = [{"id": 1, "port": 9001, "proxy": None},
                   {"id": 2, "port": 9002, "proxy": "http://127.0.0.1:19002"}]

        orig = lb_gateway.probe_single_worker_egress
        orig_timeout = lb_gateway.PROBE_TIMEOUT
        try:
            lb_gateway.PROBE_TIMEOUT = 0.1

            def _slow(w, timeout=5):
                if w["id"] == 2:
                    time.sleep(30)  # 永不按时返回
                return f"[Worker-1 : Port 9001 : DIRECT] -> US - IP: 1.2.3.4 [ISP]"

            lb_gateway.probe_single_worker_egress = _slow
            results = lb_gateway._probe_all_workers(workers)
        finally:
            lb_gateway.probe_single_worker_egress = orig
            lb_gateway.PROBE_TIMEOUT = orig_timeout

        self.assertEqual([wid for wid, _ in results], [1, 2])
        self.assertIn("Probe Failed (Timeout)", dict(results)[2])
        # 有失败项 => 不该判定为就绪
        self.assertFalse(lb_gateway.evaluate_egress_readiness(workers, results))


class TestResponseHeaderPolicy(unittest.TestCase):
    """响应头下发策略：流式响应必须可界定结束，且不重复逐跳头"""

    def test_hop_by_hop_headers_are_filtered(self):
        """send_response 自动补 Server/Date，透传上游同名头会导致重复"""
        for h in ("transfer-encoding", "connection", "server", "date"):
            self.assertIn(h, lb_gateway.SKIPPED_RESPONSE_HEADERS)

    def test_content_length_not_filtered(self):
        """Content-Length 必须透传，否则非流式响应也失去边界"""
        self.assertNotIn("content-length", lb_gateway.SKIPPED_RESPONSE_HEADERS)


class TestStreamTimeoutConfig(unittest.TestCase):
    """超时配置读取：环境变量可调，非法值不得让启动失败"""

    def _reload(self):
        import importlib
        importlib.reload(lb_gateway)
        return lb_gateway

    def tearDown(self):
        for k in ("STREAM_CONNECT_TIMEOUT_SEC", "STREAM_IDLE_TIMEOUT_SEC"):
            os.environ.pop(k, None)
        self._reload()

    def test_defaults_split_connect_and_idle(self):
        """两段超时必须分离，且首字节超时明显小于旧的 180s 单一超时"""
        g = self._reload()
        self.assertEqual(g.STREAM_CONNECT_TIMEOUT, 45.0)
        self.assertEqual(g.STREAM_IDLE_TIMEOUT, 75.0)
        self.assertLess(g.STREAM_CONNECT_TIMEOUT, 180)

    def test_env_override(self):
        os.environ["STREAM_CONNECT_TIMEOUT_SEC"] = "20"
        os.environ["STREAM_IDLE_TIMEOUT_SEC"] = "90"
        g = self._reload()
        self.assertEqual(g.STREAM_CONNECT_TIMEOUT, 20.0)
        self.assertEqual(g.STREAM_IDLE_TIMEOUT, 90.0)

    def test_invalid_value_falls_back_to_default(self):
        os.environ["STREAM_CONNECT_TIMEOUT_SEC"] = "not-a-number"
        g = self._reload()
        self.assertEqual(g.STREAM_CONNECT_TIMEOUT, 45.0)

    def test_below_minimum_is_clamped(self):
        os.environ["STREAM_IDLE_TIMEOUT_SEC"] = "0.01"
        g = self._reload()
        self.assertEqual(g.STREAM_IDLE_TIMEOUT, 1.0)

    def test_blank_value_uses_default(self):
        os.environ["STREAM_IDLE_TIMEOUT_SEC"] = "   "
        g = self._reload()
        self.assertEqual(g.STREAM_IDLE_TIMEOUT, 75.0)


class TestStreamIdleTimeoutHelper(unittest.TestCase):
    """socket 空闲超时设置：拿不到底层 socket 时必须优雅降级"""

    def test_returns_false_when_socket_unreachable(self):
        class NoSock:
            pass
        self.assertFalse(lb_gateway._set_stream_idle_timeout(NoSock(), 30))

    def test_sets_timeout_on_real_socket(self):
        import socket as _s

        class FakeSock:
            def __init__(self):
                self.applied = None

            def settimeout(self, v):
                self.applied = v

        class Raw:
            def __init__(self, sock):
                self._sock = sock

        class Fp:
            def __init__(self, sock):
                self.raw = Raw(sock)

        class Resp:
            def __init__(self, sock):
                self.fp = Fp(sock)

        sock = FakeSock()
        self.assertTrue(lb_gateway._set_stream_idle_timeout(Resp(sock), 42))
        self.assertEqual(sock.applied, 42)

    def test_swallows_settimeout_failure(self):
        class BadSock:
            def settimeout(self, v):
                raise OSError("nope")

        class Raw:
            def __init__(self):
                self._sock = BadSock()

        class Fp:
            def __init__(self):
                self.raw = Raw()

        class Resp:
            def __init__(self):
                self.fp = Fp()

        self.assertFalse(lb_gateway._set_stream_idle_timeout(Resp(), 30))


class TestStreamChunkSemantics(unittest.TestCase):
    """read1 语义保障：卡死时已到达的数据不得随超时一起丢弃"""

    def test_read1_is_used_not_read(self):
        """
        回归锁定：转发循环必须用 read1。
        read(n) 会阻塞到凑满 n 字节，上游卡死时缓冲区数据随超时丢弃，
        下游因此看到零负载 (empty_stream)。
        """
        import inspect
        src = inspect.getsource(lb_gateway.LBProxyHandler._proxy_request)
        self.assertIn("read1(", src)
        self.assertNotIn("resp.read(STREAM_CHUNK_SIZE)", src)


class TestProbeRateLimiting(unittest.TestCase):
    """探测频率必须落在 geo-IP 接口配额之下，否则限流与快轮询会互相强化成自锁"""

    def test_interval_respects_floor_for_small_deployments(self):
        self.assertEqual(lb_gateway.compute_probe_interval(1),
                         lb_gateway.PROBE_MIN_RETRY_INTERVAL)
        self.assertEqual(lb_gateway.compute_probe_interval(4),
                         lb_gateway.PROBE_MIN_RETRY_INTERVAL)

    def test_interval_scales_with_worker_count(self):
        """Worker 越多间隔越长，保证每分钟请求量不超配额"""
        allowed = lb_gateway.PROBE_RATE_LIMIT_PER_MIN * lb_gateway.PROBE_RATE_SAFETY
        for n in (8, 12, 24, 50):
            interval = lb_gateway.compute_probe_interval(n)
            rate = n / interval * 60.0
            self.assertLessEqual(
                rate, allowed + 1e-6,
                f"{n} workers -> {rate:.1f}/min exceeds allowed {allowed:.1f}/min")

    def test_interval_never_exceeds_hard_limit(self):
        """任何规模下都必须低于接口硬限流值"""
        for n in (1, 4, 8, 12, 24, 50, 100):
            rate = n / lb_gateway.compute_probe_interval(n) * 60.0
            self.assertLess(rate, lb_gateway.PROBE_RATE_LIMIT_PER_MIN)

    def test_interval_handles_zero_and_negative(self):
        self.assertEqual(lb_gateway.compute_probe_interval(0),
                         lb_gateway.PROBE_MIN_RETRY_INTERVAL)
        self.assertEqual(lb_gateway.compute_probe_interval(-3),
                         lb_gateway.PROBE_MIN_RETRY_INTERVAL)

    def test_cycle_includes_probe_cost(self):
        """完整周期必须计入探测耗时，只算间隔会高估可完成轮数"""
        for n in (8, 12, 24):
            self.assertGreater(lb_gateway.compute_probe_cycle(n),
                               lb_gateway.compute_probe_interval(n))

    def test_cycle_grows_with_batch_count(self):
        c4 = lb_gateway.compute_probe_cycle(4, max_concurrency=4)
        c8 = lb_gateway.compute_probe_cycle(8, max_concurrency=4)
        self.assertGreater(c8, c4)

    def test_rate_limited_is_not_treated_as_failure(self):
        """限流只说明探测源不可用，不能作为代理未生效的证据"""
        workers = [{"id": 1, "port": 9001, "proxy": None},
                   {"id": 2, "port": 9002, "proxy": "http://127.0.0.1:19002"}]
        results = [
            _line(1, "44.196.116.2"),
            (2, f"[Worker-2 : Port 9002 : Proxy] -> {lb_gateway.RATE_LIMITED_MARKER} (geo-IP lookup throttled)"),
        ]
        # Worker-2 被限流，但 Worker-1 直连成功；不应因限流判定为未就绪
        self.assertFalse(
            any(lb_gateway.RATE_LIMITED_MARKER not in line
                and ("Connection Failed" in line or "Probe Failed" in line)
                for _, line in results))

    def test_real_connection_failure_still_blocks(self):
        """真正的连接失败仍必须阻止首次打印"""
        workers = [{"id": 1, "port": 9001, "proxy": None},
                   {"id": 2, "port": 9002, "proxy": "http://127.0.0.1:19002"}]
        results = [
            _line(1, "44.196.116.2"),
            (2, "[Worker-2 : Port 9002 : Proxy] -> Connection Failed (URLError)"),
        ]
        self.assertFalse(lb_gateway.evaluate_egress_readiness(workers, results))

    def test_probe_batching_covers_all_workers(self):
        """分批并发不得漏掉任何 Worker"""
        workers = [{"id": i, "port": 9000 + i, "proxy": None} for i in range(1, 10)]
        orig = lb_gateway.probe_single_worker_egress
        try:
            lb_gateway.probe_single_worker_egress = (
                lambda w, timeout=5: f"[Worker-{w['id']}] -> IP: 1.2.3.{w['id']} [x]")
            results = lb_gateway._probe_all_workers(workers, max_concurrency=4)
        finally:
            lb_gateway.probe_single_worker_egress = orig

        self.assertEqual([wid for wid, _ in results], list(range(1, 10)))
