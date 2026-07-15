// Axiom 产品展示页
// 复刻 Discovery Orbit 风格: 浅灰背景、蓝色强调、大标题、未签名 Mac App 安装说明

import { useState } from "react";

export default function LandingPage() {
  const [copied, setCopied] = useState(false);

  const quarantineCmd = `xattr -dr com.apple.quarantine "/Applications/Axiom.app"`;
  const openCmd = `open "/Applications/Axiom.app"`;
  const fullScript = `$ ${quarantineCmd}\n$ ${openCmd}`;

  const copyCommand = async () => {
    try {
      await navigator.clipboard.writeText(`${quarantineCmd}\n${openCmd}`);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // ignore
    }
  };

  const navItems = [
    { label: "工作台", href: "#workbench" },
    { label: "研究轨道", href: "#journey" },
    { label: "安装说明", href: "#install" },
    { label: "安全", href: "#security" },
  ];

  return (
    <div className="min-h-screen bg-[#f4f4f5] text-[#18181b] font-sans selection:bg-blue-100">
      {/* 顶部导航 */}
      <header className="fixed top-0 left-0 right-0 z-50 bg-[#f4f4f5] border-b border-zinc-200">
        <div className="max-w-6xl mx-auto px-6 h-16 flex items-center justify-between">
          <a href="#" className="flex items-center gap-2 text-[#18181b] font-semibold">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-blue-600">
              <path d="M12 2L2 7l10 5 10-5-10-5z" />
              <path d="M2 17l10 5 10-5" />
              <path d="M2 12l10 5 10-5" />
            </svg>
            <span>Axiom</span>
          </a>
          <nav className="hidden md:flex items-center gap-8 text-sm text-[#52525b]">
            {navItems.map((item) => (
              <a key={item.label} href={item.href} className="hover:text-[#18181b] transition-colors">
                {item.label}
              </a>
            ))}
          </nav>
          <a
            href="#download"
            className="px-4 py-2 bg-[#18181b] text-white text-sm rounded-full hover:bg-[#27272a] transition-colors"
          >
            下载
          </a>
        </div>
      </header>

      {/* Hero */}
      <section id="download" className="pt-32 pb-16 px-6">
        <div className="max-w-4xl mx-auto text-center">
          {/* Logo 插画占位 */}
          <div className="mx-auto w-32 h-32 mb-8 relative">
            <div className="absolute inset-0 bg-gradient-to-br from-blue-100 to-blue-50 rounded-full blur-2xl" />
            <div className="relative w-full h-full flex items-center justify-center">
              <svg width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="text-blue-600">
                <path d="M12 2L2 7l10 5 10-5-10-5z" />
                <path d="M2 17l10 5 10-5" />
                <path d="M2 12l10 5 10-5" />
              </svg>
            </div>
          </div>

          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-white border border-zinc-200 text-[10px] font-semibold tracking-wider text-blue-600 uppercase mb-6">
            <span className="w-1.5 h-1.5 rounded-full bg-blue-600" />
            Axiom Research Core
          </div>

          <h1 className="text-5xl md:text-7xl font-semibold tracking-tight mb-6">
            Research,
            <br />
            <span className="text-blue-600">made local.</span>
          </h1>

          <p className="text-lg md:text-xl text-[#52525b] max-w-2xl mx-auto mb-10">
            云端推理，本地执行。研究过程、证据与产物，始终清晰可追溯。
          </p>

          <div className="flex flex-col sm:flex-row items-center justify-center gap-4 mb-6">
            <a
              href="/Axiom_0.0.1_aarch64.dmg"
              className="group inline-flex items-center gap-2 px-6 py-3 bg-blue-600 text-white rounded-full font-medium hover:bg-blue-700 transition-colors shadow-lg shadow-blue-600/20"
            >
              <span>下载 macOS · Apple Silicon</span>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="group-hover:translate-y-0.5 transition-transform">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                <polyline points="7 10 12 15 17 10" />
                <line x1="12" y1="15" x2="12" y2="3" />
              </svg>
            </a>
            <a
              href="#workbench"
              className="inline-flex items-center gap-2 px-6 py-3 bg-white text-[#18181b] border border-zinc-200 rounded-full font-medium hover:bg-zinc-50 transition-colors"
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" className="text-blue-600">
                <polygon points="5 3 19 12 5 21 5 3" />
              </svg>
              <span>看它如何工作</span>
            </a>
          </div>

          <div className="text-xs text-[#a1a1aa] mb-16">
            v0.0.1 · beta · macOS 12+ · Local Runtime
          </div>

          {/* 未签名安装提示 */}
          <div id="install" className="max-w-2xl mx-auto bg-white rounded-2xl border border-zinc-200 shadow-xl shadow-zinc-200/50 p-6 text-left">
            <div className="flex items-start justify-between mb-4">
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 rounded-xl bg-amber-50 flex items-center justify-center text-amber-600 text-lg font-bold">
                  β
                </div>
                <div>
                  <div className="text-[10px] font-semibold tracking-wider text-amber-600 uppercase">
                    Test Build · 首次安装
                  </div>
                  <div className="text-base font-semibold text-[#18181b]">当前测试版暂未签名</div>
                </div>
              </div>
              <span className="px-2 py-1 rounded-md bg-amber-50 text-amber-700 text-[10px] font-medium">
                安装必读
              </span>
            </div>

            <p className="text-sm text-[#52525b] mb-4">
              下载后先将 <strong>Axiom.app</strong> 拖入“应用程序”(/Applications)，再打开终端执行：
            </p>

            <div className="relative bg-[#0f172a] rounded-xl p-4 overflow-x-auto">
              <pre className="text-sm font-mono text-zinc-300 leading-relaxed">
                <code>{fullScript}</code>
              </pre>
              <button
                onClick={copyCommand}
                className="absolute top-3 right-3 px-3 py-1.5 bg-white/10 hover:bg-white/20 text-white text-xs rounded-md transition-colors"
              >
                {copied ? "已复制" : "复制命令"}
              </button>
            </div>

            <div className="mt-4 text-xs text-[#71717a] space-y-1">
              <p>• 第一句用于移除 macOS 对未签名应用的隔离标记（quarantine）。</p>
              <p>• 第二句启动应用；首次打开仍可能需要在 <strong>系统设置 → 隐私与安全性</strong> 中点击“仍要打开”。</p>
              <p>• 不需要 Apple Developer 账号，也不需要上架 Mac App Store，仅供本地自用。</p>
            </div>
          </div>
        </div>
      </section>

      {/* 工作台概念展示 */}
      <section id="workbench" className="py-24 px-6">
        <div className="max-w-6xl mx-auto">
          <div className="text-center mb-16">
            <div className="text-[10px] font-semibold tracking-wider text-blue-600 uppercase mb-3">
              The Research Workbench
            </div>
            <h2 className="text-3xl md:text-4xl font-semibold mb-4">
              本地项目 · 云端推理 · 受控执行
            </h2>
            <p className="text-[#52525b] max-w-2xl mx-auto">
              研究不该散落在聊天、终端、文件夹与浏览器标签之间。Axiom 把问题、证据、本地工具、执行日志与最终产物组织成一条可检查、可回看、可继续推进的研究轨道。
            </p>
          </div>

          <div className="rounded-[2.5rem] bg-[#0f172a] p-8 md:p-16 overflow-hidden shadow-2xl shadow-zinc-900/20">
            <div className="grid md:grid-cols-2 gap-12 items-center">
              <div className="space-y-6">
                {[
                  {
                    num: "01",
                    title: "Project Boundary",
                    head: "绑定项目，建立唯一默认边界。",
                    desc: "选择本地目录后，所有读取、修改、命令与预览都围绕这个边界发生。",
                    tags: ["realpath", "symlink guard", "session scope"],
                  },
                  {
                    num: "02",
                    title: "Local Analysis",
                    head: "大型数据留在本地。洞察进入推理。",
                    desc: "客户端分块读取并运行项目已有工具，只把 schema、采样和统计摘要交给云端。",
                    tags: ["Python", "streaming logs", "cancelable"],
                  },
                  {
                    num: "03",
                    title: "Human in the Loop",
                    head: "模型提出修改。你决定是否写入。",
                    desc: "原文件修改必须经过提案、Diff、确认与原子写入，拒绝后研究仍可继续。",
                    tags: ["proposed edit", "atomic write", "audit"],
                  },
                  {
                    num: "04",
                    title: "Local Artifacts",
                    head: "结果留在工作台，也留在你的项目里。",
                    desc: "Markdown、CSV、图像、PDF、Diff 与日志使用统一的产物查看器。",
                    tags: ["preview", "Finder", "traceable"],
                  },
                ].map((item) => (
                  <div
                    key={item.num}
                    className="p-5 rounded-2xl bg-white/5 border border-white/10 hover:bg-white/10 transition-colors"
                  >
                    <div className="text-xs text-blue-400 font-mono mb-1">{item.num}</div>
                    <div className="text-[10px] tracking-wider text-zinc-500 uppercase mb-2">{item.title}</div>
                    <h3 className="text-lg font-semibold text-zinc-100 mb-2">{item.head}</h3>
                    <p className="text-sm text-zinc-400 mb-3">{item.desc}</p>
                    <div className="flex flex-wrap gap-2">
                      {item.tags.map((tag) => (
                        <span key={tag} className="px-2 py-0.5 rounded-md bg-white/5 text-zinc-500 text-[10px] border border-white/10">
                          {tag}
                        </span>
                      ))}
                    </div>
                  </div>
                ))}
              </div>

              <div className="relative">
                <div className="absolute -inset-4 bg-gradient-to-br from-blue-500/20 to-purple-500/20 rounded-3xl blur-2xl" />
                <div className="relative bg-zinc-900 rounded-2xl border border-white/10 p-6 shadow-2xl">
                  <div className="flex items-center gap-2 mb-4">
                    <div className="w-3 h-3 rounded-full bg-red-500/80" />
                    <div className="w-3 h-3 rounded-full bg-amber-500/80" />
                    <div className="w-3 h-3 rounded-full bg-green-500/80" />
                    <div className="ml-auto text-xs text-zinc-500">Axiom Workbench</div>
                  </div>
                  <div className="space-y-3">
                    <div className="h-2 w-3/4 bg-zinc-800 rounded" />
                    <div className="h-2 w-1/2 bg-zinc-800 rounded" />
                    <div className="h-24 bg-zinc-800/50 rounded-lg mt-4 flex items-center justify-center text-zinc-600 text-xs">
                      研究轨道可视化预览
                    </div>
                    <div className="grid grid-cols-2 gap-3">
                      <div className="h-16 bg-zinc-800/50 rounded-lg" />
                      <div className="h-16 bg-zinc-800/50 rounded-lg" />
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* 核心能力 */}
      <section className="py-24 px-6 bg-white">
        <div className="max-w-6xl mx-auto">
          <div className="text-center mb-16">
            <div className="text-[10px] font-semibold tracking-wider text-blue-600 uppercase mb-3">
              Built for Serious Research
            </div>
            <h2 className="text-3xl md:text-4xl font-semibold">简单、专注，只呈现此刻真正需要的能力。</h2>
          </div>

          <div className="grid md:grid-cols-3 gap-8">
            {[
              {
                num: "01 · LOCAL FIRST",
                title: "本地优先",
                desc: "原始数据不必成为上下文成本。大型 CSV、图像与模型输出优先在本地分块、采样、统计。隐私与效率不再需要二选一。",
              },
              {
                num: "02 · CONTROLLED EXECUTION",
                title: "受控执行",
                desc: "授权不是打断，而是可理解的决定。命令、修改与删除都说明目标、范围和风险。一次授权不会偷偷扩展为任意 Shell。",
              },
              {
                num: "03 · ARTIFACTS IN CONTEXT",
                title: "上下文产物",
                desc: "结果不是附件，而是研究的一部分。消息、文件树、执行日志和预览器共享同一条导航关系，让每个结论都能回到证据。",
              },
            ].map((item) => (
              <div key={item.num} className="p-6 rounded-2xl bg-[#f4f4f5] border border-zinc-100">
                <div className="text-[10px] font-semibold tracking-wider text-blue-600 mb-3">{item.num}</div>
                <h3 className="text-xl font-semibold mb-3">{item.title}</h3>
                <p className="text-sm text-[#52525b] leading-relaxed">{item.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* 安全 */}
      <section id="security" className="py-24 px-6">
        <div className="max-w-6xl mx-auto">
          <div className="rounded-[2.5rem] bg-[#18181b] text-white p-8 md:p-16">
            <div className="grid md:grid-cols-2 gap-12 items-center">
              <div>
                <div className="text-[10px] font-semibold tracking-wider text-blue-400 uppercase mb-4">
                  Security is Part of the Product
                </div>
                <h2 className="text-3xl md:text-4xl font-semibold mb-6">
                  云端负责推理。
                  <br />
                  客户端负责受控执行。
                </h2>
                <p className="text-zinc-400 mb-8">
                  来自模型的每个本地请求都被视为不可信。前端没有 Node、Shell 或任意文件系统直通。
                </p>
                <ul className="space-y-4">
                  {[
                    "项目边界：路径规范化、realpath 与符号链接逃逸检查。",
                    "结构化工具协议：能力、程序、参数、路径与风险可验证。",
                    "有范围的授权：绑定项目、会话、能力与风险等级。",
                    "本地审计：不记录 Token、密码与完整敏感内容。",
                  ].map((item) => (
                    <li key={item} className="flex items-start gap-3 text-sm text-zinc-300">
                      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-blue-500 shrink-0 mt-0.5">
                        <polyline points="20 6 9 17 4 12" />
                      </svg>
                      {item}
                    </li>
                  ))}
                </ul>
              </div>
              <div className="bg-zinc-900 rounded-2xl border border-white/10 p-8">
                <div className="space-y-4 text-sm font-mono">
                  <div className="text-zinc-500">UNTRUSTED REQUESTS</div>
                  <div className="p-3 rounded-lg bg-white/5 text-zinc-300">Cloud Reasoning</div>
                  <div className="text-zinc-500 text-xs">理解目标 · 规划步骤</div>
                  <div className="p-3 rounded-lg bg-white/5 text-zinc-300 border-l-2 border-blue-500">
                    toolCallId · capability · risk · summary
                  </div>
                  <div className="flex items-center gap-2 text-zinc-500 text-xs">
                    <span>TRUST BOUNDARY</span>
                    <div className="flex-1 h-px bg-zinc-700" />
                  </div>
                  <div className="p-3 rounded-lg bg-blue-500/10 text-blue-200 border border-blue-500/20">
                    Local Capability Broker
                    <div className="text-xs text-blue-300/70 mt-1">验证 · 授权 · 执行 · 审计</div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Footer */}
      <footer className="py-12 px-6 border-t border-zinc-200">
        <div className="max-w-6xl mx-auto flex flex-col md:flex-row items-center justify-between gap-4">
          <a href="#" className="flex items-center gap-2 text-[#18181b] font-semibold">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-blue-600">
              <path d="M12 2L2 7l10 5 10-5-10-5z" />
              <path d="M2 17l10 5 10-5" />
              <path d="M2 12l10 5 10-5" />
            </svg>
            <span>Axiom</span>
          </a>
          <p className="text-sm text-[#71717a]">Research, made local.</p>
          <p className="text-xs text-[#a1a1aa]">© 2026 Axiom Project</p>
        </div>
      </footer>
    </div>
  );
}
