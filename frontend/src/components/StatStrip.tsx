import { Empty, Skeleton } from 'antd';
import { BookOpen, Clock3, Flame, FolderOpen, Star, TrendingUp } from 'lucide-react';
import type { StatCard } from '../types/api';
import styles from './StatStrip.module.css';

const icons = [Clock3, BookOpen, Flame, TrendingUp, FolderOpen, Star];

interface StatStripProps {
  stats?: StatCard[];
  loading?: boolean;
  count?: number;
  className?: string;
}

export function StatStrip({ stats, loading, count = 4, className }: StatStripProps) {
  if (loading) {
    return (
      <div className={[styles.strip, className].filter(Boolean).join(' ')}>
        {Array.from({ length: count }).map((_, index) => (
          <div className={styles.item} key={index}>
            <Skeleton.Avatar active size={58} />
            <Skeleton active paragraph={false} title={{ width: 120 }} />
          </div>
        ))}
      </div>
    );
  }

  if (!stats?.length) {
    return (
      <div className={[styles.strip, className].filter(Boolean).join(' ')}>
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="正在整理统计数据" />
      </div>
    );
  }

  return (
    <div className={[styles.strip, className].filter(Boolean).join(' ')}>
      {stats.slice(0, count).map((stat, index) => {
        const Icon = icons[index % icons.length];
        return (
          <div className={styles.item} key={stat.key}>
            <div className={[styles.icon, stat.tone ? styles[stat.tone] : ''].filter(Boolean).join(' ')}>
              <Icon size={38} strokeWidth={1.55} />
            </div>
            <div>
              <span className={styles.label}>{stat.label}</span>
              <strong>
                {String(stat.value ?? '-')}
                {stat.unit && <em>{stat.unit}</em>}
              </strong>
              {stat.compare && <small>{stat.compare}</small>}
            </div>
          </div>
        );
      })}
    </div>
  );
}
