"""通用工具：URL 规范化、日期解析、文本清洗、原子写入、运行锁、安全路径解析。

设计约束：
- 不发明数据：解析失败一律返回 None，由调用方显式标记为未知/待复核。
- 不泄露密钥：本模块不打印配置内容。
"""

from __future__ import annotations

import hashlib
import html as html_module
import json
import os
import re
import tempfile
import unicodedata
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

try:  # POSIX 优先使用 flock（进程退出自动释放）
    import fcntl
except ImportError:  # pragma: no cover - Windows 回退
    fcntl = None  # type: ignore[assignment]


# --------------------------------------------------------------------------
# URL 处理
# --------------------------------------------------------------------------

# 精确匹配的跟踪参数
TRACKING_PARAMS_EXACT = frozenset(
    {
        "fbclid",
        "gclid",
        "dclid",
        "msclkid",
        "yclid",
        "igshid",
        "spm",
        "from",
        "from_source",
        "share_token",
        "share_source",
        "scene",
        "srcid",
        "ref",
        "refer",
        "referrer",
        "trk",
        "trkcampaign",
        "vero_id",
        "mkt_tok",
        "oly_anon_id",
        "oly_enc_id",
        "mc_cid",
        "mc_eid",
        "_openstat",
        "gclsrc",
        "wt_mc",
    }
)
# 前缀匹配的跟踪参数
TRACKING_PARAMS_PREFIX = ("utm_", "pk_", "mc_", "hsa_", "_hs", "ns_", "wt_")

_DEFAULT_PORTS = {"http": 80, "https": 443}


def _is_tracking_param(key: str) -> bool:
    k = key.strip().lower()
    if not k:
        return False
    if k in TRACKING_PARAMS_EXACT:
        return True
    return k.startswith(TRACKING_PARAMS_PREFIX)


def normalize_url(url: Optional[str], *, drop_tracking: bool = True) -> str:
    """规范化 URL：小写 scheme/host、去默认端口、去 fragment、去跟踪参数、参数排序。

    无法解析时原样返回（不做猜测），空输入返回空串。
    """
    if url is None:
        return ""
    raw = html_module.unescape(str(url)).strip()
    raw = raw.strip("<>\"'").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
        host = (parts.hostname or "").lower()
    except ValueError:
        return raw
    if not host:
        return raw

    scheme = (parts.scheme or "").lower()
    try:
        port = parts.port
    except ValueError:
        port = None

    netloc = host
    if port and _DEFAULT_PORTS.get(scheme) != port:
        netloc = f"{host}:{port}"

    path = parts.path or "/"
    path = re.sub(r"/{2,}", "/", path)
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/") or "/"

    query = ""
    if parts.query:
        if drop_tracking:
            pairs = [
                (k, v)
                for k, v in parse_qsl(parts.query, keep_blank_values=True)
                if not _is_tracking_param(k)
            ]
            pairs.sort()
            query = urlencode(pairs, doseq=True)
        else:
            query = parts.query

    return urlunsplit((scheme, netloc, path, query, ""))


def dedupe_key(url: Optional[str]) -> str:
    """跨 http/https 的去重键。仅用于比较，不用于展示。"""
    normalized = normalize_url(url)
    if not normalized:
        return ""
    if normalized.startswith("http://"):
        return "https://" + normalized[len("http://") :]
    return normalized


def url_host(url: Optional[str]) -> str:
    if not url:
        return ""
    try:
        return (urlsplit(str(url)).hostname or "").lower()
    except ValueError:
        return ""


def is_wechat_article_url(url: Optional[str]) -> bool:
    """仅接受 https://mp.weixin.qq.com/s... 形式。"""
    if not url:
        return False
    try:
        parts = urlsplit(str(url).strip())
    except ValueError:
        return False
    if (parts.scheme or "").lower() != "https":
        return False
    if (parts.hostname or "").lower() != "mp.weixin.qq.com":
        return False
    path = parts.path or ""
    return path == "/s" or path.startswith("/s/")


def host_matches(host: str, domain: str) -> bool:
    """域名后缀匹配，要求点边界（gov.cn 匹配 samr.gov.cn，不匹配 fakegov.cn）。"""
    host = (host or "").lower().strip(".")
    domain = (domain or "").lower().strip(".")
    if not host or not domain:
        return False
    return host == domain or host.endswith("." + domain)


def is_authority_url(url: Optional[str], domains: Iterable[str]) -> bool:
    host = url_host(url)
    if not host:
        return False
    return any(host_matches(host, d) for d in domains)


def hash_id(*parts: str, length: int = 12) -> str:
    raw = "\x1f".join(str(p or "") for p in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:length]


# --------------------------------------------------------------------------
# 日期处理
# --------------------------------------------------------------------------

_DATE_PATTERNS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%Y.%m.%d",
    "%Y年%m月%d日",
    "%d/%m/%Y",
    "%m/%d/%Y",
)


def parse_datetime(value: Any) -> Optional[datetime]:
    """尽量解析为 UTC 朴素 datetime；无法确定时返回 None（不猜测）。"""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return _from_epoch(value)

    text = str(value).strip()
    if not text:
        return None

    if re.fullmatch(r"\d{10}|\d{13}", text):
        return _from_epoch(int(text))

    # RFC 2822（RSS pubDate）
    if "," in text and re.search(r"[A-Za-z]{3}", text):
        try:
            parsed = parsedate_to_datetime(text)
            if parsed is not None:
                if parsed.tzinfo:
                    parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
                return parsed
        except (TypeError, ValueError):
            pass

    # ISO 8601
    iso_candidate = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(iso_candidate)
        if parsed.tzinfo:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    except ValueError:
        pass

    cleaned = re.sub(r"\s+", " ", text)
    for pattern in _DATE_PATTERNS:
        try:
            return datetime.strptime(cleaned, pattern)
        except ValueError:
            continue

    # 从长文本中抽取日期片段（例如「发布于 2026年3月5日」）
    match = re.search(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})", cleaned)
    if match:
        try:
            return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None
    return None


def _from_epoch(value: int | float) -> Optional[datetime]:
    seconds = float(value)
    if seconds > 1e11:  # 毫秒
        seconds /= 1000.0
    try:
        return datetime.fromtimestamp(seconds, tz=timezone.utc).replace(tzinfo=None)
    except (OverflowError, OSError, ValueError):
        return None


def format_date(value: Any) -> Optional[str]:
    """返回 YYYY-MM-DD 或 None。"""
    parsed = parse_datetime(value)
    if parsed is None:
        return None
    return parsed.strftime("%Y-%m-%d")


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso_now(moment: Optional[datetime] = None) -> str:
    moment = moment or now_utc()
    return moment.replace(microsecond=0).isoformat() + "Z"


def iso_week(value: Any) -> str:
    """ISO 周次，例如 2026-W39。"""
    if isinstance(value, datetime):
        day = value.date()
    elif isinstance(value, date):
        day = value
    else:
        parsed = parse_datetime(value)
        day = (parsed or now_utc()).date()
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


def week_bounds(value: Any) -> tuple[str, str]:
    """返回该时间点所在 ISO 周的 (周一, 周日)，格式 YYYY-MM-DD。"""
    if isinstance(value, datetime):
        day = value.date()
    elif isinstance(value, date):
        day = value
    else:
        day = (parse_datetime(value) or now_utc()).date()
    monday = day - timedelta(days=day.weekday())
    return monday.strftime("%Y-%m-%d"), (monday + timedelta(days=6)).strftime("%Y-%m-%d")


# --------------------------------------------------------------------------
# 文本处理
# --------------------------------------------------------------------------

_WS_RE = re.compile(r"[ \t\u00a0\u3000]+")
_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)


def strip_html(raw: Optional[str]) -> str:
    if not raw:
        return ""
    text = _SCRIPT_RE.sub(" ", str(raw))
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</(p|div|li|h[1-6])>", "\n", text, flags=re.IGNORECASE)
    text = _TAG_RE.sub(" ", text)
    text = html_module.unescape(text)
    return clean_text(text)


def clean_text(raw: Optional[str]) -> str:
    if raw is None:
        return ""
    text = unicodedata.normalize("NFKC", str(raw))
    text = text.replace("\x00", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [_WS_RE.sub(" ", line).strip() for line in text.split("\n")]
    lines = [line for line in lines if line]
    return "\n".join(lines).strip()


def shorten(text: Optional[str], limit: int) -> str:
    """按字符数截断，超出加省略号。用于对外发布摘要/引文。"""
    if not text:
        return ""
    value = clean_text(text).replace("\n", " ")
    if limit <= 0 or len(value) <= limit:
        return value
    return value[: max(1, limit - 1)].rstrip() + "…"


def sanitize_cell_text(value: Any) -> str:
    """电子表格公式注入防护：危险前缀加单引号，并移除控制字符。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    text = str(value)
    text = text.replace("\x00", "")
    text = re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f]", "", text)
    stripped = text.lstrip(" \t\r\n")
    if stripped[:1] in {"=", "+", "-", "@"}:
        text = "'" + text
    return text


def strip_control(text: Optional[str]) -> str:
    if not text:
        return ""
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(text))


# --------------------------------------------------------------------------
# 路径与写入
# --------------------------------------------------------------------------

class UnsafePath(ValueError):
    """路径越界或包含符号链接。"""


def resolve_within(base: Path | str, relative: str, *, allow_symlink: bool = False) -> Path:
    """把相对路径安全解析到 base 目录内，拒绝 ../ 越界与符号链接。"""
    base_path = Path(base).resolve()
    raw = unquote(str(relative or "")).replace("\\", "/")
    if "\x00" in raw:
        raise UnsafePath("路径包含空字节")
    parts = [p for p in raw.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise UnsafePath("路径包含上跳片段")
    current = base_path
    for part in parts:
        current = current / part
        if not allow_symlink and current.is_symlink():
            raise UnsafePath("路径包含符号链接")
    resolved = current.resolve()
    if resolved != base_path and base_path not in resolved.parents:
        raise UnsafePath("路径超出允许目录")
    return resolved


SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def is_safe_name(name: Optional[str]) -> bool:
    if not name or ".." in name:
        return False
    return bool(SAFE_NAME_RE.match(name))


def atomic_write_text(path: Path | str, text: str, *, encoding: str = "utf-8") -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp-", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def atomic_write_json(path: Path | str, payload: Any) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def read_json(path: Path | str) -> Any:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def read_json_or_none(path: Path | str) -> Any:
    try:
        return read_json(path)
    except (OSError, ValueError):
        return None


# --------------------------------------------------------------------------
# 运行锁（服务端与 CLI 共用，防止真实运行并发）
# --------------------------------------------------------------------------

class RunLock:
    """基于 flock 的互斥锁；无 fcntl 平台回退到独占创建 + 过期清理。"""

    def __init__(self, path: Path | str, *, stale_seconds: int = 6 * 3600) -> None:
        self.path = Path(path)
        self.stale_seconds = stale_seconds
        self._handle = None
        self._sentinel: Optional[Path] = None
        self.owner: dict[str, Any] = {}

    def acquire(self, *, owner: Optional[dict[str, Any]] = None) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        info = dict(owner or {})
        info.setdefault("pid", os.getpid())
        info.setdefault("started_at", iso_now())
        self.owner = info

        if fcntl is not None:
            handle = open(self.path, "a+", encoding="utf-8")
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                handle.close()
                return False
            handle.seek(0)
            handle.truncate()
            handle.write(json.dumps(info, ensure_ascii=False))
            handle.flush()
            self._handle = handle
            return True

        sentinel = self.path.with_suffix(self.path.suffix + ".sentinel")
        for attempt in range(2):
            try:
                fd = os.open(sentinel, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                if attempt == 0 and self._is_stale(sentinel):
                    try:
                        os.unlink(sentinel)
                    except OSError:
                        pass
                    continue
                return False
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(info, ensure_ascii=False))
            self._sentinel = sentinel
            return True
        return False

    def _is_stale(self, sentinel: Path) -> bool:
        try:
            age = now_utc().timestamp() - sentinel.stat().st_mtime
        except OSError:
            return False
        return age > self.stale_seconds

    def release(self) -> None:
        if self._handle is not None:
            try:
                if fcntl is not None:
                    fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            finally:
                self._handle.close()
                self._handle = None
        if self._sentinel is not None:
            try:
                os.unlink(self._sentinel)
            except OSError:
                pass
            self._sentinel = None

    def __enter__(self) -> "RunLock":
        if not self.acquire():
            raise RuntimeError("已有运行中的任务，无法获取运行锁")
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.release()
