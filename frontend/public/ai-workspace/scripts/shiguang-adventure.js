(function () {
    'use strict';

    var api = window.apiClient;
    var entry = document.getElementById('adventureEntry');
    var workspace = document.getElementById('workspaceApp') || document.getElementById('app');
    var messages = document.getElementById('messages');
    var input = document.getElementById('input');
    if (!api || !entry || !workspace) return;

    var ASSET = '/ai-workspace/assets/adventure/';
    var overlay = null;
    var snapshot = null;
    var busy = false;
    var chatState = null;
    var active = false;
    var director = null;
    var opener = null;
    var registry = window.KakiAdventureSceneRegistry;
    var spellRegistry = window.KakiAdventureSpells;
    var badgeRegistry = window.KakiAdventureBadges;
    var spellDirector = null;
    var enemyDirector = null;
    var schoolConfirmed = false;
    var routeState = null;
    var rewardCollection = { catalog_count: 1, owned_count: 0, equip_slot: 'journey_seal', equipped: null, items: [] };
    var rewardDetailId = 0;
    var rewardComparison = null;
    var rewardError = '';
    var panelMode = 'learning';
    var prepareStep = 1;
    var prepareDraft = { topic: '', source: 'manual', identity: 'wandering_mage' };
    var forceNewOnStart = false;
    // 分页叙事：卷轴教学卡当前页索引（数字页码；'done' 表示正文已读完，进入确认题）
    var scrollPageIndex = 0;
    var NODES = ['SCROLL', 'TRIAL', 'INTERLEAVE', 'BOSS', 'TREASURY'];
    var ACTS = [
        { id: 1, name: '教', goal: '拾起卷轴，看懂规则', nodes: ['SCROLL'], climax: '卷轴展开，知识残光在手中亮起' },
        { id: 2, name: '练', goal: '选择出招，两战连击', nodes: ['TRIAL', 'INTERLEAVE'], climax: '墨迹兽退散，证据在陌生情境里也站得住' },
        { id: 3, name: '战', goal: '三道封印，终局守关', nodes: ['BOSS'], climax: '墨魇之主散去，迷雾彻底退开' },
        { id: 4, name: '收', goal: '证据回顾，收入宝库', nodes: ['TREASURY'], climax: '丰穗宝库的灯火逐一亮起，旅印入册' },
    ];
    function actForState(state) {
        for (var i = 0; i < ACTS.length; i++) {
            if (ACTS[i].nodes.indexOf(state) >= 0) return ACTS[i];
        }
        return ACTS[0];
    }
    var MASCOTS = {
        SCROLL: 'observe', TRIAL: 'encourage', INTERLEAVE: 'observe',
        BOSS: 'encourage', TREASURY: 'celebrate', completed: 'celebrate'
    };
    var METHODS = [
        { id: 'explain', route: 'understanding', icon: '讲', title: '讲给别人听', text: '用自己的语言重组概念，暴露理解空白。' },
        { id: 'recall', route: 'understanding', icon: '忆', title: '闭卷回忆', text: '先不看答案，主动提取关键线索。' },
        { id: 'practice', route: 'practice', icon: '练', title: '动手练习', text: '从判断或操作任务开始，用结果校准理解。' },
        { id: 'diagram', route: 'understanding', icon: '图', title: '画图梳理', text: '把关系画成结构，再用语言解释连接。' }
    ];
    var IDENTITIES = [
        { id: 'wandering_mage', name: '云游符咒师', style: '惯于把知识讲给同伴听，从复述中照见缺口' },
        { id: 'ink_apprentice', name: '墨坛见习法师', style: '习惯先闭卷回忆，再对照卷轴补上遗漏' },
        { id: 'lamp_heir', name: '守灯人后裔', style: '擅长把方法做成可操作的机关，靠结果校准理解' },
        { id: 'herbal_mage', name: '山野药术师', style: '偏好用例子对照，找出条件差异带来的变化' },
        { id: 'librarian', name: '藏书塔学徒', style: '习惯先提取规则骨架，再逐层补充细节' },
        { id: 'star_apprentice', name: '星象台学徒', style: '偏爱把概念放到陌生情境里检验迁移' },
    ];
    var IDENTITY_PROFILES = {
        wandering_mage: { style: '惯于把知识讲给同伴听，从复述中照见缺口' },
        ink_apprentice: { style: '习惯先闭卷回忆，再对照卷轴补上遗漏' },
        lamp_heir: { style: '擅长把方法做成可操作的机关，靠结果校准理解' },
        herbal_mage: { style: '偏好用例子对照，找出条件差异带来的变化' },
        librarian: { style: '习惯先提取规则骨架，再逐层补充细节' },
        star_apprentice: { style: '偏爱把概念放到陌生情境里检验迁移' },
    };
    var ROUTE_STORAGE_PREFIX = 'kaki_adventure_route_v1:';

    function sceneDefinition(sceneId) {
        return registry && registry.resolve ? registry.resolve(sceneId || 'camp') : {
            id: 'camp',
            name: '微光营地',
            background: 'scenes/scene_camp_prepare.png',
            fallbackBackground: '',
            playerAnchor: { x: 31, y: 77 },
            companionAnchor: { x: 68, y: 71 },
            enemyAnchor: { x: 80, y: 66 },
            entryDirection: 'left',
            exitDirection: 'right',
            availableStates: ['normal'],
            transitionStyle: 'lantern-fade',
            musicKey: 'prepare',
            ambientKey: 'camp-night',
            isFallback: false
        };
    }

    function badgeDefinition(id) {
        if (badgeRegistry && badgeRegistry.resolve) return badgeRegistry.resolve(id || 'adventure_summary');
        return {
            id: 'adventure_summary',
            displayName: '拾光有成',
            description: '完成一次有始有终的拾光漫游后，收入宝库的旅程纪要印记。',
            image: 'badges/adventure_summary_badge.png',
            silhouette: 'badges/adventure_summary_silhouette.png',
            revealEffect: 'badges/badge_reveal_halo.png',
            equipSlot: 'journey_seal',
            fallbackIcon: '旅印'
        };
    }

    function badgeAsset(relative) {
        return badgeRegistry && badgeRegistry.assetUrl ? badgeRegistry.assetUrl(relative) : ASSET + relative;
    }

    function equippedReward() {
        return rewardCollection && rewardCollection.equipped || null;
    }

    async function refreshRewards() {
        try {
            rewardCollection = unwrap(await api.getHarvestAdventureRewards());
            rewardError = '';
        } catch (reason) {
            rewardError = reason && reason.message ? reason.message : '旅印收藏暂时无法读取，请稍后重试。';
        }
        return rewardCollection;
    }

    // v3 重构：线性旅程，教→练→战→收，场景跟随剧情自动变化。
    var LINEAR_SCENES = {
        SCROLL: 'echo_archive', TRIAL: 'mist_valley', INTERLEAVE: 'transfer_bridge',
        BOSS: 'boss_core', TREASURY: 'harvest_treasury', completed: 'harvest_treasury'
    };
    function presentationSceneId(state, routeSceneId) {
        return LINEAR_SCENES[state] || routeSceneId || 'camp';
    }

    function stableIdentity() {
        var token = localStorage.getItem('token') || '';
        try {
            var payload = JSON.parse(decodeURIComponent(Array.prototype.map.call(atob(token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')), function (character) {
                return '%' + ('00' + character.charCodeAt(0).toString(16)).slice(-2);
            }).join('')));
            if (payload.sub || payload.user_id || payload.id) return String(payload.sub || payload.user_id || payload.id);
        } catch (_) {}
        var hash = 5381;
        for (var index = 0; index < token.length; index += 1) hash = ((hash << 5) + hash) ^ token.charCodeAt(index);
        return 'token-' + (hash >>> 0).toString(36);
    }

    function routeStorageKey(runId) {
        return ROUTE_STORAGE_PREFIX + encodeURIComponent(stableIdentity()) + ':' + encodeURIComponent(String(runId || ''));
    }

    function cleanRoute(runId) {
        return {
            schemaVersion: 1,
            userId: stableIdentity(),
            runId: String(runId || ''),
            currentSceneId: 'camp',
            visitedSceneIds: ['camp'],
            availableDestinationIds: ['echo_archive', 'mirror_lake', 'mechanism_workshop'],
            lastChosenDestinationId: '',
            sceneChoiceHistory: [],
            pendingChoice: true
        };
    }

    function loadRoute(runId) {
        var key = routeStorageKey(runId);
        var saved = null;
        try { saved = JSON.parse(localStorage.getItem(key) || 'null'); } catch (_) {}
        if (!saved || saved.runId !== String(runId || '') || saved.userId !== stableIdentity() || !registry || !registry.has(saved.currentSceneId)) {
            saved = cleanRoute(runId);
        }
        saved.visitedSceneIds = Array.isArray(saved.visitedSceneIds) && saved.visitedSceneIds.length ? saved.visitedSceneIds.filter(function (id) {
            return registry && registry.has ? registry.has(id) : id === 'camp';
        }) : ['camp'];
        if (saved.visitedSceneIds.indexOf('camp') < 0) saved.visitedSceneIds.unshift('camp');
        saved.sceneChoiceHistory = Array.isArray(saved.sceneChoiceHistory) ? saved.sceneChoiceHistory : [];
        saved.availableDestinationIds = Array.isArray(saved.availableDestinationIds) ? saved.availableDestinationIds : [];
        routeState = saved;
        saveRoute();
        return saved;
    }

    function saveRoute() {
        if (!routeState || !routeState.runId) return;
        localStorage.setItem(routeStorageKey(routeState.runId), JSON.stringify(routeState));
    }

    function clearStoredRoute() {
        if (!routeState || !routeState.runId) return;
        localStorage.removeItem(routeStorageKey(routeState.runId));
    }

    function emitWorld(detail) {
        document.dispatchEvent(new CustomEvent('shiguang:world', { detail: detail || {} }));
    }

    function presentationForNarrative(narrative, route) {
        narrative = narrative && typeof narrative === 'object' ? narrative : {};
        var world = narrative.world && typeof narrative.world === 'object' ? narrative.world : {};
        var stateCopy = narrative.state_copy && typeof narrative.state_copy === 'object' ? narrative.state_copy : {};
        var routes = narrative.routes && typeof narrative.routes === 'object' ? narrative.routes : {};
        var routeFrame = routes[route] && typeof routes[route] === 'object' ? routes[route] : {};
        var recommended = Array.isArray(narrative.recommended_destinations) ? narrative.recommended_destinations : [];
        return {
            primarySceneId: String(world.scene_id || recommended[0] && recommended[0].scene_id || ''),
            opening: String(stateCopy.opening || ''),
            worldRule: String(world.world_rule || ''),
            routeActionFrame: String(routeFrame.action_frame || ''),
            recommendedDestinations: recommended
        };
    }

    function narrativeRecommended() {
        var narr = snapshot && snapshot.narrative || null;
        var list = presentationForNarrative(narr, 'understanding').recommendedDestinations;
        if (!Array.isArray(list) || list.length < 2) return null;
        var filtered = [];
        for (var i = 0; i < list.length; i++) {
            var item = list[i];
            if (!item || !item.scene_id) continue;
            if (registry && registry.has && registry.has(item.scene_id)) {
                filtered.push({ id: item.scene_id, kind: 'travel', scene: sceneDefinition(item.scene_id) });
            }
        }
        return filtered.length >= 2 ? filtered : null;
    }

    function destinationOptions() {
        var current = routeState ? routeState.currentSceneId : 'camp';
        if (current === 'camp') {
            var recommended = narrativeRecommended();
            if (recommended) return recommended;
            return ['echo_archive', 'mirror_lake', 'mechanism_workshop'].map(function (id) {
                return { id: id, kind: 'travel', scene: sceneDefinition(id) };
            });
        }
        var onwardByScene = {
            echo_archive: ['mirror_lake', 'mist_valley'],
            mirror_lake: ['mechanism_workshop', 'transfer_bridge'],
            mechanism_workshop: ['echo_archive', 'mist_valley'],
            mist_valley: ['transfer_bridge', 'mirror_lake'],
            transfer_bridge: ['mechanism_workshop', 'echo_archive']
        };
        var onward = onwardByScene[current] || ['echo_archive', 'mirror_lake'];
        return [{ id: current, kind: 'stay', scene: sceneDefinition(current) }, { id: 'camp', kind: 'camp', scene: sceneDefinition('camp') }].concat(onward.map(function (id) {
            return { id: id, kind: 'travel', scene: sceneDefinition(id) };
        }));
    }

    function preloadAsset(sceneId, asset, kind) {
        if (!asset) return;
        var key = sceneId + ':' + kind;
        if (document.head.querySelector('link[data-scene-art-preload="' + key + '"]')) return;
        var preload = document.createElement('link');
        preload.rel = 'preload';
        preload.as = 'image';
        preload.type = /\.webp$/i.test(asset) ? 'image/webp' : 'image/png';
        preload.href = ASSET + asset;
        preload.setAttribute('data-scene-art-preload', key);
        if (kind === 'base') preload.setAttribute('data-scene-base-preload', sceneId);
        document.head.appendChild(preload);
    }

    function preloadSceneBase(sceneId) {
        var scene = sceneDefinition(sceneId);
        if (scene && scene.background) preloadAsset(scene.id, scene.background, 'base');
    }

    function preloadBossAssets(stage) {
        var boss = sceneDefinition('boss_core');
        preloadSceneBase('boss_core');
        if (!boss) return;
        if (stage <= 0) {
            preloadAsset(boss.id, boss.foregroundAsset, 'foregroundAsset');
            preloadAsset(boss.id, boss.phaseOneAsset, 'phaseOneAsset');
        } else if (stage === 1) {
            preloadAsset(boss.id, boss.coreExposedAsset, 'coreExposedAsset');
        } else {
            preloadAsset(boss.id, boss.restoredAsset, 'restoredAsset');
        }
    }

    function preloadForState(state) {
        var stage = Number(snapshot && snapshot.archive && snapshot.archive.boss_stage || 0);
        if (state === 'INTERLEAVE') preloadBossAssets(0);
        if (state === 'BOSS') preloadBossAssets(stage);
        if (state === 'BOSS' && stage >= 2) preloadSceneBase('harvest_treasury');
        if (state === 'TREASURY') {
            var treasury = sceneDefinition('harvest_treasury');
            preloadSceneBase('harvest_treasury');
            if (treasury.rewardRevealAsset) preloadAsset(treasury.id, treasury.rewardRevealAsset, 'rewardRevealAsset');
            if (treasury.fireworksReadyAsset) preloadAsset(treasury.id, treasury.fireworksReadyAsset, 'fireworksReadyAsset');
        }
    }

    function renderDestinationChoice() {
        var current = sceneDefinition(routeState ? routeState.currentSceneId : 'camp');
        var options = destinationOptions();
        options.forEach(function (option) {
            if (option.kind === 'travel') preloadSceneBase(option.id);
        });
        if (routeState) {
            routeState.availableDestinationIds = options.map(function (option) { return option.id; });
            saveRoute();
        }
        return '<div class="shiguang-copy shiguang-destination-choice"><small>旅途由你决定</small><div class="shiguang-kicker">' + (current.id === 'camp' ? '营灯照亮了三条可走的路' : '这一处的回应已经收好') + '</div><h2>' + (current.id === 'camp' ? '柿小吉想先带你去哪里？' : '接下来想留在这里，还是去别处看看？') + '</h2><p>不论走哪条路，已经拾到的微光都会留在行囊里。</p><div class="shiguang-destinations">' + options.map(function (option) {
            var label = option.kind === 'stay' ? '留在这里' : option.kind === 'camp' ? '返回营地' : '前往';
            return '<button type="button" data-action="destination" data-destination-id="' + escapeHtml(option.id) + '" data-destination-kind="' + escapeHtml(option.kind) + '"><span>' + escapeHtml(label) + '</span><strong>' + escapeHtml(option.scene.name) + '</strong><small>' + escapeHtml(option.scene.shortDescription || '') + '</small><em>' + escapeHtml((option.scene.learningAffordances || []).slice(0, 2).join(' · ')) + '</em></button>';
        }).join('') + '</div></div>';
    }

    function escapeHtml(value) {
        return String(value == null ? '' : value).replace(/[&<>'"]/g, function (char) {
            return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' })[char];
        });
    }

    function unwrap(result) { return result && Object.prototype.hasOwnProperty.call(result, 'data') ? result.data : result; }

    function Soundscape() {
        this.scene = 'prepare';
        var state = window.KakiAudioManager ? window.KakiAudioManager.getState() : {};
        this.muted = Boolean(state.muted);
        this.volume = Number(state.volume || 0.28);
        this.started = false;
    }
    Soundscape.prototype.ensure = function () {
        this.started = true;
        if (window.KakiAudioManager) window.KakiAudioManager.startAdventure(this.scene);
    };
    Soundscape.prototype.tone = function (frequency, duration, gain, type) {
        if (window.KakiAudioManager) window.KakiAudioManager.playSfx(frequency, duration, gain, type);
    };
    Soundscape.prototype.setScene = function (scene) {
        this.scene = scene;
        if (!this.started || !window.KakiAudioManager) return;
        if (scene === 'victory') window.KakiAudioManager.play('celebration', { volume: this.volume });
        else window.KakiAudioManager.startAdventure(scene);
    };
    Soundscape.prototype.setVolume = function (value) {
        this.volume = Math.max(0, Math.min(0.35, Number(value)));
        if (window.KakiAudioManager) window.KakiAudioManager.setVolume(this.volume);
    };
    Soundscape.prototype.toggleMute = function () {
        this.muted = window.KakiAudioManager ? window.KakiAudioManager.toggleMute() : !this.muted;
        return this.muted;
    };
    Soundscape.prototype.pause = function () {
        if (window.KakiAudioManager) window.KakiAudioManager.stop({ fadeMs: 460, reset: true });
    };
    Soundscape.prototype.destroy = function () {
        this.pause();
        this.started = false;
    };
    var audio = new Soundscape();

    function audioScene(state) {
        if (state === 'BOSS') return 'boss';
        if (state === 'TRIAL' || state === 'INTERLEAVE') return 'challenge';
        if (state === 'TREASURY' || state === 'completed') return 'victory';
        if (!state || state === 'GOAL') return 'prepare';
        return 'explore';
    }

    function inspectContext() {
        var threadId = api.threadId || sessionStorage.getItem('ai_workspace_test_thread_id') || '';
        var materialNodes = Array.prototype.slice.call(document.querySelectorAll('#materialList [data-material-id], #materialList [data-id]'));
        var artifactNodes = Array.prototype.slice.call(document.querySelectorAll('#artifactSideList [data-artifact-id], #artifactSideList [data-id]'));
        var latest = Array.prototype.slice.call(document.querySelectorAll('#messages .msg.user .message-bubble')).pop();
        var artifactTitle = artifactNodes[0] ? artifactNodes[0].textContent.trim().slice(0, 48) : '';
        var latestText = latest ? latest.textContent.replace(/\s+/g, ' ').trim().replace(/^(?:我要|我想|想要|帮我|带我|教我)(?:开始)?(?:学习|学会|入门|掌握)\s*/i, '').slice(0, 48) : '';
        return {
            threadId: threadId,
            topic: artifactTitle || latestText || '',
            materials: materialNodes.map(function (node) { return node.dataset.materialId || node.dataset.id; }).filter(Boolean),
            artifacts: artifactNodes.map(function (node) { return node.dataset.artifactId || node.dataset.id; }).filter(Boolean)
        };
    }

    function captureChat() {
        chatState = {
            threadId: api.threadId || sessionStorage.getItem('ai_workspace_test_thread_id') || '',
            scrollTop: messages ? messages.scrollTop : 0,
            draft: input ? input.value : '',
            focusInput: document.activeElement === input
        };
    }

    function restoreChat() {
        if (!chatState) return;
        if (input && input.value !== chatState.draft) input.value = chatState.draft;
        if (messages) window.requestAnimationFrame(function () { messages.scrollTop = chatState.scrollTop; });
        if (chatState.focusInput && input) input.focus();
    }

    function ensureOverlay() {
        if (overlay && overlay.isConnected) return overlay;
        overlay = document.createElement('section');
        overlay.className = 'shiguang-overlay';
        overlay.setAttribute('role', 'dialog');
        overlay.setAttribute('aria-modal', 'true');
        overlay.setAttribute('aria-label', '拾光漫游');
        overlay.innerHTML = '<div class="shiguang-loading">柿小吉正在铺开旅程卷轴……</div>';
        workspace.appendChild(overlay);
        workspace.setAttribute('data-shiguang-active', 'true');
        overlay.addEventListener('click', handleClick);
        overlay.addEventListener('input', handleInput);
        overlay.addEventListener('mouseover', handlePreviewIntent);
        overlay.addEventListener('focusin', handlePreviewIntent);
        overlay.addEventListener('submit', handleSubmit);
        overlay.addEventListener('error', handleBadgeAssetError, true);
        return overlay;
    }

    function handleBadgeAssetError(event) {
        var image = event.target;
        if (!image || !image.dataset || !image.dataset.badgeAsset) return;
        image.hidden = true;
        var holder = image.closest('.shiguang-badge-portrait, .shiguang-equipped-mark, [data-reward-display]');
        var fallback = holder && holder.querySelector('.shiguang-badge-fallback');
        if (fallback) fallback.hidden = false;
        if (holder) holder.classList.add('is-fallback');
        document.dispatchEvent(new CustomEvent('shiguang:direction', {
            detail: { type: 'asset-fallback', asset: image.src, assetKind: 'badge-' + image.dataset.badgeAsset }
        }));
    }

    function focusInside(selector) {
        if (!active || !overlay) return;
        var target = overlay.querySelector(selector || 'button:not([disabled]), input:not([disabled]), textarea:not([disabled])');
        if (target && !target.hidden) target.focus({ preventScroll: true });
    }

    function focusableControls() {
        if (!overlay) return [];
        return Array.prototype.slice.call(overlay.querySelectorAll('button:not([disabled]), input:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])')).filter(function (control) {
            return !control.hidden && !control.closest('[aria-hidden="true"], [inert]') && control.getClientRects().length > 0;
        });
    }

    function renderHeader() {
        var state = snapshot ? snapshot.state : '';
        var current = Math.max(0, NODES.indexOf(state));
        var completed = snapshot && state === 'completed';
        var progress = completed ? 100 : snapshot ? Math.round((current / (NODES.length - 1)) * 100) : 0;
        var place = sceneDefinition(presentationSceneId(state, routeState ? routeState.currentSceneId : 'camp'));
        var visited = routeState ? routeState.visitedSceneIds.length : 1;
        var archive = snapshot && snapshot.archive || {};
        var selectedSpell = snapshot && spellRegistry && spellRegistry.resolve ? spellRegistry.resolve(archive.magic_school || 'wood') : null;
        var spellBadge = selectedSpell ? '<div class="shiguang-active-spell" data-active-school="' + escapeHtml(selectedSpell.id) + '" title="当前法术：' + escapeHtml(archive.magic_school_name || selectedSpell.displayName) + '"><img src="' + escapeHtml(spellRegistry.assetUrl(selectedSpell.emblem)) + '" alt=""><span><small>当前法术</small><b>' + escapeHtml(archive.magic_school_name || selectedSpell.displayName) + '</b></span></div>' : '';
        var equipped = equippedReward();
        var badge = badgeDefinition(equipped && equipped.catalog_id);
        var equippedMark = equipped ? '<button type="button" class="shiguang-equipped-mark" data-action="reward-detail" data-artifact-id="' + Number(equipped.artifact_id) + '" title="当前装备：' + escapeHtml(badge.displayName) + '"><img data-badge-asset="body" src="' + escapeHtml(badgeAsset(badge.image)) + '" alt=""><span><small>当前旅印</small><b>' + escapeHtml(badge.displayName) + '</b></span><i class="shiguang-badge-fallback" hidden>' + escapeHtml(badge.fallbackIcon) + '</i></button>' : '';
        return '<header class="shiguang-head">' +
            '<div class="shiguang-brand"><span>拾</span><div><strong>拾光漫游</strong><small>用十分钟，完成一次有始有终的学习小旅程。</small></div></div>' +
            '<div class="shiguang-head-actions">' + equippedMark + spellBadge +
            (state ? '<div class="shiguang-act-badge" data-act="' + actForState(state).id + '"><span>第 ' + actForState(state).id + ' 幕</span><b>' + escapeHtml(actForState(state).name) + '</b></div>' : '') +
            '<div class="shiguang-progress-wrap"><span>' + (completed ? '旅程完成' : snapshot ? escapeHtml(place.name) + ' · 已探访 ' + visited + ' 处' : '出发准备') + '</span><div class="shiguang-progress" aria-label="学习证据进度"><i style="width:' + progress + '%"></i></div></div>' +
            '<button type="button" data-action="mute" class="shiguang-icon" aria-label="静音">' + (audio.muted ? '静' : '声') + '</button>' +
            '<input data-action="volume" class="shiguang-volume" type="range" min="0" max="1" step="0.05" value="' + audio.volume + '" aria-label="音量">' +
            '<button type="button" data-action="leave" class="shiguang-leave">暂时离开</button></div></header>';
    }

    function renderMap() {
        if (!routeState || !registry) return '';
        var current = routeState.currentSceneId;
        var visited = routeState.visitedSceneIds.map(function (id) { return sceneDefinition(id); });
        return '<div class="shiguang-map-shell shiguang-world-map"><div class="shiguang-map-title"><span>丰穗谷行迹</span><b>选择留下的地图</b></div><ol class="shiguang-map" aria-label="已探访地点">' + visited.map(function (scene, index) {
            return '<li class="' + (scene.id === current ? 'active' : 'completed') + '" aria-current="' + (scene.id === current ? 'location' : 'false') + '"><i>' + (index + 1) + '</i><span>' + escapeHtml(scene.name) + '</span></li>';
        }).join('') + '</ol></div>';
    }

    function itemMarkup(state) {
        if (state === 'SCROLL') return '<div class="shiguang-item shiguang-item-scroll" aria-hidden="true"></div>';
        if (state === 'SUPPLY') return '<div class="shiguang-item shiguang-item-chest" aria-hidden="true"></div>';
        if (state === 'TREASURY' || state === 'completed') {
            var reward = snapshot && snapshot.reward || {};
            var badge = badgeDefinition(reward.catalog_id);
            return '<div class="shiguang-reward-display ' + (state === 'completed' ? 'is-revealed' : 'is-pending') + '" data-reward-display data-badge-id="' + escapeHtml(badge.id) + '" aria-hidden="true">' +
                '<img class="shiguang-reward-halo" data-badge-asset="halo" src="' + escapeHtml(badgeAsset(badge.revealEffect)) + '" alt="">' +
                '<img class="shiguang-reward-badge" data-badge-asset="body" src="' + escapeHtml(badgeAsset(badge.image)) + '" alt="">' +
                '<span class="shiguang-badge-fallback" hidden>' + escapeHtml(badge.fallbackIcon) + '</span></div>';
        }
        return '';
    }

    function appearanceState(state) {
        if (state === 'completed' || state === 'TREASURY') return 'restored';
        if (snapshot && snapshot.feedback && snapshot.feedback.kind === 'needs_review') return 'anomaly';
        if (snapshot && snapshot.feedback && snapshot.feedback.kind === 'success') return 'restored';
        if (state === 'TRIAL' || state === 'INTERLEAVE' || state === 'BOSS') return 'challenge';
        if (state === 'SUPPLY' && (snapshot.archive || {}).remedial_required) return 'occupied';
        return 'normal';
    }

    function sceneArtSelection(scene, state) {
        var appearance = appearanceState(state);
        var stage = Number(snapshot && snapshot.archive && snapshot.archive.boss_stage || 0);
        if (scene.id === 'boss_core') {
            if (stage >= 3) return { state: 'boss_restored', kind: 'state', asset: scene.restoredAsset };
            if (stage >= 2) return { state: 'boss_core_exposed', kind: 'state', asset: scene.coreExposedAsset };
            if (stage >= 1) return { state: 'boss_phase_one', kind: 'state', asset: scene.phaseOneAsset };
            return { state: 'boss_normal', kind: '', asset: '' };
        }
        if (scene.id === 'harvest_treasury') {
            if (state === 'completed') return { state: 'fireworks_ready', kind: 'state', asset: scene.fireworksReadyAsset };
            if (state === 'TREASURY' && panelMode === 'learning') return { state: 'reward_reveal', kind: 'state', asset: scene.rewardRevealAsset };
            return { state: 'treasury_arrival', kind: '', asset: '' };
        }
        if (appearance === 'restored') return { state: 'restored', kind: 'restored', asset: scene.restoredAsset };
        if (appearance === 'anomaly' || appearance === 'challenge' || appearance === 'occupied') {
            return { state: 'anomaly', kind: 'anomaly', asset: scene.anomalyAsset };
        }
        return { state: 'normal', kind: '', asset: '' };
    }

    function sceneArtMarkup(scene, state) {
        var selection = sceneArtSelection(scene, state);
        var layer = function (kind, asset, artState) {
            if (!asset) return '';
            var stateClass = kind === 'foreground' ? '' : ' shiguang-scene-art-state';
            return '<img class="shiguang-scene-art-layer shiguang-scene-art-' + kind + stateClass + '" data-layer-kind="' + kind + '" data-layer-state="' + escapeHtml(artState || kind) + '" data-layer-active="true" data-asset="' + escapeHtml(ASSET + asset) + '" src="' + escapeHtml(ASSET + asset) + '" loading="eager" decoding="async" alt="" aria-hidden="true">';
        };
        return layer('foreground', scene.foregroundAsset, 'foreground') +
            layer(selection.kind, selection.asset, selection.state);
    }

    function renderScene(state, sceneId) {
        var scene = sceneDefinition(sceneId);
        var artSelection = sceneArtSelection(scene, state);
        var mascot = MASCOTS[state] || 'idle';
        var archive = snapshot.archive || {};
        var school = archive.magic_school || 'wood';
        var route = archive.selected_strategy || 'understanding';
        var showEnemy = state === 'TRIAL' || state === 'INTERLEAVE';
        var showBoss = state === 'BOSS' && Number(archive.boss_stage || 0) < 3;
        var showCelebration = state === 'TREASURY' || state === 'completed';
        var showPlayer = state !== 'completed';
        var motes = Array.from({ length: 12 }).map(function (_, index) { return '<i style="--mote:' + index + '"></i>'; }).join('');
        return '<div class="shiguang-scene shiguang-scene-' + escapeHtml(String(state).toLowerCase()) + '" data-scene-location="' + escapeHtml(scene.id) + '" data-scene-state="' + escapeHtml(artSelection.state) + '" data-scene-fallback="' + String(Boolean(scene.isFallback)) + '" data-scene-base-fallback="' + escapeHtml(scene.fallbackBackground || '') + '" data-entry-direction="' + escapeHtml(scene.entryDirection) + '" data-exit-direction="' + escapeHtml(scene.exitDirection) + '" data-transition-style="' + escapeHtml(scene.transitionStyle) + '" data-school="' + escapeHtml(school) + '" data-route="' + escapeHtml(route) + '" style="--scene:url(\'' + ASSET + scene.background + '\');--player-x:' + Number(scene.playerAnchor.x) + '%;--player-y:' + Number(scene.playerAnchor.y) + '%;--companion-x:' + Number(scene.companionAnchor.x) + '%;--companion-y:' + Number(scene.companionAnchor.y) + '%;--enemy-x:' + Number(scene.enemyAnchor.x) + '%;--enemy-y:' + Number(scene.enemyAnchor.y) + '%">' +
            '<div class="shiguang-scene-wash"></div><div class="shiguang-mist-layer" aria-hidden="true"></div>' + sceneArtMarkup(scene, state) + '<div class="shiguang-fireflies" aria-hidden="true">' + motes + '</div>' +
            (showCelebration ? '<div class="shiguang-celebration-bursts" aria-hidden="true"><i></i><i></i><i></i></div>' : '') +
            renderMap() + '<div class="shiguang-spell-circle" aria-hidden="true"><i></i><i></i></div>' +
            (showPlayer ? '<img class="shiguang-player shiguang-white-composite" src="' + ASSET + 'characters/player_scholar_alpha.png" alt="本次漫游的旅者">' : '') +
            (showEnemy ? '<img class="shiguang-enemy shiguang-white-composite" src="' + ASSET + 'characters/enemy_inkbeast_alpha.png" alt="墨迹兽">' : '') +
            (showBoss ? '<img class="shiguang-boss-character shiguang-white-composite" src="' + ASSET + 'characters/boss_moyan_master_alpha.png" alt="墨魇之主">' : '') +
            '<img class="shiguang-mascot" src="' + ASSET + 'characters/mascot_shixiaoji_' + mascot + '.png" alt="柿小吉">' + itemMarkup(state) +
            (showBoss ? '<div class="shiguang-boss-seals" aria-label="守关进度"><i class="' + (Number(archive.boss_stage || 0) >= 1 ? 'lit' : '') + '">解</i><i class="' + (Number(archive.boss_stage || 0) >= 2 ? 'lit' : '') + '">辨</i><i class="' + (Number(archive.boss_stage || 0) >= 3 ? 'lit' : '') + '">迁</i></div><div class="shiguang-fog" style="opacity:' + Math.min(.82, Math.max(0, Number(archive.boss_fog || 0) / 122)) + '"></div>' : '') +
            '<div class="shiguang-scene-caption"><span>' + escapeHtml(scene.name) + '</span><b>' + escapeHtml(scene.region) + (scene.isFallback ? ' · 暂用既有背景' : ' · 正式场景') + '</b></div></div>';
    }

    function feedbackHtml() {
        var feedback = snapshot && snapshot.feedback;
        if (!feedback || !feedback.message) return '';
        var kind = feedback.kind || '';
        var label = kind === 'success' ? '拾到一束光' : kind === 'hint' ? '柿小吉提示' : '证据待补充';
        var html = '<div class="shiguang-feedback ' + escapeHtml(kind) + '" role="status"><strong>' + label + '</strong><p>' + escapeHtml(feedback.message) + '</p>';
        if (kind === 'needs_review') {
            var ladder = Number(feedback.ladder || 1);
            var teaching = feedback.explanation || feedback.knowledge || feedback.wrong_guidance || feedback.rationale || feedback.correction || (feedback.scaffold && feedback.scaffold.length);
            if (teaching) {
                html += '<div class="shiguang-feedback-teaching" data-ladder="' + ladder + '">';
                // 三问阶梯：第 1 次给方向，第 2 次给步骤，第 3 次给完整答案
                html += '<p class="teaching-ladder-note">' + (ladder >= 3 ? '柿小吉把答案摊开了，你照着说一遍：' : ladder >= 2 ? '柿小吉开始拆解了，跟着步骤走：' : '柿小吉给你指个方向，再试一次：') + '</p>';
                if (feedback.rationale) html += '<p class="teaching-point"><b>这道题在考：</b>' + escapeHtml(feedback.rationale) + '</p>';
                if (feedback.wrong_guidance) html += '<p class="teaching-point"><b>' + (ladder >= 2 ? '柿小吉讲解：' : '方向：') + '</b>' + escapeHtml(feedback.wrong_guidance) + '</p>';
                if (feedback.scaffold && feedback.scaffold.length) {
                    html += '<ol class="teaching-scaffold">' + feedback.scaffold.map(function (step, index) {
                        return '<li>' + escapeHtml(step) + '</li>';
                    }).join('') + '</ol>';
                }
                if (feedback.explanation && ladder >= 3) html += '<p class="teaching-point"><b>完整答案：</b>' + escapeHtml(feedback.explanation) + '</p>';
                if (feedback.knowledge && ladder >= 3) html += '<p class="teaching-point"><b>知识点：</b>' + escapeHtml(feedback.knowledge) + '</p>';
                if (feedback.correction && ladder >= 3) html += '<p class="teaching-point"><b>误区纠正：</b>' + escapeHtml(feedback.correction) + '</p>';
                html += '</div>';
            }
        }
        html += '</div>';
        return html;
    }

    function taskComposer(task, action) {
        if (!task) return '';
        if (task.type === 'multiple_choice') {
            return '<div class="shiguang-options">' + (task.options || []).map(function (option, index) {
                return '<button type="button" data-action="answer" data-answer="' + escapeHtml(option) + '"><i>' + String.fromCharCode(65 + index) + '</i><span>' + escapeHtml(option) + '</span></button>';
            }).join('') + '</div>';
        }
        return '<form class="shiguang-answer" data-form-action="' + escapeHtml(action || 'submit') + '"><textarea name="answer" maxlength="500" placeholder="写下你的回答；不需要很长，但要留下可判断的证据。" required></textarea><div><button type="button" data-action="hint" class="shiguang-secondary">给我一点提示</button><button type="submit" class="shiguang-primary">提交回答</button></div></form>';
    }

    function streakChip() {
        var streak = Number(snapshot && snapshot.archive && snapshot.archive.streak || 0);
        if (streak < 2) return '';
        return '<div class="shiguang-streak" aria-label="连击 ' + streak + ' 次"><i>连击</i><b>×' + streak + '</b></div>';
    }

    function evidenceRecapLine() {
        var archive = snapshot.archive || {};
        var correct = Number(archive.correct_count || 0);
        var best = Number(archive.best_streak || 0);
        var repaired = Number(archive.repaired_count || 0);
        var line = '你答对了 ' + correct + ' 题，最高连击 ×' + best;
        if (repaired > 0) line += '，还补了 ' + repaired + ' 次缺';
        return line + '。';
    }

    function evidenceRecapMarkup() {
        var archive = snapshot.archive || {};
        var checks = (snapshot.timeline || []).filter(function (event) { return event.task_type === 'learning_check'; });
        var correct = Number(archive.correct_count || 0);
        var best = Number(archive.best_streak || 0);
        var repaired = Number(archive.repaired_count || 0);
        var reviewCount = checks.filter(function (event) { return event.result === 'needs_review'; }).length;
        return '<aside class="shiguang-evidence-recap"><small>本局战绩 · 由真实作答生成</small><dl>' +
            '<div><dt>答对</dt><dd>' + correct + ' 题</dd></div>' +
            '<div><dt>最高连击</dt><dd>×' + best + '</dd></div>' +
            '<div><dt>补缺次数</dt><dd>' + repaired + ' 次</dd></div>' +
            '<div><dt>作答证据</dt><dd>' + checks.length + ' 条</dd></div>' +
            '</dl><p>' + evidenceRecapLine() + (reviewCount ? '错过的每一处，最后都变成了证据。这就是你学会的痕迹。' : '干净利落的一趟旅程。') + '</p></aside>';
    }

    function rewardImageMarkup(item, silhouette) {
        var badge = badgeDefinition(item && item.catalog_id);
        var source = silhouette ? badge.silhouette : badge.image;
        return '<span class="shiguang-badge-portrait ' + (silhouette ? 'is-silhouette' : '') + '">' +
            '<img data-badge-asset="' + (silhouette ? 'silhouette' : 'body') + '" src="' + escapeHtml(badgeAsset(source)) + '" alt="">' +
            '<i class="shiguang-badge-fallback" hidden>' + escapeHtml(badge.fallbackIcon) + '</i></span>';
    }

    function rewardDetailMarkup(item) {
        if (!item) return '';
        var badge = badgeDefinition(item.catalog_id);
        return '<section class="shiguang-reward-detail" data-reward-detail="' + Number(item.artifact_id) + '">' +
            '<button type="button" class="shiguang-reward-back" data-action="reward-back">返回收藏架</button>' +
            rewardImageMarkup(item, false) +
            '<div><small>' + (item.equipped ? '当前装备 · 旅印槽位' : '已收藏 · 尚未装备') + '</small><h2>' + escapeHtml(badge.displayName) + '</h2>' +
            '<p>' + escapeHtml(item.acquired_reason || badge.description) + '</p><dl><div><dt>来源旅程</dt><dd>' + escapeHtml(item.topic || item.title || '拾光漫游') + '</dd></div><div><dt>收入宝库</dt><dd>' + escapeHtml(item.collected_at ? new Date(item.collected_at).toLocaleString('zh-CN') : '已保存') + '</dd></div></dl>' +
            '<div class="shiguang-reward-actions">' + (item.equipped
                ? '<button type="button" class="shiguang-secondary" data-action="reward-unequip" data-artifact-id="' + Number(item.artifact_id) + '">卸下旅印</button>'
                : '<button type="button" class="shiguang-primary" data-action="reward-equip" data-artifact-id="' + Number(item.artifact_id) + '">装备到旅印槽位</button>') +
            '</div></div></section>';
    }

    function rewardComparisonMarkup(comparison) {
        if (!comparison) return '';
        var current = comparison.current_equipped;
        var candidate = comparison.candidate;
        return '<section class="shiguang-reward-compare" role="alertdialog" aria-label="确认替换当前旅印">' +
            '<small>同一槽位已有旅印</small><h2>要替换当前装备吗？</h2><div class="shiguang-reward-compare-grid">' +
            '<article>' + rewardImageMarkup(current, false) + '<span>当前装备</span><b>' + escapeHtml(current.topic || current.title) + '</b></article>' +
            '<i aria-hidden="true">→</i>' +
            '<article>' + rewardImageMarkup(candidate, false) + '<span>准备装备</span><b>' + escapeHtml(candidate.topic || candidate.title) + '</b></article></div>' +
            '<p>取消不会改变当前装备；确认后才会调用真实替换接口。</p><div class="shiguang-reward-actions"><button type="button" class="shiguang-secondary" data-action="reward-replace-cancel">保留当前旅印</button><button type="button" class="shiguang-primary" data-action="reward-replace-confirm" data-artifact-id="' + Number(candidate.artifact_id) + '">确认替换</button></div></section>';
    }

    function rewardShelfMarkup(compact) {
        var items = rewardCollection && rewardCollection.items || [];
        var selected = items.find(function (item) { return Number(item.artifact_id) === Number(rewardDetailId); });
        if (rewardComparison) return rewardComparisonMarkup(rewardComparison);
        if (selected) return rewardDetailMarkup(selected);
        var badge = badgeDefinition('adventure_summary');
        var cards = items.length ? items.map(function (item) {
            return '<button type="button" class="shiguang-reward-tile ' + (item.equipped ? 'is-equipped' : '') + '" data-action="reward-detail" data-artifact-id="' + Number(item.artifact_id) + '">' +
                rewardImageMarkup(item, false) + '<span>' + (item.equipped ? '当前装备' : '已收藏') + '</span><b>' + escapeHtml(badge.displayName) + '</b><small>' + escapeHtml(item.topic || '拾光漫游') + '</small></button>';
        }).join('') : '<div class="shiguang-reward-tile is-locked">' + rewardImageMarkup({ catalog_id: badge.id }, true) + '<span>尚未获得</span><b>' + escapeHtml(badge.displayName) + '</b><small>完成一次拾光漫游后收入宝库</small></div>';
        return '<section class="shiguang-reward-shelf ' + (compact ? 'is-compact' : '') + '"><header><div><small>丰穗宝库 · 旅印收藏</small><h2>' + (items.length ? '你留下的旅程印记' : '展示架仍在等待第一束光') + '</h2></div><span>' + items.length + ' 枚已收藏</span></header><div class="shiguang-reward-shelf-track">' + cards + '</div>' +
            (rewardError ? '<p class="shiguang-error" role="alert">' + escapeHtml(rewardError) + '</p>' : '') + '</section>';
    }

    function rewardCandidateMarkup() {
        var reward = snapshot.reward || {};
        var badge = badgeDefinition(reward.catalog_id);
        return '<div class="shiguang-copy shiguang-treasure shiguang-reward-candidate" data-reward-candidate>' +
            '<small>拾光宝库 · 旅印候选</small><div class="shiguang-kicker">展示台的穗光正在回应</div><h2>' + escapeHtml(badge.displayName) + '</h2>' +
            '<p>' + escapeHtml(reward.acquired_reason || badge.description) + '</p>' +
            (enemyDirector ? '<aside class="shiguang-companion-recognition"><small>柿小吉记得你怎样走到这里</small><p>' + escapeHtml(enemyDirector.aftermathRecognition()) + '</p></aside>' : '') +
            '<div class="shiguang-evidence-count"><strong>' + (snapshot.timeline || []).filter(function (event) { return event.task_type === 'learning_check'; }).length + '</strong><span>段真实作答证据已收录</span></div>' +
            '<div class="shiguang-reward-actions"><button class="shiguang-secondary" data-action="reward-later">稍后处理</button><button class="shiguang-secondary" data-action="reward-candidate-detail">查看详情</button><button class="shiguang-primary" data-action="reward-collect">收入收藏</button><button class="shiguang-primary" data-action="reward-collect-equip">收入并装备</button></div><p class="shiguang-reward-promise">在你确认前不会自动装备；真实接口返回后才会更新状态。</p></div>';
    }

    function renderBody() {
        if (panelMode === 'destination') return renderDestinationChoice();
        var state = snapshot.state;
        var content = snapshot.content || {};
        var archive = snapshot.archive || {};
        if (state === 'SCROLL') {
            var concept = content.concept || {};
            var mis = content.misconception || {};
            var check = content.scroll.teaching_check || {};
            var checkMarkup = check.question ? '<div class="shiguang-teaching-check" data-teaching-check><small>理解确认 · 柿小吉考你一题</small><h3>' + escapeHtml(check.question) + '</h3>' + taskComposer(check, 'teaching-check') + '</div>' : '';
            var passed = archive.teaching_passed;
            if (passed) {
                // 已确认理解：直接给出战入口，不再展示分页正文。
                return '<div class="shiguang-copy shiguang-scroll-copy"><blockquote>' + escapeHtml(content.scroll.rule) + '</blockquote>' +
                    '<p class="teaching-done">✅ 你已经确认理解这条规则。墨色已经在石径那头聚拢——该出招了。</p>' +
                    '<button class="shiguang-primary" data-action="continue"><span>收好卷轴，前去应战</span><i>→</i></button></div>';
            }
            // 分页叙事：一页一个知识点，内容少到不用滚动，靠翻页推进（galgame 阅读节奏）。
            var pages = [
                '<div class="shiguang-page"><b>核心规则</b><blockquote>' + escapeHtml(content.scroll.rule) + '</blockquote></div>',
                (concept.explanation ? '<div class="shiguang-page"><b>为什么这条规则成立</b><p>' + escapeHtml(concept.explanation) + '</p></div>' : ''),
                (mis.claim ? '<div class="shiguang-page"><b>别踩这个误区</b><p class="is-mis">' + escapeHtml(mis.claim) + '</p><p class="is-mis">正解：' + escapeHtml(mis.correction) + '</p></div>' : ''),
                '<div class="shiguang-page"><b>卷轴旁注</b><p>' + escapeHtml(content.scroll.example) + '</p></div>'
            ].filter(Boolean);
            if (scrollPageIndex === 'done') {
                var nextAreaCheck = check.question
                    ? '<p class="teaching-wait">⬇ 先答对上面的理解确认题，【前去应战】才会亮起。</p><button class="shiguang-primary is-disabled" data-action="continue" disabled><span>收好卷轴，前去应战</span><i>→</i></button>'
                    : '<button class="shiguang-primary" data-action="continue"><span>收好卷轴，前去应战</span><i>→</i></button>';
                return '<div class="shiguang-copy shiguang-scroll-copy"><div class="shiguang-scroll-pager"><span>卷轴 · 读毕</span></div>' + checkMarkup + nextAreaCheck + '</div>';
            }
            var idx = Math.max(0, Math.min(Number(scrollPageIndex || 0), pages.length - 1));
            var isLast = idx >= pages.length - 1;
            return '<div class="shiguang-copy shiguang-scroll-copy"><div class="shiguang-scroll-pager"><span>卷轴 · ' + (idx + 1) + ' / ' + pages.length + '</span></div>' +
                pages[idx] +
                '<div class="shiguang-page-actions">' + (isLast
                    ? '<button class="shiguang-primary" data-action="scroll-finish"><span>读完卷轴，进入确认</span><i>→</i></button>'
                    : '<button class="shiguang-primary" data-action="scroll-next"><span>继续</span><i>→</i></button>') + '</div></div>';
        }
        var route = archive.selected_strategy || 'understanding';
        if (state === 'TRIAL') {
            var enemyLines = snapshot.narrative && snapshot.narrative.enemy_lines || {};
            if (!archive.selected_strategy) {
                // 练·第一战开场：先选“出招方式”（决定题目路线），敌人先嘲讽。
                return '<div class="shiguang-copy shiguang-prep"><small>练 · 初次遭遇</small><div class="shiguang-kicker">墨迹兽拦住了石径</div><h2>它正盯着你手里的卷轴。</h2><p class="shiguang-enemy-taunt">“' + escapeHtml(enemyLines.trial || '知道一个说法就够了，何必还要拿新的情境来验证？') + '”</p><p>选一种出招方式还击。方式不同，出题的路数也不同——但都得用刚收下的规则。</p><div class="shiguang-methods">' + METHODS.map(function (method) { return '<button type="button" data-action="method" data-method="' + method.id + '" data-route="' + method.route + '"><i>' + method.icon + '</i><strong>' + method.title + '</strong><span>' + escapeHtml(method.text) + '</span><em>出招</em></button>'; }).join('') + '</div></div>';
            }
            var trialAttempts = Number(snapshot.attempts && snapshot.attempts.TRIAL || 0);
            var task = trialAttempts >= 2 ? content.remedy : content[route].trial;
            var prep = trialAttempts >= 2 ? '（它换了种问法）' : trialAttempts >= 1 ? '（它还记得你刚才那句）' : '';
            return '<div class="shiguang-copy shiguang-battle-copy"><small>练 · 初次遭遇 · ' + (route === 'practice' ? '动手校准' : '解释出招') + '</small><div class="shiguang-encounter"><span class="shiguang-enemy-rank is-minion">小敌 · ' + escapeHtml(task.type === 'multiple_choice' ? '客观题' : '简答题') + '</span><b>墨迹兽拦住去路</b><span>用刚收下的规则还击' + prep + '</span></div>' + streakChip() + '<h2>' + escapeHtml(task.question) + '</h2>' + taskComposer(task) + '</div>';
        }
        if (state === 'INTERLEAVE') return '<div class="shiguang-copy shiguang-battle-copy"><small>练 · 交错挑战</small><div class="shiguang-encounter"><span class="shiguang-enemy-rank is-minion">小敌 · ' + escapeHtml(content[route].interleave.type === 'multiple_choice' ? '客观题' : '简答题') + '</span><b>规则换了情境</b><span>' + (route === 'practice' ? '把规则放进新情境里动手验证' : '用自己的语言重新连接旧线索') + '</span></div>' + streakChip() + '<h2>' + escapeHtml(content[route].interleave.question) + '</h2><p>题型已经切换，用来判断刚才的理解能否迁移，而不是重复同一道题。</p>' + taskComposer(content[route].interleave) + '</div>';
        if (state === 'BOSS') {
            var stage = Number(archive.boss_stage || 0);
            var bossTask = content.boss[stage];
            return '<div class="shiguang-copy shiguang-boss"><small>战 · 古塔守关 · ' + escapeHtml(bossTask.label) + ' ' + (stage + 1) + '/3</small><div class="shiguang-boss-title"><span>墨魇之主</span><b>只有完整证据能让迷雾退去</b><em class="shiguang-enemy-rank is-boss">BOSS · ' + escapeHtml(bossTask.type === 'multiple_choice' ? '客观题' : '简答题') + '</em></div>' + streakChip() + '<h2>' + escapeHtml(bossTask.question) + '</h2><div class="shiguang-fog-meter"><span>古塔迷雾</span><i><em style="width:' + Number(archive.boss_fog || 0) + '%"></em></i><b>' + Number(archive.boss_fog || 0) + '%</b></div><div class="shiguang-stage-seals"><span class="' + (stage >= 0 ? 'current' : '') + '">解释</span><span class="' + (stage >= 1 ? 'current' : '') + '">辨析</span><span class="' + (stage >= 2 ? 'current' : '') + '">迁移</span></div>' + taskComposer(bossTask) + '</div>';
        }
        if (state === 'TREASURY') return '<div class="shiguang-treasury-wrap">' + evidenceRecapMarkup() + rewardCandidateMarkup() + '</div>';
        if (state === 'completed') return '<div class="shiguang-copy shiguang-treasure"><small>远征凯旋</small><div class="shiguang-kicker">真实纪要已经收入宝库</div><h2>这束光已经被保存</h2><p>“拾光漫游纪要”已进入学习产物，产物 ID：' + escapeHtml(snapshot.artifact_id) + '。收藏与装备彼此独立，你随时可以更换或卸下旅印。</p></div>' + rewardShelfMarkup(false) + '<div class="shiguang-complete-actions"><button class="shiguang-secondary" data-action="return">带着纪要回到原来的聊天</button></div>';
        return '<div class="shiguang-copy"><h2>正在恢复旅程</h2></div>';
    }

    function narrativeStateCopyKey() {
        var st = snapshot && snapshot.state || '';
        var key = { SCROLL: 'opening', TRIAL: 'first_trial',
                    INTERLEAVE: 'interleave', BOSS: 'boss_reveal', TREASURY: 'resolution' }[st];
        return key;
    }

    function storyLine(speaker, speakerType, text, characterKey, tone, options) {
        options = options || {};
        if (speaker === '旁白' && snapshot && snapshot.narrative && snapshot.narrative.state_copy) {
            var _key = narrativeStateCopyKey();
            var _frozen = _key && snapshot.narrative.state_copy[_key];
            if (typeof _frozen === 'string' && _frozen.trim()) text = _frozen;
        }
        return {
            speaker: speaker,
            speakerType: speakerType,
            text: text,
            portrait: characterKey ? ASSET + 'characters/' + characterKey + '.png' : '',
            characterKey: characterKey || '',
            tone: tone || 'steady',
            skippable: options.skippable !== false,
            autoAdvance: Boolean(options.autoAdvance),
            delayAfter: Number(options.delayAfter || 180)
        };
    }

    function scenePreludeHtml() {
        if (panelMode !== 'learning' || !routeState) return '';
        var scene = sceneDefinition(presentationSceneId(snapshot && snapshot.state, routeState.currentSceneId));
        if (!scene.interactionPrelude) return '';
        return '<div class="shiguang-scene-prelude"><span>' + escapeHtml(scene.name) + '的线索</span><p>' + escapeHtml(scene.interactionPrelude) + '</p></div>';
    }

    function storyFor(state, options) {
        options = options || {};
        var content = snapshot.content || {};
        var archive = snapshot.archive || {};
        var route = archive.selected_strategy || 'understanding';
        var feedback = snapshot.feedback || {};
        if (options.local) {
            return [
                storyLine('旁白', 'narrator', feedback.kind === 'success' ? '微光向前推开一层雾，眼前的景物随之回应。' : '风声没有催促，眼前的线索仍停在原处。', '', 'narrative'),
                storyLine('柿小吉', 'companion', feedback.message || '先把眼前这一步看清，我们再继续。', 'mascot_shixiaoji_hint', feedback.kind === 'success' ? 'encouraging' : 'thinking')
            ];
        }
        if (state === 'SCROLL') return [
            storyLine('旁白', 'narrator', '卷轴从草叶间升起，旧墨迹在暖光里重新显形。', '', 'narrative'),
            storyLine('旅者', 'player', '先收下一条能反复使用的规则，再去面对前面的路。', 'player_scholar_alpha', 'thinking'),
            storyLine('柿小吉', 'companion', '别急着背下来。看看它怎样解释例子，路会更好走。', 'mascot_shixiaoji_observe', 'steady')
        ];
        var enemyLines = snapshot.narrative && snapshot.narrative.enemy_lines || {};
        var mascotLines = snapshot.narrative && snapshot.narrative.mascot_lines || {};
        if (state === 'TRIAL') {
            if (!archive.selected_strategy) {
                // 出招前：敌人先挑衅，玩家选还击方式
                return [
                    storyLine('旁白', 'narrator', '墨迹从石径右侧聚拢，挡住了刚刚亮起的路。', '', 'narrative'),
                    storyLine('墨迹兽', 'enemy', enemyLines.trial || '知道一个说法就够了，何必还要拿新的情境来验证？', 'enemy_inkbeast_alpha', 'challenging'),
                    storyLine('旅者', 'player', archive.identity_trial_line || '我会用刚收下的规则回应，让它藏不住漏洞。', 'player_scholar_alpha', 'steady'),
                    storyLine('柿小吉', 'companion', '它先出招了。选一种你最顺手的还击方式。', 'mascot_shixiaoji_encourage', 'encouraging')
                ];
            }
            var trialAttempts = Number(snapshot.attempts && snapshot.attempts.TRIAL || 0);
            return [
                storyLine('旁白', 'narrator', trialAttempts >= 1 ? '墨迹兽还在原处，等你的下一句。' : '你抬手，第一道证据已经蓄势。', '', 'narrative'),
                storyLine('墨迹兽', 'enemy', trialAttempts >= 1 ? (enemyLines.trial_review || enemyLines.trial) : enemyLines.trial, 'enemy_inkbeast_alpha', 'challenging'),
                storyLine('柿小吉', 'companion', (archive.streak >= 2 ? mascotLines.streak + ' ' : '') + (trialAttempts >= 2 ? mascotLines.remedy : mascotLines.review) + ' 用刚收下的规则回应。', 'mascot_shixiaoji_encourage', 'encouraging')
            ];
        }
        if (state === 'INTERLEAVE') return [
            storyLine('旁白', 'narrator', route === 'practice' ? '稻香小径拐进陌生地形，熟悉的规则需要换一种用法。' : '月桂古桥在雾中延伸，旧线索必须重新连接。', '', 'narrative'),
            storyLine('墨迹兽', 'enemy', enemyLines.interleave || '题目已经变了，刚才那套理解不会再有用。', 'enemy_inkbeast_alpha', 'challenging'),
            storyLine('柿小吉', 'companion', (archive.streak >= 2 ? mascotLines.streak + ' ' : '') + '正因为情境变了，才更能看出规则有没有真正被你带走。', 'mascot_shixiaoji_observe', 'encouraging')
        ];
        if (state === 'BOSS') {
            var stage = Number(archive.boss_stage || 0);
            return [
                storyLine('旁白', 'narrator', '古塔的迷雾压低下来，三道封印在深处依次亮起。', '', 'narrative'),
                storyLine('墨魇之主', 'enemy', (enemyLines.boss && enemyLines.boss[stage]) || '零散的答案进不了这座塔。', '', 'challenging'),
                storyLine('旅者', 'player', archive.identity_boss_line || '我会把三道证据串成完整的解释。', 'player_scholar_alpha', 'steady'),
                storyLine('柿小吉', 'companion', stage === 0 ? (mascotLines.boss_start || '解释、辨析、迁移，一道一道来。') : (archive.streak >= 2 ? mascotLines.streak + ' ' : '') + '雾淡了一分，封印亮了一枚。继续。', 'mascot_shixiaoji_encourage', 'encouraging')
            ];
        }
        if (state === 'TREASURY') return [
            storyLine('旁白', 'narrator', '墨色退回石缝，古塔先安静下来。片刻之后，丰穗谷的灯火才沿山路重新亮起。', '', 'narrative'),
            storyLine('旅者', 'player', '这次留下的不只是答案，还有我怎样走到答案的证据。', 'player_scholar_alpha', 'steady'),
            storyLine('柿小吉', 'companion', (enemyDirector ? enemyDirector.aftermathRecognition() + ' ' : '') + evidenceRecapLine() + ' 所以，这枚旅印属于你。', 'mascot_shixiaoji_celebrate', 'encouraging', { skippable: false })
        ];
        if (state === 'completed') return [
            storyLine('旁白', 'narrator', '丰穗印落下，旅程纪要在微光中安静成形。', '', 'narrative'),
            storyLine('柿小吉', 'companion', '带着它回去吧。今天走过的路，明天仍然找得到。', 'mascot_shixiaoji_celebrate', 'encouraging')
        ];
        return [];
    }

    function locationStory(sceneId, kind) {
        var scene = sceneDefinition(sceneId);
        if (sceneId === 'camp') {
            return [
                storyLine('旁白', 'narrator', kind === 'return' ? '远处的营灯重新靠近，刚才走过的地方仍在地图上发亮。' : '微光营地的灯被依次点亮，三条小路从谷口伸向不同方向。', '', 'narrative'),
                storyLine('柿小吉', 'companion', '回声书阁适合把规则说清，镜湖会让例子映出差异，机关工坊则能把方法变成可以动手的机关。', 'mascot_shixiaoji_welcome', 'encouraging'),
                storyLine('柿小吉', 'companion', kind === 'return' ? '刚才收好的线索还在行囊里。看看此刻最需要哪一种地点回应，再由你选路。' : '没有自动前进的唯一道路。先选一个你想去的地方，我会跟上。', 'mascot_shixiaoji_hint', 'steady')
            ];
        }
        if (kind === 'stay') {
            return [
                storyLine('旅者', 'player', '我想留在' + scene.name + '，把这里的线索继续看完。', 'player_scholar_alpha', 'steady'),
                storyLine('柿小吉', 'companion', '好。地点不变，我们把这里尚未说完的线索继续看清。', 'mascot_shixiaoji_observe', 'encouraging')
            ];
        }
        return [
            storyLine('旁白', 'narrator', scene.arrivalNarration || '沿着微光抵达' + scene.name + '。' + scene.shortDescription, '', 'narrative'),
            storyLine('旅者', 'player', scene.playerIntent || '这里的线索和营地不同，我想先仔细看看。', 'player_scholar_alpha', 'thinking'),
            storyLine('柿小吉', 'companion', scene.companionGuidance || '先观察，再行动；这里会给出不同的回应。', 'mascot_shixiaoji_observe', 'encouraging')
        ];
    }

    function travelCopy(state, sceneId) {
        var scene = sceneDefinition(sceneId);
        if (scene && scene.id !== 'camp') return '正沿着微光前往' + scene.name + '……';
        if (scene && scene.id === 'camp') return '循着营灯的暖光返回微光营地……';
        var copies = {
            SCROLL: '循着谷口的微光出发，卷轴就在前方……',
            TRIAL: '石径渐窄，墨色正在聚拢……',
            INTERLEAVE: '把刚才的规则带向陌生路段……',
            BOSS: '古塔的轮廓从雾中靠近……',
            TREASURY: '迷雾散去，灯火正沿山路亮起……',
            completed: '最后一束光回到了旅者手中……'
        };
        return copies[state] || '沿着微光继续前行……';
    }

    function SceneDirector() {
        this.token = 0;
        this.timers = [];
        this.waitResolvers = [];
        this.queue = [];
        this.queueIndex = 0;
        this.lineComplete = false;
        this.typingTimer = null;
        this.locked = false;
        this.skipRequested = false;
        this.preservedAnswer = '';
    }

    SceneDirector.prototype.emit = function (detail) {
        detail = detail || {};
        if (!detail.state && snapshot) detail.state = snapshot.state;
        document.dispatchEvent(new CustomEvent('shiguang:direction', { detail: detail }));
    };

    SceneDirector.prototype.motionScale = function () {
        if (document.hidden) return 0.02;
        return window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 0.02 : 1;
    };

    SceneDirector.prototype.schedule = function (callback, delay) {
        var self = this;
        var timer = window.setTimeout(function () {
            self.timers = self.timers.filter(function (item) { return item !== timer; });
            callback();
        }, Math.max(8, Math.round(delay * this.motionScale())));
        this.timers.push(timer);
        return timer;
    };

    SceneDirector.prototype.scheduleRealtime = function (callback, delay) {
        var self = this;
        var timer = window.setTimeout(function () {
            self.timers = self.timers.filter(function (item) { return item !== timer; });
            callback();
        }, Math.max(8, Math.round(delay)));
        this.timers.push(timer);
        return timer;
    };

    SceneDirector.prototype.wait = function (delay, token) {
        var self = this;
        if (this.skipRequested || token !== this.token) return Promise.resolve(token === this.token);
        return new Promise(function (resolve) {
            var finished = false;
            var resolver = null;
            var finish = function () {
                if (finished) return;
                finished = true;
                self.waitResolvers = self.waitResolvers.filter(function (item) { return item !== resolver; });
                resolve(token === self.token);
            };
            var timer = self.schedule(finish, delay);
            resolver = function () {
                window.clearTimeout(timer);
                self.timers = self.timers.filter(function (item) { return item !== timer; });
                finish();
            };
            self.waitResolvers.push(resolver);
        });
    };

    SceneDirector.prototype.cancel = function (reason) {
        this.token += 1;
        this.timers.splice(0).forEach(function (timer) { window.clearTimeout(timer); });
        this.waitResolvers.splice(0).forEach(function (resolve) { resolve(); });
        this.queue = [];
        this.queueIndex = 0;
        this.lineComplete = false;
        this.typingTimer = null;
        this.locked = false;
        this.skipRequested = false;
        if (reason) this.emit({ type: 'cancel', reason: reason });
    };

    SceneDirector.prototype.skipVisual = function () {
        this.skipRequested = true;
        this.waitResolvers.splice(0).forEach(function (resolve) { resolve(); });
        if (overlay) overlay.classList.add('is-transition-skipped');
    };

    SceneDirector.prototype.setPhase = function (phase, state) {
        if (!overlay) return;
        overlay.dataset.directionPhase = phase;
        this.emit({ type: 'phase', phase: phase, state: state || (snapshot && snapshot.state) });
    };

    SceneDirector.prototype.ensureShell = function () {
        if (!overlay || overlay.querySelector('[data-directed-stage]')) return;
        overlay.innerHTML = renderHeader() +
            '<main class="shiguang-stage" data-directed-stage="true">' +
            '<div class="shiguang-scene-host" data-scene-host="true"></div>' +
            '<div class="shiguang-travel" aria-live="polite" aria-hidden="true" inert><div class="shiguang-travel-motes"><i></i><i></i><i></i><i></i><i></i></div><span data-travel-copy>沿着微光继续前行……</span><button type="button" data-action="skip-transition">略过脚步</button></div>' +
            '<section class="shiguang-panel" data-panel-slot="true"></section></main>' +
            '<footer class="shiguang-credit">统一声音管理 · 庆祝音乐由用户提供 · 仅在主动开始后启用 · 后台或离开自动暂停</footer>';
    };

    SceneDirector.prototype.updateHeader = function () {
        if (!overlay) return;
        var current = overlay.querySelector('.shiguang-head');
        if (!current) return;
        var holder = document.createElement('div');
        holder.innerHTML = renderHeader();
        current.replaceWith(holder.firstElementChild);
    };

    SceneDirector.prototype.updateOverlayState = function (state, sceneId) {
        if (!overlay) return;
        overlay.dataset.state = String(state).toLowerCase();
        overlay.dataset.sceneId = sceneId || 'camp';
        overlay.dataset.school = (snapshot.archive || {}).magic_school || 'wood';
        overlay.dataset.bossStage = String(Number(snapshot.archive && snapshot.archive.boss_stage || 0));
        overlay.classList.toggle('is-success', snapshot.feedback && snapshot.feedback.kind === 'success');
        overlay.classList.toggle('is-review', snapshot.feedback && snapshot.feedback.kind === 'needs_review');
        if (enemyDirector) enemyDirector.syncOverlay(snapshot);
    };

    SceneDirector.prototype.protectImages = function (scene) {
        var self = this;
        Array.prototype.slice.call(scene.querySelectorAll('img')).forEach(function (image) {
            if (!image.getAttribute('src')) return;
            var isSceneArt = image.classList.contains('shiguang-scene-art-layer');
            var badgeAssetKind = image.dataset.badgeAsset || '';
            var onError = function () {
                image.hidden = true;
                if (badgeAssetKind) {
                    var rewardDisplay = image.closest('[data-reward-display]');
                    var fallback = rewardDisplay && rewardDisplay.querySelector('.shiguang-badge-fallback');
                    if (fallback) fallback.hidden = false;
                    if (rewardDisplay) rewardDisplay.classList.add('is-fallback');
                    self.emit({ type: 'asset-fallback', asset: image.src, assetKind: 'badge-' + badgeAssetKind });
                    return;
                }
                if (isSceneArt) {
                    image.dataset.layerLoaded = 'false';
                    scene.classList.add('has-missing-art-layer');
                    self.emit({
                        type: 'asset-fallback',
                        asset: image.src,
                        assetKind: 'scene-layer',
                        layerKind: image.dataset.layerKind,
                        sceneId: scene.dataset.sceneLocation
                    });
                    return;
                }
                scene.classList.add('has-missing-character');
                var fallback = document.createElement('span');
                fallback.className = 'shiguang-character-fallback';
                fallback.setAttribute('role', 'img');
                fallback.setAttribute('aria-label', image.alt || '角色暂时隐入雾中');
                fallback.textContent = '影';
                image.insertAdjacentElement('afterend', fallback);
                self.emit({ type: 'asset-fallback', asset: image.src, assetKind: 'character' });
            };
            image.addEventListener('error', onError, { once: true });
            image.addEventListener('load', function () {
                if (!isSceneArt) return;
                image.dataset.layerLoaded = 'true';
                self.emit({
                    type: 'asset-loaded',
                    asset: image.src,
                    assetKind: 'scene-layer',
                    layerKind: image.dataset.layerKind,
                    sceneId: scene.dataset.sceneLocation
                });
            }, { once: true });
            if (image.complete && !image.naturalWidth) onError();
            if (isSceneArt && image.complete && image.naturalWidth) image.dataset.layerLoaded = 'true';
        });
        var background = scene.style.getPropertyValue('--scene').match(/url\(['"]?(.*?)['"]?\)/);
        if (background && background[1]) {
            var fallback = scene.dataset.sceneBaseFallback ? ASSET + scene.dataset.sceneBaseFallback : '';
            var probeBackground = function (asset, isFallback) {
                var probe = new Image();
                var settled = false;
                var fail = function () {
                    if (settled) return;
                    settled = true;
                    self.emit({
                        type: 'asset-fallback',
                        asset: asset,
                        fallbackAsset: !isFallback ? fallback : '',
                        assetKind: 'scene-base',
                        sceneId: scene.dataset.sceneLocation
                    });
                    if (!isFallback && fallback && fallback !== asset) {
                        scene.style.setProperty('--scene', 'url("' + fallback + '")');
                        scene.dataset.sceneFallback = 'true';
                        probeBackground(fallback, true);
                        return;
                    }
                    scene.classList.add('has-missing-scene');
                };
                probe.onload = function () {
                    if (settled) return;
                    settled = true;
                    self.emit({
                        type: 'asset-loaded',
                        asset: asset,
                        assetKind: isFallback ? 'scene-base-fallback' : 'scene-base',
                        sceneId: scene.dataset.sceneLocation
                    });
                };
                probe.onerror = fail;
                probe.src = asset;
                self.scheduleRealtime(function () { if (!settled) fail(); }, 2500);
            };
            probeBackground(background[1], false);
        }
    };

    SceneDirector.prototype.createScene = function (state, sceneId) {
        var holder = document.createElement('div');
        holder.innerHTML = renderScene(state, sceneId);
        var scene = holder.firstElementChild;
        scene.classList.add('shiguang-directed-scene');
        scene.dataset.sceneKey = String(sceneId || 'camp');
        this.protectImages(scene);
        if (enemyDirector) enemyDirector.decorateScene(scene, snapshot);
        return scene;
    };

    SceneDirector.prototype.roleNodes = function (scene) {
        return [
            { character: 'player', nodes: Array.prototype.slice.call(scene.querySelectorAll('.shiguang-player')) },
            { character: 'companion', nodes: Array.prototype.slice.call(scene.querySelectorAll('.shiguang-mascot')) },
            { character: 'enemy', nodes: Array.prototype.slice.call(scene.querySelectorAll('.shiguang-enemy, .shiguang-boss-character, .shiguang-boss-seals')) },
            { character: 'object', nodes: Array.prototype.slice.call(scene.querySelectorAll('.shiguang-item')) }
        ];
    };

    SceneDirector.prototype.setRoleState = function (scene, character, performance, state) {
        var role = this.roleNodes(scene).find(function (item) { return item.character === character; });
        if (!role || !role.nodes.length) return;
        role.nodes.forEach(function (node) { node.dataset.performanceState = performance; });
        this.emit({ type: 'character', character: character, performance: performance, state: state });
    };

    SceneDirector.prototype.setAllRoles = function (scene, performance, state) {
        var self = this;
        this.roleNodes(scene).forEach(function (role) {
            if (role.nodes.length) self.setRoleState(scene, role.character, performance, state);
        });
    };

    SceneDirector.prototype.renderClosure = function () {
        var panel = overlay && overlay.querySelector('[data-panel-slot]');
        if (!panel) return;
        panel.className = 'shiguang-panel is-story-mode is-scene-closing';
        panel.innerHTML = '<div class="shiguang-transition-line"><small>柿小吉</small><p>这里的微光已经收好，我们沿着回应亮起的路继续。</p></div>';
    };

    SceneDirector.prototype.fullTransition = async function (fromState, toState, token, fromSceneId, toSceneId) {
        var stage = overlay.querySelector('[data-directed-stage]');
        var host = overlay.querySelector('[data-scene-host]');
        var travel = overlay.querySelector('.shiguang-travel');
        var oldScene = host.querySelector('.shiguang-scene');
        if (fromState === 'BOSS' && toState === 'TREASURY' && oldScene && oldScene.dataset.sceneLocation === 'boss_core' && oldScene.dataset.enemyFinaleComplete !== 'true') {
            var restoredBossScene = this.createScene('BOSS', 'boss_core');
            restoredBossScene.classList.add('is-boss-restored');
            this.preservePersistentCharacters(oldScene, restoredBossScene);
            oldScene.replaceWith(restoredBossScene);
            oldScene = restoredBossScene;
            this.setAllRoles(oldScene, 'idle', 'BOSS');
            this.setPhase('boss-restored', 'BOSS');
            this.emit({ type: 'boss-restored', state: 'BOSS', sceneId: 'boss_core' });
            if (!await this.wait(900, token)) return;
        }
        this.renderClosure();
        stage.classList.add('is-departing');
        this.setPhase('departing', fromState);
        if (oldScene) {
            oldScene.dataset.sceneState = 'exit';
            this.setRoleState(oldScene, 'enemy', 'exiting', fromState);
            this.setRoleState(oldScene, 'object', 'exiting', fromState);
            await this.wait(70, token);
            this.setRoleState(oldScene, 'companion', 'exiting', fromState);
            this.setRoleState(oldScene, 'player', 'exiting', fromState);
            oldScene.classList.add('is-scene-outgoing');
        }
        if (!await this.wait(250, token)) return;

        this.setPhase('travel', toState);
        stage.classList.remove('is-departing');
        stage.classList.add('is-travelling');
        travel.querySelector('[data-travel-copy]').textContent = travelCopy(toState, toSceneId);
        travel.classList.add('is-active');
        travel.setAttribute('aria-hidden', 'false');
        travel.inert = false;
        if (!await this.wait(480, token)) return;

        var nextScene = this.createScene(toState, toSceneId);
        nextScene.classList.add('is-scene-entering');
        this.setAllRoles(nextScene, 'hidden', toState);
        host.appendChild(nextScene);
        this.setPhase('arriving', toState);
        nextScene.classList.add('is-scene-visible');
        // B：背景空镜——先让玩家看清新场景（镜头聚焦），角色暂不登场
        this.emit({ type: 'scene', event: 'background-arrived', state: toState, sceneId: toSceneId });
        if (!await this.wait(360, token)) return;
        // A：立绘独立进场——旅者 → 柿小吉 → 敌人/物品，逐个亮相
        this.setRoleState(nextScene, 'player', 'entering', toState);
        if (!await this.wait(240, token)) return;
        this.setRoleState(nextScene, 'companion', 'entering', toState);
        if (!await this.wait(220, token)) return;
        this.setRoleState(nextScene, 'enemy', 'entering', toState);
        this.setRoleState(nextScene, 'object', 'entering', toState);
        if (!await this.wait(320, token)) return;

        if (oldScene && oldScene.isConnected) oldScene.remove();
        nextScene.classList.remove('is-scene-entering', 'is-scene-visible');
        this.setAllRoles(nextScene, 'idle', toState);
        stage.classList.remove('is-travelling');
        travel.classList.remove('is-active');
        travel.setAttribute('aria-hidden', 'true');
        travel.inert = true;
        await this.wait(300, token);
    };

    SceneDirector.prototype.preservePersistentCharacters = function (current, next) {
        if (!current || !next) return;
        ['.shiguang-player', '.shiguang-mascot', '.shiguang-enemy'].forEach(function (selector) {
            var previous = current.querySelector(selector);
            var replacement = next.querySelector(selector);
            if (!previous || !replacement) return;
            var replacementSource = replacement.getAttribute('src');
            if (replacementSource && previous.getAttribute('src') !== replacementSource) previous.setAttribute('src', replacementSource);
            previous.alt = replacement.alt;
            previous.className = replacement.className;
            previous.hidden = replacement.hidden;
            replacement.replaceWith(previous);
        });
    };

    SceneDirector.prototype.localTransition = async function (state, token, sceneId) {
        var stage = overlay.querySelector('[data-directed-stage]');
        var host = overlay.querySelector('[data-scene-host]');
        var current = host.querySelector('.shiguang-scene');
        this.renderClosure();
        this.setPhase('local', state);
        stage.classList.add('is-local-shift');
        if (current) {
            this.setAllRoles(current, 'thinking', state);
            current.classList.add('is-local-dimming');
        }
        if (!await this.wait(120, token)) return;
        var nextScene = this.createScene(state, sceneId);
        nextScene.classList.add('is-local-entering');
        this.preservePersistentCharacters(current, nextScene);
        this.setAllRoles(nextScene, 'idle', state);
        if (current) current.replaceWith(nextScene); else host.appendChild(nextScene);
        if (!await this.wait(260, token)) return;
        nextScene.classList.remove('is-local-entering');
        stage.classList.remove('is-local-shift');
    };

    SceneDirector.prototype.mountStable = function (state, sceneId) {
        var host = overlay.querySelector('[data-scene-host]');
        var scene = this.createScene(state, sceneId);
        if (state === 'TREASURY' || state === 'completed') {
            var restoredReward = scene.querySelector('[data-reward-display]');
            if (restoredReward) restoredReward.classList.add('is-revealed');
        }
        this.setAllRoles(scene, 'idle', state);
        host.replaceChildren(scene);
        if (enemyDirector) enemyDirector.restoreScene(scene, snapshot);
        overlay.dataset.transitionKind = 'restore';
        overlay.classList.add('is-restoring');
        this.renderInteraction('');
        var self = this;
        this.schedule(function () {
            if (overlay) overlay.classList.remove('is-restoring');
            self.emit({ type: 'restore-complete', state: state, sceneId: sceneId });
        }, 360);
    };

    SceneDirector.prototype.revealReward = function () {
        if (!overlay || !snapshot || snapshot.state !== 'TREASURY') return;
        var display = overlay.querySelector('[data-reward-display]');
        if (!display || display.classList.contains('is-revealed')) return;
        display.classList.remove('is-pending');
        display.classList.add('is-revealed');
        this.emit({ type: 'reward-reveal', catalogId: snapshot.reward && snapshot.reward.catalog_id, state: 'TREASURY' });
    };

    SceneDirector.prototype.speakingRole = function (speakerType, tone) {
        if (!overlay) return;
        var scene = overlay.querySelector('.shiguang-scene');
        if (!scene) return;
        this.setAllRoles(scene, 'idle', snapshot.state);
        var character = speakerType === 'companion' ? 'companion' : speakerType === 'player' ? 'player' : speakerType === 'enemy' ? 'enemy' : speakerType === 'object' ? 'object' : '';
        if (character) this.setRoleState(scene, character, tone === 'encouraging' ? 'encouraging' : 'speaking', snapshot.state);
        if (enemyDirector) enemyDirector.speaking(speakerType, tone, snapshot);
    };

    SceneDirector.prototype.renderDialogueLine = function () {
        if (!overlay || !this.queue.length) return;
        var self = this;
        var line = this.queue[this.queueIndex];
        var panel = overlay.querySelector('[data-panel-slot]');
        this.lineComplete = false;
        panel.className = 'shiguang-panel is-story-mode';
        panel.innerHTML = '<div class="shiguang-dialogue tone-' + escapeHtml(line.tone) + '" data-queue-index="' + this.queueIndex + '" data-queue-length="' + this.queue.length + '" data-line-complete="false" data-speaker-type="' + escapeHtml(line.speakerType) + '">' +
            '<div class="shiguang-dialogue-speaker"><span>' + (line.speakerType === 'narrator' ? '叙' : line.speaker.slice(0, 1)) + '</span><div><small>' + escapeHtml(line.speaker) + '</small><b>' + (line.speakerType === 'narrator' ? '旅途旁白' : '正在说话') + '</b></div></div>' +
            '<p data-dialogue-text aria-live="off"></p><span class="shiguang-sr-only" data-dialogue-announcement aria-live="polite"></span><div class="shiguang-dialogue-controls"><span>' + (this.queueIndex + 1) + ' / ' + this.queue.length + '</span>' +
            (line.skippable ? '<button type="button" data-action="dialogue-skip" class="shiguang-dialogue-skip">跳过余下对白</button>' : '') +
            '<button type="button" data-action="dialogue-next" class="shiguang-primary">显示全文</button></div></div>';
        panel.scrollTop = 0;
        this.speakingRole(line.speakerType, line.tone);
        this.emit({ type: 'dialogue', event: 'line-start', index: this.queueIndex, speakerType: line.speakerType, state: snapshot.state });
        focusInside('[data-action="dialogue-next"]');

        var textNode = panel.querySelector('[data-dialogue-text]');
        var characters = Array.from(line.text);
        var position = 0;
        if (this.motionScale() < .1) {
            textNode.textContent = line.text;
            this.completeLine();
            return;
        }
        var typeNext = function () {
            if (!overlay || !textNode.isConnected || self.lineComplete) return;
            position += 1;
            textNode.textContent = characters.slice(0, position).join('');
            if (position >= characters.length) {
                self.completeLine();
                return;
            }
            self.typingTimer = self.schedule(typeNext, 24);
        };
        this.typingTimer = this.schedule(typeNext, 24);
    };

    SceneDirector.prototype.completeLine = function () {
        if (!overlay || !this.queue.length || this.lineComplete) return;
        this.lineComplete = true;
        var line = this.queue[this.queueIndex];
        var dialogue = overlay.querySelector('.shiguang-dialogue');
        if (!dialogue) return;
        var text = dialogue.querySelector('[data-dialogue-text]');
        var announcement = dialogue.querySelector('[data-dialogue-announcement]');
        var next = dialogue.querySelector('[data-action="dialogue-next"]');
        if (text) text.textContent = line.text;
        if (announcement) announcement.textContent = line.speaker + '：' + line.text;
        dialogue.dataset.lineComplete = 'true';
        if (next) next.textContent = this.queueIndex >= this.queue.length - 1 ? '开始行动' : '下一句';
        if (snapshot && snapshot.state === 'TREASURY' && this.queueIndex >= this.queue.length - 1) this.revealReward();
        this.emit({ type: 'dialogue', event: 'line-complete', index: this.queueIndex, speakerType: line.speakerType, state: snapshot.state });
    };

    SceneDirector.prototype.nextDialogue = function () {
        if (!this.queue.length) return;
        if (!this.lineComplete) {
            this.completeLine();
            return;
        }
        this.queueIndex += 1;
        if (this.queueIndex >= this.queue.length) {
            this.finishDialogue();
            return;
        }
        this.renderDialogueLine();
    };

    SceneDirector.prototype.skipDialogue = function () {
        if (!this.queue.length || this.queue.some(function (line) { return !line.skippable; })) return;
        this.emit({ type: 'dialogue', event: 'queue-skipped', index: this.queueIndex, state: snapshot.state });
        this.finishDialogue();
    };

    SceneDirector.prototype.startDialogue = function (lines, preservedAnswer) {
        this.queue = lines.slice();
        this.queueIndex = 0;
        this.lineComplete = false;
        this.preservedAnswer = preservedAnswer || '';
        this.locked = true;
        this.setPhase('dialogue', snapshot.state);
        this.emit({ type: 'interaction', status: 'locked', state: snapshot.state });
        if (!this.queue.length) {
            this.finishDialogue();
            return;
        }
        this.renderDialogueLine();
    };

    SceneDirector.prototype.finishDialogue = function () {
        this.revealReward();
        this.queue = [];
        this.queueIndex = 0;
        this.lineComplete = false;
        this.typingTimer = null;
        this.renderInteraction(this.preservedAnswer);
    };

    function guideLine() {
        if (!snapshot) return '';
        var state = snapshot.state;
        var archive = snapshot.archive || {};
        var attempts = snapshot.attempts || {};
        if (state === 'SCROLL') {
            if (archive.teaching_passed) return '下一步：点【收好卷轴，前去应战】，去石径上会会墨迹兽。';
            if (scrollPageIndex === 'done') return '下一步：先答对上面的理解确认题，【前去应战】才会亮起。';
            return '先读完卷轴正文，逐页翻看，最后进入理解确认。';
        }
        if (state === 'TRIAL') {
            if (!archive.selected_strategy) return '下一步：选一种出招方式（讲 / 忆 / 练 / 图），决定这题的路线。';
            if (Number(attempts.TRIAL || 0) >= 2) return '下一步：它换了种问法——答对这一题就能通过初次遭遇。';
            return '下一步：回答墨迹兽的题目，留下一条有效证据。';
        }
        if (state === 'INTERLEAVE') return '下一步：答对迁移题，古塔就会为你亮起。';
        if (state === 'BOSS') {
            var stage = Number(archive.boss_stage || 0);
            var labels = ['解释', '辨析', '迁移'];
            return '下一步：解开「' + labels[stage] + '」封印（' + (stage + 1) + '/3），迷雾就会退去。';
        }
        if (state === 'TREASURY') return '下一步：看看这局的战绩回顾，再把旅印收入宝库。';
        return '';
    }

    SceneDirector.prototype.renderInteraction = function (preservedAnswer) {
        if (!overlay || !snapshot) return;
        var panel = overlay.querySelector('[data-panel-slot]');
        if (!panel) return;
        panel.className = 'shiguang-panel';
        // 分页叙事固定标题栏：仅卷轴教学卡（未确认时）显示，置顶、不随正文滚动。
        var titleBar = '';
        if (snapshot.state === 'SCROLL' && !(snapshot.archive || {}).teaching_passed) {
            titleBar = '<div class="shiguang-scroll-title"><span>教 · 拾光卷轴</span><h2>先带走一条可复用规则</h2><p>' + escapeHtml((snapshot.content || {}).topic || '') + '</p></div>';
        }
        var guide = guideLine() ? '<div class="shiguang-guide" role="status"><span>指南</span><p>' + escapeHtml(guideLine()) + '</p></div>' : '';
        panel.innerHTML = '<div class="shiguang-paper-ornament" aria-hidden="true"></div>' + titleBar + guide + '<div class="shiguang-interaction is-revealing">' + scenePreludeHtml() + renderBody() + feedbackHtml() + '</div>';
        if (preservedAnswer) {
            var preserved = document.createElement('aside');
            preserved.className = 'shiguang-preserved-answer';
            preserved.dataset.preservedAnswer = 'true';
            preserved.innerHTML = '<small>刚才的回答仍在这里</small><p>' + escapeHtml(preservedAnswer) + '</p>';
            var interaction = panel.querySelector('.shiguang-interaction');
            if (interaction) interaction.insertBefore(preserved, interaction.firstChild);
        }
        panel.scrollTop = 0;
        this.revealReward();
        var field = panel.querySelector('.shiguang-answer textarea');
        if (field && preservedAnswer) field.value = preservedAnswer;
        this.locked = false;
        this.setPhase('interactive', snapshot.state);
        this.emit({ type: 'interaction', status: 'unlocked', state: snapshot.state });
        focusInside(field ? '.shiguang-answer textarea' : '.shiguang-panel button:not([disabled])');
    };

    SceneDirector.prototype.lockInteraction = function (trigger) {
        if (!overlay) return;
        this.locked = true;
        var interaction = overlay.querySelector('.shiguang-interaction');
        if (interaction) interaction.classList.add('is-submitting');
        if (trigger) trigger.classList.add('is-selected');
        Array.prototype.slice.call(overlay.querySelectorAll('.shiguang-panel button, .shiguang-panel textarea')).forEach(function (control) { control.disabled = true; });
        this.emit({ type: 'interaction', status: 'submitting', state: snapshot && snapshot.state });
    };

    SceneDirector.prototype.showFailure = function (message) {
        if (!overlay) return;
        var interaction = overlay.querySelector('.shiguang-interaction');
        if (interaction) interaction.classList.remove('is-submitting');
        Array.prototype.slice.call(overlay.querySelectorAll('.shiguang-panel button, .shiguang-panel textarea')).forEach(function (control) { control.disabled = false; });
        Array.prototype.slice.call(overlay.querySelectorAll('.is-selected')).forEach(function (control) { control.classList.remove('is-selected'); });
        var panel = overlay.querySelector('[data-panel-slot]');
        var feedback = panel.querySelector('.shiguang-feedback');
        if (!feedback) {
            feedback = document.createElement('div');
            feedback.className = 'shiguang-feedback needs_review';
            feedback.setAttribute('role', 'alert');
            panel.appendChild(feedback);
        }
        feedback.innerHTML = '<strong>这一步没有完成</strong><p>' + escapeHtml(message) + '</p>';
        this.locked = false;
        this.setPhase('interactive', snapshot && snapshot.state);
        this.emit({ type: 'interaction', status: 'retry', state: snapshot && snapshot.state });
    };

    SceneDirector.prototype.present = async function (options) {
        options = options || {};
        if (!overlay || !snapshot) return;
        this.cancel();
        this.skipRequested = false;
        this.locked = true;
        var token = this.token;
        var state = snapshot.state === 'completed' ? 'completed' : snapshot.state;
        var routeSceneId = options.sceneId || routeState && routeState.currentSceneId || 'camp';
        var fromRouteSceneId = options.fromSceneId || '';
        var sceneId = presentationSceneId(state, routeSceneId);
        var fromSceneId = fromRouteSceneId ? presentationSceneId(options.fromState || '', fromRouteSceneId) : '';
        preloadForState(state);
        this.ensureShell();
        this.updateOverlayState(state, sceneId);
        this.updateHeader();
        audio.setScene(sceneDefinition(sceneId).musicKey || audioScene(snapshot.state));
        panelMode = options.panelMode || 'learning';

        if (options.restore) {
            this.mountStable(state, sceneId);
            return;
        }

        var fromState = options.fromState || '';
        var kind = fromSceneId && fromSceneId === sceneId ? 'local' : 'full';
        overlay.dataset.transitionKind = kind;
        this.emit({ type: 'transition', from: fromState, to: state, fromSceneId: fromSceneId, sceneId: sceneId, kind: kind, state: state });
        if (kind === 'local') await this.localTransition(state, token, sceneId);
        else await this.fullTransition(fromState, state, token, fromSceneId, sceneId);
        if (!active || token !== this.token || !overlay) return;
        if (enemyDirector) await enemyDirector.introduce(overlay.querySelector('.shiguang-scene'), snapshot);
        if (!active || token !== this.token || !overlay) return;
        var lines = options.storyLines || storyFor(state, { local: kind === 'local' });
        // 跨幕转场：插入一幕的开场对白，让剧情有明确的"小节目标"。
        if (fromState && fromState !== state && actForState(state).id > actForState(fromState).id) {
            var act = actForState(state);
            var actCopy = {
                1: '第 1 幕 · 教：拾起卷轴，看懂规则。',
                2: '第 2 幕 · 练：选择出招，让初次遭遇留下第一份真实证据。',
                3: '第 3 幕 · 战：解释、辨析与迁移，三重封印，终局守关。',
                4: '第 4 幕 · 收：证据回顾，把旅印收入宝库。',
            }[act.id] || '';
            lines = [storyLine('旁白', 'narrator', actCopy || ('第 ' + act.id + ' 幕 · ' + act.name + '：' + act.goal), '', 'narrative')].concat(lines);
        }
        this.startDialogue(lines, options.preservedAnswer || '');
    };

    function renderGame(options) {
        if (!overlay || !snapshot) return Promise.resolve();
        if (!director) director = new SceneDirector();
        return director.present(options || {});
    }

    function spellSelectionMarkup(currentId) {
        if (!spellRegistry || !spellRegistry.all || !spellRegistry.resolve) return '';
        var current = spellRegistry.resolve(currentId);
        return '<div class="shiguang-spell-ritual">' +
            '<div class="shiguang-schools" role="radiogroup" aria-label="选择本次法术"><span>选择本次法术</span>' +
            spellRegistry.all.map(function (school) {
                return '<label data-school="' + escapeHtml(school.id) + '" title="' + escapeHtml(school.displayName + ' · ' + school.skillName) + '">' +
                    '<input type="radio" name="school" value="' + escapeHtml(school.id) + '" ' + (current.id === school.id ? 'checked' : '') + '>' +
                    '<img src="' + escapeHtml(spellRegistry.assetUrl(school.emblem)) + '" alt="" aria-hidden="true"><span><b>' + escapeHtml(school.displayName) + '</b><small>' + escapeHtml(school.skillName) + '</small></span></label>';
            }).join('') + '</div>' +
            '<aside class="spell-selection-preview" data-preview-school="' + escapeHtml(current.id) + '" aria-live="polite">' +
                '<div class="spell-selection-preview-art"><img src="' + escapeHtml(spellRegistry.assetUrl(current.selectionPreview)) + '" alt="' + escapeHtml(current.displayName) + '蓄力预览"></div>' +
                '<div><small>法术印记正在回应</small><h3>' + escapeHtml(current.displayName) + ' · ' + escapeHtml(current.skillName) + '</h3><p>' + escapeHtml(current.ritualLine) + '</p></div>' +
            '</aside>' +
            '<div class="shiguang-spell-confirm"><button type="button" class="shiguang-secondary" data-action="confirm-school">确认选择 ' + escapeHtml(current.displayName) + '</button><span data-school-confirmation>先确认法术，再点亮营灯。</span></div></div>';
    }

    function previewSchoolRitual(schoolId, selectsCandidate) {
        if (!overlay || !spellRegistry || !spellRegistry.has(schoolId)) return;
        var school = spellRegistry.resolve(schoolId);
        var preview = overlay.querySelector('.spell-selection-preview');
        if (preview) {
            preview.dataset.previewSchool = school.id;
            var image = preview.querySelector('img');
            if (image) {
                image.src = spellRegistry.assetUrl(school.selectionPreview);
                image.alt = school.displayName + '蓄力预览';
            }
            var title = preview.querySelector('h3');
            var copy = preview.querySelector('p');
            if (title) title.textContent = school.displayName + ' · ' + school.skillName;
            if (copy) copy.textContent = school.ritualLine;
        }
        overlay.dataset.school = school.id;
        spellRegistry.preloadSelection(school.id);
        if (spellDirector) {
            var scene = overlay.querySelector('.shiguang-prepare .shiguang-scene');
            if (scene) spellDirector.previewSchool(school.id, scene);
        }
        if (!selectsCandidate) return;
        schoolConfirmed = false;
        var confirm = overlay.querySelector('[data-action="confirm-school"]');
        var startButton = overlay.querySelector('[data-action="start"]');
        var status = overlay.querySelector('[data-school-confirmation]');
        if (confirm) {
            confirm.disabled = false;
            confirm.textContent = '确认选择 ' + school.displayName;
        }
        if (startButton) startButton.disabled = true;
        if (status) status.textContent = '柿小吉：' + school.ritualLine;
    }

    async function confirmSchool(button) {
        if (!overlay || !spellRegistry || busy) return;
        var schoolField = overlay.querySelector('input[name="school"]:checked');
        var status = overlay.querySelector('[data-school-confirmation]');
        var startButton = overlay.querySelector('[data-action="start"]');
        if (!schoolField || !spellRegistry.has(schoolField.value)) return;
        var school = spellRegistry.resolve(schoolField.value);
        button.disabled = true;
        if (status) status.textContent = '印记正在回应……';
        var scene = overlay.querySelector('.shiguang-prepare .shiguang-scene');
        if (spellDirector && scene) await spellDirector.confirmSelection(school.id, scene);
        if (!active || !overlay) return;
        schoolConfirmed = true;
        spellRegistry.preloadCast(school.id);
        button.textContent = '已确认 · ' + school.displayName;
        if (status) status.textContent = '柿小吉：印记已经记住你了，出发吧。';
        if (startButton) startButton.disabled = false;
        overlay.dataset.confirmedSchool = school.id;
    }

    function renderPrepare(step) {
        var context = inspectContext();
        var stored = {};
        try { stored = JSON.parse(localStorage.getItem('shiguang_manyou_prepare') || '{}'); } catch (_) {}
        prepareStep = step || prepareStep || 1;
        var topic = prepareDraft.topic || stored.topic || context.topic;
        var preferredSource = context.materials.length ? 'material' : context.artifacts.length ? 'artifact' : context.threadId ? 'current_conversation' : 'manual';
        var selectedSource = prepareDraft.source === 'manual' && !prepareDraft.topic ? preferredSource : prepareDraft.source;
        var currentSchool = spellRegistry && spellRegistry.has(stored.school) ? stored.school : 'wood';
        schoolConfirmed = false;
        overlay.dataset.state = 'prepare';
        overlay.dataset.prepareStep = String(prepareStep);
        overlay.dataset.school = currentSchool;
        delete overlay.dataset.confirmedSchool;
        var firstStep = '<div class="shiguang-copy shiguang-prepare-step"><small>第一步 · 选择学习主题</small><div class="shiguang-guide"><span>指南</span><p>写下你想学的主题，点【下一步】选择身份与法术。</p></div><div class="shiguang-kicker">柿小吉已经在丰穗谷口等你</div><h1>今天想探索什么？</h1><p>选择当前学习内容，或者直接写下一个主题。接下来再选择你的探索方式。</p><label class="shiguang-field"><span>本次主题</span><input id="shiguangTopic" maxlength="120" value="' + escapeHtml(topic) + '" placeholder="例如：用水果理解 KNN 分类" required></label><div class="shiguang-sources" role="radiogroup" aria-label="内容来源">' + sourceCard('current_conversation','当前会话',context.threadId?'继续这次对话':'当前暂无会话',!!context.threadId,selectedSource) + sourceCard('material','上传资料',context.materials.length?context.materials.length+' 份资料可用':'当前没有资料',!!context.materials.length,selectedSource) + sourceCard('artifact','最近产物',context.artifacts.length?context.artifacts.length+' 项成果可用':'当前没有产物',!!context.artifacts.length,selectedSource) + sourceCard('manual','自定主题','从你写下的主题开始',true,selectedSource) + '</div><button class="shiguang-primary" data-action="prepare-next"><span>下一步：选择探索方式</span><i>→</i></button><p class="shiguang-error" id="shiguangError" role="alert"></p></div>';
        var identityMarkup = '<div class="shiguang-identity-select"><span>选择你的身份</span><div class="shiguang-identities" role="radiogroup" aria-label="选择身份">' + IDENTITIES.map(function (item) {
            var profile = IDENTITY_PROFILES[item.id] || {};
            return '<label data-identity="' + item.id + '"><input type="radio" name="identity" value="' + item.id + '" ' + (prepareDraft.identity === item.id ? 'checked' : '') + '><strong>' + item.name + '</strong><small>' + escapeHtml(profile.style || '') + '</small></label>';
        }).join('') + '</div></div>';
        var secondStep = '<div class="shiguang-copy shiguang-prepare-step"><small>第二步 · 选择探索方式</small><div class="shiguang-guide"><span>指南</span><p>选好身份与法术，点亮营灯即可开始漫游。</p></div><div class="shiguang-kicker">本次主题</div><h1>' + escapeHtml(topic || '你的学习主题') + '</h1><p>选择一种身份进入主题世界。身份会改变你的对话与剧情走向。</p>' + identityMarkup + '<input id="shiguangTopic" type="hidden" value="' + escapeHtml(topic) + '"><input name="source" type="hidden" value="' + escapeHtml(selectedSource) + '">' + spellSelectionMarkup(currentSchool) + '<div class="shiguang-step-actions"><button class="shiguang-secondary" data-action="prepare-back">返回上一步</button><button class="shiguang-primary" data-action="start" disabled><span>点亮营灯，开始漫游</span><i>✦</i></button></div><p class="shiguang-error" id="shiguangError" role="alert"></p>' + rewardShelfMarkup(true) + '</div>';
        overlay.innerHTML = renderHeader() + '<main class="shiguang-stage shiguang-prepare"><div class="shiguang-scene shiguang-scene-prepare" style="--scene:url(\'' + ASSET + 'scenes/scene_camp_prepare.png\')"><div class="shiguang-scene-wash"></div><div class="shiguang-camp-lantern" aria-hidden="true"></div><img class="shiguang-player shiguang-white-composite" src="' + ASSET + 'characters/player_scholar_alpha.png" alt="准备出发的旅者"><img class="shiguang-mascot" src="' + ASSET + 'characters/mascot_shixiaoji_welcome.png" alt="柿小吉"><div class="shiguang-prepare-promise"><span>主题驱动的学习世界</span><b>选择主题 · 进入场景 · 用答案改变剧情</b></div></div><section class="shiguang-panel"><div class="shiguang-paper-ornament" aria-hidden="true"></div>' + (prepareStep === 1 ? firstStep : secondStep) + '</section></main><footer class="shiguang-credit">声音将在你主动开始后启用，可随时静音或调节音量。</footer>';
        if (spellRegistry) spellRegistry.preloadSelection(currentSchool);
        if (spellDirector) {
            var scene = overlay.querySelector('.shiguang-prepare .shiguang-scene');
            if (scene) spellDirector.previewSchool(currentSchool, scene);
        }
        focusInside(prepareStep === 1 ? '#shiguangTopic' : '[data-action="confirm-school"]');
    }

    function goToPrepareStep(step) { var topicField=overlay&&overlay.querySelector('#shiguangTopic');var sourceField=overlay&&overlay.querySelector('input[name="source"]:checked');var identityField=overlay&&overlay.querySelector('input[name="identity"]:checked');var error=overlay&&overlay.querySelector('#shiguangError');if(step===2){var topic=topicField?topicField.value.trim():'';if(topic.length<2){if(error)error.textContent='请先写下至少两个字的学习主题。';return;}prepareDraft.topic=topic;prepareDraft.source=sourceField?sourceField.value:'manual';prepareDraft.identity=identityField?identityField.value:'wandering_mage';}prepareStep=step;renderPrepare(step); }

    function sourceCard(id, title, text, available, selected) {
        return '<label class="' + (!available ? 'disabled' : '') + '"><input type="radio" name="source" value="' + id + '" ' + (id === selected ? 'checked' : '') + ' ' + (!available ? 'disabled' : '') + '><strong>' + title + '</strong><small>' + text + '</small></label>';
    }

    async function start() {
        if (busy) return;
        var topicField = overlay.querySelector('#shiguangTopic');
        var schoolField = overlay.querySelector('input[name="school"]:checked');
        var sourceField = overlay.querySelector('input[name="source"]:checked');
        var error = overlay.querySelector('#shiguangError');
        var topic = topicField ? topicField.value.trim() : '';
        if (topic.length < 2) { if (error) error.textContent = '请先写下至少两个字的真实学习主题。'; return; }
        if (!schoolField || !schoolConfirmed || overlay.dataset.confirmedSchool !== schoolField.value) {
            if (error) error.textContent = '请先确认本次法术印记，再点亮营灯。';
            return;
        }
        busy = true; overlay.classList.add('is-busy'); audio.ensure(); audio.setScene('prepare');
        audio.tone(392, 0.45, 0.035, 'sine');
        window.setTimeout(function () { audio.tone(523.25, 0.62, 0.03, 'sine'); }, 120);
        var context = inspectContext();
        var source = sourceField ? sourceField.value : 'manual';
        var ids = source === 'material' ? context.materials : source === 'artifact' ? context.artifacts : [];
        var identityField = overlay.querySelector('input[name="identity"]:checked');
        var identityId = identityField ? identityField.value : (prepareDraft.identity || 'wandering_mage');
        localStorage.setItem('shiguang_manyou_prepare', JSON.stringify({ topic: topic, school: schoolField.value }));
        try {
            var started = unwrap(await api.startHarvestAdventure(topic, schoolField.value, ids, { source_type: source, force_new: true, identity_id: identityId }));
            if (!active) return;
            forceNewOnStart = false;
            scrollPageIndex = 0;
            snapshot = started;
            if (spellRegistry) spellRegistry.preloadCast((snapshot.archive || {}).magic_school || schoolField.value);
            routeState = cleanRoute(snapshot.run_id);
            saveRoute();
            await renderGame({
                fromState: 'PREPARE',
                fromSceneId: '',
                sceneId: 'camp',
                panelMode: 'learning'
            });
        } catch (reason) {
            if (error) error.textContent = reason && reason.message ? reason.message : '暂时无法开始，请稍后重试。';
        } finally { busy = false; if (overlay) overlay.classList.remove('is-busy'); }
    }

    async function advance(action, payload, trigger) {
        if (busy || !snapshot) return;
        var previousState = snapshot.state;
        var previousBossStage = Number(snapshot.archive && snapshot.archive.boss_stage || 0);
        var currentField = overlay && overlay.querySelector('.shiguang-answer textarea');
        var preservedAnswer = currentField ? currentField.value : payload && payload.answer ? payload.answer : '';
        busy = true; overlay.classList.add('is-busy');
        if (director) director.lockInteraction(trigger);
        if (enemyDirector) enemyDirector.beforeAction(action, snapshot);
        try {
            var nextSnapshot = unwrap(await api.advanceHarvestAdventure(snapshot.run_id, action, payload || {}));
            var resultKind = nextSnapshot && nextSnapshot.feedback && nextSnapshot.feedback.kind || '';
            var bossFinalSuccess = action === 'submit' && previousState === 'BOSS' && previousBossStage >= 2 && nextSnapshot.state === 'TREASURY' && resultKind === 'success';
            var isTeachingCheck = action === 'submit' && previousState === 'SCROLL';
            if (enemyDirector) enemyDirector.observeAction(action, snapshot, nextSnapshot);
            if (!isTeachingCheck && action === 'submit' && spellDirector && (resultKind === 'success' || resultKind === 'needs_review')) {
                await spellDirector.playResult({
                    host: overlay.querySelector('.shiguang-stage'),
                    school: (nextSnapshot.archive || {}).magic_school || (snapshot.archive || {}).magic_school || 'wood',
                    resultKind: resultKind,
                    bossFinal: bossFinalSuccess,
                    preservedAnswer: preservedAnswer
                });
            }
            var currentScene = overlay && overlay.querySelector('.shiguang-scene');
            if (!isTeachingCheck && action === 'submit' && enemyDirector && resultKind === 'needs_review') {
                await enemyDirector.playWrong(currentScene, snapshot, nextSnapshot);
            } else if (action === 'submit' && enemyDirector && resultKind === 'success') {
                await enemyDirector.playSuccess(currentScene, snapshot, nextSnapshot);
            }
            snapshot = nextSnapshot;
            if (routeState) {
                // v3 线性流程：不再进入“选路”面板，恢复时直接回到当前节点。
                routeState.pendingChoice = false;
                saveRoute();
            }
            if (snapshot.state === 'completed') clearStoredRoute();
            if (!active) return;
            if (snapshot.feedback && snapshot.feedback.kind === 'success') {
                audio.tone(523.25, 0.45, 0.045, 'sine');
                window.setTimeout(function () { audio.tone(659.25, 0.62, 0.035, 'sine'); }, 110);
            } else if (snapshot.feedback && snapshot.feedback.kind === 'needs_review') {
                audio.tone(196, 0.32, 0.026, 'triangle');
            } else if (snapshot.state === 'BOSS') {
                audio.tone(146.83, 0.5, 0.032, 'triangle');
            }
            await renderGame({
                fromState: previousState,
                fromSceneId: routeState ? routeState.currentSceneId : 'camp',
                sceneId: routeState ? routeState.currentSceneId : 'camp',
                panelMode: 'learning',
                preservedAnswer: resultKind === 'needs_review' || snapshot.state === previousState ? preservedAnswer : ''
            });
        } catch (reason) {
            if (active && director) director.showFailure(reason && reason.message ? reason.message : '操作未完成，请重试。');
        } finally { busy = false; if (overlay) overlay.classList.remove('is-busy'); }
    }

    async function chooseDestination(button) {
        if (busy || !snapshot || !routeState) return;
        var destinationId = button.dataset.destinationId;
        var kind = button.dataset.destinationKind || 'travel';
        if (!registry || !registry.has(destinationId)) return;
        busy = true;
        if (director) director.lockInteraction(button);
        var previousSceneId = routeState.currentSceneId;
        routeState.currentSceneId = destinationId;
        if (routeState.visitedSceneIds.indexOf(destinationId) < 0) routeState.visitedSceneIds.push(destinationId);
        routeState.lastChosenDestinationId = destinationId;
        routeState.sceneChoiceHistory.push({
            fromSceneId: previousSceneId,
            destinationId: destinationId,
            kind: kind,
            backendState: snapshot.state,
            chosenAt: new Date().toISOString()
        });
        routeState.pendingChoice = destinationId === 'camp';
        saveRoute();
        emitWorld({
            type: 'destination-chosen',
            runId: snapshot.run_id,
            fromSceneId: previousSceneId,
            destinationId: destinationId,
            kind: kind,
            backendState: snapshot.state
        });
        try {
            await renderGame({
                fromState: snapshot.state,
                fromSceneId: previousSceneId,
                sceneId: destinationId,
                panelMode: destinationId === 'camp' ? 'destination' : 'learning',
                storyLines: locationStory(destinationId, kind === 'camp' ? 'return' : kind),
                preservedAnswer: director && director.preservedAnswer || ''
            });
        } finally {
            busy = false;
            if (overlay) overlay.classList.remove('is-busy');
        }
    }

    function renderRewardState() {
        if (!active || !overlay) return;
        if (snapshot && director) {
            director.updateHeader();
            director.renderInteraction('');
        } else {
            renderPrepare();
        }
    }

    async function collectReward(equipAfter, trigger) {
        if (busy || !snapshot || snapshot.state !== 'TREASURY') return;
        var previousState = snapshot.state;
        busy = true;
        overlay.classList.add('is-busy');
        if (director) director.lockInteraction(trigger);
        try {
            snapshot = unwrap(await api.advanceHarvestAdventure(snapshot.run_id, 'claim', {}));
            clearStoredRoute();
            await refreshRewards();
            if (equipAfter && snapshot.artifact_id) {
                var equipResult = unwrap(await api.equipHarvestAdventureReward(snapshot.artifact_id, false));
                rewardCollection = equipResult;
                rewardComparison = equipResult.status === 'replace_required' ? equipResult : null;
                rewardDetailId = snapshot.artifact_id;
            }
            await renderGame({
                fromState: previousState,
                fromSceneId: routeState ? routeState.currentSceneId : 'camp',
                sceneId: routeState ? routeState.currentSceneId : 'camp',
                panelMode: 'learning'
            });
        } catch (reason) {
            rewardError = reason && reason.message ? reason.message : '这枚旅印尚未收入宝库，请重试。';
            if (snapshot && snapshot.state === 'completed') {
                await refreshRewards();
                renderRewardState();
            } else if (director) {
                director.showFailure(rewardError);
            }
        } finally {
            busy = false;
            if (overlay) overlay.classList.remove('is-busy');
        }
    }

    async function equipReward(artifactId, replaceConfirmed, trigger) {
        if (busy || !artifactId) return;
        busy = true;
        if (overlay) overlay.classList.add('is-busy');
        if (trigger) trigger.disabled = true;
        try {
            var result = unwrap(await api.equipHarvestAdventureReward(Number(artifactId), Boolean(replaceConfirmed)));
            rewardCollection = result;
            rewardComparison = result.status === 'replace_required' ? result : null;
            if (result.status === 'equipped') {
                rewardDetailId = Number(artifactId);
                rewardError = '';
            }
            renderRewardState();
        } catch (reason) {
            rewardError = reason && reason.message ? reason.message : '装备没有完成，原旅印保持不变。';
            await refreshRewards();
            renderRewardState();
        } finally {
            busy = false;
            if (overlay) overlay.classList.remove('is-busy');
        }
    }

    async function unequipReward(artifactId, trigger) {
        if (busy || !artifactId) return;
        busy = true;
        if (overlay) overlay.classList.add('is-busy');
        if (trigger) trigger.disabled = true;
        try {
            rewardCollection = unwrap(await api.unequipHarvestAdventureReward(Number(artifactId)));
            rewardComparison = null;
            rewardDetailId = Number(artifactId);
            rewardError = '';
            renderRewardState();
        } catch (reason) {
            rewardError = reason && reason.message ? reason.message : '卸下没有完成，当前旅印保持不变。';
            await refreshRewards();
            renderRewardState();
        } finally {
            busy = false;
            if (overlay) overlay.classList.remove('is-busy');
        }
    }

    function showCandidateDetails(button) {
        var candidate = button.closest('[data-reward-candidate]');
        if (!candidate || candidate.querySelector('.shiguang-reward-candidate-detail')) return;
        var reward = snapshot && snapshot.reward || {};
        var badge = badgeDefinition(reward.catalog_id);
        button.insertAdjacentHTML('beforebegin', '<dl class="shiguang-reward-candidate-detail"><div><dt>类别</dt><dd>旅程纪念</dd></div><div><dt>装备位置</dt><dd>旅印槽位</dd></div><div><dt>说明</dt><dd>' + escapeHtml(badge.description) + '</dd></div></dl>');
        button.textContent = '详情已展开';
        button.disabled = true;
    }

    // 立绘点击台词：galgame 式"点角色，角色有反应"，随便点都有事做。
    var ROLE_LINES = {
        mascot: [
            '柿小吉抖了抖绒毛："别急，证据攒够，它自然会让路。"',
            '柿小吉："你每答对一题，谷里的灯就亮一盏，我数着呢。"',
            '柿小吉扑棱了一下："要不要再翻一遍卷轴？规则不会跑。"',
            '柿小吉小声说："它最怕你把条件说全——你试试。"'
        ],
        player: [
            '旅者按了按怀里的卷轴："规则还在，走。"',
            '旅者望了望远处的雾："答错了也没关系，证据会越磨越亮。"',
            '旅者自语："先想清楚它为什么成立，再开口。"',
            '旅者拍了拍衣角的墨渍："这一路的证据，我都记着。"'
        ],
        enemy: [
            '墨迹兽甩了甩墨点："说得倒轻巧，答出来才算数。"',
            '墨迹兽咕哝："别想用漂亮话糊弄我，我要的是证据。"',
            '墨迹兽绕着你的答案打转："这里，缺了点什么。"',
            '墨迹兽低吼："换了个问法，你还认得我吗？"'
        ],
        boss: [
            '墨魇之主的雾影压得更低："解释、辨析、迁移，一道也别想跳过。"',
            '墨魇之主的声音从雾里传来："我认得你练功时的旧话。"',
            '墨魇之主低笑："证据链断了哪一环，我都看得见。"'
        ]
    };

    function showRoleLine(target) {
        if (!overlay || !active) return;
        var kind = '';
        if (target.classList.contains('shiguang-mascot')) kind = 'mascot';
        else if (target.classList.contains('shiguang-player')) kind = 'player';
        else if (target.classList.contains('shiguang-boss-character')) kind = 'boss';
        else if (target.classList.contains('shiguang-enemy')) kind = 'enemy';
        var pool = ROLE_LINES[kind];
        if (!pool || !pool.length) return;
        var line = pool[Math.floor(Math.random() * pool.length)];
        var existing = overlay.querySelector('.shiguang-role-line');
        if (existing) existing.remove();
        var bubble = document.createElement('div');
        bubble.className = 'shiguang-role-line';
        bubble.setAttribute('role', 'status');
        bubble.textContent = line;
        overlay.appendChild(bubble);
        window.setTimeout(function () {
            if (bubble.isConnected) bubble.remove();
        }, 2600);
    }

    // 场景环境信息：玩家点背景空白处，出当前场景的小信息，保证"任何地方都有事做"。
    var SCENE_LINES = {
        camp: [
            '营灯在夜色里轻轻摇晃，三条小路从谷口伸向不同方向。',
            '柿小吉把营灯拨亮了一点："先看清眼前这一步，再决定往哪走。"',
            '风从石门外灌进来，带着远处书页的味道。'
        ],
        echo_archive: [
            '书页被风翻动，回声在廊柱间打转——你每说一句，它就送回一句。',
            '一卷旧墨浮在台面上，边缘还留着上一场冒险的印子。',
            '书阁深处传来低低的沙沙声，像是有人在翻你读过的规则。'
        ],
        mist_valley: [
            '雾从石径尽头漫过来，脚下的路只亮出一步远。',
            '墨迹在雾里时聚时散，它正等着你开口。',
            '石径边立着半块石碑，上面的字被雾磨得只剩轮廓。'
        ],
        transfer_bridge: [
            '桥下的雾缓缓流动，像是在测量你带来的证据够不够重。',
            '桥面换了一组陌生纹路——规则还是那条，路已经不是那条。',
            '风从桥对岸吹来，带着古塔方向的铁锈味。'
        ],
        boss_core: [
            '三道封印在深处缓缓转动，每转一圈，雾就浓一分。',
            '古塔的砖缝里渗着冷光，墨魇之主就在雾最浓的地方。',
            '封印上刻着两个字：解释、辨析、迁移。'
        ],
        harvest_treasury: [
            '宝库的灯火一排排亮起，照着你一路上留下的证据。',
            '展示台中央空着一格——那是留给你的旅印位置。',
            '柿小吉悄悄说："它记得你是怎样走到这里的。"'
        ],
        mechanism_workshop: [
            '机关台上摆着几件半成品，齿轮咬合的声音很轻。',
            '工坊的灯下悬着一行小字："方法要能被检验，才算数。"'
        ],
        mirror_lake: [
            '湖面映着夜空，倒影里的规则比岸上的更清晰。',
            '水波一荡，倒影里的字就换了说法——它在提醒你注意条件。'
        ]
    };

    function showSceneLine() {
        if (!overlay || !active || !snapshot) return;
        var sceneId = snapshot.narrative && snapshot.narrative.world && snapshot.narrative.world.scene_id
            ? snapshot.narrative.world.scene_id
            : (routeState ? routeState.currentSceneId : '');
        var pool = SCENE_LINES[sceneId] || SCENE_LINES[presentationSceneId(snapshot.state, '')] || null;
        if (!pool) return;
        var line = pool[Math.floor(Math.random() * pool.length)];
        var existing = overlay.querySelector('.shiguang-role-line');
        if (existing) existing.remove();
        var bubble = document.createElement('div');
        bubble.className = 'shiguang-role-line is-scene';
        bubble.setAttribute('role', 'status');
        bubble.textContent = line;
        overlay.appendChild(bubble);
        window.setTimeout(function () {
            if (bubble.isConnected) bubble.remove();
        }, 3000);
    }

    function handleClick(event) {
        var button = event.target.closest('[data-action]');
        if (!button) {
            // 随便点都有事：点立绘出角色台词；对白中点任意空白推进下一句；点背景出环境信息。
            if (busy || !active) return;
            var role = event.target.closest && event.target.closest('.shiguang-player, .shiguang-mascot, .shiguang-enemy, .shiguang-boss-character');
            if (role) { showRoleLine(role); return; }
            if (director && overlay && overlay.querySelector('.shiguang-dialogue')) {
                // 对白中点击任意空白：先补全当前句，再推进下一句（galgame 点击即推进）。
                director.nextDialogue();
                return;
            }
            showSceneLine();
            return;
        }
        var action = button.dataset.action;
        if (action === 'leave' || action === 'return') return close();
        if (action === 'dialogue-next') { if (director) director.nextDialogue(); return; }
        if (action === 'dialogue-skip') { if (director) director.skipDialogue(); return; }
        if (action === 'skip-transition') { if (director) director.skipVisual(); return; }
        if (action === 'scroll-next') { scrollPageIndex = Number(scrollPageIndex || 0) + 1; if (director) director.renderInteraction(''); return; }
        if (action === 'scroll-finish') { scrollPageIndex = 'done'; if (director) director.renderInteraction(''); return; }
        if (action === 'mute') { button.textContent = audio.toggleMute() ? '静' : '声'; button.setAttribute('aria-label', audio.muted ? '取消静音' : '静音'); return; }
        if (busy || director && director.locked) return;
        if (action === 'prepare-next') return goToPrepareStep(2);
        if (action === 'prepare-back') return goToPrepareStep(1);
        if (action === 'confirm-school') return confirmSchool(button);
        if (action === 'start') return start();
        if (action === 'destination') return chooseDestination(button);
        if (action === 'reward-later') return close();
        if (action === 'reward-candidate-detail') return showCandidateDetails(button);
        if (action === 'reward-collect') return collectReward(false, button);
        if (action === 'reward-collect-equip') return collectReward(true, button);
        if (action === 'reward-detail') {
            rewardDetailId = Number(button.dataset.artifactId || 0);
            rewardComparison = null;
            return renderRewardState();
        }
        if (action === 'reward-back') {
            rewardDetailId = 0;
            rewardComparison = null;
            return renderRewardState();
        }
        if (action === 'reward-equip') return equipReward(Number(button.dataset.artifactId), false, button);
        if (action === 'reward-unequip') return unequipReward(Number(button.dataset.artifactId), button);
        if (action === 'reward-replace-cancel') {
            rewardComparison = null;
            return renderRewardState();
        }
        if (action === 'reward-replace-confirm') return equipReward(Number(button.dataset.artifactId), true, button);
        if (action === 'continue' || action === 'claim' || action === 'hint') return advance(action, {}, button);
        if (action === 'answer') return advance('submit', { answer: button.dataset.answer }, button);
        if (action === 'method') return advance('choose_strategy', { choice: button.dataset.route, method: button.dataset.method }, button);
    }

    function handleInput(event) {
        if (!event.target) return;
        if (event.target.dataset.action === 'volume') audio.setVolume(event.target.value);
        if (event.target.name === 'school' && overlay) previewSchoolRitual(event.target.value, true);
    }

    function handlePreviewIntent(event) {
        if (!event.target || !overlay || !spellRegistry) return;
        var label = event.target.closest && event.target.closest('.shiguang-schools label[data-school]');
        if (!label) return;
        var preview = overlay.querySelector('.spell-selection-preview');
        if (preview && preview.dataset.previewSchool === label.dataset.school) return;
        previewSchoolRitual(label.dataset.school, false);
    }

    function handleSubmit(event) {
        var form = event.target.closest('.shiguang-answer');
        if (!form) return;
        event.preventDefault();
        if (busy || director && director.locked) return;
        var field = form.querySelector('[name="answer"]');
        var submit = form.querySelector('[type="submit"]');
        advance(form.dataset.formAction || 'submit', { answer: field.value }, submit);
    }

    // ===== 知识规划准备阶段 =====
    var preparePlanId = null;
    var prepareCard = document.getElementById('adventurePrepare');
    var prepareBody = document.getElementById('adventurePrepareBody');
    var prepareStartBtn = document.getElementById('adventurePrepareStart');
    var prepareAdjustBtn = document.getElementById('adventurePrepareAdjust');
    var prepareCancelBtn = document.getElementById('adventurePrepareCancel');
    var prepareAdjustInput = document.getElementById('adventurePrepareAdjustInput');
    var prepareAdjustConfirm = document.getElementById('adventurePrepareAdjustConfirm');

    function hidePrepareCard() {
        if (prepareCard) prepareCard.hidden = true;
        if (prepareAdjustInput) prepareAdjustInput.hidden = true;
        if (prepareAdjustConfirm) prepareAdjustConfirm.hidden = true;
        preparePlanId = null;
    }

    function showPrepareCard(data) {
        if (!prepareCard || !prepareBody) return;
        preparePlanId = data.plan_id;
        var goals = (data.preview && data.preview.goals) || [];
        var theme = (data.preview && data.preview.theme) || '';
        var adjCount = data.adjustment_count || 0;
        var goalLabels = ['', '', ''];
        goals.forEach(function (g, i) {
            if (i < 3) goalLabels[i] = (g.kind === 'understand' ? '理解' : g.kind === 'distinguish' ? '辨析' : '迁移') + '：' + (g.text || '');
        });
        var statusLine = adjCount > 0 ? '<div class="adventure-prepare-status">已调整 ' + adjCount + '/2 次</div>' : '';
        prepareBody.innerHTML = statusLine + '<div class="adventure-prepare-theme">这次我们要弄清楚：' + escapeHtml(theme) + '</div>' +
            goalLabels.filter(Boolean).map(function (g, i) {
                return '<div class="adventure-prepare-goal"><span class="adventure-prepare-goal-num">' + (i + 1) + '</span>' + escapeHtml(g) + '</div>';
            }).join('');
        prepareCard.hidden = false;
        if (prepareAdjustInput) prepareAdjustInput.hidden = true;
        if (prepareAdjustConfirm) prepareAdjustConfirm.hidden = true;
        // v0.2.0: disable adjust after 2 times
        if (prepareAdjustBtn) {
            if (adjCount >= 2) {
                prepareAdjustBtn.disabled = true;
                prepareAdjustBtn.title = '调整次数已达上限';
                prepareAdjustBtn.textContent = '已调整 2/2 次';
            } else {
                prepareAdjustBtn.disabled = false;
                prepareAdjustBtn.title = '';
                prepareAdjustBtn.textContent = '调整重点';
            }
        }
    }

    async function doPrepare() {
        // 直接进入新版全屏准备界面（主题输入 + 身份选择 + 法术），
        // 不再走侧边栏 prepare 卡——避免"点了没反应/看不到下一步"。
        await openNow();
    }

    async function doPrepareStart() {
        if (!preparePlanId) return;
        try {
            var context = inspectContext();
            var result = unwrap(await api.startHarvestAdventure(
                context.topic || '当前学习主题', 'wood', context.artifacts || [],
                { prepared_plan_id: preparePlanId }
            ));
        } catch (_) {}
        hidePrepareCard();
        if (entry) entry.style.display = '';
        await openNow();
    }

    function doPrepareAdjustToggle() {
        if (!prepareAdjustInput || !prepareAdjustConfirm) return;
        prepareAdjustInput.hidden = false;
        prepareAdjustConfirm.hidden = false;
        prepareAdjustInput.focus();
    }

    async function doPrepareAdjustConfirm() {
        if (!preparePlanId || !prepareAdjustInput) return;
        var text = prepareAdjustInput.value.trim();
        if (!text) return;
        try {
            var result = unwrap(await api.adjustPreparedAdventure(preparePlanId, text));
            showPrepareCard(result);
            prepareAdjustInput.value = '';
        } catch (_) {}
    }

    function doPrepareCancel() {
        hidePrepareCard();
        if (entry) entry.style.display = '';
    }

    if (prepareStartBtn) prepareStartBtn.addEventListener('click', doPrepareStart);
    if (prepareAdjustBtn) prepareAdjustBtn.addEventListener('click', doPrepareAdjustToggle);
    if (prepareCancelBtn) prepareCancelBtn.addEventListener('click', doPrepareCancel);
    if (prepareAdjustConfirm) prepareAdjustConfirm.addEventListener('click', doPrepareAdjustConfirm);

    async function openNow(forcePrepare) {
        if (active && !forcePrepare) return;
        if (active && forcePrepare) close();
        opener = document.activeElement && document.activeElement !== document.body ? document.activeElement : entry;
        active = true; captureChat(); ensureOverlay();
        window.dispatchEvent(new CustomEvent('kaki:adventure-state', {
            detail: { active: true, source: 'shiguang-manyou' }
        }));
        director = new SceneDirector();
        spellDirector = window.KakiAdventureSpellDirector && spellRegistry
            ? window.KakiAdventureSpellDirector.create({ registry: spellRegistry })
            : null;
        enemyDirector = window.KakiAdventureEnemyDirector
            ? window.KakiAdventureEnemyDirector.create({ getHost: function () { return overlay; } })
            : null;
        rewardDetailId = 0;
        rewardComparison = null;
        scrollPageIndex = 0;
        if (badgeRegistry && badgeRegistry.preload) badgeRegistry.preload('adventure_summary');
        try {
            await refreshRewards();
            if (forcePrepare) { snapshot=null;forceNewOnStart=true;prepareStep=1;prepareDraft={topic:'',source:'manual'};renderPrepare(1);return; }
            snapshot = unwrap(await api.getCurrentHarvestAdventure());
            if (snapshot) {
                if (spellRegistry) spellRegistry.preloadCast((snapshot.archive || {}).magic_school || 'wood');
                loadRoute(snapshot.run_id);
                await renderGame({
                    restore: true,
                    sceneId: routeState.currentSceneId,
                    panelMode: routeState.pendingChoice ? 'destination' : 'learning'
                });
            } else renderPrepare();
        } catch (_) { renderPrepare(); }
    }

    function close() {
        var focusTarget = opener && opener.isConnected ? opener : entry;
        var completed = Boolean(snapshot && snapshot.state === 'completed');
        active = false; audio.pause();
        if (director) director.cancel('close');
        if (spellDirector) spellDirector.cancel('close');
        if (enemyDirector) enemyDirector.cancel('close');
        director = null;
        spellDirector = null;
        enemyDirector = null;
        if (overlay) {
            overlay.removeEventListener('click', handleClick);
            overlay.removeEventListener('input', handleInput);
            overlay.removeEventListener('mouseover', handlePreviewIntent);
            overlay.removeEventListener('focusin', handlePreviewIntent);
            overlay.removeEventListener('submit', handleSubmit);
            overlay.removeEventListener('error', handleBadgeAssetError, true);
            overlay.remove();
        }
        overlay = null; workspace.removeAttribute('data-shiguang-active'); restoreChat();
        forceNewOnStart = false;
        window.dispatchEvent(new CustomEvent('kaki:adventure-state', {
            detail: { active: false, completed: completed, source: 'shiguang-manyou' }
        }));
        if (focusTarget && focusTarget.focus) focusTarget.focus({ preventScroll: true });
        opener = null;
        refreshResumeState();
    }

    async function refreshResumeState() {
        try {
            var current = unwrap(await api.getCurrentHarvestAdventure());
            entry.classList.toggle('has-active-journey', !!current);
            entry.title = current ? '继续拾光漫游' : '进入拾光漫游';
            var title = entry.querySelector('strong');
            var subtitle = entry.querySelector('small');
            if (title) title.textContent = current ? '继续拾光漫游' : '拾光漫游';
            if (subtitle) subtitle.textContent = current ? '上次停在“' + ((current.nodes || []).find(function (node) { return node.status === 'active'; }) || {}).title + '”' : '用十分钟，完成一次有始有终的学习小旅程';
        } catch (_) {}
    }

    function onKeydown(event) {
        if (!active) return;
        if (event.key === 'Escape') { close(); return; }
        if (event.key !== 'Tab') return;
        var controls = focusableControls();
        if (!controls.length) { event.preventDefault(); return; }
        var current = document.activeElement;
        var index = controls.indexOf(current);
        if (index < 0) {
            event.preventDefault();
            controls[0].focus({ preventScroll: true });
        } else if (event.shiftKey && index === 0) {
            event.preventDefault();
            controls[controls.length - 1].focus({ preventScroll: true });
        } else if (!event.shiftKey && index === controls.length - 1) {
            event.preventDefault();
            controls[0].focus({ preventScroll: true });
        }
    }

    var link = document.createElement('link');
    link.rel = 'stylesheet'; link.href = '/ai-workspace/styles/adventure-v2.css'; link.dataset.shiguangStyle = 'true';
    document.head.appendChild(link);
    entry.addEventListener('click', doPrepare);
    document.addEventListener('keydown', onKeydown);
    refreshResumeState();

    window.AIHarvestAdventure = {
        open: doPrepare,
        openNew: function () { return openNow(true); },
        presentationForNarrative: presentationForNarrative,
        destroy: function () {
            if (active) close();
            entry.removeEventListener('click', doPrepare);
            document.removeEventListener('keydown', onKeydown);
            if (director) director.cancel('destroy');
            audio.destroy(); link.remove();
        }
    };
}());
