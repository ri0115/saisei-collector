#!/usr/bin/env python3
import csv, glob, json, re, unicodedata
from pathlib import Path
from collections import defaultdict, Counter

R=Path("results")
OUT=R/"maker_locator_reconciled"
OUT.mkdir(parents=True,exist_ok=True)

LEGAL=["社会医療法人","医療法人社団","医療法人財団","医療法人","一般社団法人","一般財団法人","公益財団法人","公益社団法人","社会福祉法人","学校法人","国立大学法人","公立大学法人","地方独立行政法人","独立行政法人国立病院機構","独立行政法人地域医療機能推進機構","独立行政法人","国家公務員共済組合連合会","公立学校共済組合","株式会社"]

def norm(s):
    s=unicodedata.normalize("NFKC",str(s or "")).replace("\u3000"," ").strip()
    s=re.sub(r"\s+","",s)
    s=re.sub(r"[・･\-‐‑–—―,，.。()（）「」『』【】\[\]]+","",s)
    return s.lower()

def strip_legal(s):
    x=norm(s)
    for p in LEGAL:x=x.replace(norm(p),"")
    return x

def read_tsv(p):
    with open(p,encoding="utf-8-sig",newline="") as f:return list(csv.DictReader(f,delimiter="\t"))

def fkey(pref,fac):
    return pref+"|"+strip_legal(fac)

# map v41 plan code -> facility
adds=read_tsv(R/"v42_additions_final_compact.tsv")
add_by_code={r["mhlw_plan_code"]:r for r in adds}

known=defaultdict(set)
evidence=defaultdict(list)

# v41 price-derived makers
for r in read_tsv(R/"v42_auto_prices_compact.tsv"):
    maker=(r.get("maker") or "").strip()
    code=(r.get("mhlw_plan_code") or "").strip()
    if not maker or code not in add_by_code:continue
    a=add_by_code[code]
    k=fkey(a["prefecture"],a["facility"])
    for m in maker.split("|"):
        if m.strip():known[k].add(m.strip())
    evidence[k].append("V41_PRICE")

# v40 staged makers
for p in glob.glob(str(R/"v42_v40_price_stage_[ABC]_batch*.tsv")):
    for r in read_tsv(p):
        makers=(r.get("makers") or "").strip()
        if not makers:continue
        k=fkey(r["prefecture"],r["facility"])
        for m in makers.split("|"):
            if m.strip():known[k].add(m.strip())
        evidence[k].append("V40_PRICE")

# MHLW full recovery safe
for r in read_tsv(R/"v42_maker_recovery_full366.tsv"):
    if (r.get("safe_resolved") or "").upper()!="YES":continue
    k=fkey(r["prefecture"],r["facility"])
    for m in (r.get("final_makers") or "").split("|"):
        if m.strip():known[k].add(m.strip())
    evidence[k].append("MHLW_RECOVERY")

# manual verified additions: use legal manufacturer canonical-ish
for r in read_tsv(R/"v42_maker_manual_verified_additions.tsv"):
    k=fkey(r["prefecture"],r["facility"])
    lm=(r.get("legal_manufacturer") or "").strip()
    fl=(r.get("facility_label_maker") or "").strip()
    # canonical normalization
    m=lm or fl
    if m:
        m=m.replace("Arthrex Japan","Arthrex").replace("ESTAR TECHNOLOGIES LTD.","ESTAR Technologies").replace("REV-MED Inc.","REV-MED")
        known[k].add(m)
    evidence[k].append("MANUAL_VERIFIED")

# locator candidates
loc=read_tsv(R/"maker_locator_crossmatch"/"matches.tsv")
rows=[]
for r in loc:
    if r.get("is_new_candidate")!="YES":continue
    k=fkey(r["prefecture"],r["facility"])
    manufacturer=r.get("inferred_manufacturer","").strip()
    already=manufacturer in known.get(k,set()) if manufacturer else False
    any_known=bool(known.get(k))
    rows.append({
      "prefecture":r["prefecture"],"facility":r["facility"],"mhlw_plan_codes":r["mhlw_plan_codes"],
      "locator_manufacturer":manufacturer,"locator_products":r["source_products"],"locator_confidence":r["confidence"],
      "source_operator":r["source_operator"],"match_type":r["match_type"],"source_url":r["source_url"],
      "known_manufacturers_before_locator":"|".join(sorted(known.get(k,set()))),
      "known_evidence_before_locator":"|".join(sorted(set(evidence.get(k,[])))),
      "same_manufacturer_already_known":"YES" if already else "NO",
      "facility_has_other_manufacturer_known":"YES" if any_known and not already else "NO",
      "truly_new_manufacturer_placement":"YES" if manufacturer and not already else "NO",
      "truly_new_manufacturer_facility":"YES" if manufacturer and not any_known else "NO"
    })

fields=list(rows[0].keys()) if rows else ["prefecture"]
with (OUT/"locator_candidates_reconciled.tsv").open("w",encoding="utf-8",newline="") as f:
    w=csv.DictWriter(f,fieldnames=fields,delimiter="\t");w.writeheader();w.writerows(rows)

high=[r for r in rows if r["locator_confidence"]=="HIGH" and r["truly_new_manufacturer_placement"]=="YES"]
high_newfac=[r for r in high if r["truly_new_manufacturer_facility"]=="YES"]
summary={
  "known_facility_keys_before_locator":len(known),
  "raw_locator_new_candidates":len(rows),
  "high_confidence_new_manufacturer_placements":len(high),
  "high_confidence_truly_new_facilities":len({fkey(r["prefecture"],r["facility"]) for r in high_newfac}),
  "high_new_by_manufacturer":dict(Counter(r["locator_manufacturer"] for r in high)),
  "high_new_facility_by_manufacturer":dict(Counter(r["locator_manufacturer"] for r in high_newfac)),
  "high_confidence_rows":high,
  "policy":"Counts reconcile against v40/v41 maker-bearing price rows, MHLW recovery, and manually verified additions before accepting locator matches."
}
(OUT/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print(json.dumps({k:v for k,v in summary.items() if k!="high_confidence_rows"},ensure_ascii=False))
