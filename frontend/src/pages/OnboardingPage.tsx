import { Button, Input, Progress, Spin } from 'antd';
import { ArrowRight, Check, Clock3, Mic, MicOff, Paperclip, Send, SkipForward, Sparkles, Trash2 } from 'lucide-react';
import { FormEvent, useEffect, useMemo, useRef, useState } from 'react';
import { Navigate, useNavigate, useSearchParams } from 'react-router-dom';
import {
  profileOnboardingApi,
  type AttachmentMeta,
  type MaterialUploadResult,
  type ProfileOnboardingSnapshot,
  type TranscribeResult,
} from '../api/profileOnboarding';
import { useAuthStore } from '../stores/authStore';
import { canonicalDaryaRedirect } from '../utils/daryaRedirect';
import styles from './OnboardingPage.module.css';

type ConversationMessage = { role: 'assistant' | 'user'; content: string };

interface PendingAttachment {
  id: string;
  fileName: string;
  fileType: string;
  text: string;
  pageCount: number | null;
  preview: string;
}

function attachmentMetaToPending(att: AttachmentMeta): PendingAttachment {
  return {
    id: att.id,
    fileName: att.file_name,
    fileType: att.file_type,
    text: att.text,
    pageCount: att.page_count,
    preview: att.preview,
  };
}

const FIELD_LABELS: Record<string, string> = {
  learning_goal: '学习目标',
  current_stage: '当前阶段',
  knowledge_baseline: '已有基础',
  learning_preference: '学习偏好',
  study_rhythm: '学习节奏',
  motivation: '持续动力'
};

export function OnboardingPage({ product = 'legacy' }: { product?: 'legacy' | 'darya' }) {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { user, setUser } = useAuthStore();
  const darya = product === 'darya';
  const redirectTarget = (() => {
    const value = searchParams.get('redirect');
    if (darya) return canonicalDaryaRedirect(value) || '/darya';
    if (!value || !value.startsWith('/') || value.startsWith('//')) return darya ? '/darya' : '/';
    return value;
  })();
  const started = useRef(false);
  const [snapshot, setSnapshot] = useState<ProfileOnboardingSnapshot | null>(null);
  const [messages, setMessages] = useState<ConversationMessage[]>([]);
  const [answer, setAnswer] = useState('');
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState('');

  // 多模态状态
  const [recording, setRecording] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const [parsing, setParsing] = useState(false);
  const [attachments, setAttachments] = useState<PendingAttachment[]>([]);
  const mediaRecorder = useRef<MediaRecorder | null>(null);
  const audioChunks = useRef<Blob[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const isConfirmation = snapshot?.state === 'awaiting_confirmation';
  const draftEntries = useMemo(() => Object.entries(snapshot?.draft || {}), [snapshot?.draft]);

  useEffect(() => {
    if (started.current || !localStorage.getItem('token')) return;
    started.current = true;
    profileOnboardingApi.start()
      .then((next) => {
        setSnapshot(next);
        setMessages(next.reply ? [{ role: 'assistant', content: next.reply }] : []);
        setEdits(Object.fromEntries(Object.entries(next.draft || {}).map(([key, item]) => [key, item.value])));
        // 从后端恢复已持久化的附件（刷新恢复）
        if (next.attachments?.length) {
          setAttachments(next.attachments.map(attachmentMetaToPending));
        }
      })
      .catch(() => setError(darya ? '暂时无法连接 DARYA，请检查服务后重试。' : '暂时无法连接柿小吉，请检查服务后重试。'))
      .finally(() => setLoading(false));
  }, [darya]);

  if (!localStorage.getItem('token')) {
    return <Navigate to={darya ? '/darya/login' : '/login'} replace />;
  }
  if (user?.hasProfile && searchParams.get('resume') !== '1') {
    return <Navigate to={redirectTarget} replace />;
  }

  const finish = () => {
    if (user) setUser({ ...user, hasProfile: true });
    localStorage.setItem('kaki_profile_status', 'complete');
    navigate(redirectTarget, { replace: true });
  };

  // ---- 语音录音 ----
  const startRecording = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream, { mimeType: MediaRecorder.isTypeSupported('audio/webm;codecs=opus') ? 'audio/webm;codecs=opus' : 'audio/webm' });
      mediaRecorder.current = recorder;
      audioChunks.current = [];

      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) audioChunks.current.push(event.data);
      };

      recorder.onstop = async () => {
        stream.getTracks().forEach((track) => track.stop());
        const blob = new Blob(audioChunks.current, { type: 'audio/webm' });
        if (blob.size < 100) {
          setError('录音时间太短，请重新录制。');
          setRecording(false);
          return;
        }
        setRecording(false);
        setTranscribing(true);
        setError('');
        try {
          const result: TranscribeResult = await profileOnboardingApi.transcribe(blob);
          setAnswer((prev) => (prev ? prev + ' ' : '') + result.text);
        } catch {
          setError('语音转写失败，请重试或改用文字输入。');
        } finally {
          setTranscribing(false);
        }
      };

      recorder.start();
      setRecording(true);
      setError('');
    } catch {
      setError('无法访问麦克风，请检查浏览器权限设置。');
    }
  };

  const stopRecording = () => {
    if (mediaRecorder.current && mediaRecorder.current.state === 'recording') {
      mediaRecorder.current.stop();
    }
  };

  const cancelRecording = () => {
    if (mediaRecorder.current && mediaRecorder.current.state === 'recording') {
      mediaRecorder.current.onstop = null;
      mediaRecorder.current.stop();
    }
    setRecording(false);
  };

  // ---- 文件上传 ----
  const handleFileUpload = async (file: File) => {
    setParsing(true);
    setError('');
    try {
      const result: MaterialUploadResult = await profileOnboardingApi.uploadMaterial(file);
      // 持久化附件到后端任务slots（刷新后可恢复）
      let attId = `att_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
      if (snapshot?.task_id) {
        try {
          const stored = await profileOnboardingApi.storeAttachment({
            task_id: snapshot.task_id,
            file_name: result.file_name,
            file_type: result.file_type,
            text: result.text,
            page_count: result.page_count,
            section_count: result.section_count,
            preview: result.preview,
          });
          attId = stored.id;
        } catch {
          // 后端持久化失败不阻塞前端使用
        }
      }
      setAttachments((prev) => [
        ...prev,
        {
          id: attId,
          fileName: result.file_name,
          fileType: result.file_type,
          text: result.text,
          pageCount: result.page_count,
          preview: result.preview,
        },
      ]);
    } catch (err: any) {
      setError(err?.message || '文件解析失败，请检查文件格式后重试。');
    } finally {
      setParsing(false);
    }
  };

  const removeAttachment = async (id: string) => {
    setAttachments((prev) => prev.filter((att) => att.id !== id));
    // 后端删除（非关键路径，静默失败）
    if (snapshot?.task_id) {
      try { await profileOnboardingApi.deleteAttachment(snapshot.task_id, id); } catch { /* noop */ }
    }
  };

  // ---- 发送 ----
  const buildMessageWithAttachments = (): { text: string; sourceType: string; sourceMeta: Record<string, unknown> | null } => {
    const userText = answer.trim();
    if (attachments.length === 0) {
      return { text: userText, sourceType: 'user_text', sourceMeta: null };
    }

    // 将附件提取的文本作为上下文附加到消息中
    const attachmentTexts = attachments.map((att) => {
      const header = `\n\n【附件：${att.fileName}】`;
      const body = att.text.length > 2000 ? att.text.slice(0, 2000) + '\n…(内容已截断)' : att.text;
      return header + '\n' + body;
    }).join('');

    return {
      text: userText + attachmentTexts,
      sourceType: attachments[0]?.fileType === 'image' ? 'image_ocr' : 'uploaded_material',
      sourceMeta: {
        files: attachments.map((att) => ({
          file_name: att.fileName,
          file_type: att.fileType,
          page_count: att.pageCount,
          char_count: att.text.length,
        })),
      },
    };
  };

  const sendAnswer = async (event?: FormEvent) => {
    event?.preventDefault();
    const content = answer.trim();
    if (!snapshot || (!content && attachments.length === 0) || sending) return;
    setSending(true);
    setError('');

    const { text, sourceType, sourceMeta } = buildMessageWithAttachments();
    const displayText = content || (attachments.length ? `[已上传 ${attachments.length} 个文件]` : '');

    setMessages((current) => [...current, { role: 'user', content: displayText }]);
    setAnswer('');
    setAttachments([]);

    try {
      const next = await profileOnboardingApi.send(snapshot.task_id, text || '(已上传附件)', sourceType, sourceMeta);
      setSnapshot(next);
      setMessages((current) => [...current, { role: 'assistant', content: next.reply }]);
      setEdits(Object.fromEntries(Object.entries(next.draft || {}).map(([key, item]) => [key, item.value])));
    } catch {
      setAnswer(content);
      setError('这条回答没有保存成功，请再试一次。');
    } finally {
      setSending(false);
    }
  };

  const closeTask = async (action: 'pause' | 'skip') => {
    if (!snapshot || sending) return;
    setSending(true);
    setError('');
    try {
      if (action === 'pause') {
        await profileOnboardingApi.pause(snapshot.task_id);
        navigate(darya ? (redirectTarget || '/darya') : '/', { replace: true });
      } else {
        await profileOnboardingApi.skip(snapshot.task_id);
        finish();
      }
    } catch {
      setError('当前进度没有保存成功，请再试一次。');
      setSending(false);
    }
  };

  const confirmProfile = async () => {
    if (!snapshot || sending) return;
    setSending(true);
    setError('');
    try {
      await profileOnboardingApi.confirm(snapshot.task_id, edits);
      finish();
    } catch {
      setError('画像没有保存成功，请检查修改内容后重试。');
      setSending(false);
    }
  };

  return (
    <main className={styles.onboarding} data-product={darya ? 'darya' : 'legacy'}>
      <section className={styles.card} aria-label="认识你画像引导">
          <div className={styles.header}>
          <div className={styles.identity}>
            <div className={styles.mascotFrame}>{darya ? <span aria-hidden="true" /> : <img src="/ai-workspace/assets/avatars/avatar-ai.jpg" alt="柿小吉" />}</div>
            <div>
              <span className={styles.eyebrow}><Sparkles size={14} /> 初次见面</span>
              <h1>{darya ? '让 DARYA 先认识你' : '柿小吉想先认识你'}</h1>
              <p>只记录你明确告诉我的内容，不确定的地方会先向你确认。</p>
            </div>
          </div>
          <div className={styles.headerActions}>
            <Button type="text" icon={<SkipForward size={16} />} disabled={loading || sending} onClick={() => closeTask('skip')} data-testid="onboarding-direct-start">直接开始</Button>
            <div className={styles.progressBlock} data-testid="profile-progress">
              <span>认识进度</span>
              <strong>{snapshot?.progress.current || 0} / {snapshot?.progress.total || 6}</strong>
              <Progress percent={snapshot?.progress.percent || 0} showInfo={false} strokeColor="#d16f42" trailColor="#eadfce" />
            </div>
          </div>
        </div>

        <div className={styles.stage} data-testid="profile-stage">
          {loading ? (
            <div className={styles.loading}><Spin /><span>{darya ? 'DARYA 正在准备第一个问题…' : '柿小吉正在准备第一个问题…'}</span></div>
          ) : isConfirmation ? (
            <section className={styles.confirmation}>
              <span className={styles.confirmIcon}><Check size={22} /></span>
              <h2>{darya ? 'DARYA 对我的初步了解' : '柿小吉对我的初步了解'}</h2>
              <p className={styles.summary}>{snapshot.summary}</p>
              {draftEntries.length > 0 && (
                <div className={styles.editGrid}>
                  {draftEntries.map(([key, item]) => (
                    <label key={key}>
                      <span>{FIELD_LABELS[key] || key}</span>
                      <Input.TextArea
                        autoSize={{ minRows: 2, maxRows: 4 }}
                        value={edits[key] ?? item.value}
                        onChange={(event) => setEdits((current) => ({ ...current, [key]: event.target.value }))}
                      />
                      <small>来源：你的原话 · 可信度 {Math.round(item.confidence * 100)}%</small>
                    </label>
                  ))}
                </div>
              )}
            </section>
          ) : (
            <div className={styles.conversation} aria-live="polite">
              {messages.map((message, index) => (
                <div key={`${message.role}-${index}`} className={`${styles.message} ${styles[message.role]}`}>
                  {message.role === 'assistant' && !darya && <img src="/ai-workspace/assets/avatars/avatar-ai.jpg" alt="" />}
                  <div><span>{message.role === 'assistant' ? (darya ? 'DARYA' : '柿小吉') : '我'}</span><p>{message.content}</p></div>
                </div>
              ))}
            </div>
          )}
        </div>

        {error && <p className={styles.error} role="alert">{error}</p>}

        <footer className={styles.footer}>
          {isConfirmation ? (
            <Button type="primary" size="large" icon={<ArrowRight size={18} />} loading={sending} onClick={confirmProfile}>
              确认并开始学习
            </Button>
          ) : (
            <>
              {/* 附件预览列表 */}
              {attachments.length > 0 && (
                <div className={styles.attachmentList}>
                  {attachments.map((att) => (
                    <div key={att.id} className={styles.attachmentItem}>
                      <span className={styles.attachmentIcon}><Paperclip size={14} /></span>
                      <span className={styles.attachmentName}>{att.fileName}</span>
                      <span className={styles.attachmentMeta}>
                        {att.fileType.toUpperCase()} {att.pageCount ? `· ${att.pageCount}页` : ''} · {att.text.length}字
                      </span>
                      <Button
                        type="text"
                        size="small"
                        icon={<Trash2 size={14} />}
                        onClick={() => removeAttachment(att.id)}
                        disabled={sending}
                        className={styles.attachmentDelete}
                      />
                    </div>
                  ))}
                </div>
              )}

              <form className={styles.composer} onSubmit={sendAnswer}>
                <div className={styles.composerInput}>
                  <Input.TextArea
                    data-testid="profile-answer"
                    value={answer}
                    onChange={(event) => setAnswer(event.target.value)}
                    onPressEnter={(event) => {
                      if (!event.shiftKey) {
                        event.preventDefault();
                        void sendAnswer();
                      }
                    }}
                    autoSize={{ minRows: 2, maxRows: 4 }}
                    maxLength={1200}
                    placeholder={recording ? '正在录音中…' : transcribing ? '正在转写语音…' : '用自己的话回答；也可以录音或上传文件'}
                    disabled={loading || sending || recording}
                  />
                </div>
                <div className={styles.composerActions}>
                  {/* 麦克风按钮 */}
                  {recording ? (
                    <>
                      <Button
                        type="primary"
                        danger
                        icon={<MicOff size={17} />}
                        onClick={stopRecording}
                        className={styles.recordingStop}
                      >
                        停止录音
                      </Button>
                      <Button type="text" onClick={cancelRecording} disabled={sending}>
                        取消
                      </Button>
                    </>
                  ) : (
                    <Button
                      type="text"
                      icon={transcribing ? <Spin size="small" /> : <Mic size={17} />}
                      onClick={startRecording}
                      disabled={loading || sending || transcribing}
                      loading={transcribing}
                      title="语音输入"
                    />
                  )}

                  {/* 文件上传按钮 */}
                  <label className={styles.uploadLabel} title="上传文件">
                    <Paperclip size={17} />
                    <input
                      ref={fileInputRef}
                      type="file"
                      accept=".pdf,.docx,.txt,.md,.markdown,.png,.jpg,.jpeg,.webp"
                      onChange={(event) => {
                        const file = event.target.files?.[0];
                        if (file) {
                          handleFileUpload(file);
                          event.target.value = '';
                        }
                      }}
                      disabled={loading || sending || parsing}
                      style={{ display: 'none' }}
                    />
                  </label>

                  {/* 发送按钮 */}
                  <Button
                    data-testid="profile-send"
                    htmlType="submit"
                    type="primary"
                    icon={<Send size={17} />}
                    loading={sending}
                    disabled={(!answer.trim() && attachments.length === 0) || recording}
                  >
                    发送
                  </Button>
                </div>
              </form>
            </>
          )}
          <div className={styles.secondaryActions}>
            <Button type="text" icon={<SkipForward size={16} />} disabled={loading || sending} onClick={() => closeTask('skip')}>跳过</Button>
            <Button type="text" icon={<Clock3 size={16} />} disabled={loading || sending} onClick={() => closeTask('pause')}>稍后继续</Button>
          </div>
        </footer>
      </section>
    </main>
  );
}
