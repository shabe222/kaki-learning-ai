(function(root,factory){
  var api=factory();
  if(typeof module==='object'&&module.exports)module.exports=api;
  if(root){root.KakiAvatarRuntime=api;root.AIWorkspaceAvatar=api;if(root.document)root.digitalTutorAdapter=api.createAvatarAdapter(root);}
}(typeof window!=='undefined'?window:globalThis,function(){
  'use strict';

  var RUNTIME_VERSION='KAKI_AVATAR_RUNTIME_V3_ANIMATABLE_ASSET';
  var RENDERER_INTERFACE='KAKI_AVATAR_RENDERER_V3';
  var RENDERER_KIND='animatable_asset_canvas';
  var DEFAULT_RUNTIME_URL='/ai-workspace/assets/avatar/kaki-animatable/runtime/index.mjs';
  var DEFAULT_ASSET_BASE='/ai-workspace/assets/avatar/kaki-animatable/asset-pack';
  var STATE_LABELS=Object.freeze({
    idle:'陪伴中',listening:'正在听你说',thinking:'正在思考',speaking:'正在说话',
    explaining:'正在讲解',teaching:'正在讲解',encouraging:'给你一点鼓励',celebrating:'完成啦',interrupted:'已被你打断',
    asking:'正在听你说',praise:'给你一点鼓励',correction:'一起再想想',complete:'完成啦'
  });
  var STATE_ALIASES=Object.freeze({asking:'listening',teaching:'teaching',praise:'encouraging',correction:'encouraging',complete:'celebrating'});
  var DRIVER_STATES=Object.freeze({
    idle:'IDLE',listening:'LISTENING',thinking:'THINKING',speaking:'SPEAKING',
    explaining:'TEACHING',teaching:'TEACHING',encouraging:'ENCOURAGING',celebrating:'ENCOURAGING',interrupted:'INTERRUPTED'
  });

  function normalizedState(value){
    var s=STATE_ALIASES[String(value||'').toLowerCase()]||String(value||'').toLowerCase();
    return Object.prototype.hasOwnProperty.call(DRIVER_STATES,s)?s:'idle';
  }
  function clamp(value,min,max){value=Number(value)||0;return Math.max(min,Math.min(max,value));}
  function safeStorage(env){try{return env&&env.localStorage||null;}catch(_){return null;}}
  function dynamicImport(url){return import(url);}
  function createUnavailable(doc,host,status){
    var box=doc.createElement('div');box.className='kaki-live2d-unavailable kaki-animatable-unavailable';box.dataset.assetStatus=status||'asset_required';
    box.setAttribute('role','status');box.setAttribute('aria-label','柿小吉实时动画资源暂不可用');
    var mark=doc.createElement('div');mark.className='kaki-live2d-unavailable-mark';mark.textContent='柿';
    var copy=doc.createElement('div');copy.className='kaki-live2d-unavailable-copy';
    var strong=doc.createElement('strong');strong.textContent='柿小吉实时动画暂未就绪';
    var small=doc.createElement('small');small.textContent='系统不会用整人物静态切图或演示假音频冒充实时数字导师。';
    copy.append(strong,small);box.append(mark,copy);host.appendChild(box);return box;
  }

  function createRenderer(environment,host,options){
    var env=environment||{},doc=env.document||(host&&host.ownerDocument),opts=options||{};
    if(!host||!doc)return null;
    var state='idle',teaching=false,gesture='rest',speechActive=false,speechEnvelope=0,destroyed=false;
    var driver=null,assetStatus='loading',modelReady=false,readyResolve,readyReject,unavailable=null,attachedAudio=null;
    var runtimeUrl=opts.runtimeUrl||env.KAKI_ANIMATABLE_RUNTIME_URL||DEFAULT_RUNTIME_URL;
    var assetBase=opts.assetBase||env.KAKI_ANIMATABLE_ASSET_BASE||DEFAULT_ASSET_BASE;
    var importer=opts.importer||dynamicImport;
    var readyPromise=new Promise(function(resolve,reject){readyResolve=resolve;readyReject=reject;});
    host.innerHTML='';host.dataset.renderer=RENDERER_KIND;host.dataset.runtimeVersion=RUNTIME_VERSION;host.dataset.assetStatus='loading';
    var audio=doc.createElement('audio');audio.className='kaki-avatar-audio-channel';audio.hidden=true;audio.preload='auto';audio.setAttribute('aria-hidden','true');host.appendChild(audio);

    function expose(instance){
      if(!env)return;
      env.KakiAnimatableAvatarDriver=instance;
      env.KakiDesktopPetDriver=instance;
    }
    function driverCall(name){
      if(!driver||typeof driver[name]!=='function')return null;
      var args=Array.prototype.slice.call(arguments,1);
      try{return driver[name].apply(driver,args);}catch(error){assetStatus='runtime_error';host.dataset.assetStatus=assetStatus;return null;}
    }
    function syncState(){
      if(!driver)return;
      driverCall('setState',DRIVER_STATES[state]||'IDLE');
      if(teaching){driverCall('setProfessorMode',true);driverCall('setPointerVisible',true);}else{driverCall('setProfessorMode',false);driverCall('setPointerVisible',false);}
      if(speechActive)driverCall('setSpeaking',true);
      if(speechEnvelope>0)driverCall('setMouthOpen',speechEnvelope);
    }
    function fail(error){
      if(destroyed)return;modelReady=false;assetStatus='asset_required';host.dataset.assetStatus=assetStatus;
      if(unavailable&&unavailable.parentNode)unavailable.parentNode.removeChild(unavailable);
      unavailable=createUnavailable(doc,host,assetStatus);
      readyResolve(false);
      if(env.console&&env.console.warn)env.console.warn('[KakiAvatarRuntime] animatable asset init failed',error);
    }
    Promise.resolve().then(function(){return importer(runtimeUrl);}).then(function(module){
      if(destroyed)return false;
      var Driver=module&&module.KakiAnimatableAvatarDriver;
      if(typeof Driver!=='function')throw new Error('KakiAnimatableAvatarDriver export missing');
      driver=new Driver({container:host,assetBase:assetBase,seed:20260809});
      return driver.init();
    }).then(function(){
      if(destroyed||!driver)return;
      modelReady=true;assetStatus='ready';host.dataset.assetStatus=assetStatus;
      if(unavailable&&unavailable.parentNode)unavailable.parentNode.removeChild(unavailable);unavailable=null;
      var canvas=driver.canvas;if(canvas){canvas.classList.add('kaki-live2d-canvas');canvas.classList.add('kaki-animatable-avatar-canvas');}
      expose(driver);syncState();
      return Promise.resolve(driver.attachAudioElement(audio)).catch(function(error){if(env.console&&env.console.warn)env.console.warn('[KakiAvatarRuntime] audio channel attach failed',error);});
    }).then(function(){if(modelReady){attachedAudio=audio;readyResolve(true);}}).catch(fail);

    function pointFor(options){
      options=options||{};var step=Math.max(0,Number(options.step)||0),total=Math.max(1,Number(options.total)||5);
      if(options.target&&Number.isFinite(Number(options.target.x))&&Number.isFinite(Number(options.target.y)))return {x:Number(options.target.x),y:Number(options.target.y)};
      var ratio=total<=1?.5:clamp(step/(total-1),0,1);
      return {x:64,y:245+ratio*330};
    }
    function setState(next){state=normalizedState(next);driverCall('setState',DRIVER_STATES[state]||'IDLE');return state;}
    function setTeaching(active){
      teaching=Boolean(active);
      if(teaching){state='teaching';driverCall('setState','TEACHING');driverCall('setProfessorMode',true);driverCall('setPointerVisible',true);driverCall('setAttention','ATTENTION_BOARD');}
      else{driverCall('setPointerVisible',false);driverCall('setProfessorMode',false);if(state==='teaching'||state==='explaining')state='listening';driverCall('setState',DRIVER_STATES[state]||'LISTENING');driverCall('setAttention','ATTENTION_USER');}
    }
    function interrupt(){
      speechActive=false;speechEnvelope=0;gesture='rest';teaching=false;
      if(audio&&!audio.paused){try{audio.pause();audio.currentTime=0;}catch(_){}}
      driverCall('interrupt');state='interrupted';
    }
    function attachAudioElement(element){
      if(!element||element===attachedAudio)return Promise.resolve(Boolean(driver));
      attachedAudio=element;
      return readyPromise.then(function(ok){if(!ok||!driver)return false;return Promise.resolve(driver.attachAudioElement(element)).then(function(){return true;});});
    }
    return {
      interface_version:RENDERER_INTERFACE,runtime_version:RUNTIME_VERSION,renderer_kind:RENDERER_KIND,runtime_url:runtimeUrl,asset_base:assetBase,
      ready:function(){return readyPromise;},isModelReady:function(){return modelReady;},getAssetStatus:function(){return assetStatus;},
      getAudioElement:function(){return audio;},attachAudioElement:attachAudioElement,
      setState:setState,
      setSpeechActive:function(active){speechActive=Boolean(active);if(!speechActive){speechEnvelope=0;driverCall('setMouthOpen',0);}driverCall('setSpeaking',speechActive);if(speechActive&&state!=='teaching')setState('speaking');},
      setSpeechEnvelope:function(value){speechEnvelope=speechActive?clamp(value,0,1):0;driverCall('setMouthOpen',speechEnvelope);},
      setTeaching:setTeaching,
      setAttention:function(name){driverCall('setAttention',name);},
      setPointerTarget:function(x,y){driverCall('setPointerTarget',x,y);},
      setTeachingTarget:function(options){var target=pointFor(options);driverCall('setAttention','ATTENTION_POINTER_TARGET');driverCall('setPointerVisible',true);driverCall('setPointerTarget',target.x,target.y);driverCall('playGesture','point');return target;},
      lookAtUser:function(){driverCall('setAttention','ATTENTION_USER');},
      setGesture:function(next,gestureOptions){gesture=next||'rest';if(gesture==='point'){var target=pointFor(gestureOptions);driverCall('setPointerTarget',target.x,target.y);driverCall('setPointerVisible',true);driverCall('setAttention','ATTENTION_POINTER_TARGET');driverCall('playGesture','point');}else if(gesture==='encourage')driverCall('playGesture','encourage');else if(gesture==='wave')driverCall('playGesture','wave');else if(gesture==='explain')driverCall('playGesture','explain');},
      setGaze:function(x,y){driverCall('setLook',clamp(x,-1,1),clamp(y,-1,1));},
      interrupt:interrupt,
      getState:function(){var diagnostics=driver&&driver.getDiagnostics?driver.getDiagnostics():null;return{state:state,speech_active:speechActive,speech_envelope:speechEnvelope,teaching:teaching,gesture:gesture,renderer_kind:RENDERER_KIND,runtime_version:RUNTIME_VERSION,model_ready:modelReady,asset_status:assetStatus,diagnostics:diagnostics};},
      destroy:function(){destroyed=true;try{if(audio){audio.pause();audio.removeAttribute('src');audio.load();}}catch(_){}if(driver&&driver.destroy)Promise.resolve(driver.destroy()).catch(function(){});driver=null;modelReady=false;host.innerHTML='';if(readyReject&&assetStatus==='loading')readyResolve(false);}
    };
  }

  function createAvatarAdapter(environment){
    var env=environment||{},doc=env.document,storage=safeStorage(env),root=doc&&doc.getElementById('digitalTutor'),host=doc&&doc.getElementById('digitalTutorSprite'),subtitle=doc&&doc.getElementById('digitalTutorSubtitle'),status=doc&&doc.getElementById('digitalTutorStatus'),toggle=doc&&doc.getElementById('digitalTutorToggle');
    var renderer=host?createRenderer(env,host):null,state='idle',currentMode=null,collapsed=false,timer=null,key='ai_workspace_avatar_collapsed_v3';
    try{collapsed=Boolean(storage&&storage.getItem(key)==='true');}catch(_){}
    function applyCollapsed(){if(!root)return;root.classList.toggle('is-collapsed',collapsed);if(toggle){toggle.textContent=collapsed?'展开柿小吉':'收起';toggle.setAttribute('aria-expanded',collapsed?'false':'true');toggle.title=collapsed?'展开数字导师':'收起数字导师';}try{if(storage)storage.setItem(key,collapsed?'true':'false');}catch(_){}}
    function setCollapsed(v){collapsed=Boolean(v);applyCollapsed();}
    function setState(next,options){options=options||{};var prev=state;state=normalizedState(next);if(timer)(env.clearTimeout||clearTimeout)(timer);timer=null;if(subtitle&&Object.prototype.hasOwnProperty.call(options,'subtitle'))subtitle.textContent=String(options.subtitle||'');if(status)status.textContent=STATE_LABELS[next]||STATE_LABELS[state]||STATE_LABELS.idle;if(root)root.dataset.state=state;if(renderer)renderer.setState(state);if(options.expand===true)setCollapsed(false);if(env.dispatchEvent&&env.CustomEvent)env.dispatchEvent(new env.CustomEvent('kaki:digital-tutor-state',{detail:{state:state,previousState:prev,subtitle:options.subtitle||''}}));if(options.transientMs)timer=(env.setTimeout||setTimeout)(function(){setState(options.returnState||'idle',{subtitle:options.returnSubtitle||''});},options.transientMs);return state;}
    function setMode(mode){currentMode=mode==='tutor'?'tutor':(mode==='assistant'?'assistant':null);if(root)root.hidden=true;/* 数字人收口：陪伴舞台不再单独出现，全局唯一柿小吉由 MascotCompanionHost 呈现；状态仍经 kaki:digital-tutor-state 事件桥同步，音频通道保持不变。 */if(currentMode){applyCollapsed();setState('idle',{subtitle:''});}}
    if(toggle)toggle.addEventListener('click',function(){setCollapsed(!collapsed);});applyCollapsed();if(root){root.dataset.renderer=RENDERER_KIND;root.dataset.avatarRuntime=RUNTIME_VERSION;}
    if(renderer&&env.speechAdapter&&env.speechAdapter.setPlaybackElement)env.speechAdapter.setPlaybackElement(renderer.getAudioElement(),{channel:'companion'});
    return {
      renderer_interface:RENDERER_INTERFACE,renderer_kind:RENDERER_KIND,runtime_version:RUNTIME_VERSION,
      setMode:setMode,setState:setState,setCollapsed:setCollapsed,
      setSpeechActive:function(active){if(renderer)renderer.setSpeechActive(active);if(active)setState('speaking',{subtitle:subtitle&&subtitle.textContent||''});},
      setSpeechEnvelope:function(v){if(renderer&&renderer.setSpeechEnvelope)renderer.setSpeechEnvelope(v);},
      setTeaching:function(active){if(renderer)renderer.setTeaching(active);if(root)root.classList.toggle('is-teaching',Boolean(active));},
      setGesture:function(name,options){if(renderer)renderer.setGesture(name,options);},setGaze:function(x,y){if(renderer&&renderer.setGaze)renderer.setGaze(x,y);},
      setAttention:function(name){if(renderer&&renderer.setAttention)renderer.setAttention(name);},
      setTeachingTarget:function(options){return renderer&&renderer.setTeachingTarget?renderer.setTeachingTarget(options):null;},
      lookAtUser:function(){if(renderer&&renderer.lookAtUser)renderer.lookAtUser();},interrupt:function(){if(renderer&&renderer.interrupt)renderer.interrupt();},
      getAudioElement:function(){return renderer&&renderer.getAudioElement?renderer.getAudioElement():null;},
      getState:function(){return renderer?renderer.getState():{state:state,renderer_kind:RENDERER_KIND,model_ready:false,asset_status:'host_unavailable'};},
      getAssetStatus:function(){return renderer&&renderer.getAssetStatus?renderer.getAssetStatus():'host_unavailable';},
      createRenderer:function(target,options){return createRenderer(env,target,options);},destroy:function(){if(timer)(env.clearTimeout||clearTimeout)(timer);if(renderer)renderer.destroy();}
    };
  }

  return {RUNTIME_VERSION:RUNTIME_VERSION,RENDERER_INTERFACE:RENDERER_INTERFACE,RENDERER_KIND:RENDERER_KIND,DEFAULT_RUNTIME_URL:DEFAULT_RUNTIME_URL,DEFAULT_ASSET_BASE:DEFAULT_ASSET_BASE,STATE_LABELS:STATE_LABELS,createRenderer:createRenderer,createAvatarAdapter:createAvatarAdapter};
}));
