"""Shared data-only studio controls; unspecified angles keep actual rig poses."""

LIGHTING_DEFAULTS = {'key_power': 1, 'fill_power': 1, 'rim_power': 1, 'environment': 1}
LIGHT_DIRECTION_LIMITS = {
    role + '_' + axis: bounds
    for role in ('key', 'fill', 'rim')
    for axis, bounds in (('azimuth', (-180, 180)), ('elevation', (0, 85)))
}

# Angles deliberately have no default. The installed worker owns its existing
# rig until a user/validated instruction explicitly sets a direction.
LIGHTING_LIMITS = {
    **{name: (0 if name == 'environment' else .1, 3) for name in LIGHTING_DEFAULTS},
    **LIGHT_DIRECTION_LIMITS,
}
