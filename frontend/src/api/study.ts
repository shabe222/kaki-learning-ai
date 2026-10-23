import { http } from './client';

export interface StudySkillInfo {
  name: string;
  description: string;
  standard?: string;
  source_type?: string;
  methodology_references: string[];
  vendored_code: Array<Record<string, unknown>>;
}

export interface StudyArtifactRef {
  artifact_id: number;
  type: string;
  title: string;
  kind?: string;
  audio_url?: string | null;
  duration_sec?: number | null;
  generation_provenance?: Record<string, unknown>;
}

export interface Flashcard {
  id: string;
  front: string;
  back: string;
  kind: string;
  source: string;
}

export async function generateFlashcards(payload: { source_artifact_id?: number; topic?: string; thread_id?: string }): Promise<StudyArtifactRef> {
  const response = await http.post('/api/study/flashcards', payload, { timeout: 180000 });
  return (response.data?.data ?? response.data) as StudyArtifactRef;
}

export async function reviewFlashcard(artifactId: number, cardId: string, rating: number): Promise<{ due: string; stability: number; difficulty: number }> {
  const response = await http.post(`/api/study/flashcards/${artifactId}/review`, { card_id: cardId, rating });
  return (response.data?.data ?? response.data) as { due: string; stability: number; difficulty: number };
}

export async function createMistakeBook(payload: { exam_id: number; thread_id?: string }): Promise<StudyArtifactRef> {
  const response = await http.post('/api/study/mistake-books', payload, { timeout: 180000 });
  return (response.data?.data ?? response.data) as StudyArtifactRef;
}

export async function generateAudioLesson(payload: { source_artifact_id?: number; topic?: string; thread_id?: string }): Promise<StudyArtifactRef> {
  const response = await http.post('/api/study/audio-lessons', payload, { timeout: 180000 });
  return (response.data?.data ?? response.data) as StudyArtifactRef;
}

export async function listStudySkills(): Promise<StudySkillInfo[]> {
  const response = await http.get('/api/study/skills');
  return (response.data?.data ?? response.data) as StudySkillInfo[];
}

export async function savePictureBook(payload: { book: Record<string, unknown>; thread_id?: string }): Promise<StudyArtifactRef> {
  const response = await http.post('/api/study/picture-books', payload, { timeout: 60000 });
  return (response.data?.data ?? response.data) as StudyArtifactRef;
}
