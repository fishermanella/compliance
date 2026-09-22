"""命令行入口：真实/演示运行、状态查询、导出、链接维护。

与本地服务共用同一把运行锁，避免 CLI 与服务同时跑真实采集。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from .config import configured_flags, describe_config, load_config, path_of, redacted
from .export_xlsx import column_headers, export_to_path
from .pipeline import DemoUnavailable, Pipeline, RunLocked
from .schema import normalize_demo_payload, validate_payload
from .store import Store
from .util import atomic_write_text

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_BUSY = 2


def _base_dir(value: Optional[str]) -> Path:
    if value:
        return Path(value).resolve()
    return Path(__file__).resolve().parent.parent


def cmd_run(args: argparse.Namespace) -> int:
    base = _base_dir(args.base_dir)
    config = load_config(base)

    def on_step(label: str, status: str) -> None:
        print(f"  [{status:>8}] {label}", flush=True)

    print(f"开始运行（mode={args.mode}）…")
    pipeline = Pipeline(base, config)
    try:
        outcome = pipeline.run(mode=args.mode, use_lock=args.mode != "demo", on_step=on_step)
    except RunLocked as exc:
        print(f"无法开始：{exc}", file=sys.stderr)
        return EXIT_BUSY
    except DemoUnavailable as exc:
        print(f"演示数据不可用：{exc}", file=sys.stderr)
        return EXIT_ERROR
    except Exception as exc:
        print(f"运行失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_ERROR

    result = outcome.to_result()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"完成：run_id={outcome.run_id} mode={outcome.mode}")
        print(f"  事件数：{result['event_count']}  统计：{result['stats']}")
        print(f"  报表：{outcome.report_path}")
        if outcome.data_url:
            print(f"  数据：{outcome.data_url}")
        for warning in result["warnings"][:20]:
            print(f"  ! {warning}")
    return EXIT_OK


def cmd_status(args: argparse.Namespace) -> int:
    base = _base_dir(args.base_dir)
    config = load_config(base)
    store = Store(base, config)
    flags = configured_flags(config)
    history = store.read_history()
    payload = store.read_latest()

    print("Compliance Radar 后端状态")
    print(f"  项目目录：{base}")
    print(f"  已配置：{ {k: bool(v) for k, v in flags.items()} }")
    print(f"  历史运行数：{len(history)}")
    if history:
        last = history[-1]
        print(f"  最近一次：{last.get('id')} @ {last.get('generated_at')}（{last.get('event_count')} 条）")
    if isinstance(payload, dict):
        problems = validate_payload(payload)
        print(f"  latest.json：{'契约校验通过' if not problems else '契约校验失败 ' + str(problems[:3])}")
    else:
        print("  latest.json：不存在（尚未执行真实运行）")
    print(f"  导出列数：{len(column_headers())}")
    print(f"  配置摘要：{json.dumps(describe_config(config), ensure_ascii=False)}")
    if args.verbose:
        print("  完整配置（密钥已脱敏）：")
        print(json.dumps(redacted(config), ensure_ascii=False, indent=2))
    return EXIT_OK


def cmd_export(args: argparse.Namespace) -> int:
    base = _base_dir(args.base_dir)
    config = load_config(base)
    store = Store(base, config)

    if args.run:
        payload = store.read_demo() if args.run == "demo" else store.read_week(args.run)
        if not isinstance(payload, dict):
            print(f"未找到运行 {args.run}", file=sys.stderr)
            return EXIT_ERROR
        if args.run == "demo":
            payload = normalize_demo_payload(payload)
        run_id = args.run
    elif args.mode == "demo":
        payload = store.read_demo()
        if not isinstance(payload, dict):
            print("未找到演示数据 public/data/demo.json（由前端/主控提供）", file=sys.stderr)
            return EXIT_ERROR
        payload = normalize_demo_payload(payload)
        run_id = "demo"
    else:
        payload = store.read_latest()
        if not isinstance(payload, dict):
            print("尚未执行真实运行，latest.json 不存在", file=sys.stderr)
            return EXIT_ERROR
        run_id = str((payload.get("period") or {}).get("week") or "latest")

    problems = validate_payload(payload)
    if problems:
        print("payload 不符合契约：" + "；".join(problems[:6]), file=sys.stderr)
        return EXIT_ERROR

    out = Path(args.out) if args.out else store.reports_dir / f"{run_id}{'-demo' if payload.get('mode') == 'demo' else ''}.xlsx"
    export_to_path(payload, out)
    print(f"已导出：{out}（{len(payload.get('events') or [])} 条，mode={payload.get('mode')}）")
    return EXIT_OK


def cmd_links(args: argparse.Namespace) -> int:
    base = _base_dir(args.base_dir)
    config = load_config(base)
    links_path = path_of(config, "wechat_links_file", base)

    from .collect_wechat import merge_links_text, read_links_file

    text, urls = read_links_file(links_path)
    if args.add:
        merged, saved, invalid = merge_links_text(text, "\n".join(args.add))
        if invalid:
            print("存在非法链接，未写入任何内容：", file=sys.stderr)
            for item in invalid:
                print(f"  - {item['value']}：{item['reason']}", file=sys.stderr)
            return EXIT_ERROR
        links_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(links_path, merged)
        print(f"已写入 {saved} 条新链接（文件：{links_path}）")
        return EXIT_OK

    print(f"链接文件：{links_path}")
    print(f"有效链接数：{len(urls)}")
    for url in urls:
        print(f"  - {url}")
    if not text.strip():
        print("（文件为空或不存在；请用 --add 添加，或由前端 POST /api/links 维护）")
    return EXIT_OK


def cmd_verify(args: argparse.Namespace) -> int:
    """校验发布数据的 payload 契约（CI 用）。"""
    base = _base_dir(args.base_dir)
    config = load_config(base)
    store = Store(base, config)
    payload = store.read_latest()
    if not isinstance(payload, dict):
        print("latest.json 不存在：尚无真实运行结果", file=sys.stderr)
        return EXIT_ERROR
    problems = validate_payload(payload)
    if problems:
        print("payload 契约校验失败：", file=sys.stderr)
        for item in problems[:20]:
            print(f"  - {item}", file=sys.stderr)
        return EXIT_ERROR
    history = store.read_history()
    print(
        f"契约校验通过：mode={payload.get('mode')} 事件数={len(payload.get('events') or [])} "
        f"历史条目={len(history)} 周期={(payload.get('period') or {}).get('week')}"
    )
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m monitor.cli", description="Compliance Radar CLI")
    parser.add_argument("--base-dir", default=None, help="项目根目录，默认仓库根目录")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="执行一次采集流水线")
    run.add_argument("--mode", choices=["live", "demo"], default="live")
    run.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    run.set_defaults(func=cmd_run)

    status = sub.add_parser("status", help="查看配置与运行状态")
    status.add_argument("--verbose", action="store_true", help="输出脱敏后的完整配置")
    status.set_defaults(func=cmd_status)

    export = sub.add_parser("export", help="导出 xlsx")
    export.add_argument("--mode", choices=["live", "demo"], default="live")
    export.add_argument("--run", default=None, help="历史 run_id，或 demo")
    export.add_argument("--out", default=None, help="输出文件路径")
    export.set_defaults(func=cmd_export)

    links = sub.add_parser("links", help="查看或追加微信公众号链接")
    links.add_argument("--add", nargs="*", default=None, help="追加链接（全部合法才会写入）")
    links.set_defaults(func=cmd_links)

    verify = sub.add_parser("verify", help="校验 public/data/latest.json 是否符合 payload 契约")
    verify.set_defaults(func=cmd_verify)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("已中断。", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
