// The pack's manifest in place of upstream's bundled src/manifest.json. Mount transforms are read from the
// pack's shapes with upstream's own reader the first time a pack is opened, then kept beside the manifest.
import {parseDTS} from '../src/dts/dts';
import {extractMountTransforms} from '../scripts/lib/mounts';

const data = '/t2-map-data/';
const response = await fetch(data + 'manifest.json');
// Without a pack the page still opens, so maps can be imported from it.
const manifest = response.ok ? await response.json() : {resources: {}, missions: {}, mounts: {}};
if (response.ok && !Object.keys(manifest.mounts ?? {}).length) {
  const saved = await fetch(data + 'mounts.json');
  if (saved.ok) manifest.mounts = await saved.json();
  else {
    const shapes = Object.keys(manifest.resources).filter(key => key.endsWith('.dts')).sort();
    const found = await Promise.all(shapes.map(async key => {
      try {
        const buffer = await (await fetch(data + 'base/' + key)).arrayBuffer();
        return buffer.byteLength ? extractMountTransforms(parseDTS(buffer)) : null;  // Stock xorg2.dts is empty.
      } catch (error) {
        console.warn(`Shape ${key} could not be read for its mount points: ${error}`);
        return null;
      }
    }));
    // Keyed by basename, a later path winning, as upstream's manifest builder does.
    manifest.mounts = {};
    shapes.forEach((key, index) => { if (found[index]) manifest.mounts[key.split('/').pop()!.slice(0, -4)] = found[index]; });
    await fetch('/t2_map_mounts', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(manifest.mounts)}).catch(() => {});
  }
}
export default manifest;
