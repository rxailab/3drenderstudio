"""How a product is installed where it is used: 'wall' (hung on a wall by its back) or None (it stands, lies or is held).

A wall cabinet, a wall-mounted tool board or a key box is never photographed standing on a floor, a bench or a van floor:
its scenes hang it on a wall with its doors toward the camera (scene_imagegen.WALL_RULE), the buyer places that suit it
are chosen (scene_audience.fits), and the vision check fails a picture that shows it any other way.

Where it comes from, first found wins:
  1. the user's own word: metadata['mounting'] ('wall', or 'free' for "not wall-mounted");
  2. the product's name, category and the user's scene hint ("Wall-Mounted Tool Cabinet", "壁挂工具柜");
  3. what the orientation step's vision model called it ("wall-mounted storage cabinet");
  4. its part names: keyhole mounting slots or a wall bracket.
"""
import re

KINDS = ('wall',)
# "wall" as its own word (not drywall, firewall), followed by how it is fixed or what it is.
WORDS = re.compile(r'(?i)(?<![a-z])wall[\s_-]*(?:mount(?:ed|ing|able)?|hung|hanging|cabinet|cupboard|box|rack|unit|shelf|shelves|storage|'
                   r'organi[sz]er|hooks?|bracket)(?![a-z])|(?<![a-z])pegboard(?![a-z])'
                   r'|(?<![a-z])mount(?:ed|s)?\s+(?:on|to)\s+(?:the\s+|a\s+)?wall(?![a-z])|(?<![a-z])hangs?\s+on\s+(?:the|a)\s+wall(?![a-z])'
                   r'|壁挂|挂墙|墙挂|挂壁|墙面安装|墙上安装|吊柜|壁掛|掛牆|牆掛|掛壁|牆面安裝|牆上安裝')
# what says it is NOT fixed to a wall: "no wall mounting needed", "non-wall", "不需挂墙" (a "non-slip wall mount" is one)
NOT_WALL = re.compile(r'(?i)(?<![a-z])(?:no|not|without|never)[\s-]+(?:\w+[\s-]+)?wall[\s_-]*mount|(?<![a-z])non[\s-]*wall(?![a-z])'
                      r'|不(?:需|用|必)?(?:要)?(?:挂墙|壁挂|上墙)')
# what says it stands: only when nothing says it hangs ("壁挂式 可落地" hangs, and can also stand)
STANDS = re.compile(r'(?i)free[\s-]*standing|floor[\s-]*standing|落地')
# parts made only for hanging it: keyhole slots and wall brackets (a lock's keyhole or a caster's mounting hole is not one)
PARTS = re.compile(r'(?i)key[\s_-]*hole[\s_-]*slot|(?<![a-z])wall[\s_-]*(?:bracket|mount|plate|rail|hook)|french[\s_-]*cleat|挂孔|葫芦孔')


def of(project, hint=''):
    """'wall' or None, for a stored project (or any dict with its fields)."""
    project = project if isinstance(project, dict) else {}
    metadata = project.get('metadata') if isinstance(project.get('metadata'), dict) else {}
    told = str(metadata.get('mounting') or '').strip().lower()
    if told in KINDS:
        return told
    if told in ('free', 'none', 'floor', 'bench', 'handheld'):
        return None
    words = ' '.join(str(value or '') for value in (project.get('name'), metadata.get('product_category'), metadata.get('title'), hint))
    if NOT_WALL.search(words):
        return None
    if WORDS.search(words):
        return 'wall'
    if STANDS.search(words):
        return None
    seen = project.get('orientation') if isinstance(project.get('orientation'), dict) else {}
    if WORDS.search(str(seen.get('product') or '')) and not NOT_WALL.search(str(seen.get('product') or '')):
        return 'wall'
    names = ' '.join(str(item.get('name') or '') for item in ((project.get('analysis') or {}).get('objects') or [])[:200] if isinstance(item, dict))
    if PARTS.search(names):
        return 'wall'
    return None
