import { graphApi } from './learningGraph';
import type { CourseTreeData, GraphNode, GraphSummary } from './learningGraph';
import type { LearningChapter, LearningPathPayload } from './modules';

/**
 * 学习事实单一链路：正式 LearningGraph 是首页 / 我的学习 / 练习 / 资料的
 * 唯一路径事实来源。本模块把主图谱投影成各页既有消费形状，
 * 没有图谱时返回空（由页面展示诚实空态），绝不回退到静态章节。
 */

export interface PrimaryPlan {
  graph: GraphSummary;
  tree: CourseTreeData | null;
}

export interface PrimaryPlanSummary {
  graphId: number;
  title: string;
  goal: string;
  progressPct: number;
  totalNodes: number;
  completedNodes: number;
  nextNodeTitle: string | null;
  nextNodeId: number | null;
}

export async function loadPrimaryPlan(): Promise<PrimaryPlan | null> {
  const graphs = await graphApi.list().catch(() => [] as GraphSummary[]);
  const graph = graphs.find((item) => item.is_primary) ?? graphs[0];
  if (!graph) return null;
  const tree = await graphApi.courseTree(graph.id).catch(() => null);
  return { graph, tree };
}

function nodeStage(node: GraphNode): string | undefined {
  if (node.status === 'completed') return 'completed';
  if (node.status === 'learning') return 'active';
  if (node.status === 'needs_review') return 'partial';
  return undefined;
}

function nodeToChapter(node: GraphNode, index: number): LearningChapter {
  const resources = node.resources ?? [];
  return {
    id: String(node.id),
    order: Number.isFinite(node.order_index) ? Number(node.order_index) : index + 1,
    name: node.title,
    description: node.description ?? undefined,
    difficulty: node.difficulty,
    mastery: node.mastery_score,
    stage: nodeStage(node),
    progress_pct: node.status === 'completed' ? 100 : Math.round(Number(node.mastery_score ?? 0)),
    material_count: resources.length,
    has_material: resources.some((resource) => resource.artifact_id != null || resource.status === 'ready'),
  };
}

export function planToLearningPath(plan: PrimaryPlan | null, userId: number): LearningPathPayload {
  const nodes = plan?.tree?.nodes ?? [];
  const chapters = nodes.map(nodeToChapter);
  return { user_id: userId, total_chapters: chapters.length, chapters };
}

export function summarizePlan(plan: PrimaryPlan | null): PrimaryPlanSummary | null {
  if (!plan) return null;
  const tree = plan.tree;
  const next = tree?.next_node ?? tree?.nodes?.find((node) => node.status !== 'completed') ?? null;
  return {
    graphId: plan.graph.id,
    title: plan.graph.title,
    goal: plan.graph.goal ?? '',
    progressPct: Math.round(Number(tree?.progress_pct ?? plan.graph.progress_pct ?? 0)),
    totalNodes: tree?.total_nodes ?? plan.graph.total_nodes ?? 0,
    completedNodes: plan.graph.completed_nodes ?? 0,
    nextNodeTitle: next?.title ?? null,
    nextNodeId: next?.id ?? null,
  };
}
