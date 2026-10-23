/**
 * Darya 绘本智能体 · 可嵌入组件
 * ================================================================
 * 宿主页面（如 Darya）接入方式：
 *
 *   1) 引入脚本（组件会自动加载同目录的 agent-core.js / agent-reader.js）：
 *        <script src="/path/to/agent/darya-widget.js" defer></script>
 *
 *   2) 放一个文字入口（三种形态任选）：
 *        <darya-storybook mode="link"></darya-storybook>          文字链接（默认）
 *        <darya-storybook mode="card"></darya-storybook>          带输入框的卡片
 *        <darya-storybook mode="fab"></darya-storybook>           悬浮球
 *
 *      常用属性：entry-text（入口文案）、topic、difficulty（elementary|middle|high）、
 *               pages（3|4|5）、style（colorful|hand-drawn|scientific|minimal）、api-key
 *      例：<darya-storybook mode="link" entry-text="生成绘本" topic="光合作用"></darya-storybook>
 *
 *   3) 或宿主 JS 直接调用（如从对话流调起）：
 *        const el = document.querySelector('darya-storybook');
 *        el.generate('光合作用', { difficulty: 'middle', pages: 4 });   // 打开窗口并生成
 *        el.open();                                                     // 只开窗口
 *        el.addEventListener('darya-pb:generated', e => saveArtifact(e.detail.artifact));
 *
 *   全部 UI 在 Shadow DOM 内，不污染宿主样式；API Key 走宿主注入或本机 localStorage。
 * ================================================================ */
(function () {
  'use strict';

  /* ---------------- 依赖加载 ---------------- */
  function scriptBase() {
    var scripts = document.querySelectorAll('script[src]');
    for (var i = scripts.length - 1; i >= 0; i--) {
      var src = scripts[i].getAttribute('src') || '';
      if (src.indexOf('darya-widget.js') >= 0) {
        return src.slice(0, src.lastIndexOf('/') + 1);
      }
    }
    return new URL('./', document.baseURI).href;
  }

  function loadScript(src) {
    return new Promise(function (resolve, reject) {
      var s = document.createElement('script');
      s.src = src;
      s.onload = resolve;
      s.onerror = function () { reject(new Error('加载失败: ' + src)); };
      document.head.appendChild(s);
    });
  }

  var depsPromise = null;
  function ensureDeps(base) {
    if (window.PB && customElements.get('pb-reader')) return Promise.resolve();
    if (!depsPromise) {
      // 必须先 core 后 reader：agent-reader 在脚本执行瞬间捕获 window.PB，
      // 并行加载时 reader 可能拿到空对象，导致 Narrator.stop 处崩溃。
      depsPromise = (window.PB ? Promise.resolve() : loadScript(base + 'agent-core.js'))
        .then(function () {
          return customElements.get('pb-reader') ? Promise.resolve() : loadScript(base + 'agent-reader.js');
        })
        .then(function () { return true; });
    }
    return depsPromise;
  }

  /* ---------------- 模板与样式 ---------------- */
  var TEMPLATE = document.createElement('template');
  TEMPLATE.innerHTML = `
  <style>
    :host { all: initial; display: block; font-family: "PingFang SC","Microsoft YaHei","Noto Sans SC",system-ui,sans-serif; }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    button { font-family: inherit; cursor: pointer; }

    /* ---------- 入口（宿主页面上唯一可见的东西） ---------- */
    .entry { display: inline-flex; }
    /* 宿主自己有调用入口时隐藏组件自带入口，避免页面多出一个悬浮按钮 */
    :host([entry-hidden]) .entry { display: none; }
    :host([mode="fab"]) .entry { display: block; position: fixed; right: 22px; bottom: 22px; z-index: 2147480000; }
    .dw-link {
      display: inline-flex; align-items: center; gap: 7px;
      border: none; background: transparent; color: inherit;
      font-size: 14px; padding: 4px 6px; border-radius: 10px;
      transition: background .15s;
    }
    .dw-link:hover { background: rgba(245,158,11,.12); }
    :host([mode="card"]) .dw-link {
      background: #fff; border: 1.5px dashed #e8dfcd; padding: 10px 16px; color: #6b5d43;
    }
    :host([mode="card"]) .dw-link:hover { border-color: #f59e0b; }
    :host([mode="fab"]) .dw-link {
      width: 56px; height: 56px; border-radius: 50%; justify-content: center;
      background: linear-gradient(135deg,#f59e0b,#f97316); color: #fff; font-size: 26px;
      box-shadow: 0 10px 26px rgba(245,158,11,.45); padding: 0;
    }
    :host([mode="fab"]) .dw-link:hover { transform: translateY(-2px); }

    /* ---------- 弹窗窗口 ---------- */
    .overlay { position: fixed; inset: 0; z-index: 2147483000; display: flex; align-items: center; justify-content: center; padding: 18px; }
    .overlay[hidden] { display: none; }
    .backdrop { position: absolute; inset: 0; background: rgba(40,32,16,.5); backdrop-filter: blur(3px); }
    .panel {
      position: relative; width: min(940px, 96vw); max-height: 92vh;
      background: #fdfbf7; border-radius: 20px; border: 1px solid #e7dfd0;
      box-shadow: 0 30px 90px rgba(40,32,16,.35);
      display: flex; flex-direction: column; overflow: hidden;
      animation: dwPop .28s cubic-bezier(.2,.9,.35,1.15);
    }
    @keyframes dwPop { from { opacity: 0; transform: scale(.94) translateY(18px); } to { opacity: 1; transform: none; } }
    .panel-head {
      display: flex; align-items: center; gap: 10px;
      padding: 14px 18px; border-bottom: 1px solid #eee4d0; background: #fffdf8;
    }
    .panel-head .logo { font-size: 20px; }
    .panel-head b { font-size: 15.5px; color: #3f3524; letter-spacing: 1px; }
    .panel-head .badge { font-size: 11.5px; color: #9a8a68; background: #f4efe2; padding: 3px 10px; border-radius: 999px; }
    .panel-head .close {
      margin-left: auto; width: 32px; height: 32px; border-radius: 10px;
      border: 1px solid #e2d9c8; background: #fff; color: #8a7b5e; font-size: 15px;
    }
    .panel-head .close:hover { background: #fef2f2; color: #ef4444; border-color: #f8c9c9; }
    .panel-body { overflow-y: auto; padding: 18px 20px 22px; }

    /* 表单 */
    .form-grid { display: grid; grid-template-columns: 1fr 128px 108px; gap: 10px; margin-bottom: 12px; }
    .f-field label { display: block; font-size: 12px; color: #8a7b5e; font-weight: 600; margin-bottom: 5px; letter-spacing: 1px; }
    .f-field input, .f-field select {
      width: 100%; padding: 10px 12px; border: 1.5px solid #e7dfd0; border-radius: 11px;
      font-size: 14px; font-family: inherit; background: #fff; color: #3f3524; outline: none;
    }
    .f-field input:focus, .f-field select:focus { border-color: #f59e0b; box-shadow: 0 0 0 3px rgba(245,158,11,.15); }
    .f-actions { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
    .btn-go {
      border: none; background: linear-gradient(135deg,#f59e0b,#f97316); color: #fff;
      font-size: 15px; font-weight: 700; letter-spacing: 2px; padding: 11px 28px; border-radius: 12px;
      box-shadow: 0 8px 20px rgba(245,158,11,.4); transition: transform .18s, box-shadow .18s;
    }
    .btn-go:hover:not(:disabled) { transform: translateY(-1px); }
    .btn-go:disabled { opacity: .6; cursor: default; }
    .hint { font-size: 12px; color: #a89a7c; }
    .hint b.ok { color: #10b981; } .hint b.warn { color: #d97706; }
    .examples { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 10px; align-items: center; }
    .examples span { font-size: 12px; color: #b3a482; }
    .ex-btn { font-size: 12px; color: #6b5d43; background: #f4efe2; border: 1px solid #e8dfcd; border-radius: 999px; padding: 4px 12px; }
    .ex-btn:hover { background: #fff; }

    /* 进度 */
    .progress { display: none; margin-top: 14px; }
    .progress.show { display: block; }
    .p-track { height: 8px; background: #f4efe6; border-radius: 99px; overflow: hidden; }
    .p-fill { height: 100%; width: 0%; border-radius: 99px; background: linear-gradient(90deg,#6366f1,#ec4899,#f59e0b); transition: width .4s ease; }
    .p-msg { font-size: 12.5px; color: #8a7b5e; margin-top: 6px; }

    /* 阅读器挂载 */
    .reader-mount { margin-top: 16px; }
    .reader-mount:empty { display: none; }

    @media (max-width: 640px) {
      .form-grid { grid-template-columns: 1fr; }
      .panel { max-height: 96vh; }
    }
  </style>
  <div class="entry" part="entry">
    <button class="dw-link" part="entry-button">
      <span class="dw-icon">📖</span><span class="dw-entry-text">生成教学绘本</span>
    </button>
  </div>
  <div class="overlay" hidden>
    <div class="backdrop" data-act="backdrop"></div>
    <div class="panel" role="dialog" aria-label="视觉绘本">
      <div class="panel-head">
        <span class="logo">📖</span><b>视觉绘本</b>
        <span class="badge">5E 教学 · 朗读 · 测评 · PDF</span>
        <button class="close" data-act="close" title="关闭">✕</button>
      </div>
      <div class="panel-body">
        <div class="form-grid">
          <div class="f-field">
            <label>学习主题</label>
            <input class="dw-topic" placeholder="任何主题：光合作用、分数、一战、量子纠缠……" autocomplete="off">
          </div>
          <div class="f-field">
            <label>学段</label>
            <select class="dw-difficulty">
              <option value="elementary">小学</option>
              <option value="middle" selected>初中</option>
              <option value="high">高中</option>
            </select>
          </div>
          <div class="f-field">
            <label>页数</label>
            <select class="dw-pages">
              <option value="3">3 页</option>
              <option value="4" selected>4 页</option>
              <option value="5">5 页</option>
            </select>
          </div>
        </div>
        <div class="f-field" style="margin-bottom:12px;">
          <label>画风</label>
          <select class="dw-style">
            <option value="colorful" selected>明亮多彩 · 扁平绘本</option>
            <option value="hand-drawn">温柔手绘 · 水彩</option>
            <option value="scientific">科学图解 · 教科书</option>
            <option value="minimal">简约几何 · 大色块</option>
          </select>
        </div>
        <div class="f-actions">
          <button class="btn-go">✨ 生成绘本</button>
          <span class="hint"></span>
        </div>
        <div class="examples">
          <span>试一试：</span>
          <button class="ex-btn" data-t="光合作用">光合作用</button>
          <button class="ex-btn" data-t="水循环">水循环</button>
          <button class="ex-btn" data-t="第一次世界大战">第一次世界大战</button>
          <button class="ex-btn" data-t="量子纠缠">量子纠缠</button>
          <button class="ex-btn" data-t="敦煌壁画">敦煌壁画</button>
        </div>
        <div class="progress">
          <div class="p-track"><div class="p-fill"></div></div>
          <div class="p-msg"></div>
        </div>
        <div class="reader-mount"></div>
      </div>
    </div>
  </div>`;

  /* ---------------- 组件 ---------------- */
  class DaryaStorybook extends HTMLElement {
    static get observedAttributes() {
      return ['entry-text', 'topic', 'difficulty', 'pages', 'style', 'api-key'];
    }

    constructor() {
      super();
      this.attachShadow({ mode: 'open' });
      this.shadowRoot.appendChild(TEMPLATE.content.cloneNode(true));
      this._agent = null;
      this._generating = false;
      this._base = this.getAttribute('data-base') || scriptBase();
      this._reader = null;
      this._bindEntry();
      this._bindPanel();   // 模板已在上面克隆，事件必须在此绑定（define 时的升级不走后续补丁）
    }

    connectedCallback() {
      this._applyEntryText();
    }

    attributeChangedCallback(name, oldV, newV) {
      if (oldV === newV || !this.shadowRoot) return;
      if (name === 'entry-text') this._applyEntryText();
      if (name === 'topic') { var t = this.shadowRoot.querySelector('.dw-topic'); if (t) t.value = newV || ''; }
      if (name === 'difficulty') { var d = this.shadowRoot.querySelector('.dw-difficulty'); if (d && newV) d.value = newV; }
      if (name === 'pages') { var p = this.shadowRoot.querySelector('.dw-pages'); if (p && newV) p.value = newV; }
      if (name === 'style') { var s = this.shadowRoot.querySelector('.dw-style'); if (s && newV) s.value = newV; }
    }

    _applyEntryText() {
      var t = this.getAttribute('entry-text');
      if (t) this.shadowRoot.querySelector('.dw-entry-text').textContent = t;
    }

    _bindEntry() {
      this.shadowRoot.querySelector('.dw-link').addEventListener('click', () => this.open());
    }

    /* ---------- 依赖与配置 ---------- */
    _ensureDeps() { return ensureDeps(this._base); }

    _loadLocalConfig() {
      var cfg = {};
      try { cfg = JSON.parse(localStorage.getItem('pb_agent_config') || '{}'); } catch (e) { /* ignore */ }
      // 代理模式：宿主把调用指向自己的同源代理，凭证留在服务端。
      // 此时浏览器不应再持有平台 Key，用一个占位令牌让上游鉴权由代理完成。
      var chatUrl = this.getAttribute('data-chat-url') || '';
      var imageUrl = this.getAttribute('data-image-url') || '';
      var proxyMode = !!(chatUrl || imageUrl);
      return {
        apiKey: proxyMode
          ? 'proxy'
          : (this.getAttribute('api-key') || cfg.apiKey || localStorage.getItem('glm_api_key') || ''),
        chatUrl: chatUrl,
        imageUrl: imageUrl,
        authToken: this.getAttribute('data-auth-token') || '',
        chatModel: cfg.chatModel || 'glm-4-flash',
        useAIImage: cfg.useAIImage !== false,
        imageProxy: this.getAttribute('proxy-base') || cfg.imageProxy || '/proxy?url='
      };
    }

    _emit(name, detail) {
      this.dispatchEvent(new CustomEvent('darya-pb:' + name, { detail: detail || {}, bubbles: true, composed: true }));
      if (window.parent && window.parent !== window) {
        window.parent.postMessage(Object.assign({ type: 'darya-pb:' + name }, detail || {}), '*');
      }
    }

    /* ---------- 打开 / 关闭 ---------- */
    open(opts) {
      opts = opts || {};
      if (opts.topic) this.shadowRoot.querySelector('.dw-topic').value = opts.topic;
      // 宿主可能以 display:none 隐藏入口；弹层需要宿主参与布局才能渲染。
      this.style.display = 'contents';
      var overlay = this.shadowRoot.querySelector('.overlay');
      overlay.hidden = false;
      document.documentElement.style.overflow = 'hidden';   // 锁宿主滚动
      this._emit('opened');
      this._ensureDeps().then(() => this._renderKeyHint()).catch(function (err) {
        console.warn('[darya-storybook]', err.message);
      });
      if (opts.autogenerate && (opts.topic || this.shadowRoot.querySelector('.dw-topic').value)) {
        this.generate(opts.topic, opts);
      }
    }

    close() {
      this.shadowRoot.querySelector('.overlay').hidden = true;
      this.style.display = 'none';   // 关闭后恢复隐藏入口（宿主页有自己的打开按钮）
      document.documentElement.style.overflow = '';
      if (window.PB) PB.Narrator.stop();
      this._emit('closed');
    }

    /* ---------- 生成 ---------- */
    generate(topic, opts) {
      opts = opts || {};
      this.open(opts);
      return this._ensureDeps().then(() => {
        var root = this.shadowRoot;
        if (topic) root.querySelector('.dw-topic').value = topic;
        if (opts.difficulty) root.querySelector('.dw-difficulty').value = opts.difficulty;
        if (opts.pages) root.querySelector('.dw-pages').value = String(opts.pages);
        if (opts.style) root.querySelector('.dw-style').value = opts.style;
        return this._generate();
      });
    }

    _generate() {
      return this._ensureDeps().then(() => {
        var root = this.shadowRoot;
        var topic = root.querySelector('.dw-topic').value.trim();
        if (!topic) { root.querySelector('.dw-topic').focus(); return Promise.resolve(null); }
        if (this._generating) return Promise.resolve(null);
        this._generating = true;
        root.querySelector('.btn-go').disabled = true;

        var progress = root.querySelector('.progress');
        progress.classList.add('show');
        var fill = root.querySelector('.p-fill');
        var msg = root.querySelector('.p-msg');
        var setStage = function (stage, state, detail) { if (detail) msg.textContent = detail; };
        var setProgress = function (p, m) {
          fill.style.width = p + '%';
          if (m) msg.textContent = m;
        };

        var cfg = this._loadLocalConfig();
        if (!this._agent) this._agent = new PB.PictureBookAgent(cfg);
        else Object.assign(this._agent.config, cfg);

        root.querySelector('.reader-mount').innerHTML = '';

        // 画风跟随宿主产品主题（柿子暖色 / Ocean 海蓝）；用户单独选择风格时仍可在表单调整色彩
        var hostTheme = this.getAttribute('data-theme') || 'kaki';
        var themeStyleName = hostTheme === 'ocean'
          ? '清冷海洋蓝水彩绘本，浅色背景，蓝绿主色，画面安静克制'
          : '柿子暖色手绘绘本，米色纸面质感，柿子橙与浅海蓝点缀';

        return this._agent.generate({
          topic: topic,
          difficulty: root.querySelector('.dw-difficulty').value,
          pageCount: parseInt(root.querySelector('.dw-pages').value, 10),
          style: root.querySelector('.dw-style').value,
          styleName: themeStyleName
        }, { onStage: setStage, onProgress: setProgress }).then((artifact) => {
          // 渲染阅读器（<pb-reader> 为全局自定义元素）
          var mount = root.querySelector('.reader-mount');
          mount.innerHTML = '<pb-reader></pb-reader>';
          this._reader = mount.querySelector('pb-reader');
          this._reader.load(artifact);
          // 教研模型不可用时正文只是通用模板框架；必须如实说明，不能伪装成真实讲解
          if (this._agent && this._agent._storyboardDegraded) {
            setProgress(100, '已生成（模板模式）');
            root.querySelector('.hint').innerHTML =
              '<b class="warn">● 模板模式</b> 教研模型暂时不可用，本次正文为通用框架，不是针对「'
              + topic + '」的真实讲解';
          }
          this._emit('generated', { artifact: artifact, topic: topic, degraded: !!(this._agent && this._agent._storyboardDegraded) });
          root.querySelector('.btn-go').disabled = false;
          this._generating = false;
          return artifact;
        }).catch((error) => {
          console.error('[darya-storybook] 生成失败', error);
          msg.textContent = '生成失败：' + (error && error.message || '未知错误');
          root.querySelector('.btn-go').disabled = false;
          this._generating = false;
          this._emit('error', { message: error && error.message });
          return null;
        });
      });
    }

    _renderKeyHint() {
      var hint = this.shadowRoot.querySelector('.hint');
      hint.innerHTML = '<b class="ok">● 可直接生成</b>';
    }

    /* ---------- 内部事件 ---------- */
    _bindPanel() {
      var root = this.shadowRoot;
      root.querySelector('.btn-go').addEventListener('click', () => this._generate());
      root.querySelector('[data-act="close"]').addEventListener('click', () => this.close());
      root.querySelector('[data-act="backdrop"]').addEventListener('click', () => this.close());
      root.querySelector('.dw-topic').addEventListener('keydown', (e) => {
        if (e.key === 'Enter') this._generate();
      });
      root.querySelectorAll('.ex-btn').forEach(function (b) {
        b.addEventListener('click', function () {
          root.querySelector('.dw-topic').value = b.dataset.t;
        });
      });
      document.addEventListener('keydown', (e) => {
        var overlay = root.querySelector('.overlay');
        if (e.key === 'Escape' && !overlay.hidden) this.close();
      });
    }
  }

  if (!customElements.get('darya-storybook')) customElements.define('darya-storybook', DaryaStorybook);

  /* ---------------- 宿主快捷 API ---------------- */
  window.DaryaStorybook = {
    version: '2.1',
    /** 调起第一个入口组件；无则自动创建 fab */
    open: function (opts) {
      var el = document.querySelector('darya-storybook');
      if (!el) {
        el = document.createElement('darya-storybook');
        el.setAttribute('mode', 'link');
        (opts && opts.mount || document.body).appendChild(el);
      }
      el.open(opts);
      return el;
    },
    /** 对话流调起：一句话生成绘本 */
    generate: function (topic, opts) {
      var el = this.open(opts);
      return el.generate(topic, opts);
    },
    /** 宿主下发 API Key（也可让用户在界面里存） */
    setApiKey: function (key) {
      try {
        var cfg = JSON.parse(localStorage.getItem('pb_agent_config') || '{}');
        cfg.apiKey = key;
        localStorage.setItem('pb_agent_config', JSON.stringify(cfg));
        localStorage.setItem('glm_api_key', key);
      } catch (e) { /* ignore */ }
    }
  };
})();
