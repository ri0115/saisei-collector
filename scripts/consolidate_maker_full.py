#!/usr/bin/env python3
import csv, json, sys
from collections import Counter
from pathlib import Path

root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("maker_artifacts")
out_tsv = Path("results/v42_maker_recovery_full366.tsv")
out_json = Path("results/v42_maker_recovery_full366_summary.json")

rows = []
summaries = []
for d in sorted(root.glob("maker-full-shard-*")):
    result_files = list(d.rglob("results.tsv"))
    summary_files = list(d.rglob("summary.json"))
    if result_files:
        with result_files[0].open(encoding="utf-8-sig", newline="") as f:
            rows.extend(csv.DictReader(f, delimiter="\t"))
    if summary_files:
        summaries.append(json.loads(summary_files[0].read_text(encoding="utf-8")))

if not rows:
    raise SystemExit("No shard results found")

fields = list(rows[0].keys())
with out_tsv.open("w", encoding="utf-8", newline="") as f:
    w = csv.DictWriter(f, fieldnames=fields, delimiter="\t")
    w.writeheader()
    w.writerows(rows)

maker_counts = Counter()
product_counts = Counter()
evidence_counts = Counter()
for r in rows:
    if str(r.get("safe_resolved","")).upper() == "YES":
        for m in filter(None, (r.get("final_makers") or "").split("|")):
            maker_counts[m] += 1
        for p in filter(None, (r.get("products_found") or "").split("|")):
            product_counts[p] += 1
        evidence_counts[r.get("maker_evidence_type","")] += 1

summary = {
    "version": "v42-maker-recovery-full366",
    "source_run_id": 35432363044,
    "shards": len(summaries),
    "global_candidate_count": summaries[0]["global_candidate_count"] if summaries else len(rows),
    "input": sum(s.get("input",0) for s in summaries),
    "facilities_with_pdf": sum(s.get("facilities_with_pdf",0) for s in summaries),
    "pdf_documents": sum(s.get("pdf_documents",0) for s in summaries),
    "safe_resolved": sum(s.get("safe_resolved",0) for s in summaries),
    "explicit_mhlw": sum(s.get("explicit_mhlw",0) for s in summaries),
    "validated_product_map": sum(s.get("validated_product_map",0) for s in summaries),
    "product_only_unvalidated": sum(s.get("product_only_unvalidated",0) for s in summaries),
    "no_brand_hit": sum(s.get("no_brand_hit",0) for s in summaries),
    "retry_403": sum(s.get("retry_403",0) for s in summaries),
    "maker_counts": dict(maker_counts.most_common()),
    "product_counts": dict(product_counts.most_common()),
    "evidence_counts": dict(evidence_counts),
    "locked_rule": "Only EXPLICIT_MHLW or VALIDATED_PRODUCT_MAP is manufacturer-safe. Product-only unvalidated hits do not update manufacturer share."
}
out_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(summary, ensure_ascii=False))
