(function () {
  'use strict';

  var api = window.apiClient;
  var controller = null;
  var exactSelectionText = '';
  var currentTaskState = null;
  var currentThreadId = '';
  var restoreTimer = null;
  var readingTimer = null;

  function $(id) { return document.getElementById(id); }
  function currentThread() { return sessionStorage.getItem('ai_workspace_test_thread_id') || ''; }
  function copy(value) { return value ? JSON.parse(JSON.stringify(value)) : value; }
  function artifactMetadata(artifact) {
    artifact = artifact || {};
    var raw = artifact.raw_data || artifact.metadata || {};
    return Object.assign({}, raw, artifact, {
      artifact_id: artifact.artifact_id || artifact.id,
      version: artifact.version_number || artifact.version || raw.version || 1,
      version_id: artifact.version_id || raw.version_id || null,
      graph_id: artifact.graph_id || raw.graph_id || null,
      graph_title: artifact.graph_title || raw.graph_title || null,
      graph_revision: artifact.graph_revision || raw.graph_revision || null,
      graph_node_id: artifact.graph_node_id || artifact.node_id || raw.graph_node_external_id || raw.node_id || null,
      graph_node_title: artifact.graph_node_title || artifact.node_title || raw.graph_node_title || raw.node_title || null,
      resource_plan_id: artifact.resource_plan_id || artifact.plan_id || raw.resource_plan_external_id || raw.resource_plan_id || null,
      resource_plan_title: artifact.resource_plan_title || artifact.plan_title || raw.resource_plan_title || raw.plan_title || null
    });
  }
  function state() { return controller ? controller.getState() : null; }
  function primary() { var value = state(); return value && value.primary_object; }
  function setInput(value, sendNow) {
    var input = $('input');
    if (!input) return;
    input.value = value || '';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.focus();
    if (sendNow && $('send') && !$('send').disabled) $('send').click();
  }
  function renderTaskStatus(task) {
    var root = $('taskContextStatus');
    if (!root) return;
    var status = task && (task.status || task.state || (task.task && task.task.state)) || 'idle';
    var taskItem = task && task.task || {};
    var text = '';
    if (status === 'answering') text = '对话回答中 · 可以停止生成，当前资源与输入内容会保留。';
    else if (status === 'running' || status === 'generating') text = '资源生成中 · ' + (taskItem.goal || taskItem.title || taskItem.kind || '正在处理当前任务') + '。你可以收起资源继续普通对话。';
    else if (status === 'failed') text = '任务失败 · 已有资源和旧版本仍然保留，可以重试或修改要求。';
    else if (status === 'awaiting_confirmation' || status === 'collecting_requirements' || status === 'needs_details') text = '任务尚未开始 · 请确认或补充要求，当前对象不会被覆盖。';
    root.hidden = !text;
    root.textContent = text;
  }
  function syncSelectionBar() {
    var bar = $('selectionCaptureBar');
    var summary = $('selectionCaptureSummary');
    if (!bar || !summary) return;
    var value = state();
    var selection = value && value.selection_ref;
    bar.hidden = !selection;
    if (selection) summary.textContent = selection.range_label + ' · ' + selection.summary;
  }
  function isExamObject(item) {
    var kind = String(item && (item.kind || item.type || item.artifact_kind) || '').replace(/^workspace_/, '');
    return kind === 'exam' || kind === 'exam_result';
  }
  function currentObjectForRequest(options) {
    options = options || {};
    if (options.input_context_mode === 'reply' || options.reply_to_message_id || options.target_artifact_id) return null;
    return primary();
  }
  function selectionForRequest(target) {
    var value = state();
    var selection = value && value.selection_ref;
    if (!target || !selection || !exactSelectionText) return null;
    if (Number(selection.artifact_id) !== Number(target.artifact_id) || Number(selection.version) !== Number(target.version)) return null;
    if (selection.version_id && target.version_id && Number(selection.version_id) !== Number(target.version_id)) return null;
    return {
      selected_text: exactSelectionText.slice(0, 1200),
      range_label: selection.range_label,
      purpose: selection.purpose || '解释选中内容',
      version_digest: selection.version_digest || target.content_digest || target.canonical && target.canonical.version && target.canonical.version.content_digest || null,
      block_id: selection.block_id || null,
      text_range: selection.text_range || null,
      timestamp: selection.timestamp || null,
      knowledge_refs: selection.knowledge_refs || [],
      source_refs: selection.source_refs || [],
      artifact_purpose: selection.artifact_purpose || target.canonical && target.canonical.purpose || null
    };
  }
  function openCurrentResource(item, restore) {
    if (!item) return;
    var kind = String(item.kind || item.type || '').replace('workspace_', '');
    if ((kind === 'exam' || kind === 'exam_result') && typeof window.KakiWorkspaceOpenArtifact === 'function') {
      window.KakiWorkspaceOpenArtifact(copy(item), { contextRestore: Boolean(restore) });
      return;
    }
    if (window.KakiArtifactShellHost && typeof window.KakiArtifactShellHost.open === 'function') {
      window.KakiArtifactShellHost.open(copy(item), { contextRestore: Boolean(restore) });
      return;
    }
    if (typeof window.KakiWorkspaceOpenArtifact === 'function') window.KakiWorkspaceOpenArtifact(copy(item), { contextRestore: Boolean(restore) });
  }
  function handleAction(item) {
    if (!item) return;
    if (item.id === 'retry_task') {
      var retry = Array.prototype.find.call(document.querySelectorAll('button'), function (button) { return button.textContent === '重试生成'; });
      if (retry) retry.click();
      else if ($('retry') && !$('retry').hidden) $('retry').click();
      return;
    }
    if (item.id === 'adjust_request') return setInput(item.prompt || '请帮我缩小范围后重新处理。', false);
    if (item.id === 'return_previous') return controller.setViewMode('resource');
    if (item.id === 'collapse_task') { controller.setViewMode('conversation'); return; }
    if (item.id === 'cancel_task') { if ($('stop') && !$('stop').disabled) $('stop').click(); return; }
    if (item.id === 'choose_resource') {
      if ($('artifactsPanel') && $('artifactsPanel').hidden && $('artifactsToggle')) $('artifactsToggle').click();
      return;
    }
    if (item.id === 'open_learning_node') {
      var value = state();
      var graphId = value && value.graph_ref && value.graph_ref.graph_id;
      window.location.assign(graphId ? '/learning?graph=' + encodeURIComponent(graphId) : '/learning');
      return;
    }
    if (item.id === 'run_code') {
      var run = Array.prototype.find.call(document.querySelectorAll('#artifactContent button'), function (button) { return button.textContent === '运行代码'; });
      if (run) run.click();
      else setInput('请运行当前代码，并区分运行状态与学习结果。', true);
      return;
    }
    if (item.id === 'modify_resource') return setInput('请修改当前资源：', false);
    if (item.prompt) return setInput(item.prompt, true);
  }
  function restoreResourceIfNeeded() {
    if (restoreTimer) window.clearTimeout(restoreTimer);
    restoreTimer = window.setTimeout(function () {
      var value = state();
      if (!value || !value.primary_object || value.view_mode !== 'resource') return;
      openCurrentResource(value.primary_object, true);
    }, 160);
  }
  function bindArtifact(artifact, meta) {
    if (!controller || !artifact) return null;
    var value = artifactMetadata(artifact);
    if (!value.artifact_id) return null;
    exactSelectionText = '';
    var result = controller.bindArtifact(value, meta || {});
    syncSelectionBar();
    return result;
  }
  function enrichArtifact(artifact) {
    if (!controller || !artifact) return null;
    return controller.enrichPrimary(artifactMetadata(artifact));
  }
  function loadThread(threadId) {
    if (!controller) return null;
    currentThreadId = String(threadId || '');
    exactSelectionText = '';
    var result = controller.load(currentThreadId);
    try {
      var pendingLearningContext = JSON.parse(sessionStorage.getItem('kaki_pending_learning_context_v1') || 'null');
      if (pendingLearningContext && pendingLearningContext.graph && pendingLearningContext.node) {
        controller.setGraphContext(pendingLearningContext.graph, pendingLearningContext.node, pendingLearningContext.plan || null);
        sessionStorage.removeItem('kaki_pending_learning_context_v1');
        result = controller.getState();
      }
    } catch (ignore) {
      sessionStorage.removeItem('kaki_pending_learning_context_v1');
    }
    syncSelectionBar();
    restoreResourceIfNeeded();
    return result;
  }
  function resetThread(threadId) {
    exactSelectionText = '';
    if (controller) controller.reset(threadId || currentThread());
    syncSelectionBar();
  }
  function setTask(task) {
    currentTaskState = task || null;
    if (controller) controller.setTask(currentTaskState);
    renderTaskStatus(currentTaskState);
  }
  function setConversationActive(active) {
    if (active && (!currentTaskState || ['idle','completed'].indexOf(currentTaskState.status) >= 0)) {
      renderTaskStatus({ status: 'answering' });
    } else renderTaskStatus(currentTaskState);
  }
  function clearPrimaryVisual() {
    if ($('artifactDrawer')) $('artifactDrawer').hidden = true;
    if (typeof window.KakiWorkspaceClearActiveArtifact === 'function') window.KakiWorkspaceClearActiveArtifact();
  }
  function captureSelection() {
    var root = $('artifactContent');
    var target = primary();
    if (!root || !target) return;
    var selection = window.getSelection && window.getSelection();
    if (!selection || selection.isCollapsed || !selection.rangeCount) return;
    var range = selection.getRangeAt(0);
    var node = range.commonAncestorContainer.nodeType === 1 ? range.commonAncestorContainer : range.commonAncestorContainer.parentElement;
    if (!node || !root.contains(node)) return;
    var selected = String(selection.toString() || '').replace(/\u0000/g, '').trim();
    if (!selected) return;
    exactSelectionText = selected.slice(0, 1200);
    var rangeLabel = '选中内容';
    var canonicalBlock = node.closest && node.closest('[data-canonical-block-id]');
    var pre = node.closest && node.closest('pre');
    if (pre) {
      try {
        var prefix = document.createRange();
        prefix.selectNodeContents(pre); prefix.setEnd(range.startContainer, range.startOffset);
        var startLine = prefix.toString().split('\n').length;
        var lineCount = selected.split('\n').length;
        rangeLabel = '代码第 ' + startLine + (lineCount > 1 ? '—' + (startLine + lineCount - 1) : '') + ' 行';
      } catch (ignore) { rangeLabel = '代码选中片段'; }
    } else {
      var section = node.closest && node.closest('section');
      var heading = section && section.querySelector('h1,h2,h3,strong');
      if (heading && heading.textContent) rangeLabel = String(heading.textContent).trim().slice(0, 60);
    }
    controller.setSelection({
      artifact_id: target.artifact_id,
      version: target.version,
      version_id: target.version_id,
      summary: selected.replace(/\s+/g, ' ').slice(0, 150),
      range_label: rangeLabel,
      source_label: '资源内主动选择',
      purpose: '解释选中内容',
      version_digest: target.content_digest || target.canonical && target.canonical.version && target.canonical.version.content_digest || null,
      block_id: canonicalBlock && canonicalBlock.getAttribute('data-canonical-block-id') || null,
      knowledge_refs: [],
      source_refs: [],
      artifact_purpose: target.canonical && target.canonical.purpose || null,
      recoverable_summary_only: true
    });
    syncSelectionBar();
  }
  function saveReadingPosition() {
    if (!controller || !primary()) return;
    var panel = $('artifactDrawer') && $('artifactDrawer').querySelector('.drawer-panel');
    var root = $('artifactContent');
    var scrollTop = Math.max(panel && panel.scrollTop || 0, root && root.scrollTop || 0);
    controller.setReadingPosition({ artifact_id: primary().artifact_id, version: primary().version, version_id: primary().version_id, scroll_top: scrollTop });
  }
  function scheduleReadingPosition() {
    if (readingTimer) window.clearTimeout(readingTimer);
    readingTimer = window.setTimeout(saveReadingPosition, 180);
  }
  function restoreReadingPosition() {
    var value = state();
    var position = value && value.reading_position_ref;
    if (!position || !primary() || Number(position.artifact_id) !== Number(primary().artifact_id)) return;
    window.setTimeout(function () {
      var panel = $('artifactDrawer') && $('artifactDrawer').querySelector('.drawer-panel');
      var root = $('artifactContent');
      if (panel) panel.scrollTop = position.scroll_top || 0;
      if (root) root.scrollTop = position.scroll_top || 0;
    }, 120);
  }

  if (!window.AIWorkspaceContext || !api) return;
  controller = window.AIWorkspaceContext.createController({
    onAction: handleAction,
    onPrimarySwitch: function (item) { exactSelectionText = ''; openCurrentResource(item, false); },
    onObjectRemoved: function (_, next) { exactSelectionText = ''; if (!next.primary_object) clearPrimaryVisual(); },
    onOpenFullArtifact: function (item) { window.location.assign('/artifacts/' + encodeURIComponent(item.artifact_id)); },
    onStateChange: syncSelectionBar,
    onViewModeChange: function (mode) { if (mode === 'resource') openCurrentResource(primary(), false); },
    onQuietModeChange: function (quiet) {
      if (quiet && window.speechSynthesis) window.speechSynthesis.cancel();
    }
  });

  var originalChatStream = api.chatStream && api.chatStream.bind(api);
  if (originalChatStream) {
    api.chatStream = function (message, history, callbacks, options) {
      options = Object.assign({}, options || {});
      var target = currentObjectForRequest(options);
      if (target && !isExamObject(target)) {
        options.target_artifact_id = Number(target.artifact_id);
        options.target_version_id = Number(target.version_id || target.version || 1);
      }
      if (isExamObject(target) && !options.question_context) {
        options.question_context = { exam_id: Number(target.artifact_id) };
      }
      var selected = selectionForRequest(target || (options.target_artifact_id ? {
        artifact_id: options.target_artifact_id, version: target && target.version || 1, version_id: options.target_version_id || null
      } : null));
      if (selected) options.selection_context = selected;
      return originalChatStream(message, history, callbacks, options);
    };
  }

  if ($('artifactContent')) {
    $('artifactContent').addEventListener('mouseup', captureSelection);
    $('artifactContent').addEventListener('keyup', captureSelection);
    $('artifactContent').addEventListener('scroll', scheduleReadingPosition, { passive: true });
  }
  var drawerPanel = $('artifactDrawer') && $('artifactDrawer').querySelector('.drawer-panel');
  if (drawerPanel) drawerPanel.addEventListener('scroll', scheduleReadingPosition, { passive: true });
  if ($('askSelection')) $('askSelection').addEventListener('click', function () {
    if (!exactSelectionText) {
      setInput('请先重新选中要解释的内容。', false);
      return;
    }
    // Asking from a selected artifact is a user-confirmed draft only; never auto-send.
    setInput('这部分为什么这样写？', false);
  });
  if ($('clearContextSelection')) $('clearContextSelection').addEventListener('click', function () { exactSelectionText = ''; syncSelectionBar(); });
  if ($('closeArtifact')) $('closeArtifact').addEventListener('click', function () { if (controller) controller.setViewMode('conversation'); });
  if ($('focusReadingToggle')) $('focusReadingToggle').addEventListener('click', function () {
    var app = $('workspaceApp') || $('app');
    var active = app && app.classList.toggle('focus-reading');
    $('focusReadingToggle').setAttribute('aria-pressed', active ? 'true' : 'false');
    $('focusReadingToggle').textContent = active ? '退出专注' : '专注阅读';
    if (active && controller) controller.setViewMode('resource');
  });
  window.addEventListener('pagehide', saveReadingPosition);

  window.KakiWorkspaceContextBridge = {
    load: loadThread,
    reset: resetThread,
    bindArtifact: bindArtifact,
    enrichArtifact: enrichArtifact,
    setTask: setTask,
    setGraphContext: function (graph, node, plan) { if (controller) controller.setGraphContext(graph, node, plan); return state(); },
    setConversationActive: setConversationActive,
    restoreReadingPosition: restoreReadingPosition,
    getState: state,
    getSelectionForRequest: function () { return selectionForRequest(primary()); },
    setSelectionFromShell: function (selection, exactText) {
      exactSelectionText = String(exactText || selection && selection.selected_text || '').slice(0, 1200);
      if (controller) controller.setSelection(Object.assign({}, selection || {}, {
        version: selection && (selection.artifact_version_number || selection.version),
        version_id: selection && (selection.artifact_version_id || selection.version_id),
        recoverable_summary_only: true
      }));
      syncSelectionBar();
    },
    clearSelection: function () { exactSelectionText = ''; if (controller) controller.clearSelection(); syncSelectionBar(); },
    setReadingPositionFromShell: function (position) { if (controller) controller.setReadingPosition(position || {}); },
    setViewMode: function (mode) { if (controller) controller.setViewMode(mode); },
    destroy: function () {
      if (restoreTimer) window.clearTimeout(restoreTimer);
      if (readingTimer) window.clearTimeout(readingTimer);
      if (controller) controller.destroy();
    }
  };
  loadThread(currentThread());
}());
