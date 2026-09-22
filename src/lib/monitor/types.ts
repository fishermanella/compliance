// ============ 监测系统统一类型定义 ============

// 采集层输出的原始条目（三来源统一）
export interface RawItem {
  source: string; // dataguidance | 数据何规 | 那一片数据星辰
  collection_method: string; // auto | manual_url | podcast
  title: string;
  published_date: string | null; // YYYY-MM-DD；不可确认时为 null（date_unverified）
  jurisdiction: string | null;
  authority: string | null;
  raw_content: string;
  source_url: string;
  discovery_source: string;
}

// AI 分析结果（AI 不编造事实，只理解和整理）
export interface AiAnalysis {
  title: string; // 提炼后的事项标题（拆分场景下由 AI 生成）
  is_relevant: boolean;
  relevance_level: "high" | "medium" | "low" | "irrelevant";
  relevance_reason: string;
  jurisdiction: string; // CN/EU/US/US-CA/US-CO/US-CT/US-VA/US-UT/US-TX/JP/KR/Other
  doc_type: string; // Guidance/Enforcement/Judgment/Consultation/Legislation/Standard/News/Other
  update_type: string; // 新法出台/修订/指南/执法/判决/生效/其他
  is_substantive: boolean;
  tags: string[];
  summary_zh: string;
  effect_date: string | null;
  needs_review: boolean;
  event_status: "new" | "updated" | "effective" | "background" | "needs_review";
}

export interface SourceReport {
  source: string;
  status: "success" | "unavailable" | "failed";
  coverage_status: "complete" | "incomplete" | "user_provided";
  expected_count?: number;
  collected_count?: number;
  error?: string;
}

export const JURISDICTIONS = ["CN", "EU", "US", "US-CA", "US-CO", "US-CT", "US-VA", "US-UT", "US-TX", "JP", "KR", "UK", "SG", "AU", "CA", "BR", "IN", "Other"] as const;

export const TOPIC_TAGS = [
  "AI相关",
  "生成式AI",
  "个人信息保护",
  "跨境传输",
  "儿童数据",
  "Cookie/SDK/追踪",
  "数据泄露",
  "消费者保护",
  "网络安全",
  "智能硬件",
  "App合规",
  "算法推荐",
  "自动化决策",
  "第三方数据处理",
] as const;

export const DOC_TYPES = ["Guidance", "Enforcement", "Judgment", "Consultation", "Legislation", "Standard", "News", "Other"] as const;

// 公司业务相关性画像（PRD 14.1）
export const BUSINESS_PROFILE = `
公司业务范围：AI 产品与 AI Companion、生成式 AI、智能硬件（耳机/可穿戴设备）、
移动 App 与账号体系、SDK 与 Cookie/广告追踪、语音与传感器数据、儿童与未成年人数据、
跨境数据传输、第三方服务商与云服务、消费者权益、数据泄露响应、算法推荐与自动化决策。
`;
