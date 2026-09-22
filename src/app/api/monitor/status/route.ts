// 查询监测运行状态（无 run_id 时返回最新一次）
import { NextRequest, NextResponse } from "next/server";
import { getRun, getLatestRun, type SourceDetail } from "@/lib/monitor/store";
import { HeaderUtils } from "coze-coding-dev-sdk";

export async function GET(request: NextRequest) {
  try {
    HeaderUtils.extractForwardHeaders(request.headers);
    const runId = request.nextUrl.searchParams.get("run_id");
    const run = runId ? await getRun(runId) : await getLatestRun();
    if (!run) {
      return NextResponse.json({ ok: true, run: null });
    }
    const sources = (run.sources_detail as unknown as SourceDetail[] | null) ?? [];
    return NextResponse.json({
      ok: true,
      run: {
        ...run,
        coverage_warnings: sources.filter((s) => s.status !== "success").map((s) => `${s.source}: ${s.error ?? s.status}`),
      },
    });
  } catch (e) {
    return NextResponse.json(
      { ok: false, error: `查询状态失败: ${e instanceof Error ? e.message : String(e)}` },
      { status: 500 }
    );
  }
}
