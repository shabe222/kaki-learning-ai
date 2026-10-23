/**
 * ML Project - 统一前端配置（单一来源）
 *
 * 用法：
 *   <script src="scripts/config.js"></script>  <!-- 必须在所有业务脚本前 -->
 *
 * 业务代码读取：
 *   const API = window.ML_CONFIG?.API_BASE_URL || 'http://127.0.0.1:8765';
 *
 * 部署/环境切换只需替换此文件，无需改业务代码。
 */
(function () {
  'use strict';

  // 1) 自动检测当前 host（同源则用同源端口，跨域则用 localhost:8765）
  //    这样在同一机器上跑前后端时不需要改任何配置
  var isLocal = /^localhost$|^127\.0\.0\.1$|^\[::1\]$/.test(window.location.hostname);
  // 注：后端 FastAPI 在 8765 一站式运行（API + 静态文件），同端口无需跨域
  var defaultApi = window.location.protocol === 'file:'
    ? 'http://127.0.0.1:8765'
    : window.location.origin;  // 直接使用当前页面同源地址，避免 localhost/127.0.0.1 CORS

  // 2) 允许通过 localStorage 覆盖（开发/演示切换后端地址）
  var override = null;
  try { override = localStorage.getItem('ml_api_base_url'); } catch (e) {}

  // 3) 暴露到 window
  window.ML_CONFIG = {
    API_BASE_URL: override || defaultApi,
    VERSION: '2026-06-13',
    BUILD: 'v5.3-port-8765'
  };

  // 4) 兼容老代码里直接读 window.API_BASE_URL
  window.API_BASE_URL = window.ML_CONFIG.API_BASE_URL;
})();
