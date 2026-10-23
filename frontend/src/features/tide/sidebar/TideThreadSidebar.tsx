/**
 * 左侧栏：这条对话的消息流。
 *
 * 两种看法，随时切：
 *  「列表」—— 一条消息一行，角色加一句摘要，整条线程一眼扫完。
 *  「对话」—— 按手机聊天的样子等比缩小：气泡、头像、你右我左，读起来就是聊天记录本身。
 *
 * 品牌也在这里：顶栏那一行本来挤着品牌加六个入口，把品牌挪出来之后，
 * 顶栏只剩跟"现在这件事"有关的东西。
 */

import { useEffect, useMemo, useState } from 'react';
import { ChevronsLeft, ChevronsRight, History, Plus, Sparkles } from 'lucide-react';
import { useTideStore } from '../state/tideStore';
import { projectTideMoment } from '../content/tideContentProjection';
import styles from './TideThreadSidebar.module.css';

/** 一行摘要的长度。再多就谈不上"一眼扫完"。 */
const PEEK_LENGTH = 34;

/** 一行摘要：压掉标记和换行，只留一句话的开头。 */
function peek(raw: string): string {
  const text = plainText(raw).replace(/\s+/g, ' ').trim();
  return text.length > PEEK_LENGTH ? `${text.slice(0, PEEK_LENGTH)}…` : text;
}

/** 气泡里的文字：留住换行和语气，压掉 markdown 的记号。 */
function bubbleText(raw: string): string {
  return plainText(raw).replace(/\n{3,}/g, '\n\n').trim();
}

function plainText(raw: string): string {
  return raw
    .replace(/\[blackboard\][\s\S]*?\[\/blackboard\]/gi, '\n（要点写在黑板上）\n')
    .replace(/```[^\n]*\n?/g, '')
    // 模型偶尔会自己带契约标签（[tide_center] 这类）。只认"字母/下划线开头、后面跟着换行"的，
    // 行内的方括号（比如 nums = [2, 1, 3]、nums[i]）不会被碰到。
    .replace(/\[\/?[a-z_][\w-]{1,31}\](?=\s*(?:\n|$))/gim, '')
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/^#{1,6}\s+/gm, '')
    .replace(/^\s*[-*]\s+/gm, '· ')
    .replace(/[`*_~]+/g, '')
    .replace(/^>\s?/gm, '');
}

type SidebarMode = 'rows' | 'chat';

const MODE_COPY: Record<SidebarMode, string> = {
  rows: '一条消息一行',
  chat: '手机聊天的样子，等比缩小',
};

/** 压缩态记在本机：这是这台机器/这块屏幕的习惯，不跟着账号走。 */
const SIDEBAR_COLLAPSED_KEY = 'darya_sidebar_collapsed_v1';

export function TideThreadSidebar() {
  const messages = useTideStore((state) => state.messages);
  const loadState = useTideStore((state) => state.loadState);
  const streamState = useTideStore((state) => state.streamState);
  const createThread = useTideStore((state) => state.createThread);
  const setHistoryOpen = useTideStore((state) => state.setRailOpen);
  const [mode, setMode] = useState<SidebarMode>('rows');
  // 压缩态：整条侧栏收成一根细栏，每条消息变成一个小短条。记在本机，下次进来还是上次的样子。
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === '1');
  useEffect(() => {
    localStorage.setItem(SIDEBAR_COLLAPSED_KEY, collapsed ? '1' : '0');
  }, [collapsed]);

  // 当前这一轮是哪两条：它们高亮，其余是"已经讲过的"。
  const moment = useMemo(() => projectTideMoment(messages), [messages]);
  const visible = useMemo(() => messages.filter((message) => message.role !== 'system'), [messages]);

  const busy = loadState === 'loading' || streamState === 'connecting' || streamState === 'streaming';

  if (collapsed) {
    return (
      <aside className={styles.rail} data-testid="tide-thread-sidebar" aria-label="这条对话（已收起）">
        <button
          type="button"
          className={styles.railBtn}
          onClick={() => setCollapsed(false)}
          title="展开侧栏"
          data-testid="tide-sidebar-expand"
        >
          <ChevronsRight size={15} aria-hidden="true" />
        </button>
        <button
          type="button"
          className={styles.railBtn}
          onClick={() => void createThread()}
          disabled={busy}
          title="新的对话"
          data-testid="tide-sidebar-new-thread"
        >
          <Plus size={15} aria-hidden="true" />
        </button>
        <div className={styles.railList} data-testid="tide-sidebar-rail-messages">
          {visible.map((message) => {
            const isNow = message.id === moment.currentUser?.id || message.id === moment.currentAssistant?.id;
            return (
              <span
                key={message.id}
                className={styles.railBar}
                data-now={isNow ? 'true' : 'false'}
                data-role={message.role}
                title={peek(message.content)}
                aria-label={peek(message.content)}
                data-testid="tide-sidebar-rail-message"
              />
            );
          })}
        </div>
        <button
          type="button"
          className={styles.railBtn}
          onClick={() => setHistoryOpen(true)}
          title="历史对话"
          data-testid="tide-sidebar-history"
        >
          <History size={15} aria-hidden="true" />
        </button>
      </aside>
    );
  }

  return (
    <aside className={styles.sidebar} data-testid="tide-thread-sidebar" aria-label="这条对话">
      <div className={styles.brand}>
        <img src="/assets/brand/kaki-logo-mark.png" alt="" aria-hidden="true" className={styles.logo} draggable={false} />
        <div>
          <span>柿子智学 · 现在</span>
          <strong>DARYA</strong>
        </div>
        <button
          type="button"
          className={styles.collapseBtn}
          onClick={() => setCollapsed(true)}
          title="收起侧栏"
          data-testid="tide-sidebar-collapse"
        >
          <ChevronsLeft size={15} aria-hidden="true" />
        </button>
      </div>

      <button
        type="button"
        className={styles.newThread}
        disabled={busy}
        onClick={() => void createThread()}
        data-testid="tide-sidebar-new-thread"
      >
        <Plus size={14} aria-hidden="true" />新的对话
      </button>

      <div className={styles.sectionHead}>
        <span>这条对话</span>
        <div className={styles.modeTabs} role="tablist" aria-label="侧栏显示方式">
          {(Object.keys(MODE_COPY) as SidebarMode[]).map((key) => (
            <button
              key={key}
              type="button"
              role="tab"
              aria-selected={mode === key}
              title={MODE_COPY[key]}
              className={styles.modeTab}
              data-active={mode === key ? 'true' : 'false'}
              onClick={() => setMode(key)}
              data-testid={`tide-sidebar-mode-${key}`}
            >
              {key === 'rows' ? '列表' : '对话'}
            </button>
          ))}
        </div>
      </div>

      {mode === 'rows' ? (
        <ol className={styles.list} data-testid="tide-sidebar-messages">
          {visible.length === 0 ? (
            <li className={styles.empty}>还没有消息。第一句问出去，这里就会一行一行长出来。</li>
          ) : (
            visible.map((message) => {
              const isUser = message.role === 'user';
              const isNow = message.id === moment.currentUser?.id || message.id === moment.currentAssistant?.id;
              const summary = peek(message.content) || (isUser ? '（这条没留下文字）' : '（这一轮以黑板/资料呈现）');
              return (
                <li key={message.id}>
                  <div
                    className={styles.row}
                    data-role={message.role}
                    data-now={isNow ? 'true' : 'false'}
                    data-testid="tide-sidebar-message"
                  >
                    <span className={styles.role}>{isUser ? '我说' : 'DARYA'}</span>
                    <span className={styles.peek}>{summary}</span>
                  </div>
                </li>
              );
            })
          )}
        </ol>
      ) : (
        <ol className={styles.chat} data-testid="tide-sidebar-chat">
          {visible.length === 0 ? (
            <li className={styles.empty}>还没有消息。第一句问出去，气泡就会从这里长出来。</li>
          ) : (
            visible.map((message) => {
              const isUser = message.role === 'user';
              const isNow = message.id === moment.currentUser?.id || message.id === moment.currentAssistant?.id;
              const text = bubbleText(message.content) || '（这一轮以黑板/资料呈现）';
              return (
                <li key={message.id}>
                  <div
                    className={styles.chatRow}
                    data-role={message.role}
                    data-now={isNow ? 'true' : 'false'}
                    data-testid="tide-sidebar-bubble"
                  >
                    <span className={styles.chatAvatar} aria-hidden="true">{isUser ? '我' : 'D'}</span>
                    <span className={styles.bubble}>{text}</span>
                  </div>
                </li>
              );
            })
          )}
        </ol>
      )}

      <div className={styles.footer}>
        <button
          type="button"
          className={styles.footerButton}
          onClick={() => setHistoryOpen(true)}
          data-testid="tide-sidebar-history"
        >
          <History size={14} aria-hidden="true" />历史对话
        </button>
        <p className={styles.hint}>
          <Sparkles size={12} aria-hidden="true" />
          这里只展示这条对话，回答在右边
        </p>
      </div>
    </aside>
  );
}
