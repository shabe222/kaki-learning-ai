import { useEffect, useMemo, useRef, useState } from 'react';
import { BarChart3, Layers3, Pause, Play, Volume2 } from 'lucide-react';
import type { DaryaConversationAction } from '../tideTypes';
import type { DaryaSpaceMode } from '../theme/daryaSpaceMode';
import { fetchStudySummary, type StudyTimeSummary } from '../study/studyTimeApi';
import { daryaPitchOf, daryaRateOf, readDaryaPitch, readDaryaSpeed, readDaryaVoice } from '../voice/daryaVoice';
import { tideChatApi } from './tideChatApi';
import { SafeMarkdown } from '../../../components/SafeMarkdown';
import styles from './BoardSideScreen.module.css';

type SideScreenMode = 'narration' | 'rhythm' | 'material';

const actionProgress: Record<DaryaConversationAction['status'], number> = {
  planned: 16,
  preparing: 52,
  reviewing: 82,
  ready: 100,
  failed: 100,
};

const actionCopy: Record<DaryaConversationAction['status'], string> = {
  planned: '已安排，等待开始',
  preparing: '正在生成内容',
  reviewing: '正在检查内容',
  ready: '资料已经准备好',
  failed: '这次没有准备完成',
};

function currentTime() {
  return new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date());
}

function formatMinutes(seconds: number) {
  const minutes = Math.max(0, Math.floor(seconds / 60));
  if (minutes < 1) return '刚刚开始';
  if (minutes < 60) return `${minutes} 分钟`;
  return `${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分`;
}

/** 卡片保留 Markdown 排版；送给语音合成的内容只保留可读文字。 */
function toBoardSpeechText(raw: string) {
  return raw
    .replace(/\[tide_sidecar[^\]]*\][\s\S]*?\[\/tide_sidecar\]/gi, ' ')
    .replace(/\[darya_learning_fit\][\s\S]*?\[\/darya_learning_fit\]/gi, ' ')
    .replace(/\[blackboard\][\s\S]*?\[\/blackboard\]/gi, ' ')
    .replace(/<blackboard>[\s\S]*?<\/blackboard>/gi, ' ')
    .replace(/```[\s\S]*?```/g, ' 代码请看屏幕。 ')
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/\[\/?tide_center\]/gi, ' ')
    .replace(/^\s{0,3}(?:#{1,6}\s+|>\s*|[-+*]\s+|\d+[.)]\s+)/gm, '')
    .replace(/[*_~`#|]/g, '')
    .replace(/<[^>]+>/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function RhythmLine({ summary }: { summary: StudyTimeSummary | null }) {
  const points = useMemo(() => {
    const series = summary?.series.slice(-7) ?? [];
    if (series.length === 0) return '';
    const peak = Math.max(1, ...series.map((day) => day.seconds));
    return series.map((day, index) => {
      const x = series.length === 1 ? 50 : (index / (series.length - 1)) * 100;
      const ratio = day.seconds > 0 ? Math.max(day.seconds / peak, 0.08) : 0;
      return `${x.toFixed(1)},${(31 - ratio * 24).toFixed(1)}`;
    }).join(' ');
  }, [summary]);
  if (!points) return <p className={styles.emptyNote}>还没有足够的学习记录，节律会从你第一次停留开始长出来。</p>;
  return (
    <svg className={styles.rhythmLine} viewBox="0 0 100 36" preserveAspectRatio="none" role="img" aria-label="最近七天学习时长走势">
      <path d="M 0 31 H 100" className={styles.rhythmBase} />
      <polyline points={points} className={styles.rhythmStroke} />
    </svg>
  );
}

interface BoardSideScreenProps {
  userId?: number | null;
  narration: string;
  assistantResponse: string;
  action: DaryaConversationAction | null | undefined;
  front?: boolean;
  spaceMode?: DaryaSpaceMode;
  onSpeechStateChange?: (state: 'idle' | 'loading' | 'playing') => void;
}

/**
 * 占满黑板左侧的“斜向信息屏”。内容全部来自现有业务事实：本轮回复、
 * 学习时长汇总、或当前 conversation action，避免把氛围组件做成仪表盘假数据。
 */
export function BoardSideScreen({ userId, narration, assistantResponse, action, front = false, spaceMode = 'curve', onSpeechStateChange }: BoardSideScreenProps) {
  const [mode, setMode] = useState<SideScreenMode>('narration');
  const [now, setNow] = useState(currentTime);
  const [summary, setSummary] = useState<StudyTimeSummary | null>(null);
  const [studyFailed, setStudyFailed] = useState(false);
  const [speechState, setSpeechState] = useState<'idle' | 'loading' | 'playing'>('idle');
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const renderedNarrationRef = useRef<HTMLDivElement | null>(null);
  const audioUrlRef = useRef<string | null>(null);
  const speechAbortRef = useRef<AbortController | null>(null);

  const narrationText = narration || assistantResponse;
  const spokenText = toBoardSpeechText(narrationText);
  const displayText = narration || assistantResponse || '这一轮讲解会在这里显示。';

  useEffect(() => {
    onSpeechStateChange?.(speechState);
  }, [onSpeechStateChange, speechState]);

  useEffect(() => () => onSpeechStateChange?.('idle'), [onSpeechStateChange]);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(currentTime()), 15_000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    let cancelled = false;
    void fetchStudySummary(7)
      .then((value) => { if (!cancelled) setSummary(value); })
      .catch(() => { if (!cancelled) setStudyFailed(true); });
    return () => { cancelled = true; };
  }, []);

  const stopSpeech = () => {
    speechAbortRef.current?.abort();
    speechAbortRef.current = null;
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current = null;
    }
    if (audioUrlRef.current) {
      URL.revokeObjectURL(audioUrlRef.current);
      audioUrlRef.current = null;
    }
    setSpeechState('idle');
  };

  useEffect(() => stopSpeech, [assistantResponse]);

  const playNarration = async () => {
    const textToSpeak = toBoardSpeechText(renderedNarrationRef.current?.innerText || narrationText);
    if (!textToSpeak || speechState === 'loading') return;
    if (speechState === 'playing') {
      stopSpeech();
      return;
    }
    const controller = new AbortController();
    speechAbortRef.current = controller;
    setSpeechState('loading');
    try {
      const voice = readDaryaVoice(userId);
      const blob = await tideChatApi.synthesizeSpeech(textToSpeak, {
        voice,
        rate: daryaRateOf(readDaryaSpeed(userId)),
        pitch: daryaPitchOf(readDaryaPitch(userId)),
      }, controller.signal);
      if (controller.signal.aborted) return;
      const url = URL.createObjectURL(blob);
      audioUrlRef.current = url;
      const audio = new Audio(url);
      audioRef.current = audio;
      audio.onended = stopSpeech;
      await audio.play();
      if (!controller.signal.aborted) setSpeechState('playing');
    } catch {
      if (!controller.signal.aborted) setSpeechState('idle');
    } finally {
      if (speechAbortRef.current === controller) speechAbortRef.current = null;
    }
  };

  return (
    <aside className={styles.screen} aria-label="黑板辅助信息" data-testid="darya-board-side-screen" data-mode={mode} data-front={front ? 'true' : 'false'} data-space-mode={spaceMode}>
      <time className={styles.clock} aria-label={`当前时间 ${now}`}>{now}</time>
      <div className={styles.glass}>
        <div className={styles.tabs} role="tablist" aria-label="黑板辅助信息模式">
          <button type="button" role="tab" aria-selected={mode === 'narration'} data-active={mode === 'narration'} onClick={() => setMode('narration')} title="讲解">
            <Volume2 size={13} aria-hidden="true" />
          </button>
          <button type="button" role="tab" aria-selected={mode === 'rhythm'} data-active={mode === 'rhythm'} onClick={() => setMode('rhythm')} title="学习节律">
            <BarChart3 size={13} aria-hidden="true" />
          </button>
          <button type="button" role="tab" aria-selected={mode === 'material'} data-active={mode === 'material'} onClick={() => setMode('material')} title="资料进度">
            <Layers3 size={13} aria-hidden="true" />
          </button>
        </div>

        {mode === 'narration' && (
          <section className={styles.panel} aria-label="本轮讲解">
            <span className={styles.eyebrow}>本轮讲解</span>
            <div ref={renderedNarrationRef} className={styles.narration} data-testid="darya-board-side-screen-text">
              <SafeMarkdown source={displayText} />
            </div>
            <button type="button" className={styles.playButton} onClick={() => void playNarration()} disabled={!spokenText || speechState === 'loading'} data-testid="darya-board-side-screen-play">
              {speechState === 'playing' ? <Pause size={13} aria-hidden="true" /> : <Play size={13} aria-hidden="true" />}
              {speechState === 'loading' ? '正在准备声音' : speechState === 'playing' ? '停止播放' : '播放这一轮'}
            </button>
          </section>
        )}

        {mode === 'rhythm' && (
          <section className={styles.panel} aria-label="学习节律">
            <span className={styles.eyebrow}>学习节律</span>
            <p className={styles.studyTotal}>{studyFailed ? '暂时读不到学习时长' : `今天已学习 ${formatMinutes(summary?.today_seconds ?? 0)}`}</p>
            {!studyFailed && <RhythmLine summary={summary} />}
          </section>
        )}

        {mode === 'material' && (
          <section className={styles.panel} aria-label="资料生成进度">
            <span className={styles.eyebrow}>资料进度</span>
            {action && action.kind !== 'DEMONSTRATE' ? (
              <>
                <strong className={styles.materialTitle}>{action.artifact?.title || '本轮学习资料'}</strong>
                <p className={styles.materialState}>{actionCopy[action.status]}</p>
                <div className={styles.progressTrack} aria-label={`${actionCopy[action.status]}，${actionProgress[action.status]}%`}><i style={{ width: `${actionProgress[action.status]}%` }} /></div>
              </>
            ) : <p className={styles.emptyNote}>这一轮没有资料任务；讲解和板书仍可继续查看。</p>}
          </section>
        )}
      </div>
    </aside>
  );
}
