import styles from './PageHeader.module.css';

interface PageHeaderProps {
  title: string;
  subtitle: string;
}

export function PageHeader({ title, subtitle }: PageHeaderProps) {
  return (
    <section className={styles.header}>
      <div>
        <h1>
          {title}
          <span className={styles.sparkle} aria-hidden="true">✦</span>
        </h1>
        <p>{subtitle}</p>
      </div>
    </section>
  );
}
