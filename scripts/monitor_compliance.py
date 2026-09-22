#!/usr/bin/env python3
"""
周度线上站点合规监测脚本
功能：向指定站点发起监测运行请求，轮询直到运行结束，输出中文结果播报。

用法（Calendar script_args）：
  python3 monitor_compliance.py <result_mode> <site_url>

参数顺序：
  [1] result_mode  - 固定 display_only
  [2] site_url     - 站点地址，如 https://vstx7nsnwd.coze.site
"""

import sys
import json
import time
import requests

# ── 常量 ──
POLL_INTERVAL = 10          # 轮询间隔（秒）
MAX_POLL_SECONDS = 25 * 60  # 最长轮询 25 分钟
MAX_NETWORK_RETRIES = 3     # GET 网络失败最大重试次数
TERMINAL_STATUSES = {"success", "partial", "failed"}

# ── 参数读取 ──
result_mode = sys.argv[1] if len(sys.argv) > 1 else "display_only"
site_url = sys.argv[2].rstrip("/") if len(sys.argv) > 2 else ""

if not site_url:
    print(json.dumps({
        "status": "error",
        "result_mode": "display_only",
        "message": "❌ 合规监测启动失败：未提供站点 URL 参数。"
    }, ensure_ascii=False))
    sys.exit(0)

run_id = None
run_url = f"{site_url}/api/monitor/run"
status_url = f"{site_url}/api/monitor/status"


def submit(status, message, result_mode_val="display_only"):
    """统一输出并提交结果"""
    result = {
        "status": status,
        "result_mode": result_mode_val,
        "message": message,
    }
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0)


def submit_error(message):
    submit("error", message, "display_only")


# ── Step 1: 发起监测运行 ──
print(f"[INFO] 向 {run_url} 发起监测运行请求...")
try:
    resp = requests.post(
        run_url,
        json={"trigger": "schedule"},
        timeout=30,
    )
except requests.RequestException as e:
    submit_error(f"❌ 合规监测连接失败：无法连接到站点 {site_url}，错误信息：{e}")

if resp.status_code != 200:
    submit_error(
        f"❌ 合规监测启动失败：站点返回 HTTP {resp.status_code}，"
        f"响应内容：{resp.text[:500]}"
    )

try:
    body = resp.json()
except ValueError:
    submit_error(
        f"❌ 合规监测启动失败：站点响应不是合法 JSON，内容：{resp.text[:500]}"
    )

if not body.get("ok"):
    submit_error(
        f"❌ 合规监测启动失败：站点返回 ok=false，响应：{json.dumps(body, ensure_ascii=False)[:500]}"
    )

run_id = body.get("run_id", "")
if not run_id:
    submit_error("❌ 合规监测启动失败：响应中缺少 run_id。")

print(f"[INFO] 监测运行已启动，run_id={run_id}")


# ── Step 2: 轮询状态 ──
print(f"[INFO] 开始轮询状态，间隔 {POLL_INTERVAL}s，最长 {MAX_POLL_SECONDS}s...")
start_time = time.time()
consecutive_net_failures = 0
final_data = None

while True:
    elapsed = time.time() - start_time
    if elapsed > MAX_POLL_SECONDS:
        submit_error(
            f"⚠️ 合规监测轮询超时（已超过 25 分钟），运行 ID：{run_id}。\n"
            f"监测任务可能仍在后台执行，请稍后手动查看结果。"
        )

    time.sleep(POLL_INTERVAL)

    try:
        resp = requests.get(
            status_url,
            params={"run_id": run_id},
            timeout=15,
        )
        consecutive_net_failures = 0  # 重置网络失败计数
    except requests.RequestException as e:
        consecutive_net_failures += 1
        print(f"[WARN] 轮询网络失败 ({consecutive_net_failures}/{MAX_NETWORK_RETRIES})：{e}")
        if consecutive_net_failures >= MAX_NETWORK_RETRIES:
            submit_error(
                f"❌ 合规监测轮询失败：连续 {MAX_NETWORK_RETRIES} 次网络请求失败，"
                f"运行 ID：{run_id}。错误信息：{e}"
            )
        continue

    if resp.status_code != 200:
        consecutive_net_failures += 1
        print(f"[WARN] 轮询返回 HTTP {resp.status_code} ({consecutive_net_failures}/{MAX_NETWORK_RETRIES})")
        if consecutive_net_failures >= MAX_NETWORK_RETRIES:
            submit_error(
                f"❌ 合规监测轮询失败：连续 {MAX_NETWORK_RETRIES} 次异常响应，"
                f"运行 ID：{run_id}。最后一次 HTTP {resp.status_code}。"
            )
        continue

    try:
        data = resp.json()
    except ValueError:
        consecutive_net_failures += 1
        if consecutive_net_failures >= MAX_NETWORK_RETRIES:
            submit_error(
                f"❌ 合规监测轮询失败：连续 {MAX_NETWORK_RETRIES} 次响应非 JSON，运行 ID：{run_id}。"
            )
        continue

    run = data.get("run", data)  # 兼容 {"run": {...}} 和直接返回
    status = run.get("status", "")
    print(f"[INFO] 当前状态：{status}，已耗时 {int(time.time() - start_time)}s")

    if status in TERMINAL_STATUSES:
        final_data = run
        break


# ── Step 3: 格式化播报 ──
status = final_data.get("status", "unknown")
items_collected = final_data.get("items_collected", 0)
items_relevant = final_data.get("items_relevant", 0)
items_high = final_data.get("items_high", 0)
items_verified = final_data.get("items_verified", 0)
coverage_warnings = final_data.get("coverage_warnings", [])

# 状态映射
status_map = {
    "success": "✅ 成功",
    "partial": "⚠️ 部分完成",
    "failed": "❌ 失败",
}
status_text = status_map.get(status, f"未知({status})")

# 构建播报文本
lines = [f"📋 **站点合规监测报告**", f"运行状态：{status_text}"]

lines.append(f"采集条数：{items_collected}")
lines.append(f"合规相关数：{items_relevant}")
lines.append(f"高相关数：{items_high}")
lines.append(f"官方核验数：{items_verified}")

if coverage_warnings:
    lines.append("")
    lines.append("⚠️ **覆盖缺失警告：**")
    for w in coverage_warnings:
        lines.append(f"  • {w}")

lines.append("")
lines.append(f"运行 ID：{run_id}")

message = "\n".join(lines)
submit("success", message, "display_only")
