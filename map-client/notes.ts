// What the renderer warned about on the map in view, for Preview notes. Upstream reports an
// unresolved resource, shape or script only through its logger, which writes to the console.
import {useSyncExternalStore} from 'react';

let notes: string[] = [];
const listeners = new Set<() => void>();
const changed = () => queueMicrotask(() => listeners.forEach(listener => listener()));  // Never during a render.
function text(value: unknown): string {
  if (value instanceof Error) return value.message;
  try { return typeof value === 'object' ? JSON.stringify(value) : String(value); } catch { return String(value); }
}
// Upstream's log lines in plain words. The files listed are ones the stock game names but does not hold.
const stockGaps = /^textures\/(skins\/axe|special\/lush_env|lava\/tlite1t|desert\/skies\/ice_blue_emap|ice\/skies\/icebound_emap_cloudsground|lava\/skies\/volcanic_starrynite_v5_dn)$/i;
function plain(note: string): string {
  const file = note.match(/^\[loaders\] Resource "(.+?)" not found/);
  if (file) return `Not in the game files: ${file[1]}` + (stockGaps.test(file[1]) ? ' (a known gap in the stock game files; the game lacks it too)' : '');
  const script = note.match(/^\[scriptLoader\] Script not in manifest: (\S+)/);
  if (script) return `Script not in the game files: ${script[1]}`;
  if (note.startsWith('THREE.WebGLProgram: Shader Error')) return 'A shader did not compile, so something on this map is not drawn; the browser console has the details.';
  return note.split('\n')[0];
}
export function addNote(...parts: unknown[]) {
  const rest = [...parts];
  const prefix = typeof rest[0] === 'string' && /^\[[\w-]+\]$/.test(rest[0]) ? rest.shift() + ' ' : '';
  const first = rest.shift();
  const body = typeof first === 'string' ? first.replace(/%[sdoO]/g, () => rest.length ? text(rest.shift()) : '') : text(first);
  const note = plain(prefix + [body, ...rest.map(text)].join(' ')).slice(0, 400);
  // ponytail: capped list; a map that warns more than this needs the browser console anyway.
  if (notes.length < 200 && !notes.includes(note)) { notes = [...notes, note]; changed(); }
}
export const useNotes = () => useSyncExternalStore(
  listener => { listeners.add(listener); return () => { listeners.delete(listener); }; }, () => notes);

// Errors are all kept. Of the warnings only unresolved files are: the rest is the script runtime naming the
// scripts upstream skips and the engine functions it does not simulate, the same on every map. Scripts under
// prefs/ are a server's own settings (ban list, records), which the game writes itself and no archive holds.
for (const level of ['warn', 'error'] as const) {
  const original = console[level].bind(console);
  console[level] = (...parts: unknown[]) => {
    original(...parts);
    // A bare line of text sent to console.error is a mission script calling error() to print; upstream's own start
    // with a [module], and Three's and the script runtime's with their names.
    if (level === 'error' && typeof parts[0] === 'string' && !/^(\[|THREE\.|schedule: |exec: |Warning: |Error)/.test(parts[0])) addNote('A mission script printed: ' + parts.map(String).join(' '));
    else if (level === 'error' || /^\[(loaders|scriptLoader)\]$/.test(String(parts[0])) && !parts.some(part => /\bprefs\//i.test(String(part)))) addNote(...parts);
  };
}
