#!/usr/bin/env python3
import csv,glob,json,re,unicodedata
from pathlib import Path
from collections import defaultdict,Counter

MATCH=Path("results/maker_locator_crossmatch/matches.tsv")
MANUAL=Path("results/v42_maker_manual_verified_additions.tsv")
OUT=Path("results/maker_locator_crossmatch/audited_new.tsv")
SUM=Path("results/maker_locator_crossmatch/audited_summary.json")

LEGAL=["社会医療法人","医療法人社団","医療法人財団","医療法人","一般社団法人","一般財団法人","公益財団法人","公益社団法人","社会福祉法人","学校法人","国立大学法人","公立大学法人","地方独立行政法人","独立行政法人国立病院機構","独立行政法人地域医療機能推進機構","独立行政法人","国家公務員共済組合連合会","公立学校共済組合","株式会社"]

def norm(s):
    s=unicodedata.normalize("NFKC",str(s or "")).replace("\u3000"," ")
    return re.sub(r"[\s・･\-‐‑–—―,，.。()（）「」『』【】\[\]]+","",s).lower()
def key(pref,fac):
    x=norm(fac)
    for p in LEGAL:x=x.replace(norm(p),"")
    return norm(pref)+"|"+x
def read(path):
    with open(path,encoding="utf-8-sig",newline="") as f:return list(csv.DictReader(f,delimiter="\t"))

known=defaultdict(set)
sources=defaultdict(set)
# Current price-stage manufacturer evidence across v40/v41.
for p in sorted(glob.glob("results/v42_v40_price_stage_[ABC]_batch*.tsv")):
    for r in read(p):
        makers=r.get("makers","") or r.get("maker","")
        k=key(r.get("prefecture",""),r.get("facility",""))
        for m in re.split(r"[|/]",makers):
            m=m.strip()
            if m and m not in {"不明","未確認"}:known[k].add(m);sources[k].add(p)
for p in sorted(glob.glob("results/v42_price_merge_stage_batch*.tsv")):
    for r in read(p):
        makers=r.get("maker","")
        k=key(r.get("prefecture",""),r.get("facility",""))
        for m in re.split(r"[|/]",makers):
            m=m.strip()
            if m and m not in {"不明","未確認"}:known[k].add(m);sources[k].add(p)

# Manual verified additions are canonical and may normalize legal-manufacturer names.
if MANUAL.exists():
    for r in read(MANUAL):
        k=key(r.get("prefecture",""),r.get("facility",""))
        lm=r.get("legal_manufacturer","").strip()
        label=r.get("facility_label_maker","").strip()
        # normalize common variants to share groups
        vals=[]
        if lm: vals.append(lm)
        if label: vals.append(label)
        for m in vals:
            low=m.lower()
            if "zimmer" in low: known[k].add("Zimmer Biomet")
            elif "arthrex" in low: known[k].add("Arthrex")
            elif "京セラ" in m: known[k].add("京セラ")
            elif "estar" in low: known[k].add("ESTAR Technologies")
            elif "rev-med" in low: known[k].add("REV-MED")
            elif "dsm" in low: known[k].add("DSM Biomedical")
            elif "arteriocyte" in low: known[k].add("Arteriocyte Medical Systems")
            elif "bti" in low: known[k].add("BTI Biotechnology Institute")
        sources[k].add(str(MANUAL))

matches=read(MATCH)
audit=[]
for r in matches:
    if r.get("confidence")!="HIGH" or r.get("is_new_candidate")!="YES":continue
    k=key(r["prefecture"],r["facility"])
    maker=r["inferred_manufacturer"]
    existing=known.get(k,set())
    if maker in existing:
        status="DUPLICATE_KNOWN_MAKER"
    elif existing:
        status="NEW_MAKER_EXISTING_KNOWN_FACILITY"
    else:
        status="NEW_MANUFACTURER_FACILITY"
    x=dict(r)
    x["normalized_key"]=k
    x["existing_manufacturers"]="|".join(sorted(existing))
    x["audit_status"]=status
    audit.append(x)

# Deduplicate same facility+maker across locator sources.
dedup={}
for r in audit:
    dk=(r["normalized_key"],r["inferred_manufacturer"])
    old=dedup.get(dk)
    if not old or (old["source_operator"]!="Arthrex" and r["source_operator"]=="Arthrex"):
        dedup[dk]=r
audit=list(dedup.values())
audit.sort(key=lambda r:(r["audit_status"],r["prefecture"],r["facility"],r["inferred_manufacturer"]))

fields=list(audit[0].keys()) if audit else ["facility"]
with OUT.open("w",encoding="utf-8",newline="") as f:
    w=csv.DictWriter(f,fieldnames=fields,delimiter="\t");w.writeheader();w.writerows(audit)

new_fac=[r for r in audit if r["audit_status"]=="NEW_MANUFACTURER_FACILITY"]
new_placement=[r for r in audit if r["audit_status"]=="NEW_MAKER_EXISTING_KNOWN_FACILITY"]
dup=[r for r in audit if r["audit_status"]=="DUPLICATE_KNOWN_MAKER"]
summary={
 "high_candidate_rows_before_normalized_audit":sum(1 for r in matches if r.get("confidence")=="HIGH" and r.get("is_new_candidate")=="YES"),
 "audited_unique_facility_maker_pairs":len(audit),
 "new_manufacturer_facilities":len({r["normalized_key"] for r in new_fac}),
 "new_manufacturer_placements_on_new_facilities":len(new_fac),
 "new_maker_placements_on_already_known_facilities":len(new_placement),
 "duplicate_known_maker_pairs":len(dup),
 "new_facility_by_manufacturer":dict(Counter(r["inferred_manufacturer"] for r in new_fac)),
 "new_existing_facility_placements_by_manufacturer":dict(Counter(r["inferred_manufacturer"] for r in new_placement)),
 "new_facilities":[{"prefecture":r["prefecture"],"facility":r["facility"],"maker":r["inferred_manufacturer"],"source":r["source_operator"],"products":r["source_products"]} for r in new_fac],
 "new_placements_existing_facilities":[{"prefecture":r["prefecture"],"facility":r["facility"],"maker":r["inferred_manufacturer"],"existing":r["existing_manufacturers"]} for r in new_placement]
}
SUM.write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print(json.dumps(summary,ensure_ascii=False))
