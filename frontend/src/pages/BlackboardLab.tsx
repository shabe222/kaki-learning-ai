/**
 * 黑板实验页 /blackboard-lab
 *
 * 不经过登录、不依赖后端：起来就能看到黑板的完整效果。
 * 用途：验收渲染层；以及给队友一条"拿到就能看"的最小路径。
 */

import { useCallback, useState } from 'react';
import { Blackboard } from '../features/blackboard/Blackboard';
import { buildDemoScript, buildEraseDemoScript } from '../features/blackboard/demoScript';
import type { BlackboardScript } from '../features/blackboard/types';

type Mode = 'lesson' | 'erase';

export default function BlackboardLab() {
  const [mode, setMode] = useState<Mode>('lesson');
  const [script, setScript] = useState<BlackboardScript | null>(() => buildDemoScript());
  const [busy, setBusy] = useState(false);
  const [size, setSize] = useState<'wide' | 'split' | 'call'>('wide');

  const replay = useCallback(() => {
    const next = mode === 'lesson' ? buildDemoScript() : buildEraseDemoScript();
    // 换一个对象引用，组件才会重新执行这批 ops
    setScript(null);
    window.setTimeout(() => setScript({ ...next }), 30);
  }, [mode]);

  const switchMode = useCallback((next: Mode) => {
    setMode(next);
    const s = next === 'lesson' ? buildDemoScript() : buildEraseDemoScript();
    setScript(null);
    window.setTimeout(() => setScript({ ...s }), 30);
  }, []);

  const box =
    size === 'wide'
      ? { width: '100%', height: 'calc(100vh - 150px)' }
      : size === 'split'
        ? { width: '62%', height: 'calc(100vh - 150px)' }
        : { width: 720, height: 405 };

  return (
    <div style={{ padding: 20, background: '#f2ece3', minHeight: '100vh', fontFamily: 'system-ui, sans-serif' }}>
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 14, flexWrap: 'wrap' }}>
        <strong style={{ fontSize: 15, color: '#3d2f26' }}>黑板实验页</strong>
        <button onClick={() => switchMode('lesson')} data-active={mode === 'lesson'}>
          讲解板书
        </button>
        <button onClick={() => switchMode('erase')} data-active={mode === 'erase'}>
          擦除动画
        </button>
        <span style={{ width: 1, height: 18, background: '#ddd0bf' }} />
        <button onClick={replay}>重放</button>
        <button onClick={() => setSize('wide')} data-active={size === 'wide'}>
          整宽
        </button>
        <button onClick={() => setSize('split')} data-active={size === 'split'}>
          对话分栏
        </button>
        <button onClick={() => setSize('call')} data-active={size === 'call'}>
          通话内嵌
        </button>
        <span style={{ fontSize: 12, color: busy ? '#d1550f' : '#7c6b5b' }}>
          {busy ? '正在写…（这批动作做完之前不打断）' : '空闲'}
        </span>
      </div>

      <div style={{ ...box, background: '#f1eae0', borderRadius: 12, overflow: 'hidden', boxShadow: '0 8px 30px rgba(96,72,48,.14)' }}>
        <Blackboard script={script} onBusyChange={setBusy} />
      </div>

      <p style={{ marginTop: 12, fontSize: 12, color: '#7c6b5b', lineHeight: 1.7 }}>
        这个页面只验证渲染层：粉笔质感、落笔动画、擦除动画、"指一下"。
        真实使用时板面脚本由后端下发（<code>backend/services/blackboard</code>），数据结构与这里完全一致。
      </p>
    </div>
  );
}
