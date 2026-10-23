import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowLeft, BookOpen, Check, ChevronRight, ExternalLink, FileText, LoaderCircle, LogOut, RefreshCcw, Search, ShieldCheck, UserRound, X } from 'lucide-react';
import { App as AntdApp, Button, Input, Modal } from 'antd';
import { ArtifactShell } from '../../../components/artifact/ArtifactShell';
import { authApi } from '../../../api/auth';
import { humanChangeSentence } from '../rail/TideLearningFitView';
import { useAuthStore } from '../../../stores/authStore';
import { tideChatApi } from '../chat/tideChatApi';
import { getNormalizedLearnerState, type NormalizedLearnerState, type PreferenceTaxonomy } from '../../../api/learnerState';
import type { DaryaLearningLoopSnapshot, DaryaMaterialSummary, DaryaSelfUnderstanding, DaryaUnderstandingClaim } from '../tideTypes';
import type { DaryaTheme } from '../theme/daryaTheme';
import type { DaryaBoardLayout } from '../theme/daryaBoardLayout';
import type { DaryaSpaceMode } from '../theme/daryaSpaceMode';
import type { DaryaMascotFormSetting } from '../theme/daryaMascotForm';
import {
  DARYA_PITCHES,
  DARYA_SPEEDS,
  DARYA_VOICES,
  persistDaryaPitch,
  persistDaryaSpeed,
  persistDaryaVoice,
  readDaryaPitch,
  readDaryaSpeed,
  readDaryaVoice,
  type DaryaPitchId,
  type DaryaSpeedId,
  type DaryaVoiceId,
} from '../voice/daryaVoice';
import { StudyTimeSurface } from '../study/StudyTimeSurface';
import styles from './DaryaOceanSurface.module.css';
import './daryaOceanSurface-wave01.css';
import closeoutStyles from './DaryaCloseoutSurface.module.css';

export type DaryaSecondarySurface =
  | { kind: 'materials' }
  | { kind: 'understanding' }
  | { kind: 'settings' }
  | { kind: 'time' }
  | { kind: 'artifact'; artifactId: number; versionId?: number | null; versionNumber?: number | null };

interface Props {
  surface: DaryaSecondarySurface;
  threadId: string | null;
  onBack: () => void;
  onOpenArtifact: (artifactId: number, versionId?: number | null, versionNumber?: number | null) => void;
  theme: DaryaTheme;
  onThemeChange: (theme: DaryaTheme) => void;
  boardLayout: DaryaBoardLayout;
  onBoardLayoutChange: (layout: DaryaBoardLayout) => void;
  spaceMode: DaryaSpaceMode;
  onSpaceModeChange: (mode: DaryaSpaceMode) => void;
  mascotFormSetting: DaryaMascotFormSetting;
  onMascotFormSettingChange: (setting: DaryaMascotFormSetting) => void;
  onLogout: () => void;
  onOpenUnderstanding: () => void;
}

const categoryCopy = {
  all: '全部',
  handout: '讲义',
  guide: '实操',
  assessment: '测试',
  code: '代码',
  mindmap: '导图',
  presentation: '演示',
  video: '视频',
  audio: '音频',
  picture_book: '绘本',
} as const;

// 后端返回的是用户真实拥有的全部产物；类别只决定标签，绝不静默丢弃某一类。
const categoryLabel = (value?: string) => categoryCopy[value as keyof typeof categoryCopy] || '资料';

const preferenceLabel: Record<PreferenceTaxonomy, string> = {
  visual: '图示优先', video: '视频讲解', reading: '阅读理解', document: '讲义资料',
  practice: '练习驱动', code: '代码实践', example: '例题引导', step_by_step: '逐步讲解',
  concise: '简洁表达', detailed: '详细说明',
};

const masteryStageLabel: Record<string, string> = {
  unknown: '等待可信学习证据', emerging: '正在建立理解', developing: '掌握正在发展',
  partial: '已形成部分掌握', secure: '掌握较稳定',
};

const evidenceSourceLabel: Record<string, string> = {
  explicit: '你明确说过', profile: '来自你的画像', behavioral: '来自行为观察',
};

/** 从真实 Learner State 提炼只读理解行；没有可信内容就不造句。 */
function learnerStateLines(state: NormalizedLearnerState | null) {
  if (!state) return { confirmed: [] as Array<{ label: string; value: string }>, observing: [] as Array<{ label: string; value: string }> };
  const confirmed: Array<{ label: string; value: string }> = [];
  const observing: Array<{ label: string; value: string }> = [];
  if (state.current_goal) confirmed.push({ label: '当前目标', value: state.current_goal });
  const mastery = masteryStageLabel[state.topic_mastery] || masteryStageLabel[state.knowledge_level];
  if (mastery) confirmed.push({ label: '目前进展', value: mastery });
  if (state.strengths?.length) confirmed.push({ label: '已经表现出优势', value: state.strengths.slice(0, 3).join('、') });
  const stablePreferences = (state.preference_evidence || [])
    .filter((item) => item.polarity === 'supporting' && item.evidence_type !== 'behavioral' && preferenceLabel[item.preference])
    .sort((a, b) => b.confidence - a.confidence)
    .slice(0, 2);
  stablePreferences.forEach((item) => confirmed.push({
    label: preferenceLabel[item.preference],
    value: evidenceSourceLabel[item.evidence_type] || '有明确来源',
  }));
  const weakText = state.weak_points?.length ? state.weak_points.slice(0, 3).join('、') : '';
  const focusText = state.next_teaching_focus || '';
  if (weakText && focusText && weakText === focusText) {
    observing.push({ label: '当前薄弱点 · 也是下一步焦点', value: weakText });
  } else {
    if (weakText) observing.push({ label: '仍在观察的薄弱点', value: weakText });
    if (focusText) observing.push({ label: '下一步教学焦点', value: focusText });
  }
  const behavioralPreferences = (state.preference_evidence || [])
    .filter((item) => item.polarity === 'supporting' && item.evidence_type === 'behavioral' && preferenceLabel[item.preference])
    .sort((a, b) => b.confidence - a.confidence)
    .slice(0, 2);
  behavioralPreferences.forEach((item) => observing.push({
    label: `可能偏好「${preferenceLabel[item.preference]}」`,
    value: '来自行为观察 · 不当作定论',
  }));
  return { confirmed, observing };
}

function MaterialSurface({ onBack, onOpenArtifact }: Pick<Props, 'onBack' | 'onOpenArtifact'>) {
  const [category, setCategory] = useState<keyof typeof categoryCopy>('all');
  const [query, setQuery] = useState('');
  const [rows, setRows] = useState<DaryaMaterialSummary[]>([]);
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading');
  const [error, setError] = useState('');
  const [reloadToken, setReloadToken] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setState('loading');
    tideChatApi.listMaterials(category)
      .then((items) => {
        if (!cancelled) {
          // 不再按 source_table / PASS 过滤：试卷、记忆卡、音频课、导图同样是学员的真实资料。
          // 是否通过检查由列表里的状态如实呈现，而不是把条目藏起来。
          setRows(items);
          setState('ready');
        }
      })
      .catch((reason: unknown) => {
        if (!cancelled) {
          setError(reason instanceof Error ? reason.message : '暂时无法读取资料。');
          setState('error');
        }
      });
    return () => { cancelled = true; };
  }, [category, reloadToken]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return rows;
    return rows.filter((item) => [item.title, item.topic, item.summary, categoryLabel(item.category)]
      .join(' ').toLowerCase().includes(needle));
  }, [query, rows]);

  return (
    <section className={styles.surface} aria-labelledby="darya-materials-title" data-testid="darya-my-materials">
      <header className={styles.header}>
        <button type="button" className={styles.back} onClick={onBack}><ArrowLeft size={16} />返回对话</button>
        <div><span>我的资料</span><h2 id="darya-materials-title">我的资料</h2><p>DARYA 曾为你准备的正式资料。新资料仍会先出现在所属对话里。</p></div>
      </header>
      <div className={styles.toolbar}>
        <div className={styles.filters} aria-label="资料类型">
          {(Object.keys(categoryCopy) as Array<keyof typeof categoryCopy>).map((key) => (
            <button key={key} type="button" data-active={category === key} onClick={() => setCategory(key)}>{categoryCopy[key]}</button>
          ))}
        </div>
        <label className={styles.search}><Search size={15} /><span className={styles.srOnly}>搜索资料</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索资料" /></label>
      </div>
      <div className={styles.content}>
        {state === 'loading' ? <p className={styles.state}><LoaderCircle className={styles.spin} size={18} />正在读取资料…</p>
          : state === 'error' ? (
            <div className={styles.error} role="alert">
              <p>{error}</p>
              <button type="button" onClick={() => setReloadToken((value) => value + 1)}>
                <RefreshCcw size={14} aria-hidden="true" />重新读取
              </button>
            </div>
          )
            : filtered.length === 0 ? (
              <div className={styles.empty}>
                <BookOpen size={22} />
                <h3>{rows.length > 0 ? '这一类暂时没有' : '这里还很安静'}</h3>
                <p>{rows.length > 0 ? '换个类别看看，或清空搜索词。' : '回到对话，DARYA 会在真正需要时为你准备。'}</p>
              </div>
            )
              : <ol className={styles.materialList}>{filtered.map((item) => (
                <li key={`${item.artifact_id}-${item.version}`}>
                  <button type="button" onClick={() => onOpenArtifact(item.artifact_id, null, item.version)} data-testid={`darya-material-${item.artifact_id}`}>
                    <span className={styles.materialKind}>{categoryLabel(item.category)}</span>
                    <span className={styles.materialCopy}><strong>{item.title}</strong><small>{item.summary || item.topic || '已检查，可继续使用'}</small></span>
                    <span className={styles.materialStatus}>{String(item.review_verdict || '').toUpperCase() === 'PASS' ? '已检查' : '已保存'}<ChevronRight size={15} /></span>
                  </button>
                </li>
              ))}</ol>}
      </div>
    </section>
  );
}

// 这些“为什么”每张卡都一样，区块标题下已有整段说明；只有真正个性化的理由才值得逐卡展示。
const GENERIC_CLAIM_WHY = new Set([
  '这是你自然提到的普通信息；相关时我会用它保持连续，但不会把它当成外部核实或正式学习判断。',
  '相关时我会据此调整讲解节奏或形式；它不是对你的定论。',
  '相关时我会自然使用这条由你确认的信息，不让它替代正式学习判断。',
]);

function UnderstandingClaimRow({ claim, onChanged }: { claim: DaryaUnderstandingClaim; onChanged: (next: DaryaSelfUnderstanding) => void }) {
  const [mode, setMode] = useState<'idle' | 'correct' | 'forget'>('idle');
  const [correction, setCorrection] = useState(claim.source_kind === 'memory' ? '' : claim.statement);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const apply = async (action: 'confirm' | 'correct' | 'forget') => {
    setSaving(true);
    setError('');
    try {
      if (claim.source_kind === 'memory') {
        if (claim.revision === null) throw new Error('这条理解缺少可更新版本，请刷新后再试。');
        const next = await tideChatApi.updateUnderstandingMemory(claim.source_id, {
          action: action === 'correct' ? 'correct' : 'forget',
          expected_revision: claim.revision,
          correction: action === 'correct' ? correction.trim() : null,
        });
        onChanged(next);
      } else {
        const impressionAction = action === 'confirm' ? 'accurate' : action === 'correct' ? 'modify' : 'inaccurate';
        await tideChatApi.updateImpression(claim.source_id, impressionAction, action === 'correct' ? correction.trim() : undefined);
        // 写入已经成功。回读只是为了刷新列表，失败不能反过来告诉用户「没有保存成功」，
        // 否则用户会重复确认，产生重复纠正。
        setMode('idle');
        try {
          onChanged(await tideChatApi.getSelfUnderstanding());
        } catch {
          setError('已经保存，只是列表暂时没能刷新；重新打开这一页就能看到。');
        }
        return;
      }
      setMode('idle');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '没有保存成功。');
    } finally {
      setSaving(false);
    }
  };

  return (
    <article className={styles.claim} data-certainty={claim.certainty} data-testid={`darya-understanding-${claim.id}`}>
      <span className={styles.certainty}>{claim.certainty_label}</span>
      <div className={styles.claimCopy}>
        <strong>{claim.statement}</strong>
        {claim.why_it_matters && !GENERIC_CLAIM_WHY.has(claim.why_it_matters) && <p>{claim.why_it_matters}</p>}
      </div>
      {mode === 'correct' ? (
        <div className={styles.editor}>
          <textarea value={correction} maxLength={500} aria-label="更准确的说法" onChange={(event) => setCorrection(event.target.value)} autoFocus />
          <div><button type="button" disabled={saving || correction.trim().length < 2} onClick={() => void apply('correct')}>保存纠正</button><button type="button" disabled={saving} onClick={() => setMode('idle')}>取消</button></div>
        </div>
      ) : mode === 'forget' ? (
        <div className={styles.confirmForget}><span>忘掉后，这条内容不会再用于后续理解。</span><div><button type="button" disabled={saving} onClick={() => void apply('forget')}>确认忘掉</button><button type="button" disabled={saving} onClick={() => setMode('idle')}>保留</button></div></div>
      ) : (
        <div className={styles.claimActions}>
          {claim.can_confirm && <button type="button" disabled={saving} onClick={() => void apply('confirm')}><Check size={13} />对，就是这样</button>}
          {claim.can_correct && <button type="button" disabled={saving} onClick={() => setMode('correct')}>纠正</button>}
          {claim.can_forget && <button type="button" disabled={saving} onClick={() => setMode('forget')}>忘掉</button>}
        </div>
      )}
      {error && <p className={styles.error} role="alert">{error}</p>}
    </article>
  );
}

function UnderstandingSurface({ onBack, threadId }: Pick<Props, 'onBack' | 'threadId'>) {
  const userId = useAuthStore((state) => state.user?.userId);
  const [data, setData] = useState<DaryaSelfUnderstanding | null>(null);
  const [learningLoop, setLearningLoop] = useState<DaryaLearningLoopSnapshot | null>(null);
  const [learnerState, setLearnerState] = useState<NormalizedLearnerState | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    let cancelled = false;
    Promise.all([
      tideChatApi.getSelfUnderstanding(),
      threadId ? tideChatApi.getLearningLoop(threadId).catch(() => null) : Promise.resolve(null),
      userId ? getNormalizedLearnerState(userId).catch(() => null) : Promise.resolve(null),
    ]).then(([next, loop, state]) => {
      if (!cancelled) {
        setData(next);
        setLearningLoop(loop);
        setLearnerState(state);
      }
    })
      .catch((reason: unknown) => { if (!cancelled) setError(reason instanceof Error ? reason.message : '暂时无法读取。'); });
    return () => { cancelled = true; };
  }, [threadId, userId]);
  const activeRoute = learningLoop?.active_route;
  const confirmedClaims = data?.groups.confirmed || [];
  const observingClaims = [
    ...(data?.groups.observed || []),
    ...(data?.groups.inferred || []),
    ...(data?.groups.uncertain || []),
  ];
  const profileLines = useMemo(() => learnerStateLines(learnerState), [learnerState]);
  const nothingRecorded = data?.empty && !activeRoute && profileLines.confirmed.length === 0 && profileLines.observing.length === 0;
  return (
    <section className={styles.surface} aria-labelledby="darya-understanding-title" data-testid="darya-self-understanding">
      <header className={styles.header}>
        <button type="button" className={styles.back} onClick={onBack}><ArrowLeft size={16} />返回对话</button>
        <div><span>DARYA 眼中的我</span><h2 id="darya-understanding-title">DARYA 眼中的我</h2><p>这里只放有来源的理解：我会更参考你真正完成过的练习，而不是凭一次对话下结论。</p></div>
      </header>
      <div className={styles.content}>
        {error ? <p className={styles.error}>{error}
          </p>
          : !data ? <p className={styles.state}><LoaderCircle className={styles.spin} size={18} />正在整理我对你的理解…</p>
            : nothingRecorded ? <div className={styles.empty}><FileText size={22} /><h3>我们还在慢慢认识彼此</h3><p>你不需要先填写一份画像。明确告诉我的偏好和可撤销的共同观察，会在这里出现。</p></div>
              : <>
                <section className={closeoutStyles.understandSection} data-testid="darya-understanding-current-judgment">
                  <h3>当前判断</h3>
                  {activeRoute ? (
                    <>
                      <strong>{activeRoute.current_title}</strong>
                      <p>{activeRoute.current_gap}</p>
                      <dl>
                        <div><dt>学习目标</dt><dd>{activeRoute.goal}</dd></div>
                        <div><dt>因此接下来</dt><dd>{activeRoute.next.title} —— {activeRoute.next.reason.replace(/服务端测试确认/g, '提交检查确认')}</dd></div>
                      </dl>
                    </>
                  ) : (
                    <p className={closeoutStyles.sectionQuiet}>这段对话还没有形成正式学习路线。安心聊天，DARYA 会在真实学习发生后再形成判断。</p>
                  )}
                </section>

                {activeRoute?.latest_change?.summary && (
                  <section className={closeoutStyles.understandSection} data-testid="darya-understanding-recent-change">
                    <h3>最近发生的变化</h3>
                    <p className={closeoutStyles.sectionQuiet}>{humanChangeSentence(activeRoute.latest_change)}</p>
                  </section>
                )}

                {confirmedClaims.length > 0 || profileLines.confirmed.length > 0 ? (
                  <section className={closeoutStyles.understandSection} data-testid="darya-understanding-confirmed">
                    <h3>我已经确认的</h3>
                    <p className={closeoutStyles.sectionNote}>这些内容有明确来源；我会更参考你真正完成过的练习，而不是凭一次对话给你下结论。</p>
                    {confirmedClaims.length > 0 && (
                      <div className={styles.claimGroup}>
                        {confirmedClaims.map((claim) => <UnderstandingClaimRow key={claim.id} claim={claim} onChanged={setData} />)}
                      </div>
                    )}
                    {profileLines.confirmed.length > 0 && (
                      <div className={closeoutStyles.stateLines}>
                        {profileLines.confirmed.map((line) => (
                          <div key={line.label}><span>{line.label}</span><strong>{line.value}</strong></div>
                        ))}
                      </div>
                    )}
                  </section>
                ) : (
                  <section className={closeoutStyles.understandSection} data-testid="darya-understanding-confirmed">
                    <h3>我已经确认的</h3>
                    <p className={closeoutStyles.sectionQuiet}>还没有可以确认的内容。你明确告诉我的偏好，和你真正完成过的练习里看出的稳定表现，会出现在这里。</p>
                  </section>
                )}

                {observingClaims.length > 0 || profileLines.observing.length > 0 ? (
                  <section className={closeoutStyles.understandSection} data-testid="darya-understanding-observing">
                    <h3>仍在观察的</h3>
                    <p className={closeoutStyles.sectionNote}>{observingClaims.length > 0 ? '还没有被当作定论；你可以确认、纠正，或者让 DARYA 忘掉。' : '如果我理解错了，你可以直接在对话里纠正我。'}</p>
                    {observingClaims.length > 0 && (
                      <div className={styles.claimGroup}>
                        {observingClaims.map((claim) => <UnderstandingClaimRow key={claim.id} claim={claim} onChanged={setData} />)}
                      </div>
                    )}
                    {profileLines.observing.length > 0 && (
                      <div className={closeoutStyles.stateLines}>
                        {profileLines.observing.map((line) => (
                          <div key={line.label}><span>{line.label}</span><strong>{line.value}</strong></div>
                        ))}
                      </div>
                    )}
                  </section>
                ) : (
                  <section className={closeoutStyles.understandSection} data-testid="darya-understanding-observing">
                    <h3>仍在观察的</h3>
                    <p className={closeoutStyles.sectionQuiet}>暂时没有需要你纠正的推断。如果我理解错了，你可以直接在对话里纠正我。</p>
                  </section>
                )}
              </>}
      </div>
    </section>
  );
}

function SettingsSurface({ onBack, theme, onThemeChange, boardLayout, onBoardLayoutChange, spaceMode, onSpaceModeChange, mascotFormSetting, onMascotFormSettingChange, onLogout, onOpenUnderstanding }: Pick<Props, 'onBack' | 'theme' | 'onThemeChange' | 'boardLayout' | 'onBoardLayoutChange' | 'spaceMode' | 'onSpaceModeChange' | 'mascotFormSetting' | 'onMascotFormSettingChange' | 'onLogout' | 'onOpenUnderstanding'>) {
  const { message } = AntdApp.useApp();
  const user = useAuthStore((state) => state.user);
  const clearSession = useAuthStore((state) => state.clearSession);
  const navigate = useNavigate();
  const [pwdOpen, setPwdOpen] = useState(false);
  const [currentPwd, setCurrentPwd] = useState('');
  const [nextPwd, setNextPwd] = useState('');
  const [savingPwd, setSavingPwd] = useState(false);
  const [pwdError, setPwdError] = useState('');
  const choices: Array<{ id: DaryaTheme; title: string; description: string }> = [
    { id: 'kaki', title: '柿子智学', description: '温暖、安静的默认学习环境。' },
    { id: 'ocean', title: 'Ocean', description: '清冷、专注的海岸环境。' },
  ];
  /** 黑板在对话区里怎么排。以后讨论出更好的排版，就往这个列表里加。 */
  const boardLayoutChoices: Array<{ id: DaryaBoardLayout; mark: string; title: string; description: string }> = [
    { id: 'split', mark: '分', title: '同屏', description: '左边文字自己滚，右边黑板常驻 —— 聊天和看板两不误。' },
    { id: 'full', mark: '满', title: '占满', description: '黑板占满对话区，适合投屏讲解；正文先让开，点「回到同屏」换回来。' },
  ];
  const spaceModeChoices: Array<{ id: DaryaSpaceMode; mark: string; title: string; description: string }> = [
    { id: 'curve', mark: '弧', title: '曲面环抱', description: '黑板正对你，两侧沿柔和弧线退开。' },
    { id: 'fold', mark: '折', title: '折叠环抱', description: '黑板保持正面，两侧向内转约 35°。' },
  ];
  /** 数字人站在板子旁边的形态。 */
  /** 数字人站在板子旁边的形态。跟随主题是默认：柿子主题柿小吉、海洋主题海星。 */
  const mascotFormChoices: Array<{ id: DaryaMascotFormSetting; mark: string; title: string; description: string }> = [
    { id: 'auto', mark: '随', title: '跟随主题', description: '柿子主题配柿小吉，海洋主题配海星 —— 换主题它也跟着换。' },
    { id: 'persimmon', mark: '柿', title: '柿小吉', description: '固定用柿子形态，不随主题变。' },
    { id: 'starfish', mark: '星', title: '海星', description: '固定用海洋海星形态，不随主题变。' },
  ];
  /** 语音通话里用哪个声音、多快、什么音调。和主题一样按用户记在本机。 */
  const [voice, setVoice] = useState<DaryaVoiceId>(() => readDaryaVoice(user?.userId));
  const [speed, setSpeed] = useState<DaryaSpeedId>(() => readDaryaSpeed(user?.userId));
  const [pitch, setPitch] = useState<DaryaPitchId>(() => readDaryaPitch(user?.userId));
  useEffect(() => {
    setVoice(readDaryaVoice(user?.userId));
    setSpeed(readDaryaSpeed(user?.userId));
    setPitch(readDaryaPitch(user?.userId));
  }, [user?.userId]);
  const logout = () => {
    clearSession();
    onLogout();
    navigate('/darya/login', { replace: true });
  };
  const submitPassword = async () => {
    if (nextPwd.length < 6) { setPwdError('新密码至少需要 6 个字符'); return; }
    setSavingPwd(true); setPwdError('');
    try {
      await authApi.changePassword({ current_password: currentPwd, new_password: nextPwd });
      message.success('密码已更新');
      setPwdOpen(false); setCurrentPwd(''); setNextPwd('');
    } catch (reason) {
      setPwdError(reason instanceof Error ? reason.message : '修改没有成功，请稍后再试。');
    } finally { setSavingPwd(false); }
  };
  return (
    <section className={styles.surface} aria-labelledby="darya-settings-title" data-testid="darya-settings">
      <header className={styles.header}>
        <button type="button" className={styles.back} onClick={onBack}><ArrowLeft size={16} />返回对话</button>
        <div><span>柿子智学 · 设置</span><h2 id="darya-settings-title">设置</h2><p>管理外观、账户与个性化体验。</p></div>
      </header>
      <div className={`${styles.content} ${closeoutStyles.settingsContent}`}>
        <div className={closeoutStyles.settingsLayout}>
          <nav className={closeoutStyles.settingsNav} aria-label="设置分类">
            <span>设置目录</span>
            <a href="#darya-settings-appearance">外观与对话</a>
            <a href="#darya-settings-voice">声音</a>
            <a href="#darya-settings-account">账户与安全</a>
            <a href="#darya-settings-personalization">个性化</a>
          </nav>
          <div className={closeoutStyles.settingsPanels}>
        <section id="darya-settings-appearance" className={`${closeoutStyles.themeSettings} ${closeoutStyles.settingsWide}`} aria-label="外观主题" data-testid="darya-settings-appearance">
          <header><span>外观</span><h3>主题</h3><p>会记住你的选择。</p></header>
          <div className={closeoutStyles.themeChoices} data-compact-options="true">
            {choices.map((choice) => (
              <button key={choice.id} type="button" data-active={theme === choice.id} aria-pressed={theme === choice.id} onClick={() => onThemeChange(choice.id)} data-testid={`darya-theme-${choice.id}`}>
                <span className={closeoutStyles.themeSwatch} data-theme={choice.id} aria-hidden="true" />
                <span><strong>{choice.title}</strong><small>{choice.description}</small></span>
                {theme === choice.id && <Check size={17} aria-label="当前主题" />}
              </button>
            ))}
          </div>
        </section>

        <section className={closeoutStyles.themeSettings} aria-label="黑板排版" data-testid="darya-settings-board-layout">
          <header><span>对话区</span><h3>黑板怎么排</h3><p>会记住你的选择，下一次进对话就按这个来。</p></header>
          <div className={closeoutStyles.themeChoices} data-compact-options="true">
            {boardLayoutChoices.map((choice) => (
              <button
                key={choice.id}
                type="button"
                data-active={boardLayout === choice.id}
                aria-pressed={boardLayout === choice.id}
                onClick={() => onBoardLayoutChange(choice.id)}
                data-testid={`darya-board-layout-${choice.id}`}
              >
                <span className={closeoutStyles.voiceMark} aria-hidden="true">{choice.mark}</span>
                <span><strong>{choice.title}</strong><small>{choice.description}</small></span>
                {boardLayout === choice.id && <Check size={17} aria-label="当前排版" />}
              </button>
            ))}
          </div>
        </section>

        <section className={closeoutStyles.themeSettings} aria-label="学习空间" data-testid="darya-settings-space-mode">
          <header><span>讲解空间</span><h3>空间怎么环抱你</h3><p>切换后会记住你的选择。</p></header>
          <div className={closeoutStyles.themeChoices} data-compact-options="true">
            {spaceModeChoices.map((choice) => (
              <button
                key={choice.id}
                type="button"
                data-active={spaceMode === choice.id}
                aria-pressed={spaceMode === choice.id}
                onClick={() => onSpaceModeChange(choice.id)}
                data-testid={`darya-space-mode-${choice.id}`}
              >
                <span className={closeoutStyles.voiceMark} aria-hidden="true">{choice.mark}</span>
                <span><strong>{choice.title}</strong><small>{choice.description}</small></span>
                {spaceMode === choice.id && <Check size={17} aria-label="当前空间" />}
              </button>
            ))}
          </div>
        </section>

        <section className={closeoutStyles.themeSettings} aria-label="数字人形态" data-testid="darya-settings-mascot-form">
          <header><span>数字人</span><h3>站在黑板旁边的形态</h3><p>会记住你的选择，下一次占满模式就按这个来。</p></header>
          <div className={closeoutStyles.themeChoices}>
            {mascotFormChoices.map((choice) => (
              <button
                key={choice.id}
                type="button"
                data-active={mascotFormSetting === choice.id}
                aria-pressed={mascotFormSetting === choice.id}
                onClick={() => onMascotFormSettingChange(choice.id)}
                data-testid={`darya-mascot-form-${choice.id}`}
              >
                <span className={closeoutStyles.voiceMark} aria-hidden="true">{choice.mark}</span>
                <span><strong>{choice.title}</strong><small>{choice.description}</small></span>
                {mascotFormSetting === choice.id && <Check size={17} aria-label="当前形态" />}
              </button>
            ))}
          </div>
        </section>

        <section id="darya-settings-voice" className={`${closeoutStyles.themeSettings} ${closeoutStyles.settingsWide}`} aria-label="声音" data-testid="darya-settings-voice">
          <header><span>声音</span><h3>语音通话里 DARYA 用哪个声音</h3><p>会记住你的选择，下次开始通话时生效。</p></header>
          <div className={closeoutStyles.themeChoices}>
            {DARYA_VOICES.map((option) => (
              <button
                key={option.id}
                type="button"
                data-active={voice === option.id}
                aria-pressed={voice === option.id}
                onClick={() => { setVoice(option.id); persistDaryaVoice(option.id, user?.userId); }}
                data-testid={`darya-voice-${option.id}`}
              >
                <span className={closeoutStyles.voiceMark} aria-hidden="true">{option.gender === 'male' ? '男' : '女'}</span>
                <span><strong>{option.label}</strong><small>{option.desc}</small></span>
                {voice === option.id && <Check size={17} aria-label="当前声音" />}
              </button>
            ))}
          </div>
          <div className={closeoutStyles.voiceTuning}>
            <div className={closeoutStyles.voiceTuningRow}>
              <span>语速</span>
              <div className={closeoutStyles.voiceTuningOptions}>
                {DARYA_SPEEDS.map((option) => (
                  <button
                    key={option.id}
                    type="button"
                    data-active={speed === option.id}
                    aria-pressed={speed === option.id}
                    onClick={() => { setSpeed(option.id); persistDaryaSpeed(option.id, user?.userId); }}
                    data-testid={`darya-speed-${option.id}`}
                  >
                    {option.label}
                  </button>
                ))}
              </div>
            </div>
            <div className={closeoutStyles.voiceTuningRow}>
              <span>音调</span>
              <div className={closeoutStyles.voiceTuningOptions}>
                {DARYA_PITCHES.map((option) => (
                  <button
                    key={option.id}
                    type="button"
                    data-active={pitch === option.id}
                    aria-pressed={pitch === option.id}
                    onClick={() => { setPitch(option.id); persistDaryaPitch(option.id, user?.userId); }}
                    data-testid={`darya-pitch-${option.id}`}
                  >
                    {option.label}
                  </button>
                ))}
              </div>
            </div>
          </div>
        </section>

        <section id="darya-settings-account" className={closeoutStyles.themeSettings} aria-label="账户与安全" data-testid="darya-settings-account">
          <header><span>账户与安全</span><h3>当前登录</h3><p>身份只用于保存你自己的学习记录与对话。</p></header>
          <div className={closeoutStyles.accountRows}>
            <div><span><UserRound size={14} aria-hidden="true" />用户名</span><strong>{user?.username || '—'}</strong></div>
            <div><span>邮箱</span><strong>{user?.email || '未填写'}</strong></div>
          </div>
          <div className={closeoutStyles.settingsActions}>
            <button type="button" onClick={() => { setPwdOpen(true); setPwdError(''); }} data-testid="darya-settings-change-password">修改密码</button>
            <button type="button" onClick={logout} data-testid="darya-settings-logout"><LogOut size={14} />退出登录</button>
          </div>
        </section>

        <section id="darya-settings-personalization" className={closeoutStyles.themeSettings} aria-label="个性化与学习体验" data-testid="darya-settings-personalization">
          <header><span>个性化与学习体验</span><h3>DARYA 怎么帮你</h3><p>DARYA 会根据你确认的信息和真实学习行为，调整后续解释、资料与学习路径。</p></header>
          <div className={closeoutStyles.accountRows}>
            <button type="button" className={closeoutStyles.personalLink} onClick={onOpenUnderstanding} data-testid="darya-settings-open-understanding">
              <span><UserRound size={14} aria-hidden="true" />DARYA 对你的理解</span>
              <strong>查看<ExternalLink size={13} aria-hidden="true" /></strong>
            </button>
            <button type="button" className={closeoutStyles.personalLink} onClick={onBack} data-testid="darya-settings-open-route">
              <span>当前学习方向</span>
              <strong>查看<ExternalLink size={13} aria-hidden="true" /></strong>
            </button>
          </div>
        </section>

        <section className={`${closeoutStyles.themeSettings} ${closeoutStyles.settingsWide}`} aria-label="会话与隐私" data-testid="darya-settings-security">
          <header><span>会话与隐私</span><h3>这台设备上</h3></header>
          <div className={closeoutStyles.accountRows}>
            <div><span><ShieldCheck size={14} aria-hidden="true" />登录状态</span><strong>仅保存在本机浏览器</strong></div>
            <div><span>退出登录</span><strong>会立即清除本机的登录凭据</strong></div>
          </div>
        </section>
          </div>
        </div>
      </div>

      <Modal title="修改密码" open={pwdOpen} onCancel={() => setPwdOpen(false)}
        footer={[
          <Button key="cancel" onClick={() => setPwdOpen(false)}>取消</Button>,
          <Button key="submit" type="primary" loading={savingPwd} onClick={() => void submitPassword()}>确认修改</Button>,
        ]}>
        <div style={{ display: 'grid', gap: 14 }}>
          <div><label style={{ fontWeight: 600, marginBottom: 4, display: 'block' }}>当前密码</label>
            <Input.Password value={currentPwd} onChange={(e) => setCurrentPwd(e.target.value)} autoComplete="current-password" /></div>
          <div><label style={{ fontWeight: 600, marginBottom: 4, display: 'block' }}>新密码</label>
            <Input.Password value={nextPwd} onChange={(e) => setNextPwd(e.target.value)} placeholder="至少 6 个字符" autoComplete="new-password" /></div>
          {pwdError && <p style={{ margin: 0, color: 'var(--danger, #b0523f)', fontSize: 13 }}>{pwdError}</p>}
        </div>
      </Modal>
    </section>
  );
}

export function DaryaOceanSurface({ surface, threadId, onBack, onOpenArtifact, theme, onThemeChange, boardLayout, onBoardLayoutChange, spaceMode, onSpaceModeChange, mascotFormSetting, onMascotFormSettingChange, onLogout, onOpenUnderstanding }: Props) {
  if (surface.kind === 'materials') return <MaterialSurface onBack={onBack} onOpenArtifact={onOpenArtifact} />;
  if (surface.kind === 'understanding') return <UnderstandingSurface onBack={onBack} threadId={threadId} />;
  if (surface.kind === 'settings') return <SettingsSurface onBack={onBack} theme={theme} onThemeChange={onThemeChange} boardLayout={boardLayout} onBoardLayoutChange={onBoardLayoutChange} spaceMode={spaceMode} onSpaceModeChange={onSpaceModeChange} mascotFormSetting={mascotFormSetting} onMascotFormSettingChange={onMascotFormSettingChange} onLogout={onLogout} onOpenUnderstanding={onOpenUnderstanding} />;
  if (surface.kind === 'time') return <StudyTimeSurface onBack={onBack} />;
  return (
    <section className={`${styles.surface} ${styles.artifactSurface}`} role="dialog" aria-modal="true" aria-label="DARYA 资料" data-testid="darya-artifact-surface">
      <header className={styles.artifactBack}><button type="button" onClick={onBack}><ArrowLeft size={16} />返回</button><button type="button" onClick={onBack} aria-label="关闭资料"><X size={16} /></button></header>
      <div className={styles.artifactHost}>
        <ArtifactShell artifactId={surface.artifactId} initialVersionId={surface.versionId} initialVersionNumber={surface.versionNumber} context={{ mode: 'workspace', threadId, product: 'darya' }} />
      </div>
    </section>
  );
}
