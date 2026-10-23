(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.AIWorkspaceScroll = api;
}(typeof window !== 'undefined' ? window : globalThis, function () {
  'use strict';

  function createScrollController(getContainer, getButton, scheduleFrame) {
    var following = true;
    var scheduled = false;
    var raf = scheduleFrame || function (callback) { return requestAnimationFrame(callback); };

    function box() { return getContainer(); }
    function button() { return getButton ? getButton() : null; }
    function isNearBottom() {
      var node = box();
      return node.scrollHeight - node.scrollTop - node.clientHeight < 80;
    }
    function updateButton() {
      var control = button();
      if (control) control.hidden = following || isNearBottom();
    }
    function scheduleScroll(behavior) {
      if (scheduled) return;
      scheduled = true;
      raf(function () {
        scheduled = false;
        var node = box();
        if (typeof node.scrollTo === 'function') node.scrollTo({ top: node.scrollHeight, behavior: behavior || 'auto' });
        else node.scrollTop = node.scrollHeight;
        updateButton();
      });
    }
    function userScrolled() {
      following = isNearBottom();
      updateButton();
    }
    function messageSent() {
      following = true;
      scheduleScroll('auto');
    }
    function streamUpdated() {
      if (following || isNearBottom()) {
        following = true;
        scheduleScroll('auto');
      } else updateButton();
    }
    function contentResized() {
      if (following) scheduleScroll('auto');
      else updateButton();
    }
    function returnToLatest() {
      following = true;
      scheduleScroll('smooth');
    }
    function threadChanged() {
      following = true;
      scheduleScroll('auto');
    }

    return {
      isNearBottom: isNearBottom,
      userScrolled: userScrolled,
      messageSent: messageSent,
      streamUpdated: streamUpdated,
      contentResized: contentResized,
      returnToLatest: returnToLatest,
      threadChanged: threadChanged
    };
  }

  return { createScrollController: createScrollController };
}));
