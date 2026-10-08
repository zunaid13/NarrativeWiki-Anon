"""m3fix.py: apply the 2026-10-02 fairness audit of baseline M3 negatives (C25). Each entry: (series, t, row, evidence)."""
import json, collections
ROOT = ""
FIX = [
 ("anne@b1", 4, 30, "v01:c35:p0004 'the vivid, black-eyed Stella'."),
 ("anne@b2", 2, 12, "v02:c19:p0051 Gilbert 'watched over word and thought and deed' to be worthy of his ideal; high ideals are the paragraph's subject."),
 ("anne@b2", 2, 20, "v02:c30:p0027 on the wedding day Charlotta 'rustled into a white dress, so stiffly starched that it could stand alone'."),
 ("anne@b2", 2, 23, "v02:c09:p0010 Anne of Mrs. Lynde: 'she had a very kind heart and always helped the poor'."),
 ("anne@b2", 3, 0, "v03:c39:p0002 Dorothy Gardner of Roy: 'really he isn't a bit interesting. He looks as if he ought to be, but he isn't'."),
 ("anne@b2", 4, 16, "v04:c40:p0049 Susan: 'I would rather fall to and cheer people up than weep with them', verbatim."),
 ("anne@b2", 4, 26, "v04:c32:p0012 'They were double cousins, you see. Their fathers were brothers and their mothers were twin sisters'."),
 ("anne@b2", 4, 37, "v03:c37:p0042 'Aunt Jamesina had a proper respect for the cloth even in the case of an unfledged parson'."),
 ("anne@b3", 1, 9, "v01:c30:p0025 'But Anne Shirley he simply ignored, and Anne found out that it is not pleasant to be ignored'."),
 ("anne@b3", 1, 11, "v01:c19:p0081 Anne to Miss Barry: 'Diana is a very ladylike girl'."),
 ("anne@b3", 2, 13, "v02:c27:p0044 Charlotta: 'yesterday I bruk her green and yaller bowl ... Her grandmother brought it out from England and Miss Lavendar was awful choice of it'."),
 ("anne@b3", 3, 23, "v02:c11:p0038 Paul 'fought St. Clair Donnell recently because St. Clair said the Union Jack was away ahead of the Stars and Stripes'."),
 ("anne@b3", 3, 35, "v02:c01:p0045 'Mrs. George Pye has taken her husband's orphan nephew, Anthony Pye'."),
 ("anne@b3", 4, 15, "v04:c08:p0019 Miss Cornelia 'had a fresh, round, pink-and-white face'."),
 ("anne@b3", 4, 27, "v04:c25:p0014 'The afternoons were generally spent in some merry outing with the Blythes'."),
 ("anne@b3", 4, 29, "v03:c09:p0012 Stella: 'You know how I loathe boarding. I've boarded for four years and I'm so tired of it'."),
 ("anne@b3", 5, 5, "v04:c03:p0030 'Could this splendid six feet of manhood be the little Paul of Avonlea schooldays?'"),
 ("anne@b3", 5, 10, "v05:c16:p0039 Faith to Norman Douglas: 'I came—to ask you—to go to church—and pay—to the salary'."),
 ("anne@b3", 5, 14, "v02:c30:p0027 'the resultant rampant crinkliness was plaited into two tails'."),
 ("anne@b3", 5, 21, "v05:c03:p0014 Walter 'seldom joined in the school sports, preferring to herd by himself ... and read books—especially po'try books'."),
 ("anne@b3", 5, 22, "v05:c07:p0015 'Rilla flew down the hill and along the street ... Through the Glen street they swept'."),
 ("anne@b4", 1, 7, "v01:c29:p0024 'Josie Pye took first prize for knitted lace'."),
 ("anne@x1", 2, 0, "v02:c01:p0016 Anne: Marilla 'has gone down to East Grafton to see a distant relative of hers who is very ill' (Mary Keith; a cousin's wife, c01:p0035)."),
 ("anne@x1", 2, 3, "v02:c25:p0010-p0012 'Mr. James A. Harrison'; 'James A.'; the veranda visits v02:c03-c04."),
 ("anne@x1", 3, 16, "v03:c02:p0004 Billy Andrews's 'broad, freckled countenance'."),
 ("anne@x1", 3, 36, "v03:c25:p0042 'a box containing a dozen magnificent roses' with a 'poetical quotation'; v03:c27:p0036 the sonnet."),
 ("anne@x2", 5, 29, "v01:c35:p0001 Ruby had 'a plump showy figure'."),
]
by = collections.defaultdict(list)
for s, t, i, ev in FIX: by[(s, t)].append((i, ev))
for (s, t), fixes in sorted(by.items()):
    f = f"{ROOT}docs/eval/precision/{s}_v{t}_all.jsonl"
    rows = [json.loads(l) for l in open(f, encoding="utf-8")]
    before = sum(r["human"] == "supported" for r in rows)
    for i, ev in fixes:
        r = rows[i]
        assert r["human"] in ("unsupported", "uncertain", "partial"), (s, t, i, r["human"])
        r["note"] = f"CORRECTED 2026-10-02 audit C25 (was {r['human']}: {r['note']}) -> supported: {ev}"
        r["human"] = r["human_atom"] = "supported"
    open(f, "w", encoding="utf-8", newline="\n").write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    n = len(rows) - sum(r["human"] == "frame_error" for r in rows)
    print(f"{s} t={t}: {before} -> {sum(r['human'] == 'supported' for r in rows)} / {n}")
