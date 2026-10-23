(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.AIWorkspaceContext = api;
}(typeof window !== 'undefined' ? window : globalThis, function () {
  'use strict';

  var STATE_VERSION = 1;
  var SESSION_PREFIX = 'kaki_ai_workspace_context_v1:';
  var QUIET_KEY = 'kaki_ai_workspace_quiet_mode_v1';
  var VIEW_KEY = 'kaki_ai_workspace_view_v1';
  var MAX_REFERENCES = 4;

  function nowIso() { return new Date().toISOString(); }
  function text(value, max) {
    var result = String(value == null ? '' : value).replace(/\s+/g, ' ').trim();
    return max && result.length > max ? result.slice(0, max - 1) + '…' : result;
  }
  function numberOrNull(value) {
    var parsed = Number(value);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
  }
  function storageRead(storage, key, fallback) {
    try {
      var raw = storage && storage.getItem(key);
      if (!raw) return fallback;
      return JSON.parse(raw);
    } catch (ignore) { return fallback; }
  }
  function storageWrite(storage, key, value) {
    try { if (storage) storage.setItem(key, JSON.stringify(value)); } catch (ignore) {}
  }
  function storageRemove(storage, key) {
    try { if (storage) storage.removeItem(key); } catch (ignore) {}
  }

  function emptyState(threadId, quietMode, viewMode) {
    return {
      schema_version: STATE_VERSION,
      thread_ref: threadId || '',
      graph_ref: null,
      node_ref: null,
      plan_ref: null,
      primary_object: null,
      reference_objects: [],
      selection_ref: null,
      reading_position_ref: null,
      intent: 'general_chat',
      source: 'none',
      reason: '当前没有引用资源',
      quiet_mode: Boolean(quietMode),
      view_mode: viewMode === 'resource' ? 'resource' : 'conversation',
      updated_at: nowIso()
    };
  }

  function sanitizeGraph(value) {
    if (!value) return null;
    var id = text(value.graph_id || value.external_id || value.id, 120);
    if (!id) return null;
    return {
      graph_id: id,
      title: text(value.graph_title || value.title || value.goal || '当前学习图谱', 120),
      revision: Number(value.graph_revision || value.revision || 0) || null
    };
  }
  function sanitizeNode(value) {
    if (!value) return null;
    var id = text(value.node_id || value.graph_node_id || value.external_id || value.id, 120);
    if (!id) return null;
    return {
      node_id: id,
      title: text(value.node_title || value.title || value.summary || '当前学习节点', 140),
      continuity_key: text(value.continuity_key || '', 160) || null
    };
  }
  function sanitizePlan(value) {
    if (!value) return null;
    var id = text(value.resource_plan_id || value.plan_id || value.external_id || value.id, 120);
    if (!id) return null;
    return {
      plan_id: id,
      title: text(value.plan_title || value.purpose || value.title || '当前资源计划', 140),
      resource_type: text(value.resource_type || value.kind || '', 60) || null,
      status: text(value.status || '', 40) || null,
      continuity_key: text(value.resource_plan_continuity_key || value.continuity_key || '', 160) || null
    };
  }
  function isExamKind(value) {
    var kind = text(value && (value.kind || value.artifact_kind || String(value.type || '').replace(/^workspace_/, '')), 60);
    return kind === 'exam' || kind === 'exam_result';
  }
  function sanitizeArtifact(value) {
    if (!value || isExamKind(value)) return null;
    var id = numberOrNull(value.artifact_id || value.id);
    if (!id) return null;
    var version = Number(value.version_number || value.version || 1) || 1;
    var versionId = numberOrNull(value.version_id || value.artifact_version_id);
    return {
      object_key: 'artifact:' + id + ':v' + version,
      artifact_id: id,
      kind: text(value.kind || value.artifact_kind || String(value.type || '').replace(/^workspace_/, ''), 60) || 'artifact',
      title: text(value.title || value.topic || '学习产物', 160),
      topic: text(value.topic || value.chapter || '', 160) || null,
      version: version,
      version_id: versionId,
      graph_id: text(value.graph_id || '', 120) || null,
      graph_title: text(value.graph_title || '', 140) || null,
      graph_revision: Number(value.graph_revision || 0) || null,
      node_id: text(value.graph_node_id || value.node_id || '', 120) || null,
      node_title: text(value.graph_node_title || value.node_title || '', 140) || null,
      plan_id: text(value.resource_plan_id || value.plan_id || '', 120) || null,
      plan_title: text(value.resource_plan_title || value.plan_title || '', 140) || null,
      status: text(value.status || value.generation_status || 'generated', 40),
      source: text(value.context_source || 'artifact_opened', 60),
      bound_at: nowIso()
    };
  }
  function sanitizeSelection(value) {
    if (!value || !value.summary) return null;
    return {
      artifact_id: numberOrNull(value.artifact_id),
      version: Number(value.version_number || value.version || 1) || 1,
      version_id: numberOrNull(value.version_id || value.artifact_version_id),
      summary: text(value.summary, 160),
      range_label: text(value.range_label || '选中内容', 120),
      source_label: text(value.source_label || '资源内主动选择', 120),
      purpose: text(value.purpose || '解释选中内容', 80),
      recoverable_summary_only: value.recoverable_summary_only !== false,
      captured_at: value.captured_at || nowIso()
    };
  }
  function sanitizeReadingPosition(value) {
    if (!value || !numberOrNull(value.artifact_id)) return null;
    return {
      artifact_id: numberOrNull(value.artifact_id),
      version: Number(value.version_number || value.version || 1) || 1,
      version_id: numberOrNull(value.version_id || value.artifact_version_id),
      scroll_top: Math.max(0, Number(value.scroll_top || 0) || 0),
      anchor: text(value.anchor || '', 120) || null,
      updated_at: value.updated_at || nowIso()
    };
  }

  function normalizeState(value, threadId, quietMode, viewMode) {
    var base = emptyState(threadId, quietMode, viewMode);
    if (!value || typeof value !== 'object') return base;
    base.graph_ref = sanitizeGraph(value.graph_ref);
    base.node_ref = sanitizeNode(value.node_ref);
    base.plan_ref = sanitizePlan(value.plan_ref);
    base.primary_object = sanitizeArtifact(value.primary_object);
    base.reference_objects = Array.isArray(value.reference_objects)
      ? value.reference_objects.map(sanitizeArtifact).filter(Boolean).slice(0, MAX_REFERENCES)
      : [];
    base.selection_ref = sanitizeSelection(value.selection_ref);
    base.reading_position_ref = sanitizeReadingPosition(value.reading_position_ref);
    base.intent = text(value.intent || 'general_chat', 80);
    base.source = text(value.source || 'restored', 80);
    base.reason = text(value.reason || base.reason, 220);
    base.quiet_mode = Boolean(quietMode);
    base.view_mode = viewMode === 'resource' ? 'resource' : (value.view_mode === 'resource' ? 'resource' : 'conversation');
    base.updated_at = value.updated_at || nowIso();
    if (!base.primary_object) {
      base.graph_ref = base.graph_ref || null;
      base.node_ref = base.node_ref || null;
      base.plan_ref = base.plan_ref || null;
      base.selection_ref = null;
      base.reading_position_ref = null;
    }
    return base;
  }

  function mergeArtifact(base, patch) {
    var left = sanitizeArtifact(base) || {};
    var right = sanitizeArtifact(Object.assign({}, base || {}, patch || {})) || left;
    return Object.assign({}, left, right);
  }

  function action(id, label, source, prompt, enabled) {
    return { id: id, label: label, source: source, prompt: prompt || '', enabled: enabled !== false };
  }

  function deriveActions(state, taskState) {
    var result = [];
    var status = taskState && (taskState.status || (taskState.task && taskState.task.state)) || 'idle';
    var primary = state.primary_object;
    if (status === 'failed') {
      result.push(action('retry_task', '重新尝试', 'TASK_STATE_DERIVED'));
      result.push(action('adjust_request', '修改要求', 'TASK_STATE_DERIVED', '请根据刚才失败的步骤，帮我缩小范围后重新处理。'));
      if (primary) result.push(action('return_previous', '返回当前版本', 'CONTEXT_DERIVED'));
      return result.slice(0, 3);
    }
    if (status === 'running' || status === 'generating') {
      result.push(action('collapse_task', '先收起，完成后提醒我', 'TASK_STATE_DERIVED'));
      result.push(action('cancel_task', '取消当前任务', 'TASK_STATE_DERIVED'));
      return result.slice(0, 3);
    }
    if (state.selection_ref) {
      result.push(action('explain_selection', '解释选中内容', 'CONTEXT_DERIVED', '请解释我刚才选中的内容，并结合当前学习节点说明它为什么重要。'));
      result.push(action('rephrase', '换一种讲法', 'CONTEXT_DERIVED', '请换一种更直观的方式解释我选中的内容。'));
      result.push(action('micro_practice', '生成一道小练习', 'CONTEXT_DERIVED', '请根据我选中的内容生成一道很短的小练习，先不要直接给答案。'));
      return result;
    }
    if (primary) {
      result.push(action('continue_explain', '继续解释', 'CONTEXT_DERIVED', '请继续解释当前资源，并指出与当前学习节点最相关的部分。'));
      if (primary.kind === 'code') result.push(action('run_code', '运行当前代码', 'CONTEXT_DERIVED'));
      else result.push(action('rephrase', '换一种讲法', 'CONTEXT_DERIVED', '请换一种更容易理解的方式解释当前资源。'));
      result.push(action('micro_practice', '生成一道小练习', 'CONTEXT_DERIVED', '请基于当前资源生成一道小练习，先让我作答。'));
      return result.slice(0, 3);
    }
    if (state.node_ref || state.graph_ref) {
      result.push(action('open_learning_node', '返回当前学习节点', 'CONTEXT_DERIVED'));
      result.push(action('continue_explain', '继续普通对话', 'STATIC_FALLBACK', '请继续讲解当前学习主题。'));
      result.push(action('generate_resource', '生成学习资源', 'STATIC_FALLBACK', '请为当前学习主题生成一份适合现在阅读的学习资源。'));
      return result.slice(0, 3);
    }
    return [
      action('continue_explain', '继续普通对话', 'STATIC_FALLBACK', '请继续讲解。'),
      action('choose_resource', '选择最近资源', 'STATIC_FALLBACK'),
      action('open_learning_node', '打开学习图谱', 'STATIC_FALLBACK')
    ];
  }

  function createController(options) {
    options = options || {};
    var env = options.environment || (typeof window !== 'undefined' ? window : {});
    var doc = env.document;
    var session = env.sessionStorage;
    var local = env.localStorage;
    var threadId = '';
    var state = emptyState('', local && local.getItem(QUIET_KEY) === 'true', local && local.getItem(VIEW_KEY));
    var taskState = null;
    var selectionTimer = null;
    var destroyed = false;

    function sessionKey(value) { return SESSION_PREFIX + (value || 'none'); }
    function persist() {
      state.updated_at = nowIso();
      storageWrite(session, sessionKey(threadId), state);
      try { if (local) local.setItem(QUIET_KEY, state.quiet_mode ? 'true' : 'false'); } catch (ignore) {}
      try { if (local) local.setItem(VIEW_KEY, state.view_mode); } catch (ignore) {}
      render();
      if (typeof options.onStateChange === 'function') options.onStateChange(getState());
    }
    function getState() { return JSON.parse(JSON.stringify(state)); }
    function load(nextThreadId) {
      threadId = String(nextThreadId || '');
      var quiet = local && local.getItem(QUIET_KEY) === 'true';
      var view = local && local.getItem(VIEW_KEY) || 'conversation';
      state = normalizeState(storageRead(session, sessionKey(threadId), null), threadId, quiet, view);
      render();
      return getState();
    }
    function reset(nextThreadId) {
      threadId = String(nextThreadId || threadId || '');
      state = emptyState(threadId, state.quiet_mode, 'conversation');
      storageRemove(session, sessionKey(threadId));
      persist();
    }

    function bindArtifact(artifact, meta) {
      meta = meta || {};
      var normalized = sanitizeArtifact(Object.assign({}, artifact || {}, {
        context_source: meta.source || artifact && artifact.context_source
      }));
      if (!normalized) return getState();
      if (state.primary_object && state.primary_object.object_key !== normalized.object_key) {
        var old = state.primary_object;
        state.reference_objects = [old].concat(state.reference_objects || []).filter(function (item, index, items) {
          return item && item.object_key !== normalized.object_key
            && items.findIndex(function (candidate) { return candidate.object_key === item.object_key; }) === index;
        }).slice(0, MAX_REFERENCES);
      }
      state.primary_object = normalized;
      state.reference_objects = (state.reference_objects || []).filter(function (item) { return item.object_key !== normalized.object_key; });
      state.graph_ref = sanitizeGraph({ graph_id: normalized.graph_id, graph_title: normalized.graph_title, graph_revision: normalized.graph_revision }) || state.graph_ref;
      state.node_ref = sanitizeNode({ node_id: normalized.node_id, node_title: normalized.node_title }) || state.node_ref;
      state.plan_ref = sanitizePlan({ resource_plan_id: normalized.plan_id, plan_title: normalized.plan_title, status: normalized.status, resource_type: normalized.kind }) || state.plan_ref;
      state.intent = text(meta.intent || 'discuss_artifact', 80);
      state.source = text(meta.source || 'artifact_opened', 80);
      state.reason = text(meta.reason || '你刚才主动打开了这份资源', 220);
      if (state.selection_ref && state.selection_ref.artifact_id !== normalized.artifact_id) state.selection_ref = null;
      state.view_mode = meta.keepView ? state.view_mode : 'resource';
      persist();
      return getState();
    }
    function enrichPrimary(patch) {
      if (!state.primary_object) return getState();
      state.primary_object = mergeArtifact(state.primary_object, patch || {});
      var primary = state.primary_object;
      state.graph_ref = sanitizeGraph({ graph_id: primary.graph_id, graph_title: primary.graph_title, graph_revision: primary.graph_revision }) || state.graph_ref;
      state.node_ref = sanitizeNode({ node_id: primary.node_id, node_title: primary.node_title }) || state.node_ref;
      state.plan_ref = sanitizePlan({ resource_plan_id: primary.plan_id, plan_title: primary.plan_title, status: primary.status, resource_type: primary.kind }) || state.plan_ref;
      persist();
      return getState();
    }
    function setGraphContext(graph, node, plan) {
      state.graph_ref = sanitizeGraph(graph) || state.graph_ref;
      state.node_ref = sanitizeNode(node) || state.node_ref;
      state.plan_ref = sanitizePlan(plan) || state.plan_ref;
      persist();
    }
    function switchPrimary(objectKey) {
      var target = (state.reference_objects || []).find(function (item) { return item.object_key === objectKey; });
      if (!target) return getState();
      var previous = state.primary_object;
      state.primary_object = target;
      state.reference_objects = (state.reference_objects || []).filter(function (item) { return item.object_key !== objectKey; });
      if (previous) state.reference_objects.unshift(previous);
      state.reference_objects = state.reference_objects.slice(0, MAX_REFERENCES);
      state.graph_ref = sanitizeGraph({ graph_id: target.graph_id, graph_title: target.graph_title, graph_revision: target.graph_revision });
      state.node_ref = sanitizeNode({ node_id: target.node_id, node_title: target.node_title });
      state.plan_ref = sanitizePlan({ resource_plan_id: target.plan_id, plan_title: target.plan_title, status: target.status, resource_type: target.kind });
      state.selection_ref = null;
      state.reading_position_ref = null;
      state.reason = '你刚才把这份资源设为主要讨论对象';
      state.source = 'manual_primary_switch';
      persist();
      if (typeof options.onPrimarySwitch === 'function') options.onPrimarySwitch(target);
      return getState();
    }
    function removeObject(objectKey) {
      if (state.primary_object && state.primary_object.object_key === objectKey) {
        state.primary_object = null;
        state.reference_objects = state.reference_objects || [];
        state.selection_ref = null;
        state.reading_position_ref = null;
        state.reason = '你刚才移除了主要引用对象';
        state.source = 'manual_remove';
        state.intent = 'general_chat';
        state.view_mode = 'conversation';
      } else {
        state.reference_objects = (state.reference_objects || []).filter(function (item) { return item.object_key !== objectKey; });
      }
      persist();
      if (typeof options.onObjectRemoved === 'function') options.onObjectRemoved(objectKey, getState());
      return getState();
    }
    function setSelection(selection) {
      var normalized = sanitizeSelection(Object.assign({}, selection || {}, {
        artifact_id: selection && selection.artifact_id || state.primary_object && state.primary_object.artifact_id,
        version: selection && (selection.version_number || selection.version) || state.primary_object && state.primary_object.version,
        version_id: selection && (selection.version_id || selection.artifact_version_id) || state.primary_object && state.primary_object.version_id
      }));
      state.selection_ref = normalized;
      if (normalized) {
        state.intent = 'explain_selection';
        state.source = 'user_text_selection';
        state.reason = '你刚才主动选中了资源中的一段内容';
      }
      persist();
      return getState();
    }
    function clearSelection() { state.selection_ref = null; state.intent = state.primary_object ? 'discuss_artifact' : 'general_chat'; persist(); }
    function setReadingPosition(value) {
      var normalized = sanitizeReadingPosition(Object.assign({}, value || {}, {
        artifact_id: value && value.artifact_id || state.primary_object && state.primary_object.artifact_id,
        version: value && (value.version_number || value.version) || state.primary_object && state.primary_object.version,
        version_id: value && (value.version_id || value.artifact_version_id) || state.primary_object && state.primary_object.version_id
      }));
      if (!normalized) return;
      state.reading_position_ref = normalized;
      state.updated_at = nowIso();
      storageWrite(session, sessionKey(threadId), state);
    }
    function setQuietMode(value) {
      state.quiet_mode = Boolean(value);
      persist();
      if (typeof options.onQuietModeChange === 'function') options.onQuietModeChange(state.quiet_mode);
    }
    function setViewMode(value) {
      state.view_mode = value === 'resource' && state.primary_object ? 'resource' : 'conversation';
      persist();
      if (typeof options.onViewModeChange === 'function') options.onViewModeChange(state.view_mode);
    }
    function setTask(next) { taskState = next || null; render(); }

    function labelKind(kind) {
      return ({ lecture:'讲义', question:'题目', exam:'题目', exam_result:'练习结果', code:'代码实验', mindmap:'思维导图', presentation:'演示方案', interactive_lesson:'互动课堂' })[kind] || '学习资源';
    }
    function createObjectChip(item, primary) {
      var chip = doc.createElement('span');
      chip.className = 'context-object-chip' + (primary ? ' is-primary' : '');
      chip.dataset.objectKey = item.object_key;
      var button = doc.createElement('button');
      button.type = 'button';
      button.className = 'context-object-chip-main';
      button.textContent = (primary ? '主要 · ' : '参考 · ') + item.title + ' · v' + item.version;
      button.title = primary ? '当前默认指代对象' : '设为主要对象';
      if (!primary) button.onclick = function () { switchPrimary(item.object_key); };
      var remove = doc.createElement('button');
      remove.type = 'button'; remove.className = 'context-object-chip-remove'; remove.textContent = '×';
      remove.setAttribute('aria-label', '移除 ' + item.title);
      remove.onclick = function () { removeObject(item.object_key); };
      chip.append(button, remove);
      return chip;
    }
    function renderContextSummary() {
      var title = doc.getElementById('currentContextTitle');
      var subtitle = doc.getElementById('currentContextSubtitle');
      var root = doc.getElementById('currentContextBar');
      if (!root || !title || !subtitle) return;
      var primary = state.primary_object;
      root.dataset.state = primary ? 'bound' : 'empty';
      if (!primary) {
        title.textContent = '当前没有引用资源';
        subtitle.textContent = state.node_ref ? ('仍在学习节点：' + state.node_ref.title) : '可以继续普通对话，或主动选择一份资源';
      } else {
        title.textContent = '当前对象：' + labelKind(primary.kind) + ' · ' + primary.title + ' · v' + primary.version;
        subtitle.textContent = state.node_ref ? ('关联节点：' + state.node_ref.title) : (primary.topic ? '学习主题：' + primary.topic : '已绑定到当前对话');
      }
    }
    function renderObjects() {
      var root = doc.getElementById('contextObjectList');
      if (!root) return;
      root.innerHTML = '';
      if (state.primary_object) root.appendChild(createObjectChip(state.primary_object, true));
      (state.reference_objects || []).forEach(function (item) { root.appendChild(createObjectChip(item, false)); });
      root.hidden = !root.childNodes.length;
    }
    function renderDetails() {
      var root = doc.getElementById('contextDetails');
      if (!root) return;
      var primary = state.primary_object;
      var rows = [];
      rows.push(['为什么被引用', state.reason || '由你当前的操作决定']);
      rows.push(['引用来源', state.source === 'user_text_selection' ? '你在资源中主动选择的内容' : (state.source === 'manual_primary_switch' ? '你手动切换了主要对象' : (primary ? '你主动打开或追问了这份资源' : '当前没有引用对象'))]);
      rows.push(['本次用途', state.selection_ref ? state.selection_ref.purpose : (state.intent === 'edit_artifact' ? '修改当前资源' : (primary ? '解释或继续处理当前资源' : '普通对话'))]);
      rows.push(['关联学习对象', [state.graph_ref && state.graph_ref.title, state.node_ref && state.node_ref.title, state.plan_ref && state.plan_ref.title].filter(Boolean).join(' · ') || '尚未关联学习图谱']);
      rows.push(['AI将使用', primary ? ('当前资源 v' + primary.version + (state.selection_ref ? '与选中摘要' : '的公开内容')) : '本次对话和你明确提供的资料']);
      rows.push(['AI不会使用', '未打开资源、其他会话、私有Digest与未授权个人数据']);
      root.innerHTML = '';
      rows.forEach(function (row) {
        var item = doc.createElement('div'); item.className = 'context-detail-row';
        var label = doc.createElement('span'); label.textContent = row[0];
        var value = doc.createElement('strong'); value.textContent = row[1];
        item.append(label, value); root.appendChild(item);
      });
      var selection = doc.getElementById('contextSelectionSummary');
      if (selection) {
        selection.hidden = !state.selection_ref;
        selection.textContent = state.selection_ref
          ? ('选中：' + state.selection_ref.range_label + ' · ' + state.selection_ref.summary + (state.selection_ref.recoverable_summary_only ? '（刷新后仅恢复摘要）' : ''))
          : '';
      }
    }
    function renderActions() {
      var root = doc.getElementById('smartActionStrip');
      if (!root) return;
      var actions = deriveActions(state, taskState);
      root.innerHTML = '';
      root.hidden = state.quiet_mode && !(taskState && taskState.status === 'failed');
      actions.slice(0, 3).forEach(function (item) {
        var button = doc.createElement('button');
        button.type = 'button'; button.className = 'smart-action'; button.textContent = item.label;
        button.dataset.action = item.id; button.dataset.actionSource = item.source;
        button.disabled = item.enabled === false;
        button.onclick = function () {
          if (typeof options.onAction === 'function') options.onAction(item, getState());
        };
        root.appendChild(button);
      });
    }
    function renderQuiet() {
      var button = doc.getElementById('quietModeToggle');
      var notice = doc.getElementById('quietModeNotice');
      var app = doc.getElementById('workspaceApp') || doc.getElementById('app');
      if (button) {
        button.setAttribute('aria-pressed', state.quiet_mode ? 'true' : 'false');
        button.textContent = state.quiet_mode ? '安静模式已开启' : '开启安静模式';
        button.title = state.quiet_mode ? 'AI导师仍可正常回答，只关闭主动打扰' : '关闭主动建议、语音和关系提示';
      }
      if (notice) {
        notice.hidden = !state.quiet_mode;
        notice.textContent = '安静模式不会关闭AI导师，只会关闭主动打扰。';
      }
      if (app) app.classList.toggle('quiet-mode', state.quiet_mode);
    }
    function renderView() {
      var app = doc.getElementById('workspaceApp') || doc.getElementById('app');
      var drawer = doc.getElementById('artifactDrawer');
      var conversationTab = doc.getElementById('conversationViewTab');
      var resourceTab = doc.getElementById('resourceViewTab');
      var hasResource = Boolean(state.primary_object);
      if (app) {
        app.classList.toggle('resource-open', hasResource);
        app.classList.toggle('show-resource-view', hasResource && state.view_mode === 'resource');
      }
      if (drawer) {
        drawer.classList.toggle('is-docked', hasResource);
        if (!hasResource) drawer.hidden = true;
      }
      if (conversationTab) {
        conversationTab.classList.toggle('is-active', state.view_mode !== 'resource');
        conversationTab.setAttribute('aria-selected', state.view_mode !== 'resource' ? 'true' : 'false');
      }
      if (resourceTab) {
        resourceTab.disabled = !hasResource;
        resourceTab.classList.toggle('is-active', hasResource && state.view_mode === 'resource');
        resourceTab.setAttribute('aria-selected', hasResource && state.view_mode === 'resource' ? 'true' : 'false');
      }
    }
    function renderRestore() {
      var badge = doc.getElementById('contextRestoreBadge');
      if (!badge) return;
      badge.hidden = !state.primary_object;
      badge.textContent = state.primary_object ? '已恢复当前对象' : '';
    }
    function render() {
      if (destroyed || !doc) return;
      renderContextSummary();
      renderObjects();
      renderDetails();
      renderActions();
      renderQuiet();
      renderView();
      renderRestore();
    }

    function destroy() {
      destroyed = true;
      if (selectionTimer) clearTimeout(selectionTimer);
    }

    if (doc) {
      var detailsToggle = doc.getElementById('contextDetailsToggle');
      if (detailsToggle) detailsToggle.onclick = function () {
        var details = doc.getElementById('contextDetailsPanel');
        if (!details) return;
        details.hidden = !details.hidden;
        detailsToggle.setAttribute('aria-expanded', details.hidden ? 'false' : 'true');
        detailsToggle.textContent = details.hidden ? '查看引用原因' : '收起引用说明';
      };
      var clearSelectionButton = doc.getElementById('clearContextSelection');
      if (clearSelectionButton) clearSelectionButton.onclick = clearSelection;
      var quietButton = doc.getElementById('quietModeToggle');
      if (quietButton) quietButton.onclick = function () { setQuietMode(!state.quiet_mode); };
      var conversationTab = doc.getElementById('conversationViewTab');
      if (conversationTab) conversationTab.onclick = function () { setViewMode('conversation'); };
      var resourceTab = doc.getElementById('resourceViewTab');
      if (resourceTab) resourceTab.onclick = function () { setViewMode('resource'); };
      var openFull = doc.getElementById('openArtifactFullPage');
      if (openFull) openFull.onclick = function () {
        if (state.primary_object && typeof options.onOpenFullArtifact === 'function') options.onOpenFullArtifact(state.primary_object);
      };
    }

    return {
      load: load,
      reset: reset,
      getState: getState,
      bindArtifact: bindArtifact,
      enrichPrimary: enrichPrimary,
      setGraphContext: setGraphContext,
      switchPrimary: switchPrimary,
      removeObject: removeObject,
      setSelection: setSelection,
      clearSelection: clearSelection,
      setReadingPosition: setReadingPosition,
      setQuietMode: setQuietMode,
      setViewMode: setViewMode,
      setTask: setTask,
      deriveActions: function () { return deriveActions(state, taskState); },
      render: render,
      destroy: destroy
    };
  }

  return {
    STATE_VERSION: STATE_VERSION,
    createController: createController,
    emptyState: emptyState,
    normalizeState: normalizeState,
    sanitizeArtifact: sanitizeArtifact,
    deriveActions: deriveActions
  };
}));
