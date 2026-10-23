import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { MoonStar, Volume2, VolumeX, X } from 'lucide-react';
import styles from './RelaxMoment.module.css';
import { RELAX_COPY_LINES, RELAX_CLOSING_LINE, RELAX_TYPE_MS, RELAX_HOLD_MS, RELAX_FADE_MS } from './relaxCopy';

type LayerType = 'far' | 'mid' | 'near';
type Particle = { x:number; y:number; px:number; py:number; vx:number; vy:number; life:number; maxLife:number; color:string; size:number; twinkle:number; dim:number; grav:number };
type Flash = { x:number; y:number; life:number; maxLife:number; color:string; dim:number };
type AudioState = { type:string|null; volume:number; muted:boolean; currentTime:number; duration:number; paused:boolean; ended:boolean };
type KakiAudioManager = {
  play:(type:'relax_fireworks'|'celebration', options?:{ restart?:boolean; volume?:number; onEnded?:()=>void })=>Promise<boolean>;
  stop:(options?:{ fadeMs?:number; reset?:boolean })=>Promise<void>;
  setVolume:(value:number)=>number;
  toggleMute:()=>boolean;
  getState:()=>AudioState;
};
const COLORS = ['#f6d47a', '#e7a84e', '#fff5d5', '#ffd98b', '#c96843'];
const ROCKET_COLORS = ['#f6d47a', '#ffd98b', '#fff5d5'];
type Rocket = { x:number; y:number; targetY:number; life:number; maxLife:number; color:string; trail:number; speed:number; dim:number; layer:LayerType };
const sound = () => (window as typeof window & { KakiAudioManager?:KakiAudioManager }).KakiAudioManager;

// —— 前 / 中 / 后 三层烟花的视觉参数：越远越淡、越小、越少细节 ——
const LAYER_STYLE = {
  far:  { countMul: .42, alphaMul: .4,  sizeMul: .6,  speedMul: .72 },
  mid:  { countMul: 1,   alphaMul: 1,   sizeMul: 1,   speedMul: 1 },
  near: { countMul: .32, alphaMul: .85, sizeMul: .58, speedMul: .9 },
} as const;
// 各层炸开的高度区间（占视口高度）：远景在中上部（远山之上），中景为主观赏层，近景偏下
const LAYER_Y_RANGE: Record<LayerType, [number, number]> = {
  far:  [.12, .3],
  mid:  [.26, .52],
  near: [.5, .66],
};
// 各层的重力：近景火花更接近余烬，下落感更强
const LAYER_GRAVITY: Record<LayerType, number> = { far: .038, mid: .045, near: .062 };

// 避让区域（归一化坐标）：左上音量、右上月亮/回到学习、左下文字区
const isForbidden = (x: number, y: number) => {
  if (y < .1) return true;                  // 顶部整体留白，不碰月亮与顶栏
  if (x < .18 && y < .14) return true;      // 左上角音量控件
  if (x > .7 && y < .26) return true;       // 右上角月亮 / “回到学习”
  if (x < .46 && y > .68) return true;      // 左下角动态文字区
  return false;
};

const pickLayer = (): LayerType => {
  const roll = Math.random();
  if (roll < .3) return 'far';              // 远景略少
  if (roll < .8) return 'mid';              // 中景为主
  return 'near';                            // 近景点缀，最少
};

export function RelaxMoment({ className = '', compact = false, trigger = true }: { className?: string; compact?: boolean; trigger?: boolean }) {
  const [open, setOpen] = useState(false);
  const [finished, setFinished] = useState(false);
  const [round, setRound] = useState(0);
  const [reducedMotion, setReducedMotion] = useState(false);
  const initialAudio = sound()?.getState();
  const [muted, setMuted] = useState(Boolean(initialAudio?.muted));
  const [volume, setVolume] = useState(initialAudio?.volume ?? .28);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const finishedRef = useRef(false);
  const fallbackTimerRef = useRef<number | null>(null);
  const autoCloseTimerRef = useRef<number | null>(null);

  // —— 打字机状态 ——
  const [typed, setTyped] = useState('');
  const [isTyping, setIsTyping] = useState(false);
  const [fading, setFading] = useState(false);

  const clearFallbackTimer = () => {
    if (fallbackTimerRef.current !== null) {
      window.clearTimeout(fallbackTimerRef.current);
      fallbackTimerRef.current = null;
    }
  };
  const clearAutoCloseTimer = () => {
    if (autoCloseTimerRef.current !== null) {
      window.clearTimeout(autoCloseTimerRef.current);
      autoCloseTimerRef.current = null;
    }
  };

  const startRelax = () => {
    finishedRef.current = false;
    setFinished(false);
    setRound((value) => value + 1);
    setOpen(true);
    setReducedMotion(window.matchMedia('(prefers-reduced-motion: reduce)').matches);
    clearFallbackTimer();
    clearAutoCloseTimer();
    const played = sound()?.play('relax_fireworks', {
      restart: true,
      volume,
      onEnded: () => {
        finishedRef.current = true;
        setFinished(true);
        // 音乐播放完毕：打完提醒句（约 6 秒）后自动退出页面
        clearAutoCloseTimer();
        autoCloseTimerRef.current = window.setTimeout(() => closeRelax(), 6200);
      }
    });
    // 音频不可用或播放失败时，也要让这场烟花安静收场，而不是永远不停
    void Promise.resolve(played).then((ok) => {
      if (!ok) {
        fallbackTimerRef.current = window.setTimeout(() => {
          finishedRef.current = true;
          setFinished(true);
          clearAutoCloseTimer();
          autoCloseTimerRef.current = window.setTimeout(() => closeRelax(), 6200);
        }, 48000);
      }
    });
  };
  const closeRelax = () => {
    clearFallbackTimer();
    clearAutoCloseTimer();
    void sound()?.stop({ fadeMs: 480, reset: true });
    setOpen(false);
  };

  useEffect(() => () => { clearFallbackTimer(); clearAutoCloseTimer(); void sound()?.stop({ fadeMs: 420, reset: true }); }, []);

  useEffect(() => {
    const openFromMascot = () => startRelax();
    window.addEventListener('kaki:open-relax', openFromMascot);
    return () => window.removeEventListener('kaki:open-relax', openFromMascot);
  // startRelax deliberately reads the live audio volume; this listener is mounted once.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // —— 打字机：随机两句 → 打结束句 → 停住 ——
  useEffect(() => {
    if (!open) return;
    if (reducedMotion) {
      setTyped(RELAX_COPY_LINES[0].main);
      return;
    }
    let disposed = false;
    const timers: number[] = [];
    const later = (fn: () => void, ms: number) => {
      timers.push(window.setTimeout(() => { if (!disposed && !finishedRef.current) fn(); }, ms));
    };

    // 随机决定两句的顺序，各打一遍
    const first = Math.random() < .5 ? 0 : 1;
    const second = first === 0 ? 1 : 0;
    const lines = [RELAX_COPY_LINES[first], RELAX_COPY_LINES[second]];

    const beginLine = (line: { main: string; holdMs?: number }, onDone: () => void) => {
      setTyped('');
      setFading(false);
      setIsTyping(true);
      let i = 0;
      const step = () => {
        i += 1;
        setTyped(line.main.slice(0, i));
        if (i < line.main.length) {
          later(step, RELAX_TYPE_MS + Math.random() * 40);
        } else {
          setIsTyping(false);
          later(() => {
            setFading(true);
            later(onDone, RELAX_FADE_MS);
          }, line.holdMs ?? RELAX_HOLD_MS);
        }
      };
      later(step, 140);
    };

    // 两句打完，停留一下，淡出后停住，等待结束时补提醒句
    beginLine(lines[0], () => {
      beginLine(lines[1], () => {
        setTyped('');
        setFading(false);
        setIsTyping(false);
      });
    });

    return () => { disposed = true; timers.forEach((id) => window.clearTimeout(id)); };
  }, [open, round, reducedMotion]);

  // —— 结束时补一句提醒：打出 → 停留 → 淡出消失 ——
  useEffect(() => {
    if (!open || !finished || reducedMotion) return;
    let disposed = false;
    const timers: number[] = [];
    const later = (fn: () => void, ms: number) => {
      timers.push(window.setTimeout(() => { if (!disposed) fn(); }, ms));
    };
    setTyped('');
    setFading(false);
    setIsTyping(true);
    let i = 0;
    const step = () => {
      i += 1;
      setTyped(RELAX_CLOSING_LINE.main.slice(0, i));
      if (i < RELAX_CLOSING_LINE.main.length) {
        later(step, RELAX_TYPE_MS + Math.random() * 40);
      } else {
        setIsTyping(false);
        later(() => {
          setFading(true);
          later(() => {
            setTyped('');
            setFading(false);
          }, RELAX_FADE_MS);
        }, RELAX_CLOSING_LINE.holdMs ?? RELAX_HOLD_MS);
      }
    };
    later(step, 140);
    return () => { disposed = true; timers.forEach((id) => window.clearTimeout(id)); };
  }, [open, finished, reducedMotion]);

  // —— 多层烟花引擎 ——
  useEffect(() => {
    if (!open) return;
    const canvas = canvasRef.current;
    if (!canvas) return;
    const context = canvas.getContext('2d', { alpha: true, desynchronized: true });
    if (!context) return;
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const particles: Particle[] = [];
    const flashes: Flash[] = [];
    const rockets: Rocket[] = [];
    let frame = 0;
    let previousTime = 0;
    let paletteIndex = 0;
    let launchTimer = 0;
    let nextLaunchMs = 700;
    let disposed = false;
    const lowCoreDevice = (navigator.hardwareConcurrency || 8) <= 4;
    // The scene is atmospheric rather than text-bearing, so a modest internal
    // render scale keeps trails smooth on integrated GPUs while CSS still
    // presents a full-resolution stage.
    const dpr = lowCoreDevice ? .45 : .5;
    const PARTICLE_LIMIT = lowCoreDevice ? 220 : 300;
    const ROCKET_SPEED = lowCoreDevice ? 3.8 : 5.2;

    const resize = () => {
      canvas.width = Math.round(innerWidth * dpr);
      canvas.height = Math.round(innerHeight * dpr);
      canvas.style.width = `${innerWidth}px`;
      canvas.style.height = `${innerHeight}px`;
      context.setTransform(dpr, 0, 0, dpr, 0, 0);
    };

    const pickTarget = (layer: LayerType): { x:number; y:number } => {
      const [y0, y1] = LAYER_Y_RANGE[layer];
      for (let attempt = 0; attempt < 14; attempt += 1) {
        const x = .08 + Math.random() * .84;
        const y = y0 + Math.random() * (y1 - y0);
        if (!isForbidden(x, y)) return { x, y };
      }
      // 兜底：中心偏右的安全位置
      return { x: .55 + Math.random() * .15, y: (y0 + y1) / 2 };
    };

    const launchRocket = (layer: LayerType) => {
      if (rockets.length > 3) return;
      const { x, y } = pickTarget(layer);
      const color = ROCKET_COLORS[Math.floor(Math.random() * ROCKET_COLORS.length)];
      rockets.push({ x: x * innerWidth, y: innerHeight + 10, targetY: y * innerHeight, life: 0, maxLife: 44 + Math.random() * 16, color, trail: 0, speed: ROCKET_SPEED * LAYER_STYLE[layer].speedMul, dim: LAYER_STYLE[layer].alphaMul, layer });
    };

    const burst = (x: number, y: number, isMain: boolean, layer: LayerType) => {
      const style = LAYER_STYLE[layer];
      if (particles.length > PARTICLE_LIMIT - 60) return;
      const base = isMain ? (reduced ? 30 : lowCoreDevice ? 34 : 40) : (reduced ? 14 : lowCoreDevice ? 14 : 18);
      const count = Math.max(6, Math.round(base * style.countMul));
      const color = COLORS[paletteIndex % COLORS.length];
      paletteIndex += 1;
      flashes.push({ x, y, life: 0, maxLife: isMain ? 20 : 12, color, dim: style.alphaMul });
      for (let index = 0; index < count && particles.length < PARTICLE_LIMIT; index += 1) {
        const angle = (Math.PI * 2 * index) / count + Math.random() * .04;
        const spoke = index % 5 === 0 ? 1.35 : index % 3 === 0 ? .65 : 1;
        const speed = (isMain ? 3.5 + Math.random() * 4.5 : 2 + Math.random() * 3) * spoke * style.speedMul;
        const life = isMain ? 70 + Math.random() * 30 : 40 + Math.random() * 25;
        particles.push({ x, y, px: x, py: y, vx: Math.cos(angle) * speed, vy: Math.sin(angle) * speed, life: 0, maxLife: life, color, size: (isMain ? 1.4 + Math.random() * 1.6 : .8 + Math.random() * 1) * style.sizeMul, twinkle: Math.random() * Math.PI * 2, dim: style.alphaMul, grav: LAYER_GRAVITY[layer] });
      }
    };

    const draw = (time: number) => {
      if (document.hidden) {
        if (!disposed && !reduced) frame = requestAnimationFrame(draw);
        return;
      }
      context.clearRect(0, 0, innerWidth, innerHeight);
      const elapsed = previousTime ? Math.min(50, time - previousTime) : 16.7;
      previousTime = time;

      // 有呼吸感地发射：节奏偏慢，偶尔多停一拍
      launchTimer += elapsed;
      if (!reduced && !finishedRef.current && launchTimer >= nextLaunchMs) {
        launchTimer = 0;
        launchRocket(pickLayer());
        if (Math.random() > .8) {
          const second = pickLayer();
          window.setTimeout(() => launchRocket(second), 140 + Math.random() * 220);
        }
        nextLaunchMs = 700 + Math.random() * 900;
        if (Math.random() < .12) nextLaunchMs += 1400 + Math.random() * 800; // 呼吸感：偶尔空一拍
      }

      // Draw rockets with trails
      for (let index = rockets.length - 1; index >= 0; index -= 1) {
        const rocket = rockets[index];
        rocket.life += 1;
        const progress = rocket.life / rocket.maxLife;
        rocket.y -= rocket.speed * (1 - progress * .3);
        // Trail
        context.globalAlpha = .35 * rocket.dim;
        context.strokeStyle = '#ffd98b';
        context.lineWidth = 2.2;
        context.beginPath();
        context.moveTo(rocket.x, rocket.y + 3);
        context.lineTo(rocket.x, rocket.y + 16 + progress * 22);
        context.stroke();
        // Rocket head glow
        context.globalAlpha = .72 * rocket.dim;
        context.fillStyle = rocket.color;
        context.beginPath();
        context.arc(rocket.x, rocket.y, 2.8, 0, Math.PI * 2);
        context.fill();
        context.globalAlpha = .45 * rocket.dim;
        context.fillStyle = '#fff8d9';
        context.beginPath();
        context.arc(rocket.x, rocket.y, 5.5, 0, Math.PI * 2);
        context.fill();
        context.globalAlpha = 1;
        if (rocket.y <= rocket.targetY || rocket.life >= rocket.maxLife) {
          burst(rocket.x, rocket.y, true, rocket.layer);
          if (Math.random() > .65) {
            const bx = rocket.x + (Math.random() - .5) * 40;
            const by = rocket.y + (Math.random() - .5) * 30;
            if (!isForbidden(bx / innerWidth, by / innerHeight)) burst(bx, by, false, rocket.layer);
          }
          rockets.splice(index, 1);
        }
      }

      // Draw explosion flashes
      context.globalCompositeOperation = 'lighter';
      for (let index = flashes.length - 1; index >= 0; index -= 1) {
        const flash = flashes[index];
        flash.life += 1;
        const progress = flash.life / flash.maxLife;
        const radius = 8 + progress * 56;
        context.globalAlpha = Math.max(0, (1 - progress) * .22) * flash.dim;
        context.fillStyle = flash.color;
        context.beginPath();
        context.arc(flash.x, flash.y, radius, 0, Math.PI * 2);
        context.fill();
        context.globalAlpha = Math.max(0, (1 - progress) * .58) * flash.dim;
        context.fillStyle = '#fff6d4';
        context.beginPath();
        context.arc(flash.x, flash.y, Math.max(2, radius * .15), 0, Math.PI * 2);
        context.fill();
        context.globalAlpha = 1;
        if (flash.life >= flash.maxLife) flashes.splice(index, 1);
      }

      // Update particles first, then batch their trails by visual style. Hundreds
      // of individual beginPath/stroke calls made the full-screen moment miss
      // frames even though no single long task was present.
      const trailBatches = new Map<string, number[]>();
      const sparkleRects: number[] = [];
      for (let index = particles.length - 1; index >= 0; index -= 1) {
        const particle = particles[index];
        particle.life += 1;
        particle.px = particle.x;
        particle.py = particle.y;
        particle.vy += particle.grav;
        particle.vx *= .987;
        particle.vy *= .993;
        particle.x += particle.vx;
        particle.y += particle.vy;
        if (particle.y > innerHeight + 20 || particle.x < -20 || particle.x > innerWidth + 20) {
          particles.splice(index, 1);
          continue;
        }
        const alpha = Math.max(0, 1 - particle.life / particle.maxLife) * (.72 + Math.sin(particle.life * .32 + particle.twinkle) * .28) * particle.dim;
        const alphaStep = Math.max(.2, Math.ceil(alpha * 5) / 5);
        const widthStep = particle.size < 1.25 ? 1 : particle.size < 2 ? 1.7 : 2.4;
        const batchKey = `${particle.color}|${widthStep}|${alphaStep}`;
        const segments = trailBatches.get(batchKey) || [];
        segments.push(
          particle.px, particle.py,
          particle.x - particle.vx * 3.2, particle.y - particle.vy * 3.2,
        );
        trailBatches.set(batchKey, segments);
        if (particle.life % 4 === 0) {
          const radius = Math.max(.5, particle.size * .38);
          sparkleRects.push(particle.x - radius, particle.y - radius, radius * 2);
        }
        if (particle.life >= particle.maxLife) particles.splice(index, 1);
      }
      for (const [batchKey, segments] of trailBatches) {
        const [color, width, alpha] = batchKey.split('|');
        context.globalAlpha = Number(alpha);
        context.strokeStyle = color;
        context.lineWidth = Number(width);
        context.beginPath();
        for (let index = 0; index < segments.length; index += 4) {
          context.moveTo(segments[index], segments[index + 1]);
          context.lineTo(segments[index + 2], segments[index + 3]);
        }
        context.stroke();
      }
      if (sparkleRects.length) {
        context.globalAlpha = .62;
        context.fillStyle = '#fff8d9';
        context.beginPath();
        for (let index = 0; index < sparkleRects.length; index += 3) {
          const size = sparkleRects[index + 2];
          context.rect(sparkleRects[index], sparkleRects[index + 1], size, size);
        }
        context.fill();
      }
      context.globalCompositeOperation = 'source-over';
      context.globalAlpha = 1;
      if (!disposed && !reduced) frame = requestAnimationFrame(draw);
    };

    const onCanvasClick = (event: MouseEvent) => {
      // 点击避让区域时不放烟花，把目标挪到安全位置
      const nx = event.clientX / innerWidth;
      const ny = event.clientY / innerHeight;
      let x = nx;
      let y = Math.max(.1, ny);
      if (isForbidden(x, y)) {
        y = Math.min(y, .6);
        x = Math.max(x, .5);
        if (isForbidden(x, y)) y = .35;
      }
      if (reduced) {
        burst(x * innerWidth, y * innerHeight, true, 'mid');
        draw(0);
        return;
      }
      rockets.push({ x: x * innerWidth, y: innerHeight + 10, targetY: y * innerHeight, life: 0, maxLife: 30, color: ROCKET_COLORS[Math.floor(Math.random() * 3)], trail: 0, speed: ROCKET_SPEED, dim: 1, layer: 'mid' });
    };

    resize();
    if (reduced) {
      // 减少动效：只画几朵静止的烟花，不起动画循环（前中后各一朵）
      burst(innerWidth * .3, innerHeight * .24, true, 'far');
      burst(innerWidth * .62, innerHeight * .32, true, 'mid');
      burst(innerWidth * .52, innerHeight * .58, false, 'near');
      draw(0);
    } else {
      launchRocket('mid');
      frame = requestAnimationFrame(draw);
    }
    addEventListener('resize', resize);
    canvas.addEventListener('click', onCanvasClick);
    return () => {
      disposed = true;
      cancelAnimationFrame(frame);
      removeEventListener('resize', resize);
      canvas.removeEventListener('click', onCanvasClick);
      particles.length = 0;
      flashes.length = 0;
      rockets.length = 0;
      context.clearRect(0, 0, canvas.width, canvas.height);
    };
  }, [open, round]);

  return <>
    {trigger && <button type="button" className={`${styles.trigger} ${compact ? styles.compact : ''} ${className}`} onClick={startRelax} data-testid="relax-moment-trigger">
      <MoonStar size={compact ? 16 : 21} /><span>轻松一下</span>{!compact && <small>月下看一场小烟花</small>}
    </button>}
    {open && createPortal(
      <section className={styles.overlay} role="dialog" aria-modal="true" aria-label="轻松一下" data-testid="relax-moment-overlay">
        <div className={styles.sky} aria-hidden="true"><i className={styles.moon} /><i className={styles.cloudOne} /><i className={styles.cloudTwo} /><i className={styles.ridgeFar} /><i className={styles.ridgeNear} /><i className={styles.water} /><i className={styles.lanterns} /></div>
        <canvas ref={canvasRef} className={styles.canvas} aria-label="暖金色烟花，点击任意位置可再放一束" />
        <div className={`${styles.copyText} ${fading ? styles.fading : ''}`}>
          <p className={styles.mainLine}>{typed}{isTyping && <span className={styles.caret} aria-hidden="true" />}</p>
          <small className={styles.hint}>{reducedMotion ? '已按你的系统设置减少动效' : '点击夜空，可以再放一束烟花'}</small>
        </div>
        <div className={styles.soundControls} aria-label="烟花声音控制">
          <button type="button" onClick={() => setMuted(sound()?.toggleMute() ?? !muted)} aria-label={muted ? '取消静音' : '静音'}>{muted ? <VolumeX size={17} /> : <Volume2 size={17} />}</button>
          <input type="range" min="0" max="0.35" step="0.01" value={volume} aria-label="烟花音量" onChange={(event) => { const next = Number(event.target.value); setVolume(next); sound()?.setVolume(next); }} />
        </div>
        <button type="button" className={styles.close} onClick={closeRelax} aria-label="结束轻松一下"><X size={19} /> 回到学习</button>
      </section>, document.body)}
  </>;
}
