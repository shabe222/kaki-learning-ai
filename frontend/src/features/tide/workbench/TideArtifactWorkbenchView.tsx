import { useEffect, useMemo, useState } from 'react';
import { ChevronLeft, ChevronRight, FileText, GitCompareArrows } from 'lucide-react';
import type { TideArtifactAction, TideArtifactWorkbench as Workbench } from '../tideTypes';
import styles from './TideArtifactWorkbench.module.css';

interface TideArtifactWorkbenchProps {
  workbench: Workbench;
  action: TideArtifactAction | null;
}

function readable(value: string) {
  return value || '未设置';
}

export function TideArtifactWorkbench({ workbench, action }: TideArtifactWorkbenchProps) {
  const [pageNumber, setPageNumber] = useState(workbench.focusedPageNumber);
  const [view, setView] = useState<'current' | 'compare'>('current');
  const slideCount = workbench.slides.length;
  const slide = workbench.slides[pageNumber - 1];
  const sourceSlide = workbench.sourceSlides[pageNumber - 1];
  const pageComparison = useMemo(() => (
    pageNumber === workbench.focusedPageNumber ? workbench.comparison : []
  ), [pageNumber, workbench.comparison, workbench.focusedPageNumber]);
  const canCompare = Boolean(
    workbench.sourceVersionId
    && workbench.sourceVersionId !== workbench.currentVersionId
    && sourceSlide,
  );

  useEffect(() => {
    setPageNumber(workbench.focusedPageNumber);
    setView('current');
  }, [workbench.currentVersionId, workbench.focusedPageNumber, action?.id]);

  if (!slide) {
    return (
      <section className={styles.workbench} data-testid="tide-artifact-workbench">
        <p className={styles.empty}>这个演示文稿还没有可显示的页面。</p>
      </section>
    );
  }

  return (
    <section
      className={styles.workbench}
      aria-label="DARYA 演示文稿工作台"
      data-testid="tide-artifact-workbench"
      data-artifact-id={workbench.artifactId}
      data-version-number={workbench.currentVersionNumber}
    >
      <div className={styles.metaLine}>
        <span><FileText size={13} aria-hidden="true" /> PRESENTATION</span>
        <strong>v{workbench.currentVersionNumber}</strong>
      </div>
      <div className={styles.versionLine}>
        {canCompare
          ? <>源 v{workbench.sourceVersionNumber} <span aria-hidden="true">→</span> 当前 v{workbench.currentVersionNumber}</>
          : <>当前版本 v{workbench.currentVersionNumber}</>}
      </div>

      <div className={styles.pageBar}>
        <button
          type="button"
          aria-label="上一页"
          disabled={pageNumber <= 1}
          onClick={() => setPageNumber((value) => Math.max(1, value - 1))}
        >
          <ChevronLeft size={15} aria-hidden="true" />
        </button>
        <span>第 {pageNumber} / {slideCount} 页</span>
        <button
          type="button"
          aria-label="下一页"
          disabled={pageNumber >= slideCount}
          onClick={() => setPageNumber((value) => Math.min(slideCount, value + 1))}
        >
          <ChevronRight size={15} aria-hidden="true" />
        </button>
      </div>

      {canCompare && (
        <div className={styles.viewSwitch} aria-label="产物查看方式">
          <button type="button" aria-pressed={view === 'current'} onClick={() => setView('current')}>当前页</button>
          <button type="button" aria-pressed={view === 'compare'} onClick={() => setView('compare')}>
            <GitCompareArrows size={12} aria-hidden="true" /> 前后对照
          </button>
        </div>
      )}

      {view === 'current' || !canCompare ? (
        <article className={styles.slide} data-testid="tide-workbench-slide">
          <div className={styles.slideNumber}>{String(pageNumber).padStart(2, '0')}</div>
          <h3>{slide.title || `第 ${pageNumber} 页`}</h3>
          {slide.subtitle && <p className={styles.subtitle}>{slide.subtitle}</p>}
          {slide.body && <p className={styles.body}>{slide.body}</p>}
          {slide.items.length > 0 && (
            <ul>{slide.items.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}</ul>
          )}
          {slide.code && <pre><code data-language={slide.language}>{slide.code}</code></pre>}
          {slide.layout && <p className={styles.supporting}><span>布局</span>{slide.layout}</p>}
          {slide.visualReferences.length > 0 && (
            <div className={styles.references}>
              <span>视觉参考</span>
              <ul>{slide.visualReferences.map((reference) => <li key={reference}>{reference}</li>)}</ul>
            </div>
          )}
          {slide.narration && (
            <details className={styles.notes}>
              <summary>讲述备注</summary>
              <p>{slide.narration}</p>
            </details>
          )}
        </article>
      ) : (
        <div className={styles.comparison} data-testid="tide-workbench-comparison">
          <div className={styles.compareHeading}>
            <span>第 {pageNumber} 页</span>
            <strong>只看有意义的变化</strong>
          </div>
          {pageComparison.length > 0 ? pageComparison.map((change) => (
            <section key={change.field} className={styles.change}>
              <h4>{change.label}</h4>
              <div>
                <span>之前 · v{workbench.sourceVersionNumber}</span>
                <p>{readable(change.before)}</p>
              </div>
              <div data-current="true">
                <span>现在 · v{workbench.currentVersionNumber}</span>
                <p>{readable(change.after)}</p>
              </div>
            </section>
          )) : (
            <p className={styles.empty}>这一页没有可显示的内容变化。</p>
          )}
        </div>
      )}

      {action?.proposal.scope && (
        <div className={styles.focus}>
          <span>本轮焦点</span>
          <p>{action.proposal.scope} · {action.proposal.instruction || action.reason}</p>
        </div>
      )}
    </section>
  );
}
