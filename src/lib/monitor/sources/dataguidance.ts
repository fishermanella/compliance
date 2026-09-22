// ============ DataGuidance 采集适配器 ============
// DataGuidance 为订阅制 JS 动态站点（Angular + 登录后 API api/v1/portal）。
// 当前部署环境无浏览器自动化且无订阅凭据：按 PRD 26.1 诚实标注 unavailable，
// 并保留接入路径——配置环境变量 DG_COOKIE（登录态 Cookie）后优先尝试带凭据请求。
import { fetchWithTimeout } from "../util";
import type { RawItem, SourceReport } from "../types";

const SOURCE_NAME = "DataGuidance";
const INFO_URL = "https://www.dataguidance.com/info?order=DESC_publishedOn&date=last_7_days";

export async function collectDataguidance(): Promise<{ items: RawItem[]; report: SourceReport }> {
  const report: SourceReport = {
    source: SOURCE_NAME,
    status: "unavailable",
    coverage_status: "incomplete",
    collected_count: 0,
  };
  try {
    const cookie = process.env.DG_COOKIE;
    const headers: Record<string, string> = { Accept: "text/html" };
    if (cookie) headers.Cookie = cookie;
    const res = await fetchWithTimeout(INFO_URL, { headers, timeoutMs: 25000 });
    const html = await res.text();
    const looksLikeShell =
      res.status === 401 ||
      res.status === 403 ||
      html.length < 20000 ||
      (html.includes("challenges.cloudflare.com") && !html.includes("publishedOn"));
    if (!cookie || looksLikeShell) {
      report.error = cookie
        ? "DataGuidance 无法获取数据：订阅内容未随响应返回（JS 动态渲染，需浏览器自动化或有效登录凭据），本周该来源覆盖缺失"
        : "DataGuidance 需要订阅凭据（DG_COOKIE 环境变量）且页面为 JS 动态渲染，当前无法访问，本周该来源覆盖缺失";
      return { items: [], report };
    }
    // 预留：具备凭据 + 可解析结构时走 HTML/API 解析路径
    report.status = "unavailable";
    report.error = "DataGuidance 响应可访问但内容为动态渲染，暂无可用的静态解析路径";
    return { items: [], report };
  } catch (e) {
    report.status = "failed";
    report.error = `DataGuidance 请求异常: ${e instanceof Error ? e.message : String(e)}`;
    return { items: [], report };
  }
}
