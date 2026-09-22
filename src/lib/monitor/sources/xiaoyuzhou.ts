// ============ 小宇宙「那一片数据星辰」采集适配器 ============
// 页面为 Next.js SSR，__NEXT_DATA__ 中包含 episodes 数组（eid/title/shownotes/pubDate）
import { fetchWithTimeout, stripHtml, truncate, recentDaySet, parseDateFlexible } from "../util";
import type { RawItem, SourceReport } from "../types";

const PODCAST_URL = "https://www.xiaoyuzhoufm.com/podcast/670a08d326fe2954b1a21367";
const SOURCE_NAME = "那一片数据星辰";

interface XyzEpisode {
  eid: string;
  title: string;
  description?: string;
  shownotes?: string;
  pubDate?: string;
}

export async function collectXiaoyuzhou(): Promise<{ items: RawItem[]; report: SourceReport }> {
  const report: SourceReport = {
    source: SOURCE_NAME,
    status: "failed",
    coverage_status: "complete",
    collected_count: 0,
  };
  try {
    const res = await fetchWithTimeout(PODCAST_URL, {
      headers: { Accept: "text/html" },
      timeoutMs: 25000,
    });
    if (!res.ok) {
      report.error = `小宇宙页面请求失败 HTTP ${res.status}`;
      return { items: [], report };
    }
    const html = await res.text();
    const m = html.match(/<script id="__NEXT_DATA__" type="application\/json">([\s\S]*?)<\/script>/);
    if (!m) {
      report.error = "小宇宙页面结构变化：未找到 __NEXT_DATA__ 数据";
      return { items: [], report };
    }
    const data = JSON.parse(m[1]);
    const episodes = findEpisodes(data);
    if (!episodes || episodes.length === 0) {
      report.error = "小宇宙页面结构变化：未找到节目列表";
      return { items: [], report };
    }
    const last7 = recentDaySet(7);
    const items: RawItem[] = [];
    for (const ep of episodes) {
      if (!ep?.eid || !ep.title) continue;
      const pub = parseDateFlexible(ep.pubDate);
      if (!pub || !last7.has(pub)) continue; // 仅最近 7 天
      const shownotes = stripHtml(ep.shownotes || "");
      const description = stripHtml(ep.description || "");
      const content = truncate([description, shownotes].filter(Boolean).join("\n\n"), 8000);
      items.push({
        source: SOURCE_NAME,
        collection_method: "podcast",
        title: ep.title.trim(),
        published_date: pub,
        jurisdiction: null,
        authority: null,
        raw_content: content || ep.title,
        source_url: `https://www.xiaoyuzhoufm.com/episode/${ep.eid}`,
        discovery_source: "小宇宙-那一片数据星辰",
      });
    }
    report.status = "success";
    report.collected_count = items.length;
    report.expected_count = episodes.length;
    return { items, report };
  } catch (e) {
    report.error = `小宇宙采集异常: ${e instanceof Error ? e.message : String(e)}`;
    return { items: [], report };
  }
}

function findEpisodes(obj: unknown, depth = 0): XyzEpisode[] | null {
  if (depth > 8) return null;
  if (Array.isArray(obj)) {
    if (obj.length > 0 && obj.every((o) => o && typeof o === "object" && ("eid" in (o as object) || "pubDate" in (o as object)))) {
      return obj as XyzEpisode[];
    }
    for (const o of obj) {
      const r = findEpisodes(o, depth + 1);
      if (r) return r;
    }
    return null;
  }
  if (obj && typeof obj === "object") {
    for (const v of Object.values(obj as Record<string, unknown>)) {
      const r = findEpisodes(v, depth + 1);
      if (r) return r;
    }
  }
  return null;
}
