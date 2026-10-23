import { useEffect, useRef } from 'react';
import { ChevronRight, Plus, X } from 'lucide-react';
import { tideConversationTitle, useTideStore } from '../state/tideStore';
import styles from './TideHistoryDrawer.module.css';

function relativeTime(value: string) {
  const time = new Date(value).getTime();
  if (!Number.isFinite(time)) return '';
  const minutes = Math.max(0, Math.floor((Date.now() - time) / 60_000));
  if (minutes < 1) return '刚刚';
  if (minutes < 60) return `${minutes} 分钟前`;
  if (minutes < 1_440) return `${Math.floor(minutes / 60)} 小时前`;
  if (minutes < 10_080) return `${Math.floor(minutes / 1_440)} 天前`;
  return new Date(value).toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' });
}

export function TideHistoryDrawer() {
  const conversations = useTideStore((state) => state.conversations);
  const currentThreadId = useTideStore((state) => state.currentThreadId);
  const historyOpen = useTideStore((state) => state.railOpen);
  const loadState = useTideStore((state) => state.loadState);
  const selectThread = useTideStore((state) => state.selectThread);
  const createThread = useTideStore((state) => state.createThread);
  const setHistoryOpen = useTideStore((state) => state.setRailOpen);
  const closeRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    if (!historyOpen) return;
    closeRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setHistoryOpen(false);
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [historyOpen, setHistoryOpen]);

  if (!historyOpen) return null;

  return (
    <div className={styles.layer} data-testid="tide-history-drawer">
      <button type="button" className={styles.backdrop} aria-label="关闭历史记录" onClick={() => setHistoryOpen(false)} />
      <aside className={styles.drawer} role="dialog" aria-modal="true" aria-labelledby="tide-history-title">
        <header>
          <div>
            <span>历史对话</span>
            <h2 id="tide-history-title">对话历史</h2>
          </div>
          <button ref={closeRef} type="button" className={styles.close} onClick={() => setHistoryOpen(false)} aria-label="关闭历史记录">
            <X size={17} aria-hidden="true" />
          </button>
        </header>

        <button
          type="button"
          className={styles.newThread}
          disabled={loadState === 'loading'}
          onClick={async () => {
            await createThread();
            setHistoryOpen(false);
          }}
        >
          <Plus size={15} aria-hidden="true" />新的对话
        </button>

        {conversations.length === 0 ? (
          <p className={styles.empty}>还没有历史。第一个问题会留在真实对话服务中。</p>
        ) : (
          <ol className={styles.list}>
            {conversations.map((conversation) => {
              const active = conversation.thread_id === currentThreadId;
              return (
                <li key={conversation.thread_id}>
                  <button
                    type="button"
                    className={active ? styles.itemActive : styles.item}
                    aria-current={active ? 'true' : undefined}
                    onClick={async () => {
                      await selectThread(conversation.thread_id);
                      setHistoryOpen(false);
                    }}
                  >
                    <span>
                      <strong>{tideConversationTitle(conversation)}</strong>
                      <small>{conversation.message_count || conversation.messages?.length || 0} 条 · {relativeTime(conversation.last_message_at)}</small>
                    </span>
                    <ChevronRight size={14} aria-hidden="true" />
                  </button>
                </li>
              );
            })}
          </ol>
        )}
      </aside>
    </div>
  );
}

