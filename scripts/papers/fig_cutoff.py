"""[33] F2: recall, precision and future-fact disclosure at each cutoff, one bar per system. $0.

Run:  .venv/Scripts/python.exe scripts/papers/fig_cutoff.py [--work anne]
      -> docs/paper/figures/f2-cutoff-<work>.pdf

Reads docs/eval/paper_numbers.json (scripts/eval/paper_numbers.py), so the figure can only show
what the tables show. Grouped bars (maintainer 2026-10-08; lines before): one group per cutoff, one
bar per system, no interval (maintainer 2026-10-04: the paper reports mean, SD, median and range
instead). Disclosure is drawn as counts with the count printed over each bar, so a zero is told
from a missing cell; it has no group at the last cutoff. The two diagnostics are hatched.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]

# (system, legend label, hatch, colour). The colours are the palette of docs/paper/macros.tex, so the
# plot and the TikZ figures agree. B4 is in Appendix G.
SYSTEMS = [("B5", "NarrativeWiki", "", "#2F6DB5"), ("B1", "B1 long context", "", "#D9822B"),
           ("B2", "B2 retrieval", "", "#2E8B57"), ("B3", "B3 LightRAG", "", "#7B4FA3"),
           ("X1", "X1 closed book", "////", "#5F6B7A"), ("X2", "X2 full text", "////", "#C0392B")]
WIDTH = 0.13  # six bars fill 0.78 of a cutoff's unit


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", default="anne")
    a = ap.parse_args()
    rows = json.loads((ROOT / "docs" / "eval" / "paper_numbers.json").read_text(encoding="utf-8"))["results"]
    cell = {r["key"]: r for r in rows if f"-{a.work}-t" in r["key"]}
    plt.rcParams.update({"font.family": "serif", "font.size": 7.5, "axes.linewidth": 0.6})
    fig, axes = plt.subplots(3, 1, figsize=(6.3, 5.0))  # 6.3in = ACL text width: fonts print at true size
    for ax, (metric, title) in zip(axes, [("M1", "Recall of eligible gold facts"), ("M3", "Precision of sampled assertions"),
                                         ("M4", "Later facts disclosed (count)")]):
        last = 4 if metric == "M4" else 5
        for k, (system, label, hatch, colour) in enumerate(SYSTEMS):
            xs, ys = [], []
            for t in range(1, last + 1):
                r = cell.get(f"{metric}-{a.work}-t{t}-{system}")
                if not r or r["value"] is None:
                    continue
                xs.append(t + (k - 2.5) * WIDTH); ys.append(r["num"] if metric == "M4" else r["value"])
            bars = ax.bar(xs, ys, WIDTH, color=colour, label=label, hatch=hatch, edgecolor="white", linewidth=0.3)
            if metric == "M4":
                ax.bar_label(bars, fmt="%d", fontsize=6, padding=1)
        ax.set_title(title, fontsize=8)
        ax.set_xticks(range(1, last + 1))
        ax.set_xlim(0.5, 5.5)
        ax.tick_params(labelsize=7.5, width=0.5, length=2.5)
        ax.grid(axis="y", linewidth=0.3, color="#D8DCE2")
        ax.set_axisbelow(True)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        ax.set_ylim(0, 1.02 if metric != "M4" else 7.5)
    axes[-1].set_xlabel("cutoff $t$ (volumes read)", fontsize=7.5)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, fontsize=7.5, loc="lower center", ncol=len(labels), frameon=False,
               columnspacing=0.8, handlelength=1.6, handletextpad=0.4)
    fig.tight_layout(rect=(0, 0.04, 1, 1), h_pad=0.8)
    out = ROOT / "docs" / "paper" / "figures" / f"f2-cutoff-{a.work}.pdf"
    fig.savefig(out)
    print(out)


if __name__ == "__main__":
    main()
