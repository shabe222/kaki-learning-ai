import { savePictureBook } from '../../../api/study';
import { getUserToken } from '../../../shared/auth';

/**
 * The storybook component generates a finished book inside the browser and
 * announces it with a `darya-pb:generated` event; nothing owns the result, so
 * closing the overlay used to lose the book. This module carries the
 * component's own payload to the existing artifact authority unchanged.
 *
 * Illustrations arrive in two forms. Sliced page panels are already inline
 * data URIs and survive storage as they are; covers and un-sliced pages are
 * third-party URLs. Those are fetched through the storybook asset proxy — the
 * only same-origin path to a remote illustration, and the one that already
 * carries the server-side address guard — and inlined so a stored book does
 * not depend on someone else's link staying alive. When that fetch fails the
 * URL is kept rather than the picture being dropped, and the caller is told
 * how many illustrations remain remote.
 */

const MAX_IMAGES = 16;
const MAX_IMAGE_BYTES = 4 * 1024 * 1024;
const MAX_TOTAL_BYTES = 6 * 1024 * 1024;
const FETCH_TIMEOUT_MS = 20_000;

export interface PictureBookSaveOutcome {
  artifactId: number;
  title: string;
  pageCount: number;
  /** Illustrations that could not be inlined and stay as remote links. */
  remoteImages: number;
}

function blobToDataUri(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ''));
    reader.onerror = () => reject(new Error('illustration read failed'));
    reader.readAsDataURL(blob);
  });
}

async function inlineRemoteImage(url: string, assetPath: string, token: string): Promise<{ dataUri: string | null; bytes: number }> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  try {
    const response = await fetch(`${assetPath}?url=${encodeURIComponent(url)}`, {
      headers: { Authorization: `Bearer ${token}` },
      signal: controller.signal,
    });
    if (!response.ok) return { dataUri: null, bytes: 0 };
    const blob = await response.blob();
    if (!blob.type.startsWith('image/') || blob.size > MAX_IMAGE_BYTES) return { dataUri: null, bytes: 0 };
    return { dataUri: await blobToDataUri(blob), bytes: blob.size };
  } catch {
    return { dataUri: null, bytes: 0 };
  } finally {
    window.clearTimeout(timer);
  }
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? { ...(value as Record<string, unknown>) }
    : {};
}

/** Copy only the fields the storage contract accepts; never invent content. */
function toStorageShape(raw: Record<string, unknown>): Record<string, unknown> {
  const pages = Array.isArray(raw.pages) ? raw.pages : [];
  const quiz = Array.isArray(raw.quiz) ? raw.quiz : [];
  return {
    artifact_id: typeof raw.artifact_id === 'string' ? raw.artifact_id : undefined,
    title: raw.title,
    topic: raw.topic,
    grade_text: raw.grade_text,
    style: raw.style,
    difficulty: raw.difficulty,
    summary: raw.summary,
    thinking_question: raw.thinking_question,
    cover_image: raw.cover_image ?? null,
    pages: pages.map((page) => {
      const entry = asRecord(page);
      return {
        index: entry.index,
        title: entry.title,
        content: entry.content,
        stage_label: entry.stage_label,
        fact: entry.fact,
        image_url: entry.image_url ?? null,
      };
    }),
    quiz: quiz.map((item) => {
      const entry = asRecord(item);
      return {
        question: entry.question,
        options: entry.options,
        answer: entry.answer,
        explanation: entry.explanation,
      };
    }),
    degraded: Boolean(raw.degraded),
  };
}

export async function persistGeneratedPictureBook(options: {
  artifact: unknown;
  assetPath: string;
  threadId?: string | null;
}): Promise<PictureBookSaveOutcome> {
  const book = toStorageShape(asRecord(options.artifact));
  const pages = book.pages as Array<Record<string, unknown>>;
  if (!pages.length) throw new Error('这本绘本没有可保存的页面。');

  const token = getUserToken();
  let remoteImages = 0;
  let budget = MAX_TOTAL_BYTES;
  let attempts = 0;

  const inlineField = async (holder: Record<string, unknown>, key: string) => {
    const value = holder[key];
    if (typeof value !== 'string' || !value.startsWith('https://')) return;
    if (!token || attempts >= MAX_IMAGES || budget <= 0) {
      remoteImages += 1;
      return;
    }
    attempts += 1;
    const { dataUri, bytes } = await inlineRemoteImage(value, options.assetPath, token);
    if (!dataUri || bytes > budget) {
      remoteImages += 1;
      return;
    }
    budget -= bytes;
    holder[key] = dataUri;
  };

  // Cover first: it is what the learner recognises the book by.
  const cover = { cover_image: book.cover_image };
  await inlineField(cover, 'cover_image');
  book.cover_image = cover.cover_image;
  for (const page of pages) await inlineField(page, 'image_url');

  const saved = await savePictureBook({
    book,
    thread_id: options.threadId ? String(options.threadId) : undefined,
  });
  return {
    artifactId: Number(saved.artifact_id),
    title: String(saved.title || book.title || '教学绘本'),
    pageCount: pages.length,
    remoteImages,
  };
}
