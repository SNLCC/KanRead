"""Fail on unreviewed lock/assets changes; does not promise legal clearance."""
import hashlib
import importlib.metadata as metadata
import json
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parent.parent


def check():
    manifest=json.loads((ROOT/'legal/inventory.json').read_text(encoding='utf-8'))
    errors=[]
    def verify(path,expected):
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
            errors.append(f'Missing or changed reviewed file: {path.name}')
    verify(ROOT/'requirements.lock.txt',manifest['lock_sha256'])
    canonical=lambda name:re.sub(r'[-_.]+','-',name).lower()
    expected={canonical(p['name']) for p in manifest['packages']}
    installed={canonical(d.metadata['Name']) for d in metadata.distributions()}-{'pip'}
    if installed!=expected:
        errors.append('Unreviewed environment packages: '+','.join(sorted(installed^expected)))
    for p in manifest['packages']:
        try:
            if metadata.version(p['name'])!=p['version']:errors.append('Unreviewed version: '+p['name'])
        except metadata.PackageNotFoundError:errors.append('Missing dependency: '+p['name'])
        for n in p['notices']:verify(ROOT/'legal'/n['path'],n['sha256'])
    for a in manifest['assets']:
        path=ROOT/a['path'] if a['location']=='project' else Path(metadata.distribution(a['package']).locate_file(a['path']))
        verify(path,a['sha256'])
    reviewed_assets={a['path'] for a in manifest['assets'] if a['location']=='project'}
    for path in (ROOT/'static').rglob('*'):
        if path.is_file() and path.suffix not in ('.html','.js','.css') and path.relative_to(ROOT).as_posix() not in reviewed_assets:
            errors.append('Unreviewed static asset: '+path.relative_to(ROOT).as_posix())
    for path in list((ROOT/'app').rglob('*.py'))+list((ROOT/'tests').glob('*.py')):
        if re.search(r'(?:import|from)\s+(?:pymupdf|fitz)\b',path.read_text(encoding='utf-8-sig')):
            errors.append('Removed AGPL PDF engine imported: '+path.name)
    css=(ROOT/'static/style.css').read_text(encoding='utf-8-sig')
    if re.search(r'Segoe|Microsoft YaHei|Georgia|Arial|Times New Roman|@import|https?://',css):
        errors.append('Unreviewed UI font or external stylesheet')
    from fontTools.ttLib import TTFont
    cmap=TTFont(ROOT/'static/fonts/NotoSansCJKsc-Regular.otf').getBestCmap()
    sources=[p for p in (ROOT/'static').glob('*') if p.suffix in ('.html','.js','.css')]
    text=''.join(p.read_text(encoding='utf-8-sig') for p in sources)
    missing=sorted({ord(c) for c in text if ord(c)>127 and ord(c) not in cmap})
    if missing:errors.append('UI glyphs not in bundled font: '+','.join(hex(c) for c in missing))
    if errors:raise SystemExit('\n'.join(errors))
    print('PASS: reviewed dependency versions, license files, asset hashes and authored UI font coverage. Not legal clearance.')


if __name__=='__main__':check()
