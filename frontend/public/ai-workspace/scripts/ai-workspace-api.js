(function () {
    'use strict';

    var TOKEN_KEY = 'ai_workspace_test_token';
    var USER_KEY = 'ai_workspace_test_user_id';
    var THREAD_KEY = 'ai_workspace_test_thread_id';
    var RUNTIME_LLM_KEY = 'kaki_runtime_llm_config';
    var activeController = null;

    function apiBase() {
        return window.ML_CONFIG && window.ML_CONFIG.API_BASE_URL
            ? window.ML_CONFIG.API_BASE_URL
            : 'http://127.0.0.1:8765';
    }

    function token() {
        return sessionStorage.getItem(TOKEN_KEY)
            || localStorage.getItem('auth_token')
            || localStorage.getItem('ml_auth_token') || '';
    }
    function userId() {
        var value = sessionStorage.getItem(USER_KEY);
        if (value) return Number(value);
        var stored = localStorage.getItem('ml_current_user_id') || localStorage.getItem('user_id');
        if (stored) return Number(stored);
        try {
            var user = JSON.parse(localStorage.getItem('ml_user') || '{}');
            return Number(user.id || user.user_id) || null;
        } catch (ignore) { return null; }
    }
    function threadId() { return api.threadId || sessionStorage.getItem(THREAD_KEY) || ''; }
    function runtimeLlmConfig() {
        try {
            var value = JSON.parse(sessionStorage.getItem(RUNTIME_LLM_KEY) || 'null');
            return value && value.model ? value : null;
        } catch (ignore) { return null; }
    }

    function errorFrom(response, data, stage) {
        var envelope = data && data.detail;
        var detail = envelope && typeof envelope === 'object'
            ? (envelope.message || envelope.detail || envelope.error)
            : (data && (data.detail || data.message || data.error));
        var error = new Error(typeof detail === 'string' ? detail : ('HTTP ' + response.status));
        error.status = response.status;
        error.code = (envelope && typeof envelope === 'object' && envelope.code)
            || (data && data.code) || 'http_' + response.status;
        error.requestId = response.headers.get('x-request-id') || (data && data.request_id) || '';
        error.stage = stage || 'http';
        return error;
    }

    async function request(path, options) {
        options = options || {};
        var headers = Object.assign({ 'Content-Type': 'application/json' }, options.headers || {});
        if (token()) headers.Authorization = 'Bearer ' + token();
        var response;
        try {
            response = await fetch(apiBase() + path, Object.assign({}, options, { headers: headers }));
        } catch (cause) {
            var networkError = new Error('无法连接后端服务。');
            networkError.stage = 'connect';
            networkError.code = 'network_error';
            throw networkError;
        }
        var data = null;
        try { data = await response.json(); } catch (ignore) {}
        if (!response.ok) throw errorFrom(response, data, 'http');
        return data;
    }

    async function uploadRequest(path, file, fileName) {
        var form = new FormData();
        form.append('file', file, fileName || file.name || 'upload.bin');
        var headers = {};
        if (token()) headers.Authorization = 'Bearer ' + token();
        var response;
        try {
            response = await fetch(apiBase() + path, { method: 'POST', headers: headers, body: form });
        } catch (cause) {
            var networkError = new Error('无法连接后端服务。');
            networkError.stage = 'connect';
            networkError.code = 'network_error';
            throw networkError;
        }
        var data = null;
        try { data = await response.json(); } catch (ignore) {}
        if (!response.ok) throw errorFrom(response, data, 'input_adapter');
        return data;
    }

    async function materialUploadRequest(file) {
        var form = new FormData();
        form.append('thread_id', threadId());
        form.append('file', file, file.name || 'workspace-material.bin');
        var headers = {};
        if (token()) headers.Authorization = 'Bearer ' + token();
        var response;
        try {
            response = await fetch(apiBase() + '/api/chat/materials', {
                method: 'POST', headers: headers, body: form
            });
        } catch (cause) {
            var networkError = new Error('无法连接后端服务。');
            networkError.stage = 'connect';
            networkError.code = 'network_error';
            throw networkError;
        }
        var data = null;
        try { data = await response.json(); } catch (ignore) {}
        if (!response.ok) throw errorFrom(response, data, 'material_upload');
        return data;
    }

    function jsonBody(method, body) {
        return { method: method, body: JSON.stringify(body || {}) };
    }

    function dispatchEvent(data, callbacks) {
        switch (data.type) {
            case 'context':
                if (callbacks.onContext) callbacks.onContext(data);
                break;
            case 'agent_start':
                if (callbacks.onAgentStart) callbacks.onAgentStart(data.agent, data);
                break;
            case 'agent_content':
                if (callbacks.onContent) callbacks.onContent(data.agent, data.resource_type, data.preview || '', data);
                break;
            case 'agent_complete':
                if (callbacks.onAgentComplete) callbacks.onAgentComplete(data.agent, data.status, data);
                break;
            case 'conversation_action':
                if (callbacks.onConversationAction) callbacks.onConversationAction(data.action, data);
                break;
            case 'memory_candidate':
                if (callbacks.onMemoryCandidate) callbacks.onMemoryCandidate(data.candidate);
                break;
            case 'memory_saved':
                if (callbacks.onMemorySaved) callbacks.onMemorySaved(data);
                break;
            case 'error':
                if (callbacks.onError) callbacks.onError(data);
                return 'error';
            case 'done':
                if (callbacks.onDone) callbacks.onDone(data);
                return 'done';
        }
        return '';
    }

    async function streamRequest(path, body, callbacks) {
        if (activeController) activeController.abort();
        var controller = new AbortController();
        activeController = controller;
        var response;
        try {
            response = await fetch(apiBase() + path, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': 'Bearer ' + token()
                },
                body: JSON.stringify(body),
                signal: controller.signal
            });
        } catch (cause) {
            if (controller.signal.aborted) {
                var aborted = new Error('已停止生成。');
                aborted.name = 'AbortError';
                aborted.stage = 'stream_interrupted';
                throw aborted;
            }
            var connectError = new Error('无法建立流式连接。');
            connectError.stage = 'connect';
            connectError.code = 'network_error';
            throw connectError;
        }

        if (!response.ok || !response.body) {
            var data = null;
            try { data = await response.json(); } catch (ignore) {}
            throw errorFrom(response, data, 'http');
        }

        if (callbacks.onOpen) callbacks.onOpen({
            backend: 'connected',
            auth: 'valid',
            provider: response.headers.get('x-ai-provider') || 'unknown',
            operation: response.headers.get('x-ai-operation') || 'chat_connecting',
            state: 'running'
        });

        var reader = response.body.getReader();
        var decoder = new TextDecoder('utf-8');
        var buffer = '';
        var terminal = '';
        try {
            while (!terminal) {
                var part = await reader.read();
                buffer += decoder.decode(part.value || new Uint8Array(), { stream: !part.done });
                var boundary;
                while ((boundary = buffer.indexOf('\n\n')) >= 0) {
                    var block = buffer.slice(0, boundary);
                    buffer = buffer.slice(boundary + 2);
                    var payload = block.split(/\r?\n/).filter(function (line) {
                        return line.indexOf('data:') === 0;
                    }).map(function (line) {
                        return line.slice(5).trimStart();
                    }).join('\n');
                    if (!payload) continue;
                    try { terminal = dispatchEvent(JSON.parse(payload), callbacks) || terminal; }
                    catch (parseError) {
                        if (callbacks.onError) callbacks.onError({
                            code: 'invalid_sse_json', stage: 'json_parse', message: '流数据解析失败。'
                        });
                        terminal = 'error';
                    }
                    if (terminal) break;
                }
                if (part.done) break;
            }
            if (!terminal && callbacks.onError) {
                callbacks.onError({ code: 'stream_ended_early', stage: 'stream_interrupted', message: '流式连接提前结束。' });
            }
        } catch (cause) {
            if (controller.signal.aborted || (cause && cause.name === 'AbortError')) {
                var interrupted = new Error('已停止生成。');
                interrupted.name = 'AbortError';
                interrupted.stage = 'stream_interrupted';
                throw interrupted;
            }
            if (callbacks.onError) callbacks.onError({ code: 'stream_read_error', stage: 'sse_read', message: '读取流式回复失败。' });
        } finally {
            if (activeController === controller) activeController = null;
            try { reader.releaseLock(); } catch (ignore) {}
        }
    }

    var api = {
        baseUrl: apiBase(),
        threadId: sessionStorage.getItem(THREAD_KEY) || '',
        currentUserId: userId,
        login: function (username, password) {
            return request('/api/auth/login', jsonBody('POST', { username: username, password: password }));
        },
        createConversation: function () {
            return request('/api/chat/conversations', jsonBody('POST', {}));
        },
        setConversationMode: function (mode) {
            return request('/api/chat/conversations/' + encodeURIComponent(threadId()) + '/mode', jsonBody('PATCH', { mode: mode }));
        },
        startTutorSession: function (goal, durationDays, dailyMinutes) {
            return request('/api/chat/tutor/sessions', jsonBody('POST', {
                thread_id: threadId(), goal: goal,
                duration_days: durationDays, daily_minutes: dailyMinutes
            }));
        },
        deriveArtifactAction: function (artifact_id, action) {
            return request('/api/chat/tasks/artifacts/' + encodeURIComponent(artifact_id) + '/actions/derive', jsonBody('POST', {
                thread_id: threadId(), action: action
            }));
        },
        getCurrentTutorSession: function () {
            return request('/api/chat/tutor/sessions/current?thread_id=' + encodeURIComponent(threadId()));
        },
        getResumableTutorSession: function () {
            return request('/api/chat/tutor/sessions/resumable');
        },
        pauseTutorSession: function (sessionId) {
            return request('/api/chat/tutor/sessions/' + encodeURIComponent(sessionId) + '/pause', jsonBody('POST', { message:'暂停学习' }));
        },
        resumeTutorSession: function (sessionId) {
            return request('/api/chat/tutor/sessions/' + encodeURIComponent(sessionId) + '/resume', jsonBody('POST', { message:'恢复学习' }));
        },
        saveTutorEvent: function (sessionId, eventType, payload) {
            return request('/api/chat/tutor/sessions/' + encodeURIComponent(sessionId) + '/events', jsonBody('POST', {
                event_type: eventType, payload: payload || {}
            }));
        },
        listWorkspaceArtifacts: function (scope, artifactType, tutorSessionId) {
            // scope=all 表示当前用户全部学习资产，不带 thread 过滤；conversation/tutor 才按会话过滤
            var effectiveScope = scope || 'conversation';
            var query = '&scope=' + encodeURIComponent(effectiveScope)
                + '&artifact_type=' + encodeURIComponent(artifactType || 'all');
            if (effectiveScope !== 'all') query = '?thread_id=' + encodeURIComponent(threadId()) + query;
            else query = '?' + query.slice(1);
            if (tutorSessionId) query += '&tutor_session_id=' + encodeURIComponent(tutorSessionId);
            return request('/api/chat/artifacts' + query);
        },
        getHealth: function () {
            return request('/api/health');
        },
        getRuntimeStatus: function () {
            return request('/api/chat/tasks/runtime-status/' + encodeURIComponent(threadId()));
        },
        getWorkspaceTaskState: function () {
            return request('/api/chat/tasks/state/' + encodeURIComponent(threadId()));
        },
        getChatHistory: function (id) {
            return request('/api/chat/history/' + encodeURIComponent(id));
        },
        recognizeWorkspaceImage: function (file) {
            return uploadRequest('/api/chat/input/ocr', file, file.name || 'workspace-image.png');
        },
        recognizeBlackboardHandwriting: function (blob, fileName) {
            // 黑板手写识别：canvas 截图 → OCR → 返回识别文本
            return uploadRequest('/api/chat/input/blackboard-recognize', blob, fileName || 'blackboard-writing.png');
        },
        getWorkspaceInputCapabilities: function () {
            return request('/api/chat/input/capabilities');
        },
        transcribeWorkspaceAudio: function (blob, fileName) {
            return uploadRequest('/api/chat/input/transcribe', blob, fileName || 'workspace-recording.webm');
        },
        uploadWorkspaceMaterial: materialUploadRequest,
        listWorkspaceMaterials: function () {
            return request('/api/chat/materials?thread_id=' + encodeURIComponent(threadId()));
        },
        listWorkspaceMaterialLibrary: function () {
            return request('/api/chat/materials/library');
        },
        searchWorkspaceSources: function (task) {
            return request('/api/chat/searches', jsonBody('POST', {
                thread_id: threadId(),
                query: task.query,
                source_scope: task.source_scope || 'official_first',
                limit: task.limit || 6,
                task_id: task.task_id || null,
                task_version: task.task_version || task.version || null
            }));
        },
        getWorkspaceSearch: function (searchId) {
            return request('/api/chat/searches/' + encodeURIComponent(searchId));
        },
        importWorkspaceSearchResult: function (searchId, resultId) {
            return request('/api/chat/searches/' + encodeURIComponent(searchId) + '/import', jsonBody('POST', {
                thread_id: threadId(), result_id: resultId
            }));
        },
        detachWorkspaceMaterial: function (materialId) {
            return request('/api/chat/materials/' + encodeURIComponent(materialId)
                + '/bindings/' + encodeURIComponent(threadId()), { method: 'DELETE' });
        },
        attachWorkspaceMaterial: function (materialId) {
            return request('/api/chat/materials/' + encodeURIComponent(materialId) + '/bindings', jsonBody('POST', {
                thread_id: threadId()
            }));
        },
        getMemories: function () { return request('/api/chat/memories'); },
        saveNaturalMemory: function (text, sourceThreadId, sourceMessageId) {
            return request('/api/chat/memories/from-text', jsonBody('POST', {
                text: text,
                source_thread_id: sourceThreadId,
                source_message_id: sourceMessageId
            }));
        },
        saveMemory: function (key, payload) {
            return request('/api/chat/memories/' + encodeURIComponent(key), jsonBody('PUT', payload));
        },
        deleteMemory: function (id) {
            return request('/api/chat/memories/' + encodeURIComponent(id), { method: 'DELETE' });
        },
        getImpressions: function () { return request('/api/chat/impressions'); },
        updateImpression: function (id, action, summary) {
            var payload = { action: action };
            if (summary) payload.summary = summary;
            return request('/api/chat/impressions/' + encodeURIComponent(id), jsonBody('PATCH', payload));
        },
        deleteImpression: function (id) {
            return request('/api/chat/impressions/' + encodeURIComponent(id), { method: 'DELETE' });
        },
        getSelfUnderstanding: function () {
            return request('/api/darya/self-understanding');
        },
        updateUnderstandingMemory: function (id, action, expectedRevision, correction) {
            return request('/api/darya/self-understanding/memories/' + encodeURIComponent(id), jsonBody('PATCH', {
                action: action,
                expected_revision: expectedRevision,
                correction: correction || null
            }));
        },
        getExam: function (id) { return request('/api/exam/' + encodeURIComponent(id)); },
        routeWorkspaceMessage: function (message, activeArtifact) {
            return request('/api/chat/tasks/route', jsonBody('POST', {
                thread_id: threadId(), message: message, active_artifact: activeArtifact || null
            }));
        },
        planWorkspaceTask: function (message, activeArtifact, sourceContext, inputContext) {
            inputContext = inputContext || {};
            var artifactId = Number(inputContext.target_artifact_id);
            var versionId = Number(inputContext.target_version_id);
            return request('/api/chat/tasks/plan', jsonBody('POST', {
                thread_id: threadId(), message: message, active_artifact: activeArtifact || null,
                source_context: sourceContext || null,
                input_context_mode: inputContext.input_context_mode || inputContext.mode || null,
                target_artifact_id: Number.isFinite(artifactId) && artifactId >= 1 ? artifactId : null,
                target_version_id: Number.isFinite(versionId) && versionId >= 1 ? versionId : null
            }));
        },
        getConversationCapabilityAction: function (actionKey) {
            return request('/api/chat/capability-actions/' + encodeURIComponent(actionKey));
        },
        prepareConversationCapabilityAction: function (actionKey) {
            return request('/api/chat/capability-actions/' + encodeURIComponent(actionKey) + '/prepare', jsonBody('POST', {}));
        },
        reviewConversationCapabilityAction: function (actionKey) {
            return request('/api/chat/capability-actions/' + encodeURIComponent(actionKey) + '/review', jsonBody('POST', {}));
        },
        controlWorkspaceTask: function (task, action) {
            return request('/api/chat/tasks/control', jsonBody('POST', {
                thread_id: threadId(),
                task_id: task.task_id,
                task_version: task.task_version || task.version,
                action: action
            }));
        },
        runWorkspaceExam: function (task) {
            return request('/api/chat/tasks/exam', jsonBody('POST', {
                thread_id: threadId(), topic: task.topic, count: task.question_count,
                difficulty: task.difficulty || '中等', confirmed: true,
                question_types: task.question_types,
                instruction: task.instruction || '',
                source_artifact_id: task.source_artifact_id || null,
                source_material_ids: task.source_material_ids || [],
                task_id: task.task_id,
                task_version: task.task_version
            }));
        },
        examFromWorkspaceLecture: function (artifactId, options) {
            options = options || {};
            return request('/api/chat/tasks/artifacts/' + encodeURIComponent(artifactId) + '/actions/exam', jsonBody('POST', {
                thread_id: threadId(), count: options.count || 3,
                difficulty: options.difficulty || 'intermediate',
                question_types: options.question_types || ['single_choice']
            }));
        },
        prepareTutorNextAction: function (sessionId, message, context) {
            context = context || {};
            return request('/api/chat/tutor/sessions/' + encodeURIComponent(sessionId) + '/next-action',
                jsonBody('POST', {
                    message: message,
                    active_artifact_id: context.active_artifact_id || null,
                    material_ids: context.material_ids || []
                }));
        },
        listProactiveTutorMessages: function () {
            return request('/api/chat/tutor/proactive/messages');
        },
        markProactiveConversationRead: function (conversationId) {
            return request('/api/chat/tutor/proactive/read', jsonBody('POST', {
                conversation_id: conversationId
            }));
        },
        executeProactiveTutorAction: function (messageId) {
            return request('/api/chat/tutor/proactive/messages/' + encodeURIComponent(messageId) + '/action',
                jsonBody('POST', {}));
        },
        dismissProactiveTutorMessage: function (messageId) {
            return request('/api/chat/tutor/proactive/messages/' + encodeURIComponent(messageId) + '/dismiss',
                jsonBody('POST', {}));
        },
        runWorkspaceLecture: function (task) {
            return request('/api/chat/tasks/lecture', jsonBody('POST', {
                thread_id: threadId(), topic: task.topic, difficulty: task.difficulty || '中等', confirmed: true,
                export_formats: task.export_formats || ['pdf', 'docx'],
                instruction: task.instruction || '',
                detail_level: task.detail_level,
                source_artifact_id: task.source_artifact_id || null,
                source_artifact_kind: task.source_artifact_kind || null,
                source_material_ids: task.source_material_ids || [],
                task_id: task.task_id,
                task_version: task.task_version
            }));
        },
        runWorkspaceLearningGraph: function (task) {
            return request('/api/chat/tasks/learning-graph', jsonBody('POST', {
                thread_id: threadId(),
                task_id: task.task_id,
                task_version: task.task_version
            }));
        },
        runWorkspaceCode: function (task) {
            return request('/api/chat/tasks/code', jsonBody('POST', {
                thread_id: threadId(),
                topic: task.topic,
                language: task.language || 'python',
                confirmed: true,
                instruction: task.instruction || '',
                should_run: task.should_run !== false,
                input_output_requirement: task.input_output_requirement || '',
                task_id: task.task_id,
                task_version: task.task_version
            }));
        },
        runWorkspacePresentation: function (task) {
            return request('/api/chat/tasks/presentation', jsonBody('POST', {
                thread_id: threadId(),
                topic: task.topic,
                delivery_mode: task.delivery_mode,
                learning_goal: task.learning_goal || '',
                level: task.level || '',
                duration_minutes: task.duration_minutes || null,
                slide_count: task.slide_count || null,
                presentation_style: task.presentation_style || '',
                source_artifact_id: task.source_artifact_id || null,
                confirmed: true,
                task_id: task.task_id,
                task_version: task.task_version
            }));
        },
        runWorkspaceVideo: function (task) {
            return request('/api/chat/tasks/video', jsonBody('POST', {
                thread_id: threadId(),
                topic: task.topic,
                duration_sec: task.duration_sec || 20,
                instruction: task.instruction || '',
                source_material_ids: task.source_material_ids || [],
                confirmed: true,
                task_id: task.task_id,
                task_version: task.task_version
            }));
        },
        getWorkspaceVideo: function (id) {
            return request('/api/chat/tasks/videos/' + encodeURIComponent(id));
        },
        getWorkspaceVideoStatus: function (id) {
            return request('/api/chat/tasks/videos/' + encodeURIComponent(id) + '/status');
        },
        retryWorkspaceVideo: function (id) {
            return request('/api/chat/tasks/videos/' + encodeURIComponent(id) + '/retry', jsonBody('POST', {}));
        },
        getWorkspaceVideoObjectUrl: async function (id) {
            var response = await fetch(apiBase() + '/api/chat/tasks/videos/' + encodeURIComponent(id) + '/media', {
                headers: { 'Authorization': 'Bearer ' + token() }
            });
            if (!response.ok) {
                var data = null;
                try { data = await response.json(); } catch (ignore) {}
                throw errorFrom(response, data, 'video_media');
            }
            return URL.createObjectURL(await response.blob());
        },
        runWorkspaceMindmap: function (task) {
            return request('/api/chat/tasks/mindmap', jsonBody('POST', {
                thread_id: threadId(),
                topic: task.topic || '',
                source_material_ids: task.source_material_ids || [],
                source_scope: task.source_scope || '',
                instruction: task.instruction || '',
                confirmed: true,
                task_id: task.task_id,
                task_version: task.task_version
            }));
        },
        getWorkspaceMindmap: function (id) {
            return request('/api/chat/tasks/mindmaps/' + encodeURIComponent(id));
        },
        reviseWorkspaceMindmap: function (id, instruction) {
            return request('/api/chat/tasks/mindmaps/' + encodeURIComponent(id) + '/revise', jsonBody('POST', {
                thread_id: threadId(), instruction: instruction
            }));
        },
        getWorkspacePresentation: function (id) {
            return request('/api/chat/tasks/presentations/' + encodeURIComponent(id));
        },
        reviseWorkspacePresentation: function (id, instruction) {
            return request('/api/chat/tasks/presentations/' + encodeURIComponent(id) + '/revise', jsonBody('POST', {
                thread_id: threadId(), instruction: instruction
            }));
        },
        saveInteractiveLessonEvent: function (id, eventType, payload) {
            return request('/api/chat/tasks/presentations/' + encodeURIComponent(id) + '/events', jsonBody('POST', {
                thread_id: threadId(), event_type: eventType, payload: payload || {}
            }));
        },
        submitInteractiveLessonAnswer: function (id, versionId, itemIdentity, selectedAnswer, submissionRequestId, idempotencyKey) {
            return request('/api/chat/tasks/presentations/' + encodeURIComponent(id) + '/attempts', jsonBody('POST', {
                thread_id: threadId(),
                artifact_version_id: versionId,
                item_identity: itemIdentity,
                selected_answer: selectedAnswer,
                submission_request_id: submissionRequestId,
                idempotency_key: idempotencyKey
            }));
        },
        exportWorkspacePresentation: async function (id) {
            var response = await fetch(apiBase() + '/api/chat/tasks/presentations/' + encodeURIComponent(id) + '/export/pptx', {
                headers: { 'Authorization': 'Bearer ' + token() }
            });
            if (!response.ok) {
                var data = null;
                try { data = await response.json(); } catch (ignore) {}
                throw errorFrom(response, data, 'presentation_export');
            }
            var blob = await response.blob();
            return {
                blob: blob,
                disposition: response.headers.get('content-disposition') || '',
                contentType: response.headers.get('content-type') || '',
                byteLength: blob.size
            };
        },
        generateLessonSpeech: async function (text) {
            var response = await fetch(apiBase() + '/api/video/speech', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token() },
                body: JSON.stringify({ text: text })
            });
            if (!response.ok) {
                var data = null;
                try { data = await response.json(); } catch (ignore) {}
                throw errorFrom(response, data, 'lesson_speech');
            }
            return response.blob();
        },
        getWorkspaceCode: function (id) {
            return request('/api/chat/tasks/code/' + encodeURIComponent(id));
        },
        reviseWorkspaceCode: function (id, instruction) {
            return request('/api/chat/tasks/code/' + encodeURIComponent(id) + '/revise', jsonBody('POST', {
                thread_id: threadId(), instruction: instruction
            }));
        },
        rerunWorkspaceCode: function (id) {
            return request('/api/chat/tasks/code/' + encodeURIComponent(id) + '/rerun', jsonBody('POST', {
                thread_id: threadId()
            }));
        },
        explainWorkspaceCode: function (id) {
            return request('/api/chat/tasks/code/' + encodeURIComponent(id) + '/explain', jsonBody('POST', {
                thread_id: threadId()
            }));
        },
        examFromWorkspaceCode: function (id, options) {
            options = options || {};
            return request('/api/chat/tasks/code/' + encodeURIComponent(id) + '/exam', jsonBody('POST', {
                thread_id: threadId(), count: options.count || 3,
                difficulty: options.difficulty || '中等',
                question_types: options.question_types || []
            }));
        },
        getWorkspaceArtifact: function (id) {
            return request('/api/chat/tasks/artifacts/' + encodeURIComponent(id));
        },
        getFormalLearningGraph: function (graphId) {
            return request('/api/learning-graphs/formal/' + encodeURIComponent(graphId));
        },
        getCurrentHarvestAdventure: function () {
            return request('/api/adventure/current');
        },
        startHarvestAdventure: function (topic, magicSchool, sourceIds, context) {
            context = context || {};
            return request('/api/adventure/start', jsonBody('POST', {
                topic: topic,
                magic_school: magicSchool,
                source_ids: sourceIds || [],
                thread_id: threadId() || null,
                source_type: context.source_type || null,
                force_new: Boolean(context.force_new || false),
                prepared_plan_id: context.prepared_plan_id || null,
                identity_id: context.identity_id || null,
                talent_id: context.talent_id || null
            }));
        },
        advanceHarvestAdventure: function (runId, action, payload) {
            return request('/api/adventure/advance', jsonBody('POST', {
                run_id: runId,
                action: action,
                payload: payload || {}
            }));
        },
        getHarvestAdventureRewards: function () {
            return request('/api/adventure/rewards');
        },
        equipHarvestAdventureReward: function (artifactId, replaceConfirmed) {
            return request('/api/adventure/rewards/equip', jsonBody('POST', {
                artifact_id: artifactId,
                replace_confirmed: Boolean(replaceConfirmed)
            }));
        },
        unequipHarvestAdventureReward: function (artifactId) {
            return request('/api/adventure/rewards/unequip', jsonBody('POST', {
                artifact_id: artifactId
            }));
        },
        prepareHarvestAdventure: function (topicHint, explicitConfusion, sourceIds) {
            return request('/api/adventure/prepare', jsonBody('POST', {
                topic_hint: topicHint,
                explicit_confusion: explicitConfusion || null,
                thread_id: threadId() || null,
                source_ids: sourceIds || []
            }));
        },
        getCurrentPreparedPlan: function () {
            return request('/api/adventure/prepare/current');
        },
        adjustPreparedAdventure: function (planId, focusAdjustment) {
            return request('/api/adventure/prepare/adjust', jsonBody('POST', {
                plan_id: planId,
                focus_adjustment: focusAdjustment
            }));
        },
        reviseWorkspaceArtifact: function (id, instruction) {
            return request('/api/chat/tasks/artifacts/' + encodeURIComponent(id) + '/revise', jsonBody('POST', {
                thread_id: threadId(), instruction: instruction
            }));
        },
        exportWorkspaceArtifact: async function (id, formatName, callbacks) {
            callbacks = callbacks || {};
            var response = await fetch(apiBase() + '/api/chat/tasks/artifacts/' + encodeURIComponent(id)
                + '/export/' + encodeURIComponent(formatName), {
                headers: { 'Authorization': 'Bearer ' + token() }
            });
            if (!response.ok) {
                var data = null;
                try { data = await response.json(); } catch (ignore) {}
                throw errorFrom(response, data, 'artifact_export');
            }
            var disposition = response.headers.get('content-disposition') || '';
            if (callbacks.onHeaders) callbacks.onHeaders({
                backend: 'connected', auth: 'valid',
                provider: response.headers.get('x-ai-provider') || 'unknown',
                operation: response.headers.get('x-ai-operation') || ('export_' + formatName),
                state: response.headers.get('x-ai-state') || 'running'
            });
            var blob = await response.blob();
            var utf8Name = disposition.match(/filename\*=UTF-8''([^;]+)/i);
            var fileName = '';
            if (utf8Name) {
                try { fileName = decodeURIComponent(utf8Name[1]); } catch (ignore) { fileName = utf8Name[1]; }
            }
            return {
                blob: blob,
                status: response.status,
                contentType: response.headers.get('content-type') || '',
                disposition: disposition,
                artifactId: response.headers.get('x-artifact-id') || String(id),
                byteLength: blob.size,
                fileName: fileName
            };
        },
        submitWorkspaceExam: function (id, answers, timeSpentSec) {
            return request('/api/chat/tasks/exam/' + encodeURIComponent(id) + '/submit', jsonBody('POST', {
                thread_id: threadId(), answers: answers, time_spent_sec: timeSpentSec || 0
            }));
        },
        chatStream: function (message, history, callbacks, options) {
            options = options || {};
            return streamRequest('/api/chat/stream', {
                message: message,
                history: (history || []).map(function (item) { return { role: item.role, content: item.content }; }),
                thread_id: threadId(),
                user_id: userId(),
                retry: Boolean(options.retry),
                message_id: options.message_id || null,
                style: options.style || null,
                question_context: options.question_context || null,
                source_context: options.source_context || null,
                attachment_refs: options.attachment_refs || [],
                reply_to_message_id: options.reply_to_message_id || null,
                target_artifact_id: options.target_artifact_id || null,
                target_version_id: options.target_version_id || null,
                selection_context: options.selection_context || null,
                runtime_llm_config: runtimeLlmConfig()
            }, callbacks || {});
        },
        abortActiveStream: function () {
            if (activeController) activeController.abort();
        }
    };

    window.AIWorkspaceApiClient = api;
    window.apiClient = api;
}());
