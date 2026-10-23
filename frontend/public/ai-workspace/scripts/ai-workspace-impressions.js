(function (root, factory) {
  var api = factory(root);
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.AIWorkspaceImpressions = api;
}(typeof window !== 'undefined' ? window : null, function (root) {
  'use strict';

  function confidenceLabel(value) {
    var number = Number(value) || 0;
    if (number >= 0.8) return '高';
    if (number >= 0.4) return '中';
    return '低';
  }

  function safeEvidenceRows(item) {
    return (item && Array.isArray(item.evidence_refs) ? item.evidence_refs : []).map(function (ref) {
      return {
        summary: String(ref && ref.summary || '近期行为支持这条印象'),
        polarity: ref && ref.polarity === 'opposing' ? 'opposing' : 'supporting'
      };
    });
  }

  function viewModel(item) {
    var labels = {
      candidate: '最近学到',
      active: 'AI 推断',
      uncertain: '存在不同证据',
      rejected: '已标记为不准确',
      confirmed_by_user: '你已确认'
    };
    return {
      id: item.id,
      summary: String(item.summary || ''),
      scope: String(item.scope || '当前学习场景'),
      confidenceLabel: confidenceLabel(item.confidence),
      evidenceCount: Number(item.evidence_count) || 0,
      status: item.status || 'active',
      statusLabel: labels[item.status] || 'AI 推断',
      isActive: item.status !== 'rejected',
      updatedAt: String(item.last_observed_at || item.updated_at || '').replace('T', ' ').slice(0, 16),
      evidence: safeEvidenceRows(item)
    };
  }

  function element(name, className, text) {
    var node = document.createElement(name);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function actionButton(label, handler) {
    var button = element('button', 'impression-action', label);
    button.type = 'button';
    button.addEventListener('click', handler);
    return button;
  }

  function init(options) {
    if (!root || typeof document === 'undefined') return null;
    options = options || {};
    var client = options.client || root.AIWorkspaceApiClient;
    var container = document.getElementById(options.containerId || 'aiImpressions');
    var empty = document.getElementById('impressionEmpty');
    var count = document.getElementById('impressionCount');
    var status = document.getElementById('impressionStatus');
    if (!client || !container) return null;

    function setStatus(message, isError) {
      if (!status) return;
      status.textContent = message || '';
      status.classList.toggle('err', Boolean(isError));
    }

    async function mutate(id, action, summary) {
      setStatus('正在保存…');
      try {
        await client.updateImpression(id, action, summary);
        await load();
        if (root && typeof root.dispatchEvent === 'function' && typeof root.CustomEvent === 'function') {
          root.dispatchEvent(new root.CustomEvent('kaki:impression-changed', { detail:{ id:id, action:action } }));
        }
        setStatus('已保存，AI 导师会立即按最新偏好调整呈现');
      } catch (error) {
        setStatus('保存失败：' + (error.message || '请重试'), true);
      }
    }

    function render(items) {
      container.innerHTML = '';
      if (empty) empty.hidden = items.length > 0;
      if (count) count.textContent = '共 ' + items.length + ' 条';
      items.forEach(function (item) {
        var view = viewModel(item);
        var row = element('article', 'impression-row' + (view.isActive ? '' : ' is-rejected'));
        var scrollBody = element('div', 'impression-card-scroll');
        var summary = element('strong', 'impression-summary', view.summary);
        var meta = element('small', 'impression-meta',
          '范围：' + view.scope + ' · 置信度：' + view.confidenceLabel
          + ' · 依据：' + view.evidenceCount + ' 条 · 更新：' + (view.updatedAt || '刚刚'));
        var badge = element('span', 'impression-badge', view.statusLabel);
        var actions = element('div', 'impression-actions');
        var evidence = element('div', 'impression-evidence');
        evidence.hidden = true;
        if (view.evidence.length) {
          var list = element('ul');
          view.evidence.forEach(function (ref) {
            list.appendChild(element('li', ref.polarity === 'opposing' ? 'opposing' : '',
              (ref.polarity === 'opposing' ? '相反证据：' : '') + ref.summary));
          });
          evidence.appendChild(list);
        } else {
          evidence.appendChild(element('p', '', '暂无可展示的依据摘要。'));
        }
        actions.appendChild(actionButton('查看依据', function () {
          evidence.hidden = !evidence.hidden;
        }));
        if (view.status !== 'confirmed_by_user') {
          actions.appendChild(actionButton('准确', function () { mutate(view.id, 'accurate'); }));
        }
        if (view.status !== 'rejected') {
          actions.appendChild(actionButton('不准确', function () { mutate(view.id, 'inaccurate'); }));
        }
        actions.appendChild(actionButton('修改', function () {
          var next = root.prompt('修改柿小吉对你的印象', view.summary);
          if (next && next.trim() && next.trim() !== view.summary) mutate(view.id, 'modify', next.trim());
        }));
        actions.appendChild(actionButton('删除', async function () {
          if (!root.confirm('删除这条印象吗？')) return;
          setStatus('正在删除…');
          try {
            await client.deleteImpression(view.id);
            await load();
            if (root && typeof root.dispatchEvent === 'function' && typeof root.CustomEvent === 'function') {
              root.dispatchEvent(new root.CustomEvent('kaki:impression-changed', { detail:{ id:view.id, action:'delete' } }));
            }
            setStatus('已删除，AI 导师不会再采用这条印象');
          } catch (error) {
            setStatus('删除失败：' + (error.message || '请重试'), true);
          }
        }));
        scrollBody.appendChild(summary);
        scrollBody.appendChild(meta);
        scrollBody.appendChild(evidence);
        row.appendChild(badge);
        row.appendChild(scrollBody);
        row.appendChild(actions);
        container.appendChild(row);
      });
    }

    async function load() {
      setStatus('正在更新印象…');
      try {
        var response = await client.getImpressions();
        render(response && Array.isArray(response.data) ? response.data : []);
        setStatus('');
      } catch (error) {
        setStatus('印象加载失败：' + (error.message || '请重试'), true);
      }
    }

    var opener = document.getElementById('openMemories');
    if (opener) opener.addEventListener('click', load);
    return { load: load, render: render };
  }

  if (root && typeof document !== 'undefined') {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', function () { init(); });
    } else {
      init();
    }
  }

  return {
    confidenceLabel: confidenceLabel,
    safeEvidenceRows: safeEvidenceRows,
    viewModel: viewModel,
    init: init
  };
}));
