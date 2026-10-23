import styles from './TideBottomNav.module.css';

const futureDestinations = [
  { label: '路线', position: 'route' },
  { label: '安全港', position: 'harbor' },
  { label: '世界', position: 'world' },
  { label: '学习空间', position: 'study' },
] as const;

export function TideBottomNav() {
  return (
    <nav className={styles.nav} aria-label="潮汐空间方位" data-testid="tide-bottom-nav">
      <svg className={styles.arc} viewBox="0 0 620 88" preserveAspectRatio="none" aria-hidden="true">
        <path d="M 16 78 C 108 36, 198 22, 268 20" />
        <path d="M 352 20 C 422 22, 512 36, 604 78" />
      </svg>
      <button type="button" className={styles.active} aria-current="page">
        <span className={styles.currentDot} aria-hidden="true" />
        <span>此刻</span>
      </button>
      {futureDestinations.map(({ label, position }) => (
        <button
          key={label}
          type="button"
          className={`${styles.future} ${styles[position]}`}
          disabled
          aria-label={`${label}，后续阶段开放`}
          title="后续阶段开放"
        >
          <span className={styles.futureDot} aria-hidden="true" />
          <span>{label}</span>
        </button>
      ))}
    </nav>
  );
}

