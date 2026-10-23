/**
 * 黑板能力的前端出口。
 *
 * 用法（对话区与通话面板都只是把它放进容器）：
 *
 *   const script = useBlackboardScript(moment);   // 从消息里取黑板脚本
 *   const [narration, setNarration] = useState<BoardNarration>({ saying: null, leaving: null });
 *   <NarrationCaption {...narration} />           // 板子上方：DARYA 此刻在讲的那一句
 *   <Blackboard script={script} onBusyChange={setDrawing} onNarrationChange={setNarration} />
 *
 * 设计约束：
 * - 零新依赖；几何由后端解算，这里只渲染
 * - 通话模式通过 onBusyChange 知道"还在写"，从而在动作做完前不打断自己
 * - 逐句讲解由 Blackboard 排、由 NarrationCaption 画：板面只留粉笔，字回到说话的位置
 */

export { Blackboard } from './Blackboard';
export type { BoardNarration, Narration } from './Blackboard';
export { NarrationCaption } from './NarrationCaption';
export * from './types';
export { useBlackboardScript } from './useBlackboardScript';
export { buildDemoScript } from './demoScript';
