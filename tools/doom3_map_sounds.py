"""Export only samples referenced by sound-reactive materials; no audio playback."""
import hashlib
from pathlib import Path
try:
    from tools.doom3_assets import compact, declarations, lex, path_name
except ImportError:
    from doom3_assets import compact, declarations, lex, path_name


class Sounds:
    def __init__(self,files,directory,policy=None):
        self.policy=policy
        self.files,self.directory,self.cache=files,Path(directory),{}
        self.shaders={}
        for name,asset in sorted(files.items(),key=lambda item:(item[1].campaign=='d3xp',item[0])):
            if name.endswith('.sndshd'):
                for head,body in declarations(lex(asset.read().decode('latin1'))):
                    if head:self.shaders[path_name(head[-1])]=compact(body)

    def resolve(self,entity):
        name=path_name(entity.get('s_shader',''))
        if not name:return None  # Material.cpp: no emitter gives sound == 0.
        if name not in self.cache:
            tokens=self.shaders.get(name,[]);lower=[t.lower() for t in tokens]
            # ponytail: one deterministic sample; random selections/leadin changes need a sound event timeline.
            samples=[t for t in tokens if t.lower().endswith(('.wav','.ogg')) or self.policy and (path_name(t)+'.ogg' in self.files or path_name(t)+'.wav' in self.files)]
            sample=path_name(samples[0]) if samples else name
            if self.policy and sample not in self.files and sample+'.wav' in self.files:sample+='.wav'
            if sample not in self.files:sample=str(Path(sample).with_suffix('.ogg')).replace('\\','/')
            result=dict(shader=name,looping='looping' in lower,noFlicker='no_flicker' in lower or self.policy is not None and 'noflicker' in lower)
            if sample in self.files:
                payload=self.files[sample].read();filename=hashlib.sha256(payload).hexdigest()+Path(sample).suffix
                self.directory.mkdir(parents=True,exist_ok=True);target=self.directory/filename
                if not target.exists():target.write_bytes(payload)
                result.update(file='sounds/'+filename,source=sample)
            else:result['approximation']='Missing sound sample: '+sample
            if len(samples)>1 or 'leadin' in lower:result['approximation']='First sound sample only; random selection/leadin not simulated'
            self.cache[name]=result
        return dict(self.cache[name],waitForTrigger=entity.get('s_waitfortrigger')=='1')
