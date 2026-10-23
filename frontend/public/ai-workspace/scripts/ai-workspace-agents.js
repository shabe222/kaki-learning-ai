(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.AIWorkspaceAgents = api;
}(typeof window !== 'undefined' ? window : globalThis, function () {
  'use strict';

  function topicOf(text, kind) {
    var pattern = kind === 'exam'
      ? /(?:调用(?:试题)?智能体(?:为我)?)?(?:给我|帮我)?(?:出|生成|做)(?:\s*(?:\d{1,2}|[一二两三四五六七八九十]+)\s*(?:道|个))?\s*(.+?)(?:单项选择题|单选题|选择题|判断题|填空题|简答题|练习题|题目|试题|考题|考试|试卷)/
      : /(?:帮我|给我)?(?:生成|做|写)(?:一份)?\s*(.+?)(?:讲义|学习资料)/;
    var match = String(text || '').match(pattern);
    return match && match[1] ? match[1].trim().replace(/^的|的$/g, '') : '当前主题';
  }

  function chineseNumber(value) {
    if (/^\d+$/.test(value)) return Number(value);
    var digits = { '一':1, '二':2, '两':2, '三':3, '四':4, '五':5, '六':6, '七':7, '八':8, '九':9 };
    if (value === '十') return 10;
    if (value.indexOf('十') >= 0) {
      var parts = value.split('十');
      return (digits[parts[0]] || 1) * 10 + (digits[parts[1]] || 0);
    }
    return digits[value] || 5;
  }

  function questionCount(text, fallback) {
    var match = String(text || '').match(/(\d{1,2}|[一二两三四五六七八九十]+)\s*(?:道|个)/);
    return match ? Math.min(20, Math.max(1, chineseNumber(match[1]))) : (fallback == null ? 5 : fallback);
  }

  function questionTypes(text) {
    var result = [];
    if (/单项选择题|单选题|选择题/.test(text)) result.push('single_choice');
    if (/判断题/.test(text)) result.push('true_false');
    if (/填空题/.test(text)) result.push('fill_blank');
    if (/简答题/.test(text)) result.push('short_answer');
    return result;
  }

  function isExplicitExamCommand(text) {
    return /(?:^|给我|帮我|请你?|想要|想让你|调用(?:试题)?智能体(?:为我)?|重新|再)\s*(?:出(?!的|过|来)|生成|做(?!的|过)).*(?:单项选择题|单选题|选择题|判断题|填空题|简答题|练习题|题目|试题|考题|考试|试卷)/.test(text);
  }

  function detectTaskIntent(message) {
    var text = String(message || '').trim();
    if (isExplicitExamCommand(text)) {
      return {
        kind: 'exam', topic: topicOf(text, 'exam'), count: questionCount(text),
        question_types: questionTypes(text), difficulty: '中等'
      };
    }
    if (/(?:生成|做|写).*(?:讲义|学习资料)/.test(text)) return { kind: 'lecture', topic: topicOf(text, 'lecture') };
    return null;
  }

  function answerForExam(exam, index) {
    var answers = exam && Array.isArray(exam.answers) ? exam.answers : [];
    var value = answers[index];
    if (value && typeof value === 'object') value = value.answer != null ? value.answer : (value.value != null ? value.value : value.response);
    return value == null ? '' : String(value);
  }

  function detectArtifactCommand(message, activeArtifact) {
    var text = String(message || '').trim();
    if (!activeArtifact || !activeArtifact.artifact_id) return null;
    if (activeArtifact.kind === 'exam' && /再来|再出|换一|换个/.test(text)) {
      return {
        kind: 'repeat_exam', topic: activeArtifact.topic, count: questionCount(text, activeArtifact.question_count || 1),
        question_types: questionTypes(text).length ? questionTypes(text) : (activeArtifact.question_types || []),
        difficulty: /难一点|更难|提高难度/.test(text) ? '困难'
          : (/简单一点|容易一点|降低难度/.test(text) ? '简单' : (activeArtifact.difficulty || '中等'))
      };
    }
    if (activeArtifact.kind !== 'lecture') return null;
    if (/(?:基于|根据|用).*(?:这份|这个|刚才).*(?:讲义|内容).*(?:出|生成|做).*题|(?:基于|根据)这份讲义给我出/.test(text)) {
      var count = text.match(/(\d{1,2})\s*道/);
      return { kind: 'exam_from_lecture', artifact_id: activeArtifact.artifact_id, count: count ? Number(count[1]) : 5 };
    }
    if (/(?:把|将).*(?:讲义|内容).*(?:修改|改|重写|更新|替换|删除|增加|添加|补充|调整)/.test(text)
        || /(?:修改|编辑|改写|重写|更新)(?:这份|这个|刚才的)?讲义/.test(text)) {
      return { kind: 'revise_lecture', artifact_id: activeArtifact.artifact_id, instruction: text };
    }
    return null;
  }

  function createTaskState() { return { status: 'idle', task: null, controlsEnabled: true, error: '' }; }
  function transitionTask(state, event, payload) {
    var next = Object.assign({}, state || createTaskState());
    if (event === 'planned') return Object.assign(next, { status: payload && payload.status || 'awaiting_confirmation', task: payload, controlsEnabled: true, error: '' });
    if (event === 'confirmed') return Object.assign(next, { status: 'running', controlsEnabled: false, error: '' });
    if (event === 'completed') return Object.assign(next, { status: 'completed', controlsEnabled: true, error: '' });
    if (event === 'failed') return Object.assign(next, { status: 'failed', controlsEnabled: true, error: (payload && payload.message) || '智能体执行失败' });
    if (event === 'suspended') return Object.assign(next, { status: 'suspended', controlsEnabled: true, error: '' });
    if (event === 'cancelled') return createTaskState();
    return next;
  }
  function isConfirmation(message) {
    return /^(?:是|好|好的|可以|确认(?:[，,\s]*(?:开始(?:生成|修改|执行)?|现在开始))?|开始(?:生成|修改|执行)?|生成|现在开始|没问题)[。！!\s]*$/.test(String(message || '').trim());
  }

  function isTutorContinuation(message) {
    var text = String(message || '').trim();
    if (/^(?:好|好的|可以|明白|知道了|开始|现在开始|继续|继续学习|接着来|下一步|没问题)[。！!，,\s]*$/.test(text)) return true;
    return /(?:你来安排(?:一下)?(?:下一步)?|按(?:你|导师)(?:的)?安排(?:继续|来|进行)?|接下来(?:由)?你(?:来)?决定|继续按(?:你|导师)?(?:的)?计划|照(?:你|导师)(?:的)?计划继续|帮我安排下一步)/.test(text);
  }

  return {
    detectTaskIntent: detectTaskIntent,
    detectArtifactCommand: detectArtifactCommand,
    createTaskState: createTaskState,
    transitionTask: transitionTask,
    isConfirmation: isConfirmation,
    isTutorContinuation: isTutorContinuation,
    answerForExam: answerForExam
  };
}));
