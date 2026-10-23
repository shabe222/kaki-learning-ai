(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) {
    root.AIWorkspaceSpeech = api;
    if (root.document) root.speechAdapter = api.createSpeechAdapter(root);
  }
}(typeof window !== 'undefined' ? window : globalThis, function () {
  'use strict';

  var VOICE_PERSONA = Object.freeze({
    id: 'kaki-shixiaoji-v1',
    profile: 'young_warm_clear_companion',
    language: 'zh-CN',
    server_voice_id: 'zh-CN-XiaoxiaoNeural',
    description: '年轻、温暖、清晰、有陪伴感，略可爱但不幼稚',
    presets: Object.freeze({
      chat_read_aloud: Object.freeze({ rate: 0.96, pitch: 1.03, volume: 1 }),
      call: Object.freeze({ rate: 1.01, pitch: 1.04, volume: 1 }),
      teaching: Object.freeze({ rate: 0.92, pitch: 1.01, volume: 1 }),
      encouraging: Object.freeze({ rate: 0.98, pitch: 1.05, volume: 1 }),
      serious_explaining: Object.freeze({ rate: 0.9, pitch: 0.99, volume: 1 })
    })
  });

  function codeSummary(language) {
    var normalized = String(language || '').trim().toLowerCase();
    if (normalized === 'py' || normalized === 'python') return '下面是一段 Python 示例。';
    if (normalized === 'js' || normalized === 'javascript') return '下面是一段 JavaScript 示例。';
    return '下面是一段代码示例。';
  }

  function toSpeakableText(markdown) {
    return String(markdown || '')
      .replace(/```([^\n`]*)\n?[\s\S]*?```/g, function (_, language) { return codeSummary(language); })
      .replace(/\$\$[\s\S]*?\$\$|\\\[[\s\S]*?\\\]/g, '这里有一段数学公式。')
      .replace(/\$[^$\n]+\$|\\\([^\n]*?\\\)/g, '这里有一段数学公式。')
      .replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
      .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
      .replace(/^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$/gm, '')
      .replace(/^\s{0,3}#{1,6}\s*/gm, '')
      .replace(/^\s*>+\s?/gm, '')
      .replace(/^\s*(?:[-+*]|\d+[.)])\s+/gm, '')
      .replace(/<[^>]+>/g, '')
      .replace(/https?:\/\/\S+/g, '')
      .replace(/[|]/g, '，')
      .replace(/[*_~`]/g, '')
      .replace(/\s*\n+\s*/g, '。')
      .replace(/。{2,}/g, '。')
      .replace(/[ \t]{2,}/g, ' ')
      .replace(/\s+([，。！？；：])/g, '$1')
      .trim();
  }

  function prepareSpeechSegments(content, limit) {
    var text = toSpeakableText(content);
    var maxLength = Math.max(10, Number(limit) || 220);
    var segments = [];
    while (text.length > maxLength) {
      var windowText = text.slice(0, maxLength);
      var cut = Math.max(
        windowText.lastIndexOf('。') + 1,
        windowText.lastIndexOf('！') + 1,
        windowText.lastIndexOf('？') + 1,
        windowText.lastIndexOf('；') + 1,
        windowText.lastIndexOf('，') + 1
      );
      if (cut < Math.floor(maxLength * 0.45)) cut = maxLength;
      segments.push(text.slice(0, cut));
      text = text.slice(cut);
    }
    if (text) segments.push(text);
    return segments;
  }

  function createSpeechAdapter(environment) {
    var env = environment || {};
    var synth = env.speechSynthesis;
    var Utterance = env.SpeechSynthesisUtterance;
    var activeUtterance = null;
    var playbackElement = null;
    var playbackChannel = 'unbound';
    var token = 0;
    var selectedVoiceUri = '';
    var objectUrl = '';
    var realProviderStatus = 'UNVERIFIED';
    var activeProvider = 'none';
    var lastProviderError = '';
    // 真实 TTS 稳定性：连续失败计数，>=3 才允许降级浏览器声（单次抖动不换声音）
    var realConsecutiveFailures = 0;

    function apiClient() { return env.AIWorkspaceApiClient || env.apiClient || null; }
    function realProviderConfigured() { var api = apiClient(); return Boolean(api && typeof api.generateLessonSpeech === 'function'); }
    function browserSupported() { return Boolean(synth && Utterance && typeof synth.speak === 'function' && typeof synth.cancel === 'function'); }
    function supported() { return Boolean(realProviderConfigured() || browserSupported()); }
    function voices() { return synth && typeof synth.getVoices === 'function' ? (synth.getVoices() || []) : []; }
    function resolveVoice() {
      var list = voices();
      if (!list.length) return null;
      var selected = selectedVoiceUri && list.find(function (voice) { return voice.voiceURI === selectedVoiceUri; });
      if (!selected) {
        selected = list.find(function (voice) {
          var lang = String(voice.lang || '').toLowerCase();
          var name = String(voice.name || '').toLowerCase();
          return lang.indexOf('zh-cn') === 0 && /xiaoxiao|xiaoyi|female|tingting|huihui|yunxi|zh/.test(name);
        }) || list.find(function (voice) { return String(voice.lang || '').toLowerCase().indexOf('zh') === 0; }) || list[0];
        selectedVoiceUri = selected && selected.voiceURI || '';
      }
      return selected || null;
    }
    function preset(name) { return VOICE_PERSONA.presets[name] || VOICE_PERSONA.presets.chat_read_aloud; }
    function revokeUrl() {
      if (objectUrl && env.URL && typeof env.URL.revokeObjectURL === 'function') { try { env.URL.revokeObjectURL(objectUrl); } catch (_) {} }
      objectUrl = '';
    }
    function resetAudioHandlers(element) {
      if (!element) return;
      element.onplay = null;element.onpause = null;element.onended = null;element.onerror = null;element.ontimeupdate = null;
    }
    function stop() {
      token += 1;
      activeUtterance = null;
      realConsecutiveFailures = 0;
      if (synth && typeof synth.cancel === 'function') synth.cancel();
      if (playbackElement) {
        resetAudioHandlers(playbackElement);
        try { playbackElement.pause(); playbackElement.currentTime = 0; } catch (_) {}
        try { playbackElement.removeAttribute('src'); if (typeof playbackElement.load === 'function') playbackElement.load(); } catch (_) {}
      }
      revokeUrl();
      activeProvider = 'none';
    }
    function setPlaybackElement(element, options) {
      if (playbackElement === element) { if (options && options.channel) playbackChannel = String(options.channel); return playbackElement; }
      if (playbackElement) { try { playbackElement.pause(); } catch (_) {} resetAudioHandlers(playbackElement); }
      playbackElement = element || null;
      playbackChannel = options && options.channel ? String(options.channel) : (element ? 'avatar' : 'unbound');
      return playbackElement;
    }
    function speakBrowser(value, options, currentToken, fallbackReason) {
      if (!browserSupported()) {
        if (currentToken === token && typeof options.onError === 'function') options.onError(new Error(fallbackReason || '语音服务不可用。'));
        return false;
      }
      activeProvider = 'browser_speech_synthesis_fallback';
      var utterance = new Utterance(value);
      var p = preset(options.preset || 'chat_read_aloud');
      activeUtterance = utterance;
      utterance.lang = options.lang || VOICE_PERSONA.language;
      utterance.rate = Number(options.rate || p.rate);
      utterance.pitch = Number(options.pitch || p.pitch);
      utterance.volume = Number(options.volume || p.volume);
      utterance.voice = resolveVoice();
      utterance.onstart = function () {
        if (currentToken === token && typeof options.onStart === 'function') options.onStart({provider:activeProvider,voice_uri:utterance.voice && utterance.voice.voiceURI || null,real_audio_envelope:false});
      };
      utterance.onboundary = function (event) { if (currentToken === token && typeof options.onBoundary === 'function') options.onBoundary(event || {}); };
      utterance.onpause = function () { if (currentToken === token && typeof options.onPause === 'function') options.onPause(); };
      utterance.onresume = function () { if (currentToken === token && typeof options.onResume === 'function') options.onResume(); };
      utterance.onend = function () { if (currentToken !== token) return;activeUtterance=null;activeProvider='none';if(typeof options.onEnd==='function')options.onEnd(); };
      utterance.onerror = function (event) { if(currentToken!==token)return;activeUtterance=null;activeProvider='none';if(typeof options.onError==='function')options.onError(event&&event.error?new Error(event.error):new Error('语音朗读失败。')); };
      synth.speak(utterance);
      return true;
    }
    function speakReal(value, options, currentToken, allowFallback) {
      var api = apiClient();
      if (!api || typeof api.generateLessonSpeech !== 'function' || !playbackElement || !env.URL || typeof env.URL.createObjectURL !== 'function') return Promise.resolve(false);
      realProviderStatus = 'CHECKING';
      activeProvider = 'edge_tts_existing_video_speech';
      // 真实 TTS 稳定性：后端已做多次重试+备用音色；前端遇到偶发失败先重试真实 TTS，
      // 连续失败达到阈值才降级浏览器声（避免一次网络抖动就换声音人设）。
      var attempt = 0;
      var MAX_ATTEMPTS = 2;
      function attemptReal() {
        attempt += 1;
        return Promise.resolve(api.generateLessonSpeech(value)).then(function (blob) {
          if (currentToken !== token) return true;
          if (!blob || !Number(blob.size || 0)) throw new Error('真实 TTS 返回空音频。');
          realProviderStatus = 'AVAILABLE';lastProviderError='';
          realConsecutiveFailures = 0;
          revokeUrl();objectUrl=env.URL.createObjectURL(blob);
          var audio=playbackElement;resetAudioHandlers(audio);audio.preload='auto';audio.src=objectUrl;
          var lastBoundary=-1;
          audio.onplay=function(){if(currentToken===token&&typeof options.onStart==='function')options.onStart({provider:activeProvider,voice_id:VOICE_PERSONA.server_voice_id,real_audio_envelope:true,audio_element:audio});};
          audio.onpause=function(){if(currentToken===token&&typeof options.onPause==='function')options.onPause();};
          audio.ontimeupdate=function(){
            if(currentToken!==token||typeof options.onBoundary!=='function')return;
            var duration=Number(audio.duration)||0,current=Number(audio.currentTime)||0;
            if(!duration)return;var charIndex=Math.min(value.length-1,Math.floor(current/duration*Math.max(1,value.length)));
            if(charIndex!==lastBoundary){lastBoundary=charIndex;options.onBoundary({charIndex:charIndex,elapsed:current,duration:duration,provider:activeProvider,real_audio_envelope:true});}
          };
          audio.onended=function(){if(currentToken!==token)return;resetAudioHandlers(audio);revokeUrl();activeProvider='none';if(typeof options.onEnd==='function')options.onEnd();};
          audio.onerror=function(){if(currentToken!==token)return;var error=new Error('真实 TTS 音频播放失败。');realProviderStatus='REAL_VOICE_PROVIDER_BLOCKED';lastProviderError=error.message;resetAudioHandlers(audio);revokeUrl();activeProvider='none';if(allowFallback&&browserSupported()){if(typeof options.onProviderFallback==='function')options.onProviderFallback(error);speakBrowser(value,options,currentToken,error.message);}else if(typeof options.onError==='function')options.onError(error);};
          var playResult=audio.play();
          return Promise.resolve(playResult).then(function(){return true;}).catch(function(error){throw error||new Error('真实 TTS 播放被浏览器阻止。');});
        }).catch(function(error){
          if(currentToken!==token)return true;
          realProviderStatus='REAL_VOICE_PROVIDER_BLOCKED';lastProviderError=error&&error.message||'真实 TTS 不可用';activeProvider='none';
          // 后端已内置重试，这里再补一次前端重试（应对后端瞬时 5xx/超时）；未被新 speak 打断才重试
          if(attempt < MAX_ATTEMPTS && currentToken === token){
            return new Promise(function(resolve){env.setTimeout(function(){resolve(attemptReal());},600*attempt);});
          }
          realConsecutiveFailures += 1;
          var shouldFallback = allowFallback && browserSupported() && realConsecutiveFailures >= 3;
          if(shouldFallback){if(typeof options.onProviderFallback==='function')options.onProviderFallback(error);return speakBrowser(value,options,currentToken,lastProviderError);}
          if(typeof options.onError==='function')options.onError(error instanceof Error?error:new Error(lastProviderError));
          return false;
        });
      }
      return attemptReal();
    }
    /* 声音统一：产品路径只允许柿小吉的自然 TTS（Edge TTS 神经语音）被听见。
       浏览器 SpeechSynthesis 是隐形的最后兜底，只有调用方显式传
       allowBrowserFallback:true 才会发声；正常演示路径绝不自动降级，
       避免对话中途切换到另一个声音人设。 */
    function speak(text, options) {
      options=options||{};var value=String(text||'').trim();if(!value){if(typeof options.onEnd==='function')options.onEnd();return false;}
      stop();var currentToken=token;
      var allowFallback=options.allowBrowserFallback===true;
      if(realProviderConfigured()&&playbackElement){speakReal(value,options,currentToken,allowFallback);return true;}
      if(realProviderConfigured()&&!playbackElement){realProviderStatus='REAL_VOICE_PROVIDER_BLOCKED';lastProviderError='Avatar audio channel unavailable';}
      if(!allowFallback){
        if(typeof options.onError==='function')options.onError(new Error(lastProviderError||'柿小吉的声音暂时不可用。'));
        return false;
      }
      return speakBrowser(value,options,currentToken,lastProviderError);
    }
    function isSpeaking(){if(playbackElement&&activeProvider==='edge_tts_existing_video_speech')return Boolean(!playbackElement.paused&&!playbackElement.ended);return Boolean(activeUtterance&&synth&&synth.speaking);}
    function isPaused(){if(playbackElement&&activeProvider==='edge_tts_existing_video_speech')return Boolean(playbackElement.paused&&!playbackElement.ended);return Boolean(synth&&synth.paused);}
    function getProviderStatus(){return realProviderStatus==='AVAILABLE'?'REAL_VOICE_PROVIDER_AVAILABLE':'REAL_VOICE_PROVIDER_BLOCKED';}
    function getPersona(){
      var voice=resolveVoice();
      return {
        voice_profile:VOICE_PERSONA.profile,voice_id:VOICE_PERSONA.id,server_voice_id:VOICE_PERSONA.server_voice_id,
        browser_voice_uri:voice&&voice.voiceURI||null,provider:activeProvider!=='none'?activeProvider:(realProviderConfigured()?'edge_tts_existing_video_speech':(browserSupported()?'browser_speech_synthesis_fallback':'unavailable')),
        real_provider_status:getProviderStatus(),real_provider_configured:realProviderConfigured(),voice_available:Boolean(realProviderStatus==='AVAILABLE'||voice),
        playback_channel:playbackChannel,last_provider_error:lastProviderError||null,
        provenance:realProviderConfigured()?'/api/video/speech → existing Edge TTS → HTMLMediaElement → Avatar WebAudio analyser':'Web Speech API fallback only',presets:VOICE_PERSONA.presets
      };
    }
    return {speak:speak,stop:stop,isSpeaking:isSpeaking,isPaused:isPaused,supported:supported,browserSupported:browserSupported,resolveVoice:resolveVoice,getPersona:getPersona,getProviderStatus:getProviderStatus,setPlaybackElement:setPlaybackElement,getPlaybackElement:function(){return playbackElement;}};
  }

  return {VOICE_PERSONA:VOICE_PERSONA,toSpeakableText:toSpeakableText,prepareSpeechSegments:prepareSpeechSegments,createSpeechAdapter:createSpeechAdapter};
}));
