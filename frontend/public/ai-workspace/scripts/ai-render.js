/**
 * AI 答疑 · 富文本渲染
 *
 * 同步加载：marked（Markdown 解析）+ DOMPurify（XSS 净化）
 * 懒加载：KaTeX（公式渲染）+ highlight.js（代码高亮）
 *
 * 触发条件：检测到 $ / $$ / ``` 才加载 KaTeX + highlight.js
 */

(function () {
  'use strict';

  // 同步加载 marked + DOMPurify（通过 CDN）
  var MARKED_CDN = 'https://cdn.jsdelivr.net/npm/marked@11.1.1/marked.min.js';
  var DOMPURIFY_CDN = 'https://cdn.jsdelivr.net/npm/dompurify@3.0.8/dist/purify.min.js';
  var KATEX_CSS = 'https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css';
  var KATEX_JS = 'https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.js';
  var HLJS_CDN = 'https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js';

  // 简单状态
  var markedReady = null;
  var purifyReady = null;
  var katexReady = null;
  var hljsReady = null;

  function loadScript(src) {
    return new Promise(function (resolve, reject) {
      var s = document.createElement('script');
      s.src = src;
      s.async = true;
      s.onload = function () { resolve(); };
      s.onerror = function () { reject(new Error('Failed to load: ' + src)); };
      document.head.appendChild(s);
    });
  }

  function loadCss(href) {
    return new Promise(function (resolve) {
      var l = document.createElement('link');
      l.rel = 'stylesheet';
      l.href = href;
      l.onload = function () { resolve(); };
      document.head.appendChild(l);
    });
  }

  function ensureMarked() {
    if (window.marked) return Promise.resolve(window.marked);
    if (!markedReady) {
      markedReady = loadScript(MARKED_CDN).then(function () { return window.marked; });
    }
    return markedReady;
  }

  function ensurePurify() {
    if (window.DOMPurify) return Promise.resolve(window.DOMPurify);
    if (!purifyReady) {
      purifyReady = loadScript(DOMPURIFY_CDN).then(function () { return window.DOMPurify; });
    }
    return purifyReady;
  }

  function ensureKatex() {
    if (window.katex) return Promise.resolve(window.katex);
    if (!katexReady) {
      katexReady = Promise.all([loadCss(KATEX_CSS), loadScript(KATEX_JS)]).then(function () {
        return window.katex;
      });
    }
    return katexReady;
  }

  function ensureHljs() {
    if (window.hljs) return Promise.resolve(window.hljs);
    if (!hljsReady) {
      hljsReady = loadScript(HLJS_CDN);
    }
    return hljsReady;
  }

  // 检测需要懒加载
  function needsHeavyLibraries(text) {
    if (!text) return false;
    return /(\$\$.+?\$\$|\$[^$\n]+\$|```)/.test(text);
  }

  // ===== 启发式 LaTeX 兜底（AI 后端常不包裹 $..$） =====
  // 触发条件（任一即可）：
  //   1. ≥ 2 个 LaTeX 命令（\approx, \nabla, \frac, \alpha …）
  //   2. 出现上下标结构：^{...} 或 _{...}
  // 已经包含 $..$ / $$..$$ 的不重复处理。
  function looksLikeLatexParagraph(text) {
    if (!text) return false;
    if (/\$[^$\n]+\$/.test(text)) return false; // 已包裹
    var cmdMatches = text.match(/\\[a-zA-Z]+/g);
    if (cmdMatches && cmdMatches.length >= 2) return true;
    if (/[\^_]\{[^}\n]+\}/.test(text)) return true;
    return false;
  }

  // 自动包裹未包裹的 LaTeX 段落
  // 规则：
  //   - 整段是 LaTeX → 块级 $$..$$（无内部换行，避免被 marked breaks:true 拆成 <br>）
  //   - 段内单行含 LaTeX 但不与 markdown 冲突 → 行内 $..$
  // 注意：$$ 必须紧贴内容；中间出现 \n 会被 marked 渲染为 <br>，破坏 $...$ / $$...$$ 匹配。
  function wrapStandaloneLatex(text) {
    if (!text) return text;
    return text.split(/\n\s*\n/).map(function (paragraph) {
      if (looksLikeLatexParagraph(paragraph)) {
        // 块级公式：多行内容用空格连接（KaTeX 视为同一公式），避免 breaks:true 拆 $$..$$
        return '$$' + paragraph.replace(/\n/g, ' ') + '$$';
      }
      return paragraph.split('\n').map(function (line) {
        if (looksLikeLatexParagraph(line)
            && !/^\s*[-*+>`]/.test(line)
            && !/^#+\s/.test(line)
            && !/^```/.test(line)) {
          return '$' + line.trim() + '$';
        }
        return line;
      }).join('\n');
    }).join('\n\n');
  }

  // 公式替换：$...$ 和 $$...$$
  function renderMathInHtml(html, katex) {
    if (!window.katex) return html;
    // $$...$$ 块级
    html = html.replace(/\$\$([\s\S]+?)\$\$/g, function (_, tex) {
      try {
        return '<div class="katex-display">' + window.katex.renderToString(tex.trim(), { displayMode: true, throwOnError: false, strict: 'ignore' }) + '</div>';
      } catch (e) { return '<code>' + _.replace(/[$]/g, '') + '</code>'; }
    });
    // $...$ 行内
    html = html.replace(/\$([^$\n]+?)\$/g, function (_, tex) {
      try {
        return window.katex.renderToString(tex.trim(), { displayMode: false, throwOnError: false, strict: 'ignore' });
      } catch (e) { return '<code>' + tex + '</code>'; }
    });
    return html;
  }

  // 代码块高亮
  function renderCodeHighlight(container) {
    if (!window.hljs) return;
    var blocks = container.querySelectorAll('pre code');
    blocks.forEach(function (b) {
      try { window.hljs.highlightElement(b); } catch (e) { /* noop */ }
    });
  }

  // 主入口：异步把 markdown 文本转成安全 HTML
  async function renderToHtml(text) {
    if (!text) return '';
    try {
      var marked = await ensureMarked();
      var purify = await ensurePurify();
      marked.setOptions({ breaks: true, gfm: true });
      // 兜底：未包裹的 LaTeX 段落自动包 $..$ 包裹（AI 后端没包的情况）
      var preprocessed = text;
      try { preprocessed = wrapStandaloneLatex(text); }
      catch (latexErr) { preprocessed = text; /* 兜底：原样交给 marked */ }
      var raw = marked.parse(preprocessed);
      var safe = purify.sanitize(raw, { ADD_ATTR: ['target'] });
      // 懒加载：检测到公式/代码块（用预处理后的文本，避免漏掉自动包出来的 $..$）
      if (needsHeavyLibraries(preprocessed)) {
        try { await ensureKatex(); } catch (e) { /* fallback to plain */ }
        try { await ensureHljs(); } catch (e) { /* fallback to plain */ }
        safe = renderMathInHtml(safe);
      }
      return safe;
    } catch (e) {
      // 极端 fallback：escape 纯文本，绝不让 caller 拿到 reject
      var div = document.createElement('div');
      div.textContent = text;
      return div.innerHTML.replace(/\n/g, '<br>');
    }
  }

  // 同步降级（万一 marked/DOMPurify 未加载）
  function renderToHtmlSync(text) {
    if (!text) return '';
    if (window.marked && window.DOMPurify) {
      var raw = window.marked.parse(text);
      return window.DOMPurify.sanitize(raw);
    }
    // 极端 fallback：纯文本 escape
    var div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML.replace(/\n/g, '<br>');
  }

  // 暴露 API
  window.AIRender = {
    renderToHtml: renderToHtml,
    renderToHtmlSync: renderToHtmlSync
  };
})();
