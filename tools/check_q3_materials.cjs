// Hidden, rendered Q3 material regression check; Playwright in NODE_PATH.
const {chromium} = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
(async () => {
  const base = process.argv[2], output = path.resolve(process.argv[3] || 'build/material-review');
  fs.mkdirSync(output, {recursive:true});
  const browser = await chromium.launch({headless:true, channel:'msedge'});
  try {
    const page = await browser.newPage({viewport:{width:1440,height:1000}}), errors = [], results = [];
    page.on('pageerror', error => errors.push(String(error)));
    page.on('console', message => {if (message.type()==='error') errors.push(message.text());});
    // Observe the actual draw materials without adding a test API to the app.
    await page.route('**/static/skinner.js', async route => {
      const response = await route.fetch();
      await route.fulfill({response, body:`
        const BaseRenderer = THREE.WebGLRenderer;
        THREE.WebGLRenderer = function(...args) {
          const renderer = new BaseRenderer(...args), originalRender = renderer.render;
          renderer.render = function(scene, camera) {
          window.reviewMeshes=[];
          scene.traverse(object=>{if(object.isMesh) window.reviewMeshes.push(object);});
          window.reviewRender=()=>originalRender.call(this,scene,camera);
          return originalRender.call(this,scene,camera);
          };
          return renderer;
        };
      ` + await response.text()});
    });
    await page.goto(base);
    const ready = async () => {
      await page.waitForFunction(()=>document.querySelector('#status').textContent.startsWith('Preview ready.'));
      await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    };
    await ready(); await page.selectOption('#gameSelect','q3');
    await page.waitForFunction(()=>document.querySelector('#modelTitle').textContent.startsWith('Q3 /')); await ready();
    const catalog = await (await page.request.get(base+'/list_models?game=q3')).json();
    for (const label of ['flags/b_flag','flags/r_flag','hunter / default','uriel / default','mapobjects/bitch/fembotbig','weapons2/rocketl/rocketl']) {
      const entry=catalog.find(x=>x.display_name===label); assert(entry,'Missing sample '+label);
      await page.selectOption('#modelSelect',entry.model_name); await ready();
      await page.selectOption('#viewSelect','front');
      const materials=await page.evaluate(()=>window.reviewMeshes.flatMap(mesh=>Array.isArray(mesh.material)?mesh.material:[mesh.material]).map(m=>({alphaTest:m.alphaTest,transparent:m.transparent,side:m.side,depthWrite:m.depthWrite,texture:!!m.map})));
      assert(materials.every(m=>m.texture),label+' has missing textures');
      const winding = await page.evaluate(()=> {
        let outward=0,inward=0;
        for(const mesh of window.reviewMeshes) {
          const g=mesh.geometry, p=g.attributes.position,n=g.attributes.normal,indices=g.index.array;
          for(let i=0;i<indices.length;i+=3) {
            const [a,b,c]=[0,1,2].map(j=>new THREE.Vector3().fromBufferAttribute(p,indices[i+j]));
            const normal=new THREE.Vector3().fromBufferAttribute(n,indices[i]);
            const dot=b.sub(a).cross(c.sub(a)).dot(normal);
            if(dot>1e-6)outward++;else if(dot< -1e-6)inward++;
          }
        }
        return {outward,inward};
      });
      assert(winding.outward>winding.inward*3,label+' triangle order disagrees with source normals: '+JSON.stringify(winding));
      if (/flag|hunter|uriel|fembot/.test(label)) assert(materials.some(m=>m.alphaTest===.5),label+' missing cutouts');
      if (label.startsWith('flags/')) {
        assert.equal(materials[1].alphaTest,.5); assert.equal(materials[1].side,2);
        assert.equal(materials[3].transparent,false); assert.equal(materials[3].alphaTest,0);
        const raster=await page.evaluate(()=>{
          const canvas=document.querySelector('canvas'), gl=canvas.getContext('webgl2')||canvas.getContext('webgl');
          const pixels=()=>{window.reviewRender();const p=new Uint8Array(gl.drawingBufferWidth*gl.drawingBufferHeight*4);gl.readPixels(0,0,gl.drawingBufferWidth,gl.drawingBufferHeight,gl.RGBA,gl.UNSIGNED_BYTE,p);return p;};
          const correct=pixels(), cutout=window.reviewMeshes[0].material[1];
          cutout.alphaTest=0;cutout.needsUpdate=true;const opaque=pixels();
          cutout.alphaTest=.5;cutout.needsUpdate=true;window.reviewRender();
          let changed=0;for(let i=0;i<correct.length;i+=4) if(Math.abs(correct[i]-opaque[i])+Math.abs(correct[i+1]-opaque[i+1])+Math.abs(correct[i+2]-opaque[i+2])>10)changed++;
          return {changed,glError:gl.getError()};
        });
        assert(raster.changed>100,'Cutout did not change rendered pixels: '+JSON.stringify(raster));
        assert.equal(raster.glError,0); results.push({label,materials,raster});
        for(const format of ['obj','glb']) {
          const response=await page.request.get(base+'/export_'+format+'/'+entry.model_name+'?game=q3');
          assert.equal(response.status(),200);fs.writeFileSync(path.join(output,label.replaceAll('/','_')+'.'+(format==='obj'?'zip':'glb')),await response.body());
        }
      } else results.push({label,materials});
      await page.screenshot({path:path.join(output,label.replaceAll('/','_').replaceAll(' ','')+'.png')});
      await page.selectOption('#viewSelect','back');
      await page.screenshot({path:path.join(output,label.replaceAll('/','_').replaceAll(' ','')+'-back.png')});
      await page.selectOption('#viewSelect','front');
      await page.check('#lighting'); await ready();
      await page.screenshot({path:path.join(output,label.replaceAll('/','_').replaceAll(' ','')+'-lit.png')});
      await page.uncheck('#lighting'); await ready();
    }
    assert.deepEqual(errors,[]); fs.writeFileSync(path.join(output,'results.json'),JSON.stringify(results,null,2));
    console.log('Q3 materials: '+results.length+' rendered samples, cutout pixels verified, no browser/WebGL errors.');
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
