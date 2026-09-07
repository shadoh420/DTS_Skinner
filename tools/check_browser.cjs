// Optional hidden browser acceptance runner; requires Playwright in NODE_PATH.
// node tools/check_browser.cjs http://127.0.0.1:5068 build/review
const {chromium} = require('playwright');
const fs = require('node:fs');
const path = require('node:path');

(async () => {
  const base = process.argv[2], output = path.resolve(process.argv[3] || 'build/review');
  fs.mkdirSync(output, {recursive:true});
  const browser = await chromium.launch({headless:true, channel:'msedge'});
  try {
    const page = await browser.newPage({viewport:{width:1280,height:800}, deviceScaleFactor:1});
    const errors = [];
    page.on('pageerror', error => errors.push(String(error)));
    await page.route('**/*', route => route.request().url().startsWith(base) ? route.continue() : route.abort());
    await page.goto(base);
    const ready = async () => {
      await page.waitForFunction(() => document.querySelector('#status').textContent.startsWith('Preview ready.') || document.querySelector('#status').textContent.startsWith('Preview loaded with'), null, {timeout:60000});
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    };
    await ready();
    for (const game of ['t1','t2']) {
      if (game !== 't1') {
        await page.selectOption('#gameSelect', game);
        await page.waitForFunction(() => document.querySelector('#modelTitle').textContent.startsWith('T2 /'));
        await ready();
      }
      const entries = await page.locator('#modelSelect option').evaluateAll(options => options.map(x => x.value));
      const catalog = await (await page.request.get(base+'/list_models?game='+game)).json();
      const expected = fs.readFileSync(game === 't1' ? 'model_catalog.txt' : 't2_catalog.txt','utf8').trim().split(/\r?\n/);
      if (JSON.stringify(entries) !== JSON.stringify(expected)) throw new Error(game+' UI catalog mismatch');
      const samples = game === 't1' ? ['disc','larmor','lfemale','mfemale','harmor','challs2'] : ['light_male','light_female','medium_female','heavy_male','bioderm_heavy','weapon_disc','vehicle_grav_scout','ext_flagstand','grenade_flash','grenade_flare','interior_pbase3', ...entries.filter(x=>x.startsWith('interior_')).slice(0,2)];
      for (const name of samples.filter(x=>catalog.some(entry=>entry.model_name === x && entry.status === 'ready'))) {
        await page.selectOption('#modelSelect', name);
        await ready();
        await page.screenshot({path:path.join(output,game+'-'+name+'.png')});
        console.log(game, name, await page.locator('#status').textContent());
      }
      if (game === 't2') {
        await page.selectOption('#modelSelect','light_male'); await ready();
        const beagle = await page.locator('#skinSelect option').evaluateAll(options => options.find(x=>x.value.startsWith('beagle.lmale__')).value);
        await page.selectOption('#skinSelect',beagle); await page.click('#applySkin'); await ready();
        await page.screenshot({path:path.join(output,'t2-custom-skin.png')});
        const downloadEvent = page.waitForEvent('download');
        await page.click('#exportObjBtn');
        await (await downloadEvent).saveAs(path.join(output,'custom-t2-export.zip'));
        await page.selectOption('#modelSelect','xorg2');
        await page.waitForFunction(()=>document.querySelector('#status').textContent.startsWith('Preview unavailable'));
        if (!(await page.locator('#exportObjBtn').isDisabled()) || !(await page.locator('#warnings').textContent())) throw new Error('Unsupported model not explained');
        await page.screenshot({path:path.join(output,'t2-unsupported.png')});
      }
      await page.fill('#modelSearch', game === 't1' ? 'bunker' : 'weapon');
      const shown = await page.locator('#modelSelect option').allTextContents();
      if (!shown.length) throw new Error('Empty filtered list');
      await page.fill('#modelSearch','');
    }
    await page.selectOption('#gameSelect','t1');
    await page.waitForFunction(() => document.querySelector('#modelTitle').textContent === 'T1 / disc');
    await ready();
    await page.selectOption('#skinSelect','disc.png');
    await page.click('#applySkin');
    await ready();
    if (!(await page.locator('#materialSelect').textContent()).includes('disc.png')) throw new Error('Skin override missing');
    const downloadEvent = page.waitForEvent('download');
    await page.click('#exportObjBtn');
    const download = await downloadEvent;
    await download.saveAs(path.join(output,'custom-disc-export.zip'));
    await page.click('#resetSkin'); await ready();
    if (!(await page.locator('#materialSelect').textContent()).includes('stock_disc.png')) throw new Error('Skin reset missing');
    await page.locator('summary').first().click();
    await page.fill('#textureName','rainbow.png');
    const exportRequest = page.waitForRequest(request=>request.url().includes('/export_obj/'));
    await page.click('#exportObjBtn');
    if (new URL((await exportRequest).url()).searchParams.get('texture') !== 'stock_disc.png') throw new Error('Unapplied fallback changed export');
    await page.setViewportSize({width:1024,height:768});
    await page.screenshot({path:path.join(output,'normal-window.png')});
    if (await page.evaluate(()=>document.documentElement.scrollWidth > innerWidth)) throw new Error('Horizontal overflow at 1024');
    if (errors.length) throw new Error(errors.join('\n'));
    fs.writeFileSync(path.join(output,'browser.json'),JSON.stringify({pageErrors:errors,offline:true,viewport:[1280,800],normalWindow:[1024,768],samples:'See screenshots'},null,2));
    console.log('Browser checks passed; inspect screenshots in',output);
  } finally { await browser.close(); }
})().catch(error => {console.error(error);process.exitCode=1;});
