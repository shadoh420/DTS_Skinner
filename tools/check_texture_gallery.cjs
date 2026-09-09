// node tools/check_texture_gallery.cjs URL [screenshots directory]
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
(async () => {
  const base = process.argv[2], output = path.resolve(process.argv[3] || 'build/texture-gallery-review');
  fs.mkdirSync(output,{recursive:true});
  const browser = await chromium.launch({headless:true,channel:'msedge'});
  try {
    const page = await browser.newPage({viewport:{width:1280,height:900}}), errors = [];
    let modelRequests = 0;
    page.on('pageerror', error => errors.push(String(error)));
    page.on('request', request => { if (request.url().includes('/model_json/')) modelRequests++; });
    await page.goto(base);
    const ready = () => page.waitForFunction(() => /^Preview (ready|loaded with)/.test(document.querySelector('#status').textContent) && !document.querySelector('#exportObjBtn').disabled);
    await ready();
    const original = await page.locator('#texturePreview').getAttribute('src'), requests = modelRequests;
    await page.fill('#skinSearch','disc');
    const candidate = page.locator('.texture-thumb[data-texture="disc.png"]');
    await candidate.scrollIntoViewIfNeeded();
    await candidate.locator('img').evaluate(image => image.decode());
    await candidate.click();
    assert.equal(await page.inputValue('#skinSelect'),'disc.png');
    assert.equal(await candidate.getAttribute('aria-pressed'),'true');
    assert.equal(await page.locator('#texturePreview').getAttribute('src'),original,'Browsing changed applied texture');
    assert.equal(modelRequests,requests,'Browsing reloaded the model');
    const exportRequest = page.waitForRequest(request=>request.url().includes('/export_obj/'));
    const download = page.waitForEvent('download');
    await page.click('#exportObjBtn');
    assert.equal(new URL((await exportRequest).url()).searchParams.get('materials'),'{}','Unapplied thumbnail changed export');
    await (await download).saveAs(path.join(output,'unapplied-selection.zip'));
    await page.locator('#textureGallery').scrollIntoViewIfNeeded();
    await page.screenshot({path:path.join(output,'t1-thumbnails.png')});
    await page.click('#applySkin'); await ready();
    assert.equal(new URL(await page.locator('#downloadTexture').getAttribute('href'),base).pathname,'/texture/disc.png');
    for (const game of ['t2','q3']) {
      const before = modelRequests;
      await page.selectOption('#textureGame',game);
      await page.waitForFunction(game => document.querySelector('#textureGallery img')?.src.includes('game='+game),game);
      const tiles = page.locator('.texture-thumb');
      assert.equal(await tiles.count(),await page.locator('#skinSelect option').count());
      const tile = tiles.first(); await tile.scrollIntoViewIfNeeded();
      await tile.locator('img').evaluate(image => image.decode()); await tile.click();
      const source = new URL(await tile.locator('img').getAttribute('src'),base);
      assert.equal(source.searchParams.get('game'),game);
      if (game === 't2') assert.equal(source.searchParams.get('opaque'),'1');
      assert.equal(modelRequests,before,'Library browsing changed the model');
      if (game === 'q3') {
        await page.fill('#skinSearch','textures_');
        assert(await tiles.count()>0, 'Q3 map textures missing from gallery');
        await tiles.first().scrollIntoViewIfNeeded();
        await tiles.first().locator('img').evaluate(image => image.decode());
      }
      await page.screenshot({path:path.join(output,game+'-thumbnails.png')});
    }
    await page.fill('#skinSearch','no_texture_should_match_this_987');
    assert.equal(await page.locator('.texture-thumb').count(),0);
    assert(await page.locator('#applySkin').isDisabled());
    await page.fill('#skinSearch','');
    assert(await page.locator('.texture-thumb').count()>0);
    const last = page.locator('#skinSelect option').last();
    await page.selectOption('#skinSelect',await last.getAttribute('value'));
    const selected = page.locator('.texture-thumb[aria-pressed="true"]');
    assert.equal(await selected.count(),1);
    await selected.locator('img').evaluate(image => image.decode());
    await page.setViewportSize({width:1024,height:768});
    await page.screenshot({path:path.join(output,'layout-1024.png')});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    assert.deepEqual(errors,[]);
    fs.writeFileSync(path.join(output,'gallery.json'),JSON.stringify({passed:true,checks:['T1/T2/Q3 thumbnail rendering','filename filtering and empty search','selection does not apply or change exports','explicit apply','dropdown synchronization and lazy loading','1024 layout'],pageErrors:errors},null,2));
    console.log('Texture gallery checks passed:',output);
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
