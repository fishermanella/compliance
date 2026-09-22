// 微信文章链接池（等价 PRD 的 data/wechat_links.txt 人工投喂）
import { NextRequest, NextResponse } from "next/server";
import { addWechatLinks, listWechatLinks } from "@/lib/monitor/store";
import { normalizeUrl } from "@/lib/monitor/util";
import { HeaderUtils } from "coze-coding-dev-sdk";

export async function GET(request: NextRequest) {
  try {
    HeaderUtils.extractForwardHeaders(request.headers);
    const status = request.nextUrl.searchParams.get("status") ?? undefined;
    const items = await listWechatLinks(status);
    return NextResponse.json({ ok: true, items });
  } catch (e) {
    return NextResponse.json(
      { ok: false, error: `查询链接失败: ${e instanceof Error ? e.message : String(e)}` },
      { status: 500 }
    );
  }
}

export async function POST(request: NextRequest) {
  try {
    HeaderUtils.extractForwardHeaders(request.headers);
    const body = await request.json();
    const raw: string[] = Array.isArray(body?.urls)
      ? body.urls
      : typeof body?.urls === "string"
        ? body.urls.split(/\n+/)
        : [];
    const note: string | undefined = typeof body?.note === "string" && body.note ? body.note : undefined;
    const urls = [...new Set(
      raw
        .map((u) => String(u).trim())
        .filter((u) => /^https?:\/\/mp\.weixin\.qq\.com\/s\S*$/i.test(u))
    )];
    if (urls.length === 0) {
      return NextResponse.json({ ok: false, error: "未识别到有效的微信文章链接（需以 https://mp.weixin.qq.com/s 开头）" }, { status: 400 });
    }
    const result = await addWechatLinks(urls.map((u) => ({ url: u, note })));
    return NextResponse.json({ ok: true, added: result.added, duplicated: result.duplicated });
  } catch (e) {
    return NextResponse.json(
      { ok: false, error: `添加失败: ${e instanceof Error ? e.message : String(e)}` },
      { status: 500 }
    );
  }
}
