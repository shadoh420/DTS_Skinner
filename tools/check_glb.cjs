// Independent hidden GLB render check. Test-only deps: three@0.149.0, playwright,
// gltf-validator in NODE_PATH. node tools/check_glb.cjs build/glb-review
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const assert = require('node:assert/strict');
const {chromium} = require('playwright');
const validator = require('gltf-validator');

const directory = path.resolve(process.argv[2] || 'build/glb-review');
const threeRoot = path.dirname(path.dirname(require.resolve('three')));
const selected = process.argv.slice(3);
const files = fs.readdirSync(directory).filter(name => name.endsWith('.glb') && (!selected.length || selected.includes(name))).sort();
assert(files.length, 'No GLB review inputs');
const html = `<!doctype html><html><head><meta charset="utf-8"><style>
body{margin:0;background:#18232b;color:#e4edf2;font:16px system-ui}#label{position:absolute;top:14px;left:18px}canvas{display:block}
</style><script type="importmap">{"imports":{"three":"/vendor/build/three.module.js"}}</script></head>
<body><div id="label"></div><script type="module">
import * as THREE from 'three';
import {GLTFLoader} from '/vendor/examples/jsm/loaders/GLTFLoader.js';
const renderer=new THREE.WebGLRenderer({antialias:true,preserveDrawingBuffer:true});
renderer.setSize(innerWidth,innerHeight); renderer.setClearColor(0x18232b);
renderer.outputEncoding=THREE.sRGBEncoding; document.body.appendChild(renderer.domElement);
const scene=new THREE.Scene(), camera=new THREE.PerspectiveCamera(35,innerWidth/innerHeight,.001,10000);
scene.add(new THREE.HemisphereLight(0xffffff,0x6a7480,1));
const loader=new GLTFLoader(); let model,mixer,clip,action;
window.loadModel=async name=>{
  const gltf=await loader.loadAsync('/model?name='+encodeURIComponent(name));
  model=gltf.scene; scene.add(model); model.updateMatrixWorld(true);
  clip=gltf.animations.find(x=>/^run$|^legs_run$/i.test(x.name))||gltf.animations.find(x=>/run|walk|forward/i.test(x.name))||gltf.animations.find(x=>x.duration>0);
  if(!clip) throw new Error('No playable animation in '+name);
  mixer=new THREE.AnimationMixer(model); action=mixer.clipAction(clip); action.setLoop(THREE.LoopOnce,1); action.clampWhenFinished=true; action.play();
  return {clip:clip.name,duration:clip.duration,tracks:clip.tracks.map(x=>x.name),animations:gltf.animations.map(x=>x.name)};
};
window.framePositions=vertices=>{
  const box=new THREE.Box3().setFromArray(vertices),center=box.getCenter(new THREE.Vector3());
  const size=box.getSize(new THREE.Vector3()).length(), distance=size/(2*Math.tan(THREE.MathUtils.degToRad(camera.fov)/2));
  camera.position.copy(center).add(new THREE.Vector3(1,.35,1.5).normalize().multiplyScalar(distance*1.15));
  camera.near=Math.max(size/1000,.0001); camera.far=distance*10; camera.lookAt(center);camera.updateProjectionMatrix();
};
window.sample=time=>{
  mixer.setTime(time);model.updateMatrixWorld(true);renderer.render(scene,camera);
  document.querySelector('#label').textContent=clip.name+' · '+time.toFixed(3)+' s';
  const vertices=[],activeTargets=[];
  model.traverse(mesh=>{
    if(!mesh.isMesh) return;
    const base=mesh.geometry.attributes.position, morphs=mesh.geometry.morphAttributes.position||[];
    const active=(mesh.morphTargetInfluences||[]).map((weight,i)=>({weight,i})).filter(x=>Math.abs(x.weight)>1e-7);
    activeTargets.push(active);
    for(let i=0;i<base.count;i++){
      const p=new THREE.Vector3().fromBufferAttribute(base,i);
      for(const {weight,i:target} of active){
        const delta=new THREE.Vector3().fromBufferAttribute(morphs[target],i);
        if(!mesh.geometry.morphTargetsRelative) delta.sub(new THREE.Vector3().fromBufferAttribute(base,i));
        p.addScaledVector(delta,weight);
      }
      p.applyMatrix4(mesh.matrixWorld);vertices.push(p.x,p.y,p.z);
    }
  });
  return {vertices,activeTargets,drawCalls:renderer.info.render.calls,triangles:renderer.info.render.triangles};
};
window.ready=true;
</script></body></html>`;

(async()=>{
  const results=[];
  for(const name of files){
    const report=await validator.validateBytes(new Uint8Array(fs.readFileSync(path.join(directory,name))));
    fs.writeFileSync(path.join(directory,name+'.validation.json'),JSON.stringify(report,null,2));
    assert.equal(report.issues.numErrors,0,name+' glTF validator errors: '+JSON.stringify(report.issues.messages.slice(0,8)));
    results.push({name,validator:report.issues});
  }
  const server=http.createServer((request,response)=>{
    try{
      const url=new URL(request.url,'http://localhost');
      if(url.pathname==='/'){response.setHeader('Content-Type','text/html');response.end(html);return;}
      if(url.pathname==='/favicon.ico'){response.writeHead(204).end();return;}
      let filename;
      if(url.pathname==='/model'&&files.includes(url.searchParams.get('name'))){
        filename=path.join(directory,url.searchParams.get('name'));response.setHeader('Content-Type','model/gltf-binary');
      }else if(url.pathname.startsWith('/vendor/')){
        filename=path.resolve(threeRoot,decodeURIComponent(url.pathname.slice('/vendor/'.length)));
        if(!filename.startsWith(threeRoot+path.sep)) throw new Error('Invalid vendor path');
        response.setHeader('Content-Type','text/javascript');
      }else{response.writeHead(404).end();return;}
      const stream=fs.createReadStream(filename);stream.on('error',()=>response.destroy());stream.pipe(response);
    }catch(error){response.writeHead(400).end(String(error));}
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  let browser;
  try{
    browser=await chromium.launch({headless:true,channel:'msedge'});
    for(const result of results){
      const page=await browser.newPage({viewport:{width:1000,height:800},deviceScaleFactor:1});
      page.setDefaultTimeout(120000);
      const errors=[];
      page.on('pageerror',error=>errors.push(String(error)));
      page.on('console',message=>{if(message.type()==='error') errors.push(message.text());});
      const base='http://127.0.0.1:'+server.address().port;
      await page.route('**/*',route=>route.request().url().startsWith(base)?route.continue():route.abort());
      await page.goto(base);
      await page.waitForFunction(()=>window.ready);
      const info=await page.evaluate(name=>window.loadModel(name),result.name);
      const firstTime=info.duration*.1,secondTime=info.duration*.35;
      const first=await page.evaluate(time=>window.sample(time),firstTime);
      const second=await page.evaluate(time=>window.sample(time),secondTime);
      await page.evaluate(vertices=>window.framePositions(vertices),first.vertices.concat(second.vertices));
      const capture=async(time,suffix)=>{
        const encoded=await page.evaluate(time=>{window.sample(time);return document.querySelector('canvas').toDataURL('image/png').split(',')[1];},time);
        const image=Buffer.from(encoded,'base64');
        fs.writeFileSync(path.join(directory,result.name+'.frame-'+suffix+'.png'),image);
        return image;
      };
      const firstImage=await capture(firstTime,'a'),secondImage=await capture(secondTime,'b');
      let maxDisplacement=0;
      for(let i=0;i<first.vertices.length;i+=3){
        maxDisplacement=Math.max(maxDisplacement,Math.hypot(...first.vertices.slice(i,i+3).map((x,j)=>x-second.vertices[i+j])));
      }
      assert(first.drawCalls>0&&first.triangles>0,result.name+' did not render geometry');
      if(maxDisplacement<=1e-5) console.error(JSON.stringify({info,errors,firstTargets:first.activeTargets,secondTargets:second.activeTargets}));
      assert(maxDisplacement>1e-5,result.name+' did not deform between samples');
      assert(!firstImage.equals(secondImage),result.name+' screenshots did not change');
      assert.deepEqual(errors,[],result.name+' browser errors');
      Object.assign(result,info,{firstTime,secondTime,maxDisplacement,drawCalls:first.drawCalls,triangles:first.triangles,pageErrors:errors,
                                 firstActiveTargets:first.activeTargets,secondActiveTargets:second.activeTargets});
      console.log(result.name,info.clip,'deformation',maxDisplacement,'triangles',first.triangles);
      await page.close();
    }
    fs.writeFileSync(path.join(directory,'rendered-glb-results.json'),JSON.stringify(results,null,2));
  }finally{
    if(browser) await browser.close();
    await new Promise(resolve=>server.close(resolve));
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
