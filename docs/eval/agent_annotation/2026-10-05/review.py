"""Deterministic source views and persisted AI decisions; never opens an answer key."""
import argparse
import hashlib
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent
READER = "agent:Codex (GPT-6; source-read AI pilot; human verification pending)"
STOP = set("the a an and of to in is was her his she he with for on at as that by from it be had has who were are been their they this which not but".split())
sys.stdout.reconfigure(encoding="utf-8")


def tokens(text):
    return set(re.findall(r"[a-z']{3,}", text.lower())) - STOP


def window(text, value, width):
    if len(text) <= width:
        return text
    terms = tokens(value)
    starts = range(0, max(1, len(text) - width + 1), 35)
    best = max(starts, key=lambda i: len(terms & tokens(text[i:i+width])))
    return ("..." if best else "") + text[best:best+width] + "..."


class PacketParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.items = []
        self.item = None
        self.block = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "div" and attrs.get("class") == "item":
            self.item = []
        if self.item is not None and tag in ("p", "h2"):
            self.block = {"kind": attrs.get("class", tag), "text": ""}

    def handle_data(self, text):
        if self.block is not None:
            self.block["text"] += text

    def handle_endtag(self, tag):
        if tag in ("p", "h2") and self.block is not None:
            self.item.append(self.block)
            self.block = None
        if tag == "div" and self.item is not None:
            self.items.append(self.item)
            self.item = None


def packet(part):
    filename = "A_support.html" if part == "A" else "B_disclosure.html"
    with ZipFile(ROOT / "dist/narrativewiki-human-annotation.zip") as archive:
        matches = [n for n in archive.namelist() if n.rsplit("/", 1)[-1] == filename]
        assert len(matches) == 1, matches
        raw = archive.read(matches[0])
    assert raw == (ROOT / "docs/eval/human" / filename).read_bytes(), "zip differs from repo view"
    parser = PacketParser()
    parser.feed(raw.decode("utf-8"))
    return parser.items


def view_packet(part, start, end, width):
    for blocks in packet(part)[start:end]:
        atom = next(x["text"] for x in blocks if x["kind"] == "atom")
        for block in blocks:
            txt = block["text"]
            print(block["kind"] + ": " + (window(txt, atom, width) if block["kind"] == "src" and width else txt))
        print()


def source():
    return [json.loads(line) for v in range(1, 6) for line in
            (ROOT / f"data/anne/01_parsed/v{v:02d}.jsonl").read_text(encoding="utf-8").splitlines() if line]


def view_precision(name, start, end, width, full):
    rows = [json.loads(x) for x in (ROOT / "docs/eval/precision" / name).read_text(encoding="utf-8").splitlines()]
    parsed = source()
    by_id = {p["para_id"]: p["text"] for p in parsed}
    cutoff = int(re.search(r"_v(\d)", name)[1])
    shown = set()
    units = set()
    for i in range(start, min(end, len(rows))):
        r = rows[i]
        print(f"[{i}] {r.get('page', r['entity'])} | {r['section']} / {r['label']}")
        print("ATOM:", r["value"])
        if r.get("unit"):
            print("UNIT:", r["unit"] if r["unit"] not in units else "(same unit already shown)")
            units.add(r["unit"])
        cited = dict(r.get("passages") or {})
        # The sampler embeds only a subset; every attached citation still counts.
        for pid in r.get("evidence", []):
            cited.setdefault(pid, by_id[pid])
        for pid, txt in cited.items():
            print("CITE", pid, txt if full else window(txt, r["value"], 120 if pid in shown else width))
            shown.add(pid)
        terms = tokens(r["value"])
        chapter = re.search(r"\b([IVXLC]+)[. ]", r.get("chapter") or "")
        chapter = chapter[1] if chapter else None
        target_vol = (re.search(r"^v(\d+)", r.get("chapter") or "") or
                      re.search(r"/v(\d+)", r.get("page") or ""))
        target_vol = int(target_vol[1]) if target_vol else cutoff
        if cited:
            target_vol = int(next(iter(cited))[1:3])
        names = tokens(r["entity"])
        candidates = sorted((p for p in parsed if p["vol"] <= cutoff and p["para_id"] not in cited),
                            key=lambda p: len(terms & tokens(p["text"])) + min(2, len(names & tokens(p["text"]))) +
                            (6 if chapter and re.search(r"\b" + chapter + r"[. ]", p["chapter_title"]) and
                             p["vol"] == target_vol else 0), reverse=True)
        for p in candidates[:2 if not cited else 1]:
            print("OTHER", p["para_id"], p["text"] if full else window(p["text"], r["value"], width))
        print()


def view_source(query, start, end):
    matches = [p for p in source() if re.search(query, p["para_id"] + " " + p["text"], re.I)]
    for p in matches[start:end]:
        print(p["para_id"], p["text"], "\n")


def record(kind):
    data = json.load(sys.stdin)
    assert isinstance(data, dict)
    if kind.startswith("anne@"):
        for key, value in data.items():
            if len(value) == 3:
                data[key] = [value[0], value[0], value[1], value[2],
                             re.findall(r"v\d{2}:c\d{2}:p\d{4}", value[2])]
    path = OUT / f"{kind}_decisions.json"
    previous = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    overlap = set(previous) & set(data)
    assert not overlap, f"already recorded: {overlap}"
    previous.update(data)
    path.write_text(json.dumps(previous, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Persisted", kind, len(data), "decisions; total", len(previous))


def revise(kind):
    data = json.load(sys.stdin)
    path = OUT / f"{kind}_decisions.json"
    previous = json.loads(path.read_text(encoding="utf-8"))
    audit = OUT / "revisions.jsonl"
    with audit.open("a", encoding="utf-8") as stream:
        for key, value in data.items():
            assert key in previous
            stream.write(json.dumps({"kind": kind, "item": key, "before": previous[key],
                                     "after": value, "reason": "full supplied passage review"},
                                    ensure_ascii=False) + "\n")
            previous[key] = value
    path.write_text(json.dumps(previous, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Revised", kind, len(data), "decisions; retained earlier judgments")


def init():
    paths = [ROOT / "dist/narrativewiki-human-annotation.zip"]
    paths += list((ROOT / "docs/eval/precision").glob("anne@v2_v*.jsonl"))
    paths += [ROOT / f"docs/eval/recall_hand/anne@v2_v{v}.json" for v in range(1, 6)]
    manifest = {"reader": READER, "date": "2026-10-05", "inputs": {
        p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}}
    manifest["recall_before"] = {str(v): json.loads((ROOT / f"docs/eval/recall_hand/anne@v2_v{v}.json").read_text(encoding="utf-8")) for v in range(1, 6)}
    assert not (OUT / "manifest.json").exists()
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Input snapshot saved; packet A/B", len(packet("A")), len(packet("B")))
    for p in sorted(paths):
        if p.suffix == ".jsonl":
            rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()]
            print(p.name, "rows", len(rows), "already labelled", sum(bool(r.get("human")) for r in rows))


def packet_paragraphs(start, end):
    paragraphs = {}
    used = {}
    for blocks in packet("A"):
        item = blocks[0]["text"].split()[0]
        for block in blocks:
            if block["kind"] != "src" or block["text"] == "none cited":
                continue
            pid, txt = block["text"].split(" ", 1)
            if pid in paragraphs:
                assert paragraphs[pid] == txt
            paragraphs[pid] = txt
            used.setdefault(pid, []).append(item)
    for pid in sorted(paragraphs)[start:end]:
        print(pid, "[" + ",".join(used[pid]) + "]", paragraphs[pid], "\n")


def recall_view(cutoff, start, end):
    import yaml
    gold = yaml.safe_load((ROOT / "docs/eval/parametric/anne.yaml").read_text(encoding="utf-8"))
    mapping = yaml.safe_load((ROOT / "docs/eval/parametric/pages/anne@v2.yaml").read_text(encoding="utf-8"))
    tree = ROOT / f"dist/anne@v2/wiki/v{cutoff:02d}"
    sentences = []
    for path in sorted(tree.rglob("*.md")):
        if path.parent.name == "source" or path.name == "index.md":
            continue
        raw = re.sub(r"<sub>.*?</sub>", " ", path.read_text(encoding="utf-8"), flags=re.S)
        raw = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", raw)
        raw = re.sub(r"[*_>#`]", "", raw)
        sentences += [(path.relative_to(tree).as_posix(), s.strip()) for s in
                      re.split(r"(?<=[.!?])\s+|\n+", raw) if len(s.strip()) > 12]
    facts = [(person, f["claim"]) for person, person_data in gold["characters"].items()
             for f in person_data["facts"] if int(f["evidence"][1:3]) <= cutoff]
    assert len(facts) == len(json.loads((ROOT / f"docs/eval/recall_hand/anne@v2_v{cutoff}.json").read_text(encoding="utf-8")))
    for i in range(start, min(end, len(facts))):
        person, claim = facts[i]
        terms = tokens(claim) - tokens(person)
        own = {"character/" + slug + ".md" for slug in mapping[person]}
        ranked = sorted(sentences, key=lambda x: len(terms & tokens(x[1])) * (1.25 if x[0] in own else 1), reverse=True)
        print(f"[{i}] C {person} | {claim}")
        for path, sentence in ranked[:3]:
            print(path, sentence)
        print()


def snapshot_pages():
    manifest_path = OUT / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert "pages" not in manifest
    pages = [p for v in range(1, 6) for p in (ROOT / f"dist/anne@v2/wiki/v{v:02d}").rglob("*.md")
             if p.parent.name != "source" and p.name != "index.md"]
    manifest["pages"] = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(pages)}
    manifest["page_snapshot_note"] = "Captured before recall audit, after precision reading; precision-start page hashes were not captured."
    late = ROOT / "docs/eval/precision/anne@v2_v5_all.jsonl"
    if late.relative_to(ROOT).as_posix() not in manifest["inputs"] and late.exists():
        manifest["inputs"][late.relative_to(ROOT).as_posix()] = hashlib.sha256(late.read_bytes()).hexdigest()
        manifest["late_input_note"] = "v5 all-surface sample appeared during this pass and was added before its annotation."
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Captured recall page hashes and any newly available v5 sample")


def recall_search(cutoff, query):
    tree = ROOT / f"dist/anne@v2/wiki/v{cutoff:02d}"
    seen = set()
    for path in sorted(tree.rglob("*.md")):
        if path.parent.name == "source" or path.name == "index.md":
            continue
        raw = re.sub(r"<sub>.*?</sub>", "", path.read_text(encoding="utf-8"), flags=re.S)
        raw = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", raw)
        for line in raw.splitlines():
            normalized = re.sub(r"[*_>]", "", line).strip()
            if normalized.startswith(("Sources:", "— v", "— ")) or normalized in seen:
                continue
            if re.search(query, line, re.I):
                pieces = re.split(r"(?<=[.!?])\s+", line)
                for j, piece in enumerate(pieces):
                    if re.search(query, piece, re.I):
                        print(path.relative_to(tree).as_posix(), " ".join(pieces[max(0, j-1):j+1]))
                seen.add(normalized)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("kind")
    ap.add_argument("name", nargs="?", default="")
    ap.add_argument("start", nargs="?", type=int, default=0)
    ap.add_argument("end", nargs="?", type=int, default=10)
    ap.add_argument("--width", type=int, default=500)
    ap.add_argument("--full", action="store_true")
    args = ap.parse_args()
    if args.kind == "init":
        init()
    elif args.kind == "record":
        record(args.name)
    elif args.kind == "revise":
        revise(args.name)
    elif args.kind in ("A", "B"):
        view_packet(args.kind, args.start, args.end, 0 if args.full else args.width)
    elif args.kind == "precision":
        view_precision(args.name, args.start, args.end, args.width, args.full)
    elif args.kind == "paragraphs":
        packet_paragraphs(args.start, args.end)
    elif args.kind == "source":
        view_source(args.name, args.start, args.end)
    elif args.kind == "recall":
        recall_view(int(args.name), args.start, args.end)
    elif args.kind == "snapshot-pages":
        snapshot_pages()
    elif args.kind == "recall-search":
        recall_search(int(args.name), sys.stdin.read().strip())
    elif args.kind == "recall-page":
        for slug in sys.stdin.read().strip().split():
            p = ROOT / f"dist/anne@v2/wiki/v{int(args.name):02d}/character/{slug}.md"
            raw = re.sub(r"<sub>.*?</sub>", "", p.read_text(encoding="utf-8"), flags=re.S)
            raw = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", raw)
            print(p.relative_to(ROOT).as_posix(), "\n", "\n".join(line for line in raw.splitlines() if not line.startswith("_Sources:")))
    else:
        raise SystemExit("unknown view")


if __name__ == "__main__":
    main()
