import { App as AntdApp } from 'antd';
import { useCallback, useState } from 'react';
import { canUseFallbackData, getDataMode } from '../api/dataMode';
import { moduleApi } from '../api/modules';
import { resolveModuleAction } from '../contracts/moduleActionContracts';
import { recordActionEvent } from '../stores/actionEventStore';
import type { ActionEventStatus } from '../stores/actionEventStore';

interface ActionPayload {
  action: string;
  title: string;
  payload?: Record<string, unknown>;
}

interface ActionOptions {
  reportType?: string;
  successText?: string;
}

const localUiActions = new Set([
  'filter-note-tab',
  'filter-note-tag',
  'open-source-dialog',
  'copy-note-content',
  'export-note',
  'list-recent-favorites',
  'open-related-content',
  'list-agent-outputs',
  'download-agent-output',
  'view-agent-status-log',
  'list-agent-records',
  'view-agent-task',
  'view-exam-report',
  'list-weak-points',
  'review-weak-point',
  'switch-exam-type',
  'set-exam-difficulty',
  'set-question-types',
  'set-question-count'
]);

function notifyArtifactGenerated() {
  window.dispatchEvent(new CustomEvent('kaki-artifact-generated'));
}

export function useModuleActions(module: string) {
  const { message } = AntdApp.useApp();
  const [runningAction, setRunningAction] = useState<string | null>(null);
  const [lastAction, setLastAction] = useState<ActionPayload | null>(null);

  const runAction = useCallback(async (action: string, title: string, payload?: Record<string, unknown>, options?: ActionOptions) => {
    const contract = resolveModuleAction(module, action);
    if (!contract) {
      message.error(`${title} action contract missing`);
      return false;
    }

    const actionKey = `${action}:${title}`;
    const commitAction = (status: ActionEventStatus) => {
      recordActionEvent({ module, action, title, payload, status, transport: contract.transport });
      setLastAction({ action, title, payload });
    };

    setRunningAction(actionKey);
    try {
      if (contract.transport === 'report-export') {
        await moduleApi.exportReport(options?.reportType ?? String(payload?.type ?? 'full'));
      } else if (module === 'learning' && (action === 'continue-lesson' || action === 'start-practice') && payload?.chapterId) {
        await moduleApi.startChapter(String(payload.chapterId));
      } else if (module === 'video' && (action === 'generate-script' || action === 'regenerate-script')) {
        await moduleApi.generateVideo(String(payload?.topic ?? title), 'script');
      } else if (module === 'video' && action === 'generate-interactive-ppt') {
        const topic = String(payload?.topic ?? title);
        await moduleApi.generateDocument({ topic, style: 'interactive-classroom-ppt', mode: 'interactive-ppt' });
      } else if (module === 'video' && action === 'generate-ppt-handout') {
        const topic = String(payload?.topic ?? title);
        await moduleApi.generateDocument({ topic, style: 'ppt-handout', mode: 'ppt-handout' });
      } else if (module === 'exam' && action === 'generate-exam') {
        // 走 question 智能体同步触发：把设置项透传给 LLM
        await moduleApi.invokeAgent('question', {
          topic: String(payload?.topic ?? payload?.chapter ?? 'machine-learning'),
          examType: String(payload?.examType ?? 'mock'),
          chapterIds: Array.isArray(payload?.chapterIds) ? payload.chapterIds : [String(payload?.chapter ?? 'chapter-3')],
          difficulty: String(payload?.difficulty ?? '中等'),
          questionTypes: Array.isArray(payload?.questionTypes) ? payload.questionTypes : ['单选题', '多选题', '判断题'],
          count: Number(payload?.count ?? 30)
        });
        // 然后真正创建 ExamPaper
        await moduleApi.createExam(
          String(payload?.examType ?? 'mock'),
          Array.isArray(payload?.chapterIds) ? payload.chapterIds.map(String) : [String(payload?.chapter ?? 'chapter-3')],
          {
            difficulty: String(payload?.difficulty ?? '全部'),
            questionTypes: Array.isArray(payload?.questionTypes) ? payload.questionTypes : undefined,
            count: Number(payload?.count ?? 0) || undefined
          }
        );
      } else if (module === 'exam' && action === 'generate-question-artifact') {
        await moduleApi.generateQuestionArtifact({
          topic: String(payload?.topic ?? payload?.chapter ?? title),
          count: Number(payload?.questionCount ?? 10),
          style: String(payload?.difficulty ?? 'medium')
        });
      } else if (module === 'exam' && action === 'generate-mindmap') {
        await moduleApi.generateMindmap({
          topic: String(payload?.topic ?? payload?.chapter ?? title),
          style: 'learning-map'
        });
      } else if (module === 'qa' && action === 'create-question') {
        await moduleApi.askTutorArtifact({ question: String(payload?.message ?? title), context: payload });
      } else if (module === 'qa' && action === 'ask-ai-tutor') {
        await moduleApi.askTutorArtifact({ question: String(payload?.message ?? title), context: payload });
      } else if (module === 'notes' && action === 'create-note') {
        await moduleApi.saveItem({
          type: 'note',
          title,
          content: String(payload?.content ?? title),
          raw_data: { source: 'notes-page' }
        });
      } else if (action === 'trigger-agent') {
        // 跨模块：让 exam / agents / learning 等页面都能调用指定智能体
        await moduleApi.invokeAgent(String(payload?.agentName ?? payload?.target ?? title), payload ?? {});
      } else if (localUiActions.has(action)) {
        await Promise.resolve();
      } else {
        await moduleApi.create(module, { action, title, payload });
      }
      commitAction('success');
      notifyArtifactGenerated();
      message.success(options?.successText ?? `${title} synced`);
      return true;
    } catch {
      if (canUseFallbackData()) {
        commitAction('local-fallback');
        message.warning(`${title} saved to local demo stream (${getDataMode()} mode)`);
        return true;
      }

      commitAction('error');
      message.error(`${title} failed; data mode is ${getDataMode()}`);
      return false;
    } finally {
      setRunningAction(null);
    }
  }, [message, module]);

  const isRunning = useCallback((action: string, title: string) => runningAction === `${action}:${title}`, [runningAction]);

  return { runAction, isRunning, lastAction, notifyArtifactGenerated };
}
