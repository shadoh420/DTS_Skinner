"""Bounded compiled WORLD.DAT reader and conservative entry-spawn selection.

Format verified against the installed game's reader (0x4ae94). This is not a
script interpreter: portal continuations and gameplay history remain unresolved.
"""
import math
from pathlib import PurePosixPath
import struct


def read_world(data):
    if not 30 <= len(data) <= 16 * 1024 * 1024:
        raise ValueError('Invalid WORLD.DAT size')
    pos, budget = 30, 100000

    def take(size):
        nonlocal pos
        if pos + size > len(data):
            raise ValueError('Truncated WORLD.DAT')
        value = data[pos:pos + size]; pos += size
        return value

    def word():
        return struct.unpack('<H', take(2))[0]

    def count():
        nonlocal budget
        n = word(); budget -= n
        if budget < 0:
            raise ValueError('WORLD.DAT record limit exceeded')
        return range(n)

    def ids():
        return [word() for _ in count()]

    def name(raw):
        if b'\0' not in raw:
            raise ValueError('Unterminated WORLD.DAT name')
        return raw.split(b'\0')[0].decode('ascii')

    objects = []
    for _ in count():
        ids(); take(10); objects.append(name(take(11))); take(30)
    zones = []
    for _ in count():
        zones.append(dict(offset=pos, enabled=word(), one_shot=word(),
                          name=name(take(11)), scripts=ids()))
    scenes = []
    for _ in count():
        scenes.append(dict(offset=pos, name=name(take(9)), objects=ids(), zones=ids()))
    for _ in count():
        take(4); ids()  # Timed records, not character placements.
    flags, variables = word(), word()
    scripts = []
    for _ in count():
        script = dict(offset=pos, initial=word(), groups=[])
        for _ in count():
            blocks = []
            for _ in count():
                block = dict(offset=pos, next_group=word(), conditions=[], actions=[])
                for _ in count():
                    offset = pos; raw = take(37)
                    condition = dict(offset=offset, op=raw[0])
                    if raw[0] in (1, 4, 10):
                        condition['index'] = struct.unpack_from('<H', raw, 1)[0]
                    block['conditions'].append(condition)
                for _ in count():
                    offset = pos; raw = take(27)
                    action = dict(offset=offset, op=raw[0])
                    if raw[0] == 30:
                        character = name(raw[1:13])
                        if not character or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in character):
                            raise ValueError('Invalid WORLD.DAT character name')
                        values = struct.unpack_from('<fffh', raw, 13)
                        if not all(math.isfinite(v) for v in values):
                            raise ValueError('Non-finite WORLD.DAT spawn')
                        action.update(character=character, position=list(values[:3]), heading=values[3])
                    elif raw[0] == 31:
                        action['room'] = struct.unpack_from('<H', raw, 1)[0]
                    block['actions'].append(action)
                blocks.append(block)
            script['groups'].append(blocks)
        scripts.append(script)
    if pos != len(data):
        raise ValueError('Trailing WORLD.DAT data')
    if any(i >= len(objects) for s in scenes for i in s['objects']) or any(i >= len(zones) for s in scenes for i in s['zones']):
        raise ValueError('Invalid WORLD.DAT scene reference')
    if any(i >= len(scripts) for z in zones for i in z['scripts']):
        raise ValueError('Invalid WORLD.DAT script reference')
    for script in scripts:
        if script['initial'] >= len(script['groups']):
            raise ValueError('Invalid WORLD.DAT initial script group')
        for group in script['groups']:
            for block in group:
                if block['next_group'] >= len(script['groups']):
                    raise ValueError('Invalid WORLD.DAT next script group')
                for c in block['conditions']:
                    if c['op'] in (1, 4, 10) and c['index'] >= (len(zones) if c['op'] == 1 else flags):
                        raise ValueError('Invalid WORLD.DAT condition reference')
                if any(a['op'] == 31 and a['room'] >= len(scenes) for a in block['actions']):
                    raise ValueError('Invalid WORLD.DAT portal room')
    return dict(scenes=scenes, zones=zones, scripts=scripts, flags=flags, variables=variables)


def entry_spawns(world):
    """Select proven initial zxentry blocks; audit every other spawn separately.

    WORLD opcode 30 supplies name/XYZ/degrees (handler 0x2b5ec). Event 1 is a
    zone event; condition 10 requires a clear flag. Fresh flags are zero. Only
    the first block in the initial group is admitted: no preceding branch or
    portal transition is guessed. This currently proves CORICORI, not all rooms.
    """
    rows = []
    for si, script in enumerate(world['scripts']):
        for gi, group in enumerate(script['groups']):
            for bi, block in enumerate(group):
                events = [c['index'] for c in block['conditions'] if c['op'] == 1]
                linked = [s['name'] for s in world['scenes'] if any(
                    z in s['zones'] and si in world['zones'][z]['scripts'] for z in events)]
                reason = ('unresolved-script-continuation' if gi != script['initial'] else
                          'later-or-non-entry-event' if len(events) != 1 or world['zones'][events[0]]['name'].casefold() != 'zxentry' else
                          'disabled-entry-zone' if not world['zones'][events[0]]['enabled'] else
                          'unresolved-branch-order' if bi != 0 else
                          'puzzle-or-unsupported-condition' if any(c['op'] not in (1, 10) for c in block['conditions']) else
                          'ambiguous-room-link' if len(linked) != 1 else '')
                # A room-changing or other unhandled action before a spawn could
                # change its coordinate system. Do not partially execute a script.
                prior = []
                for ai, action in enumerate(block['actions']):
                    if action['op'] == 30:
                        why = reason or ('unresolved-pre-spawn-action' if any(op not in (30, 21) for op in prior) else '')
                        rows.append(dict(character=action['character'], npc=True,
                            position=action['position'], heading=action['heading'], rooms=linked,
                            status='excluded' if why else 'selected', reason=why or 'Initial zxentry event; fresh clear flags',
                            source_record=dict(file='WORLD.DAT', offset=action['offset'], script=si, group=gi, block=bi, action=ai),
                            entity=f'world-{action["offset"]:x}', conditions=block['conditions']))
                    prior.append(action['op'])
    # Create-by-name is idempotent in the game; a repeated spawn repositions the
    # existing actor rather than drawing an additional copy.
    seen = {}
    for row in rows:
        if row['status'] == 'selected':
            key = row['rooms'][0].casefold(), row['character'].casefold()
            if key in seen:
                seen[key].update(status='excluded', reason='superseded-initial-spawn')
            seen[key] = row
    return rows


def room_spawns(rows, source):
    room = PurePosixPath(source).stem.casefold()
    return [r for r in rows if r['status'] == 'selected' and any(n.casefold() == room for n in r['rooms'])]
