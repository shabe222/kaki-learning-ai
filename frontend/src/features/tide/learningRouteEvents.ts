export const DARYA_LEARNING_ROUTE_UPDATED = 'darya:learning-route-updated';

export type DaryaLearningRouteUpdateSource = 'assessment' | 'code_submission';

export function notifyDaryaLearningRouteUpdated(
  threadId: string,
  source: DaryaLearningRouteUpdateSource,
) {
  window.dispatchEvent(new CustomEvent(DARYA_LEARNING_ROUTE_UPDATED, {
    detail: { threadId, source },
  }));
}
