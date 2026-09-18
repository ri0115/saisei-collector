#!/usr/bin/env python3
import csv, json, re, unicodedata
from datetime import datetime, timezone
from pathlib import Path

SRC=Path("results/v41_additions_latest.json")
OUT_JSON=Path("results/v41_additions_normalized.json")
OUT_SUM=Path("results/v41_additions_postprocess_summary.json")
OUT_CSV=Path("results/v41_additions_prices_normalized.csv")

PRODUCT_CANON={
    "GPS III":"GPS","GPSⅢ":"GPS","GPS":"GPS","APS":"APS",
    "ACP MAX":"ACP MAX","ACP":"ACP","Angel":"Angel",
    "Condensia":"Condensia","コンデンシア":"Condensia",
    "Mycells":"MyCells","MyCells":"MyCells","TriCeLL":"TriCeLL",
    "PEAK":"PEAK","PRGF":"PRGF-Endoret","Endoret":"PRGF-Endoret",
    "MAGELLAN":"MAGELLAN","マゼラン":"MAGELLAN",
}
MAKER={
    "GPS":"Zimmer Biomet","APS":"Zimmer Biomet",
    "ACP":"Arthrex","ACP MAX":"Arthrex","Angel":"Arthrex",
    "Condensia":"京セラ","PRGF-Endoret":"BTI",
    "MyCells":"MyCells系","TriCeLL":"TriCeLL系","PEAK":"PEAK系","MAGELLAN":"MAGELLAN系",
}

ALIASES=[
    ("PRGF-Endoret","PRGF-Endoret"),("PRGF-Endoret","Endoret"),("PRGF-Endoret","PRGF"),
    ("Condensia","コンデンシア"),("Condensia","Condensia"),
    ("MAGELLAN","MAGELLAN"),("MAGELLAN","マゼラン"),
    ("ACP MAX","ACP MAX"),("GPS","GPS III"),("GPS","GPSⅢ"),
    ("TriCeLL","TriCeLL"),("MyCells","MyCells"),("MyCells","Mycells"),
    ("Angel","Angel"),("PEAK","PEAK"),("APS","APS"),("GPS","GPS"),("ACP","ACP"),
]
ALIASES=sorted(ALIASES,key=lambda x:len(x[1]),reverse=True)
PRODUCT_RE=re.compile("|".join(re.escape(a) for _,a in ALIASES),re.I)
AMOUNT_RE=re.compile(r"(?<!\d)(\d[\d,\s]*(?:\.\d+)?)\s*(万円|円)")
ANC_RE=re.compile(r"キャンセル料|キャンセル料金|初診料|再診料|診察料|検査料|血液検査|感染症検査|採血料|保管料|保存料|文書料|証明書|処置費用|装置.*費用|手数料",re.I)
TREAT_RE=re.compile(r"治療費(?:用)?|施術料|施術料金|治療料金|投与費用|培養費用|総額|単回|片膝|両膝|1本あたり|再移植|軟骨組織採取.*移植|PRP骨髄内注入治療|PRP|APS|ACP|GPS|Angel|Condensia|コンデンシア|PRGF|Endoret|幹細胞培養|細胞投与|1部位|1回",re.I)
COMPLEX_FEE_RE=re.compile(r"治療費とは別|別途|初診料|再診料|診察料|検査料|検査費用|キャンセル料|キャンセル料金|保管料|保管費用|採取料|処置費用|手数料",re.I)
RANGE_RE=re.compile(r"[~〜～]")

def now():
    return datetime.now(timezone.utc).isoformat()

def parse_amount(m):
    raw=re.sub(r"[\s,]","",m.group(1))
    try:
        return int(round(float(raw)*(10000 if m.group(2)=="万円" else 1)))
    except Exception:
        return None

def product_tokens(text):
    out=[]
    for m in PRODUCT_RE.finditer(text):
        token=m.group(0)
        canon=None
        for c,a in ALIASES:
            if token.lower()==a.lower():
                canon=c;break
        if canon:
            out.append((m.start(),m.end(),canon))
    return out

def amount_occurrences(text,amount):
    return [(m.start(),m.end()) for m in AMOUNT_RE.finditer(text) if parse_amount(m)==amount]

def choose_occurrence(text,amount):
    occ=amount_occurrences(text,amount)
    if not occ:
        return None
    prods=product_tokens(text)
    best=None
    for st,en in occ:
        prev=[p for p in prods if p[0]<st]
        last=prev[-1] if prev else None
        dist=(st-last[1]) if last else 999
        local=text[max(0,st-70):min(len(text),en+70)]
        score=(1 if re.search(r"治療費|施術料|料金|費用|PRP|幹細胞|培養|投与|細胞|部位|回",local,re.I) else 0)-dist/1000
        if best is None or score>best[0]:
            best=(score,st,en)
    return (best[1],best[2])

def paired_product(text,amount):
    occ=choose_occurrence(text,amount)
    if not occ:
        return None
    st,_=occ
    prev=[p for p in product_tokens(text) if p[0]<st]
    if not prev:
        return None
    p=prev[-1]
    if st-p[1]>100:
        return None
    return p[2]

def nearest_label_before(text,amount):
    occ=choose_occurrence(text,amount)
    if not occ:
        return None
    st,_=occ
    best=None
    for typ,rgx in [("ancillary",ANC_RE),("treatment",TREAT_RE)]:
        for m in rgx.finditer(text[:st]):
            dist=st-m.end()
            if dist<=90 and (best is None or dist<best[0]):
                best=(dist,typ,m.group(0))
    return best[1:] if best else None

def infer_tax(text,amount):
    occ=choose_occurrence(text,amount)
    if not occ:
        return None
    st,en=occ
    local=text[max(0,st-80):min(len(text),en+80)]
    if re.search(r"税込|消費税込",local,re.I):
        return "税込"
    if re.search(r"税別|税抜|税抜き",local,re.I):
        return "税別"
    return None

def canonical_products(raw):
    out=[]
    for x in raw or []:
        y=PRODUCT_CANON.get(x,x)
        if y and y not in out:
            out.append(y)
    return out

def augment_candidates(prices,tclass):
    """Recover OCR-split or neighboring prices from already-saved excerpts; no refetch."""
    out=[dict(p) for p in (prices or [])]
    seen={(int(p.get("amount") or 0),p.get("page"),p.get("excerpt","")) for p in out}
    source=list(out)
    for p in source:
        excerpt=unicodedata.normalize("NFKC",p.get("excerpt") or "")
        if not excerpt:
            continue
        for m in AMOUNT_RE.finditer(excerpt):
            amount=parse_amount(m)
            if not amount or not 1000<=amount<=20000000:
                continue
            key=(amount,p.get("page"),p.get("excerpt",""))
            if key in seen:
                continue
            seen.add(key)
            local=excerpt[max(0,m.start()-90):min(len(excerpt),m.end()+90)]
            out.append({
                "amount":amount,"tax":"不明","unit":"","score":0,"products":[],
                "excerpt":p.get("excerpt",""),"page":p.get("page"),"line":local,
                "synthetic_postprocess":True
            })

        # Capture explicit price ranges as structured rows rather than a single max value.
        for rm in re.finditer(r"(?<!\\d)(\\d[\\d,]*)\\s*[~〜～]\\s*(\\d[\\d,]*)\\s*円",excerpt):
            lo=int(rm.group(1).replace(",","")); hi=int(rm.group(2).replace(",",""))
            if lo>hi: lo,hi=hi,lo
            if not 1000<=lo<=hi<=20000000:
                continue
            prefix=excerpt[max(0,rm.start()-100):rm.start()]
            prod=paired_product(excerpt,hi) or paired_product(excerpt,lo)
            out.append({
                "amount":None,"amount_min":lo,"amount_max":hi,"price_type":"range",
                "tax":infer_tax(excerpt,hi) or infer_tax(excerpt,lo) or "不明",
                "unit":"","score":0,"products":[prod] if prod else [],
                "excerpt":p.get("excerpt",""),"page":p.get("page"),"line":rm.group(0),
                "synthetic_postprocess":True,"postprocess_keep":bool(prod),
                "postprocess_reason":"structured_price_range" if prod else "unresolved_price_range"
            })
    return out

def normalize_candidate(p,tclass):
    q=dict(p)
    text=unicodedata.normalize("NFKC",q.get("excerpt") or "")
    line=unicodedata.normalize("NFKC",q.get("line") or "")
    amount=int(q.get("amount") or 0)

    paired=paired_product(text,amount)
    original=canonical_products(q.get("products") or [])
    if paired:
        products=[paired]
        q["product_pair_method"]="nearest_preceding_product_in_excerpt"
    else:
        products=original
        q["product_pair_method"]="context_tokens_unresolved" if len(original)>1 else "context_token"
    q["products_source"]=q.get("products") or []
    q["products"]=products
    q["makers"]=list(dict.fromkeys(MAKER[x] for x in products if x in MAKER))

    if q.get("tax") in ("",None,"不明"):
        tax=infer_tax(text,amount)
        if tax:
            q["tax_source"]=q.get("tax","不明")
            q["tax"]=tax
            q["tax_inferred"]=True

    stem=bool(re.search(r"幹細胞|MSC|ASC|ADRC|脂肪|SVF|MFAT",tclass or "",re.I))
    min_amt=100000 if stem else 15000
    keep=False;why="unclear"
    if amount<min_amt:
        why="below_minimum_treatment_price"
    else:
        label=nearest_label_before(text,amount)
        if label and label[0]=="ancillary":
            why="ancillary_label_nearest"
        elif label and label[0]=="treatment":
            keep=True;why="treatment_label_nearest"
        elif paired:
            keep=True;why="paired_product_price"
        elif re.search(r"治療費|治療料金|投与費用|培養費用",line,re.I):
            if not stem or amount>=500000:
                keep=True;why="explicit_treatment_price"
        elif stem and amount>=500000:
            occ=choose_occurrence(text,amount)
            local=text[max(0,occ[0]-80):min(len(text),occ[1]+80)] if occ else text
            if re.search(r"cell|細胞|万個|億|幹細胞|培養",local,re.I) and not re.search(r"キャンセル|開始後|採取料|保管",local,re.I):
                keep=True;why="cell_dose_table"
        elif re.search(r"PRP|APS|ACP|GPS|Angel|Condensia|コンデンシア|PRGF|Endoret",line,re.I):
            keep=True;why="explicit_prp_product_line"

    q["postprocess_keep"]=keep
    q["postprocess_reason"]=why
    return q

def resolve_qc_item(item,normed):
    ranges=[p for p in normed if p.get("price_type")=="range" and p.get("postprocess_keep")]
    scalar=[p for p in normed if p.get("price_type")!="range"]
    kept=[p for p in scalar if p.get("postprocess_keep")]
    uniq=[];seen=set()
    for p in kept:
        key=(p.get("amount"),tuple(p.get("products") or []),p.get("page"),p.get("line"))
        if key not in seen:
            seen.add(key);uniq.append(p)
    kept=uniq

    # If explicit product ranges were recovered, they are safer than a parser-created max value.
    if ranges:
        rseen=set();runique=[]
        for p in ranges:
            key=(p.get("amount_min"),p.get("amount_max"),tuple(p.get("products") or []))
            if key not in rseen:
                rseen.add(key);runique.append(p)
        if runique and all(p.get("products") for p in runique):
            return True,runique,"structured_product_price_range"

    alltext=" ".join((p.get("excerpt") or "")+" "+(p.get("line") or "") for p in scalar)
    complex_fee=bool(COMPLEX_FEE_RE.search(alltext))
    stem=bool(re.search(r"幹細胞|MSC|ASC|ADRC|脂肪|SVF|MFAT",item.get("treatment_class",""),re.I))
    min_amt=100000 if stem else 15000

    # Prefer explicit totals over component fees when a total block exists.
    totals=[p for p in kept if re.search(r"総額",p.get("excerpt") or "",re.I)]
    if totals:
        return True,totals,"explicit_total_price_block"

    unresolved_high=[
        p for p in scalar
        if (p.get("amount") or 0)>=min_amt
        and not p.get("postprocess_keep")
        and p.get("postprocess_reason") not in ("ancillary_label_nearest","below_minimum_treatment_price")
    ]

    if len(kept)==1:
        p=kept[0]
        if stem and (p.get("amount") or 0)<500000:
            return False,kept,"stem_cell_component_needs_review"
        if p.get("postprocess_reason") in ("paired_product_price","cell_dose_table","explicit_treatment_price","explicit_prp_product_line","treatment_label_nearest"):
            if not unresolved_high:
                return True,kept,"single_clear_treatment_price"

    # Mixed fee pages can still be resolved if every high-value amount is classified
    # as either a treatment price or an ancillary fee.
    if complex_fee and kept and not unresolved_high:
        return True,kept,"treatment_prices_separated_from_ancillary_fees"

    if complex_fee:
        return False,kept,"multiple_fee_components_needs_review"

    if 2<=len(kept)<=12 and not unresolved_high:
        product_table=all(p.get("product_pair_method")=="nearest_preceding_product_in_excerpt" for p in kept)
        labelled_table=all(p.get("postprocess_reason") in ("paired_product_price","treatment_label_nearest","explicit_treatment_price","explicit_prp_product_line","cell_dose_table") for p in kept)
        if product_table:
            return True,kept,"structured_product_price_table"
        if labelled_table:
            return True,kept,"structured_treatment_price_table"

    if stem and 2<=len(kept)<=12 and not unresolved_high and all((p.get("amount") or 0)>=500000 for p in kept):
        return True,kept,"structured_cell_dose_price_table"

    return False,kept,"still_ambiguous"

def main():
    data=json.loads(SRC.read_text(encoding="utf-8"))
    items=data.get("items",[])
    promoted=0
    normalized_auto=0
    normalized_qc=0
    normalized_fail=0
    price_records=0
    reason_counts={}

    for item in items:
        item["status_source"]=item.get("status","QC")
        item["qc_reason_source"]=item.get("qc_reason","")
        base_prices=item.get("prices") or []
        if item["status_source"]=="QC" and "ambiguous" in (item.get("qc_reason") or ""):
            base_prices=augment_candidates(base_prices,item.get("treatment_class",""))
        normed=[]
        for p in base_prices:
            if p.get("price_type")=="range":
                q=dict(p)
                q["products"]=canonical_products(q.get("products") or [])
                q["makers"]=list(dict.fromkeys(MAKER[x] for x in q["products"] if x in MAKER))
                normed.append(q)
            else:
                normed.append(normalize_candidate(p,item.get("treatment_class","")))
        item["prices_normalized"]=normed
        item["status_normalized"]=item["status_source"]
        item["postprocess_resolution"]="source_status_retained"

        if item["status_source"]=="QC" and "ambiguous" in (item.get("qc_reason") or ""):
            ok,kept,reason=resolve_qc_item(item,normed)
            item["postprocess_resolution"]=reason
            item["prices_normalized"]=kept if ok else normed
            if ok:
                item["status_normalized"]="AUTO"
                promoted+=1

        status=item["status_normalized"]
        if status=="AUTO": normalized_auto+=1
        elif status=="QC": normalized_qc+=1
        else: normalized_fail+=1
        price_records+=len(item.get("prices_normalized") or [])
        reason_counts[item["postprocess_resolution"]]=reason_counts.get(item["postprocess_resolution"],0)+1

    summary={
        "version":"v41-postprocess-1.1",
        "updated_at":now(),
        "total":len(items),
        "source_auto":sum(1 for x in items if x.get("status_source")=="AUTO"),
        "source_qc":sum(1 for x in items if x.get("status_source")=="QC"),
        "source_fail":sum(1 for x in items if x.get("status_source")=="FAIL"),
        "promoted_qc_to_auto":promoted,
        "normalized_auto":normalized_auto,
        "normalized_qc":normalized_qc,
        "normalized_fail":normalized_fail,
        "normalized_price_records":price_records,
        "resolution_counts":reason_counts,
    }

    OUT_JSON.write_text(json.dumps({"version":"v41-postprocess-1.1","updated_at":now(),"summary":summary,"items":items},ensure_ascii=False,indent=2),encoding="utf-8")
    OUT_SUM.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")

    cols=["plan_id","mhlw_plan_code","v41_lane","facility_id","prefecture","facility","category","treatment","treatment_class","status_source","status_normalized","postprocess_resolution","document_url","price_type","amount","amount_min","amount_max","tax","unit","products","makers","product_pair_method","postprocess_reason","score","page","excerpt","synthetic_postprocess"]
    with OUT_CSV.open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=cols);w.writeheader()
        for it in items:
            for p in (it.get("prices_normalized") or [None]):
                w.writerow({
                    "plan_id":it.get("plan_id",""),"mhlw_plan_code":it.get("mhlw_plan_code",""),"v41_lane":it.get("v41_lane",""),
                    "facility_id":it.get("facility_id",""),"prefecture":it.get("prefecture",""),"facility":it.get("facility",""),
                    "category":it.get("category",""),"treatment":it.get("treatment",""),"treatment_class":it.get("treatment_class",""),
                    "status_source":it.get("status_source",""),"status_normalized":it.get("status_normalized",""),
                    "postprocess_resolution":it.get("postprocess_resolution",""),"document_url":it.get("document_url",""),
                    "price_type":"" if not p else p.get("price_type","point"),"amount":"" if not p else p.get("amount",""),
                    "amount_min":"" if not p else p.get("amount_min",""),"amount_max":"" if not p else p.get("amount_max",""),
                    "tax":"" if not p else p.get("tax",""),"unit":"" if not p else p.get("unit",""),"products":"" if not p else "|".join(p.get("products") or []),
                    "makers":"" if not p else "|".join(p.get("makers") or []),"product_pair_method":"" if not p else p.get("product_pair_method",""),
                    "postprocess_reason":"" if not p else p.get("postprocess_reason",""),"score":"" if not p else p.get("score",""),
                    "page":"" if not p else p.get("page",""),"excerpt":"" if not p else p.get("excerpt",""),
                    "synthetic_postprocess":"" if not p else p.get("synthetic_postprocess",False),
                })
    print(json.dumps(summary,ensure_ascii=False))

if __name__=="__main__":
    main()
