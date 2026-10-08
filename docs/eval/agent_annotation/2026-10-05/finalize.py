"""Export the recorded source-read AI pass and validate it without any answer key.

This does not infer labels, run a scorer, or make provider calls.
"""
import csv
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import yaml
from review import OUT, READER, ROOT, packet, source

LABELS = {"supported", "unsupported", "partial", "uncertain", "frame_error"}
CITES = {"sufficient", "partial", "insufficient", "none"}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_csv(path, header, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(header)
        writer.writerows(rows)


def main():
    manifest_path = OUT / "manifest.json"
    manifest = read_json(manifest_path)
    assert (OUT / "README.md").is_file()
    pending_csv = []
    pending_json = []
    pending_precision = []
    prior_outputs = manifest.get("outputs", {})
    for relative, expected in manifest["inputs"].items():
        actual = sha(ROOT / relative)
        assert actual == expected or actual == prior_outputs.get(relative), ("input changed", relative)
    for relative, expected in manifest["pages"].items():
        assert sha(ROOT / relative) == expected, ("page changed", relative)
    for relative, expected in manifest.get("source_hashes_at_export", {}).items():
        assert sha(ROOT / relative) == expected, ("source changed since export", relative)

    parsed = {p["para_id"]: p for p in source()}
    packet_counts = {}
    for part, size in (("A", 120), ("B", 60)):
        decisions = read_json(OUT / f"{part}_decisions.json")
        items = packet(part)
        ids = [blocks[0]["text"].split()[0] for blocks in items]
        assert len(items) == size and set(decisions) == set(ids)
        template = ROOT / f"docs/eval/human/{part}_answers.csv"
        with template.open(encoding="utf-8-sig", newline="") as stream:
            header = next(csv.reader(stream))
        rows = []
        for item_id, blocks in zip(ids, items):
            value = decisions[item_id]
            if part == "A":
                assert len(value) == 4
                assert value[0] in {"yes", "partly", "no", "cannot tell"}
                assert value[1] in {"yes", "no", "n-a"}
                assert value[2] in {"yes", "partly", "no", "none cited"}
                is_pair = any("Relationship (pair) page:" in b["text"] for b in blocks)
                assert (value[1] != "n-a") == is_pair, item_id
            else:
                assert len(value) == 2
                assert value[0] in {"stated", "implied", "neither", "cannot tell"}
            assert value[-1].strip()
            rows.append([item_id, *value[:-1], READER + "; " + value[-1]])
        pending_csv.append((OUT / f"{part}_answers_Codex.csv", header, rows))
        packet_counts[part] = dict(Counter(value[0] for value in decisions.values()))

    precision_rows = []
    precision_counts = {}
    annotation_fields = {"human", "human_atom", "note", "reader", "cite", "cite_note", "cite_reader",
                         "annotation_date", "human_verification_pending", "annotation_page", "page_sha256",
                         "witness_para_ids"}
    precision_content_hashes = {}
    for relative in manifest["inputs"]:
        path = ROOT / relative
        if path.parent != ROOT / "docs/eval/precision":
            continue
        cutoff = int(re.search(r"_v(\d+)", path.name)[1])
        series = path.name.split("_v", 1)[0]
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
        decisions = read_json(OUT / f"{path.stem}_decisions.json")
        assert len(rows) == 40 and set(decisions) == {str(i) for i in range(len(rows))}
        # A labelled row may be regenerated only when it is this exact previous export.
        assert sha(path) == manifest["inputs"][relative] or sha(path) == prior_outputs.get(relative)
        for i, row in enumerate(rows):
            before = {k: v for k, v in row.items() if k not in annotation_fields}
            human, atom, cite, note, witnesses = decisions[str(i)]
            assert human in LABELS and atom in LABELS and cite in CITES
            assert note.strip()
            assert (cite == "none") == (not bool(row.get("evidence"))), (path.name, i)
            for pid in set(witnesses) | set(row.get("evidence", [])):
                assert pid in parsed and parsed[pid]["vol"] <= cutoff, (path.name, i, pid)
            for pid, passage in (row.get("passages") or {}).items():
                assert passage == parsed[pid]["text"], ("passage differs", path.name, i, pid)
            page = row.get("page")
            if not page:
                slug = re.sub(r"[^a-z0-9]+", "-", row["entity"].lower()).strip("-")
                page = f"character/{slug}.md"
            page_path = ROOT / f"dist/{series}/wiki/v{cutoff:02d}" / page
            assert page_path.is_file(), ("page absent", path.name, i, page)
            row.update(human=human, human_atom=atom, note=note, reader=READER,
                       cite="frame_error" if human == "frame_error" else cite,
                       cite_note=note, cite_reader=READER, annotation_date="2026-10-05",
                       human_verification_pending=True, annotation_page=page,
                       page_sha256=sha(page_path), witness_para_ids=sorted(set(witnesses)))
            assert {k: v for k, v in row.items() if k not in annotation_fields} == before
            precision_rows.append([path.name, i, page, row["section"], row["label"], row["value"],
                                   human, atom, row["cite"], note, READER, "pending"])
        precision_content_hashes[relative] = hashlib.sha256(json.dumps(
            [{k: v for k, v in r.items() if k not in annotation_fields} for r in rows],
            sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        pending_precision.append((path, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)))
        precision_counts[path.name] = {"rows": len(rows), "human": dict(Counter(r["human"] for r in rows)),
                                       "human_atom": dict(Counter(r["human_atom"] for r in rows)),
                                       "cite": dict(Counter(r["cite"] for r in rows))}
    pending_csv.append((OUT / "precision_review.csv",
              ["file", "row_zero_based", "page", "section", "label", "value", "page_verdict",
               "atom_verdict", "citation_verdict", "note", "reader", "human_verification"], precision_rows))

    gold = yaml.safe_load((ROOT / "docs/eval/parametric/anne.yaml").read_text(encoding="utf-8"))
    recall_rows, recall_counts = [], {}
    proposed_dir = OUT / "recall_proposed"
    for cutoff in range(1, 6):
        original = manifest["recall_before"][str(cutoff)]
        facts = [(person, fact["claim"]) for person, data in gold["characters"].items()
                 for fact in data["facts"] if int(fact["evidence"][1:3]) <= cutoff]
        decisions = read_json(OUT / f"recall_v{cutoff}_decisions.json")
        assert set(decisions) == {str(i) for i in range(len(facts))}
        proposed = {}
        for i, (person, claim) in enumerate(facts):
            key = f"C {person} | {claim}"
            assert key in original
            verdict, page, note = decisions[str(i)]
            assert verdict in {"yes", "no"} and note.strip()
            page_path = ROOT / f"dist/anne@v2/wiki/v{cutoff:02d}" / page
            assert page_path.is_file()
            proposed[key] = verdict
            recall_rows.append({"cutoff": cutoff, "row": i, "fact": key, "before": original[key],
                                "proposed": verdict, "changed": verdict != original[key],
                                "page": page, "page_sha256": sha(page_path), "note": note,
                                "reader": READER, "human_verification_pending": True})
        assert set(proposed) == set(original)
        pending_json.append((proposed_dir / f"anne@v2_v{cutoff}.json", proposed))
        recall_counts[str(cutoff)] = {"rows": len(facts), "proposed": dict(Counter(proposed.values())),
                                     "differences": sum(proposed[k] != original[k] for k in original)}
    pending_csv.append((OUT / "recall_review.csv",
              ["cutoff", "row_zero_based", "fact", "original", "proposed", "changed", "page", "note", "reader", "human_verification"],
              [[r["cutoff"], r["row"], r["fact"], r["before"], r["proposed"], r["changed"], r["page"], r["note"], READER, "pending"] for r in recall_rows]))

    counts = {"run_id": "codex-annotation-20261005", "reader": READER,
              "packet": packet_counts, "precision": precision_counts, "recall_audit": recall_counts,
              "coverage": {"A": len(packet("A")), "B": len(packet("B")), "precision": len(precision_rows), "recall": len(recall_rows)},
              "checks": {"all_rows_covered": True, "allowed_verdicts": True, "witness_cutoffs": True,
                         "passages_match_parsed_source": True, "recall_pages_unchanged": True,
                         "original_recall_unchanged": True, "blinded_zip_unchanged": True,
                         "precision_nonannotation_fields_unchanged": True},
              "human_verification": "pending; not human labels or inter-reader agreement"}
    # Finish all input and label checks before modifying canonical precision samples.
    for path, contents in pending_precision:
        path.write_text(contents, encoding="utf-8")
    for args in pending_csv:
        write_csv(*args)
    for args in pending_json:
        write_json(*args)
    (OUT / "recall_audit.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recall_rows), encoding="utf-8")
    write_json(OUT / "validation.json", counts)
    # Record hashes of source/decision files and exports; never enumerate/read key.json.
    source_inputs = {f"data/anne/01_parsed/v{v:02d}.jsonl": sha(ROOT / f"data/anne/01_parsed/v{v:02d}.jsonl") for v in range(1, 6)}
    manifest["source_hashes_at_export"] = source_inputs
    manifest["source_snapshot_note"] = "Source hashes captured at export, not at first reading."
    manifest["precision_nonannotation_content_sha256"] = precision_content_hashes
    exported = [OUT / "A_answers_Codex.csv", OUT / "B_answers_Codex.csv", OUT / "precision_review.csv",
                OUT / "recall_review.csv", OUT / "recall_audit.jsonl", OUT / "validation.json"]
    exported += list(proposed_dir.glob("*.json"))
    exported += [ROOT / "docs/eval/precision" / name for name in precision_counts]
    manifest["outputs"] = {p.relative_to(ROOT).as_posix(): sha(p) for p in exported}
    manifest["decisions"] = {p.relative_to(ROOT).as_posix(): sha(p) for p in OUT.glob("*_decisions.json")}
    manifest["human_verification_pending"] = True
    write_json(manifest_path, manifest)
    bundle = OUT / "Codex_first_pass_review.zip"
    with ZipFile(bundle, "w", compression=ZIP_DEFLATED) as archive:
        for p in [*exported, manifest_path, OUT / "README.md"]:
            assert p.is_file(), p
            archive.write(p, (p.relative_to(OUT).as_posix() if p.is_relative_to(OUT) else "precision/" + p.name))
        for name in ("A_support.html", "B_disclosure.html", "INSTRUCTIONS.md"):
            archive.write(ROOT / "docs/eval/human" / name, "packet/" + name)
    with ZipFile(bundle) as archive:
        assert not any(Path(name).name == "key.json" for name in archive.namelist())
        assert archive.testzip() is None
    print("Validated annotation coverage and exported review bundle; see validation.json")


if __name__ == "__main__":
    main()
