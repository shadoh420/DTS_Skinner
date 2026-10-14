// Shared Maps-page import feedback. Every viewer's status line starts with "Importing" while its single import
// request runs; this shows the elapsed time in a corner badge until that text changes. The badge lives outside the
// viewers' own markup (the Tribes 2 page is a compiled React app), so no viewer needs to call it.
(() => {
  'use strict';
  const RUNNING = /^\s*Importing\b/;
  let watched = null, started = 0, timer = 0, badge = null;
  const elapsed = () => { const s = Math.round((Date.now() - started) / 1000); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };
  const show = text => {
    if (!badge) {
      badge = document.createElement('div');
      badge.id = 'import-timer';
      badge.setAttribute('role', 'status');
      badge.style.cssText = 'position:fixed;left:12px;bottom:44px;z-index:2147483000;padding:6px 10px;border-radius:6px;' +
        'background:rgba(20,28,34,.92);color:#cfe3ea;font:12px/1.3 system-ui,sans-serif;box-shadow:0 2px 8px rgba(0,0,0,.4);pointer-events:none';
      document.body.append(badge);
    }
    badge.textContent = text;
  };
  const tick = () => {
    if (watched && watched.isConnected && RUNNING.test(watched.textContent)) {
      show(`Import running · ${elapsed()} (keep this page open)`);
      return;
    }
    clearInterval(timer); timer = 0; watched = null;
    const done = `Import finished in ${elapsed()}`;
    show(done);
    // Some viewers reload right after a first import: carry the message over.
    try { sessionStorage.setItem('skinner.importTimer', JSON.stringify({done, at: Date.now()})); } catch (_) { /* Shown until the reload only. */ }
    setTimeout(() => { if (!timer && badge) { badge.remove(); badge = null; } }, 8000);
  };
  const check = node => {
    if (timer) { if (!(watched && watched.isConnected && RUNNING.test(watched.textContent))) tick(); return; }
    const element = node.nodeType === 3 ? node.parentElement : node;
    if (timer || !element || element.id === 'import-timer' || !RUNNING.test(element.textContent) || element.textContent.length > 300) return;
    watched = element; started = Date.now();
    tick(); timer = setInterval(tick, 1000);
  };
  const start = () => new MutationObserver(records => {
    for (const record of records) {
      check(record.target);
      for (const node of record.addedNodes) check(node);
    }
  }).observe(document.body, {subtree: true, childList: true, characterData: true});
  const resume = () => {
    try {
      const saved = JSON.parse(sessionStorage.getItem('skinner.importTimer') || 'null');
      sessionStorage.removeItem('skinner.importTimer');
      if (saved && Date.now() - saved.at < 15000) { show(saved.done); setTimeout(() => { if (!timer && badge) { badge.remove(); badge = null; } }, 8000); }
    } catch (_) { /* Nothing to carry over. */ }
  };
  const ready = () => { start(); resume(); };
  if (document.body) ready(); else document.addEventListener('DOMContentLoaded', ready);
})();
