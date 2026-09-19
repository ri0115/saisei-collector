#!/usr/bin/env python3
import csv, glob, json, re, unicodedata
from collections import defaultdict
from pathlib import Path

OUT=Path("results/maker_title_scan")
OUT.mkdir(parents=True,exist_ok=True)

def norm(s):
    return unicodedata.normalize("NFKC",str(s or "")).replace("\u3000"," ").strip()

def fkey(pref,fac):
    return norm(pref)+"|"+re.sub(r"\s+","",norm(fac))

PRODUCT_PATTERNS=[
("ACP MAX",re.compile(r"(?i)ACP[\s・_-]*MAX")),
("ACP",re.compile(r"(?i)(?<![A-Za-z])ACP(?![A-Za-z])|ACPダブルシリンジ|ACP[- ]?PRP")),
("Angel",re.compile(r"(?i)(?<![A-Za-z])Angel(?![A-Za-z])(?:\s*c?PRP)?")),
("GPS",re.compile(r"(?i)(?<![A-Za-z])G\s*P\s*S\s*(?:III|Ⅲ|3)?(?![A-Za-z])")),
("APS",re.compile(r"(?i)(?<![A-Za-z])A\s*P\s*S(?![A-Za-z])|Autologous\s+Protein\s+Solution")),
("Condensia",re.compile(r"(?i)Condensia|コンデンシア")),
("MyCells",re.compile(r"(?i)My\s*cells?|Mycells|マイセル")),
("TriCeLL",re.compile(r"(?i)Tri\s*Cell|TriCeLL|トライセル")),
("MAGELLAN",re.compile(r"(?i)MAGELLAN|Magellan|マゼラン")),
("PRGF-Endoret",re.compile(r"(?i)PRGF[- ]?Endoret|Endoret|PRGF")),
("PEAK",re.compile(r"(?i)(?<![A-Za-z])PEAK(?![A-Za-z])(?:\s*(?:PRP)?\s*(?:System|システム))?")),
("YCELL",re.compile(r"(?i)(?<![A-Za-z])Y\s*CELL(?:BIO)?(?:\s*Medical)?|ワイセル")),
]
MAKER_PATTERNS=[
("Zimmer Biomet",re.compile(r"(?i)Zimmer\s*Biomet|ジンマー(?:・|\s|-)?バイオメット")),
("Arthrex",re.compile(r"(?i)Arthrex|アースレックス")),
("京セラ",re.compile(r"(?i)Kyocera|京セラ")),
("ESTAR Technologies",re.compile(r"(?i)ESTAR\s*TECHNOLOGIES")),
("メッド・アライアンス",re.compile(r"(?i)メッド[・･ ]?(?:アライアンス|アイアンス)")),
("ハイレックスメディカル",re.compile(r"(?i)ハイレックスメディカル|HI[- ]?LEX\s*MEDICAL")),
("BTI",re.compile(r"(?i)\bBTI\b")),
("Ycellbio Medical",re.compile(r"(?i)Ycellbio\s*Medical")),
("Johnson & Johnson",re.compile(r"(?i)Johnson\s*&\s*Johnson|ジョンソン[・･ ]?エンド[・･ ]?ジョンソン")),
]
PRODUCT_DEFAULT_MAKER={
"GPS":"Zimmer Biomet","APS":"Zimmer Biomet",
"ACP":"Arthrex","ACP MAX":"Arthrex","Angel":"Arthrex",
"Condensia":"京セラ","MyCells":"ESTAR Technologies",
"TriCeLL":"メッド・アライアンス","MAGELLAN":"ハイレックスメディカル",
"PRGF-Endoret":"BTI","YCELL":"Ycellbio Medical"
}

def hits(text):
    s=norm(text); p=set();m=set()
    for x,rx in PRODUCT_PATTERNS:
        if rx.search(s):p.add(x)
    for x,rx in MAKER_PATTERNS:
        if rx.search(s):m.add(x)
    return p,m

def read_tsv(path):
    with open(path,encoding="utf-8-sig",newline="") as f:
        return list(csv.DictReader(f,delimiter="\t"))

# Facility records from exact plan sources.
fac=defaultdict(lambda:{
    "prefecture":"","facility":"","classes":set(),"codes":set(),"treatments":set(),
    "existing_makers":set(),"existing_products":set(),"source_texts":[]
})

# v40 queued plans include full treatment names in latest JSON.
for p in ["results/latest.json","results/b_latest.json","results/c_latest.json"]:
    d=json.load(open(p,encoding="utf-8"))
    for r in d.get("items",[]):
        k=fkey(r.get("prefecture"),r.get("facility")); x=fac[k]
        x["prefecture"]=norm(r.get("prefecture"));x["facility"]=norm(r.get("facility"))
        x["classes"].add(norm(r.get("treatment_class")))
        x["treatments"].add(norm(r.get("treatment")))
        code=""
        m=re.search(r"/download/([^/]+)/",str(r.get("document_url") or ""))
        if m:code=m.group(1)
        if code:x["codes"].add(code)
        x["source_texts"].append(("v40_treatment",norm(r.get("treatment"))))

# v41 additions exact plan master.
for r in read_tsv("results/v42_additions_final_compact.tsv"):
    if r.get("scope_status")=="OUT_OF_SCOPE":continue
    k=fkey(r.get("prefecture"),r.get("facility"));x=fac[k]
    x["prefecture"]=norm(r.get("prefecture"));x["facility"]=norm(r.get("facility"))
    x["classes"].add(norm(r.get("treatment_class")));x["codes"].add(norm(r.get("mhlw_plan_code")))
    x["treatments"].add(norm(r.get("treatment")));x["source_texts"].append(("v41_treatment",norm(r.get("treatment"))))

# Existing product/maker evidence from price-stage files. This also catches product values whose maker field was blank.
for p in sorted(glob.glob("results/v42_v40_price_stage_[ABC]_batch*.tsv")):
    for r in read_tsv(p):
        k=fkey(r.get("prefecture"),r.get("facility"));x=fac[k]
        x["prefecture"]=norm(r.get("prefecture"));x["facility"]=norm(r.get("facility"))
        x["classes"].add(norm(r.get("treatment_class")))
        if r.get("mhlw_plan_code"):x["codes"].add(norm(r["mhlw_plan_code"]))
        for mm in re.split(r"[|/]",norm(r.get("makers"))):
            if mm and mm not in {"不明","未確認"}:x["existing_makers"].add(mm)
        for pp in re.split(r"[|/]",norm(r.get("products"))):
            if pp:x["existing_products"].add(pp)
        x["source_texts"].append(("v40_price_product",norm(r.get("products"))))
        x["source_texts"].append(("v40_price_unit",norm(r.get("unit"))))

for p in sorted(glob.glob("results/v42_price_merge_stage_batch*.tsv")):
    for r in read_tsv(p):
        k=fkey(r.get("prefecture"),r.get("facility"));x=fac[k]
        x["prefecture"]=norm(r.get("prefecture"));x["facility"]=norm(r.get("facility"))
        x["classes"].add(norm(r.get("treatment_class")))
        if r.get("mhlw_plan_code"):x["codes"].add(norm(r["mhlw_plan_code"]))
        mm=norm(r.get("maker"));pp=norm(r.get("product"))
        if mm and mm not in {"不明","未確認"}:x["existing_makers"].add(mm)
        if pp:x["existing_products"].add(pp)
        x["source_texts"].append(("v41_price_product",pp))
        x["source_texts"].append(("v41_price_unit",norm(r.get("unit"))))

results=[]
unknown_before=0
for k,x in fac.items():
    relevant="|".join(x["classes"])
    if "PRP" not in relevant and "APS" not in relevant:continue
    if x["existing_makers"]:continue
    unknown_before+=1
    products=set();explicit=set();evidence=[]
    # Scan exact plan title/treatment and exact price product/unit text only.
    for source,text in x["source_texts"]:
        ps,ms=hits(text)
        if ps or ms:
            products.update(ps);explicit.update(ms)
            evidence.append({"source":source,"text":text,"products":sorted(ps),"makers":sorted(ms)})
    # Also canonicalize existing product strings through the dictionary.
    for pp in x["existing_products"]:
        ps,ms=hits(pp)
        products.update(ps);explicit.update(ms)
        if ps or ms:evidence.append({"source":"existing_product","text":pp,"products":sorted(ps),"makers":sorted(ms)})
    inferred={PRODUCT_DEFAULT_MAKER[p] for p in products if p in PRODUCT_DEFAULT_MAKER}
    makers=explicit or inferred
    if makers:
        results.append({
            "prefecture":x["prefecture"],"facility":x["facility"],
            "mhlw_plan_codes":"|".join(sorted(x["codes"])),"treatment_classes":"|".join(sorted(x["classes"])),
            "products_found":"|".join(sorted(products)),"makers_found":"|".join(sorted(makers)),
            "evidence_type":"EXPLICIT" if explicit else "PRODUCT_MAP",
            "evidence_json":json.dumps(evidence[:20],ensure_ascii=False)
        })

fields=["prefecture","facility","mhlw_plan_codes","treatment_classes","products_found","makers_found","evidence_type","evidence_json"]
with open(OUT/"resolved.tsv","w",encoding="utf-8",newline="") as f:
    w=csv.DictWriter(f,fieldnames=fields,delimiter="\t");w.writeheader();w.writerows(results)
summary={
    "prp_aps_manufacturer_unknown_before":unknown_before,
    "resolved_by_exact_title_or_product_fields":len(results),
    "remaining_after_title_scan":unknown_before-len(results),
    "resolution_rate":round(len(results)/unknown_before,4) if unknown_before else 0,
    "explicit_maker_facilities":sum(r["evidence_type"]=="EXPLICIT" for r in results),
    "product_map_facilities":sum(r["evidence_type"]=="PRODUCT_MAP" for r in results),
}
json.dump(summary,open(OUT/"summary.json","w",encoding="utf-8"),ensure_ascii=False,indent=2)
print(json.dumps(summary,ensure_ascii=False,indent=2))
