(function () {
  'use strict';

  var ASSET = '/ai-workspace/assets/adventure/';
  var API = localStorage.getItem('shiguang_demo_api') || 'http://127.0.0.1:8901';

  // ===== 角色 =====
  var ROLES = [
    { id: 'wandering_mage', seal: '符', name: '云游符咒师', desc: '惯于把知识讲给同伴听，从复述中照见缺口。', tag: '讲述型学者' },
    { id: 'lamp_heir', seal: '灯', name: '守灯人后裔', desc: '擅长把方法做成可操作的机关，靠结果校准理解。', tag: '动手派' },
    { id: 'star_apprentice', seal: '星', name: '星象台学徒', desc: '偏爱把概念放到陌生情境里检验迁移。', tag: '迁移派' }
  ];
  var COLOR_MAP = {
    fire: 'drop-shadow(0 18px 16px rgba(28,20,15,.3)) drop-shadow(0 0 18px rgba(232,130,74,.35))',
    water: 'drop-shadow(0 18px 16px rgba(28,20,15,.3)) drop-shadow(0 0 18px rgba(111,178,196,.35))',
    wind: 'drop-shadow(0 18px 16px rgba(28,20,15,.3)) drop-shadow(0 0 18px rgba(159,201,168,.35))',
    wood: 'drop-shadow(0 18px 16px rgba(28,20,15,.3)) drop-shadow(0 0 18px rgba(224,176,96,.35))'
  };
  var customSwatch = 'wood';
  var selectedSchool = 'wood';
  var pendingSchool = 'wood';

  // ===== 阶段信息（背景/立绘/进度） =====
  var STAGES = {
    dialogue: { label: '回声书阁 · 结伴同行', bg: 'scenes/echo_archive_base.webp', mascot: 'mascot_shixiaoji_welcome.png' },
    chest: { label: '拾光宝库 · 古树宝箱', bg: 'scenes/harvest_treasury_base.webp', mascot: 'mascot_shixiaoji_hint.png' },
    minion: { label: '雾谷 · 墨迹兽', bg: 'scenes/mist_valley_base.webp', mascot: 'mascot_shixiaoji_encourage.png' },
    boss: { label: '封印古塔 · 墨魇之主', bg: 'scenes/boss_core_base.webp', mascot: 'mascot_shixiaoji_encourage.png' },
    victory: { label: '丰穗谷 · 灯火归位', bg: 'scenes/harvest_treasury_base.webp', mascot: 'mascot_shixiaoji_celebrate.png' }
  };
  var STAGE_ORDER = ['dialogue', 'chest', 'minion', 'boss', 'victory'];

  // ===== DOM =====
  var roleSelect = document.getElementById('roleSelect');
  var roleCards = document.getElementById('roleCards');
  var game = document.getElementById('game');
  var scene = document.getElementById('scene');
  var charMascot = document.getElementById('charMascot');
  var charPlayer = document.getElementById('charPlayer');
  var charEnemy = document.getElementById('charEnemy');
  var charBoss = document.getElementById('charBoss');
  var clueFloat = document.getElementById('clueFloat');
  var progress = document.getElementById('stageProgress');
  var dialog = document.getElementById('dialog');
  var dBar = document.getElementById('dBar');
  var dName = document.getElementById('dName');
  var dRole = document.getElementById('dRole');
  var dTag = document.getElementById('dTag');
  var dText = document.getElementById('dText');
  var dOptions = document.getElementById('dOptions');
  var dInput = document.getElementById('dInput');
  var dAnswer = document.getElementById('dAnswer');
  var dSubmit = document.getElementById('dSubmit');
  var dFeedback = document.getElementById('dFeedback');
  var victory = document.getElementById('victory');
  var victoryTitle = document.getElementById('victoryTitle');
  var victoryText = document.getElementById('victoryText');
  var restartBtn = document.getElementById('restartBtn');

  var session = null;   // 后端会话快照
  var role = null;
  var busy = false;
  var typingTimer = null;

  // ===== 基础 =====
  function unwrap(r) { return r && Object.prototype.hasOwnProperty.call(r, 'data') ? r.data : r; }

  function request(path, body) {
    var opts = { method: 'POST', headers: { 'Content-Type': 'application/json; charset=utf-8' } };
    opts.body = JSON.stringify(body || {});
    return fetch(API + path, opts).then(function (res) {
      if (!res.ok) return res.json().then(function (data) {
        var e = new Error((data && data.detail) || ('HTTP ' + res.status));
        e.status = res.status; throw e;
      });
      return res.json();
    }).then(function (env) { return env && env.data; });
  }

  function escapeHtml(v) {
    return String(v == null ? '' : v).replace(/[&<>'"]/g, function (c) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' })[c];
    });
  }

  // ===== 角色选择 =====
  function renderRoles() {
    var customCard = document.getElementById('customCard');
    // 清掉已插入的预设卡（保留自定义卡），避免重复
    Array.prototype.forEach.call(document.querySelectorAll('#roleCards .role-card:not(.is-custom)'), function (el) { el.remove(); });
    var preset = ROLES.map(function (r) {
      return '<div class="role-card" data-role="' + r.id + '">' +
        '<strong>' + escapeHtml(r.name) + '</strong>' +
        '<span>' + escapeHtml(r.desc) + '</span>' +
        '<em>' + escapeHtml(r.tag) + '</em></div>';
    }).join('');
    if (customCard) customCard.insertAdjacentHTML('beforebegin', preset);
    else roleCards.innerHTML = preset;
  }
  roleCards.addEventListener('click', function (e) {
    var card = e.target.closest('[data-role]');
    if (!card || busy) return;
    if (card.dataset.role === 'custom') {
      var form = document.getElementById('customForm');
      form.classList.toggle('on');
      if (form.classList.contains('on')) document.getElementById('customName').focus();
      return;
    }
    role = ROLES.find(function (r) { return r.id === card.dataset.role; }) || ROLES[0];
    startAdventure(role);
  });

  // 自定义形象：色块选择 + 出发
  var swatchBox = document.getElementById('customSwatches');
  if (swatchBox) swatchBox.addEventListener('click', function (e) {
    var sw = e.target.closest('i[data-c]');
    if (!sw) return;
    customSwatch = sw.dataset.c;
    Array.prototype.forEach.call(swatchBox.children, function (i) { i.classList.remove('sel'); });
    sw.classList.add('sel');
  });
  var customGo = document.getElementById('customGo');
  if (customGo) customGo.addEventListener('click', function () {
    var name = (document.getElementById('customName').value || '').trim();
    if (!name) { document.getElementById('customName').focus(); return; }
    role = { id: 'custom', name: name.slice(0, 8), desc: '以你自己的名字，走进丰穗谷', tag: '自定义形象', swatch: customSwatch };
    startAdventure(role);
  });

  // ===== 开场：第一步输入主题 → 第二步选身份 =====

  // 快捷主题 chips：点击填入输入框
  var topicChips = document.getElementById('topicChips');
  if (topicChips) topicChips.addEventListener('click', function (e) {
    var chip = e.target.closest('[data-topic]');
    if (!chip) return;
    if (topicInput) { topicInput.value = chip.dataset.topic; topicInput.focus(); }
  });

  var topicInput = document.getElementById('topicInput');
  var stepTopic = document.getElementById('stepTopic');
  var stepRole = document.getElementById('stepRole');
  var topicNext = document.getElementById('topicNext');
  var topicBack = document.getElementById('topicBack');
  var roleTopicLabel = document.getElementById('roleTopicLabel');

  function goStepRole() {
    var topic = (topicInput && topicInput.value.trim()) || '';
    if (!topic) { if (topicInput) topicInput.focus(); return; }
    if (roleTopicLabel) roleTopicLabel.textContent = '主题：「' + topic + '」· 选择你的身份，与柿小吉一起出发';
    if (stepTopic) stepTopic.hidden = true;
    if (stepRole) stepRole.hidden = false;
    renderRoles();
    loadArchive();
  }
  if (topicNext) topicNext.addEventListener('click', goStepRole);
  if (topicBack) topicBack.addEventListener('click', function () {
    if (stepRole) stepRole.hidden = true;
    if (stepTopic) { stepTopic.hidden = false; if (topicInput) topicInput.focus(); }
  });
  if (topicInput) {
    topicInput.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') goStepRole();
    });
  }

  var starting = false;
  async function startAdventure(selectedRole) {
    if (starting) return;
    starting = true;
    try {
      var topic = (topicInput && topicInput.value.trim()) || '';
      var startBody = { role_id: selectedRole.id };
      if (selectedRole.id === 'custom') startBody.name = selectedRole.name;
      if (topic) startBody.topic = topic;
      session = unwrap(await request('/api/demo/start', startBody));
      roleSelect.style.display = 'none';
      game.classList.add('on');
      // 自定义形象：给旅者立绘加上所选形象的色调光晕
      if (selectedRole.swatch && COLOR_MAP[selectedRole.swatch]) {
        charPlayer.style.filter = COLOR_MAP[selectedRole.swatch];
      }
      renderStage(session.stage);
      setSpeaker('柿小吉', '学习同伴 · 丰穗谷', '开场');
      // 第一轮：请求开场白（advance 内部自管 busy，这里不能提前占锁）
      await advance({ choice: '' });
    } catch (err) {
      alert('无法连接游戏服务：' + (err && err.message || '请确认 8901 已启动'));
    } finally {
      starting = false;
    }
  }

  // ===== 阶段渲染 =====
  function renderStage(stage) {
    var info = STAGES[stage] || STAGES.dialogue;
    scene.style.backgroundImage = 'url("' + ASSET + info.bg + '")';
    // 固定立绘：主角/柿小吉常驻显示，墨迹兽/墨魇之主按阶段显隐
    charMascot.src = ASSET + 'characters/' + info.mascot;
    charPlayer.src = ASSET + 'characters/player_scholar_alpha.png';
    charEnemy.src = ASSET + 'characters/enemy_inkbeast_alpha.png';
    charBoss.src = ASSET + 'characters/boss_moyan_master_alpha.png';
    charMascot.style.opacity = '1';
    charPlayer.style.opacity = '1';
    charEnemy.style.opacity = stage === 'minion' ? '1' : '0';
    charBoss.style.opacity = stage === 'boss' ? '1' : '0';
    charMascot.style.transform = '';
    charPlayer.style.transform = '';
    charEnemy.style.transform = '';
    charBoss.style.transform = '';
    charMascot.style.filter = '';
    charPlayer.style.filter = '';
    charEnemy.style.filter = '';
    charBoss.style.filter = '';
    var idx = STAGE_ORDER.indexOf(stage);
    progress.innerHTML = STAGE_ORDER.map(function (s, i) {
      return '<i class="' + (i <= idx ? 'on' : '') + '" title="' + STAGES[s].label + '"></i>';
    }).join('') + '<span>' + (info.label || '') + '</span>';
    // 法器条：仅战斗阶段显示
    var artifacts = document.getElementById('dArtifacts');
    if (artifacts) {
      if (stage === 'chest' || stage === 'minion' || stage === 'boss') {
        artifacts.classList.add('on');
        selectArtifact(pendingSchool, false);
      } else {
        artifacts.classList.remove('on');
      }
    }
  }

  // ===== 法器 =====
  var ART = document.getElementById('dArtifacts');
  if (ART) ART.addEventListener('click', function (e) {
    var art = e.target.closest('.art[data-s]');
    if (!art || art.disabled) return;
    selectArtifact(art.dataset.s, true);
  });
  function selectArtifact(school, user) {
    if (!ART) return;
    pendingSchool = school;
    Array.prototype.forEach.call(ART.querySelectorAll('.art'), function (b) {
      b.classList.toggle('sel', b.dataset.s === school);
    });
    if (user) audio.tone(520 + Math.random() * 80, 0.18, 0.02, 'sine');
  }

  // 法术三段演出：蓄力 → 弹道飞行 → 命中爆开（在敌人/BOSS/宝箱位置）
  function spellTargetPoint() {
    var target = null;
    if (charEnemy.style.opacity === '1') target = charEnemy;
    else if (charBoss.style.opacity === '1') target = charBoss;
    if (target) {
      var r = target.getBoundingClientRect();
      return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
    }
    // 宝箱阶段：场景右中偏下（宝箱位置）
    return { x: window.innerWidth * 0.7, y: window.innerHeight * 0.52 };
  }
  function playSpellBurst(school) {
    var pr = charPlayer.getBoundingClientRect();
    var start = { x: pr.left + pr.width * 0.7, y: pr.top + pr.height * 0.35 };
    var end = spellTargetPoint();
    var el = function (cls, w, h) {
      var d = document.createElement('div');
      d.className = cls;
      d.style.width = w + 'px'; d.style.height = h + 'px';
      return d;
    };
    // ① 蓄力：主角法器光球
    var charge = el('spell-charge', 110, 110);
    charge.style.left = start.x + 'px'; charge.style.top = start.y + 'px';
    charge.innerHTML = '<img src="' + ASSET + 'spells/' + school + '_charge.png" alt="">';
    document.body.appendChild(charge);
    // ② 弹道：trail 从主角飞向目标
    var trail = el('spell-trail', 150, 60);
    var angle = Math.atan2(end.y - start.y, end.x - start.x) * 180 / Math.PI;
    trail.style.left = start.x + 'px'; trail.style.top = start.y + 'px';
    trail.style.transform = 'rotate(' + angle + 'deg)';
    trail.innerHTML = '<img src="' + ASSET + 'spells/' + school + '_trail.png" alt="">';
    document.body.appendChild(trail);
    // ③ 命中爆开
    window.setTimeout(function () {
      var burst = el('spell-burst', Math.max(160, 180), Math.max(160, 180));
      burst.style.left = end.x + 'px'; burst.style.top = end.y + 'px';
      burst.innerHTML = '<img src="' + ASSET + 'spells/' + school + '_impact_restore.png" alt=""><i class="ring"></i>';
      document.body.appendChild(burst);
      window.setTimeout(function () {
        if (charge.isConnected) charge.remove();
        if (trail.isConnected) trail.remove();
        if (burst.isConnected) burst.remove();
      }, 900);
    }, 480);
    // 弹道飞行动画（transition 驱动）
    window.requestAnimationFrame(function () {
      window.requestAnimationFrame(function () {
        trail.style.transition = 'left .42s cubic-bezier(.3,.7,.4,1), top .42s cubic-bezier(.3,.7,.4,1)';
        trail.style.left = end.x + 'px';
        trail.style.top = end.y + 'px';
      });
    });
  }

  // ===== 旁白衔接：进入新场景前的过渡旁白（{topic} 会替换为当前主题） =====
  var NARRATION = {
    dialogue: [
      ['旁白', '丰穗谷的风从石门间穿过，一束微光落在旅者脚边。'],
      ['旁白', '书页被风翻开，回声在廊柱间打转。这里是旧书坡——你每说一句，它都送回一句。'],
      ['旁白', '柿小吉从书阁深处探出头来："「{topic}」这团墨，今天要一起收拾掉它。"'],
    ],
    chest: [
      ['旁白', '穿过书阁的回廊，一盏古灯在树根下亮着——灯下压着一页旧题。'],
      ['旁白', '树根下嵌着一只古铜宝箱，箱面的纹路，像是「{topic}」留下的测试题。'],
      ['旁白', '柿小吉拍了拍箱子："「{topic}」的知识藏在里面，答对才能打开。"'],
    ],
    minion: [
      ['旁白', '雾从石径尽头漫过来，墨色在雾里时聚时散——像是嗅到了新鲜的知识。'],
      ['旁白', '墨迹兽的低吼从雾中传来：它闻到「{topic}」的味道了。'],
      ['旁白', '一只墨迹兽从雾里探出头："「{topic}」？说得倒轻巧，答出来才算数。"'],
    ],
    boss: [
      ['旁白', '古塔的轮廓从雾中靠近，三道封印在深处缓缓转动。'],
      ['旁白', '墨魇之主的雾影压下来，封印上的字逐个亮起：解释、辨析、迁移。'],
      ['旁白', '塔门在身后合拢。墨魇之主的声音从雾里传来："这一路捡来的「{topic}」证据，够不够进这座塔？"'],
    ],
  };
  var narrationIndex = {};

  function showStageNarration(stage, done) {
    if (!stage || stage === 'victory' || stage === 'dialogue') { if (done) done(); return; }
    var pool = NARRATION[stage];
    if (!pool) { if (done) done(); return; }
    if (!narrationIndex[stage]) narrationIndex[stage] = 0;
    var idx = narrationIndex[stage];
    narrationIndex[stage] = (idx + 1) % pool.length;
    var line = pool[idx % pool.length];
    var topicName = (session && session.topic) || '这个主题';
    setSpeaker(line[0], '丰穗谷 · 回响', '场景过渡');
    typeText(String(line[1]).replace(/\{topic\}/g, topicName), function () {
      renderOptions(['继续']);
    });
    // 覆盖默认的选项点击推进行为
    var handler = function (e) {
      var btn = e.target.closest('[data-opt]');
      if (!btn) return;
      e.stopImmediatePropagation();
      dOptions.removeEventListener('click', handler);
      if (done) done();
    };
    dOptions.addEventListener('click', handler);
  }
  // 覆写 advance 中的阶段切换反馈，先播旁白再推进
  var origRenderInteraction = null; // 占位
  // 旁白播完后继续显示反馈
  function showStageFeedback(next) {
    if (next.stage === 'victory') {
      showVictory(next);
      return;
    }
    if (next.stage === 'dialogue') {
      var last = next.history && next.history[next.history.length - 1];
      if (last && last.speaker === '柿小吉') {
        setSpeaker('柿小吉', '学习同伴 · 丰穗谷', '第 ' + (next.dialogue_index) + ' 轮');
        typeText(last.text, function () {
          renderOptions(next.options || []);
        });
      }
      return;
    }
    // 答题阶段：显示题目
    if (next.stage === 'chest' || next.stage === 'minion' || next.stage === 'boss') {
      var stageLabel = next.stage === 'chest' ? '宝箱 · 知识考验'
        : next.stage === 'minion' ? ('雾谷 · 墨迹兽 ' + next.stage_index + '/' + next.stage_total)
        : ('古塔 · ' + (next.boss_stage_name || '') + ' ' + next.stage_index + '/' + next.stage_total);
      setSpeaker(next.stage === 'boss' ? '墨魇之主' : '柿小吉', next.stage === 'boss' ? '封印古塔 · 最终守关' : '学习同伴 · 丰穗谷', stageLabel);
      var q = next.task || {};
      if (q.type === 'multiple_choice') {
        typeText(q.question, function () { renderChoiceOptions(q.options || [], q); });
      } else {
        typeText(q.question, function () { renderAnswerInput(q); });
      }
      // 阶段切换反馈
      if (next.stage === 'chest' && next.feedback && next.feedback.kind === 'info') {
        showFeedback(next.feedback.message, 'info');
      }
      if (next.stage === 'minion' && next.feedback && next.feedback.kind === 'success' && next.stage_index === 0) {
        showFeedback(next.feedback.message, 'success');
      }
      if (next.stage === 'boss' && next.feedback && next.feedback.kind === 'success' && next.stage_index === 0) {
        showFeedback(next.feedback.message, 'success');
        if (next.clue) showClue(next.clue);
      } else if (next.clue && next.stage_index === 0) {
        showClue(next.clue);
      }
      // 答错反馈：明确提示重新作答，避免"点了没反应"
      if (next.feedback && next.feedback.kind === 'needs_review') {
        showFeedback(next.feedback.message, 'needs_review');
      }
      return;
    }
  }
  var _advanceOrig = undefined;
  // 改 advance 中阶段切换处的逻辑
  async function advance(payload) {
    if (busy) return;
    busy = true;
    setBusyUI(true);
    var prevStage = session ? session.stage : null;
    try {
      var next = unwrap(await request('/api/demo/advance', Object.assign({ run_id: session.run_id, action: 'advance' }, payload)));
      session = next;
      renderStage(next.stage);

      // 答对且进入新题/新阶段 → 播放法器爆开
      if (next.feedback && next.feedback.kind === 'success' && next.stage !== 'dialogue') {
        playSpellBurst(pendingSchool);
      }

      // 阶段切换：先播旁白过渡，再显示题目
      var stageChanged = next.stage !== prevStage;
      if (stageChanged && (next.stage === 'chest' || next.stage === 'minion' || next.stage === 'boss')) {
        showStageNarration(next.stage, function () {
          showStageFeedback(next);
        });
        return;
      }
      showStageFeedback(next);
    } catch (err) {
      showFeedback('操作未完成：' + (err && err.message || '请重试'), 'needs_review');
    } finally {
      busy = false;
      setBusyUI(false);
    }
  }

  // ===== UI 子函数 =====
  function setBusyUI(on) {
    dOptions.style.pointerEvents = on ? 'none' : '';
    dSubmit.disabled = on;
  }

  function setSpeaker(name, roleLine, tag) {
    dialog.dataset.speaker = name;
    dName.textContent = name;
    dRole.textContent = roleLine || '丰穗谷';
    dTag.textContent = tag || '';
  }

  function typeText(text, done) {
    dText.textContent = '';
    dOptions.innerHTML = '';
    dInput.classList.remove('on');
    dFeedback.classList.remove('on');
    var chars = Array.from(text || '');
    var i = 0;
    if ((window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) || document.hidden) {
      dText.textContent = text;
      if (done) done();
      return;
    }
    clearTimeout(typingTimer);
    (function step() {
      i += 1;
      dText.textContent = chars.slice(0, i).join('');
      if (i >= chars.length) { if (done) done(); return; }
      typingTimer = setTimeout(step, 22);
    })();
  }

  function renderOptions(options) {
    dOptions.innerHTML = options.map(function (opt, i) {
      return '<button type="button" data-opt="' + i + '"><i>' + String.fromCharCode(65 + i) + '</i> ' + escapeHtml(opt) + '</button>';
    }).join('');
    dOptions.style.display = 'grid';
  }
  dOptions.addEventListener('click', function (e) {
    var btn = e.target.closest('[data-opt]');
    if (!btn || busy) return;
    // 全局处理器只负责对话阶段的选项；答题/旁白按钮由各自处理器处理，避免把答案劫持成 choice
    if (!session || session.stage !== 'dialogue') return;
    dOptions.innerHTML = '';
    advance({ choice: session.options[Number(btn.dataset.opt)] || '' });
  });
  function renderChoiceOptions(options, task) {
    dOptions.innerHTML = options.map(function (opt, i) {
      return '<button type="button" data-opt="' + i + '"><i>' + String.fromCharCode(65 + i) + '</i> ' + escapeHtml(opt) + '</button>';
    }).join('');
    dOptions.style.display = 'grid';
    var handler = function (e) {
      var btn = e.target.closest('[data-opt]');
      if (!btn || busy) return;
      e.stopImmediatePropagation();
      dOptions.removeEventListener('click', handler);
      dOptions.innerHTML = '';
      advance({ answer: options[Number(btn.dataset.opt)] });
    };
    dOptions.addEventListener('click', handler);
  }

  function renderAnswerInput(task) {
    dOptions.style.display = 'none';
    dInput.classList.add('on');
    dAnswer.focus();
    dSubmit.onclick = function () {
      var v = dAnswer.value.trim();
      if (!v) return;
      dAnswer.value = '';
      advance({ answer: v });
    };
    dAnswer.onkeydown = function (e) {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); dSubmit.click(); }
    };
  }

  function showFeedback(msg, kind) {
    dFeedback.className = 'd-feedback on ' + (kind || 'info');
    dFeedback.textContent = msg;
  }

  function showClue(text) {
    clueFloat.textContent = text;
    clueFloat.hidden = false;
    clearTimeout(clueFloat._t);
    clueFloat._t = setTimeout(function () { clueFloat.hidden = true; }, 5200);
  }

  function showVictory(snap) {
    game.classList.remove('on');
    victoryTitle.textContent = '拾光有成 · ' + (role ? role.name : '');
    victoryText.textContent = '你与柿小吉一起走过了回声书阁的对话、宝库的考验、雾谷的墨迹兽，最终用「' + snap.topic + '」的知识击败了墨魇之主。丰穗谷的灯火，因你亮起。';
    victory.classList.add('on');
  }

  restartBtn.addEventListener('click', function () {
    victory.classList.remove('on');
    game.classList.remove('on');
    roleSelect.style.display = '';
    session = null;
    // 回到第一步：主题输入
    var st = document.getElementById('stepTopic');
    var sr = document.getElementById('stepRole');
    if (st) st.hidden = false;
    if (sr) sr.hidden = true;
    if (stepTopic) stepTopic.hidden = false;
    if (stepRole) stepRole.hidden = true;
  });

  var backMainBtn = document.getElementById('backMainBtn');
  if (backMainBtn) backMainBtn.addEventListener('click', function () {
    window.location.href = '/ai-learning';
  });

  // ===== 返回对话（所有界面常驻） =====
  var backDialogBtn = document.getElementById('backDialogBtn');
  if (backDialogBtn) backDialogBtn.addEventListener('click', function () {
    window.location.href = '/ai-learning';
  });

  // ===== 历史存档：加载列表 + 恢复继续 =====
  var archiveList = document.getElementById('archiveList');
  function getRequest(path) {
    return fetch(API + path, { method: 'GET', headers: { 'Accept': 'application/json' } })
      .then(function (res) { if (!res.ok) throw new Error('HTTP ' + res.status); return res.json(); })
      .then(function (env) { return env && env.data; });
  }
  async function loadArchive() {
    if (!archiveList) return;
    try {
      var data = await getRequest('/api/demo/list');
      var sessions = (data && data.sessions) || [];
      // 同主题+角色去重：只保留最新一条，避免存档列表堆积
      var seen = {};
      sessions = sessions.filter(function (s) {
        var key = (s.topic || '') + '|' + (s.role_name || '');
        if (seen[key]) return false;
        seen[key] = true;
        return true;
      });
      if (!sessions.length) { archiveList.hidden = true; return; }
      archiveList.hidden = false;
      archiveList.innerHTML = '<small>历史存档 · 点进去继续上次的冒险</small>' + sessions.map(function (s) {
        var stageName = { dialogue: '对话中', chest: '宝箱前', minion: '雾谷 · 小怪', boss: '古塔 · BOSS' }[s.stage] || s.stage;
        return '<button type="button" class="archive-item" data-run="' + escapeHtml(s.run_id) + '">' +
          '<b>' + escapeHtml(s.topic) + '</b>' +
          '<span>' + escapeHtml(s.role_name) + ' · ' + stageName + '</span>' +
          '<em>继续 →</em></button>';
      }).join('');
      Array.prototype.forEach.call(archiveList.querySelectorAll('.archive-item'), function (btn) {
        btn.addEventListener('click', function () { resumeInto(btn.dataset.run); });
      });
    } catch (_) { archiveList.hidden = true; }
  }

  async function resumeInto(runId) {
    if (busy) return;
    busy = true;
    try {
      var next = unwrap(await request('/api/demo/resume', { run_id: runId }));
      session = next;
      roleSelect.style.display = 'none';
      game.classList.add('on');
      renderStage(next.stage);
      setBusyUI(true);
      renderResumed(next);
    } catch (err) {
      alert('无法恢复存档：' + (err && err.message || '请重试'));
    } finally { busy = false; setBusyUI(false); }
  }

  function renderResumed(next) {
    if (next.stage === 'victory') { showVictory(next); return; }
    if (next.stage === 'dialogue') {
      var last = next.history && next.history[next.history.length - 1];
      setSpeaker('柿小吉', '学习同伴 · 丰穗谷', '第 ' + (next.dialogue_index) + ' 轮 · 继续');
      typeText(last ? last.text : '我们继续刚才的话题吧。', function () {
        renderOptions(next.options || []);
      });
      return;
    }
    if (next.stage === 'chest' || next.stage === 'minion' || next.stage === 'boss') {
      var stageLabel = next.stage === 'chest' ? '宝箱 · 知识考验'
        : next.stage === 'minion' ? ('雾谷 · 墨迹兽 ' + next.stage_index + '/' + next.stage_total)
        : ('古塔 · ' + (next.boss_stage_name || '') + ' ' + next.stage_index + '/' + next.stage_total);
      setSpeaker(next.stage === 'boss' ? '墨魇之主' : '柿小吉', next.stage === 'boss' ? '封印古塔 · 最终守关' : '学习同伴 · 丰穗谷', stageLabel + ' · 继续');
      var q = next.task || {};
      if (q.type === 'multiple_choice') typeText(q.question, function () { renderChoiceOptions(q.options || [], q); });
      else typeText(q.question, function () { renderAnswerInput(q); });
      return;
    }
  }

  // ===== 启动 =====
  renderRoles();
  // 角色选择第二步显示时加载历史存档
  var stepRoleEl = document.getElementById('stepRole');
  if (stepRoleEl && window.MutationObserver) {
    new MutationObserver(function () {
      if (!stepRoleEl.hidden) loadArchive();
    }).observe(stepRoleEl, { attributes: true, attributeFilter: ['hidden'] });
  }
  // 调试/演示辅助：?auto=wandering_mage 直接开始，跳过角色选择点击
  var autoRole = new URLSearchParams(location.search).get('auto');
  if (autoRole) {
    var found = ROLES.find(function (r) { return r.id === autoRole; }) || ROLES[0];
    window.setTimeout(function () { startAdventure(found); }, 400);
  }
})();
