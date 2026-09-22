"""配置加载：内置默认值 → config.example.json → config.local.json（git 忽略）→ 环境变量 / .env。

安全约定：
- 密钥只来自环境变量或 .env，绝不写入会被静态服务的目录。
- 本模块不做任何日志输出；对外的展示一律走 redacted()。
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any, Mapping, Optional

# 权威来源域名白名单（仅域名后缀，可由 config.local.json 覆盖）
DEFAULT_AUTHORITY_DOMAINS = (
    # 中国
    "gov.cn",
    "npc.gov.cn",
    "cac.gov.cn",
    "miit.gov.cn",
    "mofcom.gov.cn",
    "samr.gov.cn",
    "csrc.gov.cn",
    "nfra.gov.cn",
    "tc260.org.cn",
    # 欧盟
    "europa.eu",
    "eur-lex.europa.eu",
    "edpb.europa.eu",
    "ec.europa.eu",
    # 美国
    "ftc.gov",
    "congress.gov",
    "federalregister.gov",
    "nist.gov",
    "sec.gov",
    # 美国加州
    "leginfo.legislature.ca.gov",
    "cppa.ca.gov",
    "oag.ca.gov",
    # 日本
    "ppc.go.jp",
    "meti.go.jp",
    "cao.go.jp",
    # 韩国
    "pipc.go.kr",
    "msit.go.kr",
)

DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 ComplianceRadar/0.1 (+internal-monitoring)"
)

DEFAULTS: dict[str, Any] = {
    "server": {
        "host": "127.0.0.1",
        "port": 8765,
        "api_token_header": "X-Radar-Request",
        "api_token_value": "1",
    },
    "paths": {
        "public_dir": "public",
        "data_dir": "data",
        "raw_dir": "data/raw",
        "wechat_links_file": "data/wechat_links.txt",
        "demo_payload": "public/data/demo.json",
        "latest_payload": "public/data/latest.json",
        "history_index": "public/data/history.json",
        "weeks_dir": "public/data/weeks",
        "reports_dir": "public/reports",
        "lock_file": "data/.radar-run.lock",
        "config_example": "config.example.json",
        "config_local": "config.local.json",
    },
    "privacy": {
        "max_quote_chars": 300,
        "max_fact_chars": 600,
        "max_summary_chars": 400,
    },
    "schedule": {
        "label": "每周一 09:00 · 北京时间",
        "engine": "WorkBuddy / GitHub Actions 待部署",
    },
    "dataguidance": {
        "name": "DataGuidance",
        "url": "",
        "engine": "playwright",
        "max_pages": 20,
        "timeout_ms": 45000,
        "user_agent": DEFAULT_UA,
        "count_regex": r"共\s*([0-9][0-9,]{0,9})\s*条",
        "selectors": {
            "all_tab": "",
            "expected_count": "",
            "item": "",
            "item_title": "",
            "item_link": "",
            "item_date": "",
            "item_authority": "",
            "item_jurisdiction": "",
            "item_type": "",
            "item_category": "",
            "next_page": "",
        },
    },
    "wechat": {
        "name": "微信公众号",
        "links_file": "data/wechat_links.txt",
        "request_timeout": 20,
        "max_items": 50,
        "use_playwright_fallback": True,
        "playwright_timeout_ms": 30000,
        "min_delay_seconds": 1.5,
        "user_agent": DEFAULT_UA,
    },
    "podcast": {
        "name": "播客 · 那一片数据星辰",
        "rss_url": "",
        "past_days": 7,
        "max_items": 30,
        "request_timeout": 20,
        "user_agent": DEFAULT_UA,
    },
    "official": {
        "domains": list(DEFAULT_AUTHORITY_DOMAINS),
        "max_links_per_event": 3,
        "request_timeout": 15,
        "user_agent": DEFAULT_UA,
        "fetch_pages": True,
        "prefer_keywords": ["公告", "条例", "办法", "规定", "指引", "指南", "通知", "regulation", "guidance", "notice"],
    },
    "ai": {
        "base_url": "https://api.openai.com/v1",
        "model": "",
        "api_key": "",
        "timeout": 90,
        "max_events_per_request": 20,
        "max_input_chars": 1500,
        "temperature": 0,
    },
    "human_verified": {},
}

# 环境变量 → 配置路径
ENV_MAP = {
    "OPENAI_API_KEY": "ai.api_key",
    "OPENAI_MODEL": "ai.model",
    "OPENAI_BASE_URL": "ai.base_url",
    "RADAR_DATAGUIDANCE_URL": "dataguidance.url",
    "RADAR_PODCAST_RSS_URL": "podcast.rss_url",
    "RADAR_WECHAT_LINKS_FILE": "wechat.links_file",
    "RADAR_PORT": "server.port",
    "RADAR_HOST": "server.host",
}


def deep_merge(base: dict[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    for key, value in (overlay or {}).items():
        if isinstance(value, Mapping) and isinstance(base.get(key), dict):
            deep_merge(base[key], value)
        else:
            base[key] = copy.deepcopy(value)
    return base


def get(config: Mapping[str, Any], path: str, default: Any = None) -> Any:
    node: Any = config
    for part in path.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return default
        node = node[part]
    return node


def set_path(config: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    node = config
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


def parse_dotenv(path: Path | str) -> dict[str, str]:
    """极简 .env 解析：KEY=VALUE，支持 # 注释、export 前缀、成对引号。不打印任何内容。"""
    values: dict[str, str] = {}
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return values
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def load_config(
    base_dir: Path | str,
    *,
    env: Optional[Mapping[str, str]] = None,
    load_env_file: bool = True,
) -> dict[str, Any]:
    base = Path(base_dir).resolve()
    config = copy.deepcopy(DEFAULTS)

    example_path = base / str(get(config, "paths.config_example"))
    if example_path.is_file():
        try:
            deep_merge(config, json.loads(example_path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass

    local_path = base / str(get(config, "paths.config_local"))
    if local_path.is_file():
        try:
            deep_merge(config, json.loads(local_path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass

    environ: dict[str, str] = dict(os.environ if env is None else env)
    if load_env_file:
        dotenv_path = base / ".env"
        for key, value in parse_dotenv(dotenv_path).items():
            environ.setdefault(key, value)

    for env_key, config_path in ENV_MAP.items():
        if environ.get(env_key):
            raw = environ[env_key]
            if config_path == "server.port":
                try:
                    set_path(config, config_path, int(raw))
                except ValueError:
                    continue
            else:
                set_path(config, config_path, raw)

    # 复合型环境变量（CI 用，避免把选择器写进仓库）
    selectors_raw = environ.get("RADAR_DATAGUIDANCE_SELECTORS")
    if selectors_raw:
        try:
            parsed_selectors = json.loads(selectors_raw)
            if isinstance(parsed_selectors, dict):
                deep_merge(config, {"dataguidance": {"selectors": parsed_selectors}})
        except ValueError:
            pass

    domains_raw = environ.get("RADAR_OFFICIAL_DOMAINS")
    if domains_raw:
        domains = [item.strip().lower() for item in domains_raw.split(",") if item.strip()]
        if domains:
            set_path(config, "official.domains", domains)

    config["_base_dir"] = str(base)
    return config


def path_of(config: Mapping[str, Any], key: str, base_dir: Optional[Path | str] = None) -> Path:
    """解析 paths.* 配置为绝对路径。"""
    base = Path(base_dir or config.get("_base_dir") or ".")
    relative = str(get(config, f"paths.{key}", ""))
    return (base / relative).resolve()


def configured_flags(config: Mapping[str, Any]) -> dict[str, bool]:
    return {
        "openai": bool(get(config, "ai.api_key")) and bool(get(config, "ai.model")),
        "dataguidance": bool(get(config, "dataguidance.url")),
        "podcast": bool(get(config, "podcast.rss_url")),
        "wechat": bool(get(config, "wechat.links_file")),
    }


def redacted(config: Mapping[str, Any]) -> dict[str, Any]:
    """返回可安全展示/序列化的配置副本（不含密钥）。"""
    safe = copy.deepcopy({k: v for k, v in config.items() if not k.startswith("_")})
    ai = safe.get("ai")
    if isinstance(ai, dict):
        present = bool(ai.get("api_key"))
        ai["api_key"] = "***" if present else ""
        ai["api_key_present"] = present
    return safe


def describe_config(config: Mapping[str, Any]) -> dict[str, Any]:
    """给 /api/status 用的最小信息，不含任何密钥。"""
    flags = configured_flags(config)
    return {
        "configured": flags,
        "dataguidance_url_set": bool(get(config, "dataguidance.url")),
        "dataguidance_selectors_set": any(
            bool(v) for v in (get(config, "dataguidance.selectors", {}) or {}).values()
        ),
        "podcast_rss_set": bool(get(config, "podcast.rss_url")),
        "openai_model": str(get(config, "ai.model", "") or ""),
        "openai_base_url": str(get(config, "ai.base_url", "") or ""),
        "schedule": {
            "label": str(get(config, "schedule.label", "")),
            "engine": str(get(config, "schedule.engine", "")),
        },
    }
