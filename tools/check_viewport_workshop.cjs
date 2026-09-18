// Packaged or source UI check: node tools/check_viewport_workshop.cjs URL [output directory].
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

(async () => {
  const base = process.argv[2], output = path.resolve(process.argv[3] || 'build/viewport-review');
  assert(base, 'Pass the running app URL');
  fs.mkdirSync(output, {recursive:true});
  const browser = await chromium.launch({headless:true, channel:'msedge'});
  const page = await browser.newPage({viewport:{width:1280,height:720}}), errors = [];
  page.on('pageerror', error => errors.push(String(error)));
  const ready = () => page.waitForFunction(() => /^Preview (ready|loaded with)/.test(document.querySelector('#status').textContent) && !document.querySelector('#exportObjBtn').disabled);
  const capture = async name => {
    const download = page.waitForEvent('download');
    await page.click('#saveViewport');
    const file = await download;
    assert(file.suggestedFilename().endsWith('_view.png'));
    const destination = path.join(output, name + '.png');
    await file.saveAs(destination);
    const bytes = fs.readFileSync(destination);
    assert.equal(bytes.subarray(1,4).toString(), 'PNG');
    const pixels = await page.evaluate(async encoded => {
      const img = new Image(); img.src = 'data:image/png;base64,' + encoded; await img.decode();
      const canvas = document.createElement('canvas'); canvas.width = img.width; canvas.height = img.height;
      const ctx = canvas.getContext('2d'); ctx.drawImage(img,0,0);
      const rgba = ctx.getImageData(0,0,img.width,img.height).data;
      let different = 0;
      for (let i=0;i<rgba.length;i+=4) if (rgba[i]!==rgba[0] || rgba[i+1]!==rgba[1] || rgba[i+2]!==rgba[2]) different++;
      const view = document.querySelector('#c');
      return {different,width:img.width,height:img.height,viewWidth:view.width,viewHeight:view.height};
    }, bytes.toString('base64'));
    assert.equal(pixels.width,pixels.viewWidth); assert.equal(pixels.height,pixels.viewHeight);
    assert(pixels.different > 100, 'PNG contains only a blank background');
    return pixels.different;
  };
  try {
    await page.goto(base); await ready();
    assert.equal(await page.locator('#transformControls').getAttribute('open'), null);
    await page.click('#transformControls summary');
    await page.click('#rotX90');
    await page.click('#undoAction');
    await page.click('#transformControls summary');
    assert(!(await page.locator('#positionX').isVisible()));
    const narrow = await capture('fov45');
    await page.fill('#fieldOfView','90'); await page.locator('#fieldOfView').press('Tab');
    assert.equal(await page.inputValue('#fieldOfViewRange'),'90');
    const wide = await capture('fov90');
    assert(wide < narrow * .6, 'FOV did not widen the actual rendered view');
    await page.click('#undoAction'); assert.equal(await page.inputValue('#fieldOfView'),'45');
    await page.click('#redoAction'); assert.equal(await page.inputValue('#fieldOfView'),'90');
    await page.fill('#fieldOfView','200'); await page.locator('#fieldOfView').press('Tab');
    assert.equal(await page.inputValue('#fieldOfView'),'110');
    await page.locator('#fieldOfViewRange').focus(); await page.keyboard.press('Home'); await page.keyboard.press('ArrowRight');
    assert.equal(await page.inputValue('#fieldOfView'),'21');
    await page.click('#resetAll'); await ready(); assert.equal(await page.inputValue('#fieldOfView'),'45');
    await page.screenshot({path:path.join(output,'model-1280.png')});
    await page.click('#expandGallery');
    for (const [width,height] of [[1280,720],[960,600],[640,720]]) {
      await page.setViewportSize({width,height});
      for (const scrollTop of [0,100000]) {
        await page.locator('#galleryDialog .browser-controls').evaluate((el,y) => {el.scrollTop=y;},scrollTop);
        const visible = await page.locator('#applySkin').evaluate(el => {
          const r = el.getBoundingClientRect(), hit = document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
          return r.top>=0 && r.bottom<=innerHeight && (hit===el || el.contains(hit));
        });
        assert(visible, `Apply clipped or obscured at ${width}x${height}`);
      }
      await page.screenshot({path:path.join(output,`gallery-${width}.png`)});
    }
    await page.setViewportSize({width:1280,height:720});
    await page.selectOption('#skinSelect','ammo.png');
    const applied = page.waitForResponse(r => r.url().includes('/model_json/') && r.ok());
    await page.click('#applySkin'); await applied; await ready();
    await page.click('#closeGallery');
    assert((await page.locator('#textureDetails').innerText()).includes('ammo.png'));
    for (const game of ['t2','q3']) {
      await page.selectOption('#gameSelect',game); await ready();
      await capture(game + '-view');
    }
    assert.deepEqual(errors,[]);
    fs.writeFileSync(path.join(output,'checks.json'),JSON.stringify({passed:true,checks:['visible Apply at three sizes and scroll positions','collapsed transforms and undo','FOV pixels, slider, clamp, undo/redo and reset','PNG dimensions and nonblank T1/T2/Q3 captures','apply texture through modal'],errors},null,2));
    console.log('Viewport workshop checks passed:',output);
  } finally { await browser.close(); }
})().catch(error => {console.error(error);process.exitCode=1;});
