/* global document, window */

(function () {
    'use strict';

    var PHASES = ['idle', 'previewing', 'charging', 'casting', 'travelling', 'impact', 'resolving', 'completed', 'cancelled'];

    function SpellDirector(options) {
        options = options || {};
        this.registry = options.registry || window.KakiAdventureSpells;
        this.host = options.host || null;
        this.node = null;
        this.timers = [];
        this.waiters = [];
        this.token = 0;
        this.phase = 'idle';
    }

    SpellDirector.prototype.emit = function (detail) {
        document.dispatchEvent(new CustomEvent('shiguang:spell', {
            detail: detail || {}
        }));
    };

    SpellDirector.prototype.motionScale = function () {
        return window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 0.08 : 1;
    };

    SpellDirector.prototype.clearWaits = function () {
        this.timers.splice(0).forEach(function (timer) { window.clearTimeout(timer); });
        this.waiters.splice(0).forEach(function (done) { done(false); });
    };

    SpellDirector.prototype.cancel = function (reason) {
        this.token += 1;
        this.clearWaits();
        this.setPhase('cancelled', { reason: reason || 'cancel' });
        if (this.node && this.node.isConnected) this.node.remove();
        this.node = null;
        this.host = null;
    };

    SpellDirector.prototype.setPhase = function (phase, detail) {
        if (PHASES.indexOf(phase) < 0) phase = 'idle';
        this.phase = phase;
        if (this.node) this.node.dataset.spellPhase = phase;
        this.emit(Object.assign({ type: 'phase', phase: phase }, detail || {}));
    };

    SpellDirector.prototype.wait = function (delay, token) {
        var self = this;
        return new Promise(function (resolve) {
            var finished = false;
            var finish = function (value) {
                if (finished) return;
                finished = true;
                self.waiters = self.waiters.filter(function (item) { return item !== finish; });
                resolve(value);
            };
            var timer = window.setTimeout(function () {
                self.timers = self.timers.filter(function (item) { return item !== timer; });
                finish(token === self.token);
            }, Math.max(16, Math.round(delay * self.motionScale())));
            self.timers.push(timer);
            self.waiters.push(finish);
        });
    };

    SpellDirector.prototype.coordinates = function (host) {
        var bounds = host.getBoundingClientRect();
        var player = host.querySelector('.shiguang-player');
        var target = host.querySelector('.shiguang-boss-character, .shiguang-enemy');
        var playerBounds = player ? player.getBoundingClientRect() : null;
        var targetBounds = target ? target.getBoundingClientRect() : null;
        var originX = playerBounds ? playerBounds.right - bounds.left - playerBounds.width * 0.2 : bounds.width * 0.18;
        var originY = playerBounds ? playerBounds.top - bounds.top + playerBounds.height * 0.42 : bounds.height * 0.48;
        var targetX = targetBounds ? targetBounds.left - bounds.left + targetBounds.width * 0.48 : bounds.width * 0.78;
        var targetY = targetBounds ? targetBounds.top - bounds.top + targetBounds.height * 0.48 : bounds.height * 0.46;
        return {
            originX: Math.max(48, originX),
            originY: Math.max(68, originY),
            targetX: Math.min(bounds.width - 48, targetX),
            targetY: Math.max(68, targetY)
        };
    };

    SpellDirector.prototype.mount = function (schoolId, host, options) {
        options = options || {};
        var self = this;
        var school = this.registry.resolve(schoolId);
        var point = this.coordinates(host);
        var node = document.createElement('div');
        node.className = 'shiguang-spell-fx ' + school.accentClass;
        node.dataset.spellSchool = school.id;
        node.dataset.spellPhase = 'idle';
        node.dataset.fallbackEffect = school.fallbackEffect;
        node.style.setProperty('--spell-origin-x', point.originX + 'px');
        node.style.setProperty('--spell-origin-y', point.originY + 'px');
        node.style.setProperty('--spell-target-x', point.targetX + 'px');
        node.style.setProperty('--spell-target-y', point.targetY + 'px');
        node.style.setProperty('--spell-boss-scale', String(options.bossFinal ? school.bossScale : 1));
        node.innerHTML = '<img class="shiguang-spell-charge" src="' + this.registry.assetUrl(options.preview ? school.selectionPreview : school.castAsset) + '" alt="" aria-hidden="true">' +
            (options.preview ? '' : '<img class="shiguang-spell-trail" src="' + this.registry.assetUrl(school.trailAsset) + '" alt="" aria-hidden="true">' +
                '<img class="shiguang-spell-impact" src="' + this.registry.assetUrl(school.impactAsset) + '" alt="" aria-hidden="true">' +
                '<img class="shiguang-spell-restore" src="' + this.registry.assetUrl(school.restoreAsset) + '" alt="" aria-hidden="true">') +
            '<i class="shiguang-spell-fallback" aria-hidden="true"></i>';
        Array.prototype.slice.call(node.querySelectorAll('img')).forEach(function (image) {
            image.addEventListener('error', function () {
                image.hidden = true;
                node.classList.add('is-fallback');
                self.emit({
                    type: 'asset-fallback',
                    school: school.id,
                    asset: image.getAttribute('src') || '',
                    fallbackEffect: school.fallbackEffect
                });
            }, { once: true });
        });
        host.appendChild(node);
        this.host = host;
        this.node = node;
        return { school: school, node: node };
    };

    SpellDirector.prototype.previewSchool = async function (schoolId, host) {
        if (!host || !this.registry) return false;
        this.cancel('preview-change');
        var token = this.token;
        var mounted = this.mount(schoolId, host, { preview: true });
        mounted.node.classList.add('is-selection-preview');
        this.setPhase('previewing', { school: mounted.school.id, mode: 'selection' });
        await this.wait(380, token);
        return token === this.token;
    };

    SpellDirector.prototype.confirmSelection = async function (schoolId, host) {
        if (!host || !this.registry) return false;
        this.cancel('selection-confirm');
        var token = this.token;
        var mounted = this.mount(schoolId, host, { preview: true });
        mounted.node.classList.add('is-selection-preview', 'is-confirming');
        this.setPhase('charging', { school: mounted.school.id, mode: 'selection-confirm' });
        await this.wait(520, token);
        if (token !== this.token) return false;
        this.setPhase('completed', { school: mounted.school.id, mode: 'selection-confirm' });
        await this.wait(180, token);
        if (this.node && this.node.isConnected) this.node.remove();
        this.node = null;
        return true;
    };

    SpellDirector.prototype.playResult = async function (options) {
        options = options || {};
        var host = options.host;
        var schoolId = options.school || 'wood';
        var result = options.resultKind || '';
        if (!host || !this.registry || (result !== 'success' && result !== 'needs_review')) return false;
        this.cancel('new-result');
        var token = this.token;
        var mounted = this.mount(schoolId, host, { bossFinal: Boolean(options.bossFinal) });
        var school = mounted.school;
        var timing = school.animationTiming;
        this.setPhase('charging', { school: school.id, result: result, bossFinal: Boolean(options.bossFinal) });
        if (!await this.wait(timing.charge, token)) return false;
        if (result === 'needs_review') {
            mounted.node.classList.add('is-fizzle');
            this.setPhase('cancelled', { school: school.id, result: result, effect: 'fizzle' });
            await this.wait(420, token);
            if (this.node && this.node.isConnected) this.node.remove();
            this.node = null;
            return false;
        }
        this.setPhase('casting', { school: school.id, result: result });
        if (!await this.wait(timing.cast, token)) return false;
        this.setPhase('travelling', { school: school.id, result: result });
        if (!await this.wait(timing.travel, token)) return false;
        this.setPhase('impact', { school: school.id, result: result, bossFinal: Boolean(options.bossFinal) });
        if (!await this.wait(timing.impact, token)) return false;
        this.setPhase('resolving', { school: school.id, result: result, restore: true });
        if (!await this.wait(timing.resolve, token)) return false;
        this.setPhase('completed', { school: school.id, result: result, restore: true });
        await this.wait(160, token);
        if (this.node && this.node.isConnected) this.node.remove();
        this.node = null;
        return true;
    };

    window.KakiAdventureSpellDirector = {
        phases: PHASES.slice(),
        create: function (options) { return new SpellDirector(options); }
    };
})();
