import {defineConfig} from 'vite';
import path from 'node:path';
export default defineConfig({
  base:'/static/t2-maps/',
  publicDir:false,
  define:{'process.env':JSON.stringify({BASE_PATH:'/static/t2-maps/',GAME_ASSETS_BASE_URL:'/t2-map-data/base/',LOG_LEVEL:'warn',RELAY_URL:'',DEMOS_BASE_URL:'',CAST_BASE_URL:''})},
  esbuild:{jsx:'automatic'},
  resolve:{alias:[{find:/^\.\/manifest\.json$/,replacement:path.resolve('skinner/manifest.ts')}]},
  plugins:[{name:'offline-settings',transform(source,id){
    if(id.endsWith('/SettingsProvider.tsx')) return source.replace('const [audioEnabled, setAudioEnabled] = useState(true);','const [audioEnabled, setAudioEnabled] = useState(false);').replaceAll('localStorage.getItem("settings")','localStorage.getItem("skinner.t2maps.settings")').replaceAll('localStorage.setItem("settings",','localStorage.setItem("skinner.t2maps.settings",');
    if(id.endsWith('/PlayerModel.tsx')) return source.replace('https://assets.tribes2.online/skins/files/','/t2-map-data/skins/').replace('https://assets.tribes2.online/skins/manifest.json','/t2-map-data/skins.json');
    if(id.endsWith('/MouseAndKeyboardHandler.tsx')) {
      const replacements = [
        ['export function MouseAndKeyboardHandler() {', 'export function MouseAndKeyboardHandler() {\n  const {settings: {invertX, invertY}} = useMouseLook();'],
        ['gl.domElement.requestPointerLock();', 'gl.domElement.requestPointerLock()?.catch(() => console.warn("Mouse capture unavailable; drag to look and use WASD."));'],
        ['deltaYaw = locked.deltaX * mouseSensitivity;', 'deltaYaw = mouseDelta(locked.deltaX, mouseSensitivity, invertX);'],
        ['deltaPitch = locked.deltaY * mouseSensitivity;', 'deltaPitch = mouseDelta(locked.deltaY, mouseSensitivity, invertY);'],
        ['deltaYaw += dragSign * drag.deltaX * DRAG_SENSITIVITY;', 'deltaYaw += mouseDelta(drag.deltaX, DRAG_SENSITIVITY, invertX);'],
        ['deltaPitch += dragSign * drag.deltaY * DRAG_SENSITIVITY;', 'deltaPitch += mouseDelta(drag.deltaY, DRAG_SENSITIVITY, invertY);'],
      ];
      for (const [before, after] of replacements) {
        if(!source.includes(before)) throw new Error('Upstream mouse input changed: ' + before);
        source = source.replace(before, after);
      }
      return 'import {mouseDelta, useMouseLook} from "../../skinner/mouse-look";\n' + source;
    }
  }}],
  build:{target:'esnext',outDir:process.env.SKINNER_MAP_OUTPUT,emptyOutDir:true,rollupOptions:{input:path.resolve('skinner/index.html')}},
});
