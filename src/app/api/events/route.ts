// 监测事项列表（筛选：周次/法域/主题/相关性/搜索）
import { NextRequest, NextResponse } from "next/server";
import { listEvents } from "@/lib/monitor/store";
import { HeaderUtils } from "coze-coding-dev-sdk";

export async function GET(request: NextRequest) {
  try {
    HeaderUtils.extractForwardHeaders(request.headers);
    const sp = request.nextUrl.searchParams;
    const { items, total } = await listEvents({
      week: sp.get("week") ?? undefined,
      jurisdiction: sp.get("jurisdiction") ?? undefined,
      tag: sp.get("tag") ?? undefined,
      relevance: sp.get("relevance") ?? undefined,
      search: sp.get("search") ?? undefined,
      only_relevant: sp.get("only_relevant") === "1",
      limit: sp.get("limit") ? Number(sp.get("limit")) : undefined,
      offset: sp.get("offset") ? Number(sp.get("offset")) : undefined,
    });
    return NextResponse.json({ ok: true, total, items });
  } catch (e) {
    return NextResponse.json(
      { ok: false, error: `查询列表失败: ${e instanceof Error ? e.message : String(e)}` },
      { status: 500 }
    );
  }
}
