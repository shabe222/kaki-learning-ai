import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ArrowLeft, Camera, ChevronRight, LoaderCircle, RotateCcw } from 'lucide-react';
import type { DaryaGameCognitiveEvent, GameAction, GameStateSnapshot, SceneHotspot } from '../contracts';
import { ACTOR_ASSETS, type StoryBeat } from '../content/shared';
import { scene01Dialogue, scene01Opening } from '../content/scene01';
import { cameraTangJiBeats, scene02ExplorationDialogue, unpoweredBroadcastBeats } from '../content/scene02';
import { canonicalSceneRegistry } from '../registry/sceneRegistry';
import { GameRuntime } from '../runtime/engine';
import { gameStateApi } from '../state/gameStateApi';
import styles from './DaryaGameSurface.module.css';
import scene04Styles from './DaryaGameScene04.module.css';

interface Props { onBack: () => void; onRequestDaryaSupport: (event: DaryaGameCognitiveEvent) => void }
type DetailKind = 'roster' | 'supplement' | 'identity' | 'cameraCompare';

const runtime = new GameRuntime(canonicalSceneRegistry);

function StoryDialogue({ beat, onNext, busy }: { beat: StoryBeat; onNext: () => void; busy: boolean }) {
  return (
    <div className={styles.dialogue} data-testid="game-dialogue" role="status">
      {beat.speaker && <strong>{beat.speaker}</strong>}
      <p>{beat.text}</p>
      <button type="button" onClick={onNext} disabled={busy} aria-label="继续">
        {busy ? <LoaderCircle className={styles.spin} size={18} /> : <ChevronRight size={21} />}
      </button>
    </div>
  );
}

function EvidenceDetail({ kind, onClose }: { kind: DetailKind; onClose: () => void }) {
  if (kind === 'cameraCompare') {
    return (
      <div className={styles.detailBackdrop} role="dialog" aria-label="历史影像对照">
        <div className={styles.compare}>
          <article className={styles.identityCard}>
            <img src="/darya-game/scene02/camera-tangji.png" alt="旧学生证影像" />
            <div><small>学生安全联络员</small><h3>唐霁</h3><p>教学楼二层 · 现场协助</p></div>
          </article>
          <div className={styles.cameraPanel}>
            <img src="/darya-game/scene02/scene02-hallway.png" alt="顾遥摄像机中的走廊" />
            <img className={styles.cameraPerson} src="/darya-game/scene02/camera-tangji.png" alt="画面里的旧校服学生" />
            <span>REC · 00:17:44</span>
          </div>
        </div>
        <button type="button" className={styles.closeDetail} onClick={onClose}>完成对照</button>
      </div>
    );
  }
  return (
    <div className={styles.detailBackdrop} role="dialog" aria-label="调查记录">
      <article className={styles.paper}>
        {kind === 'roster' && <><small>第十三次疏散 · 安全联络记录</small><h3>疏散确认记录</h3><dl><div><dt>区域</dt><dd>教学楼二层</dd></div><div><dt>应确认人数</dt><dd className={styles.number}>117</dd></div><div><dt>已确认人数</dt><dd className={styles.number}>116</dd></div><div><dt>确认时间</dt><dd>17:12</dd></div><div><dt>状态</dt><dd>未完成</dd></div></dl></>}
        {kind === 'supplement' && <><small>同轮疏散 · 临时人员补充页</small><h3>补充记录</h3><p className={styles.recordSentence}>临时访客五人，已确认四人。</p></>}
        {kind === 'identity' && <><small>旧学生证 · 安全员登记夹页</small><h3>安全联络员记录</h3><div className={styles.identity}><img src="/darya-game/scene02/camera-tangji.png" alt="旧学生证照片" /><div><span>学生安全联络员</span><strong>唐霁</strong><p>负责区域：教学楼二层<br />状态：现场协助</p></div></div></>}
        <button type="button" onClick={onClose}>收起记录</button>
      </article>
    </div>
  );
}

export function DaryaGameSurface({ onBack, onRequestDaryaSupport }: Props) {
  const [snapshot, setSnapshot] = useState<GameStateSnapshot | null>(null);
  const [loadState, setLoadState] = useState<'loading' | 'ready' | 'error'>('loading');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [localBeat, setLocalBeat] = useState<StoryBeat | null>(null);
  const [detail, setDetail] = useState<DetailKind | null>(null);
  const supportOrigin = useRef(false);

  const load = useCallback(async () => {
    setLoadState('loading'); setError('');
    try { setSnapshot(await gameStateApi.current()); setLoadState('ready'); }
    catch (reason) { setError(reason instanceof Error ? reason.message : '无法读取调查进度。'); setLoadState('error'); }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const run = useCallback(async (action: GameAction) => {
    if (!snapshot || busy) return null;
    setBusy(true); setError('');
    try {
      const updated = await gameStateApi.replace(runtime.applyAction(snapshot, action));
      setSnapshot(updated);
      return updated;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '这一步没有保存，请重试。');
      try { setSnapshot(await gameStateApi.current()); } catch { /* retain the visible failure */ }
      return null;
    } finally { setBusy(false); }
  }, [busy, snapshot]);

  useEffect(() => {
    if (!supportOrigin.current && snapshot?.current_scene === 'scene04' && snapshot.story_state.daryaCognitiveMomentOpened && !snapshot.story_state.daryaSupportReturned && !busy) {
      void run({ type: 'RETURN_FROM_DARYA_SUPPORT' });
    }
  }, [busy, run, snapshot]);

  useEffect(() => {
    (window as Window & { __daryaCanonicalGame?: unknown }).__daryaCanonicalGame = {
      getState: () => snapshot,
      getVisibleActors: () => [...document.querySelectorAll('[data-game-actor][data-stage-visible="true"]')].map((node) => (node as HTMLElement).dataset.gameActor),
      getRealityTangJi: () => Boolean(document.querySelector('[data-game-actor="tangji"][data-stage-visible="true"]')),
      getNameLeak: () => Boolean(snapshot && !snapshot.story_state.tangJiNameKnown && document.body.innerText.includes('唐霁')),
    };
    return () => { delete (window as Window & { __daryaCanonicalGame?: unknown }).__daryaCanonicalGame; };
  }, [snapshot]);

  const scene = snapshot ? canonicalSceneRegistry.get(snapshot.current_scene) : null;
  const presentation = useMemo(() => snapshot ? runtime.restore(snapshot) : null, [snapshot]);
  const story = snapshot?.story_state;
  const broadcastStep = Number(story?.unpoweredBroadcastStep || 0);
  const cameraStep = Number(story?.cameraSequenceStep || 0);

  const inspect = async (hotspot: SceneHotspot) => {
    if (!snapshot || busy) return;
    if (snapshot.current_scene === 'scene01') {
      if (hotspot.id === 'ENTER_SCHOOL') {
        if (!story?.scene01ReadyToEnter) { setLocalBeat({ speaker: '顾遥', text: '先在外面看看，至少确认两处再进去。' }); return; }
        await run({ type: 'ENTER_SCENE02' }); return;
      }
      const updated = await run({ type: 'INSPECT_SCENE01', hotspotId: hotspot.id });
      if (updated) setLocalBeat(scene01Dialogue[hotspot.id]);
      return;
    }
    if (snapshot.current_scene === 'scene02') {
      if (hotspot.id === 'OLD_SPEAKER') {
        if (story?.unpoweredBroadcastCompleted) setLocalBeat({ speaker: '', text: '没有反应。' });
        else await run({ type: 'START_BROADCAST' });
        return;
      }
      if (hotspot.id === 'GU_YAO_CAMERA_S02') {
        if (!story?.unpoweredBroadcastCompleted) { setLocalBeat({ speaker: '顾遥', text: '先拍着。回去以后再慢慢看。' }); return; }
        if (story.cameraObservationConflictConfirmed) { setLocalBeat({ speaker: '顾遥', text: '画面还在。先别动它。' }); return; }
        await run({ type: 'START_CAMERA' }); return;
      }
      if (hotspot.id === 'SCENE03_DOOR') {
        if (!story?.cameraObservationConflictConfirmed) { setLocalBeat({ speaker: '沈知', text: '先把走廊里的异常确认清楚。' }); return; }
        await run({ type: 'ENTER_SCENE03' }); return;
      }
      const updated = await run({ type: 'INSPECT_SCENE02', hotspotId: hotspot.id });
      if (updated) setLocalBeat(scene02ExplorationDialogue[hotspot.id]);
      return;
    }
    if (snapshot.current_scene === 'scene03') {
      if (hotspot.id === 'ARCHIVE_DRAWER' && !story?.evacRosterFound) { setLocalBeat({ speaker: '沈知', text: '先看看桌上的疏散表。' }); return; }
      if (hotspot.id === 'TEACHER_DESK' && !(story?.evacRosterFound && story?.temporaryVisitorRecordFound)) { setLocalBeat({ speaker: '', text: '教师桌里只剩一些散页。' }); return; }
      if (hotspot.id === 'GU_YAO_CAMERA' && !story?.tangJiNameKnown) { setLocalBeat({ speaker: '顾遥', text: '我留着刚才那段。' }); return; }
      const updated = await run({ type: 'INSPECT_SCENE03', hotspotId: hotspot.id });
      if (!updated) return;
      if (hotspot.id === 'EVAC_ROSTER') setDetail('roster');
      else if (hotspot.id === 'ARCHIVE_DRAWER') setDetail('supplement');
      else if (hotspot.id === 'TEACHER_DESK') setDetail('identity');
      else if (hotspot.id === 'GU_YAO_CAMERA') setDetail('cameraCompare');
      else if (hotspot.id === 'BLACKBOARD') setLocalBeat({ speaker: '', text: '黑板上只有擦过的粉笔印。' });
      else if (hotspot.id === 'OLD_NOTICE') setLocalBeat({ speaker: '', text: '公告栏只剩几张褪色的安全流程纸。' });
      else if (hotspot.id === 'WINDOW') setLocalBeat({ speaker: '', text: '窗外还能听见海风。' });
      else if (hotspot.id === 'SHEN_ZHI') setLocalBeat({ speaker: '沈知', text: story?.evacRosterFound ? '这批记录应该都在这间教室附近。' : '先把当年的确认表找出来。' });
      else if (hotspot.id === 'DARYA') setLocalBeat({ speaker: 'DARYA', text: story?.humanTruthFragment01 ? '先把这些记录留着。' : '先看记录。' });
      return;
    }
    if (snapshot.current_scene === 'scene04') {
      const updated = await run({ type: 'INSPECT_SCENE04', hotspotId: hotspot.id });
      if (!updated) return;
      const copy: Record<string, StoryBeat> = {
        BROADCAST_CONSOLE: { speaker: '周野', text: '总电源仍然是断的。台面上却有一组推子停在播出位置。' },
        PATCH_PANEL: { speaker: '顾遥', text: '这根线刚被动过。它绕开了纸上标的那一路。' },
        ROUTE_LEDGER: { speaker: '沈知', text: '记录说信号该去东楼；现在的跳线却指向旧广播室自己。两份状态，至少有一份已经失效。' },
        COAST_WINDOW: { speaker: '', text: '海风从窗框的缝里进来，纸页轻轻抬了一角。' },
      };
      setLocalBeat(copy[hotspot.id]);
    }
  };

  if (loadState !== 'ready' || !snapshot || !scene || !presentation) {
    return <section className={styles.surface}><div className={styles.loading}>{loadState === 'loading' ? <><LoaderCircle className={styles.spin} />正在读取调查进度…</> : <><p>{error || '无法读取调查进度。'}</p><button type="button" onClick={() => void load()}><RotateCcw size={16} />重试</button></>}</div></section>;
  }

  if (snapshot.current_scene === 'scene04Ready') {
    return (
      <section className={styles.surface} data-testid="darya-game-surface" data-scene="scene04Ready">
        <div className={styles.readyCard}><small>DARYA · INVESTIGATION</small><h2>旧广播室的方向已经明确</h2><p>117 人应确认，116 人已确认；临时访客五人，已确认四人。摄像机画面中的学生已与旧安全联络记录建立联系。</p><strong>门就在走廊尽头。</strong><button type="button" onClick={() => void run({ type: 'ENTER_SCENE04' })}>进入旧广播室</button></div>
      </section>
    );
  }

  if (snapshot.current_scene === 'scene05Ready') {
    return <section className={styles.surface} data-testid="darya-game-surface" data-scene="scene05Ready"><div className={styles.readyCard}><small>DARYA · INVESTIGATION</small><h2>下一段信号已经明确</h2><p>纸面记录不能替代眼前状态。那条被改过的线路通向旧礼堂一侧。</p><strong>我们还没有解开广播的含义，但已经知道下一步该去哪里。</strong><button type="button" onClick={onBack}>回到 DARYA</button></div></section>;
  }

  const cameraRealityEmpty = snapshot.current_scene === 'scene02' && cameraStep === 3;
  const cameraImageVisible = snapshot.current_scene === 'scene02' && (cameraStep === 2 || cameraStep === 5);
  const activeActors = cameraRealityEmpty ? [] : presentation.actors;
  const activeBeat = broadcastStep > 0 ? unpoweredBroadcastBeats[broadcastStep - 1]
    : cameraStep > 0 ? cameraTangJiBeats[cameraStep - 1] : localBeat;
  const inspected = snapshot.current_scene === 'scene01' ? story?.inspectedHotspots || []
    : snapshot.current_scene === 'scene02' ? story?.scene02InspectedHotspots || []
      : snapshot.current_scene === 'scene03' ? story?.scene03Hotspots || [] : story?.scene04Hotspots || [];

  return (
    <section className={styles.surface} data-testid="darya-game-surface" data-scene={snapshot.current_scene}>
      <header className={styles.topbar}>
        <button type="button" onClick={onBack}><ArrowLeft size={16} />返回 DARYA</button>
        <div><small>DARYA · INVESTIGATION</small><strong>《风眼旧校》</strong></div>
        <span>{busy ? '保存中…' : '已自动保存'}</span>
      </header>
      <div className={`${styles.stage} ${snapshot.current_scene === 'scene04' ? scene04Styles.broadcastRoom : ''}`} data-testid="game-stage">
        {presentation.background && <img className={styles.background} src={presentation.background} alt="" />}
        <div className={styles.vignette} />
        {snapshot.current_scene === 'scene04' && <div className={scene04Styles.broadcastSet} aria-hidden="true"><div className={scene04Styles.windowGlow} /><div className={scene04Styles.consoleDesk}><i /><i /><i /><i /><span /></div><div className={scene04Styles.patchPanel}>{Array.from({ length: 15 }, (_, index) => <i key={index} />)}<b /><b /><b /></div><div className={scene04Styles.routePaper}><i /><i /><i /><i /></div></div>}
        <div className={styles.sceneLabel}><strong>{presentation.title}</strong><small>{presentation.subtitle}</small></div>
        {activeActors.map((actor) => <div key={actor.id} className={styles.actor} data-game-actor={actor.id} data-stage-visible="true" data-depth={actor.depth} style={{ left: actor.left, height: actor.height }}><img src={ACTOR_ASSETS[actor.id].src} alt={ACTOR_ASSETS[actor.id].name} /></div>)}
        {cameraImageVisible && <div className={styles.cameraView} data-testid="camera-layer"><img src="/darya-game/scene02/scene02-hallway.png" alt="摄像机走廊画面" /><img className={styles.cameraPerson} src="/darya-game/scene02/camera-tangji.png" alt="画面里的旧校服学生" /><span><Camera size={14} /> REC · {cameraStep === 5 ? '00:17:44' : '00:17:42'}</span></div>}
        {cameraRealityEmpty && <div className={styles.realityEmpty} data-testid="reality-empty"><small>REALITY</small><p>那个位置是空的。</p></div>}
        {!story?.openingCompleted && snapshot.current_scene === 'scene01' ? <div className={styles.startCard}><small>风眼旧校 · SCENE 01</small><h1>旧校门</h1><div>{scene01Opening.map((beat) => <p key={`${beat.speaker}-${beat.text}`}><strong>{beat.speaker}</strong>{beat.text}</p>)}</div><button type="button" onClick={() => void run({ type: 'BEGIN_SCENE01' })} disabled={busy}>开始调查</button></div>
          : !activeBeat && !detail && <div className={styles.hotspots}>{scene.hotspots.filter((hotspot) => hotspot.id !== 'SCENE03_DOOR' || story?.cameraObservationConflictConfirmed).map((hotspot) => <button key={hotspot.id} type="button" className={styles.hotspot} data-core={hotspot.core || undefined} data-done={inspected.includes(hotspot.id) || undefined} data-hotspot={hotspot.id} style={{ left: hotspot.x, top: hotspot.y }} onClick={() => void inspect(hotspot)}><span>{hotspot.label}</span></button>)}</div>}
        {activeBeat && <StoryDialogue beat={activeBeat} busy={busy} onNext={() => {
          if (broadcastStep > 0) void run({ type: 'ADVANCE_BROADCAST' });
          else if (cameraStep > 0) void run({ type: 'ADVANCE_CAMERA' });
          else setLocalBeat(null);
        }} />}
        {detail && <EvidenceDetail kind={detail} onClose={() => setDetail(null)} />}
        {snapshot.current_scene === 'scene03' && story?.scene04Ready && !detail && !activeBeat && <button type="button" className={styles.continueButton} onClick={() => void run({ type: 'ENTER_SCENE04_READY' })}>继续前往旧广播室 <ChevronRight size={17} /></button>}
        {snapshot.current_scene === 'scene04' && story?.routeConflictObserved && !story.daryaCognitiveMomentOpened && !activeBeat && <div className={scene04Styles.cognitiveMoment}><small>DARYA</small><p>问题不只是“哪张纸写错了”。你现在卡住的是：当记录和眼前状态冲突，怎样判断哪一个还能作为依据。</p><div><button type="button" onClick={async () => { supportOrigin.current = true; const updated = await run({ type: 'OPEN_DARYA_SUPPORT' }); if (updated) onRequestDaryaSupport({ scene_id: 'scene04', event_id: `scene04-route-conflict-r${updated.revision}`, story_state_refs: ['broadcastRoomInspected', 'patchPanelInspected', 'routeLedgerInspected', 'routeConflictObserved'], visible_clue_refs: ['BROADCAST_CONSOLE', 'PATCH_PANEL', 'ROUTE_LEDGER'], player_choice: '和 DARYA 理一理', cognitive_need_hint: 'conflicting recorded and observed state; authority judgment' }); else supportOrigin.current = false; }}>和 DARYA 理一理</button><button type="button" onClick={() => void run({ type: 'DEFER_DARYA_SUPPORT', playerChoice: '先记下，继续调查' })}>先记下，继续调查</button></div></div>}
        {snapshot.current_scene === 'scene04' && story?.daryaSupportReturned && !story.scene04InvestigationCompleted && !activeBeat && <button type="button" className={styles.continueButton} onClick={() => void run({ type: 'COMPLETE_SCENE04' })}>回到跳线板前 <ChevronRight size={17} /></button>}
        {snapshot.current_scene === 'scene04' && story?.scene05Ready && !activeBeat && <button type="button" className={styles.continueButton} onClick={() => void run({ type: 'ENTER_SCENE05_READY' })}>沿着线路继续 <ChevronRight size={17} /></button>}
      </div>
      {error && <p className={styles.error} role="alert">{error}</p>}
    </section>
  );
}
