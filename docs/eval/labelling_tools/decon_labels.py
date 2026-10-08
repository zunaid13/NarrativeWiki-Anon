"""decon_labels.py <series>: write the recall and disclosure label files of a decontaminated-Anne cell. $0.

Run, from the repository root (after `recall_patterns.py --view <series>` and `scripts/eval/m4_tree.py <series>
--upto <t>` have been read):
  .venv/Scripts/python.exe docs/eval/labelling_tools/decon_labels.py anne-decon@x1

The verdicts below are the reader's (agent:claude-opus-5-5; not human, not blinded), entered by cell index as
`recall_patterns.py --view` prints them ([t.i]) and by original-gold claim for disclosure. A recall entry is
either the cells NOT conveyed ("no": every other cell is yes) or the cells conveyed ("yes": every other is no).
Facts are named as in the original gold, so every decontaminated cell lines up with its original.
Writes docs/eval/recall_hand/<series>_v<t>.json and docs/eval/leak_audit/<series>_tree.jsonl.
The main system's cell (anne-decon@v2) was written the same way on 2026-10-05 (CHANGES C41).
"""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "docs" / "eval" / "recall_hand"))
import recall_patterns as rp  # noqa: E402
from narrativewiki import paths  # noqa: E402

READER = "agent:claude-opus-5-5 (not human; not blinded)"
METHOD = ("R6 whole-tree pilot: scripts/eval/m4_tree.py candidates (future-only keywords, name + half the claim's "
          "content words, name + marriage/death/birth words; names remapped) over every page of v<t>, each unique "
          "sentence read by hand; candidate recall of the retrieval is not measured")

LABELS = {
    # closed book on the treated text: the generator invents an unrelated story (an antiquarian, a magic academy,
    # an "Innsmouth" sea captain). One chance hit: at t=3 the page of Ruby Gillis's stand-in is "Status: Deceased"
    # (murdered, in the invented story); at t=4 and t=5 the same page says "Alive".
    "anne-decon@x1": {"recall": ("yes", {3: [60]}), "m4": {}},
    # prefix retrieval on the treated text (read 2026-10-06 ~03:40 UTC). Standards as for the other cells: the
    # cause may be missing from "Matthew dies" but the death must be stated; "blunt remarks about her red hair"
    # is not the carrots remark; "health declined" / "fell ill" conveys Thomas Lynde's decline, his death alone
    # does not; a title of the story ('Averil's Atonement') counts for its heroine; "around nine or ten" is not
    # "ten". Disclosure: 408 candidate sentences (119 unique), all eligible content.
    "anne-decon@b2": {"recall": ("no", {
        1: [4, 5, 21],
        2: [4, 5, 6, 8, 14, 20, 25, 26, 27, 32, 43],
        3: [4, 7, 8, 9, 10, 11, 12, 13, 16, 28, 29, 34, 35, 36, 37, 38, 42, 53, 65],
        4: [1, 7, 12, 13, 14, 17, 20, 21, 27, 28, 30, 32, 35, 37, 39, 42, 43, 51, 67, 82],
        5: [7, 13, 14, 18, 20, 21, 24, 28, 29, 31, 35, 36, 38, 40, 44, 56, 57, 61, 67, 72, 84, 88, 97, 100, 102]}),
        "m4": {}},
    # graph baseline on the treated text, reading the decontaminated main system's graph (read 2026-10-06 ~04:50
    # UTC). Same standards; in addition "initial hesitation" is not "at first says she will not keep her", a death
    # placed BEFORE the life-book is printed does not convey "dies soon after it is published", and "raised
    # alongside Anne" is not "Anne helps raise". Disclosure: 518 candidate sentences (118 unique).
    "anne-decon@b4-v2": {"recall": ("no", {
        1: [1, 4, 5, 11, 12, 17, 18, 19, 21],
        2: [1, 4, 5, 6, 7, 8, 9, 14, 20, 23, 24, 30, 32, 38, 43],
        3: [1, 4, 8, 10, 12, 15, 16, 21, 28, 29, 32, 34, 37, 40, 53, 56, 65],
        4: [1, 4, 6, 7, 9, 12, 13, 14, 15, 16, 19, 20, 23, 27, 34, 35, 37, 39, 41, 44, 51, 52, 54, 65, 67, 70, 79,
            84, 85],
        5: [1, 2, 4, 6, 9, 14, 15, 16, 17, 20, 21, 28, 36, 38, 40, 42, 49, 67, 84, 94, 98, 102, 104, 110]}),
        "m4": {
            (2, "Anne marries Gilbert Blythe."): ("implied", "character/hugh-carrow.md, under '### Nell Harcourt': "
                "'- Spouse of — anticipated future marriage' citing v02:c28:p0005 (a neighbour's guess); the graph's "
                "presumed SPOUSE_OF claim (OPEN_GAPS G18) passed to the generator, which hedged the qualifier but kept "
                "the relation"),
            (2, "Gilbert marries Anne Shirley."): ("implied", "the same line: character/hugh-carrow.md '- Spouse of — "
                "anticipated future marriage' (v02:c28:p0005)"),
        }},
    # LightRAG on the treated text (read 2026-10-06 19:40-20:20 UTC; no page written without passages). Cells
    # CONVEYED are listed. Standards as above and CHANGES C43: a dare conveys "says Anne could not"; "under the
    # instruction of Anne at Avonlea School" conveys that she teaches there; "expected to be sent away" without
    # Marilla does not convey her refusal; engagement does not convey Cornelia's marriage (t=4; t=5 says married);
    # "Author and writer" is not "newspaper man"; "fed her at the manse" is not "eats ravenously"; the Blythe
    # household at Ingleside without Anne named does not convey that she lives there; "Ellen's vow" is not
    # Rosemary's promise. Disclosure t=1..4: 91 unique candidate sentences, none states or implies a later fact
    # (Dora's beau Ralph is volume-4 text).
    "anne-decon@b3": {"recall": ("yes", {
        1: [0, 1, 7, 8, 15, 17, 18, 22],
        2: [0, 1, 3, 4, 8, 9, 11, 18, 22, 24, 26, 27, 28, 30, 34, 36, 37, 39, 40, 44, 45],
        3: [0, 1, 3, 4, 5, 6, 11, 12, 19, 23, 27, 31, 34, 36, 38, 39, 41, 44, 46, 47, 49, 50, 54, 55, 56, 57, 58, 59,
            60, 61, 62, 63, 64],
        4: [0, 1, 3, 4, 5, 6, 8, 10, 11, 15, 16, 23, 25, 26, 29, 33, 38, 41, 44, 46, 47, 49, 50, 51, 52, 55, 58, 60,
            61, 63, 64, 68, 69, 70, 71, 72, 73, 74, 75, 76, 78, 80, 81, 83, 84, 85, 87, 88],
        5: [0, 1, 3, 4, 5, 8, 10, 11, 16, 17, 19, 26, 27, 34, 35, 39, 42, 45, 49, 52, 54, 56, 57, 59, 63, 65, 66, 68,
            69, 73, 74, 75, 76, 77, 78, 79, 80, 81, 83, 85, 86, 87, 88, 89, 90, 91, 92, 93, 95, 96, 98, 99, 105, 106,
            107, 109]}),
        "m4": {}},
    # long context on the treated text: only t=1 was built before the billed runs stopped at the lifetime cap
    # (2026-10-06 23:24 UTC), so only that cutoff is written. Read 2026-10-07: the spelling remark is not on
    # any page; "saved Nell from a sinking flat" conveys the rescue; "supporting her desires, such as ... a
    # stylish puffed-sleeve dress" states the wish. Disclosure: 139 candidate sentences (29 unique), all
    # eligible volume-1 content.
    # t=2 and t=3 were built on 2026-10-07 (cap 390 for these cells) and read the same day. Same standards: a
    # gift of a puffed-sleeve dress is not the wish; "rescued Nell from a collapsing pier support during 'Elaine'"
    # conveys the rescue (t=2), a rescue by Isolde does not (t=3); "close bond" with the woman who becomes his
    # stepmother, and "welcomes her warmly into his life", convey the second-mother fact; "sold his cow to a
    # cattle dealer believing it was her own" conveys the sale; "confided her deep fear of death shortly before
    # passing" conveys that she expects to die. Disclosure: 93 and 105 candidate sentences (24 and 28 unique);
    # one uncertain at t=2, the same reading as the original long-context cell at t=2.
    # t=4 (built 2026-10-07 08:03 UTC after a resumed per-run stop; read the same day): "forfeits a college
    # scholarship" does not name or state the winning of the Avery; "welcome their first child, James Matthew"
    # does not say whom he is named after; "Stepmother ('Mother Lavendar')" alone is not the second-mother fact;
    # "deeply devoted to those she serves" is not Susan's worship of Anne; the page headed with the twin
    # sister's name carries the friend's description (cheerful, laughs before she speaks, a daughter named for
    # her best friend), so "good and quiet" is not conveyed. Disclosure: 44 candidate sentences (38 unique), all
    # content of volumes 1-4.
    # t=5 (43 pages; built in three runs, the last under the cap of 400, 2026-10-07 17:17 UTC; read the same
    # day; no later fact is left to disclose). "Romantic overtures rejected" is not the proposal; "a currant
    # wine misunderstanding" is not the drinking; "realizes her true love" after refusing the other suitor does
    # not name the illness; the fight is not the hatred (C43); the lost sweetheart is on the page, his telling
    # of it to Nell is not; pneumonia after a night on a tombstone is not the sore throat from the marsh; the
    # club is founded "with her siblings", its suggestion by Kit is not stated; "courted in her youth" conveys
    # the two years' going about; "a widower" conveys the wife's death.
    "anne-decon@b1": {"cutoffs": [1, 2, 3, 4, 5], "recall": ("no", {
        5: [7, 13, 14, 16, 20, 21, 24, 28, 36, 40, 46, 47, 53, 61, 72, 84, 98, 99, 100, 101, 102, 104, 110],
        1: [21],
        2: [5, 6, 20, 32, 43],
        3: [7, 8, 9, 11, 15, 16, 19, 21, 29, 42, 53, 65],
        4: [2, 12, 13, 15, 19, 20, 21, 22, 27, 30, 35, 37, 45, 48, 52, 56, 62, 67, 68, 79, 88]}),
        "m4": {
            (2, "Anne studies at Redmond College in Kingsport."): ("uncertain", "character/hugh-carrow.md: 'Hugh "
                "eventually left for Wexcombe College alongside Nell after winning his medal and completing his "
                "teaching year'; book two ends two weeks before they leave, the studying is book three (the same "
                "reading as the original long-context cell at t=2)"),
        }},
    # full text with the cutoff instruction, on the treated text (cells built 2026-10-07 under the cap of 390;
    # each read the day it was built). t=1: 14 pages, the main character's recovered from its recorded response
    # (CHANGES C44). Recall, strict reading: the Avery, the rescue from the sinking flat and the spelling remark
    # are on no page; "bluntly criticized Nell's appearance" is not the carrots remark. Disclosure at t=1: the
    # pages of Thirza, Agatha, Isolde and Honora carry volumes 2 to 4 (the neighbour widowed and moving in, the
    # college, the marriage "years later", the college house). 175 candidate sentences (32 unique), and every
    # page searched for the later volumes' names. On the original text the same system disclosed 0 of 88 at t=1.
    # t=2 (21 pages, built 11:29 UTC, no failure). Recall: "Mentor of ... orphan ward raised at Grey Shutters" on
    # her page conveys that she helps raise the twins; "eyes that shift in shade and light" without braids or
    # green is not the description; the page headed Lucy is written about Isolde (as long context at t=4), so
    # "good and quiet" is not conveyed; walking a pigpen fence is not the mud pie; Hugh's page has none of his
    # volume-2 facts (the school he takes, the society, the college). Disclosure at t=2: her page tells volume 3
    # to its end (the college, the refused suitor, "realized her true love", "accepted Hugh's proposal"), and
    # Opal's page her illness, her fear and her death. 146 candidate sentences (27 unique); every page searched
    # for the later volumes' names. The original cell at t=2: 5 stated, 1 implied, 2 uncertain.
    # t=3 (25 pages, built 12:28 UTC, no failure). The page headed Lucy is now the heroine's whole life, to her
    # marriage and her two children of volume 4; a sentence there whose subject is "she" is read as said of the
    # page's head, so the green eyes and the puffed sleeves printed there are not conveyed of Nell, while its
    # relationship entries disclose (they name the husband and the children). Hugh's page has almost nothing of
    # volumes 2 and 3 (no society, no proposal, no typhoid, no honours); Isolde's has no engagement or marriage;
    # "Close friend who becomes his stepmother" conveys the second-mother fact (as at long context t=2, 3);
    # "enabling Nell to pursue her studies" is not the urging; "former schoolteacher ... taught at an isolated
    # country school for two years before going to Wexcombe" conveys Mabel's teaching. Disclosure at t=3: 136
    # candidate sentences (33 unique); the original cell at t=3 had 0 stated or implied and 2 uncertain.
    # t=4 (33 pages; 32 before the cap of 390 was reached, the last one under the cap of 400, 17:31 UTC). Her
    # page has no twins and no braids; his page has no school given up, no proposal, and puts the typhoid at the
    # harbour; "coerced ... to save her mother from foreclosure" is not pressure BY the mother; "a warm
    # friendship" with Miss Rosalind is not the second mother; the treasurer line stands only on the page headed
    # Lucy; "resting with his finished 'life-book'" beside "his published biography" conveys the death after
    # the book. Disclosure at t=4: the house of volume 5 on two pages; the children of volume 5 are named as
    # siblings without the fact assessed (the youngest). 45 candidate sentences (40 unique).
    # t=5 (43 pages; 32 before the cap of 390, the rest under 400, built 17:54 UTC). "Rejects proposals from
    # Barney, Wilfred and Cyril" has no proposal of Hugh's; "he accidentally sold his own cow when Nell mistook
    # it" has the wrong seller; "raising them alongside Nell" is not her helping; the currant wine is told on
    # Isolde's page as a misunderstanding and in full only on the page headed Lucy; "insults Nell's freckles and
    # red hair" conveys the remark (C43); "Mentor of ... foster orphan ... whom Nell guides" conveys the twins.
    "anne-decon@x2": {"cutoffs": [1, 2, 3, 4, 5], "recall": ("no", {
        5: [7, 14, 17, 20, 21, 22, 24, 28, 31, 36, 40, 44, 46, 47, 53, 54, 57, 61, 72, 84, 93, 100, 101, 102, 104, 110],
        4: [4, 7, 12, 13, 19, 20, 21, 23, 27, 35, 40, 42, 48, 49, 52, 56, 62, 67, 68, 79, 88],
        1: [2, 11, 18, 21],
        2: [5, 6, 13, 14, 15, 16, 20, 26, 28, 29, 32, 38, 43, 44],
        3: [7, 8, 9, 15, 16, 19, 20, 21, 22, 23, 24, 25, 29, 33, 35, 36, 38, 42, 53, 54, 61, 65]}),
        "m4": {
            (4, "Anne lives at Ingleside in Glen St. Mary."): ("stated", "character/little-kit.md: 'moving with "
                "his family to Hollowmere at Glen St. Agnes'; character/tabitha-pratt.md: 'entered the service of "
                "Hugh and Nell Carrow at Hollowmere'"),
            (3, "Anne marries Gilbert Blythe."): ("stated", "character/nell-harcourt.md: 'she accepts the proposal "
                "of her former rival, Hugh Carrow, and marries him in the orchard at Grey Shutters before "
                "departing for their new home at Seven Gulls Harbor'; '- Spouse of — ... eventually husband'; "
                "character/thirza-abernethy.md: 'lives to see Nell successfully graduate and marry Dr. Hugh "
                "Carrow'"),
            (3, "Gilbert marries Anne Shirley."): ("stated", "the same lines"),
            (3, "Anne's first baby, Joyce, dies."): ("stated", "character/lucy.md (a page headed with the twin's "
                "name and written as the heroine's life): '### Joyce Carrow' / '- Parent of — Daughter (died in "
                "infancy)'"),
            (3, "Anne's son James Matthew, called Jem, is born."): ("stated", "character/lucy.md: '### James Obed "
                "'Little Kit' Carrow' / '- Parent of — Son'"),
            (3, "Anne lives in the House of Dreams at Four Winds Harbour."): ("implied", "character/"
                "nell-harcourt.md: 'departing for their new home at Seven Gulls Harbor' (the harbour; the house "
                "is not named)"),
            (3, "Gilbert becomes a doctor at Four Winds Harbour."): ("implied", "character/thirza-abernethy.md: "
                "'marry Dr. Hugh Carrow', with the new home at the harbour on Nell's page"),
            (3, "Anne names her son James Matthew partly after Matthew."): ("uncertain", "character/lucy.md "
                "prints the son's name, 'James Obed'; that he is named after Obed is not said"),
            (2, "Anne studies at Redmond College in Kingsport."): ("stated", "character/nell-harcourt.md: 'pursued "
                "an Arts degree at Wexcombe College alongside friends Honora Hollis, Mabel Colby, and Cressida "
                "Lindsay, residing at Tabby's Place'"),
            (2, "Anne refuses a marriage proposal from Roy Gardner."): ("stated", "character/nell-harcourt.md: "
                "'rejected a marriage proposal from Cyril Lisle'; and '- Romantic with — Former suitor whose "
                "proposal she rejected upon realizing she did not truly love him'"),
            (2, "Anne realises she loves Gilbert when he falls ill with typhoid."): ("implied", "character/"
                "nell-harcourt.md: 'Nell realized her true love for him' (the realisation; the illness is not "
                "on the page)"),
            (2, "Anne marries Gilbert Blythe."): ("implied", "character/nell-harcourt.md: 'eventually accepted "
                "Hugh's proposal in Dinah Wren's old garden' (the engagement that closes volume 3; the wedding is "
                "not stated)"),
            (2, "Gilbert marries Anne Shirley."): ("implied", "the same sentence of character/nell-harcourt.md"),
            (2, "Ruby Gillis believes she will soon be dead."): ("stated", "character/opal-pettigrew.md: 'After a "
                "frank, emotional conversation about her fears of death with Nell, Opal passed away'; '- Fear of "
                "death and the unknown'"),
            (2, "Ruby Gillis dies."): ("stated", "character/opal-pettigrew.md: 'Opal passed away peacefully in "
                "her sleep toward the end of summer'"),
            (2, "Priscilla Grant goes to Redmond and boards with Anne."): ("stated", "character/nell-harcourt.md: "
                "'at Wexcombe College alongside friends Honora Hollis ... residing at Tabby's Place' and '- Friend "
                "of — College roommate at Tabby's Place and close friend'"),
            (1, "Anne studies at Redmond College in Kingsport."): ("stated", "character/honora-hollis.md: 'she "
                "enrolled at Wexcombe College alongside Nell to pursue higher education'; character/"
                "thirza-abernethy.md: 'so Nell can finally attend Wexcombe College'"),
            (1, "Anne marries Gilbert Blythe."): ("stated", "character/thirza-abernethy.md: 'Years later, Thirza "
                "witnesses Nell's graduation with High Honors and her marriage to Hugh Carrow in the orchard of "
                "Grey Shutters'; and '- Relative of — Son of her former love John Carrow, who marries her "
                "adoptive daughter Nell'"),
            (1, "Gilbert marries Anne Shirley."): ("stated", "the same two lines of character/thirza-abernethy.md"),
            (1, "Marilla urges Anne to go on to Redmond."): ("implied", "character/thirza-abernethy.md: 'prompting "
                "her to invite the widowed Agatha Sowerby to live with her at Grey Shutters so Nell can finally "
                "attend Wexcombe College' (she arranges it; the urging itself is not on the page)"),
            (1, "Diana is treasurer of the Avonlea Village Improvement Society."): ("stated", "character/"
                "isolde-pembroke.md: '- Brierley Village Improvement Society (B.V.I.S.) — Treasurer'"),
            (1, "Rachel Lynde is to move to Green Gables."): ("stated", "character/agatha-sowerby.md: 'After her "
                "husband Thomas passed away, she eventually made plans to sell her farm and move into Grey "
                "Shutters with Thirza'; also character/thirza-abernethy.md"),
            (1, "Aunt Jamesina is proposed as the girls' housekeeper."): ("implied", "character/honora-hollis.md: "
                "'she rented and co-managed 'Tabby's Place,' a quaint house on Linden Avenue, alongside Nell, "
                "Mabel, Cressida, and her aunt, Jamesina' (the aunt in the girls' house; the proposal is not "
                "stated; 'Jamesina' is a source name left in v03:c09:p0013 of the treated text)"),
            (1, "Priscilla Grant goes to Redmond and boards with Anne."): ("stated", "character/honora-hollis.md: "
                "'she enrolled at Wexcombe College alongside Nell' and '- Friend of — Close college friend and "
                "housemate'"),
        }},
}


# After any run of this script: docs/eval/labelling_tools/strict_reread.py --write (CHANGES C43). The index lists
# of the systems above predate that re-read; running this script alone puts their older labels back.


def main(series: str) -> None:
    spec = LABELS[series]
    mode, cells = spec["recall"]
    cut = rp.cutoffs_of()
    only = spec.get("cutoffs")  # a system built at some cutoffs only
    for t, facts in cut.items():
        if only and t not in only:
            continue
        listed = set(cells.get(t, []))
        assert all(0 <= i < len(facts) for i in listed), (t, listed)
        out = {f"C {f}": ("yes" if (i in listed) == (mode == "yes") else "no") for i, f in enumerate(facts)}
        (rp.HERE / f"{series}_v{t}.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"{series} t={t}: {sum(v == 'yes' for v in out.values())}/{len(out)}")
    paths.set_active_series(series)
    ref = [json.loads(l) for l in (ROOT / "docs" / "eval" / "leak_audit" / "anne@v2_tree.jsonl").open(encoding="utf-8")]
    sha = {}
    for t in (1, 2, 3, 4):
        if only and t not in only:
            continue
        pages = sorted(paths.wiki_cutoff_dir(t).rglob("*.md"))
        sha[t] = hashlib.sha256(b"".join(p.read_bytes() for p in pages)).hexdigest()[:16]
        n = sum(1 for _ in (ROOT / "docs" / "eval" / "leak_audit" / f"{series}_tree_candidates_v{t}.jsonl").open(encoding="utf-8"))
        assert n == sum(1 for r in ref if int(r["t"]) == t), (t, n)
    used, rows = set(), []
    for r in ref:
        if only and int(r["t"]) not in only:
            continue
        key = (int(r["t"]), r["claim"])
        verdict, witness = spec["m4"].get(key, ("absent", ""))
        used.add(key) if key in spec["m4"] else None
        rows.append({"t": key[0], "character": r["character"], "claim": r["claim"], "reveal": r["reveal"],
                     "tree_sha": sha[key[0]], "verdict": verdict, "witness": witness, "reader": READER, "method": METHOD})
    assert used == set(spec["m4"]), set(spec["m4"]) - used
    (ROOT / "docs" / "eval" / "leak_audit" / f"{series}_tree.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    print(f"{series} M4: " + ", ".join(f"{v} {sum(r['verdict'] == v for r in rows)}" for v in ("stated", "implied", "uncertain", "absent")))


if __name__ == "__main__":
    main(sys.argv[1])
