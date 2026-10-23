/**
 * 绘本智能体核心 PictureBookAgent v2.0
 * ---------------------------------------------------------------
 * 多智能体流水线：
 *   ① DARYA 教研（GLM-4）  —— 按 5E 教学模式生成任意主题的分页脚本 + 认知分层测评题
 *   ② DARYA 插画（GLM-Image）—— 固定主角 + 统一画风 token，保证跨页角色一致性
 *   ③ DARYA 审编（本地规则）—— JSON 校验 / 内容质检 / 插画提示词安全过滤 / 降级决策
 *
 * 对外契约（兼容"柿子智学"工作台协议）：
 *   - new PictureBookAgent(config).generate(request, handlers) → Promise<artifact>
 *   - artifact 结构与主项目 /api/chat/artifacts 的 artifact 对齐（kind: 'storybook'）
 *   - PB.detectStorybookIntent(text) → 与 AIWorkspaceAgents.detectTaskIntent 同构
 *
 * 本文件零依赖，可在任意宿主页面直接 <script> 引入。
 */
(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.PB = api;
}(typeof window !== 'undefined' ? window : globalThis, function () {
  'use strict';

  /* ================================================================
   * 常量配置
   * ================================================================ */
  var CHAT_URL = 'https://open.bigmodel.cn/api/paas/v4/chat/completions';
  var IMAGE_URL = 'https://open.bigmodel.cn/api/paas/v4/images/generations';
  var KEY_STORAGE = 'glm_api_key';          // 与旧版应用共用，已保存过 key 的用户无需重填
  var IMG_CACHE_STORAGE = 'pb_agent_image_cache';
  var HISTORY_STORAGE = 'pb_agent_history';

  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

  var GRADE_TEXT = { elementary: '小学', middle: '初中', high: '高中' };

  var STAGE_LABEL = {
    engage: '情境引入', explore: '探究观察', explain: '原理讲解',
    elaborate: '迁移应用', evaluate: '评价反思', general: '知识讲解'
  };

  // 画风 → 绘图风格 token（DARYA 插画的"一致性基因"）
  // 每本书选定后全书逐字复用：媒介、笔触、光线、背景全部钉死，最大程度锚定画风
  var STYLE_TOKENS = {
    minimal: '媒介为数字平涂矢量插画，无描边，大色块几何造型，无纹理，均匀柔光，浅色纯净背景，角色为二头身',
    colorful: '媒介为水彩质感数字插画，细腻纸纹，无描边，圆润软萌造型，柔和均匀的晨光，浅色渐变背景，角色为二头身',
    scientific: '媒介为精细科学图解插画，细钢笔线稿加淡彩上色，结构清晰，均匀白光，浅米色背景，角色为二头身',
    'hand-drawn': '媒介为温柔手绘水彩，颗粒纸纹，柔和晕染边缘，无描边，午后暖光，奶油色背景，角色为二头身'
  };

  // 学段默认配色（封面取色不可用时的统一回退，保证全书措辞一致）
  var DEFAULT_PALETTES = {
    biology: '全书统一主色调为草绿、鹅黄与天蓝',
    chemistry: '全书统一主色调为紫罗兰、粉紫与浅灰白',
    physics: '全书统一主色调为天蓝、靛蓝与银白',
    math: '全书统一主色调为橙黄、奶黄与浅棕',
    history: '全书统一主色调为砖红、赭石与米白',
    geography: '全书统一主色调为青蓝、湖绿与沙色',
    culture: '全书统一主色调为玫粉、桃红与米白',
    tech: '全书统一主色调为靛蓝、紫罗兰与银白',
    general: '全书统一主色调为靛蓝、鹅黄与天蓝'
  };

  var CATEGORY_COLORS = {
    biology: { primary: '#10b981', bg: '#d1fae5' },
    chemistry: { primary: '#8b5cf6', bg: '#ede9fe' },
    physics: { primary: '#3b82f6', bg: '#dbeafe' },
    math: { primary: '#f59e0b', bg: '#fef3c7' },
    history: { primary: '#b45309', bg: '#fef3c7' },
    geography: { primary: '#06b6d4', bg: '#cffafe' },
    culture: { primary: '#ec4899', bg: '#fce7f3' },
    tech: { primary: '#6366f1', bg: '#e0e7ff' },
    general: { primary: '#6366f1', bg: '#e0e7ff' }
  };

  /* ================================================================
   * 工具函数
   * ================================================================ */
  function uid(prefix) {
    return (prefix || 'pb') + '_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2, 8);
  }

  function clamp(n, min, max) { return Math.min(max, Math.max(min, n)); }

  function safeJSONParse(text) {
    if (!text) return null;
    var s = String(text).trim();
    s = s.replace(/^```(?:json)?\s*/i, '').replace(/```\s*$/i, '');
    var start = s.indexOf('{');
    var end = s.lastIndexOf('}');
    if (start === -1 || end === -1 || end <= start) return null;
    s = s.slice(start, end + 1);
    try { return JSON.parse(s); } catch (e) { return null; }
  }

  function storageGet(key, fallback) {
    try { var v = localStorage.getItem(key); return v == null ? fallback : JSON.parse(v); }
    catch (e) { return fallback; }
  }
  function storageSet(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* 隐私模式忽略 */ }
  }

  // 字符串哈希（缓存 key 用）：提示词有很长的公共前缀，绝不能按前缀截断做 key
  function hashStr(s) {
    var h = 5381;
    for (var i = 0; i < s.length; i++) {
      h = ((h << 5) + h) + s.charCodeAt(i);
      h |= 0;
    }
    return (h >>> 0).toString(36) + '_' + s.length;
  }

  /* ================================================================
   * 画风质检 StyleGuard（DARYA 审编的像素级检测器）
   * ------------------------------------------------------------
   * 通过本地图片代理拉取生成图 → 缩小画布 → 计算画风指纹：
   *   色相直方图（按饱和度加权）+ 平均饱和度 + 平均明度 + 边缘密度
   * 与封面基准比对，偏差超阈值的页面自动以强化统一提示词重绘一次。
   * 代理不可用时自动降级：跳过像素检测，仅靠风格基因措辞统一。
   * ================================================================ */
  var HUE_NAMES = ['红', '橙', '金黄', '黄绿', '草绿', '青绿', '天蓝', '靛蓝', '蓝紫', '紫', '品红', '玫红'];

  function rgbToHsl(r, g, b) {
    r /= 255; g /= 255; b /= 255;
    var max = Math.max(r, g, b), min = Math.min(r, g, b), h = 0, s = 0;
    var l = (max + min) / 2;
    if (max !== min) {
      var d = max - min;
      s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
      if (max === r) h = ((g - b) / d + (g < b ? 6 : 0));
      else if (max === g) h = (b - r) / d + 2;
      else h = (r - g) / d + 4;
      h *= 60;
    }
    return { h: h, s: s, l: l };
  }

  function computeProfile(data, w, h) {
    var hue = new Array(12).fill(0);
    var satSum = 0, lumSum = 0;
    var gray = new Float32Array(w * h);
    var i, px = 0;
    for (i = 0; i < w * h; i++) {
      var r = data[i * 4], g = data[i * 4 + 1], b = data[i * 4 + 2];
      var hsl = rgbToHsl(r, g, b);
      gray[i] = 0.299 * r + 0.587 * g + 0.114 * b;
      lumSum += hsl.l;
      satSum += hsl.s;
      if (hsl.s > 0.12 && hsl.l > 0.08 && hsl.l < 0.95) {
        hue[Math.min(11, Math.floor(hsl.h / 30))] += hsl.s;
      }
    }
    // 边缘密度（画风笔触强弱的代理指标：水彩晕染低、线稿钢笔高）
    var edge = 0;
    for (var y = 0; y < h - 1; y++) {
      for (var x = 0; x < w - 1; x++) {
        var idx = y * w + x;
        edge += Math.abs(gray[idx] - gray[idx + 1]) + Math.abs(gray[idx] - gray[idx + w]);
      }
    }
    var n = w * h;
    return {
      hue: hue.map(function (v) { return v / n; }),
      hueTotal: hue.reduce(function (a, b) { return a + b; }, 0) / n,
      sat: satSum / n,
      lum: lumSum / n,
      edge: edge / ((w - 1) * h + w * (h - 1))
    };
  }

  var StyleGuard = {
    // 与主项目后端对齐的代理端点；不可用时自动禁用像素检测
    proxyOf: function (config, imageUrl) {
      var base = (config && config.imageProxy) || '/proxy?url=';
      return base + encodeURIComponent(imageUrl);
    },

    analyze: function (config, imageUrl) {
      if (typeof createImageBitmap === 'undefined') return Promise.resolve({ ok: false, reason: 'UNSUPPORTED' });
      // dataURL（联画切片）可直接读取；远程图走代理
      var src = String(imageUrl).indexOf('data:') === 0 ? imageUrl : this.proxyOf(config, imageUrl);
      // 代理端点是宿主自己的受保护接口，需要带上宿主令牌
      var headers = (config && config.authToken) ? { 'Authorization': 'Bearer ' + config.authToken } : undefined;
      return fetch(src, headers ? { headers: headers } : undefined).then(function (r) {
        if (!r.ok) throw new Error('proxy HTTP ' + r.status);
        return r.blob();
      }).then(function (blob) {
        if (!/^image\//.test(blob.type)) throw new Error('not image: ' + blob.type);
        return createImageBitmap(blob);
      }).then(function (bmp) {
        try {
          var w = 64, h = 48;
          var canvas = document.createElement('canvas');
          canvas.width = w; canvas.height = h;
          var ctx = canvas.getContext('2d');
          ctx.drawImage(bmp, 0, 0, w, h);
          var data = ctx.getImageData(0, 0, w, h).data;
          if (bmp.close) bmp.close();
          return { ok: true, profile: computeProfile(data, w, h) };
        } catch (e) {
          if (bmp.close) bmp.close();
          return { ok: false, reason: 'CANVAS' };
        }
      }).catch(function () {
        return { ok: false, reason: 'PROXY_UNAVAILABLE' };
      });
    },

    // 偏差度 0~100，越高越不一致
    // 注意：色相部分变化可能只是场景内容不同（草地页 vs 海滩页），权重适中；
    // 但主色相整体翻转意味着整本书观感断裂，用下限硬性拉高
    deviation: function (a, b) {
      var chi = 0;
      for (var i = 0; i < 12; i++) {
        var s = a.hue[i] + b.hue[i];
        if (s > 0.0001) { var d = a.hue[i] - b.hue[i]; chi += (d * d) / s; }
      }
      var hueDist = Math.min(1, chi / 1.4);
      var satDiff = Math.abs(a.sat - b.sat);
      var lumDiff = Math.abs(a.lum - b.lum);
      var edgeDiff = Math.abs(a.edge - b.edge) / Math.max(0.004, (a.edge + b.edge) / 2);
      var v = hueDist * 0.58 + Math.min(1, satDiff * 2.6) * 0.18 + Math.min(1, lumDiff * 2.4) * 0.13 + Math.min(1, edgeDiff) * 0.11;
      var dev = Math.round(Math.min(100, v * 100));
      return Math.max(dev, Math.round(hueDist * 85));   // 主色相翻转的硬下限
    },

    // 从封面指纹提取主色调命名（回灌到后续页提示词）
    paletteNames: function (profile) {
      var bins = profile.hue.map(function (v, i) { return { v: v, i: i }; })
        .sort(function (a, b) { return b.v - a.v; });
      var names = [];
      for (var k = 0; k < bins.length && names.length < 3; k++) {
        if (bins[k].v <= 0.001) break;
        if (names.indexOf(HUE_NAMES[bins[k].i]) === -1) names.push(HUE_NAMES[bins[k].i]);
      }
      var tone = profile.lum > 0.62 ? '浅色' : (profile.lum < 0.38 ? '深色' : '中明度');
      return '全书统一' + tone + '主色调为' + (names.join('、') || '暖白') + '，低饱和柔和配色';
    }
  };

  /* ================================================================
   * ① DARYA 教研：GLM-4 分页脚本生成
   * ================================================================ */
  // 宿主代理模式下 Authorization 携带的是**宿主会话令牌**（服务端据此鉴权并注入平台 Key）；
  // 直连模式下才是平台 Key 本身。
  function authHeaders(config) {
    var token = (config && config.authToken) || (config && config.apiKey) || '';
    return { 'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json' };
  }

  var LLMClient = {
    chat: function (config, messages, options) {
      options = options || {};
      if (!config.apiKey) {
        return Promise.reject({ code: 'NO_API_KEY', message: '未配置 API Key' });
      }
      var controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
      var timeoutId = controller ? setTimeout(function () { controller.abort(); }, options.timeout || 60000) : null;

      // 宿主可把调用指向自己的同源代理（凭证留在服务端，浏览器不持有 Key）
      return fetch(config.chatUrl || CHAT_URL, {
        method: 'POST',
        headers: authHeaders(config),
        body: JSON.stringify({
          model: config.chatModel || 'glm-4-flash',
          messages: messages,
          temperature: options.temperature != null ? options.temperature : 0.7,
          max_tokens: options.maxTokens || 3000
        }),
        signal: controller ? controller.signal : undefined
      }).then(function (response) {
        if (timeoutId) clearTimeout(timeoutId);
        if (!response.ok) {
          return response.json().catch(function () { return {}; }).then(function (err) {
            throw { code: 'HTTP_' + response.status, message: (err.error && err.error.message) || ('HTTP ' + response.status) };
          });
        }
        return response.json();
      }).then(function (data) {
        var content = data && data.choices && data.choices[0] && data.choices[0].message
          ? data.choices[0].message.content : '';
        if (!content) throw { code: 'EMPTY_RESPONSE', message: '模型返回为空' };
        return content;
      }).catch(function (error) {
        if (timeoutId) clearTimeout(timeoutId);
        if (error && error.name === 'AbortError') throw { code: 'TIMEOUT', message: '教研模型请求超时' };
        throw error && error.code ? error : { code: 'NETWORK_ERROR', message: (error && error.message) || '网络请求失败' };
      });
    }
  };

  function buildDesignMessages(request) {
    var system = [
      '你是一位资深的中小学教研专家兼儿童科普绘本编剧，精通 5E 教学模式、布鲁姆认知目标分类与绘本分镜语言，熟悉《神奇校车》《肚子里有个火车站》等优秀科普绘本的排版语言。',
      '任务：为给定主题创作一本横版教学绘本的分页脚本。要求：',
      '1. 科学性：事实、数字、术语必须准确，没有把握的细节宁可不写，禁止编造；',
      '2. 教学设计：页面依次覆盖 5E 模式的 engage（情境引入）→ explore（探究观察）→ explain（原理讲解）→ elaborate（迁移应用）阶段，最后一页自然收束；',
      '3. 认知适配：小学用具体比喻和短句，初中讲清概念与机制，高中体现学科语言与深层机理；',
      '4. 每页正文必须写满 60~110 字（硬性要求，两到四个完整句子，真实绘本图大字少但每页要有完整的叙述），口语化、有画面感、适合朗读，不要出现 Markdown；',
      '5. 设计一个贯穿全书的卡通主角（吉祥物），正文中主角要像故事人物一样亲历情节，而不是旁白解说；',
      '6. line 是主角在本页说话的台词（10~16字，第一人称，口语化，用感叹或提问的语气，像《神奇校车》里的对白，而不是总结陈词）；',
      '7. fact 是本页的知识便签（18~26字，陈述句，只写本页最核心的一个知识点或数据，像学生笔记本里的一句话）；',
      '8. mascot.appearance 必须是完整的形象规格书，按顺序写全：物种或人种、性别、年龄段、发型与发色、服装及颜色、1个标志性配饰，60字内。禁止只写服装配饰而不写物种——这个描述将逐字用于每一页的AI绘图，任何含糊都会导致主角走形；',
      '9. image_prompt 是该页横版画面的分镜描述，必须与正文情节严格一致：主角正在做正文里写的那件事、地点与正文相符、情绪与情节相符；只写画面需要的元素，禁止出现与情节无关的道具；60字内，不要提到文字或标注。',
      '',
      '仅输出 JSON，不要输出任何解释文字，格式如下：',
      '{',
      '  "title": "书名，不超过10字",',
      '  "subtitle": "副标题，不超过16字",',
      '  "subject": "biology|chemistry|physics|math|history|geography|culture|tech|general 之一",',
      '  "lighting": "全书统一光线与时间设定，20字内（如：晴天上午，光源统一左上方，明亮柔和）",',
      '  "setting": "主要场景设定，30字内（地点+标志性景物，跨页重复出现的地点必须保持同一特征）",',
      '  "props": ["反复出现的道具规格书，各20字内，0~3个（如：蓝色圆框放大镜）"],',
      '  "mascot": { "name": "主角名字", "appearance": "外形描述，60字内，中文，按顺序写全：物种或人种、性别、年龄段、发型与发色、服装及颜色、1个标志性配饰。禁止只写服装配饰而不写物种" },',
      '  "pages": [',
      '    { "stage": "engage|explore|explain|elaborate 之一", "title": "页标题，不超过12字",',
      '      "content": "本页正文",',
      '      "line": "主角台词",',
      '      "fact": "知识便签",',
      '      "image_prompt": "本页横版画面分镜，与正文情节一致" }',
      '  ],',
      '  "quiz": [ 3 道单项选择题，{ "question": "题干", "options": ["A选项","B选项","C选项","D选项"], "answer": 正确项索引0-3, "explanation": "一句话解析", "bloom": "记忆|理解|应用|分析|评价|创造 之一" } ],',
      '  "summary": "全书知识总结，60字内",',
      '  "thinking_question": "一个开放式延伸思考题"',
      '}'
    ].join('\n');

    var user = '主题：' + request.topic +
      '\n学段：' + (GRADE_TEXT[request.difficulty] || '初中') +
      '\n内容页数：' + request.pageCount +
      '\n画风偏好：' + (request.styleName || '儿童绘本') +
      '\n请生成。';

    return [
      { role: 'system', content: system },
      { role: 'user', content: user }
    ];
  }

  /* ================================================================
   * ③ DARYA 审编：结构校验 + 质检规则
   * ================================================================ */
  var Reviewer = {
    // 校验教研产出的结构，缺失字段给出修复或判定失败
    validateStoryboard: function (story, request) {
      if (!story || typeof story !== 'object') return { ok: false, reason: 'JSON 解析失败' };
      if (!Array.isArray(story.pages) || story.pages.length < 2) return { ok: false, reason: '页面数量不足' };
      if (!story.title) story.title = request.topic + '的故事';

      var stages = Object.keys(STAGE_LABEL);
      story.pages = story.pages.slice(0, request.pageCount + 2).map(function (page, i) {
        page.stage = stages.indexOf(page.stage) >= 0 ? page.stage : (i === 0 ? 'engage' : 'explain');
        page.title = String(page.title || ('第' + (i + 1) + '站')).slice(0, 24);
        page.content = String(page.content || '').trim();
        page.line = String(page.line || '').replace(/["“”]/g, '').trim().slice(0, 20);
        page.fact = String(page.fact || '').replace(/["“”]/g, '').trim().slice(0, 30);
        page.image_prompt = String(page.image_prompt || request.topic).slice(0, 120);
        return page;
      }).filter(function (page) { return page.content.length > 10; });

      if (story.pages.length < 2) return { ok: false, reason: '有效页面不足' };

      // 文本量节奏检查（真实绘本图大字少，页间不宜悬殊）
      story._textWarnings = [];
      story.pages.forEach(function (page, i) {
        if (page.content.length < 25 || page.content.length > 200) {
          story._textWarnings.push({ page: i + 1, length: page.content.length });
        }
      });

      story.mascot = story.mascot && story.mascot.name ? story.mascot
        : { name: '小问号', appearance: '一只圆滚滚的橙色小猫头鹰，男孩，头上有一撮呆毛，戴蓝色圆框眼镜，胸前有问号形状的白色羽毛' };
      // 形象规格书过短（缺物种/性别等关键信息）会导致跨页走形，直接替换为完整规格
      if (!story.mascot.appearance || story.mascot.appearance.length < 20) {
        story.mascot.appearance = '一只圆滚滚的橙色小猫头鹰，男孩，头上有一撮呆毛，戴蓝色圆框眼镜，胸前有问号形状的白色羽毛';
      }
      story.mascot.appearance = String(story.mascot.appearance).slice(0, 80);

      // 全书视觉设定（光线/场景/道具），逐字注入每页插画提示词
      story.lighting = String(story.lighting || '').replace(/["“”]/g, '').trim().slice(0, 40);
      story.setting = String(story.setting || '').replace(/["“”]/g, '').trim().slice(0, 50);
      story.props = Array.isArray(story.props)
        ? story.props.map(function (p) { return String(p).replace(/["“”]/g, '').trim().slice(0, 30); })
            .filter(function (p) { return p.length > 2; }).slice(0, 3)
        : [];

      story.quiz = Array.isArray(story.quiz) ? story.quiz.filter(function (q) {
        return q && q.question && Array.isArray(q.options) && q.options.length >= 2 &&
          typeof q.answer === 'number' && q.answer >= 0 && q.answer < q.options.length;
      }).slice(0, 3).map(function (q) {
        return {
          question: String(q.question),
          // 模型偶尔自带 "A." 前缀，统一剥掉，渲染层再补
          options: q.options.map(function (o) { return String(o).replace(/^\s*[A-Da-d][.、．:：]\s*/, ''); }).slice(0, 4),
          answer: q.answer, explanation: String(q.explanation || ''),
          bloom: ['记忆', '理解', '应用', '分析', '评价', '创造'].indexOf(q.bloom) >= 0 ? q.bloom : '理解'
        };
      }) : [];

      story.summary = String(story.summary || '').trim();
      story.thinking_question = String(story.thinking_question || '').trim();
      if (!CATEGORY_COLORS[story.subject]) story.subject = 'general';
      return { ok: true, story: story };
    },

    // 插画提示词安全过滤与统一（DARYA 审编的核心规则）
    // 结构：统一画风 DNA（媒介/笔触/光线/背景，逐字复用）→ 主色调 → 主角 → 情节 → 禁忌
    sanitizeImagePrompt: function (prompt, story, styleToken, reinforced) {
      var s = String(prompt || '');
      var blocked = [
        ['血', ''], ['尸体', ''], ['武器交锋', ''], ['战争场面', '历史场景'],
        ['死亡', ''], ['杀', ''], ['恐怖', ''], ['裸露', '']
      ];
      blocked.forEach(function (pair) { s = s.split(pair[0]).join(pair[1]); });
      s = s.replace(/["“”]/g, '').replace(/\s+/g, ' ').trim();

      var mascot = story.mascot || { appearance: '' };
      var palette = story.palette || '';
      var head = reinforced ? '【最高优先级】严格保持与同系列插画完全一致的画风：' : '统一画风：';
      var full = head + styleToken + (palette ? '，' + palette : '') + '。' +
        (story.lighting ? '全书光线：' + story.lighting + '。' : '') +
        (story.setting ? '场景设定：' + story.setting + '。' : '') +
        (story.props && story.props.length ? '道具规格：' + story.props.join('；') + '。' : '') +
        '主角固定为：' + mascot.appearance + '。每张图中主角的物种、性别、发型、服装必须完全一致，禁止改变。' +
        '横版4:3，三分法构图，主角表情生动夸张，画面一角保留干净纯色区域。' +
        '画面内容：' + s + '。' +
        '只绘制与情节相关的元素，禁止无关道具，主角动作要与情节一致。' +
        '纯插画，禁止任何文字、字母、数字、标注、边框。';
      return full.slice(0, 700);
    }
  };

  /* ================================================================
   * ② DARYA 插画：GLM-Image + 缓存 + SVG 兜底
   * ================================================================ */
  var ImageClient = {
    cacheGet: function (key) {
      var cache = storageGet(IMG_CACHE_STORAGE, {});
      var item = cache[key];
      if (!item) return null;
      if (Date.now() - item.timestamp > 7 * 24 * 60 * 60 * 1000) { delete cache[key]; return null; }
      return item.url;
    },
    cacheSet: function (key, url) {
      var cache = storageGet(IMG_CACHE_STORAGE, {});
      cache[key] = { url: url, timestamp: Date.now() };
      var keys = Object.keys(cache);
      if (keys.length > 60) {
        keys.sort(function (a, b) { return cache[a].timestamp - cache[b].timestamp; })
          .slice(0, keys.length - 60).forEach(function (k) { delete cache[k]; });
      }
      storageSet(IMG_CACHE_STORAGE, cache);
    },

    generate: function (config, prompt, retryCount) {
      retryCount = retryCount || 0;
      // key 必须覆盖完整提示词：风格基因/设定等公共前缀很长，截断会导致不同页面互相命中缓存
      var cacheKey = hashStr((config.imageModel || '') + '|' + (config.imageSize || '') + '|' + prompt);
      var cached = this.cacheGet(cacheKey);
      if (cached) return Promise.resolve({ ok: true, url: cached, cached: true });

      if (!config.apiKey) return Promise.resolve({ ok: false, reason: 'NO_API_KEY' });

      var controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
      var timeoutId = controller ? setTimeout(function () { controller.abort(); }, config.imageTimeout || 90000) : null;

      // 宿主可把调用指向自己的同源代理（凭证留在服务端，浏览器不持有 Key）
      return fetch(config.imageUrl || IMAGE_URL, {
        method: 'POST',
        headers: authHeaders(config),
        body: JSON.stringify({ model: config.imageModel || 'glm-image', prompt: prompt, size: config.imageSize || '1152x864' }),
        signal: controller ? controller.signal : undefined
      }).then(function (response) {
        if (timeoutId) clearTimeout(timeoutId);
        if (!response.ok) return response.json().catch(function () { return {}; }).then(function (err) {
          throw { code: 'HTTP_' + response.status, message: (err.error && err.error.message) || ('HTTP ' + response.status) };
        });
        return response.json();
      }).then(function (data) {
        if (timeoutId) clearTimeout(timeoutId);
        var url = data && data.data && data.data[0] && data.data[0].url;
        if (!url) throw { code: 'INVALID_RESPONSE', message: '图片返回格式异常' };
        ImageClient.cacheSet(cacheKey, url);
        return { ok: true, url: url };
      }).catch(function (error) {
        if (timeoutId) clearTimeout(timeoutId);
        var code = error && error.name === 'AbortError' ? 'TIMEOUT' : (error && error.code) || 'IMAGE_ERROR';
        var message = error && error.message;

        // 瞬时故障（限流/服务端/网络/超时）自动退避重试，最多 2 次
        var transient = code === 'HTTP_429' || code === 'TIMEOUT' ||
          /^HTTP_5/.test(code) || code === 'NETWORK_ERROR' || code === 'IMAGE_ERROR';
        if (transient && retryCount < 2) {
          var wait = code === 'HTTP_429' ? 3500 : 1800;
          return sleep(wait).then(function () {
            return ImageClient.generate(config, prompt, retryCount + 1);
          });
        }
        return { ok: false, reason: code, message: message };
      });
    }
  };

  // SVG 兜底插图（AI 不可用时保持"扁平场景插画"成品感，而非占位图形）
  function generateFallbackSVG(page, subject, title) {
    var c = CATEGORY_COLORS[subject] || CATEGORY_COLORS.general;
    var p = c.primary, bg = c.bg;
    var seed = String((page && page.title) || title || '').split('').reduce(function (a, ch) { return a + ch.charCodeAt(0); }, 0);
    var v = seed % 4;   // 同一本书每页场景不同

    // 天空 / 远山 / 草地 分层场景
    var scene = '<defs><linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">' +
      '<stop offset="0" stop-color="' + bg + '"/><stop offset="1" stop-color="#ffffff"/></linearGradient></defs>' +
      '<rect width="400" height="400" fill="url(#sky)"/>' +
      // 太阳与云
      '<circle cx="66" cy="70" r="26" fill="#fbbf24" opacity="0.9"/>' +
      '<g stroke="#fbbf24" stroke-width="4" stroke-linecap="round" opacity="0.7">' +
      '<path d="M66 32v-10"/><path d="M66 118v-10"/><path d="M28 70H18"/><path d="M114 70h-10"/>' +
      '<path d="M38 42l-7-7"/><path d="M94 98l-7-7"/><path d="M38 98l-7 7"/><path d="M94 42l-7 7"/></g>' +
      '<g fill="#ffffff" opacity="0.85"><ellipse cx="300" cy="76" rx="42" ry="16"/><ellipse cx="330" cy="66" rx="28" ry="13"/></g>' +
      // 远山与丘陵
      '<path d="M-10 260 Q80 190 170 258 T400 250 V410 H-10 Z" fill="' + p + '" opacity="0.28"/>' +
      '<path d="M-10 300 Q120 240 240 296 T410 292 V410 H-10 Z" fill="' + p + '" opacity="0.5"/>' +
      '<rect y="336" width="400" height="64" fill="' + p + '" opacity="0.75"/>';

    // 各页道具（按 stage 变化）
    var props = '';
    if (v === 0) {
      // 引入页：一本摊开的书 + 星光
      props = '<g transform="translate(120,196)"><path d="M0 40 Q40 18 80 40 V78 Q40 58 0 78 Z" fill="#fff" stroke="' + p + '" stroke-width="5" stroke-linejoin="round"/>' +
        '<path d="M80 40 Q120 18 160 40 V78 Q120 58 80 78 Z" fill="#fffdf5" stroke="' + p + '" stroke-width="5" stroke-linejoin="round"/>' +
        '<path d="M80 40 V78" stroke="' + p + '" stroke-width="5"/></g>' +
        '<g fill="#fbbf24"><path d="M96 160l4 10 10 4-10 4-4 10-4-10-10-4 10-4z"/><path d="M312 210l3 8 8 3-8 3-3 8-3-8-8-3 8-3z"/></g>';
    } else if (v === 1) {
      // 探究页：放大镜 + 幼苗
      props = '<g transform="translate(112,190)"><circle cx="70" cy="70" r="52" fill="#ffffff" opacity="0.7" stroke="' + p + '" stroke-width="10"/>' +
        '<path d="M108 108 L146 146" stroke="' + p + '" stroke-width="14" stroke-linecap="round"/></g>' +
        '<g transform="translate(268,268)"><path d="M0 68 C0 34 14 18 0 0 C22 16 26 44 0 68Z" fill="' + p + '" opacity="0.85"/><rect x="-4" y="60" width="8" height="26" rx="4" fill="#8d6e63"/></g>';
    } else if (v === 2) {
      // 原理页：三个相连的知识节点
      props = '<g stroke="' + p + '" stroke-width="7" opacity="0.8"><path d="M120 220 L200 168 L286 216"/><path d="M200 168 L200 258"/></g>' +
        '<g><circle cx="120" cy="220" r="22" fill="#fff" stroke="' + p + '" stroke-width="7"/>' +
        '<circle cx="200" cy="168" r="26" fill="#fff" stroke="' + p + '" stroke-width="7"/>' +
        '<circle cx="286" cy="216" r="22" fill="#fff" stroke="' + p + '" stroke-width="7"/>' +
        '<circle cx="200" cy="258" r="18" fill="' + p + '" opacity="0.6"/></g>';
    } else {
      // 应用页：小房子与树
      props = '<g transform="translate(96,212)"><rect x="0" y="36" width="96" height="70" rx="8" fill="#fff" stroke="' + p + '" stroke-width="6"/>' +
        '<path d="M-10 40 L48 0 L106 40 Z" fill="' + p + '" opacity="0.85"/>' +
        '<rect x="38" y="70" width="24" height="36" rx="4" fill="' + p + '" opacity="0.7"/></g>' +
        '<g transform="translate(268,236)"><rect x="14" y="52" width="12" height="46" rx="5" fill="#8d6e63"/>' +
        '<circle cx="20" cy="36" r="30" fill="' + p + '" opacity="0.8"/><circle cx="0" cy="52" r="18" fill="' + p + '" opacity="0.6"/><circle cx="42" cy="52" r="18" fill="' + p + '" opacity="0.6"/></g>';
    }

    // 主角：圆形小猫头鹰（与 AI 主角气质呼应的扁平版）
    var mascot = '<g transform="translate(292,300)">' +
      '<path d="M-16 -34 L-26 -48 L-6 -42 Z" fill="#f59e0b"/><path d="M16 -34 L26 -48 L6 -42 Z" fill="#f59e0b"/>' +
      '<circle cx="0" cy="0" r="34" fill="#f59e0b"/>' +
      '<ellipse cx="0" cy="8" rx="21" ry="24" fill="#fff7e8"/>' +
      '<circle cx="-12" cy="-8" r="10" fill="#fff"/><circle cx="12" cy="-8" r="10" fill="#fff"/>' +
      '<circle cx="-12" cy="-8" r="4.5" fill="#3f3524"/><circle cx="12" cy="-8" r="4.5" fill="#3f3524"/>' +
      '<path d="M-5 2 L0 9 L5 2 Z" fill="#fb923c"/>' +
      '<path d="M-34 4 Q-44 14 -38 26" stroke="#f59e0b" stroke-width="8" fill="none" stroke-linecap="round"/>' +
      '<path d="M34 4 Q44 14 38 26" stroke="#f59e0b" stroke-width="8" fill="none" stroke-linecap="round"/>' +
      '<path d="M-8 36 h6 M2 36 h6" stroke="#fb923c" stroke-width="4" stroke-linecap="round"/></g>';

    return '<svg viewBox="0 0 400 400" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="' + (title || '') + '">' +
      scene + props + mascot + '</svg>';
  }

  /* ================================================================
   * 朗读（Web Speech API，零成本）
   * ================================================================ */
  var Narrator = {
    speaking: false,
    _voice: null,
    pickVoice: function () {
      if (this._voice || typeof speechSynthesis === 'undefined') return this._voice;
      var voices = speechSynthesis.getVoices() || [];
      this._voice = voices.filter(function (v) { return /zh[-_]CN/i.test(v.lang); })[0] ||
        voices.filter(function (v) { return /zh/i.test(v.lang); })[0] || null;
      return this._voice;
    },
    speak: function (text, onEnd) {
      if (typeof speechSynthesis === 'undefined') { if (onEnd) onEnd(); return; }
      this.stop();
      var utter = new SpeechSynthesisUtterance(text);
      var voice = this.pickVoice();
      if (voice) utter.voice = voice;
      utter.lang = 'zh-CN';
      utter.rate = 0.95;
      utter.pitch = 1.05;
      var self = this;
      utter.onstart = function () { self.speaking = true; };
      utter.onend = function () { self.speaking = false; if (onEnd) onEnd(); };
      utter.onerror = function () { self.speaking = false; };
      speechSynthesis.speak(utter);
    },
    stop: function () {
      if (typeof speechSynthesis !== 'undefined') speechSynthesis.cancel();
      this.speaking = false;
    }
  };

  /* ================================================================
   * 总编排器
   * ================================================================ */
  function PictureBookAgent(config) {
    this.config = Object.assign({
      apiKey: '',
      chatModel: 'glm-4-flash',
      imageModel: 'glm-image',
      imageSize: '1152x864',
      imageLayout: 'grid',        // grid=四格联画一次生成（画风由构造保证一致）；single=逐页生成
      useAIImage: true,
      imageTimeout: 120000,
      imageProxy: '/proxy?url='   // 图片代理（像素级画风质检用），对应 server.py /proxy
    }, config || {});
    // 教研模型不可用时是否退化为本地模板脚本；宿主据此向用户如实说明
    this._storyboardDegraded = false;
    this._degradedReason = '';
  }

  PictureBookAgent.prototype.hasAPIKey = function () { return !!this.config.apiKey; };

  /**
   * 生成一本绘本
   * @param request {topic, difficulty, pageCount, style}
   * @param handlers {onStage(stage, state, detail), onProgress(percent, message)}
   * @returns Promise<artifact>
   */
  PictureBookAgent.prototype.generate = function (request, handlers) {
    var self = this;
    handlers = handlers || {};
    request = Object.assign({ topic: '', difficulty: 'middle', pageCount: 4, style: 'colorful' }, request);

    var emitStage = function (stage, state, detail) {
      if (handlers.onStage) handlers.onStage(stage, state, detail);
    };
    var emitProgress = function (percent, message) {
      if (handlers.onProgress) handlers.onProgress(clamp(Math.round(percent), 0, 100), message);
    };

    emitStage('design', 'running', '正在按 5E 模式设计教学脚本');
    emitProgress(4, 'DARYA 教研：分析主题与学段…');

    return this._designStoryboard(request)
      .then(function (story) {
        var review = Reviewer.validateStoryboard(story, request);
        if (!review.ok && self.config.apiKey) {
          // JSON 修复重试一次
          emitStage('design', 'running', '脚本校验未通过，重新生成中');
          return self._designStoryboard(request, true).then(function (retry) {
            var review2 = Reviewer.validateStoryboard(retry, request);
            if (!review2.ok) throw { code: 'DESIGN_FAILED', message: '教研脚本两次生成均未通过校验：' + review2.reason };
            return review2.story;
          });
        }
        if (!review.ok) throw { code: 'DESIGN_FAILED', message: review.reason };
        return review.story;
      })
      .then(function (story) {
        // 自洽校对二遍稿：术语统一 / 事实互洽 / 称谓统一 / 视角统一 / 难度适配
        emitStage('design', 'running', 'DARYA 教研：术语·事实·视角自洽校对…');
        emitProgress(22, 'DARYA 教研：自洽校对中…');
        return self._consistencyPass(story, request).then(function (result) {
          if (result.fixed) {
            var re = Reviewer.validateStoryboard(result.story, request);
            if (re.ok) {
              story = re.story;
              story._selfCheck = '已修订（术语/事实/视角）';
            } else {
              story._selfCheck = '校对稿未过校验，保留原稿';
            }
          } else {
            story._selfCheck = result.skipped ? '未启用（本地模式）' : '通过';
          }
          return story;
        });
      })
      .then(function (story) {
        // 扩写闭环：校对后仍有页面过短（<45字）→ 追加一轮针对性扩写
        var shortPages = story.pages.filter(function (p) { return p.content.length < 45; }).length;
        if (!shortPages || !self.config.apiKey) {
          if (shortPages) story._selfCheck += '，' + shortPages + '页偏短';
          return story;
        }
        emitStage('design', 'running', 'DARYA 教研：' + shortPages + ' 页文字过短，扩写中…');
        emitProgress(26, 'DARYA 教研：扩写过短页面…');
        return self._expansionPass(story, request).then(function (result) {
          if (result.fixed) {
            var re2 = Reviewer.validateStoryboard(result.story, request);
            if (re2.ok) {
              var before = story.pages.reduce(function (a, p) { return a + p.content.length; }, 0);
              var after = re2.story.pages.reduce(function (a, p) { return a + p.content.length; }, 0);
              if (after > before) {                  // 只有确实变厚了才采纳
                story = re2.story;
                story._selfCheck += '，已扩写';
              }
            }
          }
          var still = story.pages.filter(function (p) { return p.content.length < 45; }).length;
          if (still) story._selfCheck += '，' + still + '页仍偏短';
          return story;
        });
      })
      .then(function (story) {
        emitStage('design', 'done', '脚本完成：《' + story.title + '》共 ' + story.pages.length + ' 页 · 自洽校对' + (story._selfCheck || ''));
        emitProgress(28, 'DARYA 教研：脚本完成');

        emitStage('art', 'running', 'DARYA 插画：绘制' + (story.pages.length + 1) + '张一致性插画');
        return self._renderIllustrations(request, story, emitProgress).then(function () {
          var total = story.pages.length + 1;
          var aiTotal = story._aiImageCount || 0;   // 封面已计入 _aiImageCount
          var detail = aiTotal > 0
            ? '插画完成：' + aiTotal + '/' + total + ' 张 AI 插图（' + (story.panel_mode === 'grid' ? '四格联画一次成型' : story.panel_mode === 'single-fallback' ? '联画回退逐页' : '逐页') + '）' + (aiTotal < total ? '，其余由场景插画兜底' : '')
            : '插画完成：本机矢量插画模式';
          if (story._styleScores && story._styleScores.length) {
            var avg = Math.round(story._styleScores.reduce(function (a, b) { return a + b; }, 0) / story._styleScores.length);
            detail += '，画风一致性 ' + avg + ' 分';
            if (story._repainted && story._repainted.length) {
              detail += '，已自动重绘第 ' + story._repainted.join('、') + ' 页';
            }
          }
          emitStage('art', 'done', detail);
          return story;
        });
      })
      .then(function (story) {
        emitStage('review', 'running', 'DARYA 审编：质检内容与插画');
        emitProgress(92, 'DARYA 审编：组装成品…');
        var artifact = self._buildArtifact(request, story);
        var reviewMsg = '质检通过：' + artifact.pages.length + ' 页正文 / ' + artifact.quiz.length + ' 道测评题';
        if (story._textWarnings && story._textWarnings.length) {
          reviewMsg += '，文字量提示：第 ' + story._textWarnings.map(function (w) { return w.page; }).join('、') + ' 页';
        }
        emitStage('review', 'done', reviewMsg);
        emitProgress(100, '完成！');
        return artifact;
      });
  };

  // 瞬时故障码：这类错误重试一次通常就过了，不该直接退到模板
  function isTransientFailure(error) {
    var code = String((error && error.code) || '');
    return code === 'TIMEOUT' || code === 'NETWORK_ERROR' ||
      code === 'HTTP_429' || /^HTTP_5/.test(code);
  }

  // DARYA 教研调用（isRetry=true 表示重试；transientAttempt 表示已为瞬时故障重试过）
  PictureBookAgent.prototype._designStoryboard = function (request, isRetry, transientAttempt) {
    var self = this;
    var messages = buildDesignMessages(request);
    if (isRetry) {
      messages.push({ role: 'user', content: '上一次输出的 JSON 校验未通过。请严格重新输出完整合法的 JSON，不要包含任何 JSON 以外的字符。' });
    }
    return LLMClient.chat(this.config, messages, { maxTokens: 3500 })
      .then(function (content) {
        var story = safeJSONParse(content);
        if (story) { self._storyboardDegraded = false; return story; }
        if (isRetry) return self._fallbackStoryboard(request);
        return self._designStoryboard(request, true, transientAttempt);
      })
      .catch(function (error) {
        // 网络抖动 / 限流 / 5xx 先退避重试一次：真实链路要能扛住一次抖动，
        // 否则一有波动就换成模板内容。
        var attempt = transientAttempt || 0;
        if (isTransientFailure(error) && attempt < 1) {
          console.warn('[绘本智能体] 教研模型瞬时故障，退避重试：', error && error.message);
          return sleep(1800).then(function () {
            return self._designStoryboard(request, isRetry, attempt + 1);
          });
        }
        // 确实不可用 → 本地兜底脚本。兜底正文只是通用框架，必须让学习者知道。
        console.warn('[绘本智能体] 教研模型不可用，启用本地兜底脚本：', error && error.message);
        self._degradedReason = (error && error.message) || '教研模型暂时不可用';
        return self._fallbackStoryboard(request);
      });
  };

  // 扩写二遍稿：只针对过短页面补足叙述，其他字段原样保留
  PictureBookAgent.prototype._expansionPass = function (story, request) {
    if (!this.config.apiKey) return Promise.resolve({ story: story, fixed: false, skipped: true });
    var messages = [
      { role: 'system', content: [
        '你是绘本文字编辑。脚本中部分页面的正文太短（少于45字），显得单薄。',
        '任务：把所有不足 60 字的页面正文扩写到 60~110 字。要求：',
        '1. 保持原有事实、术语、人称、主角名字完全不变，禁止新增未经证实的事实；',
        '2. 扩写方向：补主角的具体动作、环境细节、感官描写或一句角色互动，让页面有完整的叙述；',
        '3. 语言保持学段适配、口语化、适合朗读；',
        '4. 其余所有字段（line/fact/image_prompt/lighting/setting/props/mascot/quiz/summary 等）原样保留，不要改写。',
        '仅输出修订后的完整 JSON。'
      ].join('\n') },
      { role: 'user', content: '学段：' + (GRADE_TEXT[request && request.difficulty] || '初中') + '\n脚本：\n' + JSON.stringify(story) }
    ];
    return LLMClient.chat(this.config, messages, { temperature: 0.5, maxTokens: 3500 })
      .then(function (content) {
        var revised = safeJSONParse(content);
        if (!revised || !Array.isArray(revised.pages)) {
          return { story: story, fixed: false };
        }
        return { story: revised, fixed: true };
      })
      .catch(function () {
        return { story: story, fixed: false };
      });
  };

  // 教研自洽校对（二遍稿）：只修五类跨页一致性问题，结构不动
  PictureBookAgent.prototype._consistencyPass = function (story, request) {
    if (!this.config.apiKey) return Promise.resolve({ story: story, fixed: false, skipped: true });
    var messages = [
      { role: 'system', content: [
        '你是绘本教研审稿人。下面是一本教学绘本的完整脚本JSON。',
        '只修复以下五类跨页一致性问题，不做其他改动：',
        '1. 术语不一致：同一概念在不同页用了不同名称（如"叶绿体/叶绿素"混用、单位前后不一），统一为正确且全篇一致的术语；',
        '2. 事实矛盾：前后页事实、数字、量级互相矛盾，以正确的为准；',
        '3. 称谓不统一：主角名字与称呼在各页不一致，统一为 mascot.name；',
        '4. 叙述视角混乱：第三人称与第二人称（"你"）混用，统一为第三人称；',
        '5. 超纲词汇：明显超出学段的术语，换成学段内说法。',
        '6. 正文长度：不足 60 字的页面扩写到 60~110 字（保持事实正确与原有情节，补足动作、对话与细节描写）。',
        '保持 JSON 结构与所有字段完全不变（lighting/setting/props/mascot 原样保留），台词与知识便签若涉及上述问题同步修正。',
        '仅输出修订后的完整 JSON，不要任何解释。'
      ].join('\n') },
      { role: 'user', content: '学段：' + (GRADE_TEXT[request && request.difficulty] || '初中') + '\n脚本：\n' + JSON.stringify(story) }
    ];
    return LLMClient.chat(this.config, messages, { temperature: 0.2, maxTokens: 3500 })
      .then(function (content) {
        var revised = safeJSONParse(content);
        if (!revised || !Array.isArray(revised.pages)) {
          console.warn('[绘本智能体] 自洽校对解析失败，保留原稿');
          return { story: story, fixed: false };
        }
        return { story: revised, fixed: true };
      })
      .catch(function (error) {
        console.warn('[绘本智能体] 自洽校对不可用，保留原稿：', error && error.message);
        return { story: story, fixed: false };
      });
  };

  // DARYA 插画：封面 + 逐页生成，一致性 token 贯穿
  // 单张图的鲁棒策略：完整 prompt 失败 → 简化 prompt 再试（各自内部还有限流退避重试）
  PictureBookAgent.prototype._drawOne = function (config, fullPrompt, simplifiedPrompt) {
    var self = this;
    return ImageClient.generate(config, fullPrompt).then(function (first) {
      if (first.ok) return first;
      console.warn('[绘本智能体] 完整提示词失败，简化重试：', first.reason);
      return sleep(1000).then(function () {
        return ImageClient.generate(self.config, simplifiedPrompt);
      });
    });
  };

  /* ================================================================
   * 四格联画管线：四页场景放进同一次生成请求（2048x1536 → 2x2 切片）
   * 同一次扩散过程出图，画风一致性由构造保证，不依赖提示词约束；
   * 成本 1 次调用代替 4 次，速度约 1 分钟。
   * ================================================================ */
  PictureBookAgent.prototype._buildGridPrompt = function (story, styleToken, scenes) {
    var slots = ['左上格', '右上格', '左下格', '右下格'];
    var body = scenes.map(function (sc, i) { return slots[i] + '：' + sc; }).join('；');
    var head = '统一画风：' + styleToken + (story.palette ? '，' + story.palette : '') + '。' +
      (story.lighting ? '全书光线：' + story.lighting + '。' : '') +
      (story.setting ? '场景设定：' + story.setting + '。' : '') +
      (story.props && story.props.length ? '道具规格：' + story.props.join('；') + '。' : '') +
      '主角固定为：' + (story.mascot ? story.mascot.appearance : '') + '，每格中主角的物种、性别、发型、服装必须完全一致。';
    var layout = '这是一张2×2四格联画，格子之间用纯白色细边框分隔，每格都是一幅完整的独立横版插画：' + body + '。' +
      '四个格子必须画风完全统一、光线方向一致、主角形象一致，像同一本书的连续四页。';
    var tail = '纯插画，画面中禁止出现任何文字、字母、数字、标注。';
    return (head + layout + tail).slice(0, 900);
  };

  PictureBookAgent.prototype._sliceGrid = function (config, gridUrl) {
    var src = StyleGuard.proxyOf(config, gridUrl);
    return fetch(src).then(function (r) {
      if (!r.ok) throw new Error('proxy HTTP ' + r.status);
      return r.blob();
    }).then(function (blob) { return createImageBitmap(blob); }).then(function (bmp) {
      try {
        var halfW = Math.floor(bmp.width / 2), halfH = Math.floor(bmp.height / 2);
        var slots = [[0, 0], [halfW, 0], [0, halfH], [halfW, halfH]];
        var panels = [], thumbs = [];
        for (var i = 0; i < 4; i++) {
          var c = document.createElement('canvas');
          c.width = halfW; c.height = halfH;
          c.getContext('2d').drawImage(bmp, slots[i][0], slots[i][1], halfW, halfH, 0, 0, halfW, halfH);
          panels.push(c.toDataURL('image/jpeg', 0.86));
          var t = document.createElement('canvas');
          t.width = 24; t.height = 18;
          var tctx = t.getContext('2d');
          tctx.drawImage(bmp, slots[i][0], slots[i][1], halfW, halfH, 0, 0, 24, 18);
          thumbs.push(tctx.getImageData(0, 0, 24, 18).data);
        }
        if (bmp.close) bmp.close();

        // 质量校验：每格非空白，且相邻格内容确实不同（防止模型无视分格指令）
        function lumStd(d) {
          var vals = [];
          for (var k = 0; k < d.length; k += 4) vals.push(0.299 * d[k] + 0.587 * d[k + 1] + 0.114 * d[k + 2]);
          var mean = vals.reduce(function (a, b) { return a + b; }, 0) / vals.length;
          var vr = vals.reduce(function (a, v) { return a + (v - mean) * (v - mean); }, 0) / vals.length;
          return Math.sqrt(vr);
        }
        function diff(d1, d2) {
          var sum = 0, n = 0;
          for (var k = 0; k < d1.length; k += 8) {
            sum += Math.abs(d1[k] - d2[k]) + Math.abs(d1[k + 1] - d2[k + 1]) + Math.abs(d1[k + 2] - d2[k + 2]);
            n += 3;
          }
          return sum / n;
        }
        for (var p = 0; p < 4; p++) {
          if (lumStd(thumbs[p]) < 10) return { ok: false, reason: 'PANEL_BLANK' };
        }
        for (var q = 1; q < 4; q++) {
          if (diff(thumbs[0], thumbs[q]) < 6) return { ok: false, reason: 'PANELS_IDENTICAL' };
        }
        return { ok: true, panels: panels };
      } catch (e) {
        if (bmp.close) bmp.close();
        return { ok: false, reason: 'SLICE_ERROR' };
      }
    }).catch(function () {
      return { ok: false, reason: 'GRID_FETCH_FAIL' };
    });
  };

  // 联画逐格质检（与封面基准比对），偏差过大的格子退回单页重绘（保留更优版本）
  PictureBookAgent.prototype._guardPanels = function (story, emitProgress) {
    var self = this;
    var count = Math.min(4, story.pages.length);
    var index = 0;

    function next() {
      if (index >= count) return Promise.resolve();
      var page = story.pages[index];
      var percent = 34 + (index / count) * 50;
      emitProgress(percent, 'DARYA 审编：第 ' + (index + 1) + ' 格画风校验…');
      return StyleGuard.analyze(self.config, page.image_url).then(function (probe) {
        if (probe.ok) {
          story._panelProfiles.push(probe.profile);
          var dev = story._refProfile ? StyleGuard.deviation(probe.profile, story._refProfile) : 0;
          page.style_deviation = dev;
          story._styleScores.push(100 - dev);
          if (dev >= 42) {
            emitProgress(percent, '第 ' + (index + 1) + ' 格画风偏离（' + dev + '），单页重绘…');
            var styleToken = story.styleToken;
            var prompt = Reviewer.sanitizeImagePrompt(page.image_prompt, story, styleToken, true);
            var simplified = '儿童绘本插画，' + (story.palette ? story.palette + '，' : '') + page.image_prompt.slice(0, 60) + '，柔和明亮';
            var originalPanel = page.image_url;   // 重绘更差时回滚
            return self._drawOne(self.config, prompt, simplified).then(function (result) {
              if (!result.ok) return undefined;
              return StyleGuard.analyze(self.config, result.url).then(function (probe2) {
                var dev2 = probe2.ok ? StyleGuard.deviation(probe2.profile, story._refProfile) : 100;
                story._repainted.push(page.pageNumber || (index + 1));
                story._aiImageCount++;
                if (dev2 < dev) {          // 只保留更优版本
                  page.image_url = result.url;
                  page.style_deviation = dev2;
                  story._styleScores.push(100 - dev2);
                } else {
                  page.image_url = originalPanel;
                  story._styleScores.push(100 - dev);
                }
              });
            });
          }
        }
        return undefined;
      }).then(function () {
        index++;
        return next();
      });
    }
    return next();
  };

  // 封面↔内页一致性：内页由联画互相一致，若它们与封面整体偏差大 → 封面才是跑偏方，重绘封面
  PictureBookAgent.prototype._checkCover = function (story, request, emitProgress) {
    var self = this;
    if (!story.cover_image || !self.config.apiKey || !story._panelProfiles.length) return Promise.resolve();
    var devs = story.pages.map(function (p) { return p.style_deviation; })
      .filter(function (d) { return d != null; }).sort(function (a, b) { return a - b; });
    if (!devs.length) return Promise.resolve();
    var median = devs[Math.floor(devs.length / 2)];
    if (median < 50) return Promise.resolve();     // 内页都贴合封面，封面没问题

    emitProgress(38, '封面与内页画风偏离，重绘封面…');
    var prompt = Reviewer.sanitizeImagePrompt(
      '绘本封面大场景：展示「' + request.topic + '」最具代表性的视觉奇观，主角位于画面中心，姿态活泼，背景元素丰富饱满',
      story, story.styleToken, true);
    var simplified = '儿童绘本封面插画，' + (story.palette ? story.palette + '，' : '') + '关于' + request.topic + '，主角居中，色彩柔和明亮';
    return self._drawOne(self.config, prompt, simplified).then(function (result) {
      if (!result.ok) return undefined;
      return StyleGuard.analyze(self.config, result.url).then(function (probe) {
        if (!probe.ok) return undefined;
        var newDevs = story._panelProfiles.map(function (pp) { return StyleGuard.deviation(probe.profile, pp); })
          .sort(function (a, b) { return a - b; });
        var newMedian = newDevs[Math.floor(newDevs.length / 2)];
        if (newMedian < median) {                  // 新封面更贴内页才采纳
          story.cover_image = result.url;
          story._repainted.push('封面');
        }
        return undefined;
      });
    });
  };

  PictureBookAgent.prototype._renderIllustrations = function (request, story, emitProgress) {
    var self = this;
    var styleToken = STYLE_TOKENS[request.style] || STYLE_TOKENS.colorful;
    story.styleToken = styleToken;
    story._aiImageCount = 0;
    story._styleScores = [];
    story._repainted = [];
    story.pages.forEach(function (p, i) { p.pageNumber = i + 1; });

    var useAI = this.config.useAIImage && this.config.apiKey;
    var index = 0;
    story._panelProfiles = [];

    // 单张插画 + 画风质检（与封面基准比对，偏差大自动重绘一次）
    function drawWithGuard(page, basePrompt, simplified, label) {
      return self._drawOne(self.config, basePrompt, simplified).then(function (result) {
        if (!result.ok) return result;
        return StyleGuard.analyze(self.config, result.url).then(function (probe) {
          // 像素质检不可用 → 直接采用
          if (!probe.ok || !story._refProfile) {
            if (probe.ok && !story._refProfile) story._refProfile = probe.profile;
            return result;
          }
          var dev = StyleGuard.deviation(probe.profile, story._refProfile);
          page.style_deviation = dev;
          if (dev < 42) {
            story._styleScores.push(100 - dev);
            return result;
          }
          // 偏差过大：强化统一提示词重绘一次，保留更好的一版
          emitProgress(0, label + '画风偏离（' + dev + '），自动重绘…');
          var reinforcedPrompt = Reviewer.sanitizeImagePrompt(
            page.image_prompt || basePrompt, story, styleToken, true);
          return self._drawOne(self.config, reinforcedPrompt, simplified).then(function (retry) {
            if (!retry.ok) {
              story._styleScores.push(100 - dev);
              return result;
            }
            return StyleGuard.analyze(self.config, retry.url).then(function (probe2) {
              var dev2 = probe2.ok ? StyleGuard.deviation(probe2.profile, story._refProfile) : 100;
              story._repainted.push(page.pageNumber || 0);
              if (dev2 < dev) {
                page.style_deviation = dev2;
                story._styleScores.push(100 - dev2);
                return retry;
              }
              story._styleScores.push(100 - dev);
              return result;
            });
          });
        });
      });
    }

    function drawPage() {
      if (index >= story.pages.length) return Promise.resolve();
      var page = story.pages[index];
      page.pageNumber = index + 1;
      var percent = 30 + (index / story.pages.length) * 58;
      var prompt = Reviewer.sanitizeImagePrompt(page.image_prompt, story, styleToken);
      page.image_prompt_final = prompt;
      var simplified = '儿童绘本插画，' + (story.palette ? story.palette + '，' : '') + page.image_prompt.slice(0, 60) + '，柔和明亮';

      if (!useAI) {
        page.image_url = null;
        emitProgress(percent, 'DARYA 插画：矢量绘制 ' + (index + 1) + '/' + story.pages.length);
        index++;
        return sleep(120).then(drawPage);
      }

      emitProgress(percent, 'DARYA 插画：绘制第 ' + (index + 1) + '/' + story.pages.length + ' 张插画…');
      return drawWithGuard(page, prompt, simplified, '第' + (index + 1) + '页').then(function (result) {
        if (result.ok) {
          page.image_url = result.url;
          page.image_cached = !!result.cached;
          story._aiImageCount++;
        } else {
          console.warn('[绘本智能体] 第' + (index + 1) + '页插画降级：', result.reason);
          page.image_url = null;
          page.image_fallback_reason = result.reason;
        }
        index++;
        return sleep(600).then(drawPage);   // 调用间隔，降低限流概率
      });
    }

    // 分发：默认走四格联画（画风由构造保证一致），失败或指定 single 时回退逐页
    function dispatchPages() {
      var gridEligible = self.config.imageLayout !== 'single' && story.pages.length <= 5;
      if (!gridEligible) return drawPage();
      var count = Math.min(4, story.pages.length);
      var scenes = [];
      for (var i = 0; i < count; i++) scenes.push(story.pages[i].image_prompt);
      if (story.pages.length === 3) scenes.push('主角的半身肖像特写，柔和纯色背景，微笑看向前方');   // 3页书：第4格画肖像给扉页用
      emitProgress(31, 'DARYA 插画：四格联画一次成型…');
      var gridPrompt = self._buildGridPrompt(story, styleToken, scenes);
      return ImageClient.generate(self.config, gridPrompt).then(function (g) {
        if (!g.ok) throw new Error(g.reason || 'GRID_FAIL');
        story._grid_image = g.url;
        return self._sliceGrid(self.config, g.url);
      }).then(function (sliced) {
        if (!sliced.ok) throw new Error(sliced.reason || 'SLICE_FAIL');
        for (var i = 0; i < count; i++) {
          story.pages[i].image_url = sliced.panels[i];
          story._aiImageCount++;
        }
        if (story.pages.length === 3) story.titlepage_image = sliced.panels[3];   // 肖像格给扉页
        story.panel_mode = 'grid';
        emitProgress(40, 'DARYA 插画：联画切片完成，逐格质检…');
        return self._guardPanels(story, emitProgress);
      }).then(function () {
        return self._checkCover(story, request, emitProgress);
      }).catch(function (err) {
        console.warn('[绘本智能体] 四格联画不可用，回退逐页模式：', err && (err.message || err.reason));
        story.panel_mode = 'single-fallback';
        index = 0;
        return drawPage();
      }).then(function () {
        // 5 页书：联画覆盖前 4 页，第 5 页单独生成
        if (story.pages.length === 5 && story.pages[4].image_url == null) {
          index = 4;
          return drawPage();
        }
      });
    }

    if (!useAI) {
      story.palette = DEFAULT_PALETTES[story.subject] || DEFAULT_PALETTES.general;
      return drawPage();
    }

    // 封面先行：它是全书画风的基准（后续页与其比对，并从中提取主色调）
    var coverPrompt = Reviewer.sanitizeImagePrompt(
      '绘本封面大场景：展示「' + request.topic + '」最具代表性的视觉奇观，' +
      '主角位于画面中心，姿态活泼，背景元素丰富饱满',
      story, styleToken
    );
    var coverSimplified = '儿童绘本封面插画，关于' + request.topic + '，主角居中，色彩柔和明亮';

    emitProgress(29, 'DARYA 插画：绘制封面…');
    return this._drawOne(self.config, coverPrompt, coverSimplified).then(function (result) {
      if (result.ok) {
        story.cover_image = result.url;
        story._aiImageCount++;
        return StyleGuard.analyze(self.config, result.url).then(function (probe) {
          if (probe.ok) {
            story._refProfile = probe.profile;
            story.palette = StyleGuard.paletteNames(probe.profile);   // 封面取色回灌
          } else {
            console.warn('[绘本智能体] 画风像素质检不可用（', probe.reason, '），启用默认调色板');
            story.palette = DEFAULT_PALETTES[story.subject] || DEFAULT_PALETTES.general;
          }
          return sleep(600).then(dispatchPages);
        });
      }
      console.warn('[绘本智能体] 封面插画降级：', result.reason);
      story.palette = DEFAULT_PALETTES[story.subject] || DEFAULT_PALETTES.general;
      return sleep(600).then(dispatchPages);
    });
  };

  // 产物组装（兼容主项目 artifact 协议）
  PictureBookAgent.prototype._buildArtifact = function (request, story) {
    return {
      artifact_id: uid('storybook'),
      kind: 'storybook',
      topic: request.topic,
      difficulty: request.difficulty,
      grade_text: GRADE_TEXT[request.difficulty] || '初中',
      style: request.style,
      style_token: story.styleToken,
      title: story.title,
      subtitle: story.subtitle || '',
      subject: story.subject || 'general',
      mascot: story.mascot,
      cover_image: story.cover_image || null,
      titlepage_image: story.titlepage_image || null,
      image_size: this.config.imageSize || '1152x864',
      format: 'landscape-4:3',
      lighting: story.lighting || '',
      setting: story.setting || '',
      props: story.props || [],
      pages: story.pages.map(function (page, i) {
        return {
          index: i + 1,
          stage: page.stage,
          stage_label: STAGE_LABEL[page.stage] || STAGE_LABEL.general,
          title: page.title,
          content: page.content,
          line: page.line || '',
          fact: page.fact || '',
          narration: page.content,
          image_url: page.image_url || null,
          image_fallback_reason: page.image_fallback_reason || null,
          style_deviation: page.style_deviation != null ? page.style_deviation : null
        };
      }),
      quiz: story.quiz,
      summary: story.summary,
      thinking_question: story.thinking_question,
      stats: {
        ai_images: story._aiImageCount || 0,
        total_images: story.pages.length,
        panel_mode: story.panel_mode || (this.config.imageLayout === 'single' ? 'single' : 'grid-fallback'),
        grid_image: story._grid_image || null,
        style_consistency: (story._styleScores && story._styleScores.length)
          ? Math.round(story._styleScores.reduce(function (a, b) { return a + b; }, 0) / story._styleScores.length)
          : null,
        repainted_pages: story._repainted || [],
        palette: story.palette || null,
        self_check: story._selfCheck || null,
        text_warnings: story._textWarnings || [],
        generated_at: Date.now()
      },
      generator: 'storybook-agent/2.0',
      created_at: Date.now()
    };
  };

  /* ================================================================
   * 本地兜底脚本（教研模型不可用时保证 100% 出书）
   * ================================================================ */
  PictureBookAgent.prototype._fallbackStoryboard = function (request) {
    this._storyboardDegraded = true;
    var topic = request.topic;
    var frames = [
      { stage: 'engage', title: '遇见' + topic, line: '哇，' + topic + '！我们一起去看看吧！', fact: '本册主角：一只爱提问的小猫头鹰。', content: '大家好，我是小问号！今天我们要认识一个有趣的主题——' + topic + '。也许你听说过它，也许这是第一次见面。别急，跟着我一步一步来，你会发现它就在我们身边，而且比想象中更有意思。准备好了吗？我们出发！', image_prompt: '小猫头鹰站在开满花的草地上，好奇地望向前方的奇妙世界，天空明亮' },
      { stage: 'explore', title: '观察' + topic, line: '咦，它是由什么组成的呢？', fact: '观察是科学探究的第一步。', content: '让我们先来观察：' + topic + '是什么样子的？它由哪些部分组成？把这些部分一个一个看清楚，就像拆开一件礼物。观察是学习的第一步——先看清"是什么"，我们才能弄懂"为什么"。', image_prompt: '小猫头鹰拿着放大镜仔细观察眼前漂浮的发光结构，细节丰富' },
      { stage: 'explain', title: '原理揭秘', line: '原来背后藏着这样的规律！', fact: '把复杂过程拆成一条链路更好理解。', content: '现在来揭开' + topic + '的原理。它为什么会这样运行？背后的规律是什么？把过程想象成一条流水线：每一步都有自己的任务，合在一起就完成了整个"魔法"。理解了这条链路，你就掌握了它的核心。', image_prompt: '小猫头鹰在黑板前指着流程示意讲解，周围漂浮着示意图形' },
      { stage: 'elaborate', title: '生活中的它', line: '我能用它做点什么呀？', fact: '能讲给别人听，才是真的学会了。', content: '学会了' + topic + '，它能用在哪里呢？其实从日常生活到科学技术，处处都有它的影子。试着找一找身边的例子，向家人讲一讲你学到的内容——能把别人讲明白，才是真的学会了。', image_prompt: '小猫头鹰在城市与自然交汇的场景中指着各种应用场景，画面热闹温馨' },
      { stage: 'elaborate', title: '回顾与延伸', line: '下一个秘密等着我去发现！', fact: '好奇心是最重要的学习动力。', content: '最后和小问号一起回顾：我们认识了' + topic + '的样子，拆解了它的原理，还找到了它在生活中的应用。关于' + topic + '还有更深的奥秘等待你去探索——保持好奇心，下一个发现者就是你！', image_prompt: '小猫头鹰坐在堆积的书本上眺望远方的星空，充满希望的氛围' }
    ];
    var pageCount = clamp(request.pageCount || 4, 3, 5);
    var pages = frames.slice(0, pageCount);
    return {
      title: '认识' + topic,
      subtitle: '和小问号一起探索',
      subject: 'general',
      lighting: '晴天上午，光源统一左上方，明亮柔和',
      setting: '开满野花的绿色草地公园，远处有一棵大橡树',
      props: ['蓝色圆框放大镜', '红色小背包'],
      mascot: { name: '小问号', appearance: '一只圆滚滚的橙色小猫头鹰，男孩，头上有一撮呆毛，戴蓝色圆框眼镜，胸前有问号形状的白色羽毛' },
      pages: pages,
      quiz: [
        { question: '这本绘本主要介绍了什么？', options: [topic, '无关内容', '随意的其他知识', '没有主题'], answer: 0, explanation: '整本书都围绕「' + topic + '」展开。', bloom: '记忆' },
        { question: '学习一个新主题时，第一步应该做什么？', options: ['直接背结论', '先观察"是什么"', '马上做题', '跳过不理解的部分'], answer: 1, explanation: '观察与理解概念是学习的第一步。', bloom: '理解' },
        { question: '怎样检验自己是否真正学会了？', options: ['能讲给别人听', '看过一遍就算', '抄一遍笔记', '多读几遍'], answer: 0, explanation: '能清晰地讲给别人听，说明真正理解了。', bloom: '评价' }
      ],
      summary: '本次学习围绕' + topic + '展开：先观察现象，再理解原理，最后联系生活应用。',
      thinking_question: '关于' + topic + '，你还想弄清楚哪个问题？试着把它写下来，去查一查、问一问。'
    };
  };

  /* ================================================================
   * 主项目意图识别（与 AIWorkspaceAgents.detectTaskIntent 同构）
   * ================================================================ */
  // 去掉口语化的前缀/后缀，只留下干净的主题词
  function cleanTopic(raw) {
    var t = String(raw || '').trim();
    t = t.replace(/\d{1,2}\s*页|[一二两三四五六七八九十]+\s*页/g, '');
    var fillers = ['帮我', '给我', '请你', '麻烦', '想要', '想', '要', '生成', '做', '画',
      '来', '编', '写', '出', '一本', '一份', '一个', '关于', '介绍', '讲讲', '有关', '的'];
    var changed = true;
    while (changed) {
      changed = false;
      t = t.replace(/^\s+/, '');
      for (var i = 0; i < fillers.length; i++) {
        if (t.indexOf(fillers[i]) === 0) { t = t.slice(fillers[i].length); changed = true; }
      }
      if (t.slice(-1) === '的') { t = t.slice(0, -1); changed = true; }
    }
    return t.trim();
  }

  function detectStorybookIntent(message) {
    var text = String(message || '').trim();
    if (!/(绘本|图画书|连环画)/.test(text)) return null;
    if (!/(?:生成|做|画|来|编|写|要|想要|帮我|给我)/.test(text)) return null;
    var match = text.match(/([一-龥a-zA-Z0-9·]{2,24}?)(?:的)?(?:科普)?(?:绘本|图画书|连环画)/);
    var topic = match && match[1] ? cleanTopic(match[1]) : '';
    if (topic.length < 2) topic = '当前主题';
    var count = text.match(/(\d{1,2}|[一二两三四五六七八九十]+)\s*(?:页|张)/);
    var digits = { '一': 1, '两': 2, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9 };
    var pageCount = 4;
    if (count) pageCount = /^\d+$/.test(count[1]) ? Number(count[1]) : (digits[count[1]] || 4);
    var difficulty = /小学|儿童|简单/.test(text) ? 'elementary' : (/高中|深入/.test(text) ? 'high' : 'middle');
    return { kind: 'storybook', topic: topic, pages: clamp(pageCount, 3, 5), difficulty: difficulty };
  }

  /* ================================================================
   * 学习记录（测评闭环数据）
   * ================================================================ */
  var History = {
    add: function (record) {
      var list = storageGet(HISTORY_STORAGE, []);
      list.unshift(record);
      storageSet(HISTORY_STORAGE, list.slice(0, 50));
    },
    list: function () { return storageGet(HISTORY_STORAGE, []); },
    stats: function () {
      var list = this.list();
      var books = list.length;
      var answered = 0, correct = 0;
      list.forEach(function (r) {
        answered += (r.total || 0);
        correct += (r.score || 0);
      });
      return { books: books, answered: answered, correct: correct, accuracy: answered ? Math.round(correct / answered * 100) : 0 };
    }
  };

  /* ================================================================
   * 导出
   * ================================================================ */
  return {
    PictureBookAgent: PictureBookAgent,
    detectStorybookIntent: detectStorybookIntent,
    generateFallbackSVG: generateFallbackSVG,
    StyleGuard: StyleGuard,
    STAGE_LABEL: STAGE_LABEL,
    GRADE_TEXT: GRADE_TEXT,
    CATEGORY_COLORS: CATEGORY_COLORS,
    DEFAULT_PALETTES: DEFAULT_PALETTES,
    STYLE_TOKENS: STYLE_TOKENS,
    Narrator: Narrator,
    History: History,
    utils: { uid: uid, safeJSONParse: safeJSONParse, clamp: clamp }
  };
}));
