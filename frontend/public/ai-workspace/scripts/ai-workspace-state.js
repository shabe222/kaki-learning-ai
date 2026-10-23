(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.AIWorkspaceState = api;
}(typeof window !== 'undefined' ? window : null, function () {
  'use strict';

  function createRuntimeStatus() {
    return {
      backend: 'Checking', auth: 'Unknown', stream: 'Idle', provider: 'Unknown',
      operation: 'idle', state: 'idle'
    };
  }

  function providerLabel(value) {
    var normalized = String(value || '').toLowerCase();
    if (normalized === 'deepseek') return 'DeepSeek';
    if (normalized === 'qwen') return 'Qwen';
    if (normalized === 'spark') return 'Spark';
    if (normalized === 'mock') return 'Mock';
    return value && value !== 'unknown' ? String(value) : 'Unknown';
  }

  function applyRuntimeSnapshot(current, snapshot) {
    var next = Object.assign({}, current);
    snapshot = snapshot || {};
    if (snapshot.backend) next.backend = snapshot.backend === 'connected' ? '已连接' : '错误';
    if (snapshot.auth) next.auth = snapshot.auth === 'valid' ? '已认证' : (snapshot.auth === 'expired' ? '未登录' : '未知');
    if (snapshot.provider) next.provider = providerLabel(snapshot.provider);
    if (snapshot.operation) next.operation = snapshot.operation;
    if (snapshot.state) next.state = snapshot.state;
    if (snapshot.operation === 'chat_connecting') next.stream = 'Connecting';
    else if (snapshot.operation === 'chat_streaming') next.stream = 'Receiving';
    else if (snapshot.state === 'failed') next.stream = 'Failed';
    else next.stream = 'Idle';
    return next;
  }

  function runtimeOperationLabel(operation) {
    return ({
      idle: '空闲',
      chat_connecting: 'DeepSeek 普通对话连接中',
      chat_streaming: 'DeepSeek 流式回复中',
      semantic_routing: 'DeepSeek 正在理解任务',
      learning_graph_running: '正在编排正式学习图谱',
      exam_running: '正在调用试题智能体',
      lecture_running: '正在调用讲义智能体',
      presentation_running: '正在生成 PPT',
      interactive_lesson_running: '正在生成互动课堂',
      lecture_revising: '正在修改当前讲义',
      code_running: '正在调用代码智能体',
      code_revising: '正在修改当前代码',
      code_rerunning: '正在重新运行代码',
      export_pdf: '正在构建 PDF',
      export_docx: '正在构建 DOCX',
      completed: '已完成',
      failed: '真实失败'
    })[operation] || '空闲';
  }

  function transitionRuntimeStatus(current, event) {
    var next = Object.assign({}, current);
    if (event === 'connecting') {
      next.stream = 'Connecting'; next.operation = 'chat_connecting'; next.state = 'running';
    }
    if (event === 'content') {
      next.stream = 'Receiving';
      next.operation = 'chat_streaming';
      next.state = 'running';
    }
    if (event === 'completed') {
      next.stream = 'Idle'; next.operation = 'completed'; next.state = 'completed';
    }
    if (event === 'idle') {
      next.stream = 'Idle'; next.operation = 'idle'; next.state = 'idle';
    }
    if (event === 'provider_failure') {
      next.stream = 'Failed';
      next.operation = 'failed';
      next.state = 'failed';
    }
    if (event === 'backend_failure') {
      next.backend = '错误';
      next.stream = 'Failed';
      next.operation = 'failed';
      next.state = 'failed';
    }
    if (event === 'auth_expired') next.auth = '未登录';
    return next;
  }

  function groupMemories(items) {
    return (items || []).reduce(function (groups, item) {
      (item.memory_type === 'learning_goal' ? groups.goals : groups.preferences).push(item);
      return groups;
    }, { preferences: [], goals: [] });
  }

  return {
    createRuntimeStatus: createRuntimeStatus,
    transitionRuntimeStatus: transitionRuntimeStatus,
    applyRuntimeSnapshot: applyRuntimeSnapshot,
    runtimeOperationLabel: runtimeOperationLabel,
    groupMemories: groupMemories,
  };
}));
