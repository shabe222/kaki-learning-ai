export type ModuleActionTransport = 'module-create' | 'report-export' | 'backend';

export interface ModuleActionContract {
  action: string;
  modules: readonly string[];
  transport: ModuleActionTransport;
  description: string;
}

const learning = ['learning'] as const;
const dashboard = ['dashboard'] as const;
const resources = ['resources', 'generic'] as const;
const agents = ['agents'] as const;
const video = ['video'] as const;
const exam = ['exam'] as const;
const profile = ['profile'] as const;
const report = ['report'] as const;
const qaNotes = ['qa', 'notes'] as const;
// 跨模块共享：考试中心 / 学习 / 资料 / 答疑等都可以触发智能体
const allModules = ['learning', 'dashboard', 'resources', 'generic', 'agents', 'video', 'exam', 'profile', 'report', 'qa', 'notes'] as const;

export const ACTION_CONTRACTS: readonly ModuleActionContract[] = [
  { action: 'complete-dashboard-todo', modules: dashboard, transport: 'module-create', description: 'Mark a dashboard todo as completed.' },
  { action: 'open-dashboard-activity', modules: dashboard, transport: 'module-create', description: 'Open a dashboard activity record.' },
  { action: 'apply-dashboard-advice', modules: dashboard, transport: 'module-create', description: 'Apply a dashboard AI learning suggestion.' },

  { action: 'switch-course', modules: learning, transport: 'module-create', description: 'Switch the active course context.' },
  { action: 'continue-lesson', modules: learning, transport: 'module-create', description: 'Continue the current lesson.' },
  { action: 'start-practice', modules: learning, transport: 'module-create', description: 'Start chapter practice.' },

  { action: 'upload-resource', modules: resources, transport: 'module-create', description: 'Start resource upload.' },
  { action: 'select-upload-file', modules: resources, transport: 'module-create', description: 'Open file selection for upload.' },
  { action: 'preview-resource', modules: resources, transport: 'module-create', description: 'Preview a selected resource.' },
  { action: 'open-ai-artifact', modules: resources, transport: 'module-create', description: 'Open an AI generated resource artifact.' },
  { action: 'view-resource', modules: resources, transport: 'module-create', description: 'View resource detail.' },
  { action: 'download-resource', modules: resources, transport: 'module-create', description: 'Download a resource.' },
  { action: 'favorite-resource', modules: resources, transport: 'module-create', description: 'Toggle resource favorite state.' },
  { action: 'resource-more', modules: resources, transport: 'module-create', description: 'Open additional resource actions.' },

  { action: 'create-agent', modules: agents, transport: 'module-create', description: 'Create a new learning agent.' },
  { action: 'trigger-agent', modules: allModules, transport: 'module-create', description: 'Trigger an agent for a target module.' },
  { action: 'view-agent-task', modules: agents, transport: 'module-create', description: 'Open the current agent task detail.' },
  { action: 'list-agent-outputs', modules: agents, transport: 'module-create', description: 'List all agent outputs.' },
  { action: 'download-agent-output', modules: agents, transport: 'module-create', description: 'Download an agent output.' },
  { action: 'view-agent-status-log', modules: agents, transport: 'module-create', description: 'View agent status logs.' },
  { action: 'list-agent-records', modules: agents, transport: 'module-create', description: 'List agent execution records.' },

  { action: 'play-video', modules: video, transport: 'module-create', description: 'Start or resume video playback.' },
  { action: 'generate-script', modules: video, transport: 'module-create', description: 'Generate a class script from video context.' },
  { action: 'generate-interactive-ppt', modules: video, transport: 'module-create', description: 'Generate an interactive classroom PPT artifact.' },
  { action: 'generate-ppt-handout', modules: video, transport: 'module-create', description: 'Generate a PPT handout artifact.' },
  { action: 'regenerate-script', modules: video, transport: 'module-create', description: 'Regenerate the class script.' },
  { action: 'create-video-note', modules: video, transport: 'module-create', description: 'Create a timestamp note.' },
  { action: 'list-video-notes', modules: video, transport: 'module-create', description: 'List video timestamp notes.' },
  { action: 'filter-video-resource', modules: video, transport: 'module-create', description: 'Filter external video resources.' },
  { action: 'sort-video-resource', modules: video, transport: 'module-create', description: 'Sort external video resources.' },
  { action: 'open-external-resource', modules: video, transport: 'module-create', description: 'Open an external resource.' },
  { action: 'apply-ai-advice', modules: video, transport: 'module-create', description: 'Apply a video learning suggestion.' },

  { action: 'switch-exam-type', modules: exam, transport: 'module-create', description: 'Switch exam creation type.' },
  { action: 'set-exam-difficulty', modules: exam, transport: 'module-create', description: 'Set exam paper difficulty.' },
  { action: 'set-question-types', modules: exam, transport: 'module-create', description: 'Set the exam question type set.' },
  { action: 'set-question-count', modules: exam, transport: 'module-create', description: 'Set the exam question count.' },
  { action: 'generate-exam', modules: exam, transport: 'module-create', description: 'Generate an exam paper.' },
  { action: 'generate-question-artifact', modules: exam, transport: 'module-create', description: 'Generate persisted practice questions.' },
  { action: 'generate-mindmap', modules: exam, transport: 'module-create', description: 'Generate a persisted mindmap artifact.' },
  { action: 'list-my-exams', modules: exam, transport: 'module-create', description: 'List owned exam papers.' },
  { action: 'list-available-exams', modules: exam, transport: 'module-create', description: 'List available exams.' },
  { action: 'start-exam', modules: exam, transport: 'module-create', description: 'Start or continue an exam.' },
  { action: 'list-exam-records', modules: exam, transport: 'module-create', description: 'List exam records.' },
  { action: 'list-weak-points', modules: exam, transport: 'module-create', description: 'List exam weak points.' },
  { action: 'review-weak-point', modules: exam, transport: 'module-create', description: 'Start weak point review.' },
  { action: 'continue-exam', modules: exam, transport: 'module-create', description: 'Continue an unfinished exam.' },
  { action: 'view-exam-report', modules: exam, transport: 'module-create', description: 'Open an exam report.' },

  { action: 'list-profile-weak-points', modules: profile, transport: 'module-create', description: 'List profile weak points.' },
  { action: 'practice-profile-weak-point', modules: profile, transport: 'module-create', description: 'Practice a profile weak point.' },
  { action: 'view-learning-habits', modules: profile, transport: 'module-create', description: 'View learning habit details.' },
  { action: 'list-profile-assessments', modules: profile, transport: 'module-create', description: 'List profile assessments.' },
  { action: 'list-improvement-paths', modules: profile, transport: 'module-create', description: 'List recommended improvement paths.' },
  { action: 'start-improvement-path', modules: profile, transport: 'module-create', description: 'Start a recommended improvement path.' },

  { action: 'list-learning-records', modules: report, transport: 'module-create', description: 'List report learning records.' },
  { action: 'export-report', modules: report, transport: 'report-export', description: 'Export a report document.' },

  { action: 'create-question', modules: ['qa'], transport: 'module-create', description: 'Create a new question.' },
  { action: 'ask-ai-tutor', modules: ['qa'], transport: 'module-create', description: 'Ask the persisted AI tutor endpoint.' },
  { action: 'create-note', modules: ['notes'], transport: 'module-create', description: 'Create a new note.' },
  { action: 'filter-note-tab', modules: qaNotes, transport: 'module-create', description: 'Filter notes by tab.' },
  { action: 'filter-note-tag', modules: qaNotes, transport: 'module-create', description: 'Filter notes by tag.' },
  { action: 'open-source-dialog', modules: qaNotes, transport: 'module-create', description: 'Open the source QA dialog.' },
  { action: 'copy-note-content', modules: qaNotes, transport: 'module-create', description: 'Copy note content.' },
  { action: 'export-note', modules: qaNotes, transport: 'module-create', description: 'Export a note.' },
  { action: 'list-recent-favorites', modules: qaNotes, transport: 'module-create', description: 'List recent favorite notes.' },
  { action: 'open-related-content', modules: qaNotes, transport: 'module-create', description: 'Open related learning content.' }
];

export function resolveModuleAction(module: string, action: string) {
  return ACTION_CONTRACTS.find((contract) => contract.action === action && contract.modules.includes(module)) ?? null;
}
