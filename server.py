#!/usr/bin/env python3
"""Compliance Radar 本地后端服务（开发用，非生产部署）。

对外契约：
  GET  /api/status              -> {backend, configured{openai,dataguidance,podcast}, running, last_run, schedule, capabilities}
  GET  /api/data                -> 当前真实 payload（缺失时返回空的 live payload）
  GET  /api/links               -> {text}
  POST /api/links  {text}       -> {saved, invalid, total}；存在非法条目返回 400 且不覆盖原文件
  POST /api/run    {}           -> {status:'running'}；已有运行返回 409
  GET  /api/job                 -> {status, steps:[{label,status}], error?, result?}
  GET  /api/export?mode=demo|live[&run=<id>|demo]  -> xlsx 下载

安全边界：
  - 仅绑定回环地址（默认 127.0.0.1），无生产级鉴权，请勿对外暴露。
  - 静态文件只服务 public/，拒绝路径穿越与符号链接，拒绝点文件。
  - POST 需要同源 + 自定义请求头 X-Radar-Request: 1，拒绝跨站与未知 Host。
  - 配置中的密钥仅来自环境变量 / .env，不写入响应，不写入日志。
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import threading
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, unquote, urlsplit

if __package__ in (None, ""):  # 允许 python server.py 直接运行
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from monitor import __version__
from monitor.ai import prompt_contract
from monitor.collect_dataguidance import selectors_configured, selectors_of
from monitor.collect_wechat import merge_links_text, read_links_file
from monitor.config import configured_flags, describe_config, get, load_config
from monitor.export_xlsx import column_headers, export_bytes
from monitor.fetch import playwright_available
from monitor.pipeline import Pipeline, RunLocked, STEP_PENDING, STEP_RUNNING, step_labels
from monitor.schema import empty_live_payload, normalize_demo_payload, validate_payload
from monitor.store import Store
from monitor.util import RunLock, UnsafePath, is_safe_name, now_utc, resolve_within

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}
MAX_BODY_BYTES = 256 * 1024
DENY_DOTFILES = True

CONTENT_TYPE_JSON = "application/json; charset=utf-8"
CONTENT_TYPE_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def hostname_of(value: str) -> str:
    """从 Host / Origin 中取出主机名（不含端口）。"""
    text = (value or "").strip()
    if not text:
        return ""
    if "://" in text:
        text = urlsplit(text).netloc or urlsplit(text).hostname or ""
    if text.startswith("["):
        return (text.split("]")[0] + "]").lower()
    return text.split(":")[0].strip().lower()


def is_loopback_host(value: str) -> bool:
    return hostname_of(value) in LOOPBACK_HOSTS


class App:
    """请求处理与运行状态（与 HTTP 层解耦，便于测试）。"""

    def __init__(self, base_dir: Path | str, config: dict[str, Any]) -> None:
        self.base_dir = Path(base_dir).resolve()
        self.config = config
        self.store = Store(self.base_dir, config)
        # 可重入锁：start_run 等路径会在持锁状态下调用 job_state()/is_running()
        self._lock = threading.RLock()
        self._run_lock: Optional[RunLock] = None
        self._thread: Optional[threading.Thread] = None
        self.job: dict[str, Any] = {
            "status": "idle",
            "steps": [{"label": label, "status": STEP_PENDING} for label in step_labels()],
            "error": None,
            "result": None,
            "run_id": None,
            "started_at": None,
            "finished_at": None,
        }

    # ---------------- 状态 ----------------

    def configured(self) -> dict[str, bool]:
        return configured_flags(self.config)

    def is_running(self) -> bool:
        with self._lock:
            return self.job["status"] == STEP_RUNNING

    def job_state(self) -> dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self.job, ensure_ascii=False))

    def last_run(self) -> Optional[dict[str, Any]]:
        history = self.store.read_history()
        if history:
            entry = history[-1]
            return {
                "id": entry.get("id"),
                "generated_at": entry.get("generated_at"),
                "event_count": entry.get("event_count"),
                "mode": entry.get("mode", "live"),
                "report_url": entry.get("report_url"),
                "data_url": entry.get("data_url"),
            }
        payload = self.store.read_latest()
        if isinstance(payload, dict):
            return {
                "id": None,
                "generated_at": payload.get("generated_at"),
                "event_count": len(payload.get("events") or []),
                "mode": payload.get("mode", "live"),
                "report_url": None,
                "data_url": None,
            }
        return None

    def status_payload(self) -> dict[str, Any]:
        flags = self.configured()
        wechat_text, wechat_urls = read_links_file(self.store.wechat_links)
        selectors = selectors_of(self.config)
        return {
            "backend": True,
            "configured": {
                "openai": bool(flags.get("openai")),
                "dataguidance": bool(flags.get("dataguidance")),
                "podcast": bool(flags.get("podcast")),
            },
            "running": self.is_running(),
            "last_run": self.last_run(),
            "schedule": {
                "label": str(get(self.config, "schedule.label", "")),
                "engine": str(get(self.config, "schedule.engine", "")),
            },
            "capabilities": {
                "version": __version__,
                "manual_run": True,
                "links_editable": True,
                "history": True,
                "dedupe": True,
                "official_extraction": True,
                "human_verification_required": True,
                "export": ["xlsx"],
                "export_columns": len(column_headers()),
                "export_headers": column_headers(),
                "export_modes": ["live", "demo"],
                "playwright_available": playwright_available(),
                "steps": step_labels(),
                "ai": prompt_contract(),
                "sources": {
                    "dataguidance": {
                        "configured": bool(flags.get("dataguidance")),
                        "engine": str(get(self.config, "dataguidance.engine", "playwright")),
                        "url_set": bool(get(self.config, "dataguidance.url")),
                        "selectors_configured": selectors_configured(selectors),
                        "verified_against_site": False,
                        "note": "PRD 未提供目标超链接，选择器未经真实站点验证。",
                    },
                    "wechat": {
                        "configured": True,
                        "links_file": str(get(self.config, "paths.wechat_links_file", "data/wechat_links.txt")),
                        "link_count": len(wechat_urls),
                        "has_content": bool(wechat_text.strip()),
                        "playwright_fallback": bool(get(self.config, "wechat.use_playwright_fallback", True)),
                        "bypasses_captcha": False,
                    },
                    "podcast": {
                        "configured": bool(flags.get("podcast")),
                        "adapter": "rss",
                        "rss_url_set": bool(get(self.config, "podcast.rss_url")),
                        "note": "PRD 未提供既有代码与订阅地址，未配置时返回 not_configured。",
                    },
                },
                "config": describe_config(self.config),
                "limitations": [
                    "本地开发服务，仅绑定回环地址，无生产级鉴权。",
                    "AI 初筛不构成法律意见；官方链接被取回也不等于事实核验完成。",
                    "DataGuidance 与播客来源在未配置前返回 not_configured，不产生任何伪造数据。",
                ],
            },
        }

    # ---------------- 数据 ----------------

    def data_payload(self) -> dict[str, Any]:
        payload = self.store.read_latest()
        if not isinstance(payload, dict):
            return empty_live_payload(self.configured())
        problems = validate_payload(payload)
        if problems:
            fallback = empty_live_payload(self.configured())
            fallback["warnings"].append(
                "已存在的 latest.json 不符合 payload 契约，已忽略并返回空数据：" + "；".join(problems[:5])
            )
            return fallback
        return payload

    def links_text(self) -> str:
        text, _ = read_links_file(self.store.wechat_links)
        return text

    def save_links(self, incoming: str) -> tuple[bool, dict[str, Any]]:
        try:
            existing = self.store.wechat_links.read_text(encoding="utf-8")
        except OSError:
            existing = ""
        merged, saved, invalid = merge_links_text(existing, incoming)
        if invalid:
            return False, {
                "saved": 0,
                "invalid": invalid,
                "total": len([line for line in existing.splitlines() if line.strip() and not line.strip().startswith("#")]),
            }
        from monitor.util import atomic_write_text

        self.store.wechat_links.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self.store.wechat_links, merged)
        total = len([line for line in merged.splitlines() if line.strip() and not line.strip().startswith("#")])
        return True, {"saved": saved, "invalid": [], "total": total}

    # ---------------- 运行 ----------------

    def start_run(self) -> tuple[bool, str, Optional[dict[str, Any]]]:
        with self._lock:
            if self.job["status"] == STEP_RUNNING:
                return False, "已有运行中的任务", self.job_state()

        lock = RunLock(self.store.lock_path)
        if not lock.acquire(owner={"source": "server", "pid": os.getpid()}):
            with self._lock:
                self.job["status"] = "error"
                self.job["error"] = "已有真实运行在进行中（可能是 CLI 或其他进程），并发运行被拒绝。"
            return False, "已有真实运行在进行中（CLI 或其他进程持有运行锁）", self.job_state()

        with self._lock:
            self.job = {
                "status": STEP_RUNNING,
                "steps": [{"label": label, "status": STEP_PENDING} for label in step_labels()],
                "error": None,
                "result": None,
                "run_id": None,
                "started_at": now_utc().isoformat() + "Z",
                "finished_at": None,
            }
        self._run_lock = lock
        thread = threading.Thread(target=self._run_worker, name="radar-run", daemon=True)
        self._thread = thread
        thread.start()
        return True, "running", None

    def _set_step(self, label: str, status: str) -> None:
        with self._lock:
            for step in self.job["steps"]:
                if step["label"] == label:
                    step["status"] = status
                    break

    def _run_worker(self) -> None:
        pipeline = Pipeline(self.base_dir, self.config, store=self.store)
        try:
            outcome = pipeline.run(mode="live", use_lock=False, on_step=self._set_step)
            with self._lock:
                self.job["status"] = "complete"
                self.job["result"] = outcome.to_result()
                self.job["run_id"] = outcome.run_id
                self.job["finished_at"] = now_utc().isoformat() + "Z"
        except RunLocked as exc:
            with self._lock:
                self.job["status"] = "error"
                self.job["error"] = str(exc)
                self.job["finished_at"] = now_utc().isoformat() + "Z"
        except Exception as exc:  # 任何异常都必须显式暴露，不伪造成功
            with self._lock:
                self.job["status"] = "error"
                self.job["error"] = f"{type(exc).__name__}: {exc}"
                self.job["finished_at"] = now_utc().isoformat() + "Z"
            traceback.print_exc()
        finally:
            if self._run_lock is not None:
                self._run_lock.release()
                self._run_lock = None

    # ---------------- 导出 ----------------

    def export_payload(self, *, mode: str, run: str) -> tuple[Optional[dict[str, Any]], Optional[str], Optional[str]]:
        """返回 (payload, 文件名, 错误信息)。"""
        if run:
            if run == "demo":
                payload = self.store.read_demo()
                if not isinstance(payload, dict):
                    return None, None, "未找到演示数据 public/data/demo.json"
                return normalize_demo_payload(payload), "compliance-radar-demo.xlsx", None
            if not is_safe_name(run):
                return None, None, "非法的 run 参数"
            payload = self.store.read_week(run)
            if not isinstance(payload, dict):
                return None, None, f"未找到历史运行 {run}"
            return payload, f"{run}.xlsx", None

        if mode == "demo":
            payload = self.store.read_demo()
            if not isinstance(payload, dict):
                return None, None, "未找到演示数据 public/data/demo.json（由前端/主控提供）"
            return normalize_demo_payload(payload), "compliance-radar-demo.xlsx", None

        payload = self.data_payload()
        week = str((payload.get("period") or {}).get("week") or "current")
        return payload, f"compliance-radar-{week}.xlsx", None


class Handler(BaseHTTPRequestHandler):
    server_version = f"ComplianceRadar/{__version__}"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    app: App  # 由 make_handler 注入

    # ---------------- 基础工具 ----------------

    def log_message(self, fmt: str, *args: Any) -> None:
        # 只记录方法/路径/状态码，且对查询串做脱敏，避免密钥或链接参数进入日志
        try:
            message = fmt % args
        except Exception:
            message = str(fmt)
        message = _redact(message)
        sys.stderr.write(f"[radar] {self.address_string()} {message}\n")

    def _send(self, status: int, body: bytes, content_type: str, extra: Optional[dict[str, str]] = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD" and body:
            self.wfile.write(body)

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, CONTENT_TYPE_JSON)

    def _error(self, status: int, message: str, **extra: Any) -> None:
        payload = {"error": message, "status": int(status)}
        payload.update(extra)
        self._send_json(status, payload)

    def _check_host(self) -> bool:
        host = self.headers.get("Host", "")
        if not host:
            self._error(HTTPStatus.BAD_REQUEST, "缺少 Host 头")
            return False
        if not is_loopback_host(host):
            self._error(HTTPStatus.FORBIDDEN, f"拒绝的 Host：{hostname_of(host)}（仅允许回环地址）")
            return False
        return True

    def _check_post_security(self) -> bool:
        token_header = str(get(self.app.config, "server.api_token_header", "X-Radar-Request"))
        token_value = str(get(self.app.config, "server.api_token_value", "1"))
        if (self.headers.get(token_header) or "").strip() != token_value:
            self._error(HTTPStatus.FORBIDDEN, f"缺少或错误的 {token_header} 请求头（同源校验）")
            return False

        origin = self.headers.get("Origin")
        if origin and not is_loopback_host(origin):
            self._error(HTTPStatus.FORBIDDEN, "拒绝跨站 Origin")
            return False

        fetch_site = (self.headers.get("Sec-Fetch-Site") or "").strip().lower()
        if fetch_site == "cross-site":
            self._error(HTTPStatus.FORBIDDEN, "拒绝跨站请求（Sec-Fetch-Site: cross-site）")
            return False

        referer = self.headers.get("Referer")
        if referer and not is_loopback_host(referer):
            self._error(HTTPStatus.FORBIDDEN, "拒绝跨站 Referer")
            return False
        return True

    def _read_raw_body(self) -> Optional[bytes]:
        """读取请求体。返回 None 表示超限（已丢弃并会关闭连接）。"""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return b""
        if length <= 0:
            return b""
        if length > MAX_BODY_BYTES:
            self.rfile.read(min(length, MAX_BODY_BYTES))
            self.close_connection = True
            return None
        return self.rfile.read(length)

    def _parse_json_body(self, raw: bytes) -> tuple[bool, dict[str, Any]]:
        if not raw or not raw.strip():
            return True, {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            self._error(HTTPStatus.BAD_REQUEST, "请求体不是合法 JSON")
            return False, {}
        if not isinstance(data, dict):
            self._error(HTTPStatus.BAD_REQUEST, "请求体必须是 JSON 对象")
            return False, {}
        return True, data

    # ---------------- 路由 ----------------

    def do_GET(self) -> None:  # noqa: N802
        if not self._check_host():
            return
        parsed = urlsplit(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path.startswith("/api/"):
            self._handle_api_get(path, query)
            return
        self._serve_static(path)

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def do_POST(self) -> None:  # noqa: N802
        raw = self._read_raw_body()
        if raw is None:
            self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "请求体过大")
            return
        if not self._check_host():
            return
        parsed = urlsplit(self.path)
        if not parsed.path.startswith("/api/"):
            self._error(HTTPStatus.NOT_FOUND, "未知接口")
            return
        if not self._check_post_security():
            return

        if parsed.path == "/api/links":
            ok, body = self._parse_json_body(raw)
            if not ok:
                return
            text = body.get("text")
            if not isinstance(text, str):
                self._error(HTTPStatus.BAD_REQUEST, "字段 text 必须是字符串")
                return
            success, result = self.app.save_links(text)
            self._send_json(HTTPStatus.OK if success else HTTPStatus.BAD_REQUEST, result)
            return

        if parsed.path == "/api/run":
            ok, _body = self._parse_json_body(raw)
            if not ok:
                return
            started, message, _state = self.app.start_run()
            if not started:
                payload = {"status": "running" if self.app.is_running() else "error", "error": message}
                self._send_json(HTTPStatus.CONFLICT, payload)
                return
            self._send_json(HTTPStatus.OK, {"status": "running"})
            return

        self._error(HTTPStatus.NOT_FOUND, "未知接口")

    def _handle_api_get(self, path: str, query: dict[str, list[str]]) -> None:
        if path == "/api/status":
            self._send_json(HTTPStatus.OK, self.app.status_payload())
            return
        if path == "/api/data":
            self._send_json(HTTPStatus.OK, self.app.data_payload())
            return
        if path == "/api/links":
            self._send_json(HTTPStatus.OK, {"text": self.app.links_text()})
            return
        if path == "/api/job":
            self._send_json(HTTPStatus.OK, self.app.job_state())
            return
        if path == "/api/export":
            mode = (query.get("mode") or ["live"])[0].strip().lower()
            run = (query.get("run") or [""])[0].strip()
            if mode not in ("live", "demo"):
                self._error(HTTPStatus.BAD_REQUEST, "mode 必须是 live 或 demo")
                return
            payload, filename, error = self.app.export_payload(mode=mode, run=run)
            if error or payload is None:
                self._error(HTTPStatus.NOT_FOUND, error or "无法导出")
                return
            problems = validate_payload(payload)
            if problems:
                self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "导出数据不符合契约：" + "；".join(problems[:5]))
                return
            try:
                blob = export_bytes(payload)
            except Exception as exc:
                self._error(HTTPStatus.INTERNAL_SERVER_ERROR, f"生成 Excel 失败：{exc}")
                return
            self._send(
                HTTPStatus.OK,
                blob,
                CONTENT_TYPE_XLSX,
                extra={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
            return
        self._error(HTTPStatus.NOT_FOUND, "未知接口")

    def _serve_static(self, path: str) -> None:
        public_dir = self.app.store.public_dir
        relative = unquote(path or "/")
        if relative in ("", "/"):
            relative = "/index.html"
        if DENY_DOTFILES and any(part.startswith(".") for part in relative.split("/") if part):
            self._error(HTTPStatus.NOT_FOUND, "未找到资源")
            return
        try:
            target = resolve_within(public_dir, relative)
        except UnsafePath:
            self._error(HTTPStatus.NOT_FOUND, "未找到资源")
            return
        if target.is_dir():
            index = target / "index.html"
            if not index.is_file():
                self._error(HTTPStatus.NOT_FOUND, "未找到资源")
                return
            target = index
        if not target.is_file():
            self._error(HTTPStatus.NOT_FOUND, "未找到资源")
            return
        try:
            data = target.read_bytes()
        except OSError:
            self._error(HTTPStatus.NOT_FOUND, "未找到资源")
            return
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in ("application/json", "application/javascript"):
            content_type += "; charset=utf-8"
        self._send(HTTPStatus.OK, data, content_type)


def _redact(text: str) -> str:
    """日志脱敏：去掉查询串，且不记录任何疑似密钥内容。"""
    lowered = text.lower()
    for marker in ("token", "key", "secret", "authorization", "password"):
        if marker in lowered:
            return "[redacted]"
    if "?" in text:
        head, _, tail = text.partition("?")
        rest = tail[tail.index(" ") :] if " " in tail else ""
        return f"{head}?[redacted-query]{rest}"
    return text


def make_handler(app: App) -> type[Handler]:
    return type("BoundHandler", (Handler,), {"app": app})


def create_server(
    base_dir: Path | str,
    *,
    config: Optional[dict[str, Any]] = None,
    port: Optional[int] = None,
    host: Optional[str] = None,
) -> tuple[ThreadingHTTPServer, App]:
    base = Path(base_dir).resolve()
    config = config if config is not None else load_config(base)
    app = App(base, config)

    bind_host = str(host or get(config, "server.host", "127.0.0.1") or "127.0.0.1")
    if hostname_of(bind_host) not in LOOPBACK_HOSTS:
        raise ValueError(f"出于安全考虑仅允许绑定回环地址，收到：{bind_host}")
    bind_port = int(port if port is not None else get(config, "server.port", 8765))

    server = ThreadingHTTPServer((bind_host, bind_port), make_handler(app))
    server.daemon_threads = True
    server.app = app  # type: ignore[attr-defined]
    return server, app


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Compliance Radar 本地后端（仅回环地址）")
    parser.add_argument("--port", type=int, default=None, help="监听端口，默认 8765")
    parser.add_argument("--host", default=None, help="仅允许回环地址，默认 127.0.0.1")
    parser.add_argument("--base-dir", default=None, help="项目根目录，默认本文件所在目录")
    args = parser.parse_args(argv)

    base_dir = Path(args.base_dir).resolve() if args.base_dir else Path(__file__).resolve().parent
    config = load_config(base_dir)
    try:
        server, app = create_server(base_dir, config=config, port=args.port, host=args.host)
    except OSError as exc:
        print(f"启动失败：{exc}（端口可能已被占用，可用 --port 指定其他端口）", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"启动失败：{exc}", file=sys.stderr)
        return 1

    host, port = server.server_address[0], server.server_address[1]
    flags = app.configured()
    print(f"Compliance Radar 后端 v{__version__} 已启动：http://{host}:{port}")
    print(f"  静态目录：{app.store.public_dir}（仅服务该目录，防穿越/符号链接）")
    print(
        "  已配置来源："
        f"openai={'是' if flags.get('openai') else '否'} "
        f"dataguidance={'是' if flags.get('dataguidance') else '否'} "
        f"podcast={'是' if flags.get('podcast') else '否'}"
    )
    print("  仅本机可访问；未配置的密钥不会写入响应或日志。按 Ctrl+C 停止。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
