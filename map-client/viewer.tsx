import {Suspense, useCallback, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {QueryClient, QueryClientProvider} from '@tanstack/react-query';
import {NuqsAdapter} from 'nuqs/adapters/react';
import {ErrorBoundary} from 'react-error-boundary';
import {SettingsProvider, useSettings} from '../src/components/SettingsProvider';
import {InputProvider} from '../src/components/InputProducer';
import {GameView} from '../src/components/GameView';
import {useProgress} from '@react-three/drei';
import {MouseLookProvider, useMouseLook} from './mouse-look';

const client = new QueryClient({defaultOptions:{queries:{refetchOnWindowFocus:false,retry:false}}});
function Viewer() {
  const [missionLoading, setMissionLoading] = useState(true);
  const [progress, setProgress] = useState(0);
  const assets = useProgress();
  const {fov, setFov, fogEnabled, setFogEnabled} = useSettings();
  const {settings, setSettings} = useMouseLook();
  const onLoadingChange = useCallback((loading:boolean, amount = 0) => {setMissionLoading(loading); setProgress(amount);},[]);
  return <>
    <header><a href="/">← Models</a><strong>T2 Maps · Katabatic</strong><span>Offline preview · free-flight, no collision or audio</span>
      <label>FOV <input aria-label="Map field of view" type="number" min="30" max="110" value={fov} onChange={e=>setFov(Math.max(30,Math.min(110,Number(e.target.value)||90)))}/></label>
      <label><input type="checkbox" checked={fogEnabled} onChange={e=>setFogEnabled(e.target.checked)}/> Fog</label>
      <label><input type="checkbox" checked={settings.invertX} onChange={e=>setSettings({...settings,invertX:e.target.checked})}/> Invert horizontal</label>
      <label><input type="checkbox" checked={settings.invertY} onChange={e=>setSettings({...settings,invertY:e.target.checked})}/> Invert vertical</label>
      <button onClick={()=>{location.hash=''; location.reload();}}>Reset view</button>
    </header>
    <main><InputProvider><GameView missionName="Katabatic" missionType="CTF" onLoadingChange={onLoadingChange} dpr={1}/></InputProvider></main>
    <footer><span role="status">{missionLoading ? `Loading mission ${Math.round(progress*100)}%` : assets.active ? `Loading map assets ${Math.round(assets.progress)}%` : assets.errors.length ? `Map loaded with ${assets.errors.length} missing assets` : 'Map ready'}</span>
      <span>Click to capture / drag to look · WASD move · Space up · Shift down · Wheel speed · Esc release · 1–9 viewpoints</span>
      <details><summary>Preview notes</summary>Katabatic CTF only. Stock axe texture and the sky environment-map reference are missing; upstream fallbacks remain. No gameplay or map editing. Renderer by <a href="https://github.com/exogen/t2-mapper">exogen</a> · <a href="/static/t2-maps/THIRD-PARTY-NOTICES.txt">Notices</a></details></footer>
  </>;
}
createRoot(document.getElementById('root')!).render(
  <ErrorBoundary fallbackRender={({error})=><p>Map could not load: {String(error)} <a href="/">Back to models</a></p>}>
    <Suspense fallback={<p>Loading map components…</p>}><NuqsAdapter><QueryClientProvider client={client}><SettingsProvider><MouseLookProvider><Viewer/></MouseLookProvider></SettingsProvider></QueryClientProvider></NuqsAdapter></Suspense>
  </ErrorBoundary>
);
