import { Plus, MessageCircleQuestion } from 'lucide-react';
import type { QaConversation } from '../../api/qa';
import styles from './ConversationHistory.module.css';

interface ConversationHistoryProps {
  conversations: QaConversation[];
  currentThreadId: string | null;
  loading: boolean;
  onSelect: (threadId: string) => void;
  onNew: () => void;
}

function relativeTime(iso: string): string {
  if (!iso) return '';
  const target = new Date(iso).getTime();
  if (Number.isNaN(target)) return iso;
  const diff = Date.now() - target;
  if (diff < 60_000) return '刚刚';
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前`;
  if (diff < 7 * 86_400_000) return `${Math.floor(diff / 86_400_000)} 天前`;
  return new Date(iso).toLocaleDateString('zh-CN');
}

function previewOf(conv: QaConversation): string {
  const firstUser = conv.messages?.find((message) => message.role === 'user');
  const firstAny = conv.messages?.[0];
  const text = firstUser?.content ?? firstAny?.content ?? '';
  if (!text) return `会话 ${conv.thread_id.slice(-4)}`;
  return text.length > 36 ? `${text.slice(0, 36)}…` : text;
}

export function ConversationHistory({
  conversations,
  currentThreadId,
  loading,
  onSelect,
  onNew,
}: ConversationHistoryProps) {
  return (
    <aside className={styles.panel} data-testid="qa-history-panel">
      <div className={styles.header}>
        <strong>历史会话</strong>
        <button
          type="button"
          className={styles.newButton}
          onClick={onNew}
          data-testid="qa-new-conversation"
        >
          <Plus size={16} strokeWidth={2.2} />
          <span>新建对话</span>
        </button>
      </div>

      {loading ? (
        <div className={styles.skeleton} data-testid="qa-history-loading">
          <span /><span /><span />
        </div>
      ) : conversations.length === 0 ? (
        <div className={styles.empty} data-testid="qa-history-empty">
          <MessageCircleQuestion size={28} strokeWidth={1.6} />
          <p>暂无历史对话</p>
          <small>提出第一个问题，开启你的学习答疑</small>
        </div>
      ) : (
        <ul className={styles.list} data-testid="qa-history-list">
          {conversations.map((conv) => {
            const active = conv.thread_id === currentThreadId;
            return (
              <li key={conv.id}>
                <button
                  type="button"
                  className={active ? styles.itemActive : styles.item}
                  onClick={() => onSelect(conv.thread_id)}
                  data-testid={`qa-history-item-${conv.thread_id}`}
                >
                  <span className={styles.preview}>{previewOf(conv)}</span>
                  <small className={styles.meta}>
                    <span>{conv.message_count} 条</span>
                    <span>{relativeTime(conv.last_message_at)}</span>
                  </small>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </aside>
  );
}
