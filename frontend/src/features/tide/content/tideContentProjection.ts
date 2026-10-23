import type { DaryaLearningFitProjection, TideMessage } from '../tideTypes';

export type TideSidecarKind =
  | 'action-plan'
  | 'learning-map'
  | 'exploration-map'
  | 'explanation'
  | 'checklist'
  | 'draft'
  | 'resource-map'
  | 'workflow'
  | 'code'
  | 'working-material'
  | 'learning-fit';

export interface TideSidecarObject {
  id: string;
  kind: TideSidecarKind;
  eyebrow: string;
  title: string;
  content: string;
  learningFit?: DaryaLearningFitProjection | null;
}

export interface TideMoment {
  currentUser: TideMessage | null;
  currentAssistant: TideMessage | null;
  userCue: string;
  centerResponse: string;
  olderTurnCount: number;
  sidecar: TideSidecarObject | null;
}

function lastIndexWhere<T>(items: T[], predicate: (item: T) => boolean) {
  for (let index = items.length - 1; index >= 0; index -= 1) {
    if (predicate(items[index])) return index;
  }
  return -1;
}

const LEGACY_IDENTITY = new RegExp(`${['柿', '小', '吉'].join('')}(?:老师)?`, 'g');
const EMOJI = /[\u{1F1E6}-\u{1F1FF}\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/gu;
const BLACKBOARD = /\[blackboard\]([\s\S]*?)\[\/blackboard\]/gi;
const BLACKBOARD_XML = /<blackboard>([\s\S]*?)<\/blackboard>/gi;
const DETAIL_SIGNAL = /详细|深入|展开|推导|原理|步骤|完整|系统|分析|计划|清单|公式|证明|代码|实现|debug|code|why|explain/i;
const CODE_SIGNAL = /```|`[^`]+`|\b(?:function|const|let|var|class|def|import|SELECT|CREATE)\b/i;
const TIDE_CENTER_OPEN = /\[tide_center\]/i;
const TIDE_CENTER_CLOSE = /\[\/tide_center\]/i;
const TIDE_SIDECAR_OPEN = /\[tide_sidecar\s+([^\]]*)\]/i;
const TIDE_SIDECAR_CLOSE = /\[\/tide_sidecar\]/i;
const DARYA_LEARNING_FIT = /\[darya_learning_fit\]([\s\S]*?)\[\/darya_learning_fit\]/i;
const TIDE_SIDECAR_KINDS = new Set<TideSidecarKind>([
  'action-plan', 'learning-map', 'exploration-map', 'explanation', 'checklist',
  'draft', 'resource-map', 'workflow', 'code', 'working-material', 'learning-fit',
]);

const RUN_EVENT = /^\s*已运行代码\s*v\d+(?:\s*·\s*Run\s*\d+)?[：:]\s*([A-Za-z_]+)\s*。?\s*$/;
const RUN_EVENT_COPY: Record<string, string> = {
  SUCCEEDED: '这次代码运行成功了。',
  FAILED: '这次代码没有运行成功。',
  RUNNING: '代码正在运行，结果马上出来。',
  PENDING: '代码正在等待运行。',
  TIMED_OUT: '这次代码运行超时了。',
  SANDBOX_UNAVAILABLE: '运行环境暂时不可用，稍后再试。',
};

export function applyTideVoiceContract(raw: string) {
  const runEvent = RUN_EVENT.exec(String(raw || ''));
  if (runEvent) {
    // 运行事件的开发者文案只做投影级改写，原始会话数据不动；技术细节留在代码实验里。
    return RUN_EVENT_COPY[runEvent[1]] || '这次代码试运行结束了，详情在代码实验里。';
  }
  return String(raw || '')
    .replace(LEGACY_IDENTITY, 'DARYA')
    .replace(EMOJI, '')
    .replace(/咱们/g, '我们')
    .replace(/！{1,}/g, '。')
    .replace(/(?:别慌|慌是正常的|紧张很正常|不要紧张|先深呼吸一下|先深呼吸|我来帮你哦|给你三个小技巧)[，。；：:\s]*/g, '')
    .replace(/^(?:#{1,6}\s*)?(?:当前判断|当前结论|回答)\s*[：:]?\s*/i, '')
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

/**
 * 剥离旧的文本黑板块。
 *
 * 迁移期还要认它 —— 老消息里带着整块板书文本，直接显示会在正文里出现一大段
 * 和板面重复的内容。但它**不再**被投影成右栏 Sidecar：板上的东西现在由黑板
 * 自己画出来，正文里不该有第二份。
 */
function extractBlackboard(raw: string) {
  const visible = raw.replace(BLACKBOARD, '').replace(BLACKBOARD_XML, '');
  return { visible: visible.trim(), blocked: BLACKBOARD.test(raw) || BLACKBOARD_XML.test(raw) };
}

function attribute(source: string, name: string) {
  const quoted = new RegExp(`${name}\\s*=\\s*["']([^"']+)["']`, 'i').exec(source);
  if (quoted) return quoted[1].trim();
  return new RegExp(`${name}\\s*=\\s*([^\\s]+)`, 'i').exec(source)?.[1]?.trim() || '';
}

function tideSidecarKind(value: string): TideSidecarKind {
  return TIDE_SIDECAR_KINDS.has(value as TideSidecarKind)
    ? value as TideSidecarKind
    : 'working-material';
}

function tideEyebrow(kind: TideSidecarKind) {
  const labels: Record<TideSidecarKind, string> = {
    'action-plan': '工作材料 · 行动计划',
    'learning-map': '工作材料 · 学习地图',
    'exploration-map': '工作材料 · 探索地图',
    explanation: '工作材料 · 讲解',
    checklist: '工作材料 · 检查清单',
    draft: '工作材料 · 草稿',
    'resource-map': '工作材料 · 资源',
    workflow: '工作材料 · 流程',
    code: '工作材料 · 代码',
    'working-material': '工作材料',
    'learning-fit': '学习契合 · 此刻',
  };
  return labels[kind];
}

function extractTideContract(raw: string) {
  const centerOpen = TIDE_CENTER_OPEN.exec(raw);
  const sidecarOpen = TIDE_SIDECAR_OPEN.exec(raw);
  if (!centerOpen) {
    return { matched: /^\s*\[tide_/i.test(raw), center: '', sidecar: null as TideSidecarObject | null };
  }

  const centerStart = centerOpen.index + centerOpen[0].length;
  const centerTail = raw.slice(centerStart);
  const centerClose = TIDE_CENTER_CLOSE.exec(centerTail);
  const centerEnd = centerClose ? centerStart + centerClose.index : (sidecarOpen?.index ?? raw.length);
  const center = raw.slice(centerStart, centerEnd).trim();

  if (!sidecarOpen) return { matched: true, center, sidecar: null as TideSidecarObject | null };
  const sidecarStart = sidecarOpen.index + sidecarOpen[0].length;
  const sidecarTail = raw.slice(sidecarStart);
  const sidecarClose = TIDE_SIDECAR_CLOSE.exec(sidecarTail);
  const rawContent = raw.slice(sidecarStart, sidecarClose ? sidecarStart + sidecarClose.index : raw.length).trim();
  const fitMatch = DARYA_LEARNING_FIT.exec(rawContent);
  let learningFit: DaryaLearningFitProjection | null = null;
  if (fitMatch) {
    try {
      const value = JSON.parse(fitMatch[1]) as DaryaLearningFitProjection;
      if (value?.contract_version === 'darya_visual_learner_fit_v1') learningFit = value;
    } catch {
      learningFit = null;
    }
  }
  const content = rawContent.replace(DARYA_LEARNING_FIT, '').trim();
  if (!content) return { matched: true, center, sidecar: null as TideSidecarObject | null };

  const kind = tideSidecarKind(attribute(sidecarOpen[1], 'type'));
  const title = attribute(sidecarOpen[1], 'title') || '我们一起在做的事';
  return {
    matched: true,
    center,
    sidecar: {
      id: '',
      kind,
      eyebrow: tideEyebrow(kind),
      title,
      content,
      learningFit,
    } satisfies TideSidecarObject,
  };
}

function plainText(raw: string) {
  return raw
    .replace(/```[\s\S]*?```/g, '')
    .replace(/^#{1,6}\s+/gm, '')
    .replace(/^\s*[-*+]\s+/gm, '')
    .replace(/^\s*\d+[.)、]\s+/gm, '')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/[*_~`>|]/g, '')
    .replace(/\s+/g, ' ')
    .trim();
}

function clipSentence(raw: string, maxLength = 132) {
  const text = plainText(raw);
  if (text.length <= maxLength) return text;
  const boundary = text.slice(0, maxLength).search(/[。！？；](?!.*[。！？；])/);
  if (boundary >= 44) return text.slice(0, boundary + 1);
  return `${text.slice(0, maxLength).trim()}…`;
}

function sidecarTitle(question: string, kind: TideSidecarKind) {
  const topic = plainText(question)
    .replace(/^(请|能否|可以|帮我|给我|麻烦)?\s*(详细|深入|完整|系统)?\s*(解释|讲解|分析|说明|写出|给出|实现)?\s*/i, '')
    .replace(/[？?。！!]$/g, '')
    .slice(0, 22)
    .trim();
  if (kind === 'code') return `${topic || '代码'} · 实现与说明`;
  if (kind === 'working-material') return `${topic || '当前问题'} · 工作材料`;
  return `${topic || '当前问题'} · 深入理解`;
}

function centerFromDetailed(content: string, kind: TideSidecarKind) {
  const firstParagraph = content
    .split(/\n\s*\n/)
    .map((part) => part.trim())
    .find((part) => part && !part.startsWith('```')) || content;
  const summary = clipSentence(firstParagraph, 124) || '关键内容已经整理完成。';
  const direction = kind === 'code'
    ? '实现与关键说明在右侧。'
    : kind === 'working-material'
      ? '完整工作材料在右侧。'
      : '完整解释与推导在右侧。';
  return `${summary}${/[。！？]$/.test(summary) ? '' : '。'}${direction}`;
}

export function projectTideMoment(messages: TideMessage[]): TideMoment {
  const visibleMessages = messages.filter((message) => message.role !== 'system');
  const assistantIndex = lastIndexWhere(visibleMessages, (message) => message.role === 'assistant');
  const currentAssistant = assistantIndex >= 0 ? visibleMessages[assistantIndex] : null;
  const userSearchEnd = assistantIndex >= 0 ? assistantIndex : visibleMessages.length;
  const userCandidates = visibleMessages.slice(0, userSearchEnd);
  const currentUserIndex = lastIndexWhere(userCandidates, (message) => message.role === 'user');
  const currentUser = currentUserIndex >= 0 ? userCandidates[currentUserIndex] : null;
  const userTurns = visibleMessages.filter((message) => message.role === 'user').length;

  if (!currentAssistant) {
    return {
      currentUser,
      currentAssistant: null,
      userCue: currentUser ? clipSentence(currentUser.content, 96) : '',
      centerResponse: '',
      olderTurnCount: Math.max(0, userTurns - (currentUser ? 1 : 0)),
      sidecar: null,
    };
  }

  const voiced = applyTideVoiceContract(currentAssistant.content);
  const tideContract = extractTideContract(voiced);
  if (tideContract.matched) {
    return {
      currentUser,
      currentAssistant,
      userCue: currentUser ? clipSentence(currentUser.content, 96) : '',
      centerResponse: tideContract.center,
      olderTurnCount: Math.max(0, userTurns - (currentUser ? 1 : 0)),
      sidecar: tideContract.sidecar ? { ...tideContract.sidecar, id: currentAssistant.id } : null,
    };
  }
  const { visible, blocked } = extractBlackboard(voiced);
  const question = currentUser?.content || '';
  const hasCode = CODE_SIGNAL.test(voiced) || /代码|实现|编程|程序|code/i.test(question);
  const substantial = visible.length > 250 || visible.split('\n').length >= 7;
  const detailed = substantial || DETAIL_SIGNAL.test(question) || hasCode;
  const kind: TideSidecarKind = hasCode ? 'code' : 'working-material';
  const sidecarContent = applyTideVoiceContract(visible);
  const sidecar = detailed && sidecarContent
    ? {
        id: currentAssistant.id,
        kind,
        eyebrow: kind === 'code' ? '工作材料 · 代码' : '工作材料 · 背景',
        title: sidecarTitle(question, kind),
        content: sidecarContent,
      }
    : null;

  return {
    currentUser,
    currentAssistant,
    userCue: currentUser ? clipSentence(currentUser.content, 96) : '',
    centerResponse: sidecar
      ? centerFromDetailed(sidecarContent, kind)
      // 老消息里正文可能只有一块板书文本：板上的东西现在会画出来，
      // 正文不该空着说"没有可显示内容"。
      : visible || (blocked ? '这一轮 DARYA 写在黑板上。' : visible),
    olderTurnCount: Math.max(0, userTurns - (currentUser ? 1 : 0)),
    sidecar,
  };
}

/** 把历史消息投影成可直接回看的正文：去掉 tide 契约标签、黑板块与学习契合载荷。 */
export function projectRecededText(raw: string) {
  const stripped = String(raw || '')
    .replace(DARYA_LEARNING_FIT, '')
    .replace(TIDE_CENTER_OPEN, '')
    .replace(TIDE_CENTER_CLOSE, '')
    .replace(TIDE_SIDECAR_OPEN, '')
    .replace(TIDE_SIDECAR_CLOSE, '');
  const { visible } = extractBlackboard(stripped);
  return applyTideVoiceContract(visible);
}
