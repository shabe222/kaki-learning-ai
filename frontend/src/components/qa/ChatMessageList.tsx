import { useEffect, useRef } from 'react';
import { Bot, CircleAlert, RefreshCcw, UserRound } from 'lucide-react';
import type { QaChatHistoryMessage } from '../../api/qa';
import styles from './ChatMessageList.module.css';

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  pending?: boolean;
  failed?: boolean;
}

interface ChatMessageListProps {
  messages: ChatMessage[];
  streamingPreview?: string | null;
  loading: boolean;
  errorText?: string | null;
  onRetryLast?: () => void;
}

function formatText(text: string): string {
  // Phase 1 不引入 Markdown 渲染，仅做轻量换行与空白整理
  return text.replace(/\r\n/g, '\n').trim();
}

export function ChatMessageList({ messages, streamingPreview, loading, errorText, onRetryLast }: ChatMessageListProps) {
  const bottomRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [messages.length, streamingPreview, loading, errorText]);

  if (messages.length === 0 && !loading && !errorText) {
    return (
      <div className={styles.empty} data-testid="qa-message-empty">
        <Bot size={36} strokeWidth={1.6} />
        <p>开始第一次提问</p>
        <small>我会基于你的学习画像与最近章节，给你一段克制、可执行的回答。</small>
      </div>
    );
  }

  return (
    <div className={styles.scroll} data-testid="qa-message-list">
      {messages.map((message) => (
        <div
          key={message.id}
          className={message.role === 'user' ? styles.userRow : styles.assistantRow}
          data-testid={`qa-message-${message.role}-${message.id}`}
        >
          <div className={styles.avatar}>
            {message.role === 'user' ? (
              <UserRound size={18} strokeWidth={1.8} />
            ) : (
              <Bot size={18} strokeWidth={1.8} />
            )}
          </div>
          <div className={styles.bubbleWrap}>
            <div
              className={
                message.role === 'user'
                  ? styles.userBubble
                  : message.failed
                    ? styles.failedBubble
                    : styles.assistantBubble
              }
            >
              <span className={styles.role}>
                {message.role === 'user' ? '我' : 'DARYA'}
              </span>
              <p className={styles.content}>{formatText(message.content)}</p>
              {message.pending && (
                <small className={styles.pendingHint} data-testid="qa-message-pending">对方正在思考…</small>
              )}
              {message.failed && onRetryLast && (
                <button
                  type="button"
                  className={styles.retryButton}
                  onClick={onRetryLast}
                  data-testid="qa-message-retry"
                >
                  <RefreshCcw size={14} strokeWidth={2} /> 重试
                </button>
              )}
            </div>
          </div>
        </div>
      ))}

      {loading && (
        <div className={styles.assistantRow} data-testid="qa-message-loading">
          <div className={styles.avatar}><Bot size={18} strokeWidth={1.8} /></div>
          <div className={styles.bubbleWrap}>
            <div className={styles.assistantBubble}>
              <span className={styles.role}>DARYA</span>
              <p className={styles.content}>
                {streamingPreview ? streamingPreview : '正在整理回答…'}
              </p>
            </div>
          </div>
        </div>
      )}

      {errorText && (
        <div className={styles.errorBlock} data-testid="qa-message-error">
          <CircleAlert size={18} strokeWidth={1.8} />
          <div>
            <strong>回答失败</strong>
            <p>{errorText}</p>
            {onRetryLast && (
              <button
                type="button"
                className={styles.retryButton}
                onClick={onRetryLast}
                data-testid="qa-error-retry"
              >
                <RefreshCcw size={14} strokeWidth={2} /> 重发
              </button>
            )}
          </div>
        </div>
      )}

      <div ref={bottomRef} />
    </div>
  );
}
