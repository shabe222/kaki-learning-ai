import { useEffect, useRef } from 'react';
import coastMother from '../../../assets/tide/coast-mother-v1.png';
import styles from './SceneLayerSlot.module.css';

const depthPlanes = [
  { name: 'islands', label: '远岛景深' },
  { name: 'sea', label: '海面景深' },
  { name: 'shore', label: '近岸景深' },
] as const;

export function SceneLayerSlot() {
  const slotRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const slot = slotRef.current;
    if (!slot || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    let frame = 0;
    const move = (event: PointerEvent) => {
      window.cancelAnimationFrame(frame);
      frame = window.requestAnimationFrame(() => {
        const x = event.clientX / window.innerWidth - 0.5;
        const y = event.clientY / window.innerHeight - 0.5;
        slot.style.setProperty('--scene-air-x', `${x * -0.8}px`);
        slot.style.setProperty('--scene-air-y', `${y * -0.4}px`);
        slot.style.setProperty('--scene-island-x', `${x * -2.2}px`);
        slot.style.setProperty('--scene-island-y', `${y * -1}px`);
        slot.style.setProperty('--scene-sea-x', `${x * -3.5}px`);
        slot.style.setProperty('--scene-sea-y', `${y * -1.4}px`);
        slot.style.setProperty('--scene-shore-x', `${x * -5}px`);
        slot.style.setProperty('--scene-shore-y', `${y * -1.8}px`);
      });
    };
    window.addEventListener('pointermove', move, { passive: true });
    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener('pointermove', move);
    };
  }, []);

  return (
    <div
      ref={slotRef}
      className={styles.slot}
      data-testid="tide-scene-layer-slot"
      data-scene-state="coast-foundation"
      aria-hidden="true"
    >
      <div className={styles.kakiPaper} data-scene-plane="kaki-paper" />
      <img
        className={styles.baseCoast}
        src={coastMother}
        alt=""
        draggable="false"
        data-scene-plane="base"
        data-continuity-layer="true"
      />
      <div className={styles.atmosphere} data-depth-layer="atmosphere" />
      {depthPlanes.map((plane) => (
        <div key={plane.name} className={`${styles.depthPlane} ${styles[plane.name]}`} data-scene-plane={plane.name}>
          <img src={coastMother} alt="" draggable="false" data-plane-label={plane.label} />
        </div>
      ))}
    </div>
  );
}
