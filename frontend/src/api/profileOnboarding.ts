import { postData, postFormData, deleteData } from './client';

export interface ProfileEvidence {
  value: string;
  source_text: string;
  source_type?: string;
  source_metadata?: Record<string, unknown> | null;
  confidence: number;
  confirmed: boolean;
}

export interface AttachmentMeta {
  id: string;
  file_name: string;
  file_type: string;
  text: string;
  char_count: number;
  page_count: number | null;
  section_count: number;
  preview: string;
  created_at: string;
}

export interface ProfileOnboardingSnapshot {
  task_id: string;
  thread_id?: string;
  task_type: 'profile_onboarding';
  state: 'active' | 'awaiting_confirmation' | 'suspended' | 'completed' | 'skipped';
  reply: string;
  progress: { current: number; total: number; percent: number };
  draft: Record<string, ProfileEvidence>;
  summary: string;
  attachments?: AttachmentMeta[];
}

export interface TranscribeResult {
  text: string;
  engine: string;
  language: string;
  duration_seconds: number;
}

export interface MaterialUploadResult {
  text: string;
  file_type: string;
  file_name: string;
  page_count: number | null;
  section_count: number;
  preview: string;
}

export const profileOnboardingApi = {
  start: () => postData<ProfileOnboardingSnapshot>('/api/profile-onboarding/start'),
  send: (taskId: string, message: string, sourceType?: string, sourceMeta?: Record<string, unknown> | null) =>
    postData<ProfileOnboardingSnapshot>('/api/profile-onboarding/message', {
      task_id: taskId,
      message,
      source_type: sourceType || 'user_text',
      source_metadata: sourceMeta || null,
    }),
  confirm: (taskId: string, edits: Record<string, string>) => postData<ProfileOnboardingSnapshot>('/api/profile-onboarding/confirm', {
    task_id: taskId,
    edits
  }),
  pause: (taskId: string) => postData<ProfileOnboardingSnapshot>('/api/profile-onboarding/pause', { task_id: taskId }),
  skip: (taskId: string) => postData<ProfileOnboardingSnapshot>('/api/profile-onboarding/skip', { task_id: taskId }),

  /** 上传音频，返回语音转写文本 */
  transcribe: async (audioBlob: Blob): Promise<TranscribeResult> => {
    const form = new FormData();
    form.append('file', audioBlob, 'recording.webm');
    return postFormData<TranscribeResult>('/api/profile-onboarding/transcribe', form);
  },

  /** 上传文件（PDF/DOCX/TXT/MD/图片），返回解析文本 */
  uploadMaterial: async (file: File): Promise<MaterialUploadResult> => {
    const form = new FormData();
    form.append('file', file);
    return postFormData<MaterialUploadResult>('/api/profile-onboarding/upload-material', form);
  },

  /** 持久化附件元信息到任务slots */
  storeAttachment: (payload: {
    task_id: string;
    file_name: string;
    file_type: string;
    text: string;
    page_count?: number | null;
    section_count?: number;
    preview?: string;
  }) => postData<AttachmentMeta>('/api/profile-onboarding/attachment', payload),

  /** 从任务slots中删除附件 */
  deleteAttachment: (taskId: string, attachmentId: string) =>
    deleteData<{ deleted: boolean }>(`/api/profile-onboarding/attachment/${taskId}/${attachmentId}`),
};
