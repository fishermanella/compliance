#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DataGuidance 监管动态抓取脚本（仅用于本地测试 / 协议风险评估）

目标页面：
    https://www.dataguidance.com/info?order=DESC_publishedOn&date=last_7_days

输出文件：
    assets/data/events-draft.json

⚠️ 重要说明：
    DataGuidance 为付费商业数据库。本脚本仅用于本地技术验证与协议风险评估，
    请勿用于高频抓取、绕过付费墙或任何商业用途。抓取行为应遵守目标网站的
    robots.txt 与使用条款。

字段结构（草稿，供人工审核后再覆盖正式 events.json）：
    [{"id":1,"juris":"JP","org":"...","type":"指南","title":"...","summary":"...",
      "date":"2026-09-18","relevance":"","topics":[],"evidence":"","source":"DataGuidance"}]

约定：
    - relevance / evidence 留空（不自动填写，需人工审核）
    - topics 留空数组（主题分类由人工完成）
    - juris / org / type 为尽力推断，无法确定时留空字符串
    - 无法确认的日期留空，禁止编造
"""

import json
import os
import re
import sys
import time

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
TARGET_URL = "https://www.dataguidance.com/info?order=DESC_publishedOn&date=last_7_days"
OUTPUT_PATH = os.path.join("assets", "data", "events-draft.json")
REQUEST_DELAY = 2.0          # 每次请求间隔（秒），避免高频请求
REQUEST_TIMEOUT = 30         # 单次请求超时（秒）
MAX_RETRIES = 3              # 最大重试次数

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Cache-Control": "no-cache",
}

# 法域关键词 -> 缩写（仅用于草稿粗略判断，供人工复核）
JURISDICTION_HINTS = {
    "US-CA": ["California", "加州", "CPPA", "CCPA", "CPRA"],
    "CN": ["中国", "网信办", "人大", "CAC", "PIPL", "China", "个人信息保护法", "数据安全法"],
    "EU": ["欧盟", "European", "GDPR", "EDPB", "CJEU", "EU AI Act", "AI Act",
           "European Commission", "European Parliament", "Council"],
    "US": ["美国", "FTC", "联邦贸易委员会", "United States", "CCPA", "CPRA"],
    "JP": ["日本", "Japan", "APPI", "PPC", "个人信息保护委员会"],
    "KR": ["韩国", "Korea", "PIPA", "PIPC"],
}

# 文件类型关键词 -> 类型（草稿粗略判断）
TYPE_HINTS = [
    (["guidance", "guideline", "指南", "指引", "意见", "FAQ"], "指南"),
    (["decision", "ruling", "judgment", "判决", "裁决"], "法院判决"),
    (["enforcement", "fine", "penalty", "执法", "处罚"], "执法动态"),
    (["consultation", "征求意见", "咨询"], "咨询文件"),
    (["draft", "草案", "拟"], "草案"),
    (["report", "报告"], "监管报告"),
    (["standard", "标准"], "标准"),
    (["act", "law", "regulation", "法案", "法律", "条例", "法令"], "法律"),
    (["policy", "政策", "声明"], "政策声明"),
]

# 疑似付费墙 / 登录墙的页面特征
PAYWALL_HINTS = ["sign in", "log in", "login", "subscribe", "subscription",
                 "request a demo", "free trial", "premium", "付费"]


def log(msg):
    print("[crawler] " + msg, flush=True)


def fetch_page(url):
    """带重试与延时地抓取页面，返回 response 文本；失败抛异常。"""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            log("请求 %s（第 %d 次）" % (url, attempt))
            resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            if resp.status_code in (401, 403, 429):
                log("收到 %d：可能触发登录墙或限流，停止重试。" % resp.status_code)
                raise RuntimeError("HTTP %d（登录墙/限流）" % resp.status_code)
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as exc:
            log("请求异常：%s" % exc)
            if attempt < MAX_RETRIES:
                log("等待 %.1f 秒后重试……" % REQUEST_DELAY)
                time.sleep(REQUEST_DELAY)
            else:
                raise

    raise RuntimeError("抓取失败")


def detect_paywall(html, soup):
    """检测页面是否疑似付费墙/登录墙。"""
    text = soup.get_text(" ", strip=True).lower()
    for hint in PAYWALL_HINTS:
        if hint in text:
            return True
    return False


def normalize_date(raw):
    """把多种日期格式归一化为 YYYY-MM-DD；无法识别返回空字符串。"""
    if not raw:
        return ""
    raw = raw.strip()
    # 2026-09-18 / 2026/09/18
    m = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", raw)
    if m:
        return "%s-%02d-%02d" % (m.group(1), int(m.group(2)), int(m.group(3)))
    # 18 Sep 2026 / Sep 18, 2026 / September 18 2026
    months = {
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    }
    m = re.search(
        r"(\d{1,2})\s+([A-Za-z]{3,9})[.,]?\s+(\d{4})", raw
    ) or re.search(
        r"([A-Za-z]{3,9})[.,]?\s+(\d{1,2}),?\s+(\d{4})", raw
    )
    if m:
        try:
            if m.group(2).isalpha():
                day, month_name, year = m.group(1), m.group(2), m.group(3)
            else:
                month_name, day, year = m.group(1), m.group(2), m.group(3)
            month = months.get(month_name[:3].lower())
            if month:
                return "%s-%02d-%02d" % (year, month, int(day))
        except (ValueError, AttributeError):
            pass
    return ""


def detect_jurisdiction(text):
    for code, keywords in JURISDICTION_HINTS.items():
        for kw in keywords:
            if kw.lower() in text.lower():
                return code
    return ""


def detect_type(text):
    lowered = text.lower()
    for keywords, typ in TYPE_HINTS:
        for kw in keywords:
            if kw.lower() in lowered:
                return typ
    return "其他"


def extract_items(html):
    """从 HTML 中提取条目（title, url, date, summary）列表。尽力而为。"""
    soup = BeautifulSoup(html, "html.parser")
    items = []

    # 常见列表结构：article / li 内嵌链接 / 带 h 标题的链接
    candidates = []
    for sel in ["article", "li.article", "div.article", "div.list-item",
                "div.card", "li.list-item", "div.row", "tr"]:
        candidates.extend(soup.select(sel))

    seen = set()
    if candidates:
        for node in candidates:
            link = node.find("a", href=True)
            if not link:
                continue
            title = link.get_text(" ", strip=True)
            if not title or len(title) < 5:
                continue
            if title in seen:
                continue
            seen.add(title)
            items.append({
                "title": title,
                "url": link.get("href"),
                "date": "",
                "summary": "",
                "node": node,
            })
    else:
        # 兜底：所有包含日期特征的链接
        for link in soup.find_all("a", href=True):
            text = link.get_text(" ", strip=True)
            if not text or len(text) < 5 or text in seen:
                continue
            seen.add(text)
            items.append({
                "title": text,
                "url": link.get("href"),
                "date": "",
                "summary": "",
                "node": link,
            })

    # 尽力从节点文本中提取日期与摘要
    for it in items:
        node_text = it["node"].get_text(" ", strip=True)
        it["date"] = normalize_date(node_text)
        # 摘要：标题之外的首段较长的文本
        desc = re.sub(r"\s+", " ", node_text).strip()
        if desc and desc != it["title"]:
            it["summary"] = desc[:300]

    return items


def build_draft(items):
    events = []
    for idx, it in enumerate(items, start=1):
        text = (it["title"] or "") + " " + (it.get("summary") or "")
        events.append({
            "id": idx,
            "juris": detect_jurisdiction(text),
            "org": "",
            "type": detect_type(text),
            "title": it["title"],
            "summary": it.get("summary", ""),
            "date": it.get("date", ""),
            "relevance": "",
            "topics": [],
            "evidence": "",
            "source": "DataGuidance",
        })
    return events


def write_output(events):
    out_dir = os.path.dirname(OUTPUT_PATH)
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(events, f, ensure_ascii=False, indent=2)
    log("已写入 %s（共 %d 条草稿）" % (OUTPUT_PATH, len(events)))


def main():
    log("启动 DataGuidance 监管动态抓取（仅本地测试）")
    try:
        html = fetch_page(TARGET_URL)
    except RuntimeError as exc:
        log("抓取失败：%s" % exc)
        log("输出空草稿并退出。")
        write_output([])
        sys.exit(1)

    soup = BeautifulSoup(html, "html.parser")
    if detect_paywall(html, soup):
        log("⚠️ 页面疑似付费墙/登录墙，无法获取完整列表。")
        write_output([])
        sys.exit(1)

    items = extract_items(html)
    if not items:
        log("⚠️ 未解析到任何条目（页面可能是 JS 渲染的 SPA）。")
        write_output([])
        sys.exit(0)

    events = build_draft(items)
    write_output(events)

    log("抓取完成：共 %d 条草稿（relevance / evidence 留空，待人工审核）" % len(events))
    sys.exit(0)


if __name__ == "__main__":
    main()
