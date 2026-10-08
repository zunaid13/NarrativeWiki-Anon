"""emptyctx.py: baseline page calls whose prompt carried no passages (the title-only branch), per run."""
import glob
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding='utf-8')
for d in sorted(glob.glob('data/*@*/_runs/*baseline*')) + sorted(glob.glob('data/*@*/_runs/*b3*')):
    f = os.path.join(d, 'calls.jsonl')
    if not os.path.exists(f):
        continue
    n, empty = 0, []
    for l in open(f, encoding='utf-8'):
        r = json.loads(l)
        p = r.get('prompt') or ''
        if not isinstance(p, str) or 'Write the page for the character' not in p:
            continue
        n += 1
        if p.startswith('The novels:'):
            m = re.search(r'character "([^"]+)', p)
            empty.append(m.group(1) if m else '?')
    series = os.path.normpath(d).split(os.sep)[1]
    if empty or '--all' in sys.argv:
        print('%-10s %-40s pages %3d  title-only %3d  %s' % (series, os.path.basename(d), n, len(empty), empty[:12]))
