import { useMemo, useRef } from 'react';
import katex from 'katex';
import 'katex/dist/katex.min.css';
import type { ArtifactPublicProjection, ArtifactSelectionReference } from '../../api/artifacts';
import styles from './DocumentRenderer.module.css';

export interface DocumentHeading {
  id: string;
  level: number;
  text: string;
}

type DocumentBlock =
  | { type: 'heading'; level: number; text: string; anchor: string }
  | { type: 'paragraph'; text: string; anchor: string }
  | { type: 'unordered'; items: string[]; anchor: string }
  | { type: 'ordered'; items: string[]; anchor: string }
  | { type: 'quote'; text: string; anchor: string }
  | { type: 'callout'; tone: 'important' | 'example' | 'mistake' | 'note'; title: string; text: string; anchor: string }
  | { type: 'code'; language: string; text: string; anchor: string }
  | { type: 'table'; headers: string[]; rows: string[][]; anchor: string }
  | { type: 'formula'; tex: string; anchor: string }
  | { type: 'rule'; anchor: string };

function slug(value: string, index: number) {
  const base = value.trim().toLowerCase().replace(/[^\p{L}\p{N}]+/gu, '-').replace(/^-|-$/g, '').slice(0, 54);
  return `${base || 'section'}-${index + 1}`;
}

function checksum(value: string) {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return `fnv1a-${(hash >>> 0).toString(16).padStart(8, '0')}`;
}

function splitTableRow(line: string) {
  return line.trim().replace(/^\||\|$/g, '').split('|').map(value => value.trim());
}

function calloutType(raw: string): { tone: 'important' | 'example' | 'mistake' | 'note'; title: string } | null {
  const key = raw.replace(/[\[\]]/g, '').replace(/[！!：:]/g, '').trim().toLowerCase();
  if (/^(重点|important|核心)/.test(key)) return { tone: 'important', title: '重点' };
  if (/^(例子|示例|example)/.test(key)) return { tone: 'example', title: '例子' };
  if (/^(易错|误区|mistake|warning)/.test(key)) return { tone: 'mistake', title: '易错点' };
  if (/^(说明|补充|note)/.test(key)) return { tone: 'note', title: '补充说明' };
  return null;
}

export function parseDocumentBlocks(content: string): { blocks: DocumentBlock[]; headings: DocumentHeading[] } {
  const lines = String(content || '').replace(/\r\n/g, '\n').split('\n');
  const blocks: DocumentBlock[] = [];
  const headings: DocumentHeading[] = [];
  let index = 0;
  let anchorIndex = 0;
  const nextAnchor = (label: string) => slug(label, anchorIndex++);

  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) { index += 1; continue; }

    if (/^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/.test(line)) {
      blocks.push({ type: 'rule', anchor: nextAnchor('divider') });
      index += 1;
      continue;
    }

    const fence = line.match(/^```\s*([\w+-]*)\s*$/);
    if (fence) {
      const language = fence[1] || 'text';
      const body: string[] = [];
      index += 1;
      while (index < lines.length && !/^```\s*$/.test(lines[index])) body.push(lines[index++]);
      if (index < lines.length) index += 1;
      blocks.push({ type: 'code', language, text: body.join('\n'), anchor: nextAnchor(`code-${language}`) });
      continue;
    }

    if (/^\$\$\s*$/.test(line.trim())) {
      const body: string[] = [];
      index += 1;
      while (index < lines.length && !/^\$\$\s*$/.test(lines[index].trim())) body.push(lines[index++]);
      if (index < lines.length) index += 1;
      blocks.push({ type: 'formula', tex: body.join('\n').trim(), anchor: nextAnchor('formula') });
      continue;
    }
    const inlineFormula = line.trim().match(/^\$\$(.+)\$\$$/);
    if (inlineFormula) {
      blocks.push({ type: 'formula', tex: inlineFormula[1].trim(), anchor: nextAnchor('formula') });
      index += 1;
      continue;
    }

    const heading = line.match(/^(#{1,4})\s+(.+)$/);
    if (heading) {
      const level = heading[1].length;
      const text = heading[2].trim();
      const anchor = nextAnchor(text);
      blocks.push({ type: 'heading', level, text, anchor });
      headings.push({ id: anchor, level, text });
      index += 1;
      continue;
    }

    if (line.includes('|') && index + 1 < lines.length && /^\s*\|?\s*:?-{3,}/.test(lines[index + 1])) {
      const headers = splitTableRow(line);
      index += 2;
      const rows: string[][] = [];
      while (index < lines.length && lines[index].includes('|') && lines[index].trim()) rows.push(splitTableRow(lines[index++]));
      blocks.push({ type: 'table', headers, rows, anchor: nextAnchor(headers.join('-')) });
      continue;
    }

    if (/^\s*[-*+]\s+/.test(line)) {
      const items: string[] = [];
      while (index < lines.length && /^\s*[-*+]\s+/.test(lines[index])) items.push(lines[index++].replace(/^\s*[-*+]\s+/, '').trim());
      blocks.push({ type: 'unordered', items, anchor: nextAnchor(items[0] || 'list') });
      continue;
    }

    if (/^\s*\d+[.)]\s+/.test(line)) {
      const items: string[] = [];
      while (index < lines.length && /^\s*\d+[.)]\s+/.test(lines[index])) items.push(lines[index++].replace(/^\s*\d+[.)]\s+/, '').trim());
      blocks.push({ type: 'ordered', items, anchor: nextAnchor(items[0] || 'steps') });
      continue;
    }

    if (/^>\s*/.test(line)) {
      const quoteLines: string[] = [];
      while (index < lines.length && /^>\s*/.test(lines[index])) quoteLines.push(lines[index++].replace(/^>\s?/, ''));
      const first = quoteLines[0] || '';
      const callout = calloutType(first);
      if (callout) {
        blocks.push({
          type: 'callout', tone: callout.tone, title: callout.title,
          text: quoteLines.slice(1).join('\n').trim() || first.replace(/^\[[^\]]+\]\s*/, ''),
          anchor: nextAnchor(callout.title),
        });
      } else {
        blocks.push({ type: 'quote', text: quoteLines.join('\n').trim(), anchor: nextAnchor('quote') });
      }
      continue;
    }

    const paragraph: string[] = [line.trim()];
    index += 1;
    while (index < lines.length && lines[index].trim()
      && !/^(#{1,4})\s+/.test(lines[index])
      && !/^```/.test(lines[index])
      && !/^\s*([-*+]|\d+[.)])\s+/.test(lines[index])
      && !/^>\s*/.test(lines[index])
      && !/^\$\$/.test(lines[index].trim())) {
      if (lines[index].includes('|') && index + 1 < lines.length && /^\s*\|?\s*:?-{3,}/.test(lines[index + 1])) break;
      paragraph.push(lines[index].trim());
      index += 1;
    }
    const text = paragraph.join(' ');
    blocks.push({ type: 'paragraph', text, anchor: nextAnchor(text.slice(0, 36)) });
  }
  return { blocks, headings };
}

function mathHtml(tex: string, displayMode: boolean) {
  try { return katex.renderToString(tex, { displayMode, throwOnError: false, strict: 'ignore', output: 'htmlAndMathml' }); }
  catch { return ''; }
}

function Formula({ tex }: { tex: string }) {
  const html = mathHtml(tex, true);
  return <div className={styles.formula} role="math" aria-label={`TeX 公式：${tex}`}>
    {html ? <span dangerouslySetInnerHTML={{ __html: html }} /> : <code>{tex}</code>}
  </div>;
}

function renderInline(text: string) {
  const parts = text.split(/(`[^`]+`|\*\*[^*]+\*\*|\$[^$]+\$)/g).filter(Boolean);
  return parts.map((part, index) => {
    if (/^`[^`]+`$/.test(part)) return <code key={index}>{part.slice(1, -1)}</code>;
    if (/^\*\*[^*]+\*\*$/.test(part)) return <strong key={index}>{part.slice(2, -2)}</strong>;
    if (/^\$[^$]+\$$/.test(part)) {
      const tex = part.slice(1, -1);
      const html = mathHtml(tex, false);
      return html ? <span className={styles.inlineFormula} key={index} role="math" aria-label={`TeX 公式：${tex}`} dangerouslySetInnerHTML={{ __html: html }} /> : <code key={index}>{tex}</code>;
    }
    return <span key={index}>{part}</span>;
  });
}

export function DocumentRenderer({
  artifact,
  onSelection,
}: {
  artifact: ArtifactPublicProjection;
  onSelection?: (selection: ArtifactSelectionReference) => void;
}) {
  const rootRef = useRef<HTMLElement>(null);
  const content = artifact.render_payload.content || '';
  const parsed = useMemo(() => parseDocumentBlocks(content), [content]);

  function captureSelection() {
    if (!onSelection || !rootRef.current || !window.getSelection) return;
    const selection = window.getSelection();
    if (!selection || selection.isCollapsed || !selection.rangeCount) return;
    const range = selection.getRangeAt(0);
    const common = range.commonAncestorContainer.nodeType === Node.ELEMENT_NODE
      ? range.commonAncestorContainer as Element
      : range.commonAncestorContainer.parentElement;
    if (!common || !rootRef.current.contains(common)) return;
    const selectedText = selection.toString().split(String.fromCharCode(0)).join('').trim();
    if (!selectedText) return;
    const section = common.closest<HTMLElement>('[data-section-anchor]');
    const anchor = section?.dataset.sectionAnchor || null;
    let rangeLabel = section?.dataset.sectionLabel || '讲义选中段落';
    const code = common.closest('pre');
    if (code) {
      try {
        const prefix = document.createRange();
        prefix.selectNodeContents(code);
        prefix.setEnd(range.startContainer, range.startOffset);
        const startLine = prefix.toString().split('\n').length;
        const lineCount = selectedText.split('\n').length;
        rangeLabel = `代码第 ${startLine}${lineCount > 1 ? `—${startLine + lineCount - 1}` : ''} 行`;
      } catch { rangeLabel = '代码选中片段'; }
    }
    onSelection({
      artifact_id: artifact.artifact_id,
      artifact_version_id: artifact.version_id,
      artifact_version_number: artifact.version_number,
      section_anchor: anchor,
      range_label: rangeLabel,
      summary: selectedText.replace(/\s+/g, ' ').slice(0, 160),
      selected_text: selectedText.slice(0, 1200),
      content_checksum: checksum(content),
      purpose: '解释选中内容',
    });
  }

  return <article ref={rootRef} className={styles.document} onMouseUp={captureSelection} onKeyUp={captureSelection}>
    {parsed.blocks.map((block, index) => {
      const common = { id: block.anchor, 'data-section-anchor': block.anchor, 'data-section-label': block.type === 'heading' ? block.text : `第 ${index + 1} 段` };
      if (block.type === 'heading') {
        const Tag = `h${Math.min(block.level + 1, 4)}` as keyof JSX.IntrinsicElements;
        return <Tag key={block.anchor} {...common}>{renderInline(block.text)}</Tag>;
      }
      if (block.type === 'paragraph') return <p key={block.anchor} {...common}>{renderInline(block.text)}</p>;
      if (block.type === 'unordered') return <ul key={block.anchor} {...common}>{block.items.map((item, i) => <li key={i}>{renderInline(item)}</li>)}</ul>;
      if (block.type === 'ordered') return <ol key={block.anchor} {...common}>{block.items.map((item, i) => <li key={i}>{renderInline(item)}</li>)}</ol>;
      if (block.type === 'quote') return <blockquote key={block.anchor} {...common}>{renderInline(block.text)}</blockquote>;
      if (block.type === 'callout') return <aside key={block.anchor} {...common} className={`${styles.callout} ${styles[block.tone]}`}><strong>{block.title}</strong><p>{renderInline(block.text)}</p></aside>;
      if (block.type === 'code') return <section key={block.anchor} {...common} className={styles.codeBlock}><div>{block.language}</div><pre><code>{block.text}</code></pre></section>;
      if (block.type === 'table') return <div key={block.anchor} {...common} className={styles.tableWrap}><table><thead><tr>{block.headers.map((header, i) => <th key={i}>{renderInline(header)}</th>)}</tr></thead><tbody>{block.rows.map((row, r) => <tr key={r}>{block.headers.map((_, c) => <td key={c}>{renderInline(row[c] || '')}</td>)}</tr>)}</tbody></table></div>;
      if (block.type === 'formula') return <section key={block.anchor} {...common}><Formula tex={block.tex} /></section>;
      if (block.type === 'rule') return <hr key={block.anchor} {...common} />;
      return null;
    })}
  </article>;
}

export function DocumentTableOfContents({
  content,
  onNavigate,
}: {
  content: string;
  onNavigate?: (anchor: string) => void;
}) {
  const headings = useMemo(() => parseDocumentBlocks(content).headings, [content]);
  if (!headings.length) return <p className={styles.emptyToc}>这份讲义还没有章节目录。</p>;
  return <nav className={styles.toc} aria-label="讲义目录">
    <strong>目录</strong>
    {headings.map(item => <button key={item.id} type="button" data-level={item.level} onClick={() => onNavigate?.(item.id)}>{item.text}</button>)}
  </nav>;
}
