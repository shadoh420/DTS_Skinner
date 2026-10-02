// Hidden browser check of the Reflex Maps page: node tools/check_reflex_maps.cjs URL [output directory].
// Starts a new map, builds and carves brushes with the keyboard and mouse, undoes and redoes, saves the map and reads
// the download back; works the game's editor gestures, create types, vertex mode, the property panel, the bridge tool,
// turning, the texture keys, the clipper and prefabs; then, when maps are imported, draws the first one and checks it is
// not blank.
// Uses the same optional Playwright dependency as check_browser.cjs.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {ReflexMap: M} = require('../static/reflex-maps/mapfile.js');
const {ReflexBrush: B} = require('../static/reflex-maps/brush.js');
(async () => {
  const base = process.argv[2], output = path.resolve(process.argv[3] || 'build/reflex-review');
  fs.mkdirSync(output, {recursive: true});
  const browser = await chromium.launch({headless: true, args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader']});
  try {
    const page = await browser.newPage({viewport: {width: 1280, height: 800}, acceptDownloads: true}), errors = [];
    page.on('pageerror', error => errors.push(String(error)));
    page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
    const status = () => page.textContent('#status'), said = () => page.textContent('#selection');
    const brushes = () => page.evaluate(() => window.ReflexMap.global(window.skinnerReflexMaps.map).items.filter(item => item.kind === 'brush').length);
    // A click `left` pixels left of the middle of the view.
    // Leaves edit mode and comes back with no frame between, so play mode does not move the camera.
    const refresh = () => page.evaluate(() => { window.skinnerReflexMaps.setEditing(false); window.skinnerReflexMaps.setEditing(true); });
    const centre = async (left = 0) => { const box = await page.locator('#c').boundingBox(); await page.mouse.click(box.x + box.width / 2 - left, box.y + box.height / 2); };

    // A new map opens in edit mode.
    await page.goto(`${base}/maps/reflex/`);
    await page.waitForFunction(() => /brushes/.test(document.getElementById('status').textContent));
    assert.equal(await page.isVisible('#tools'), true, 'a new map starts in edit mode');
    assert.equal(await brushes(), 0);
    await page.selectOption('#grid', '8');

    // B makes a box in front of the camera; a click on it selects it.
    await page.click('#newBrush');
    assert.equal(await brushes(), 1);
    await centre();
    assert.match(await said(), /1 selected/);

    // A second box, moved along by Shift and the arrows, is carved out of the first (Ctrl+Shift+S).
    await page.click('#newBrush');
    for (let i = 0; i < 3; i++) await page.keyboard.press('Shift+ArrowRight');
    await page.keyboard.press('Shift+PageUp');
    const before = await page.evaluate(() => { const g = window.skinnerReflexMaps.map.groups.find(g => g.kind === 'global'); return g.items.filter(i => i.kind === 'brush').map(b => b.vertices); });
    assert.equal(before.length, 2);
    await page.keyboard.press('Control+Shift+S');
    assert.match(await said(), /Carved 1 brushes/);
    const carved = await page.evaluate(() => window.skinnerReflexMaps.map.groups.find(g => g.kind === 'global').items.filter(i => i.kind === 'brush'));
    // 64-unit boxes, the cutter 24 along x and 8 up: what is left of the first is 64³ less the 40 × 56 × 64 overlap.
    const kept = carved.slice(0, -1).reduce((sum, brush) => sum + B.volume(brush), 0);
    assert.ok(Math.abs(kept - (64 ** 3 - 40 * 56 * 64)) < 1, `carved volume ${kept}`);
    carved.forEach(brush => assert.deepEqual(B.check(brush), []));

    // Delete the cutter (Backspace, as the game; Delete is a texture key), undo it back, redo the delete.
    await page.keyboard.press('Backspace');
    const afterDelete = await brushes();
    await page.keyboard.press('Control+z');
    assert.equal(await brushes(), afterDelete + 1);
    await page.keyboard.press('Control+Shift+Z');
    assert.equal(await brushes(), afterDelete);

    // Hollow what is left of the first box (the middle of the view is the notch carved out of it) into walls one
    // grid step (8) thick.
    await centre(60);
    assert.match(await said(), /1 selected/);
    const hollowed = await brushes();
    await page.keyboard.press('h');
    assert.match(await said(), /Hollowed into \d+ walls/);
    assert.ok(await brushes() > hollowed);
    await page.screenshot({path: path.join(output, 'edited.png')});

    // Save downloads the map; it reads back with the same brushes.
    const [download] = await Promise.all([page.waitForEvent('download'), page.keyboard.press('Control+s')]);
    const saved = fs.readFileSync(await download.path(), 'utf8');
    fs.writeFileSync(path.join(output, 'edited.map'), saved);
    const map = M.parse(saved);
    assert.equal(M.global(map).items.filter(item => item.kind === 'brush').length, await brushes());
    assert.equal(M.write(map), saved);
    M.global(map).items.filter(item => item.kind === 'brush').forEach(brush => assert.deepEqual(B.check(brush), []));

    // 0 flies, as in the game; 0 again edits; Tab does the same.
    await page.keyboard.press('0');
    assert.equal(await page.isVisible('#tools'), false);
    await page.keyboard.press('0');
    assert.equal(await page.isVisible('#tools'), true);
    await page.keyboard.press('Tab');
    assert.equal(await page.isVisible('#tools'), false);
    await page.keyboard.press('Tab');

    // The game's editor gestures on a fresh box: drag moves it on the grid, Alt-drag lifts it, Shift-drag on its top
    // face pulls that face up; Z and X undo and redo; G clones; K picks up a material and M puts it on.
    await page.evaluate(() => { const m = window.skinnerReflexMaps; m.selected.clear(); });
    await page.click('#newBrush');
    const boxOf = () => page.evaluate(() => { const m = window.skinnerReflexMaps; const [item] = m.selected; return item && window.ReflexBrush.bounds(item); });
    // Where a point of the game's axes is on the page.
    const screen = point => page.evaluate(([x, y, z]) => {
      const {camera, renderer} = window.skinnerReflexMaps;
      camera.updateMatrixWorld();  // As the page does before it picks: a pose set since the last frame counts.
      const v = new THREE.Vector3(x, y, -z).project(camera), rect = renderer.domElement.getBoundingClientRect();
      return [rect.left + (v.x + 1) / 2 * rect.width, rect.top + (1 - v.y) / 2 * rect.height];
    }, point);
    const dragFrom = async (from, to, modifier) => {
      if (modifier) await page.keyboard.down(modifier);
      await page.mouse.move(...from); await page.mouse.down(); await page.mouse.move(...to, {steps: 8}); await page.mouse.up();
      if (modifier) await page.keyboard.up(modifier);
    };
    const start = await boxOf(), middle = start.min.map((v, i) => (v + start.max[i]) / 2);
    const top = [middle[0], start.max[1], middle[2]], front = await screen(top);
    await dragFrom(front, [front[0] + 120, front[1]]);
    const moved = await boxOf();
    assert.ok(moved.min[0] - start.min[0] >= 8 && (moved.min[0] - start.min[0]) % 8 === 0, `drag moved ${moved.min[0] - start.min[0]} along x`);
    assert.equal(moved.min[1], start.min[1], 'a plain drag keeps the height');
    let at = await screen([(moved.min[0] + moved.max[0]) / 2, moved.max[1], middle[2]]);
    await dragFrom(at, [at[0], at[1] - 80], 'Alt');
    const lifted = await boxOf();
    assert.ok(lifted.min[1] > moved.min[1] && lifted.min[1] % 8 === 0, `Alt-drag lifted to ${lifted.min[1]}`);
    assert.equal(lifted.max[1] - lifted.min[1], start.max[1] - start.min[1], 'moving keeps the size');
    at = await screen([(lifted.min[0] + lifted.max[0]) / 2, lifted.max[1], middle[2]]);
    await dragFrom(at, [at[0], at[1] - 60], 'Shift');
    const pulled = await boxOf();
    assert.equal(pulled.min[1], lifted.min[1], 'pulling the top face leaves the bottom');
    assert.ok(pulled.max[1] > lifted.max[1] && pulled.max[1] % 8 === 0, `Shift-drag pulled the top to ${pulled.max[1]}`);
    const count = await brushes();
    await page.keyboard.press('z');
    assert.deepEqual(await page.evaluate(() => window.ReflexBrush.bounds(window.ReflexMap.global(window.skinnerReflexMaps.map).items.at(-1))), lifted, 'Z undoes the face pull');
    await page.keyboard.press('x');
    assert.equal(await brushes(), count);
    await page.keyboard.press('g');
    assert.equal(await brushes(), count + 1, 'G clones');
    // K over a face picks its material; M puts it on the selection.
    // The clone (selected after G, and placed beside its original) is given wood.
    await page.evaluate(() => { const m = window.skinnerReflexMaps, g = window.ReflexMap.global(m.map), [clone] = m.selected, at = g.items.indexOf(clone);
      g.items[at] = {...clone, faces: clone.faces.map(face => ({...face, material: 'common/materials/wood/bare', colour: '0xff332805'}))}; });
    // Reloaded, as an edited file would be, with the camera kept where it was.
    await page.evaluate(() => {
      const m = window.skinnerReflexMaps, position = m.camera.position.clone(), rotation = m.camera.rotation.clone();
      m.load(window.ReflexMap.write(m.map), 'untitled');
      m.camera.position.copy(position); m.camera.rotation.copy(rotation);
    });
    await refresh();
    // The clone stands 8 along x from the box: the strip of its top beyond the box is its own.
    at = await screen([pulled.max[0] + 4, pulled.max[1], middle[2]]);
    await page.mouse.move(...at);
    await page.keyboard.press('k');
    assert.equal(await page.evaluate(() => window.skinnerReflexMaps.template.material), 'common/materials/wood/bare');
    await page.evaluate(() => { const m = window.skinnerReflexMaps, g = window.ReflexMap.global(m.map); m.selected.clear(); m.selected.add(g.items.find(i => i.kind === 'brush')); });
    await page.keyboard.press('m');
    assert.ok(await page.evaluate(() => [...window.skinnerReflexMaps.selected][0].faces.every(face => face.material === 'common/materials/wood/bare' && face.colour === '0xff332805')), 'M puts the material on the selection');
    await page.screenshot({path: path.join(output, 'gestures.png')});

    // Create types, entities and vertex mode, on a new map seen from a fixed place: the camera 512 back and 384 up,
    // looking at the origin.
    await page.evaluate(() => {
      const m = window.skinnerReflexMaps;
      m.load(window.ReflexMap.write(window.ReflexMap.empty()), 'untitled');
      m.camera.position.set(0, 384, 512); m.camera.lookAt(0, 0, 0);
    });
    const items = () => page.evaluate(() => window.ReflexMap.global(window.skinnerReflexMaps.map).items.map(item => item.kind === 'brush'
      ? {kind: 'brush', bounds: window.ReflexBrush.bounds(item), corners: item.vertices.length, owner: (window.skinnerReflexMaps.flat.brushes.find(e => e.source === item).owner || {}).type}
      : {kind: 'entity', type: item.type, properties: Object.fromEntries(item.properties.map(p => [p.name, p.value]))}));
    const clickAt = async (point, options) => { await page.mouse.click(...await screen(point), options); };
    // 1 and a drag on the ground (nothing under the mouse: the plane y = 0) makes a brush four grid steps tall.
    await page.keyboard.press('1');
    assert.match(await page.textContent('#mode'), /Create brush \(1\)/);
    await dragFrom(await screen([-32, 0, -32]), await screen([32, 0, 32]));
    let now = await items();
    assert.deepEqual(now[1].bounds, {min: [-32, 0, -32], max: [32, 32, 32]}, 'a dragged brush stands on its footprint');
    assert.equal(now[1].owner, 'WorldSpawn');
    // 6 and a click on its top places a pickup there, on the grid; 8 a player spawn with angles.
    await page.keyboard.press('6');
    await clickAt([9, 32, 7]);
    now = await items();
    assert.deepEqual(now.at(-1), {kind: 'entity', type: 'Pickup', properties: {position: [8, 32, 8], pickupType: 40}});
    await page.keyboard.press('8');
    await clickAt([-16, 32, 16]);
    now = await items();
    assert.equal(now.at(-1).type, 'PlayerSpawn');
    assert.deepEqual(now.at(-1).properties.position, [-16, 32, 16]);
    assert.equal(now.at(-1).properties.angles.length, 3);
    // 2 and a drag on the top makes a teleporter: its entity, then the brush that is its volume, drawn as a volume.
    await page.keyboard.press('2');
    await dragFrom(await screen([-24, 32, -24]), await screen([24, 32, -8]));
    now = await items();
    assert.equal(now.at(-2).type, 'Teleporter');
    assert.deepEqual([now.at(-1).bounds, now.at(-1).owner], [{min: [-24, 32, -24], max: [24, 64, -8]}, 'Teleporter']);
    assert.ok(await page.evaluate(() => window.skinnerReflexMaps.scene.getObjectByProperty('type', 'Mesh') && true));
    // Escape leaves create mode; B's box goes into the WorldSpawn's brushes, ahead of the entities, so it is the world's.
    await page.keyboard.press('Escape');
    assert.equal(await page.evaluate(() => window.skinnerReflexMaps.createType), 0);
    await page.click('#newBrush');
    now = await items();
    assert.deepEqual(now.map(item => item.kind === 'brush' ? `brush:${item.owner}` : item.type),
      ['WorldSpawn', 'brush:WorldSpawn', 'brush:WorldSpawn', 'Pickup', 'PlayerSpawn', 'Teleporter', 'brush:Teleporter']);
    await page.keyboard.press('Backspace');
    // A click on the pickup's marker selects it; a drag moves it on the grid.
    await page.keyboard.press('Escape');
    await clickAt([8, 32, 8]);
    assert.match(await page.textContent('#selection'), /Pickup/);
    const pickup = await screen([8, 32, 8]);
    await dragFrom(pickup, [pickup[0] + 90, pickup[1]]);
    now = await items();
    const dropped = now.find(item => item.type === 'Pickup').properties.position;
    assert.ok(dropped[0] > 8 && (dropped[0] - 8) % 8 === 0 && dropped[1] === 32 && dropped[2] === 8, `pickup moved to ${dropped}`);
    // Deleting a teleporter's volume deletes the teleporter with it.
    await clickAt([0, 64, -16]);
    assert.match(await page.textContent('#selection'), /Teleporter volume/);
    await page.keyboard.press('Backspace');
    now = await items();
    assert.equal(now.filter(item => item.type === 'Teleporter' || item.owner === 'Teleporter').length, 0);
    // V shows the corners of the selected brush; Alt-dragging one moves only it, up on the grid.
    await clickAt([-20, 8, -32]);
    await page.keyboard.press('v');
    assert.equal(await page.evaluate(() => window.skinnerReflexMaps.vertexMode), true);
    const corner = await screen([-32, 32, -32]);
    await dragFrom(corner, [corner[0], corner[1] - 50], 'Alt');
    now = await items();
    const cornersNow = await page.evaluate(() => window.ReflexMap.global(window.skinnerReflexMaps.map).items.find(item => item.kind === 'brush').vertices);
    const raised = cornersNow.filter(v => v[1] > 32);
    assert.equal(raised.length, 1, 'one corner moved');
    assert.ok(raised[0][0] === -32 && raised[0][2] === -32 && raised[0][1] % 8 === 0, `corner at ${raised[0]}`);
    await page.keyboard.press('z');
    // A corner dropped on another corner becomes it: the top front right corner onto the bottom one.
    const upper = await screen([32, 32, -32]), lower = await screen([32, 0, -32]);
    await dragFrom(upper, lower, 'Alt');
    const welded = await page.evaluate(() => window.ReflexMap.global(window.skinnerReflexMaps.map).items.find(item => item.kind === 'brush'));
    assert.equal(welded.vertices.length, 7, 'welded corners');
    assert.ok(welded.faces.every(face => new Set(face.indices).size === face.indices.length && face.indices.length >= 3));
    await page.screenshot({path: path.join(output, 'vertices.png')});
    await page.keyboard.press('z');
    assert.equal(await page.evaluate(() => window.ReflexMap.global(window.skinnerReflexMaps.map).items.find(item => item.kind === 'brush').vertices.length), 8);
    // N shows the properties of what is selected; with nothing selected, the map's WorldSpawn. A change is one step
    // of undo.
    await page.keyboard.press('Escape'); await page.keyboard.press('Escape');
    await page.keyboard.press('n');
    assert.equal(await page.isVisible('#props'), true);
    assert.match(await page.textContent('#props h3'), /WorldSpawn \(the map\)/);
    await page.selectOption('#props .add select', {label: 'title (String256)'});
    await page.click('#props .add button');
    await page.fill('#props label[title="String256 title"] input', 'Test Walk');
    await page.press('#props label[title="String256 title"] input', 'Enter');
    now = await items();
    assert.equal(now[0].properties.title, 'Test Walk');
    // A pickup's type from the labelled list.
    await clickAt(dropped);
    assert.match(await page.textContent('#props h3'), /Pickup/);
    await page.screenshot({path: path.join(output, 'properties.png')});
    await page.selectOption('#props label[title="UInt8 pickupType"] select', '4');
    now = await items();
    assert.equal(now.find(item => item.type === 'Pickup').properties.pickupType, 4);
    await page.keyboard.press('z');
    now = await items();
    assert.equal(now.find(item => item.type === 'Pickup').properties.pickupType, 40, 'Z undoes a property');
    // A target, and a teleporter linked to it by choosing its name: the teleporter's volume shows its entity's properties.
    await page.keyboard.press('Escape');
    await page.keyboard.press('4');
    await clickAt([24, 32, 24]);
    await page.keyboard.press('Escape');
    await page.keyboard.press('2');
    await dragFrom(await screen([-24, 32, -24]), await screen([24, 32, -8]));
    await page.keyboard.press('Escape');
    assert.match(await page.textContent('#props h3'), /Teleporter/);
    assert.match(await page.textContent('#props'), /Set target to the name of a Target/);
    await page.selectOption('#props .add select', {label: 'target (String32)'});
    await page.click('#props .add button');
    now = await items();
    assert.equal(now.find(item => item.type === 'Teleporter').properties.target, 'target1', 'target starts as the map\'s Target');
    await page.keyboard.press('Backspace');
    await page.keyboard.press('n');
    assert.equal(await page.isVisible('#props'), false);

    // The bridge tool: Shift-click a face, B, aim at another face, the wheel for steps, a click makes the brushes.
    // Two blocks: the low box's top bridged to the side of a high one, an arch.
    await page.evaluate(() => {
      const m = window.skinnerReflexMaps, g = window.ReflexMap.global(m.map);
      g.items.splice(window.ReflexMap.worldInsertAt(g), 0, {kind: 'brush', ...window.ReflexBrush.box([160, 96, -32], [192, 160, 32], m.template)});
      m.load(window.ReflexMap.write(m.map), 'untitled');
      m.camera.position.set(80, 300, 520); m.camera.lookAt(80, 60, 0);
    });
    await refresh();
    const brushesBefore = (await items()).filter(item => item.kind === 'brush').length;
    await page.keyboard.down('Shift'); await clickAt([0, 32, 0]); await page.keyboard.up('Shift');
    assert.match(await page.textContent('#selection'), /Face picked \(4 corners\)/);
    await page.keyboard.press('b');
    assert.equal(await page.evaluate(() => window.skinnerReflexMaps.bridging), true);
    await page.mouse.move(...await screen([160, 128, 0]));
    assert.match(await page.textContent('#selection'), /Bridge of 4 steps/);
    await page.mouse.wheel(0, -100);
    await page.mouse.wheel(0, -100);
    assert.equal(await page.evaluate(() => window.skinnerReflexMaps.segments), 6);
    assert.match(await page.textContent('#mode'), /wheel 6 steps/);
    await page.screenshot({path: path.join(output, 'bridge.png')});
    await page.mouse.down(); await page.mouse.up();
    now = await items();
    const bridged = now.filter(item => item.kind === 'brush');
    assert.equal(await page.evaluate(() => window.skinnerReflexMaps.selected.size), 6, 'the new bridge is selected, not the brush under the click');
    assert.equal(bridged.length, brushesBefore + 6, 'six bridge brushes');
    assert.ok(bridged.every(item => item.owner === 'WorldSpawn'), 'bridge brushes are the world\'s');
    await page.screenshot({path: path.join(output, 'bridged.png')});
    await page.keyboard.press('z');
    assert.equal((await items()).filter(item => item.kind === 'brush').length, brushesBefore);

    // Turning (numpad + and −, by the angle step), the texture keys, the clipper and prefabs, on a new map with one box
    // and a player spawn, seen from the same fixed place.
    const fresh = () => page.evaluate(() => {
      const m = window.skinnerReflexMaps, map = window.ReflexMap.empty(), g = window.ReflexMap.global(map);
      g.items.push({kind: 'brush', ...window.ReflexBrush.box([-32, 0, -32], [32, 64, 32], m.template)});
      m.load(window.ReflexMap.write(map), 'untitled');
      m.camera.position.set(0, 384, 512); m.camera.lookAt(0, 0, 0);
    });
    const firstBrush = () => page.evaluate(() => window.ReflexMap.global(window.skinnerReflexMaps.map).items.find(item => item.kind === 'brush'));
    await fresh();
    await page.selectOption('#grid', '16');
    await page.selectOption('#angle', '90');
    await refresh();
    // A box 64 by 16 and a spawn at its corner, turned a quarter about their middle (32, 8): the turn would leave
    // them 8 off the grid, so they move the rest of the way onto it.
    await page.evaluate(() => {
      const m = window.skinnerReflexMaps, g = window.ReflexMap.global(m.map);
      g.items.splice(window.ReflexMap.worldInsertAt(g), 0, {kind: 'brush', ...window.ReflexBrush.box([0, 0, 0], [64, 32, 16], m.template)});
      g.items.push({kind: 'entity', type: 'PlayerSpawn', properties: [{type: 'Vector3', name: 'position', value: [0, 0, 0]}, {type: 'Vector3', name: 'angles', value: [0, 0, 0]}]});
      m.load(window.ReflexMap.write(m.map), 'untitled');
      const items = window.ReflexMap.global(m.map).items;
      m.selected.add(items[2]); m.selected.add(items.at(-1));
    });
    await page.keyboard.press('NumpadAdd');
    now = await items();
    assert.deepEqual(now[2].bounds, {min: [32, 0, -16], max: [48, 32, 48]}, 'numpad + turns the box a quarter, onto the grid');
    assert.deepEqual([now.at(-1).properties.position, now.at(-1).properties.angles], [[32, 0, 48], [90, 0, 0]], 'the spawn turns with it');
    assert.equal(await page.evaluate(() => window.skinnerReflexMaps.selected.size), 2, 'what was turned stays selected');
    await page.keyboard.press('NumpadSubtract');
    now = await items();
    assert.equal(now.at(-1).properties.angles[0], 0, 'numpad − turns back');
    await page.keyboard.press('z'); await page.keyboard.press('z');
    assert.deepEqual((await items())[2].bounds, {min: [0, 0, 0], max: [64, 32, 16]});

    // The texture keys work on the face under the cursor: the top of the large box.
    await fresh();
    await refresh();
    const topFace = async () => { const brush = await firstBrush(); return brush.faces.find(face => face.indices.every(i => brush.vertices[i][1] === 64)); };
    await page.mouse.move(...await screen([-8, 64, 8]));
    for (const key of ['ArrowRight', 'ArrowUp', 'ArrowUp', 'Home', 'PageUp', 'Period', 'Delete']) await page.keyboard.press(key);
    const textured = await topFace();
    assert.deepEqual([textured.u, textured.v, textured.scaleU, textured.scaleV, textured.rotation], [16, 32, -1.25, 0.75, 90], 'arrows move, Home and Delete scale, PgUp flips, . turns');
    assert.match(await said(), /Texture: offset 16 32 · scale -1.25 0.75 · rotation 90°/);
    assert.ok(await page.evaluate(() => { let found = false; window.skinnerReflexMaps.scene.traverse(o => { if (o.geometry && o.geometry.getAttribute('texcoord')) found = true; }); return found; }), 'the face shows its texture coordinates');
    await page.screenshot({path: path.join(output, 'texture.png')});
    await page.keyboard.press('Comma');
    assert.equal((await topFace()).rotation, 0);
    for (let i = 0; i < 8; i++) await page.keyboard.press('z');
    assert.deepEqual(await topFace().then(face => [face.u, face.v, face.scaleU, face.scaleV, face.rotation]), [0, 0, 1, 1, 0], 'each key is a step of undo');
    // Shift and the arrows nudge the selection instead.
    await clickAt([0, 64, 0]);
    await page.keyboard.press('Shift+PageUp');
    assert.equal((await items())[1].bounds.min[1], 16, 'Shift+PgUp lifts the selection a grid step');
    await page.keyboard.press('z');

    // The clipper: C, two points on the top at x = 16, Enter keeps the side away from the camera (which looks from
    // x = 0); Shift+Enter keeps both; the wheel flips the side.
    await clickAt([0, 64, 0]);
    await page.keyboard.press('c');
    assert.equal(await page.evaluate(() => window.skinnerReflexMaps.clipMode), true);
    await clickAt([17, 64, -15]);
    await clickAt([15, 64, 17]);
    assert.deepEqual(await page.evaluate(() => window.skinnerReflexMaps.clipPoints), [[16, 64, -16], [16, 64, 16]], 'points land on the grid');
    assert.match(await page.textContent('#mode'), /Clip \(C\): 2 of 3 points/);
    await page.screenshot({path: path.join(output, 'clip.png')});
    await page.keyboard.press('Enter');
    now = await items();
    assert.deepEqual(now.filter(item => item.kind === 'brush').map(item => item.bounds), [{min: [16, 0, -32], max: [32, 64, 32]}]);
    await page.keyboard.press('z');
    await clickAt([0, 64, 0]);
    await page.keyboard.press('Escape'); await page.keyboard.press('Escape');
    await clickAt([0, 64, 0]);
    await page.keyboard.press('c');
    await clickAt([17, 64, -15]); await clickAt([15, 64, 17]);
    await page.keyboard.press('Shift+Enter');
    assert.deepEqual((await items()).filter(item => item.kind === 'brush').map(item => item.bounds).sort((a, b) => a.min[0] - b.min[0]),
      [{min: [-32, 0, -32], max: [16, 64, 32]}, {min: [16, 0, -32], max: [32, 64, 32]}], 'Shift+Enter splits');
    await page.keyboard.press('z');
    await page.keyboard.press('Escape'); await page.keyboard.press('Escape');
    await clickAt([0, 64, 0]);
    await page.keyboard.press('c');
    await clickAt([17, 64, -15]); await clickAt([15, 64, 17]);
    await page.mouse.wheel(0, 100);
    await page.keyboard.press('Enter');
    assert.deepEqual((await items()).filter(item => item.kind === 'brush').map(item => item.bounds), [{min: [-32, 0, -32], max: [16, 64, 32]}], 'the wheel flips the side kept');
    await page.keyboard.press('z');
    await page.keyboard.press('Escape'); await page.keyboard.press('Escape');
    assert.equal(await page.evaluate(() => window.skinnerReflexMaps.clipMode), false, 'Escape leaves clip mode');

    // Prefabs, from the console (`): the box becomes prefab "block", placed where it stood.
    const run = async line => { await page.keyboard.press('Backquote'); await page.keyboard.type(line); await page.keyboard.press('Enter'); };
    await clickAt([0, 64, 0]);
    await run('me_createprefab block');
    assert.match(await said(), /Prefab block: 1 brushes and 0 entities, placed at 0 0 0/);
    now = await items();
    assert.deepEqual(now.map(item => item.type || item.kind), ['WorldSpawn', 'Prefab']);
    assert.deepEqual(await page.evaluate(() => window.skinnerReflexMaps.map.groups.map(g => g.kind === 'global' ? 'global' : g.name)), ['block', 'global']);
    assert.equal(await page.evaluate(() => window.skinnerReflexMaps.flat.brushes.length), 1, 'the prefab is drawn where the box was');
    // A click on the drawn box selects its placement; G clones it, and a drag moves the clone.
    await page.keyboard.press('Escape');
    await clickAt([0, 64, 0]);
    assert.match(await said(), /1 selected · Prefab block · 1 brushes/);
    await page.keyboard.press('g');
    const cloneAt = await screen([40, 64, 0]);  // Only the clone (16 along x) is there.
    await dragFrom(cloneAt, [cloneAt[0] + 150, cloneAt[1]]);
    now = await items();
    const placements = now.filter(item => item.type === 'Prefab');
    assert.equal(placements.length, 2);
    const offsetX = placements[1].properties.position[0];
    assert.ok(offsetX > 16 && offsetX % 16 === 0, `the clone moved to x ${offsetX}`);
    // me_breakprefab: the clone becomes a brush of the map; its top pulled up 3 or more grid steps; me_updateprefab
    // puts it back, and the other placement changes with it.
    await run('me_breakprefab');
    now = await items();
    assert.deepEqual(now.map(item => item.type || item.kind), ['WorldSpawn', 'brush', 'Prefab']);
    const broken = now[1].bounds;
    assert.deepEqual(broken, {min: [offsetX - 32, 0, -32], max: [offsetX + 32, 64, 32]});
    at = await screen([broken.max[0] - 8, 64, 0]);
    await dragFrom(at, [at[0], at[1] - 80], 'Shift');
    const pulledTop = (await items())[1].bounds.max[1];
    assert.ok(pulledTop > 64, `top pulled to ${pulledTop}`);
    await run('me_updateprefab');
    assert.match(await said(), /Prefab block now holds 1 brushes and 0 entities, in its 2 placements/);
    const tops = await page.evaluate(() => window.skinnerReflexMaps.flat.brushes.map(entry => Math.max(...entry.brush.vertices.map(v => v[1]))));
    assert.deepEqual(tops, [pulledTop, pulledTop], 'both placements show the updated prefab');
    now = await items();
    assert.deepEqual(now.map(item => item.type || item.kind), ['WorldSpawn', 'Prefab', 'Prefab']);
    assert.deepEqual(now[2].properties.position, [offsetX, 0, 0], 'the placement goes back where it was');
    await page.keyboard.press('z');
    assert.deepEqual(await page.evaluate(() => window.skinnerReflexMaps.flat.brushes.map(entry => Math.max(...entry.brush.vertices.map(v => v[1])))).then(t => t.sort((a, b) => a - b)), [64, pulledTop].sort((a, b) => a - b), 'undo puts the prefab back as it was');
    await page.keyboard.press('x');
    // me_listprefabs lists it with its placements; Place and a click on the ground puts down a third.
    await run('me_listprefabs');
    assert.match(await page.textContent('#props .prefabs'), /block\s*2/);
    await page.screenshot({path: path.join(output, 'prefabs.png')});
    await page.click('#props .prefabs button:has-text("Place")');
    assert.match(await page.textContent('#mode'), /Create prefab block/);
    await clickAt([-128, 0, 96]);
    now = await items();
    assert.deepEqual([now.at(-1).type, now.at(-1).properties.prefabName, now.at(-1).properties.position], ['Prefab', 'block', [-128, 0, 96]]);
    assert.match(await page.textContent('#props .prefabs'), /block\s*3/);
    await page.keyboard.press('Escape');
    await page.click('#prefabsButton');
    assert.equal(await page.isVisible('#props'), false);
    const prefabMap = M.parse(await page.evaluate(() => window.ReflexMap.write(window.skinnerReflexMaps.map)));
    assert.equal(M.flatten(prefabMap).brushes.length, 3);
    assert.equal(M.prefab(prefabMap, 'block').items.length, 2);

    // Several faces picked (Ctrl+Shift-click adds): the texture keys and Shift+M work on all of them.
    await fresh();
    await refresh();
    await page.keyboard.down('Shift'); await clickAt([0, 64, 0]); await page.keyboard.up('Shift');
    await page.keyboard.down('Control'); await page.keyboard.down('Shift'); await clickAt([0, 32, -32]); await page.keyboard.up('Shift'); await page.keyboard.up('Control');
    assert.match(await said(), /2 faces picked/);
    await page.keyboard.press('ArrowRight');
    const shifted = await firstBrush();
    assert.deepEqual(shifted.faces.map(face => face.u).sort((a, b) => a - b), [0, 0, 0, 0, 16, 16], 'the arrow moved the texture of both picked faces');
    // The material browser: the game's materials and Skinner's libraries; a click chooses, Shift+M puts it on the picked faces.
    await page.click('#materialsButton');
    assert.match(await page.textContent('#propsTitle'), /Materials/);
    await page.selectOption('#props select', 't1');
    await page.waitForSelector('#props .materials button');
    const chosen = await page.getAttribute('#props .materials button', 'title');
    assert.match(chosen, /^skinner\/t1\//);
    await page.click('#props .materials button');
    assert.equal(await page.evaluate(() => window.skinnerReflexMaps.template.material), chosen);
    await page.keyboard.press('Shift+M');
    assert.equal((await firstBrush()).faces.filter(face => face.material === chosen).length, 2, 'Shift+M painted the two picked faces');
    assert.ok(await page.evaluate(() => window.skinnerReflexMaps.scene.children[0].children[0].material.length) >= 2, 'the library texture is drawn in a group of its own');
    await page.waitForTimeout(500);
    await page.screenshot({path: path.join(output, 'materials.png')});
    await page.click('#materialsButton');
    await page.keyboard.press('Escape');

    // Mirror: left to right as the camera sees it (across x here), about the selection's middle; Shift+click upside down.
    await page.evaluate(() => {
      const m = window.skinnerReflexMaps, g = window.ReflexMap.global(m.map);
      g.items.splice(window.ReflexMap.worldInsertAt(g), 0, {kind: 'brush', ...window.ReflexBrush.rebuild({vertices: [[64, 0, 0], [128, 0, 0], [64, 0, 32], [128, 0, 32], [64, 48, 0], [64, 48, 32]],
        faces: [[0, 1, 3, 2], [0, 2, 5, 4], [0, 4, 1], [2, 3, 5], [1, 4, 5, 3]].map(indices => ({...m.template, indices}))})});
      m.load(window.ReflexMap.write(m.map), 'untitled');
      m.camera.position.set(0, 384, 512); m.camera.lookAt(0, 0, 0);
      m.selected.add(window.ReflexMap.global(m.map).items[2]);
    });
    await refresh();
    const wedgeBefore = await page.evaluate(() => { const [item] = window.skinnerReflexMaps.selected; return item.vertices.filter(v => v[1] === 48).map(v => v[0]); });
    await page.click('#mirror');
    const wedgeAfter = await page.evaluate(() => { const [item] = window.skinnerReflexMaps.selected; return {top: item.vertices.filter(v => v[1] === 48).map(v => v[0]), problems: window.ReflexBrush.check(item), bounds: window.ReflexBrush.bounds(item)}; });
    assert.deepEqual([wedgeBefore, wedgeAfter.top, wedgeAfter.problems, wedgeAfter.bounds], [[64, 64], [128, 128], [], {min: [64, 0, 0], max: [128, 48, 32]}], 'the wedge leans the other way');
    await page.click('#mirror', {modifiers: ['Shift']});
    assert.deepEqual(await page.evaluate(() => { const [item] = window.skinnerReflexMaps.selected; return item.vertices.filter(v => v[1] === 0).length; }), 2, 'Shift+Mirror turns it upside down');
    await page.keyboard.press('z'); await page.keyboard.press('z');

    // Editing a prefab in place: a double click opens a placement, and its other placement changes as it is edited.
    await page.keyboard.press('Escape');
    await clickAt([0, 64, 0]);
    await run('me_createprefab slab');
    await page.keyboard.press('g');
    for (let i = 0; i < 9; i++) await page.keyboard.press('Shift+ArrowLeft');
    now = await items();
    const slabs = now.filter(item => item.type === 'Prefab');
    assert.equal(slabs.length, 2);
    const other = slabs[0].properties.position, opened = slabs[1].properties.position;
    await page.mouse.dblclick(...await screen([opened[0], 64, opened[2]]));
    assert.equal(await page.evaluate(() => window.skinnerReflexMaps.openPrefab && window.skinnerReflexMaps.openPrefab.name), 'slab');
    assert.match(await page.textContent('#mode'), /prefab slab open/);
    // Pull the open copy's top up; the other copy's top follows at once.
    await clickAt([opened[0], 64, opened[2]]);
    at = await screen([opened[0], 64, opened[2]]);
    await dragFrom(at, [at[0], at[1] - 60], 'Shift');
    // The slab's copies (the wedge from the mirror step stands apart, beyond x 64).
    const liveTops = await page.evaluate(() => window.skinnerReflexMaps.flat.brushes.filter(entry => window.ReflexBrush.bounds(entry.brush).max[0] <= 64)
      .map(entry => ({placed: entry.path.length > 0, top: Math.max(...entry.brush.vertices.map(v => v[1]))})));
    const openTop = liveTops.find(entry => !entry.placed).top;
    assert.ok(openTop > 64, `pulled to ${openTop}`);
    assert.deepEqual(liveTops.filter(entry => entry.placed).map(entry => entry.top), [openTop], 'the other placement changed with it');
    await page.screenshot({path: path.join(output, 'prefab-in-place.png')});
    // Escape clears the selection, then closes the prefab: the pieces go back into a placement where they were.
    await page.keyboard.press('Escape'); await page.keyboard.press('Escape');
    assert.equal(await page.evaluate(() => window.skinnerReflexMaps.openPrefab), null);
    now = await items();
    assert.deepEqual(now.map(item => item.type || item.kind), ['WorldSpawn', 'brush', 'Prefab', 'Prefab']);
    assert.deepEqual(now.filter(item => item.type === 'Prefab').map(item => item.properties.position).sort((a, b) => a[0] - b[0]), [opened, other].sort((a, b) => a[0] - b[0]));
    assert.deepEqual(await page.evaluate(() => window.skinnerReflexMaps.flat.brushes.filter(entry => entry.path.length).map(entry => Math.max(...entry.brush.vertices.map(v => v[1])))), [openTop, openTop]);

    // Play mode (0): walking from where the camera is, a teleporter to its Target, a jump pad onto its Target; F flies.
    await page.evaluate(() => {
      const m = window.skinnerReflexMaps, M = window.ReflexMap, B = window.ReflexBrush, map = M.empty(), g = M.global(map);
      const face = {u: 0, v: 0, scaleU: 1, scaleV: 1, rotation: 0, colour: '0x00000000', material: 'structural/dev/dev_grey128'};
      const vector = (name, value) => ({type: 'Vector3', name, value}), string = (name, value) => ({type: 'String32', name, value});
      g.items.push({kind: 'brush', ...B.box([-1024, -16, -1024], [1024, 0, 1024], face)});
      g.items.push({kind: 'entity', type: 'Target', properties: [vector('position', [600, 0, 600]), vector('angles', [90, 0, 0]), string('name', 'far')]});
      g.items.push({kind: 'entity', type: 'Target', properties: [vector('position', [-400, 200, 0]), string('name', 'up')]});
      g.items.push({kind: 'entity', type: 'Teleporter', properties: [string('target', 'far')]}, {kind: 'brush', ...B.box([300, 0, -32], [364, 96, 32], {...face, material: ''})});
      g.items.push({kind: 'entity', type: 'JumpPad', properties: [string('target', 'up')]}, {kind: 'brush', ...B.box([-132, 0, -32], [-68, 16, 32], {...face, material: ''})});
      m.load(M.write(map), 'play');
      m.setEditing(true);
      m.camera.position.set(0, 200, 0); m.camera.rotation.set(0, -Math.PI / 2, 0);  // Looking along +x.
    });
    await page.keyboard.press('0');
    assert.equal(await page.evaluate(() => window.skinnerReflexMaps.walking), true);
    await page.waitForFunction(() => { const p = window.skinnerReflexMaps.player; return p && p.ground; }, null, {timeout: 10000});
    const standing = await page.evaluate(() => window.skinnerReflexMaps.player.origin);
    // On the ground once within a quarter unit of it, as Quake 3 checks.
    assert.ok(standing[1] >= 24.125 && standing[1] < 24.4, `the player lands on the floor: ${standing}`);
    assert.match(await status(), /walking/);
    // Walking along +x for a moment, then into the teleporter at x 300.
    await page.keyboard.down('w');
    await page.waitForFunction(() => window.skinnerReflexMaps.player.origin[0] > 500, null, {timeout: 10000});
    await page.keyboard.up('w');
    const arrived = await page.evaluate(() => window.skinnerReflexMaps.player.origin);
    assert.ok(Math.hypot(arrived[0] - 600, arrived[2] - 600) < 120, `teleported to ${arrived}`);
    // Onto the jump pad: thrown up and across to its Target.
    await page.evaluate(() => { const p = window.skinnerReflexMaps.player; p.origin = [-100, 24.125, 0]; p.velocity = [0, 0, 0]; });
    await page.waitForFunction(() => window.skinnerReflexMaps.player.origin[1] > 150, null, {timeout: 5000});
    await page.waitForFunction(() => window.skinnerReflexMaps.player.ground, null, {timeout: 10000});
    const landed = await page.evaluate(() => window.skinnerReflexMaps.player.origin);
    assert.ok(landed[0] < -300, `the jump pad threw the player to ${landed}`);
    await page.screenshot({path: path.join(output, 'play.png')});
    await page.keyboard.press('f');
    assert.equal(await page.evaluate(() => window.skinnerReflexMaps.walking), false, 'F flies');
    assert.match(await status(), /speed/);
    await page.keyboard.press('f');
    await page.keyboard.press('0');
    assert.equal(await page.isVisible('#tools'), true);

    // The edited map still writes and reads back.
    const written = await page.evaluate(() => window.ReflexMap.write(window.skinnerReflexMaps.map));
    assert.equal(M.write(M.parse(written)), written);
    await page.keyboard.press('Escape');

    // An imported map, when there is one, draws something other than the sky.
    const maps = await (await page.request.get(`${base}/reflex-map-data/index.json`)).json().catch(() => []);
    if (Array.isArray(maps) && maps.length) {
      await page.goto(`${base}/maps/reflex/?map=${encodeURIComponent(maps[0].id)}`);
      await page.waitForFunction(() => /brushes|could not/.test(document.getElementById('status').textContent), null, {timeout: 60000});
      assert.match(await status(), /brushes/);
      const shot = path.join(output, `${maps[0].id}.png`);
      await page.screenshot({path: shot});
      const colours = await page.evaluate(() => {
        const {renderer, scene, camera} = window.skinnerReflexMaps, gl = renderer.getContext();
        renderer.render(scene, camera);
        // A 16 × 16 grid of pixels over the whole view.
        const seen = new Set(), pixel = new Uint8Array(4);
        for (let y = 0; y < 16; y++) for (let x = 0; x < 16; x++) {
          gl.readPixels(Math.floor((x + .5) * gl.drawingBufferWidth / 16), Math.floor((y + .5) * gl.drawingBufferHeight / 16), 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, pixel);
          seen.add(pixel.slice(0, 3).join());
        }
        return seen.size;
      });
      assert.ok(colours > 8, `${maps[0].id} shows ${colours} colours`);
      console.log(`${maps[0].name}: ${await status()}`);
    }
    assert.deepEqual(errors, []);
    console.log(`Reflex map page checks passed; screenshots and the saved map are in ${output}`);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
