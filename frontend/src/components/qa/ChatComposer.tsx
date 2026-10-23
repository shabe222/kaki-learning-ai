import { useEffect, useRef, useState } from 'react';
import { SendHorizonal, Square, X } from 'lucide-react';
import styles from './ChatComposer.module.css';

interface ChatComposerProps {
  onSend: (text: string) => void;
  onCancel?: () => void;
  sending: boolean;
  disabled?: boolean;
  placeholder?: string;
}

const MAX_LEN = 800;

export function ChatComposer({ onSend, onCancel, sending, disabled, placeholder }: ChatComposerProps) {
  const [value, setValue] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = 'auto';
    textarea.style.height = `${Math.min(textarea.scrollHeight, 168)}px`;
  }, [value]);

  const submit = () => {
    const text = value.trim();
    if (!text || sending || disabled) return;
    onSend(text);
    setValue('');
  };

  const handleKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  };

  const clearText = () => {
    setValue('');
    textareaRef.current?.focus();
  };

  return (
    <div className={styles.composer} data-testid="qa-composer">
      <div className={styles.field}>
        <textarea
          ref={textareaRef}
          value={value}
          onChange={(event) => setValue(event.target.value.slice(0, MAX_LEN))}
          onKeyDown={handleKeyDown}
          placeholder={placeholder || '把学习问题写下来，回车发送，Shift+Enter 换行'}
          rows={1}
          disabled={disabled}
          data-testid="qa-composer-input"
        />
        {value && !sending && (
          <button
            type="button"
            className={styles.clearButton}
            onClick={clearText}
            aria-label="清空"
            data-testid="qa-composer-clear"
          >
            <X size={14} />
          </button>
        )}
      </div>
      <div className={styles.actions}>
        <span className={styles.counter} data-testid="qa-composer-counter">
          {value.length} / {MAX_LEN}
        </span>
        {sending ? (
          <button
            type="button"
            className={styles.cancelButton}
            onClick={onCancel}
            data-testid="qa-composer-cancel"
            title="当前为非流式接口，无法中断后端执行中的请求；此按钮仅结束前端等待"
          >
            <Square size={14} /> 取消等待
          </button>
        ) : (
          <button
            type="button"
            className={value.trim() ? styles.sendButton : styles.sendButtonDisabled}
            onClick={submit}
            disabled={!value.trim() || disabled}
            data-testid="qa-composer-send"
          >
            <SendHorizonal size={16} /> 发送
          </button>
        )}
      </div>
    </div>
  );
}
