import {defineConfig} from 'vite';
import path from 'node:path';
export default defineConfig({
  base:'/static/t2-maps/',
  publicDir:false,
  define:{'process.env':JSON.stringify({BASE_PATH:'/static/t2-maps/',GAME_ASSETS_BASE_URL:'/t2-map-data/base/',LOG_LEVEL:'warn',RELAY_URL:'',DEMOS_BASE_URL:'',CAST_BASE_URL:''})},
  esbuild:{jsx:'automatic'},
  resolve:{alias:[{find:/^\.\/manifest\.json$/,replacement:path.resolve('skinner/manifest.ts')}]},
  plugins:[{name:'local-map-npcs',enforce:'pre',transform(source,id){
    if(id.endsWith('/GameView.tsx')) {
      const before = '<EntityScene />';
      if(!source.includes(before)) throw new Error('Upstream GameView changed: ' + before);
      return 'import {MapNPCs} from "../../skinner/npcs";\nimport {MapEnvironment} from "../../skinner/environment";\n' + source.replace(before, before + '\n<MapNPCs />\n<MapEnvironment />');
    }
  }}, {name:'mission-environment',enforce:'pre',transform(source,id){
    const patch = (replacements: string[][], what: string) => {
      source = source.replace(/\r\n/g, '\n');  // The checkout may have Windows line endings.
      for (const [before, after] of replacements) {
        if(source.split(before).length !== 2) throw new Error(`Upstream ${what} changed: ${before}`);
        source = source.replace(before, after);
      }
      return source;
    };
    if(id.endsWith('/stream/missionEntityBridge.ts')) {
      // Mission-placed vehicles are ShapeBase objects drawn from their datablock's shape, as static shapes are;
      // the mission type hides them the same way. Upstream leaves them out.
      return patch([
        ['  "waypoint",\n]);', '  "waypoint",\n  "flyingvehicle",\n  "hovervehicle",\n  "wheeledvehicle",\n]);'],
        ['    case "StaticShape":\n    case "Item":', '    case "FlyingVehicle":\n    case "HoverVehicle":\n    case "WheeledVehicle":\n    case "StaticShape":\n    case "Item":'],
      ], 'mission entity bridge');
    }
    if(id.endsWith('/dif/dif.ts')) {
      // Keep the animated lights upstream skips, for skinner/interiorLights (Interior v0 layout, as Interior::read).
      return patch([
        ['  r.skipArray("animated lights", 16);\n  r.skipArray("light states", 13);\n  r.skipArray("light state data", 10);\n  const stateBufferSize = r.count("light state buffer", 1);\n  r.u32(); // flags\n  r.skip(stateBufferSize);',
         '  const animatedLights = r.array("animated lights", 16, () => ({nameIndex: r.u32(), stateIndex: r.u32(), stateCount: r.u16(), flags: r.u16(), duration: r.u32()}));\n' +
         '  const lightStates = r.array("light states", 13, () => ({red: r.u8(), green: r.u8(), blue: r.u8(), activeTime: r.u32(), dataIndex: r.u32(), dataCount: r.u16()}));\n' +
         '  const lightStateData = r.array("light state data", 10, () => ({surfaceIndex: r.u32(), mapIndex: r.u32(), lightStateIndex: r.u16()}));\n' +
         '  const stateBufferSize = r.count("light state buffer", 1);\n  r.u32(); // flags\n  const stateDataBuffer = r.bytes.slice(r.offset, r.offset + stateBufferSize);\n  r.skip(stateBufferSize);'],
        ['    coordBins,\n    coordBinIndices,\n  };', '    coordBins,\n    coordBinIndices,\n    animatedLights,\n    lightStates,\n    lightStateData,\n    stateDataBuffer,\n  };'],
      ], 'interior reader');
    }
    if(id.endsWith('/dif/difLoader.ts')) {
      return 'import {animateInteriorLights} from "../../skinner/interiorLights";\n' + patch([
        ['      throw failure.reason;\n    }\n    return model;', '      throw failure.reason;\n    }\n    animateInteriorLights(model);\n    return model;'],
      ], 'interior loader');
    }
  }}, {name:'offline-settings',transform(source,id){
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
    if(id.endsWith('/WaterBlock.tsx')) {
      // A water block's environment map that is not in the game files added upstream's white fallback texture to
      // the water, at full strength. It adds nothing instead, as upstream's sky does with a missing environment map.
      const before = 'textureToUrl(envMapTexture ?? "special/lush_env")';
      if(!source.includes(before)) throw new Error('Upstream water environment map changed: ' + before);
      return source.replace(before, 'textureToUrl(envMapTexture ?? "special/lush_env", process.env.BASE_PATH + "black.png")');
    }
    if(id.endsWith('/shapeMaterial.ts')) {
      // Three declares transformedNormal for an unlit material only when it is skinned or has Three's own envMap, so
      // upstream's reflection lookup does not compile for the instanced glowing parts of shapes and they are not drawn.
      // The instanced normal is computed here as Three's defaultnormal_vertex does.
      const before = '#elif defined(USE_SKINNING) || defined(USE_INSTANCING)';
      if(!source.includes(before)) throw new Error('Upstream shape reflection shader changed: ' + before);
      return source.replace(before, `#elif defined(USE_INSTANCING) && !defined(USE_SKINNING)
    mat3 _im = mat3(instanceMatrix);
    vec3 _eyeN = normalize(normalMatrix * (_im * (normal / vec3(dot(_im[0], _im[0]), dot(_im[1], _im[1]), dot(_im[2], _im[2])))));
  #elif defined(USE_SKINNING)`);
    }
  }}],
  build:{target:'esnext',outDir:process.env.SKINNER_MAP_OUTPUT,emptyOutDir:true,rollupOptions:{input:path.resolve('skinner/index.html')}},
});
