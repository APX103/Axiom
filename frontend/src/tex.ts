// LaTeX → 渲染 HTML 的轻量转换。
// 不依赖完整 LaTeX 编译,做"够看"的渲染:
// - 平衡花括号提取 \command{...} (支持嵌套,如 \title{\textbf{...}})
// - \section/\subsection → h2/h3/h4
// - \title/\author/\date → 头部
// - \begin{itemize}/\item → 列表
// - \begin{abstract} → 摘要块
// - $...$ \(...\) \[...\] 数学 → KaTeX
// - \textbf/\textit/\emph/\texttt → 加粗/斜体/等宽
// - \cite{key} → [key]
// - 剥离 % 注释
// - 处理 \\ 换行
// 完整 .tex 仍可下载本地编译。

import katex from "katex";

interface BibEntry {
  key: string;
  type: string; // article / inproceedings / misc / book ...
  fields: Record<string, string>; // title / author / year / journal / ...
}

interface ParsedDoc {
  title: string;
  author: string;
  date: string;
  abstract: string;
  body: string;
  references: BibEntry[]; // 解析出的参考文献列表
  citeMap: Record<string, number>; // cite key → 编号 (1-based)
}

/** 解析 BibTeX 内容成条目列表。简易: 按 @type{key, fields} 切。 */
function parseBibtex(bib: string): BibEntry[] {
  const entries: BibEntry[] = [];
  const re = /@(\w+)\s*\{\s*([^,\s]+)\s*,/g;
  let m: RegExpExecArray | null;
  const starts: { type: string; key: string; idx: number }[] = [];
  while ((m = re.exec(bib))) {
    starts.push({ type: m[1].toLowerCase(), key: m[2], idx: re.lastIndex });
  }
  for (let i = 0; i < starts.length; i++) {
    const s = starts[i];
    const end = i + 1 < starts.length ? starts[i + 1].idx - 1 : bib.length;
    const block = bib.slice(s.idx, end);
    // 解析 fields: key = {value} 或 key = "value"
    const fields: Record<string, string> = {};
    const fre = /(\w+)\s*=\s*[\{"]([\s\S]*?)[\}"]/g;
    let fm: RegExpExecArray | null;
    while ((fm = fre.exec(block))) {
      fields[fm[1].toLowerCase()] = fm[2].replace(/[{}]/g, "").trim();
    }
    entries.push({ key: s.key, type: s.type, fields });
  }
  return entries;
}

/** 格式化一个 bib 条目为可读引用文本。 */
export function formatBibEntry(e: BibEntry): string {
  const f = e.fields;
  const authors = f.author || "";
  const title = f.title || "";
  const year = f.year || "";
  let venue = "";
  if (f.journal) venue = f.journal;
  else if (f.booktitle) venue = f.booktitle;
  else if (f.howpublished) venue = f.howpublished.replace(/\\url\{/g, "").replace(/\}/g, "");
  else if (f.publisher) venue = f.publisher;
  const vol = f.volume ? ` ${f.volume}` : "";
  const pages = f.pages ? `:${f.pages}` : "";
  let out = "";
  if (authors) out += `${authors}. `;
  if (title) out += `"${title}," `;
  if (venue) out += `${venue}`;
  if (vol || pages) out += `${vol}${pages}`;
  if (year) out += `, ${year}`;
  return out.trim() || e.key;
}

/** 提取 \cmd{...},用平衡花括号匹配 (支持嵌套)。返回首个匹配的内容(去外层括号)。 */
function extractBraced(tex: string, cmd: string): string {
  // 用字符串查找 \cmd{,避免正则转义陷阱
  const tag = `\\${cmd}{`;
  const idx = tex.indexOf(tag);
  if (idx === -1) return "";
  let i = idx + tag.length; // 指向 { 之后
  let depth = 1;
  const begin = i;
  while (i < tex.length && depth > 0) {
    if (tex[i] === "{") depth++;
    else if (tex[i] === "}") depth--;
    if (depth === 0) return tex.slice(begin, i).trim();
    i++;
  }
  return tex.slice(begin).trim(); // 未闭合,取到末尾
}

// 渲染期间的引用编号映射 (模块级, parseTex 设置)
let _citeMap: Record<string, number> = {};

export function parseTex(tex: string, bib?: string): ParsedDoc {
  // 先剥离注释 (% 到行尾,但 \% 是字面量)
  const cleaned = stripComments(tex);

  const title = extractBraced(cleaned, "title");
  const author = extractBraced(cleaned, "author");
  const date = extractBraced(cleaned, "date");

  // abstract
  let abstract = "";
  const absM = cleaned.match(/\\begin\{abstract\}([\s\S]*?)\\end\{abstract\}/);
  if (absM) abstract = absM[1].trim();

  // 正文
  const docM = cleaned.match(/\\begin\{document\}([\s\S]*?)\\end\{document\}/);
  let body = docM ? docM[1] : cleaned;
  body = body.replace(/\\begin\{abstract\}[\s\S]*?\\end\{abstract\}/g, "");
  body = body.replace(/\\maketitle/g, "");
  // 去掉 \bibliographystyle / \bibliography (前端不编译,自己生成列表)
  body = body.replace(/\\bibliographystyle\{[^}]*\}/g, "");
  body = body.replace(/\\bibliography\{[^}]*\}/g, "");

  // 解析 bib + 构建引用编号 (按正文首次出现顺序)
  const references: BibEntry[] = bib ? parseBibtex(bib) : [];
  _citeMap = {};
  let nextNum = 1;
  const citeRe = /\\cite[ptp]*\{([^}]+)\}|\\cite\{([^}]+)\}/g;
  let cm: RegExpExecArray | null;
  while ((cm = citeRe.exec(cleaned))) {
    const keys = (cm[1] || cm[2]).split(",").map((k) => k.trim());
    for (const k of keys) {
      if (!_citeMap[k]) _citeMap[k] = nextNum++;
    }
  }

  return {
    title: renderInline(title),
    author: renderInline(author),
    date: renderInline(date),
    abstract: renderInline(abstract),
    body: bodyToHtml(body),
    references,
    citeMap: _citeMap,
  };
}

/** 剥离 LaTeX 注释 (% 到行尾),保留 \%。 */
function stripComments(tex: string): string {
  return tex
    .split("\n")
    .map((line) => line.replace(/(^|[^\\])%.*/g, "$1"))
    .join("\n");
}

function bodyToHtml(body: string): string {
  // 表格先转 (tabular / tabularx / table 环境)
  body = body.replace(/\\begin\{tabular\}(\{[^}]*\})?([\s\S]*?)\\end\{tabular\}/g, (_, _spec, inner) =>
    tabularToHtml(inner)
  );
  // tabularx: 去掉外层 table 环境包裹 + 内部 tabularx{宽度}{spec}...end
  body = body.replace(/\\begin\{tabularx\}\{[^}]*\}\{[^}]*\}([\s\S]*?)\\end\{tabularx\}/g, (_, inner) =>
    tabularToHtml(inner)
  );
  // table 环境: 取其内的 tabularx/tabular (上面已转), 剩余 caption 文本
  body = body.replace(/\\begin\{table\}([\s\S]*?)\\end\{table\}/g, (_, inner) => {
    const cap = inner.match(/\\caption\{([\s\S]*?)\}/);
    const tbl = inner.match(/<table[\s\S]*?<\/table>/);
    return (cap ? `<p class="text-xs text-muted">${renderInline(cap[1])}</p>` : "") + (tbl ? tbl[0] : "");
  });

  // display math \[...\] 可跨行, 先整体提取成占位行, 避免被逐行处理拆碎
  body = body.replace(/\\\[([\s\S]*?)\\\]/g, (_whole, expr: string) => {
    const rendered = katexRender(expr.trim(), true);
    return `\n@@DISPLAYMATH@@${rendered}@@/DISPLAYMATH@@\n`;
  });

  // lstlisting 代码块 → <pre><code> (保留原样, 不走 renderInline 避免转义混乱)
  body = body.replace(/\\begin\{lstlisting\}(\[[^\]]*\])?([\s\S]*?)\\end\{lstlisting\}/g, (_whole, _opt, code) => {
    const esc = code.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    return `\n@@LST@@${esc}@@/LST@@\n`;
  });
  // framed 环境 → 灰底提示框 (整块, 内部按段落 renderInline)
  body = body.replace(/\\begin\{framed\}([\s\S]*?)\\end\{framed\}/g, (_whole, inner) => {
    const parts = inner
      .split(/\n\s*\n/)
      .map((p: string) => p.trim())
      .filter(Boolean)
      .map((p: string) => `<p>${renderInline(p)}</p>`)
      .join("");
    return `\n@@FRAMED@@${parts}@@/FRAMED@@\n`;
  });

  const out: string[] = [];
  const lines = body.split("\n");
  let inItemize = false;
  let inEnumerate = false;
  const closeList = () => {
    if (inItemize) {
      out.push("</ul>");
      inItemize = false;
    }
    if (inEnumerate) {
      out.push("</ol>");
      inEnumerate = false;
    }
  };

  for (const raw of lines) {
    const line = raw.trim();
    if (!line) continue;

    let m: RegExpMatchArray | null;
    // 预处理标记的代码块 / framed 块 / display math (已转成占位行)
    if (line.startsWith("@@LST@@")) {
      closeList();
      out.push(`<pre class="bg-code text-inverse p-3 my-2 overflow-auto text-xs rounded"><code>${line.slice(8, -8)}</code></pre>`);
      continue;
    }
    if (line.startsWith("@@FRAMED@@")) {
      closeList();
      out.push(`<div class="my-3 p-3 bg-elevated border-l-4 border-accent-secondary rounded text-sm text-default">${line.slice(10, -10)}</div>`);
      continue;
    }
    if (line.startsWith("@@DISPLAYMATH@@")) {
      closeList();
      out.push(`<div class="my-3 text-center overflow-x-auto text-default">${line.slice(16, -17)}</div>`);
      continue;
    }
    if ((m = line.match(/^\\section\*?\{([\s\S]*)\}$/))) {
      closeList();
      // 去掉标题中残留的 \label{...}
      const title = m[1].replace(/\\label\{[^}]*\}/g, "").trim();
      out.push(`<h2>${renderInline(title)}</h2>`);
    } else if ((m = line.match(/^\\subsection\*?\{([\s\S]*)\}$/))) {
      closeList();
      const title = m[1].replace(/\\label\{[^}]*\}/g, "").trim();
      out.push(`<h3>${renderInline(title)}</h3>`);
    } else if ((m = line.match(/^\\subsubsection\*?\{([\s\S]*)\}$/))) {
      closeList();
      const title = m[1].replace(/\\label\{[^}]*\}/g, "").trim();
      out.push(`<h4>${renderInline(title)}</h4>`);
    } else if (/\\begin\{itemize\}/.test(line)) {
      closeList();
      inItemize = true;
      out.push("<ul>");
    } else if (/\\begin\{enumerate\}/.test(line)) {
      closeList();
      inEnumerate = true;
      out.push("<ol>");
    } else if (/\\end\{itemize\}|\\end\{enumerate\}/.test(line)) {
      closeList();
    } else if ((m = line.match(/^\\item\s+([\s\S]*)/))) {
      out.push(`<li>${renderInline(m[1])}</li>`);
    } else if (/^\\(title|author|date|maketitle|usepackage|documentclass|begin\{document\}|end\{document\}|label\{|tableofcontents|bibliographystyle|bibliography\{)/.test(line)) {
      // skip 元命令
    } else {
      closeList();
      out.push(`<p>${renderInline(line)}</p>`);
    }
  }
  closeList();
  return out.join("\n");
}

/** 渲染行内 LaTeX → HTML。 */
export function renderInline(s: string): string {
  if (!s) return "";
  // 数学优先 (避免内容里的 $ 被误处理)
  s = s.replace(/\$\$([\s\S]+?)\$\$/g, (_, e) => katexRender(e, true));
  s = s.replace(/\\\[([\s\S]+?)\\\]/g, (_, e) => katexRender(e, true));
  s = s.replace(/(^|[^\\])\$([^$\n]+?)\$/g, (_m, pre, e) => pre + katexRender(e, false));
  s = s.replace(/\\\(([\s\S]+?)\\\)/g, (_, e) => katexRender(e, false));

  // \\ 换行 → <br>
  s = s.replace(/\\\\/g, "<br>");
  // \par → 段落
  s = s.replace(/\\par\b/g, "<br>");

  // 格式命令 (用平衡括号处理嵌套)
  const fmtCommands: [string, string][] = [
    ["textbf", "strong"],
    ["textit", "em"],
    ["emph", "em"],
    ["texttt", "code"],
    ["underline", "u"],
    ["small", "span"],
    ["large", "span"],
    ["textsc", "span"],
  ];
  for (const [cmd, tag] of fmtCommands) {
    s = replaceBraced(s, cmd, (inner) => `<${tag}>${inner}</${tag}>`);
  }

  // siunitx 宏包: \SI{数值}{单位} → "数值 单位"
  // 单位里的 \giga → G, \mega → M, \kilo → k, \milli → m, \micro → µ, \nano → n 等
  const UNIT_MAP: Record<string, string> = {
    giga: "G", mega: "M", kilo: "k", milli: "m", micro: "µ", nano: "n",
    pico: "p", tera: "T", centi: "c", deci: "d",
    hertz: "Hz", joule: "J", watt: "W", volt: "V", ampere: "A",
    kelvin: "K", celsius: "°C", mole: "mol", gram: "g", kilogram: "kg",
    meter: "m", metre: "m", second: "s", liter: "L", litre: "L",
    pascal: "Pa", newton: "N", tesla: "T", electronvolt: "eV",
    percent: "%", degree: "°", radian: "rad", bar: "bar",
    voltampere: "VA", farad: "F", ohm: "Ω", siemens: "S", henry: "H",
    candela: "cd", lux: "lx", weber: "Wb", gray: "Gy", sievert: "Sv",
    becquerel: "Bq", katal: "kat",
  };
  const convUnit = (unitStr: string): string => {
    let s = unitStr;
    // \square\meter → meter², \cubic\meter → meter³ (幂次跟在被修饰的单位后面)
    s = s.replace(/\\square\s*\\([a-z]+)/g, (_, u) => `\\${u}²`);
    s = s.replace(/\\cubic\s*\\([a-z]+)/g, (_, u) => `\\${u}³`);
    // \per → / (在单位替换前处理, 这样 /m 的结构保留)
    s = s.replace(/\\per\b\s*/g, "/");
    // \sqrt{m} → √m
    s = s.replace(/\\sqrt\{([^}]*)\}/g, (_, e) => `√${e}`);
    // 替换前缀+单位命令为符号
    for (const [cmd, sym] of Object.entries(UNIT_MAP)) {
      s = s.replace(new RegExp(`\\\\${cmd}\\b`, "g"), sym);
    }
    // 残余的 \text{...} 去括号
    s = s.replace(/\\text\{([^}]*)\}/g, "$1");
    // 清理残余的反斜杠命令和多余空格
    s = s.replace(/\\[a-zA-Z]+/g, "").replace(/\s+/g, " ").trim();
    return s;
  };

  // \SIrange{起}{止}{单位} → "起–止 单位"
  s = s.replace(/\\SIrange\s*\{([^}]*)\}\s*\{([^}]*)\}\s*\{([^}]*)\}/g,
    (_m, lo: string, hi: string, unit: string) => {
      const u = convUnit(unit);
      return u ? `${lo}–${hi}\\,${u}` : `${lo}–${hi}`;
    });

  // \SI{数值}{单位} → "数值\\,单位" (\\, 是 LaTeX 的小间距, 后续渲染)
  s = s.replace(/\\SI\s*\{([^}]*)\}\s*\{([^}]*)\}/g,
    (_m, val: string, unit: string) => {
      const u = convUnit(unit);
      return u ? `${val}\\,${u}` : val;
    });

  // 引用 (支持 natbib: \cite \citep \citet \citeauthor \citealp 等)
  // 渲染成可点击的 [N] 锚点 → 底部参考文献列表 (N 按正文首次出现顺序编号)
  s = s.replace(/\\cite[ptp]*\{([^}]*)\}/g, (_whole, keys: string) => {
    return keys
      .split(",")
      .map((k: string) => k.trim())
      .map((k: string) => {
        const n = _citeMap[k];
        if (n) return `<a href="#ref-${n}" class="text-link">[${n}]</a>`;
        return `<a class="text-faint">[?]</a>`; // bib 缺失
      })
      .join("");
  });
  s = s.replace(/\\cite\{([^}]*)\}/g, (_whole, keys: string) => {
    return keys
      .split(",")
      .map((k: string) => k.trim())
      .map((k: string) => {
        const n = _citeMap[k];
        if (n) return `<a href="#ref-${n}" class="text-blue-600">[${n}]</a>`;
        return `<a class="text-gray-400">[?]</a>`;
      })
      .join("");
  });
  s = s.replace(/\\ref\{([^}]*)\}/g, "↗");
  s = s.replace(/\\label\{[^}]*\}/g, "");
  s = s.replace(/\\url\{([^}]*)\}/g, (_, u) => `<a href="${u}" class="text-link underline break-all">${u}</a>`);
  s = replaceBraced(s, "href", (inner) => {
    // \href{url}{text}
    const hm = inner.match(/^([^}]*)\}\{([\s\S]*)$/);
    if (hm) return `<a href="${hm[1]}" class="text-link underline">${hm[2]}</a>`;
    return inner;
  });

  // 清理纯间距命令
  s = s.replace(/\\(noindent|vspace\*?\{[^}]*\}|hspace\*?\{[^}]*\}|smallskip|medskip|bigskip|newpage|clearpage|pagebreak|linebreak|hfill)/g, " ");

  // 丢弃未知 \command{content} → 保留 content (平衡括号)
  s = stripUnknownCommands(s);

  // 丢弃剩余的无参数未知命令 \command
  s = s.replace(/\\[a-zA-Z]+\b/g, "");
  // 丢弃 \后接单个非字母符号 (如 \, \&)
  s = s.replace(/\\([{}%&#$_])/g, "$1");
  s = s.replace(/\\[,!;:]/g, " ");

  return s;
}

/** 把 \cmd{...} (平衡括号) 替换为 replacer(innerContent)。 */
function replaceBraced(s: string, cmd: string, replacer: (inner: string) => string): string {
  const tag = `\\${cmd}{`;
  let result = "";
  let i = 0;
  while (i < s.length) {
    const idx = s.indexOf(tag, i);
    if (idx === -1) {
      result += s.slice(i);
      break;
    }
    result += s.slice(i, idx);
    // 找匹配的 }
    let j = idx + tag.length;
    let depth = 1;
    while (j < s.length && depth > 0) {
      if (s[j] === "{") depth++;
      else if (s[j] === "}") depth--;
      if (depth === 0) break;
      j++;
    }
    const inner = s.slice(idx + tag.length, j);
    result += replacer(inner);
    i = j + 1;
  }
  return result;
}

/** 剥离未知命令 \xxx{content} → content (保留内容,去掉命令名和花括号)。
 * 支持多参数命令: \xxx{a}{b} → "a b" (连续花括号组的内容用空格拼接)。
 */
function stripUnknownCommands(s: string): string {
  for (let pass = 0; pass < 5; pass++) {
    const before = s;
    let out = "";
    let i = 0;
    const knownFmt = new Set([
      "textbf", "textit", "emph", "texttt", "underline",
      "cite", "citep", "citet", "citeauthor", "citealp",
      "ref", "url", "href", "label",
    ]);
    while (i < s.length) {
      const m = /^\\([a-zA-Z@]+)\s*(?=\{)/.exec(s.slice(i));
      if (m) {
        const cmd = m[1];
        // m[0] 不含 {, 所以 pos 指向第一个 {
        let pos = i + m[0].length;

        // 收集所有连续的 {..} 参数组
        const args: string[] = [];
        while (pos < s.length && s[pos] === "{") {
          // 平衡括号提取一组
          let depth = 1;
          let j = pos + 1;
          while (j < s.length && depth > 0) {
            if (s[j] === "{") depth++;
            else if (s[j] === "}") depth--;
            if (depth === 0) break;
            j++;
          }
          args.push(s.slice(pos + 1, j));
          pos = j + 1;
          // 跳过参数间的空白 (LaTeX 允许 \SI{1}{2} 或 \SI{1} {2})
          while (pos < s.length && s[pos] === " ") pos++;
        }

        if (knownFmt.has(cmd)) {
          // 已知格式命令: 保留原样 (第一个参数)
          out += `\\${cmd}{${args[0]}}`;
        } else {
          // 未知命令: 保留所有参数内容, 空格拼接
          out += args.join(" ");
        }
        i = pos;
      } else {
        out += s[i];
        i++;
      }
    }
    s = out;
    if (s === before) break;
  }
  return s;
}

function katexRender(expr: string, display: boolean): string {
  try {
    return katex.renderToString(expr, { displayMode: display, throwOnError: false });
  } catch {
    return `<code>${expr}</code>`;
  }
}

function tabularToHtml(inner: string): string {
  const rows = inner
    .replace(/\\hline/g, "")
    .split("\\\\")
    .map((r) => r.trim())
    .filter(Boolean);
  const trs = rows.map((r) => {
    const cells = r.split("&").map((c) => renderInline(c.trim()));
    return `<tr>${cells.map((c) => `<td>${c}</td>`).join("")}</tr>`;
  });
  return `<table class="border-collapse my-3"><tbody>${trs.join("")}</tbody></table>`;
}
