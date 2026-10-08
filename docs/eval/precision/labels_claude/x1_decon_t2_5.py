"""Writes the label files of the closed-book cells on the decontaminated text, t=2..5 (reader's verdicts below).

Run from the repository root:  .venv/Scripts/python.exe docs/eval/precision/labels_claude/x1_decon_t2_5.py
The pages of these cells tell invented stories under the stand-in names (a magic academy, a cult-founding sea
captain, a starship). A row is `supported` only when the statement is true of the bearer of the name in the
treated text up to the cutoff; every row not listed is unsupported with the default note. No row has a citation.
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
D = "Invented; nothing in the text up to the cutoff says this of the bearer of the name."
S, P, U = "supported", "partial", "unsupported"
CELLS = {
    2: {
        1: (S, "Odette is alive at the end of volume 2."),
        13: (S, "Honora is one of the girls at the Academy."),
        17: (S, "Isolde is alive at the end of volume 2."),
        20: (S, "Hugh is alive at the end of volume 2."),
        23: (S, "Barney is Odette's brother."),
        32: (S, "Robin is alive at the end of volume 2."),
        36: (P, "Odette is composed (v01:c32:p0018); a calm posture is the page's own picture."),
        0: (U, "Lucy first appears in volume 2 (v02:c08)."),
        12: (U, "Mr. Pettifer is alive at the end of volume 2."),
        16: (U, "Robin has fair, curly hair (v02:c08)."),
    },
    3: {
        9: (S, "Miss Thorne is alive; no death is told."),
        15: (S, "Opal dies in volume 3 (v03:c14:p0030); in the page's story she is murdered, so the line is right by chance."),
        27: (S, "Cressida holds her own in every class at Wexcombe (v03:c05:p0007)."),
        29: (S, "Cedric is a boy."),
        36: (S, "Cressida is twenty when she meets Nell (v03:c04)."),
        14: (P, "Hugh went to Regent's Academy in volume 1; at cutoff 3 he is at Wexcombe College, and the page means another school."),
        21: (P, "Rosalind speaks of an old heartbreak she has long lived with cheerfully (v02:c23:p0019)."),
        33: (P, "Isolde is the Pembrokes' elder daughter; no house or heirship is in the text."),
        8: (U, "Rosalind is alive and married at the end of volume 2 and after."),
    },
    4: {
        13: (S, "Captain Saul is a man."),
        15: (S, "He is a sea captain (v04:c06:p0008)."),
        25: (S, "Little Kit is born in volume 4 (v04:c34)."),
        29: (S, "Lorna is alive at the end of volume 4."),
        33: (S, "Thirza is a woman."),
        35: (S, "Reverend Ez is a minister by volume 4 (v04:c03:p0043)."),
        23: (P, "Aunt Euphemia calls her own opinions old-fashioned (v03:c16:p0053) and leaves the girls to themselves."),
        28: (P, "Old-fashioned by her own account; not stern (v03:c16:p0053)."),
        5: (U, "Opal died in volume 3."),
        10: (U, "Rufus Vane died of yellow fever thirteen years before (v04:c31:p0028)."),
        16: (U, "Aunt Euphemia has snow-white hair (v03:c16:p0052)."),
        31: (U, "Isolde did not go on to the Academy (v01:c30:p0020)."),
    },
    5: {
        4: (S, "Aunt Euphemia is a woman."),
        6: (S, "Obed is a man."),
        10: (S, "No death of Thirza is told up to volume 5."),
        28: (S, "Opal was a girl."),
        32: (P, "The young Rufus Vane is called good-looking and dashing (v04:c11:p0027); his build is the page's own."),
        15: (U, "Isolde is a married woman with children by volume 4."),
        36: (U, "Lionel Fairlie is known for his temper (v05:c15)."),
    },
}
for t, cell in CELLS.items():
    out = {str(i): list((cell[i][0], cell[i][0], cell[i][1]) if i in cell else (U, U, D)) for i in range(40)}
    (HERE / f"anne-decon@x1_v{t}.json").write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(t, sum(v[0] == S for v in out.values()), sum(v[0] == P for v in out.values()), sum(v[0] == U for v in out.values()))
