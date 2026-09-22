// ============ 微信公众号「数据何规」文章采集适配器 ============
// 文章 URL 由用户每周投喂（等价 PRD 的 data/wechat_links.txt 人工投喂流程）
import { fetchWithTimeout, stripHtml, truncate, firstMatch, parseDateFlexible } from "../util";
import type { RawItem, SourceReport } from "../types";

const SOURCE_NAME = "数据何规";

export interface WechatFetchResult {
  ok: boolean;
  error?: string;
  item?: RawItem;
}

export async function fetchWechatArticle(url: string): Promise<WechatFetchResult> {
  try {
    const res = await fetchWithTimeout(url, {
      headers: { Accept: "text/html" },
      timeoutMs: 25000,
    });
    if (!res.ok) {
      return { ok: false, error: `微信文章请求失败 HTTP ${res.status}` };
    }
    const html = await res.text();
    if (html.includes("环境异常") || html.includes("去验证")) {
      return { ok: false, error: "微信访问验证拦截（IP 异常），请稍后重试或更换链接" };
    }
    const title = firstMatch(html, [
      /<meta property="og:title" content="([^"]+)"/,
      /<h1[^>]*id="activity-name"[^>]*>\s*([\s\S]*?)\s*<\/h1>/,
      /var msg_title = '([^']+)'/,
    ]);
    if (!title) {
      return { ok: false, error: "未能解析文章标题（链接可能已失效或内容被删除）" };
    }
    const author =
      firstMatch(html, [
        /<meta property="og:article:author" content="([^"]+)"/,
        /<span[^>]*id="js_name"[^>]*>\s*([\s\S]*?)\s*<\/span>/,
        /var nickname = htmlDecode\("([^"]+)"\)/,
      ]) ?? SOURCE_NAME;
    const createTime = firstMatch(html, [
      /var ct = "(\d{10})"/,
      /createTime\s*=\s*'(\d{10})'/,
      /"create_time":(\d{10})/,
    ]);
    const publishTime = firstMatch(html, [
      /<em[^>]*id="publish_time"[^>]*>\s*([\s\S]*?)\s*<\/em>/,
      /"publish_time":"([^"]+)"/,
    ]);
    const published = parseDateFlexible(createTime ?? publishTime);
    const bodyMatch = html.match(/<div[^>]*id="js_content"[^>]*>([\s\S]*?)<\/div>\s*<script/);
    const rawContent = truncate(bodyMatch ? stripHtml(bodyMatch[1]) : "", 12000);
    const item: RawItem = {
      source: author === SOURCE_NAME ? SOURCE_NAME : author,
      collection_method: "manual_url",
      title: title.replace(/&nbsp;/g, " ").trim(),
      published_date: published,
      jurisdiction: "CN",
      authority: author === SOURCE_NAME ? SOURCE_NAME : author,
      raw_content: rawContent || title,
      source_url: url,
      discovery_source: "微信公众号-数据何规（人工投喂）",
    };
    return { ok: true, item };
  } catch (e) {
    return { ok: false, error: `微信文章抓取异常: ${e instanceof Error ? e.message : String(e)}` };
  }
}

export async function collectWechat(
  links: { id: string; url: string }[]
): Promise<{ items: RawItem[]; report: SourceReport; markStatus: (id: string, status: string, error?: string) => Promise<void> }> {
  const report: SourceReport = {
    source: SOURCE_NAME,
    status: "failed",
    coverage_status: "user_provided",
    collected_count: 0,
  };
  const items: RawItem[] = [];
  const errors: string[] = [];
  let failed = 0;
  for (const link of links) {
    const r = await fetchWechatArticle(link.url);
    if (r.ok && r.item) {
      items.push(r.item);
      await markStatusSafe(link.id, "fetched");
    } else {
      failed++;
      errors.push(`${truncate(link.url, 60)}: ${r.error}`);
      await markStatusSafe(link.id, r.error?.includes("失效") ? "invalid" : "unavailable", r.error);
    }
  }
  if (links.length === 0) {
    report.status = "unavailable";
    report.coverage_status = "user_provided";
    report.error = "本周未投喂任何微信文章链接（可在页面提交 mp.weixin.qq.com 链接）";
  } else if (items.length === 0) {
    report.status = "failed";
    report.error = `投喂的 ${links.length} 条链接全部解析失败：${errors.slice(0, 3).join("；")}`;
  } else {
    report.status = "success";
    report.collected_count = items.length;
    report.expected_count = links.length;
    if (failed > 0) {
      report.coverage_status = "incomplete";
      report.error = `${failed} 条链接解析失败：${errors.slice(0, 2).join("；")}`;
    }
  }
  return { items, report, markStatus: markStatusSafe };
}

async function markStatusSafe(id: string, status: string, error?: string): Promise<void> {
  try {
    const { updateWechatLinkStatus } = await import("../store");
    await updateWechatLinkStatus(id, status, error);
  } catch {
    // 状态更新失败不阻断主流程
  }
}
