// ============ 数据访问层（Supabase） ============
import { getSupabaseClient } from "@/storage/database/supabase-client";

export interface MonitorRunRow {
  id: string;
  status: string;
  trigger: string;
  week_key: string | null;
  started_at: string;
  finished_at: string | null;
  sources_detail: SourceDetail[] | null;
  items_collected: number;
  items_relevant: number;
  items_high: number;
  items_verified: number;
  error: string | null;
}

export interface SourceDetail {
  source: string;
  status: "success" | "unavailable" | "failed";
  coverage_status: "complete" | "incomplete" | "user_provided";
  expected_count?: number;
  collected_count?: number;
  error?: string;
}

export interface ComplianceEventRow {
  id: string;
  source: string;
  collection_method: string;
  title: string;
  published_date: string | null;
  jurisdiction: string | null;
  authority: string | null;
  event_type: string | null;
  raw_content: string | null;
  source_url: string;
  discovery_source: string | null;
  is_relevant: boolean;
  relevance_level: string | null;
  relevance_reason: string | null;
  doc_type: string | null;
  update_type: string | null;
  is_substantive: boolean | null;
  tags: string[] | null;
  summary_zh: string | null;
  effect_date: string | null;
  needs_review: boolean;
  official_source_url: string | null;
  official_source_name: string | null;
  official_verified: string;
  official_note: string | null;
  status: string;
  run_id: string | null;
  week_key: string | null;
  dedupe_key: string;
  created_at: string;
  updated_at: string;
}

export interface WechatLinkRow {
  id: string;
  url: string;
  note: string | null;
  status: string;
  error: string | null;
  created_at: string;
}

function sb() {
  return getSupabaseClient();
}

// ---------- 监测运行 ----------
export async function createRun(trigger: string, weekKey: string): Promise<string> {
  const client = await sb();
  const { data, error } = await client
    .from("monitor_runs")
    .insert({ status: "running", trigger, week_key: weekKey })
    .select("id")
    .single();
  if (error) throw new Error(`创建监测运行失败: ${error.message}`);
  return data.id as string;
}

export async function updateRun(id: string, patch: Partial<MonitorRunRow>): Promise<void> {
  const client = await sb();
  const { error } = await client.from("monitor_runs").update(patch).eq("id", id);
  if (error) throw new Error(`更新监测运行失败: ${error.message}`);
}

export async function getRun(id: string): Promise<MonitorRunRow | null> {
  const client = await sb();
  const { data, error } = await client.from("monitor_runs").select("*").eq("id", id).maybeSingle();
  if (error) throw new Error(`查询监测运行失败: ${error.message}`);
  return (data as MonitorRunRow) ?? null;
}

export async function getLatestRun(): Promise<MonitorRunRow | null> {
  const client = await sb();
  const { data, error } = await client
    .from("monitor_runs")
    .select("*")
    .order("started_at", { ascending: false })
    .limit(1)
    .maybeSingle();
  if (error) throw new Error(`查询最新监测运行失败: ${error.message}`);
  return (data as MonitorRunRow) ?? null;
}

// ---------- 监测事项 ----------
export async function getExistingDedupeKeys(keys: string[]): Promise<Set<string>> {
  if (keys.length === 0) return new Set();
  const client = await sb();
  const found = new Set<string>();
  const chunkSize = 200;
  for (let i = 0; i < keys.length; i += chunkSize) {
    const chunk = keys.slice(i, i + chunkSize);
    const { data, error } = await client.from("compliance_events").select("dedupe_key").in("dedupe_key", chunk);
    if (error) throw new Error(`查询去重键失败: ${error.message}`);
    (data ?? []).forEach((r: { dedupe_key: string }) => found.add(r.dedupe_key));
  }
  return found;
}

export async function insertEvents(
  rows: (Partial<ComplianceEventRow> & { source_url: string; title: string; source: string; collection_method: string; dedupe_key: string })[]
): Promise<number> {
  if (rows.length === 0) return 0;
  const client = await sb();
  const { error, count } = await client.from("compliance_events").insert(rows, { count: "exact" });
  if (error) {
    // 并发或重复键导致的部分失败：逐条重试，忽略唯一键冲突
    let ok = 0;
    for (const row of rows) {
      const { error: e2 } = await client.from("compliance_events").insert(row);
      if (!e2) ok++;
      else if (!String(e2.message).includes("duplicate key")) throw new Error(`写入监测事项失败: ${e2.message}`);
    }
    return ok;
  }
  return count ?? rows.length;
}

export interface EventFilters {
  week?: string;
  jurisdiction?: string;
  tag?: string;
  relevance?: string; // high | medium | low
  search?: string;
  only_relevant?: boolean;
  limit?: number;
  offset?: number;
}

export async function listEvents(
  filters: EventFilters
): Promise<{ items: ComplianceEventRow[]; total: number }> {
  const client = await sb();
  let query = client.from("compliance_events").select("*", { count: "exact" });
  if (filters.week) query = query.eq("week_key", filters.week);
  if (filters.jurisdiction) query = query.eq("jurisdiction", filters.jurisdiction);
  if (filters.relevance) query = query.eq("relevance_level", filters.relevance);
  if (filters.only_relevant) query = query.eq("is_relevant", true);
  if (filters.tag) query = query.contains("tags", [filters.tag]);
  if (filters.search) query = query.or(`title.ilike.%${filters.search}%,summary_zh.ilike.%${filters.search}%,authority.ilike.%${filters.search}%`);
  const limit = Math.min(filters.limit ?? 50, 200);
  const offset = filters.offset ?? 0;
  query = query
    .order("published_date", { ascending: false, nullsFirst: false })
    .order("created_at", { ascending: false })
    .range(offset, offset + limit - 1);
  const { data, error, count } = await query;
  if (error) throw new Error(`查询监测事项失败: ${error.message}`);
  return { items: (data as ComplianceEventRow[]) ?? [], total: count ?? 0 };
}

export async function getEvent(id: string): Promise<ComplianceEventRow | null> {
  const client = await sb();
  const { data, error } = await client.from("compliance_events").select("*").eq("id", id).maybeSingle();
  if (error) throw new Error(`查询监测事项详情失败: ${error.message}`);
  return (data as ComplianceEventRow) ?? null;
}

// ---------- 微信链接池（等价 data/wechat_links.txt） ----------
export async function addWechatLinks(urls: { url: string; note?: string }[]): Promise<{ added: number; duplicated: number }> {
  if (urls.length === 0) return { added: 0, duplicated: 0 };
  const client = await sb();
  let added = 0;
  let duplicated = 0;
  for (const u of urls) {
    const { data, error } = await client
      .from("wechat_links")
      .insert({ url: u.url, note: u.note ?? null })
      .select("id");
    if (error) {
      if (String(error.message).includes("duplicate")) duplicated++;
      else throw new Error(`添加微信链接失败: ${error.message}`);
    } else {
      added += data?.length ?? 0;
    }
  }
  return { added, duplicated };
}

export async function listWechatLinks(status?: string): Promise<WechatLinkRow[]> {
  const client = await sb();
  let query = client.from("wechat_links").select("*");
  if (status) query = query.eq("status", status);
  const { data, error } = await query.order("created_at", { ascending: false }).limit(200);
  if (error) throw new Error(`查询微信链接失败: ${error.message}`);
  return (data as WechatLinkRow[]) ?? [];
}

export async function updateWechatLinkStatus(id: string, status: string, error?: string): Promise<void> {
  const client = await sb();
  const { error: e } = await client.from("wechat_links").update({ status, error: error ?? null }).eq("id", id);
  if (e) throw new Error(`更新微信链接状态失败: ${e.message}`);
}

export async function consumePendingWechatLinks(): Promise<WechatLinkRow[]> {
  const client = await sb();
  const { data, error } = await client.from("wechat_links").select("*").eq("status", "pending").limit(50);
  if (error) throw new Error(`读取待处理微信链接失败: ${error.message}`);
  return (data as WechatLinkRow[]) ?? [];
}

// ---------- Dashboard 统计 ----------
export interface DashboardStats {
  week_key: string;
  items_monitored: number;
  items_relevant: number;
  items_high: number;
  items_verified: number;
  jurisdiction_distribution: { jurisdiction: string; count: number }[];
  tag_distribution: { tag: string; count: number }[];
  top_events: ComplianceEventRow[];
}

export async function getDashboardStats(weekKey?: string): Promise<DashboardStats> {
  const client = await sb();
  const week = weekKey ?? currentIsoWeek();
  const { data: rows, error } = await client.from("compliance_events").select("*").eq("week_key", week);
  if (error) throw new Error(`统计本周数据失败: ${error.message}`);
  const list = (rows as ComplianceEventRow[]) ?? [];
  const relevant = list.filter((r) => r.is_relevant);
  const jurisdictionCount = new Map<string, number>();
  relevant.forEach((r) => {
    const j = r.jurisdiction || "Other";
    jurisdictionCount.set(j, (jurisdictionCount.get(j) ?? 0) + 1);
  });
  const tagCount = new Map<string, number>();
  relevant.forEach((r) => (r.tags ?? []).forEach((t) => tagCount.set(t, (tagCount.get(t) ?? 0) + 1)));
  const top = [...relevant]
    .filter((r) => r.relevance_level === "high")
    .sort((a, b) => (b.published_date ?? "").localeCompare(a.published_date ?? ""))
    .slice(0, 5);
  return {
    week_key: week,
    items_monitored: list.length,
    items_relevant: relevant.length,
    items_high: relevant.filter((r) => r.relevance_level === "high").length,
    items_verified: relevant.filter((r) => r.official_verified === "verified").length,
    jurisdiction_distribution: [...jurisdictionCount.entries()]
      .map(([jurisdiction, count]) => ({ jurisdiction, count }))
      .sort((a, b) => b.count - a.count),
    tag_distribution: [...tagCount.entries()]
      .map(([tag, count]) => ({ tag, count }))
      .sort((a, b) => b.count - a.count),
    top_events: top,
  };
}

function currentIsoWeek(): string {
  // 与 util.isoWeekKey 保持一致；独立实现避免循环依赖
  const now = new Date();
  const d = new Date(Date.UTC(now.getFullYear(), now.getMonth(), now.getDate()));
  const dayNum = d.getUTCDay() || 7;
  d.setUTCDate(d.getUTCDate() + 4 - dayNum);
  const yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
  const week = Math.ceil(((d.getTime() - yearStart.getTime()) / 86400000 + 1) / 7);
  return `${d.getUTCFullYear()}-W${String(week).padStart(2, "0")}`;
}
