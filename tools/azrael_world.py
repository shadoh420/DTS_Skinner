"""Bounded WORLD.DAT reader and authored first-arrival population simulation.

Format verified against the installed game's reader (0x4ae94). Only portal and
initial entry events are generated: puzzles, combat and conversations are not
invented. Unsupported operations stop the entire arrival transaction.
"""
from collections import deque
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
        scripts = ids(); fields = struct.unpack('<HHBHBH', take(10))
        objects.append(dict(scripts=scripts, fields=fields, name=name(take(11))))
        take(30)
    zones = []
    for _ in count():
        zones.append(dict(offset=pos, enabled=word(), one_shot=word(),
                          name=name(take(11)), scripts=ids()))
    scenes = []
    for _ in count():
        scenes.append(dict(offset=pos, name=name(take(9)), objects=ids(), zones=ids()))
    timers = []
    for _ in count():
        timers.append(dict(fields=(word(), word()), scripts=ids()))
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
                    if raw[0] in (0, 1, 2, 3, 4, 7, 9, 10, 11, 12, 14, 16):
                        condition['index'] = struct.unpack_from('<H', raw, 1)[0]
                    if raw[0] in (0, 9, 11):
                        condition['value'] = struct.unpack_from('<H', raw, 3)[0]
                    if raw[0] == 12:
                        condition['value'] = raw[3]
                    if raw[0] in (8, 17, 18):
                        condition.update(character=name(raw[1:13]), room=struct.unpack_from('<H', raw, 13)[0])
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
                        action.update(room=struct.unpack_from('<H', raw, 1)[0], arrival=struct.unpack_from('<H', raw, 3)[0])
                    elif raw[0] == 21:
                        action.update(state=raw[1], character=name(raw[2:14]))
                    elif raw[0] in (11, 18, 19, 20, 32):
                        action['character'] = name(raw[1:13])
                    elif raw[0] == 17:
                        action.update(index=struct.unpack_from('<H', raw, 2)[0], value=raw[1])
                    elif raw[0] in (0, 1, 2, 3, 4, 7, 8, 9, 10, 13, 14, 15, 16, 22, 23, 24, 25, 27, 28, 33, 35, 36, 37):
                        action.update(index=struct.unpack_from('<H', raw, 1)[0], value=struct.unpack_from('<H', raw, 3)[0])
                    block['actions'].append(action)
                blocks.append(block)
            script['groups'].append(blocks)
        scripts.append(script)
    if pos != len(data):
        raise ValueError('Trailing WORLD.DAT data')
    if any(i >= len(objects) for s in scenes for i in s['objects']) or any(i >= len(zones) for s in scenes for i in s['zones']):
        raise ValueError('Invalid WORLD.DAT scene reference')
    if any(i >= len(scripts) for z in zones + objects + timers for i in z['scripts']):
        raise ValueError('Invalid WORLD.DAT script reference')
    for script in scripts:
        if script['initial'] >= len(script['groups']):
            raise ValueError('Invalid WORLD.DAT initial script group')
        for group in script['groups']:
            for block in group:
                if block['next_group'] >= len(script['groups']):
                    raise ValueError('Invalid WORLD.DAT next script group')
                for c in block['conditions']:
                    limits = {1:len(zones), 2:len(timers), 3:len(objects), 4:flags, 9:variables, 10:flags, 11:variables, 14:len(objects)}
                    if c['op'] in limits and c['index'] >= limits[c['op']]:
                        raise ValueError('Invalid WORLD.DAT condition reference')
                    if c['op'] in (17,18) and c['room'] >= len(scenes):
                        raise ValueError('Invalid WORLD.DAT dead-character room')
                if any(a['op'] == 31 and a['room'] >= len(scenes) for a in block['actions']):
                    raise ValueError('Invalid WORLD.DAT portal room')
                for action in block['actions']:
                    limits = {0:len(timers), 1:len(timers), 2:len(objects), 3:len(zones), 4:flags,
                              8:len(objects), 9:len(objects), 14:variables, 15:variables,
                              17:len(objects), 23:len(objects), 24:len(objects), 25:len(objects),
                              27:len(objects), 28:len(objects), 33:len(objects), 36:len(objects)}
                    if action['op'] in limits and action['index'] >= limits[action['op']]:
                        raise ValueError('Invalid WORLD.DAT action reference')
                    if action['op'] == 31 and action['arrival'] >= len(objects):
                        raise ValueError('Invalid WORLD.DAT arrival object')
    return dict(objects=objects, scenes=scenes, zones=zones, timers=timers,
                scripts=scripts, flags=flags, variables=variables)


def spawn_records(world):
    """Every authored spawn, with provenance independent of its selection."""
    rows = []
    for si, script in enumerate(world['scripts']):
        for gi, group in enumerate(script['groups']):
            for bi, block in enumerate(group):
                for ai, action in enumerate(block['actions']):
                    if action['op'] == 30:
                        rows.append(dict(character=action['character'], npc=True,
                            position=action['position'], heading=action['heading'], rooms=[],
                            status='excluded', reason='not-reached-by-arrival-program',
                            source_record=dict(file='WORLD.DAT', offset=action['offset'], script=si, group=gi, block=bi, action=ai),
                            entity=f'world-{action["offset"]:x}', conditions=block['conditions']))
    return rows


class Blocked(ValueError):
    """A transaction cannot be completed without guessing game state."""


class _Choice(Exception):
    def __init__(self, field, index, options):
        self.field, self.index, self.options = field, index, options


def _response_effects(world, si, block):
    """Possible spawns/portals in a response and its automatic continuations."""
    spawns, destinations, todo, seen = set(), set(), [block], set()
    while todo:
        current = todo.pop()
        identity = id(current)
        if identity in seen:
            continue
        seen.add(identity)
        for action in current['actions']:
            if action['op'] == 30:
                spawns.add(action['offset'])
            elif action['op'] == 31:
                destinations.add(action['room'])
        for child in world['scripts'][si]['groups'][current['next_group']]:
            if not any(c['op'] in (0,1,2,3,7,8,12,16) for c in child['conditions']):
                todo.append(child)
    return spawns, destinations


def _blocked_context(world, state, si, gi, block, error):
    """Include possible affected spawns, without executing the blocked suffix."""
    spawns, destinations = _response_effects(world, si, block)
    error.context = dict(reason=str(error), execution_room=state['room'], script=si, group=gi,
                         blocked_spawns=sorted(spawns), destinations=sorted(destinations))
    return error


def _options(value):
    return value if isinstance(value, frozenset) else frozenset([value])


def _test_value(state, field, index, predicate):
    options = _options(state[field][index])
    yes = frozenset(v for v in options if predicate(v))
    no = options - yes
    if yes and no:
        raise _Choice(field, index, [yes, no])
    return bool(yes)


def fresh_state(world, room):
    return dict(room=room, flags=[0]*world['flags'], variables=[0]*world['variables'],
                groups=[s['initial'] for s in world['scripts']],
                enabled=[z['enabled'] for z in world['zones']], inventory=set(), held=None,
                dead=set(), inactive=set(), actors={}, actors_ambiguous=False, timers=set(), route=[], decisions=[], removed=[])


def condition_matches(condition, event, state):
    """R.EXE 0x3d7fc: conjunction, typed events, flags, words and dead-name/room."""
    op = condition['op']
    if op in (0, 1, 2, 3, 7, 8, 12, 16):
        if event['op'] != op:
            return False
        keys = ('character', 'room') if op == 8 else ('index', 'value') if op in (0, 12) else ('index',)
        return all(condition[k] == event.get(k) for k in keys)
    if op in (4, 10):
        return _test_value(state, 'flags', condition['index'], lambda v: bool(v) == (op == 4))
    if op in (9, 11):
        return _test_value(state, 'variables', condition['index'], lambda v: (v == condition['value']) == (op == 9))
    if op == 14:
        # 0x184200/0x184202 are refreshed by the scene contact query, not by
        # inventory selection. A portal-only interpreter cannot infer contact.
        raise Blocked(f'Contact condition op 14 object {condition["index"]} at {condition["offset"]:#x} requires scene contact')
    if op in (17, 18):
        return ((condition['character'].casefold(), condition['room']) in state['dead']) == (op == 17)
    if op in (5, 6, 13, 15):
        return True  # Explicit no-test cases; 13 is the unconditional continuation.
    raise Blocked(f'Unhandled condition op {op} at {condition["offset"]:#x}')


def _apply_action(action, state, world, source):
    """Population-relevant effects of ProcessResponses (0x2ad40).

    Geometry/audio/AI commands are classified explicitly; they do not synthesize
    later completion events. Conversations, movies and game-ending commands stop
    the transaction. Room changes really clear actors (0x1129f -> 0x24570).
    """
    op = action['op']
    if op == 3:
        state['enabled'][action['index']] = action['value']
    elif op == 4:
        state['flags'][action['index']] = action['value']
    elif op in (14, 15):
        i, value = action['index'], action['value']
        state['variables'][i] = frozenset((v + value) & 65535 for v in _options(state['variables'][i] if op == 14 else 0))
    elif op in (0, 1):
        (state['timers'].add if op == 0 else state['timers'].discard)(action['index'])
    elif op in (8, 9):
        (state['inventory'].add if op == 8 else state['inventory'].discard)(action['index'])
    elif op == 33:
        state['held'] = action['index']
    elif op == 7:
        if action['index'] == 65535:
            state['held'] = None
    elif op == 30:
        actor = dict(character=action['character'], position=action['position'], heading=action['heading'],
                     source_record=source, state=1)
        key = action['character'].casefold()
        if key in state['inactive']:
            state['removed'].append(dict(actor, reason='existing-inactive-actor'))
        else:
            if key in state['actors']:
                state['removed'].append(dict(state['actors'][key], reason='repositioned-by-name'))
            state['actors'][key] = actor
    elif op == 21:
        if state['actors_ambiguous']:
            raise Blocked(f'Ambiguous transit actors before animation op 21 at {action["offset"]:#x}')
        key = action['character'].casefold()
        if key in state['actors']:
            state['actors'][key]['state'] = action['state']
            if action['state'] in (12, 14, 15):  # Explicit terminal states, not a living REST preview.
                state['removed'].append(dict(state['actors'].pop(key), reason='terminal-animation-state'))
                state['inactive'].add(key)
                if action['state'] == 12:  # 0x2464a -> 0x20da0 records name/room.
                    state['dead'].add((key, state['room']))
    elif op == 31:
        destination = action['room']
        if destination != state['room']:
            state['removed'].extend(dict(a, reason='room-change') for a in state['actors'].values())
            state['actors'] = {}
            state['inactive'] = set()
            state['actors_ambiguous'] = False
        state['route'].append(dict(source=state['room'], destination=destination,
                                   arrival=action.get('arrival'), offset=action['offset']))
        state['room'] = destination
    elif op in (2, 10, 11, 13, 16, 17, 18, 19, 20, 22, 23, 24, 25, 27, 28, 36, 38, 40):
        # Object/scene settings, player movement/damage, AI route setup, media.
        # No flag/variable or script-group writes on these handlers. Navigation
        # is the authored portal graph, not collision/health/time simulation.
        pass
    elif op in (32, 34, 35, 37):
        raise Blocked(f'External conversation/movie/game flow op {op} at {action["offset"]:#x}')
    else:
        raise Blocked(f'Unhandled action op {op} at {action["offset"]:#x}')


def run_event(world, state, event, scripts):
    """Atomic event plus queued op-13 continuations (0x2ba40..0x2baf2).

    First matching block wins (0x3dea0); actions execute before the next queued
    continuation pass. The caller's state survives an unsupported operation.
    """
    state = {k: ([dict(a) for a in v] if k == 'route' else list(v)) if isinstance(v, list)
             else set(v) if isinstance(v, set) else {n: dict(a) for n,a in v.items()} if k == 'actors'
             else v for k,v in state.items() if k not in ('decisions', 'removed')}
    state['decisions'], state['removed'] = [], []
    pending = list(scripts)
    for _ in range(256):
        responses, following = [], []
        for si in pending:
            gi = state['groups'][si]
            if isinstance(gi, frozenset):
                if len(gi) != 1:
                    raise _Choice('groups', si, [frozenset([v]) for v in gi])
                gi = next(iter(gi))
            blocks = enumerate(world['scripts'][si]['groups'][gi])
            if '_dispatch' in world:
                table, unconditional = world['_dispatch'][si][gi]
                blocks = sorted(unconditional + table.get((event['op'], event.get('index')), []))
            for bi, block in blocks:
                matched = True
                for c in block['conditions']:
                    try:
                        result = condition_matches(c, event, state)
                    except Blocked as exc:
                        raise _blocked_context(world, state, si, gi, block, exc)
                    if c['op'] not in (0, 1, 2, 3, 7, 8, 12, 13, 16):
                        state['decisions'].append(dict(script=si, group=gi, block=bi, condition=c, matched=result))
                    if not result:
                        matched = False
                        break
                if matched:
                    responses.append((si, gi, bi, block))
                    state['groups'][si] = block['next_group']
                    following.append(si)
                    break
        if event['op'] == 1 and world['zones'][event['index']]['one_shot']:
            state['enabled'][event['index']] = 0
        for si, gi, bi, block in responses:
            for ai, action in enumerate(block['actions']):
                source = dict(file='WORLD.DAT', offset=action['offset'], script=si, group=gi, block=bi, action=ai)
                try:
                    _apply_action(action, state, world, source)
                except Blocked as exc:
                    raise _blocked_context(world, state, si, gi, block, exc)
        if not following:
            return state
        event, pending = dict(op=13), following
    raise Blocked('Continuation cycle exceeds 256 passes')


def _event_variants(world, state, event, scripts):
    pending, results, blocked = [state], [], []
    while pending:
        candidate = pending.pop()
        try:
            results.append(run_event(world, candidate, event, scripts))
        except _Choice as choice:
            for option in choice.options:
                split = dict(candidate)
                split[choice.field] = list(candidate[choice.field])
                split[choice.field][choice.index] = next(iter(option)) if len(option) == 1 else option
                pending.append(split)
            if len(pending) + len(results) > 128:
                blocked.append(dict(reason='Arrival branch limit (128)', execution_room=candidate['room']))
                break
        except Blocked as exc:
            blocked.append(getattr(exc, 'context', dict(reason=str(exc), execution_room=candidate['room'])))
    return results, blocked


def _merge_state(previous, incoming):
    """Conservative union at a transit room; never choose an arbitrary route.

    Values are finite sets. Lost correlations can exclude a valid actor, but
    cannot justify one: an actor must survive every resulting arrival variant.
    Transit actor transforms are irrelevant after the next room-changing portal.
    """
    merged, changed = dict(previous), False
    for field in ('flags', 'variables', 'groups', 'enabled'):
        values = []
        for a, b in zip(previous[field], incoming[field]):
            if a == b:
                values.append(a)
                continue
            union = _options(a) | _options(b)
            if len(union) > 32:
                raise Blocked(f'Unbounded {field} domain')
            changed |= union != _options(a)
            values.append(next(iter(union)) if len(union) == 1 else union)
        merged[field] = values
    for field in ('inventory', 'dead', 'inactive'):
        if previous[field] != incoming[field]:
            # These histories need relational tracking; do not pretend a union
            # means the player necessarily holds an item or killed an actor.
            raise Blocked(f'Ambiguous {field} history')
    if previous['held'] != incoming['held']:
        raise Blocked('Ambiguous selected item')
    ambiguous = previous['actors_ambiguous'] or incoming['actors_ambiguous'] or previous['actors'] != incoming['actors']
    changed |= ambiguous != previous['actors_ambiguous']
    merged['actors_ambiguous'] = ambiguous
    return merged, changed


def _portal_events(world):
    """Linked zone/activation events whose block can lead to a portal."""
    events = {}
    for si, script in enumerate(world['scripts']):
        automatic = lambda b: not any(c['op'] in (0, 1, 2, 3, 7, 8, 12, 16) for c in b['conditions'])
        portal_groups = {gi for gi, group in enumerate(script['groups'])
                         if any(automatic(b) and any(a['op'] == 31 for a in b['actions']) for b in group)}
        changed = True
        while changed:
            before = len(portal_groups)
            portal_groups.update(gi for gi, group in enumerate(script['groups'])
                                 if any(automatic(b) and b['next_group'] in portal_groups for b in group))
            changed = len(portal_groups) != before
        for gi in range(len(script['groups'])):
            for block in script['groups'][gi]:
                if not (any(a['op'] == 31 for a in block['actions']) or block['next_group'] in portal_groups):
                    continue
                for c in block['conditions']:
                    if c['op'] in (1, 3):
                        events.setdefault((c['op'], c['index']), set()).add(si)
    return events


def first_arrivals(world, max_states=4096):
    """Explore portal routes with carried state, separately per destination.

    Stop each search on its first arrival at that destination. This allows
    revisiting transit rooms without mistaking a destination's return population
    for its first visit. Differing routes are intersected, never chosen by their
    traversal order. Transit states join conservatively to avoid exponential
    route enumeration. A search that does not finish publishes no actors.
    """
    dispatch = []
    for script in world['scripts']:
        groups = []
        for group in script['groups']:
            table, unconditional = {}, []
            for bi, block in enumerate(group):
                event = next((c for c in block['conditions'] if c['op'] in (0,1,2,3,7,8,12,16)), None)
                if event is None:
                    unconditional.append((bi, block))
                else:
                    table.setdefault((event['op'], event.get('index')), []).append((bi, block))
            groups.append((table, unconditional))
        dispatch.append(groups)
    world = dict(world, _dispatch=dispatch)
    entry = [(ri, zi) for ri, room in enumerate(world['scenes']) for zi in room['zones']
             if world['zones'][zi]['name'].casefold() == 'zxentry' and world['zones'][zi]['enabled']]
    if len(entry) != 1:
        return dict(rooms=[], blockers=[dict(reason='No unique enabled new-game zxentry')], searches=0)
    start_room, start_zone = entry[0]
    start = fresh_state(world, start_room)
    try:
        start = run_event(world, start, dict(op=1, index=start_zone), world['zones'][start_zone]['scripts'])
    except Blocked as exc:
        return dict(rooms=[], blockers=[getattr(exc,'context',dict(reason=str(exc), room=start_room))], searches=0)
    portal_events = _portal_events(world)
    outgoing = []
    for room in world['scenes']:
        candidates = []
        for (op, index), linked in portal_events.items():
            kind = 'zones' if op == 1 else 'objects'
            if index in room[kind]:
                scripts = world[kind][index]['scripts']
                if any(s in linked for s in scripts):
                    candidates.append((dict(op=op, index=index), scripts))
        outgoing.append(candidates)
    graph = []
    for candidates in outgoing:
        destinations = set()
        for event, scripts in candidates:
            for si in scripts:
                for group in world['scripts'][si]['groups']:
                    for block in group:
                        if any(c['op'] == event['op'] and c.get('index') == event['index'] for c in block['conditions']):
                            destinations.update(_response_effects(world, si, block)[1])
        graph.append(destinations)
    def affected_destinations(blocker):
        pending = list(blocker.get('destinations', []))
        if 'execution_room' in blocker:
            pending.append(blocker['execution_room'])
        affected = set()
        while pending:
            index = pending.pop()
            if index not in affected:
                affected.add(index)
                pending.extend(graph[index] - affected)
        return affected
    rooms, all_blockers, total = [], {}, 0
    for target, room in enumerate(world['scenes']):
        queue, joined, arrivals, blockers, decisions = deque([start_room]), {start_room:start}, {}, {}, {}
        steps = 0
        exhausted = False
        while queue:
            state = joined[queue.popleft()]
            steps += 1
            if steps > max_states:
                exhausted = True
                break
            if state['room'] == target:
                signature = tuple(sorted((n, tuple(a['position']), a['heading']) for n,a in state['actors'].items()))
                arrivals.setdefault(signature, state)
                continue
            for event, scripts in outgoing[state['room']]:
                if event['op'] == 1 and not any(_options(state['enabled'][event['index']])):
                    continue
                variants, errors = _event_variants(world, state, event, scripts)
                for error in errors:
                    item = dict(error, room=state['room'], event=event, scripts=scripts)
                    blockers[str(item)] = item
                for after in variants:
                    for d in after['decisions']:
                        if not d['matched']:
                            decisions[(d['script'], d['group'], d['block'], d['condition']['offset'])] = d
                    if len(after['route']) <= len(state['route']):
                        continue
                    destination = after['room']
                    if destination == target:
                        signature = tuple(sorted((n,tuple(a['position']),a['heading']) for n,a in after['actors'].items()))
                        arrivals.setdefault(signature, after)
                    elif destination not in joined:
                        joined[destination] = after
                        queue.append(destination)
                    else:
                        try:
                            merged, changed = _merge_state(joined[destination], after)
                        except Blocked as exc:
                            exhausted = True
                            blockers[str(exc)] = dict(room=destination, reason=str(exc))
                            continue
                        if changed:
                            joined[destination] = merged
                            if destination not in queue:
                                queue.append(destination)
        total += steps
        snapshots = list(arrivals.values())
        actors = []
        unsafe = [b for b in blockers.values() if target in affected_destinations(b)]
        if snapshots and not exhausted and not unsafe:
            for name, actor in snapshots[0]['actors'].items():
                signature = (actor['position'], actor['heading'])
                if all(name in s['actors'] and (s['actors'][name]['position'], s['actors'][name]['heading']) == signature for s in snapshots):
                    actors.append(dict(actor, route=snapshots[0]['route']))
        rooms.append(dict(index=target, name=room['name'].strip(), actors=actors, states=steps,
                          variants=len(arrivals), status='search-limit' if exhausted else 'blocked' if unsafe else 'reachable' if snapshots else 'unreachable',
                          blockers=unsafe,
                          alternatives=[dict(route=s['route'], actors=list(s['actors'].values()), removed=s['removed']) for s in snapshots],
                          decisions=list(decisions.values())))
        all_blockers.update(blockers)
    return dict(rooms=rooms, blockers=list(all_blockers.values()), searches=total)


def entry_spawns(world, *, simulation=None):
    """One audit row per spawn action, plus room-qualified selected instances."""
    simulation = first_arrivals(world) if simulation is None else simulation
    rows = spawn_records(world)
    by_offset = {r['source_record']['offset']: r for r in rows}
    for row in rows:
        if any(c['op'] in (0,1,2,3,7,8,12,16) for c in row['conditions']):
            row['reason'] = 'later-or-non-entry-event'
    for room in simulation['rooms']:
        for alternative in room['alternatives']:
            for removed in alternative['removed']:
                offset = removed['source_record']['offset']
                if removed['reason'] != 'room-change':
                    item = dict(room=room['name'], reason=removed['reason'])
                    removals = by_offset[offset].setdefault('arrival_removals', [])
                    if item not in removals:
                        removals.append(item)
        candidate_offsets = {a['source_record']['offset'] for alt in room['alternatives'] for a in alt['actors']}
        for offset in candidate_offsets:
            row = by_offset[offset]
            if row['status'] != 'selected':
                row['reason'] = 'search-limit' if room['status'] == 'search-limit' else 'ambiguous-first-arrival'
        for actor in room['actors']:
            row = by_offset[actor['source_record']['offset']]
            row.update(status='selected', reason='Agreed first-arrival portal routes')
            row['rooms'].append(room['name'])
            row.setdefault('routes', {})[room['name']] = actor['route']
        for decision in room['decisions']:
            if decision['condition']['op'] in (4, 9, 10, 11, 14, 17, 18):
                for row in rows:
                    src = row['source_record']
                    if all(src[k] == decision[k] for k in ('script', 'group', 'block')) and row['reason'] not in ('Agreed first-arrival portal routes', 'ambiguous-first-arrival', 'search-limit'):
                        row['reason'] = 'state-gated'
                        row.setdefault('deciding_conditions', [])
                        if decision['condition'] not in row['deciding_conditions']:
                            row['deciding_conditions'].append(decision['condition'])
    for blocker in simulation['blockers']:
        for offset in blocker.get('blocked_spawns', []):
            if offset in by_offset and by_offset[offset]['status'] != 'selected':
                by_offset[offset].update(reason='blocked-operation', blocker=blocker)
    return rows


def room_spawns(rows, source):
    room = PurePosixPath(source).stem.casefold()
    return [r for r in rows if r['status'] == 'selected' and any(n.casefold() == room for n in r['rooms'])]
