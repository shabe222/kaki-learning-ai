(function () {
    'use strict';

    var ROOT = '/ai-workspace/assets/adventure/';
    var badges = [
        {
            id: 'adventure_summary',
            displayName: '拾光有成',
            description: '完成一次有始有终的拾光漫游后，收入宝库的旅程纪要印记。',
            image: 'badges/adventure_summary_badge.png',
            silhouette: 'badges/adventure_summary_silhouette.png',
            revealEffect: 'badges/badge_reveal_halo.png',
            rarityClass: 'journey-keepsake',
            category: 'journey',
            frameStyle: 'harvest-hex',
            accentClass: 'harvest-gold',
            equipSlot: 'journey_seal',
            fallbackIcon: '旅印'
        }
    ];

    function assetUrl(relative) {
        return ROOT + String(relative || '').replace(/^\/+/, '');
    }

    function resolve(id) {
        return badges.find(function (badge) { return badge.id === id; }) || badges[0];
    }

    function preload(id) {
        var badge = resolve(id);
        return [badge.image, badge.silhouette, badge.revealEffect].map(function (relative) {
            var image = new Image();
            image.src = assetUrl(relative);
            return image;
        });
    }

    window.KakiAdventureBadges = {
        all: badges.slice(),
        assetUrl: assetUrl,
        resolve: resolve,
        preload: preload
    };
})();
