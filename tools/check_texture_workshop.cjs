// Real-library browser regression: node tools/check_texture_workshop.cjs URL [output directory].
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

(async () => {
  const base = process.argv[2], output = path.resolve(process.argv[3] || 'build/texture-workshop-review');
  assert(base, 'Pass the running app URL');
  fs.mkdirSync(output, {recursive:true});
  const browser = await chromium.launch({headless:true, channel:'msedge'});
  const page = await browser.newPage({viewport:{width:1280,height:900}}), errors = [], checks = [];
  page.on('pageerror', error => errors.push(String(error)));
  const ready = () => page.waitForFunction(() => /^Preview (ready|loaded with)/.test(document.querySelector('#status').textContent) && !document.querySelector('#exportObjBtn').disabled && !document.querySelector('#skinSelect').disabled);
  const get = async url => {
    const response = await page.request.get(new URL(url, base).href);
    assert(response.ok(), `${url}: ${response.status()}`);
    return response;
  };
  const metadata = async () => (await get('/texture_metadata?game=t1')).json();
  const names = () => page.locator('#skinSelect option').evaluateAll(options => options.map(option => option.value).sort());
  const sameNames = async expected => {
    // Hue indexing is requested only when first used; wait for that user action.
    await page.waitForFunction(() => !document.querySelector('.workspace').inert);
    assert.deepEqual(await names(), expected.map(item => item.filename).sort());
  };
  const imageSource = async selector => {
    const value = await page.locator(selector).getAttribute('src');
    if (!value) return value;
    const url = new URL(value, base);
    url.searchParams.delete('v');
    return url.href;
  };
  const modelAction = async action => {
    const response = page.waitForResponse(response => response.url().includes('/model_json/') && response.ok());
    await action(); await response; await ready();
  };
  const exportMaterials = async suffix => {
    const request = page.waitForRequest(request => request.url().includes('/export_obj/'));
    const download = page.waitForEvent('download');
    await page.click('#exportObjBtn');
    const materials = JSON.parse(new URL((await request).url()).searchParams.get('materials'));
    await (await download).saveAs(path.join(output, suffix + '.zip'));
    return materials;
  };
  let taggedTexture, originalTags, tagsTouched = false;
  const saveTags = async action => {
    const response = page.waitForResponse(response => response.request().method() === 'POST' && response.url().includes('tag'));
    await action(); assert((await response).ok(), 'Saving tags failed');
  };
  const storedTags = async () => (await metadata()).find(item => item.filename === taggedTexture).tags;
  const inspectTiles = async () => page.locator('.texture-thumb').evaluateAll(tiles => {
    const clip = document.querySelector('#textureGallery').getBoundingClientRect();
    const visible = tiles.filter(tile => {
      const box = tile.getBoundingClientRect();
      return box.left >= clip.left && box.right <= clip.right && box.top >= Math.max(0,clip.top) && box.bottom <= Math.min(innerHeight,clip.bottom);
    });
    const overflow = visible.filter(tile => {
      const box = tile.getBoundingClientRect();
      const contents = [...tile.querySelectorAll('img,span,small')].map(child => child.getBoundingClientRect());
      return contents.some((child, index) => child.left < box.left || child.right > box.right || child.top < box.top || child.bottom > box.bottom || (index && child.top < contents[index-1].bottom));
    }).map(tile => tile.dataset.texture);
    return {visible:visible.length,overflow};
  });
  try {
    await page.goto(base); await ready();
    const inventory = await metadata();
    assert(inventory.length > 12, 'This regression needs a representative T1 texture library');
    assert(inventory.every(item => typeof item.filename === 'string' && Array.isArray(item.tags) && (item.hue === null || Number.isFinite(item.hue))));
    const chosen = inventory.find(item => item.width > 0 && item.height > 0 && item.width !== item.height && item.hue !== null) || inventory.find(item => item.width > 0 && item.height > 0);
    assert(chosen, 'No readable texture in the T1 library');
    taggedTexture = chosen.filename; originalTags = chosen.tags;
    const originalList = await (await get('/list_textures?game=t1')).json();
    const originalBytes = await (await get('/texture/' + encodeURIComponent(chosen.filename) + '?game=t1')).body();
    const appliedBefore = await imageSource('#texturePreview');
    await page.selectOption('#skinSelect', chosen.filename);
    await page.locator('#candidatePreview').evaluate(image => image.decode());
    const candidateBefore = await imageSource('#candidatePreview');
    assert.equal(await imageSource('#texturePreview'), appliedBefore, 'Selecting a candidate changed the applied material');

    await page.click('#expandGallery');
    await page.locator('#galleryDialog').waitFor({state:'visible'});
    const tileLayout = await inspectTiles(), visibleTiles = tileLayout.visible;
    assert(visibleTiles >= 12, `Expanded browser only shows ${visibleTiles} complete thumbnails`);
    assert.deepEqual(tileLayout.overflow, [], 'Thumbnail image or labels overlap or extend outside their tile');
    await page.screenshot({path:path.join(output,'expanded-1280.png')});
    await page.click('#rotateTextureRight');
    const modalRotation = await imageSource('#candidatePreview');
    assert.notEqual(modalRotation, candidateBefore);
    await page.click('#galleryUndo');
    assert.equal(await imageSource('#candidatePreview'), candidateBefore, 'Modal Undo did not restore candidate');
    await page.click('#galleryRedo');
    assert.equal(await imageSource('#candidatePreview'), modalRotation, 'Modal Redo did not restore rotation');
    await page.locator('#rotateTextureRight').focus();
    await page.keyboard.press('Control+z');
    assert.equal(await imageSource('#candidatePreview'), candidateBefore, 'Ctrl+Z outside input did not undo rotation');
    await page.keyboard.press('Control+Shift+z');
    assert.equal(await imageSource('#candidatePreview'), modalRotation, 'Ctrl+Shift+Z did not redo rotation');
    const tagsBeforeTyping = await page.inputValue('#textureTags'), historyBeforeTyping = await page.locator('#historyStatus').textContent();
    await page.locator('#textureTags').focus();
    await page.keyboard.press('End');
    await page.locator('#textureTags').pressSequentially('temporarydraft');
    await page.keyboard.press('Control+z');
    assert.equal(await imageSource('#candidatePreview'), modalRotation, 'Ctrl+Z in text field undid global rotation');
    assert.equal(await page.locator('#historyStatus').textContent(), historyBeforeTyping, 'Text undo changed global history');
    await page.fill('#textureTags', tagsBeforeTyping);
    await page.click('#galleryUndo');
    assert.equal(await imageSource('#candidatePreview'), candidateBefore);
    await page.locator('#skinSearch').pressSequentially('disc', {delay:25});
    assert.equal(await page.inputValue('#skinSearch'), 'disc', 'Typing lost search focus or characters');
    assert((await names()).length > 0, 'Typed filename search returned no results');
    await page.fill('#skinSearch', '');
    await page.selectOption('#skinSelect', chosen.filename);
    await page.setViewportSize({width:1024,height:768});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, '1024 layout overflows horizontally');
    const dialog = await page.locator('#galleryDialog').boundingBox();
    assert(dialog.x >= 0 && dialog.y >= 0 && dialog.x + dialog.width <= 1025 && dialog.y + dialog.height <= 769, 'Expanded dialog extends outside the viewport');
    const smallLayout = await inspectTiles();
    assert(smallLayout.visible > 0, '1024 gallery has no complete tiles');
    assert.deepEqual(smallLayout.overflow, [], '1024 thumbnail image or labels overflow their tile');
    await page.screenshot({path:path.join(output,'expanded-1024.png')});
    await page.click('#closeGallery');
    await page.screenshot({path:path.join(output,'compact-1024.png')});
    await page.setViewportSize({width:1280,height:900});
    checks.push(`expanded browser displays ${visibleTiles} complete thumbnails; compact and 1024 layouts`);
    checks.push('thumbnail images and labels contained without overlap; modal Undo/Redo; keyboard history and native text undo; real multi-character search typing');

    await page.click('#rotateTextureRight');
    const rotated = await imageSource('#candidatePreview');
    assert.notEqual(rotated, candidateBefore, 'Rotation did not change candidate');
    assert.equal(await imageSource('#texturePreview'), appliedBefore, 'Rotation changed material before Apply');
    await page.click('#undoAction');
    assert.equal(await imageSource('#candidatePreview'), candidateBefore, 'Undo did not restore candidate');
    await page.click('#redoAction');
    assert.equal(await imageSource('#candidatePreview'), rotated, 'Redo did not restore rotation');
    const downloadEvent = page.waitForEvent('download');
    await page.click('#saveTextureCopy');
    const copyPath = path.join(output,'rotated-copy.png');
    await (await downloadEvent).saveAs(copyPath);
    const png = fs.readFileSync(copyPath);
    assert.equal(png.subarray(1,4).toString(), 'PNG');
    assert.equal(png.readUInt32BE(16), chosen.height, 'Rotated PNG width');
    assert.equal(png.readUInt32BE(20), chosen.width, 'Rotated PNG height');
    for (const id of ['flipTextureX','flipTextureY']) {
      const before = await imageSource('#candidatePreview');
      await page.click('#' + id);
      assert.notEqual(await imageSource('#candidatePreview'), before, `${id} did not change candidate`);
      await page.click('#undoAction');
      assert.equal(await imageSource('#candidatePreview'), before, `Undo ${id}`);
    }
    await page.click('#rotateTextureLeft');
    assert.equal(await imageSource('#candidatePreview'), candidateBefore, 'Counterclockwise rotation did not reverse clockwise rotation');
    await page.click('#undoAction');
    await page.click('#resetTextureCopy');
    assert.equal(await imageSource('#candidatePreview'), candidateBefore, 'Reset copy did not restore source');
    await page.click('#undoAction');
    assert.equal(await imageSource('#candidatePreview'), rotated, 'Reset copy undo lost rotation');
    assert.deepEqual(await exportMaterials('unapplied'), {}, 'An unapplied candidate changed export materials');
    await modelAction(() => page.click('#applySkin'));
    const slot = await page.inputValue('#materialSelect');
    const appliedRotated = await imageSource('#texturePreview');
    assert.notEqual(appliedRotated, appliedBefore);
    const appliedMaterials = await exportMaterials('applied-rotation');
    assert.equal(appliedMaterials[slot].filename, chosen.filename);
    assert.deepEqual(appliedMaterials[slot].transform, {rotation:90,flip_x:false,flip_y:false});
    await modelAction(() => page.click('#undoAction'));
    assert.equal(await imageSource('#texturePreview'), appliedBefore, 'Undo Apply did not restore material');
    await modelAction(() => page.click('#redoAction'));
    assert.equal(await imageSource('#texturePreview'), appliedRotated, 'Redo Apply lost rotation');
    await modelAction(() => page.click('#resetSkin'));
    assert.equal(await imageSource('#texturePreview'), appliedBefore, 'Reset slot did not restore material');
    await modelAction(() => page.click('#undoAction'));
    assert.equal(await imageSource('#texturePreview'), appliedRotated, 'Undo Reset slot lost material');
    assert.deepEqual(await (await get('/texture/' + encodeURIComponent(chosen.filename) + '?game=t1')).body(), originalBytes, 'Original PNG changed');
    assert.deepEqual(await (await get('/list_textures?game=t1')).json(), originalList, 'Temporary copies accumulated in texture library');
    checks.push('temporary rotation/flips/reset, explicit Apply, PNG dimensions, source preservation, export transform, undo/redo');

    await page.fill('#positionY', '1.25');
    await page.fill('#moveStep', '.5');
    await page.click('#moveYP');
    assert.equal(Number(await page.inputValue('#positionY')), 1.75, 'Position blur redirected the step input');
    assert.equal(Number(await page.inputValue('#moveStep')), .5, 'Move step lost the typed value');
    await page.click('#undoAction');
    assert.equal(Number(await page.inputValue('#positionY')), 1.25, 'Undo movement did not preserve typed position');
    checks.push('position-to-step focus transition keeps both typed values and movement undo');
    const positionBefore = Number(await page.inputValue('#positionX'));
    const step = Number(await page.inputValue('#moveStep'));
    await page.click('#moveXP');
    assert.equal(Number(await page.inputValue('#positionX')), positionBefore + step);
    await page.click('#undoAction');
    assert.equal(Number(await page.inputValue('#positionX')), positionBefore);
    await page.click('#redoAction');
    assert.equal(Number(await page.inputValue('#positionX')), positionBefore + step);
    const modelBefore = await page.inputValue('#modelSelect');
    const nextModel = modelBefore === 'shotgun' ? 'disc' : 'shotgun';
    assert(nextModel, 'Need a second model for history check');
    await modelAction(() => page.selectOption('#modelSelect', nextModel));
    await modelAction(() => page.click('#undoAction'));
    assert.equal(await page.inputValue('#modelSelect'), modelBefore);
    assert.equal(await imageSource('#texturePreview'), appliedRotated, 'Model undo lost material override');
    await modelAction(() => page.click('#redoAction'));
    assert.equal(await page.inputValue('#modelSelect'), nextModel);
    checks.push('model movement and model selection undo/redo');

    await page.selectOption('#skinSelect', chosen.filename);
    await page.locator('#textureFilters').evaluate(details => { details.open = true; });
    await page.fill('#widthMin', String(chosen.width));
    await page.fill('#widthMax', String(chosen.width + 20));
    await page.fill('#heightMin', String(chosen.height));
    await page.fill('#heightMax', String(chosen.height + 20));
    await sameNames(inventory.filter(item => item.width >= chosen.width && item.width <= chosen.width + 20 && item.height >= chosen.height && item.height <= chosen.height + 20));
    await page.fill('#widthMin','999999');
    assert.equal((await names()).length, 0, 'Impossible size range returned textures');
    assert(await page.locator('#applySkin').isDisabled());
    await page.click('#clearTextureFilters');
    for (const tolerance of [0,64]) {
      await page.selectOption('#skinSelect', chosen.filename);
      await page.fill('#sizeTolerance',String(tolerance));
      await page.click('#similarSize');
      await sameNames(inventory.filter(item => item.width > 0 && item.height > 0 && Math.abs(item.width-chosen.width) <= tolerance && Math.abs(item.height-chosen.height) <= tolerance));
      await page.click('#clearTextureFilters');
    }
    const colored = inventory.find(item => item.hue !== null && item.width > 0);
    if (colored) for (const tolerance of [0,180]) {
      await page.selectOption('#skinSelect', colored.filename);
      await page.fill('#hueTolerance',String(tolerance));
      await page.click('#similarHue');
      await sameNames(inventory.filter(item => item.hue !== null && Math.min(Math.abs(item.hue-colored.hue),360-Math.abs(item.hue-colored.hue)) <= tolerance));
      await page.click('#clearTextureFilters');
    }
    const neutral = inventory.find(item => item.hue === null && item.width > 0);
    if (neutral) {
      await page.selectOption('#skinSelect', neutral.filename);
      await page.click('#similarHue');
      await sameNames(inventory.filter(item => item.hue === null && item.width > 0));
      await page.click('#clearTextureFilters');
    }
    checks.push(`inclusive dimensions, empty range, configurable size/hue similarity${neutral ? ', neutral hue matching' : ' (no neutral texture available)'}`);

    await page.selectOption('#skinSelect', chosen.filename);
    const newTag = 'workshopcheck' + Date.now();
    await page.fill('#textureTags', [...originalTags,newTag].join(', '));
    tagsTouched = true;
    await saveTags(() => page.click('#saveTextureTags'));
    assert((await storedTags()).includes(newTag), 'Tags were not saved');
    await saveTags(() => page.click('#undoAction'));
    assert.deepEqual(await storedTags(), originalTags, 'Undo tags was not persisted');
    await saveTags(() => page.click('#redoAction'));
    assert((await storedTags()).includes(newTag), 'Redo tags was not persisted');
    await page.fill('#skinSearch',newTag);
    assert.deepEqual(await names(), [chosen.filename], 'Tag search did not identify tagged texture');
    await page.click('#rotateTextureRight');
    await page.reload(); await ready();
    assert(await page.locator('#undoAction').isDisabled(), 'Reload retained old session history');
    await page.selectOption('#skinSelect', chosen.filename);
    assert.equal(await imageSource('#candidatePreview'), candidateBefore, 'Reload retained temporary rotation');
    assert((await page.inputValue('#textureTags')).includes(newTag), 'Reload lost persisted tag');
    assert.deepEqual(errors, []);
    checks.push('persistent searchable tags with persisted undo/redo; reload clears temporary copy and session history');
    fs.writeFileSync(path.join(output,'workshop.json'),JSON.stringify({passed:true,texture:chosen.filename,visibleTiles,checks,pageErrors:errors},null,2));
    console.log('Texture workshop checks passed:', output);
  } finally {
    try {
      if (tagsTouched) {
        await page.reload(); await ready();
        await page.selectOption('#skinSelect', taggedTexture);
        await page.fill('#textureTags', originalTags.join(', '));
        await saveTags(() => page.click('#saveTextureTags'));
        assert.deepEqual(await storedTags(), originalTags, 'Regression failed to restore original user tags');
      }
    } finally { await browser.close(); }
  }
})().catch(error => {console.error(error); process.exitCode=1;});
