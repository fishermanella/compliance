// Dashboard 统计（本周数字、法域分布、主题分布、本周重点、覆盖提示）
import { NextRequest, NextResponse } from "next/server";
import { getDashboardStats, getLatestRun, type SourceDetail } from "@/lib/monitor/store";
import { HeaderUtils } from "coze-coding-dev-sdk";

export async function GET(request: NextRequest) {
  try {
    HeaderUtils.extractForwardHeaders(request.headers);
    const week = request.nextUrl.searchParams.get("week") ?? undefined;
    const [stats, run] = await Promise.all([getDashboardStats(week), getLatestRun()]);
    const sources = (run?.sources_detail as unknown as SourceDetail[] | null) ?? [];
    return NextResponse.json({
      ok: true,
      stats,
      latest_run: run
        ? {
            id: run.id,
            status: run.status,
            trigger: run.trigger,
            week_key: run.week_key,
            started_at: run.started_at,
            finished_at: run.finished_at,
            items_collected: run.items_collected,
            items_relevant: run.items_relevant,
            items_high: run.items_high,
            items_verified: run.items_verified,
            coverage_warnings: sources.filter((s) => s.status !== "success").map((s) => `${s.source}: ${s.error ?? s.status}`),
          }
        : null,
    });
  } catch (e) {
    return NextResponse.json(
      { ok: false, error: `统计失败: ${e instanceof Error ? e.message : String(e)}` },
      { status: 500 }
    );
  }
}
