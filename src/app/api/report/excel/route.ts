// Excel 周报下载（GET /api/report/excel?week=2026-W39）
import { NextRequest, NextResponse } from "next/server";
import { listEvents } from "@/lib/monitor/store";
import { buildWeeklyExcel } from "@/lib/monitor/excel";
import { HeaderUtils } from "coze-coding-dev-sdk";

export async function GET(request: NextRequest) {
  try {
    HeaderUtils.extractForwardHeaders(request.headers);
    const week = request.nextUrl.searchParams.get("week") ?? undefined;
    const { items } = await listEvents({
      week,
      only_relevant: true,
      limit: 200,
    });
    const weekKey = week ?? items[0]?.week_key ?? new Date().toISOString().slice(0, 10);
    const { buffer, filename } = await buildWeeklyExcel(items, weekKey);
    return new NextResponse(new Uint8Array(buffer), {
      status: 200,
      headers: {
        "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "Content-Disposition": `attachment; filename*=UTF-8''${encodeURIComponent(filename)}`,
        "Cache-Control": "no-store",
      },
    });
  } catch (e) {
    return NextResponse.json(
      { ok: false, error: `生成周报失败: ${e instanceof Error ? e.message : String(e)}` },
      { status: 500 }
    );
  }
}
