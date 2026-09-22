// ============ 监测流程编排：采集 → 去重 → AI 分析 → 官方核验 → 入库 ============
import { isoWeekKey, normalizeUrl } from "./util";
import { collectDataguidance } from "./sources/dataguidance";
import { collectXiaoyuzhou } from "./sources/xiaoyuzhou";
import { collectWechat } from "./sources/wechat";
import { analyzeBatch } from "./analyze";
import { verifyEvent } from "./verify";
import {
  createRun,
  updateRun,
  consumePendingWechatLinks,
  getExistingDedupeKeys,
  insertEvents,
  type SourceDetail,
} from "./store";
import type { RawItem } from "./types";

export function startMonitorRun(trigger: string): Promise<string> {
  return runMonitor(trigger);
}

async function runMonitor(trigger: string): Promise<string> {
  const weekKey = isoWeekKey();
  const runId = await createRun(trigger, weekKey);
  executeMonitor(runId, weekKey).catch(async (e) => {
    console.error("[monitor] 运行异常:", e);
    try {
      await updateRun(runId, {
        status: "failed",
        finished_at: new Date().toISOString(),
        error: e instanceof Error ? e.message : String(e),
      });
    } catch {
      /* 更新失败仅记录日志 */
    }
  });
  return runId;
}

async function executeMonitor(runId: string, weekKey: string): Promise<void> {
  const started = Date.now();
  // ---------- 1. 并行采集三来源 ----------
  const [dg, xyz] = await Promise.all([collectDataguidance(), collectXiaoyuzhou()]);
  let wechatLinks: { id: string; url: string }[] = [];
  try {
    wechatLinks = await consumePendingWechatLinks();
  } catch (e) {
    console.error("[monitor] 读取微信链接池失败:", e);
  }
  const wechat = await collectWechat(wechatLinks);
  const sourcesDetail: SourceDetail[] = [dg.report, wechat.report, xyz.report];
  const raws: RawItem[] = [...dg.items, ...wechat.items, ...xyz.items];
  console.log(`[monitor] 采集完成: dg=${dg.items.length} wechat=${wechat.items.length} xyz=${xyz.items.length}`);

  // ---------- 2. 去重（跨 run 数据库去重 + 本 run 内去重） ----------
  const seen = new Set<string>();
  const deduped: RawItem[] = [];
  const dedupeKeys: string[] = [];
  for (const raw of raws) {
    const key = normalizeUrl(raw.source_url);
    if (seen.has(key)) continue;
    seen.add(key);
    deduped.push(raw);
    dedupeKeys.push(key);
  }
  const existing = await getExistingDedupeKeys(dedupeKeys);
  const fresh: RawItem[] = [];
  const freshKeys: string[] = [];
  deduped.forEach((raw, i) => {
    if (!existing.has(dedupeKeys[i])) {
      fresh.push(raw);
      freshKeys.push(dedupeKeys[i]);
    }
  });
  console.log(`[monitor] 去重: 采集 ${raws.length} → 新增 ${fresh.length}（已有 ${raws.length - fresh.length}）`);

  // ---------- 3. AI 分析 ----------
  const analyses = fresh.length > 0 ? await analyzeBatch(fresh) : [];

  // ---------- 4. 官方来源核验（仅高相关，PRD 12） ----------
  let verifiedCount = 0;
  const rowsToInsert: Parameters<typeof insertEvents>[0] = [];
  for (let i = 0; i < fresh.length; i++) {
    const raw = fresh[i];
    const list = analyses[i];
    list.forEach((a, splitIdx) => {
      const isRelevant = a.is_relevant && a.relevance_level !== "irrelevant";
      const key = splitIdx === 0 ? freshKeys[i] : `${freshKeys[i]}#item${splitIdx}`;
      rowsToInsert.push({
        source: raw.source,
        collection_method: raw.collection_method,
        title: a.title,
        published_date: raw.published_date,
        jurisdiction: a.jurisdiction || raw.jurisdiction,
        authority: raw.authority,
        event_type: a.doc_type,
        raw_content: raw.raw_content,
        source_url: raw.source_url,
        discovery_source: raw.discovery_source,
        is_relevant: isRelevant,
        relevance_level: a.relevance_level,
        relevance_reason: a.relevance_reason,
        doc_type: a.doc_type,
        update_type: a.update_type,
        is_substantive: a.is_substantive,
        tags: a.tags,
        summary_zh: a.summary_zh,
        effect_date: a.effect_date,
        needs_review: a.needs_review || !isRelevant,
        official_verified: "pending",
        status: a.event_status,
        run_id: runId,
        week_key: weekKey,
        dedupe_key: key,
      });
    });
  }
  // 核验高相关事项（并发 3）
  const highRows = rowsToInsert.filter((r) => r.relevance_level === "high");
  const CONC = 3;
  for (let i = 0; i < highRows.length; i += CONC) {
    const slice = highRows.slice(i, i + CONC);
    const results = await Promise.all(
      slice.map(async (r) => {
        try {
          return await verifyEvent({
            title: r.title,
            authority: r.authority ?? null,
            jurisdiction: r.jurisdiction ?? null,
            source_url: r.source_url,
            summary_zh: r.summary_zh ?? null,
          });
        } catch (e) {
          return {
            official_source_url: null,
            official_source_name: null,
            official_verified: "unverified" as const,
            official_note: `核验异常: ${e instanceof Error ? e.message : String(e)}`,
          };
        }
      })
    );
    slice.forEach((r, j) => {
      const v = results[j];
      r.official_source_url = v.official_source_url;
      r.official_source_name = v.official_source_name;
      r.official_verified = v.official_verified;
      r.official_note = v.official_note;
      if (v.official_verified === "verified") verifiedCount++;
    });
  }
  // 低/中相关且未核验：标记 unverified
  rowsToInsert.forEach((r) => {
    if (r.official_verified === "pending") r.official_verified = "unverified";
  });

  // ---------- 5. 入库 ----------
  let inserted = 0;
  if (rowsToInsert.length > 0) {
    try {
      inserted = await insertEvents(rowsToInsert as never);
    } catch (e) {
      console.error("[monitor] 入库失败:", e);
      throw e;
    }
  }
  const relevantRows = rowsToInsert.filter((r) => r.is_relevant);
  const highCount = relevantRows.filter((r) => r.relevance_level === "high").length;

  // ---------- 6. 更新运行状态 ----------
  const hasUnavailable = sourcesDetail.some((s) => s.status !== "success");
  const status = relevantRows.length === 0 && fresh.length === 0 && sourcesDetail.every((s) => s.status !== "failed")
    ? hasUnavailable
      ? "partial"
      : "success"
    : hasUnavailable
      ? "partial"
      : "success";
  await updateRun(runId, {
    status,
    finished_at: new Date().toISOString(),
    sources_detail: sourcesDetail,
    items_collected: raws.length,
    items_relevant: relevantRows.length,
    items_high: highCount,
    items_verified: verifiedCount,
    error: sourcesDetail
      .filter((s) => s.error)
      .map((s) => `${s.source}: ${s.error}`)
      .join("；") || null,
  });
  console.log(
    `[monitor] 运行完成 run=${runId} 耗时=${Math.round((Date.now() - started) / 1000)}s 采集=${raws.length} 入库=${inserted} 相关=${relevantRows.length} 高=${highCount} 核验=${verifiedCount} 状态=${status}`
  );
}
