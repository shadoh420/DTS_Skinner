"""JA MP world-item facts (codemp/game/bg_misc.c), extending the JK2 table."""
try:
    from tools.jk2_map_items import MP_ITEMS as JK2_ITEMS
except ImportError:
    from jk2_map_items import MP_ITEMS as JK2_ITEMS

MP_ITEMS = {k:v for k,v in JK2_ITEMS.items() if k != 'item_datapad'}
MP_ITEMS.update({
    'item_medpac_big': ('holdable', 'models/items/big_bacta.md3'),
    'item_jetpack': ('holdable', 'models/items/psgun.glm'),
    'item_healthdisp': ('holdable', 'models/map_objects/mp/bacta.md3'),
    'item_ammodisp': ('holdable', 'models/map_objects/mp/bacta.md3'),
    'item_eweb_holdable': ('holdable', 'models/map_objects/hoth/eweb_model.glm'),
    'item_cloak': ('holdable', 'models/items/psgun.glm'),
    'weapon_melee': ('weapon', 'models/weapons2/stun_baton/baton_w.glm'),
    'weapon_saber': ('weapon', 'models/weapons2/saber/saber_w.md3'),
    'weapon_blaster_pistol': ('weapon', 'models/weapons2/blaster_pistol/blaster_pistol_w.glm'),
    'weapon_concussion_rifle': ('weapon', 'models/weapons2/concussion/c_rifle_w.glm'),
    'ammo_all': ('ammo', 'models/items/battery.md3'),
    'team_ctf_redflag': ('team', 'models/flags/r_flag.md3'),
    'team_ctf_blueflag': ('team', 'models/flags/b_flag.md3'),
    'team_ctf_neutralflag': ('team', 'models/flags/n_flag.md3'),
})
