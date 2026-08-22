#!/usr/bin/env python3
"""
Smart Load Balancer Gateway for Gemini-Web2API
- Port: 8081 (Unified Entrypoint)
- Sticky Session based on User / First-Prompt MD5 fingerprint
- Least-Connection scheduling for new sessions
- Automatic failover & retry on 429/5xx/ConnectionError
- Full SSE stream & chunked response pass-through
- Debug logging mode via DEBUG=true / --debug
"""

import sys
import os
import json
import time
import hashlib
import threading
import argparse
from collections import OrderedDict
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import urllib.request
import urllib.error
import http.client

# 全局状态锁与路由表
LOCK = threading.Lock()
ACTIVE_CONNS = {}       # {worker_id: count}
SESSION_MAP = OrderedDict()  # {session_id: (worker_id, last_seen_time)} - LRU 映射表
WORKER_STATUS = {}      # {worker_id: {"last_fail": 0, "fail_count": 0}}
WORKERS = []            # [{"id": 1, "port": 9001, "proxy": None}, ...]
RR_INDEX = 0            # 轮询游标
REQ_COUNTER = 0
DEBUG_ENABLED = False

SESSION_TTL = 1800      # 会话粘滞有效期 (30分钟)
MAX_SESSIONS = 50000    # 最大缓存会话数上限 (LRU 淘汰防止内存无限增长)
FAIL_PENALTY_SEC = 20   # 故障节点冷却降权时间 (秒)
PROBE_TIMEOUT = 5       # 单个出口 IP 探测源的超时 (秒)

# 不可透传的响应头：
# - 逐跳头 (hop-by-hop) 由本网关自行决定，不能照搬上游
# - Server / Date 由 send_response() 自动补齐，透传会造成重复头
SKIPPED_RESPONSE_HEADERS = frozenset({
    "transfer-encoding", "connection", "keep-alive", "server", "date",
    "proxy-authenticate", "proxy-authorization", "te", "trailer", "upgrade",
})


def log_debug(msg):
    if DEBUG_ENABLED:
        print(f"[DEBUG @ {time.strftime('%H:%M:%S')}] {msg}", flush=True)


DEFAULT_WORKER = {"id": 1, "port": 9001, "proxy": None}


def sanitize_workers(raw_workers, gateway_port=8081):
    """
    校验并规范化 workers 配置 (纯函数)。

    workers.json 由启动脚本生成，属系统边界输入，故逐条校验而非直接信任：
    丢弃缺失/非法 id 与 port 的条目、网关自身端口以及重复 id，
    避免畸形配置在启动或请求路径上以 KeyError / TypeError 形式远距离爆炸。
    返回 (合法 worker 列表, 被丢弃原因列表)。
    """
    if not isinstance(raw_workers, list):
        return [], [f"`workers` must be a list, got {type(raw_workers).__name__}"]

    cleaned = []
    dropped = []
    seen_ids = set()

    for idx, w in enumerate(raw_workers):
        if not isinstance(w, dict):
            dropped.append(f"entry #{idx}: not an object")
            continue

        wid, wport = w.get("id"), w.get("port")

        # bool 是 int 子类，需显式排除以免 True 被当作 id=1
        if not isinstance(wid, int) or isinstance(wid, bool) or wid <= 0:
            dropped.append(f"entry #{idx}: invalid id {wid!r}")
            continue
        if not isinstance(wport, int) or isinstance(wport, bool) or not (1 <= wport <= 65535):
            dropped.append(f"Worker-{wid}: invalid port {wport!r}")
            continue
        if wport == gateway_port:
            dropped.append(f"Worker-{wid}: port {wport} collides with gateway")
            continue
        if wid in seen_ids:
            dropped.append(f"Worker-{wid}: duplicate id")
            continue

        proxy = w.get("proxy")
        if proxy is not None and not (isinstance(proxy, str) and proxy.strip()):
            dropped.append(f"Worker-{wid}: invalid proxy {proxy!r}, treated as DIRECT")
            proxy = None

        seen_ids.add(wid)
        cleaned.append({"id": wid, "port": wport,
                        "proxy": proxy.strip() if isinstance(proxy, str) else None})

    return cleaned, dropped


def load_workers(config_path="workers.json", gateway_port=8081):
    global WORKERS, ACTIVE_CONNS, WORKER_STATUS
    if not os.path.exists(config_path):
        WORKERS = [dict(DEFAULT_WORKER)]
    else:
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            raw = data.get("workers", []) if isinstance(data, dict) else data
            cleaned, dropped = sanitize_workers(raw, gateway_port=gateway_port)
            for reason in dropped:
                print(f"[LB] Skipped invalid worker config -> {reason}", flush=True)
            WORKERS = cleaned or [dict(DEFAULT_WORKER)]
            if not cleaned:
                print("[LB] No valid worker in config; falling back to single direct worker.",
                      flush=True)
        except Exception as e:
            print(f"[LB] Failed to read {config_path}: {e}", flush=True)
            WORKERS = [dict(DEFAULT_WORKER)]

    with LOCK:
        for w in WORKERS:
            wid = w["id"]
            if wid not in ACTIVE_CONNS:
                ACTIVE_CONNS[wid] = 0
            if wid not in WORKER_STATUS:
                WORKER_STATUS[wid] = {"last_fail": 0, "fail_count": 0}

    # 各 Worker 的端口与出口路由已由启动脚本逐行打印，此处只报总数避免重复长行输出
    proxied = sum(1 for w in WORKERS if w.get("proxy"))
    print(f"[LB] Loaded {len(WORKERS)} worker(s) "
          f"({proxied} proxied, {len(WORKERS) - proxied} direct).", flush=True)


def cleanup_stale_sessions():
    now = time.time()
    with LOCK:
        stale_keys = [k for k, v in SESSION_MAP.items() if now - v[1] > SESSION_TTL]
        for k in stale_keys:
            del SESSION_MAP[k]


def record_session(session_id, wid, seen_time=None):
    """记录/更新 Session 映射 (带 LRU 容量上限保护)"""
    if not session_id:
        return
    if seen_time is None:
        seen_time = time.time()
    # 移至最新位置
    if session_id in SESSION_MAP:
        SESSION_MAP.move_to_end(session_id)
    SESSION_MAP[session_id] = (wid, seen_time)

    # 达到上限时弹出最老未使用的会话 (FIFO / LRU 头部)
    while len(SESSION_MAP) > MAX_SESSIONS:
        SESSION_MAP.popitem(last=False)


def extract_request_meta(headers, body_bytes):
    """
    提取会话特征指纹与调试摘要
    """
    session_id = None
    prompt_snippet = ""
    model_name = ""

    try:
        if body_bytes:
            body = json.loads(body_bytes.decode("utf-8"))
            model_name = body.get("model", "")

            # 1. 客户端显式传递的 user 标识
            if body.get("user"):
                session_id = f"usr_{body['user']}"

            # 2. 对话首条 user prompt 特征指纹
            messages = body.get("messages", [])
            if isinstance(messages, list):
                for m in messages:
                    if isinstance(m, dict) and m.get("role") == "user":
                        content = m.get("content", "")
                        if isinstance(content, list):
                            text_parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
                            content = "".join(text_parts)
                        if content:
                            prompt_snippet = str(content).strip()[:60].replace("\n", " ")
                            if not session_id:
                                h = hashlib.md5(str(content)[:128].encode("utf-8")).hexdigest()[:12]
                                session_id = f"ctx_{h}"
                            break
    except Exception:
        pass

    # 3. 兜底按 Cookie / Ctoken / Authorization 特征指纹粘滞
    if not session_id:
        cookie_header = headers.get("Cookie") or headers.get("cookie") or headers.get("X-Gemini-Cookie") or headers.get("x-gemini-cookie") or headers.get("X-Ctoken") or headers.get("x-ctoken")
        if cookie_header:
            h = hashlib.md5(cookie_header.encode("utf-8")).hexdigest()[:10]
            session_id = f"cookie_{h}"

    # 4. 兜底按 Authorization Key 粘滞
    if not session_id:
        auth = headers.get("Authorization") or headers.get("authorization")
        if auth:
            h = hashlib.md5(auth.encode("utf-8")).hexdigest()[:10]
            session_id = f"auth_{h}"

    return session_id, model_name, prompt_snippet


def select_worker(session_id, exclude_wids=None, acquire_slot=False):
    """
    选择目标 Worker。

    acquire_slot=True 时在同一临界区内递增 ACTIVE_CONNS 并返回占用后的计数，
    避免"选中"与"计数"之间的窗口导致并发请求读到滞后负载。
    调用方必须在请求结束时调用 release_worker_slot(wid) 释放。
    返回 (worker, route_type, active_count)；acquire_slot=False 时 active_count 为当前值。
    """
    global RR_INDEX
    if exclude_wids is None:
        exclude_wids = set()

    def _take(w, route_type):
        wid = w["id"]
        if acquire_slot:
            ACTIVE_CONNS[wid] = ACTIVE_CONNS.get(wid, 0) + 1
        return w, route_type, ACTIVE_CONNS.get(wid, 0)

    now = time.time()
    with LOCK:
        available_workers = [w for w in WORKERS if w["id"] not in exclude_wids]
        if not available_workers:
            available_workers = WORKERS

        # 检查是否为老会话
        if session_id and session_id in SESSION_MAP:
            bound_wid, _ = SESSION_MAP[session_id]
            st = WORKER_STATUS.get(bound_wid, {"last_fail": 0})
            is_cooling_down = (now - st.get("last_fail", 0)) < FAIL_PENALTY_SEC

            # 若绑定的 Worker 正常且未被本次请求排除，优先命中
            if bound_wid not in exclude_wids and not is_cooling_down:
                target = next((w for w in available_workers if w["id"] == bound_wid), None)
                if target:
                    record_session(session_id, bound_wid, now)
                    return _take(target, "STICKY")

        # 最少连接 + 严格循环轮询（并发相等时严格依次推进轮换）
        def score(w):
            wid = w["id"]
            base_conn = ACTIVE_CONNS.get(wid, 0)
            st = WORKER_STATUS.get(wid, {"last_fail": 0, "fail_count": 0})
            penalty = 100 if (now - st["last_fail"] < FAIL_PENALTY_SEC) else 0
            return base_conn + penalty

        min_score = min(score(w) for w in available_workers)
        candidates = [w for w in available_workers if score(w) == min_score]

        best_worker = candidates[RR_INDEX % len(candidates)]
        RR_INDEX += 1

        if session_id:
            record_session(session_id, best_worker["id"], now)
        return _take(best_worker, "LEAST_CONN")


def release_worker_slot(wid):
    """释放 select_worker(acquire_slot=True) 占用的连接计数"""
    with LOCK:
        ACTIVE_CONNS[wid] = max(0, ACTIVE_CONNS.get(wid, 1) - 1)


def record_worker_success(wid):
    with LOCK:
        if wid in WORKER_STATUS:
            WORKER_STATUS[wid]["fail_count"] = 0


def record_worker_failure(wid):
    with LOCK:
        if wid in WORKER_STATUS:
            WORKER_STATUS[wid]["last_fail"] = time.time()
            WORKER_STATUS[wid]["fail_count"] += 1


class LBProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        pass

    def _send_json_error(self, code, message, err_type):
        """以 OpenAI 兼容格式下发错误响应 (仅可在响应头未发送前调用)"""
        payload = json.dumps({
            "error": {"message": message, "type": err_type, "code": code}
        }).encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(payload)
        except (ConnectionResetError, BrokenPipeError):
            pass

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, PUT, DELETE")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        self._proxy_request("GET")

    def do_POST(self):
        self._proxy_request("POST")

    def do_HEAD(self):
        self._proxy_request("HEAD")

    def _proxy_request(self, method):
        global REQ_COUNTER
        with LOCK:
            REQ_COUNTER += 1
            req_id = REQ_COUNTER

        start_time = time.time()

        # 1. 读取请求 Body (Content-Length 属客户端输入，畸形值不得让处理线程崩溃)
        try:
            content_len = int(self.headers.get("Content-Length", 0) or 0)
        except (TypeError, ValueError):
            log_debug(f"[Req #{req_id}] Malformed Content-Length header; rejected.")
            self._send_json_error(400, "Malformed Content-Length header", "bad_request")
            return
        if content_len < 0:
            self._send_json_error(400, "Negative Content-Length", "bad_request")
            return

        body_bytes = self.rfile.read(content_len) if content_len > 0 else b""

        # 2. 会话指纹与调试信息提取
        session_id, model_name, prompt_snippet = extract_request_meta(self.headers, body_bytes) if method == "POST" else (None, "", "")

        # 3. 准备转发 Header
        fwd_headers = {}
        for k, v in self.headers.items():
            if k.lower() not in ("host", "content-length", "connection"):
                fwd_headers[k] = v
        fwd_headers["Connection"] = "close"

        exclude_wids = set()
        max_attempts = min(len(WORKERS), 3) if len(WORKERS) > 1 else 1

        # 响应头一旦下发即无法再改写状态行，此后禁止 failover 重试，
        # 否则第二个 Worker 的状态行与响应体会拼接进已发出的字节流。
        headers_sent = False

        for attempt in range(max_attempts):
            worker, route_type, curr_active = select_worker(
                session_id, exclude_wids, acquire_slot=True)
            wid = worker["id"]
            wport = worker["port"]
            wproxy = worker.get("proxy") or "DIRECT"

            # 占用已在 select_worker 内完成，必须在本轮结束时释放
            try:
                target_url = f"http://127.0.0.1:{wport}{self.path}"

                if attempt == 0:
                    log_debug(
                        f"[Req #{req_id}] [{method}] {self.path} | Model: '{model_name or 'N/A'}' | Session: {session_id or 'NONE'} | "
                        f"Prompt: \"{prompt_snippet[:40]}...\" -> Selected: Worker-{wid} (Port {wport}, Egress: {wproxy}, Route: {route_type}, Active: {curr_active})"
                    )
                else:
                    log_debug(
                        f"[Req #{req_id}] [RETRY #{attempt}] Failover -> Worker-{wid} (Port {wport}, Egress: {wproxy})"
                    )

                req = urllib.request.Request(
                    target_url,
                    data=body_bytes if method in ("POST", "PUT") else None,
                    headers=fwd_headers,
                    method=method
                )

                try:
                    resp = urllib.request.urlopen(req, timeout=180)

                    upstream_headers = resp.getheaders()
                    has_content_length = any(
                        hk.lower() == "content-length" for hk, _ in upstream_headers
                    )

                    self.send_response(resp.status)
                    for hk, hv in upstream_headers:
                        if hk.lower() not in SKIPPED_RESPONSE_HEADERS:
                            self.send_header(hk, hv)
                    self.send_header("Access-Control-Allow-Origin", "*")

                    # 上游为流式响应 (无 Content-Length) 时，剥掉 Transfer-Encoding 后
                    # 必须显式关闭连接来界定响应结束，否则 HTTP/1.1 keep-alive 下
                    # 客户端既无长度也无分块标记，只能挂住等超时。
                    if not has_content_length:
                        self.send_header("Connection", "close")
                        self.close_connection = True

                    self.end_headers()
                    headers_sent = True

                    # 流式透传：区分"客户端断开"与"上游截断"
                    client_gone = False
                    upstream_error = None
                    try:
                        while True:
                            chunk = resp.read(8192)
                            if not chunk:
                                break
                            try:
                                self.wfile.write(chunk)
                                self.wfile.flush()
                            except (ConnectionResetError, BrokenPipeError):
                                client_gone = True
                                break
                    except Exception as stream_err:
                        upstream_error = stream_err
                    finally:
                        resp.close()

                    elapsed = time.time() - start_time
                    if upstream_error is not None:
                        # 上游中途断流：响应已部分下发，无法重试，只能降权并如实记账
                        record_worker_failure(wid)
                        log_debug(
                            f"[Req #{req_id}] Worker-{wid} stream truncated after {elapsed:.2f}s "
                            f"({type(upstream_error).__name__}: {upstream_error}); response already partially sent."
                        )
                    else:
                        record_worker_success(wid)
                        reason = " (client disconnected)" if client_gone else ""
                        log_debug(
                            f"[Req #{req_id}] Completed in {elapsed:.2f}s | HTTP {resp.status} "
                            f"via Worker-{wid} ({wproxy}){reason}"
                        )
                    return

                except urllib.error.HTTPError as e:
                    if e.code in (429, 500, 502, 503, 504) and attempt < max_attempts - 1 and not headers_sent:
                        log_debug(f"[Req #{req_id}] Worker-{wid} failed with HTTP {e.code}, triggering failover...")
                        record_worker_failure(wid)
                        exclude_wids.add(wid)
                        continue

                    record_worker_failure(wid)
                    if headers_sent:
                        log_debug(f"[Req #{req_id}] Worker-{wid} HTTP {e.code} after headers sent; connection closed.")
                        return

                    err_body = e.read()
                    self.send_response(e.code)
                    for hk, hv in e.headers.items():
                        if hk.lower() not in ("transfer-encoding", "connection", "content-length"):
                            self.send_header(hk, hv)
                    self.send_header("Content-Length", str(len(err_body)))
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    headers_sent = True
                    self.wfile.write(err_body)
                    elapsed = time.time() - start_time
                    log_debug(f"[Req #{req_id}] Finished with Error HTTP {e.code} in {elapsed:.2f}s via Worker-{wid}")
                    return

                except Exception as e:
                    record_worker_failure(wid)
                    exclude_wids.add(wid)

                    if attempt < max_attempts - 1 and not headers_sent:
                        log_debug(f"[Req #{req_id}] Worker-{wid} connection error ({e}), triggering failover...")
                        continue

                    elapsed = time.time() - start_time
                    if headers_sent:
                        log_debug(f"[Req #{req_id}] Worker-{wid} failed after headers sent ({e}); connection closed.")
                        return

                    log_debug(f"[Req #{req_id}] All workers failed after {elapsed:.2f}s: {e}")
                    self._send_json_error(
                        502,
                        f"Load balancer: all upstream workers unavailable ({e})",
                        "bad_gateway",
                    )
                    headers_sent = True
                    return

            finally:
                release_worker_slot(wid)


def probe_single_worker_egress(worker, timeout=PROBE_TIMEOUT):
    """探测单个 Worker 实例的实际出口 IP 与归属地信息"""
    wid = worker["id"]
    wport = worker["port"]
    proxy = worker.get("proxy")

    if proxy:
        port_str = proxy.rstrip("/").split(":")[-1]
        route_desc = f"Proxy (:{port_str})"
    else:
        route_desc = "DIRECT (Native)"

    headers = {"User-Agent": "gemflow-egress-probe/1.0"}
    if proxy:
        proxy_handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        opener = urllib.request.build_opener(proxy_handler)
    else:
        opener = urllib.request.build_opener()

    info_str = "Unknown / Probe Failed"
    # 1. 尝试 ip-api.com (HTTP, 免 API Key)
    try:
        req = urllib.request.Request("http://ip-api.com/json", headers=headers)
        with opener.open(req, timeout=timeout) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("status") == "success":
                    country = data.get("country", "Unknown")
                    city = data.get("city", "")
                    query = data.get("query", "Unknown")
                    org = data.get("org") or data.get("isp") or "Unknown"
                    location = f"{country} ({city})" if city else country
                    info_str = f"{location} - IP: {query} [{org}]"
                    return f"[{f'Worker-{wid} : Port {wport} : {route_desc}':<38}] -> {info_str}"
    except Exception:
        pass

    # 2. 兜底尝试 ipinfo.io (HTTPS)
    try:
        req = urllib.request.Request("https://ipinfo.io/json", headers=headers)
        with opener.open(req, timeout=timeout) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                ip = data.get("ip", "Unknown")
                country = data.get("country", "Unknown")
                city = data.get("city", "")
                org = data.get("org", "Unknown")
                location = f"{country} ({city})" if city else country
                info_str = f"{location} - IP: {ip} [{org}]"
                return f"[{f'Worker-{wid} : Port {wport} : {route_desc}':<38}] -> {info_str}"
    except Exception as e:
        info_str = f"Connection Failed ({type(e).__name__})"

    return f"[{f'Worker-{wid} : Port {wport} : {route_desc}':<38}] -> {info_str}"


def evaluate_egress_readiness(workers, results):
    """
    判定本轮探测是否代表"代理链路已生效"，可以打印首次状态面板。

    纯函数，便于单测。判定口径：
    - 所有 Worker 探测均成功（无连接/探测失败）
    - 若同时存在直连 Worker，则代理 Worker 至少有一个出口 IP 与原生直连 IP 不同
      （用于排除 mihomo 尚未就绪、代理端口实际回落直连的情况）

    不要求各代理出口 IP 互不相同：健康节点少于 Worker 数时轮转复用是合法结果。
    """
    ip_by_wid = {}
    for wid, line in results:
        if "IP: " in line:
            ip_by_wid[wid] = line.split("IP: ")[1].split()[0]

    has_failed = any("Connection Failed" in line or "Probe Failed" in line
                     for _, line in results)
    if has_failed or not results:
        return False

    proxy_wids = {w["id"] for w in workers if w.get("proxy")}
    direct_wids = {w["id"] for w in workers if not w.get("proxy")}

    if not proxy_wids:
        return True

    proxy_ips = {ip_by_wid[wid] for wid in proxy_wids if wid in ip_by_wid}
    if not proxy_ips:
        return False

    direct_ips = {ip_by_wid[wid] for wid in direct_wids if wid in ip_by_wid}
    if direct_ips:
        return bool(proxy_ips - direct_ips)

    return True


def _probe_all_workers(workers):
    """
    并发探测所有 Worker 出口，返回按 id 升序的 [(wid, line), ...]。

    超时线程会被放弃但仍可能继续写入结果字典，故读取前必须在锁内快照，
    否则并发修改会破坏后续排序与遍历。未按时返回的 Worker 显式补占位行，
    避免面板缺行以及 readiness 判定误认为全部成功。
    """
    collected = {}
    res_lock = threading.Lock()
    threads = []

    def _probe_w(w):
        res = probe_single_worker_egress(w)
        with res_lock:
            collected[w["id"]] = res

    for w in workers:
        t = threading.Thread(target=_probe_w, args=(w,), daemon=True)
        threads.append(t)
        t.start()

    # 单次探测最坏耗时约 2 x PROBE_TIMEOUT (两个 IP 源串行)，留余量后统一回收
    deadline = time.time() + PROBE_TIMEOUT * 2 + 4
    for t in threads:
        t.join(timeout=max(0.1, deadline - time.time()))

    with res_lock:
        snapshot = dict(collected)

    results = []
    for w in workers:
        wid = w["id"]
        line = snapshot.get(wid)
        if line is None:
            route_desc = "Proxy" if w.get("proxy") else "DIRECT (Native)"
            label = f"Worker-{wid} : Port {w['port']} : {route_desc}"
            line = f"[{label:<38}] -> Probe Failed (Timeout)"
        results.append((wid, line))

    results.sort(key=lambda x: x[0])
    return results


def async_inspect_egress_ips(initial_delay=3.0, poll_interval=300.0,
                             first_print_deadline=180.0):
    """
    后台异步守护线程：持续探测各 Worker 出口 IP 并打印状态面板。

    首次打印需等待代理链路实际生效 (见 evaluate_egress_readiness)，
    但最长只等 first_print_deadline 秒，超时后无条件打印当前实况，
    避免节点异常时面板永久静默；此后每 poll_interval 周期打印一次。
    """
    def _probe_round(has_ever_printed, started_at):
        """执行一轮探测并按需打印，返回更新后的 has_ever_printed"""
        with LOCK:
            workers_copy = list(WORKERS)
        if not workers_copy:
            return has_ever_printed

        results = _probe_all_workers(workers_copy)

        if has_ever_printed:
            # 稳定期：每轮如实打印当前出口实况
            should_print = True
        elif evaluate_egress_readiness(workers_copy, results):
            should_print = True
        else:
            # 代理链路迟迟未生效时，超过截止时间也打印一次便于排查
            should_print = (time.time() - started_at) >= first_print_deadline
            if should_print:
                print("[LB] Warning: proxy egress not confirmed within "
                      f"{int(first_print_deadline)}s. Printing current state as-is.",
                      flush=True)

        if not should_print:
            return has_ever_printed

        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n========== [Worker Egress IP Status @ {timestamp}] ==========", flush=True)
        for _, line in results:
            print(line, flush=True)
        print("=" * 70 + "\n", flush=True)
        return True

    def _worker_task():
        if initial_delay > 0:
            time.sleep(initial_delay)

        has_ever_printed = False
        started_at = time.time()

        while True:
            try:
                has_ever_printed = _probe_round(has_ever_printed, started_at)
            except Exception as e:
                # 守护线程一旦抛出即永久静默，兜底捕获保证下一轮继续
                print(f"[LB] Egress inspection round failed: {type(e).__name__}: {e}",
                      flush=True)
            time.sleep(5.0 if not has_ever_printed else poll_interval)

    t = threading.Thread(target=_worker_task, daemon=True)
    t.start()



def start_lb_server(port=8081, config_path="workers.json", debug=False):
    global DEBUG_ENABLED
    DEBUG_ENABLED = debug
    if DEBUG_ENABLED:
        print("[LB] >>> DEBUG logging mode is ENABLED <<<", flush=True)

    load_workers(config_path, gateway_port=port)

    # 启动后台异步出口 IP 探测
    async_inspect_egress_ips(initial_delay=15.0)

    def timer_loop():
        while True:
            time.sleep(300)
            cleanup_stale_sessions()

    t = threading.Thread(target=timer_loop, daemon=True)
    t.start()

    server_address = ("0.0.0.0", port)
    httpd = ThreadingHTTPServer(server_address, LBProxyHandler)
    print(f"[LB] Gemini Load Balancer Gateway listening on http://0.0.0.0:{port}", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Gemini-Web2API Load Balancer Gateway")
    parser.add_argument("--port", type=int, default=8081, help="Gateway listen port (default: 8081)")
    parser.add_argument("--config", type=str, default="workers.json", help="Workers config JSON file")
    parser.add_argument("--debug", action="store_true", help="Enable verbose debug logging")
    args = parser.parse_args()

    # 从命令行参数或环境变量 DEBUG=true / DEBUG=1 中读取调试开关
    debug_mode = args.debug or os.environ.get("DEBUG", "").strip().lower() in ("true", "1", "yes", "on")
    start_lb_server(port=args.port, config_path=args.config, debug=debug_mode)
