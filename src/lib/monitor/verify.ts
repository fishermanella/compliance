// ============ 官方来源核验模块（PRD：优先官方原文 > 官方指南 > 可信二手 > 新闻媒体） ============
// 通过公开搜索定位官方来源；只引用搜索候选列表中真实存在的 URL，AI 不编造链接。
import { SearchClient, LLMClient, Config } from "coze-coding-dev-sdk";
import { parseJsonLoose, truncate } from "./util";
import type { ComplianceEventRow } from "./store";

interface OfficialPick {
  tier: "official" | "trusted_secondary" | "news";
  url: string;
  name: string;
  note?: string;
}

const OFFICIAL_HOST_PATTERNS = [
  /\.gov\.cn$/, /\.gov$/, /\.europa\.eu$/, /\.eu$/, /go\.jp$/, /go\.kr$/, /\.gc\.ca$/, /\.gov\.au$/, /\.gov\.sg$/,
  /\.gouv\.fr$/, /\.bund\.de$/, /\.gov\.uk$/, /\.cncn\.gov\.cn$/,
];

const TRUSTED_SECONDARY_HOSTS = [
  "iapp.org", "gdprhub.eu", "noyb.eu", "epic.org", "futureoflife.org", "hai.stanford.edu",
];

function classify(url: string): OfficialPick["tier"] {
  try {
    const host = new URL(url).hostname.toLowerCase();
    if (OFFICIAL_HOST_PATTERNS.some((p) => p.test(host))) return "official";
    if (host.endsWith("europa.eu") || host.endsWith(".gov") || host.endsWith(".gov.cn")) return "official";
    if (TRUSTED_SECONDARY_HOSTS.some((h) => host === h || host.endsWith("." + h))) return "trusted_secondary";
    return "news";
  } catch {
    return "news";
  }
}

function hostName(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

/** 构造检索词：标题关键词 + 机构/规则名（限长） */
function buildQuery(ev: Pick<ComplianceEventRow, "title" | "authority" | "jurisdiction">): string {
  const base = truncate(ev.title.replace(/^Vol\.\d+\s*\|\s*/, ""), 80);
  const extra = ev.authority && ev.authority.length < 40 ? ` ${ev.authority}` : "";
  return `${base}${extra} 官方 原文`;
}

/** 用 LLM 从候选中选择最相关的官方来源（返回索引，URL 必须来自候选，防编造） */
async function pickByLLM(
  query: string,
  candidates: { title: string; url: string; snippet: string }[]
): Promise<number | null> {
  if (candidates.length === 0) return null;
  try {
    const llm = new LLMClient(new Config());
    const response = await llm.invoke(
      [
        {
          role: "system",
          content:
            "你从搜索结果候选中为一条监管动态选择最合适的官方来源链接。规则：官方原文 > 官方指南 > 可信二手（IAPP/GDPRhub/知名律所/合规机构）> 新闻媒体。优先选择政府/监管机构域名，或明确指向该具体动态原文的链接。只输出 JSON：{\"index\": 候选序号} 或 {\"index\": -1}（表示都不合适）。不要输出其他内容。",
        },
        {
          role: "user",
          content: `监管动态：${truncate(query, 200)}\n候选列表：\n${candidates
            .map((c, i) => `${i}. [${c.title}] ${c.url} — ${truncate(c.snippet, 120)}`)
            .join("\n")}`,
        },
      ],
      { temperature: 0.1, thinking: "disabled" }
    );
    const parsed = parseJsonLoose<{ index: number }>(response.content);
    if (parsed && typeof parsed.index === "number" && parsed.index >= 0 && parsed.index < candidates.length) {
      return parsed.index;
    }
    return null;
  } catch {
    return null;
  }
}

export interface VerifyResult {
  official_source_url: string | null;
  official_source_name: string | null;
  official_verified: "verified" | "unverified" | "pending";
  official_note: string | null;
}

export async function verifyEvent(
  ev: Pick<ComplianceEventRow, "title" | "authority" | "jurisdiction" | "source_url" | "summary_zh">
): Promise<VerifyResult> {
  const query = buildQuery(ev);
  let candidates: { title: string; url: string; snippet: string }[] = [];
  try {
    const search = new SearchClient(new Config());
    const res = await search.webSearch(query, 8, false);
    candidates = (res.web_items ?? [])
      .filter((i): i is NonNullable<typeof i> & { url: string } => Boolean(i.url && i.url.startsWith("http")))
      .map((i) => ({ title: i.title ?? "", url: i.url, snippet: i.snippet ?? "" }));
  } catch (e) {
    return {
      official_source_url: null,
      official_source_name: null,
      official_verified: "unverified",
      official_note: `官方来源检索失败，已保留原始来源（待核实）: ${e instanceof Error ? e.message : String(e)}`,
    };
  }
  if (candidates.length === 0) {
    return {
      official_source_url: null,
      official_source_name: null,
      official_verified: "unverified",
      official_note: "未检索到官方来源，已保留原始来源链接（官方原文待核实）",
    };
  }
  // 规则优先：候选中的官方域名结果
  const officialCandidates = candidates
    .map((c, i) => ({ ...c, tier: classify(c.url), i }))
    .filter((c) => c.tier === "official");
  if (officialCandidates.length > 0) {
    const pick = officialCandidates[0];
    return {
      official_source_url: pick.url,
      official_source_name: pick.title || hostName(pick.url),
      official_verified: "verified",
      official_note: null,
    };
  }
  // LLM 从全部候选中选择
  const idx = await pickByLLM(query, candidates);
  if (idx !== null) {
    const pick = candidates[idx];
    const tier = classify(pick.url);
    return {
      official_source_url: pick.url,
      official_source_name: pick.title || hostName(pick.url),
      official_verified: tier === "news" ? "unverified" : "verified",
      official_note: tier === "news" ? "未找到官方原文，暂以可信媒体/二手来源替代（官方原文待核实）" : null,
    };
  }
  return {
    official_source_url: null,
    official_source_name: null,
    official_verified: "unverified",
    official_note: "搜索结果均不匹配官方来源，已保留原始来源链接（官方原文待核实）",
  };
}
