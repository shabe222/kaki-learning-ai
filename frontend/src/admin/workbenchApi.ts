import { getData, postData, putData } from '../api/client';
export type AdminTheme = 'kaki' | 'ocean';
export interface Preferences { theme: AdminTheme | null; compact: boolean; auto_refresh: boolean; admin_id: number; username: string; role: string; can_operate: boolean; can_configure: boolean }
export interface UserRow { user_id: number; username: string; is_active: boolean; purpose: string; has_profile: boolean; created_at: string; last_active_at: string | null; chat_count: number; message_count: number; week_sessions: number; week_items: number; item_count: number; task_count: number; error_count: number; answer_record_count: number }
export interface TaskRow { key: string; record_kind: string; id: number; user_id: number; type: string; status: string; quality:string; delivery:string; created_at: string; duration_ms: number | null; artifact_id: number | null; stage: string | null; error_category: string | null; tracking: string }
export interface QualityRow { artifact_id: number; user_id: number; type: string; created_at: string; version_id: number | null; version_number: number; generation_status: string | null; defect_code:string|null; verdict: string | null; review_id: string | null; review_round: number | null; contract: string | null; reviewed_at: string | null; content_verdict: string | null; measured: boolean | null; research_verdict: string | null; quality: string; review_source: string }
export interface Issue { id: number; category: string; severity: string; object_type: string; object_id: string; user_id: number | null; state: string; first_seen_at: string; last_seen_at: string; owner_id: number | null; result: string | null; evidence: string | null }
export interface List<T> { items: T[]; total: number; page: number; page_size: number; updated_at: string }
export interface Overview { scope: string; window: string; source: string; updated_at: string; users: number; active_users_7d: number; artifacts_7d: number; tasks_7d: number; terminal_sample: number; success_rate: number | null; avg_duration_ms: number | null; duration_sample: number; usage_tokens: number | null; cost: number | null; trend: {date: string; artifacts: number; tasks: number}[]; purpose_note: string }
export interface Version { id: number; version_number: number; parent_version_id: number | null; status: string; content_digest: string; created_at: string }
export interface Review { review_id: string; artifact_version_id: number; verdict: string; round: number; authority_kind: string; review_contract_version: string; created_at: string; correctness: number | null; clarity: number | null; completeness: number | null; grounding: number | null; instructional_value: number | null }
export interface QualityDetail extends QualityRow {versions: Version[]; reviews: Review[]; dimensions:{source:string;version_id:number|null;scores:Record<string,number>}[];defects:{review_id:string;severity?:string;code?:string;category?:string}[];revision_rounds:number|null;content_review_rounds:number|null;sample_authorized: boolean; privacy_note: string}
export interface TaskDetail extends TaskRow {quality: string; retry_eligible: boolean; cancel_eligible: boolean; retry_reason: string; cancel_reason: string; stages: string[]; retry_links: {source_artifact_id: number; target_artifact_id: number; created_at: string}[]; usage_tokens: number | null; cost: number | null}
export interface UserDetail extends UserRow {email: string | null; formal_attempt_count: number; code_run_count: number; profile_completion: number | null; activity_timeline: {id: number; time: string; label: string}[]; management_records: {id: number; admin_id: number; action: string; summary: string; created_at: string}[]}
export interface Health {base: {provider: string; mode: string; services: Record<string, string | boolean>; llm: {conversation: {initialized: boolean; mode: string}}}; database_probe: string; checked_at: string; api_routes: number; capabilities: {name: string; state: string; last_success_at: string | null; basis: string}[]; probes: {capability: string; state: string; checked_at: string; basis: string}[]; config: Record<string, {runtime?: string; env_file?: string; effect?: string; secret?: boolean} | string | boolean>}
const base = '/api/admin/workbench';
export const workbench = {
  get<T>(path: string, params?: Record<string, unknown>, signal?: AbortSignal) { return getData<T>(base + path, params, signal); },
  put<T>(path: string, body: unknown) { return putData<T>(base + path, body); },
  post<T>(path: string, body: unknown) { return postData<T>(base + path, body); },
};
export function readAdminTheme(adminId?: number): AdminTheme | null {
  const value = localStorage.getItem(adminId ? `darya_admin_theme:${adminId}` : 'darya_admin_theme:last');
  return value === 'ocean' || value === 'kaki' ? value : null;
}
export function cacheAdminTheme(value: AdminTheme, adminId: number) {
  localStorage.setItem(`darya_admin_theme:${adminId}`, value); localStorage.setItem('darya_admin_theme:last', value);
}
