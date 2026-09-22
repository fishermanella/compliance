// ============ Excel 周报生成（严格 15 列表头 + 排版规范） ============
import ExcelJS from "exceljs";
import type { ComplianceEventRow } from "./store";

const HEADERS = [
  "编号",
  "跟踪周次",
  "监测日期",
  "国家/地区",
  "发布机构/立法机关",
  "文件类型",
  "文件名称",
  "摘要",
  "生效/拟生效日期",
  "原文链接",
  "监测渠道",
  "是否为新增/实质更新",
  "更新类别",
  "与公司相关性初判",
  "涉及主题",
];

function relevanceLabel(level: string | null): string {
  switch (level) {
    case "high":
      return "高";
    case "medium":
      return "中";
    case "low":
      return "低";
    case "irrelevant":
      return "不相关";
    default:
      return "待复核";
  }
}

export async function buildWeeklyExcel(
  events: ComplianceEventRow[],
  weekKey: string
): Promise<{ buffer: Buffer; filename: string }> {
  const wb = new ExcelJS.Workbook();
  wb.creator = "AI Compliance Monitor";
  const ws = wb.addWorksheet("合规周报");
  ws.addRow(HEADERS);
  ws.getRow(1).font = { bold: true, size: 11, color: { argb: "FF1D3557" } };
  ws.getRow(1).fill = { type: "pattern", pattern: "solid", fgColor: { argb: "FFEDF1F7" } };
  ws.getRow(1).height = 22;

  events.forEach((ev, i) => {
    const monitorDate = ev.published_date || (ev.created_at || "").slice(0, 10) || "";
    ws.addRow([
      i + 1,
      weekKey,
      monitorDate,
      ev.jurisdiction || "Other",
      ev.authority || "-",
      ev.doc_type || "Other",
      ev.title,
      ev.summary_zh || "",
      ev.effect_date || "-",
      ev.official_source_url || ev.source_url,
      `${ev.source}${ev.discovery_source ? `（${ev.discovery_source}）` : ""}`,
      ev.is_substantive === null || ev.is_substantive === undefined ? "待复核" : ev.is_substantive ? "是" : "否",
      ev.update_type || "其他",
      relevanceLabel(ev.relevance_level),
      (ev.tags ?? []).join("、"),
    ]);
  });

  // 排版规范：冻结首行、开启筛选、自动换行、日期 YYYY-MM-DD、自适应列宽（封顶）
  ws.views = [{ state: "frozen", ySplit: 1 }];
  if (events.length > 0) {
    ws.autoFilter = { from: { row: 1, column: 1 }, to: { row: 1, column: HEADERS.length } };
  }
  const widthCaps = [6, 10, 12, 12, 22, 14, 40, 60, 14, 46, 24, 16, 12, 14, 20];
  ws.columns.forEach((col, i) => {
    col.width = widthCaps[i] ?? 16;
  });
  ws.eachRow((row, rowNumber) => {
    if (rowNumber === 1) return;
    row.alignment = { vertical: "top", wrapText: true };
    row.height = 60;
  });
  // 链接列蓝色下划线
  const linkCol = ws.getColumn(10);
  linkCol.font = { color: { argb: "FF1155CC" }, underline: true };

  const today = new Date().toISOString().slice(0, 10);
  const buffer = Buffer.from(await wb.xlsx.writeBuffer());
  return { buffer, filename: `新法追踪_${today}.xlsx` };
}
