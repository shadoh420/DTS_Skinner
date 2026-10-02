import {Suspense, useCallback, useEffect, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {QueryClient, QueryClientProvider} from '@tanstack/react-query';
import {NuqsAdapter} from 'nuqs/adapters/react';
import {ErrorBoundary} from 'react-error-boundary';
import {SettingsProvider, useSettings} from '../src/components/SettingsProvider';
import {InputProvider} from '../src/components/InputProducer';
import {GameView} from '../src/components/GameView';
import {useProgress} from '@react-three/drei';
import {MouseLookProvider, useMouseLook} from './mouse-look';
import {addNote, useNotes} from './notes';
import manifest from './manifest';

type Mission = {displayName: string | null; missionTypes: string[]; source?: string};
const missions: Record<string, Mission> = manifest.missions;
// A display name that is not the file name (the Training missions: "Newblood") is shown after it.
const flat = (text: string) => text.toLowerCase().replace(/[^a-z0-9]/g, '');
function label(name: string) {
  const display = missions[name].displayName;
  return !display ? name : flat(name).startsWith(flat(display)) ? display : `${name} · ${display}`;
}
const names = Object.keys(missions).sort((a, b) => label(a).localeCompare(label(b)));
// Upstream's group names for these archives; any other archive is listed under its own name.
const groupNames: Record<string, string> = {'missions.vl2': 'Official', 'Classic_maps_v1.vl2': 'Classic', 'TR2final105-client.vl2': 'Team Rabbit 2'};
const group = (name: string) => groupNames[missions[name].source ?? ''] ?? missions[name].source ?? 'Maps';
const groups = [...new Set(names.map(group))].sort((a, b) => Number(b === 'Official') - Number(a === 'Official') || a.localeCompare(b));

// The map in view is ?mission=Name~Type, as upstream links it; the type defaults to the mission's first.
function pick(name: string, type?: string): [string, string] {
  const key = names.find(item => item.toLowerCase() === name.toLowerCase()) ?? (missions.Katabatic ? 'Katabatic' : names[0] ?? '');
  const types = missions[key]?.missionTypes ?? [];
  return [key, type && types.includes(type) ? type : types[0] ?? ''];
}
const [name, type] = (() => {
  const [name, type] = (new URLSearchParams(location.search).get('mission') ?? '').split('~');
  return pick(name, type);
})();
// A map opens on a fresh page, as in the T1 viewer: upstream switches in place, but its caches keep every map's
// textures (1.6 GB after all 84) and report an unresolved file only the first time it is asked for.
function choose([next, nextType]: [string, string]) {
  location.assign(`${location.pathname}?mission=${encodeURIComponent(next)}${missions[next].missionTypes.length > 1 ? '~' + encodeURIComponent(nextType) : ''}`);
}

const client = new QueryClient({defaultOptions:{queries:{refetchOnWindowFocus:false,retry:false}}});
const gameKey = 'skinner.t2maps.game';
function ImportPanel() {
  const [game, setGame] = useState(() => { try { return localStorage.getItem(gameKey) || ''; } catch { return ''; } });
  const [replace, setReplace] = useState(false);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("Imports every mission in the game's stock, Classic and Team Rabbit 2 archives. The game folder is only read.");
  async function run() {
    if (!game.trim()) { setStatus('Enter your Tribes 2 folder.'); return; }
    setBusy(true); setStatus('Importing maps… this copies about 370 MB.');
    try {
      try { localStorage.setItem(gameKey, game.trim()); } catch {/* Storage may be unavailable. */}
      const response = await fetch('/import_t2_maps', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({game: game.trim(), replace})});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Import failed');
      if (result.imported.length) { location.reload(); return; }
      setStatus(`${result.skipped.length} maps are already imported. Tick Re-import existing maps to rebuild the pack.`);
    } catch (error) { setStatus(String((error as Error).message || error)); }
    setBusy(false);
  }
  return <details className="import" open={!names.length || undefined}><summary>Import maps</summary><div>
    <label>Tribes 2 folder <input type="text" value={game} onChange={e=>setGame(e.target.value)} placeholder="C:\Dynamix\Tribes2\GameData"/></label>
    <label><input type="checkbox" checked={replace} onChange={e=>setReplace(e.target.checked)}/> Re-import existing maps</label>
    <button disabled={busy} onClick={run}>Import</button><span role="status">{status}</span>
  </div></details>;
}

function Viewer() {
  const [missionLoading, setMissionLoading] = useState(true);
  const [progress, setProgress] = useState(0);
  const assets = useProgress();
  const notes = useNotes().filter(() => name);  // Without a pack there is no map for a note to be about.
  const {fov, setFov, fogEnabled, setFogEnabled} = useSettings();
  const {settings, setSettings} = useMouseLook();
  const onLoadingChange = useCallback((loading:boolean, amount = 0) => {setMissionLoading(loading); setProgress(amount);},[]);
  // Upstream waits for the mission's scripts to report ready, without a limit.
  useEffect(() => {
    if (!name || !missionLoading) return;
    const timer = setTimeout(() => addNote('Mission scripts have not finished loading after 90 seconds; the scene may be incomplete.'), 90000);
    return () => clearTimeout(timer);
  }, [missionLoading]);
  return <>
    <header><a href="/">← Models</a><strong>T2 Maps</strong>
      {name && <select aria-label="Map" value={name} onChange={e=>choose(pick(e.target.value))}>
        {groups.map(item => <optgroup key={item} label={item}>{names.filter(mission => group(mission) === item).map(mission =>
          <option key={mission} value={mission}>{label(mission)}</option>)}</optgroup>)}
      </select>}
      {name && missions[name].missionTypes.length > 1 && <select aria-label="Mission type" value={type} onChange={e=>choose([name, e.target.value])}>
        {missions[name].missionTypes.map(item => <option key={item}>{item}</option>)}
      </select>}
      <label>FOV <input aria-label="Map field of view" type="number" min="30" max="110" value={fov} onChange={e=>setFov(Math.max(30,Math.min(110,Number(e.target.value)||90)))}/></label>
      <label><input type="checkbox" checked={fogEnabled} onChange={e=>setFogEnabled(e.target.checked)}/> Fog</label>
      <label><input type="checkbox" checked={settings.invertX} onChange={e=>setSettings({...settings,invertX:e.target.checked})}/> Invert horizontal</label>
      <label><input type="checkbox" checked={settings.invertY} onChange={e=>setSettings({...settings,invertY:e.target.checked})}/> Invert vertical</label>
      <button onClick={()=>{location.hash=''; location.reload();}}>Reset view</button>
      <ImportPanel/>
    </header>
    <main>{name
      // The canvas state stays reachable so a build can be checked where the browser draws no frames by itself.
      ? <InputProvider><GameView missionName={name} missionType={type} onLoadingChange={onLoadingChange} dpr={1} onCreated={state=>{(window as any).skinnerMapCanvas = state;}}/></InputProvider>
      : <p>No Tribes 2 maps are imported yet. Open Import maps above and enter your Tribes 2 folder.</p>}</main>
    <footer><span role="status">{!name ? 'No maps imported' : missionLoading ? `Loading mission ${Math.round(progress*100)}%` : assets.active ? `Loading map assets ${Math.round(assets.progress)}%` : assets.errors.length ? `Map loaded with ${assets.errors.length} missing assets` : 'Map ready'}</span>
      <span>Click to capture / drag to look · WASD move · Space up · Shift down · Wheel speed · Esc release · 1–9 viewpoints</span>
      <details><summary>Preview notes{notes.length ? ` (${notes.length})` : ''}</summary>Free-flight preview without collision, gameplay, audio or map editing. What the renderer could not resolve on this map is listed here. Renderer by <a href="https://github.com/exogen/t2-mapper">exogen</a> · <a href="/static/t2-maps/THIRD-PARTY-NOTICES.txt">Notices</a>
        {notes.length > 0 && <ul>{notes.map(note => <li key={note}>{note}</li>)}</ul>}</details></footer>
  </>;
}
createRoot(document.getElementById('root')!).render(
  <ErrorBoundary fallbackRender={({error})=><p>Map could not load: {String(error)} <a href="/">Back to models</a></p>}>
    <Suspense fallback={<p>Loading map components…</p>}><NuqsAdapter><QueryClientProvider client={client}><SettingsProvider><MouseLookProvider><Viewer/></MouseLookProvider></SettingsProvider></QueryClientProvider></NuqsAdapter></Suspense>
  </ErrorBoundary>
);
