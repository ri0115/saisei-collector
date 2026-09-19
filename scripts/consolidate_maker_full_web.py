#!/usr/bin/env python3
import csv,json,sys
from collections import Counter
from pathlib import Path
root=Path(sys.argv[1]) if len(sys.argv)>1 else Path("maker_web_artifacts")
rows=[]; sums=[]
for d in sorted(root.glob("maker-web-shard-*")):
    rf=list(d.rglob("results.tsv")); sf=list(d.rglob("summary.json"))
    if rf:
        with rf[0].open(encoding="utf-8-sig",newline="") as f: rows.extend(csv.DictReader(f,delimiter="\t"))
    if sf: sums.append(json.loads(sf[0].read_text(encoding="utf-8")))
out=Path("results/v42_maker_recovery_official_web.tsv")
fields=list(rows[0].keys()) if rows else ["facility_key"]
with out.open("w",encoding="utf-8",newline="") as f:
    w=csv.DictWriter(f,fieldnames=fields,delimiter="\t");w.writeheader();w.writerows(rows)
mc=Counter();pc=Counter();et=Counter()
for r in rows:
    if r.get("official_safe_resolved")=="YES":
        for m in filter(None,(r.get("official_final_makers") or "").split("|")): mc[m]+=1
    if r.get("official_product_resolved")=="YES":
        for p in filter(None,(r.get("official_products") or "").split("|")): pc[p]+=1
    et[r.get("official_evidence_type","")]+=1
summary={
 "version":"v42-maker-recovery-official-web",
 "input":len(rows),
 "manufacturer_safe":sum(r.get("official_safe_resolved")=="YES" for r in rows),
 "product_resolved":sum(r.get("official_product_resolved")=="YES" for r in rows),
 "maker_counts":dict(mc.most_common()),"product_counts":dict(pc.most_common()),
 "evidence_counts":dict(et),
 "locked_rule":"Only pages containing a full/legal-stripped target facility alias are accepted; directories, social media, vendor sites and literature sites are excluded."
}
Path("results/v42_maker_recovery_official_web_summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print(json.dumps(summary,ensure_ascii=False))
