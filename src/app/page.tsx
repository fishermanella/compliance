'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import Link from 'next/link';

interface SourceDetail { source: string; status: string; coverage_status: string; error?: string }
interface RunInfo {
  id: string; status: string; trigger: string; week_key: string | null;
  started_at: string; finished_at: string | null;
  items_collected: number; items_relevant: number; items_high: number; items_verified: number;
  coverage_warnings: string[];
}
interface TopEvent {
  id: string; title: string; summary_zh: string | null; relevance_reason: string | null;
  jurisdiction: string | null; tags: string[] | null; official_source_url: string | null;
  official_verified: string; published_date: string | null; source_url: string;
}
interface Stats {
  week_key: string; items_monitored: number; items_relevant: number; items_high: number;
  items_verified: number;
  jurisdiction_distribution: { jurisdiction: string; count: number }[];
  tag_distribution: { tag: string; count: number }[];
  top_events: TopEvent[];
}

const STATUS_TEXT: Record<string, string> = {
  running: '运行中', success: '已完成', partial: '部分完成', failed: '失败',
};

export default function DashboardPage() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [run, setRun] = useState<RunInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [linksOpen, setLinksOpen] = useState(false);
  const [linksText, setLinksText] = useState('');
  const [linksMsg, setLinksMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [submittingLinks, setSubmittingLinks] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await fetch('/api/dashboard');
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || '加载失败');
      setStats(data.stats);
      setRun(data.latest_run);
      setError(null);
      return data.latest_run as RunInfo | null;
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      return null;
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [load]);

  const startPolling = useCallback((runId: string) => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const res = await fetch(`/api/monitor/status?run_id=${runId}`);
        const data = await res.json();
        if (data.ok && data.run) {
          setRun(data.run);
          if (data.run.status !== 'running') {
            if (pollRef.current) clearInterval(pollRef.current);
            load();
          }
        }
      } catch { /* 轮询失败忽略，下轮继续 */ }
    }, 5000);
  }, [load]);

  const runMonitor = async () => {
    setStarting(true);
    setError(null);
    try {
      const res = await fetch('/api/monitor/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ trigger: 'manual' }),
      });
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || '启动失败');
      setRun({
        id: data.run_id, status: 'running', trigger: 'manual', week_key: null,
        started_at: new Date().toISOString(), finished_at: null,
        items_collected: 0, items_relevant: 0, items_high: 0, items_verified: 0,
        coverage_warnings: [],
      });
      startPolling(data.run_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setStarting(false);
    }
  };

  const submitLinks = async () => {
    setSubmittingLinks(true);
    setLinksMsg(null);
    try {
      const res = await fetch('/api/wechat-links', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ urls: linksText }),
      });
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || '提交失败');
      setLinksMsg({ ok: true, text: `已添加 ${data.added} 条链接${data.duplicated ? `，${data.duplicated} 条重复已忽略` : ''}。下次运行监测时将自动抓取。` });
      setLinksText('');
    } catch (e) {
      setLinksMsg({ ok: false, text: e instanceof Error ? e.message : String(e) });
    } finally {
      setSubmittingLinks(false);
    }
  };

  const running = run?.status === 'running';
  const maxJur = stats?.jurisdiction_distribution?.[0]?.count ?? 1;

  return (
    <div className="mx-auto max-w-6xl px-6 py-10 cr-fade-in">
      {/* 顶部标题 + 操作区 */}
      <div className="flex flex-wrap items-start justify-between gap-4 mb-8">
        <div>
          <h1 className="text-[28px] font-semibold tracking-tight text-[#1D1D1F] mb-1.5">
            Compliance Monitoring Dashboard
          </h1>
          <p className="text-[14px] text-[#8E8E93]">
            合规雷达 · 全球 AI 与数据合规监管动态监测
            {stats ? <span className="ml-2 inline-block rounded-full bg-[#EDF1F7] px-2.5 py-0.5 text-[12px] text-[#1D3557]">跟踪周次 {stats.week_key}</span> : null}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2.5">
          <button
            onClick={() => setLinksOpen(true)}
            className="cr-btn-secondary px-4 py-2 text-[14px]"
          >
            投喂微信文章链接
          </button>
          <a
            href="/api/report/excel"
            className="cr-btn-secondary px-4 py-2 text-[14px] no-underline"
          >
            Download Weekly Report
          </a>
          <button
            onClick={runMonitor}
            disabled={starting || running}
            className="cr-btn-primary px-5 py-2 text-[14px]"
          >
            {running ? '监测运行中…' : starting ? '启动中…' : 'Run Monitor'}
          </button>
        </div>
      </div>

      {error ? (
        <div className="cr-card p-4 mb-6 text-[14px] text-[#B3261E] border-[#F3C1BD] bg-[#FDF3F2]">
          加载出错：{error}（可刷新重试）
        </div>
      ) : null}

      {/* 运行状态条 */}
      {run ? (
        <div className="cr-card p-4 mb-6 flex flex-wrap items-center gap-x-6 gap-y-2 text-[13px]">
          <span className="flex items-center gap-2">
            <span className={`inline-block h-2 w-2 rounded-full ${running ? 'bg-[#1D3557] cr-pulse' : run.status === 'failed' ? 'bg-[#B3261E]' : 'bg-[#137333]'}`} />
            <span className="text-[#2C2C2E] font-medium">最近运行：{STATUS_TEXT[run.status] ?? run.status}</span>
          </span>
          <span className="text-[#8E8E93]">
            {run.finished_at ? new Date(run.finished_at).toLocaleString('zh-CN') : new Date(run.started_at).toLocaleString('zh-CN')}
            <span className="ml-1">· {run.trigger === 'schedule' ? '定时触发' : '手动触发'}</span>
          </span>
          {run.status !== 'running' ? (
            <span className="text-[#8E8E93]">
              采集 {run.items_collected} · 相关 {run.items_relevant} · 高相关 {run.items_high} · 官方核验 {run.items_verified}
            </span>
          ) : (
            <span className="text-[#1D3557]">正在采集与 AI 分析，预计需要几分钟，完成后自动刷新…</span>
          )}
        </div>
      ) : null}

      {/* 覆盖状态提示（PRD 26.1：明确提示覆盖缺失） */}
      {run?.coverage_warnings?.length ? (
        <div className="cr-card p-4 mb-6 text-[13px] text-[#92400E] border-[#FDE68A] bg-[#FFFBEB]">
          <div className="font-medium mb-1">数据覆盖提示</div>
          {run.coverage_warnings.map((w, i) => (
            <div key={i} className="text-[#92400E]/90">· {w}</div>
          ))}
        </div>
      ) : null}

      {/* 统计数字 */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
        {[
          { label: 'Items Monitored', value: stats?.items_monitored, hint: '本周监测条目' },
          { label: 'Compliance Relevant', value: stats?.items_relevant, hint: '合规相关事项' },
          { label: 'High Relevance', value: stats?.items_high, hint: '高相关（需关注）' },
          { label: 'Official Sources Verified', value: stats?.items_verified, hint: '已核验官方来源' },
        ].map((s) => (
          <div key={s.label} className="cr-card cr-card-hover p-5">
            <div className="text-[12px] text-[#8E8E93] font-medium tracking-wide uppercase">{s.label}</div>
            <div className="cr-num text-[32px] leading-tight font-semibold text-[#1D1D1F] mt-2">
              {loading ? <span className="inline-block h-8 w-16 rounded bg-black/5 cr-pulse" /> : s.value ?? 0}
            </div>
            <div className="text-[12px] text-[#8E8E93] mt-1">{s.hint}</div>
          </div>
        ))}
      </div>

      {/* 分布图 */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-8">
        <div className="cr-card p-6">
          <h2 className="text-[16px] font-semibold text-[#1D1D1F] mb-4">法域分布</h2>
          {!stats || stats.jurisdiction_distribution.length === 0 ? (
            <EmptyHint loading={loading} text="本周暂无法域分布数据" />
          ) : (
            <div className="space-y-2.5">
              {stats.jurisdiction_distribution.slice(0, 8).map((j) => (
                <div key={j.jurisdiction} className="flex items-center gap-3">
                  <span className="w-16 text-[13px] text-[#2C2C2E] shrink-0">{j.jurisdiction}</span>
                  <div className="flex-1 h-2.5 rounded-full bg-[#F0F0EE] overflow-hidden">
                    <div
                      className="h-full rounded-full bg-[#1D3557] transition-all"
                      style={{ width: `${Math.max((j.count / maxJur) * 100, 6)}%` }}
                    />
                  </div>
                  <span className="cr-num w-8 text-right text-[13px] text-[#8E8E93]">{j.count}</span>
                </div>
              ))}
            </div>
          )}
        </div>
        <div className="cr-card p-6">
          <h2 className="text-[16px] font-semibold text-[#1D1D1F] mb-4">主题分布</h2>
          {!stats || stats.tag_distribution.length === 0 ? (
            <EmptyHint loading={loading} text="本周暂无主题分布数据" />
          ) : (
            <div className="flex flex-wrap gap-2">
              {stats.tag_distribution.map((t) => (
                <Link
                  key={t.tag}
                  href={`/monitor?tag=${encodeURIComponent(t.tag)}`}
                  className="inline-flex items-center gap-1.5 rounded-full border border-[#E5E5EA] bg-white px-3 py-1.5 text-[13px] text-[#2C2C2E] hover:border-[#1D3557]/30 hover:bg-[#EDF1F7] transition-colors no-underline"
                >
                  {t.tag}
                  <span className="cr-num text-[12px] text-[#8E8E93]">{t.count}</span>
                </Link>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* 本周重点 */}
      <div className="cr-card p-6">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-[16px] font-semibold text-[#1D1D1F]">本周重点 · 高相关事项</h2>
          <Link href="/monitor?only_relevant=1" className="text-[13px] text-[#1D3557] hover:underline no-underline">
            查看全部监管动态 →
          </Link>
        </div>
        {!stats || stats.top_events.length === 0 ? (
          <EmptyHint loading={loading} text="本周暂无高相关事项。可点击 Run Monitor 运行一次监测，或等待每周定时任务。" />
        ) : (
          <div className="space-y-4">
            {stats.top_events.map((ev) => (
              <div key={ev.id} className="border border-[#E5E5EA] rounded-xl p-4 hover:border-[#1D3557]/25 transition-colors">
                <div className="flex flex-wrap items-center gap-2 mb-2">
                  <span className="rounded-md bg-[#1D3557] px-2 py-0.5 text-[11px] font-medium text-white">{ev.jurisdiction ?? 'Other'}</span>
                  {(ev.tags ?? []).slice(0, 3).map((t) => (
                    <span key={t} className="rounded-md bg-[#F0F0EE] px-2 py-0.5 text-[11px] text-[#2C2C2E]">{t}</span>
                  ))}
                  <span className="rounded-md bg-[#FEF3C7] px-2 py-0.5 text-[11px] font-medium text-[#B45309]">高相关</span>
                  {ev.published_date ? <span className="ml-auto text-[12px] text-[#8E8E93]">{ev.published_date}</span> : null}
                </div>
                <div className="text-[15px] font-medium text-[#1D1D1F] mb-1.5 leading-snug">{ev.title}</div>
                {ev.summary_zh ? <p className="text-[13px] text-[#3A3A3C] leading-relaxed mb-2">{ev.summary_zh}</p> : null}
                {ev.relevance_reason ? (
                  <p className="text-[13px] text-[#1D3557] leading-relaxed"><span className="text-[#8E8E93]">为什么值得关注：</span>{ev.relevance_reason}</p>
                ) : null}
                <div className="mt-2.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-[12px]">
                  {ev.official_source_url ? (
                    <a href={ev.official_source_url} target="_blank" rel="noreferrer" className="text-[#137333] hover:underline no-underline">
                      ✓ 官方来源
                    </a>
                  ) : (
                    <span className="text-[#8E8E93]">官方原文待核实</span>
                  )}
                  <a href={ev.source_url} target="_blank" rel="noreferrer" className="text-[#1D3557] hover:underline no-underline">
                    原始来源 ↗
                  </a>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* 微信链接投喂弹窗 */}
      {linksOpen ? (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4"
          onClick={(e) => { if (e.target === e.currentTarget) setLinksOpen(false); }}
        >
          <div className="cr-card w-full max-w-lg p-6 cr-fade-in">
            <h3 className="text-[17px] font-semibold text-[#1D1D1F] mb-1.5">投喂微信文章链接</h3>
            <p className="text-[13px] text-[#8E8E93] mb-4 leading-relaxed">
              每周从微信公众号「数据何规」复制文章链接粘贴到下方（每行一条）。链接将在下次运行监测时自动抓取分析。
            </p>
            <textarea
              value={linksText}
              onChange={(e) => setLinksText(e.target.value)}
              placeholder={'https://mp.weixin.qq.com/s/xxxxxxxx\nhttps://mp.weixin.qq.com/s/yyyyyyyy'}
              rows={5}
              className="w-full rounded-lg border border-[#E5E5EA] bg-white px-3 py-2.5 text-[13px] text-[#2C2C2E] outline-none focus:border-[#1D3557]/50 focus:ring-2 focus:ring-[#1D3557]/10 resize-y font-mono"
            />
            {linksMsg ? (
              <p className={`mt-2 text-[13px] ${linksMsg.ok ? 'text-[#137333]' : 'text-[#B3261E]'}`}>{linksMsg.text}</p>
            ) : null}
            <div className="mt-4 flex justify-end gap-2.5">
              <button onClick={() => setLinksOpen(false)} className="cr-btn-secondary px-4 py-2 text-[14px]">关闭</button>
              <button onClick={submitLinks} disabled={submittingLinks || !linksText.trim()} className="cr-btn-primary px-4 py-2 text-[14px]">
                {submittingLinks ? '提交中…' : '提交链接'}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function EmptyHint({ loading, text }: { loading: boolean; text: string }) {
  if (loading) {
    return <div className="space-y-3">{[0, 1, 2].map((i) => <div key={i} className="h-10 rounded-lg bg-black/5 cr-pulse" />)}</div>;
  }
  return <p className="text-[13px] text-[#8E8E93] py-6 text-center">{text}</p>;
}
