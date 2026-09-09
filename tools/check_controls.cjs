// Hidden browser regression: node tools/check_controls.cjs URL [output directory].
// Uses the same optional Playwright dependency as check_browser.cjs.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
(async () => {
  const base = process.argv[2], output = path.resolve(process.argv[3] || 'build/controls-review');
  fs.mkdirSync(output, {recursive:true});
  const browser = await chromium.launch({headless:true, channel:'msedge'});
  try {
    const page = await browser.newPage({viewport:{width:1280,height:900}}), errors = [];
    page.on('pageerror', error => errors.push(String(error)));
    // Observe actual rendered transforms without a production test API.
    await page.route('**/static/skinner.js', async route => {
      const response = await route.fetch();
      await route.fulfill({response, body:`
        if (${Boolean(process.env.LEGACY_POINTER_LOCK)}) {
          const requestLock = Element.prototype.requestPointerLock;
          Element.prototype.requestPointerLock = function(...args) { requestLock.apply(this,args); };
        }
        const BaseRenderer = THREE.WebGLRenderer;
        THREE.WebGLRenderer = function(...args) {
          const renderer = new BaseRenderer(...args), render = renderer.render;
          renderer.render = function(scene, camera) {
            window.reviewCamera = camera;
            window.reviewGroup = scene.children.find(object => object.isGroup);
            return render.call(this, scene, camera);
          };
          return renderer;
        };
      ` + await response.text()});
    });
    await page.goto(base);
    const ready = async () => {
      await page.waitForFunction(() => window.reviewGroup && /^Preview (ready|loaded with)/.test(document.querySelector('#status').textContent) && !document.querySelector('#exportObjBtn').disabled);
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    };
    const state = () => page.evaluate(() => ({position:reviewGroup.position.toArray(), rotation:reviewGroup.quaternion.toArray(), camera:reviewCamera.position.toArray(), look:reviewCamera.quaternion.toArray()}));
    const close = (a,b,epsilon=1e-5) => assert(a.every((v,i)=>Math.abs(v-b[i])<epsilon), `${a} != ${b}`);
    await ready();
    const normal = await state();
    await page.click('#fullscreen');
    await page.waitForFunction(() => document.fullscreenElement === document.querySelector('#viewport'));
    assert.equal(await page.locator('#fullscreen').textContent(),'Exit fullscreen');
    await page.screenshot({path:path.join(output,'fullscreen.png')});
    await page.click('#fullscreen');
    await page.waitForFunction(() => !document.fullscreenElement);
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(resolve)));
    await page.click('#rotZ90');
    await page.fill('#positionY', '1.25');
    await page.fill('#moveStep', '.5');
    await page.click('#moveYP');
    close((await state()).position,[0,1.75,0]);
    await page.fill('#positionX', '-.3');
    await page.fill('#positionZ', '.7');
    const moved = await state();
    close(moved.position,[-.3,1.75,.7]);
    const reloaded = page.waitForResponse(response => response.url().includes('/model_json/'));
    await page.click('#loadModelBtn'); await reloaded; await ready();
    close((await state()).position,moved.position);
    close((await state()).rotation,moved.rotation);
    await page.selectOption('#modelSelect','shotgun'); await ready();
    await page.selectOption('#modelSelect','disc'); await ready();
    close((await state()).position,moved.position);
    await page.screenshot({path:path.join(output,'axis-controls.png')});
    await page.click('#resetAll'); await ready();
    close((await state()).position,normal.position); close((await state()).rotation,normal.rotation);
    close((await state()).camera,normal.camera);
    await page.selectOption('#viewSelect','bottom');
    await page.click('#rotX90'); await page.click('#resetRot');
    close((await state()).look,normal.look); close((await state()).rotation,normal.rotation);
    // Real pointer capture, keyboard movement, look and release (no mock controls).
    await page.click('#walkMode');
    await page.waitForFunction(() => document.pointerLockElement === document.querySelector('#c') && document.querySelector('#walkMode').getAttribute('aria-pressed')==='true');
    const start = await state();
    await page.keyboard.down('w'); await page.waitForTimeout(250); await page.keyboard.up('w');
    const walked = await state();
    assert(Math.hypot(...walked.camera.map((v,i)=>v-start.camera[i])) > .2,'W did not move camera');
    assert.equal(walked.camera[1],start.camera[1],'Walk should stay level');
    await page.keyboard.down('e'); await page.waitForTimeout(150); await page.keyboard.up('e');
    assert((await state()).camera[1] > walked.camera[1], 'E did not rise');
    await page.mouse.move(500,450); await page.mouse.move(530,470);
    const looked = await state();
    assert.notDeepEqual(looked.look,start.look,'Mouse did not look');
    await page.mouse.wheel(0,-100);
    assert(Number(await page.inputValue('#walkSpeed'))>5,'Wheel did not adjust speed');
    await page.keyboard.press('Enter');
    await page.waitForFunction(() => !document.pointerLockElement);
    close((await state()).camera,looked.camera); close((await state()).look,looked.look,1e-4);
    const stopped = await state(); await page.waitForTimeout(150); close((await state()).camera,stopped.camera);
    // Shortcut outside text fields, fly direction, Escape and reset after movement.
    await page.selectOption('#navigationStyle','fly');
    await page.locator('#walkMode').focus();
    await page.keyboard.press('Shift+Backquote');
    await page.waitForFunction(() => !!document.pointerLockElement);
    const flyStart = await state();
    await page.keyboard.down('w'); await page.waitForTimeout(150); await page.keyboard.up('w');
    assert(Math.abs((await state()).camera[1]-flyStart.camera[1])>.01,'Fly did not follow pitch');
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => !document.pointerLockElement);
    await page.click('#resetAll'); await ready(); close((await state()).camera,normal.camera);
    // Every texture library can be applied to every game without changing the model game.
    for (const target of ['t1','t2','q3']) {
      if (target !== 't1') { await page.selectOption('#gameSelect',target); await ready(); }
      for (const source of ['t1','t2','q3']) {
        await page.selectOption('#textureGame',source);
        await page.waitForFunction(() => !document.querySelector('#applySkin').disabled);
        const filename = await page.locator('#skinSelect option').first().getAttribute('value');
        const responseEvent = page.waitForResponse(response => response.url().includes('/model_json/'));
        await page.click('#applySkin');
        const model = await (await responseEvent).json(); await ready();
        const slot = Number(await page.inputValue('#materialSelect'));
        assert.equal(model.material_texture_games[slot],source);
        assert.equal(model.material_textures[slot],filename);
        assert.equal(new URL(await page.locator('#downloadTexture').getAttribute('href'),base).searchParams.get('game'),source);
        assert(await page.evaluate(slot => !!reviewGroup.children[0].material[slot].map,slot),'Rendered replacement missing');
        assert.equal(await page.inputValue('#gameSelect'),target);
        await page.screenshot({path:path.join(output,`${source}-texture-on-${target}.png`)});
        if (target === 't1' && source === 't2') {
          // Simulate an atomic edit fingerprint in the source library, not the model library.
          const beforeReload = await state();
          await page.route('**/texture_versions?game=t2', async route => {
            const response = await route.fetch(), versions = await response.json();
            versions[filename] = ['edited',42,'replacement-file'];
            await route.fulfill({response,json:versions});
          });
          await page.waitForResponse(response => response.url().includes('/model_json/'), {timeout:7000});
          await ready(); close((await state()).camera,beforeReload.camera);
          assert.equal(new URL(await page.locator('#downloadTexture').getAttribute('href'),base).searchParams.get('game'),'t2');
          await page.unroute('**/texture_versions?game=t2');
          for (const [button, extension] of [['exportObjBtn','zip'], ['exportGlbBtn','glb']]) {
            const download = page.waitForEvent('download', {timeout:120000});
            await page.click('#'+button); await (await download).saveAs(path.join(output,`t1-with-t2.${extension}`));
          }
        }
      }
      await page.click('#resetAll'); await ready();
      assert(await page.locator('#resetSkin').isDisabled());
      assert.equal(await page.inputValue('#textureGame'),target);
    }
    await page.selectOption('#gameSelect','t2'); await ready();
    await page.selectOption('#modelSelect','interior_pbase3'); await ready();
    await page.screenshot({path:path.join(output,'interior.png')});
    assert(await page.evaluate(() => reviewCamera.near <= .01),'Interior near plane too large');
    for (const size of [{width:1024,height:768},{width:800,height:600}]) {
      await page.setViewportSize(size);
      await page.screenshot({path:path.join(output,`layout-${size.width}.png`)});
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth > innerWidth),false);
      const controls = await page.locator('#moveYP').boundingBox(), canvas = await page.locator('#c').boundingBox();
      assert(controls.y >= canvas.y && controls.y+controls.height <= canvas.y+canvas.height,'Axis controls outside viewport');
    }
    assert.deepEqual(errors,[]);
    fs.writeFileSync(path.join(output,'controls.json'),JSON.stringify({passed:true,checks:['world-axis movement and persistence','reset orientation including camera','reset all','pointer capture, walk, fly, look, speed, exit','all 9 game texture combinations rendered','interior clipping','1024 and 800 layouts'],pageErrors:errors},null,2));
    console.log('Controls checks passed:', output);
  } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exitCode=1;});
