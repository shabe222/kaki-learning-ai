import { ArrowRight, Pause } from 'lucide-react';
import { SafeMarkdown } from '../../../components/SafeMarkdown';
import type { DaryaActiveRouteProjection, DaryaLearningFitProjection } from '../tideTypes';
import styles from './TideLearningFitView.module.css';

interface TideLearningFitViewProps {
  route?: DaryaActiveRouteProjection | null;
  fit?: DaryaLearningFitProjection | null;
  resourceContent?: string;
  paused?: boolean;
  onOpenUnderstanding?: () => void;
}

const resourceLabels: Record<string, string> = {
  EXPLANATION_CARD: '短解释', PRACTICE_TASK: '小练习', WORKED_EXAMPLE: '完整例子',
  QUICK_CHECK: '快速检查', HANDOUT: '定向讲义', CODE: '代码练习', ASSESSMENT: '分层测评',
};

function resourceLabel(value?: string | null) {
  return resourceLabels[value || ''] || value || '当前材料';
}

const changeSourceCopy: Record<string, string> = {
  assessment: '作答',
  code_submission: '提交',
  conversation: '对话',
};

/** 只做投影级改写：来源变成"刚才那次××"，内容仍来自真实 route 数据。 */
export function humanChangeSentence(change: NonNullable<DaryaActiveRouteProjection['latest_change']>) {
  const source = changeSourceCopy[change.source] || '学习';
  const body = (change.explanation || change.summary || '').replace(/服务端测试确认/g, '提交检查确认').replace(/。+$/, '');
  return `刚才那次${source}让我更确定：${body}。`;
}

/**
 * Active Route 的人话投影：只回答三层问题——
 * 我现在在哪 / 为什么这样安排 / 下一步是什么。
 * 最近变化最多保留一句；内部证据语言不上屏。路线权威本身不受影响。
 */
export function TideLearningFitView({ route, fit, resourceContent = '', paused = false, onOpenUnderstanding }: TideLearningFitViewProps) {
  const current = route?.current_title || fit?.route?.now || fit?.target || '当前学习节点';
  const gap = route?.current_gap || fit?.gap || '还需要一轮可观察证据才能确认';
  const next = route?.next.title || fit?.target || current;
  const nextReason = (route?.next.reason || fit?.why_now || fit?.route?.reason || '').replace(/服务端测试确认/g, '提交检查确认');
  const whyArrangement = (route?.why_this_learning || fit?.preference_reason || '先按当前的学习情况安排，等下一次真实练习后再调整。').replace(/服务端测试确认/g, '提交检查确认');
  const latestChange = route?.latest_change;
  const materialType = route?.next.resource_type || fit?.resource_type;
  const milestones = route?.passed_milestones || [];

  return (
    <div
      className={styles.view}
      data-testid="tide-active-route"
      data-route-revision={route?.revision || 'fit-only'}
      data-route-paused={paused ? 'true' : 'false'}
    >
      {paused && (
        <div className={styles.paused} role="status">
          <Pause size={14} aria-hidden="true" />
          <span><strong>路线已安静保留</strong>你可以聊别的；说“继续刚才”时再接回来。</span>
        </div>
      )}

      <ol className={styles.routeLayers} aria-label="当前学习路线">
        <li data-layer="now">
          <span>我现在在哪</span>
          <strong>{current}</strong>
          <p>{gap}</p>
          {milestones.length > 0 && (
            <small>已经走过：{milestones.map((milestone) => milestone.title).join(' → ')}</small>
          )}
        </li>
        <li data-layer="why">
          <span>为什么这样安排</span>
          <p>{whyArrangement}</p>
        </li>
        <li data-layer="next">
          <span>下一步是什么{materialType ? ` · ${resourceLabel(materialType)}` : ''}</span>
          <strong>{next}</strong>
          {nextReason && nextReason !== whyArrangement && <p>{nextReason}</p>}
        </li>
      </ol>

      {latestChange?.summary && (
        <p className={styles.changeLine} aria-label="最近变化">
          <span>最近变化</span>
          {humanChangeSentence(latestChange)}
        </p>
      )}

      <p className={styles.correctionHint}>如果这个判断不对，直接在对话里告诉 DARYA 就好；它只影响这段学习路线。</p>

      {onOpenUnderstanding && (
        <button type="button" className={styles.understandingLink} onClick={onOpenUnderstanding} data-testid="tide-open-understanding-link">
          查看 DARYA 对我的理解<ArrowRight size={13} aria-hidden="true" />
        </button>
      )}

      {resourceContent && (
        <div className={styles.resource}>
          <div className={styles.resourceHeading}><span>本轮材料</span><strong>{resourceLabel(fit?.resource_type)}</strong></div>
          <SafeMarkdown source={resourceContent} className={styles.markdown} />
        </div>
      )}
    </div>
  );
}
