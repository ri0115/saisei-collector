#!/usr/bin/env python3
# Canonical rebuild trigger: evidence inputs are tracked by workflow paths.
import csv, glob, json, re, unicodedata
from pathlib import Path
from collections import defaultdict, Counter

ROOT=Path(".")
RESULTS=ROOT/"results"
REG=json.loads((ROOT/"data/product_manufacturer_registry.json").read_text(encoding="utf-8"))["products"]

LEGAL=["社会医療法人","医療法人社団","医療法人財団","医療法人","一般社団法人","一般財団法人","公益財団法人","公益社団法人","社会福祉法人","学校法人","国立大学法人","公立大学法人","地方独立行政法人","独立行政法人国立病院機構","独立行政法人地域医療機能推進機構","独立行政法人","国家公務員共済組合連合会","公立学校共済組合","株式会社"]

def norm(s):
    return unicodedata.normalize("NFKC",str(s or "")).replace("\u3000"," ").strip()

def compact(s):
    s=norm(s)
    s=re.sub(r"\s+","",s)
    s=re.sub(r"[・･\-‐‑–—―,，.。()（）「」『』【】\[\]]","",s)
    return s.lower()

def strip_legal(s):
    x=compact(s)
    for p in LEGAL:
        x=x.replace(compact(p),"")
    return x

def facility_key(pref,fac):
    return norm(pref)+"|"+strip_legal(fac)

def read_tsv(path):
    with open(path,encoding="utf-8-sig",newline="") as f:
        return list(csv.DictReader(f,delimiter="\t"))

ALIASES={
 "zimmerbiomet":"Zimmer Biomet","zimmer biomet":"Zimmer Biomet","ジンマーバイオメット":"Zimmer Biomet",
 "arthrex":"Arthrex","arthrex japan":"Arthrex","アースレックス":"Arthrex",
 "京セラ":"京セラ","kyocera":"京セラ",
 "mycells系":"ESTAR Technologies","estar technologies":"ESTAR Technologies","estar technologies ltd.":"ESTAR Technologies",
 "peak系":"DSM Biomedical","dsm biomedical":"DSM Biomedical",
 "tricell系":"REV-MED","rev-med":"REV-MED","rev-med inc.":"REV-MED","メッドアライアンス":"REV-MED","メッド・アライアンス":"REV-MED",
 "bti":"BTI Biotechnology Institute","bti biotechnology institute":"BTI Biotechnology Institute",
 "magellan系":"Arteriocyte Medical Systems","arteriocyte medical systems":"Arteriocyte Medical Systems","arteriocyte medical systems, inc.":"Arteriocyte Medical Systems",
 "hi-lex medical":"Arteriocyte Medical Systems","ハイレックスメディカル":"Arteriocyte Medical Systems",
 "ycellbio medical":"Ycellbio Medical","ycellbio medical co., ltd":"Ycellbio Medical",
}

PRODUCT_TO_GROUP={p:(x.get("manufacturer_share_group") or "").strip() for p,x in REG.items()}

def canonical_maker(m):
    x=norm(m)
    if not x:return ""
    k=x.lower().replace("　"," ").strip()
    k2=re.sub(r"\s+"," ",k)
    if k2 in ALIASES:return ALIASES[k2]
    k3=compact(x)
    for a,v in ALIASES.items():
        if compact(a)==k3:return v
    return x

def add_pair(store, pref, fac, maker, source, product=""):
    maker=canonical_maker(maker)
    if not maker:return
    fk=facility_key(pref,fac)
    if not fk or fk.endswith("|"):return
    store[fk]["prefecture"]=norm(pref)
    store[fk]["facility"]=norm(fac)
    store[fk]["makers"].add(maker)
    store[fk]["sources"].add(source)
    if product:store[fk]["products"].add(norm(product))

def add_product_pairs(store,pref,fac,products,source):
    for p in re.split(r"[|/]",norm(products)):
        p=norm(p)
        if not p:continue
        g=PRODUCT_TO_GROUP.get(p,"")
        if g:add_pair(store,pref,fac,g,source,p)

def main():
    fac=defaultdict(lambda:{"prefecture":"","facility":"","makers":set(),"products":set(),"sources":set()})

    # 1. Current price-stage manufacturer evidence (v40 + v41).
    for p in sorted(glob.glob(str(RESULTS/"v42_v40_price_stage_[ABC]_batch*.tsv"))):
        for r in read_tsv(p):
            pref,facility=r.get("prefecture",""),r.get("facility","")
            makers=r.get("makers","")
            products=r.get("products","")
            for m in re.split(r"[|/]",norm(makers)):
                if norm(m):add_pair(fac,pref,facility,m,"PRICE_STAGE_V40",products)
            # If product is a validated registry product, use it too; this captures later product-only rows.
            add_product_pairs(fac,pref,facility,products,"PRICE_STAGE_V40_PRODUCT")
    for p in sorted(glob.glob(str(RESULTS/"v42_price_merge_stage_batch*.tsv"))):
        for r in read_tsv(p):
            pref,facility=r.get("prefecture",""),r.get("facility","")
            maker=r.get("maker","")
            product=r.get("product","")
            for m in re.split(r"[|/]",norm(maker)):
                if norm(m):add_pair(fac,pref,facility,m,"PRICE_STAGE_V41",product)
            add_product_pairs(fac,pref,facility,product,"PRICE_STAGE_V41_PRODUCT")

    # 2. MHLW all-attachment safe recovery.
    p=RESULTS/"v42_maker_recovery_full366.tsv"
    if p.exists():
        for r in read_tsv(p):
            if str(r.get("safe_resolved","")).upper()=="YES":
                for m in re.split(r"[|/]",norm(r.get("final_makers",""))):
                    if norm(m):add_pair(fac,r.get("prefecture",""),r.get("facility",""),m,"MHLW_FULL",r.get("products_found",""))

    # 3. Manually verified official/MHLW additions.
    p=RESULTS/"v42_maker_manual_verified_additions.tsv"
    if p.exists():
        for r in read_tsv(p):
            makers=r.get("legal_manufacturer","")
            for maker in re.split(r"[|/]", norm(makers)):
                if norm(maker):
                    add_pair(fac,r.get("prefecture",""),r.get("facility",""),maker,"MANUAL_VERIFIED",r.get("product",""))

    # 4. Manufacturer-operated facility locators: only HIGH confidence exact matches.
    p=RESULTS/"maker_locator_crossmatch"/"matches.tsv"
    locator_accepted=[]
    if p.exists():
        for r in read_tsv(p):
            if r.get("confidence")!="HIGH":continue
            maker=r.get("inferred_manufacturer","")
            if not maker:continue
            add_pair(fac,r.get("prefecture",""),r.get("facility",""),maker,"MANUFACTURER_LOCATOR",r.get("source_products",""))
            locator_accepted.append(r)

    # Count unique facility/manufacturer pairs from reconstructed current evidence.
    maker_counts=Counter()
    source_counts=Counter()
    rows=[]
    for fk,x in sorted(fac.items(),key=lambda kv:(kv[1]["prefecture"],kv[1]["facility"])):
        for m in sorted(x["makers"]):
            maker_counts[m]+=1
        for s in x["sources"]:source_counts[s]+=1
        rows.append({
            "facility_key":fk,"prefecture":x["prefecture"],"facility":x["facility"],
            "manufacturers":"|".join(sorted(x["makers"])),
            "manufacturer_count":len(x["makers"]),
            "products":"|".join(sorted(x["products"])),
            "sources":"|".join(sorted(x["sources"]))
        })

    out=RESULTS/"maker_canonical_rebuild"
    out.mkdir(exist_ok=True)
    with (out/"facility_manufacturers.tsv").open("w",encoding="utf-8",newline="") as f:
        fields=["facility_key","prefecture","facility","manufacturers","manufacturer_count","products","sources"]
        w=csv.DictWriter(f,fieldnames=fields,delimiter="\t");w.writeheader();w.writerows(rows)

    locator_unique={facility_key(r.get("prefecture",""),r.get("facility","")) for r in locator_accepted}
    summary={
      "facility_universe":949,
      "manufacturer_known_facilities":len(rows),
      "manufacturer_known_rate_pct":round(len(rows)/949*100,2),
      "manufacturer_placements":sum(maker_counts.values()),
      "manufacturer_counts":dict(maker_counts.most_common()),
      "manufacturer_share_pct":{k:round(v/sum(maker_counts.values())*100,2) for k,v in maker_counts.most_common()},
      "multi_manufacturer_facilities":sum(int(r["manufacturer_count"])>1 for r in rows),
      "source_presence_facilities":dict(source_counts),
      "manufacturer_locator_high_rows":len(locator_accepted),
      "manufacturer_locator_high_unique_facilities":len(locator_unique),
      "policy":[
        "Rebuilt from current staged price evidence, MHLW exact-plan attachment recovery, manually verified official pages, and HIGH-confidence manufacturer-operated facility locators.",
        "Facility/manufacturer pairs are deduplicated before counting.",
        "Product-to-manufacturer normalization uses data/product_manufacturer_registry.json.",
        "REVIEW locator matches are excluded.",
        "Share is documented facility adoption/availability share, not sales-volume or procedure-volume share."
      ]
    }
    (out/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=False))

if __name__=="__main__":main()
