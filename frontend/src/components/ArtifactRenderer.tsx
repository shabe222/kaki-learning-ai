import { useMemo, useState } from 'react';
// audio lesson renderer accepts both registry kind (audio) and metadata kind (audio_lesson)
import { Button, Empty, Tag, message } from 'antd';
import { Download, FileCode2, GitBranch, Headphones, Presentation, ScrollText } from 'lucide-react';
import { http } from '../api/client';
import { reviewFlashcard } from '../api/study';
import { AuthenticatedVideo } from './AuthenticatedVideo';
import { SafeMarkdown } from './SafeMarkdown';
import styles from './ArtifactRenderer.module.css';

export interface UnifiedArtifact {
  artifact_id: number;
  kind: string;
  type: string;
  title: string;
  topic?: string;
  content?: string;
  raw_data?: Record<string, unknown>;
  status?: string;
  version?: number;
  version_id?: number;
  created_at?: string;
  export_formats?: string[];
}

function parseJson(value?: string): unknown {
  if (!value) return null;
  try { return JSON.parse(value); } catch { return null; }
}

function artifactData(artifact: UnifiedArtifact) {
  const parsed = parseJson(artifact.content);
  const parsedData = Array.isArray(parsed)
    ? { questions: parsed }
    : (typeof parsed === 'object' && parsed ? parsed : {});
  return { ...parsedData, ...(artifact.raw_data || {}) } as Record<string, any>;
}

function safeVideoUrl(value: unknown) {
  const raw = String(value || '').trim();
  if (!raw) return '';
  if (/^\/api\/chat\/tasks\/videos\/\d+\/media$/.test(raw)) return raw;
  try {
    const parsed = new URL(raw);
    return parsed.protocol === 'http:' || parsed.protocol === 'https:' ? parsed.toString() : '';
  } catch {
    return '';
  }
}

function safeStaticAudioUrl(value: unknown) {
  const raw = String(value || '').trim();
  return /^\/static\/audio\/[A-Za-z0-9._-]+\.mp3$/.test(raw) ? raw : '';
}

/** 绘本插图：服务端存下来的同源文件、远端链接、或旧版内联数据三种形态。 */
function safePictureBookImage(value: unknown) {
  const raw = String(value || '').trim();
  if (!raw) return '';
  if (raw.startsWith('data:image/')) return raw;
  if (raw.startsWith('https://')) return raw;
  return /^\/static\/picture_books\/[A-Za-z0-9._/-]+$/.test(raw) ? raw : '';
}

function TextDocument({ value }: { value: string }) {
  return <SafeMarkdown source={value} className={styles.document} testId="artifact-renderer" />;
}

function TreeNode({ node }: { node: any }) {
  if (!node) return null;
  return <li><span>{node.label || node.name || node.title || '未命名节点'}</span>{Array.isArray(node.children) && node.children.length ? <ul>{node.children.map((child: any, i: number) => <TreeNode key={child.id || i} node={child} />)}</ul> : null}</li>;
}

function QuestionSet({ questions }: { questions: any[] }) {
  return <div className={styles.questions}>{questions.map((q, i) => <section key={q.id || i}>
    <strong>{i + 1}. {q.content || q.question || q.title}</strong>
    {Array.isArray(q.options) && q.options.map((option: unknown, j: number) => <div className={styles.option} key={j}>{String(option)}</div>)}
    {(q.answer || q.correct_answer) && <details><summary>查看参考答案</summary><p>{String(q.answer || q.correct_answer)}</p>{q.explanation && <p>{String(q.explanation)}</p>}</details>}
  </section>)}</div>;
}

const RATING_LABELS: Array<{ value: number; label: string }> = [
  { value: 1, label: '忘了' },
  { value: 2, label: '费劲' },
  { value: 3, label: '记得' },
  { value: 4, label: '秒答' },
];

function FlashcardDeck({ artifactId, cards }: { artifactId: number; cards: any[] }) {
  const [revealed, setRevealed] = useState<Record<string, boolean>>({});
  const [reviewed, setReviewed] = useState<Record<string, string>>({});
  const [busyId, setBusyId] = useState<string | null>(null);

  async function grade(cardId: string, rating: number) {
    if (busyId) return;
    setBusyId(cardId);
    try {
      const result = await reviewFlashcard(artifactId, cardId, rating);
      const dueDate = new Date(result.due);
      setReviewed((prev) => ({ ...prev, [cardId]: Number.isNaN(dueDate.getTime()) || dueDate.getTime() <= Date.now()
        ? '稍后再来'
        : `下次复习 ${dueDate.toLocaleDateString()}` }));
    } catch {
      message.error('复习状态没有保存成功，稍后再试。');
    } finally {
      setBusyId(null);
    }
  }

  return <div className={styles.questions} data-testid="artifact-flashcards">
    {cards.map((card, i) => <section key={card.id || i}>
      <strong>{i + 1}. {String(card.front)}</strong>
      <span className={styles.option}>{card.kind === 'application' ? '应用' : card.kind === 'fact' ? '事实' : '概念'}</span>
      {revealed[card.id]
        ? <div>
            <p>{String(card.back)}</p>
            {card.source && <p><small>依据：{String(card.source)}</small></p>}
            {reviewed[card.id]
              ? <Tag color="green">{reviewed[card.id]}</Tag>
              : <Button.Group>{RATING_LABELS.map(({ value, label }) => (
                  <Button key={value} size="small" disabled={busyId === card.id} onClick={() => void grade(card.id, value)}>{label}</Button>
                ))}</Button.Group>}
          </div>
        : <Button size="small" onClick={() => setRevealed((prev) => ({ ...prev, [card.id]: true }))}>翻开背面</Button>}
    </section>)}
  </div>;
}

function MistakeBook({ mistakes }: { mistakes: any[] }) {
  return <div className={styles.questions} data-testid="artifact-mistake-book">
    {mistakes.map((mistake, i) => <section key={mistake.index ?? i}>
      <strong>错题 {i + 1} · {String(mistake.error_type || '待复盘')}</strong>
      <p>可能的误区：{String(mistake.misconception)}</p>
      <p>补救：{String(mistake.remediation)}</p>
      <details><summary>变式练习（{Array.isArray(mistake.variants) ? mistake.variants.length : 0} 题）</summary>
        {Array.isArray(mistake.variants) && mistake.variants.map((variant: any, j: number) => <div key={j} className={styles.option}>
          <strong>{variant.type === 'choice' ? '选择' : '简答'}：{String(variant.question)}</strong>
          {Array.isArray(variant.options) && variant.options.map((option: unknown, k: number) => <div key={k}>{String(option)}</div>)}
          <details><summary>查看答案</summary><p>{String(variant.answer)}</p>{variant.explanation && <p>{String(variant.explanation)}</p>}</details>
        </div>)}
      </details>
    </section>)}
  </div>;
}

function PictureBook({ book }: { book: Record<string, any> }) {
  const pages: any[] = Array.isArray(book.pages) ? book.pages : [];
  const quiz: any[] = Array.isArray(book.quiz) ? book.quiz : [];
  const cover = safePictureBookImage(book.cover_image);
  const status = (book.image_status || {}) as Record<string, any>;
  const uncaptured = Number(status.remote || 0) + Number(status.missing || 0);
  return <div className={styles.document} data-testid="artifact-picture-book">
    <div className={styles.pictureBookCover}>
      {cover
        ? <img src={cover} alt="" />
        : <div className={styles.pictureBookCoverFallback} aria-hidden="true" />}
      <div>
        <strong>{String(book.title || '教学绘本')}</strong>
        {book.summary ? <p>{String(book.summary)}</p> : null}
        <span>
          {[book.grade_text, book.style, pages.length ? `${pages.length} 页` : '']
            .filter(Boolean).map((part) => String(part)).join(' · ')}
        </span>
      </div>
    </div>
    {uncaptured > 0 && (
      // 存下来的是当时能取到的图；取不到的如实说明，不装作完整。
      <p className={styles.pictureBookNotice}>
        有 {uncaptured} 张插图没能随书保存，对应页面只显示文字。
      </p>
    )}
    <ol className={styles.pictureBookPages}>
      {pages.map((page, index) => {
        const image = safePictureBookImage(page.image_url);
        return <li key={page.index ?? index}>
          <details open={index === 0}>
            <summary>
              {`第 ${page.index ?? index + 1} 页 · ${String(page.title || '')}`}
              {page.stage_label ? `（${String(page.stage_label)}）` : ''}
            </summary>
            {image
              ? <img src={image} alt="" loading="lazy" />
              : <div className={styles.pictureBookPageFallback}>{page.image_note ? String(page.image_note) : '这一页没有插图'}</div>}
            {page.content ? <p>{String(page.content)}</p> : null}
            {page.fact ? <p className={styles.pictureBookFact}>小知识：{String(page.fact)}</p> : null}
          </details>
        </li>;
      })}
    </ol>
    {book.thinking_question ? <p className={styles.pictureBookFact}>想一想：{String(book.thinking_question)}</p> : null}
    {quiz.length > 0 && <section>
      <h2>读完想想看</h2>
      {quiz.map((item, index) => <div key={index}>
        <strong>{index + 1}. {String(item.question || '')}</strong>
        {Array.isArray(item.options) && item.options.map((option: unknown, optionIndex: number) => (
          <div className={styles.option} key={optionIndex}>{String(option)}</div>
        ))}
        {item.answer ? <details><summary>查看答案</summary>
          <p>{String(item.answer)}</p>
          {item.explanation ? <p>{String(item.explanation)}</p> : null}
        </details> : null}
      </div>)}
    </section>}
  </div>;
}

export function ArtifactRenderer({ artifact, compact = false }: { artifact: UnifiedArtifact; compact?: boolean }) {
  const data = useMemo(() => artifactData(artifact), [artifact]);
  const kind = String(artifact.kind || artifact.type || '').replace('workspace_', '');
  const slides = Array.isArray(data.slides) ? data.slides : [];
  const questions = Array.isArray(data.questions) ? data.questions : [];
  const tree = data.tree_data || data.mindmap || data.tree;
  const source = kind === 'code' ? String(data.source || artifact.content || '') : '';
  const documentText = [data.document, data.content, data.tutor_response, data.summary]
    .find((value) => typeof value === 'string' && value.trim());
  const videoUrl = kind === 'video' ? safeVideoUrl(data.video_url) : '';
  const transcript = kind === 'video' ? String(data.transcript || artifact.content || '').trim() : '';
  const flashcards = Array.isArray(data.cards) ? data.cards : [];
  const mistakes = Array.isArray(data.mistakes) ? data.mistakes : [];
  const audioUrl = kind === 'audio' || kind === 'audio_lesson' ? safeStaticAudioUrl(data.audio_url) : '';
  const lessonTranscript = kind === 'audio' || kind === 'audio_lesson' ? String(data.transcript || '').trim() : '';

  if (kind === 'flashcards' && flashcards.length) {
    return <div className={styles.document} data-testid="artifact-deck">
      <FlashcardDeck artifactId={artifact.artifact_id} cards={flashcards} />
    </div>;
  }
  if (kind === 'mistake_book' && mistakes.length) {
    return <div className={styles.document} data-testid="artifact-mistakes">
      <MistakeBook mistakes={mistakes} />
    </div>;
  }
  if (kind === 'picture_book' && Array.isArray(data.pages) && data.pages.length) {
    return <PictureBook book={data} />;
  }
  if (kind === 'audio' || kind === 'audio_lesson') {
    return <div className={styles.document} data-testid="artifact-audio">
      {audioUrl
        ? <audio controls src={audioUrl} style={{ width: '100%' }} />
        : <Empty description="音频文件不可用" image={Empty.PRESENTED_IMAGE_SIMPLE} />}
      {lessonTranscript ? <section><h2><Headphones size={16} /> 课程文稿</h2><TextDocument value={lessonTranscript} /></section> : null}
    </div>;
  }

  if (kind === 'video') return <div className={styles.document} data-testid="artifact-video">
    {videoUrl ? <AuthenticatedVideo src={videoUrl} title={artifact.title} />
      : <Empty description="视频媒体当前不可用；系统不会把非视频页面冒充成视频。" image={Empty.PRESENTED_IMAGE_SIMPLE} />}
    {transcript ? <section><h2>视频讲稿</h2><TextDocument value={transcript} /></section> : null}
  </div>;
  if (slides.length) {
    // 预览使用生成时的主题快照：App Shell 用当前主题，deck 保持它的生成版本主题。
    const deckTheme = String(data.presentation_theme || 'kaki') === 'ocean' ? 'ocean' : 'kaki';
    return <div className={`${styles.slides} ${styles[`deck_${deckTheme}`]}`} data-deck-theme={deckTheme}>{slides.map((slide: any, i: number) => {
      const design = slide.design && typeof slide.design === 'object' ? slide.design : {};
      const imagePosition = ['right', 'left', 'background', 'full_bleed', 'inline', 'none'].includes(String(design.image_position))
        ? String(design.image_position) : 'inline';
      const density = ['airy', 'balanced', 'dense'].includes(String(design.density)) ? String(design.density) : 'balanced';
      return <article key={slide.id || i} className={styles[`deckVisual_${imagePosition}`]} data-density={density}>
        <small>{i + 1} / {slides.length}</small>
        {slide.image && imagePosition !== 'none' && <img className={styles.deckImage} src={String(slide.image)} alt="" loading="lazy" />}
        <div className={styles.deckContent}>
          <h2>{slide.title || `第 ${i + 1} 页`}</h2>
          {slide.subtitle && <p className={styles.deckSubtitle}>{String(slide.subtitle)}</p>}
          {design.emphasis && <p className={styles.deckEmphasis}>{String(design.emphasis)}</p>}
          {slide.body && <p>{slide.body}</p>}{Array.isArray(slide.items) && <ul>{slide.items.map((x: unknown, j: number) => <li key={j}>{String(x)}</li>)}</ul>}
          {slide.code ? <pre className={styles.deckCode}>{String(slide.code)}</pre> : null}
        </div>
        {slide.narration && <aside>讲解提示：{slide.narration}</aside>}
      </article>;
    })}</div>;
  }
  if (tree) return <div className={styles.mindmap}><ul><TreeNode node={tree} /></ul></div>;
  if (source) return <div className={styles.code}><div><FileCode2 size={18} />{data.language || 'code'}</div><pre>{source}</pre>{data.last_run || data.run ? <pre className={styles.run}>stdout: {(data.last_run || data.run).stdout || '（空）'}\nstderr: {(data.last_run || data.run).stderr || '（空）'}\nexit code: {(data.last_run || data.run).exit_code ?? '—'}</pre> : null}</div>;
  if (questions.length) return <QuestionSet questions={questions} />;
  if (Array.isArray(data.nodes) && Array.isArray(data.edges)) return <div className={styles.graph}><GitBranch /><strong>知识网络</strong><p>{data.nodes.length} 个节点 · {data.edges.length} 条关系</p>{data.nodes.map((n: any, i: number) => <Tag key={n.id || i}>{n.label || n.name}</Tag>)}</div>;
  if (documentText) return <TextDocument value={String(documentText)} />;
  if (artifact.content) return <TextDocument value={artifact.content} />;
  return <Empty description="该产物没有可渲染内容" image={Empty.PRESENTED_IMAGE_SIMPLE} />;
}

export async function downloadUnifiedArtifact(artifact: UnifiedArtifact, format?: string) {
  const kind = String(artifact.kind || '').replace('workspace_', '');
  let url = `/api/items/${artifact.artifact_id}`;
  let extension = format || 'json';
  if (kind === 'lecture') { extension = format || 'pdf'; const versionQuery = artifact.version_id ? `?version_id=${encodeURIComponent(artifact.version_id)}` : artifact.version ? `?version=${encodeURIComponent(artifact.version)}` : ''; url = `/api/chat/tasks/artifacts/${artifact.artifact_id}/export/${extension}${versionQuery}`; }
  else if (kind === 'presentation' || kind === 'interactive_lesson') { extension = 'pptx'; url = `/api/chat/tasks/presentations/${artifact.artifact_id}/export/pptx`; }
  if (url.startsWith('/api/items/')) {
    const data = artifactData(artifact);
    const isMarkdownDocument = ['document', 'report', 'note', 'tutor'].includes(kind)
      && [data.document, data.content, data.tutor_response, data.summary].some((value) => typeof value === 'string' && value.trim());
    const text = kind === 'code'
      ? String(data.source || artifact.content || '')
      : isMarkdownDocument
        ? String([data.document, data.content, data.tutor_response, data.summary].find((value) => typeof value === 'string' && value.trim()))
        : JSON.stringify({ title: artifact.title, type: artifact.type, content: artifact.content, data }, null, 2);
    const mime = kind === 'code' ? 'text/plain;charset=utf-8' : isMarkdownDocument ? 'text/markdown;charset=utf-8' : 'application/json;charset=utf-8';
    const blob = new Blob([text], { type: mime });
    const objectUrl = URL.createObjectURL(blob); const link = document.createElement('a');
    const outputExtension = kind === 'code' ? (data.language === 'python' ? 'py' : 'txt') : isMarkdownDocument ? 'md' : 'json';
    link.href = objectUrl; link.download = `${artifact.title}.${outputExtension}`; link.click(); URL.revokeObjectURL(objectUrl); return;
  }
  const response = await http.get(url, { responseType: 'blob', timeout: 60000 });
  const objectUrl = URL.createObjectURL(response.data); const link = document.createElement('a');
  link.href = objectUrl; link.download = `${artifact.title}.${extension}`; link.click(); URL.revokeObjectURL(objectUrl);
}

export function ArtifactActions({ artifact }: { artifact: UnifiedArtifact }) {
  const kind = String(artifact.kind || '');
  return <div className={styles.actions}>
    <span>{kind.includes('presentation') ? <Presentation size={16} /> : <ScrollText size={16} />}{kind || '学习产物'}</span>
    <Button icon={<Download size={15} />} onClick={() => void downloadUnifiedArtifact(artifact)}>{kind === 'lecture' ? '导出 PDF' : kind.includes('presentation') || kind === 'interactive_lesson' ? '导出 PPT' : '导出'}</Button>
  </div>;
}
