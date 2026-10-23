import styles from './DaryaGameEntry.module.css';

interface Props {
  available?: boolean;
  onOpen?: () => void;
}

export function DaryaGameEntry({ available = true, onOpen }: Props) {
  return (
    <button
      type="button"
      className={styles.entry}
      disabled={!available}
      onClick={available ? onOpen : undefined}
      data-testid="darya-game-entry"
      aria-label="继续《风眼旧校》调查"
      title={available ? '继续《风眼旧校》调查' : '调查暂不可用'}
    >
      继续《风眼旧校》调查
    </button>
  );
}
