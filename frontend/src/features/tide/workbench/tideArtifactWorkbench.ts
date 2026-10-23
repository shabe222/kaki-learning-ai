import type {
  TideArtifactAction,
  TideArtifactComparisonChange,
  TideArtifactVersionProjection,
  TideArtifactWorkbench,
  TidePresentationSlide,
} from '../tideTypes';

const comparisonFields: Array<{
  field: TideArtifactComparisonChange['field'];
  label: string;
  value: (slide: TidePresentationSlide) => string;
}> = [
  { field: 'title', label: '标题', value: (slide) => slide.title },
  { field: 'subtitle', label: '副标题', value: (slide) => slide.subtitle },
  { field: 'body', label: '正文', value: (slide) => slide.body },
  { field: 'items', label: '要点', value: (slide) => slide.items.join('\n') },
  { field: 'narration', label: '讲述备注', value: (slide) => slide.narration },
  { field: 'code', label: '代码', value: (slide) => slide.code },
  { field: 'layout', label: '布局', value: (slide) => slide.layout },
  { field: 'visualReferences', label: '视觉参考', value: (slide) => slide.visualReferences.join('\n') },
];

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function cleanText(value: unknown, limit = 6000) {
  return typeof value === 'string' ? value.trim().slice(0, limit) : '';
}

function cleanList(value: unknown, limit = 30) {
  return Array.isArray(value)
    ? value.map((item) => cleanText(item, 800)).filter(Boolean).slice(0, limit)
    : [];
}

function visualReferences(value: unknown) {
  if (!Array.isArray(value)) return [];
  return value.map((item) => {
    if (typeof item === 'string') return cleanText(item, 800);
    const record = asRecord(item);
    return cleanText(record.title || record.label || record.url || record.src, 800);
  }).filter(Boolean).slice(0, 12);
}

function normalizeSlide(value: unknown, index: number): TidePresentationSlide {
  const slide = asRecord(value);
  return {
    index,
    type: cleanText(slide.type, 60) || 'content',
    title: cleanText(slide.title, 300),
    subtitle: cleanText(slide.subtitle, 600),
    body: cleanText(slide.body, 6000),
    items: cleanList(slide.items || slide.points),
    narration: cleanText(slide.notes || slide.speaker_notes || slide.narration, 6000),
    code: cleanText(slide.code, 8000),
    language: cleanText(slide.language || slide.lang, 60),
    layout: cleanText(slide.layout || slide.layout_name, 200),
    visualReferences: visualReferences(slide.visual_references || slide.visuals),
  };
}

function slidesFrom(projection: TideArtifactVersionProjection) {
  const data = asRecord(projection.render_payload?.data || projection.raw_data);
  return Array.isArray(data.slides)
    ? data.slides.map((slide, index) => normalizeSlide(slide, index))
    : [];
}

function focusedPage(action: TideArtifactAction | null, slideCount: number) {
  const direct = Number(action?.proposal.page_number || 0);
  const match = String(action?.proposal.scope || action?.proposal.instruction || '')
    .match(/(?:第\s*(\d+)\s*页|page\s*(\d+))/i);
  const candidate = direct || Number(match?.[1] || match?.[2] || 1);
  return Math.max(1, Math.min(candidate, Math.max(slideCount, 1)));
}

function meaningfulComparison(
  before: TidePresentationSlide | undefined,
  after: TidePresentationSlide | undefined,
) {
  if (!before || !after) return [];
  return comparisonFields.flatMap(({ field, label, value }) => {
    const beforeValue = value(before).trim();
    const afterValue = value(after).trim();
    if (beforeValue === afterValue || (!beforeValue && !afterValue)) return [];
    return [{ field, label, before: beforeValue, after: afterValue }];
  });
}

export function buildArtifactWorkbench(
  current: TideArtifactVersionProjection,
  source: TideArtifactVersionProjection | null,
  action: TideArtifactAction | null,
): TideArtifactWorkbench {
  const slides = slidesFrom(current);
  const sourceSlides = source ? slidesFrom(source) : [];
  const focusedPageNumber = focusedPage(action, slides.length);
  return {
    artifactId: current.artifact_id,
    title: current.title,
    currentVersionId: current.version_id,
    currentVersionNumber: current.version_number,
    sourceVersionId: source?.version_id ?? null,
    sourceVersionNumber: source?.version_number ?? null,
    focusedPageNumber,
    slides,
    sourceSlides,
    comparison: meaningfulComparison(
      sourceSlides[focusedPageNumber - 1],
      slides[focusedPageNumber - 1],
    ),
  };
}
