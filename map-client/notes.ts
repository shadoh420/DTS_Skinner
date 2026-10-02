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
export function addNote(...parts: unknown[]) {
  const rest = [...parts];
  const prefix = typeof rest[0] === 'string' && /^\[[\w-]+\]$/.test(rest[0]) ? rest.shift() + ' ' : '';
  const first = rest.shift();
  const body = typeof first === 'string' ? first.replace(/%[sdoO]/g, () => rest.length ? text(rest.shift()) : '') : text(first);
  const note = (prefix + [body, ...rest.map(text)].join(' ')).slice(0, 400);
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
    if (level === 'error' || /^\[(loaders|scriptLoader)\]$/.test(String(parts[0])) && !parts.some(part => /\bprefs\//i.test(String(part)))) addNote(...parts);
  };
}
