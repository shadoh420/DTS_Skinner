// Loads every mission of an imported pack, in each of its types, through upstream's script runtime, the way
// its missionLoad.integration.spec.ts loads one, and lists what fails or is not in the pack.
// Run from the pinned upstream checkout, with LOG_LEVEL=error and TSX_TSCONFIG_PATH=tsconfig.node.json set:
//   node --import=tsx/esm skinner/check-pack.ts <pack> [report.json]
import {readFileSync, writeFileSync} from 'node:fs';
import path from 'node:path';
import picomatch from 'picomatch';
import {parseMissionScript} from '../src/mission';
import {runServer} from '../src/torqueScript';
import {createScriptLoader} from '../src/torqueScript/scriptLoader.node.ts';
import {ignoreScripts} from '../src/torqueScript/ignoreScripts';
import {walkMissionTree} from '../src/stream/missionEntityBridge';

const pack = process.argv[2];
if (!pack) throw new Error('Pass the imported map pack directory');
const manifest = JSON.parse(readFileSync(path.join(pack, 'manifest.json'), 'utf8'));
const mounts = JSON.parse(readFileSync(path.join(pack, 'mounts.json'), 'utf8'));  // Written when the viewer first opens the pack.
const files = Object.keys(manifest.resources);
const has = (file: string) => file.toLowerCase() in manifest.resources;
const fileSystem = {
  findFiles: (pattern: string) => { const matches = picomatch(pattern, {nocase: true}); return files.filter(file => matches(file)); },
  isFile: (file: string) => has(file.replace(/\\/g, '/')),
};
const loader = createScriptLoader({searchPaths: [path.join(pack, 'base')]});
const loadScript = (file: string) => loader(file.toLowerCase());  // The pack's paths are lowercase.
const quiet = {log: console.log, warn: console.warn, error: console.error, debug: console.debug, info: console.info};
const report: Record<string, string[]> = {};
let runs = 0;

for (const [name, mission] of Object.entries<any>(manifest.missions).sort()) {
  const problems: string[] = [];
  // The importer reads names and types in Python; upstream's parser is the reference.
  const parsed = parseMissionScript(readFileSync(path.join(pack, 'base', mission.resourcePath), 'utf8'));
  if (parsed.displayName !== mission.displayName || parsed.missionTypes.join() !== mission.missionTypes.join())
    problems.push(`manifest says ${mission.displayName} [${mission.missionTypes}], upstream reads ${parsed.displayName} [${parsed.missionTypes}]`);
  for (const type of mission.missionTypes.length ? mission.missionTypes : ['']) {
    runs++;
    const errors = new Set<string>();
    console.log = console.warn = console.debug = console.info = () => {};
    console.error = (...parts: unknown[]) => { errors.add(parts.map(String).join(' ').slice(0, 200)); };
    const controller = new AbortController();
    let timer: NodeJS.Timeout | undefined;
    try {
      const {runtime, ready} = runServer({missionName: name, missionType: type,
        runtimeOptions: {loadScript, fileSystem, ignoreScripts, signal: controller.signal, mountTransforms: mounts} as any});
      try {
        await Promise.race([ready, new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('scripts not ready after 60 s')), 60000); })]);
        const group = runtime.getObjectByName('MissionGroup');
        if (!group) throw new Error('no MissionGroup');
        const cleanup = runtime.getObjectByName('MissionCleanup');
        const missing = new Set<string>();
        const need = (file: string, what: string) => { if (!has(file)) missing.add(`${what} ${file}`); };
        for (const entity of [...walkMissionTree(group, runtime, type), ...(cleanup ? walkMissionTree(cleanup, runtime, type) : [])] as any[]) {
          if (entity.terrainData) need('terrains/' + entity.terrainData.terrFileName, 'terrain');
          if (entity.interiorData) need('interiors/' + entity.interiorData.interiorFile, 'interior');
          if (entity.skyData?.materialList) need('textures/' + entity.skyData.materialList, 'sky');
          if (entity.renderType === 'Shape' && !entity.shapeName) missing.add(`shape for ${entity.className} ${entity.dataBlock ?? ''}`.trim());
          else if (entity.shapeName) need('shapes/' + entity.shapeName, 'shape');
        }
        for (const item of missing) problems.push(`${type}: missing ${item}`);
      } finally {
        clearTimeout(timer);
        controller.abort();
        runtime.destroy();
      }
    } catch (error) {
      problems.push(`${type}: ${(error as Error).message}`);
    }
    Object.assign(console, quiet);
    for (const error of errors) problems.push(`${type}: ${error}`);
  }
  console.log(problems.length ? `FAIL ${name}\n  ${problems.join('\n  ')}` : `ok   ${name} [${mission.missionTypes}]`);
  if (problems.length) report[name] = problems;
}
console.log(`${Object.keys(manifest.missions).length} missions, ${runs} mission/type loads, ${Object.keys(report).length} with problems`);
if (process.argv[3]) writeFileSync(process.argv[3], JSON.stringify(report, null, 2));
process.exit(Object.keys(report).length ? 1 : 0);
