"""The words a listing page may print: a product name cleaned of file-name noise, selling points that are really mesh
names, and the helpers that fit a headline or body to a line. Split out of ``brandkit_set``, which still re-exports every name.

The step that asks a model to translate the words (``localise``) stays in ``brandkit_set``: its name labels the request.
"""

import re
import unicodedata


MODEL_SUFFIXES = r'\.(?:glb|gltf|fbx|obj|stl|usdz?|blend|3ds|dae|ply|zip)$'
# Tokens a file name carries that are not part of the product's name: versions, exports, scan and texture labels.
FILE_TOKENS = re.compile(r'^(?:v\d+|rev\d*|r\d+|final|copy|export(?:ed)?|model|mesh|scan(?:ned)?|baked|low(?:poly)?|high(?:poly)?|lod\d*|untitled|'
                         r'new|draft|test|tmp|temp|\d+k|glb|gltf|fbx|obj|stl|usdz?|blend|zip)$', re.I)
SMALL_WORDS = {'a', 'an', 'and', 'or', 'of', 'for', 'with', 'the', 'to', 'in', 'on', 'by', 'at', 'x'}


def humanised(text):
    """The product's name as a page prints it. A title that is still the uploaded file's stem ("06_diagonal_cutting_pliers")
    loses its numbering, extension and file words (v2, final, lowpoly), its underscores become spaces and an all-lower or
    all-upper name is set in title case ("Diagonal Cutting Pliers"). A name the seller wrote (spaces, mixed case, no
    underscore or leading number) is returned as it is: "DeWalt DCD777" stays."""
    text = ' '.join(str(text or '').split())
    stem = re.sub(MODEL_SUFFIXES, '', text, flags=re.I)
    if stem == text and '_' not in stem and not re.match(r'^\d+[\s._-]+\S', stem):
        return text
    if ' ' not in stem and '_' not in stem and stem.count('-') >= 2:
        stem = stem.replace('-', ' ')            # "06-diagonal-cutting-pliers"
    # The upload's numbering ("06_", "3.", a zero-padded "06 "), not a number that is part of the name ("3-in-1").
    stem = re.sub(r'^0\d*\s+(?=\S)', '', re.sub(r'^(?:\d+[_.]+)+(?=\S)', '', stem))
    words = [word for word in re.split(r'[\s_]+', stem) if word.strip('-.')]
    words = [word for word in words if not FILE_TOKENS.match(word)] or words
    while len(words) > 1 and re.fullmatch(r'0\d*', words[-1]):
        words.pop()                              # a copy number: "pliers_01"
    letters = [word for word in words if re.search(r'[A-Za-z]', word)]
    if letters and (all(word == word.lower() for word in letters) or all(word == word.upper() for word in letters)):
        words = [word.lower() if index and word.lower() in SMALL_WORDS else word[:1].upper() + word[1:].lower() if re.search(r'[A-Za-z]', word) else word
                 for index, word in enumerate(words)]
    return ' '.join(words)


# Names a 3D program gives the shapes it makes: a selling point carrying one was made from the file, not from the seller.
PRIMITIVES = {'cylinder', 'cube', 'plane', 'sphere', 'uvsphere', 'icosphere', 'circle', 'torus', 'cone', 'mesh', 'object', 'polysurface',
              'geometry', 'primitive', 'untitled', 'node', 'null', 'empty', 'bezier', 'nurbs', 'instance'}


def _words(text):
    return re.findall(r'[a-z]+|[一-鿿]+', unicodedata.normalize('NFKC', str(text or '')).lower())


def _mesh_key(name):
    """A part name without its numbering and punctuation: "Cylinder.002" and "cylinder" are one name."""
    tokens = re.split(r'[^0-9a-z一-鿿]+', unicodedata.normalize('NFKC', str(name or '')).lower())
    return ' '.join(token for token in tokens if token and not token.isdigit())


def mesh_named(title, product, strict=True):
    """Why a selling point's title is a name out of the 3D file rather than the seller's words, or None.

    Pliers uploaded without a description were printed with "Cylinder grip" pointing at the rivet: the drafter had made a
    selling point out of the mesh "Cylinder". A title is a mesh name when it carries a file name's marks ("Handle_L",
    "Cube.001"), is one of the model's own part names, or (``strict``) uses a 3D program's shape name ("Cylinder",
    "Plane") that nothing the seller wrote (the pasted description, the headline, the subline) uses. The page builders
    ask without ``strict``: by then the line may be a translation of the seller's own words."""
    raw = ' '.join(str(title or '').split())
    if not raw:
        return None
    # "Cube.001" (a letter before the dot), not a measurement such as "0.01 mm accuracy".
    if '_' in raw or re.search(r'[A-Za-z]\.\d{2,}\b', raw):
        return '带有文件名的写法'
    told = ' '.join(str(product.get(key) or '') for key in ('source', 'headline', 'subline'))
    key = _mesh_key(raw)
    for name in product.get('_model_parts') or ():
        if key and key == _mesh_key(name) and raw.lower() not in told.lower():
            return '是模型里的部件名 %s' % name
    if strict:
        said = set(_words(told))
        shapes = [word for word in _words(raw) if word in PRIMITIVES and word not in said]
        if shapes:
            return '用的是三维软件给形状起的名字 %s' % shapes[0].capitalize()
    return None


def printable(product):
    """(product, notes): the record with only the selling points a page may print. A point whose title is a mesh name
    (``mesh_named``) is left out and said, so the set never labels a part with what the file called it.

    Only a record that was drafted or extracted (``drafted``) can carry a point the seller did not write. Points typed by
    hand into a record that was never drafted are the seller's own: "Hydraulic cylinder" or a part named "Trigger" stays,
    and only a file name's marks ("Handle_L", "Cube.001") are refused there."""
    kept, notes = [], []
    machine = bool(product.get('drafted'))
    for row in product.get('features') or []:
        why = mesh_named(row.get('title'), product) if machine else mesh_named(row.get('title'), {}, strict=False)
        if why:
            notes.append('卖点「%s」%s，不是你写的卖点，没有印到图上；请在「产品信息」里改成产品真正的卖点。' % (row.get('title'), why))
        else:
            kept.append(row)
    if len(kept) == len(product.get('features') or []):
        return product, notes
    return {**product, 'features': kept}, notes


_HAN = re.compile(r'[\u4e00-\u9fff]')
_NUMERALS = {'零': 0, '一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}


def _arabic(text):
    """Chinese numerals up to 99 written as figures ("四颗" -> "4颗", "十二" -> "12")."""
    def value(match):
        word = match.group(0)
        if '十' not in word:
            return ''.join(str(_NUMERALS[char]) for char in word)
        tens, _, ones = word.partition('十')
        return str((_NUMERALS.get(tens, 1) if tens else 1) * 10 + (_NUMERALS.get(ones, 0) if ones else 0))
    return re.sub(r'[零一二两三四五六七八九]?十[一二三四五六七八九]?|[零一二两三四五六七八九]+', value, text)


def _strings(product, scenes, parts):
    """Every piece of the record's own words that can reach a page, with the most characters a page gives it."""
    found = [(product.get('title'), 24), (product.get('headline'), 40), (product.get('subline'), 60), ((product.get('usecase') or {}).get('headline'), 40)]
    for row in product.get('features') or []:
        found += [(row.get('title'), 22), (row.get('detail'), 40)]
    for row in product.get('specs') or []:
        found += [(row.get('label'), 18), (row.get('value'), 30)]
    for row in product.get('figures') or []:
        found += [(row.get('label'), 18), (row.get('unit'), 8)]
    for row in scenes or []:
        found += [(row.get('title'), 18), (row.get('body'), 40)]
    for row in parts or []:
        found += [(row.get('name'), 20), (row.get('caption'), 40)]
    limits = {}
    for text, limit in found:
        if isinstance(text, str) and text.strip():
            limits[text] = min(limit, limits.get(text, limit))
    return limits


def _body(text, limit):
    """A body line that fits ``limit``: whole, or its leading whole clause; never a sentence cut off mid-phrase (None then)."""
    text = ' '.join(str(text or '').split())
    if len(text) <= limit:
        return text or None
    cut = max((match.start() for match in re.finditer(r'[.;,，；。:：]|\s[-–—]\s', text[:limit + 1])), default=-1)
    return text[:cut].rstrip(' ,，;；:：-–—') + ('.' if not re.search(r'[\u4e00-\u9fff]', text) else '。') if cut >= limit * .35 else None


def _keep_together(text):
    """Joined words (CR-V, Li-Ion, 20V-MAX) are not broken at their hyphen."""
    return re.sub(r'(?<=[A-Za-z0-9])-(?=[A-Za-z0-9])', '-\u2060', str(text)) if text else text


def _clip(text, limit):
    """Text that fits ``limit`` characters without an ellipsis (a listing never prints "…"): the leading segment that fits
    (up to a dash, comma, slash, bracket or " with"), else the whole words that fit; line breaks the author put in are kept."""
    lines = [' '.join(line.split()) for line in str(text or '').split('\n')]
    text = '\n'.join(line for line in lines if line)
    if len(text) <= limit:
        return text
    text = text.rstrip('…').rstrip('.').rstrip()
    for match in re.finditer(r'\s[-–—|/]\s|[,，;；:：(（/]|\swith\s|\sfor\s|，', text):
        head = text[:match.start()].rstrip(' ,，;；:：-–—')
        if len(head) > limit:
            break
        best = head
    if 'best' in locals() and len(best) >= 4:
        return best
    cut = text[:limit]
    if re.search(r'[A-Za-z0-9]', cut[-1:]) and len(text) > limit and re.search(r'[A-Za-z0-9]', text[limit:limit + 1]) and ' ' in cut:
        cut = cut[:cut.rindex(' ')]          # a Latin word is not cut in two
    return cut.rstrip(' ,，;；:：-–—\n')


def _headline(text, language, limit):
    """A headline with its line break where a reader pauses: the author's own, else (in Chinese) after the
    clause mark nearest the middle, so a template never breaks a phrase in two."""
    text = _clip(text, limit)
    if language != 'zh' or '\n' in text or len(text) <= 7:
        return text
    marks = [index for index, char in enumerate(text[:-1]) if char in '，、；：,;:' and 2 <= index]
    if not marks:
        return text
    cut = min(marks, key=lambda index: abs(index - len(text) / 2))
    return text[:cut + 1] + '\n' + text[cut + 1:]
