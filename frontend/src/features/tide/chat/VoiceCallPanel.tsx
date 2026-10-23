import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Mic, MicOff, PhoneCall, PhoneOff, Volume2 } from 'lucide-react';
import { tideChatApi } from './tideChatApi';
import { useTideStore } from '../state/tideStore';
import { Blackboard, NarrationCaption } from '../../blackboard';
import type { BoardNarration } from '../../blackboard';
import type { BlackboardScript } from '../../blackboard/types';
import { daryaPitchOf, daryaRateOf, readDaryaPitch, readDaryaSpeed, readDaryaVoice } from '../voice/daryaVoice';
import styles from './VoiceCallPanel.module.css';

interface VoiceCallPanelProps {
  onEnd: () => void;
}

type CallPhase = 'listening' | 'thinking' | 'speaking' | 'muted';

const PHASE_COPY: Record<CallPhase, string> = {
  listening: '在听你说……',
  thinking: 'DARYA 在想……',
  speaking: 'DARYA 在说……',
  muted: '麦克风已静音',
};

/** 从原始回复里提取可朗读文本：黑板内容改为口头提示，代码不朗读。 */
function speakableText(raw: string): string {
  let t = raw || '';
  t = t.replace(/\[tide_sidecar[^\]]*\][\s\S]*?\[\/tide_sidecar\]/gi, ' ');
  t = t.replace(/\[tide_center\]/gi, ' ').replace(/\[\/tide_center\]/gi, ' ');
  t = t.replace(/\[darya_learning_fit\][\s\S]*?\[\/darya_learning_fit\]/gi, ' ');
  t = t.replace(/\[blackboard\][\s\S]*?\[\/blackboard\]/gi, ' 我把要点写在黑板上了。 ');
  t = t.replace(/<blackboard>[\s\S]*?<\/blackboard>/gi, ' 我把要点写在黑板上了。 ');
  t = t.replace(/```[\s\S]*?```/g, ' 代码我直接放在对话里，你可以看。 ');
  t = t.replace(/\[([^\]]*)\]\([^)]*\)/g, '$1');
  t = t.replace(/\[[^\]]*\]/g, ' ');
  t = t.replace(/[#*`>_~|]/g, ' ');
  t = t.replace(/\s+/g, ' ').trim();
  return t.slice(0, 600);
}

/**
 * 连续语音通话：mic → 转写 → 同一个会话（store.send）→ DARYA 回复 → TTS 播报。
 * 没有数字人/头像：只有转写与回复文字 + 克制的控制行。黑板走 canonical 投影（右侧）。
 */
export function VoiceCallPanel({ onEnd }: VoiceCallPanelProps) {
  const send = useTideStore((state) => state.send);
  const messages = useTideStore((state) => state.messages);
  const streamState = useTideStore((state) => state.streamState);
  const abortStream = useTideStore((state) => state.abort);
  /** 通话里也要看得见后台在干什么：研究作业有真实阶段，不是装饰。 */
  const researchJob = useTideStore((state) => state.researchJob);
  const researchState = useTideStore((state) => state.researchState);

  const [phase, setPhase] = useState<CallPhase>('listening');
  const [transcript, setTranscript] = useState('');
  const [replyText, setReplyText] = useState('');
  const [boardScript, setBoardScript] = useState<BlackboardScript | null>(null);
  /** 板子是不是还在落笔。通话的打断判定要看得见它，验收也要。 */
  const [drawing, setDrawing] = useState(false);
  /** DARYA 此刻在板子上讲的那一句。摆在板子上方 —— 板面只留粉笔。 */
  const [boardNarration, setBoardNarration] = useState<BoardNarration>({ saying: null, leaving: null });
  const [ttsHint, setTtsHint] = useState('');
  /**
   * 说话用哪个声音、多快、什么音调。设置里改，通话开始时定下来；改了下次通话生效。
   * 放在 ref 里是为了让 speak 的回调保持稳定——否则改一次设置就会重建 speak，
   * 连带把它挂着的播报 effect 也重跑一遍。
   */
  const speechOptionsRef = useRef({
    voice: readDaryaVoice(tideChatApi.currentUserId()),
    rate: daryaRateOf(readDaryaSpeed(tideChatApi.currentUserId())),
    pitch: daryaPitchOf(readDaryaPitch(tideChatApi.currentUserId())),
  });

  const streamRef = useRef<MediaStream | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const ttsBlobRef = useRef<Blob | null>(null);
  const ttsAbortRef = useRef<AbortController | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const rafRef = useRef<number | null>(null);
  const mutedRef = useRef(false);
  const endedRef = useRef(false);
  const speakingRef = useRef(false);
  const silenceMsRef = useRef(0);
  const noiseFloorRef = useRef(0.02);
  const speechSeenRef = useRef(false);
  const spokeRef = useRef('');
  const baselineRef = useRef<string | null>(null);
  const transcribingRef = useRef(false);
  const phaseRef = useRef<CallPhase>('listening');
  phaseRef.current = phase;
  /** 黑板是不是还在落笔。为真时"用户说话 350ms 就停播"的判定要往后推。 */
  const drawingRef = useRef(false);
  /** 已经累积够说话的时长、但被黑板挡住的那次打断：等这一批画完再执行。 */
  const deferredBargeInRef = useRef(false);

  const cleanupAudio = useCallback(() => {
    // 打断/结束都要连同"正在合成"的那一次请求一起停掉，否则声音会晚到。
    ttsAbortRef.current?.abort();
    ttsAbortRef.current = null;
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current = null;
    }
  }, []);

  /** 停掉播报、回到听用户说话的状态 */
  const stopSpeaking = useCallback(() => {
    cleanupAudio();
    setPhase('listening');
    speakingRef.current = false;
  }, [cleanupAudio]);

  /**
   * 真正停掉「在飞的那一轮」，并等它落定。
   *
   * `store.send` 的第一行是 `if (get().activeController || get().sendInFlight) return;`
   * —— 被打断的那一轮如果还在流式，学生刚说的话会被**静默丢掉**：TTS 停了、
   * 界面显示「在想」，然后旧答案的后半段接着念完。2026-09-22 实测的正是这个形状。
   *
   * 但只调 abort() 还不够：abort 只是发出中止信号，`activeController` 是在那次
   * fetch 的 catch 里才清掉的，紧接着 send() 仍然会被 guard 挡掉——2026-09-22
   * 第二次实测就是这样：页面显示「已停止这次回答」，而插进来的那句话根本没落库。
   * 所以这里要等到 store 真的空下来（catch 已经跑完）再返回。
   */
  const interruptInFlight = useCallback(async () => {
    if (!useTideStore.getState().activeController) return true;
    abortStream();
    const started = Date.now();
    while (useTideStore.getState().activeController && Date.now() - started < 2000) {
      await new Promise((resolve) => setTimeout(resolve, 40));
    }
    return !useTideStore.getState().activeController;
  }, [abortStream]);

  /**
   * 黑板在落笔时不让出：记下板子还在写。
   *
   * 这期间用户说话不算数 —— 停播的判定推迟到这一批画写动作做完再执行（见 VAD 循环）。
   * 讲一半被切掉、板子上留着半张图，比晚半秒回应更难理解。
   * 手动"打断播报"按钮走的是另一条路，随时有效。
   */
  const handleBoardBusy = useCallback((busy: boolean) => {
    drawingRef.current = busy;
    setDrawing(busy);
  }, []);

  const stopRecorder = useCallback(() => {
    try {
      if (recorderRef.current && recorderRef.current.state === 'recording') recorderRef.current.stop();
    } catch { /* already stopped */ }
  }, []);

  const segmentStartedAtRef = useRef(Date.now());
  const startSegment = useCallback(() => {
    if (endedRef.current || mutedRef.current || !streamRef.current) return;
    if (recorderRef.current && recorderRef.current.state === 'recording') return;
    segmentStartedAtRef.current = Date.now();
    speechSeenRef.current = false;
    try {
      const mime = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
        ? 'audio/webm;codecs=opus'
        : MediaRecorder.isTypeSupported('audio/webm') ? 'audio/webm' : '';
      const recorder = new MediaRecorder(streamRef.current, mime ? { mimeType: mime } : undefined);
      const w0 = window as unknown as { __segLog?: string[] };
      (w0.__segLog = w0.__segLog || []).push('start@' + Date.now() % 100000);
      // 每段独立 chunks：段停止后 VAD 循环会立刻开新段，共享缓冲会被新段清空。
      const chunks: Blob[] = [];
      recorder.ondataavailable = (event) => {
        if (event.data && event.data.size > 0) chunks.push(event.data);
      };
      recorder.onstop = async () => {
        const blob = new Blob(chunks, { type: mime || 'audio/webm' });
        const hadSpeech = speechSeenRef.current;
        speechSeenRef.current = false;
        (window as unknown as { __segLog?: string[] }).__segLog?.push('stop chunks=' + chunks.length + ' bytes=' + blob.size + ' speech=' + hadSpeech);
        if (endedRef.current) return;
        // 这一段没有探测到人声（纯环境噪声/静音）→ 直接丢弃，不浪费转写。
        if (!hadSpeech || blob.size < 2600) {
          startSegment();
          return;
        }
        transcribingRef.current = true;
        try {
          (window as unknown as { __segLog?: string[] }).__segLog?.push('transcribing bytes=' + blob.size);
          const result = await tideChatApi.transcribeAudio(blob);
          (window as unknown as { __segLog?: string[] }).__segLog?.push('transcribed text=' + (result.text || '').slice(0, 30));
          const text = (result.text || '').trim();
          if (endedRef.current) return;
          if (text) {
            // 打断：停掉正在播的 TTS，**并且真正停掉在飞的那一轮**，再发新话。
            cleanupAudio();
            setTranscript(text);
            setPhase('thinking');
            const idle = await interruptInFlight();
            if (!idle) {
              // 说不清就别说。宁可他再说一遍，也不要显示成「在想」然后什么也没发生。
              setTtsHint('上一轮还没停下来，这句再说一次。');
              setPhase(mutedRef.current ? 'muted' : 'listening');
              return;
            }
            // 第三个参数告诉后端：这一轮的回答会被朗读出来。模型据此决定说多少、
            // 怎么说；不带它的话模型不知道自己是在被听，会按「正常长度」写一整套。
            await send(text, false, true);
          }
        } catch (reason) {
          (window as unknown as { __segLog?: string[] }).__segLog?.push('transcribe-error ' + String(reason).slice(0, 80));
          setTtsHint('这句没能听清，再说一次。');
        } finally {
          transcribingRef.current = false;
          startSegment();
        }
      };
      recorder.start();
      recorderRef.current = recorder;
    } catch {
      setTtsHint('麦克风不可用。');
    }
  }, [cleanupAudio, interruptInFlight, send]);

  const speak = useCallback(async (text: string) => {
    if (endedRef.current || !text) return;
    setPhase('speaking');
    setTtsHint('');
    const controller = new AbortController();
    ttsAbortRef.current = controller;
    try {
      // 声音、语速、音调都来自设置里的选择。以前这里写死 zh-CN-XiaoxiaoNeural，
      // 后端其实一直收这几个参数——所以「换个声音、调快慢」以前产品里做不到。
      const blob = await tideChatApi.synthesizeSpeech(text, speechOptionsRef.current, controller.signal);
      if (endedRef.current || controller.signal.aborted) return;
      ttsBlobRef.current = blob;
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      audioRef.current = audio;
      audio.onended = () => {
        if (endedRef.current) return;
        URL.revokeObjectURL(url);
        if (audioRef.current === audio) audioRef.current = null;
        setPhase(mutedRef.current ? 'muted' : 'listening');
      };
      await audio.play();
    } catch {
      if (endedRef.current || controller.signal.aborted) return;
      setTtsHint('语音播报没有成功；文字回复已经在对话里。');
      setPhase(mutedRef.current ? 'muted' : 'listening');
    } finally {
      if (ttsAbortRef.current === controller) ttsAbortRef.current = null;
    }
  }, []);

  /** 最新一条已完成的助手回复，以及它在消息里的位置（位置能区分两轮一样的话术）。 */
  const lastTurn = useMemo(() => {
    let index = -1;
    messages.forEach((message, at) => {
      if (message.role === 'assistant' && message.status === 'complete') index = at;
    });
    if (index < 0) return null;
    return { message: messages[index], key: `${index}\u0000${messages[index].content.trim()}` };
  }, [messages]);
  const lastAssistantKey = lastTurn?.key ?? null;

  // 回复完成 → TTS 播报（只播通话开始之后的新回复；不重播旧消息）
  useEffect(() => {
    if (endedRef.current) return;
    const lastAssistant = lastTurn?.message ?? null;
    if (!lastAssistant || lastAssistant.status !== 'complete') return;
    // 一轮说一次，键是"第几条助手消息 + 内容"。
    //
    // 只按内容判重会漏掉真实的一轮：DEMONSTRATE 的话术是固定的一句，连着两轮
    // 长得一模一样，第二次就被当成重播跳过 —— 通话于是停在"在想…"，既不说话
    // 也不回到听的状态。只按消息 id 也不行：这一轮结束后 store 会把流式 id
    // 换成持久化 id，那样同一轮会被念两遍。
    const stamp = lastTurn!.key;
    if (baselineRef.current === null) {
      // 刚进通话时最后一条回复是旧的，不重播它。
      baselineRef.current = stamp;
      return;
    }
    if (stamp === spokeRef.current || stamp === baselineRef.current) return;
    spokeRef.current = stamp;
    const spoken = speakableText(lastAssistant.content);
    setReplyText(spoken || '（这一轮以文字和黑板呈现。）');
    void speak(spoken);
  }, [messages, speak, streamState, lastAssistantKey]);

  // 这一轮已经结束了却没轮到我播报（例如就是通话开始前的那条回复），
  // 别把通话停在"在想…"里 —— 麦克风还在录，用户会以为 DARYA 卡住了。
  useEffect(() => {
    if (endedRef.current || phaseRef.current !== 'thinking') return;
    if (!lastAssistantKey) return;
    setPhase(mutedRef.current ? 'muted' : 'listening');
  }, [lastAssistantKey, streamState]);

  /**
   * 黑板跟着最新一轮回复走。
   *
   * 和播报分开：DEMONSTRATE 的回复话术是固定的，连着两轮话术一样时上面的
   * 播报逻辑会提前返回，板子要是挂在它里面就会停在上一张图。
   */
  /** 后端在真实阶段点发出来的那一句（在听的时候这就是唯一的动静） */
  const liveProgress = useMemo(() => {
    const last = messages[messages.length - 1];
    return last && last.role === 'assistant' && last.status === 'streaming'
      ? last.progress || ''
      : '';
  }, [messages]);

  /**
   * 边干活边聊：后台任务在通话里也要有个说法。
   *
   * 以前通话中「生成」「附件」按钮是藏起来的，长期任务的状态（研究作业有
   * PLANNED→SEARCHING→READING→SYNTHESIZING→REVIEWING→READY 六个真实阶段）
   * 只有这一轮的流式进度会被念出来，任务本身在通话面板里看不见。
   * 学生在听，看不到右栏，所以这里给一行字。
   */
  const jobLine = useMemo(() => {
    if (researchState === 'starting') return '深度研究：正在开始…';
    if (!researchJob) return '';
    const label = researchJob.title || researchJob.question || '';
    if (researchJob.status === 'READY') return `深度研究 · ${label}　做完了`;
    if (researchJob.status === 'PARTIAL') return `深度研究 · ${label}　已有阶段结果`;
    if (researchJob.status === 'FAILED') return `深度研究 · ${label}　这次没做成`;
    return `深度研究 · ${label}　${researchJob.stage_copy || researchJob.status}`;
  }, [researchJob, researchState]);

  /**
   * 任务走到终态时说一句——这是「边干活边聊」里真正会被人听到的那一半。
   *
   * 只在「在听」的时候说：学生在说话、或者 DARYA 正在想/正在说的时候插一句会打架。
   * 所以依赖里带上 phase，等它回到 listening 再判一次；说过一次就靠 stamp 记住。
   */
  const announcedJobRef = useRef('');
  useEffect(() => {
    if (endedRef.current || !researchJob) return;
    const status = researchJob.status;
    if (status !== 'READY' && status !== 'PARTIAL' && status !== 'FAILED') return;
    if (phase !== 'listening') return;
    const stamp = `${researchJob.artifact_id}:${status}`;
    if (announcedJobRef.current === stamp) return;
    announcedJobRef.current = stamp;
    const label = researchJob.title || researchJob.question || '刚才那份研究';
    const notice = status === 'READY'
      ? `嗯，${label}做完了，要不要我讲一下。`
      : status === 'PARTIAL'
        ? `${label}已经有阶段分析和检索记录了，正式报告还需要补证据。要不要我讲一下已有结果。`
        : `哎，${label}这次没做成，回头我说说卡在哪。`;
    // 说了什么就要显示什么：只 speak 不 setReplyText 的话，面板上 DARYA 那行还停在
    // 占位文字，而它其实已经开口了——「说出口的和看得见的不一致」正是这个产品
    // 一直在拒绝的形状。
    setReplyText(notice);
    void speak(notice);
  }, [phase, researchJob, speak]);

  const latestBoardScript = lastTurn?.message.conversationAction?.blackboard_script ?? null;  // 通话开始时就已经挂着的那一板是翻回来的，直接给终态；通话中画出来的才边讲边画。
  const liveMessageIds = useTideStore((state) => state.liveMessageIds);
  const latestBoardAnimate = Boolean(lastTurn && liveMessageIds.includes(lastTurn.message.id));

  useEffect(() => {
    setBoardScript(latestBoardScript);
  }, [latestBoardScript]);

  // VAD 监听：说话后静音 1.3s 结束一段；播报中持续发声 → 打断
  useEffect(() => {
    let cancelled = false;
    const start = async () => {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        if (cancelled) {
          stream.getTracks().forEach((track) => track.stop());
          return;
        }
        streamRef.current = stream;
        const ctx = new AudioContext();
        audioCtxRef.current = ctx;
        const source = ctx.createMediaStreamSource(stream);
        const analyser = ctx.createAnalyser();
        analyser.fftSize = 512;
        source.connect(analyser);
        const buffer = new Uint8Array(analyser.frequencyBinCount);
        let speechMs = 0;
        // 先校准 1.2s 环境噪声底（假设备/驱动都可能带底噪），再开始判定。
        let calibrateN = 0;
        let calibrateSum = 0;
        let calibrateMax = 0;
        const tick = () => {
          if (cancelled || endedRef.current) return;
          analyser.getByteTimeDomainData(buffer);
          let sum = 0;
          for (let i = 0; i < buffer.length; i += 1) {
            const v = (buffer[i] - 128) / 128;
            sum += v * v;
          }
          const rms = Math.sqrt(sum / buffer.length);
          const w = window as unknown as { __vadLog?: number[] };
          if (!Array.isArray(w.__vadLog)) w.__vadLog = [];
          w.__vadLog.push(Math.round(rms * 1000) / 1000);
          if (w.__vadLog.length > 240) w.__vadLog.shift();
          if (calibrateN < 12) {
            calibrateN += 1;
            calibrateSum += rms;
            calibrateMax = Math.max(calibrateMax, rms);
            if (calibrateN === 12) {
              // 校准窗里已经在说话（如外部注入音频）时，不能把语音当噪声底。
              noiseFloorRef.current = calibrateMax > 0.05
                ? 0.02
                : Math.max(0.012, (calibrateSum / 12) * 1.4 + calibrateMax * 0.25);
            }
            rafRef.current = window.setTimeout(tick, 100);
            return;
          }
          const speakGate = Math.max(0.05, noiseFloorRef.current * 2.4);
          const silenceGate = Math.max(0.028, noiseFloorRef.current * 1.3);
          const active = !mutedRef.current && rms > speakGate;
          if (active) {
            speechMs += 100;
            speechSeenRef.current = true;
            silenceMsRef.current = 0;
            if (phaseRef.current === 'speaking' && speechMs >= 350) {
              if (drawingRef.current) {
                // 板子还在写：记下这次打断，等这一批做完再执行。
                deferredBargeInRef.current = true;
              } else {
                stopSpeaking();
              }
            }
          } else {
            speechMs = Math.max(0, speechMs - 60);
            if (!mutedRef.current && rms > silenceGate) silenceMsRef.current = 0;
            else silenceMsRef.current += 100;
          }
          // 被黑板挡下来的那次打断，在每一轮里重新判一次：这一批画完了，
          // 用户还在说（或刚说完、累计时长还没退回去）就现在停播；
          // 已经安静下来就当没发生过，下一次他开口会立刻按原规则打断。
          if (deferredBargeInRef.current && !drawingRef.current) {
            deferredBargeInRef.current = false;
            if (phaseRef.current === 'speaking' && speechMs >= 350) stopSpeaking();
          }
          const recording = recorderRef.current?.state === 'recording';
          // 看门狗：链路意外中断（无录音且不在转写/思考）时重新拉起；时间戳在 startSegment 内统一重置。
          if (!mutedRef.current && !transcribingRef.current && !recording && phaseRef.current !== 'thinking') {
            startSegment();
          }
          if (recording) {
            const spokeEnough = speechMs >= 260;
            const segmentMs = Date.now() - segmentStartedAtRef.current;
            if ((spokeEnough && silenceMsRef.current >= 1300) || segmentMs > 24000) {
              silenceMsRef.current = 0;
              speechMs = 0;
              stopRecorder();
            }
          }
          rafRef.current = window.setTimeout(tick, 100);
        };
        const safeTick = () => {
          try { tick(); }
          catch (error) {
            const w = window as unknown as { __vadErr?: string[] };
            (w.__vadErr = w.__vadErr || []).push(String(error));
          }
        };
        safeTick();
      } catch {
        setTtsHint('拿不到麦克风；请允许麦克风权限后重新开始通话。');
      }
    };
    void start();
    return () => {
      cancelled = true;
      if (rafRef.current) window.clearTimeout(rafRef.current);
    };
  }, [cleanupAudio, startSegment, stopRecorder, stopSpeaking]);

  const endCall = useCallback(() => {
    endedRef.current = true;
    cleanupAudio();
    stopRecorder();
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    audioCtxRef.current?.close().catch(() => undefined);
    onEnd();
  }, [cleanupAudio, onEnd, stopRecorder]);

  const toggleMute = () => {
    const next = !mutedRef.current;
    mutedRef.current = next;
    if (next) {
      stopRecorder();
      cleanupAudio();
      setPhase('muted');
    } else {
      silenceMsRef.current = 0;
      setPhase('listening');
      startSegment();
    }
  };

  const bargeIn = () => {
    cleanupAudio();
    mutedRef.current = false;
    setPhase('listening');
    startSegment();
  };

  const replay = () => {
    if (!ttsBlobRef.current) return;
    const url = URL.createObjectURL(ttsBlobRef.current);
    const audio = new Audio(url);
    audioRef.current = audio;
    void audio.play();
  };

  return (
    <div className={styles.call} data-testid="darya-voice-call" data-phase={phase}>
      <div className={styles.callHead}>
        <PhoneCall size={15} aria-hidden="true" />
        <strong>正在和 DARYA 通话</strong>
        <span className={styles.phase}>
          {/* 通话里人在听，不看屏幕；等待期间说出后端正在做什么，
              比一直停在「DARYA 在想……」更像有人在那边干活。 */}
          {phase === 'thinking' && liveProgress ? liveProgress : PHASE_COPY[phase]}
        </span>
      </div>
      <div className={styles.lines}>
        <p className={styles.youLine} data-testid="darya-voice-transcript">
          <span>你</span>
          {transcript || '（等待你说话）'}
        </p>
        <p className={styles.daryaLine} data-testid="darya-voice-reply">
          <span>DARYA</span>
          {replyText || '（DARYA 会在这里回答，同时用声音说出来）'}
        </p>
        {jobLine && (
          <p className={styles.hint} role="status" data-testid="darya-voice-job">{jobLine}</p>
        )}
        {ttsHint && <p className={styles.hint} role="status" data-testid="darya-voice-hint">{ttsHint}</p>}
      </div>
      {boardScript && (
        <div className={styles.boardBlock}>
          {/* 逐句讲解在板子上方，不压在板面上 */}
          <NarrationCaption {...boardNarration} />
          <div
            className={styles.boardStage}
            style={{ aspectRatio: `${boardScript.size?.w ?? 1600} / ${boardScript.size?.h ?? 1000}` }}
            data-testid="darya-voice-blackboard"
            data-drawing={drawing ? 'true' : 'false'}
          >
            <Blackboard
              script={boardScript}
              animate={latestBoardAnimate}
              onBusyChange={handleBoardBusy}
              onNarrationChange={setBoardNarration}
            />
          </div>
        </div>
      )}
      <div className={styles.controls}>
        <button type="button" onClick={toggleMute} aria-pressed={phase === 'muted'} data-testid="darya-voice-mute">
          {phase === 'muted' ? <MicOff size={15} aria-hidden="true" /> : <Mic size={15} aria-hidden="true" />}
          <span>{phase === 'muted' ? '取消静音' : '静音'}</span>
        </button>
        <button type="button" onClick={bargeIn} data-testid="darya-voice-barge">
          <Volume2 size={15} aria-hidden="true" />
          <span>打断播报</span>
        </button>
        {ttsBlobRef.current && (
          <button type="button" onClick={replay} data-testid="darya-voice-replay">
            <Volume2 size={15} aria-hidden="true" />
            <span>重听</span>
          </button>
        )}
        <button type="button" className={styles.end} onClick={endCall} data-testid="darya-voice-end">
          <PhoneOff size={15} aria-hidden="true" />
          <span>结束通话</span>
        </button>
      </div>
    </div>
  );
}
