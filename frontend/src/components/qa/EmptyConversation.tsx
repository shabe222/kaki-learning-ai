import { Sparkles, BookOpen, ClipboardCheck, FileText, PlaySquare, Layers } from 'lucide-react';
import type { LearningChapter } from '../../api/modules';
import styles from './EmptyConversation.module.css';

interface EmptyConversationProps {
  hasContext: boolean;
  activeChapter?: LearningChapter | null;
  totalChapters?: number;
  completedChapters?: number;
}

const quickPrompts = [
  { key: 'concept', label: '讲解一个概念', icon: BookOpen, text: '请用一段简洁的话解释一下当前章节的核心概念，并给一个生活化的例子。' },
  { key: 'practice', label: '出一题练练', icon: ClipboardCheck, text: '请围绕当前章节出一个简答题，给我思考的方向与参考答案。' },
  { key: 'summary', label: '整理本章要点', icon: FileText, text: '请把当前章节的要点整理成 5 条结构化笔记，便于我快速回顾。' },
  { key: 'video', label: '找一段讲解视频', icon: PlaySquare, text: '请帮我找一段讲解当前章节的入门视频，并说明适合什么水平观看。' },
  { key: 'path', label: '调整学习计划', icon: Layers, text: '请根据我对当前章节的掌握情况，给出接下来 3 天的学习计划。' }
];

interface QuickPromptsProps {
  onPick: (text: string) => void;
}

export function QuickPrompts({ onPick }: QuickPromptsProps) {
  return (
    <div className={styles.quickPrompts} data-testid="qa-quick-prompts">
      {quickPrompts.map((prompt) => {
        const Icon = prompt.icon;
        return (
          <button
            key={prompt.key}
            type="button"
            className={styles.quickButton}
            onClick={() => onPick(prompt.text)}
            data-testid={`qa-quick-prompt-${prompt.key}`}
          >
            <Icon size={16} strokeWidth={1.8} />
            <span>{prompt.label}</span>
          </button>
        );
      })}
    </div>
  );
}

export function EmptyConversation({ hasContext, activeChapter, totalChapters, completedChapters }: EmptyConversationProps) {
  return (
    <div className={styles.empty} data-testid="qa-empty-conversation">
      <div className={styles.glyph}>
        <Sparkles size={32} strokeWidth={1.6} />
      </div>
      <h2>开始今天的第一次提问</h2>
      <p>把你的问题写下来，我会结合你的学习画像与当前章节给出一段克制、可执行的回答。</p>
      {hasContext && activeChapter ? (
        <div className={styles.contextHint} data-testid="qa-empty-context">
          <span>当前章节：{activeChapter.name ?? activeChapter.id}</span>
          {typeof activeChapter.progress_pct === 'number' && (
            <span>完成度 {Math.round(Number(activeChapter.progress_pct))}%</span>
          )}
          {typeof totalChapters === 'number' && totalChapters > 0 && (
            <span>已学 {completedChapters ?? 0} / {totalChapters} 章</span>
          )}
        </div>
      ) : (
        <div className={styles.contextHint} data-testid="qa-empty-no-context">
          <span>尚未读取到当前学习上下文，先从任意问题开始。</span>
        </div>
      )}
    </div>
  );
}
