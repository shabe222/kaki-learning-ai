(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.AIWorkspaceMemoryEditor = api;
}(typeof window !== 'undefined' ? window : null, function () {
  'use strict';

  var DEFINITIONS = {
    response_style: {
      label: '回答风格',
      options: [['concise', '简短'], ['normal', '正常'], ['detailed', '详细']]
    },
    content_format: {
      label: '内容形式',
      options: [['text', '文字'], ['visual', '图示'], ['video', '视频'], ['code', '代码'], ['practice_first', '先练习'], ['mixed', '混合']]
    },
    explanation_language: {
      label: '讲解语言',
      options: [['zh-CN', '中文'], ['en', '英文'], ['bilingual', '中英双语']]
    },
    teaching_method: {
      label: '教学方式',
      options: [['direct', '直接回答'], ['socratic', '苏格拉底式提问'], ['feynman', '费曼学习法'], ['example_first', '先举例'], ['step_by_step', '分步骤']]
    },
    difficulty_preference: {
      label: '难度偏好',
      options: [['basic', '基础'], ['intermediate', '中级'], ['advanced', '高级'], ['adaptive', '自适应']]
    },
    learning_goal: { label: '学习目标', options: null }
  };

  function definitions() { return DEFINITIONS; }
  function bounded(value, name, required) {
    var text = String(value == null ? '' : value).trim();
    if (required && !text) throw new Error(name + ' is required');
    if (text.length > 120) throw new Error(name + ' is too long');
    return text || null;
  }
  function buildValue(key, fields) {
    var definition = DEFINITIONS[key];
    if (!definition) throw new Error('memory key is not allowed');
    fields = fields || {};
    if (definition.options) {
      var allowed = definition.options.map(function (item) { return item[0]; });
      if (allowed.indexOf(fields.value) < 0) throw new Error('memory value is not allowed');
      return fields.value;
    }
    return {
      subject: bounded(fields.subject, 'subject', true),
      topic: bounded(fields.topic, 'topic', true),
      target: bounded(fields.target, 'target', true),
      deadline: bounded(fields.deadline, 'deadline', false)
    };
  }
  function buildPayload(key, fields, threadId, sourceMessageId) {
    if (!threadId) throw new Error('thread_id is required');
    return {
      value: buildValue(key, fields),
      evidence_text: '用户在记忆面板手动设置',
      source_thread_id: threadId,
      source_message_id: sourceMessageId || ('manual_' + Date.now()),
      confirmed: true
    };
  }

  return { definitions: definitions, buildValue: buildValue, buildPayload: buildPayload };
}));
