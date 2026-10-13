/* Classification for existing exports, which retain entity names/models but no NPC flag.
   Include actor turrets/sentries, tank-like monsters, cinematic characters (including
   cinematic player doubles), spawner previews and hidden/triggered actors already exported.
   Exclude vehicles, scenery turrets, corpses/ragdolls, gibs, pickups and character-shaped
   props. This changes visibility only: no new spawns, poses, animation or game state.
   Map alias tokens below were checked against local model metadata (spawnargs/mesh);
   do not match arbitrary "marine"/"strogg" substrings in scenery model paths. */
(function(root) {
  'use strict';
  const actor = /(?:^|_)(?:monster|char|character|npc)(?:_|$)/;
  const excluded = /(?:^|_)(?:vehicle|ragdoll|corpse|dead|gib)(?:_|$)/;
  const scenery = /^(?:func_|env_|moveable_|item_|weapon|ammo_|powerup_|pda_|light|trigger_|target_|info_|misc_)/;
  const doomMap = /^(?:admin|alphalabs[123]|caverns|comm1|commoutside|cpu1|cpuboss|delta[1-5abx]*|enpro|erebus[1-6]|hell1|hellhole|maledict|marscity2?|monorail|phobos2|recycling[12]|site3|underground)_/;
  const doomActor = /(?:^|_)(?:imp\d*|maggot\d*|pinky|campbell\d*|swann|marine\d*|scientist\d*|labcoat|skeleton|player\d*|plyr|vagary\d*|hellknight\d*|sentry|sabaoth|fatty|commando|zombie|hazmat|hazguy|betruger|hk[12]|female|lostsoul|wraith|soldier\d*|mcneil|doctor|sarge2?|security|victim|civilian\d*|maint[12]?|receptionist\d*|sec|suit|zct|rev|manc|sci)(?:_|$)/;
  const doomAliases = new Set([
    'alphalabs1_helldoll', 'delta1_scipull', 'delta2b_keycardzombie', 'delta2b_tankimp', 'delta2b_tankrev',
    'erebus1_intro_detonate', 'erebus1_intro_flash', 'erebus1_intro_pda', 'hellhole_walkmarine',
    'erebus2_cinematic_hunter1', 'erebus2_spooked', 'erebus5_cloud_cinematic', 'erebus6_bottech',
    'hellhole_cin_cyberdemon', 'hellhole_cin_cyberdemon2', 'underground_invasion_labzombie',
    'maledict_death_cinematic', 'maledict_death_cinematic_split', 'maledict_intro_cinematic',
    'monorail_raisecommando_changer', 'monorail_rider', 'phobos2_cinematic_hunterdeath',
    'underground_maint_old_charles', 'underground_zsecshotgun'
  ]);
  const quakeAliases = new Set([
    'dispersal_stroggified_kane', 'dispersal_tactical_transfer', 'dispersal_tactical_transfer_rail',
    'hub2_kane_marine', 'mcc_makron', 'medlabs_nanocyte_kidnap_creature', 'medlabs_voss_marine',
    'storage_guardian', 'storage_stroggified_kane', 'storage_tech_marine'
  ]);
  function isNPC(entity, game) {
    if (typeof entity.npc === 'boolean') return entity.npc;
    if (game !== 'doom3' && game !== 'quake4') return false;
    const cls = (entity.classname || '').toLowerCase(), model = (entity.model || '').toLowerCase();
    if (scenery.test(cls) || excluded.test(cls) || excluded.test(model)) return false;
    const namedActor = name => actor.test(name) || (game === 'doom3'
      ? doomAliases.has(name) || doomMap.test(name) && doomActor.test(name)
      : quakeAliases.has(name));
    return namedActor(cls) || !model.includes('/') && namedActor(model.replace(/^model_/, ''));
  }
  // Count rendered owners, not surfaces/material passes; unknown/world owners remain visible.
  function owners(scene) {
    const rendered = new Set((scene.surfaces || []).filter(s => s.count > 0).map(s => s.entity));
    return new Set((scene.entities || []).filter(e => e.name && rendered.has(e.name) && isNPC(e, scene.game)).map(e => e.name));
  }
  const api = {isNPC, owners};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.idTech4NPCs = api;
})(typeof window === 'undefined' ? globalThis : window);
