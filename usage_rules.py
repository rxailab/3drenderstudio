"""How common hand tools are really held and used, for the scene pictures and their check.

The pose agent writes usage rules for each product (articulation.usage), but its words vary from run to run and are
often vague ("pliers grip the wire in the jaws"): pictures came back with a gloved hand pinching the pliers' head, a
thick cable cut at the very tips of the jaws, a claw hammer pressed claw-first against a wall with the nail nowhere
near its slot. For the tools that recur in a hardware shop the right way is not a matter of opinion, so it is written
down here once, short and checkable (which part the hand is on, where the work sits, at what angle), and put in front
of the agent's own rules. The same list goes to the image model (scene_imagegen.prompt), to the pose agent
(scene_pose.decide) and to the vision check (scene_imagegen.check), so a picture is asked for and judged by one rule.

A tool is recognised by words in its name, the scene's product words, the pose agent's product_kind or the category,
in English or Chinese. A more specific kind covers a general one (diagonal cutters are pliers, but their rule is the
cutters' rule), and a word that belongs to another tool does not count (a hammer drill is a drill, not a hammer; a pipe
cutter is not a pair of cutting pliers).
"""
import re

LIMIT = 6          # rules per picture: the canonical ones first, then the agent's own
LENGTH = 200       # characters per rule that the prompt, the pose agent and the check keep

# (kind, words, not_with, covers, rules in English, rules in Chinese). English words count as whole words or phrases;
# Chinese ones as substrings. not_with: words that mean another tool; covers: kinds this one replaces.
KINDS = (
    ('cutters', ('diagonal cutter', 'diagonal cutters', 'diagonal cutting pliers', 'side cutter', 'side cutters', 'cutting pliers',
                 'wire cutter', 'wire cutters', 'flush cutter', 'flush cutters', 'end cutter', 'end cutters', 'nipper', 'nippers',
                 '斜口钳', '偏口钳', '水口钳', '剪线钳', '断线钳', '尖嘴剪钳', '电子钳'),
     ('pipe cutter', 'tube cutter', 'bolt cutter', '割管'), ('pliers',),
     ('both handles are gripped in the palm with the fingers wrapped round them; no finger pinches the head, the jaws or the pivot',
      'the wire sits deep in the jaws close to the pivot, square to the cutting edges and straight on both sides of them, never at the jaw tips',
      'only a wire thin enough for the jaws is cut; a thick cable is never squeezed in the tips'),
     ('双手柄握在掌心、手指环握手柄，不捏钳头、钳口或铆轴',
      '导线放在靠近铆轴的刃口深处，与刃口垂直，两侧导线保持笔直，不用钳口尖端剪',
      '只剪钳口容得下的细线，不用钳尖去夹粗电缆')),
    ('pliers', ('pliers', 'plier', 'combination pliers', 'lineman pliers', "lineman's pliers", 'needle nose pliers', 'long nose pliers',
                '钳子', '钢丝钳', '老虎钳', '尖嘴钳', '鲤鱼钳', '扁嘴钳', '克丝钳'),
     ('pipe cutter', 'bolt cutter'), (),
     ('both handles are gripped in the palm with the fingers wrapped round them; no finger pinches the head, the jaws or the pivot',
      'the piece is held deep and square inside the jaws, never balanced on the jaw tips'),
     ('双手柄握在掌心、手指环握手柄，不捏钳头、钳口或铆轴', '工件夹在钳口内、居中且方正，不只用钳尖勉强夹住')),
    ('scissors', ('scissors', 'shears', 'snips', 'tin snips', 'pruning shears', 'secateurs', '剪刀', '剪子', '铁皮剪', '枝剪', '修枝剪', '园艺剪'),
     ('wire cutter', 'bolt cutter'), (),
     ('the thumb and fingers go through or round both handles; no finger is on the blades or the pivot',
      'the material sits deep between the blades near the pivot, the blades square to it'),
     ('拇指和其余手指分别握住或穿过两侧手柄，不碰刀刃和铆轴', '材料放在靠近铆轴的刀刃深处，刀刃与材料垂直')),
    ('hammer', ('hammer', 'claw hammer', 'mallet', '锤', '羊角锤', '榔头', '橡胶锤'),
     ('hammer drill', 'rotary hammer', 'demolition hammer', '电锤', '冲击钻'), (),
     ('one hand grips the lower half of the handle with the whole hand; never the head or the neck',
      'driving a nail: the hammer face strikes the nail head squarely, the nail upright and perpendicular to the surface, held near its point',
      'pulling a nail: the nail shank sits deep in the claw\'s V-slot, the hammer head rests on the surface and the handle is levered '
      'back; never the claw just pressed against the surface'),
     ('单手握住手柄下半段，整只手环握，不握锤头或锤颈',
      '钉钉子：锤面正对钉帽敲击，钉子垂直于表面，手扶钉子下部',
      '起钉子：钉身卡进羊角的 V 形槽深处，钉帽在羊角上方，锤头抵在表面上、手柄向后扳；不能只把羊角顶在墙上')),
    ('saw', ('saw', 'hand saw', 'handsaw', 'hacksaw', 'panel saw', 'pruning saw', 'coping saw', '锯', '手锯', '钢锯', '手板锯', '木工锯', '园艺锯', '折叠锯'),
     ('chainsaw', 'chain saw', 'jigsaw', 'circular saw', 'miter saw', 'mitre saw', 'table saw', 'reciprocating saw', '电锯', '链锯', '曲线锯', '圆锯', '切割机'), (),
     ('one hand grips the handle, the other steadies the board away from the blade',
      'the teeth sit on the cut line, the blade square to the face of the board, the board supported on a bench or trestle'),
     ('一只手握锯柄，另一只手在远离锯条处扶稳木板', '锯齿落在切割线上，锯条与板面垂直，木板架在工作台或支架上')),
    ('drill', ('drill', 'cordless drill', 'drill driver', 'impact driver', 'cordless driver', 'hammer drill', '电钻', '起子机', '冲击钻', '手电钻', '锂电钻'),
     (), ('screwdriver', 'hammer'),
     ('one hand grips the pistol grip, the index finger on the trigger',
      'the bit is seated in the screw head, or the drill bit on the mark, on the axis of the screw or hole, square to the surface'),
     ('一只手握住手柄，食指扣在扳机上', '批头卡在螺丝头里（或钻头对准标记），与螺丝或孔同轴，垂直于表面')),
    ('screwdriver', ('screwdriver', 'screw driver', 'phillips screwdriver', 'flat screwdriver', '螺丝刀', '螺丝批', '起子', '改锥', '改刀'),
     ('impact driver', 'cordless driver', 'drill driver', '起子机', '电动'), (),
     ('the handle is gripped in the palm, the fingers round it, the shaft in line with the forearm',
      'the bit is seated in the screw head on the screw\'s axis, the shaft perpendicular to the surface'),
     ('手柄握在掌心，手指环握，刀杆与前臂成一直线', '刀头卡在螺丝头槽内，与螺丝同轴，刀杆垂直于表面')),
    ('caliper', ('caliper', 'calliper', 'vernier', 'digital caliper', 'dial caliper', '卡尺', '游标卡尺', '数显卡尺', '带表卡尺'),
     (), (),
     ('one hand holds the beam, the thumb on the thumb wheel or slider',
      'the jaws close squarely on both faces of the workpiece, the beam perpendicular to those faces, never tilted across them or on one edge'),
     ('一只手握住尺身，拇指放在滚轮或滑块上', '量爪方正地夹住工件两个面，尺身与被测面垂直，不斜搭、不只碰一个棱')),
    ('tape', ('tape measure', 'measuring tape', 'tape rule', '卷尺', '钢卷尺', '盒尺'),
     (), (),
     ('one hand holds the case',
      'the hook is caught on the edge of the work and the blade lies flat along the surface, straight and not twisted'),
     ('一只手握住尺盒', '尺钩钩住工件边缘，尺带平贴表面、拉直不扭')),
    ('wrench', ('wrench', 'spanner', 'combination wrench', 'adjustable wrench', '扳手', '呆扳手', '梅花扳手', '活动扳手', '开口扳手'),
     ('torque wrench', 'impact wrench', '电动扳手'), (),
     ('the hand grips the far end of the handle',
      'the jaws or the ring sit fully on the flats of the nut or bolt head, in the plane of the nut, never on its corners'),
     ('手握住手柄末端', '开口或梅花孔完全套在螺母的对边上，与螺母在同一平面，不卡在棱角上')),
)


def _norm(text):
    return re.sub(r'[\s_\-]+', ' ', str(text or '')).casefold().strip()


def _has(text, word):
    if not word.isascii():
        return word in text
    return re.search(r'(?<![a-z])%s(?![a-z])' % re.escape(word), text) is not None


def kinds(*texts):
    """The tool kinds named in ``texts`` (a product name, a category, the agent's product_kind), most specific first."""
    text = ' '.join(_norm(item) for item in texts if item)
    found = []
    for kind, words, not_with, covers, _, _ in KINDS:
        if any(_has(text, word) for word in words) and not any(_has(text, word) for word in not_with):
            found.append((kind, covers))
    covered = {name for _, covers in found for name in covers}
    return [kind for kind, _ in found if kind not in covered]


def canonical(*texts, language='en'):
    """The written-down rules for the tool kinds in ``texts``."""
    table = {kind: (en, zh) for kind, _, _, _, en, zh in KINDS}
    rules = []
    for kind in kinds(*texts):
        rules += list(table[kind][1 if language == 'zh' else 0])
    return rules


def words_for(project, product=''):
    """Every name the product goes by: the scene's product words, the project and file names, the category and the
    pose agent's product_kind."""
    project = project if isinstance(project, dict) else {}
    metadata = project.get('metadata') if isinstance(project.get('metadata'), dict) else {}
    record = project.get('articulation') if isinstance(project.get('articulation'), dict) else {}
    stem = re.sub(r'\.[a-z0-9]+$', '', str(project.get('model_path') or '').replace('\\', '/').rsplit('/', 1)[-1], flags=re.I)
    return [str(value) for value in (product, project.get('name'), metadata.get('product_category'), record.get('product_kind'), stem) if value]


def merged(words, own=None, language='en', limit=LIMIT):
    """The canonical rules for the tool named in ``words``, then the agent's ``own`` rules that add something: at most ``limit``."""
    words = [words] if isinstance(words, str) else list(words or [])
    rules = []; seen = set()
    for rule in canonical(*words, language=language) + [str(item) for item in (own or [])]:
        text = re.sub(r'\s+', ' ', rule).strip().rstrip('.。')
        if text and text.casefold() not in seen:
            seen.add(text.casefold()); rules.append(text)
    return rules[:limit]
