/* A map's entities file, as tools/import_diabotical_map.py writes it (maps/ID-HASH.ent): u32 length of a JSON head
   (padded to 4 bytes), the head {props: [["model|material|m", count, tinted]], markers: [[kind, x, y, z]], liquids:
   [[x, y, z, width, height, depth, material, colour 0xRRGGBB or null, alpha 0-255 or null, ocean 0/1]], decals: [[material, count]], lights (read_lights in the importer), billboards (read_billboards), pfx (read_pfx)}, then each prop group's page matrices in
   turn, 12 float32 each (the top three rows, row by row), then for each tinted group its props' color, color2 and
   color3, 3 uint32 each (0x1RRGGBB, or 0 where the material's own accent stands), then each decal group's boxes
   (24 float32 each: the matrix of the box its texture is projected from, then of the box it is cut to), then per decal its colour (0xRRGGBBAA), flags (1 mirrored, 2 v2, 4 v3) and order
   (int32). Material is empty where the model's own are drawn, m
   marks mirrored props. Marker and liquid positions are the game's (the page's x, y, -z). Node runs it too
   (tests/diabotical_maps.test.cjs). */
(function (exports) {
  'use strict';
  function parseEntities(buffer) {
    const length = new DataView(buffer).getUint32(0, true);
    const head = JSON.parse(new TextDecoder().decode(new Uint8Array(buffer, 4, length)));
    let at = 4 + length;
    const props = head.props.map(([key, count]) => {
      const [model, material, mirrored] = key.split('|');
      const matrices = new Float32Array(buffer, at, count * 12);
      at += count * 48;
      return {model, material, mirrored: mirrored === 'm', matrices, tints: null};
    });
    head.props.forEach(([, count, tinted], i) => {
      if (!tinted) return;
      props[i].tints = new Uint32Array(buffer, at, count * 3);
      at += count * 12;
    });
    const decals = head.decals.map(([material, count]) => {
      const matrices = new Float32Array(buffer, at, count * 24);
      at += count * 96;
      return {material, matrices};
    });
    for (const decal of decals) {
      const count = decal.matrices.length / 24;
      Object.assign(decal, {extras: new Uint32Array(buffer, at, count * 3), orders: new Int32Array(buffer, at, count * 3)});
      at += count * 12;
    }
    return {props, markers: head.markers, liquids: head.liquids, decals, lights: head.lights || null, billboards: head.billboards || [], pfx: head.pfx || []};
  }

  exports.DiaboticalEntities = {parseEntities};
})(typeof module !== 'undefined' ? module.exports : window);
