import { Button, Card, Empty, Progress, Skeleton, Timeline } from 'antd';
import type { TimelineItem } from '../types/api';
import emptyIllustration from '../assets/generated/empty-persimmon-basket.jpg';
import styles from './DataPanel.module.css';

export function PaperCard({ title, children, extra, className }: { title: string; children: React.ReactNode; extra?: React.ReactNode; className?: string }) {
  return (
    <Card className={[styles.card, className].filter(Boolean).join(' ')} title={title} extra={extra} variant="outlined">
      {children}
    </Card>
  );
}

export function TrendChart({ data, unit }: { data?: Array<{ label: string; value: number }>; unit?: 'h' | '%' }) {
  if (!data?.length) {
    return <Empty image={<img src={emptyIllustration} alt="" aria-hidden="true" style={{ width: 116, height: 'auto', borderRadius: 12, mixBlendMode: 'multiply' }} />} styles={{ image: { height: 'auto' } }} description="暂无趋势数据，完成一次练习后会出现变化" />;
  }
  const maxValue = Math.max(...data.map((item) => item.value), 1);
  const scoreMode = unit === '%' || maxValue > 10;
  const axisMax = scoreMode ? 100 : Math.ceil(maxValue);
  const chartTop = 8;
  const chartHeight = 82;
  const points = data
    .map((item, index) => {
      const x = (index / Math.max(data.length - 1, 1)) * 100;
      const y = chartTop + (1 - Math.min(item.value, axisMax) / axisMax) * chartHeight;
      return `${x},${y}`;
    })
    .join(' ');
  const areaPoints = `0,${chartTop + chartHeight} ${points} 100,${chartTop + chartHeight}`;
  const markerIndex = data.reduce((best, item, index) => (item.value > data[best].value ? index : best), 0);
  const marker = data[markerIndex];
  const markerX = (markerIndex / Math.max(data.length - 1, 1)) * 100;
  const markerY = chartTop + (1 - Math.min(marker.value, axisMax) / axisMax) * chartHeight;
  const markerStyle = {
    left: `${markerX}%`,
    top: `${markerY}%`
  } as React.CSSProperties;
  const yLabels = scoreMode
    ? (unit === '%' ? ['100%', '75%', '50%', '25%', '0%'] : ['100', '75', '50', '25', '0'])
    : [axisMax, Math.round(axisMax * 0.75), Math.round(axisMax * 0.5), Math.round(axisMax * 0.25), 0].map((item) => `${item}h`);
  return (
    <div className={styles.trend}>
      <div className={styles.yAxis}>
        {yLabels.map((label, idx) => <span key={`y-${idx}-${label}`}>{label}</span>)}
      </div>
      <svg viewBox="0 0 100 100" preserveAspectRatio="none">
        <polyline points={areaPoints} className={styles.area} />
        <polyline points={points} className={styles.line} />
        {data.map((item, index) => {
          const x = (index / Math.max(data.length - 1, 1)) * 100;
          const y = chartTop + (1 - Math.min(item.value, axisMax) / axisMax) * chartHeight;
          return <circle key={`dot-${index}-${item.label}`} cx={x} cy={y} r="1.4" className={styles.lineDot} />;
        })}
      </svg>
      <span className={styles.marker} style={markerStyle}>{unit === '%' ? `${marker.value}%` : scoreMode ? marker.value : `${marker.value}h`}</span>
      <div className={styles.axis}>{data.map((item, index) => <span key={`x-${index}-${item.label}`}>{item.label}</span>)}</div>
    </div>
  );
}

export function RadarChart({ data }: { data?: Array<{ label: string; value: number }> }) {
  if (!data?.length) {
    return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无能力数据" />;
  }
  const size = 230;
  const center = size / 2;
  const radius = 82;
  const points = data.map((item, index) => {
    const angle = (Math.PI * 2 * index) / data.length - Math.PI / 2;
    const valueRadius = radius * (item.value / 100);
    return `${center + Math.cos(angle) * valueRadius},${center + Math.sin(angle) * valueRadius}`;
  });
  return (
    <div className={styles.radar}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        {[0.35, 0.65, 1].map((scale) => (
          <polygon
            key={scale}
            points={data
              .map((_, index) => {
                const angle = (Math.PI * 2 * index) / data.length - Math.PI / 2;
                return `${center + Math.cos(angle) * radius * scale},${center + Math.sin(angle) * radius * scale}`;
              })
              .join(' ')}
            className={styles.grid}
          />
        ))}
        {data.map((_, index) => {
          const angle = (Math.PI * 2 * index) / data.length - Math.PI / 2;
          return <line key={index} x1={center} y1={center} x2={center + Math.cos(angle) * radius} y2={center + Math.sin(angle) * radius} className={styles.gridSpoke} />;
        })}
        <polygon points={points.join(' ')} className={styles.radarArea} />
        <polygon points={points.join(' ')} className={styles.radarLine} />
        {points.map((point, idx) => {
          const [cx, cy] = point.split(',').map(Number);
          return <circle key={`rdot-${idx}-${point}`} cx={cx} cy={cy} r="4" className={styles.radarDot} />;
        })}
      </svg>
      {data.map((item, index) => {
        const angle = (Math.PI * 2 * index) / data.length - Math.PI / 2;
        const labelRadius = radius + 22;
        const x = center + Math.cos(angle) * labelRadius;
        const y = center + Math.sin(angle) * labelRadius;
        return (
        <span key={`rlabel-${index}-${item.label}`} className={styles.radarLabel} style={{ left: `${(x / size) * 100}%`, top: `${(y / size) * 100}%` }}>
          {item.label} {item.value}
        </span>
      );})}
    </div>
  );
}

export function TimelinePanel({ items, loading }: { items?: TimelineItem[]; loading?: boolean }) {
  if (loading) return <Skeleton active paragraph={{ rows: 5 }} />;
  if (!items?.length) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无节点数据" />;
  return (
    <Timeline
      items={items.map((item) => ({
        color: item.status === 'active' ? '#ff5a14' : item.status === 'done' ? '#789048' : '#d8c7b8',
        children: (
          <div className={styles.timelineItem}>
            <strong>{item.title}</strong>
            <span>{item.time}</span>
            <p>{item.description}</p>
          </div>
        )
      }))}
    />
  );
}

export function ProgressList({ items, onPractice }: { items?: Array<{ label: string; value: number }>; onPractice?: (item: { label: string; value: number }) => void }) {
  if (!items?.length) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无进度数据" />;
  return (
    <div className={styles.progressList}>
      {items.map((item, index) => (
        <div key={`p-${index}-${item.label}`} className={styles.progressItem}>
          <span>{item.label}</span>
          <Progress percent={item.value} showInfo={false} strokeColor="#ff5a14" trailColor="#efe4da" />
          <Button size="small" onClick={() => onPractice?.(item)} disabled={!onPractice}>去练习</Button>
        </div>
      ))}
    </div>
  );
}
