import {createContext, useContext, useEffect, useState, type ReactNode} from 'react';

const storageKey = 'skinner.t2maps.mouse';
const defaults = {invertX:false, invertY:false};
const MouseLookContext = createContext<{
  settings: typeof defaults;
  setSettings: (settings: typeof defaults) => void;
} | null>(null);

// InputConsumer subtracts these deltas from Three Euler angles: positive looks right/down.
export const mouseDelta = (delta:number, sensitivity:number, inverted:boolean) =>
  delta * sensitivity * (inverted ? -1 : 1);

export function MouseLookProvider({children}:{children:ReactNode}) {
  const [settings, setSettings] = useState(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(storageKey) || '{}');
      return {invertX:saved.invertX === true, invertY:saved.invertY === true};
    } catch {return defaults;}
  });
  useEffect(() => {
    try {localStorage.setItem(storageKey, JSON.stringify(settings));} catch {/* Storage may be unavailable. */}
  }, [settings]);
  return <MouseLookContext.Provider value={{settings,setSettings}}>{children}</MouseLookContext.Provider>;
}

export const useMouseLook = () => useContext(MouseLookContext)!;
