import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useAuthStore } from '../../../stores/authStore';
import { DaryaAnnouncements } from '../../../components/DaryaAnnouncements';
import { getNormalizedLearnerState } from '../../../api/learnerState';
import { getData } from '../../../api/client';
import { VoiceCallPanel } from './VoiceCallPanel';
import { BoardSideScreen } from './BoardSideScreen';
import { activeBlackboard } from './activeBlackboard';
import { BookOpen, CircleAlert, ExternalLink, FolderOpen, History, LoaderCircle, Maximize2, Mic, Minimize2, PanelRight, Paperclip, Plus, Presentation, RefreshCcw, Repeat2, Send, Settings, ShieldCheck, Sparkles, Square, UserRound } from 'lucide-react';
import { SafeMarkdown } from '../../../components/SafeMarkdown';
import { useGentleReveal } from '../response/useGentleReveal';
import { useSentenceCadence } from '../response/useSentenceCadence';
import { RevealingCaret, ThinkingTrace } from '../response/ThinkingTrace';
import { Blackboard, NarrationCaption } from '../../blackboard';
import type { BoardNarration } from '../../blackboard';
import type { DaryaBoardLayout } from '../theme/daryaBoardLayout';
import type { DaryaSpaceMode } from '../theme/daryaSpaceMode';
import type { DaryaMascotForm, DaryaMascotFormSetting } from '../theme/daryaMascotForm';
import { projectTideMoment } from '../content/tideContentProjection';
import type { DaryaSearchGrounding } from '../tideTypes';
import { useTideStore } from '../state/tideStore';
import { tideChatApi } from './tideChatApi';
import { loadStorybookWidget } from './storybookLoader';
import { persistGeneratedPictureBook } from './pictureBookPersistence';
import { StudyClock } from '../study/StudyClock';
import styles from './TideChatSurface.module.css';

const MAX_DRAFT_LENGTH = 4_000;

/**
 * 占满模式里站在板子右边的数字人（柿小吉 / 海星，两种皮肤同一套动作）。
 *
 * 动作精灵图：12 列 × 5 行，每行一个完整动作（12 帧一循环）——
 * 行 1 站立、行 2 说话、行 3 思考、行 4 指黑板、行 5 指黑板+说话。
 * 讲解词里提到"指/看"就切指黑板行，提到"思考/自查"就切思考行，
 * 其余说话切说话行，没有讲解词就站着。只做渲染，是板子旁安静的同伴。
 */
const COMPANION_SHEETS: Record<DaryaMascotForm, string> = {
  persimmon: '/ai-workspace/assets/avatars/mascot-persimmon-actions-isolated-v1.png',
  starfish: '/ai-workspace/assets/avatars/mascot-starfish-actions-isolated-v1.png',
};

/**
 * 输入母图的真实绘制网格右移，且少量红白噪点把相邻帧连起来。
 * `*-isolated-v1.png` 已逐帧保留角色主体、去掉相邻帧并留下透明缓冲，
 * 所以这里必须按标准整格定位，不能再二次放大裁角色。
 */
const COMPANION_ROWS = { stand: 0, speak: 1, think: 2, pointSpeak: 3, point: 4 } as const;

function companionRowFor(caption: string): number {
  if (/指|看黑板|注意看|看这一|看这里|看下面/.test(caption)) return COMPANION_ROWS.pointSpeak;
  if (/思考|想想|想一想|自查|问自己|想想看/.test(caption)) return COMPANION_ROWS.think;
  if (caption) return COMPANION_ROWS.speak;
  return COMPANION_ROWS.stand;
}

/**
 * 标准精灵格定位。图集有 12 × 5 个不重叠的 512px 帧，
 * background-position 的 0% / 100% 分别对齐第一格 / 最后一格。
 */
function companionFramePosition(row: number, col: number, cols: number, rows: number) {
  return {
    x: `${(col / (cols - 1) * 100).toFixed(4)}%`,
    y: `${(row / (rows - 1) * 100).toFixed(4)}%`,
  };
}

function BoardCompanion({ form, formSetting, onFormSettingChange, caption, screenFront, spaceMode, speechState }: { form: DaryaMascotForm; formSetting: DaryaMascotFormSetting; onFormSettingChange?: (setting: DaryaMascotFormSetting) => void; caption: string; screenFront: boolean; spaceMode: DaryaSpaceMode; speechState: 'idle' | 'loading' | 'playing' }) {
  const [col, setCol] = useState(0);
  const [menuOpen, setMenuOpen] = useState(false);
  const companionRef = useRef<HTMLDivElement>(null);
  const row = speechState === 'playing'
    ? (spaceMode === 'fold' ? COMPANION_ROWS.pointSpeak : COMPANION_ROWS.speak)
    : speechState === 'loading' ? COMPANION_ROWS.think
      : spaceMode === 'fold' ? COMPANION_ROWS.point : companionRowFor(caption);
  const captionText = caption
    .replace(/\[blackboard\][\s\S]*?\[\/blackboard\]/gi, '')
    .replace(/[`*_#]+/g, '')
    .replace(/\s+/g, ' ')
    .trim();
  const shortCaption = captionText.length > 64 ? `${captionText.slice(0, 62).trimEnd()}…` : captionText;
  useEffect(() => {
    const timer = window.setInterval(() => setCol((value) => (value + 1) % 12), caption || speechState === 'playing' ? 180 : 300);
    return () => window.clearInterval(timer);
  }, [caption, speechState]);
  useEffect(() => {
    if (!menuOpen) return;
    const closeOutside = (event: PointerEvent) => {
      if (!companionRef.current?.contains(event.target as Node)) setMenuOpen(false);
    };
    document.addEventListener('pointerdown', closeOutside);
    return () => document.removeEventListener('pointerdown', closeOutside);
  }, [menuOpen]);
  const cols = 12;
  const rows = 5;
  const position = companionFramePosition(row, col, cols, rows);
  return (
    <div ref={companionRef} className={styles.boardCompanion} data-row={row} data-speaking={caption || speechState === 'playing' ? 'true' : 'false'} data-speech-state={speechState} data-screen-front={screenFront ? 'true' : 'false'} data-space-mode={spaceMode} onKeyDown={(event) => { if (event.key === 'Escape') setMenuOpen(false); }}>
      {shortCaption && <div className={styles.boardCompanionCaption} aria-hidden="true"><span>{shortCaption}</span></div>}
      <button type="button" className={styles.boardCompanionTrigger} aria-label={`切换数字人形象，当前${form === 'persimmon' ? '柿小吉' : '海星'}`} aria-expanded={menuOpen} aria-haspopup="true" onClick={() => setMenuOpen((open) => !open)} data-testid="darya-mascot-trigger">
        <span
          className={styles.boardCompanionSprite}
          style={{
            backgroundImage: `url("${COMPANION_SHEETS[form]}")`,
            backgroundSize: `${cols * 100}% ${rows * 100}%`,
            '--mascot-frame-x': position.x,
            '--mascot-frame-y': position.y,
          } as React.CSSProperties}
          aria-hidden="true"
        />
      </button>
      {menuOpen && (
        <div className={styles.boardCompanionMenu} role="group" aria-label="数字人形象" data-testid="darya-mascot-menu">
          {([['auto', '跟随主题'], ['persimmon', '柿小吉'], ['starfish', '海星']] as const).map(([setting, label]) => (
            <button key={setting} type="button" aria-pressed={formSetting === setting} onClick={() => { onFormSettingChange?.(setting); setMenuOpen(false); }} data-testid={`darya-mascot-${setting}`}>{label}</button>
          ))}
        </div>
      )}
    </div>
  );
}

/** 收起后的那一行也要说清它在哪一步，不然「展开」是个盲盒。 */
function researchStatusLabel(status: string): string {
  if (status === 'READY') return '已完成';
  if (status === 'PARTIAL') return '有阶段结果';
  if (status === 'FAILED') return '未完成';
  return '进行中';
}

// 生成中心：DARYA 当前真实可调用的全部生成能力。点选后把快捷指令填进输入框，
// 用户补全「」里的内容再发送，由对话语义路由唤起对应智能体。
const CAPABILITY_GROUPS: Array<{
  group: string;
  items: Array<{ key: string; label: string; desc: string; command: string; direct?: 'storybook' | 'voice-call' | 'url-material' | 'research' }>;
}> = [
  {
    group: '学习资料',
    items: [
      { key: 'lecture', label: '讲义', desc: '原理讲透 + 误区提醒 + 思考题', command: '帮我生成一份关于「」的讲义，把核心原理讲清楚' },
      { key: 'practice', label: '练习题', desc: '按我的水平出题，干扰项来自常见误区', command: '帮我出几道关于「」的练习题，带解析' },
      { key: 'mindmap', label: '思维导图', desc: '章节结构一图看清，方便复习', command: '帮我生成一张「」的思维导图' },
      { key: 'code', label: '代码示例', desc: '可运行、逐行讲解的示例', command: '帮我生成一个「」的 Python 代码示例' },
      { key: 'learning-graph', label: '学习路线', desc: '从目标到节点的完整规划', command: '帮我制定一份「」的学习路线' },
      { key: 'url-material', label: '网页资料', desc: '把一个网页读成可引用的资料来源', command: '', direct: 'url-material' },
      { key: 'research', label: '深度研究', desc: '多轮检索 · 每条结论带出处 · 后台跑完', command: '', direct: 'research' },
    ],
  },
  {
    group: '多媒体成品',
    items: [
      { key: 'presentation', label: 'PPT 课件', desc: '每页图文结合 · 可导出 PPTX', command: '帮我生成一份讲解「」的PPT课件' },
      { key: 'video', label: '教学视频', desc: '老师出镜讲解 + 逐句字幕卡', command: '帮我生成一段讲解「」的教学视频' },
      { key: 'audio-lesson', label: '音频课', desc: '把讲义变成能听的课', command: '帮我把「」生成一节音频课' },
      { key: 'storybook', label: '教学绘本', desc: '五页图文绘本 · 5E 教学法', command: '', direct: 'storybook' },
      { key: 'voice-call', label: '语音通话', desc: '连续对话：说完停顿即发送，回复朗读', command: '', direct: 'voice-call' },
    ],
  },
  {
    group: '复盘巩固',
    items: [
      { key: 'flashcards', label: '记忆卡', desc: '检索练习 + 间隔重复复习', command: '根据这份材料帮我生成记忆卡' },
      { key: 'mistake-book', label: '错题本', desc: '错因诊断 + 变式再练', command: '根据刚才的考试帮我生成错题本' },
      { key: 'exam', label: '试卷测验', desc: '组卷判分，纳入学习评估', command: '帮我出一份关于「」的试卷，判分并讲评' },
    ],
  },
];
type VoiceState = 'idle' | 'requesting' | 'recording' | 'transcribing' | 'error';

function preferredRecorderOptions(): MediaRecorderOptions | undefined {
  const candidates = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', 'audio/mp4'];
  const mimeType = candidates.find((candidate) => MediaRecorder.isTypeSupported(candidate));
  return mimeType ? { mimeType } : undefined;
}

function voiceErrorMessage(error: unknown) {
  if (error instanceof DOMException && error.name === 'NotAllowedError') {
    return '麦克风权限没有开启。允许访问后可以重试，打字不受影响。';
  }
  if (error instanceof DOMException && error.name === 'NotFoundError') {
    return '没有找到可用麦克风。你仍可直接打字。';
  }
  return '没能听清，再试一次。录音内容不会自动发送。';
}

interface TideChatSurfaceProps {
  /** 通话开始/结束。主区要据此决定能不能被二级界面收掉 —— 黑板在通话面板里。 */
  onVoiceCallChange?: (active: boolean) => void;
  /** 右侧「DARYA 当前工作」开着没有。默认收起，开关在顶栏。 */
  workbenchOpen?: boolean;
  onToggleWorkbench?: () => void;
  /** 黑板在对话区里怎么排。设置里选的，这里只按它摆。 */
  boardLayout?: DaryaBoardLayout;
  onBoardLayoutChange?: (layout: DaryaBoardLayout) => void;
  spaceMode?: DaryaSpaceMode;
  onSpaceModeChange?: (mode: DaryaSpaceMode) => void;
  /** 数字人形态：柿小吉或海星。设置里选的，这里只按它画。 */
  mascotForm?: DaryaMascotForm;
  mascotFormSetting?: DaryaMascotFormSetting;
  onMascotFormSettingChange?: (setting: DaryaMascotFormSetting) => void;
  onOpenMaterials: () => void;
  onOpenUnderstanding: () => void;
  onOpenSettings: () => void;
  onOpenTime: () => void;
  onOpenArtifact: (artifactId: number, versionId?: number | null) => void;
}

const actionKindCopy = {
  HANDOUT: '讲义',
  PRACTICAL_GUIDE: '实操',
  ASSESSMENT: '测试',
  CODE_LAB: '代码',
  PRESENTATION: 'PPT',
  MINDMAP: '思维导图',
  VIDEO: '教学视频',
  STORYBOOK: '教学绘本',
  DEMONSTRATE: '图解',
} as const;

const actionStatusCopy = {
  planned: '正在准备',
  preparing: '正在准备',
  reviewing: '正在检查',
  ready: '已准备好',
  failed: '暂时未完成',
} as const;

const evidenceStatusCopy = {
  SUPPORTED: '已核对',
  PARTIALLY_SUPPORTED: '部分可核对',
  CONFLICTING: '来源仍有冲突',
  UNVERIFIED: '暂未核实',
  NOT_SEARCHED: '本轮无需检索',
} as const;

const authorityCopy = {
  primary: '一手来源',
  authoritative: '权威机构',
  secondary: '可信二手来源',
  community: '社区线索',
  unknown: '待核对来源',
} as const;

function evidenceDateLabel(source: DaryaSearchGrounding['sources'][number]) {
  const raw = source.freshness.source_date || source.freshness.retrieved_at;
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) return source.freshness.source_date || '本轮读取';
  const label = new Intl.DateTimeFormat('zh-CN', {
    month: 'numeric',
    day: 'numeric',
    hour: source.freshness.source_date ? undefined : '2-digit',
    minute: source.freshness.source_date ? undefined : '2-digit',
  }).format(parsed);
  return source.freshness.source_date ? label : `${label} 核对`;
}

function DaryaEvidencePanel({ evidence }: { evidence: DaryaSearchGrounding }) {
  if (evidence.decision === 'SEARCH_NOT_NEEDED') return null;
  return (
    <section className={styles.evidencePanel} data-testid="darya-search-evidence" data-evidence-status={evidence.evidence_status}>
      <div className={styles.evidenceHeading}>
        <span><ShieldCheck size={14} aria-hidden="true" />资料依据</span>
        <small>{evidenceStatusCopy[evidence.evidence_status]}</small>
      </div>
      {evidence.sources.length > 0 ? (
        <div className={styles.evidenceSources}>
          {evidence.sources.map((source) => (
            <details className={styles.evidenceSource} key={source.source_id}>
              <summary>
                <span>{source.title}</span>
                <small>{authorityCopy[source.authority]} · {evidenceDateLabel(source)}</small>
              </summary>
              <p>{source.claim_provenance.evidence_span}</p>
              <div>
                <span>{source.publisher} · {source.trust_reason}</span>
                <a href={source.url} target="_blank" rel="noreferrer">查看原始来源 <ExternalLink size={12} aria-hidden="true" /></a>
              </div>
            </details>
          ))}
        </div>
      ) : (
        <p className={styles.evidenceEmpty}>这次没有取得可核对来源，因此未附链接，也没有把未核实内容写成确定事实。</p>
      )}
      {evidence.conflict.status === 'UNRESOLVED' && (
        <p className={styles.evidenceConflict}>两个说法仍未被可靠证据消解，DARYA 没有替你随机选边。</p>
      )}
      {evidence.conflict.status === 'RESOLVED_SCOPE_MISMATCH' && (
        <p className={styles.evidenceConflict}>这不是谁绝对对或错：两个说法讲的是不同适用范围。</p>
      )}
      <small className={styles.evidenceBoundary}>只用于当前对话的知识依据 · 不写入长期记忆或掌握度</small>
    </section>
  );
}

export function TideChatSurface({ onVoiceCallChange, workbenchOpen, onToggleWorkbench, boardLayout = 'split', onBoardLayoutChange, spaceMode = 'curve', onSpaceModeChange, mascotForm = 'persimmon', mascotFormSetting = 'auto', onMascotFormSettingChange, onOpenMaterials, onOpenUnderstanding, onOpenSettings, onOpenTime, onOpenArtifact }: TideChatSurfaceProps) {
  const messages = useTideStore((state) => state.messages);
  const draft = useTideStore((state) => state.draft);
  const loadState = useTideStore((state) => state.loadState);
  const streamState = useTideStore((state) => state.streamState);
  const error = useTideStore((state) => state.error);
  const setDraft = useTideStore((state) => state.setDraft);
  const send = useTideStore((state) => state.send);
  const retryLast = useTideStore((state) => state.retryLast);
  const abort = useTideStore((state) => state.abort);
  const advanceConversationAction = useTideStore((state) => state.advanceConversationAction);
  const hydrate = useTideStore((state) => state.hydrate);
  const userContextState = useTideStore((state) => state.userContextState);
  const userContextError = useTideStore((state) => state.userContextError);
  const uploadUserContextDocument = useTideStore((state) => state.uploadUserContextDocument);
  const ingestUserContextUrl = useTideStore((state) => state.ingestUserContextUrl);
  const researchJob = useTideStore((state) => state.researchJob);
  const researchState = useTideStore((state) => state.researchState);
  const researchError = useTideStore((state) => state.researchError);
  const researchCollapsed = useTideStore((state) => state.researchCollapsed);
  const setResearchCollapsed = useTideStore((state) => state.setResearchCollapsed);
  const startResearch = useTideStore((state) => state.startResearch);
  const refreshResearch = useTideStore((state) => state.refreshResearch);
  const retryResearch = useTideStore((state) => state.retryResearch);
  const createThread = useTideStore((state) => state.createThread);
  const setHistoryOpen = useTideStore((state) => state.setRailOpen);
  const setScrollTop = useTideStore((state) => state.setScrollTop);
  // 只有这一轮真的在浏览器里长出来过，才播动画；翻历史翻回来的旧消息一进来就是全文，
  // 否则往上滚一次，每一条都会重新"写"一遍，那才是真的假。
  const streamedMessageIds = useRef<Set<string>>(new Set());
  const momentStageRef = useRef<HTMLDivElement | null>(null);
  const stageRef = useRef<HTMLDivElement | null>(null);
  const boardPaneRef = useRef<HTMLDivElement | null>(null);
  const followStreamRef = useRef(true);
  /** 触摸上翻用的：上一指的 Y。空表示这一摸还没开始。 */
  const lastTouchYRef = useRef<number | null>(null);
  /** 上一次"跟着走"时内容有多高。高度没变就不动滚动条。 */
  const lastFollowedHeightRef = useRef(0);
  /** 这一会话里有没有真的走过一轮。没有就只当读者，别动他的滚动条。 */
  const liveTurnRef = useRef(false);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const contextFileRef = useRef<HTMLInputElement | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const retryAudioRef = useRef<Blob | null>(null);
  const voiceGenerationRef = useRef(0);
  const transcriptionControllerRef = useRef<AbortController | null>(null);
  const shouldTranscribeRecordingRef = useRef(false);
  const mountedRef = useRef(true);
  // 两条专属流程共用一个内联表单：网页资料只要一个地址，深度研究只要一个问题。
  const [composerTask, setComposerTask] = useState<'url-material' | 'research' | null>(null);
  const [taskValue, setTaskValue] = useState('');
  const [taskError, setTaskError] = useState<string | null>(null);
  const [voiceState, setVoiceState] = useState<VoiceState>('idle');
  const [voiceError, setVoiceError] = useState<string | null>(null);
  const moment = useMemo(() => projectTideMoment(messages), [messages]);
  const assistant = moment.currentAssistant;
  if (assistant?.status === 'streaming') streamedMessageIds.current.add(assistant.id);
  const revealEnabled = Boolean(assistant && streamedMessageIds.current.has(assistant.id));
  const centerText = moment.centerResponse || '';
  // 一轮一句一句地说；说完之后整段落定 —— 答案是要能回头重读的，不能说完就没了。
  const sentenceCadence = useSentenceCadence(centerText, {
    enabled: revealEnabled,
    concluded: Boolean(assistant && assistant.status !== 'streaming'),
  });
  const { visible: revealedCenter, revealing } = useGentleReveal(sentenceCadence.visible, {
    enabled: revealEnabled,
  });
  const showThinking = Boolean(assistant && assistant.status !== 'complete' && !revealedCenter);
  // 黑板上该是什么：最近的、还没被新板替换或明确换题的那一块。
  //
  // 原来只取"当前这一轮"的脚本，于是用户追问一句（哪怕说的是"板我看懂了"），
  // 黑板立刻从 DOM 里消失 —— 想回头看一眼刚才的读写对比也没有了。真正的黑板
  // 会一直挂着，直到有人写下新的一板；明确换题时从当前画面移开旧板。
  const blackboard = useMemo(() => activeBlackboard(messages), [messages]);
  const blackboardScript = blackboard?.script ?? null;
  // 只有这一会话里当场画出来的那一板才边讲边画；翻回来的直接给终态。
  const liveMessageIds = useTideStore((state) => state.liveMessageIds);
  const boardAnimate = Boolean(blackboard && liveMessageIds.includes(blackboard.ownerId));
  // DARYA 此刻在板子上讲的那一句。由黑板排出来，摆在板子上方 —— 板面只留粉笔。
  const [boardNarration, setBoardNarration] = useState<BoardNarration>({ saying: null, leaving: null });
  const [companionSpeechState, setCompanionSpeechState] = useState<'idle' | 'loading' | 'playing'>('idle');
  // 黑板占不满对话区，由设置里的「黑板排版」说了算；板子上那颗按钮是它的快捷开关。
  const boardFull = boardLayout === 'full' && Boolean(blackboardScript);
  const toggleBoardFull = useCallback(() => {
    onBoardLayoutChange?.(boardFull ? 'split' : 'full');
  }, [boardFull, onBoardLayoutChange]);
  // 占满模式下「回看全文」把文字层翻开；再点收回去。
  const [showFullText, setShowFullText] = useState(false);
  const [screenFront, setScreenFront] = useState(false);

  /**
   * 文字栏的宽度：按这块板子的宽高比预算，开讲后锁定。
   *
   * 板子的比例每块都不一样 —— 新板 16:10，改多了会变高到 1.1。分栏宽度写死的话，
   * 竖长的板子两侧会各空出约 100px，横板又拿不满。所以在板子到达之前，按它的比例
   * 和对话区的可视高度预算出板子该多宽，剩下的全给文字，收在 360–440px 之间。
   *
   * 这个宽度只随"换了哪块板"和"窗口大小"变，不随字幕、不随流式正文变 ——
   * 所以讲解全程文字栏和板子都不会跳。占满模式下文字栏是隐藏的，不用算。
   */
  const [textColumnWidth, setTextColumnWidth] = useState<number | null>(null);
  useLayoutEffect(() => {
    const stage = stageRef.current;
    const pane = boardPaneRef.current;
    if (!stage || !pane || !blackboardScript || boardFull) { setTextColumnWidth(null); return; }
    const fit = () => {
      if (!pane.clientHeight) return;
      const ratio = (blackboardScript.size?.w ?? 1600) / (blackboardScript.size?.h ?? 1000);
      const paneStyle = getComputedStyle(pane);
      const headerEl = pane.querySelector('[class*="boardHeader"]');
      const headerH = headerEl ? headerEl.getBoundingClientRect().height : 78;
      const padX = parseFloat(paneStyle.paddingLeft) + parseFloat(paneStyle.paddingRight);
      const availH = pane.clientHeight - parseFloat(paneStyle.paddingTop) - parseFloat(paneStyle.paddingBottom) - headerH;
      const availW = pane.clientWidth - padX;
      const boardW = Math.min(availW, availH * ratio);
      const width = Math.max(360, Math.min(440, stage.clientWidth - boardW - padX));
      setTextColumnWidth(Math.round(width));
    };
    fit();
    const observer = new ResizeObserver(fit);
    observer.observe(stage);
    observer.observe(pane);
    return () => observer.disconnect();
  }, [blackboardScript, boardFull]);
  const busy = streamState === 'connecting' || streamState === 'streaming';
  const uploadingContext = userContextState === 'uploading';
  const terminalProblem = moment.currentAssistant?.status === 'failed' || moment.currentAssistant?.status === 'stopped';
  // 历史轮次不再出现在主区 —— 整条对话在左侧栏里一览无余，中间这一栏只服务当前这一轮。

  /**
   * 盯着研究任务，直到它到终态。
   *
   * 任务在服务端跑几分钟，前端只负责如实显示进度。到 READY 或 FAILED 就停 ——
   * 一个已经结束的任务不需要继续轮询。轮询上限也设了，避免任何情况下无限拉。
   */
  useEffect(() => {
    if (!researchJob) return;
    if (researchJob.status === 'READY' || researchJob.status === 'PARTIAL' || researchJob.status === 'FAILED') return;
    let cancelled = false;
    let ticks = 0;
    const timer = window.setInterval(() => {
      if (cancelled) return;
      ticks += 1;
      if (ticks > 200) {
        window.clearInterval(timer);
        return;
      }
      void refreshResearch(researchJob.artifact_id);
    }, 3000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [researchJob, refreshResearch]);

  const submitComposerTask = useCallback(async () => {
    const value = taskValue.trim();
    if (!value) return;
    setTaskError(null);
    try {
      if (composerTask === 'url-material') {
        await ingestUserContextUrl(value);
      } else if (composerTask === 'research') {
        await startResearch(value);
      }
      setComposerTask(null);
      setTaskValue('');
    } catch (error) {
      setTaskError(error instanceof Error ? error.message : '这一步没有完成，请稍后再试。');
    }
  }, [composerTask, ingestUserContextUrl, startResearch, taskValue]);

  const releaseMicrophone = useCallback(() => {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    recorderRef.current = null;
    chunksRef.current = [];
  }, []);

  const invalidateVoiceRequest = useCallback(() => {
    voiceGenerationRef.current += 1;
    transcriptionControllerRef.current?.abort();
    transcriptionControllerRef.current = null;
    return voiceGenerationRef.current;
  }, []);

  const transcribe = useCallback(async (audio: Blob, recordingGeneration?: number) => {
    if (!mountedRef.current) return;
    const generation = recordingGeneration ?? invalidateVoiceRequest();
    if (generation !== voiceGenerationRef.current) return;
    const draftAtRequestStart = useTideStore.getState().draft;
    const controller = new AbortController();
    transcriptionControllerRef.current?.abort();
    transcriptionControllerRef.current = controller;
    retryAudioRef.current = audio;
    setVoiceState('transcribing');
    setVoiceError(null);
    try {
      const result = await tideChatApi.transcribeAudio(audio, controller.signal);
      if (!mountedRef.current || generation !== voiceGenerationRef.current) return;
      const currentDraft = useTideStore.getState().draft;
      if (currentDraft !== draftAtRequestStart) {
        retryAudioRef.current = null;
        setVoiceState('idle');
        return;
      }
      const transcript = result.text.trim();
      if (!transcript) {
        setVoiceError('没能听清，再试一次。');
        setVoiceState('error');
        return;
      }
      const trimmedDraft = currentDraft.trimEnd();
      setDraft(trimmedDraft ? `${trimmedDraft}\n${transcript}` : transcript);
      retryAudioRef.current = null;
      setVoiceState('idle');
      requestAnimationFrame(() => textareaRef.current?.focus());
    } catch (error) {
      if (!mountedRef.current || generation !== voiceGenerationRef.current || controller.signal.aborted) return;
      setVoiceError(voiceErrorMessage(error));
      setVoiceState('error');
    } finally {
      if (transcriptionControllerRef.current === controller) transcriptionControllerRef.current = null;
    }
  }, [invalidateVoiceRequest, setDraft]);

  const startRecording = useCallback(async () => {
    if (voiceState === 'requesting' || voiceState === 'transcribing') return;
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
      setVoiceError('当前浏览器不支持可靠录音。你仍可直接打字。');
      setVoiceState('error');
      return;
    }
    const generation = invalidateVoiceRequest();
    shouldTranscribeRecordingRef.current = true;
    setVoiceState('requesting');
    setVoiceError(null);
    retryAudioRef.current = null;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      if (!mountedRef.current || generation !== voiceGenerationRef.current) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      const recorder = new MediaRecorder(stream, preferredRecorderOptions());
      streamRef.current = stream;
      recorderRef.current = recorder;
      chunksRef.current = [];
      recorder.addEventListener('dataavailable', (event) => {
        if (event.data.size > 0) chunksRef.current.push(event.data);
      });
      recorder.addEventListener('stop', () => {
        const audio = new Blob(chunksRef.current, { type: recorder.mimeType || 'audio/webm' });
        const shouldTranscribe = shouldTranscribeRecordingRef.current
          && generation === voiceGenerationRef.current;
        releaseMicrophone();
        if (!mountedRef.current || !shouldTranscribe) return;
        if (audio.size === 0) {
          setVoiceError('没有录到声音，可以重试或直接打字。');
          setVoiceState('error');
          return;
        }
        void transcribe(audio, generation);
      }, { once: true });
      recorder.start(250);
      setVoiceState('recording');
    } catch (error) {
      releaseMicrophone();
      if (!mountedRef.current) return;
      setVoiceError(voiceErrorMessage(error));
      setVoiceState('error');
    }
  }, [invalidateVoiceRequest, releaseMicrophone, transcribe, voiceState]);

  const stopRecording = useCallback(() => {
    if (recorderRef.current?.state === 'recording') recorderRef.current.stop();
  }, []);

  const cancelVoice = useCallback(() => {
    shouldTranscribeRecordingRef.current = false;
    invalidateVoiceRequest();
    const recorder = recorderRef.current;
    if (recorder?.state === 'recording') recorder.stop();
    releaseMicrophone();
    retryAudioRef.current = null;
    setVoiceError(null);
    setVoiceState('idle');
    requestAnimationFrame(() => textareaRef.current?.focus());
  }, [invalidateVoiceRequest, releaseMicrophone]);

  useEffect(() => {
    const element = textareaRef.current;
    if (!element) return;
    element.style.height = 'auto';
    element.style.height = `${Math.min(element.scrollHeight, 144)}px`;
  }, [draft]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      shouldTranscribeRecordingRef.current = false;
      invalidateVoiceRequest();
      if (recorderRef.current?.state === 'recording') recorderRef.current.stop();
      releaseMicrophone();
      retryAudioRef.current = null;
    };
  }, [invalidateVoiceRequest, releaseMicrophone]);

  // 把上次读到的地方还回去 —— 只在真的"换了地方"（首次载入、换线程）时做一次。
  //
  // scrollTop 本身只是滚动条的镜像（唯一写它的是 onScroll）。原来拿它当依赖，
  // 于是跟随刚写到底部、内容又长高了一截的时候，它会把旧位置写回去：位置一降，
  // onScroll 就判成"用户往上翻了"，从此这一轮再也不跟随。上一版板子停在画面外，根子在这。
  const restoredThreadRef = useRef<string | null>(null);
  useEffect(() => {
    const element = momentStageRef.current;
    if (!element || loadState !== 'ready') return;
    const threadId = useTideStore.getState().currentThreadId;
    if (restoredThreadRef.current === threadId) return;
    restoredThreadRef.current = threadId;
    element.scrollTop = useTideStore.getState().scrollTop;
  }, [loadState]);

  // 换线程/重新载入时解除"跟随"：这之后长出来的是历史，不是新内容。
  useEffect(() => {
    if (loadState === 'loading') liveTurnRef.current = false;
    if (busy) liveTurnRef.current = true;
  }, [loadState, busy]);

  /**
   * 内容长高了就跟着到底部（只要用户没有自己往上翻）。
   *
   * 判据是"高度变了"，不是某一个状态位。原来只在 messages/busy 变化时跟一次，跟不上
   * 内容真正的长法：正文是一句一句"长"出来的（逐句节奏是组件内部的状态，不动 messages），
   * 黑板是整块落下来的（落下来那一下 busy 已经结束了）。跟不住那一下，板子就停在画面外面。
   *
   * 两个闸门缺一不可：这一会话里真的有过一轮（不然翻历史时内容一长高就把人拽到底部，
   * 存的阅读位置就白存了），以及用户没有自己往上翻。高度没变就不动滚动条，
   * 所以打开面板之类的重渲染也不会把用户拽回去。
   */
  useLayoutEffect(() => {
    const stage = momentStageRef.current;
    if (!stage) return;
    if (!liveTurnRef.current || !followStreamRef.current) return;
    if (stage.scrollHeight === lastFollowedHeightRef.current) return;
    lastFollowedHeightRef.current = stage.scrollHeight;
    stage.scrollTop = stage.scrollHeight;
  });

  // DARYA 的手 · 视觉绘本：组件按需注入，通道未配置或加载失败必须让用户看见
  const authToken = useAuthStore((state) => state.token);
  const [storybookState, setStorybookState] = useState<'idle' | 'opening' | 'ready' | 'unavailable' | 'failed' | 'saved' | 'save-failed'>('idle');
  const [storybookNotice, setStorybookNotice] = useState<string | null>(null);
  // 绘本组件按「生成完成」派发事件，但产物只有落进正式产物权威才算真的存在。
  const storybookAssetPathRef = useRef('/api/darya/storybook/asset');

  // DARYA 的手 · 视觉绘本组件（Shadow DOM；模型凭证留在服务端，浏览器只走同源代理）
  const openStorybook = useCallback(async () => {
    if (storybookState === 'opening') return;
    setStorybookState('opening');
    setStorybookNotice('正在打开教学绘本…');
    try {
      await loadStorybookWidget();
      const config = await getData<{
        configured?: boolean;
        chatPath?: string;
        imagePath?: string;
        assetPath?: string;
      }>('/api/darya/storybook/config');
      if (!config?.configured) {
        setStorybookState('unavailable');
        setStorybookNotice('教学绘本的模型通道还没有配置，暂时无法生成。请联系管理员配置后再试。');
        return;
      }

      let el = document.querySelector('darya-storybook') as (HTMLElement & { open?: () => void }) | null;
      if (!el) {
        el = document.createElement('darya-storybook') as HTMLElement & { open?: () => void };
        el.setAttribute('mode', 'link');
        // 宿主已有自己的入口，隐藏组件自带入口，避免页面上多出一个悬浮按钮
        el.setAttribute('entry-hidden', '');
        el.style.display = 'none';
        document.body.appendChild(el);
      }
      // 绘本画风跟随当前产品主题（柿子暖色 / Ocean 海蓝）
      const theme = document.querySelector('.tide-preview-root')?.getAttribute('data-darya-theme') === 'ocean' ? 'ocean' : 'kaki';
      el.setAttribute('data-theme', theme);
      storybookAssetPathRef.current = config.assetPath || '/api/darya/storybook/asset';
      el.setAttribute('data-chat-url', config.chatPath || '');
      el.setAttribute('data-image-url', config.imagePath || '');
      el.setAttribute('proxy-base', `${config.assetPath || '/api/darya/storybook/asset'}?url=`);
      el.setAttribute('data-auth-token', authToken || '');

      if (typeof el.open !== 'function') {
        setStorybookState('failed');
        setStorybookNotice('教学绘本组件没有正常加载，请刷新页面后重试。');
        return;
      }
      el.open();
      setStorybookState('ready');
      setStorybookNotice(null);
    } catch (reason) {
      setStorybookState('failed');
      setStorybookNotice(
        reason instanceof Error && reason.message
          ? `${reason.message}，请稍后重试。`
          : '教学绘本没能打开，请稍后重试。',
      );
    }
  }, [authToken, storybookState]);

  // DARYA 的手 · PPT：读取当前主题快照 + 学习者水平，生成后直接打开产物
  const currentThreadId = useTideStore((state) => state.currentThreadId);
  const userId = useAuthStore((state) => state.user?.userId);

  // 绘本组件生成完只会派发一个事件，产物在浏览器里没有别的拥有者：关掉弹层
  // 就再也找不回来。这里接住它，存进正式产物权威，失败时可重新保存。
  const storybookBookRef = useRef<unknown>(null);
  const currentThreadIdRef = useRef<string | null>(currentThreadId);
  currentThreadIdRef.current = currentThreadId;

  const saveStorybookBook = useCallback(async () => {
    const artifact = storybookBookRef.current;
    if (!artifact) return;
    setStorybookState('ready');
    setStorybookNotice('绘本已生成，正在保存到「我的资料」…');
    try {
      const outcome = await persistGeneratedPictureBook({
        artifact,
        assetPath: storybookAssetPathRef.current,
        threadId: currentThreadIdRef.current,
      });
      setStorybookState('saved');
      setStorybookNotice(
        outcome.remoteImages
          ? `《${outcome.title}》已保存到「我的资料」（${outcome.pageCount} 页）。其中 ${outcome.remoteImages} 张插图只能保留原图链接，原图失效时会显示不出来。`
          : `《${outcome.title}》已保存到「我的资料」（${outcome.pageCount} 页），关掉绘本也能找回来。`,
      );
    } catch (reason) {
      setStorybookState('save-failed');
      setStorybookNotice(
        reason instanceof Error && reason.message
          ? `绘本已经生成，但没能保存：${reason.message}`
          : '绘本已经生成，但没能保存到「我的资料」。',
      );
    }
  }, []);

  useEffect(() => {
    const onGenerated = (event: Event) => {
      const detail = (event as CustomEvent<{ artifact?: unknown }>).detail || {};
      if (!detail.artifact) return;
      storybookBookRef.current = detail.artifact;
      void saveStorybookBook();
    };
    document.addEventListener('darya-pb:generated', onGenerated);
    return () => document.removeEventListener('darya-pb:generated', onGenerated);
  }, [saveStorybookBook]);

  const [voiceCallOpen, setVoiceCallOpen] = useState(false);
  const [pptPanelOpen, setPptPanelOpen] = useState(false);
  const [pptTopic, setPptTopic] = useState('');
  const [pptState, setPptState] = useState<'idle' | 'generating'>('idle');
  const [pptError, setPptError] = useState('');
  const [capabilityOpen, setCapabilityOpen] = useState(false);

  const openPptPanel = useCallback(() => {
    const lastUser = [...messages].reverse().find((m) => m.role === 'user');
    setPptTopic((lastUser?.content || '').replace(/\s+/g, ' ').trim().slice(0, 80) || moment.userCue || '');
    setPptError('');
    setPptPanelOpen(true);
  }, [messages, moment.userCue]);

  const generatePpt = useCallback(async () => {
    const topic = pptTopic.trim();
    if (!topic || !currentThreadId || pptState === 'generating') return;
    const theme = document.querySelector('.tide-preview-root')?.getAttribute('data-darya-theme') === 'ocean' ? 'ocean' : 'kaki';
    setPptState('generating'); setPptError('');
    try {
      let level = '';
      if (userId) {
        try { level = (await getNormalizedLearnerState(userId)).knowledge_level || ''; } catch { level = ''; }
      }
      const artifact = await tideChatApi.generatePresentation({
        thread_id: currentThreadId, topic, theme, level, confirmed: true,
      });
      setPptPanelOpen(false);
      onOpenArtifact(artifact.artifact_id, null);
    } catch (reason) {
      setPptError(reason instanceof Error ? reason.message : '生成没有完成，请稍后再试。');
    } finally {
      setPptState('idle');
    }
  }, [pptTopic, currentThreadId, pptState, userId, onOpenArtifact]);

  const submit = () => {
    if (!draft.trim() || busy) return;
    followStreamRef.current = true;
    void send();
  };

  const openDeliveredArtifact = (artifact: { id?: number | string; title?: string } | null | undefined) => {
    const id = Number(artifact?.id);
    if (Number.isFinite(id) && id > 0) onOpenArtifact(id, null);
  };

  return (
    <section className={styles.surface} aria-label="DARYA 对话中心" data-testid="tide-chat-surface">
      <header className={styles.heading}>
        {boardFull && (
          <nav className={styles.fullNav} aria-label="学习导航">
            <div className={styles.fullBrand}>
              <img src="/assets/brand/kaki-logo-mark.png" alt="" aria-hidden="true" />
              <span>DARYA</span>
            </div>
            <span className={styles.fullNavActive} aria-current="page">学习</span>
            <button type="button" className={styles.fullNavLink} onClick={() => setHistoryOpen(true)}>历史</button>
            <button type="button" className={styles.fullNavLink} onClick={onOpenMaterials}>我的资料</button>
            <button
              type="button"
              className={styles.fullNewThread}
              onClick={() => void createThread()}
              disabled={loadState === 'loading' || busy}
              aria-label="新的对话"
              title="新的对话"
            >
              <Plus size={16} aria-hidden="true" />
            </button>
          </nav>
        )}
        {/* 品牌已经搬去左侧栏 —— 顶栏那一行原来挤着品牌加六个入口，
            腾出左边之后，这里只留跟"现在这件事"有关的东西。 */}
        {researchJob && (
          // 研究任务挂在标题栏上。它是后台在做的一件事，不该占对话区的地方 ——
          // 之前它压在输入框上方，一出现就把黑板挤矮一截，演示时看得最明显。
          // 收起来只留一颗胶囊说清是哪件事、到哪一步；点开才在浮层里给报告和来源。
          <div className={styles.researchChipWrap}>
            <button
              type="button"
              className={styles.researchChip}
              data-status={researchJob.status}
              aria-expanded={!researchCollapsed}
              onClick={() => setResearchCollapsed(!researchCollapsed)}
              data-testid="darya-research-collapsed"
            >
              <Sparkles size={13} strokeWidth={1.7} aria-hidden="true" />
              <span>{researchJob.title || researchJob.question}</span>
              <em>{researchStatusLabel(researchJob.status)}</em>
            </button>
            {!researchCollapsed && (
              <section className={styles.researchPopover} data-testid="darya-research-status" data-status={researchJob.status}>
                <div className={styles.taskStripHead}>
                  <strong>深度研究 · {researchJob.title || researchJob.question}</strong>
                  <button type="button" onClick={() => setResearchCollapsed(true)} aria-label="收起研究任务">×</button>
                </div>
                <p className={styles.taskStripStage}>{researchJob.stage_copy || researchJob.summary}</p>
                {researchJob.status === 'FAILED' && researchJob.error && (
                  <p className={styles.taskStripError} data-testid="darya-research-error">{researchJob.error}</p>
                )}
                {(researchJob.status === 'READY' || researchJob.status === 'PARTIAL') && (
                  <div className={styles.taskStripResult} data-testid="darya-research-report">
                    <p className={styles.taskStripMeta}>
                      {researchJob.status === 'PARTIAL'
                        ? `阶段分析 · 已取回来源 ${researchJob.source_refs.length} 条 · 正式报告待核对`
                        : `来源 ${researchJob.source_refs.length} 条 · 引用 ${researchJob.citation_report?.cited_count ?? 0} 处`}
                      {researchJob.status === 'READY' && researchJob.content_review?.measured ? ' · 已通过内容检查' : ''}
                    </p>
                    <SafeMarkdown source={researchJob.status === 'PARTIAL' ? researchJob.working_result : researchJob.report} className={styles.centerMarkdown} />
                    {researchJob.source_refs.length > 0 && (
                      <ol className={styles.taskStripSources}>
                        {researchJob.source_refs.map((ref, index) => (
                          <li key={`${ref.url || ref.title}-${index}`}>
                            {ref.url
                              ? <a href={ref.url} target="_blank" rel="noreferrer noopener">{ref.title || ref.url}</a>
                              : (ref.title || '未命名来源')}
                          </li>
                        ))}
                      </ol>
                    )}
                  </div>
                )}
                {(researchJob.status === 'PARTIAL' || researchJob.status === 'FAILED') && (
                  <button type="button" className={styles.researchRetry} onClick={() => void retryResearch(researchJob.artifact_id)} data-testid="darya-research-retry">
                    继续补证据
                  </button>
                )}
                {researchError && <p className={styles.taskStripError}>{researchError}</p>}
              </section>
            )}
          </div>
        )}
        <div className={styles.utilityActions}>
          {/* 钟放在最左边：它是"你的状态"，和右边那排"你要去哪"不是一类东西。 */}
          <StudyClock onOpen={onOpenTime} />
          <DaryaAnnouncements className={styles.historyButton}/>
          {!boardFull && <button type="button" className={styles.historyButton} onClick={() => setHistoryOpen(true)}><History size={15} strokeWidth={1.6} aria-hidden="true" /><span>历史</span></button>}
          {!boardFull && <button type="button" className={styles.historyButton} onClick={onOpenMaterials} data-testid="darya-open-materials"><FolderOpen size={15} aria-hidden="true" /><span>我的资料</span></button>}
          <button type="button" className={styles.historyButton} onClick={onOpenUnderstanding} aria-label="DARYA 眼中的我" data-testid="darya-open-understanding"><UserRound size={15} aria-hidden="true" /><span>DARYA 眼中的我</span></button>
          <button type="button" className={styles.historyButton} onClick={onOpenSettings} aria-label="设置" data-testid="darya-open-settings"><Settings size={15} aria-hidden="true" /><span>设置</span></button>
          {/* 右侧工作台默认收起，所以得有个地方把它叫出来。 */}
          <button
            type="button"
            className={styles.historyButton}
            aria-pressed={Boolean(workbenchOpen)}
            aria-label="当前工作"
            onClick={onToggleWorkbench}
            data-testid="darya-toggle-workbench"
          >
            <PanelRight size={15} aria-hidden="true" /><span>当前工作</span>
          </button>
        </div>
      </header>

      <div
        ref={stageRef}
        className={styles.momentStage}
        data-with-board={blackboardScript ? 'true' : 'false'}
        data-board-full={boardFull ? 'true' : 'false'}
        data-text-overlay={boardFull && showFullText ? 'true' : 'false'}
        style={textColumnWidth ? { gridTemplateColumns: `${textColumnWidth}px minmax(0, 1fr)` } : undefined}
      >
      {/* 有黑板的时候对话区一分为二：左边文字自己滚，右边黑板常驻。
          这样正常对话时正文不会被黑板顶出画面 —— 之前黑板占满整个对话区，
          读 DARYA 说什么必须往上滚，那不是设计，是取舍没有做完。 */}
      {/* 占满模式下「回看全文」把文字层翻开盖在板子上；这条收起钮跟着浮层走。 */}
      {boardFull && showFullText && (
        <button
          type="button"
          className={styles.textOverlayClose}
          onClick={() => setShowFullText(false)}
          data-testid="darya-text-overlay-close"
        >
          收起文字
        </button>
      )}
      <div
        ref={momentStageRef}
        className={styles.textPane}
        aria-live="polite"
        data-testid="tide-current-moment"
        onWheel={(event) => {
          // 「跟不跟随」只由一件事决定：用户有没有自己往上翻。而"自己往上翻"只有
          // 滚轮/触摸/键盘这类真实输入能证明 —— 滚动位置的变化证明不了：
          // 内容自己长高会把位置顶远，程序化滚动会把它拽回来，发送还会让内容缩短、
          // 位置被浏览器夹回去。这三种都曾被当成"用户翻走了"，于是这一轮再也不跟随。
          if (event.deltaY < 0) followStreamRef.current = false;
        }}
        onTouchMove={(event) => {
          const touch = event.touches[0];
          if (!touch) return;
          if (!lastTouchYRef.current) { lastTouchYRef.current = touch.clientY; return; }
          if (touch.clientY > lastTouchYRef.current) followStreamRef.current = false;
          lastTouchYRef.current = touch.clientY;
        }}
        onTouchEnd={() => { lastTouchYRef.current = null; }}
        onKeyDown={(event) => {
          if (['ArrowUp', 'PageUp', 'Home'].includes(event.key)) followStreamRef.current = false;
          if (['ArrowDown', 'PageDown', 'End'].includes(event.key)) followStreamRef.current = true;
        }}
        onScroll={(event) => {
          const element = event.currentTarget;
          // 滚回底部就是想继续跟。这一条对触摸和键盘同样成立。
          if (element.scrollHeight - element.scrollTop - element.clientHeight < 220) {
            followStreamRef.current = true;
          }
          setScrollTop(element.scrollTop);
        }}
      >
        {loadState === 'loading' && messages.length === 0 ? (
          <div className={styles.state} role="status">
            <LoaderCircle className={styles.spinner} size={20} aria-hidden="true" />
            <strong>正在接回当前对话</strong>
            <p>已有记录仍由原有服务保存。</p>
          </div>
        ) : loadState === 'error' && messages.length === 0 ? (
          <div className={styles.state} role="alert">
            <CircleAlert size={20} aria-hidden="true" />
            <strong>连接暂时中断</strong>
            <p>{error}</p>
            <button type="button" onClick={() => void hydrate(true)}>
              <RefreshCcw size={14} aria-hidden="true" />重新连接
            </button>
          </div>
        ) : messages.length === 0 ? (
          <div className={styles.fresh} data-testid="tide-empty-state">
            <span className={styles.place}>岸边 · 此刻</span>
            <h2>从一个正在发生的问题开始。</h2>
            <p>我会直接回应你，需要展开的材料会放在右侧。</p>
          </div>
        ) : (
          <div className={styles.exchange}>
            {/* 历史轮次不再进主区：整条对话在左侧栏里，中间这一栏只放当前这一轮。 */}
            {moment.currentUser && (
              <p className={styles.userCue} data-testid="tide-message-user">
                <span>我说</span>
                {moment.userCue}
              </p>
            )}

            {moment.currentAssistant && (
              <article
                className={styles.currentResponse}
                data-message-status={moment.currentAssistant.status}
                data-testid="tide-message-assistant"
              >
                {moment.currentAssistant.status !== 'complete' && (
                  <div className={styles.responseMeta}>
                  {moment.currentAssistant.searchStatus && <small data-testid="darya-search-status">{moment.currentAssistant.searchStatus}</small>}
                  {moment.currentAssistant.status === 'streaming' && (assistant?.progressTrail?.length ?? 0) === 0 && (
                    <small data-testid="darya-progress">正在形成</small>
                  )}
                  {moment.currentAssistant.status === 'stopped' && <small>已停止</small>}
                  {moment.currentAssistant.status === 'failed' && <small>未完成</small>}
                  </div>
                )}
                {(assistant?.progressTrail?.length ?? 0) > 0 && (
                  <ThinkingTrace
                    stages={assistant?.progressTrail ?? []}
                    settled={Boolean(revealedCenter)}
                  />
                )}
                {sentenceCadence.narrating ? (
                  // 一句一句说：上一句一边淡出，下一句一边长出来，两层叠在同一格里，
                  // 交接处不空一拍，眼睛也始终只跟着一处。
                  <div
                    className={styles.sentenceStage}
                    data-testid="darya-sentence-stage"
                    // 给验收脚本看的：这一刻是不是在一句一句地说、这一句有多少字、
                    // 已经露出来多少、上一句是不是还在淡出
                    data-speaking={sentenceCadence.narrating ? 'true' : 'false'}
                    data-visible-length={sentenceCadence.visible.length}
                    data-revealed-length={revealedCenter.length}
                    data-handoff={sentenceCadence.leaving ? 'true' : 'false'}
                  >
                    {sentenceCadence.leaving && (
                      <SafeMarkdown
                        source={sentenceCadence.leaving}
                        className={`${styles.centerMarkdown} ${styles.sentenceLeaving}`}
                      />
                    )}
                    {revealedCenter && (
                      <SafeMarkdown source={revealedCenter} className={styles.centerMarkdown} />
                    )}
                  </div>
                ) : (
                  revealedCenter && (
                    <SafeMarkdown source={revealedCenter} className={styles.centerMarkdown} />
                  )
                )}
                {revealing && <RevealingCaret />}
                {!revealedCenter && blackboardScript && !showThinking && (
                  <SafeMarkdown source="这一轮我把要点写在黑板上。" className={styles.centerMarkdown} />
                )}
                {!revealedCenter && !blackboardScript && !showThinking && !busy && (
                  <SafeMarkdown source="这次回答没有留下可显示内容。" className={styles.centerMarkdown} />
                )}
                {moment.currentAssistant.artifact?.id != null && !moment.currentAssistant.conversationAction && (
                  <button type="button" className={styles.deliveredArtifact} onClick={() => openDeliveredArtifact(moment.currentAssistant?.artifact)}>
                    <BookOpen size={13} aria-hidden="true" />打开「{moment.currentAssistant.artifact.title || '这份资料'}」
                  </button>
                )}
                {moment.currentAssistant.searchEvidence && (
                  <DaryaEvidencePanel evidence={moment.currentAssistant.searchEvidence} />
                )}
                {moment.currentAssistant.conversationAction?.kind !== 'DEMONSTRATE' && moment.currentAssistant.conversationAction && (() => {
                  const action = moment.currentAssistant.conversationAction;
                  return (
                    <section className={styles.inlineArtifact} data-status={action.status} data-testid="darya-inline-artifact">
                      <div className={styles.inlineArtifactHead}>
                        <span>{actionKindCopy[action.kind]}</span>
                        <small>{actionStatusCopy[action.status]}</small>
                      </div>
                      <strong>{action.artifact?.title || (action.status === 'failed'
                        ? `${actionKindCopy[action.kind]}${action.working_result?.body ? '初稿已保留，待完善' : '暂时未完成'}`
                        : action.kind === 'STORYBOOK' ? '打开视觉绘本创作器' : `${actionKindCopy[action.kind]}正在形成`)}</strong>
                      <p>{action.reason}</p>
                      {(action.status === 'planned' || action.status === 'preparing') && <p>生成仍在进行。页面会继续查询服务端状态，刷新后也可接续。</p>}
                      <div className={styles.inlineArtifactActions}>
                        <button type="button" disabled={action.status !== 'ready' || (!action.artifact && action.kind !== 'STORYBOOK')} onClick={() => action.kind === 'STORYBOOK' ? openStorybook() : action.artifact && onOpenArtifact(action.artifact.artifact_id, action.artifact.version_id)}>
                          {action.kind === 'ASSESSMENT' || action.kind === 'CODE_LAB' || action.kind === 'STORYBOOK' ? '开始' : '打开'}
                        </button>
                        {action.status === 'failed' && <button type="button" onClick={() => void advanceConversationAction(action).catch(() => undefined)}>重试准备</button>}
                      </div>
                      {action.error_message && action.status === 'failed' && <p className={styles.inlineArtifactError}>{action.error_message}</p>}
                      {action.status === 'failed' && action.working_result?.body && (
                        <div className={styles.inlineArtifactDraft} data-testid="darya-action-working-result">
                          <strong>本轮已保留的初稿</strong>
                          <SafeMarkdown source={action.working_result.body} className={styles.centerMarkdown} />
                        </div>
                      )}
                      {action.trace.length > 0 && (
                        <details className={styles.turnTrace} data-testid="darya-turn-trace">
                          <summary>本次协作 · {action.trace.length} 步已完成</summary>
                          <ol>{action.trace.map((step, index) => <li key={`${step.label}-${index}`}><span>{step.label}</span><p>{step.detail}</p>{step.technical_label && <small>{step.technical_label}</small>}</li>)}</ol>
                        </details>
                      )}
                    </section>
                  );
                })()}
                {terminalProblem && (
                  <button type="button" className={styles.retry} onClick={() => void retryLast()}>
                    <RefreshCcw size={14} aria-hidden="true" />重试这个问题
                  </button>
                )}
              </article>
            )}

            {error && streamState === 'error' && (
              <div className={styles.streamError} role="alert" data-testid="tide-stream-error">
                <CircleAlert size={15} aria-hidden="true" />
                <span>{error}</span>
              </div>
            )}
          </div>
        )}
      </div>

      {/* 黑板自己的 pane：文字在左边滚它的，板子和逐句讲解在这里常驻。
          字幕条高度固定、永远占位 —— 板子的尺寸就不再跟着字幕一跳一跳。 */}
      {blackboardScript && (
        <div ref={boardPaneRef} className={styles.boardPane} data-full={boardFull ? 'true' : 'false'} data-testid="darya-board-pane">
          <div className={styles.boardHeader}>
            <div className={styles.boardCaptionSlot}>
              <NarrationCaption {...boardNarration} />
            </div>
            {boardFull && (
              <div className={styles.spaceModeSwitch} role="group" aria-label="学习空间形态" data-testid="darya-space-mode-switch">
                <button type="button" aria-pressed={spaceMode === 'curve'} onClick={() => onSpaceModeChange?.('curve')} data-testid="darya-space-curve">曲面</button>
                <button type="button" aria-pressed={spaceMode === 'fold'} onClick={() => onSpaceModeChange?.('fold')} data-testid="darya-space-fold">折叠 35°</button>
              </div>
            )}
            {boardFull && (
              <button
                type="button"
                className={styles.boardPill}
                onClick={() => setShowFullText((value) => !value)}
                aria-pressed={showFullText}
                data-testid="darya-board-review-text"
              >
                {showFullText ? '收起文字' : '回看全文'}
              </button>
            )}
          </div>
          <div className={styles.boardSlot} data-front={screenFront ? 'screen' : 'blackboard'} data-space-mode={spaceMode}>
            {boardFull && (
              <div className={styles.learningShell} aria-hidden="true" data-testid="darya-learning-shell" />
            )}
            {boardFull && (
              <BoardSideScreen
                userId={userId}
                narration={boardNarration.saying?.text ?? ''}
                assistantResponse={centerText}
                action={moment.currentAssistant?.conversationAction}
                front={screenFront}
                spaceMode={spaceMode}
                onSpeechStateChange={setCompanionSpeechState}
              />
            )}
            {boardFull && <BoardCompanion form={mascotForm} formSetting={mascotFormSetting} onFormSettingChange={onMascotFormSettingChange} caption={boardNarration.saying?.text ?? ''} screenFront={screenFront} spaceMode={spaceMode} speechState={companionSpeechState} />}
            <div
              className={styles.boardStage}
              // 板子按脚本自己的比例画；能放多大由这块 pane 决定（见 .boardPane）
              style={{ aspectRatio: `${blackboardScript.size?.w ?? 1600} / ${blackboardScript.size?.h ?? 1000}` }}
              data-testid="darya-blackboard"
              data-front={screenFront ? 'false' : 'true'}
            >
              <Blackboard
                script={blackboardScript}
                animate={boardAnimate}
                onNarrationChange={setBoardNarration}
              />
              {/* 放大/还原键就在板子右上角：同屏时放大到占满对话区，占满时一键还原。 */}
              {boardFull && (
                <button
                  type="button"
                  className={styles.boardSwap}
                  onClick={() => setScreenFront((value) => !value)}
                  title={screenFront ? '让黑板回到正面' : '让辅助屏正面展示'}
                  aria-label={screenFront ? '让黑板回到正面' : '让辅助屏正面展示'}
                  aria-pressed={screenFront}
                  data-testid="darya-board-swap-surface"
                >
                  <Repeat2 size={14} aria-hidden="true" />
                </button>
              )}
              <button
                type="button"
                className={styles.boardZoom}
                onClick={toggleBoardFull}
                title={boardFull ? '回到同屏' : '放大到占满对话区'}
                aria-pressed={boardFull}
                data-testid="darya-board-zoom"
              >
                {boardFull ? <Minimize2 size={14} aria-hidden="true" /> : <Maximize2 size={14} aria-hidden="true" />}
              </button>
            </div>
          </div>
        </div>
      )}
      </div>

      <div className={styles.composerRegion}>
        {composerTask && (
          <section className={styles.taskStrip} data-testid="darya-composer-task" data-task={composerTask}>
            <div className={styles.taskStripHead}>
              <strong>{composerTask === 'url-material' ? '把网页读成资料' : '开一个深度研究'}</strong>
              <button type="button" onClick={() => { setComposerTask(null); setTaskError(null); }} aria-label="取消">×</button>
            </div>
            <p className={styles.taskStripStage}>
              {composerTask === 'url-material'
                ? '贴一个 http/https 网页地址。DARYA 会抓正文、记下来源，之后生成内容时会引用它。'
                : '写清要研究什么。DARYA 会分几轮检索、只按来源写报告，每条结论带编号出处。'}
            </p>
            <div className={styles.taskStripForm}>
              <input
                type="text"
                value={taskValue}
                onChange={(event) => setTaskValue(event.target.value)}
                onKeyDown={(event) => { if (event.key === 'Enter') { event.preventDefault(); void submitComposerTask(); } }}
                placeholder={composerTask === 'url-material' ? 'https://…' : '例如：为什么天空是蓝色的'}
                aria-label={composerTask === 'url-material' ? '网页地址' : '研究问题'}
                data-testid="darya-composer-task-input"
              />
              <button
                type="button"
                onClick={() => void submitComposerTask()}
                disabled={!taskValue.trim() || userContextState === 'uploading' || researchState === 'starting'}
                data-testid="darya-composer-task-submit"
              >
                {composerTask === 'url-material' ? '导入' : '开始研究'}
              </button>
            </div>
            {taskError && <p className={styles.taskStripError} data-testid="darya-composer-task-error">{taskError}</p>}
          </section>
        )}
        <div className={styles.composer} data-testid="tide-composer">
          {voiceCallOpen && (
            <VoiceCallPanel onEnd={() => { setVoiceCallOpen(false); onVoiceCallChange?.(false); }} />
          )}
          {capabilityOpen && !voiceCallOpen && (
            <div className={styles.capabilityPanel} data-testid="darya-capability-panel" role="dialog" aria-label="生成中心">
              <div className={styles.capabilityHead}>
                <strong><Sparkles size={14} aria-hidden="true" /> 生成中心</strong>
                <small>选一条能力，补全「」里的内容后发送，DARYA 会调对应智能体生成</small>
                <button type="button" className={styles.capabilityClose} onClick={() => setCapabilityOpen(false)} aria-label="关闭生成中心">×</button>
              </div>
              <div className={styles.capabilityGroups}>
                {CAPABILITY_GROUPS.map((group) => (
                  <section key={group.group} className={styles.capabilityGroup}>
                    <h4>{group.group}</h4>
                    <div className={styles.capabilityItems}>
                      {group.items.map((item) => (
                        <button
                          key={item.key}
                          type="button"
                          className={styles.capabilityItem}
                          data-testid={`darya-capability-${item.key}`}
                          onClick={() => {
                            if (item.direct === 'storybook') {
                              setCapabilityOpen(false);
                              openStorybook();
                              return;
                            }
                            if (item.direct === 'voice-call') {
                              setCapabilityOpen(false);
                              setVoiceCallOpen(true);
                              onVoiceCallChange?.(true);
                              return;
                            }
                            if (item.direct === 'url-material' || item.direct === 'research') {
                              setCapabilityOpen(false);
                              setComposerTask(item.direct);
                              setTaskValue('');
                              setTaskError(null);
                              return;
                            }
                            setDraft(item.command);
                            setCapabilityOpen(false);
                            textareaRef.current?.focus();
                          }}
                        >
                          <strong>{item.label}</strong>
                          <small>{item.desc}</small>
                        </button>
                      ))}
                    </div>
                  </section>
                ))}
              </div>
            </div>
          )}
          {pptPanelOpen && (
            <div className={styles.pptPanel} data-testid="darya-ppt-panel">
              <div className={styles.pptPanelHead}>
                <strong>把话题做成 PPT</strong>
                <small>按当前主题生成 · 可导出可编辑 PPTX</small>
              </div>
              <input
                value={pptTopic}
                maxLength={80}
                placeholder="PPT 主题"
                onChange={(event) => setPptTopic(event.target.value)}
                data-testid="darya-ppt-topic"
              />
              {pptError && <p className={styles.pptError} role="alert">{pptError}</p>}
              <div className={styles.pptActions}>
                <button type="button" onClick={() => setPptPanelOpen(false)}>取消</button>
                <button
                  type="button"
                  disabled={!pptTopic.trim() || pptState === 'generating'}
                  onClick={() => void generatePpt()}
                  data-testid="darya-ppt-generate"
                >
                  {pptState === 'generating' ? '生成中…' : '生成 PPT'}
                </button>
              </div>
            </div>
          )}
          {storybookNotice && (
            <p
              className={styles.storybookNotice}
              data-kind={storybookState === 'opening' ? 'progress' : storybookState === 'saved' ? 'saved' : 'error'}
              role={storybookState === 'opening' || storybookState === 'saved' ? 'status' : 'alert'}
              data-testid="darya-storybook-notice"
            >
              {storybookState === 'saved'
                ? <FolderOpen size={13} aria-hidden="true" />
                : <CircleAlert size={13} aria-hidden="true" />}
              <span>{storybookNotice}</span>
              {(storybookState === 'unavailable' || storybookState === 'failed') && (
                <button type="button" onClick={() => void openStorybook()}>重试</button>
              )}
              {storybookState === 'save-failed' && (
                <button type="button" onClick={() => void saveStorybookBook()}>重新保存</button>
              )}
            </p>
          )}
          <label htmlFor="tide-message">把下一步放到潮水线上</label>
          <div className={styles.inputLine}>
            <textarea
              ref={textareaRef}
              id="tide-message"
              value={draft}
              rows={1}
              maxLength={MAX_DRAFT_LENGTH}
              placeholder="现在最需要想清楚什么？"
              disabled={loadState === 'loading'}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                  event.preventDefault();
                  submit();
                }
              }}
              data-testid="tide-composer-input"
              data-voice-state={voiceState}
            />
            <input
              ref={contextFileRef}
              className={styles.hiddenFileInput}
              type="file"
              accept=".pdf,.docx,.txt,.md,.markdown"
              onChange={(event) => {
                const file = event.target.files?.[0];
                event.target.value = '';
                if (file) void uploadUserContextDocument(file).catch(() => undefined);
              }}
              data-testid="darya-context-upload-input"
            />
            {!voiceCallOpen && (
            <button
              type="button"
              className={styles.contextButton}
              onClick={() => contextFileRef.current?.click()}
              disabled={loadState === 'loading' || uploadingContext}
              aria-label="上传个人背景文档"
              title="上传 PDF、DOCX、TXT 或 Markdown，让 DARYA 提取可确认的背景"
              data-testid="darya-context-upload-control"
            >
              {uploadingContext ? (
                <LoaderCircle className={styles.spinner} size={15} aria-hidden="true" />
              ) : (
                <Paperclip size={16} aria-hidden="true" />
              )}
              <span className={styles.contextLabel}>附件</span>
            </button>
            )}
            {!voiceCallOpen && (
              <button
                type="button"
                className={`${styles.contextButton} ${capabilityOpen ? styles.contextButtonActive : ''}`}
                onClick={() => setCapabilityOpen((open) => !open)}
                disabled={loadState === 'loading' || busy}
                aria-label="打开生成中心"
                aria-expanded={capabilityOpen}
                title="查看 DARYA 能生成的全部资料，点选一条快捷指令开始"
                data-testid="darya-capability-center"
              >
                <Sparkles size={16} aria-hidden="true" />
                <span className={styles.contextLabel}>生成</span>
              </button>
            )}
            <button
              type="button"
              className={`${styles.voiceButton} ${voiceState === 'recording' ? styles.voiceButtonRecording : ''}`}
              onClick={voiceState === 'recording' ? stopRecording : () => void startRecording()}
              disabled={loadState === 'loading' || voiceState === 'requesting' || voiceState === 'transcribing'}
              aria-label={voiceState === 'recording' ? '停止录音' : '使用麦克风输入'}
              aria-pressed={voiceState === 'recording'}
              data-testid="darya-voice-control"
            >
              {voiceState === 'requesting' || voiceState === 'transcribing' ? (
                <LoaderCircle className={styles.spinner} size={15} aria-hidden="true" />
              ) : voiceState === 'recording' ? (
                <Square size={11} fill="currentColor" aria-hidden="true" />
              ) : (
                <Mic size={16} aria-hidden="true" />
              )}
            </button>
            {busy ? (
              <button type="button" className={styles.stopButton} onClick={abort} data-testid="tide-stop">
                <Square size={12} fill="currentColor" aria-hidden="true" />
                <span>停止</span>
              </button>
            ) : (
              <button
                type="button"
                className={styles.sendButton}
                onClick={submit}
                disabled={!draft.trim() || loadState === 'loading'}
                data-testid="tide-send"
              >
                <Send size={15} aria-hidden="true" />
                <span>发送</span>
              </button>
            )}
          </div>
          <div className={styles.voiceStatus} aria-live="polite" data-testid="darya-voice-status">
            {uploadingContext && <span>正在读取文档，只提取可确认的当前对话背景…</span>}
            {userContextState === 'error' && userContextError && (
              <span className={styles.voiceError} role="alert">{userContextError}</span>
            )}
            {voiceState === 'requesting' && <span>等待麦克风权限…</span>}
            {voiceState === 'recording' && (
              <span className={styles.recordingStatus}>
                正在录音，再点麦克风完成
                <button type="button" onClick={cancelVoice} data-testid="darya-voice-cancel">取消</button>
              </span>
            )}
            {voiceState === 'transcribing' && (
              <span>
                正在转成文字…
                <button type="button" onClick={cancelVoice} data-testid="darya-voice-cancel">取消</button>
              </span>
            )}
            {voiceState === 'error' && voiceError && (
              <span className={styles.voiceError} role="alert">
                {voiceError}
                {retryAudioRef.current && (
                  <button type="button" onClick={() => void transcribe(retryAudioRef.current as Blob)}>
                    重试转写
                  </button>
                )}
              </span>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}
