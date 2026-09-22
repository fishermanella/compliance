'use client';

import { Suspense, useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'next/navigation';

interface EventRow {
  id: string; source: string; collection_method: string; title: string;
  published_date: string | null; jurisdiction: string | null; authority: string | null;
  event_type: string | null; source_url: string; discovery_source: string | null;
  is_relevant: boolean; relevance_level: string | null; relevance_reason: string | null;
  doc_type: string | null; update_type: string | null; is_substantive: boolean | null;
  tags: string[] | null; summary_zh: string | null; effect_date: string | null;
  needs_review: boolean; official_source_url: string | null; official_source_name: string | null;
  official_verified: string; official_note: string | null; status: string;
}

const JURISDICTION_OPTIONS = ['CN', 'EU', 'US', 'US-CA', 'JP', 'KR', 'UK', 'SG', 'AU', 'CA', 'Other'];
const RELEVANCE_OPTIONS = [
  { value: 'high', label: '高相关' },
  { value: 'medium', label: '中相关' },
  { value: 'low', label: '低相关' },
];
const TAG_OPTIONS = [
  'AI相关', '生成式AI', '个人信息保护', '跨境传输', '儿童数据', 'Cookie/SDK/追踪',
  '数据泄露', '消费者保护', '网络安全', '智能硬件', 'App合规', '算法推荐', '自动化决策', '第三方数据处理',
];

export default function MonitorPage() {
  return (
    <Suspense fallback={<PageSkeleton />}>
      <MonitorContent />
    </Suspense>
  );
}

function MonitorContent() {
  const sp = useSearchParams();
  const [search, setSearch] = useState(sp.get('search') ?? '');
  const [searchInput, setSearchInput] = useState(sp.get('search') ?? '');
  const [jurisdiction, setJurisdiction] = useState(sp.get('jurisdiction') ?? '');
  const [tag, setTag] = useState(sp.get('tag') ?? '');
  const [relevance, setRelevance] = useState(sp.get('relevance') ?? '');
  const [onlyRelevant, setOnlyRelevant] = useState((sp.get('only_relevant') ?? '1') === '1');
  const [items, setItems] = useState<EventRow[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [limit, setLimit] = useState(30);

  const load = useCallback(async (opts: { append?: boolean; nextLimit?: number } = {}) => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams();
      if (search) params.set('search', search);
      if (jurisdiction) params.set('jurisdiction', jurisdiction);
      if (tag) params.set('tag', tag);
      if (relevance) params.set('relevance', relevance);
      if (onlyRelevant) params.set('only_relevant', '1');
      params.set('limit', String(opts.nextLimit ?? limit));
      const res = await fetch(`/api/events?${params.toString()}`);
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || '加载失败');
      setItems((prev) => (opts.append ? [...prev, ...data.items] : data.items));
      setTotal(data.total);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [search, jurisdiction, tag, relevance, onlyRelevant, limit]);

  useEffect(() => {
    load();
    // 筛选条件变化时重新加载（limit 重置）
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search, jurisdiction, tag, relevance, onlyRelevant]);

  const relevanceBadge = (level: string | null) => {
    switch (level) {
      case 'high':
        return <span className="rounded-md bg-[#FEF3C7] px-2 py-0.5 text-[11px] font-medium text-[#B45309]">高相关</span>;
      case 'medium':
        return <span className="rounded-md bg-[#EDF1F7] px-2 py-0.5 text-[11px] font-medium text-[#1D3557]">中相关</span>;
      case 'low':
        return <span className="rounded-md bg-[#F0F0EE] px-2 py-0.5 text-[11px] text-[#6B6B70]">低相关</span>;
      case 'irrelevant':
        return <span className="rounded-md bg-[#F0F0EE] px-2 py-0.5 text-[11px] text-[#8E8E93]">不相关</span>;
      default:
        return <span className="rounded-md bg-[#F0F0EE] px-2 py-0.5 text-[11px] text-[#8E8E93]">待复核</span>;
    }
  };

  const hasFilter = search || jurisdiction || tag || relevance || !onlyRelevant;

  return (
    <div className="mx-auto max-w-6xl px-6 py-10 cr-fade-in">
      <h1 className="text-[28px] font-semibold tracking-tight text-[#1D1D1F] mb-1.5">监管动态</h1>
      <p className="text-[14px] text-[#8E8E93] mb-6">
        全部监测事项 · 快速检索与筛选
        {total > 0 ? <span className="ml-2">共 {total} 条</span> : null}
      </p>

      {/* 筛选条 */}
      <div className="cr-card p-4 mb-6 flex flex-wrap items-center gap-2.5">
        <form
          onSubmit={(e) => { e.preventDefault(); setSearch(searchInput.trim()); }}
          className="flex items-center gap-2 flex-1 min-w-[220px]"
        >
          <input
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="搜索标题、摘要、发布机构…"
            className="flex-1 rounded-lg border border-[#E5E5EA] bg-white px-3 py-2 text-[13px] outline-none focus:border-[#1D3557]/50 focus:ring-2 focus:ring-[#1D3557]/10"
          />
          <button type="submit" className="cr-btn-primary px-3.5 py-2 text-[13px] shrink-0">搜索</button>
        </form>
        <select
          value={jurisdiction}
          onChange={(e) => setJurisdiction(e.target.value)}
          className="rounded-lg border border-[#E5E5EA] bg-white px-3 py-2 text-[13px] outline-none focus:border-[#1D3557]/50"
        >
          <option value="">全部法域</option>
          {JURISDICTION_OPTIONS.map((j) => <option key={j} value={j}>{j}</option>)}
        </select>
        <select
          value={tag}
          onChange={(e) => setTag(e.target.value)}
          className="rounded-lg border border-[#E5E5EA] bg-white px-3 py-2 text-[13px] outline-none focus:border-[#1D3557]/50 max-w-[160px]"
        >
          <option value="">全部主题</option>
          {TAG_OPTIONS.map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
        <select
          value={relevance}
          onChange={(e) => setRelevance(e.target.value)}
          className="rounded-lg border border-[#E5E5EA] bg-white px-3 py-2 text-[13px] outline-none focus:border-[#1D3557]/50"
        >
          <option value="">全部相关性</option>
          {RELEVANCE_OPTIONS.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
        </select>
        <label className="flex items-center gap-1.5 text-[13px] text-[#2C2C2E] cursor-pointer select-none">
          <input
            type="checkbox"
            checked={onlyRelevant}
            onChange={(e) => setOnlyRelevant(e.target.checked)}
            className="h-3.5 w-3.5 accent-[#1D3557]"
          />
          仅看合规相关
        </label>
        {hasFilter ? (
          <button
            onClick={() => { setSearch(''); setSearchInput(''); setJurisdiction(''); setTag(''); setRelevance(''); setOnlyRelevant(true); }}
            className="text-[13px] text-[#1D3557] hover:underline"
          >
            重置
          </button>
        ) : null}
      </div>

      {error ? (
        <div className="cr-card p-4 mb-6 text-[14px] text-[#B3261E] border-[#F3C1BD] bg-[#FDF3F2]">加载出错：{error}</div>
      ) : null}

      {/* 列表 */}
      {!loading && items.length === 0 && !error ? (
        <div className="cr-card p-10 text-center">
          <p className="text-[15px] text-[#2C2C2E] font-medium mb-1.5">暂无匹配的监管动态</p>
          <p className="text-[13px] text-[#8E8E93]">
            {hasFilter ? '试试放宽筛选条件。' : '返回监测总览点击 Run Monitor 运行一次监测，或等待每周定时任务自动运行。'}
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {items.map((ev) => {
            const open = expanded === ev.id;
            return (
              <div
                key={ev.id}
                className="cr-card cr-card-hover overflow-hidden cursor-pointer"
                onClick={() => setExpanded(open ? null : ev.id)}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setExpanded(open ? null : ev.id); } }}
              >
                <div className="p-4">
                  <div className="flex flex-wrap items-center gap-2 mb-2">
                    <span className="rounded-md bg-[#1D3557] px-2 py-0.5 text-[11px] font-medium text-white">{ev.jurisdiction ?? 'Other'}</span>
                    {(ev.tags ?? []).slice(0, 3).map((t) => (
                      <span key={t} className="rounded-md bg-[#F0F0EE] px-2 py-0.5 text-[11px] text-[#2C2C2E]">{t}</span>
                    ))}
                    {relevanceBadge(ev.relevance_level)}
                    {ev.official_verified === 'verified' ? (
                      <span className="text-[11px] text-[#137333]">✓ 官方已核验</span>
                    ) : null}
                    <span className="ml-auto text-[12px] text-[#8E8E93]">
                      {ev.published_date ?? '日期未确认'} · {ev.source}
                    </span>
                  </div>
                  <div className="text-[15px] font-medium text-[#1D1D1F] leading-snug">{ev.title}</div>
                  {!open && ev.summary_zh ? (
                    <p className="text-[13px] text-[#6B6B70] leading-relaxed mt-1.5 line-clamp-2">{ev.summary_zh}</p>
                  ) : null}
                </div>
                {open ? (
                  <div className="border-t border-[#E5E5EA] bg-[#FAFAF8] p-4 text-[13px] leading-relaxed cr-fade-in">
                    <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-8 gap-y-2.5 mb-3">
                      <Item label="发布机构" value={ev.authority} />
                      <Item label="文件类型" value={ev.doc_type} />
                      <Item label="更新类别" value={ev.update_type} />
                      <Item label="生效/拟生效日期" value={ev.effect_date} />
                      <Item label="发布日期" value={ev.published_date ?? '未确认（日期未经核实）'} />
                      <Item label="监测渠道" value={ev.discovery_source ?? ev.source} />
                    </dl>
                    {ev.summary_zh ? (
                      <div className="mb-2.5">
                        <span className="text-[#8E8E93]">AI 中文摘要：</span>
                        <span className="text-[#2C2C2E]">{ev.summary_zh}</span>
                      </div>
                    ) : null}
                    {ev.relevance_reason ? (
                      <div className="mb-2.5">
                        <span className="text-[#8E8E93]">为什么值得关注：</span>
                        <span className="text-[#1D3557]">{ev.relevance_reason}</span>
                      </div>
                    ) : null}
                    <div className="mb-2.5 flex flex-wrap items-center gap-x-4 gap-y-1">
                      <span className="text-[#8E8E93]">官方来源：</span>
                      {ev.official_source_url ? (
                        <a href={ev.official_source_url} target="_blank" rel="noreferrer" className="text-[#137333] hover:underline no-underline break-all"
                           onClick={(e) => e.stopPropagation()}>
                          ✓ {ev.official_source_name || ev.official_source_url}
                        </a>
                      ) : (
                        <span className="text-[#8E8E93]">
                          官方原文待核实{ev.official_note ? `（${ev.official_note}）` : ''}
                        </span>
                      )}
                    </div>
                    <a
                      href={ev.source_url}
                      target="_blank"
                      rel="noreferrer"
                      className="text-[#1D3557] hover:underline no-underline break-all"
                      onClick={(e) => e.stopPropagation()}
                    >
                      查看原始来源 ↗
                    </a>
                  </div>
                ) : null}
              </div>
            );
          })}
          {items.length < total ? (
            <div className="pt-2 text-center">
              <button
                onClick={() => { const next = limit + 30; setLimit(next); load({ append: true, nextLimit: next }); }}
                disabled={loading}
                className="cr-btn-secondary px-5 py-2 text-[14px]"
              >
                {loading ? '加载中…' : `加载更多（已显示 ${items.length}/${total}）`}
              </button>
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
}

function Item({ label, value }: { label: string; value: string | null | undefined }) {
  return (
    <div>
      <span className="text-[#8E8E93]">{label}：</span>
      <span className="text-[#2C2C2E]">{value || '—'}</span>
    </div>
  );
}

function PageSkeleton() {
  return (
    <div className="mx-auto max-w-6xl px-6 py-10">
      <div className="h-8 w-64 rounded bg-black/5 cr-pulse mb-6" />
      <div className="space-y-3">
        {[0, 1, 2, 3, 4].map((i) => <div key={i} className="h-24 rounded-xl bg-black/5 cr-pulse" />)}
      </div>
    </div>
  );
}
