(function () {
    'use strict';

    var api = window.apiClient;
    var active = false;
    var messages = [];
    var lastFailedMessage = '';
    var lastFailedMessageId = '';
    var memoriesLoaded = false;
    var debugInfo = {};
    var temporaryStyle = null;
    var ignoredCandidateKeys = {};
    var confirmedMemories = [];
    var activeArtifact = null;
    var codeArtifactViews = {};
    var taskState = window.AIWorkspaceAgents.createTaskState();
    var runtimeStatus = window.AIWorkspaceState.createRuntimeStatus();
    var runtimePollTimer = null;
    var runtimeIdleTimer = null;
    var inputAdapterBusy = false;
    var workspaceMaterials = [];
    var workspaceMaterialLibrary = [];
    var pendingMaterialIds = [];
    var activeMaterialId = '';
    var replyTarget = null;
    var inputCapabilities = { checked: false, backendAsr: false, browserAsr: false };
    var browserRecognition = null;
    var currentConversationMode = null;
    var tutorPlan = null;
    var tutorPedagogyState = {};
    var conversationTransitionVersion = 0;
    var selectedImageFile = null;
    var selectedImageUrl = '';
    var mediaRecorder = null;
    var mediaStream = null;
    var recordedChunks = [];
    var recordingStartedAt = 0;
    var recordingTimer = null;
    var cancelCurrentRecording = false;
    var lastAudioBlob = null;
    var lastAudioFileName = 'workspace-recording.webm';
    var speechAdapter = window.speechAdapter;
    var avatarAdapter = window.digitalTutorAdapter;
    var activeSpeechButton = null;
    var SIDEBAR_STATE_KEY = 'ai_workspace_sidebar_state_v1';
    var sidebarState = { sidebarCollapsed: false, historyCollapsed: false };
    var currentArtifactFilter = 'all';
    var myMaterialsRows = [];
    var myMaterialsCategory = 'all';
    var myMaterialsReturn = null;
    var selfUnderstandingReturn = null;
    var selfUnderstandingData = null;
    var $ = function (id) { return document.getElementById(id); };
    var key = {
        token: 'ai_workspace_test_token',
        userId: 'ai_workspace_test_user_id',
        threadId: 'ai_workspace_test_thread_id'
    };
    function token() {
        return sessionStorage.getItem(key.token)
            || localStorage.getItem('auth_token')
            || localStorage.getItem('ml_auth_token') || '';
    }
    var embedded = Boolean($('workspaceApp'));
    var scrollController = window.AIWorkspaceScroll.createScrollController(
        function () { return $('messages'); },
        function () { return $('returnToLatest'); }
    );
    function appRoot() { return $('workspaceApp') || $('app'); }
    function loadSidebarState() {
        try {
            var stored = JSON.parse(localStorage.getItem(SIDEBAR_STATE_KEY) || '{}');
            sidebarState.sidebarCollapsed = stored.sidebarCollapsed === true;
            sidebarState.historyCollapsed = stored.historyCollapsed === true;
        } catch (ignore) {
            sidebarState = { sidebarCollapsed: false, historyCollapsed: false };
        }
    }
    function saveSidebarState() {
        localStorage.setItem(SIDEBAR_STATE_KEY, JSON.stringify(sidebarState));
    }
    function applySidebarState() {
        var root = appRoot();
        var sidebarToggle = $('sidebarToggle');
        var historyToggle = $('historyToggle');
        var historyPanel = $('historyPanel');
        if (!root || !sidebarToggle || !historyToggle || !historyPanel) return;
        root.classList.toggle('sidebar-collapsed', sidebarState.sidebarCollapsed);
        sidebarToggle.textContent = sidebarState.sidebarCollapsed ? '›' : '‹';
        sidebarToggle.setAttribute('aria-expanded', sidebarState.sidebarCollapsed ? 'false' : 'true');
        sidebarToggle.title = sidebarState.sidebarCollapsed ? '展开整个侧栏' : '收起整个侧栏';
        historyPanel.hidden = sidebarState.historyCollapsed;
        historyToggle.textContent = sidebarState.historyCollapsed ? '⌄' : '⌃';
        historyToggle.setAttribute('aria-expanded', sidebarState.historyCollapsed ? 'false' : 'true');
        historyToggle.title = sidebarState.historyCollapsed ? '展开历史会话' : '收起历史会话';
    }
    function syncReturnToLatestOffset() {
        var control = $('returnToLatest');
        var composer = $('composerShell') && $('composerShell').parentElement;
        if (!control || !composer) return;
        control.style.bottom = Math.ceil(composer.getBoundingClientRect().height + 12) + 'px';
    }

    function currentThread() { return sessionStorage.getItem(key.threadId); }
    function applyPendingVideoIntent() {
        var prompt = sessionStorage.getItem('kaki_video_generation_intent_v1') || '';
        if (!prompt || !$('input')) return;
        sessionStorage.removeItem('kaki_video_generation_intent_v1');
        $('input').value = prompt;
        $('input').dispatchEvent(new Event('input', { bubbles:true }));
        $('input').focus();
        setStatus('已带入视频生成请求，请补充主题后发送。');
    }
    function beginConversationTransition() {
        conversationTransitionVersion += 1;
        return conversationTransitionVersion;
    }
    function isConversationTransitionCurrent(version, threadId) {
        return version === conversationTransitionVersion
            && (!threadId || threadId === currentThread());
    }
    function setThread(threadId) {
        sessionStorage.setItem(key.threadId, threadId);
        api.threadId = threadId;
        pendingMaterialIds = [];
        activeMaterialId = sessionStorage.getItem('ai_workspace_active_material_' + threadId) || '';
        clearReplyTarget();
        if (window.KakiWorkspaceContextBridge) window.KakiWorkspaceContextBridge.load(threadId);
    }
    function taskStorageKey() { return 'ai_workspace_task_' + (currentThread() || 'none'); }
    function saveTaskState() { sessionStorage.setItem(taskStorageKey(), JSON.stringify(taskState)); if (window.KakiWorkspaceContextBridge) window.KakiWorkspaceContextBridge.setTask(taskState); }
    function loadTaskState(rawMessages) {
        try { taskState = JSON.parse(sessionStorage.getItem(taskStorageKey())) || window.AIWorkspaceAgents.createTaskState(); }
        catch (error) { taskState = window.AIWorkspaceAgents.createTaskState(); }
        if (taskState.status === 'idle' && Array.isArray(rawMessages)) {
            var planned = rawMessages.slice().reverse().find(function (item) {
                return item.task && (item.task.status === 'awaiting_confirmation' || item.task.status === 'needs_details');
            });
            var failed = rawMessages.slice().reverse().find(function (item) { return item.task && item.task.status === 'failed'; });
            var cancelled = rawMessages.slice().reverse().find(function (item) { return item.task && item.task.status === 'cancelled'; });
            var completed = rawMessages.slice().reverse().find(function (item) { return item.artifact; });
            if (cancelled && (!planned || rawMessages.indexOf(cancelled) > rawMessages.indexOf(planned))) {
                taskState = window.AIWorkspaceAgents.createTaskState();
            } else if (failed && (!completed || rawMessages.indexOf(failed) > rawMessages.indexOf(completed))) {
                taskState = window.AIWorkspaceAgents.transitionTask(
                    window.AIWorkspaceAgents.transitionTask(taskState, 'planned', failed.task),
                    'failed',
                    { message: failed.content }
                );
            } else if (planned && (!completed || rawMessages.indexOf(planned) > rawMessages.indexOf(completed))) {
                taskState = window.AIWorkspaceAgents.transitionTask(taskState, 'planned', planned.task);
            }
        }
        saveTaskState();
    }
    function shortId(value) { return value ? value.slice(0, 14) + '…' : '—'; }
    function renderConversationMode() {
        // Conversation mode remains an internal compatibility detail. DARYA is
        // the single visible intelligence; users never operate a Tutor/Assistant switch.
        if (avatarAdapter) avatarAdapter.setMode(currentConversationMode);
    }
    function setAvatarState(state, subtitle, options) {
        if (!avatarAdapter) return;
        avatarAdapter.setState(state, Object.assign({ subtitle: subtitle || '' }, options || {}));
    }
    function renderRuntimeStatus() {
        $('meta').textContent = 'Backend: ' + runtimeStatus.backend + ' · Auth: ' + runtimeStatus.auth
            + ' · Provider: ' + runtimeStatus.provider
            + ' · 运行: ' + window.AIWorkspaceState.runtimeOperationLabel(runtimeStatus.operation);
    }
    function transitionRuntime(event) {
        runtimeStatus = window.AIWorkspaceState.transitionRuntimeStatus(runtimeStatus, event);
        renderRuntimeStatus();
    }
    function applyRuntimeSnapshot(snapshot) {
        runtimeStatus = window.AIWorkspaceState.applyRuntimeSnapshot(runtimeStatus, snapshot || {});
        renderRuntimeStatus();
    }
    async function syncRuntimeStatus() {
        if (!currentThread()) {
            try {
                var health = await api.getHealth();
                var snapshot = {
                    backend: (health && health.backend) || (health && health.status === 'ok' ? 'connected' : 'error'),
                    provider: (health && health.provider) || ((health && health.llm && health.llm.mode) || 'unknown'),
                    auth: token() ? 'valid' : 'expired'
                };
                applyRuntimeSnapshot(snapshot);
            } catch (error) {
                transitionRuntime('backend_failure');
            }
            return;
        }
        try {
            var response = await api.getRuntimeStatus();
            var data = (response && response.data) || {};
            if (!data.backend) {
                var healthFallback = await api.getHealth().catch(function() { return null; });
                if (healthFallback) {
                    data.backend = healthFallback.backend || (healthFallback.status === 'ok' ? 'connected' : 'error');
                    data.provider = data.provider || healthFallback.provider || ((healthFallback.llm && healthFallback.llm.mode) || 'unknown');
                }
            }
            applyRuntimeSnapshot(data);
        } catch (error) {
            if (error && error.status === 401) transitionRuntime('auth_expired');
            else transitionRuntime('backend_failure');
        }
    }
    function startRuntimePolling() {
        if (embedded) return;
        if (runtimeIdleTimer) window.clearTimeout(runtimeIdleTimer);
        if (runtimePollTimer) window.clearInterval(runtimePollTimer);
        syncRuntimeStatus();
        runtimePollTimer = window.setInterval(syncRuntimeStatus, 300);
    }
    function stopRuntimePolling() {
        if (runtimePollTimer) window.clearInterval(runtimePollTimer);
        runtimePollTimer = null;
        syncRuntimeStatus();
        runtimeIdleTimer = window.setTimeout(syncRuntimeStatus, 3200);
    }
    function setStatus(text) { $('status').textContent = text || ''; }
    function readCallPreference() {
        try {
            var raw = localStorage.getItem('kaki_call_preference_v1');
            if (raw) { var parsed = JSON.parse(raw); return { accepts: Number(parsed.accepts) || 0, declines: Number(parsed.declines) || 0 }; }
        } catch (ignore) {}
        return { accepts: 0, declines: 0 };
    }
    function recordCallPreference(accepted) {
        var pref = readCallPreference();
        if (accepted) { pref.accepts += 1; pref.declines = 0; }
        else { pref.declines += 1; }
        try { localStorage.setItem('kaki_call_preference_v1', JSON.stringify(pref)); } catch (ignore) {}
        return pref;
    }
    function lastVoiceSummary() {
        try {
            var raw = localStorage.getItem('kaki_last_voice_summary_v1');
            if (raw) { var parsed = JSON.parse(raw); if (parsed && parsed.summary) return parsed; }
        } catch (ignore) {}
        return null;
    }
    function naturalTaskError(error) {
        var message = String(error && error.message || '暂时无法完成，请稍后重试。');
        if (/duration_minutes/.test(message)) return '互动课堂时长需要在 1 到 60 分钟之间。';
        if (/slide_count/.test(message)) return 'PPT 页数需要在 1 到 30 页之间。';
        return message;
    }
    function setDebug(info) {
        debugInfo = Object.assign({}, debugInfo, info || {});
        $('debug').textContent = [
            'thread: ' + shortId(currentThread()),
            'HTTP: ' + (debugInfo.http || '—'),
            'request_id: ' + (debugInfo.requestId || '—'),
            'code: ' + (debugInfo.code || '—'),
            'stage: ' + (debugInfo.stage || '—'),
            'SSE completed: ' + (debugInfo.completed ? 'yes' : 'no'),
            'memory_loaded: ' + (debugInfo.memory_loaded === true ? 'true' : debugInfo.memory_loaded === false ? 'false' : '—'),
            'memory_keys: ' + JSON.stringify(debugInfo.memory_keys || []),
            'effective_response_style: ' + (debugInfo.effective_response_style || '—'),
            'memory_source: ' + (debugInfo.memory_source || '—'),
            'source_material_ids: ' + JSON.stringify(debugInfo.source_material_ids || []),
            'source_citations: ' + JSON.stringify(debugInfo.source_citations || []),
            'export_evidence: ' + JSON.stringify(debugInfo.export_evidence || null),
            'message: ' + (debugInfo.message || '—')
        ].join('\n');
    }
    function isLegacy(threadId) {
        var normalized = (threadId || '').trim().toLowerCase();
        return !normalized || normalized === 'default' || normalized.indexOf('qa-') === 0;
    }
    function clearMessages() {
        stopSpeechPlayback();
        messages = [];
        $('messages').innerHTML = '';
    }
    function setSpeechButtonState(button, state) {
        if (!button) return;
        var labels = {
            idle: '🔊 朗读',
            speaking: '■ 停止',
            continue: '🔊 继续朗读',
            failed: '朗读失败'
        };
        button.dataset.state = state;
        button.textContent = labels[state] || labels.idle;
        button.setAttribute('aria-pressed', state === 'speaking' ? 'true' : 'false');
        button.setAttribute('aria-label', state === 'speaking' ? '正在朗读，点击停止' : button.textContent);
        button.title = state === 'speaking' ? '正在朗读，再次点击停止' : button.textContent;
    }
    function stopSpeechPlayback() {
        if (speechAdapter) speechAdapter.stop();
        if (avatarAdapter && avatarAdapter.setSpeechActive) avatarAdapter.setSpeechActive(false);
        if (activeSpeechButton) {
            activeSpeechButton._speechIndex = 0;
            setSpeechButtonState(activeSpeechButton, 'idle');
        }
        activeSpeechButton = null;
        setAvatarState('idle', '');
    }
    function configureSpeechButton(button, content) {
        if (!button || !window.AIWorkspaceSpeech) return;
        var segments = window.AIWorkspaceSpeech.prepareSpeechSegments(content);
        button._speechSegments = segments;
        button._speechIndex = 0;
        button.hidden = !segments.length;
        setSpeechButtonState(button, 'idle');
        button.onclick = function () {
            if (!speechAdapter || !speechAdapter.supported()) {
                setSpeechButtonState(button, 'failed');
                setStatus('当前浏览器不支持语音朗读。');
                return;
            }
            if (activeSpeechButton === button && speechAdapter.isSpeaking()) {
                stopSpeechPlayback();
                setStatus('已停止朗读。');
                return;
            }
            if (activeSpeechButton && activeSpeechButton !== button) stopSpeechPlayback();
            var index = button._speechIndex || 0;
            var segment = button._speechSegments[index];
            if (!segment) {
                button._speechIndex = 0;
                segment = button._speechSegments[0];
            }
            activeSpeechButton = button;
            setSpeechButtonState(button, 'speaking');
            setStatus('正在朗读 AI 回复…');
            speechAdapter.speak(segment, {
                allowBrowserFallback: true,
                preset: 'chat_read_aloud',
                onStart: function () {
                    if (avatarAdapter && avatarAdapter.setSpeechActive) avatarAdapter.setSpeechActive(true);
                    setAvatarState('speaking', segment, { expand: true });
                },
                onBoundary: function () {
                    if (avatarAdapter && avatarAdapter.setSpeechEnvelope) avatarAdapter.setSpeechEnvelope(0.9);
                },
                onPause: function () { if (avatarAdapter && avatarAdapter.setSpeechActive) avatarAdapter.setSpeechActive(false); },
                onResume: function () { if (avatarAdapter && avatarAdapter.setSpeechActive) avatarAdapter.setSpeechActive(true); },
                onEnd: function () {
                    if (avatarAdapter && avatarAdapter.setSpeechActive) avatarAdapter.setSpeechActive(false);
                    if (activeSpeechButton !== button) return;
                    activeSpeechButton = null;
                    if (index + 1 < button._speechSegments.length) {
                        button._speechIndex = index + 1;
                        setSpeechButtonState(button, 'continue');
                        setStatus('本段朗读完成，可以继续朗读。');
                    } else {
                        button._speechIndex = 0;
                        setSpeechButtonState(button, 'idle');
                        setStatus('朗读完成。');
                    }
                    setAvatarState('idle', '');
                },
                onError: function () {
                    if (avatarAdapter && avatarAdapter.setSpeechActive) avatarAdapter.setSpeechActive(false);
                    if (activeSpeechButton === button) activeSpeechButton = null;
                    button._speechIndex = 0;
                    setSpeechButtonState(button, 'failed');
                    setStatus('柿小吉的声音暂时连不上，可以稍后再试。');
                    setAvatarState('idle', '');
                }
            });
        };
    }
    function updateSpeechForNode(node, content) {
        if (node && node._speechButton) configureSpeechButton(node._speechButton, content);
    }
    var defaultInputPlaceholder = '输入文字，或上传图片、使用语音输入……';
    function messageIdentity(metadata, row) {
        metadata = metadata || {};
        return String(metadata.id || metadata.message_id || (row && row.dataset && row.dataset.messageId) || '').trim();
    }
    function markReplySource(messageId) {
        var list = $('messages');
        if (!list) return;
        Array.prototype.forEach.call(list.querySelectorAll('.msg.is-reply-target'), function (node) {
            node.classList.remove('is-reply-target');
        });
        if (!messageId) return;
        var source = list.querySelector('.msg[data-message-id="' + messageId.replace(/"/g, '') + '"]');
        if (source) source.classList.add('is-reply-target');
    }
    function renderReplyTarget() {
        var root = $('replyContext');
        var label = $('replyContextText');
        var input = $('input');
        if (!root) return;
        root.hidden = !replyTarget;
        if (replyTarget && label) label.textContent = replyTarget.label || '正在追问上一条回复';
        if (input) input.placeholder = replyTarget ? '针对刚才的内容继续提问…' : defaultInputPlaceholder;
        markReplySource(replyTarget && replyTarget.reply_to_message_id);
        syncReturnToLatestOffset();
    }
    function clearReplyTarget() {
        replyTarget = null;
        renderReplyTarget();
    }
    function setReplyTarget(target) {
        replyTarget = target || null;
        renderReplyTarget();
        var input = $('input');
        var bar = $('replyContext');
        if (bar && replyTarget && typeof bar.scrollIntoView === 'function') {
            try { bar.scrollIntoView({ block: 'nearest', behavior: 'smooth' }); } catch (ignore) { bar.scrollIntoView(false); }
        }
        if (input && typeof input.focus === 'function') {
            try { input.focus({ preventScroll: false }); } catch (ignore) { input.focus(); }
        }
        if (replyTarget) setStatus('已进入追问，输入问题后发送即可。');
    }
    function artifactKind(artifact) {
        return String(artifact && (artifact.kind || artifact.type) || '').replace(/^workspace_/, '');
    }
    function isExamArtifact(artifact) {
        var kind = artifactKind(artifact);
        return kind === 'exam' || kind === 'exam_result';
    }
    function beginFollowup(options) {
        options = options || {};
        var artifact = options.artifact || null;
        var examFollowup = isExamArtifact(artifact);
        try {
            if (window.KakiWorkspaceContextBridge) {
                if (artifact && artifact.artifact_id && !examFollowup) {
                    normalizeArtifactId(artifact);
                    window.KakiWorkspaceContextBridge.bindArtifact(artifact, {
                        source: 'artifact_followup',
                        reason: '你选择了追问这份资源',
                        keepView: true
                    });
                }
                window.KakiWorkspaceContextBridge.setViewMode('conversation');
            }
        } catch (ignore) {}
        setReplyTarget({
            mode: 'reply',
            reply_to_message_id: options.messageId || null,
            target_artifact_id: artifact && !examFollowup ? Number(artifact.artifact_id) || null : null,
            target_version_id: artifact && artifact.version_id && !examFollowup
                ? Number(artifact.version_id) || null
                : null,
            exam_id: examFollowup ? Number(artifact.artifact_id) || null : null,
            label: options.label || '正在追问：柿小吉上一条回复'
        });
    }
    function beginArtifactEdit(artifact, prompt) {
        if (!artifact || !artifact.artifact_id) return;
        activeArtifact = artifact;
        if (window.KakiWorkspaceContextBridge) window.KakiWorkspaceContextBridge.bindArtifact(artifact, { source:'artifact_edit', reason:'你选择了修改这份资源', intent:'edit_artifact', keepView:true });
        setReplyTarget({
            mode: 'edit',
            target_artifact_id: Number(artifact.artifact_id),
            target_version_id: Number(artifact.version || 1),
            label: '正在修改：' + (artifact.title || '当前学习产物')
        });
        $('input').value = prompt || '';
        $('input').focus();
    }
    function requestFormalArtifactAction(artifact, prompt) {
        if (!artifact || !artifact.artifact_id || !prompt) return;
        activeArtifact = artifact;
        if (window.KakiWorkspaceContextBridge) {
            window.KakiWorkspaceContextBridge.bindArtifact(artifact, {
                source:'artifact_learning_chain',
                reason:'你从当前学习成果选择了下一步',
                keepView:true
            });
            window.KakiWorkspaceContextBridge.setViewMode('conversation');
        }
        $('input').value = prompt;
        resizeComposer();
        $('send').click();
    }
    async function runDerivedArtifactAction(artifact, action, triggerButton, statusText) {
        // The button click is the whole intent: run the structured derive task
        // directly instead of routing a chat message through the semantic router.
        if (!artifact || !artifact.artifact_id) return;
        if (triggerButton) { triggerButton.disabled = true; }
        activeArtifact = artifact;
        setStatus(statusText || '正在生成…');
        try {
            var response = await api.deriveArtifactAction(artifact.artifact_id, action);
            var derived = response.data || {};
            if (response.idempotent_replay) {
                var replayText = derived.summary || '已生成，沿用上次结果。';
                addMessage('assistant', replayText);
                renderArtifact(derived);
                setStatus('已沿用上次生成结果。');
            } else {
                var summaryText = derived.summary || '已基于这份讲义生成学习成果。';
                var node = addMessage('assistant', summaryText);
                renderAssistant(node, summaryText);
                renderArtifact(derived);
                messages.push({ role: 'assistant', content: summaryText, artifact: derived });
                activeArtifact = derived;
                await loadHistory({ renderConversation: false });
                setStatus('已生成，可以继续学习。');
            }
        } catch (error) {
            setStatus(error && error.message === '这个学习产物正在生成，请稍候。'
                ? error.message : '暂时无法生成，请稍后重试。');
        } finally {
            if (triggerButton) { triggerButton.disabled = false; }
        }
    }
    function appendArtifactFollowup(actions, artifact) {
        normalizeArtifactId(artifact);
        if (!actions || !artifact || !artifact.artifact_id) return;
        var button = document.createElement('button');
        button.type = 'button';
        button.textContent = '追问';
        button.onclick = function () {
            activeArtifact = artifact;
            beginFollowup({
                artifact: artifact,
                label: '正在追问：' + (artifact.title || '当前学习产物')
            });
        };
        actions.appendChild(button);
    }
    function renderMessageAttachments(bubble, refs) {
        refs = Array.isArray(refs) ? refs : [];
        if (!refs.length) return;
        var root = document.createElement('div');
        root.className = 'message-attachments';
        refs.forEach(function (ref) {
            var row = document.createElement('div');
            row.className = 'message-attachment';
            var icon = document.createElement('span');
            icon.textContent = String(ref.type || '文件').toUpperCase();
            var copy = document.createElement('span');
            var name = document.createElement('b');
            name.textContent = ref.file_name || '已附加资料';
            var status = document.createElement('small');
            status.textContent = (ref.parse_status === 'ready' ? '已解析' : (ref.parse_status || '已附加'))
                + (ref.page_count ? ' · ' + ref.page_count + '页' : '');
            copy.append(name, status);
            row.append(icon, copy);
            root.appendChild(row);
        });
        bubble.appendChild(root);
    }
    function addMessage(role, content, metadata) {
        metadata = metadata || {};
        var row = document.createElement('article');
        row.className = 'msg ' + role;
        var messageId = messageIdentity(metadata, row);
        if (messageId) row.dataset.messageId = messageId;
        var avatar = document.createElement('img');
        avatar.className = 'avatar';
        avatar.alt = role === 'assistant' ? '柿小吉' : '用户';
        avatar.src = role === 'assistant' ? '/ai-workspace/assets/avatars/avatar-ai.jpg' : '/ai-workspace/assets/avatars/avatar-user.svg';
        var bubble = document.createElement('div');
        bubble.className = 'message-bubble';
        bubble.textContent = content || '';
        renderMessageAttachments(bubble, metadata.attachment_refs);
        var column = document.createElement('div');
        column.className = 'message-column';
        column.appendChild(bubble);
        if (role === 'assistant') {
            var tools = document.createElement('div');
            tools.className = 'message-tools';
            var speechButton = document.createElement('button');
            speechButton.className = 'message-speech-button';
            speechButton.type = 'button';
            bubble._speechButton = speechButton;
            tools.appendChild(speechButton);
            // 朗读按钮用剥离指令后的正文（[blackboard]/[control:xxx] 不念）
            var speechContent = content;
            var bh = window.KakiBlackboardHost;
            if (bh && bh.extractBoardDirective) { var di = bh.extractBoardDirective(speechContent); if (di) speechContent = di.rest || ''; }
            if (bh && bh.stripControlMarkers) speechContent = bh.stripControlMarkers(speechContent);
            configureSpeechButton(speechButton, speechContent);
            var followButton = document.createElement('button');
            followButton.className = 'message-follow-button';
            followButton.type = 'button';
            followButton.textContent = '追问';
            followButton.onclick = function () {
                beginFollowup({
                    messageId: messageIdentity(metadata, row),
                    label: '正在追问：柿小吉上一条回复'
                });
            };
            row._followButton = followButton;
            tools.appendChild(followButton);
            column.appendChild(tools);
        }
        row.append(avatar, column);
        $('messages').appendChild(row);
        return bubble;
    }
    function conversationActionStatusCopy(status) {
        return {
            planned:'正在准备', preparing:'正在准备', reviewing:'正在检查',
            ready:'已准备好', failed:'准备未完成'
        }[status] || '正在准备';
    }
    function conversationActionTypeCopy(kind) {
        return {
            HANDOUT:'讲义', PRACTICAL_GUIDE:'实操', ASSESSMENT:'测试', CODE_LAB:'代码'
        }[kind] || '学习资料';
    }
    function renderConversationTrace(root, trace) {
        var rows = Array.isArray(trace) ? trace.filter(function (item) {
            return item && item.status === 'completed' && item.label && item.detail;
        }) : [];
        if (!rows.length) return;
        var details = document.createElement('details');
        details.className = 'turn-collaboration-trace';
        var summary = document.createElement('summary');
        summary.textContent = '本次协作 · ' + rows.length + ' 步已完成';
        var list = document.createElement('ol');
        rows.forEach(function (item) {
            var row = document.createElement('li');
            var head = document.createElement('span');
            var label = document.createElement('strong'); label.textContent = item.label;
            head.appendChild(label);
            if (item.technical_label) {
                var technical = document.createElement('small'); technical.textContent = item.technical_label;
                head.appendChild(technical);
            }
            var detail = document.createElement('p'); detail.textContent = item.detail;
            row.append(head, detail); list.appendChild(row);
        });
        details.append(summary, list); root.appendChild(details);
    }
    function renderConversationAction(bubble, action) {
        if (!bubble || !action || !action.action_key) return null;
        var column = bubble.parentNode;
        if (!column) return null;
        var previous = column.querySelector('.conversation-action-wrap');
        if (previous) previous.remove();
        var wrap = document.createElement('div');
        wrap.className = 'conversation-action-wrap';
        wrap.dataset.actionKey = action.action_key;
        var card = document.createElement('section');
        card.className = 'inline-artifact-entry';
        card.dataset.status = action.status || 'planned';
        var eyebrow = document.createElement('span'); eyebrow.className = 'inline-artifact-eyebrow';
        eyebrow.textContent = action.status === 'ready' ? '为你准备好' : 'DARYA 正在接手';
        var title = document.createElement('strong');
        title.textContent = action.artifact && action.artifact.title
            ? action.artifact.title : conversationActionTypeCopy(action.kind);
        var meta = document.createElement('p'); meta.textContent = action.reason || '针对当前学习状态准备';
        var footer = document.createElement('div');
        var status = document.createElement('span'); status.className = 'inline-artifact-status';
        status.textContent = conversationActionStatusCopy(action.status);
        var primary = document.createElement('button'); primary.type = 'button'; primary.className = 'inline-artifact-primary';
        primary.textContent = action.kind === 'ASSESSMENT' || action.kind === 'CODE_LAB' ? '开始' : '打开';
        primary.disabled = action.status !== 'ready' || !action.artifact;
        primary.onclick = function () {
            if (!action.artifact || action.status !== 'ready') return;
            if (window.KakiArtifactShellHost && typeof window.KakiArtifactShellHost.open === 'function') {
                window.KakiArtifactShellHost.open(action.artifact, { source:'conversation_inline_action' });
            }
        };
        footer.append(status, primary);
        card.append(eyebrow, title, meta, footer);
        if (action.status === 'failed') {
            var recovery = document.createElement('div'); recovery.className = 'inline-artifact-recovery';
            var copy = document.createElement('span'); copy.textContent = action.error_message || '准备没有完成，原对话仍然保留。';
            var retry = document.createElement('button'); retry.type = 'button'; retry.textContent = '重试';
            retry.onclick = function () { executeConversationAction(action, bubble); };
            recovery.append(copy, retry); card.appendChild(recovery);
        }
        wrap.appendChild(card);
        renderConversationTrace(wrap, action.trace);
        column.appendChild(wrap);
        scrollController.contentResized();
        return wrap;
    }
    async function executeConversationAction(initialAction, bubble) {
        var action = Object.assign({}, initialAction || {});
        if (!action.action_key) return action;
        try {
            if (action.status === 'planned' || action.status === 'failed') {
                action.status = 'preparing'; action.error_message = null;
                renderConversationAction(bubble, action);
                action = (await api.prepareConversationCapabilityAction(action.action_key)).data;
                renderConversationAction(bubble, action);
            }
            if (action.status === 'reviewing') {
                action = (await api.reviewConversationCapabilityAction(action.action_key)).data;
                renderConversationAction(bubble, action);
            }
            return action;
        } catch (error) {
            try { action = (await api.getConversationCapabilityAction(action.action_key)).data; }
            catch (ignore) {
                action.status = 'failed'; action.retryable = true;
                action.error_message = '这份资料暂时没有准备完成，可以重试；原对话仍然保留着。';
            }
            renderConversationAction(bubble, action);
            setStatus(action.error_message || naturalTaskError(error));
            return action;
        }
    }
    function renderAssistant(node, content) {
        // —— 黑板绘制指令：AI 输出 [blackboard]…[/blackboard] 时，指令进通话黑板渲染，气泡只显示正文 ——
        var blackboardHost = window.KakiBlackboardHost;
        if (blackboardHost && blackboardHost.extractBoardDirective) {
            var directiveInfo = blackboardHost.extractBoardDirective(content);
            if (directiveInfo) {
                // 通话黑板由 KakiVoiceCallRuntime 的 updateBoard 接管渲染；
                // 这里只负责把指令从对话气泡中剥离，避免「板书」等说辞原样进入文本
                content = directiveInfo.rest || '';
            }
        }
        if (blackboardHost && blackboardHost.stripControlMarkers) {
            content = blackboardHost.stripControlMarkers(content);
        }
        // 朗读按钮用剥离后的正文：指令（[blackboard]/[control:xxx]）绝不能念出来
        updateSpeechForNode(node, content);
        if (!content) { node.textContent = ''; return; }
        if (window.AIRender && window.AIRender.renderToHtml) {
            window.AIRender.renderToHtml(content).then(function (html) {
                node.innerHTML = html;
                scrollController.contentResized();
            });
        }
    }
    function resizeComposer() {
        var input = $('input');
        if (!input) return;
        input.style.height = 'auto';
        var nextHeight = Math.min(Math.max(input.scrollHeight, 44), 132);
        input.style.height = nextHeight + 'px';
        input.style.overflowY = input.scrollHeight > 132 ? 'auto' : 'hidden';
        scrollController.contentResized();
    }
    function appendInputText(text) {
        var value = String(text || '').trim();
        if (!value || !$('input')) return;
        var current = $('input').value.trim();
        $('input').value = current ? current + '\n' + value : value;
        resizeComposer();
        $('input').focus();
    }
    function setAdapterBusy(value) {
        inputAdapterBusy = Boolean(value);
        updateComposer(active);
    }
    function readableTimer(elapsedSeconds) {
        var minutes = Math.floor(elapsedSeconds / 60);
        var seconds = elapsedSeconds % 60;
        return String(minutes).padStart(2, '0') + ':' + String(seconds).padStart(2, '0');
    }
    function stopRecordingClock() {
        if (recordingTimer) window.clearInterval(recordingTimer);
        recordingTimer = null;
    }
    function closeMediaStream() {
        if (mediaStream) mediaStream.getTracks().forEach(function (track) { track.stop(); });
        mediaStream = null;
    }
    function releaseVoiceCapture(cancelled) {
        cancelCurrentRecording = Boolean(cancelled);
        if (browserRecognition) {
            try { browserRecognition.abort(); } catch (ignore) {}
        }
        if (mediaRecorder && mediaRecorder.state !== 'inactive') {
            try { mediaRecorder.stop(); } catch (ignore) {}
        }
        closeMediaStream();
        stopRecordingClock();
        if (cancelled) showNormalComposer();
    }
    function showNormalComposer() {
        stopRecordingClock();
        if ($('recordingState')) $('recordingState').hidden = true;
        if ($('normalComposer')) $('normalComposer').hidden = false;
        if ($('stopRecording')) $('stopRecording').disabled = false;
    }
    function showRecordingComposer(label) {
        if ($('normalComposer')) $('normalComposer').hidden = true;
        if ($('recordingState')) $('recordingState').hidden = false;
        if ($('recordingLabel')) $('recordingLabel').textContent = label || '正在听你说话…';
    }
    function materialFileType(fileName) {
        var match = String(fileName || '').toLowerCase().match(/\.([a-z0-9]+)$/);
        return match ? match[1].toUpperCase() : '文件';
    }
    function materialSize(bytes) {
        var value = Number(bytes || 0);
        if (value < 1024) return value + ' B';
        if (value < 1024 * 1024) return (value / 1024).toFixed(1) + ' KB';
        return (value / 1024 / 1024).toFixed(1) + ' MB';
    }
    function materialStatusText(item) {
        if (item.parse_status === 'uploading' || item.parse_status === 'parsing') {
            return materialSize(item.size_bytes) + ' · 正在解析…';
        }
        if (item.parse_status === 'failed') {
            return '解析失败 · ' + (item.parse_error || '可以重试');
        }
        var detail = item.page_count ? item.page_count + '页' : (item.section_count ? item.section_count + '节' : materialSize(item.size_bytes));
        return '已解析 · ' + detail + ' · 可用于当前对话';
    }
    function renderWorkspaceMaterials() {
        var root = $('materialList');
        if (!root) return;
        root.innerHTML = '';
        var pendingItems = workspaceMaterials.filter(function (item) {
            return item._local_id || pendingMaterialIds.indexOf(item.material_id) >= 0;
        });
        root.hidden = !pendingItems.length;
        pendingItems.forEach(function (item) {
            var row = document.createElement('div');
            row.className = 'material-row';
            row.dataset.status = item.parse_status;
            var icon = document.createElement('span');
            icon.className = 'material-icon';
            icon.textContent = materialFileType(item.file_name);
            var copy = document.createElement('span');
            copy.className = 'material-copy';
            var name = document.createElement('strong');
            name.textContent = item.file_name || '未命名资料';
            var status = document.createElement('small');
            status.textContent = materialStatusText(item);
            copy.append(name, status);
            var actions = document.createElement('span');
            actions.className = 'material-actions';
            if (item.parse_status === 'failed' && item._file) {
                var retry = document.createElement('button');
                retry.type = 'button'; retry.textContent = '重试';
                retry.onclick = function () { uploadWorkspaceFiles([item._file], item._local_id); };
                actions.appendChild(retry);
            }
            var remove = document.createElement('button');
            remove.type = 'button'; remove.textContent = '删除';
            remove.setAttribute('aria-label', '从当前对话删除 ' + (item.file_name || '资料'));
            remove.onclick = function () { detachWorkspaceMaterial(item); };
            actions.appendChild(remove);
            row.append(icon, copy, actions);
            root.appendChild(row);
        });
    }
    function renderMaterialLibraryPicker() {
        var panel = $('materialLibraryPicker');
        var root = $('materialLibraryList');
        var status = $('materialLibraryStatus');
        if (!panel || !root) return;
        root.innerHTML = '';
        if (!workspaceMaterialLibrary.length) {
            var empty = document.createElement('p');
            empty.className = 'material-library-empty';
            empty.textContent = '资料库还没有可绑定的资料。可以先上传一份资料。';
            root.appendChild(empty);
            if (status) status.textContent = '0 份资料';
            return;
        }
        if (status) status.textContent = workspaceMaterialLibrary.length + ' 份资料，可加入当前对话';
        var boundIds = {};
        workspaceMaterials.forEach(function (item) { if (item && item.material_id) boundIds[item.material_id] = true; });
        workspaceMaterialLibrary.forEach(function (item) {
            var row = document.createElement('div');
            row.className = 'material-library-row';
            var copy = document.createElement('span');
            copy.className = 'material-library-copy';
            var name = document.createElement('strong');
            name.textContent = item.file_name || '未命名资料';
            var meta = document.createElement('small');
            var category = item.category && item.category !== '未分类' ? ' · ' + item.category : '';
            meta.textContent = materialStatusText(item) + category;
            copy.append(name, meta);
            var button = document.createElement('button');
            button.type = 'button';
            var isBound = Boolean(boundIds[item.material_id]);
            var isActive = item.material_id === activeMaterialId;
            button.textContent = isActive ? '正在使用' : isBound ? '使用这份' : '加入对话';
            button.disabled = isActive || item.parse_status !== 'ready';
            button.onclick = async function () {
                button.disabled = true;
                try {
                    if (!isBound) await api.attachWorkspaceMaterial(item.material_id);
                    await loadWorkspaceMaterials();
                    setActiveMaterial(item.material_id);
                    renderWorkspaceMaterials();
                    renderMaterialLibraryPicker();
                    panel.hidden = true;
                    setStatus('已从资料库选择「' + (item.file_name || '资料') + '」，下一轮会按当前资料边界检索。');
                } catch (error) {
                    button.disabled = false;
                    setStatus('资料绑定失败：' + error.message);
                }
            };
            row.append(copy, button);
            root.appendChild(row);
        });
    }
    async function openMaterialLibraryPicker() {
        var panel = $('materialLibraryPicker');
        var status = $('materialLibraryStatus');
        if (!panel) return;
        panel.hidden = false;
        if (status) status.textContent = '正在读取资料库…';
        try {
            var response = await api.listWorkspaceMaterialLibrary();
            workspaceMaterialLibrary = response.data || [];
            renderMaterialLibraryPicker();
        } catch (error) {
            workspaceMaterialLibrary = [];
            if ($('materialLibraryList')) $('materialLibraryList').innerHTML = '';
            if (status) status.textContent = '资料库读取失败：' + error.message;
        }
    }
    function setActiveMaterial(materialId) {
        activeMaterialId = materialId || '';
        if (currentThread()) {
            sessionStorage.setItem('ai_workspace_active_material_' + currentThread(), activeMaterialId);
        }
    }
    function pendingAttachmentRefs() {
        return workspaceMaterials.filter(function (item) {
            return pendingMaterialIds.indexOf(item.material_id) >= 0
                && item.parse_status === 'ready' && item.material_id;
        }).map(function (item) {
            return {
                material_id: item.material_id,
                file_name: item.file_name,
                type: item.file_type || materialFileType(item.file_name).toLowerCase(),
                parse_status: item.parse_status,
                page_count: item.page_count || null
            };
        });
    }
    function clearPendingAttachments() {
        pendingMaterialIds = [];
        renderWorkspaceMaterials();
    }
    function activeMaterialIds() {
        var pending = pendingAttachmentRefs().map(function (item) { return item.material_id; });
        if (pendingMaterialIds.length && pending.length) return pending;
        if (activeMaterialId && workspaceMaterials.some(function (item) {
            return item.material_id === activeMaterialId && item.parse_status === 'ready';
        })) return [activeMaterialId];
        return [];
    }
    async function loadWorkspaceMaterials() {
        if (!currentThread()) return;
        try {
            var response = await api.listWorkspaceMaterials();
            workspaceMaterials = response.data || [];
            if (activeMaterialId && !workspaceMaterials.some(function (item) { return item.material_id === activeMaterialId; })) {
                setActiveMaterial('');
            }
            renderWorkspaceMaterials();
            if ($('materialLibraryPicker') && !$('materialLibraryPicker').hidden) renderMaterialLibraryPicker();
            if (workspaceMaterials.length) {
                setStatus('已恢复 ' + workspaceMaterials.length + ' 份当前会话资料。');
            }
        } catch (error) {
            workspaceMaterials = [];
            renderWorkspaceMaterials();
            if ($('materialLibraryPicker') && !$('materialLibraryPicker').hidden) renderMaterialLibraryPicker();
            setStatus('当前会话资料恢复失败：' + error.message);
        }
    }
    function renderWorkspaceSearch(search) {
        if (!search || !window.AIWorkspaceSearch) return null;
        var card = window.AIWorkspaceSearch.renderSearchResults(search, {
            importResult: function (searchId, resultId) {
                return api.importWorkspaceSearchResult(searchId, resultId);
            },
            onImported: async function (material) {
                if (material && material.material_id) setActiveMaterial(material.material_id);
                await loadWorkspaceMaterials();
                setStatus('资料已导入并绑定当前对话');
                scrollController.contentResized();
            }
        });
        $('messages').appendChild(card);
        scrollController.contentResized();
        return card;
    }
    async function detachWorkspaceMaterial(item) {
        if (!item) return;
        if (!item.material_id) {
            workspaceMaterials = workspaceMaterials.filter(function (candidate) { return candidate !== item; });
            renderWorkspaceMaterials();
            return;
        }
        try {
            await api.detachWorkspaceMaterial(item.material_id);
            workspaceMaterials = workspaceMaterials.filter(function (candidate) {
                return candidate.material_id !== item.material_id;
            });
            pendingMaterialIds = pendingMaterialIds.filter(function (id) { return id !== item.material_id; });
            if (activeMaterialId === item.material_id) setActiveMaterial('');
            renderWorkspaceMaterials();
            setStatus('已从当前对话移除资料，资料库中的原文件仍保留。');
        } catch (error) {
            setStatus('移除资料失败：' + error.message);
        }
    }
    async function uploadWorkspaceFiles(files, replaceLocalId) {
        var selected = Array.prototype.slice.call(files || []);
        if (!selected.length) return;
        var supported = /\.(png|jpe?g|webp|pdf|docx|txt|md|markdown)$/i;
        selected = selected.filter(function (file) {
            if (!supported.test(file.name || '')) {
                setStatus('不支持“' + (file.name || '这个文件') + '”，请上传图片、PDF、DOCX、TXT 或 Markdown。');
                return false;
            }
            if (file.size > 30 * 1024 * 1024) {
                setStatus('“' + file.name + '”不能超过 30 MB。');
                return false;
            }
            return true;
        });
        if (!selected.length) return;
        setAdapterBusy(true);
        for (var index = 0; index < selected.length; index += 1) {
            var file = selected[index];
            var localId = replaceLocalId || ('pending_' + Date.now() + '_' + index);
            var pending = {
                _local_id: localId, _file: file, file_name: file.name,
                size_bytes: file.size, parse_status: 'uploading'
            };
            var existing = workspaceMaterials.findIndex(function (item) { return item._local_id === localId; });
            if (existing >= 0) workspaceMaterials.splice(existing, 1, pending);
            else workspaceMaterials.unshift(pending);
            renderWorkspaceMaterials();
            try {
                var response = await api.uploadWorkspaceMaterial(file);
                var saved = response.data;
                var position = workspaceMaterials.findIndex(function (item) { return item._local_id === localId; });
                if (position >= 0) workspaceMaterials.splice(position, 1, saved);
                else workspaceMaterials.unshift(saved);
                if (pendingMaterialIds.indexOf(saved.material_id) < 0) pendingMaterialIds.push(saved.material_id);
                renderWorkspaceMaterials();
                if (saved.file_type === 'image' && saved.preview_text) {
                    appendInputText(saved.preview_text);
                    setStatus('图片文字已识别并填入输入框，文件也已绑定当前对话。');
                } else {
                    setStatus('资料解析完成，可以在对话中直接引用。');
                }
            } catch (error) {
                pending.parse_status = 'failed';
                pending.parse_error = error.message;
                renderWorkspaceMaterials();
                setStatus('资料解析失败：' + error.message);
            }
        }
        if ($('materialUpload')) $('materialUpload').value = '';
        setAdapterBusy(false);
    }
    function clearImageSelection() {
        selectedImageFile = null;
        if (selectedImageUrl) URL.revokeObjectURL(selectedImageUrl);
        selectedImageUrl = '';
        if ($('imageUpload')) $('imageUpload').value = '';
        if ($('imagePreview')) $('imagePreview').hidden = true;
        if ($('retryOcr')) $('retryOcr').hidden = true;
    }
    async function recognizeSelectedImage() {
        if (!selectedImageFile || inputAdapterBusy) return;
        setAdapterBusy(true);
        $('imageStatus').textContent = '识别中…';
        $('retryOcr').hidden = true;
        try {
            var response = await api.recognizeWorkspaceImage(selectedImageFile);
            appendInputText(response.data && response.data.text);
            $('imageStatus').textContent = '识别完成，可修改后发送';
            setStatus('图片文字已填入输入框。');
        } catch (error) {
            $('imageStatus').textContent = 'OCR 识别失败';
            $('retryOcr').hidden = false;
            setStatus('OCR 识别失败：' + error.message);
        } finally {
            setAdapterBusy(false);
        }
    }
    function useImageFile(file) {
        if (!file) return;
        if (['image/png', 'image/jpeg', 'image/webp'].indexOf(file.type) < 0) {
            setStatus('OCR 识别失败：请上传 PNG、JPG 或 WebP 图片。');
            return;
        }
        if (file.size > 10 * 1024 * 1024) {
            setStatus('OCR 识别失败：图片不能超过 10 MB。');
            return;
        }
        clearImageSelection();
        selectedImageFile = file;
        selectedImageUrl = URL.createObjectURL(file);
        $('imageThumb').src = selectedImageUrl;
        $('imageName').textContent = file.name || '待识别图片';
        $('imageStatus').textContent = '等待识别';
        $('imagePreview').hidden = false;
        recognizeSelectedImage();
    }
    function preferredRecordingType() {
        var types = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', 'audio/mp4'];
        return types.find(function (type) { return MediaRecorder.isTypeSupported(type); }) || '';
    }
    function recordingExtension(type) {
        if (type.indexOf('ogg') >= 0) return '.ogg';
        if (type.indexOf('mp4') >= 0) return '.mp4';
        return '.webm';
    }
    function browserSpeechRecognitionConstructor() {
        return window.SpeechRecognition || window.webkitSpeechRecognition || null;
    }
    function showVoiceHint(message) {
        if (!$('voiceHint')) return;
        $('voiceHint').textContent = message || '';
        $('voiceHint').hidden = !message;
        syncReturnToLatestOffset();
    }
    async function probeInputCapabilities() {
        var BrowserRecognition = browserSpeechRecognitionConstructor();
        inputCapabilities.browserAsr = Boolean(BrowserRecognition);
        try {
            var response = await api.getWorkspaceInputCapabilities();
            inputCapabilities.backendAsr = Boolean(response.data && response.data.asr && response.data.asr.available);
        } catch (error) {
            inputCapabilities.backendAsr = false;
        }
        inputCapabilities.checked = true;
        if (!inputCapabilities.backendAsr && inputCapabilities.browserAsr) {
            showVoiceHint('当前使用浏览器语音识别，文字仍会先进入输入框。');
        } else if (!inputCapabilities.backendAsr && !inputCapabilities.browserAsr) {
            showVoiceHint('当前环境没有可用的语音转写服务，请使用文字输入。');
        } else {
            showVoiceHint('');
        }
        updateComposer(active);
        return inputCapabilities;
    }
    function beginBrowserRecognition() {
        var BrowserRecognition = browserSpeechRecognitionConstructor();
        if (!BrowserRecognition) return;
        var transcript = '';
        cancelCurrentRecording = false;
        browserRecognition = new BrowserRecognition();
        browserRecognition.lang = 'zh-CN';
        browserRecognition.continuous = true;
        browserRecognition.interimResults = true;
        browserRecognition.onresult = function (event) {
            transcript = '';
            for (var index = 0; index < event.results.length; index += 1) {
                transcript += event.results[index][0].transcript || '';
            }
            if ($('recordingLabel')) $('recordingLabel').textContent = transcript || '正在听你说话…';
        };
        browserRecognition.onerror = function (event) {
            showVoiceHint('浏览器语音识别失败：' + (event.error || '请检查麦克风权限。'));
        };
        browserRecognition.onend = function () {
            browserRecognition = null;
            stopRecordingClock();
            showNormalComposer();
            if (!cancelCurrentRecording && transcript.trim()) {
                appendInputText(transcript.trim());
                showVoiceHint('语音文字已填入输入框，可以修改后发送。');
            }
            setAvatarState('idle', '');
            updateComposer(active);
        };
        browserRecognition.start();
        recordingStartedAt = Date.now();
        showRecordingComposer('正在听你说话…');
        setAvatarState('asking', '我在认真听，你可以说完后再检查文字。', { expand: true });
        $('recordingTimer').textContent = '00:00';
        recordingTimer = window.setInterval(function () {
            $('recordingTimer').textContent = readableTimer(Math.floor((Date.now() - recordingStartedAt) / 1000));
        }, 500);
        updateComposer(active);
    }
    async function transcribeLastRecording() {
        if (!lastAudioBlob || inputAdapterBusy) return;
        setAdapterBusy(true);
        if ($('retryTranscription')) $('retryTranscription').hidden = true;
        showRecordingComposer('正在转写…');
        setAvatarState('thinking', '正在把语音整理成可以修改的文字。', { expand: true });
        if ($('recordingTimer')) $('recordingTimer').textContent = '请稍候';
        try {
            var response = await api.transcribeWorkspaceAudio(lastAudioBlob, lastAudioFileName);
            appendInputText(response.data && response.data.text);
            setStatus('语音文字已填入输入框。');
            showNormalComposer();
            setAvatarState('idle', '转写完成，检查后再发送。');
        } catch (error) {
            showNormalComposer();
            if ($('retryTranscription')) $('retryTranscription').hidden = false;
            setStatus('语音转写失败：' + error.message);
            setAvatarState('correction', '转写没有成功，你仍然可以继续使用文字输入。', { transientMs: 2600 });
        } finally {
            setAdapterBusy(false);
        }
    }
    async function beginRecording() {
        stopSpeechPlayback();
        if (active || inputAdapterBusy || mediaRecorder) return;
        if (!inputCapabilities.checked) await probeInputCapabilities();
        if (!inputCapabilities.backendAsr) {
            if (inputCapabilities.browserAsr) beginBrowserRecognition();
            return;
        }
        if (!window.MediaRecorder || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            setStatus('当前浏览器不支持录音，请继续使用文字输入。');
            return;
        }
        try {
            mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
            var mimeType = preferredRecordingType();
            mediaRecorder = mimeType
                ? new MediaRecorder(mediaStream, { mimeType: mimeType })
                : new MediaRecorder(mediaStream);
            recordedChunks = [];
            cancelCurrentRecording = false;
            mediaRecorder.ondataavailable = function (event) {
                if (event.data && event.data.size) recordedChunks.push(event.data);
            };
            mediaRecorder.onstop = function () {
                var stoppedRecorder = mediaRecorder;
                mediaRecorder = null;
                closeMediaStream();
                stopRecordingClock();
                if (cancelCurrentRecording) {
                    recordedChunks = [];
                    showNormalComposer();
                    setStatus('已取消录音。');
                    updateComposer(active);
                    return;
                }
                var type = stoppedRecorder.mimeType || mimeType || 'audio/webm';
                lastAudioBlob = new Blob(recordedChunks, { type: type.split(';')[0] });
                lastAudioFileName = 'workspace-recording' + recordingExtension(type);
                recordedChunks = [];
                transcribeLastRecording();
            };
            mediaRecorder.start(250);
            recordingStartedAt = Date.now();
            showRecordingComposer('正在听你说话…');
            setAvatarState('asking', '我在认真听，你可以说完后再检查文字。', { expand: true });
            $('stopRecording').disabled = false;
            $('recordingTimer').textContent = '00:00';
            recordingTimer = window.setInterval(function () {
                $('recordingTimer').textContent = readableTimer(Math.floor((Date.now() - recordingStartedAt) / 1000));
            }, 500);
            setStatus('正在录音，完成后点击“停止并转写”。');
            updateComposer(active);
        } catch (error) {
            closeMediaStream();
            mediaRecorder = null;
            showNormalComposer();
            setStatus('无法开始录音：' + (error.message || '请检查麦克风权限。'));
            setAvatarState('correction', '麦克风暂时不可用，文字输入仍然可以使用。', { transientMs: 2600 });
        }
    }
    function finishRecording(cancelled) {
        cancelCurrentRecording = Boolean(cancelled);
        if (browserRecognition) {
            browserRecognition.stop();
            return;
        }
        if (!mediaRecorder || mediaRecorder.state === 'inactive') return;
        if (!cancelled) {
            $('recordingLabel').textContent = '正在准备转写…';
        }
        mediaRecorder.stop();
        if (!cancelled) window.setTimeout(function () { $('stopRecording').disabled = true; }, 0);
    }
    function rememberedText(candidate) {
        if (candidate.key === 'response_style' && candidate.value === 'concise') {
            return '已记住：你更喜欢简短回答。';
        }
        return '已记住：' + candidate.display_text + '。';
    }
    function isExplicitMemoryCommand(message) {
        return ['请记住', '帮我记下来', '帮我记下', '记下来', '把这个偏好记住', '记住我']
            .some(function (term) { return String(message || '').indexOf(term) >= 0; });
    }
    function memoryPayload(candidate) {
        return {
            value: candidate.value,
            evidence_text: candidate.evidence_text,
            source_thread_id: candidate.source_thread_id,
            source_message_id: candidate.source_message_id,
            confirmed: true
        };
    }
    async function saveCandidate(bar, candidate, retryButton) {
        try {
            await api.saveMemory(candidate.key, memoryPayload(candidate));
            bar.textContent = rememberedText(candidate);
            setStatus(rememberedText(candidate));
            await loadMemories();
        } catch (error) {
            bar.textContent = '';
            var errorText = document.createElement('span');
            errorText.textContent = '这次没有保存成功，请重试。';
            retryButton.disabled = false;
            bar.append(errorText, retryButton);
            setStatus('这次没有保存成功，请重试。');
        }
    }
    function renderMemoryCandidate(answerNode, candidate) {
        if (!candidate || !candidate.key || ignoredCandidateKeys[candidate.key]) return;
        var bar = document.createElement('div');
        bar.className = 'memory-confirm';
        answerNode.closest('.msg').insertAdjacentElement('afterend', bar);
        if (candidate.authorization === 'explicit') {
            var retryExplicit = document.createElement('button');
            retryExplicit.textContent = '重试保存';
            retryExplicit.onclick = function () {
                retryExplicit.disabled = true;
                bar.textContent = '正在保存长期偏好…';
                saveCandidate(bar, candidate, retryExplicit);
            };
            bar.textContent = '正在保存长期偏好…';
            saveCandidate(bar, candidate, retryExplicit);
            return;
        }
        var text = document.createElement('span');
        text.textContent = '是否记住：' + candidate.display_text + '？';
        var remember = document.createElement('button');
        remember.textContent = '记住';
        var currentOnly = document.createElement('button');
        currentOnly.textContent = '仅本次会话';
        var ignore = document.createElement('button');
        ignore.textContent = '忽略';
        bar.append(text, remember, currentOnly, ignore);
        remember.onclick = async function () {
            remember.disabled = true;
            await saveCandidate(bar, candidate, remember);
        };
        currentOnly.onclick = function () {
            if (candidate.key === 'response_style') temporaryStyle = candidate.value;
            bar.textContent = '仅在本次会话生效';
        };
        ignore.onclick = function () {
            ignoredCandidateKeys[candidate.key] = true;
            bar.remove();
        };
    }
    function memoryValueText(item) {
        if (item.value && typeof item.value === 'object' && item.value.description) {
            return item.value.description;
        }
        if (item.key === 'learning_goal' && item.value && typeof item.value === 'object') {
            return item.value.target || item.value.topic || item.value.subject || '学习目标';
        }
        var labels = {
            concise: '简短', normal: '正常', detailed: '详细',
            'zh-CN': '中文', en: '英文', bilingual: '中英双语',
            code: '代码', video: '视频', visual: '图示', practice_first: '先练习',
            socratic: '苏格拉底式提问', feynman: '费曼学习法',
            example_first: '先举例', step_by_step: '分步骤', direct: '直接回答',
            hands_on: '边做边学', theory_first: '先理论后练习'
        };
        return labels[item.value] || String(item.value || '');
    }
    function memoryLabel(item) {
        var labels = {
            response_style: '回答风格', content_format: '内容形式',
            explanation_language: '讲解语言', teaching_method: '教学方式',
            difficulty_preference: '难度偏好', learning_goal: '当前目标',
            general_preference: '其他偏好'
        };
        return (labels[item.key] || item.key) + '：' + memoryValueText(item);
    }
    function renderMemoryGroup(container, items) {
        container.innerHTML = '';
        items.forEach(function (item) {
            var row = document.createElement('article');
            row.className = 'memory-row';
            var content = document.createElement('div');
            var title = document.createElement('strong');
            title.textContent = memoryLabel(item);
            var meta = document.createElement('small');
            var explicit = item.value && typeof item.value === 'object' && item.value.source === 'explicit';
            meta.textContent = (explicit ? '用户主动设置' : '保存于 ' + (item.updated_at || item.created_at || '—'))
                + ' · key: ' + item.key + ' · 来源 ' + shortId(item.source_thread_id);
            content.append(title, meta);
            var actions = document.createElement('div');
            actions.className = 'memory-row-actions';
            var edit = document.createElement('button');
            edit.className = 'memory-text-action';
            edit.textContent = '编辑';
            edit.onclick = function () { editMemory(item); };
            var remove = document.createElement('button');
            remove.className = 'memory-text-action';
            remove.textContent = '删除';
            remove.onclick = async function () {
                remove.disabled = true;
                try {
                    await api.deleteMemory(item.id);
                    await loadMemories();
                } catch (error) {
                    remove.disabled = false;
                    setStatus('删除失败：' + (error.message || '未知错误'));
                }
            };
            actions.append(edit, remove);
            row.append(content, actions);
            container.appendChild(row);
        });
    }
    async function loadMemories() {
        var result = await api.getMemories();
        var items = result.data || [];
        confirmedMemories = items;
        memoriesLoaded = true;
        var grouped = window.AIWorkspaceState.groupMemories(items);
        $('memoryEmpty').hidden = grouped.preferences.length > 0;
        $('memoryGoalsEmpty').hidden = grouped.goals.length > 0;
        $('memoryCount').textContent = '共 ' + grouped.preferences.length + ' 条';
        $('memoryGoalCount').textContent = '共 ' + grouped.goals.length + ' 条';
        renderMemoryGroup($('memoryPreferences'), grouped.preferences);
        renderMemoryGroup($('memoryGoals'), grouped.goals);
        return items;
    }

    function naturalMemorySentence(item) {
        var enumText = {
            response_style: { concise: '我喜欢回答简洁一点。', normal: '我喜欢正常长度的回答。', detailed: '我喜欢详细的回答。' },
            content_format: { text: '我喜欢文字内容。', visual: '我喜欢用图示学习。', video: '我喜欢用视频学习。', code: '我喜欢看代码示例。', practice_first: '我喜欢先做题再讲解。', mixed: '我喜欢混合形式的学习内容。' },
            explanation_language: { 'zh-CN': '我喜欢用中文讲解。', en: '我喜欢用英文讲解。', bilingual: '我喜欢中英双语讲解。' },
            teaching_method: { direct: '我喜欢直接回答。', socratic: '我喜欢苏格拉底式提问。', feynman: '我喜欢费曼学习法。', example_first: '我喜欢先看例子。', step_by_step: '我喜欢一步一步讲解。', hands_on: '我喜欢边做边学，通过实际操作理解概念。', theory_first: '我喜欢先看理论，再通过练习巩固。' },
            difficulty_preference: { basic: '我喜欢基础难度。', intermediate: '我喜欢中等难度。', advanced: '我喜欢进阶难度。', adaptive: '我喜欢难度自适应。' }
        };
        if (item.value && typeof item.value === 'object' && item.value.raw_text) {
            return item.value.raw_text;
        }
        if (item.key === 'learning_goal' && item.value && typeof item.value === 'object') {
            return '我的学习目标是' + (item.value.target || item.value.topic || item.value.subject || '') + '。';
        }
        return (enumText[item.key] && enumText[item.key][item.value]) || item.evidence_text || memoryLabel(item);
    }

    function editMemory(item) {
        $('naturalMemoryInput').value = naturalMemorySentence(item);
        $('manualMemoryStatus').textContent = '修改后再次保存，会更新同类记忆。';
        $('naturalMemoryInput').focus();
    }

    async function saveNaturalMemory(event) {
        event.preventDefault();
        var text = $('naturalMemoryInput').value.trim();
        if (!text) {
            $('manualMemoryStatus').textContent = '请先用一句话告诉柿小吉。';
            return;
        }
        $('manualMemorySave').disabled = true;
        $('manualMemoryStatus').textContent = '正在理解并保存…';
        try {
            var messageId = window.crypto && window.crypto.randomUUID ? 'manual_' + window.crypto.randomUUID() : 'manual_' + Date.now();
            var result = await api.saveNaturalMemory(text, currentThread(), messageId);
            await loadMemories();
            $('naturalMemoryInput').value = '';
            $('manualMemoryStatus').textContent = '已记住：' + (result.interpretation || memoryLabel(result.data));
        } catch (error) {
            $('manualMemoryStatus').textContent = '暂时不能保存：' + (error.message || '请说得更具体一些');
        } finally {
            $('manualMemorySave').disabled = false;
        }
    }
    async function openMemoryDrawer() {
        $('memoryDrawer').hidden = false;
        try {
            await loadMemories();
        }
        catch (error) { setStatus('记忆加载失败：' + (error.message || '未知错误')); }
    }
    function displayArtifactTitle(value, fallback) {
        return String(value || fallback || '学习产物').replace(/^(Python)(?:\s+Python)+/i, '$1');
    }
    function learnerStatus(value) {
        var labels = { queued:'正在准备', pending:'正在准备', running:'处理中', generating:'生成中', ready:'可查看', passed:'已通过检查', success:'已完成', completed:'已完成', failed:'生成失败', error:'暂时不可用' };
        return labels[String(value || '').toLowerCase()] || '已完成';
    }
    function renderVideoArtifact(artifact) {
        var card = document.createElement('section');
        card.className = 'artifact-card';
        var title = document.createElement('strong');
        title.textContent = displayArtifactTitle(artifact.title, '教学视频');
        var meta = document.createElement('small');
        meta.textContent = '教学视频 · ' + (artifact.duration_sec || 20) + ' 秒 · ' + learnerStatus(artifact.status) + (artifact.dry_run ? ' · 本机讲解' : '');
        var summary = document.createElement('p');
        summary.textContent = artifact.summary || '视频任务状态已更新。';
        var actions = document.createElement('div');
        actions.className = 'artifact-actions';
        var open = document.createElement('button');
        open.textContent = artifact.status === 'ready' ? '打开视频' : '视频尚未就绪';
        open.disabled = artifact.status !== 'ready';
        open.onclick = function () { openVideoArtifact(artifact); };
        actions.appendChild(open);
        if (artifact.status === 'failed' && artifact.retryable) {
            var retry = document.createElement('button');
            retry.textContent = '从原产物重试';
            retry.onclick = async function () {
                retry.disabled = true;
                try {
                    var queued = (await api.retryWorkspaceVideo(artifact.artifact_id)).data;
                    setStatus(queued.summary || '视频任务已重新入队。');
                    var ready = await waitForVideoArtifact(queued);
                    renderArtifact(ready);
                } catch (error) {
                    setStatus('重试失败：' + naturalTaskError(error));
                } finally { retry.disabled = false; }
            };
            actions.appendChild(retry);
        }
        card.append(title, meta, summary, actions);
        $('messages').appendChild(card);
        scrollController.contentResized();
    }

    async function openVideoArtifact(item) {
        normalizeArtifactId(item);
        if (!item || !item.artifact_id) return;
        setStatus('正在加载视频…');
        try {
            // 始终向后端取完整数据（video_url/transcript/status），不依赖传入卡片对象的字段
            var result = await api.getWorkspaceVideo(item.artifact_id);
            var artifact = (result && result.data) || item;
            activeArtifact = artifact;
            if (window.KakiWorkspaceContextBridge) {
                window.KakiWorkspaceContextBridge.bindArtifact(artifact, { source:'artifact_opened', reason:'你刚才主动打开了这份视频', keepView:true });
            }
            // 模态视频播放层：独立 fixed 弹层、最高层级，不受抽屉/输入框遮挡影响
            showVideoModal(artifact);
        } catch (error) {
            setStatus('视频加载失败：' + error.message);
        }
    }

    function showVideoModal(artifact) {
        var doc = document;
        var existing = doc.getElementById('kakiVideoModal');
        if (existing && existing.parentNode) existing.parentNode.removeChild(existing);
        var modal = doc.createElement('div');
        modal.id = 'kakiVideoModal';
        modal.className = 'kaki-video-modal';
        modal.style.cssText = 'position:fixed;inset:0;z-index:9999;display:flex;align-items:center;justify-content:center;background:rgba(15,12,9,.72);padding:24px;';
        var card = doc.createElement('div');
        card.style.cssText = 'position:relative;width:min(860px,96vw);max-height:92vh;overflow:auto;background:#fff;border-radius:16px;box-shadow:0 30px 80px rgba(0,0,0,.4);';
        var head = doc.createElement('div');
        head.style.cssText = 'display:flex;align-items:center;justify-content:space-between;padding:14px 18px;border-bottom:1px solid #eee;';
        var title = doc.createElement('strong');
        title.textContent = displayArtifactTitle(artifact.title, '教学视频');
        var close = doc.createElement('button');
        close.textContent = '关闭';
        close.style.cssText = 'padding:6px 14px;border:1px solid #e2d2c2;border-radius:999px;background:#fffdf9;color:#8a5432;font-weight:700;cursor:pointer;';
        close.onclick = function () { if (modal.parentNode) modal.parentNode.removeChild(modal); };
        head.append(title, close);
        var body = doc.createElement('div');
        body.style.cssText = 'padding:18px;';
        if (artifact.video_url && artifact.status === 'ready') {
            var video = doc.createElement('video');
            video.controls = true;
            video.autoplay = true;
            video.style.cssText = 'display:block;width:100%;max-height:64vh;border-radius:12px;background:#000;';
            try { video.src = awaitProxy(artifact.artifact_id); } catch (ignore) {}
            body.appendChild(video);
        } else {
            var pending = doc.createElement('p');
            pending.textContent = artifact.summary || '视频尚未就绪。';
            body.appendChild(pending);
        }
        if (artifact.transcript) {
            var h4 = doc.createElement('h4');
            h4.textContent = '视频讲稿';
            h4.style.cssText = 'margin:16px 0 6px;color:#4a3527;';
            var pre = doc.createElement('pre');
            pre.textContent = artifact.transcript;
            pre.style.cssText = 'white-space:pre-wrap;line-height:1.6;color:#4a3527;background:#faf3ea;border:1px solid #f0e2d2;border-radius:12px;padding:12px 14px;margin:0;';
            body.append(h4, pre);
        }
        card.append(head, body);
        modal.appendChild(card);
        doc.body.appendChild(modal);
    }
    function awaitProxy(artifact_id) {
        // video.src 需要 ObjectURL；用异步方式填充
        api.getWorkspaceVideoObjectUrl(artifact_id).then(function (url) {
            var v = document.querySelector('#kakiVideoModal video');
            if (v) v.src = url;
        }).catch(function () {});
        return '';
    }

    // 对话消息里的产物只带 id，侧栏列表带 artifact_id；统一成 artifact_id，
    // 否则 exam 的作答请求会拼出 /api/exam/undefined 并被后端以 422 拒绝。
    function normalizeArtifactId(artifact) {
        if (artifact && artifact.artifact_id == null && artifact.id != null) artifact.artifact_id = artifact.id;
        return artifact;
    }

    function renderArtifact(artifact) {
        if (!artifact || !artifact.kind) return;
        normalizeArtifactId(artifact);
        if (artifact.kind === 'tutor_plan') { renderTutorPlanArtifact(artifact); return; }
        if (artifact.kind === 'learning_graph') {
            var graphCard = document.createElement('section');
            graphCard.className = 'artifact-card';
            var graphTitle = document.createElement('strong');
            graphTitle.textContent = displayArtifactTitle(artifact.title, '正式学习图谱');
            var graphSummary = document.createElement('p');
            graphSummary.textContent = artifact.summary || '';
            var graphMeta = document.createElement('small');
            graphMeta.textContent = 'v0.1.3 · 修订 ' + (artifact.graph_revision || 1) + ' · ' + (artifact.node_count || 0) + ' 个知识节点';
            var graphActions = document.createElement('div');
            graphActions.className = 'artifact-actions';
            var openGraph = document.createElement('button');
            openGraph.textContent = '打开正式学习图谱';
            openGraph.onclick = function () { window.location.assign(artifact.url || ('/learning?graph=' + encodeURIComponent(artifact.graph_id))); };
            graphActions.appendChild(openGraph);
            graphCard.append(graphTitle, graphMeta, graphSummary, graphActions);
            $('messages').appendChild(graphCard);
            scrollController.contentResized();
            return;
        }
        if (['lecture', 'exam', 'exam_result', 'code', 'presentation', 'interactive_lesson', 'mindmap', 'video'].indexOf(artifact.kind) >= 0) {
            activeArtifact = artifact;
            if (window.KakiWorkspaceContextBridge && artifact.artifact_id && artifact.kind !== 'exam' && artifact.kind !== 'exam_result') {
                window.KakiWorkspaceContextBridge.bindArtifact(artifact, { source:'artifact_generated_or_visible', reason:'这份资源刚刚在当前对话中出现', keepView:true });
            }
        }
        if (artifact.kind === 'code') { renderCodeArtifact(artifact); return; }
        if (artifact.kind === 'video') { renderVideoArtifact(artifact); return; }
        if (artifact.kind === 'mindmap') { renderMindmapArtifact(artifact); return; }
        if (artifact.kind === 'presentation' || artifact.kind === 'interactive_lesson') {
            renderPresentationArtifact(artifact);
            return;
        }
        var card = document.createElement('section');
        card.className = 'artifact-card';
        var title = document.createElement('strong');
        title.textContent = displayArtifactTitle(artifact.title, artifact.kind === 'exam' ? '练习题' : '学习讲义');
        var summary = document.createElement('p');
        summary.textContent = artifact.summary || '';
        var meta = document.createElement('small');
        meta.textContent = artifact.kind === 'lecture'
            ? ('讲义 · 第 ' + (artifact.version || 1) + ' 版 · ' + learnerStatus(artifact.quality_status || 'passed'))
            : ((artifact.question_count || '') + ' 道题 · ' + (artifact.difficulty || '中等'));
        var actions = document.createElement('div');
        actions.className = 'artifact-actions';
        var action = document.createElement('button');
        if (artifact.kind === 'exam') {
            action.textContent = '开始作答';
            action.onclick = function () { openExam(artifact.artifact_id); };
        } else if (artifact.kind === 'exam_result') {
            action.textContent = '查看练习结果';
            action.onclick = function () { openExam(artifact.artifact_id); };
        } else {
            action.textContent = '在线查看';
            action.onclick = function () { openLecture(artifact); };
            actions.appendChild(action);
            ['pdf', 'docx'].forEach(function (formatName) {
                var exportButton = document.createElement('button');
                exportButton.textContent = formatName === 'pdf' ? '导出 PDF' : '导出 Word';
                exportButton.onclick = function () { downloadArtifact(artifact, formatName, exportButton); };
                actions.appendChild(exportButton);
            });
            var revise = document.createElement('button');
            revise.textContent = '调整这份讲义';
            revise.onclick = function () {
                beginArtifactEdit(artifact, '请修改这份讲义：');
            };
            var quiz = document.createElement('button');
            quiz.textContent = '生成练习';
            quiz.onclick = async function () {
                activeArtifact = artifact;
                quiz.disabled = true;
                setStatus('正在基于这份讲义准备练习…');
                try {
                    var response = await api.examFromWorkspaceLecture(artifact.artifact_id, { count: 3 });
                    var examArtifact = response.data || {};
                    var summaryText = examArtifact.summary || '已基于这份讲义生成 3 道练习题。';
                    var node = addMessage('assistant', summaryText);
                    renderAssistant(node, summaryText);
                    renderArtifact(examArtifact);
                    messages.push({ role: 'assistant', content: summaryText, artifact: examArtifact });
                    activeArtifact = examArtifact;
                    await loadHistory({ renderConversation: false });
                    setStatus('练习已生成，可以开始作答。');
                } catch (error) {
                    setStatus(error && error.message === '这份练习正在生成，请稍候。'
                        ? error.message : '暂时无法基于讲义出题，请稍后重试。');
                } finally {
                    quiz.disabled = false;
                }
            };
            actions.append(revise);
            var learningChain = document.createElement('section');
            learningChain.className = 'artifact-learning-chain';
            var chainLabel = document.createElement('strong');
            chainLabel.textContent = '继续学习';
            var chainHint = document.createElement('small');
            chainHint.textContent = '根据这份讲义，柿小吉建议从一次主动练习开始。';
            var chainActions = document.createElement('div');
            var cards = document.createElement('button');
            cards.type = 'button'; cards.textContent = '生成知识卡';
            cards.onclick = function () { runDerivedArtifactAction(artifact, 'KNOWLEDGE_CARD', cards, '正在生成知识卡…'); };
            var map = document.createElement('button');
            map.type = 'button'; map.textContent = '生成导图';
            map.onclick = function () { runDerivedArtifactAction(artifact, 'MINDMAP', map, '正在生成思维导图…'); };
            var exam = document.createElement('button');
            exam.type = 'button'; exam.textContent = '开始测验';
            exam.onclick = quiz.onclick;
            chainActions.append(quiz, cards, map, exam);
            learningChain.append(chainLabel, chainHint, chainActions);
            card.append(title, meta, summary, learningChain, actions);
        }
        if (!actions.childNodes.length) actions.appendChild(action);
        appendArtifactFollowup(actions, artifact);
        if (!card.childNodes.length) card.append(title, meta, summary, actions);
        else if (!card.contains(actions)) card.append(actions);
        $('messages').appendChild(card);
        scrollController.contentResized();
    }
    function tutorTaskKind(agentType) {
        var map = { EXAM:'exam', LECTURE:'lecture', PRESENTATION:'presentation', CODE:'code', MINDMAP:'mindmap', VIDEO:'video' };
        return map[String(agentType || '').toUpperCase()] || String(agentType || '').toLowerCase();
    }
    async function openTutorTaskArtifact(task) {
        if (!task || !task.artifact_id) return;
        try {
            var result = await api.listWorkspaceArtifacts('conversation', 'all', tutorPlan && tutorPlan.session_id);
            var items = result.data || [];
            var artifact = items.find(function (item) { return Number(item.artifact_id) === Number(task.artifact_id); });
            if (!artifact) artifact = { artifact_id:task.artifact_id, kind:tutorTaskKind(task.agent_type), title:'导师生成的学习资源' };
            await openSidebarArtifact(artifact);
        } catch (error) {
            setStatus('学习资源打开失败：' + error.message);
        }
    }
    var TUTOR_TEACHER_NEXT = {
        collect_more_evidence: '我会先了解你的基础，再决定从哪里开始讲。',
        reteach_scaffold: '我先换一种方式，把当前内容给你讲清楚。',
        misconception_distinction: '我们先做一次辨析，把容易混淆的地方分清。',
        targeted_practice: '先通过一个小练习确认你的掌握情况，再调整学习难度。',
        transfer_challenge: '我们用一个新的情境，检验你能不能灵活运用。',
        refresh_tutor_decision: '我先重新看看你的学习情况，再安排下一步。',
        plan_completed: '这份学习计划已经完成，我们再挑一个新目标吧。',
        default: '我们继续当前的学习。'
    };
    var TUTOR_VOICE_TOPIC_PLACEHOLDERS = /当前学习主题|当前知识点|当前学习对象|当前主题|未命名|^$/;
    function tutorPlanTopic(plan) {
        return String(plan && plan.title || '').trim();
    }
    function tutorPlanStage(plan) {
        var stage = plan && plan.current_stage || null;
        var title = String((stage || {}).title || '').trim();
        if (!title) {
            var publicView = (plan && plan.tutor_public) || {};
            title = String((publicView.current_object || {}).title || '').trim();
        }
        if (!title) return '';
        var pieces = title.split('·');
        return pieces.length > 1 ? pieces[pieces.length - 1].trim() : title;
    }
    function teacherNextText(plan) {
        var publicView = (plan && plan.tutor_public) || {};
        var action = publicView.action || (plan && plan.next_action || {}).action;
        return TUTOR_TEACHER_NEXT[action] || TUTOR_TEACHER_NEXT.default;
    }
    function renderVoiceHistoryLine(plan) {
        var lastVoice = lastVoiceSummary();
        if (!lastVoice || !lastVoice.summary) return null;
        var topic = String(lastVoice.topic || '').trim();
        if (!topic || TUTOR_VOICE_TOPIC_PLACEHOLDERS.test(topic)) {
            topic = tutorPlanTopic(plan);
            if (!topic) return null;
        }
        var turns = Number(lastVoice.user_turns || 0);
        var line = document.createElement('div');
        line.className = 'tutor-voice-banner';
        line.textContent = turns > 0
            ? '上次语音学习：' + topic + '，共 ' + turns + ' 轮交流。'
            : '上次语音学习：' + topic + '。';
        return line;
    }
    function renderTutorPlanArtifact(plan) {
        tutorPlan = plan;
        var card = document.createElement('section');
        card.className = 'tutor-plan-card tutor-plan-teaching';
        if (plan.session_id) card.dataset.tutorSessionId = String(plan.session_id);
        var head = document.createElement('div');
        head.className = 'tutor-plan-head';
        var title = document.createElement('strong');
        title.textContent = '🍊 柿小吉导师';
        var lantern = document.createElement('span');
        lantern.className = 'tutor-lantern';
        lantern.dataset.state = ((plan.tutor_public || {}).lifecycle || {}).state || 'active';
        lantern.setAttribute('aria-label', '当前学习状态');
        var percent = document.createElement('b');
        percent.textContent = Math.round(plan.progress_pct || 0) + '%';
        head.append(lantern, title, percent);
        card.appendChild(head);

        var voiceLine = renderVoiceHistoryLine(plan);
        if (voiceLine) card.appendChild(voiceLine);

        var publicView = plan.tutor_public || {};
        var lifecycle = publicView.lifecycle || {};
        var topic = tutorPlanTopic(plan);
        var stage = tutorPlanStage(plan);
        var nextAction = publicView.action || (plan.next_action || {}).action;
        var orchestration = publicView.orchestration || {};

        var facts = document.createElement('div');
        facts.className = 'tutor-plan-facts';
        facts.innerHTML = '<p><span>正在学习</span><strong></strong></p><p><span>当前阶段</span><strong></strong></p><p><span>进度</span><strong></strong></p>';
        var factValues = facts.querySelectorAll('strong');
        factValues[0].textContent = topic || '——';
        factValues[1].textContent = stage || '——';
        factValues[2].textContent = Math.round(plan.progress_pct || 0) + '%';
        card.appendChild(facts);

        var approach = document.createElement('div');
        approach.className = 'tutor-approach';
        var approachTitle = document.createElement('strong');
        approachTitle.textContent = '我会这样带你学习';
        var approachList = document.createElement('ol');
        ['了解你的基础情况', '讲解核心概念', '通过练习确认理解', '根据你的表现调整难度'].forEach(function (step) {
            var item = document.createElement('li');
            item.textContent = step;
            approachList.appendChild(item);
        });
        approach.append(approachTitle, approachList);
        card.appendChild(approach);

        var next = document.createElement('p');
        next.className = 'tutor-next-teacher';
        next.textContent = teacherNextText(plan);
        card.appendChild(next);

        var actions = document.createElement('div');
        actions.className = 'tutor-primary-actions';
        if (nextAction !== 'plan_completed') {
            var firstStep = document.createElement('button');
            firstStep.type = 'button';
            firstStep.className = 'tutor-first-step';
            firstStep.textContent = '开始第一步';
            firstStep.disabled = orchestration.can_continue === false;
            if (firstStep.disabled && orchestration.stop_reason) firstStep.title = orchestration.stop_reason;
            firstStep.onclick = function () {
                if (firstStep.disabled || active) return;
                startTutorQuestion('按你的安排继续', { tutor_delegation: true });
            };
            var later = document.createElement('button');
            later.type = 'button';
            later.className = 'tutor-later';
            later.textContent = '稍后继续';
            actions.append(firstStep, later);
        }
        card.appendChild(actions);

        var mini = document.createElement('div');
        mini.className = 'tutor-plan-mini';
        mini.hidden = true;
        var miniCopy = document.createElement('span');
        miniCopy.textContent = topic ? 'AI 导师 · ' + topic : 'AI 导师';
        var miniResume = document.createElement('button');
        miniResume.type = 'button';
        miniResume.textContent = '继续学习';
        mini.append(miniCopy, miniResume);
        card.appendChild(mini);
        var laterBtn = actions.querySelector('.tutor-later');
        if (laterBtn) {
            laterBtn.onclick = function () {
                card.classList.add('is-minimized');
                mini.hidden = false;
            };
        }
        miniResume.onclick = function () {
            card.classList.remove('is-minimized');
            mini.hidden = true;
        };

        var whyDetails = document.createElement('details');
        whyDetails.className = 'tutor-why-details';
        var whySummary = document.createElement('summary');
        whySummary.textContent = '为什么这样安排？';
        whyDetails.appendChild(whySummary);
        var whyBody = document.createElement('div');
        whyBody.className = 'tutor-why-body';
        if (lifecycle.message || lifecycle.label) {
            var lifecycleLine = document.createElement('p');
            lifecycleLine.textContent = lifecycle.message || lifecycle.label || '学习进行中';
            whyBody.appendChild(lifecycleLine);
        }
        var pedagogy = publicView.pedagogical_strategy || null;
        if (pedagogy && (pedagogy.why_text || pedagogy.status_text)) {
            var pedagogyLine = document.createElement('p');
            pedagogyLine.textContent = (pedagogy.status_text ? pedagogy.status_text + '。' : '') + (pedagogy.why_text || '');
            whyBody.appendChild(pedagogyLine);
            if (Array.isArray(pedagogy.execution) && pedagogy.execution.length) {
                var executionLine = document.createElement('small');
                executionLine.textContent = '这次会这样进行：' + pedagogy.execution.join('；');
                whyBody.appendChild(executionLine);
            }
        }
        if (publicView.reason) {
            var reasonLine = document.createElement('p');
            reasonLine.textContent = publicView.reason;
            whyBody.appendChild(reasonLine);
        }
        var evidence = publicView.evidence || {};
        if (evidence.text) {
            var evidenceLine = document.createElement('p');
            evidenceLine.textContent = evidence.text;
            whyBody.appendChild(evidenceLine);
        }
        if (orchestration.can_continue === false && orchestration.stop_reason) {
            var stopReason = document.createElement('small');
            stopReason.className = 'tutor-orchestration-stop';
            stopReason.textContent = orchestration.stop_reason;
            whyBody.appendChild(stopReason);
        }
        var personalization = publicView.personalization || null;
        if (personalization && personalization.text) {
            var personalizationLine = document.createElement('p');
            personalizationLine.textContent = personalization.text;
            whyBody.appendChild(personalizationLine);
            if (personalization.scope_note) {
                var personalizationScope = document.createElement('small');
                personalizationScope.textContent = personalization.scope_note;
                whyBody.appendChild(personalizationScope);
            }
        }
        if ((plan.plan_nodes || []).length) {
            var nodeDetails = document.createElement('div');
            nodeDetails.className = 'tutor-node-details';
            var nodeLabel = document.createElement('small');
            nodeLabel.textContent = '学习路径';
            nodeDetails.appendChild(nodeLabel);
            var nodeList = document.createElement('ul');
            (plan.plan_nodes || []).forEach(function (node) {
                var nodeState = (plan.node_states || {})[node.id] || {};
                var item = document.createElement('li');
                item.textContent = (nodeState.status === 'completed' ? '✓ ' : '') + (node.title || '');
                if (node.id === plan.current_node_id) item.classList.add('is-current');
                nodeList.appendChild(item);
            });
            nodeDetails.appendChild(nodeList);
            whyBody.appendChild(nodeDetails);
        }
        if (lifecycle.can_pause || lifecycle.can_resume) {
            var pauseLink = document.createElement('button');
            pauseLink.type = 'button';
            pauseLink.className = 'tutor-lifecycle-link';
            pauseLink.textContent = lifecycle.can_resume ? '恢复学习' : '暂停学习';
            pauseLink.onclick = async function () {
                if (pauseLink.disabled) return;
                pauseLink.disabled = true;
                try {
                    var response = lifecycle.can_resume
                        ? await api.resumeTutorSession(plan.session_id)
                        : await api.pauseTutorSession(plan.session_id);
                    tutorPlan = response.data || tutorPlan;
                    renderTutorPlanArtifact(tutorPlan);
                    setStatus(((tutorPlan.tutor_public || {}).lifecycle || {}).message || '学习位置已更新。');
                } catch (error) {
                    setStatus('学习位置更新失败：' + error.message);
                } finally {
                    pauseLink.disabled = false;
                }
            };
            whyBody.appendChild(pauseLink);
        }
        whyDetails.appendChild(whyBody);
        card.appendChild(whyDetails);

        var task = publicView.task || plan.tutor_task || null;
        if (task) {
            var taskRow = document.createElement('div');
            taskRow.className = 'tutor-task-state';
            var taskText = document.createElement('span');
            var stateText = task.state_label || task.state || '已准备';
            taskText.textContent = '学习资源：' + stateText;
            taskRow.appendChild(taskText);
            if (task.provider_blocked || task.error_code === 'PROVIDER_BLOCKED') {
                var blocked = document.createElement('small');
                blocked.textContent = '这份资源暂时没有制作成功，可以稍后重试；我不会用聊天文字冒充资源。';
                taskRow.appendChild(blocked);
            }
            if (task.state === 'completed' && task.artifact_id) {
                var open = document.createElement('button');
                open.type = 'button';
                open.textContent = '打开学习资源';
                open.onclick = function () { openTutorTaskArtifact(task); };
                taskRow.appendChild(open);
            }
            card.appendChild(taskRow);
        }

        if (nextAction === 'plan_completed') {
            setAvatarState('complete', '恭喜你完成了这份学习计划！', { expand: true });
        } else if (['collect_more_evidence', 'misconception_distinction', 'targeted_practice', 'transfer_challenge'].indexOf(nextAction) >= 0) {
            setAvatarState('asking', teacherNextText(plan), { expand: true });
        } else {
            setAvatarState('teaching', teacherNextText(plan), { expand: true });
        }

        var callPreference = readCallPreference();
        var callInviteSuppressed = callPreference.declines >= 2 && callPreference.accepts === 0;
        if (nextAction !== 'plan_completed' && lifecycle.state !== 'paused' && !callInviteSuppressed) {
            var callInvite = document.createElement('div');
            callInvite.className = 'tutor-call-invite';
            var callInviteCopy = document.createElement('span');
            var callInviteTitle = document.createElement('strong');
            var inviteCopy = callPreference.accepts >= 2 ? '上次语音讲解效果不错，要继续用语音学习吗？' : '要不要我直接给你讲一讲？';
            callInviteTitle.textContent = inviteCopy;
            var callInviteHint = document.createElement('small');
            callInviteHint.textContent = '接受后进入同一个 Tutor / 当前学习对象的连续通话，不会自动呼叫。';
            callInviteCopy.append(callInviteTitle, callInviteHint);
            var callInviteActions = document.createElement('span');
            callInviteActions.className = 'tutor-call-invite-actions';
            var acceptCall = document.createElement('button');
            acceptCall.type = 'button'; acceptCall.textContent = '接受通话';
            var laterCall = document.createElement('button');
            laterCall.type = 'button'; laterCall.textContent = '稍后';
            var prewarmPayload = { tutor_public: publicView, current_object: publicView.current_object || (plan.tutor_context || {}).node || {}, session_id: plan.session_id };
            window.setTimeout(function () { if (window.KakiVoiceCallRuntime) window.KakiVoiceCallRuntime.prewarm(prewarmPayload); }, 0);
            acceptCall.onclick = function () {
                recordCallPreference(true);
                if (!window.KakiVoiceCallRuntime) return setStatus('连续通话运行时还没有就绪。');
                window.KakiVoiceCallRuntime.prewarm(prewarmPayload);
                window.KakiVoiceCallRuntime.start('tutor_invite', { context: prewarmPayload });
            };
            laterCall.onclick = function () { recordCallPreference(false); callInvite.hidden = true; };
            callInviteActions.append(acceptCall, laterCall);
            callInvite.append(callInviteCopy, callInviteActions);
            card.appendChild(callInvite);
        }

        var completionSummary = null;
        if (nextAction === 'plan_completed') {
            completionSummary = document.createElement('section');
            completionSummary.className = 'tutor-completion-summary';
            var completedTitles = (plan.plan_nodes || []).filter(function (node) {
                return ((plan.node_states || {})[node.id] || {}).status === 'completed';
            }).map(function (node) { return node.title; }).slice(0, 3);
            var personalized = publicView.personalization || {};
            var preferenceValue = personalized.presentation_policy || personalized.strategy_summary || '';
            var preferenceCopy = typeof preferenceValue === 'string'
                ? preferenceValue
                : (preferenceValue.primary || preferenceValue.summary || '');
            completionSummary.innerHTML = '<span>今天完成</span><strong></strong><p></p><small></small>';
            completionSummary.querySelector('strong').textContent = completedTitles.length ? completedTitles.join(' · ') : '完成了今天安排的学习';
            completionSummary.querySelector('p').textContent = preferenceCopy
                ? '我发现：' + preferenceCopy
                : '我会继续根据你的练习结果和学习选择，调整更适合你的讲法。';
            completionSummary.querySelector('small').textContent = '下一步：' + (publicView.next_recommendation || '用一个新案例巩固今天的内容');
            card.appendChild(completionSummary);
        }

        var existingPlanCard = null;
        if (plan.session_id) {
            existingPlanCard = Array.from(document.querySelectorAll('.tutor-plan-card')).find(function (item) {
                return item.dataset.tutorSessionId === String(plan.session_id);
            }) || null;
        }
        if (existingPlanCard) existingPlanCard.replaceWith(card);
        else $('messages').appendChild(card);
        scrollController.contentResized();
    }
    function renderMindmapArtifact(artifact) {
        var card = document.createElement('section');
        card.className = 'artifact-card mindmap-card';
        var title = document.createElement('strong');
        title.textContent = artifact.title || '学习思维导图';
        var meta = document.createElement('small');
        meta.textContent = '思维导图 · 第 ' + (artifact.version || 1) + ' 版'
            + ((artifact.source_material_ids || []).length ? ' · 来源资料 ' + artifact.source_material_ids.length + ' 份' : '');
        var summary = document.createElement('p');
        summary.textContent = artifact.summary || '';
        var preview = document.createElement('div');
        preview.className = 'mindmap-preview';
        var branches = artifact.tree_data && Array.isArray(artifact.tree_data.children)
            ? artifact.tree_data.children.slice(0, 7) : [];
        branches.forEach(function (branch) {
            var chip = document.createElement('span');
            chip.textContent = branch.label || '';
            preview.appendChild(chip);
        });
        var actions = document.createElement('div');
        actions.className = 'artifact-actions';
        var openButton = document.createElement('button');
        openButton.textContent = '打开导图';
        openButton.onclick = function () { openMindmapArtifact(artifact); };
        var reviseButton = document.createElement('button');
        reviseButton.textContent = '继续修改';
        reviseButton.onclick = function () {
            beginArtifactEdit(artifact, '请修改刚才的思维导图：');
        };
        actions.append(openButton, reviseButton);
        appendArtifactFollowup(actions, artifact);
        card.append(title, meta, summary, preview, actions);
        $('messages').appendChild(card);
        scrollController.contentResized();
    }
    function appendMindmapNode(parent, node) {
        var item = document.createElement('li');
        var label = document.createElement('span');
        label.className = 'mindmap-node';
        label.textContent = node.label || '';
        if (node.note) label.title = node.note;
        item.appendChild(label);
        if (Array.isArray(node.children) && node.children.length) {
            var children = document.createElement('ul');
            node.children.forEach(function (child) { appendMindmapNode(children, child); });
            item.appendChild(children);
        }
        parent.appendChild(item);
    }
    function renderMindmapTree(tree) {
        var canvas = document.createElement('div');
        canvas.className = 'mindmap-canvas';
        var root = document.createElement('ul');
        root.className = 'mindmap-tree';
        appendMindmapNode(root, tree || {});
        canvas.appendChild(root);
        return canvas;
    }
    function downloadTextArtifact(fileName, text, contentType) {
        var blob = new Blob([text], { type: contentType || 'text/plain;charset=utf-8' });
        var url = URL.createObjectURL(blob);
        var link = document.createElement('a');
        link.href = url; link.download = fileName; document.body.appendChild(link);
        link.click(); link.remove(); window.setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
    }
    async function openMindmapArtifact(artifact) {
        $('artifactDrawer').hidden = false;
        $('artifactTitle').textContent = artifact.title || '学习思维导图';
        $('artifactContent').textContent = '正在加载思维导图…';
        try {
            var result = artifact.tree_data ? { data: artifact } : await api.getWorkspaceMindmap(artifact.artifact_id);
            artifact = result.data;
            activeArtifact = artifact;
            if (window.KakiWorkspaceContextBridge) window.KakiWorkspaceContextBridge.bindArtifact(artifact, { source:'artifact_opened', reason:'你刚才主动打开了这份资源', keepView:true });
            $('artifactContent').textContent = '';
            $('artifactContent').appendChild(renderMindmapTree(artifact.tree_data));
            var actions = document.createElement('div'); actions.className = 'artifact-actions';
            var exportButton = document.createElement('button'); exportButton.textContent = '导出导图 JSON';
            exportButton.onclick = function () { downloadTextArtifact((artifact.title || '学习思维导图') + '.json', JSON.stringify(artifact.tree_data || {}, null, 2), 'application/json;charset=utf-8'); };
            actions.appendChild(exportButton); $('artifactContent').appendChild(actions);
        } catch (error) {
            $('artifactContent').textContent = '思维导图加载失败：' + error.message;
        }
    }
    function renderPresentationArtifact(artifact) {
        var card = document.createElement('section');
        card.className = 'artifact-card';
        var title = document.createElement('strong');
        title.textContent = artifact.title || (artifact.kind === 'interactive_lesson' ? '互动课堂' : '学习演示');
        var meta = document.createElement('small');
        meta.textContent = (artifact.kind === 'interactive_lesson' ? '互动课堂' : 'PPT')
            + ' · ' + ((artifact.slides || []).length || artifact.slide_count || 0) + ' 页';
        var summary = document.createElement('p');
        summary.textContent = artifact.summary || '';
        var viewer = document.createElement('div');
        viewer.className = 'presentation-viewer';
        viewer.hidden = true;
        var actions = document.createElement('div');
        actions.className = 'artifact-actions';
        var openButton = document.createElement('button');
        openButton.textContent = artifact.kind === 'interactive_lesson' ? '开始课堂' : '在线查看';
        openButton.onclick = async function () {
            viewer.hidden = false;
            renderPresentationSlide(artifact, viewer, (artifact.progress || {}).current_slide || 0);
            if (window.KakiWorkspaceContextBridge) window.KakiWorkspaceContextBridge.restoreReadingPosition();
            if (artifact.kind === 'interactive_lesson') {
                await api.saveInteractiveLessonEvent(artifact.artifact_id, 'interactive_lesson_opened', {
                    slide_index: (artifact.progress || {}).current_slide || 0
                });
            }
        };
        var exportButton = document.createElement('button');
        exportButton.textContent = '导出 PPT';
        exportButton.onclick = function () { downloadPresentation(artifact, exportButton); };
        var reviseButton = document.createElement('button');
        reviseButton.textContent = '修改';
        reviseButton.onclick = function () { beginArtifactEdit(artifact, '请修改这个学习产物：'); };
        actions.append(openButton, exportButton, reviseButton);
        appendArtifactFollowup(actions, artifact);
        card.append(title, meta, summary, viewer, actions);
        $('messages').appendChild(card);
        scrollController.contentResized();
    }
    function renderPresentationSlide(artifact, viewer, index) {
        var slides = Array.isArray(artifact.slides) ? artifact.slides : [];
        if (!slides.length) {
            viewer.textContent = '暂时没有可显示的页面。';
            return;
        }
        index = Math.max(0, Math.min(index, slides.length - 1));
        var slide = slides[index] || {};
        if (artifact.kind === 'interactive_lesson') {
            setAvatarState('teaching', slide.title || '我们继续课堂讲解。', { expand: true });
        }
        viewer.textContent = '';
        var heading = document.createElement('strong');
        heading.textContent = (index + 1) + ' / ' + slides.length + ' · ' + (slide.title || '未命名页面');
        var body = document.createElement('p');
        body.textContent = Array.isArray(slide.bullets) ? slide.bullets.join('\n') : (slide.content || '');
        var controls = document.createElement('div');
        controls.className = 'artifact-actions';
        var previous = document.createElement('button');
        previous.textContent = '上一页';
        previous.disabled = index === 0;
        previous.onclick = function () { renderPresentationSlide(artifact, viewer, index - 1); };
        var readSlide = document.createElement('button');
        readSlide.textContent = '朗读本页';
        readSlide.onclick = function () {
            if (!speechAdapter || !speechAdapter.supported()) {
                setStatus('当前浏览器没有可用的语音朗读能力。');
                return;
            }
            var teachingText = (slide.title || '') + '。' + (Array.isArray(slide.bullets) ? slide.bullets.join('。') : (slide.content || ''));
            speechAdapter.speak(teachingText, {
                allowBrowserFallback: true,
                preset: 'teaching',
                onStart: function () { if (avatarAdapter && avatarAdapter.setSpeechActive) avatarAdapter.setSpeechActive(true); setAvatarState('teaching', slide.title || '正在讲解本页', { expand:true }); },
                onBoundary: function () { if (avatarAdapter && avatarAdapter.setSpeechEnvelope) avatarAdapter.setSpeechEnvelope(0.9); },
                onPause: function () { if (avatarAdapter && avatarAdapter.setSpeechActive) avatarAdapter.setSpeechActive(false); },
                onResume: function () { if (avatarAdapter && avatarAdapter.setSpeechActive) avatarAdapter.setSpeechActive(true); },
                onEnd: function () { if (avatarAdapter && avatarAdapter.setSpeechActive) avatarAdapter.setSpeechActive(false); setAvatarState('teaching', '我们继续这页内容。'); },
                onError: function () { if (avatarAdapter && avatarAdapter.setSpeechActive) avatarAdapter.setSpeechActive(false); setStatus('柿小吉的声音暂时连不上，这一页可以先看文字。'); }
            });
        };
        var next = document.createElement('button');
        next.textContent = index === slides.length - 1 ? '完成' : '下一页';
        next.onclick = async function () {
            if (artifact.kind === 'interactive_lesson') {
                var eventType = index === slides.length - 1 ? 'lesson_completed' : 'slide_viewed';
                var response = await api.saveInteractiveLessonEvent(artifact.artifact_id, eventType, { slide_index: index });
                artifact.progress = (response.data || {}).progress || artifact.progress;
                if (eventType === 'lesson_completed') {
                    setAvatarState('complete', '这节互动课堂完成了，做得很好！', { expand: true });
                }
            }
            if (index < slides.length - 1) renderPresentationSlide(artifact, viewer, index + 1);
        };
        controls.append(previous, readSlide, next);
        viewer.append(heading, body);
        var interaction = slide.interaction || {};
        if (artifact.kind === 'interactive_lesson' && Array.isArray(interaction.options)) {
            setAvatarState('asking', interaction.prompt || '想一想，再选择你的答案。', { expand: true });
            var choices = document.createElement('div');
            choices.className = 'artifact-actions';
            interaction.options.forEach(function (option, selectedIndex) {
                var choice = document.createElement('button');
                choice.textContent = option;
                choice.onclick = async function () {
                    if (choice.dataset.submitting === '1') return;
                    choice.dataset.submitting = '1';
                    try {
                        var latestResponse = await api.getWorkspacePresentation(artifact.artifact_id);
                        var latestArtifact = latestResponse.data || artifact;
                        var versionId = Number(latestArtifact.artifact_version_id || artifact.artifact_version_id || 0);
                        if (!versionId) throw new Error('互动课堂正式版本尚未就绪');
                        var itemIdentity = 'slide-' + index;
                        var requestId = choice.dataset.submissionRequestId;
                        if (!requestId) {
                            requestId = (window.crypto && window.crypto.randomUUID)
                                ? window.crypto.randomUUID() : ('answer-' + Date.now() + '-' + selectedIndex);
                            choice.dataset.submissionRequestId = requestId;
                        }
                        var idempotencyKey = choice.dataset.idempotencyKey;
                        if (!idempotencyKey) {
                            idempotencyKey = 'interactive-answer:' + artifact.artifact_id + ':' + versionId + ':' + itemIdentity + ':' + requestId;
                            choice.dataset.idempotencyKey = idempotencyKey;
                        }
                        var response = await api.submitInteractiveLessonAnswer(
                            artifact.artifact_id, versionId, itemIdentity, selectedIndex, requestId, idempotencyKey
                        );
                        var answer = response.data || {};
                        var correct = Boolean(answer.result && answer.result.correct);
                        choice.textContent += correct ? ' ✓' : ' ✗';
                        setAvatarState(correct ? 'praise' : 'correction', correct ? '回答正确，继续保持！' : '没关系，我们结合讲解再想一次。', { transientMs: 2600, returnState: 'teaching' });
                        Array.from(choices.children).forEach(function (button) { button.disabled = true; });
                        if (currentConversationMode === 'tutor' && tutorPlan) {
                            await loadHistory();
                            var evidence = tutorPlan && tutorPlan.tutor_public && tutorPlan.tutor_public.evidence;
                            setStatus(evidence && evidence.text ? evidence.text : '这次作答已记录为正式学习证据，等待可信评估更新。');
                        }
                    } catch (error) {
                        delete choice.dataset.submitting;
                        setStatus('答案保存失败：' + error.message);
                        setAvatarState('correction', '这次答案没有安全保存，请重试。', { transientMs: 2600, returnState: 'teaching' });
                    }
                };
                choices.appendChild(choice);
            });
            viewer.appendChild(choices);
        }
        viewer.appendChild(controls);
        if (artifact.kind === 'interactive_lesson') {
            api.saveInteractiveLessonEvent(artifact.artifact_id, 'slide_viewed', { slide_index: index })
                .catch(function (error) { setStatus('课堂进度保存失败：' + error.message); });
        }
        scrollController.contentResized();
    }

    function renderCodeArtifact(artifact) {
        var card = document.createElement('section');
        card.className = 'artifact-card';
        var title = document.createElement('strong');
        title.textContent = displayArtifactTitle(artifact.title, '代码示例');
        var meta = document.createElement('small');
        var version = artifact.version || 1;
        var summary = document.createElement('p');
        summary.textContent = artifact.summary || '';
        var sourceBlock = document.createElement('pre');
        sourceBlock.className = 'code-artifact-source';
        sourceBlock.textContent = artifact.source || '';
        var runBlock = document.createElement('pre');
        runBlock.className = 'code-artifact-run';
        var explanation = document.createElement('p');
        explanation.className = 'code-artifact-explanation';
        explanation.textContent = artifact.explanation || '这是一段可以运行的 Python 示例。';
        var knowledge = document.createElement('p');
        knowledge.className = 'code-artifact-knowledge';
        var points = Array.isArray(artifact.knowledge_points) ? artifact.knowledge_points : [];
        knowledge.textContent = points.length ? ('知识点：' + points.join(' / ')) : '';
        var actions = document.createElement('div');
        actions.className = 'artifact-actions';
        var rerunBtn = document.createElement('button');
        rerunBtn.textContent = '运行代码';
        rerunBtn.onclick = function () { executeCodeRun(artifact, rerunBtn); };
        var reviseBtn = document.createElement('button');
        reviseBtn.textContent = '修改代码';
        reviseBtn.onclick = function () {
            beginArtifactEdit(artifact, '请修改这段代码：');
        };
        var explainBtn = document.createElement('button');
        explainBtn.textContent = '逐行解释';
        explainBtn.onclick = function () { runCodeAction(artifact, 'explain', explainBtn); };
        var examBtn = document.createElement('button');
        examBtn.textContent = '基于代码出题';
        examBtn.onclick = function () { runCodeAction(artifact, 'exam', examBtn); };
        actions.append(rerunBtn, reviseBtn, explainBtn, examBtn);
        appendArtifactFollowup(actions, artifact);
        card.append(title, meta, summary, sourceBlock, runBlock, explanation, knowledge, actions);
        $('messages').appendChild(card);
        codeArtifactViews[String(artifact.artifact_id)] = { meta: meta, runBlock: runBlock };
        updateCodeRunView(artifact, artifact.run && artifact.run.exit_code != null ? 'finished' : 'pending');
        scrollController.contentResized();
    }
    function updateCodeRunView(artifact, state, requestError) {
        var view = artifact && codeArtifactViews[String(artifact.artifact_id)];
        if (!view) return;
        var run = artifact.run || {};
        var version = artifact.version || 1;
        if (state === 'running') {
            view.meta.textContent = '代码 · 第 ' + version + ' 版 · 正在运行…';
            view.runBlock.textContent = '正在运行…';
            return;
        }
        if (state === 'request_failed') {
            view.meta.textContent = '代码 · 第 ' + version + ' 版 · 运行请求失败';
            view.runBlock.textContent = '请求错误：' + (requestError || '未知错误');
            return;
        }
        if (state === 'pending') {
            view.meta.textContent = '代码 · 第 ' + version + ' 版 · 尚未运行';
            view.runBlock.textContent = 'stdout:\n（空）\n\nstderr:\n（空）\n\nexit code: —';
            return;
        }
        var exitCode = run.exit_code;
        var succeeded = exitCode === 0;
        view.meta.textContent = '代码 · 第 ' + version + ' 版 · '
            + (succeeded ? '运行成功' : '运行失败') + ' · exit code: ' + exitCode;
        view.runBlock.textContent = 'stdout:\n' + (run.stdout || '（空）')
            + '\n\nstderr:\n' + (run.stderr || '（空）')
            + '\n\nexit code: ' + exitCode;
    }
    async function executeCodeRun(artifact, button) {
        if (!artifact || !artifact.artifact_id) throw new Error('缺少代码产物 ID。');
        var original = button ? button.textContent : '';
        if (button) { button.disabled = true; button.textContent = '正在运行…'; }
        updateCodeRunView(artifact, 'running');
        startRuntimePolling();
        try {
            var response = await api.rerunWorkspaceCode(artifact.artifact_id);
            var data = response.data || {};
            Object.assign(artifact, data);
            activeArtifact = artifact;
            updateCodeRunView(artifact, 'finished');
            var summary = data.summary || '代码已重新运行。';
            var node = addMessage('assistant', summary);
            renderAssistant(node, summary);
            messages.push({ role: 'assistant', content: summary, artifact: artifact });
            setStatus(artifact.run && artifact.run.exit_code === 0 ? '代码运行成功' : '代码运行失败');
            await loadHistory({ renderConversation: false });
            return artifact;
        } catch (error) {
            updateCodeRunView(artifact, 'request_failed', error.message);
            setStatus('代码运行失败：' + error.message);
            setDebug({ stage: 'code_action_rerun', message: error.message });
            throw error;
        } finally {
            if (button) { button.disabled = false; button.textContent = original; }
            updateComposer(false);
            stopRuntimePolling();
        }
    }
    async function runCodeAction(artifact, action, button) {
        if (!artifact || !artifact.artifact_id) return;
        if (action === 'rerun') return executeCodeRun(artifact, button);
        var original = button.textContent;
        button.disabled = true; button.textContent = '正在处理…';
        startRuntimePolling();
        try {
            var response;
            if (action === 'rerun') response = await api.rerunWorkspaceCode(artifact.artifact_id);
            else if (action === 'explain') response = await api.explainWorkspaceCode(artifact.artifact_id);
            else if (action === 'exam') response = await api.examFromWorkspaceCode(artifact.artifact_id, { count: 3 });
            else throw new Error('unsupported code action: ' + action);
            var data = response.data || {};
            if (action === 'exam') {
                var examArtifact = Object.assign({}, data, { summary: data.summary || ('已基于当前代码生成 ' + (data.question_count || 0) + ' 道题。') });
                renderArtifact(examArtifact);
                setStatus('基于代码的练习已生成');
            } else {
                if (action === 'explain') {
                    var lines = Array.isArray(data.lines) ? data.lines : [];
                    var explainText = lines.length
                        ? lines.map(function (l) { return '第 ' + l.lineno + ' 行：' + (l.hint || l.code); }).join('\n')
                        : '当前代码无更多可解释的内容。';
                    var node = addMessage('assistant', explainText);
                    renderAssistant(node, explainText);
                    messages.push({ role: 'assistant', content: explainText });
                } else {
                    var summary = data.summary || '代码已重新运行。';
                    var node2 = addMessage('assistant', summary);
                    renderAssistant(node2, summary);
                    messages.push({ role: 'assistant', content: summary });
                    if (data.run) artifact.run = data.run;
                }
                scrollController.contentResized();
                setStatus(action === 'rerun' ? '代码已重新运行' : '已展示逐行解释');
            }
        } catch (error) {
            setStatus('操作失败：' + error.message);
            setDebug({ stage: 'code_action_' + action, message: error.message });
        } finally {
            button.disabled = false; button.textContent = original;
            stopRuntimePolling();
        }
    }
    async function runCodeArtifactAction(artifactId, action, instruction, options) {
        if (!artifactId) return;
        options = options || {};
        startRuntimePolling();
        if (!options.skipUser) {
            addMessage('user', instruction || actionLabel(action));
            messages.push({ role: 'user', content: instruction || actionLabel(action) });
            scrollController.messageSent();
        }
        try {
            var response;
            if (action === 'rerun') response = await api.rerunWorkspaceCode(artifactId);
            else if (action === 'revise') response = await api.reviseWorkspaceCode(artifactId, instruction);
            else if (action === 'explain') response = await api.explainWorkspaceCode(artifactId);
            else if (action === 'exam') response = await api.examFromWorkspaceCode(artifactId, { count: options.count || 3 });
            else throw new Error('unsupported code action: ' + action);
            var data = response.data || {};
            if (action === 'revise' || action === 'rerun') {
                var newArtifact = Object.assign({}, activeArtifact || {}, data);
                activeArtifact = newArtifact;
                var node = addMessage('assistant', data.summary || '代码已更新。');
                renderAssistant(node, data.summary || '代码已更新。');
                renderArtifact(newArtifact);
                messages.push({ role: 'assistant', content: data.summary || '代码已更新。', artifact: newArtifact });
            } else if (action === 'explain') {
                var lines = Array.isArray(data.lines) ? data.lines : [];
                var explainText = lines.length
                    ? lines.map(function (l) { return '第 ' + l.lineno + ' 行：' + (l.hint || l.code); }).join('\n')
                    : '当前代码无更多可解释的内容。';
                var node2 = addMessage('assistant', explainText);
                renderAssistant(node2, explainText);
                messages.push({ role: 'assistant', content: explainText });
            } else if (action === 'exam') {
                var examArtifact = Object.assign({}, data, { summary: data.summary || ('已基于当前代码生成 ' + (data.question_count || 0) + ' 道题。') });
                var node3 = addMessage('assistant', examArtifact.summary);
                renderAssistant(node3, examArtifact.summary);
                renderArtifact(examArtifact);
                messages.push({ role: 'assistant', content: examArtifact.summary, artifact: examArtifact });
            }
            scrollController.contentResized();
            setStatus(actionLabel(action) + '完成');
            await loadHistory({ renderConversation: false });
        } catch (error) {
            setStatus(actionLabel(action) + '失败：' + error.message);
            setDebug({ stage: 'code_action_' + action, message: error.message });
        } finally {
            updateComposer(false);
            stopRuntimePolling();
        }
    }
    function actionLabel(action) {
        var map = { rerun: '代码重新运行', revise: '代码修改', explain: '逐行解释', exam: '基于代码出题' };
        return map[action] || '代码操作';
    }
    async function downloadPresentation(artifact, button) {
        var original = button.textContent;
        button.disabled = true;
        button.textContent = '正在导出…';
        startRuntimePolling();
        try {
            var result = await api.exportWorkspacePresentation(artifact.artifact_id);
            var url = URL.createObjectURL(result.blob);
            var link = document.createElement('a');
            link.href = url;
            link.download = (artifact.title || '学习演示') + '.pptx';
            document.body.appendChild(link); link.click(); link.remove();
            window.setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
            setStatus('PPT 导出成功');
        } catch (error) {
            setStatus('PPT 导出失败：' + error.message);
        } finally {
            button.disabled = false;
            button.textContent = original;
            stopRuntimePolling();
        }
    }
    async function downloadArtifact(artifact, formatName, button) {
        var original = button.textContent;
        button.disabled = true;
        button.textContent = '正在导出…';
        startRuntimePolling();
        try {
            var result = await api.exportWorkspaceArtifact(artifact.artifact_id, formatName, {
                onHeaders: applyRuntimeSnapshot
            });
            var url = URL.createObjectURL(result.blob);
            var link = document.createElement('a');
            link.href = url;
            link.download = result.fileName || ((artifact.title || '学习讲义') + (formatName === 'pdf' ? '.pdf' : '.docx'));
            document.body.appendChild(link); link.click(); link.remove();
            window.setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
            setDebug({ export_evidence: {
                format: formatName, status: result.status, content_type: result.contentType,
                content_disposition: result.disposition, file_name: link.download,
                byte_length: result.byteLength, artifact_id: result.artifactId
            } });
            setStatus(original + '成功');
        } catch (error) {
            setStatus(original + '失败：' + error.message);
            setDebug({ export_evidence: { format: formatName, status: error.status || 0, error: error.code || 'export_failed' } });
        } finally {
            button.disabled = false; button.textContent = original;
            stopRuntimePolling();
        }
    }
    async function openLecture(artifact) {
        if (window.KakiArtifactShellHost && typeof window.KakiArtifactShellHost.open === 'function') {
            window.KakiArtifactShellHost.open(artifact, { source: 'artifact_opened' });
            return;
        }
        $('artifactDrawer').hidden = false;
        $('artifactTitle').textContent = artifact.title || '学习讲义';
        $('artifactContent').textContent = '正在加载讲义…';
        try {
            var result = artifact.content ? { data: artifact } : await api.getWorkspaceArtifact(artifact.artifact_id);
            activeArtifact = result.data;
            if (window.KakiWorkspaceContextBridge) window.KakiWorkspaceContextBridge.bindArtifact(result.data, { source:'artifact_opened', reason:'你刚才主动打开了这份讲义', keepView:true });
            var html = await window.AIRender.renderToHtml(result.data.content || '');
            $('artifactContent').innerHTML = html;
            if (window.KakiWorkspaceContextBridge) window.KakiWorkspaceContextBridge.restoreReadingPosition();
        } catch (error) { $('artifactContent').textContent = '讲义加载失败：' + error.message; }
    }
    async function openExam(examId) {
        $('artifactDrawer').hidden = false;
        $('artifactTitle').textContent = '练习作答';
        $('artifactContent').textContent = '正在加载题目…';
        if (examId == null || examId === '' || !Number.isFinite(Number(examId))) {
            $('artifactContent').textContent = '这份练习的编号已经丢失，请在左侧「学习产物」里重新打开。';
            return;
        }
        try {
            var result = await api.getExam(examId);
            renderExamForm(examId, result.data);
        } catch (error) { $('artifactContent').textContent = '题目加载失败：' + error.message; }
    }
    function renderExamForm(examId, exam) {
        var root = $('artifactContent');
        root.innerHTML = '';
        var completed = exam.status === 'completed';
        if (completed) {
            var resultHeading = document.createElement('h3');
            resultHeading.textContent = '练习结果';
            var resultScore = document.createElement('p');
            resultScore.textContent = '得分：' + exam.score + ' / ' + exam.total_score;
            root.append(resultHeading, resultScore);
        }
        (exam.questions || []).forEach(function (question, index) {
            var block = document.createElement('section');
            block.className = 'exam-question';
            var title = document.createElement('strong');
            title.textContent = (index + 1) + '. ' + (question.content || question.title || '题目');
            block.appendChild(title);
            var options = Array.isArray(question.options) ? question.options : [];
            var submittedAnswer = window.AIWorkspaceAgents.answerForExam(exam, index);
            if ((question.type === 'judge' || question.type === 'true_false') && !options.length) {
                options = ['A. 正确', 'B. 错误'];
            }
            if (options.length) {
                var list = document.createElement('div'); list.className = 'exam-options';
                options.forEach(function (option, optionIndex) {
                    var label = document.createElement('label');
                    var input = document.createElement('input'); input.type = 'radio'; input.name = 'exam-q-' + index; input.value = String.fromCharCode(65 + optionIndex);
                    input.checked = submittedAnswer === input.value;
                    input.disabled = completed;
                    label.append(input, document.createTextNode(' ' + option)); list.appendChild(label);
                });
                block.appendChild(list);
            } else {
                var answer = document.createElement('textarea'); answer.className = 'exam-answer'; answer.dataset.question = index; answer.placeholder = '输入你的答案';
                answer.value = submittedAnswer;
                answer.disabled = completed;
                block.appendChild(answer);
            }
            if (completed) {
                var submitted = document.createElement('p');
                submitted.className = 'exam-submitted-answer';
                submitted.textContent = '你的答案：' + (submittedAnswer || '未作答');
                block.appendChild(submitted);
                if (question.answer != null) {
                    var reference = document.createElement('p');
                    reference.className = 'exam-reference-answer';
                    reference.textContent = '参考答案：' + question.answer;
                    block.appendChild(reference);
                }
                var grade = Array.isArray(exam.grading) ? exam.grading[index] : null;
                if (grade && grade.feedback) {
                    var feedback = document.createElement('p');
                    feedback.className = 'exam-feedback';
                    feedback.textContent = '批改：' + grade.feedback;
                    block.appendChild(feedback);
                } else if (question.explanation) {
                    var explanation = document.createElement('p');
                    explanation.className = 'exam-explanation';
                    explanation.textContent = '解析：' + question.explanation;
                    block.appendChild(explanation);
                }
            }
            root.appendChild(block);
        });
        if (!completed) {
            var submit = document.createElement('button');
            submit.type = 'button';
            submit.textContent = '提交答案';
            submit.onclick = async function () {
                var answers = (exam.questions || []).map(function (_question, index) {
                    var checked = root.querySelector('input[name="exam-q-' + index + '"]:checked');
                    var written = root.querySelector('textarea[data-question="' + index + '"]');
                    return checked ? checked.value : (written ? written.value.trim() : '');
                });
                submit.disabled = true; submit.textContent = '正在批改…';
                try {
                    var response = await api.submitWorkspaceExam(examId, answers, 0);
                    var result = response.data || {};
                    $('artifactDrawer').hidden = true;
                    await loadHistory();
                    if (result.evaluation_status === 'evaluated') {
                        setStatus('练习已批改，可信评估已更新到 revision ' + String(result.evaluation_revision || '') + '；AI 导师已据此刷新下一步。');
                    } else if (result.evidence_pending) {
                        setStatus('练习结果已安全记录；可信评估暂未完成，AI 导师会保持等待，不提前判断掌握。');
                    } else {
                        setStatus('练习已批改并记录；AI 导师已重新读取可信学习状态。');
                    }
                } catch (error) {
                    submit.disabled = false; submit.textContent = '提交答案';
                    setStatus('提交失败：' + error.message);
                }
            };
            root.appendChild(submit);
        }
    }
    async function chooseConversationMode(mode) {
        releaseVoiceCapture(true);
        await api.setConversationMode(mode);
        var hasConversation = messages.length > 0 || Boolean($('messages').querySelector('.message'));
        currentConversationMode = mode;
        renderConversationMode();
        var picker = $('messages').querySelector('.mode-picker');
        if (picker) picker.remove();
        if (mode === 'assistant') tutorPlan = null;
        if (mode === 'assistant') {
            if (!hasConversation) {
                $('messages').innerHTML = '';
                addMessage('assistant', '你来决定学什么。我负责讲解、答疑和生成学习资料。');
            }
        } else if (!tutorPlan && !$('messages').querySelector('.tutor-goal-card')) {
            if (!hasConversation) $('messages').innerHTML = '';
            renderTutorGoalForm();
        }
        await loadHistory({ renderConversation: false });
    }
    async function applyPendingTutorEntry() {
        var pending = null;
        try { pending = JSON.parse(sessionStorage.getItem('kaki_pending_tutor_entry_v1') || 'null'); }
        catch (ignore) {}
        if (!pending || !pending.graph || !pending.node) return;
        sessionStorage.removeItem('kaki_pending_tutor_entry_v1');
        await chooseConversationMode('tutor');
        if (window.KakiWorkspaceContextBridge && window.KakiWorkspaceContextBridge.setGraphContext) {
            window.KakiWorkspaceContextBridge.setGraphContext(pending.graph, pending.node, pending.plan || null);
        }
        var goal = document.querySelector('.tutor-goal-card input[required]');
        if (goal) {
            goal.value = '围绕「' + String(pending.graph.graph_title || '当前学习计划') + '」学习「' + String(pending.node.node_title || '当前任务') + '」';
            goal.dispatchEvent(new Event('input', { bubbles:true }));
            goal.focus();
        }
        setStatus('已带入“我的学习”中的当前任务；确认时间后即可开始。');
    }
    async function ensureConversationModeForSend() {
        if (currentConversationMode) return;
        await api.setConversationMode('assistant');
        currentConversationMode = 'assistant';
        renderConversationMode();
        tutorPlan = null;
        var picker = $('messages').querySelector('.mode-picker');
        if (picker) picker.remove();
    }
    function renderTutorGoalForm() {
        var card = document.createElement('section');
        card.className = 'tutor-goal-card';
        var title = document.createElement('h3');
        title.textContent = '今天想学什么？';
        var copy = document.createElement('p');
        copy.textContent = '告诉我一个目标，剩下的学习顺序、讲解和练习由我来安排。';
        var form = document.createElement('form');
        var goal = document.createElement('input');
        goal.type = 'text'; goal.required = true; goal.placeholder = '例如：学会用 KNN 做分类';
        var timeRow = document.createElement('div'); timeRow.className = 'tutor-time-row';
        var durationField = document.createElement('label'); durationField.className = 'tutor-time-field';
        durationField.innerHTML = '<span>学习时间</span>';
        var duration = document.createElement('select');
        [15, 30, 60, 0].forEach(function (minutes) {
            var opt = document.createElement('option');
            opt.value = String(minutes);
            opt.textContent = minutes === 0 ? '自定义时长' : minutes + ' 分钟';
            if (minutes === 30) opt.selected = true;
            duration.appendChild(opt);
        });
        var customField = document.createElement('label'); customField.className = 'tutor-time-field';
        customField.innerHTML = '<span>自定义（分钟）</span>';
        var customMinutes = document.createElement('input');
        customMinutes.type = 'number'; customMinutes.min = 5; customMinutes.max = 240; customMinutes.step = 5;
        customMinutes.value = '45'; customMinutes.disabled = true; customMinutes.placeholder = '5-240';
        duration.addEventListener('change', function () {
            customMinutes.disabled = duration.value !== '0';
            if (duration.value === '0') customMinutes.focus();
        });
        durationField.appendChild(duration);
        customField.appendChild(customMinutes);
        timeRow.append(durationField, customField);
        var time = document.createElement('div'); time.className = 'tutor-time-summary';
        time.innerHTML = '<span>预计投入</span><strong>30 分钟</strong><small>我会安排讲解、练习和反馈</small>';
        var submit = document.createElement('button'); submit.type = 'submit'; submit.textContent = '开始安排';
        form.append(goal, timeRow, time, submit);
        form.onsubmit = async function (event) {
            event.preventDefault(); submit.disabled = true; submit.textContent = '正在规划…';
            var minutes = duration.value === '0' ? Math.max(5, Math.min(240, parseInt(customMinutes.value, 10) || 45)) : parseInt(duration.value, 10);
            time.querySelector('strong').textContent = minutes + ' 分钟';
            try {
                var response = await api.startTutorSession(goal.value.trim(), 1, minutes);
                tutorPlan = response.data;
                await loadHistory();
                setStatus('今天的学习已经安排好，我们从第一步开始。');
            } catch (error) {
                setStatus('学习计划创建失败：' + error.message);
                submit.disabled = false; submit.textContent = '开始安排';
            }
        };
        card.append(title, copy, form);
        $('messages').appendChild(card);
    }
    function renderConversation(rawMessages) {
        renderConversationMode();
        stopSpeechPlayback();
        activeArtifact = null;
        codeArtifactViews = {};
        $('messages').innerHTML = '';
        loadTaskState(rawMessages);
        messages = Array.isArray(rawMessages) ? rawMessages.map(function (item) {
            return item;
        }) : [];
        if (!messages.length) {
            if (!currentConversationMode) addMessage('assistant', '想从哪里开始，直接告诉我就好。');
            else if (currentConversationMode === 'tutor') renderTutorGoalForm();
            else addMessage('assistant', '你来决定学什么。我负责讲解、答疑和生成学习资料。');
        }
        messages.forEach(function (item) {
            var messageMeta = Object.assign({}, item, item.metadata || {});
            var node = addMessage(item.role, item.content, messageMeta);
            if (item.role === 'assistant') renderAssistant(node, item.content);
            if (item.role === 'assistant' && item.task && item.task.state === 'awaiting_confirmation') {
                renderTaskProposal(node, item.task);
            }
            if (messageMeta.kind === 'proactive_tutor') renderProactiveTutorActions(node, messageMeta);
            if (item.role === 'assistant' && messageMeta.conversation_action) {
                renderConversationAction(node, messageMeta.conversation_action);
            } else if (item.role === 'assistant' && messageMeta.orchestration_trace) {
                var traceRoot = document.createElement('div'); traceRoot.className = 'conversation-action-wrap';
                renderConversationTrace(traceRoot, messageMeta.orchestration_trace);
                node.parentNode.appendChild(traceRoot);
            }
            if (item.artifact) renderArtifact(item.artifact);
            if (item.search) renderWorkspaceSearch(item.search);
        });
        scrollController.threadChanged();
    }
    function sessionButton(session) {
        var button = document.createElement('button');
        var title = session.title || '新对话';
        button.className = session.thread_id === currentThread() ? 'session selected' : 'session';
        button.innerHTML = '<strong></strong><small></small>';
        button.querySelector('strong').textContent = title;
        var unread = Number(session.unread_count || 0);
        var latest = session.latest_proactive_message;
        button.querySelector('small').textContent = latest && latest.content
            ? (unread ? '● ' + unread + ' 条未读 · ' : '') + latest.content
            : (session.message_count || 0) + ' 条消息';
        if (unread) {
            button.classList.add('has-unread');
            button.setAttribute('aria-label', title + '，' + unread + ' 条导师未读消息');
        }
        button.onclick = function () { selectConversation(session); };
        return button;
    }

    async function executeProactiveTutorAction(metadata, button) {
        if (!metadata.proactive_message_id || button.disabled) return;
        button.disabled = true;
        setStatus('AI 导师正在执行这条学习建议…');
        try {
            var response = await api.executeProactiveTutorAction(metadata.proactive_message_id);
            var data = response.data || {};
            if (data.artifact_id) {
                var resources = await api.listWorkspaceArtifacts('tutor', 'all', data.tutor_session_id);
                var artifact = (resources.data || []).find(function (item) {
                    return String(item.artifact_id || item.id) === String(data.artifact_id);
                });
                if (artifact) await openSidebarArtifact(artifact);
            } else if (data.task) {
                taskState = { status:data.task.state, task:data.task, controlsEnabled:false, error:data.task.error_code || '' };
                saveTaskState();
                await runAgentTask();
            } else {
                await loadHistory({ renderConversation:true });
            }
            setStatus('导师建议已接入当前学习任务。');
        } catch (error) {
            button.disabled = false;
            setStatus('导师建议执行失败：' + error.message);
        }
    }

    function renderProactiveTutorActions(bubble, metadata) {
        var column = bubble && bubble.parentNode;
        if (!column || !metadata.proactive_message_id) return;
        var actions = document.createElement('div');
        actions.className = 'proactive-tutor-actions';
        var primary = document.createElement('button');
        primary.type = 'button';
        primary.className = 'proactive-tutor-primary';
        primary.textContent = (metadata.recommended_action || {}).label || '按导师建议继续';
        primary.onclick = function () { executeProactiveTutorAction(metadata, primary); };
        var dismiss = document.createElement('button');
        dismiss.type = 'button';
        dismiss.className = 'proactive-tutor-dismiss';
        dismiss.textContent = '稍后再说';
        dismiss.onclick = async function () {
            dismiss.disabled = true;
            try {
                await api.dismissProactiveTutorMessage(metadata.proactive_message_id);
                await loadHistory({ renderConversation:true });
            } catch (error) {
                dismiss.disabled = false;
                setStatus('暂时无法收起这条建议：' + error.message);
            }
        };
        actions.append(primary, dismiss);
        column.appendChild(actions);
        var row = column.parentNode;
        row.classList.add('proactive-tutor-message');
        if (metadata.encouragement) {
            row.classList.add('proactive-encouragement');
            setAvatarState('happy', '这一阶段完成得很扎实。');
            window.setTimeout(function () { setAvatarState('idle', ''); }, 1600);
        }
    }
    function renderSessions(rows) {
        var normal = $('sessions');
        var legacy = $('legacySessions');
        normal.innerHTML = '';
        legacy.innerHTML = '';
        rows.forEach(function (session) {
            (isLegacy(session.thread_id) ? legacy : normal).appendChild(sessionButton(session));
        });
    }
    async function openCodeArtifact(artifact) {
        $('artifactDrawer').hidden = false;
        $('artifactTitle').textContent = displayArtifactTitle(artifact.title, '代码示例');
        $('artifactContent').textContent = '正在加载代码…';
        try {
            var result = artifact.source ? { data: artifact } : await api.getWorkspaceCode(artifact.artifact_id);
            artifact = result.data;
            activeArtifact = artifact;
            if (window.KakiWorkspaceContextBridge) window.KakiWorkspaceContextBridge.bindArtifact(artifact, { source:'artifact_opened', reason:'你刚才主动打开了这份资源', keepView:true });
            var root = $('artifactContent');
            root.textContent = '';
            var source = document.createElement('pre');
            source.className = 'code-artifact-source';
            source.textContent = artifact.source || '';
            var run = document.createElement('pre');
            run.className = 'code-artifact-run';
            var latestRun = artifact.run || {};
            run.textContent = 'stdout:\n' + (latestRun.stdout || '（空）')
                + '\n\nstderr:\n' + (latestRun.stderr || '（空）')
                + '\n\nexit code: ' + (latestRun.exit_code == null ? '—' : latestRun.exit_code);
            var actions = document.createElement('div');
            actions.className = 'artifact-actions';
            var rerun = document.createElement('button');
            rerun.textContent = '运行代码';
            rerun.onclick = async function () {
                await executeCodeRun(artifact, rerun);
                openCodeArtifact(artifact);
            };
            var revise = document.createElement('button');
            revise.textContent = '修改代码';
            revise.onclick = function () { beginArtifactEdit(artifact, '请修改这段代码：'); };
            var exportCode = document.createElement('button');
            exportCode.textContent = '导出源代码';
            exportCode.onclick = function () {
                var ext = String(artifact.language || '').toLowerCase() === 'python' ? 'py' : 'txt';
                downloadTextArtifact((artifact.title || '学习代码') + '.' + ext, artifact.source || '', 'text/plain;charset=utf-8');
            };
            actions.append(rerun, exportCode, revise);
            root.append(source, run, actions);
            if (window.KakiWorkspaceContextBridge) window.KakiWorkspaceContextBridge.restoreReadingPosition();
        } catch (error) {
            $('artifactContent').textContent = '代码加载失败：' + error.message;
        }
    }
    async function openPresentationArtifact(artifact) {
        $('artifactDrawer').hidden = false;
        $('artifactTitle').textContent = artifact.title || (artifact.kind === 'interactive_lesson' ? '互动课堂' : '学习演示');
        $('artifactContent').textContent = '正在加载学习内容…';
        try {
            var result = Array.isArray(artifact.slides) ? { data: artifact } : await api.getWorkspacePresentation(artifact.artifact_id);
            artifact = result.data;
            activeArtifact = artifact;
            if (window.KakiWorkspaceContextBridge) window.KakiWorkspaceContextBridge.bindArtifact(artifact, { source:'artifact_opened', reason:'你刚才主动打开了这份资源', keepView:true });
            var root = $('artifactContent');
            root.textContent = '';
            var viewer = document.createElement('div');
            viewer.className = 'presentation-viewer';
            root.appendChild(viewer);
            renderPresentationSlide(artifact, viewer, (artifact.progress || {}).current_slide || 0);
            if (artifact.kind === 'interactive_lesson') {
                await api.saveInteractiveLessonEvent(artifact.artifact_id, 'interactive_lesson_opened', {
                    slide_index: (artifact.progress || {}).current_slide || 0
                });
            }
        } catch (error) {
            $('artifactContent').textContent = '学习内容加载失败：' + error.message;
        }
    }
    async function openSidebarArtifact(item, options) {
        normalizeArtifactId(item);
        if (!item || !item.artifact_id) return;
        options = options || {};
        var kind = item.kind || String(item.type || '').replace('workspace_', '');
        if (kind === 'video') return openVideoArtifact(item);
        if (kind === 'exam' || kind === 'exam_result') return openExam(item.artifact_id);
        if (window.KakiArtifactShellHost && typeof window.KakiArtifactShellHost.open === 'function') {
            window.KakiArtifactShellHost.open(item, options);
            return;
        }
        if (window.KakiWorkspaceContextBridge && !options.contextRestore) {
            window.KakiWorkspaceContextBridge.bindArtifact(item, { source:'artifact_opened', reason:'你刚才主动打开了这份资源' });
        }
        if (kind === 'exam' || kind === 'exam_result') return openExam(item.artifact_id);
        if (kind === 'lecture') return openLecture(item);
        if (kind === 'mindmap') return openMindmapArtifact(item);
        if (kind === 'code') return openCodeArtifact(item);
        if (kind === 'presentation' || kind === 'interactive_lesson') return openPresentationArtifact(item);
        setStatus('暂不支持打开此类学习产物。');
    }
    window.KakiWorkspaceOpenArtifact = openSidebarArtifact;
    window.KakiWorkspaceClearActiveArtifact = function () { activeArtifact = null; };
    function refreshArtifactSidebarIfOpen() {
        if ($('artifactsPanel') && !$('artifactsPanel').hidden) {
            return loadArtifactSidebar(currentArtifactFilter);
        }
        return Promise.resolve();
    }
    async function loadArtifactSidebar(artifactType) {
        if (!$('artifactSideList') || !currentThread()) return;
        currentArtifactFilter = artifactType || currentArtifactFilter || 'all';
        try {
            // 学习产物 = 当前用户全部学习资产（跨会话），按 user_id 全量展示；
            // 不按当前对话/导师会话过滤，避免"只能看到当前聊天里生成的内容"。
            var response = await api.listWorkspaceArtifacts('all', currentArtifactFilter, tutorPlan && tutorPlan.session_id);
            var rows = response.data || [];
            $('artifactSideList').innerHTML = '';
            // 更新筛选按钮 active 状态
            Array.from(($('artifactFilters') || {}).children || []).forEach(function (btn) {
                btn.classList.toggle('active', btn.dataset.type === currentArtifactFilter);
            });
            // 更新摘要
            var summary = $('artifactSummary');
            var summaryText = $('artifactSummaryText');
            var summaryBar = $('artifactSummaryBar');
            var summaryCounts = $('artifactSummaryCounts');
            if (summary && summaryText && summaryBar && summaryCounts) {
                var total = rows.length;
                var counts = { handout:0, guide:0, assessment:0, code:0 };
                rows.forEach(function (item) { if (counts[item.category] !== undefined) counts[item.category]++; });
                var labelMap = { handout:'讲义', guide:'实操', assessment:'测试', code:'代码' };
                var countParts = [];
                for (var k in counts) { if (counts[k] > 0) countParts.push((labelMap[k] || k) + ' ' + counts[k]); }
                summaryText.textContent = 'DARYA 已准备 ' + total + ' 份资料';
                summaryBar.style.setProperty('--bar-width', Math.min(100, Math.round(total / 12 * 100)) + '%');
                summaryCounts.textContent = countParts.join(' · ');
                summary.hidden = false;
            }
            if (!rows.length) {
                var empty = document.createElement('div');
                empty.className = 'artifact-empty';
                empty.innerHTML = '<p>还没有可用资料</p><small>对话中的讲义、实操、测试和代码会出现在这里</small>';
                $('artifactSideList').appendChild(empty);
                return;
            }
            var labels = { handout:'讲义', guide:'实操', assessment:'测试', code:'代码' };
            rows.forEach(function (item) {
                var button = document.createElement('button');
                button.className = 'artifact-side-item';
                button.type = 'button';
                button.textContent = item.title + ' · ' + (labels[item.category] || '资料');
                button.onclick = function () { openSidebarArtifact(item); };
                $('artifactSideList').appendChild(button);
            });
        } catch (error) {
            $('artifactSideList').textContent = '资料加载失败：' + error.message;
        }
    }
    function myMaterialLabel(item) {
        return ({ handout:'讲义', guide:'实操', assessment:'测试', code:'代码' })[item && item.category] || '资料';
    }
    function renderMyMaterials() {
        if (!$('myMaterialsList')) return;
        var query = String(($('myMaterialsSearch') && $('myMaterialsSearch').value) || '').trim().toLowerCase();
        var rows = myMaterialsRows.filter(function (item) {
            var haystack = [item.title, item.topic, item.chapter, myMaterialLabel(item)].join(' ').toLowerCase();
            return !query || haystack.indexOf(query) >= 0;
        });
        $('myMaterialsList').innerHTML = '';
        $('myMaterialsStatus').textContent = query
            ? '找到 ' + rows.length + ' 份匹配资料'
            : '共 ' + rows.length + ' 份可用资料';
        if (!rows.length) {
            var empty = document.createElement('div');
            empty.className = 'my-materials-empty';
            empty.textContent = query
                ? '没有匹配的资料，换个关键词试试。'
                : '这个分类里还没有资料。回到对话，DARYA 会在真正需要时为你准备。';
            $('myMaterialsList').appendChild(empty);
            return;
        }
        rows.forEach(function (item) {
            var row = document.createElement('button');
            row.type = 'button'; row.className = 'my-material-row';
            var kind = document.createElement('span'); kind.className = 'my-material-kind'; kind.textContent = myMaterialLabel(item);
            var copy = document.createElement('span'); copy.className = 'my-material-copy';
            var title = document.createElement('strong'); title.textContent = item.title || '未命名资料';
            var context = document.createElement('small');
            context.textContent = item.topic || item.chapter || (item.status === 'PASSED' ? '已检查，可继续使用' : '来自学习对话');
            copy.append(title, context);
            var open = document.createElement('span'); open.className = 'my-material-open'; open.textContent = '打开 →';
            row.append(kind, copy, open);
            row.onclick = function () { openSidebarArtifact(item, { source:'my_materials' }); };
            $('myMaterialsList').appendChild(row);
        });
    }
    async function loadMyMaterials(category) {
        myMaterialsCategory = category || myMaterialsCategory || 'all';
        Array.from(($('myMaterialsFilters') || {}).children || []).forEach(function (button) {
            button.classList.toggle('is-active', button.dataset.category === myMaterialsCategory);
        });
        $('myMaterialsStatus').textContent = '正在读取资料…';
        $('myMaterialsList').innerHTML = '';
        try {
            var response = await api.listWorkspaceArtifacts('all', myMaterialsCategory, tutorPlan && tutorPlan.session_id);
            myMaterialsRows = response.data || [];
            renderMyMaterials();
        } catch (error) {
            myMaterialsRows = [];
            $('myMaterialsStatus').textContent = '暂时无法读取资料：' + error.message;
        }
    }
    function openMyMaterials() {
        if (!$('myMaterialsSurface') || active) return;
        if ($('selfUnderstandingSurface') && !$('selfUnderstandingSurface').hidden) closeSelfUnderstanding();
        myMaterialsReturn = {
            scrollTop: $('messages').scrollTop,
            draft: $('input').value,
            focused: document.activeElement === $('input')
        };
        appRoot().classList.add('materials-open');
        $('myMaterialsSurface').hidden = false;
        $('myMaterialsOpen').setAttribute('aria-pressed', 'true');
        loadMyMaterials(myMaterialsCategory);
        window.requestAnimationFrame(function () { if ($('myMaterialsSearch')) $('myMaterialsSearch').focus(); });
    }
    function closeMyMaterials() {
        if (!$('myMaterialsSurface')) return;
        appRoot().classList.remove('materials-open');
        $('myMaterialsSurface').hidden = true;
        $('myMaterialsOpen').setAttribute('aria-pressed', 'false');
        var previous = myMaterialsReturn;
        myMaterialsReturn = null;
        window.requestAnimationFrame(function () {
            if (!previous) return;
            $('input').value = previous.draft || '';
            resizeComposer();
            $('messages').scrollTop = previous.scrollTop || 0;
            if (previous.focused) $('input').focus();
        });
    }
    function understandingGroupCopy(key) {
        return ({
            confirmed: { title:'你确认过的', note:'会在相关时影响讲解方式' },
            inferred: { title:'我目前的理解', note:'可以确认，也可以随时纠正' },
            uncertain: { title:'还需要确认的', note:'暂时不会被当作定论' }
        })[key];
    }
    function makeUnderstandingButton(label, action, className) {
        var button = document.createElement('button');
        button.type = 'button'; button.textContent = label;
        button.dataset.action = action;
        if (className) button.className = className;
        return button;
    }
    function renderUnderstandingEditor(row, claim) {
        if (row.querySelector('.understanding-editor')) return;
        var editor = document.createElement('div'); editor.className = 'understanding-editor';
        var input = document.createElement('textarea');
        input.maxLength = 500;
        input.value = claim.source_kind === 'memory' ? '' : claim.statement;
        input.placeholder = claim.source_kind === 'memory'
            ? '写下更准确的学习方式，例如：我喜欢先看一条短规则，再马上练习。'
            : '写下更准确的说法';
        input.setAttribute('aria-label', '更准确的说法');
        var tools = document.createElement('div'); tools.className = 'understanding-editor-tools';
        var save = makeUnderstandingButton('保存纠正', 'save-correction', 'save');
        var cancel = makeUnderstandingButton('取消', 'cancel-correction');
        tools.append(save, cancel); editor.append(input, tools); row.appendChild(editor); input.focus();
    }
    function renderUnderstandingConfirm(row) {
        if (row.querySelector('.understanding-confirm')) return;
        var panel = document.createElement('div'); panel.className = 'understanding-confirm';
        var copy = document.createElement('span'); copy.textContent = '忘掉后，这条内容不会再用于后续理解。';
        var tools = document.createElement('div');
        tools.append(makeUnderstandingButton('确认忘掉', 'confirm-forget'), makeUnderstandingButton('保留', 'cancel-forget'));
        panel.append(copy, tools); row.appendChild(panel);
    }
    function understandingClaimRow(claim) {
        var row = document.createElement('article');
        row.className = 'understanding-row'; row.dataset.claimId = claim.id; row.dataset.certainty = claim.certainty;
        var badge = document.createElement('span'); badge.className = 'understanding-certainty'; badge.textContent = claim.certainty_label;
        var copy = document.createElement('span'); copy.className = 'understanding-copy';
        var statement = document.createElement('strong'); statement.textContent = claim.statement;
        var why = document.createElement('p'); why.textContent = claim.why_it_matters;
        copy.append(statement, why);
        var actions = document.createElement('span'); actions.className = 'understanding-actions';
        if (claim.can_confirm) actions.appendChild(makeUnderstandingButton('对，就是这样', 'confirm')); 
        if (claim.can_correct) actions.appendChild(makeUnderstandingButton('不对', 'correct'));
        if (claim.can_forget) actions.appendChild(makeUnderstandingButton('忘掉这条', 'forget'));
        row.append(badge, copy, actions);
        return row;
    }
    function renderSelfUnderstanding() {
        var list = $('selfUnderstandingList');
        if (!list) return;
        list.innerHTML = '';
        var data = selfUnderstandingData || { groups:{}, empty:true };
        if (data.empty) {
            var empty = document.createElement('section'); empty.className = 'understanding-empty';
            var title = document.createElement('h3'); title.textContent = '我们还在慢慢认识彼此';
            var body = document.createElement('p'); body.textContent = '当你明确告诉我学习偏好，或我们有了足够、可撤销的共同观察，它们会出现在这里。你不需要先填写一份画像。';
            empty.append(title, body); list.appendChild(empty);
            $('selfUnderstandingStatus').textContent = '目前没有需要你检查的内容';
            return;
        }
        ['confirmed','inferred','uncertain'].forEach(function (key) {
            var rows = (data.groups && data.groups[key]) || [];
            if (!rows.length) return;
            var section = document.createElement('section'); section.className = 'understanding-group';
            var head = document.createElement('header'); head.className = 'understanding-group-head';
            var copy = understandingGroupCopy(key); var title = document.createElement('h3'); title.textContent = copy.title;
            var note = document.createElement('small'); note.textContent = copy.note;
            head.append(title, note); section.appendChild(head);
            rows.forEach(function (claim) { section.appendChild(understandingClaimRow(claim)); });
            list.appendChild(section);
        });
        $('selfUnderstandingStatus').textContent = '共 ' + data.claims.length + ' 条理解；正式学习证据始终优先';
    }
    async function loadSelfUnderstanding() {
        $('selfUnderstandingStatus').textContent = '正在整理我对你的理解…';
        try {
            var response = await api.getSelfUnderstanding();
            selfUnderstandingData = response.data;
            renderSelfUnderstanding();
        } catch (error) {
            $('selfUnderstandingStatus').textContent = '暂时无法读取：' + error.message;
        }
    }
    function openSelfUnderstanding() {
        if (!$('selfUnderstandingSurface') || active) return;
        if (!$('myMaterialsSurface').hidden) closeMyMaterials();
        selfUnderstandingReturn = {
            scrollTop: $('messages').scrollTop,
            draft: $('input').value,
            focused: document.activeElement === $('input')
        };
        appRoot().classList.add('understanding-open');
        $('selfUnderstandingSurface').hidden = false;
        $('selfUnderstandingOpen').setAttribute('aria-pressed', 'true');
        loadSelfUnderstanding();
        window.requestAnimationFrame(function () { $('selfUnderstandingClose').focus(); });
    }
    function closeSelfUnderstanding() {
        if (!$('selfUnderstandingSurface')) return;
        appRoot().classList.remove('understanding-open');
        $('selfUnderstandingSurface').hidden = true;
        $('selfUnderstandingOpen').setAttribute('aria-pressed', 'false');
        var previous = selfUnderstandingReturn; selfUnderstandingReturn = null;
        window.requestAnimationFrame(function () {
            if (!previous) return;
            $('input').value = previous.draft || '';
            resizeComposer(); $('messages').scrollTop = previous.scrollTop || 0;
            if (previous.focused) $('input').focus();
        });
    }
    function findUnderstandingClaim(id) {
        return ((selfUnderstandingData && selfUnderstandingData.claims) || []).find(function (item) { return item.id === id; });
    }
    async function applyUnderstandingAction(row, claim, action, correction) {
        $('selfUnderstandingStatus').textContent = '正在保存你的选择…';
        try {
            var response;
            if (claim.source_kind === 'memory') {
                response = await api.updateUnderstandingMemory(claim.source_id, action, claim.revision, correction);
                selfUnderstandingData = response.data;
            } else {
                var impressionAction = action === 'confirm' ? 'accurate' : (action === 'correct' ? 'modify' : 'inaccurate');
                await api.updateImpression(claim.source_id, impressionAction, correction);
                response = await api.getSelfUnderstanding(); selfUnderstandingData = response.data;
            }
            renderSelfUnderstanding();
            $('selfUnderstandingStatus').textContent = action === 'forget' ? '已忘掉这条理解' : (action === 'confirm' ? '已按你的确认更新' : '已按你的纠正更新');
            window.dispatchEvent(new CustomEvent('kaki:understanding-changed', { detail:{ claim_id:claim.id, action:action } }));
        } catch (error) {
            $('selfUnderstandingStatus').textContent = '没有保存成功：' + error.message;
            if (error.status === 409) loadSelfUnderstanding();
        }
    }
    async function handleUnderstandingClick(event) {
        var button = event.target.closest('button[data-action]'); if (!button) return;
        var row = button.closest('.understanding-row'); if (!row) return;
        var claim = findUnderstandingClaim(row.dataset.claimId); if (!claim) return;
        var action = button.dataset.action;
        if (action === 'correct') return renderUnderstandingEditor(row, claim);
        if (action === 'forget') return renderUnderstandingConfirm(row);
        if (action === 'cancel-correction') { var editor = row.querySelector('.understanding-editor'); if (editor) editor.remove(); return; }
        if (action === 'cancel-forget') { var confirm = row.querySelector('.understanding-confirm'); if (confirm) confirm.remove(); return; }
        if (action === 'save-correction') {
            var input = row.querySelector('.understanding-editor textarea');
            var value = String(input && input.value || '').trim();
            if (!value) { $('selfUnderstandingStatus').textContent = '请先写下更准确的说法'; return; }
            return applyUnderstandingAction(row, claim, 'correct', value);
        }
        if (action === 'confirm-forget') return applyUnderstandingAction(row, claim, 'forget');
        if (action === 'confirm') return applyUnderstandingAction(row, claim, 'confirm');
    }
    function mergeFreshTutorPlan(rawMessages, freshPlan) {
        var rows = Array.isArray(rawMessages) ? rawMessages.slice() : [];
        var found = false;
        rows = rows.map(function (item) {
            if (!item || !item.artifact || item.artifact.kind !== 'tutor_plan') return item;
            found = true;
            return Object.assign({}, item, {
                artifact: Object.assign({}, item.artifact, freshPlan || {}, { kind:'tutor_plan' })
            });
        });
        if (!found && freshPlan) {
            rows.push({ role:'assistant', content:'', artifact:Object.assign({ kind:'tutor_plan' }, freshPlan) });
        }
        return rows;
    }
    async function refreshCurrentTutorProjection(options) {
        options = options || {};
        if (currentConversationMode !== 'tutor' || !currentThread()) return null;
        var response = await api.getCurrentTutorSession();
        var freshPlan = response.data || null;
        if (!freshPlan) return null;
        tutorPlan = freshPlan;
        if (options.render !== false) renderTutorPlanArtifact(freshPlan);
        if (options.status && freshPlan.tutor_public) {
            var evidence = freshPlan.tutor_public.evidence || {};
            setStatus(evidence.text || freshPlan.tutor_public.reason || 'AI 导师已刷新当前学习安排。');
        }
        return freshPlan;
    }

    async function refreshTutorAfterImpressionChange() {
        if (currentConversationMode !== 'tutor' || !currentThread()) return;
        try {
            await loadHistory({ renderConversation:true });
            setStatus('AI 导师已按最新印象重新调整讲法。');
        } catch (error) {
            setDebug({ http:error.status, requestId:error.requestId, stage:'tutor_personalization_refresh', message:error.message });
        }
    }
    window.addEventListener('kaki:impression-changed', refreshTutorAfterImpressionChange);

    async function loadHistory(options) {
        options = options || {};
        var transitionVersion = Object.prototype.hasOwnProperty.call(options, 'transitionVersion')
            ? options.transitionVersion : conversationTransitionVersion;
        var expectedThreadId = options.expectedThreadId || currentThread();
        var userId = api.currentUserId();
        if (!userId) throw new Error('未识别到当前登录用户，请重新登录。');
        var result = await api.getChatHistory(userId);
        if (!isConversationTransitionCurrent(transitionVersion, expectedThreadId)) return null;
        var taskResponse = await api.getWorkspaceTaskState();
        if (!isConversationTransitionCurrent(transitionVersion, expectedThreadId)) return null;
        var serverTask = taskResponse.data && taskResponse.data.task;
        taskState = serverTask
            ? { status: serverTask.state, task: serverTask, controlsEnabled: serverTask.state !== 'running', error: serverTask.failure_reason || '' }
            : window.AIWorkspaceAgents.createTaskState();
        saveTaskState();
        var rows = result.data || [];
        var selected = rows.find(function (item) { return item.thread_id === expectedThreadId; });
        if (selected) currentConversationMode = selected.mode || null;
        renderConversationMode();
        if (selected && currentConversationMode === 'tutor') {
            var tutorResponse = await api.getCurrentTutorSession();
            if (!isConversationTransitionCurrent(transitionVersion, expectedThreadId)) return null;
            tutorPlan = tutorResponse.data || null;
            if (tutorPlan) {
                result = await api.getChatHistory(userId);
                if (!isConversationTransitionCurrent(transitionVersion, expectedThreadId)) return null;
                rows = result.data || [];
                selected = rows.find(function (item) { return item.thread_id === expectedThreadId; });
            }
            if (selected && tutorPlan) selected.messages = mergeFreshTutorPlan(selected.messages, tutorPlan);
        } else {
            tutorPlan = null;
        }
        if (!isConversationTransitionCurrent(transitionVersion, expectedThreadId)) return null;
        renderSessions(rows);
        if (options.renderConversation !== false) {
            renderConversation(selected ? selected.messages : []);
        }
        refreshArtifactSidebarIfOpen();
    }
    async function selectConversation(session) {
        releaseVoiceCapture(true);
        if (active || inputAdapterBusy) return;
        var transitionVersion = beginConversationTransition();
        setThread(session.thread_id);
        currentConversationMode = session.mode || null;
        tutorPlan = null;
        renderConversationMode();
        temporaryStyle = null;
        renderConversation(session.messages || []);
        if (Number(session.unread_count || 0) > 0) {
            try {
                await api.markProactiveConversationRead(session.thread_id);
            } catch (error) {
                setDebug({ stage:'proactive_read', message:error.message });
            }
        }
        loadWorkspaceMaterials();
        loadHistory({
            transitionVersion: transitionVersion,
            expectedThreadId: session.thread_id
        }).catch(showHistoryError);
        syncRuntimeStatus();
    }
    function showHistoryError(error) {
        transitionRuntime('backend_failure');
        setStatus('历史加载失败，请检查后端连接。');
        setDebug({ stage: 'history_load', message: error.message });
    }
    async function createConversation() {
        releaseVoiceCapture(true);
        if (active) return;
        var transitionVersion = beginConversationTransition();
        currentConversationMode = null;
        tutorPlan = null;
        temporaryStyle = null;
        activeArtifact = null;
        renderConversationMode();
        clearMessages();
        updateComposer(true);
        setStatus('正在创建新对话…');
        try {
            var response = await api.createConversation();
            if (!isConversationTransitionCurrent(transitionVersion)) return;
            var conversation = response.data || {};
            if (!conversation.thread_id) throw new Error('后端没有返回会话 ID。');
            setThread(conversation.thread_id);
            if (window.KakiWorkspaceContextBridge) window.KakiWorkspaceContextBridge.reset(conversation.thread_id);
            currentConversationMode = conversation.mode || null;
            renderConversationMode();
            temporaryStyle = null;
            activeArtifact = null;
            taskState = window.AIWorkspaceAgents.createTaskState();
            saveTaskState();
            clearMessages();
            await Promise.all([
                loadHistory({ transitionVersion: transitionVersion, expectedThreadId: conversation.thread_id }),
                loadMemories(),
                loadWorkspaceMaterials()
            ]);
            if (!isConversationTransitionCurrent(transitionVersion, conversation.thread_id)) return;
            scrollController.threadChanged();
            await syncRuntimeStatus();
            setStatus('新对话已创建');
        } catch (error) {
            showHistoryError(error);
            throw error;
        } finally {
            updateComposer(false);
        }
    }
    function updateComposer(isActive) {
        active = isActive;
        if (window.KakiWorkspaceContextBridge) window.KakiWorkspaceContextBridge.setConversationActive(isActive);
        var recording = Boolean((mediaRecorder && mediaRecorder.state !== 'inactive') || browserRecognition);
        var blocked = isActive || inputAdapterBusy || recording;
        $('send').disabled = blocked;
        $('stop').disabled = !isActive;
        $('stop').hidden = !isActive;
        $('retry').hidden = isActive || !lastFailedMessage;
        if ($('imageTool')) $('imageTool').disabled = blocked;
        if ($('libraryTool')) $('libraryTool').disabled = blocked;
        if ($('voiceTool')) $('voiceTool').disabled = blocked
            || (inputCapabilities.checked && !inputCapabilities.backendAsr && !inputCapabilities.browserAsr);
        if ($('retryTranscription')) $('retryTranscription').disabled = blocked;
    }
    function streamFailure(data) {
        updateComposer(false);
        lastFailedMessage = lastFailedMessage || $('input').value.trim();
        if ((data && data.stage) === 'provider_request' || (data && data.code) === 'PROVIDER_CONNECTION_FAILED') {
            transitionRuntime('provider_failure');
        } else {
            runtimeStatus.stream = 'Failed';
            renderRuntimeStatus();
        }
        setStatus((data && data.message) || '流式连接中断，可以重试。');
        setDebug({
            http: data && data.http,
            requestId: data && data.request_id,
            code: data && data.code,
            stage: data && data.stage || 'stream_interrupted',
            message: data && data.message,
            completed: false
        });
    }
    async function planAgentTask(userMessage, messageMetadata) {
        updateComposer(true); setStatus('正在准备智能体任务…');
        try {
            var response = await api.planWorkspaceTask(userMessage, activeArtifact, {
                material_ids: activeMaterialIds(),
                policy: 'auto'
            }, messageMetadata || {});
            var data = response.data;
            if (data.mode === 'chat') {
                if (data.task && data.task.state !== 'idle') {
                    taskState = { status:data.task.state, task:data.task, controlsEnabled:true, error:'' };
                    taskState = window.AIWorkspaceAgents.transitionTask(taskState, 'suspended');
                    saveTaskState();
                }
                updateComposer(false);
                return false;
            }

            addMessage('user', userMessage, messageMetadata);
            messages.push(Object.assign({ role: 'user', content: userMessage }, messageMetadata || {}));
            scrollController.messageSent();
            if (data.mode === 'artifact_action') {
                var operation = data.execution_payload || {};
                if (operation.artifact_kind === 'presentation') {
                    if (operation.action !== 'revise') throw new Error('暂不支持这个学习产物操作。');
                    startRuntimePolling();
                    try {
                        var presentationTargetResponse = await api.getWorkspacePresentation(operation.artifact_id);
                        var presentationTarget = presentationTargetResponse.data || {};
                        if (presentationTarget.kind !== 'presentation' && presentationTarget.kind !== 'interactive_lesson') {
                            throw new Error('未找到要修改的 PPT 或互动课堂。');
                        }
                        var revisedPresentationResponse = await api.reviseWorkspacePresentation(
                            operation.artifact_id, userMessage
                        );
                        var revisedPresentation = revisedPresentationResponse.data;
                        var revisedPresentationNode = addMessage('assistant', revisedPresentation.summary);
                        renderAssistant(revisedPresentationNode, revisedPresentation.summary);
                        renderArtifact(revisedPresentation);
                        messages.push({ role:'assistant', content:revisedPresentation.summary, artifact:revisedPresentation });
                        activeArtifact = revisedPresentation;
                        updateComposer(false);
                        await loadHistory({ renderConversation:false });
                        setStatus(revisedPresentation.kind === 'interactive_lesson' ? '互动课堂修改完成' : 'PPT 修改完成');
                    } finally {
                        stopRuntimePolling();
                    }
                    return true;
                }
                if (operation.artifact_kind === 'lecture') {
                    var lectureTarget = activeArtifact && activeArtifact.kind === 'lecture'
                        && Number(activeArtifact.artifact_id) === Number(operation.artifact_id)
                        ? activeArtifact : messages.slice().reverse().map(function (item) { return item.artifact; })
                            .find(function (item) {
                                return item && item.kind === 'lecture'
                                    && Number(item.artifact_id) === Number(operation.artifact_id);
                            });
                    if (!lectureTarget) throw new Error('未找到要修改的讲义。');
                    if (operation.action !== 'revise') throw new Error('暂不支持这个讲义操作。');
                    startRuntimePolling();
                    try {
                        var revisedLectureResponse = await api.reviseWorkspaceArtifact(lectureTarget.artifact_id, userMessage);
                        var revisedLecture = revisedLectureResponse.data;
                        var revisedLectureNode = addMessage('assistant', revisedLecture.summary);
                        renderAssistant(revisedLectureNode, revisedLecture.summary);
                        renderArtifact(revisedLecture);
                        messages.push({ role: 'assistant', content: revisedLecture.summary, artifact: revisedLecture });
                        activeArtifact = revisedLecture;
                        updateComposer(false);
                        await loadHistory({ renderConversation: false });
                        setStatus('讲义修改完成');
                    } finally {
                        stopRuntimePolling();
                    }
                    return true;
                }
                if (operation.artifact_kind === 'mindmap') {
                    var mindmapTarget = activeArtifact && activeArtifact.kind === 'mindmap'
                        && Number(activeArtifact.artifact_id) === Number(operation.artifact_id)
                        ? activeArtifact : messages.slice().reverse().map(function (item) { return item.artifact; })
                            .find(function (item) {
                                return item && item.kind === 'mindmap'
                                    && Number(item.artifact_id) === Number(operation.artifact_id);
                            });
                    if (!mindmapTarget) throw new Error('未找到要操作的思维导图。');
                    if (operation.action === 'open') {
                        await openMindmapArtifact(mindmapTarget);
                    } else if (operation.action === 'revise') {
                        startRuntimePolling();
                        try {
                            var revisedResponse = await api.reviseWorkspaceMindmap(mindmapTarget.artifact_id, userMessage);
                            var revised = revisedResponse.data;
                            var revisedNode = addMessage('assistant', revised.summary);
                            renderAssistant(revisedNode, revised.summary);
                            renderArtifact(revised);
                            messages.push({ role: 'assistant', content: revised.summary, artifact: revised });
                            activeArtifact = revised;
                            updateComposer(false);
                            await loadHistory({ renderConversation: false });
                            setStatus('思维导图修改完成');
                        } finally {
                            stopRuntimePolling();
                        }
                    } else {
                        throw new Error('暂不支持这个思维导图操作。');
                    }
                    return true;
                }
                if (operation.artifact_kind !== 'code') {
                    throw new Error('暂不支持这个产物操作。');
                }
                var target = activeArtifact && Number(activeArtifact.artifact_id) === Number(operation.artifact_id)
                    ? activeArtifact : messages.slice().reverse().map(function (item) { return item.artifact; })
                        .find(function (item) {
                            return item && item.kind === 'code'
                                && Number(item.artifact_id) === Number(operation.artifact_id);
                        });
                if (!target) throw new Error('未找到要运行的代码产物。');
                if (operation.action === 'run') {
                    await executeCodeRun(target, null);
                } else if (operation.action === 'revise') {
                    activeArtifact = target;
                    await runCodeArtifactAction(target.artifact_id, 'revise', userMessage, { skipUser: true });
                } else {
                    throw new Error('暂不支持这个产物操作。');
                }
                return true;
            }
            if (data.mode === 'cancelled' || !data.task || data.task.state === 'idle') {
                taskState = window.AIWorkspaceAgents.transitionTask(taskState, 'cancelled'); saveTaskState();
                var cancelledNode = addMessage('assistant', data.confirmation); renderAssistant(cancelledNode, data.confirmation);
                messages.push({ role: 'assistant', content: data.confirmation });
                setStatus('已取消生成任务'); updateComposer(false);
                await loadHistory({ renderConversation: false });
                return true;
            }
            taskState = { status:data.task.state, task:data.task, controlsEnabled:data.task.state !== 'running', error:'' };
            saveTaskState();
            if (data.confirmation) {
                var node = addMessage('assistant', data.confirmation); renderAssistant(node, data.confirmation);
                messages.push({ role: 'assistant', content: data.confirmation });
                if (data.task && data.task.state === 'awaiting_confirmation') renderTaskProposal(node, data.task);
                scrollController.contentResized();
            }
            if (data.execute) {
                await runAgentTask();
                return true;
            }
            setStatus(data.task.state === 'collecting_requirements' ? '请补充任务信息' : '等待你的确认');
            updateComposer(false); await loadHistory({ renderConversation: false });
            return true;
        } catch (error) {
            taskState = window.AIWorkspaceAgents.transitionTask(taskState, 'failed', error); saveTaskState();
            setStatus('任务准备失败：' + naturalTaskError(error)); setDebug({ http:error.status, requestId:error.requestId, stage:'task_plan', message:error.message }); updateComposer(false);
            return true;
        }
    }
    function finishAgentTask(artifact) {
        activeArtifact = artifact || activeArtifact;
        if (artifact && window.KakiWorkspaceContextBridge && artifact.artifact_id) {
            window.KakiWorkspaceContextBridge.bindArtifact(artifact, { source:'task_completed', reason:'这份资源刚刚由当前任务生成', keepView:true });
        }
        taskState = window.AIWorkspaceAgents.createTaskState();
        saveTaskState();
    }
    function taskProductCopy(task) {
        var kind = task.kind || task.agent_type || 'lecture';
        var labels = { lecture:'讲义', exam:'练习', code:'代码示例', mindmap:'思维导图', video:'教学视频', presentation:task.delivery_mode === 'interactive_lesson' ? '互动课堂' : '演示文稿', learning_graph:'学习路线', search:'学习资料' };
        var learning = kind === 'learning_graph';
        var steps = learning ? ['整理本次学习重点', '匹配适合你的学习方式', '准备讲解与练习']
            : (kind === 'exam' ? ['确定考查重点', '生成并检查题目', '整理作答页面']
            : (kind === 'video' ? ['整理完整讲解', '生成同步画面', '合成教学视频'] : ['规划内容结构', '生成核心内容与示例', '整理并检查成品']));
        var topic = String(task.topic || task.goal || (task.slots && (task.slots.topic || task.slots.goal || task.slots.user_goal)) || '').trim();
        if (learning) topic = topic.replace(/^(?:我要|我想|想要|帮我|带我|教我)?(?:开始)?(?:学习|学会|入门|掌握)\s*/i, '').trim();
        return {
            label:labels[kind] || '学习资源', steps:steps,
            estimate:learning ? '建议投入 30 分钟' : (kind === 'video' ? '约 2–5 分钟' : '约 30 秒'),
            eyebrow:learning ? '今天的学习安排' : '制作方案',
            title:learning ? ('我帮你安排一次' + (topic ? ('“' + topic + '”') : '专注') + '学习') : ('我来帮你制作' + (labels[kind] || '学习资源')),
            startLabel:learning ? '开始安排' : '开始制作',
            adjustLabel:learning ? '修改目标' : '调整要求',
            cancelLabel:learning ? '稍后再安排' : '暂不制作'
        };
    }
    function renderTaskProposal(bubble, task) {
        if (!bubble || !task || task.state !== 'awaiting_confirmation') return;
        var column = bubble.parentNode;
        if (!column || column.querySelector('.task-proposal')) return;
        var copy = taskProductCopy(task);
        var card = document.createElement('section'); card.className = 'task-proposal';
        card.innerHTML = '<div class="task-proposal-head"><span></span><strong></strong><small></small></div><ul></ul><div class="task-proposal-actions"></div>';
        card.querySelector('span').textContent = copy.eyebrow;
        card.querySelector('strong').textContent = copy.title;
        card.querySelector('small').textContent = copy.estimate;
        var list = card.querySelector('ul');
        copy.steps.forEach(function (step) { var item=document.createElement('li'); item.textContent=step; list.appendChild(item); });
        var actions = card.querySelector('.task-proposal-actions');
        var start=document.createElement('button'); start.type='button'; start.className='task-proposal-primary'; start.textContent=copy.startLabel;
        var adjust=document.createElement('button'); adjust.type='button'; adjust.textContent=copy.adjustLabel;
        var cancel=document.createElement('button'); cancel.type='button'; cancel.textContent=copy.cancelLabel;
        start.onclick=async function(){ start.disabled=true;adjust.disabled=true;cancel.disabled=true;start.textContent='正在准备…';try{var response=await api.controlWorkspaceTask(task,'confirm');var data=response.data||{};if(!data.task||!data.execute)throw new Error('安排已经变化，请刷新后重试。');taskState={status:data.task.state,task:data.task,controlsEnabled:false,error:''};saveTaskState();card.remove();await runAgentTask();}catch(error){start.disabled=false;adjust.disabled=false;cancel.disabled=false;start.textContent=copy.startLabel;setStatus('暂时无法开始：'+naturalTaskError(error));}};
        adjust.onclick=function(){$('input').placeholder=task.kind==='learning_graph'?'告诉我想学习什么、希望达到什么程度…':'告诉我需要增加、删减或改变的内容…';$('input').focus();setStatus(task.kind==='learning_graph'?'告诉我新的目标，我会重新安排。':'直接描述你的调整，我会更新制作方案。');};
        cancel.onclick=async function(){start.disabled=true;adjust.disabled=true;cancel.disabled=true;try{await api.controlWorkspaceTask(task,'cancel');taskState=window.AIWorkspaceAgents.createTaskState();saveTaskState();card.remove();setStatus('这项制作已取消。');}catch(error){start.disabled=false;adjust.disabled=false;cancel.disabled=false;setStatus('暂时无法取消：'+naturalTaskError(error));}};
        actions.append(start,adjust,cancel);column.appendChild(card);
    }
    function renderTaskProgress(task) {
        var copy=taskProductCopy(task);var card=document.createElement('section');card.className='task-progress-card';
        card.innerHTML='<div class="task-progress-agent"><span class="task-progress-avatar">柿</span><span><strong>柿小吉</strong><small>正在准备你的'+copy.label+'</small></span></div><ol></ol>';
        var list=card.querySelector('ol');copy.steps.forEach(function(step){var li=document.createElement('li');li.textContent=step;list.appendChild(li);});$('messages').appendChild(card);scrollController.contentResized();
        return {advance:function(index){Array.prototype.forEach.call(list.children,function(li,i){li.dataset.state=i<index?'done':(i===index?'active':'pending');});},complete:function(){Array.prototype.forEach.call(list.children,function(li){li.dataset.state='done';});card.classList.add('is-complete');}};
    }
    async function runWorkspaceSearch(task) {
        return api.searchWorkspaceSources(task);
    }
    async function waitForVideoArtifact(initialArtifact) {
        var artifact = initialArtifact;
        var stageLabels = {
            queued:'视频任务已进入队列…', generating_script:'正在生成教学讲稿…',
            generating_audio:'正在合成讲解音频…',
            generating_avatar:'正在生成柿小吉讲解画面，云端口型通常需要 1 到 4 分钟…',
            composing:'正在合成最终教学视频…'
        };
        for (var attempt = 0; attempt < 600; attempt += 1) {
            if (artifact.status === 'ready') return artifact;
            if (artifact.status === 'failed') {
                var failed = new Error(artifact.safe_error || '视频生成失败，可以从原产物重试。');
                failed.code = artifact.error_code || 'CLOUD_VIDEO_FAILED';
                failed.artifact = artifact;
                throw failed;
            }
            setStatus(stageLabels[artifact.status] || artifact.summary || '视频任务正在运行…');
            await new Promise(function (resolve) { window.setTimeout(resolve, 2000); });
            artifact = (await api.getWorkspaceVideoStatus(artifact.artifact_id)).data;
        }
        var timeoutError = new Error('视频任务仍在后台运行，可稍后从学习产物继续查看。');
        timeoutError.code = 'VIDEO_POLL_TIMEOUT';
        throw timeoutError;
    }
    async function runAgentTask() {
        var task = taskState.task;
        var productProgress = renderTaskProgress(task);
        productProgress.advance(0);
        taskState.status = 'running'; taskState.controlsEnabled = false; saveTaskState(); updateComposer(true);
        if (task.kind === 'learning_graph') setAvatarState('thinking', '我正在把学习目标编排成可持续更新的正式图谱。', { expand: true });
        else if (task.kind === 'exam') setAvatarState('asking', '我正在准备一组能真实检验理解的题目。', { expand: true });
        else if (task.kind === 'presentation' && task.delivery_mode === 'interactive_lesson') setAvatarState('teaching', '互动课堂正在准备，很快就开始。', { expand: true });
        else setAvatarState('thinking', task.kind === 'search' ? '正在查找可核验的真实资料。' : '正在组织这次学习任务。', { expand: currentConversationMode === 'tutor' });
        setStatus(task.kind === 'learning_graph' ? '正在为你准备学习路线…'
            : (task.kind === 'search' ? '正在搜索真实的官方、高校与学术资料…'
            : (task.kind === 'exam' ? '正在准备并检查练习题…'
            : (task.kind === 'code' ? '正在生成并运行代码示例…'
            : (task.kind === 'mindmap' ? '正在整理知识结构…'
            : (task.kind === 'video' ? '正在生成完整教学视频…'
            : (task.kind === 'presentation' ? (task.delivery_mode === 'interactive_lesson' ? '正在生成互动课堂…' : '正在生成 PPT…')
            : (task.kind === 'revise_lecture' ? '讲义智能体正在修改…' : '讲义智能体正在生成真实内容…'))))))));
        startRuntimePolling();
        try {
            var response;
            productProgress.advance(1);
            if (task.kind === 'learning_graph') response = await api.runWorkspaceLearningGraph(task);
            else if (task.kind === 'search') response = await runWorkspaceSearch(task);
            else if (task.kind === 'exam') response = await api.runWorkspaceExam(task);
            else if (task.kind === 'revise_lecture') response = await api.reviseWorkspaceArtifact(task.source_artifact_id, task.instruction);
            else if (task.kind === 'code') response = await api.runWorkspaceCode(task);
            else if (task.kind === 'mindmap') response = await api.runWorkspaceMindmap(task);
            else if (task.kind === 'video') response = await api.runWorkspaceVideo(task);
            else if (task.kind === 'presentation') response = await api.runWorkspacePresentation(task);
            else response = await api.runWorkspaceLecture(task);
            if (task.kind === 'search') {
                var search = response.data || {};
                var foundText = '找到 ' + (search.results || []).length + ' 条可核验资料。你可以查看原始来源，选择需要的内容导入资料库。';
                var searchNode = addMessage('assistant', foundText);
                renderAssistant(searchNode, foundText);
                renderWorkspaceSearch(search);
                messages.push({ role:'assistant', content:foundText, search:search });
                finishAgentTask();
                setStatus('权威资料搜索完成');
                setAvatarState('idle', '资料已经找到，你可以选择需要的内容导入。');
                stopRuntimePolling();
                updateComposer(false);
                await loadHistory({ renderConversation:false });
                return;
            }
            var artifact = response.data;
            if (task.kind === 'video') artifact = await waitForVideoArtifact(artifact);
            productProgress.advance(2);
            var node = addMessage('assistant', artifact.summary); renderAssistant(node, artifact.summary);
            renderArtifact(artifact); messages.push({ role:'assistant', content:artifact.summary, artifact:artifact });
            scrollController.contentResized();
            finishAgentTask(artifact);
            productProgress.complete();
            setStatus(task.kind === 'learning_graph' ? '正式学习图谱已生成'
                : (task.kind === 'exam' ? '练习题生成完成'
                : (task.kind === 'code' ? '代码生成并运行完成'
                : (task.kind === 'mindmap' ? '思维导图生成完成'
                : (task.kind === 'video' ? '教学视频生成完成'
                : (task.kind === 'presentation' ? (task.delivery_mode === 'interactive_lesson' ? '互动课堂生成完成' : 'PPT 生成完成')
                : (task.kind === 'revise_lecture' ? '讲义修改完成' : '讲义生成完成')))))));
            if (task.kind === 'learning_graph') setAvatarState('idle', '正式学习图谱已经准备好，可以打开继续学习。');
            else if (task.kind === 'exam') setAvatarState('asking', '题目准备好了，开始作答吧。', { expand: true });
            else if (task.kind === 'presentation' && task.delivery_mode === 'interactive_lesson') setAvatarState('teaching', '互动课堂准备好了，我们开始。', { expand: true });
            else setAvatarState('idle', '学习产物已经准备好。');
            stopRuntimePolling();
            updateComposer(false); await loadHistory({ renderConversation:false });
            if (currentConversationMode === 'tutor') await refreshCurrentTutorProjection({ render:true });
        } catch (error) {
            taskState = window.AIWorkspaceAgents.transitionTask(taskState, 'failed', error); saveTaskState();
            var failureMessage = taskProductCopy(task).label + '生成遇到问题，当前没有产生可用成品。';
            messages.push({ role: 'assistant', content: failureMessage, task: taskState.task });
            var failureNode = addMessage('assistant', failureMessage);
            var recoveryActions = document.createElement('div');
            recoveryActions.className = 'task-failure-actions';
            var retryAgent = document.createElement('button');
            retryAgent.textContent = '重新生成';
            retryAgent.onclick = runAgentTask;
            var simpleAgent = document.createElement('button');
            simpleAgent.textContent = '换成简版';
            simpleAgent.onclick = function () {
                task.slots = Object.assign({}, task.slots || {}, { detail_level:'brief', length:'short' });
                runAgentTask();
            };
            var cancelAgent = document.createElement('button');
            cancelAgent.textContent = '取消';
            cancelAgent.onclick = function () {
                taskState = window.AIWorkspaceAgents.createTaskState();
                saveTaskState(); recoveryActions.remove(); setStatus('这次生成已取消。'); updateComposer(false);
            };
            recoveryActions.append(retryAgent, simpleAgent, cancelAgent);
            failureNode.appendChild(recoveryActions);
            if (error.artifact) renderArtifact(error.artifact);
            setStatus('生成遇到问题，可以重新生成或换成简版。');
            setAvatarState('correction', '这次任务没有成功，可以重试，普通聊天仍然可用。', { transientMs: 2800 });
            setDebug({ http:error.status, requestId:error.requestId, stage:'agent_execution', message:error.message }); updateComposer(false);
            stopRuntimePolling();
        }
    }
    function hasPendingAgentTask() {
        var status = taskState && (taskState.status || (taskState.task && taskState.task.state));
        return ['collecting_requirements', 'awaiting_confirmation', 'running'].indexOf(status) >= 0;
    }
    function isExplicitTutorDelegation(message) {
        if (currentConversationMode !== 'tutor' || !tutorPlan || hasPendingAgentTask()) return false;
        return window.AIWorkspaceAgents.isTutorContinuation(message);
    }
    function shouldStartTutorQuestion(message) {
        if (!isExplicitTutorDelegation(message)) return false;
        var publicView = tutorPlan.tutor_public || {};
        var orchestration = publicView.orchestration || {};
        if (orchestration.can_continue === false) return true;
        var action = publicView.action || (tutorPlan.next_action || {}).action;
        return ['collect_more_evidence', 'reteach_scaffold', 'misconception_distinction', 'targeted_practice', 'transfer_challenge'].indexOf(action) >= 0;
    }
    async function startTutorQuestion(userMessage, messageMetadata) {
        var publicView = tutorPlan && tutorPlan.tutor_public || {};
        var orchestration = publicView.orchestration || {};
        var recoveryAttempt = orchestration.can_continue === false;
        updateComposer(true);
        setStatus('AI 导师正在依据可信学习状态准备下一步…');
        try {
            var response = await api.prepareTutorNextAction(tutorPlan.session_id, userMessage, {
                active_artifact_id: activeArtifact && activeArtifact.id || null,
                material_ids: activeMaterialIds()
            });
            var task = response.data;
            if (task.tutor_decision) {
                tutorPlan.next_action = task.tutor_decision;
                tutorPlan.tutor_context = task.tutor_context || tutorPlan.tutor_context;
                tutorPlan.tutor_public = task.tutor_public || tutorPlan.tutor_public;
                tutorPlan.tutor_task = task.tutor_task || tutorPlan.tutor_task;
            }
            messages.push(Object.assign({ role: 'user', content: userMessage }, messageMetadata || {}));
            addMessage('user', userMessage, messageMetadata);
            scrollController.messageSent();
            if (task.recovery_only || task.state === 'evidence_pending') {
                var refreshedPublic = task.tutor_public || {};
                var refreshedOrchestration = refreshedPublic.orchestration || {};
                var stopText = refreshedOrchestration.stop_reason
                    || '正式考试结果已经保留，可信评估暂时还没完成；稍后再按导师安排继续即可重试。';
                messages.push({ role: 'assistant', content: stopText });
                addMessage('assistant', stopText);
                setStatus(stopText);
                setAvatarState('idle', stopText, { expand: true });
                if (tutorPlan) renderTutorPlanArtifact(tutorPlan);
                updateComposer(false);
                return true;
            }
            taskState = { status: task.state, task: task, controlsEnabled: false, error: task.error_code || '' };
            saveTaskState();
            if (task.state === 'failed' && task.error_code === 'PROVIDER_BLOCKED') {
                var blocked = 'AI 导师已做出真实资源动作，但当前真实生成 Provider 不可用；系统没有用 mock 产物冒充成功。';
                messages.push({ role: 'assistant', content: blocked, task: task });
                addMessage('assistant', blocked);
                setStatus('真实资源 Provider 当前不可用，任务已保留为可审计失败状态。');
                updateComposer(false);
                await loadHistory();
                return true;
            }
            await runAgentTask();
            return true;
        } catch (error) {
            updateComposer(false);
            setStatus('导师下一步准备失败：' + error.message);
            setDebug({ http:error.status, requestId:error.requestId, stage:'tutor_next_action', message:error.message });
            return false;
        }
    }
    function followupRefIds(snapshot) {
        snapshot = snapshot || {};
        var artifactId = Number(snapshot.target_artifact_id);
        var versionId = Number(snapshot.target_version_id);
        var examId = Number(snapshot.exam_id);
        var messageId = String(snapshot.reply_to_message_id || '').trim();
        return {
            input_context_mode: snapshot.mode || null,
            reply_to_message_id: messageId || null,
            target_artifact_id: Number.isFinite(artifactId) && artifactId >= 1 ? artifactId : null,
            target_version_id: Number.isFinite(versionId) && versionId >= 1 ? versionId : null,
            exam_id: Number.isFinite(examId) && examId >= 1 ? examId : null
        };
    }
    function questionContextForSend(artifact, followupRefs) {
        var examId = followupRefs && followupRefs.exam_id;
        if (!examId && isExamArtifact(artifact)) examId = Number(artifact.artifact_id || artifact.id);
        if (Number.isFinite(examId) && examId >= 1) return { exam_id: examId };
        if (!artifact) return null;
        var kind = artifactKind(artifact);
        if (kind === 'interactive_lesson' || kind === 'presentation') {
            return {
                artifact_id: artifact.artifact_id,
                artifact_kind: kind,
                title: artifact.title,
                current_slide: (artifact.progress || {}).current_slide || 0,
                outline: (artifact.outline || []).slice(0, 20)
            };
        }
        if (kind === 'code') {
            return { artifact_id: artifact.artifact_id, artifact_kind: 'code' };
        }
        return null;
    }
    function isConversationCapabilityCandidate(message) {
        var text = String(message || '').replace(/\s+/g, '');
        if (!text) return false;
        if (/(?:先让我|让我|我先).{0,12}(?:自己想|自己做|自己试|想一想|做一做)|(?:不要|别|先别).{0,10}(?:继续提示|提示|追问|推荐|生成|打扰)/.test(text)) return false;
        if (/^(?:为什么|为何|怎么|什么时候|什么情况|有必要).{0,24}(?:出题|测验|讲义|资料|实操|代码练习|CodeLab|code.?lab)/i.test(text)) return false;
        var explicitAction = /(?:自己写|写代码|代码练习|代码验证|用代码|CodeLab|code.?lab|测一下|测测|测验|出几道题|出题|考考我|掌握了没有|会没会|到底会不会|动手试|实际操作|实操|带我做|别只讲|跟着做|做一遍|整理一下|整理这个|压一下重点|压成重点|适合看的资料|一份资料|一份讲义|学习资料|帮我归纳)/i.test(text);
        if (explicitAction) return true;
        if (/[？?]|什么意思|到底是什么|为什么|怎么理解|区别是什么/.test(text)) return false;
        return /^(?:我想)?(?:练|练习|试|试试)(?:一?下|一?练)?[。！!，,]*$/.test(text);
    }
    // In-flight send latch: `active` is only set after the planning awaits
    // below, so a double click during `ensureConversationModeForSend()` would
    // otherwise start two paid plan/stream requests. The latch covers the
    // whole send body; the finally releases it together with `active`.
    var sendGuardBusy = false;
    async function send(message, retry) {
        if (active || sendGuardBusy || !message) return;
        sendGuardBusy = true;
        try {
            await sendInner(message, retry);
        } finally {
            sendGuardBusy = false;
        }
    }
    async function sendInner(message, retry) {
        await ensureConversationModeForSend();
        var userMessage = message.trim();
        if (window.KakiVoiceCallRuntime && window.KakiVoiceCallRuntime.getState().active) {
            window.KakiVoiceCallRuntime.onFormalSendStart({ message:userMessage, context: window.KakiWorkspaceRuntimeBridge && window.KakiWorkspaceRuntimeBridge.getCallContext ? window.KakiWorkspaceRuntimeBridge.getCallContext() : {} });
        }
        setAvatarState('thinking', '正在理解你的问题。', { expand: currentConversationMode === 'tutor' });
        var outgoingAttachments = retry ? pendingAttachmentRefs() : pendingAttachmentRefs();
        var referenceSnapshot = replyTarget ? Object.assign({}, replyTarget) : null;
        var followupRefs = followupRefIds(referenceSnapshot);
        var isFollowup = followupRefs.input_context_mode === 'reply';
        var messageId = retry ? lastFailedMessageId : ((window.crypto && window.crypto.randomUUID)
            ? window.crypto.randomUUID() : 'message_' + Date.now());
        var messageMetadata = {
            id: messageId,
            attachment_refs: outgoingAttachments,
            input_context_mode: followupRefs.input_context_mode,
            reply_to_message_id: followupRefs.reply_to_message_id,
            target_artifact_id: followupRefs.target_artifact_id,
            target_version_id: followupRefs.target_version_id
        };
        if (!retry && !isFollowup && !outgoingAttachments.length && shouldStartTutorQuestion(userMessage)) {
            var tutorQuestionStarted = await startTutorQuestion(userMessage, messageMetadata);
            if (tutorQuestionStarted) {
                clearReplyTarget();
                $('input').value = '';
            }
            return;
        }
        // Server semantic planning is consulted for every non-delegation
        // message, tutor mode included.  Frontend regex intent hints never veto
        // the plan call: contextual tutor requests (for example a post-call
        // "根据刚才聊的内容出题") do not match the narrow explicit-command shapes,
        // and planAgentTask falls through to the ordinary chat stream whenever
        // the server router answers mode 'chat'.
        // 追问必须走对话流：规划器常把追问误判成改产物/新任务，结果只有“任务准备失败”，没有回复。
        if (!retry && !isFollowup && !isExplicitTutorDelegation(userMessage)
            && !isConversationCapabilityCandidate(userMessage)) {
            var handled = await planAgentTask(userMessage, messageMetadata);
            if (handled) {
                if (outgoingAttachments.length) setActiveMaterial(outgoingAttachments[0].material_id);
                clearPendingAttachments();
                clearReplyTarget();
                $('input').value = '';
                return;
            }
        }
        if (!retry) {
            messages.push(Object.assign({ role: 'user', content: userMessage }, messageMetadata));
            addMessage('user', userMessage, messageMetadata);
            scrollController.messageSent();
        }
        $('input').value = '';
        resizeComposer();
        updateComposer(true);
        if (!memoriesLoaded) {
            try { await loadMemories(); }
            catch (error) {
                updateComposer(false);
                return showHistoryError(error);
            }
        }
        setStatus('正在连接 AI 服务…');
        var answer = '';
        var pendingConversationAction = null;
        var answerNode = addMessage('assistant', '');
        scrollController.streamUpdated();
        return api.chatStream(userMessage, messages.slice(0, -1), {
            onOpen: function (snapshot) {
                applyRuntimeSnapshot(snapshot);
            },
            onContext: function (context) {
                setDebug({
                    http: '',
                    requestId: '',
                    code: '',
                    stage: '',
                    completed: false,
                    message: '',
                    memory_loaded: context.memory_loaded,
                    memory_keys: context.memory_keys,
                    effective_response_style: context.effective_response_style,
                    memory_source: context.memory_source,
                    source_material_ids: context.source_material_ids,
                    source_citations: context.source_citations
                });
            },
            onContent: function (_, __, chunk) {
                answer += chunk;
                if (window.KakiVoiceCallRuntime && window.KakiVoiceCallRuntime.getState().active) window.KakiVoiceCallRuntime.onAssistantChunk(chunk, answer);
                answerNode.textContent = answer;
                scrollController.streamUpdated();
                transitionRuntime('content');
                setStatus('正在接收回复…');
            },
            onConversationAction: function (action) {
                pendingConversationAction = action || null;
                if (pendingConversationAction) setStatus('DARYA 已接手，正在准备学习资料…');
            },
            onMemoryCandidate: function (candidate) {
                candidate.authorization = isExplicitMemoryCommand(userMessage) ? 'explicit' : 'confirm';
                renderMemoryCandidate(answerNode, candidate);
            },
            onMemorySaved: function () {
                loadMemories().catch(function (error) {
                    setStatus('记忆已保存，但列表刷新失败：' + (error.message || '未知错误'));
                });
            },
            onError: function (data) {
                if (window.KakiVoiceCallRuntime && window.KakiVoiceCallRuntime.getState().active) window.KakiVoiceCallRuntime.onFormalSendError(data && data.message || '回复中断');
                answerNode.closest('.msg').remove();
                lastFailedMessage = userMessage;
                lastFailedMessageId = messageId;
                streamFailure(data || {});
                setAvatarState('correction', '回复中断了，可以重试。', { transientMs: 2600 });
            },
            onDone: function (data) {
                if (!answer) return streamFailure({ stage: 'stream_interrupted', message: '未收到 AI 内容。' });
                if (window.KakiVoiceCallRuntime && window.KakiVoiceCallRuntime.getState().active) window.KakiVoiceCallRuntime.onFormalSendDone(answer);
                var assistantMessageId = data && data.assistant_message_id;
                var assistantMessage = { role: 'assistant', content: answer, id: assistantMessageId };
                if (pendingConversationAction) {
                    assistantMessage.conversation_action = pendingConversationAction;
                    assistantMessage.orchestration_trace = pendingConversationAction.trace || [];
                }
                messages.push(assistantMessage);
                var answerRow = answerNode.closest('.msg');
                if (assistantMessageId && answerRow) {
                    answerRow.dataset.messageId = assistantMessageId;
                    if (answerRow._followButton) answerRow._followButton.disabled = false;
                }
                renderAssistant(answerNode, answer);
                if (pendingConversationAction) renderConversationAction(answerNode, pendingConversationAction);
                scrollController.contentResized();
                var finishTurn = function (completedAction) {
                    if (completedAction) {
                        assistantMessage.conversation_action = completedAction;
                        assistantMessage.orchestration_trace = completedAction.trace || [];
                        setStatus(completedAction.status === 'ready' ? '' : (completedAction.error_message || '资料仍在准备中'));
                        refreshArtifactSidebarIfOpen();
                    } else {
                        setStatus('');
                    }
                    lastFailedMessage = '';
                    lastFailedMessageId = '';
                    if (outgoingAttachments.length) setActiveMaterial(outgoingAttachments[0].material_id);
                    else if (data && data.source_material_ids && data.source_material_ids.length) setActiveMaterial(data.source_material_ids[0]);
                    clearPendingAttachments();
                    clearReplyTarget();
                    updateComposer(false);
                    transitionRuntime('completed');
                    setAvatarState('idle', currentConversationMode === 'tutor' ? '问题回答完了，我们继续当前学习计划。' : '');
                    setDebug({ http: 200, requestId: data && data.request_id, completed: true });
                    if (currentConversationMode === 'tutor') {
                        refreshCurrentTutorProjection({ render: true }).catch(showHistoryError);
                    }
                    loadHistory({ renderConversation: false }).catch(showHistoryError);
                    window.setTimeout(function () {
                        if (!active && runtimeStatus.stream === 'Completed') transitionRuntime('idle');
                    }, 1200);
                };
                if (pendingConversationAction && pendingConversationAction.status !== 'ready') {
                    executeConversationAction(pendingConversationAction, answerNode).then(finishTurn);
                } else {
                    finishTurn(pendingConversationAction);
                }
            }
        }, {
            retry: Boolean(retry),
            message_id: messageId,
            style: temporaryStyle,
            source_context: {
                material_ids: activeMaterialIds(),
                policy: 'auto'
            },
            attachment_refs: pendingAttachmentRefs(),
            input_context_mode: followupRefs.input_context_mode,
            reply_to_message_id: followupRefs.reply_to_message_id,
            target_artifact_id: followupRefs.target_artifact_id,
            target_version_id: followupRefs.target_version_id,
            question_context: questionContextForSend(activeArtifact, followupRefs)
        }).catch(function (error) {
            if (window.KakiVoiceCallRuntime && window.KakiVoiceCallRuntime.getState().active) window.KakiVoiceCallRuntime.onFormalSendError(error && error.message || '连接失败');
            if (error && error.name === 'AbortError') {
                answerNode.closest('.msg').remove();
                lastFailedMessage = userMessage;
                lastFailedMessageId = messageId;
                streamFailure({ stage: 'stream_interrupted', message: '已停止生成。' });
                return;
            }
            answerNode.closest('.msg').remove();
            lastFailedMessage = userMessage;
            lastFailedMessageId = messageId;
            streamFailure({ stage: error.stage || 'connect', message: error.message });
        });
    }
    window.KakiWorkspaceRuntimeBridge = {
        getCallContext: function () {
            var publicView = tutorPlan && tutorPlan.tutor_public || {};
            return {
                thread_id: currentThread(),
                conversation_mode: currentConversationMode,
                current_object: publicView.current_object || (tutorPlan && tutorPlan.tutor_context && tutorPlan.tutor_context.node) || (activeArtifact ? { kind:activeArtifact.kind, title:activeArtifact.title, artifact_id:activeArtifact.artifact_id } : null),
                tutor_public: publicView,
                tutor_session_id: tutorPlan && tutorPlan.session_id || null,
                active_artifact_id: activeArtifact && activeArtifact.artifact_id || null,
                material_ids: activeMaterialIds()
            };
        },
        getInputCapabilities: function () { return Object.assign({}, inputCapabilities); },
        transcribeAudio: function (blob, fileName) { return api.transcribeWorkspaceAudio(blob, fileName); },
        sendVoiceMessage: function (text) { return send(String(text || ''), false); },
        abortFormalSend: function () { try { api.abortActiveStream(); } catch (ignore) {} },
        isConversationBusy: function () { return Boolean(active); },
        restoreAfterCall: function () { renderConversationMode(); setAvatarState('idle', currentConversationMode === 'tutor' ? '我们回到刚才的学习位置。' : ''); },
        getVoicePersona: function () { return speechAdapter && speechAdapter.getPersona ? speechAdapter.getPersona() : null; },
        reportVoiceEnded: function (payload) {
            var data = payload || {};
            var topic = String(data.topic || '').trim();
            if (!topic || /当前学习主题|当前知识点|当前学习对象|当前主题|^$/.test(topic)) {
                topic = String(((tutorPlan && tutorPlan.title) || (tutorPlan && tutorPlan.tutor_public && tutorPlan.tutor_public.current_object) || {}).title || '').trim();
                if (!topic) return Promise.resolve({ skipped: true });
            }
            var turns = Number(data.user_turns || 0);
            var summary = '语音学习了「' + topic + '」，共 ' + turns + ' 轮对话。';
            try { localStorage.setItem('kaki_last_voice_summary_v1', JSON.stringify({ topic: topic, summary: summary, user_turns: turns, at: Date.now(), reason: data.reason || 'ended' })); } catch (ignore) {}
            var sessionId = (tutorPlan && tutorPlan.session_id) || (data && data.session_id) || null;
            if (!sessionId) return Promise.resolve({ skipped: true });
            return api.saveTutorEvent(sessionId, 'voice_session_ended', {
                topic: topic, summary: summary, reason: data.reason || 'ended',
                user_turns: data.user_turns || 0, assistant_turns: data.assistant_turns || 0,
                interruptions: data.interruptions || 0, duration_seconds: data.duration_seconds || 0
            }).catch(function () { return null; });
        }
    };

    async function login() {
        $('loginError').textContent = '';
        try {
            var result = await api.login($('username').value, $('password').value);
            sessionStorage.setItem(key.token, result.data.token);
            sessionStorage.setItem(key.userId, result.data.user_id);
            if ($('login')) $('login').hidden = true;
            appRoot().hidden = false;
            await createConversation();
            await probeInputCapabilities();
        } catch (error) {
            $('loginError').textContent = error.message || '登录失败。';
        }
    }
    if ($('loginBtn')) $('loginBtn').onclick = login;
    if ($('newChat')) $('newChat').onclick = function () { createConversation().catch(showHistoryError); };
    if ($('historyToggle')) $('historyToggle').onclick = function () {
        sidebarState.historyCollapsed = !sidebarState.historyCollapsed;
        saveSidebarState();
        applySidebarState();
    };
    if ($('sidebarToggle')) $('sidebarToggle').onclick = function () {
        sidebarState.sidebarCollapsed = !sidebarState.sidebarCollapsed;
        saveSidebarState();
        applySidebarState();
    };
    if ($('artifactsToggle')) $('artifactsToggle').onclick = function () {
        var panel = $('artifactsPanel');
        panel.hidden = !panel.hidden;
        $('artifactsToggle').textContent = panel.hidden ? '⌄' : '⌃';
        $('artifactsToggle').setAttribute('aria-expanded', panel.hidden ? 'false' : 'true');
        if (!panel.hidden) loadArtifactSidebar(currentArtifactFilter);
    };
    if ($('artifactFilters')) $('artifactFilters').onclick = function (event) {
        var type = event.target && event.target.dataset && event.target.dataset.type;
        if (type) loadArtifactSidebar(type);
    };
    if ($('artifactSearchToggle')) $('artifactSearchToggle').onclick = function () {
        var box = $('artifactSearchBox');
        if (box) {
            box.hidden = !box.hidden;
            if (!box.hidden) { var input = $('artifactSearchInput'); if (input) input.focus(); }
        }
    };
    if ($('artifactSearchInput')) $('artifactSearchInput').oninput = function () {
        if (!$('artifactSideList') || !currentThread()) return;
        var query = this.value.trim().toLowerCase();
        api.listWorkspaceArtifacts('all', 'all', tutorPlan && tutorPlan.session_id).then(function (response) {
            var rows = response.data || [];
            var labels = { handout:'讲义', guide:'实操', assessment:'测试', code:'代码' };
            $('artifactSideList').innerHTML = '';
            var filtered = query ? rows.filter(function (item) { return (item.title || '').toLowerCase().indexOf(query) >= 0; }) : rows;
            if (!filtered.length) {
                var empty = document.createElement('div');
                empty.className = 'artifact-empty';
                empty.innerHTML = '<p>' + (query ? '没有找到匹配的资料' : '还没有可用资料') + '</p><small>' + (query ? '换个关键词试试' : '对话中的讲义、实操、测试和代码会显示在这里') + '</small>';
                $('artifactSideList').appendChild(empty);
                return;
            }
            filtered.forEach(function (item) {
                var button = document.createElement('button');
                button.className = 'artifact-side-item';
                button.type = 'button';
                button.textContent = item.title + ' · ' + (labels[item.category] || '资料');
                button.onclick = function () { openSidebarArtifact(item); };
                $('artifactSideList').appendChild(button);
            });
        }).catch(function (error) {
            $('artifactSideList').textContent = '资料加载失败：' + error.message;
        });
    };
    if ($('myMaterialsOpen')) $('myMaterialsOpen').onclick = openMyMaterials;
    if ($('myMaterialsClose')) $('myMaterialsClose').onclick = closeMyMaterials;
    if ($('myMaterialsFilters')) $('myMaterialsFilters').onclick = function (event) {
        var category = event.target && event.target.dataset && event.target.dataset.category;
        if (category) loadMyMaterials(category);
    };
    if ($('myMaterialsSearch')) $('myMaterialsSearch').oninput = renderMyMaterials;
    if ($('selfUnderstandingOpen')) $('selfUnderstandingOpen').onclick = openSelfUnderstanding;
    if ($('selfUnderstandingClose')) $('selfUnderstandingClose').onclick = closeSelfUnderstanding;
    if ($('selfUnderstandingList')) $('selfUnderstandingList').onclick = handleUnderstandingClick;
    if ($('openMemories')) $('openMemories').onclick = openSelfUnderstanding;
    if ($('closeMemories')) $('closeMemories').onclick = function () { $('memoryDrawer').hidden = true; };
    if ($('closeArtifact')) $('closeArtifact').onclick = function () { if (window.KakiWorkspaceContextBridge) window.KakiWorkspaceContextBridge.getState(); $('artifactDrawer').hidden = true; };
    if ($('clearReplyContext')) $('clearReplyContext').onclick = clearReplyTarget;
    if ($('send')) $('send').onclick = async function () { var value = $('input').value; await send(value); };
    if ($('retry')) $('retry').onclick = function () { send(lastFailedMessage, true); };
    if ($('stop')) $('stop').onclick = function () { api.abortActiveStream(); };
    var runtimeLlmKey = 'kaki_runtime_llm_config';
    function syncRuntimeLlmFields() {
        var saved = null;
        try { saved = JSON.parse(sessionStorage.getItem(runtimeLlmKey) || 'null'); } catch (ignore) {}
        saved = saved || { provider:'deepseek', model:'deepseek-chat', base_url:'', api_key:'' };
        if ($('runtimeProvider')) $('runtimeProvider').value = saved.provider || 'deepseek';
        if ($('runtimeModel')) $('runtimeModel').value = saved.model || 'deepseek-chat';
        if ($('runtimeBaseUrl')) $('runtimeBaseUrl').value = saved.base_url || '';
        if ($('runtimeApiKey')) $('runtimeApiKey').value = saved.api_key || '';
    }
    function saveRuntimeLlmFields() {
        var value = {
            provider: $('runtimeProvider').value,
            model: ($('runtimeModel').value || 'deepseek-chat').trim(),
            base_url: $('runtimeBaseUrl').value.trim() || null,
            api_key: $('runtimeApiKey').value.trim() || null
        };
        sessionStorage.setItem(runtimeLlmKey, JSON.stringify(value));
        setStatus('本次会话将使用 ' + value.model + '；密钥不写入账号。');
    }
    ['runtimeProvider','runtimeModel','runtimeBaseUrl','runtimeApiKey'].forEach(function (id) {
        if ($(id)) $(id).addEventListener('change', saveRuntimeLlmFields);
    });
    syncRuntimeLlmFields();
    if ($('logout')) $('logout').onclick = function () { Object.keys(key).forEach(function (name) { sessionStorage.removeItem(key[name]); }); location.reload(); };
    loadSidebarState();
    applySidebarState();
    if ($('naturalMemoryForm')) $('naturalMemoryForm').onsubmit = saveNaturalMemory;
    if ($('returnToLatest')) $('returnToLatest').onclick = function () { scrollController.returnToLatest(true); };
    function closeComposerToolMenu() {
        if (!$('composerToolMenu')) return;
        $('composerToolMenu').hidden = true;
        if ($('composerMore')) $('composerMore').setAttribute('aria-expanded', 'false');
    }
    if ($('composerMore')) $('composerMore').onclick = function (event) {
        event.stopPropagation();
        var willOpen = $('composerToolMenu').hidden;
        $('composerToolMenu').hidden = !willOpen;
        $('composerMore').setAttribute('aria-expanded', String(willOpen));
    };
    document.addEventListener('click', function (event) {
        if ($('composerToolMenu') && !$('composerToolMenu').hidden && !$('composerToolMenu').contains(event.target) && event.target !== $('composerMore')) closeComposerToolMenu();
    });
    document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape' && appRoot().classList.contains('materials-open')) closeMyMaterials();
    });
    function navigateLearningTool(path) { closeComposerToolMenu(); window.location.href = path; }
    if ($('toolPractice')) $('toolPractice').onclick = function () { navigateLearningTool('/exam'); };
    if ($('toolLearning')) $('toolLearning').onclick = function () { navigateLearningTool('/learning'); };
    if ($('toolNotes')) $('toolNotes').onclick = function () { navigateLearningTool('/notes'); };
    if ($('toolVideo')) $('toolVideo').onclick = function () { navigateLearningTool('/video'); };
    if ($('toolExplore')) $('toolExplore').onclick = function () {
        closeComposerToolMenu();
        if (window.AIHarvestAdventure && window.AIHarvestAdventure.openNew) window.AIHarvestAdventure.openNew();
        else {
            var adventureEntry = $('adventureEntry');
            if (adventureEntry) adventureEntry.click();
        }
        if (!window.AIHarvestAdventure && !$('adventureEntry')) { $('input').placeholder = '输入一个主题，我会把它变成可互动的学习世界…'; $('input').focus(); }
    };
    if ($('imageTool')) $('imageTool').onclick = function () { closeComposerToolMenu(); $('materialUpload').click(); };
    if ($('libraryTool')) $('libraryTool').onclick = function () { closeComposerToolMenu(); openMaterialLibraryPicker(); };
    if ($('closeMaterialLibrary')) $('closeMaterialLibrary').onclick = function () { $('materialLibraryPicker').hidden = true; };
    if ($('materialUpload')) $('materialUpload').onchange = function (event) {
        uploadWorkspaceFiles(event.target.files);
    };
    if ($('imageUpload')) $('imageUpload').onchange = function (event) { useImageFile(event.target.files && event.target.files[0]); };
    if ($('retryOcr')) $('retryOcr').onclick = recognizeSelectedImage;
    if ($('deleteImage')) $('deleteImage').onclick = clearImageSelection;
    if ($('voiceTool')) $('voiceTool').onclick = beginRecording;
    if ($('cancelRecording')) $('cancelRecording').onclick = function () { finishRecording(true); };
    if ($('stopRecording')) $('stopRecording').onclick = function () { finishRecording(false); };
    if ($('retryTranscription')) $('retryTranscription').onclick = transcribeLastRecording;
    if ($('composerShell')) {
        $('composerShell').addEventListener('dragover', function (event) {
            if (!active && !inputAdapterBusy) {
                event.preventDefault(); event.dataTransfer.dropEffect = 'copy';
                $('composerShell').classList.add('is-dragging');
            }
        });
        $('composerShell').addEventListener('dragleave', function () {
            $('composerShell').classList.remove('is-dragging');
        });
        $('composerShell').addEventListener('drop', function (event) {
            if (active || inputAdapterBusy) return;
            event.preventDefault();
            $('composerShell').classList.remove('is-dragging');
            uploadWorkspaceFiles(event.dataTransfer.files);
        });
    }
    $('messages').addEventListener('scroll', scrollController.userScrolled, { passive: true });
    window.addEventListener('pagehide', function () {
        stopSpeechPlayback();
        releaseVoiceCapture(true);
        if (window.KakiVoiceCallRuntime && window.KakiVoiceCallRuntime.destroy) window.KakiVoiceCallRuntime.destroy();
        if (avatarAdapter) avatarAdapter.destroy();
    });
    window.AIWorkspaceDestroy = function () {
        active = false;
        if (runtimePollTimer) window.clearInterval(runtimePollTimer);
        if (runtimeIdleTimer) window.clearTimeout(runtimeIdleTimer);
        if (recordingTimer) window.clearInterval(recordingTimer);
        runtimePollTimer = null;
        runtimeIdleTimer = null;
        recordingTimer = null;
        try { api.abortActiveStream(); } catch (ignore) {}
        stopSpeechPlayback();
        releaseVoiceCapture(true);
        if (window.KakiVoiceCallRuntime && window.KakiVoiceCallRuntime.destroy) window.KakiVoiceCallRuntime.destroy();
        if (avatarAdapter) avatarAdapter.destroy();
        if (window.KakiWorkspaceContextBridge) window.KakiWorkspaceContextBridge.destroy();
        window.removeEventListener('kaki:impression-changed', refreshTutorAfterImpressionChange);
    };
    document.addEventListener('visibilitychange', function () {
        if (document.hidden) releaseVoiceCapture(true);
    });
    $('input').addEventListener('input', resizeComposer);
    $('input').onkeydown = function (event) {
        if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); $('send').click(); }
    };
    resizeComposer();
    syncReturnToLatestOffset();
    if (window.ResizeObserver && $('composerShell')) {
        var composerResizeObserver = new ResizeObserver(syncReturnToLatestOffset);
        composerResizeObserver.observe($('composerShell').parentElement);
    }
    renderRuntimeStatus();
    if (embedded) {
        var formalToken = localStorage.getItem('auth_token') || localStorage.getItem('ml_auth_token') || '';
        var formalUserId = localStorage.getItem('user_id') || '';
        if (!formalUserId) {
            try {
                var formalUser = JSON.parse(localStorage.getItem('ml_user') || '{}');
                formalUserId = formalUser.id || formalUser.user_id || '';
            } catch (ignore) {}
        }
        if (formalToken) sessionStorage.setItem(key.token, formalToken);
        if (formalUserId) sessionStorage.setItem(key.userId, formalUserId);
    }
    if (sessionStorage.getItem(key.token)) {
        if ($('login')) $('login').hidden = true;
        appRoot().hidden = false;
        api.threadId = currentThread();
        if (currentThread()) {
            Promise.all([loadHistory(), loadWorkspaceMaterials()]).then(function () { syncRuntimeStatus(); return applyPendingTutorEntry(); }).then(applyPendingVideoIntent).catch(showHistoryError);
        }
        else createConversation().then(applyPendingTutorEntry).then(applyPendingVideoIntent).catch(showHistoryError);
        probeInputCapabilities();
    }
}());
