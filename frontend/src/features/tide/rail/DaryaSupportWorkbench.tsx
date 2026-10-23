import { BookOpenCheck, Compass, SearchCheck, Target } from 'lucide-react';
import type { DaryaCognitionOrchestration } from '../tideTypes';
import styles from './TideContextRail.module.css';

export function DaryaSupportWorkbench({ plan }: { plan: DaryaCognitionOrchestration }) {
  const brief = plan.work_brief;
  const help = plan.help_opportunity;
  const facts = [...brief.confirmed_facts, ...brief.observed_facts].slice(0, 3);
  const unknown = help.highest_value_unknown || brief.unknowns[0];
  const researchWhy = plan.research_task.questions[0]?.why_it_changes_the_plan;

  return (
    <section className={styles.supportWorkbench} aria-label="DARYA 当前工作" data-testid="darya-support-workbench">
      <div className={styles.supportLead}>
        <Target size={15} aria-hidden="true" />
        <div><span>我理解的目标</span><p>{brief.goal || help.true_goal}</p></div>
      </div>
      {facts.length > 0 && <div className={styles.supportLine}><span>我会参考</span><p>{facts.join('；')}</p></div>}
      {(unknown || plan.research_task.needed) && (
        <div className={styles.supportLine}>
          <SearchCheck size={14} aria-hidden="true" />
          <div><span>{plan.research_task.needed ? '我正在主动核对' : '我还要判断'}</span><p>{unknown || researchWhy || '先补齐会影响方案的关键信息'}</p></div>
        </div>
      )}
      {brief.strategy && <div className={styles.supportLine}><Compass size={14} aria-hidden="true" /><div><span>我会怎样帮你</span><p>{brief.strategy}</p></div></div>}
      {help.best_immediate_deliverable && <div className={styles.supportLine}><BookOpenCheck size={14} aria-hidden="true" /><div><span>现在先做</span><p>{help.best_immediate_deliverable}</p></div></div>}
      {(help.success_signal || brief.success_checks[0]) && <div className={styles.supportCheck}>收束依据：{help.success_signal || brief.success_checks[0]}</div>}
      {brief.resource_purposes.length > 0 && <p className={styles.supportResources}>需要资料时，我会按这个目的补充：{brief.resource_purposes.join('；')}</p>}
    </section>
  );
}
