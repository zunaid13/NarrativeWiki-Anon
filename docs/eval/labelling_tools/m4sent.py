"""m4sent.py <series> <t>: unique candidate sentences, each once, with the fact ids it matched."""
import json, sys, collections
sys.stdout.reconfigure(encoding="utf-8")
s, t = sys.argv[1], sys.argv[2]
rows = [json.loads(l) for l in open(f"docs/eval/leak_audit/{s}_tree_candidates_v{t}.jsonl", encoding="utf-8")]
by = collections.OrderedDict()
for i, r in enumerate(rows):
    for h in r["hits"]:
        k = (h["page"], h["sentence"])
        by.setdefault(k, []).append((i, h["kind"].split(":")[0]))
strong = [k for k, v in by.items() if any(x[1] != "event" for x in v)]
weak = [k for k in by if k not in strong]
for label, keys in (("STRONG", strong), ("EVENT", weak)):
    print(f"== {label} ({len(keys)})")
    for k in keys:
        ids = sorted({i for i, _ in by[k]})
        print(f"  {k[0][10:36]:26s} {k[1][:260]}  <- {ids[:12]}{'…' if len(ids) > 12 else ''}")
print("facts:", "; ".join(f"[{i}] r{r['reveal']} {r['character'].split()[0]}: {r['claim'][:60]}" for i, r in enumerate(rows) if r["hits"]))
