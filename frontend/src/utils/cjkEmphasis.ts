// CommonMark 的星号定界规则在“闭合星号紧跟中文标点、后接中文正文”时拒绝闭合，
// 例如 **低风险假设“原理层薄弱”**编排 会把星号原样显示出来。
// 原因：闭合定界符前的中文引号属于标点，按规则其后必须跟空白或标点才能闭合，
// 而这里跟的是汉字。在这类标点与星号之间插入零宽空格（U+200B，既非空白也非标点），
// 让闭合定界符满足 right-flanking 条件；对本来就能闭合的输入没有可见变化。
const CLOSE_STAR_AFTER_CJK_PUNCT =
  /([\u2015\u2018-\u201d\u2026\u3001\u3002\u3009\u300b\u300f\u3011\u3015\uff01\uff09\uff0c\uff1a\uff1b\uff1f\uff5d])(\*\*)(?=[^\s*])/g;

export function cjkFriendlyStrongEmphasis(markdown: string): string {
  return String(markdown || '').replace(CLOSE_STAR_AFTER_CJK_PUNCT, '$1\u200b$2');
}

// 生成侧常写出 LaTeX 的 \(...\) 与 \[...\] 定界符，而渲染链只识别 $ 与 $$。
// 这里做一次定界符归一化；公式本体原样保留，交给 KaTeX 渲染。
const DISPLAY_TEX = /\\\[([\s\S]*?)\\\]/g;
const INLINE_TEX = /\\\(([\s\S]*?)\\\)/g;

export function normalizeLatexDelimiters(markdown: string): string {
  const text = String(markdown || '');
  return text
    .replace(DISPLAY_TEX, (_match, tex: string) => `$$${tex.trim()}$$`)
    .replace(INLINE_TEX, (_match, tex: string) => `$${tex.trim()}$`);
}
