import {
  BookOpen,
  Bot,
  ChevronLeft,
  ChevronRight,
  Clock3,
  FileText,
  Network,
  NotebookPen,
  Pin,
  Plus,
  Search,
  Sparkles,
  Star,
  Tags,
  Trash2,
  Wand2,
  X,
} from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { App as AntdApp, Button, Drawer, Input, Modal, Popconfirm, Select, Tag } from 'antd';
import {
  batchDeleteNotes, createNote, deleteNote, getNote,
  getNoteCategories, getNoteTags, listNotes, updateNote,
  requestAISuggestion, applySuggestion, dismissSuggestion, undoAI,
  saveRevision, restoreRevision, getRevision,
  type NoteItem, type ApplySuggestionResult, type UndoResult, type SuggestionItem,
} from '../api/notes';
import { StateBlock } from '../components/StateBlock';
import { SafeMarkdown } from '../components/SafeMarkdown';
import emptyNotesArt from '../assets/generated/practice-pack-notebook.jpg';
import styles from './NotesPage.module.css';

function shortTime(v?: string | null) {
  if (!v) return '-';
  return v.replace('T', ' ').slice(0, 16);
}

interface EditorDraft {
  noteId: number;
  title: string;
  content: string;
  category: string;
  tags: string[];
  version: number;
}

type SaveState = 'idle' | 'editing' | 'saving' | 'saved' | 'error';

export function NotesPage() {
  const { message } = AntdApp.useApp();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notes, setNotes] = useState<NoteItem[]>([]);
  const [categories, setCategories] = useState<{ category: string; count: number }[]>([]);
  const [tags, setTags] = useState<string[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [activeNote, setActiveNote] = useState<NoteItem | null>(null);
  const [noteLoading, setNoteLoading] = useState(false);
  const [keyword, setKeyword] = useState('');
  const [categoryFilter, setCategoryFilter] = useState('all');
  const [pinnedOnly, setPinnedOnly] = useState(false);

  // Editor state
  const [editTitle, setEditTitle] = useState('');
  const [editContent, setEditContent] = useState('');
  const [editCategory, setEditCategory] = useState('general');
  const [editTags, setEditTags] = useState<string[]>([]);
  const [saveState, setSaveState] = useState<SaveState>('idle');
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const dirtyRef = useRef(false);
  const activeIdRef = useRef<number | null>(null);
  const draftRef = useRef<EditorDraft | null>(null);
  const queuedDraftRef = useRef<EditorDraft | null>(null);
  const saveChainRef = useRef<Promise<boolean>>(Promise.resolve(true));
  const latestSaveResultRef = useRef(true);
  const selectionRequestRef = useRef(0);
  const aiRequestRef = useRef(0);

  // AI panel
  const [aiDrawerOpen, setAiDrawerOpen] = useState(false);
  const [aiBusy, setAiBusy] = useState('');
  const [aiResult, setAiResult] = useState<{ kind: string; text: string } | null>(null);
  const editorRef = useRef<HTMLTextAreaElement>(null);

  // Right panel — tags editor
  const [tagsOpen, setTagsOpen] = useState(false);
  const [allTags, setAllTags] = useState<string[]>([]);

  useEffect(() => {
    document.body.classList.add('notes-route-active');
    return () => document.body.classList.remove('notes-route-active');
  }, []);

  // Load note list
  const loadList = useCallback(async () => {
    setLoading(true); setError('');
    try {
      const params: Record<string, unknown> = {};
      if (keyword) params.search = keyword;
      if (categoryFilter !== 'all') params.category = categoryFilter;
      if (pinnedOnly) params.pinned = true;
      const items = await listNotes(params);
      setNotes(items);
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败');
    } finally { setLoading(false); }
  }, [keyword, categoryFilter, pinnedOnly]);

  // Load categories & tags
  const loadMeta = useCallback(async () => {
    try {
      const [cats, t] = await Promise.all([getNoteCategories(), getNoteTags()]);
      setCategories(cats);
      setAllTags(t);
    } catch { /* ignore */ }
  }, []);

  useEffect(() => { loadList(); loadMeta(); }, [loadList, loadMeta]);

  useEffect(() => () => {
    if (saveTimer.current) clearTimeout(saveTimer.current);
  }, []);

  const adoptServerNote = useCallback((note: NoteItem, state: SaveState = 'idle') => {
    if (saveTimer.current) {
      clearTimeout(saveTimer.current);
      saveTimer.current = null;
    }
    activeIdRef.current = note.id;
    setActiveId(note.id);
    setActiveNote(note);
    setEditTitle(note.title);
    setEditContent(note.content || '');
    setEditCategory(note.category || 'general');
    setEditTags(note.tags || []);
    draftRef.current = {
      noteId: note.id,
      title: note.title,
      content: note.content || '',
      category: note.category || 'general',
      tags: note.tags || [],
      version: (draftRef.current?.version || 0) + 1,
    };
    dirtyRef.current = false;
    setSaveState(state);
  }, []);

  const persistDraft = useCallback(async (draft: EditorDraft): Promise<boolean> => {
    const isCurrentDraft = () => {
      const current = draftRef.current;
      return activeIdRef.current === draft.noteId
        && current?.noteId === draft.noteId
        && current.version === draft.version;
    };

    if (activeIdRef.current === draft.noteId) setSaveState('saving');

    try {
      const updateResult = await updateNote(draft.noteId, {
        title: draft.title,
        content: draft.content,
        category: draft.category,
        tags: draft.tags,
      });
      if (updateResult.id !== draft.noteId) {
        throw new Error('保存校验失败：服务器返回了不一致的笔记 ID');
      }

      // The current PUT contract only returns id/updated_at. Re-read the note so
      // success is based on persisted server data rather than an optimistic UI.
      const serverNote = await getNote(draft.noteId);
      if (serverNote.id !== draft.noteId || serverNote.content !== draft.content) {
        throw new Error('保存校验失败：服务器正文与本次提交不一致');
      }

      // A newer visible draft may have appeared while this request was running.
      // The queue will persist it next; an older response must never replace it.
      if (!isCurrentDraft()) return true;

      setActiveNote(serverNote);
      setEditTitle(serverNote.title);
      setEditContent(serverNote.content || '');
      setEditCategory(serverNote.category || 'general');
      setEditTags(serverNote.tags || []);
      draftRef.current = {
        noteId: serverNote.id,
        title: serverNote.title,
        content: serverNote.content || '',
        category: serverNote.category || 'general',
        tags: serverNote.tags || [],
        version: draft.version,
      };
      setNotes(prev => (prev || []).map(note => note.id === serverNote.id ? serverNote : note));
      dirtyRef.current = false;
      setSaveState('saved');
      return true;
    } catch (error) {
      if (isCurrentDraft()) {
        dirtyRef.current = true;
        setSaveState('error');
        message.error(error instanceof Error
          ? `${error.message}，您的输入仍保留`
          : '笔记保存失败，您的输入仍保留');
      }
      return false;
    }
  }, []);

  // PUT requests are serialized. When edits arrive during a request, only the
  // newest queued draft is sent next, so an older request cannot finish last.
  const enqueueSave = useCallback((draft: EditorDraft): Promise<boolean> => {
    queuedDraftRef.current = draft;
    const runQueue = async () => {
      let result = latestSaveResultRef.current;
      while (queuedDraftRef.current) {
        const nextDraft = queuedDraftRef.current;
        queuedDraftRef.current = null;
        result = await persistDraft(nextDraft);
        latestSaveResultRef.current = result;
      }
      return result;
    };
    const next = saveChainRef.current.then(runQueue, runQueue);
    saveChainRef.current = next;
    return next;
  }, [persistDraft]);

  const scheduleSave = useCallback((draft: EditorDraft) => {
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => {
      saveTimer.current = null;
      void enqueueSave(draft);
    }, 2000);
  }, [enqueueSave]);

  const flushCurrentDraft = useCallback(async (): Promise<boolean> => {
    if (saveTimer.current) {
      clearTimeout(saveTimer.current);
      saveTimer.current = null;
    }
    const current = draftRef.current;
    if (dirtyRef.current && current) return await enqueueSave(current);
    return await saveChainRef.current;
  }, [enqueueSave]);

  // Select note
  const selectNote = useCallback(async (id: number) => {
    if (dirtyRef.current && !(await flushCurrentDraft())) {
      message.error('当前笔记尚未保存，已取消切换');
      return;
    }
    const selectionRequest = ++selectionRequestRef.current;
    aiRequestRef.current += 1;
    activeIdRef.current = id;
    setActiveId(id);
    setNoteLoading(true);
    setAiResult(null);
    setCurrentSuggestion(null);
    setAiBusy('');
    try {
      const note = await getNote(id);
      if (selectionRequest !== selectionRequestRef.current) return;
      adoptServerNote(note);
    } catch {
      if (selectionRequest === selectionRequestRef.current) setActiveNote(null);
    } finally {
      if (selectionRequest === selectionRequestRef.current) setNoteLoading(false);
    }
  }, [adoptServerNote, flushCurrentDraft]);

  // Tag changes use the same latest-draft queue as title/content changes.
  const saveTags = async (newTags: string[]) => {
    const current = draftRef.current;
    if (!current) return;
    const next = { ...current, tags: newTags, version: current.version + 1 };
    draftRef.current = next;
    setEditTags(newTags);
    dirtyRef.current = true;
    setSaveState('editing');
    if (saveTimer.current) {
      clearTimeout(saveTimer.current);
      saveTimer.current = null;
    }
    await enqueueSave(next);
  };

  const updateDraft = (patch: Partial<Pick<EditorDraft, 'title' | 'content' | 'category' | 'tags'>>) => {
    const current = draftRef.current;
    if (!current) return;
    const next = { ...current, ...patch, version: current.version + 1 };
    draftRef.current = next;
    dirtyRef.current = true;
    setSaveState('editing');
    scheduleSave(next);
  };

  const onTitleChange = (value: string) => {
    setEditTitle(value);
    updateDraft({ title: value });
  };
  const onContentChange = (value: string) => {
    setEditContent(value);
    updateDraft({ content: value });
  };

  // Create new note
  const doCreate = async () => {
    if (!(await flushCurrentDraft())) return;
    try {
      const { id } = await createNote({ title: '新笔记', content: '', category: 'general' });
      message.success('笔记已创建');
      await loadList();
      await selectNote(id);
    } catch (e) {
      message.error(e instanceof Error ? e.message : '创建失败');
    }
  };

  // Delete note
  const doDelete = async () => {
    if (!activeId) return;
    if (!(await flushCurrentDraft())) return;
    try {
      await deleteNote(activeId);
      message.success('笔记已删除');
      activeIdRef.current = null;
      draftRef.current = null;
      setActiveId(null); setActiveNote(null);
      await loadList();
    } catch (e) {
      message.error(e instanceof Error ? e.message : '删除失败');
    }
  };

  // Toggle pin
  const doTogglePin = async () => {
    if (!activeId) return;
    try {
      const newPin = !activeNote?.is_pinned;
      await updateNote(activeId, { is_pinned: newPin });
      setActiveNote(prev => prev ? { ...prev, is_pinned: newPin } : null);
      setNotes(prev => (prev || []).map(n => n.id === activeId ? { ...n, is_pinned: newPin } : n));
    } catch { /* ignore */ }
  };

  // Toggle favorite
  const doToggleFavorite = async () => {
    if (!activeId) return;
    try {
      const newFav = !activeNote?.is_favorite;
      await updateNote(activeId, { is_favorite: newFav });
      setActiveNote(prev => prev ? { ...prev, is_favorite: newFav } : null);
      setNotes(prev => (prev || []).map(n => n.id === activeId ? { ...n, is_favorite: newFav } : n));
    } catch { /* ignore */ }
  };

  // AI suggestion state
  const [currentSuggestion, setCurrentSuggestion] = useState<{id:number;note_id:number;note_version:number|null;action_type:string;apply_mode:string;content:string;structured:any}|null>(null);
  const [versionHistory, setVersionHistory] = useState<any[]>([]);
  const [versionOpen, setVersionOpen] = useState(false);

  // AI actions - real API
  const askAi = async (kind: string) => {
    if (!activeId) return;
    if (!(await flushCurrentDraft())) return;
    const noteId = activeId;
    const requestId = ++aiRequestRef.current;
    setAiBusy(kind);
    setAiResult(null); setCurrentSuggestion(null);
    try {
      const result = await requestAISuggestion(noteId, kind);
      if (requestId !== aiRequestRef.current || activeIdRef.current !== noteId) return;
      // Determine apply_mode: from suggestion's own field or derive from action_type
      const applyModeMap: Record<string, string> = {
        polish: 'replace_content', structure: 'replace_content', expand: 'replace_content',
        summarize: 'append_content', extract_knowledge: 'append_content', outline: 'append_content',
        check_understanding: 'display_only', '偏差分析': 'display_only', '下一步建议': 'display_only',
        generate_exercises: 'artifact_only', extract_questions: 'display_only', mindmap_plan: 'display_only',
      };
      const am = (result as any).apply_mode || applyModeMap[kind] || 'replace_content';
      setCurrentSuggestion({id:result.id, note_id:result.note_id, note_version:result.note_version, action_type:result.action_type, apply_mode: am, content:result.suggestion_content||'', structured:result.structured_result});
      setAiResult({kind, text: result.suggestion_content || ''});
      // The actionable result lives beside the editor. Close the modal drawer
      // so its mask cannot block Replace / Append / Insert controls.
      setAiDrawerOpen(false);
      message.success('AI 建议已生成');
      // Keep the actionable suggestion bound to this editor instance. A later
      // note reload is unnecessary and would invalidate this note-scoped result.
    } catch (e) {
      setAiResult({kind, text: `AI 失败: ${e instanceof Error ? e.message : '未知错误'}`});
      message.error('AI 处理失败');
    } finally {
      if (requestId === aiRequestRef.current) setAiBusy('');
    }
  };

  const doAcceptSuggestion = async () => {
    if (!activeId || !currentSuggestion) return;
    if (currentSuggestion.note_id !== activeId) {
      setCurrentSuggestion(null); setAiResult(null);
      message.warning('这条建议不属于当前笔记，请重新生成');
      return;
    }
    if (!(await flushCurrentDraft())) return;
    const am = currentSuggestion.apply_mode || 'replace_content';
    // Prevent applying display_only or artifact_only
    if (am === 'display_only') { message.warning('此建议仅供查看，不能应用到笔记正文'); return; }
    if (am === 'artifact_only') { message.warning('此建议需要生成独立产物，不能直接应用到笔记正文'); return; }
    try {
      const result = await applySuggestion(activeId, currentSuggestion.id);
      if (result.note) {
        adoptServerNote(result.note, 'saved');
      }
      const verb = am === 'append_content' ? '已追加到笔记' : '已替换笔记内容';
      message.success(`AI 建议${verb}`);
      setCurrentSuggestion(null); setAiResult(null);
    } catch (e) { message.error(e instanceof Error ? e.message : '应用失败'); }
  };

  // 插入到光标当前位置：作为一次普通的用户编辑进入自动保存草稿链，
  // 建议本身在服务端保持未应用状态，仍可复制或关闭。
  const insertSuggestionAtCursor = () => {
    if (!currentSuggestion) return;
    const textarea = editorRef.current;
    const start = textarea ? textarea.selectionStart : editContent.length;
    const end = textarea ? textarea.selectionEnd : start;
    const next = `${editContent.slice(0, start)}${currentSuggestion.content}${editContent.slice(end)}`;
    onContentChange(next);
    message.success('已插入到当前位置');
  };

  const replaceSelectionWithSuggestion = () => {
    if (!currentSuggestion) return;
    const textarea = editorRef.current;
    const start = textarea?.selectionStart ?? 0;
    const end = textarea?.selectionEnd ?? start;
    if (start === end) {
      message.warning('请先在原文中选中要替换的段落');
      textarea?.focus();
      return;
    }
    const next = `${editContent.slice(0, start)}${currentSuggestion.content}${editContent.slice(end)}`;
    onContentChange(next);
    requestAnimationFrame(() => {
      textarea?.focus();
      const caret = start + currentSuggestion.content.length;
      textarea?.setSelectionRange(caret, caret);
    });
    message.success('已替换选中的段落，保存前仍可继续修改');
  };

  const regenerateSuggestion = () => {
    const kind = aiResult?.kind || currentSuggestion?.action_type;
    if (!kind) return;
    void askAi(kind);
  };

  const doDismissSuggestion = async () => {
    if (!activeId || !currentSuggestion) return;
    try {
      await dismissSuggestion(activeId, currentSuggestion.id);
      message.success('建议已关闭');
      setCurrentSuggestion(null); setAiResult(null);
      if (activeId) selectNote(activeId);
    } catch (e) { message.error('操作失败'); }
  };

  const doUndoAI = async () => {
    if (!activeId) return;
    if (!(await flushCurrentDraft())) return;
    try {
      const result = await undoAI(activeId);
      // Update editor immediately with restored note
      if (result.note) {
        adoptServerNote(result.note, 'saved');
      }
      message.success('已撤销 AI 修改');
    } catch (e: any) {
      // Check for 409 conflict (user edited after AI apply)
      if (e?.response?.status === 409) {
        const detail = e.response.data?.detail;
        if (detail?.code === 'NOTE_CHANGED_AFTER_AI_APPLY') {
          Modal.warning({
            title: '无法撤销',
            content: 'AI 建议应用后笔记已有新的修改。为避免丢失您的内容，请通过版本历史手动恢复。',
            okText: '查看版本历史',
            onOk: () => { setVersionOpen(true); },
          });
          return;
        }
        if (detail?.code === 'AI_UNDO_STATE_INCONSISTENT') {
          message.error('数据状态不一致，请联系支持');
          return;
        }
      }
      message.error(e instanceof Error ? e.message : '撤销失败');
    }
  };

  const doSaveVersion = async () => {
    if (!activeId) return;
    if (!(await flushCurrentDraft())) return;
    try {
      const rev = await saveRevision(activeId);
      if (rev) message.success(`版本 ${rev.revision_number} 已保存`);
      else message.info('内容未变化，跳过保存');
      if (activeId) selectNote(activeId);
    } catch (e) { message.error('保存版本失败'); }
  };

  const doRestoreVersion = async (revId: number) => {
    if (!activeId) return;
    if (!(await flushCurrentDraft())) return;
    try { await restoreRevision(activeId, revId); message.success('版本已恢复'); setVersionOpen(false); selectNote(activeId); }
    catch (e) { message.error('恢复失败'); }
  };

  const aiActions = [
    { kind: 'expand', label: '扩写补充', icon: Wand2, desc: '补充细节与深度' },
    { kind: 'polish', label: '润色整理', icon: Sparkles, desc: '优化表达与结构' },
    { kind: 'structure', label: '结构化', icon: Network, desc: '层次清晰的组织' },
    { kind: 'summarize', label: '生成摘要', icon: FileText, desc: '提炼核心要点' },
    { kind: 'extract', label: '提取知识点', icon: BookOpen, desc: '识别关键概念' },
    { kind: 'check', label: '理解检查', icon: Bot, desc: '指出偏差与遗漏' },
    { kind: 'next', label: '下一步建议', icon: ChevronRight, desc: '推荐学习方向' },
    { kind: 'questions', label: '生成练习题', icon: Wand2, desc: '检验理解程度' },
    { kind: 'outline', label: '转复习提纲', icon: Tags, desc: '考点与重点' },
  ];

  const actionLabels: Record<string, string> = {
    expand: '扩写补充', polish: '润色整理', structure: '结构化整理', summarize: '生成摘要',
    extract: '提取知识点', extract_knowledge: '提取知识点', check: '理解检查', check_understanding: '理解检查',
    next: '下一步建议', 下一步建议: '下一步建议', questions: '生成练习题', generate_exercises: '生成练习题',
    outline: '转复习提纲',
  };
  const busyLabel = aiBusy ? (actionLabels[aiBusy] || '处理') : '';

  // Content preview for note list
  const preview = (content?: string) => {
    if (!content) return '空笔记';
    return content.replace(/\s+/g, ' ').slice(0, 80) + (content.length > 80 ? '…' : '');
  };

  return (
    <div className={styles.page} data-testid="notes-page">
      {/* ===== Left Panel ===== */}
      <aside className={styles.leftPanel}>
        <div className={styles.leftHeader}>
          <h2><NotebookPen size={20} /> 我的笔记</h2>
          <Input
            prefix={<Search size={14} />}
            placeholder="搜索笔记..."
            value={keyword}
            onChange={e => setKeyword(e.target.value)}
            allowClear
            size="small"
          />
          <div style={{ display: 'flex', gap: 6 }}>
            <Select
              size="small"
              value={categoryFilter}
              onChange={v => setCategoryFilter(v)}
              style={{ flex: 1 }}
              options={[
                { value: 'all', label: `全部 (${notes.length})` },
                ...(categories || []).map(c => ({ value: c.category, label: `${c.category} (${c.count})` })),
              ]}
            />
            <Button
              size="small"
              type={pinnedOnly ? 'primary' : 'default'}
              icon={<Pin size={12} />}
              onClick={() => setPinnedOnly(!pinnedOnly)}
            />
          </div>
          <Button type="primary" block icon={<Plus size={14} />} onClick={doCreate}>
            新建笔记
          </Button>
        </div>

        <div className={styles.noteList}>
          {loading ? (
            <div className={styles.skeleton}>
              {[1, 2, 3, 4, 5].map(i => <div key={i} className={styles.skeletonItem} />)}
            </div>
          ) : error ? (
            <div className={styles.emptyNotes}>
              <strong>加载失败</strong>
              <small>{error}</small>
              <Button size="small" onClick={loadList}>重试</Button>
            </div>
          ) : (notes || []).length === 0 ? (
            <div className={styles.emptyNotes}>
              <img className={styles.emptyArt} src={emptyNotesArt} alt="" aria-hidden="true" />
              <strong>还没有笔记</strong>
              <small>记录今天学到的一件事吧。<br />柿小吉会帮你整理和理解。</small>
              <Button type="primary" icon={<Plus size={14} />} onClick={doCreate}>写第一篇笔记</Button>
            </div>
          ) : (
            (notes || []).map(note => (
              <div
                key={note.id}
                className={styles.noteItem}
                data-active={activeId === note.id}
                onClick={() => selectNote(note.id)}
              >
                <div className={styles.noteItemTitle}>
                  {note.is_pinned && <Pin size={11} style={{ color: '#ff5a14', flexShrink: 0 }} />}
                  {note.is_favorite && <Star size={11} style={{ color: '#f5a623', flexShrink: 0 }} />}
                  {note.title || '无标题'}
                </div>
                <div className={styles.noteItemMeta}>
                  <Tag style={{ fontSize: 11, lineHeight: '18px', padding: '0 6px' }}>{note.category || '未分类'}</Tag>
                  <time>{shortTime(note.updated_at)}</time>
                </div>
                <div className={styles.noteItemPreview}>{preview(note.plain_text || note.content)}</div>
              </div>
            ))
          )}
        </div>
      </aside>

      {/* ===== Middle Panel — Editor ===== */}
      <main className={styles.middlePanel}>
        {noteLoading ? (
          <StateBlock state="loading" title="加载笔记内容..." />
        ) : activeNote ? (
          <>
            <div className={styles.editorHeader}>
              <input
                className={styles.titleInput}
                value={editTitle}
                onChange={e => onTitleChange(e.target.value)}
                placeholder="笔记标题"
                maxLength={200}
              />
              <div className={styles.editorToolbar}>
                <span className={styles.saveStatus} data-state={saveState}>
                  {saveState === 'saving' && <><Clock3 size={12} /> 保存中…</>}
                  {saveState === 'saved' && <>✓ 已保存</>}
                  {saveState === 'error' && <>✗ 保存失败</>}
                  {saveState === 'editing' && <>编辑中</>}
                  {saveState === 'idle' && <>未修改</>}
                </span>
                <Button size="small" icon={activeNote.is_pinned ? <Pin size={12} style={{color:'#ff5a14'}} /> : <Pin size={12} />}
                  onClick={doTogglePin} type={activeNote.is_pinned ? 'primary' : 'default'} />
                <Button size="small" icon={activeNote.is_favorite ? <Star size={12} style={{color:'#f5a623'}} /> : <Star size={12} />}
                  onClick={doToggleFavorite} type={activeNote.is_favorite ? 'primary' : 'default'} />
                <Button size="small" icon={<Bot size={13} />} data-testid="notes-ai-drawer-open" onClick={() => setAiDrawerOpen(true)}>AI 帮我</Button>
                <Button size="small" icon={<Tags size={12} />} onClick={() => setTagsOpen(true)}>标签</Button>
                <Popconfirm title="确认删除这条笔记？" onConfirm={doDelete} okText="删除" cancelText="取消">
                  <Button size="small" danger icon={<Trash2 size={12} />} />
                </Popconfirm>
              </div>
            </div>

            <textarea
              ref={editorRef}
              className={styles.contentEditor}
              value={editContent}
              onChange={e => onContentChange(e.target.value)}
              placeholder="在这里写下你的理解、感悟和思考…"
            />

            {aiBusy && <div className={styles.resultBusy} data-testid="note-ai-busy" role="status">
              <Sparkles size={14} />柿小吉正在帮你{busyLabel}，马上就好…
            </div>}

            {!aiBusy && currentSuggestion && (() => {
              const am = currentSuggestion.apply_mode || 'replace_content';
              const actionLabel = actionLabels[currentSuggestion.action_type] || actionLabels[aiResult?.kind || ''] || '整理';
              return (
                <div className={styles.resultPanel} data-testid="note-ai-result">
                  <div className={styles.resultHeader}>
                    <Sparkles size={14} />
                    <strong>柿小吉帮你做了：{actionLabel}</strong>
                    {am === 'display_only' && <span className={styles.resultMode}>仅供查看参考</span>}
                    {am === 'artifact_only' && <span className={styles.resultMode}>会生成为独立学习产物</span>}
                  </div>
                  <SafeMarkdown className={styles.resultPreview} source={currentSuggestion.content} testId="note-ai-preview" />
                  <div className={styles.resultActions}>
                    {am === 'replace_content' && <Button size="small" type="primary" data-testid="note-ai-replace" onClick={doAcceptSuggestion}>替换原文</Button>}
                    {am === 'append_content' && <Button size="small" type="primary" data-testid="note-ai-append" onClick={doAcceptSuggestion}>保留原文并追加</Button>}
                    {(am === 'replace_content' || am === 'append_content') && (
                      <Button size="small" data-testid="note-ai-insert" onClick={insertSuggestionAtCursor}>插入到当前位置</Button>
                    )}
                    {(am === 'replace_content' || am === 'append_content') && (
                      <Button size="small" data-testid="note-ai-replace-selection" onClick={replaceSelectionWithSuggestion}>替换选中段落</Button>
                    )}
                    <Button size="small" data-testid="note-ai-regenerate" onClick={regenerateSuggestion}>重新生成</Button>
                    <Button size="small" data-testid="note-ai-copy" onClick={() => { navigator.clipboard.writeText(currentSuggestion.content); message.success('已复制'); }}>复制</Button>
                    <Button size="small" data-testid="note-ai-close" onClick={doDismissSuggestion}>关闭</Button>
                  </div>
                </div>
              );
            })()}

            {!aiBusy && aiResult && !currentSuggestion && (
              <div className={styles.resultPanel} data-testid="note-ai-error">
                <div className={styles.resultHeader}><X size={14} /><strong>这次没有处理好</strong></div>
                <SafeMarkdown className={styles.resultPreview} source={aiResult.text.replace(/^AI 失败: /, '')} />
                <div className={styles.resultActions}>
                  <Button size="small" onClick={() => setAiResult(null)}>关闭</Button>
                </div>
              </div>
            )}

            <div className={styles.associationBar}>
              <strong>关联：</strong>
              {activeNote.graph_node_id ? (
                <Tag color="green">已关联学习图谱</Tag>
              ) : <Tag>未关联学习图谱</Tag>}
              {(activeNote.material_ids || []).length > 0 && (
                <Tag color="blue">资料 ×{activeNote.material_ids.length}</Tag>
              )}
              {(activeNote.artifact_ids || []).length > 0 && (
                <Tag color="purple">产物 ×{activeNote.artifact_ids.length}</Tag>
              )}
              <span style={{ marginLeft: 'auto', fontSize: 12 }}>
                {activeNote.word_count || editContent.length} 字 · 更新于 {shortTime(activeNote.updated_at)}
              </span>
            </div>
          </>
        ) : (
          <div className={styles.editorEmpty}>
            <img className={styles.emptyArt} src={emptyNotesArt} alt="" aria-hidden="true" />
            <strong>选择或创建一篇笔记</strong>
            <small>左侧列表中选择笔记开始编辑，或点击"新建笔记"创建第一篇。</small>
            <Button type="primary" icon={<Plus size={14} />} onClick={doCreate}>新建笔记</Button>
            <Button icon={<Bot size={14} />} data-testid="notes-ai-drawer-open" onClick={() => setAiDrawerOpen(true)}>AI 帮我</Button>
          </div>
        )}
      </main>

      {/* ===== Right Panel — AI 助手 ===== */}
      <Drawer title="AI 帮我" open={aiDrawerOpen} onClose={() => setAiDrawerOpen(false)} width={380} data-testid="notes-ai-drawer">
        <div className={styles.rightHeader}>
          <h3><Bot size={16} /><span>AI 助手</span></h3>
        </div>

        <div className={styles.rightContent}>
          {!activeNote ? (
            <div className={styles.aiEmpty}>
              <Bot size={28} strokeWidth={1} /><br />
              打开一篇笔记，<br />AI 助手将帮你理解和整理。
            </div>
          ) : (
            <>
              <div className={styles.aiActionLabel}>内容处理</div>
              <div className={styles.aiActionGroup}>
                {aiActions.slice(0, 3).map(a => (
                  <Button key={a.kind} size="small" block icon={<a.icon size={13} />}
                    loading={aiBusy === a.kind} onClick={() => askAi(a.kind)}>{a.label}</Button>
                ))}
              </div>
              <div className={styles.aiActionLabel}>理解与诊断</div>
              <div className={styles.aiActionGroup}>
                {aiActions.slice(3, 6).map(a => (
                  <Button key={a.kind} size="small" block icon={<a.icon size={13} />}
                    loading={aiBusy === a.kind} onClick={() => askAi(a.kind)}>{a.label}</Button>
                ))}
              </div>
              <div className={styles.aiActionLabel}>进阶</div>
              <div className={styles.aiActionGroup}>
                {aiActions.slice(6).map(a => (
                  <Button key={a.kind} size="small" block icon={<a.icon size={13} />}
                    loading={aiBusy === a.kind} onClick={() => askAi(a.kind)}>{a.label}</Button>
                ))}
              </div>
              <Button size="small" block icon={<Clock3 size={13} />} onClick={doSaveVersion}>保存当前版本</Button>
              <Button size="small" block icon={<Clock3 size={13} />} onClick={doUndoAI}>撤销 AI 修改</Button>
              <Button size="small" block icon={<FileText size={13} />} onClick={() => { setVersionOpen(true); }}>查看版本历史</Button>
              {(currentSuggestion || aiBusy) && <p className={styles.aiHint}>结果会显示在编辑区旁边，可以直接替换、插入或追加。</p>}
            </>
          )}
        </div>
      </Drawer>

      {/* Tags Manager Drawer */}
      <Drawer title="管理标签" open={tagsOpen} onClose={() => setTagsOpen(false)} width={360}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <Input.Search
            placeholder="添加标签..."
            enterButton="添加"
            onSearch={async (val) => {
              if (!val.trim() || !activeId) return;
              const newTags = [...new Set([...editTags, val.trim()])];
              saveTags(newTags);
            }}
          />
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {(allTags || []).map(t => (
              <Tag
                key={t}
                color={editTags.includes(t) ? 'orange' : undefined}
                style={{ cursor: 'pointer' }}
                onClick={() => {
                  const newTags = editTags.includes(t)
                    ? editTags.filter(x => x !== t)
                    : [...editTags, t];
                  saveTags(newTags);
                }}
              >
                {editTags.includes(t) ? `✓ ${t}` : t}
              </Tag>
            ))}
          </div>
          <div style={{ fontWeight: 600, fontSize: 13, marginTop: 8 }}>当前笔记标签：</div>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {editTags.length ? editTags.map(t => (
              <Tag key={t} closable color="orange" onClose={() => {
                saveTags(editTags.filter(x => x !== t));
              }}>{t}</Tag>
            )) : <span style={{ color: 'var(--muted)', fontSize: 13 }}>暂无标签</span>}
          </div>
        </div>
      </Drawer>
    </div>
  );
}
