"""pview.py <precision file> [--labels] [--full i,j,...] [--w N]: compact view of an _all precision sample.

Default: one block per row with page/section/label/value, the screen verdict and the cited passages
(each cut to N chars around the best-overlapping window). --labels: existing human labels only.
"""
import json
import re
import sys

sys.stdout.reconfigure(encoding='utf-8')
F = sys.argv[1]
rows = [json.loads(l) for l in open(F, encoding='utf-8')]
W = int(sys.argv[sys.argv.index('--w') + 1]) if '--w' in sys.argv else 420
full = set()
if '--full' in sys.argv:
    full = {int(x) for x in sys.argv[sys.argv.index('--full') + 1].split(',')}
if '--labels' in sys.argv:
    for i, r in enumerate(rows):
        print('[%d] %s | %s/%s | %s || %s / %s || %s' % (
            i, r['page'][-28:], r['section'], r['label'], r['value'][:150],
            r.get('human'), r.get('human_atom'), (r.get('note') or '')[:260]))
    sys.exit()
STOP = set('the a an and of to in is was her his she he with for on at as that by from it be had has who'.split())


def window(text, value, w):
    if len(text) <= w:
        return text
    vw = {x for x in re.findall(r"[a-z']+", value.lower()) if x not in STOP and len(x) > 2}
    best, bi = -1, 0
    for i in range(0, max(1, len(text) - w), 60):
        seg = text[i:i + w].lower()
        sc = sum(1 for x in vw if x in seg)
        if sc > best:
            best, bi = sc, i
    return ('...' if bi else '') + text[bi:bi + w] + '...'


for i, r in enumerate(rows):
    if full and i not in full:
        continue
    print('=' * 100)
    print('[%d] %s | %s | %s/%s | atom_of=%s' % (i, r['page'], r['surface'], r['section'], r['label'], r.get('atom_of')))
    print('VALUE:', r['value'])
    if r.get('unit') and i in full:
        print('UNIT:', r['unit'])
    sc = r.get('screen') or {}
    print('SCREEN:', sc.get('supported'), '-', (sc.get('reason') or '')[:200])
    ps = r.get('passages') or {}
    print('EVIDENCE (%d):' % len(r.get('evidence') or []), ' '.join(r.get('evidence') or [])[:300])
    shown = 0
    scored = []
    vw = {x for x in re.findall(r"[a-z']+", r['value'].lower()) if x not in STOP and len(x) > 2}
    for pid, txt in ps.items():
        scored.append((sum(1 for x in vw if x in txt.lower()), pid, txt))
    scored.sort(key=lambda z: -z[0])
    lim = len(scored) if i in full else 3
    for sc_, pid, txt in scored[:lim]:
        print('  <%s> (%d) %s' % (pid, sc_, txt if i in full else window(txt, r['value'], W)))
