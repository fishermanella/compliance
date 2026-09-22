// 触发一次监测运行（异步执行，立即返回 run_id）
import { NextRequest, NextResponse } from "next/server";
import { startMonitorRun } from "@/lib/monitor/runner";
import { HeaderUtils } from "coze-coding-dev-sdk";

export async function POST(request: NextRequest) {
  try {
    HeaderUtils.extractForwardHeaders(request.headers);
    let trigger = "manual";
    try {
      const body = await request.json();
      if (body?.trigger === "schedule") trigger = "schedule";
    } catch {
      /* 无 body 或非 JSON，使用默认 */
    }
    const runId = await startMonitorRun(trigger);
    return NextResponse.json({ ok: true, run_id: runId, message: "监测运行已启动" });
  } catch (e) {
    console.error("[api/monitor/run] 启动失败:", e);
    return NextResponse.json(
      { ok: false, error: `启动监测失败: ${e instanceof Error ? e.message : String(e)}` },
      { status: 500 }
    );
  }
}
