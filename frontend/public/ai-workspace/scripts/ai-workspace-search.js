(function () {
    'use strict';

    function element(tag, className, text) {
        var node = document.createElement(tag);
        if (className) node.className = className;
        if (text != null) node.textContent = text;
        return node;
    }

    function sourceLabel(result) {
        var kind = {
            government: '政府来源',
            university: '高校来源',
            academic: '学术资料'
        }[result.source_kind] || '网页资料';
        var content = result.content_kind === 'abstract_index' ? ' · 摘要/索引（非全文）' : '';
        return kind + ' · ' + (result.source_name || '未知来源') + content;
    }

    function renderSearchResults(search, options) {
        options = options || {};
        var root = element('section', 'search-result-set');
        root.dataset.searchId = search.search_id || '';
        var head = element('header', 'search-result-head');
        var title = element('div', '', '权威资料搜索');
        title.appendChild(element('small', '', '“' + (search.query || '') + '”'));
        var count = element('span', '', String((search.results || []).length) + ' 条');
        head.append(title, count);
        root.appendChild(head);
        if (!(search.results || []).length) {
            root.appendChild(element('p', 'search-result-empty', '暂时没有找到可验证的资料，可以换个关键词再试。'));
            return root;
        }

        (search.results || []).forEach(function (result) {
            var card = element('article', 'search-result-card');
            card.dataset.resultId = result.result_id;
            var copy = element('div', 'search-result-copy');
            copy.append(
                element('strong', '', result.title),
                element('p', '', result.summary),
                element('small', '', sourceLabel(result))
            );
            var actions = element('div', 'search-result-actions');
            var link = element('a', '', '查看来源');
            link.href = result.url;
            link.target = '_blank';
            link.rel = 'noopener noreferrer';
            var importButton = element('button', '', '导入资料库');
            importButton.type = 'button';
            if (result.imported_material_id) {
                card.dataset.importStatus = 'ready';
                importButton.disabled = false;
                importButton.textContent = '已导入 · 重新绑定/导入';
                importButton.title = '资料仍在资料库时会重新绑定当前会话；若原资料已删除则重新导入。';
            }
            importButton.onclick = async function () {
                importButton.disabled = true;
                importButton.textContent = '正在导入…';
                card.dataset.importStatus = 'running';
                try {
                    var response = await options.importResult(search.search_id, result.result_id);
                    var material = response.data || {};
                    card.dataset.importStatus = 'ready';
                    result.imported_material_id = material.material_id;
                    importButton.textContent = '已导入 · ' + material.material_id;
                    if (options.onImported) options.onImported(material, result);
                } catch (error) {
                    card.dataset.importStatus = 'failed';
                    importButton.disabled = false;
                    importButton.textContent = '导入失败，重试';
                    var errorNode = card.querySelector('.search-result-error') || element('small', 'search-result-error');
                    errorNode.textContent = error && error.message ? error.message : '暂时无法导入该资料';
                    if (!errorNode.parentNode) card.appendChild(errorNode);
                }
            };
            actions.append(link, importButton);
            card.append(copy, actions);
            root.appendChild(card);
        });
        return root;
    }

    window.AIWorkspaceSearch = { renderSearchResults: renderSearchResults };
})();
