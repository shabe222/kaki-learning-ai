(function () {
    'use strict';

    var ASSET = '/ai-workspace/assets/adventure/';
    var STORAGE_PREFIX = 'kaki_enemy_companion_v1:';
    var STATES = [
        'hidden', 'foreshadow', 'entering', 'idle', 'speaking', 'challenging',
        'reacting', 'hit', 'staggered', 'phase_changed', 'defeated_pose',
        'transforming', 'dissolving', 'cleared'
    ];
    var ENEMIES = {
        ordinary: {
            id: 'inkbeast',
            name: '墨迹兽',
            defeatedAsset: 'characters/enemy_inkbeast_defeated.png',
            resultAsset: 'items/knowledge_seed.png',
            resultName: '澄明知种',
            entryStyle: 'ink-from-trace'
        },
        boss: {
            id: 'moyan-master',
            name: '墨魇之主',
            defeatedAsset: 'characters/boss_moyan_master_defeated.png',
            resultAsset: 'items/knowledge_core_seal.png',
            resultName: '三证知核印',
            entryStyle: 'fog-from-tower'
        }
    };

    function safeParse(value, fallback) {
        try {
            var parsed = JSON.parse(value);
            return parsed && typeof parsed === 'object' ? parsed : fallback;
        } catch (_) { return fallback; }
    }

    function escapeHtml(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function enemyKind(state) {
        if (state === 'BOSS') return 'boss';
        if (state === 'TRIAL' || state === 'INTERLEAVE') return 'ordinary';
        return '';
    }

    function timelineHasReview(snapshot) {
        return Boolean((snapshot && snapshot.timeline || []).some(function (event) {
            return event && (
                event.result === 'needs_review'
                || event.feedback_kind === 'needs_review'
                || event.status === 'needs_review'
                || event.kind === 'needs_review'
            );
        }));
    }

    function Director(options) {
        options = options || {};
        this.getHost = options.getHost || function () { return document.querySelector('.shiguang-overlay'); };
        this.token = 0;
        this.timers = [];
        this.seen = {};
        this.runId = '';
        this.behavior = { wrongSeen: false, hadCorrection: false, hintUsed: false };
        this.finalCompleted = false;
    }

    Director.prototype.motionScale = function () {
        if (document.hidden) return 0.03;
        return window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 0.08 : 1;
    };

    Director.prototype.wait = function (delay, token) {
        var self = this;
        if (token !== this.token) return Promise.resolve(false);
        return new Promise(function (resolve) {
            var timer = window.setTimeout(function () {
                self.timers = self.timers.filter(function (item) { return item !== timer; });
                resolve(token === self.token);
            }, Math.max(320, Math.round(delay * self.motionScale())));
            self.timers.push(timer);
        });
    };

    Director.prototype.cancel = function () {
        this.token += 1;
        this.timers.splice(0).forEach(function (timer) { window.clearTimeout(timer); });
    };

    Director.prototype.storageKey = function (runId) {
        return STORAGE_PREFIX + String(runId || 'unknown');
    };

    Director.prototype.bindRun = function (snapshot) {
        var runId = String(snapshot && snapshot.run_id || '');
        if (!runId || this.runId === runId) return;
        this.runId = runId;
        this.behavior = safeParse(localStorage.getItem(this.storageKey(runId)), {
            wrongSeen: false,
            hadCorrection: false,
            hintUsed: false
        });
        this.behavior.wrongSeen = Boolean(this.behavior.wrongSeen || timelineHasReview(snapshot));
        this.behavior.hadCorrection = Boolean(this.behavior.hadCorrection);
        this.behavior.hintUsed = Boolean(this.behavior.hintUsed);
        this.persist(snapshot);
    };

    Director.prototype.persist = function (snapshot) {
        if (snapshot) this.bindRun(snapshot);
        if (this.runId) localStorage.setItem(this.storageKey(this.runId), JSON.stringify(this.behavior));
        this.syncOverlay(snapshot);
    };

    Director.prototype.syncOverlay = function (snapshot) {
        var host = this.getHost();
        if (!host) return;
        host.dataset.hadCorrection = String(Boolean(this.behavior.hadCorrection));
        host.dataset.hintUsed = String(Boolean(this.behavior.hintUsed));
        host.dataset.bossStage = String(Number(snapshot && snapshot.archive && snapshot.archive.boss_stage || 0));
    };

    Director.prototype.emit = function (state, detail) {
        detail = detail || {};
        window.dispatchEvent(new CustomEvent('shiguang:enemy', {
            detail: Object.assign({
                state: state,
                runId: this.runId,
                hadCorrection: Boolean(this.behavior.hadCorrection),
                hintUsed: Boolean(this.behavior.hintUsed)
            }, detail)
        }));
    };

    Director.prototype.root = function (scene) {
        scene = scene || this.getHost();
        return scene && scene.querySelector ? scene.querySelector('[data-enemy-root]') : null;
    };

    Director.prototype.setState = function (state, scene, snapshot, detail) {
        if (STATES.indexOf(state) < 0) return;
        var root = this.root(scene);
        if (!root) return;
        root.dataset.enemyState = state;
        var stage = Number(snapshot && snapshot.archive && snapshot.archive.boss_stage || 0);
        root.dataset.bossStage = String(stage);
        var ownerScene = root.closest('.shiguang-scene');
        if (ownerScene) ownerScene.dataset.enemyState = state;
        this.syncOverlay(snapshot);
        this.emit(state, Object.assign({
            enemyId: root.dataset.enemyId || '',
            enemyKind: root.dataset.enemyKind || '',
            bossStage: stage
        }, detail || {}));
    };

    Director.prototype.ensureResult = function (scene, kind, hidden) {
        if (!scene || !kind) return null;
        var config = ENEMIES[kind];
        var current = scene.querySelector('[data-knowledge-result]');
        if (current) {
            current.hidden = Boolean(hidden);
            return current;
        }
        var result = document.createElement('div');
        result.className = 'shiguang-knowledge-result';
        result.dataset.knowledgeResult = config.id;
        result.hidden = Boolean(hidden);
        result.innerHTML = '<span aria-hidden="true"></span><img src="' + escapeHtml(ASSET + config.resultAsset) +
            '" alt="' + escapeHtml(config.resultName) + '"><small>' + escapeHtml(config.resultName) + '</small>';
        var image = result.querySelector('img');
        image.addEventListener('error', function () {
            image.hidden = true;
            result.classList.add('is-fallback');
            result.querySelector('span').textContent = '知';
        }, { once: true });
        scene.appendChild(result);
        return result;
    };

    Director.prototype.decorateScene = function (scene, snapshot) {
        if (!scene || !snapshot) return scene;
        this.bindRun(snapshot);
        this.syncOverlay(snapshot);
        var kind = enemyKind(snapshot.state);
        var root = scene.querySelector('.shiguang-enemy, .shiguang-boss-character');
        if (root && kind) {
            var config = ENEMIES[kind];
            root.dataset.enemyRoot = config.id;
            root.dataset.enemyId = config.id;
            root.dataset.enemyKind = kind;
            root.dataset.enemyState = 'hidden';
            root.dataset.entryStyle = config.entryStyle;
            root.dataset.misconception = this.misconceptionFor(snapshot.state, snapshot);
            root.dataset.bossStage = String(Number(snapshot.archive && snapshot.archive.boss_stage || 0));
            root.setAttribute('aria-label', config.name + '，正在等待场景回应');

            var foreshadow = document.createElement('div');
            foreshadow.className = 'shiguang-enemy-foreshadow';
            foreshadow.dataset.enemyForeshadow = config.id;
            foreshadow.hidden = true;
            foreshadow.setAttribute('aria-hidden', 'true');
            foreshadow.innerHTML = '<i></i><i></i><i></i>';
            scene.appendChild(foreshadow);

            var fallback = document.createElement('div');
            fallback.className = 'shiguang-enemy-fallback';
            fallback.dataset.enemyFallback = config.id;
            fallback.hidden = true;
            fallback.setAttribute('role', 'img');
            fallback.setAttribute('aria-label', config.name + '暂时隐入墨雾');
            fallback.innerHTML = '<i></i><span>' + escapeHtml(config.name) + '</span>';
            scene.appendChild(fallback);

            root.addEventListener('error', function () {
                root.hidden = true;
                fallback.hidden = false;
                scene.classList.add('has-enemy-fallback');
            });
        }
        if (snapshot.state === 'TREASURY' || snapshot.state === 'completed') {
            this.ensureResult(scene, 'boss', false);
            scene.dataset.enemyState = 'cleared';
            scene.dataset.enemyFinaleComplete = 'true';
        }
        return scene;
    };

    Director.prototype.restoreScene = function (scene, snapshot) {
        if (!scene || !snapshot) return;
        this.bindRun(snapshot);
        var kind = enemyKind(snapshot.state);
        var root = this.root(scene);
        if (root && kind) {
            root.hidden = false;
            this.setState(snapshot.state === 'BOSS' ? 'challenging' : 'idle', scene, snapshot, { restored: true });
        }
        if (snapshot.state === 'TREASURY' || snapshot.state === 'completed') {
            this.ensureResult(scene, 'boss', false);
            scene.dataset.enemyFinaleComplete = 'true';
            this.emit('cleared', { enemyId: ENEMIES.boss.id, enemyKind: 'boss', restored: true, bossStage: 3 });
        }
        this.syncOverlay(snapshot);
    };

    Director.prototype.introduce = async function (scene, snapshot) {
        if (!scene || !snapshot) return;
        this.bindRun(snapshot);
        var kind = enemyKind(snapshot.state);
        var root = this.root(scene);
        if (!kind || !root) return;
        var stage = Number(snapshot.archive && snapshot.archive.boss_stage || 0);
        var seenKey = snapshot.state + ':' + kind + ':' + stage + ':' + (scene.dataset.sceneLocation || '');
        if (this.seen[seenKey]) {
            root.hidden = false;
            this.setState(snapshot.state === 'BOSS' ? 'challenging' : 'idle', scene, snapshot, { repeated: true });
            return;
        }
        this.seen[seenKey] = true;
        var token = this.token;
        var foreshadow = scene.querySelector('[data-enemy-foreshadow]');
        root.hidden = true;
        if (foreshadow) foreshadow.hidden = false;
        this.setState('foreshadow', scene, snapshot);
        if (!await this.wait(kind === 'boss' ? 680 : 460, token)) return;
        if (foreshadow) foreshadow.hidden = true;
        root.hidden = false;
        this.setState('entering', scene, snapshot);
        if (!await this.wait(kind === 'boss' ? 820 : 620, token)) return;
        this.setState('idle', scene, snapshot);
    };

    Director.prototype.speaking = function (speakerType, tone, snapshot) {
        var scene = this.getHost() && this.getHost().querySelector('.shiguang-scene');
        if (!scene || !this.root(scene)) return;
        if (speakerType === 'enemy') this.setState(tone === 'challenging' ? 'challenging' : 'speaking', scene, snapshot);
        else if (this.root(scene).dataset.enemyState === 'speaking') this.setState('idle', scene, snapshot);
    };

    Director.prototype.beforeAction = function (action, snapshot) {
        if (action !== 'submit') return;
        var scene = this.getHost() && this.getHost().querySelector('.shiguang-scene');
        if (this.root(scene)) this.setState('reacting', scene, snapshot, { action: action });
    };

    Director.prototype.observeAction = function (action, previous, next) {
        this.bindRun(next || previous);
        var resultKind = next && next.feedback && next.feedback.kind || '';
        if (action === 'hint') this.behavior.hintUsed = true;
        if (action === 'submit' && resultKind === 'needs_review') this.behavior.wrongSeen = true;
        if (action === 'submit' && resultKind === 'success' && this.behavior.wrongSeen) this.behavior.hadCorrection = true;
        this.persist(next || previous);
    };

    Director.prototype.playWrong = async function (scene, previous, next) {
        if (!this.root(scene)) return;
        var token = this.token;
        this.setState('reacting', scene, previous, { resultKind: 'needs_review' });
        scene.classList.add('is-enemy-fizzle');
        this.emit('reacting', { enemyId: this.root(scene).dataset.enemyId, effect: 'fizzle', resultKind: 'needs_review' });
        if (!await this.wait(760, token)) return;
        scene.classList.remove('is-enemy-fizzle');
        this.setState('challenging', scene, previous, { resultKind: 'needs_review' });
        this.syncOverlay(next);
    };

    Director.prototype.createImpactAnchor = function (scene) {
        var old = scene.querySelector('[data-enemy-impact-anchor]');
        if (old) old.remove();
        var impact = document.createElement('div');
        impact.className = 'shiguang-spell-impact shiguang-enemy-impact-anchor';
        impact.dataset.enemyImpactAnchor = 'true';
        impact.setAttribute('aria-hidden', 'true');
        impact.innerHTML = '<i></i><i></i>';
        scene.appendChild(impact);
        return impact;
    };

    Director.prototype.playSuccess = async function (scene, previous, next) {
        var root = this.root(scene);
        if (!root || !previous || !next) return;
        var kind = root.dataset.enemyKind;
        var token = this.token;
        var config = ENEMIES[kind];
        var previousStage = Number(previous.archive && previous.archive.boss_stage || 0);
        var nextStage = Number(next.archive && next.archive.boss_stage || 0);
        var finalOrdinary = previous.state === 'INTERLEAVE' && next.archive && next.archive.boss_ready;
        var finalBoss = previous.state === 'BOSS' && next.state === 'TREASURY';
        var impact = this.createImpactAnchor(scene);

        this.setState('hit', scene, previous, { resultKind: 'success', fromBossStage: previousStage, toBossStage: nextStage });
        if (!await this.wait(kind === 'boss' ? 620 : 470, token)) return;
        this.setState('staggered', scene, previous, { resultKind: 'success' });
        if (!await this.wait(kind === 'boss' ? 560 : 380, token)) return;

        if (!finalOrdinary && !finalBoss) {
            if (impact) impact.remove();
            if (kind === 'boss' && nextStage !== previousStage) {
                root.dataset.bossStage = String(nextStage);
                this.setState('phase_changed', scene, next, { fromBossStage: previousStage, toBossStage: nextStage });
                await this.wait(780, token);
            } else {
                this.setState('challenging', scene, next);
            }
            return;
        }

        root.src = ASSET + config.defeatedAsset;
        root.hidden = false;
        root.alt = config.name + '收起锋芒，知识的光正从墨色中显露';
        this.setState('defeated_pose', scene, previous, { resultKind: 'success' });
        if (impact) impact.remove();
        if (!await this.wait(kind === 'boss' ? 2600 : 1400, token)) return;

        var result = this.ensureResult(scene, kind, false);
        var aftermath = document.createElement('div');
        aftermath.className = 'shiguang-enemy-aftermath';
        aftermath.dataset.aftermath = kind;
        aftermath.setAttribute('role', 'status');
        aftermath.innerHTML = '<small>风声安静了一会儿</small><p>' +
            escapeHtml(kind === 'boss'
                ? '三道证据在知核印中重新对齐，古塔先恢复呼吸，庆祝稍后才会抵达。'
                : '原先挡路的误解凝成一枚知种，新的路从它身后慢慢亮起。') +
            '</p>';
        scene.appendChild(aftermath);
        this.setState('transforming', scene, previous, { knowledgeResult: config.resultName });
        if (result) result.classList.add('is-forming');
        if (!await this.wait(kind === 'boss' ? 1250 : 920, token)) return;

        this.setState('dissolving', scene, previous, { knowledgeResult: config.resultName });
        root.classList.add('is-dissolving');
        if (!await this.wait(720, token)) return;
        root.hidden = true;
        this.setState('cleared', scene, next, { knowledgeResult: config.resultName });

        if (kind === 'boss') {
            scene.dataset.enemyFinaleComplete = 'true';
            this.finalCompleted = true;
        }
        await this.wait(kind === 'boss' ? 1800 : 900, token);
    };

    Director.prototype.hasCompletedFinale = function () {
        return this.finalCompleted;
    };

    Director.prototype.misconceptionFor = function (state, snapshot) {
        var content = snapshot && snapshot.content || {};
        var archive = snapshot && snapshot.archive || {};
        var route = archive.selected_strategy || 'understanding';
        var claim = (content.misconception && content.misconception.claim) || '';
        // 优先使用游戏导演（narrative）编排的敌人台词
        var enemyLines = snapshot && snapshot.narrative && snapshot.narrative.enemy_lines;
        if (enemyLines) {
            if (state === 'TRIAL' && enemyLines.trial) return enemyLines.trial;
            if (state === 'INTERLEAVE' && enemyLines.interleave) return enemyLines.interleave;
            if (state === 'BOSS') {
                var stage = Number(archive.boss_stage || 0);
                if (enemyLines.boss && enemyLines.boss[stage]) return enemyLines.boss[stage];
            }
        }
        if (state === 'TRIAL') {
            if (claim) return claim + '——你敢说，它真站得住吗？';
            var trial = content[route] && content[route].trial || {};
            return '记住一个说法就算理解了吗？换到未见情境，它也一定站得住吗？';
        }
        if (state === 'INTERLEAVE') return '题面已经换了，刚才那条规则真的还能解释新的情境吗？';
        if (state === 'BOSS') {
            var stage = Number(archive.boss_stage || 0);
            var staged = [
                claim ? claim : '只说结论还不够。你能解释它为什么能面对未见情境吗？',
                '线索被收起来之后，你还能从记忆里找回关键证据吗？',
                '最后一道雾不会被复述吹散：请指出缺口，并给出能改善判断的具体行动。'
            ];
            return staged[Math.min(2, stage)];
        }
        return '把眼前的证据连起来，我才会让开。';
    };

    Director.prototype.supportFor = function (snapshot) {
        var content = snapshot && snapshot.content || {};
        var correction = (content.misconception && content.misconception.correction) || '';
        if (correction) return '刚才的答案已经保留。' + correction;
        return '刚才的答案已经保留。先指出它缺少哪一段证据，再用新的情境把这段推理补完整。';
    };

    Director.prototype.aftermathRecognition = function () {
        if (this.behavior.hadCorrection && this.behavior.hintUsed) {
            return '你没有把第一次缺口藏起来，而是完成了修正；守关时也主动借过一束线索，再把最后一步亲手接好。';
        }
        if (this.behavior.hadCorrection) return '你保留了第一次回答，并用后续证据完成修正；这枚旅印记得的是这段真实过程。';
        if (this.behavior.hintUsed) return '你在需要时主动借过一束线索，又用自己的回答走完了后面的路。';
        return '你让解释、回忆与迁移彼此照应，知核印记住了这条完整路径。';
    };

    window.KakiAdventureEnemyDirector = {
        STATES: STATES.slice(),
        enemies: ENEMIES,
        create: function (options) { return new Director(options); }
    };
}());
