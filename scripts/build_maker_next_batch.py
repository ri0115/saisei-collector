#!/usr/bin/env python3
import csv
import glob
import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

ROOT=Path(".")
RESULTS=ROOT/"results"

LEGAL=[
    "社会医療法人社団","社会医療法人","医療法人社団","医療法人財団","医療法人",
    "一般社団法人","一般財団法人","公益財団法人","公益社団法人","社会福祉法人",
    "学校法人","国立大学法人","公立大学法人","地方独立行政法人",
    "独立行政法人国立病院機構","独立行政法人地域医療機能推進機構",
    "独立行政法人","国家公務員共済組合連合会","公立学校共済組合","株式会社"
]
PREFS=[
    "北海道","青森県","岩手県","宮城県","秋田県","山形県","福島県","茨城県","栃木県",
    "群馬県","埼玉県","千葉県","東京都","神奈川県","新潟県","富山県","石川県","福井県",
    "山梨県","長野県","岐阜県","静岡県","愛知県","三重県","滋賀県","京都府","大阪府",
    "兵庫県","奈良県","和歌山県","鳥取県","島根県","岡山県","広島県","山口県","徳島県",
    "香川県","愛媛県","高知県","福岡県","佐賀県","長崎県","熊本県","大分県","宮崎県",
    "鹿児島県","沖縄県"
]
TERMINAL_PREFIXES=("CONFIRMED_NEW","ALREADY_CANONICAL","HOLD","REJECT","OUTSIDE_LOCKED_CORE_REVIEW")

def norm(s):
    return unicodedata.normalize("NFKC",str(s or "")).replace("\u3000"," ").strip()

def compact(s):
    s=norm(s)
    s=re.sub(r"\s+","",s)
    s=re.sub(r"[・･\-‐‑–—―,，.。()（）「」『』【】\[\]・•＆&]","",s)
    return s.lower()

def strip_legal(s):
    x=compact(s)
    for p in LEGAL:
        x=x.replace(compact(p),"")
    return x

def facility_key(pref,facility):
    return norm(pref)+"|"+strip_legal(facility)

def pref_from_address(address):
    a=norm(address)
    return next((p for p in PREFS if p in a),"")

def addr_key(address,pref=""):
    a=norm(address)
    a=re.sub(r"〒?\d{3}-?\d{4}","",a)
    if pref:
        a=a.replace(pref,"")
    a=compact(a)
    a=a.replace("丁目","").replace("番地","").replace("番","").replace("号","")
    return a

def read_tsv(path):
    with open(path,encoding="utf-8-sig",newline="") as f:
        return list(csv.DictReader(f,delimiter="\t"))

def split_makers(s):
    return [norm(x) for x in re.split(r"[|/]",norm(s)) if norm(x)]

def add_core(pool,pref,facility,address="",source="",facility_id="",plan_code=""):
    pref,facility=norm(pref),norm(facility)
    if not pref or not facility:
        return
    k=facility_key(pref,facility)
    x=pool[k]
    x["prefecture"]=pref
    if not x["facility"]:
        x["facility"]=facility
    if address and not x["address"]:
        x["address"]=norm(address)
    if source:
        x["sources"].add(source)
    if facility_id:
        x["facility_ids"].add(norm(facility_id))
    if plan_code:
        x["plan_codes"].add(norm(plan_code))

def build_core_pool():
    pool=defaultdict(lambda:{
        "prefecture":"","facility":"","address":"",
        "sources":set(),"facility_ids":set(),"plan_codes":set()
    })

    # v40 queued core records committed as price-stage files.
    for p in sorted(glob.glob(str(RESULTS/"v42_v40_price_stage_[ABC]_batch*.tsv"))):
        for r in read_tsv(p):
            add_core(pool,r.get("prefecture"),r.get("facility"),"",
                     "V40_STAGE",r.get("facility_id"),r.get("mhlw_plan_code"))

    # v40 facilities that were outside the price-recovery queue.
    p=ROOT/"data"/"v40_nonqueue_facility_master.tsv"
    if p.exists():
        for r in read_tsv(p):
            add_core(pool,r.get("prefecture"),r.get("facility"),r.get("address"),
                     "V40_NONQUEUE",r.get("facility_id"),"")

    # v41 additions contain current facility address and scope status.
    p=RESULTS/"v42_additions_final_compact.tsv"
    if p.exists():
        for r in read_tsv(p):
            if norm(r.get("scope_status")).upper()=="OUT_OF_SCOPE":
                continue
            add_core(pool,r.get("prefecture"),r.get("facility"),r.get("address"),
                     "V41",r.get("facility_id"),r.get("mhlw_plan_code"))

    # Manufacturer-recovery pool adds core facilities that were not price-stage candidates.
    for name in ["v42_maker_recovery_full366.tsv","v42_maker_recovery_official_strict_v2.tsv"]:
        p=RESULTS/name
        if not p.exists():
            continue
        for r in read_tsv(p):
            add_core(pool,r.get("prefecture"),r.get("facility"),"",
                     "MAKER_RECOVERY","",r.get("mhlw_plan_codes"))

    return pool

def canonical_pairs():
    pairs=set()
    p=RESULTS/"maker_canonical_rebuild"/"facility_manufacturers.tsv"
    if not p.exists():
        return pairs
    for r in read_tsv(p):
        fk=facility_key(r.get("prefecture"),r.get("facility"))
        for m in split_makers(r.get("manufacturers")):
            pairs.add((fk,m))
    return pairs

def terminal_audit_pairs():
    pairs=set()
    for p in sorted(glob.glob(str(RESULTS/"v42_maker_manual_audit_batch*.tsv"))):
        for r in read_tsv(p):
            status=norm(r.get("status")).upper()
            if not status.startswith(TERMINAL_PREFIXES):
                continue
            fk=facility_key(r.get("prefecture"),r.get("facility"))
            for m in split_makers(r.get("manufacturer")):
                pairs.add((fk,m))
    return pairs

def load_locators():
    rows=[]
    p=RESULTS/"maker_locator_crossmatch"/"arthrex_locator.tsv"
    if p.exists():
        for r in read_tsv(p):
            rows.append({
                "maker":"Arthrex",
                "prefecture":norm(r.get("address")),
                "name":norm(r.get("name")),
                "product":norm(r.get("products")),
                "address":"",
                "url":norm(r.get("source_url")),
            })
    p=RESULTS/"maker_locator_crossmatch"/"zimmer_locator.tsv"
    if p.exists():
        for r in read_tsv(p):
            rows.append({
                "maker":"Zimmer Biomet",
                "prefecture":pref_from_address(r.get("address")),
                "name":norm(r.get("name")),
                "product":norm(r.get("products")),
                "address":norm(r.get("address")),
                "url":norm(r.get("source_url")),
            })
    return rows

def name_match(a,b):
    aa,bb=strip_legal(a),strip_legal(b)
    if not aa or not bb:
        return 0,"NONE"
    if aa==bb:
        return 100,"NORMALIZED_EXACT"
    shorter=min(len(aa),len(bb))
    if shorter>=5 and (aa in bb or bb in aa):
        return 92,"CONTAINS"
    return 0,"NONE"

def address_match(core_address,locator_address,pref):
    if not core_address or not locator_address:
        return 0,"NONE"
    a,b=addr_key(core_address,pref),addr_key(locator_address,pref)
    if not a or not b:
        return 0,"NONE"
    if a==b:
        return 100,"ADDRESS_EXACT"
    shorter=min(len(a),len(b))
    if shorter>=8 and (a in b or b in a):
        return 96,"ADDRESS_CONTAINS"
    return 0,"NONE"

def main():
    core=build_core_pool()
    known=canonical_pairs()
    audited=terminal_audit_pairs()
    locators=load_locators()

    by_pref=defaultdict(list)
    for l in locators:
        if l["prefecture"]:
            by_pref[l["prefecture"]].append(l)

    best={}
    for fk,x in core.items():
        for l in by_pref.get(x["prefecture"],[]):
            nm,nm_type=name_match(x["facility"],l["name"])
            am,am_type=address_match(x["address"],l["address"],x["prefecture"])
            if max(nm,am)<92:
                continue

            pair=(fk,l["maker"])
            if pair in known or pair in audited:
                continue

            product_upper=norm(l["product"]).upper()
            if l["maker"]=="Zimmer Biomet" and "APS" not in product_upper:
                confidence="HOLD_POLICY"
                eligible=False
                reason="Zimmer locator is PRP-only; APS/GPS not established."
            elif nm==100 or am>=96:
                confidence="HIGH"
                eligible=True
                reason="Exact normalized facility match or address agreement in manufacturer-operated locator."
            else:
                confidence="REVIEW"
                eligible=True
                reason="Name containment match; manual identity check required."

            score=max(nm,am)
            row={
                "prefecture":x["prefecture"],
                "facility":x["facility"],
                "core_sources":"|".join(sorted(x["sources"])),
                "facility_ids":"|".join(sorted(x["facility_ids"])),
                "plan_codes":"|".join(sorted(x["plan_codes"])),
                "core_address":x["address"],
                "manufacturer":l["maker"],
                "product":l["product"],
                "locator_name":l["name"],
                "locator_address":l["address"],
                "name_match":nm_type,
                "address_match":am_type,
                "score":score,
                "confidence":confidence,
                "eligible_for_next":"YES" if eligible else "NO",
                "reason":reason,
                "source_url":l["url"],
            }
            k=(fk,l["maker"])
            old=best.get(k)
            rank={"HIGH":3,"REVIEW":2,"HOLD_POLICY":1}
            if old is None or (rank[row["confidence"]],row["score"])>(rank[old["confidence"]],old["score"]):
                best[k]=row

    order={"HIGH":0,"REVIEW":1,"HOLD_POLICY":2}
    queue=sorted(best.values(),key=lambda r:(order[r["confidence"]],-int(r["score"]),r["prefecture"],r["facility"],r["manufacturer"]))
    for i,r in enumerate(queue,1):
        r["rank"]=i

    fields=[
        "rank","prefecture","facility","core_sources","facility_ids","plan_codes","core_address",
        "manufacturer","product","locator_name","locator_address","name_match","address_match",
        "score","confidence","eligible_for_next","reason","source_url"
    ]
    out=RESULTS/"maker_candidate_queue.tsv"
    with out.open("w",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields,delimiter="\t")
        w.writeheader(); w.writerows(queue)

    next_rows=[r for r in queue if r["eligible_for_next"]=="YES"][:10]
    out=RESULTS/"maker_next_batch.tsv"
    with out.open("w",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields,delimiter="\t")
        w.writeheader(); w.writerows(next_rows)

    counts=Counter(r["confidence"] for r in queue)
    summary={
        "core_candidate_pool_facilities":len(core),
        "canonical_pairs":len(known),
        "terminal_audited_pairs":len(audited),
        "locator_rows":len(locators),
        "candidate_pairs":len(queue),
        "confidence_counts":dict(counts),
        "next_batch_size":len(next_rows),
        "next_batch_high":sum(r["confidence"]=="HIGH" for r in next_rows),
        "next_batch_review":sum(r["confidence"]=="REVIEW" for r in next_rows),
        "policy":[
            "Generate only from repository-known core candidates; do not infer a facility into the 949 denominator from locator presence alone.",
            "Zimmer Biomet PRP-only locator rows are HOLD_POLICY and excluded from next batch.",
            "PRP-FD/PFC-FD/PDF-FD processing-outsourcing services are not manufacturer-share candidates.",
            "Terminally audited facility/manufacturer pairs are suppressed to avoid repeat work.",
            "maker_next_batch.tsv is capped at 10 rows to reduce ChatGPT timeout risk."
        ]
    }
    (RESULTS/"maker_next_batch_summary.json").write_text(
        json.dumps(summary,ensure_ascii=False,indent=2)+"\n",encoding="utf-8"
    )
    print(json.dumps(summary,ensure_ascii=False))

if __name__=="__main__":
    main()
