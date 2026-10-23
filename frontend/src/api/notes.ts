import { getData, postData, putData, deleteData } from './client';

export interface NoteItem {
  id: number; user_id: number; title: string; content: string; plain_text: string;
  graph_node_id: number | null; graph_id?: number | null;
  material_ids: number[]; artifact_ids: number[];
  tags: string[]; category: string; is_pinned: boolean; is_favorite: boolean;
  word_count: number; status?: string; content_format?: string; summary?: string | null;
  ai_suggestions: any[]; created_at: string; updated_at: string; last_saved_at?: string | null;
  links?: any[]; revisions?: any[]; suggestions?: any[];
}

const BASE = '/api/notes';

// getData/postData/putData already call unwrapData() which strips {success,data} wrapper.
// Return type is the business payload directly — no extra .data needed.

export async function listNotes(params?: Record<string, unknown>): Promise<NoteItem[]> {
  const qs = params ? '?' + new URLSearchParams(params as Record<string, string>).toString() : '';
  return await getData<NoteItem[]>(`${BASE}${qs}`);
}

export async function createNote(body: {
  title: string; content?: string; graph_node_id?: number; graph_id?: number;
  artifact_ids?: number[]; tags?: string[]; category?: string;
}): Promise<{ id: number; title: string }> {
  return await postData<{ id: number; title: string }>(BASE, body);
}

export async function getNote(id: number): Promise<NoteItem> {
  return await getData<NoteItem>(`${BASE}/${id}`);
}

export async function updateNote(id: number, body: Record<string, unknown>): Promise<{ id: number; updated_at: string }> {
  return await putData<{ id: number; updated_at: string }>(`${BASE}/${id}`, body);
}

export async function deleteNote(id: number): Promise<void> {
  await deleteData(`${BASE}/${id}`);
}

export async function batchDeleteNotes(ids: number[]): Promise<{ deleted: number }> {
  return await postData<{ deleted: number }>(`${BASE}/batch-delete`, { ids });
}

export async function getNoteCategories(): Promise<{ category: string; count: number }[]> {
  return await getData<{ category: string; count: number }[]>(`${BASE}/categories`);
}

export async function getNoteTags(): Promise<string[]> {
  return await getData<string[]>(`${BASE}/tags`);
}

export async function restoreNote(noteId: number): Promise<void> {
  await postData(`${BASE}/${noteId}/restore`);
}

// AI
export interface SuggestionItem {
  id: number; note_id: number; action_type: string; apply_mode: string;
  status: string; suggestion_content: string | null;
  structured_result: any;
  provider: string; model: string; tokens_used: number;
  source_revision: number | null;
  source_content_hash: string | null;
  pre_apply_revision_id: number | null;
  applied_revision_id: number | null;
  applied_content_hash: string | null;
  created_at: string | null; applied_at: string | null;
  undone_at: string | null; undo_revision_id: number | null;
}

export async function requestAISuggestion(noteId: number, actionType: string): Promise<{
  id: number; note_id: number; note_version: number | null;
  status: string; action_type: string; suggestion_content: string | null;
  structured_result: any; tokens_used: number;
}> {
  return await postData<any>(`${BASE}/${noteId}/ai-suggestions?action_type=${encodeURIComponent(actionType)}`);
}

export interface ApplySuggestionResult {
  note: NoteItem; suggestion_id: number; status: string;
  apply_mode: string;
  pre_apply_revision_id: number | null; applied_revision_id: number | null;
  provider: string; model: string;
  source_content_hash: string; applied_content_hash: string;
}
export async function applySuggestion(noteId: number, suggestionId: number, acceptContent?: string): Promise<ApplySuggestionResult> {
  return await postData<ApplySuggestionResult>(`${BASE}/${noteId}/ai-suggestions/${suggestionId}/apply`, acceptContent ? { accept_content: acceptContent } : {});
}

export async function dismissSuggestion(noteId: number, suggestionId: number): Promise<void> {
  await postData(`${BASE}/${noteId}/ai-suggestions/${suggestionId}/dismiss`);
}

export interface UndoResult {
  status: string; suggestion_id: number;
  pre_ai_undo_revision_id: number | null; ai_undo_revision_id: number | null;
  restored_from_revision_id: number; note: NoteItem;
  content_hash: string;
}
export async function undoAI(noteId: number, suggestionId?: number): Promise<UndoResult> {
  const qs = suggestionId ? `?suggestion_id=${suggestionId}` : '';
  return await postData<UndoResult>(`${BASE}/${noteId}/undo-ai${qs}`);
}

// Links
export async function addLink(noteId: number, linkType: string, targetId: number, targetTitle?: string): Promise<{ id: number; link_type: string; target_id: number }> {
  return await postData<{ id: number; link_type: string; target_id: number }>(`${BASE}/${noteId}/links`, { link_type: linkType, target_id: targetId, target_title: targetTitle || '' });
}

export async function removeLink(noteId: number, linkId: number): Promise<void> {
  await deleteData(`${BASE}/${noteId}/links/${linkId}`);
}

// Revisions
export async function saveRevision(noteId: number): Promise<{ id: number; revision_number: number } | null> {
  return await postData<{ id: number; revision_number: number } | null>(`${BASE}/${noteId}/revisions`);
}

export async function getRevision(noteId: number, revisionId: number): Promise<{
  id: number; revision_number: number; title: string; content: string; change_source: string; created_at: string;
}> {
  return await getData<any>(`${BASE}/${noteId}/revisions/${revisionId}`);
}

export async function restoreRevision(noteId: number, revisionId: number): Promise<{ id: number; updated_at: string }> {
  return await postData<{ id: number; updated_at: string }>(`${BASE}/${noteId}/revisions/${revisionId}/restore`);
}
