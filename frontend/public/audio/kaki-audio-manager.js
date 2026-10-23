(function () {
  'use strict';
  if (window.KakiAudioManager) return;

  var SESSION_KEY = 'kaki_audio_session_v1';
  var MUSIC_SOURCE = '/audio/kaki-fireworks-night-clean.m4a';
  var MAIN_TYPES = ['adventure_bgm', 'celebration', 'relax_fireworks'];
  var media = new Audio();
  media.preload = 'metadata';
  media.loop = false;

  var stored = {};
  try { stored = JSON.parse(sessionStorage.getItem(SESSION_KEY) || '{}'); } catch (_) { stored = {}; }
  var volume = Number.isFinite(Number(stored.volume)) ? Math.max(0, Math.min(.35, Number(stored.volume))) : .28;
  var muted = Boolean(stored.muted);
  var currentType = null;
  var currentScene = null;
  var endedCallback = null;
  var timeCallback = null;
  var audioContext = null;
  var masterGain = null;
  var toneTimer = null;
  var fadeFrame = 0;
  var operationId = 0;
  var visibilityPaused = false;
  var warned = false;

  function persist() {
    try { sessionStorage.setItem(SESSION_KEY, JSON.stringify({ volume: volume, muted: muted })); } catch (_) {}
  }
  function reportOnce(error) {
    if (warned) return;
    warned = true;
    console.warn('[kaki-audio] Audio unavailable; visual experience continues.', error && error.message ? error.message : error);
  }
  function ensureContext() {
    if (!audioContext) {
      var Context = window.AudioContext || window.webkitAudioContext;
      if (!Context) return false;
      audioContext = new Context();
      masterGain = audioContext.createGain();
      masterGain.gain.value = muted ? 0 : volume;
      masterGain.connect(audioContext.destination);
    }
    if (audioContext.state === 'suspended') audioContext.resume().catch(reportOnce);
    return true;
  }
  function clearToneTimer() {
    if (toneTimer) window.clearInterval(toneTimer);
    toneTimer = null;
  }
  function tone(frequency, duration, gain, wave) {
    if (!ensureContext() || !masterGain || muted || document.hidden) return;
    var now = audioContext.currentTime;
    var oscillator = audioContext.createOscillator();
    var envelope = audioContext.createGain();
    oscillator.type = wave || 'sine';
    oscillator.frequency.setValueAtTime(frequency, now);
    envelope.gain.setValueAtTime(.0001, now);
    envelope.gain.exponentialRampToValueAtTime(gain || .02, now + .05);
    envelope.gain.exponentialRampToValueAtTime(.0001, now + duration);
    oscillator.connect(envelope);
    envelope.connect(masterGain);
    oscillator.start(now);
    oscillator.stop(now + duration + .06);
  }
  function stopMediaImmediate(reset) {
    cancelAnimationFrame(fadeFrame);
    media.pause();
    if (reset !== false) {
      try { media.currentTime = 0; } catch (_) {}
    }
    media.volume = muted ? 0 : volume;
    endedCallback = null;
    timeCallback = null;
  }
  function setVolume(next) {
    volume = Math.max(0, Math.min(.35, Number(next)));
    media.volume = muted ? 0 : volume;
    if (masterGain) masterGain.gain.value = muted ? 0 : volume;
    persist();
    return volume;
  }
  function setMuted(next) {
    muted = Boolean(next);
    media.muted = muted;
    media.volume = muted ? 0 : volume;
    if (masterGain) masterGain.gain.value = muted ? 0 : volume;
    persist();
    return muted;
  }
  function stop(options) {
    options = options || {};
    var id = ++operationId;
    clearToneTimer();
    if (audioContext && audioContext.state === 'running') audioContext.suspend().catch(function () {});
    var fadeMs = Math.max(0, Number(options.fadeMs || 0));
    if (media.paused || fadeMs === 0) {
      stopMediaImmediate(options.reset !== false);
      currentType = null;
      currentScene = null;
      return Promise.resolve();
    }
    var start = performance.now();
    var from = media.volume;
    return new Promise(function (resolve) {
      function step(now) {
        if (id !== operationId) return resolve();
        var progress = Math.min(1, (now - start) / fadeMs);
        media.volume = from * (1 - progress);
        if (progress < 1) fadeFrame = requestAnimationFrame(step);
        else {
          stopMediaImmediate(options.reset !== false);
          currentType = null;
          currentScene = null;
          resolve();
        }
      }
      fadeFrame = requestAnimationFrame(step);
    });
  }
  function playMusic(type, options) {
    options = options || {};
    if (type !== 'celebration' && type !== 'relax_fireworks') return Promise.resolve(false);
    ++operationId;
    clearToneTimer();
    if (audioContext && audioContext.state === 'running') audioContext.suspend().catch(function () {});
    var sameActive = currentType === type && !media.paused && !media.ended;
    if (sameActive && !options.restart) return Promise.resolve(true);
    stopMediaImmediate(true);
    currentType = type;
    currentScene = options.scene || null;
    endedCallback = typeof options.onEnded === 'function' ? options.onEnded : null;
    timeCallback = typeof options.onTime === 'function' ? options.onTime : null;
    media.src = MUSIC_SOURCE;
    media.muted = muted;
    media.volume = muted ? 0 : Math.min(volume, Number(options.volume || volume));
    try { media.currentTime = 0; } catch (_) {}
    var result = media.play();
    if (!result || typeof result.then !== 'function') return Promise.resolve(true);
    return result.then(function () { return true; }).catch(function (error) {
      reportOnce(error);
      return false;
    });
  }
  function startAdventure(scene) {
    if (currentType === 'celebration' || currentType === 'relax_fireworks') stopMediaImmediate(true);
    currentType = 'adventure_bgm';
    currentScene = scene || 'prepare';
    ensureContext();
    clearToneTimer();
    var schemes = {
      prepare: [220, 330, 4200], explore: [246.94, 369.99, 3600],
      challenge: [196, 293.66, 2500], boss: [146.83, 220, 1900]
    };
    var scheme = schemes[currentScene] || schemes.explore;
    function phrase() {
      tone(scheme[0], 1.3, .018, currentScene === 'boss' ? 'triangle' : 'sine');
      window.setTimeout(function () { if (currentType === 'adventure_bgm') tone(scheme[1], 1.05, .012, 'sine'); }, 160);
    }
    phrase();
    toneTimer = window.setInterval(phrase, scheme[2]);
    return true;
  }
  function playSfx(frequency, duration, gain, wave) {
    ensureContext();
    tone(frequency, duration, gain, wave);
  }
  function getState() {
    return {
      type: currentType,
      scene: currentScene,
      volume: volume,
      muted: muted,
      currentTime: Number(media.currentTime || 0),
      duration: Number.isFinite(media.duration) ? media.duration : 0,
      paused: media.paused,
      ended: media.ended
    };
  }

  media.addEventListener('timeupdate', function () {
    if (timeCallback) timeCallback(getState());
  });
  media.addEventListener('ended', function () {
    var callback = endedCallback;
    endedCallback = null;
    timeCallback = null;
    currentType = null;
    currentScene = null;
    if (callback) callback(getState());
  });
  media.addEventListener('error', function () { reportOnce(media.error || new Error('decode failed')); });
  document.addEventListener('visibilitychange', function () {
    if (document.hidden) {
      visibilityPaused = Boolean(currentType);
      if (!media.paused) media.pause();
      if (audioContext && audioContext.state === 'running') audioContext.suspend().catch(function () {});
      return;
    }
    if (!visibilityPaused || !currentType) return;
    visibilityPaused = false;
    if (currentType === 'celebration' || currentType === 'relax_fireworks') {
      media.volume = muted ? 0 : Math.min(.12, volume);
      media.play().then(function () { media.volume = muted ? 0 : volume; }).catch(reportOnce);
    } else if (currentType === 'adventure_bgm' && audioContext) {
      audioContext.resume().catch(reportOnce);
    }
  });

  window.KakiAudioManager = {
    types: MAIN_TYPES.concat(['ui_sfx']),
    source: MUSIC_SOURCE,
    play: playMusic,
    startAdventure: startAdventure,
    playSfx: playSfx,
    stop: stop,
    pause: function () {
      clearToneTimer();
      if (!media.paused) media.pause();
      if (audioContext && audioContext.state === 'running') audioContext.suspend().catch(function () {});
    },
    setVolume: setVolume,
    toggleMute: function () { return setMuted(!muted); },
    setMuted: setMuted,
    getState: getState
  };
})();
