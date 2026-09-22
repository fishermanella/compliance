import { sql } from "drizzle-orm";
import {
  pgTable,
  text,
  varchar,
  timestamp,
  boolean,
  integer,
  jsonb,
  index,
  uniqueIndex,
} from "drizzle-orm/pg-core";
import { createSchemaFactory } from "drizzle-zod";
import { z } from "zod";

// ============ 监测运行记录 ============
// 每次触发监测（手动/定时）产生一条记录，记录各数据源采集状态与完整性
export const monitorRuns = pgTable(
  "monitor_runs",
  {
    id: varchar("id", { length: 36 }).primaryKey().default(sql`gen_random_uuid()`),
    // running / success / partial / failed
    status: varchar("status", { length: 20 }).notNull().default("running"),
    // manual / schedule
    trigger: varchar("trigger", { length: 20 }).notNull().default("manual"),
    // ISO 周标识，如 2026-W39
    week_key: varchar("week_key", { length: 12 }),
    started_at: timestamp("started_at", { withTimezone: true }).defaultNow().notNull(),
    finished_at: timestamp("finished_at", { withTimezone: true }),
    // 各数据源采集明细 [{source, status, coverage_status, expected_count, collected_count, error}]
    sources_detail: jsonb("sources_detail").$type<MonitorSourceDetail[]>(),
    items_collected: integer("items_collected").default(0).notNull(),
    items_relevant: integer("items_relevant").default(0).notNull(),
    items_high: integer("items_high").default(0).notNull(),
    items_verified: integer("items_verified").default(0).notNull(),
    error: text("error"),
    created_at: timestamp("created_at", { withTimezone: true }).defaultNow().notNull(),
  },
  (table) => [
    index("monitor_runs_status_idx").on(table.status),
    index("monitor_runs_week_key_idx").on(table.week_key),
    index("monitor_runs_started_at_idx").on(table.started_at),
  ]
);

export interface MonitorSourceDetail {
  source: string;
  status: "success" | "unavailable" | "failed";
  coverage_status: "complete" | "incomplete" | "user_provided";
  expected_count?: number;
  collected_count?: number;
  error?: string;
}

// ============ 微信文章链接池（人工投喂，等价于 data/wechat_links.txt） ============
export const wechatLinks = pgTable(
  "wechat_links",
  {
    id: varchar("id", { length: 36 }).primaryKey().default(sql`gen_random_uuid()`),
    url: text("url").notNull().unique(),
    note: text("note"),
    // pending / fetched / unavailable / invalid
    status: varchar("status", { length: 20 }).notNull().default("pending"),
    error: text("error"),
    created_at: timestamp("created_at", { withTimezone: true }).defaultNow().notNull(),
  },
  (table) => [index("wechat_links_status_idx").on(table.status)]
);

// ============ 合规监管事项 ============
// 三个来源统一转换后的 Compliance Event，含 AI 分析与官方来源核验结果
export const complianceEvents = pgTable(
  "compliance_events",
  {
    id: varchar("id", { length: 36 }).primaryKey().default(sql`gen_random_uuid()`),
    // dataguidance / 数据何规 / 那一片数据星辰
    source: varchar("source", { length: 50 }).notNull(),
    // auto / manual_url / podcast
    collection_method: varchar("collection_method", { length: 30 }).notNull(),
    title: text("title").notNull(),
    // YYYY-MM-DD；缺失时为空（date_unverified，不得由 AI 猜测）
    published_date: varchar("published_date", { length: 20 }),
    // CN / EU / US / US-CA / US-CO / JP / KR ...
    jurisdiction: varchar("jurisdiction", { length: 20 }),
    authority: text("authority"),
    // Guidance / Enforcement / Judgment / Consultation / Legislation / Standard / News / Other
    event_type: varchar("event_type", { length: 40 }),
    raw_content: text("raw_content"),
    source_url: text("source_url").notNull(),
    discovery_source: text("discovery_source"),

    // ---- AI 分析（AI Analysis，与 Source Fact 分离展示）----
    is_relevant: boolean("is_relevant").default(false).notNull(),
    // high / medium / low / irrelevant
    relevance_level: varchar("relevance_level", { length: 20 }),
    relevance_reason: text("relevance_reason"),
    doc_type: varchar("doc_type", { length: 40 }),
    // 新法出台 / 修订 / 指南 / 执法 / 判决 / 生效 / 其他
    update_type: varchar("update_type", { length: 40 }),
    // 是否为新增/实质更新
    is_substantive: boolean("is_substantive"),
    tags: jsonb("tags").$type<string[]>(),
    summary_zh: text("summary_zh"),
    // 生效/拟生效日期 YYYY-MM-DD
    effect_date: varchar("effect_date", { length: 20 }),
    needs_review: boolean("needs_review").default(false).notNull(),

    // ---- 官方来源核验 ----
    official_source_url: text("official_source_url"),
    official_source_name: text("official_source_name"),
    // verified / unverified / pending
    official_verified: varchar("official_verified", { length: 20 }).default("pending").notNull(),
    official_note: text("official_note"),

    // ---- 事项状态 ----
    // new / updated / effective / background / needs_review
    status: varchar("status", { length: 20 }).default("new").notNull(),

    run_id: varchar("run_id", { length: 36 }).references(() => monitorRuns.id),
    week_key: varchar("week_key", { length: 12 }),
    // 去重键：归一化 source_url
    dedupe_key: text("dedupe_key").notNull(),
    created_at: timestamp("created_at", { withTimezone: true }).defaultNow().notNull(),
    updated_at: timestamp("updated_at", { withTimezone: true }).defaultNow().notNull(),
  },
  (table) => [
    uniqueIndex("compliance_events_dedupe_key_idx").on(table.dedupe_key),
    index("compliance_events_run_id_idx").on(table.run_id),
    index("compliance_events_week_key_idx").on(table.week_key),
    index("compliance_events_jurisdiction_idx").on(table.jurisdiction),
    index("compliance_events_relevance_idx").on(table.relevance_level),
    index("compliance_events_source_idx").on(table.source),
    index("compliance_events_published_idx").on(table.published_date),
    index("compliance_events_tags_idx").using("gin", table.tags),
  ]
);

const { createInsertSchema } = createSchemaFactory({ coerce: { date: true } });
export type MonitorRun = typeof monitorRuns.$inferSelect;
export type WechatLink = typeof wechatLinks.$inferSelect;
export type ComplianceEvent = typeof complianceEvents.$inferSelect;
export { createInsertSchema };
