/* global Image, document, window */

(function () {
    'use strict';

    var ROOT = '/ai-workspace/assets/adventure/';
    var schools = [
        {
            id: 'fire',
            displayName: '炎咒',
            skillName: '余焰',
            emblem: 'spells/fire_emblem.png',
            selectionPreview: 'spells/fire_charge.png',
            castOrigin: { x: 18, y: 48 },
            castAsset: 'spells/fire_charge.png',
            trailAsset: 'spells/fire_trail.png',
            impactAsset: 'spells/fire_impact_restore.png',
            restoreAsset: 'spells/fire_impact_restore.png',
            accentClass: 'spell-fire',
            animationTiming: { charge: 460, cast: 210, travel: 520, impact: 500, resolve: 480 },
            bossScale: 1.28,
            fallbackEffect: 'ember-bloom',
            ritualLine: '余焰认得你的决心。确认后，它会跟着真实答案亮起。'
        },
        {
            id: 'water',
            displayName: '水咒',
            skillName: '清心',
            emblem: 'spells/water_emblem.png',
            selectionPreview: 'spells/water_charge.png',
            castOrigin: { x: 18, y: 48 },
            castAsset: 'spells/water_charge.png',
            trailAsset: 'spells/water_trail.png',
            impactAsset: 'spells/water_impact_restore.png',
            restoreAsset: 'spells/water_impact_restore.png',
            accentClass: 'spell-water',
            animationTiming: { charge: 560, cast: 240, travel: 620, impact: 560, resolve: 520 },
            bossScale: 1.22,
            fallbackEffect: 'cleansing-ripple',
            ritualLine: '清心会把杂音沉下去。确认后，让证据像水纹一样传开。'
        },
        {
            id: 'wind',
            displayName: '风咒',
            skillName: '回风',
            emblem: 'spells/wind_emblem.png',
            selectionPreview: 'spells/wind_charge.png',
            castOrigin: { x: 18, y: 48 },
            castAsset: 'spells/wind_charge.png',
            trailAsset: 'spells/wind_trail.png',
            impactAsset: 'spells/wind_impact_restore.png',
            restoreAsset: 'spells/wind_impact_restore.png',
            accentClass: 'spell-wind',
            animationTiming: { charge: 420, cast: 180, travel: 450, impact: 440, resolve: 440 },
            bossScale: 1.34,
            fallbackEffect: 'wind-ring',
            ritualLine: '回风最擅长带走犹疑。确认后，让清楚的回答自己找到去路。'
        },
        {
            id: 'wood',
            displayName: '木咒',
            skillName: '生息',
            emblem: 'spells/wood_emblem.png',
            selectionPreview: 'spells/wood_charge.png',
            castOrigin: { x: 18, y: 48 },
            castAsset: 'spells/wood_charge.png',
            trailAsset: 'spells/wood_trail.png',
            impactAsset: 'spells/wood_impact_restore.png',
            restoreAsset: 'spells/wood_impact_restore.png',
            accentClass: 'spell-wood',
            animationTiming: { charge: 600, cast: 250, travel: 650, impact: 620, resolve: 620 },
            bossScale: 1.25,
            fallbackEffect: 'living-wreath',
            ritualLine: '生息会从一条可靠线索长起。确认后，让每次作答留下新的枝叶。'
        }
    ];
    var byId = Object.create(null);
    schools.forEach(function (school) { byId[school.id] = school; });

    function resolve(id) {
        return byId[id] || byId.wood;
    }

    function absolute(asset) {
        return asset ? ROOT + asset : '';
    }

    function preload(asset, schoolId, kind) {
        if (!asset) return Promise.resolve(false);
        var href = absolute(asset);
        var key = schoolId + ':' + kind;
        var existing = document.head.querySelector('link[data-spell-preload="' + key + '"]');
        if (!existing) {
            existing = document.createElement('link');
            existing.rel = 'preload';
            existing.as = 'image';
            existing.type = 'image/png';
            existing.href = href;
            existing.setAttribute('data-spell-preload', key);
            existing.setAttribute('data-spell-school', schoolId);
            document.head.appendChild(existing);
        }
        return new Promise(function (done) {
            var image = new Image();
            image.onload = function () { done(true); };
            image.onerror = function () { done(false); };
            image.src = href;
            if (image.complete) done(Boolean(image.naturalWidth));
        });
    }

    function preloadSelection(currentId) {
        var current = resolve(currentId);
        var requests = schools.map(function (school) {
            return preload(school.emblem, school.id, 'emblem');
        });
        requests.push(preload(current.selectionPreview, current.id, 'selectionPreview'));
        return Promise.all(requests);
    }

    function preloadCast(id) {
        var school = resolve(id);
        var assets = [
            ['castAsset', school.castAsset],
            ['trailAsset', school.trailAsset],
            ['impactAsset', school.impactAsset],
            ['restoreAsset', school.restoreAsset]
        ];
        return Promise.all(assets.map(function (entry) {
            return preload(entry[1], school.id, entry[0]);
        }));
    }

    window.KakiAdventureSpells = {
        root: ROOT,
        all: schools.slice(),
        has: function (id) { return Boolean(byId[id]); },
        resolve: resolve,
        assetUrl: absolute,
        preloadSelection: preloadSelection,
        preloadCast: preloadCast
    };
})();
