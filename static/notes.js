// Notes on the model or map in view, saved to local-data/notes.json (tools/notes.py) for agents to read later.
// One shared box for the model browser and every map page; app.py adds this script to the map pages.
(() => {
  // The model browser names its model (skinner.js sets data-notes-*); map pages have <select id="map">, T2 ?mission=.
  function subject() {
    const data = document.body.dataset;
    if (data.notesPage) return {page: data.notesPage, subject: data.notesSubject || ''};
    const map = document.getElementById('map');
    const mission = new URLSearchParams(location.search).get('mission');
    return {page: location.pathname, subject: (map && map.value) || mission || ''};
  }
  const make = (tag, css, text) => { const el = document.createElement(tag); el.style.cssText = css; if (text) el.textContent = text; return el; };
  const font = 'font: 13px system-ui, sans-serif;';
  const button = make('button', `${font} position: fixed; right: 12px; bottom: 12px; z-index: 2147483647; padding: 6px 12px; border-radius: 6px; border: 1px solid #556; background: #223; color: #eef; cursor: pointer;`, 'Notes');
  const panel = make('div', `${font} position: fixed; right: 12px; bottom: 48px; z-index: 2147483647; width: min(360px, calc(100vw - 24px)); padding: 10px; border-radius: 8px; border: 1px solid #556; background: #1b1d26; color: #dde; box-shadow: 0 6px 24px #0008; display: none;`);
  const title = make('div', 'font-weight: 600; margin-bottom: 6px; overflow-wrap: anywhere;');
  const text = make('textarea', `${font} width: 100%; box-sizing: border-box; height: 160px; resize: vertical; background: #10121a; color: #eef; border: 1px solid #445; border-radius: 4px; padding: 6px;`);
  text.placeholder = 'A texture issue, a wrong piece, anything worth checking later…';
  text.setAttribute('aria-label', 'Note');
  const status = make('div', 'opacity: .7; margin-top: 4px; min-height: 1.2em;');
  status.setAttribute('role', 'status');
  panel.append(title, text, status);
  // Map viewers fly on WASD; keep typing out of their key handlers.
  for (const type of ['keydown', 'keyup', 'keypress']) text.addEventListener(type, event => event.stopPropagation());

  let current = null, saved = '', timer = 0;
  const key = s => `${s.page}\n${s.subject}`;
  async function save() {
    clearTimeout(timer);
    if (!current || text.value === saved) return;
    const body = {...current, text: text.value};
    try {
      const response = await fetch('/notes', {method: 'POST', keepalive: true, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Not saved');
      if (current && key(current) === key(body)) { saved = body.text; status.textContent = body.text.trim() ? 'Saved' : 'Note removed'; mark(); }
    } catch (error) { status.textContent = `Not saved: ${error.message}`; }
  }
  const mark = () => { button.textContent = saved.trim() ? 'Notes •' : 'Notes'; };
  async function load() {
    const next = subject();
    if (current && key(current) === key(next)) return;
    await save();
    current = next; saved = text.value = ''; mark();
    title.textContent = `Note: ${next.subject || 'this page'}`;
    status.textContent = '';
    try {
      const response = await fetch(`/notes?page=${encodeURIComponent(next.page)}&subject=${encodeURIComponent(next.subject)}`);
      const [note] = await response.json();
      if (current === next) { saved = text.value = note ? note.text : ''; mark(); }
    } catch (error) { status.textContent = `Notes unavailable: ${error.message}`; }
  }
  text.addEventListener('input', () => { status.textContent = ''; clearTimeout(timer); timer = setTimeout(save, 700); });
  text.addEventListener('blur', save);
  button.addEventListener('click', () => { panel.style.display = panel.style.display === 'none' ? 'block' : 'none'; if (panel.style.display === 'block') text.focus(); });
  addEventListener('pagehide', save);
  document.body.append(panel, button);
  load();
  // ponytail: polls for the model or map in view, since each of the 15 pages switches it its own way; 0.5 s is plenty.
  setInterval(load, 500);
})();
