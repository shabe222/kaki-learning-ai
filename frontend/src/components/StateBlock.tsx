import { Button, Spin } from 'antd';
import styles from './StateBlock.module.css';

interface StateBlockProps {
  state: 'loading' | 'empty' | 'error';
  title?: string;
  description?: string;
  onRetry?: () => void;
}

export function StateBlock({ state, title, description, onRetry }: StateBlockProps) {
  if (state === 'loading') {
    return (
      <div className={styles.block} data-testid="state-loading">
        <Spin />
        <span>{title || '正在加载数据'}</span>
      </div>
    );
  }

  if (state === 'error') {
    return (
      <div className={styles.block} data-testid="state-error">
        <strong>{title || '数据加载失败'}</strong>
        <span>{description || '请稍后重试，或检查接口服务是否可用。'}</span>
        {onRetry && <Button onClick={onRetry}>重试</Button>}
      </div>
    );
  }

  return (
    <div className={styles.block} data-testid="state-empty">
      <span>{description || title || '暂无数据'}</span>
    </div>
  );
}
