// ============ AI 分析模块（LLM，不编造事实，只理解和整理） ============
import { LLMClient, Config } from "coze-coding-dev-sdk";
import { truncate } from "./util";
import { BUSINESS_PROFILE, TOPIC_TAGS, type AiAnalysis, type RawItem } from "./types";
import { parseJsonLoose } from "./util";

const SYSTEM_PROMPT = `你是企业 AI 合规团队的监管动态分析助手，帮助合规负责人跟踪全球 AI 与数据合规动态。

${BUSINESS_PROFILE}

对每条输入的原始监测事项，输出以下字段：
1. relevance_level（与公司相关性初判）：high=需要合规团队本周期关注或行动；medium=记录在案且显著影响业务或未来一季度内生效；low=弱相关仅记录；irrelevant=与业务画像无关
2. relevance_reason（为什么值得关注）：1-2 句中文，说明与公司业务的具体关联点；irrelevant 时可写"与业务无直接关联"
3. jurisdiction（国家/地区）：CN、EU、US、US-CA、US-CO、US-CT、US-VA、US-UT、US-TX、JP、KR、UK、SG、AU、CA、BR、IN、Other 之一；涉及美国联邦用 US；涉及欧盟机构用 EU；一个事项涉及多法域时取最主要的一个
4. doc_type（文件类型）：Guidance/Enforcement/Judgment/Consultation/Legislation/Standard/News/Other
5. update_type（更新类别）：新法出台/修订/指南/执法/判决/生效/征求意见/其他
6. is_substantive（是否为新增/实质更新）：boolean。程序性更新、征求意见稿再发布、合并稿均为 false；新规则、新执法行动、新判决为 true
7. tags（涉及主题）：从以下列表选择 0-3 个：${TOPIC_TAGS.join("、")}
8. summary_zh（摘要）：80-150 字中文，客观概括事项内容；如与公司业务相关，末尾用一句话点出潜在影响。不得编造输入中不存在的信息
9. effect_date（生效/拟生效日期）：仅当输入中明确给出时，输出 YYYY-MM-DD；否则 null。严禁猜测
10. needs_review：输入信息不足以完成判断时 true
11. event_status：new=新出台/新执法；updated=已有规则的实质更新；effective=生效类动态；background=背景资讯；needs_review=待人工复核

硬性规则：
- AI 不编造事实：机构名称、日期、规则名称必须来自输入文本；输入没有的信息留空或标 needs_review
- 若输入为播客 shownotes 且包含多条独立监管动态（如"第一条…第二条…"），将每条拆分为独立的输出项，title 用简洁中文短标题概括该条动态本身
- 普通文章输入：输出一条，title 保持原文标题（过长可精简但不得改变含义）
- 严格输出 JSON 数组，数组元素带 item_index 字段（对应输入序号），不要输出任何其他文字

输出格式：
[{"item_index":0,"title":"...","relevance_level":"high","relevance_reason":"...","jurisdiction":"EU","doc_type":"Guidance","update_type":"新法出台","is_substantive":true,"tags":["AI相关"],"summary_zh":"...","effect_date":null,"needs_review":false,"event_status":"new"}]`;

function client(): LLMClient {
  return new LLMClient(new Config());
}

/** 批量分析原始事项（每批 batch 条），返回与 RawItem 数量对应的 AiAnalysis[][]（拆分） */
export async function analyzeBatch(
  items: RawItem[],
  opts: { batchSize?: number } = {}
): Promise<AiAnalysis[][]> {
  const batchSize = opts.batchSize ?? 3;
  const results: AiAnalysis[][] = items.map(() => []);
  const batches: number[][] = [];
  for (let i = 0; i < items.length; i += batchSize) {
    batches.push(items.map((_, idx) => idx).slice(i, i + batchSize));
  }
  const llm = client();
  for (const batch of batches) {
    const payload = batch.map((idx) => ({
      item_index: idx,
      source: items[idx].source,
      title: items[idx].title,
      published_date: items[idx].published_date,
      jurisdiction_hint: items[idx].jurisdiction,
      authority: items[idx].authority,
      content: truncate(items[idx].raw_content, 6000),
      source_url: items[idx].source_url,
    }));
    try {
      const response = await llm.invoke(
        [
          { role: "system", content: SYSTEM_PROMPT },
          { role: "user", content: `请分析以下监测事项（JSON）：\n${JSON.stringify(payload)}` },
        ],
        { temperature: 0.2, thinking: "disabled" }
      );
      const parsed = parseJsonLoose<(AiAnalysis & { item_index: number })[]>(response.content);
      if (Array.isArray(parsed)) {
        for (const a of parsed) {
          if (typeof a.item_index !== "number" || !batch.includes(a.item_index)) continue;
          results[a.item_index].push(normalizeAnalysis(a));
        }
      }
    } catch (e) {
      console.error("[analyze] 批次分析失败:", e);
      // 失败批次留空：该批次原始事项标记 needs_review
      for (const idx of batch) {
        results[idx].push(fallbackAnalysis(items[idx], `AI 分析失败: ${e instanceof Error ? e.message : String(e)}`));
      }
    }
  }
  // 每条原始事项至少有一个输出（AI 漏掉时补 needs_review 占位）
  return results.map((arr, idx) => (arr.length > 0 ? arr : [fallbackAnalysis(items[idx], "AI 未返回该事项的分析结果，请人工复核")]));
}

function normalizeAnalysis(a: AiAnalysis & { item_index?: number }): AiAnalysis {
  const validLevels = new Set(["high", "medium", "low", "irrelevant"]);
  const validStatus = new Set(["new", "updated", "effective", "background", "needs_review"]);
  return {
    title: (a.title || "未命名事项").slice(0, 300),
    is_relevant: validLevels.has(a.relevance_level) ? a.relevance_level !== "irrelevant" : Boolean(a.is_relevant),
    relevance_level: validLevels.has(a.relevance_level) ? a.relevance_level : "low",
    relevance_reason: (a.relevance_reason || "").slice(0, 500),
    jurisdiction: (a.jurisdiction || "Other").toUpperCase().slice(0, 20),
    doc_type: a.doc_type || "Other",
    update_type: a.update_type || "其他",
    is_substantive: Boolean(a.is_substantive),
    tags: Array.isArray(a.tags) ? a.tags.slice(0, 3) : [],
    summary_zh: (a.summary_zh || "").slice(0, 800),
    effect_date: a.effect_date && /^\d{4}-\d{2}-\d{2}$/.test(a.effect_date) ? a.effect_date : null,
    needs_review: Boolean(a.needs_review),
    event_status: validStatus.has(a.event_status) ? a.event_status : "needs_review",
  };
}

function fallbackAnalysis(item: RawItem, reason: string): AiAnalysis {
  return {
    title: item.title,
    is_relevant: false,
    relevance_level: "low",
    relevance_reason: reason,
    jurisdiction: item.jurisdiction || "Other",
    doc_type: "Other",
    update_type: "其他",
    is_substantive: false,
    tags: [],
    summary_zh: truncate(item.raw_content, 200) || item.title,
    effect_date: null,
    needs_review: true,
    event_status: "needs_review",
  };
}
