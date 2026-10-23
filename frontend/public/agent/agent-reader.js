/**
 * 绘本阅读器组件 <pb-reader> v2.1 —— 真实绘本版式
 * ---------------------------------------------------------------
 * 版式参照真实横开本绘本（285×210mm ≈ 4:3）：
 *   · 封面  ：全幅插画 + 底部渐变标题带
 *   · 正文页：全幅插画（4:3）+ 底部米白文字带（图大字少，1~3 行）
 *   · 结尾页：知识回顾 + 延伸思考 + 布鲁姆分层互动测评
 * 单页槽渲染：任意时刻 DOM 中只有当前页，天然杜绝叠影。
 *
 * 宿主项目一行代码嵌入：
 *   <pb-reader></pb-reader>
 *   document.querySelector('pb-reader').load(artifact);
 */
(function () {
  'use strict';
  var PB = window.PB || {};

  var template = document.createElement('template');
  template.innerHTML = `
  <style>
    :host { display: block; width: 100%; }
    * { box-sizing: border-box; margin: 0; padding: 0; }

    .pb-stage {
      position: relative;
      max-width: 860px;
      margin: 0 auto;
      background: #fdfbf7;
      border-radius: 20px;
      border: 1px solid #e7dfd0;
      box-shadow: 0 24px 60px rgba(60, 44, 20, 0.14);
      overflow: hidden;
    }

    .pb-toolbar {
      position: absolute; top: 14px; right: 14px; z-index: 6;
      display: flex; gap: 8px;
    }
    .pb-tool {
      border: 1px solid rgba(226,217,200,.9); background: rgba(255,255,255,0.92);
      border-radius: 10px; padding: 7px 12px; font-size: 13px; color: #6b5d43;
      cursor: pointer; transition: all .18s ease; font-family: inherit;
      display: inline-flex; align-items: center; gap: 5px;
    }
    .pb-tool:hover { background: #fff; transform: translateY(-1px); box-shadow: 0 4px 12px rgba(60,44,20,.12); }
    .pb-tool.on { background: #f59e0b; border-color: #f59e0b; color: #fff; }

    /* 单页槽：一页占满，翻页动画作用于槽 */
    .pb-slot {
      position: relative; overflow: hidden;
      perspective: 1600px;
      touch-action: pan-y;   /* 横向滑动翻页，纵向保留滚动 */
    }
    .pb-page-anim { animation: pbFlipNext .46s cubic-bezier(.22,.9,.36,1); transform-origin: left center; backface-visibility: hidden; }
    .pb-page-anim.dir-prev { animation-name: pbFlipPrev; transform-origin: right center; }
    @keyframes pbFlipNext {
      from { opacity: 0; transform: rotateY(-36deg) translateX(26px); }
      to   { opacity: 1; transform: none; }
    }
    @keyframes pbFlipPrev {
      from { opacity: 0; transform: rotateY(36deg) translateX(-26px); }
      to   { opacity: 1; transform: none; }
    }

    /* 悬浮大箭头（桌面端显著，移动端变小） */
    .pb-edge {
      position: absolute; top: 50%; transform: translateY(-50%); z-index: 5;
      width: 46px; height: 46px; border-radius: 50%;
      border: 1px solid rgba(226,217,200,.9); background: rgba(255,255,255,.88);
      color: #6b5d43; font-size: 22px; line-height: 1; cursor: pointer;
      display: flex; align-items: center; justify-content: center;
      box-shadow: 0 6px 18px rgba(60,44,20,.18);
      transition: all .18s ease; font-family: inherit; padding-bottom: 3px;
    }
    .pb-edge:hover:not(:disabled) { background: #fff; transform: translateY(-50%) scale(1.08); }
    .pb-edge:disabled { opacity: .25; cursor: default; }
    .pb-edge-left { left: 14px; }
    .pb-edge-right { right: 14px; }
    .pb-edge svg { width: 20px; height: 20px; }

    /* 边缘翻页热区的悬浮提示 */
    @media (hover: hover) {
      .pb-slot::before, .pb-slot::after {
        content: "‹"; position: absolute; top: 50%; transform: translateY(-50%);
        z-index: 2; font-size: 30px; color: rgba(63,53,36,.35);
        opacity: 0; transition: opacity .2s; pointer-events: none;
      }
      .pb-slot::before { left: 8px; }
      .pb-slot::after { content: "›"; right: 8px; }
      .pb-stage:hover .pb-slot::before, .pb-stage:hover .pb-slot::after { opacity: 1; }
    }

    .pb-page { display: flex; flex-direction: column; width: 100%; }

    /* ---------- 封面：全幅插画 + 渐变标题带 ---------- */
    .pb-cover { position: relative; background: #efe9dc; }
    .pb-cover .pb-art { aspect-ratio: 4 / 3; }
    .pb-cover .pb-band {
      position: absolute; left: 0; right: 0; bottom: 0;
      padding: 64px 34px 22px;
      background: linear-gradient(180deg, rgba(43,34,18,0) 0%, rgba(43,34,18,.62) 62%);
      text-align: center; color: #fff;
    }
    .pb-cover .pb-badge {
      position: absolute; top: 16px; left: 16px;
      font-size: 12px; letter-spacing: 2px; color: #fff;
      background: rgba(43,34,18,.55); backdrop-filter: blur(4px);
      padding: 5px 14px; border-radius: 999px;
    }
    .pb-cover h1 { font-size: 34px; letter-spacing: 3px; text-shadow: 0 2px 10px rgba(0,0,0,.35); font-family: "Yuanti SC","YouYuan","Kaiti SC","KaiTi","Microsoft YaHei",serif; font-weight: 700; }
    .pb-cover .pb-sub { font-size: 14.5px; opacity: .92; margin-top: 6px; }
    .pb-cover .pb-meta { font-size: 12px; opacity: .8; margin-top: 8px; }

    /* ---------- 正文页：全幅插画 + 底部文字带 ---------- */
    .pb-content { position: relative; background: #fff; }
    .pb-art { position: relative; width: 100%; aspect-ratio: 4 / 3; overflow: hidden; background: #efe9dc; }
    .pb-art img, .pb-art svg { width: 100%; height: 100%; object-fit: cover; display: block; }
    .pb-stage-tag {
      position: absolute; top: 14px; left: 14px; z-index: 2;
      font-size: 12px; font-weight: 600; letter-spacing: 1px;
      color: #fff; padding: 5px 13px; border-radius: 999px;
      box-shadow: 0 4px 12px rgba(40,32,16,.25);
    }
    .pb-caption {
      padding: 15px 26px 13px;
      background: #fffdf8; border-top: 1px solid #f0e8d8;
    }
    .pb-caption h2 {
      font-size: 17.5px; color: #3f3524; letter-spacing: 1px; margin-bottom: 6px;
      display: flex; align-items: baseline; justify-content: space-between;
    }
    .pb-caption h2 .pb-pageno { font-size: 12px; color: #b3a482; font-weight: 400; }
    .pb-caption p { font-size: 15px; line-height: 1.9; color: #4a4232; text-align: justify; }

    /* ---------- 留白版式（画幅节奏的"呼吸页"） ---------- */
    .pb-content.framed { background: #faf6ee; padding: 24px 28px 18px; }
    .pb-content.framed .pb-art {
      aspect-ratio: 4 / 2.9; border-radius: 14px;
      border: 1px solid #e4dac6; box-shadow: 0 10px 26px rgba(60,44,20,.10);
    }
    .pb-content.framed .pb-caption {
      background: transparent; border-top: none; padding: 16px 8px 4px; text-align: center;
    }
    .pb-content.framed .pb-caption h2 { justify-content: center; gap: 12px; }
    .pb-content.framed .pb-caption p { text-align: center; }

    /* ---------- 对话气泡（《神奇校车》式角色台词） ---------- */
    .pb-bubble {
      position: absolute; top: 64px; right: 16px; z-index: 3;
      max-width: 52%;
      background: rgba(255,255,255,.96);
      border: 2px solid #3f3524; border-radius: 16px;
      padding: 9px 14px;
      font-family: "Kaiti SC","STKaiti","KaiTi","Yuanti SC","YouYuan",serif;
      font-size: 14.5px; line-height: 1.6; color: #3f3524;
      box-shadow: 0 6px 16px rgba(40,32,16,.18);
    }
    .pb-bubble::after {
      content: ""; position: absolute; left: 26px; bottom: -11px;
      width: 16px; height: 16px; background: rgba(255,255,255,.96);
      border-right: 2px solid #3f3524; border-bottom: 2px solid #3f3524;
      transform: rotate(45deg); border-radius: 0 0 4px 0;
    }
    .pb-bubble.alt { right: auto; left: 16px; }
    .pb-bubble.alt::after { left: auto; right: 26px; }

    /* ---------- 知识便签（学生笔记风） ---------- */
    .pb-fact {
      position: absolute; bottom: 14px; left: 14px; z-index: 3;
      max-width: 58%;
      background: #fff9d9;
      border: 1px solid #e8d48a; border-radius: 4px;
      padding: 8px 12px 7px;
      font-family: "Kaiti SC","STKaiti","KaiTi","Yuanti SC","YouYuan",serif;
      font-size: 12.5px; line-height: 1.65; color: #6b5220;
      box-shadow: 0 5px 14px rgba(60,44,20,.15);
      transform: rotate(-1.2deg);
    }
    .pb-fact::before {
      content: ""; position: absolute; top: -7px; left: 50%; margin-left: -22px;
      width: 44px; height: 13px;
      background: rgba(200,190,160,.5);
      transform: rotate(1deg); border-radius: 2px;
    }
    .pb-fact b { font-weight: 700; color: #8a6a1c; }

    /* ---------- 扉页 ---------- */
    .pb-titlepage {
      background: #faf6ee; min-height: 470px;
      display: flex; flex-direction: column; align-items: center; justify-content: center;
      text-align: center; gap: 14px; padding: 40px 30px;
      position: relative;
    }
    .pb-titlepage .ornament { color: #c9b98e; font-size: 15px; letter-spacing: 6px; }
    .pb-tp-art {
      width: 170px; aspect-ratio: 4/3; border-radius: 12px; overflow: hidden;
      border: 1px solid #e4dac6; box-shadow: 0 10px 26px rgba(60,44,20,.16);
    }
    .pb-tp-art img { width: 100%; height: 100%; object-fit: cover; display: block; }
    .pb-titlepage h1 {
      font-family: "Kaiti SC","STKaiti","KaiTi","Yuanti SC","YouYuan",serif;
      font-size: 40px; letter-spacing: 6px; color: #3f3524;
    }
    .pb-titlepage .pb-sub { font-size: 15px; color: #8a7b5e; letter-spacing: 2px; }
    .pb-titlepage .pb-mascot-chip {
      display: inline-flex; align-items: center; gap: 9px;
      background: #fff; border: 1px solid #e4dac6; border-radius: 999px;
      padding: 8px 18px; margin-top: 8px;
      font-family: "Kaiti SC","STKaiti","KaiTi",serif;
      font-size: 14px; color: #6b5d43;
    }
    .pb-titlepage .pb-mascot-chip .avatar {
      width: 30px; height: 30px; border-radius: 50%;
      background: linear-gradient(135deg, #f59e0b, #f97316);
      color: #fff; display: inline-flex; align-items: center; justify-content: center;
      font-size: 15px; font-weight: 700;
    }
    .pb-titlepage .pb-colophon {
      position: absolute; bottom: 16px; left: 0; right: 0;
      font-size: 11px; color: #b3a482; letter-spacing: 1.5px;
    }

    /* 绘本故事字体（正文与标题） */
    .pb-caption p, .pb-caption h2 {
      font-family: "Kaiti SC","STKaiti","KaiTi","Yuanti SC","YouYuan","Microsoft YaHei",serif;
    }
    .pb-caption h2 { font-weight: 700; }

    /* ---------- 结尾页 ---------- */
    .pb-ending { padding: 30px 30px 22px; background: #fdfbf7; min-height: 420px; }
    .pb-ending h2 { text-align: center; font-size: 25px; margin-bottom: 14px; }
    .pb-recap {
      background: #fff; border: 1px solid #eee4d0; border-radius: 14px;
      padding: 14px 18px; font-size: 14.5px; line-height: 1.9; color: #4a4232; margin-bottom: 12px;
    }
    .pb-recap strong { color: #3f3524; }
    .pb-think {
      background: linear-gradient(135deg, #fff7e8, #fdf0d5);
      border: 1px solid #f2ddb0; border-radius: 14px;
      padding: 14px 18px; font-size: 14.5px; line-height: 1.9; color: #6b5220; margin-bottom: 16px;
    }
    .pb-think strong { display: block; margin-bottom: 5px; font-size: 13px; letter-spacing: 1px; }

    .pb-quiz-head { font-size: 15px; font-weight: 700; color: #3f3524; margin: 4px 0 10px; }
    .pb-quiz-item { background: #fff; border: 1px solid #eee4d0; border-radius: 14px; padding: 13px 16px; margin-bottom: 11px; }
    .pb-q-title { font-size: 14px; color: #4a4232; margin-bottom: 9px; line-height: 1.7; }
    .pb-q-title .pb-bloom {
      display: inline-block; font-size: 11px; color: #7c6c4d; background: #f4efe2;
      border-radius: 6px; padding: 1px 8px; margin-left: 8px; vertical-align: 1px;
    }
    .pb-opts { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
    .pb-opt {
      text-align: left; font-size: 13.5px; padding: 9px 12px; border-radius: 10px;
      border: 1.5px solid #e8dfcd; background: #fbf8f1; color: #55492f; cursor: pointer;
      transition: all .15s ease; font-family: inherit; line-height: 1.5;
    }
    .pb-opt:hover { border-color: #d4c5a0; background: #fff; }
    .pb-opt.correct { border-color: #10b981; background: #ecfdf5; color: #065f46; }
    .pb-opt.wrong { border-color: #f87171; background: #fef2f2; color: #991b1b; }
    .pb-opt:disabled { cursor: default; opacity: .9; }
    .pb-explain { margin-top: 8px; font-size: 12.5px; color: #8a7b5e; line-height: 1.6; }
    .pb-quiz-score {
      text-align: center; font-size: 15px; font-weight: 700; color: #10b981;
      background: #ecfdf5; border-radius: 12px; padding: 10px; margin-bottom: 10px;
    }

    /* ---------- 控制条 ---------- */
    .pb-controls {
      display: flex; align-items: center; justify-content: center; gap: 16px;
      padding: 13px; border-top: 1px solid #ece3d0; background: rgba(255,255,255,.75);
    }
    .pb-nav {
      font-family: inherit; font-size: 14px; padding: 9px 20px; border-radius: 12px;
      border: 1px solid #e2d9c8; background: #fff; color: #55492f; cursor: pointer; transition: all .18s;
    }
    .pb-nav:hover:not(:disabled) { box-shadow: 0 4px 14px rgba(60,44,20,.14); transform: translateY(-1px); }
    .pb-nav:disabled { opacity: .4; cursor: default; }
    .pb-nav.primary { background: #f59e0b; border-color: #f59e0b; color: #fff; font-weight: 600; }
    .pb-dots { display: flex; gap: 7px; }
    .pb-dot { width: 8px; height: 8px; border-radius: 50%; background: #ddd2b8; transition: all .2s; cursor: pointer; }
    .pb-dot.on { background: #f59e0b; width: 22px; border-radius: 6px; }
    .pb-counter { font-size: 13px; color: #9a8a68; min-width: 52px; text-align: center; }

    .pb-empty { padding: 80px 20px; text-align: center; color: #b3a482; font-size: 14px; }

    @media (max-width: 640px) {
      .pb-cover h1 { font-size: 26px; }
      .pb-opts { grid-template-columns: 1fr; }
      .pb-caption p { font-size: 14px; }
      .pb-ending { padding: 24px 18px 18px; }
    }
  </style>
  <div class="pb-stage" part="stage">
    <div class="pb-toolbar">
      <button class="pb-tool" data-act="tts" title="朗读本页">🔊 朗读</button>
      <button class="pb-tool" data-act="auto" title="自动朗读翻页">🎵 跟读</button>
      <button class="pb-tool" data-act="print" title="导出 PDF">🖨 导出</button>
      <button class="pb-tool" data-act="close" title="关闭绘本">✕</button>
    </div>
    <div class="pb-slot"><div class="pb-empty">📖 绘本将在这里呈现</div></div>
    <button class="pb-edge pb-edge-left" data-act="prev" aria-label="上一页">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M15 18l-6-6 6-6"/></svg>
    </button>
    <button class="pb-edge pb-edge-right" data-act="next" aria-label="下一页">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M9 6l6 6-6 6"/></svg>
    </button>
    <div class="pb-controls">
      <button class="pb-nav" data-act="prev">← 上一页</button>
      <div class="pb-dots"></div>
      <span class="pb-counter">1 / 1</span>
      <button class="pb-nav primary" data-act="next">下一页 →</button>
    </div>
  </div>`;

  var PrintStyle = document.createElement('style');
  PrintStyle.textContent = `
    @media print {
      body > *:not(.pb-print-root) { display: none !important; }
      .pb-print-root { display: block !important; }
      .pb-print-page { page-break-after: always; padding: 20px 6px; }
      .pb-print-page h2 { font-size: 18pt; margin-bottom: 8pt; }
      .pb-print-page img { width: 78%; aspect-ratio: 4/3; object-fit: cover; border-radius: 8pt; display: block; margin: 0 auto 10pt; }
      .pb-print-page p { font-size: 12.5pt; line-height: 1.9; color: #222; text-align: justify; }
      .pb-print-cover { text-align: center; padding-top: 90px; }
      .pb-print-cover h1 { font-size: 30pt; margin: 12pt 0 6pt; }
      .pb-print-cover img { width: 62%; }
    }
    .pb-print-root { display: none; }
  `;

  class PbReader extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: 'open' });
      this.shadowRoot.appendChild(template.content.cloneNode(true));
      document.head.appendChild(PrintStyle);
      this.artifact = null;
      this.pages = [];
      this.index = 0;
      this.autoRead = false;
      this._answers = {};     // 测评作答状态跨页保留
      this._animToken = 0;
      this._drag = null;
      this._bindEvents();
    }

    disconnectedCallback() { PB.Narrator && PB.Narrator.stop(); }

    _bindEvents() {
      this.shadowRoot.addEventListener('click', (e) => {
        var opt = e.target.closest('.pb-opt');
        if (opt) { this._pickOption(opt); return; }
        var btn = e.target.closest('[data-act]');
        if (!btn) return;
        var act = btn.dataset.act;
        if (act === 'prev') this.prev();
        if (act === 'next') this.next();
        if (act === 'tts') this._toggleTTS(btn);
        if (act === 'auto') { this.autoRead = !this.autoRead; btn.classList.toggle('on', this.autoRead); if (this.autoRead) this._readCurrent(); }
        if (act === 'close') this._emit('pb:close');
        if (act === 'print') this._exportPDF();
      });

      // 手势滑动 + 跟手拖拽（触摸和鼠标通用）；交互元素上不启动
      var stage = this.shadowRoot.querySelector('.pb-stage');
      var INTERACTIVE = 'button, .pb-opt, a, input, select, textarea';
      stage.addEventListener('pointerdown', (e) => {
        if (!this.pages.length || e.target.closest(INTERACTIVE)) return;
        this._drag = { x: e.clientX, y: e.clientY, moved: false };
      });
      stage.addEventListener('pointermove', (e) => {
        if (!this._drag) return;
        var dx = e.clientX - this._drag.x;
        if (Math.abs(dx) > 8) this._drag.moved = true;
        if (this._drag.moved) {
          var slot = this.shadowRoot.querySelector('.pb-slot');
          slot.style.transition = 'none';
          slot.style.transform = 'translateX(' + (dx * 0.16) + 'px)';
        }
      });
      stage.addEventListener('pointerup', (e) => {
        if (!this._drag) return;
        var dx = e.clientX - this._drag.x, dy = e.clientY - this._drag.y;
        var moved = this._drag.moved;
        this._drag = null;
        var slot = this.shadowRoot.querySelector('.pb-slot');
        slot.style.transition = ''; slot.style.transform = '';
        if (moved && Math.abs(dx) > 55 && Math.abs(dx) > Math.abs(dy) * 1.4) {
          if (dx < 0) this.next(); else this.prev();
          return;
        }
        // 非拖拽的轻点：页面左右 1/3 热区翻页（正文区，排除文字与浮层）
        if (!moved) {
          if (e.target.closest(INTERACTIVE + ', .pb-caption, .pb-bubble, .pb-fact, .pb-stage-tag, .pb-ending')) return;
          var rect = slot.getBoundingClientRect();
          var rel = (e.clientX - rect.left) / Math.max(1, rect.width);
          if (rel > 0.66) this.next();
          else if (rel < 0.34) this.prev();
        }
      });
      stage.addEventListener('pointercancel', () => {
        this._drag = null;
        var slot = this.shadowRoot.querySelector('.pb-slot');
        slot.style.transition = ''; slot.style.transform = '';
      });

      document.addEventListener('keydown', (e) => {
        if (!this.pages.length || !this.offsetParent) return;
        var t = e.target;
        if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT')) return;
        if (e.key === 'ArrowRight' || e.key === 'PageDown' || e.key === ' ') { e.preventDefault(); this.next(); }
        else if (e.key === 'ArrowLeft' || e.key === 'PageUp') { e.preventDefault(); this.prev(); }
        else if (e.key === 'Home') { e.preventDefault(); this._show(0, -1); }
        else if (e.key === 'End') { e.preventDefault(); this._show(this.pages.length - 1, 1); }
      });
    }

    _emit(name, detail) { this.dispatchEvent(new CustomEvent(name, { detail: detail, bubbles: true, composed: true })); }

    /** 载入绘本产物 */
    load(artifact) {
      if (!artifact || !Array.isArray(artifact.pages)) return;
      this.artifact = artifact;
      this.index = 0;
      this._answers = {};
      this.pages = this._buildPages(artifact);
      var dots = this.shadowRoot.querySelector('.pb-dots');
      dots.innerHTML = this.pages.map((_, i) => '<span class="pb-dot" data-i="' + i + '"></span>').join('');
      this._show(0);
    }

    _buildPages(artifact) {
      var list = [{ type: 'cover', data: artifact }];
      list.push({ type: 'titlepage', data: artifact });   // 扉页：真实绘本的书籍结构
      artifact.pages.forEach((p, i) => list.push({ type: 'content', data: p, pageNumber: i + 1 }));
      list.push({ type: 'ending', data: artifact });
      return list;
    }

    _show(i, dir) {
      if (!this.pages.length || !this.pages[i]) return;
      PB.Narrator.stop();
      var oldIdx = this.index;
      var direction = dir != null ? dir : (i >= oldIdx ? 1 : -1);
      this.index = i;
      var page = this.pages[i];
      var slot = this.shadowRoot.querySelector('.pb-slot');

      // 单页槽渲染 + 方向感知的翻页动画
      this._animToken++;
      var token = this._animToken;
      slot.innerHTML = '<div class="pb-page pb-page-anim' + (direction < 0 ? ' dir-prev' : '') + '">' + this._pageHTML(page) + '</div>';
      if (page.type === 'ending') this._restoreQuizState();

      var dots = this.shadowRoot.querySelectorAll('.pb-dot');
      dots.forEach((d, idx) => d.classList.toggle('on', idx === i));
      this.shadowRoot.querySelector('.pb-counter').textContent = (i + 1) + ' / ' + this.pages.length;
      this.shadowRoot.querySelector('[data-act="prev"]').disabled = i === 0;
      this.shadowRoot.querySelector('.pb-edge-left').disabled = i === 0;
      var nextBtn = this.shadowRoot.querySelector('[data-act="next"]');
      var edgeRight = this.shadowRoot.querySelector('.pb-edge-right');
      nextBtn.disabled = i === this.pages.length - 1;
      edgeRight.disabled = i === this.pages.length - 1;
      nextBtn.textContent = i === this.pages.length - 1 ? '回到封面 🔄' : '下一页 →';

      if (this.autoRead) setTimeout(() => { if (token === this._animToken) this._readCurrent(); }, 420);
    }

    _pageHTML(page) {
      if (page.type === 'cover') return this._coverHTML(page);
      if (page.type === 'titlepage') return this._titlePageHTML(page);
      if (page.type === 'content') return this._contentHTML(page);
      return this._endingHTML(page);
    }

    /* 扉页（安静的文字页，真实绘本的呼吸结构） */
    _titlePageHTML(page) {
      var a = page.data;
      var subjectText = { biology: '生命科学', chemistry: '化学', physics: '物理', math: '数学', history: '历史', geography: '地理', culture: '人文', tech: '科技', general: '综合' }[a.subject] || '综合';
      return '<div class="pb-titlepage">' +
        (a.titlepage_image
          ? '<div class="pb-tp-art"><img src="' + a.titlepage_image + '" alt=""></div>'
          : (a.cover_image ? '<div class="pb-tp-art"><img src="' + a.cover_image + '" alt=""></div>' : '')) +
        '<div class="ornament">❋ ❋ ❋</div>' +
        '<h1>' + a.title + '</h1>' +
        '<p class="pb-sub">' + (a.subtitle || '') + '</p>' +
        '<span class="pb-mascot-chip"><span class="avatar">' + (a.mascot && a.mascot.name ? a.mascot.name.slice(0, 1) : '问') + '</span>' +
        (a.mascot && a.mascot.name || '小问号') + ' 为你讲述</span>' +
        '<p class="pb-sub" style="margin-top:10px;">' + (a.grade_text || '') + ' · ' + subjectText + ' · 5E 教学模式</p>' +
        '<div class="pb-colophon">视觉绘本教研 · 插画 联合创作</div>' +
        '</div>';
    }

    _coverHTML(page) {
      var a = page.data;
      var art = a.cover_image
        ? '<img src="' + a.cover_image + '" alt="封面">'
        : PB.generateFallbackSVG({ title: a.title }, a.subject, a.title);
      var aiCount = (a.stats && a.stats.ai_images || 0) + (a.cover_image ? 1 : 0);
      var total = (a.pages ? a.pages.length : 0) + 1;
      var artLabel = aiCount > 0 ? 'AI 插画 ' + aiCount + '/' + total : '矢量插画';
      return '<div class="pb-cover">' +
        '<div class="pb-art">' + art + '</div>' +
        '<span class="pb-badge">' + (a.grade_text || '') + ' · 教学绘本</span>' +
        '<div class="pb-band">' +
          '<h1>' + a.title + '</h1>' +
          '<p class="pb-sub">' + (a.subtitle || '') + '</p>' +
          '<p class="pb-meta">主角：' + (a.mascot && a.mascot.name || '小问号') + '　·　' + (a.pages ? a.pages.length : 0) + ' 页　·　' + artLabel + '</p>' +
        '</div></div>';
    }

    _contentHTML(page) {
      var d = page.data;
      var colors = (PB.CATEGORY_COLORS && PB.CATEGORY_COLORS[this.artifact.subject]) || PB.CATEGORY_COLORS.general;
      var img = d.image_url
        ? '<img src="' + d.image_url + '" alt="' + d.title + '">'
        : PB.generateFallbackSVG(d, this.artifact.subject, d.title);

      // 画幅节奏：引入/迁移 = 满版出血页；探究/讲解 = 留白呼吸页（《野兽国》式节拍）
      var framed = d.stage === 'explore' || d.stage === 'explain';
      var bubbleAlt = page.pageNumber % 2 === 0;   // 气泡左右交替，版面更有节奏

      // 角色台词气泡（把"作者讲述"变成"角色亲历"）
      var bubble = d.line
        ? '<div class="pb-bubble' + (bubbleAlt ? ' alt' : '') + '">“' + d.line + '”</div>'
        : '';
      // 知识便签
      var fact = d.fact
        ? '<div class="pb-fact"><b>✎ 笔记</b>　' + d.fact + '</div>'
        : '';

      return '<div class="pb-content' + (framed ? ' framed' : '') + '">' +
        '<div class="pb-art">' +
          img +
          '<span class="pb-stage-tag" style="background:' + colors.primary + '">' + (d.stage_label || '') + '</span>' +
          bubble +
          fact +
        '</div>' +
        '<div class="pb-caption">' +
          '<h2>' + d.title + '<span class="pb-pageno">第 ' + page.pageNumber + ' 页</span></h2>' +
          '<p>' + d.content + '</p>' +
        '</div></div>';
    }

    _endingHTML(page) {
      var a = page.data;
      var quizHTML = '';
      if (a.quiz && a.quiz.length) {
        quizHTML = '<div class="pb-quiz-head">🧩 学一学 · 测一测（' + a.quiz.length + ' 题）</div>' +
          a.quiz.map((q, qi) =>
            '<div class="pb-quiz-item" data-qi="' + qi + '">' +
            '<div class="pb-q-title">' + (qi + 1) + '. ' + q.question + '<span class="pb-bloom">' + q.bloom + '</span></div>' +
            '<div class="pb-opts">' + q.options.map((opt, oi) =>
              '<button class="pb-opt" data-qi="' + qi + '" data-oi="' + oi + '">' + 'ABCD'[oi] + '. ' + opt + '</button>').join('') +
            '</div><div class="pb-explain" hidden></div></div>').join('');
      }
      return '<div class="pb-ending">' +
        '<h2>🎉 读完了！</h2>' +
        '<div class="pb-recap"><strong>知识回顾：</strong>' + (a.summary || '') + '</div>' +
        (a.thinking_question ? '<div class="pb-think"><strong>💡 延伸思考</strong>' + a.thinking_question + '</div>' : '') +
        quizHTML + '</div>';
    }

    /* ---------- 测评 ---------- */
    // 翻页返回测评页时，恢复已作答状态（单页槽重渲染会清空 DOM 状态）
    _restoreQuizState() {
      var self = this;
      var answered = Object.keys(this._answers || {});
      if (!answered.length || !this.artifact) return;
      answered.forEach(function (qi) {
        var oi = self._answers[qi];
        var quiz = self.artifact.quiz[qi];
        var item = self.shadowRoot.querySelector('.pb-quiz-item[data-qi="' + qi + '"]');
        if (!item || !quiz) return;
        item.dataset.done = '1';
        item.querySelectorAll('.pb-opt').forEach(function (o) {
          o.disabled = true;
          if (Number(o.dataset.oi) === quiz.answer) o.classList.add('correct');
          else if (Number(o.dataset.oi) === oi) o.classList.add('wrong');
        });
        var exp = item.querySelector('.pb-explain');
        if (exp) {
          exp.hidden = false;
          exp.textContent = (oi === quiz.answer ? '✅ 答对了！' : '❌ 正确答案是 ' + 'ABCD'[quiz.answer] + '。') + (quiz.explanation || '');
        }
      });
      // 恢复总分条
      if (Object.keys(this._answers).length === this.artifact.quiz.length) {
        var score = Object.keys(this._answers).reduce(function (acc, k) {
          return acc + (self._answers[k] === self.artifact.quiz[k].answer ? 1 : 0);
        }, 0);
        var total = this.artifact.quiz.length;
        var head = this.shadowRoot.querySelector('.pb-quiz-head');
        if (head && !this.shadowRoot.querySelector('.pb-quiz-score')) {
          var scoreBar = document.createElement('div');
          scoreBar.className = 'pb-quiz-score';
          scoreBar.textContent = '本册测评：' + score + ' / ' + total + ' 题 · 答对率 ' + Math.round(score / total * 100) + '%';
          head.parentNode.insertBefore(scoreBar, head);
        }
      }
    }

    _pickOption(btn) {
      var qi = Number(btn.dataset.qi), oi = Number(btn.dataset.oi);
      var quiz = this.artifact.quiz[qi];
      if (!quiz || this._answers[qi] != null) return;
      this._answers[qi] = oi;

      var item = this.shadowRoot.querySelector('.pb-quiz-item[data-qi="' + qi + '"]');
      var opts = item.querySelectorAll('.pb-opt');
      opts.forEach((o) => {
        o.disabled = true;
        if (Number(o.dataset.oi) === quiz.answer) o.classList.add('correct');
      });
      if (oi !== quiz.answer) btn.classList.add('wrong');
      var exp = item.querySelector('.pb-explain');
      exp.hidden = false;
      exp.textContent = (oi === quiz.answer ? '✅ 答对了！' : '❌ 正确答案是 ' + 'ABCD'[quiz.answer] + '。') + (quiz.explanation || '');

      if (Object.keys(this._answers).length === this.artifact.quiz.length) {
        var score = Object.keys(this._answers).reduce((acc, k) => acc + (this._answers[k] === this.artifact.quiz[k].answer ? 1 : 0), 0);
        var total = this.artifact.quiz.length;
        var head = this.shadowRoot.querySelector('.pb-quiz-head');
        var scoreBar = document.createElement('div');
        scoreBar.className = 'pb-quiz-score';
        scoreBar.textContent = '本册测评：' + score + ' / ' + total + ' 题 · 答对率 ' + Math.round(score / total * 100) + '%';
        head.parentNode.insertBefore(scoreBar, head);
        PB.History.add({
          topic: this.artifact.topic, title: this.artifact.title,
          score: score, total: total, date: Date.now()
        });
        this._emit('pb:quiz-done', { score: score, total: total });
      }
    }

    /* ---------- 翻页 ---------- */
    next() {
      if (this.index >= this.pages.length - 1) this._show(0, 1);
      else this._show(this.index + 1, 1);
    }
    prev() { if (this.index > 0) this._show(this.index - 1, -1); }

    _currentPageText() {
      var page = this.pages[this.index];
      if (!page) return '';
      if (page.type === 'cover') return '现在开始阅读绘本《' + page.data.title + '》';
      if (page.type === 'content') {
        var d = page.data;
        var speaker = (this.artifact.mascot && this.artifact.mascot.name) || '主角';
        return d.title + '。' + (d.line ? speaker + '说：' + d.line + '。' : '') + d.content;
      }
      return '本册小结：' + (page.data.summary || '') + '延伸思考：' + (page.data.thinking_question || '');
    }

    _toggleTTS(btn) {
      if (PB.Narrator.speaking) { PB.Narrator.stop(); btn.classList.remove('on'); btn.textContent = '🔊 朗读'; return; }
      btn.classList.add('on'); btn.textContent = '⏸ 停止';
      PB.Narrator.speak(this._currentPageText(), () => {
        btn.classList.remove('on'); btn.textContent = '🔊 朗读';
      });
    }

    _readCurrent() {
      var ttsBtn = this.shadowRoot.querySelector('[data-act="tts"]');
      if (ttsBtn) { ttsBtn.classList.add('on'); ttsBtn.textContent = '⏸ 停止'; }
      PB.Narrator.speak(this._currentPageText(), () => {
        if (ttsBtn) { ttsBtn.classList.remove('on'); ttsBtn.textContent = '🔊 朗读'; }
        if (this.autoRead && this.index < this.pages.length - 1) this.next();
      });
    }

    /** 导出 PDF：构建打印专用 DOM 后调用系统打印 */
    _exportPDF() {
      if (!this.artifact) return;
      var a = this.artifact;
      var root = document.createElement('div');
      root.className = 'pb-print-root';
      var cover = '<div class="pb-print-page pb-print-cover">' +
        '<p style="letter-spacing:4px;color:#888;">' + (a.grade_text || '') + '教学绘本</p>' +
        '<h1>' + a.title + '</h1><p>' + (a.subtitle || '') + '</p>' +
        (a.cover_image ? '<img src="' + a.cover_image + '">' : '') + '</div>';
      var body = a.pages.map((p) =>
        '<div class="pb-print-page"><h2>第' + p.index + '页 · ' + p.title + '（' + p.stage_label + '）</h2>' +
        (p.image_url ? '<img src="' + p.image_url + '">' : '') +
        (p.line ? '<p style="text-align:center;color:#555;">“' + p.line + '” —— ' + (a.mascot && a.mascot.name || '') + '</p>' : '') +
        '<p>' + p.content + '</p>' +
        (p.fact ? '<p style="font-size:10.5pt;color:#777;">✎ 笔记：' + p.fact + '</p>' : '') + '</div>').join('');
      var quiz = (a.quiz && a.quiz.length)
        ? '<div class="pb-print-page"><h2>测一测</h2>' + a.quiz.map((q, qi) =>
            '<p><strong>' + (qi + 1) + '. ' + q.question + '</strong><br>' +
            q.options.map((o, oi) => (oi === q.answer ? '✔ ' : '') + 'ABCD'[oi] + '. ' + o).join('<br>') +
            '<br><span style="color:#666">解析：' + (q.explanation || '') + '</span></p>').join('') + '</div>'
        : '';
      root.innerHTML = cover + body + quiz;
      document.body.appendChild(root);
      var clean = function () { root.remove(); window.removeEventListener('afterprint', clean); };
      window.addEventListener('afterprint', clean);
      setTimeout(function () { window.print(); }, 60);
    }
  }

  if (!customElements.get('pb-reader')) customElements.define('pb-reader', PbReader);
})();
