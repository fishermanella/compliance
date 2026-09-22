// 监测事项详情
import { NextRequest, NextResponse } from "next/server";
import { getEvent } from "@/lib/monitor/store";
import { HeaderUtils } from "coze-coding-dev-sdk";

export async function GET(_request: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  try {
    const { id } = await ctx.params;
    const ev = await getEvent(id);
    if (!ev) {
      return NextResponse.json({ ok: false, error: "事项不存在" }, { status: 404 });
    }
    return NextResponse.json({ ok: true, event: ev });
  } catch (e) {
    return NextResponse.json(
      { ok: false, error: `查询详情失败: ${e instanceof Error ? e.message : String(e)}` },
      { status: 500 }
    );
  }
}
