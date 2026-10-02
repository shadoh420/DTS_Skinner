// Hidden browser check of the Reflex Maps page: node tools/check_reflex_maps.cjs URL [output directory].
// Starts a new map, builds and carves brushes with the keyboard and mouse, undoes and redoes, saves the map and reads
// the download back; then, when maps are imported, draws the first one and checks it is not blank.
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
    const centre = async (left = 0) => { const box = await page.locator('#c').boundingBox(); await page.mouse.click(box.x + box.width / 2 - left, box.y + box.height / 2); };

    // A new map opens in edit mode.
    await page.goto(`${base}/maps/reflex/`);
    await page.waitForFunction(() => /brushes/.test(document.getElementById('status').textContent));
    assert.equal(await page.isVisible('#tools'), true, 'a new map starts in edit mode');
    assert.equal(await brushes(), 0);

    // B makes a box in front of the camera; a click on it selects it.
    await page.keyboard.press('b');
    assert.equal(await brushes(), 1);
    await centre();
    assert.match(await said(), /1 selected/);

    // A second box, moved along by the arrows, is carved out of the first (Ctrl+Shift+S).
    await page.keyboard.press('b');
    for (let i = 0; i < 3; i++) await page.keyboard.press('ArrowRight');
    await page.keyboard.press('PageUp');
    const before = await page.evaluate(() => { const g = window.skinnerReflexMaps.map.groups.find(g => g.kind === 'global'); return g.items.filter(i => i.kind === 'brush').map(b => b.vertices); });
    assert.equal(before.length, 2);
    await page.keyboard.press('Control+Shift+S');
    assert.match(await said(), /Carved 1 brushes/);
    const carved = await page.evaluate(() => window.skinnerReflexMaps.map.groups.find(g => g.kind === 'global').items.filter(i => i.kind === 'brush'));
    // 64-unit boxes, the cutter 24 along x and 8 up: what is left of the first is 64³ less the 40 × 56 × 64 overlap.
    const kept = carved.slice(0, -1).reduce((sum, brush) => sum + B.volume(brush), 0);
    assert.ok(Math.abs(kept - (64 ** 3 - 40 * 56 * 64)) < 1, `carved volume ${kept}`);
    carved.forEach(brush => assert.deepEqual(B.check(brush), []));

    // Delete the cutter, undo it back, redo the delete.
    await page.keyboard.press('Delete');
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

    // Tab flies; Tab again edits.
    await page.keyboard.press('Tab');
    assert.equal(await page.isVisible('#tools'), false);
    await page.keyboard.press('Tab');
    assert.equal(await page.isVisible('#tools'), true);

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
