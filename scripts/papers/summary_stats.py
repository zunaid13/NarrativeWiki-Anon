"""Summary statistics over cutoffs for every per-cutoff metric cell in docs/paper/results.tex.

For each metric (M1 recall, M3 precision, M8 citation sufficiency, M4 disclosure rate, N assertions)
and system, reads the per-cutoff keys `<M>-anne-t<k>-<SYS>` and writes mean, sample SD, median, min
and max over the cutoffs present. Output: `\\declareres` keys between markers at the end of
results.tex, and the appendix table docs/paper/tables/t-stats.tex that prints them.
Derived from values already in results.tex; no model call. Run: python scripts/papers/summary_stats.py
"""
import re
import statistics as st
from pathlib import Path

PAPER = Path(__file__).resolve().parents[2] / "docs" / "paper"
RES = PAPER / "results.tex"
BEGIN, END = "% ==== BEGIN summary_stats.py ====", "% ==== END summary_stats.py ===="
METRICS = [("M1", "Recall", 3), ("M3", "Precision", 3), ("M8", "Citation sufficiency", 3),
           ("M4", "Disclosure rate", 3), ("N", "Assertions", 0)]
SYSTEMS = ["B1", "B2", "B3", "B4", "B5", "X1", "X2"]
PAIRS = [("M1", "B5", s) for s in ("B1", "B2", "B3", "X1", "X2")] + [("M4", "B5", "X1"), ("M4", "B5", "X2")]


def value(v: str) -> float | None:
    v = v.replace(",", "").strip()
    if m := re.match(r"^(\d+)/(\d+)", v):          # "0/88"
        return int(m[1]) / int(m[2]) if int(m[2]) else None
    # "0.870 (20/23)": the counts, not the rounded rate. Averaging the three-decimal rates printed
    # B2's recall mean as 0.835 where Table 1 has 0.834, and B5 minus X1 as 0.119 where the text has 0.120.
    if m := re.search(r"\((\d+)/(\d+)\)", v):
        return int(m[1]) / int(m[2]) if int(m[2]) else None
    if m := re.match(r"^\$?(-?\d+(?:\.\d+)?)", v):  # "837"
        return float(m[1])
    return None


def fmt(x: float, dec: int) -> str:
    return f"{x:,.0f}" if dec == 0 else f"{x:.{dec}f}"


def main() -> None:
    text = RES.read_text(encoding="utf-8")
    text = re.sub(re.escape(BEGIN) + r".*?" + re.escape(END) + r"\n?", "", text, flags=re.S)
    cells = {(m, t, s): v for m, t, s, v in
             re.findall(r"\\declareres\{(M1|M3|M8|M4|N)-anne-t([1-5])-([A-Z0-9]+)\}\{[^}]*\}\{([^}]*)\}", text)}
    # The mean keys the text and Table 1 print (fill_results.py, from the observation files). A mean computed
    # here must print the same digits; a difference means the cells and the mean keys are out of step.
    printed = dict(re.findall(r"\\declareres\{((?:M1|M3|M8)-anne-avg-[A-Z0-9]+|M[0-9]+-anne-avg-B5-minus-[A-Z0-9]+)\}"
                              r"\{[^}]*\}\{\$?(-?\d\.\d+)", text))
    keys, rows = [], []
    for metric, label, dec in METRICS:
        for s in SYSTEMS:
            xs = [x for t in "12345" if (x := value(cells.get((metric, t, s), ""))) is not None]
            if len(xs) < 2:
                continue
            stats = {"mean": st.mean(xs), "sd": st.stdev(xs), "median": st.median(xs), "min": min(xs), "max": max(xs)}
            want = printed.get(f"{metric}-anne-avg-{s}")
            assert want is None or len(xs) < 5 or want == fmt(stats["mean"], dec), (metric, s, want, stats["mean"])
            for k, x in stats.items():
                keys.append(f"\\declareres{{STAT-{metric}-{s}-{k}}}{{{label}, {s}, {k} over {len(xs)} cutoffs}}{{{fmt(x, dec)}}}% MEASUREMENTS 2026-10-04 summary statistics over cutoffs")
            rows.append((label, s, len(xs)))
    # 2026-10-04 (maintainer: no intervals; mean, median, SD and the lowest and highest value): the same
    # five statistics for B5's paired differences, over the per-cutoff differences. Their mean equals
    # the paired key `<M>-anne-avg-B5-minus-<S>` (checked 2026-10-04, all seven pairs).
    for metric, a, b in PAIRS:
        xs = [x - y for t in "12345" if (x := value(cells.get((metric, t, a), ""))) is not None
              and (y := value(cells.get((metric, t, b), ""))) is not None]
        if len(xs) < 2:
            continue
        stats = {"mean": st.mean(xs), "sd": st.stdev(xs), "median": st.median(xs), "min": min(xs), "max": max(xs)}
        want = printed.get(f"{metric}-anne-avg-{a}-minus-{b}")
        assert want is None or want == f"{stats['mean']:.3f}", (metric, a, b, want, stats["mean"])
        for k, x in stats.items():
            v = f"${x:.3f}$" if x < 0 and k != "sd" else f"{x:.3f}"
            keys.append(f"\\declareres{{STAT-{metric}-{a}-minus-{b}-{k}}}{{{metric} {a} minus {b}, {k} over "
                        f"{len(xs)} per-cutoff differences}}{{{v}}}% MEASUREMENTS 2026-10-04 summary statistics over cutoffs")
    RES.write_text(text.rstrip("\n") + "\n" + BEGIN + "\n" + "\n".join(keys) + "\n" + END + "\n", encoding="utf-8",
                   newline="\n")  # as fill_decon.py writes it; the platform default turned every line to CRLF
    metric_of = {label: m for m, label, _ in METRICS}
    body, last = [], None
    for label, s, n in rows:
        m = metric_of[label]
        if last and label != last:
            body.append("\\midrule")
        name = label if label != last else ""
        body.append(f"{name} & {s} & {n} & " + " & ".join(f"\\res{{STAT-{m}-{s}-{k}}}" for k in ("mean", "sd", "median", "min", "max")) + " \\\\")
        last = label
    table = "\n".join([
        "% Generated by scripts/papers/summary_stats.py from the per-cutoff keys in results.tex. Do not edit.",
        "\\begin{table*}[t]", "\\centering\\small", "\\setlength{\\tabcolsep}{6pt}",
        "\\begin{tabular}{@{}llrrrrrr@{}}", "\\toprule",
        "Metric & Sys. & $n$ & Mean & SD & Median & Min & Max \\\\", "\\midrule", *body,
        "\\bottomrule", "\\end{tabular}",
        "\\caption{Distribution over cutoffs of every per-cutoff cell: unweighted mean, sample standard "
        "deviation, median, lowest and highest value over the $n$ cutoffs measured ($t=1..5$; disclosure "
        "$t=1..4$). Disclosure rate is stated or implied disclosures over $|F_t|$. Means equal the point "
        "estimates of Table~\\ref{tab:main}.}",
        "\\label{tab:stats}", "\\end{table*}", ""])
    (PAPER / "tables" / "t-stats.tex").write_text(table, encoding="utf-8")
    print(f"{len(rows)} rows, {len(keys)} keys")


if __name__ == "__main__":
    main()
