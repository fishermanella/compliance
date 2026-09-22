import type { Metadata } from 'next';
import Link from 'next/link';
import './globals.css';

export const metadata: Metadata = {
  title: {
    default: 'Compliance Radar | 合规雷达',
    template: '%s | Compliance Radar',
  },
  description:
    'AI Compliance Monitor：每周自动监测全球 AI 与数据合规监管动态，AI 分析相关性、生成中文摘要、核验官方来源并输出合规周报。',
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body className="antialiased min-h-screen flex flex-col">
        <header className="sticky top-0 z-40 border-b border-black/5 bg-white/80 backdrop-blur-md">
          <div className="mx-auto max-w-6xl px-6 h-16 flex items-center justify-between">
            <Link href="/" className="flex items-center gap-2.5 no-underline">
              <span className="inline-flex h-8 w-8 items-center justify-center rounded-lg bg-[#1D3557] text-white text-[15px] font-semibold select-none">
                C
              </span>
              <span className="text-[17px] font-semibold tracking-tight text-[#1D1D1F]">
                Compliance Radar
              </span>
              <span className="hidden sm:inline text-[12px] text-[#8E8E93] font-normal ml-1">
                AI 合规监管动态监测
              </span>
            </Link>
            <nav className="flex items-center gap-1 text-[14px]">
              <Link
                href="/"
                className="px-3 py-1.5 rounded-md text-[#1D1D1F] hover:bg-black/5 transition-colors no-underline"
              >
                监测总览
              </Link>
              <Link
                href="/monitor"
                className="px-3 py-1.5 rounded-md text-[#1D1D1F] hover:bg-black/5 transition-colors no-underline"
              >
                监管动态
              </Link>
            </nav>
          </div>
        </header>
        <main className="flex-1">{children}</main>
        <footer className="border-t border-black/5 py-6">
          <div className="mx-auto max-w-6xl px-6 text-[12px] text-[#8E8E93] flex flex-wrap gap-x-6 gap-y-1">
            <span>Compliance Radar · AI Compliance Monitor</span>
            <span>数据来源：DataGuidance / 微信公众号「数据何规」/ 小宇宙「那一片数据星辰」</span>
            <span>AI 分析仅供合规参考，请以官方原文为准</span>
          </div>
        </footer>
      </body>
    </html>
  );
}
