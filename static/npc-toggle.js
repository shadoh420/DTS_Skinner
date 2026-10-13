// Shared Maps control. Viewers set the count and callback; empty maps keep the saved preference.
(() => {
  'use strict';
  const key = 'skinner.maps.' + (location.pathname.split('/')[2] || 't2') + '.npcs';
  let enabled = true;
  try { enabled = localStorage.getItem(key) !== 'false'; } catch (_) { /* Storage may be unavailable. */ }
  const label = document.createElement('label'), input = document.createElement('input'), text = document.createElement('span');
  label.id = 'npc-toggle';
  input.type = 'checkbox';
  input.checked = enabled;
  input.setAttribute('aria-label', 'NPCs');
  label.append(input, text);
  const control = window.skinnerNPCToggle = {
    get enabled() { return enabled; },
    onChange: () => {},
    setCount(n) {
      const count = Number.isFinite(n) ? Math.max(0, Math.floor(n)) : 0;
      input.disabled = count === 0;
      text.textContent = count ? `NPCs (${count})` : 'NPCs (none)';
      label.title = count ? 'Show or hide NPCs' : 'No NPCs on this map';
    }
  };
  input.addEventListener('change', () => {
    enabled = input.checked;
    try { localStorage.setItem(key, String(enabled)); } catch (_) { /* Keep the in-page preference. */ }
    control.onChange(enabled);
  });
  // Space toggles the checkbox without also moving a map camera.
  for (const type of ['keydown', 'keyup', 'keypress']) input.addEventListener(type, event => event.stopPropagation());
  control.setCount(0);
  function mount() {
    const header = document.querySelector('header');
    if (!header) return false;
    header.append(label);
    return true;
  }
  function ready() {
    if (mount()) return;
    // T2 creates its header asynchronously in React. Observe only until it is available.
    const observer = new MutationObserver(() => { if (mount()) observer.disconnect(); });
    observer.observe(document.body, {childList: true, subtree: true});
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', ready, {once: true});
  else ready();
})();
