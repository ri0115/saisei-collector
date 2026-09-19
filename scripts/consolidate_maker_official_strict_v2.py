#!/usr/bin/env python3
import csv,json,glob,sys
from pathlib import Path
from collections import Counter
root=Path(sys.argv[1]) if len(sys.argv)>1 else Path("strict_v2_artifacts")
files=glob.glob(str(root/"**"/"results.tsv"),recursive=True)
rows=[]
for p in files:
    with open(p,encoding="utf-8-sig",newline="") as f: rows+=list(csv.DictReader(f,delimiter="\t"))
by={r["facility_key"]:r for r in rows}; rows=sorted(by.values(),key=lambda r:(r["prefecture"],r["facility"]))
out=Path("results");out.mkdir(exist_ok=True)
fields=list(rows[0].keys()) if rows else ["facility_key"]
with (out/"v42_maker_recovery_official_strict_v2.tsv").open("w",encoding="utf-8",newline="") as f:
    w=csv.DictWriter(f,fieldnames=fields,delimiter="\t");w.writeheader();w.writerows(rows)
safe=[r for r in rows if r.get("safe_resolved")=="YES"];mc=Counter();pc=Counter()
for r in safe:
    for x in filter(None,r.get("manufacturer_share_groups","").split("|")):mc[x]+=1
    for x in filter(None,r.get("official_products","").split("|")):pc[x]+=1
summary={"input":len(rows),"safe_resolved":len(safe),"product_resolved":sum(r.get("product_resolved")=="YES" for r in rows),
         "explicit":sum(r.get("evidence_type")=="EXPLICIT_OFFICIAL" for r in safe),
         "validated_product_map":sum(r.get("evidence_type")=="VALIDATED_PRODUCT_MAP_OFFICIAL" for r in safe),
         "maker_counts":dict(mc.most_common()),"product_counts":dict(pc.most_common()),
         "status":"STRICT_V2_AUDIT_REQUIRED_BEFORE_MERGE"}
(out/"v42_maker_recovery_official_strict_v2_summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print(json.dumps(summary,ensure_ascii=False))
