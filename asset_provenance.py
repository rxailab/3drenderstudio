"""Read-only provenance derived from an OBJ's explicitly disclosed fallback MTL."""
from pathlib import Path


def fallback_material_names(path):
    asset=Path(path).resolve()
    if asset.suffix.lower()!='.obj': return set()
    names=set()
    try:
        refs=[]
        with asset.open(encoding='utf-8-sig',errors='replace') as source:
            for line in source:
                stripped=line.strip()
                if stripped.lower().startswith('mtllib '): refs.append(stripped[7:].strip().strip('"'))
                # Material libraries are normally declared before geometry. Stop
                # here rather than rereading a potentially huge OBJ on each poll.
                elif stripped.startswith(('v ','vn ','vt ','f ')): break
        for ref in refs:
            candidate=(asset.parent/ref).resolve()
            if asset.parent not in candidate.parents or not candidate.is_file() or candidate.stat().st_size>16*1024*1024: continue
            lines=candidate.read_text(encoding='utf-8-sig',errors='replace').splitlines()
            if not any(line.strip().lower()=='# fallback materials only; original mtl was not supplied.' for line in lines): continue
            names.update(line.strip()[7:].strip() for line in lines if line.strip().lower().startswith('newmtl '))
    except (OSError,ValueError): pass
    return names


def enrich_project(project, data_root):
    """Describe known placeholder values without changing the plan or assets."""
    try:
        root=(Path(data_root)/project['id']/'originals').resolve()
        asset=Path(project['model_path']).resolve()
        if root not in asset.parents: return project
        names=fallback_material_names(asset)
        if not names: return project
        analysis=project.get('analysis')
        if not isinstance(analysis,dict): return project
        for material in analysis.get('materials',[]):
            if material.get('name') in names and material.get('source') in ('model','fallback'):
                material.update(source='fallback',confidence=0.0,suggested_preset=None,suggestion_confidence=0.0)
        warning='模型引用的 MTL 标明为替代材质，颜色与材质参数不代表原始产品；请以实际材质信息为准'
        warnings=analysis.setdefault('warnings',[])
        if warning not in warnings: warnings.append(warning)
    except (KeyError,TypeError,OSError,ValueError): pass
    return project
