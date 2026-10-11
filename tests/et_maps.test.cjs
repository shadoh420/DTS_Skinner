'use strict';
const assert = require('node:assert/strict');
const et = require('../static/et-maps/viewer.js');
const {viewer} = require('./rtcw_maps.test.cjs');
assert.equal(et.sceneFile(''), 'scene.json');
assert.equal(et.sceneFile('?map=radar&built=1'), 'scene-built.json');
assert.equal(et.sceneFile('?built=0'), 'scene.json');
const shader = {implicitType:'implicitmask', map:'leaf.png'};
let stages = et.stagesOf(shader, true, false);
assert.equal(stages[0].alphaFunc, 'ge128');
assert.equal(stages[1].depthFunc, 'equal');
assert.equal(stages[1].map, '$lightmap');
assert.deepEqual(et.stagesOf({...shader, implicitType:'implicitblend'}, false, false)[0].blend, ['gl_src_alpha','gl_one_minus_src_alpha']);
assert.equal(et.stagesOf(shader, false, true)[0].rgbGen[0], 'lightingdiffuse');
assert.equal(et.stagesOf({}, true, false), null);
assert.equal(et.stagesOf({...shader, implicitType:'implicitmap'}, true, false)[1].depthWrite, true);
viewer('et', {
  scene(scene) {
    scene.lightmapAtlas = {file:'atlas.png', size:256, columns:2, rows:1, valid:[0], count:2};
    scene.shaders[0].distanceCull = [100,200,.5];
    scene.shaders[0].stages[0].tcGenVectors = [1,0,0,0,1,0];
    scene.shaders[0].stages[0].tcGen = 'vector';
    scene.bspNotes = [];
  },
  check({mesh}) {
    assert.equal(mesh.geometry.attributes.lm.array[0], 1/1024);
    assert.equal(mesh.geometry.attributes.lm.array[1], 1/512);
    assert.equal(mesh.geometry.attributes.foliageOrigin.count, 25);
    assert.ok(mesh.material.vertexShader.includes('dot(P, vec3(1.0, 0.0, 0.0))'));
    assert.ok(mesh.material.vertexShader.includes('vertexTint.a = clamp(alpha'));
    assert.ok(mesh.material.fragmentShader.includes('if (vFoliage < .5) discard;'));
    assert.equal(mesh.material.uniforms.uFovScale.value, 1);
  }
}).then(()=>console.log('ET built scene, implicit stages, external lightmap UVs, terrain vectors and foliage passed')).catch(error=>{console.error(error);process.exitCode=1;});
