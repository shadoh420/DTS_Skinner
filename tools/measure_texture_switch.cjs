// node tools/measure_texture_switch.cjs http://127.0.0.1:5079
// Run against a freshly started server for cold metadata-cache measurements.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
(async () => {
  const browser = await chromium.launch({headless:true, channel:'msedge'});
  try {
    const page = await browser.newPage({viewport:{width:1280,height:900}});
    const errors = [], requests = [];
    page.on('pageerror', error => errors.push(String(error)));
    page.on('request', request => requests.push(request.url()));
    await page.addInitScript(() => {
      window.galleryAdded = 0;
      document.addEventListener('DOMContentLoaded', () => {
        new MutationObserver(records => { for (const r of records) window.galleryAdded += r.addedNodes.length; })
          .observe(document.querySelector('#textureGallery'), {childList:true});
      });
    });
    for (const [index, game] of ['t1','t2','q3','t1','t2','q3'].entries()) {
      const start = Date.now(), requestStart = requests.length;
      if (index === 0) await page.goto(process.argv[2]);
      else { await page.evaluate(() => {window.galleryAdded=0;}); await page.selectOption('#gameSelect',game); }
      await page.waitForFunction(game => document.querySelector('#modelTitle').textContent.startsWith(game.toUpperCase()+' /') &&
        /^Preview (ready|loaded with)/.test(document.querySelector('#status').textContent) && !document.querySelector('.workspace').inert,
        game, {timeout:90000});
      const ms=Date.now()-start;
      const state = await page.evaluate(() => ({galleryAdded:window.galleryAdded, galleryNodes:document.querySelector('#textureGallery').children.length,
        textures:document.querySelector('#skinSelect').options.length}));
      console.log(JSON.stringify({game,cache:index<3?'cold':'warm',ms,...state,textureRequests:requests.slice(requestStart).filter(url=>url.includes('/texture/')).length}));
      assert(requests.slice(requestStart).filter(url => url.includes('/texture_metadata?')).every(url => new URL(url).searchParams.get('details') === 'dimensions'),
        'Game switching decoded colors before a hue search was requested');
      const firstTile = await page.locator('.texture-thumb').first().elementHandle();
      await page.selectOption('#skinSelect', {index:1});
      assert(await firstTile.evaluate(tile => tile.isConnected), 'Selecting a texture rebuilt the gallery');
    }
    assert.deepEqual(errors,[]);
  } finally { await browser.close(); }
})().catch(error => {console.error(error);process.exitCode=1;});
