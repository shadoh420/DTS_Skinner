"""Bloodlines' particle definitions (tools/vtmb_particles.py): KeyValues reading, tracks, spawns and map emitters."""
import unittest

from tools import vtmb_particles as vp

FILES = {
    'particles/fire.txt': 'Particle { loop "1" fps "30"\n spawn { particle "Flame" rate "40" radius "0~5" theta "0~360" z "10" }\n'
                          ' // spawn\n { particle "Smoke" rate "10" }\n spawn { particle "Glow" burst "1" }\n'
                          ' spawn { particle "Pulse" burst "0,3(15),0" } }',
    'particles/flame.txt': 'Particle { frames "30" max_frames "35" sprite "FlameMass" size "20,10,4" color "0,20~60,10~50,0"'
                           ' elevation_speed "10~25" mask "0" }',
    'particles/smoke.txt': 'Particle { frames "60" sprite "Cloud" }',
    'particles/glow.txt': 'Particle { frames "60" sprite "Flare" }',
    'particles/pulse.txt': 'Particle { frames "15" sprite "Flare" }',
    'particles/timer.txt': 'Particle { loop "0" min_frames "20" frames "50" max_frames "80"\n'
                           ' spawn { particle "Drops" burst "1" x "4" } }',
    'particles/drops.txt': 'Particle { loop "1" frames "20"\n spawn { particle "Drip" rate "1~4" } }',
    'particles/drip.txt': 'Particle { sprite "DropletFast" frames "20" movealign "1" elevation_speed "-275~-250" collide { } }',
    'particles/stars.txt': 'Particle { loop "0"\n spawn { particle "Star" burst "500" radius "1000" phi "100~180" } }',
    'particles/star.txt': 'Particle { loop "1" sprite "Ember" color "255~100,255~100" mask "0" }',
    'particles/rain.txt': 'Particle { loop "1" precipitation "1"\n spawn { particle "Raindrop" rate "1000" }\n'
                          ' spawn { particle "Haze" rate "70" radius "800" theta "0~360" } }',
    'particles/raindrop.txt': 'Particle { sprite "DropletFast" frames "15" movealign "1" z_speed "-400~-600" size "3"'
                              ' height "10" mask "0" precipitation "1" collide { } }',
    'particles/haze.txt': 'Particle { sprite "Furball" frames "300" size "500" precipitation "1" }',
}
read = lambda name: FILES[name].encode() if name in FILES else None


class TracksTest(unittest.TestCase):
    def test_keys_spread_between_timed_ones(self):
        self.assertEqual(vp.track('0,-10(15),-50~-100(20)', 60),
                         [(0., 0., 0., 0., False), (0., .25, -10., -10., False), (.25, 1 / 3, -50., -100., False)])
        self.assertEqual(vp.track('0,0,60,0', 70)[2], (1 / 3, 2 / 3, 60., 60., False))
        self.assertEqual(vp.track('255', 30), [(0., 0., 255., 255., True)])

    def test_steps_bursts_and_errors(self):
        self.assertEqual(vp.track('1!,-1!,1', 30), [(0., 0., 1., 1., True), (.5, .5, -1., -1., True), (.5, 1., 1., 1., False)])
        self.assertEqual(vp.track('10(15)', 30, burst=True), [(.5, .5, 10., 10., True)])
        self.assertAlmostEqual(vp.track('0,5(-10)', 30)[1][1], 2 / 3)  # a negative frame counts back from the end
        for bad in ('1(5),2', 'a', '1~', '1(5', '1 2'):
            self.assertIsNone(vp.track(bad, 30), bad)

    def test_commented_spawn_becomes_plain_keys(self):
        kv = vp.keyvalues(FILES['particles/fire.txt'])
        self.assertEqual([vp.find(v, 'particle') for k, v in kv if isinstance(v, list)], ['Flame', 'Glow', 'Pulse'])
        self.assertIn(('{', 'particle'), kv)


class DefinitionsTest(unittest.TestCase):
    def test_load_rules(self):
        fire = vp.load('particles/Fire.txt', read)
        self.assertEqual([s['child']['name'] for s in fire['spawns']], ['flame', 'glow', 'pulse'])
        flame = fire['spawns'][0]['child']
        self.assertEqual(flame['life'], [1., 35 / 30])
        self.assertEqual(flame['sprite'], 'flamemass')
        self.assertEqual(flame['tracks']['red'], [(0., 0., 255., 255., True)])  # defaults
        self.assertEqual(fire['spawns'][0]['tracks']['radius'], [(0., 0., 0., 5., True)])
        self.assertIsNone(vp.load('missing', read))


class EmittersTest(unittest.TestCase):
    def run_emitters(self, *entities, **options):
        return vp.emitters(entities, read, lambda e: ([0, 0, 0], [100, 50, 20]), lambda origin: origin[2] - 64, lambda sprite: [.5, .5],
                           **options)

    def test_fire_rate_bursts_and_ramp(self):
        found, counts = self.run_emitters(dict(classname='env_particle', particle_definition='Fire', origin='10 20 30',
                                               ramp_scale='0.5', active='1', attach_type='1'))
        sprites = [f[0] for f in found]
        self.assertEqual(sprites, ['particles/flamemass', 'particles/flare'])  # the single burst of a short glow plays once
        (_, flame, emitter), (_, pulse, every) = found
        self.assertEqual(emitter['period'], 1 / 20)  # rate 40 x ramp_scale 0.5
        self.assertEqual(flame['tracks'][16], [[0., 0., 20., 20.]])
        self.assertEqual((pulse['count'], pulse['delay']), (2, .5 * pulse['parentLife']))  # burst 3 x 0.5 at frame 15 of 30
        self.assertEqual(every['period'], pulse['parentLife'])
        self.assertEqual(counts['one burst of a short-lived particle at the start (plays once)'], 1)

    def test_timer_collapses_and_drips_stop_at_the_floor(self):
        (sprite, drip, emitter), = self.run_emitters(dict(classname='env_particle', particle_definition='Timer', origin='0 0 100'))[0]
        self.assertEqual(sprite, 'particles/dropletfast')
        self.assertEqual(emitter['origin'], [4., 0., 100.])
        self.assertEqual(emitter['floor'], 36.)
        self.assertEqual(drip['rateMax'], 4.)
        self.assertTrue(drip['collide'] and drip['movealign'])

    def test_stars_stay_from_the_start(self):
        (_, star, emitter), = self.run_emitters(dict(classname='env_particle', particle_definition='Stars', attach_type='-1'))[0]
        self.assertEqual((star['count'], star['loop'], emitter['period'], emitter['offset']), (500, True, 1e6, -1000.))
        self.assertEqual(star['spawn']['phi'], [100., 180.])

    def test_fill_cap_thins_and_brightens(self):
        FILES['particles/blaze.txt'] = 'Particle { loop "1"\n spawn { particle "Glare" rate "100" } }'
        FILES['particles/glare.txt'] = 'Particle { frames "30" sprite "Flare2" size "200" }'  # 100 alive x 200^2
        try:
            (_, glare, emitter), = self.run_emitters(dict(classname='env_particle', particle_definition='Blaze'))[0]
        finally:
            del FILES['particles/blaze.txt'], FILES['particles/glare.txt']
        keep = vp.FILL / (100 * 200 * 200)
        self.assertAlmostEqual(glare['thin'], keep)
        self.assertAlmostEqual(glare['gain'], 1 / keep)
        self.assertAlmostEqual(emitter['period'], 1 / (100 * keep))
        fire = self.run_emitters(dict(classname='env_particle', particle_definition='Fire'))[0]
        self.assertNotIn('gain', fire[0][1])  # an ordinary fire is drawn whole

    def test_func_particle_box_and_skips(self):
        found, counts = self.run_emitters(
            dict(classname='func_particle', particle_definition='particles/Fire.txt', model='*1'),
            dict(classname='env_particle', particle_definition='Fire', attach_type='5'),
            dict(classname='env_particle', particle_definition='Fire', active='0'),
            dict(classname='env_particle', particle_definition='Fire', parentname='npc'),
            dict(classname='env_particle', particle_definition='Fire', ramp_scale='0'))
        self.assertEqual(found[0][2]['origin'], [50., 25., 10.])
        self.assertEqual(found[0][1]['box'], [[-50., -25., -10.], [50., 25., 10.]])
        self.assertEqual(counts['env_particle: attach_type 5 (attached)'], 1)
        self.assertEqual(counts['env_particle: starts inactive (waits for a trigger)'], 1)
        self.assertEqual(counts['env_particle: follows a parent entity'], 1)
        self.assertEqual(counts['env_particle: ramp_scale 0 (nothing spawns until an input)'], 1)

    def test_rain_around_the_player(self):
        rain = dict(classname='env_particle', particle_definition='Rain', attach_type='11', targetname='Rain_Emitter',
                    spawnbounds='256', origin='5 5 5')
        (sprite, drop, emitter), = (found := self.run_emitters(rain))[0]
        self.assertEqual(sprite, 'particles/dropletfast')
        self.assertTrue(drop['follow'] and drop['outdoors'])
        self.assertEqual(drop['box'], [[-256., -256., 0.], [256., 256., 256.]])  # attach 11: from the feet up
        self.assertNotIn('floor', emitter)  # its floor is the page's feet
        self.assertEqual((emitter['period'], drop['count']), (.01, 10))  # 1000 a second, in tens (particles.js's 0.01 s floor)
        self.assertEqual(found[1]['haze around the player that the fill cap would thin below 1% (deferred)'], 1)
        cube = self.run_emitters(dict(rain, attach_type='10', spawnbounds=''))[0][0][1]['box']
        self.assertEqual(cube, [[-512., -512., -512.], [512., 512., 512.]])  # attach 10, the default spawnbounds
        # sm_hub_1: ramp_scale 0 until an enabled logic_timer's SetRateScale; a map without rain leaves culls it all.
        off = dict(rain, ramp_scale='0')
        self.assertEqual(self.run_emitters(off)[1]['env_particle: ramp_scale 0 (nothing spawns until an input)'], 1)
        self.assertEqual(len(self.run_emitters(off, rate_scales={'rain_emitter': 1.})[0]), 1)
        culled = self.run_emitters(rain, rain_leaves=False)
        self.assertEqual(culled[0], [])
        self.assertEqual(culled[1]['env_particle: rain around the player in a map without rain leaves (the engine culls it)'], 1)

    def test_rate_scales_from_enabled_timers(self):
        from tools.import_vtmb_map import rate_scales
        lump = (b'{\n"classname" "logic_timer"\n"StartDisabled" "0"\n"OnTimer" "rain_emitter,SetRateScale,1,0,-1,,"\n'
                b'"OnTimer" "world,FadeGlobalWetness,1,10,-1,,"\n}\n'
                b'{\n"classname" "logic_timer"\n"StartDisabled" "1"\n"OnTimer" "other,SetRateScale,1,0,-1,,"\n}\n'
                b'{\n"classname" "logic_relay"\n"OnTrigger" "third,SetRateScale,1,0,-1,,"\n}\n')
        self.assertEqual(rate_scales(lump), {'rain_emitter': 1.})


if __name__ == '__main__':
    unittest.main()
