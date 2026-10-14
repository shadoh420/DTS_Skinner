import {Suspense, useCallback, useEffect, useMemo, useRef, useState} from 'react';
import {ErrorBoundary} from 'react-error-boundary';
import * as SkeletonUtils from 'three/examples/jsm/utils/SkeletonUtils.js';
import {useStaticShape} from '../src/components/GenericShape';
import {useAnisotropy} from '../src/components/useAnisotropy';
import {DTSAnimationMixer} from '../src/dts/dtsAnimationMixer';
import {processShapeScene, disposeClonedScene} from '../src/stream/playbackUtils';
import {misPositionToThree, misRotationToThreeQuat} from '../src/torqueScript/vecMath';
import {SHAPE_MODEL_ROTATION_Y} from '../src/world/placement';
import {missionPack} from './manifest';
import {addNote} from './notes';

type NPC = {npc: true; entity: string; classname: string; datablock: string; name: string;
  shape: string; skin: string; pose: string; position: number[]; rotation: number[]; scale: number[]};
const toggle = () => (window as any).skinnerNPCToggle;

function Actor({npc, loaded}: {npc: NPC; loaded: (id: string, present: boolean) => void}) {
  const gltf = useStaticShape(npc.shape);
  const anisotropy = useAnisotropy();
  const scene = useMemo(() => {
    const copy = SkeletonUtils.clone(gltf.scene);
    processShapeScene(copy, npc.shape, {anisotropy, emap: true, skinName: npc.skin});
    // An external constructor sequence overrides an embedded clip of the same name.
    const clip = gltf.animations.findLast(clip => clip.name.toLowerCase() === npc.pose);
    if (clip) {
      const mixer = new DTSAnimationMixer(copy);
      mixer.clipAction(clip).play();
      mixer.update(0); // Hold the first standing frame; never simulate AI or advance time.
    } else addNote(`NPC ${npc.name || npc.entity}: standing sequence unavailable; showing model default pose.`);
    return copy;
  }, [gltf, npc, anisotropy]);
  useEffect(() => {
    loaded(npc.entity, true);
    return () => {loaded(npc.entity, false); disposeClonedScene(scene);};
  }, [scene, npc.entity, loaded]);
  return <group position={misPositionToThree(npc.position.join(' '))}
    quaternion={misRotationToThreeQuat(npc.rotation.join(' '))}
    scale={[npc.scale[1], npc.scale[2], npc.scale[0]]} userData={npc}>
    <primitive object={scene} rotation={[0, SHAPE_MODEL_ROTATION_Y, 0]}/>
  </group>;
}

export function MapNPCs() {
  const [enabled, setEnabled] = useState(() => toggle()?.enabled ?? true);
  const ids = useRef(new Set<string>());
  const loaded = useCallback((id: string, present: boolean) => {
    if (present) ids.current.add(id); else ids.current.delete(id);
    toggle()?.setCount(ids.current.size);
  }, []);
  useEffect(() => {
    const control = toggle();
    if (control) {control.onChange = setEnabled; control.setCount(ids.current.size);}
    missionPack.notes.forEach(addNote);
    return () => {if (control) {control.onChange = () => {}; control.setCount(0);}};
  }, []);
  // Parent visibility stays authoritative even when a DTS child's LOD rewrites visibility.
  return <group visible={enabled}>{missionPack.npcs.filter((npc: NPC) => npc.npc === true).map((npc: NPC) =>
    <ErrorBoundary key={npc.entity} fallback={null} onError={error => addNote(`NPC ${npc.name || npc.entity}: ${error.message}`)}>
      <Suspense fallback={null}><Actor npc={npc} loaded={loaded}/></Suspense>
    </ErrorBoundary>)}</group>;
}
