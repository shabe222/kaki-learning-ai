import type { ArtifactPublicProjection, ArtifactSelectionReference, CanonicalArtifactBlockV1 } from '../../api/artifacts';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import 'katex/dist/katex.min.css';
import { cjkFriendlyStrongEmphasis, normalizeLatexDelimiters } from '../../utils/cjkEmphasis';
import styles from './CanonicalBlockRenderer.module.css';

interface Props {
  artifact: ArtifactPublicProjection;
  onSelection?: (selection: ArtifactSelectionReference) => void;
}

function visibleText(block: CanonicalArtifactBlockV1) {
  return String(block.data.text || block.data.title || block.data.label || block.data.code || '');
}

// Selection ranges use this visible-text coordinate system, never Markdown source offsets.
// Keep it aligned with services.workspace_references._canonical_visible_text.
function canonicalVisibleText(markdown: string) {
  return markdown
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/^(?:#{1,6}\s+|>\s?|[-+*]\s+|\d+\.\s+)/gm, '')
    .replace(/(\*\*|__|~~|\*)/g, '')
    .replace(/\s+/g, '');
}

function MarkdownText({ text }: { text: string }) {
  const normalized = normalizeLatexDelimiters(cjkFriendlyStrongEmphasis(text));
  return <ReactMarkdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex]}>{normalized}</ReactMarkdown>;
}

export function CanonicalBlockRenderer({ artifact, onSelection }: Props) {
  const canonical = artifact.canonical;
  if (!canonical) return null;
  const versionDigest = canonical.version.content_digest;
  const artifactPurpose = canonical.purpose;

  function capture(block: CanonicalArtifactBlockV1) {
    if (!onSelection) return;
    const selection = window.getSelection();
    const selectedText = String(selection?.toString() || '').replace(/\u0000/g, '').trim();
    if (!selectedText) return;
    const text = canonicalVisibleText(visibleText(block));
    const selectedVisibleText = canonicalVisibleText(selectedText);
    const start = text.indexOf(selectedVisibleText);
    if (start < 0) return;
    onSelection({
      artifact_id: artifact.artifact_id,
      artifact_version_id: artifact.version_id,
      artifact_version_number: artifact.version_number,
      section_anchor: block.block_id,
      range_label: block.type === 'HEADING' ? selectedText.slice(0, 80) : '选中区块',
      summary: selectedText.replace(/\s+/g, ' ').slice(0, 160),
      selected_text: selectedText.slice(0, 1200),
      content_checksum: versionDigest,
      version_digest: versionDigest,
      block_id: block.block_id,
      text_range: { start, end: start + selectedVisibleText.length },
      knowledge_refs: block.knowledge_refs,
      source_refs: block.source_refs,
      artifact_purpose: artifactPurpose,
      purpose: '解释选中内容',
    });
  }

  function render(block: CanonicalArtifactBlockV1) {
    const text = visibleText(block);
    const common = {
      id: block.block_id,
      'data-section-anchor': block.block_id,
      'data-canonical-block-id': block.block_id,
      onMouseUp: () => capture(block),
    };
    if (block.type === 'HEADING') {
      const level = Math.min(3, Math.max(1, Number(block.data.level || 2)));
      if (level === 1) return <h1 key={block.block_id} {...common}>{text}</h1>;
      if (level === 2) return <h2 key={block.block_id} {...common}>{text}</h2>;
      return <h3 key={block.block_id} {...common}>{text}</h3>;
    }
    if (block.type === 'CALLOUT') return <aside key={block.block_id} {...common} className={styles.callout}><MarkdownText text={text} /></aside>;
    if (block.type === 'EXAMPLE') return <section key={block.block_id} {...common} className={styles.example}><strong>例子</strong><MarkdownText text={text} /></section>;
    if (block.type === 'CONCEPT') return <section key={block.block_id} {...common} className={styles.concept}><strong>关键概念</strong><MarkdownText text={text} /></section>;
    if (block.type === 'CHECKPOINT') return <section key={block.block_id} {...common} className={styles.checkpoint}><strong>检查点</strong><MarkdownText text={text} /></section>;
    if (block.type === 'TASK') return <section key={block.block_id} {...common} className={styles.task}><strong>动手做</strong><MarkdownText text={text} /></section>;
    if (block.type === 'SOURCE') return <aside key={block.block_id} {...common} className={styles.source}><strong>来源</strong><MarkdownText text={text} /></aside>;
    return <div key={block.block_id} {...common} className={styles.text}><MarkdownText text={text} /></div>;
  }

  return <article className={styles.document} data-testid="canonical-artifact-renderer" data-contract={canonical.contract}>
    {canonical.blocks.map(render)}
  </article>;
}

export default CanonicalBlockRenderer;
