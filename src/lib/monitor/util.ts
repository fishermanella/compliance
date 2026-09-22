// ============ 监测系统通用工具 ============

/** 带超时的 fetch（服务端使用） */
export async function fetchWithTimeout(
  url: string,
  options: RequestInit & { timeoutMs?: number } = {}
): Promise<Response> {
  const { timeoutMs = 20000, ...rest } = options;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, {
      ...rest,
      signal: controller.signal,
      headers: {
        "User-Agent":
          "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        ...(rest.headers || {}),
      },
    });
  } finally {
    clearTimeout(timer);
  }
}

/** 清洗 HTML 标签，保留纯文本 */
export function stripHtml(html: string): string {
  return html
    .replace(/<style[\s\S]*?<\/style>/gi, " ")
    .replace(/<script[\s\S]*?<\/script>/gi, " ")
    .replace(/<br\s*\/?>/gi, "\n")
    .replace(/<\/(p|div|li|h[1-6]|section)>/gi, "\n")
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/[ \t]+/g, " ")
    .replace(/\n\s*\n+/g, "\n")
    .trim();
}

/** 截断文本 */
export function truncate(text: string, maxLen: number): string {
  if (!text) return "";
  return text.length <= maxLen ? text : text.slice(0, maxLen) + "…";
}

/** ISO 周标识：2026-W39 */
export function isoWeekKey(date: Date = new Date()): string {
  const d = new Date(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()));
  const dayNum = d.getUTCDay() || 7;
  d.setUTCDate(d.getUTCDate() + 4 - dayNum);
  const yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
  const week = Math.ceil(((d.getTime() - yearStart.getTime()) / 86400000 + 1) / 7);
  return `${d.getUTCFullYear()}-W${String(week).padStart(2, "0")}`;
}

/** 最近 N 天（含当天）的日期字符串集合，YYYY-MM-DD */
export function recentDaySet(days: number, ref: Date = new Date()): Set<string> {
  const set = new Set<string>();
  for (let i = 0; i < days; i++) {
    const d = new Date(ref.getTime() - i * 86400000);
    set.add(d.toISOString().slice(0, 10));
  }
  return set;
}

/** 归一化 URL 作为去重键：去 hash、去常见追踪参数、小写 host */
export function normalizeUrl(raw: string): string {
  try {
    const u = new URL(raw.trim());
    const drop = ["utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "spm", "from", "scene", "chksm"];
    drop.forEach((k) => u.searchParams.delete(k));
    u.hash = "";
    const path = u.pathname.replace(/\/+$/, "") || "/";
    const params = u.searchParams.toString();
    return `${u.host.toLowerCase()}${path}${params ? "?" + params : ""}`;
  } catch {
    return raw.trim();
  }
}

/** 解析多种日期格式 → YYYY-MM-DD；失败返回 null（不得猜测） */
export function parseDateFlexible(input: string | number | null | undefined): string | null {
  if (input === null || input === undefined || input === "") return null;
  if (typeof input === "number" || /^\d{10,13}$/.test(String(input))) {
    const n = Number(input);
    const d = new Date(n < 1e12 ? n * 1000 : n);
    return isNaN(d.getTime()) ? null : d.toISOString().slice(0, 10);
  }
  const s = String(input).trim();
  const iso = s.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (iso) return `${iso[1]}-${iso[2]}-${iso[3]}`;
  const cn = s.match(/^(\d{4})年(\d{1,2})月(\d{1,2})日/);
  if (cn) return `${cn[1]}-${cn[2].padStart(2, "0")}-${cn[3].padStart(2, "0")}`;
  const slash = s.match(/^(\d{4})\/(\d{1,2})\/(\d{1,2})/);
  if (slash) return `${slash[1]}-${slash[2].padStart(2, "0")}-${slash[3].padStart(2, "0")}`;
  try {
    const d = new Date(s);
    if (!isNaN(d.getTime())) return d.toISOString().slice(0, 10);
  } catch {
    /* ignore */
  }
  return null;
}

/** 从 HTML 中提取第一个匹配的内容 */
export function firstMatch(html: string, patterns: RegExp[]): string | null {
  for (const p of patterns) {
    const m = html.match(p);
    if (m && m[1]) return m[1].trim();
  }
  return null;
}

/** 安全 JSON 解析（容错 LLM 输出的 markdown fence） */
export function parseJsonLoose<T>(text: string): T | null {
  if (!text) return null;
  let t = text.trim();
  const fence = t.match(/```(?:json)?\s*([\s\S]*?)```/);
  if (fence) t = fence[1].trim();
  const start = Math.min(...["{", "["].map((c) => (t.indexOf(c) === -1 ? Infinity : t.indexOf(c))));
  if (start !== Infinity && start > 0) t = t.slice(start);
  try {
    return JSON.parse(t) as T;
  } catch {
    try {
      return JSON.parse(t.replace(/,\s*([\]}])/g, "$1")) as T;
    } catch {
      return null;
    }
  }
}
