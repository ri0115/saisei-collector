#!/usr/bin/env python3
import csv,json,re
from datetime import datetime,timezone
from pathlib import Path
from collections import defaultdict

BASE=Path("results/v41_additions_normalized.json")
CRAWL=Path("results/v41_official_seed_crawl.json")
OUT=Path("results/v41_additions_with_official_fallback.json")
SUM=Path("results/v41_official_fallback_summary.json")
CSV=Path("results/v41_official_fallback_prices.csv")

ANC_LINE=re.compile(r"初診|再診|診察料|検査料|血液検査|細胞採取|脂肪採取|保管|キャンセル|返金|採血後|加工後|原価|作業費|文書料|送料|手数料",re.I)

def now():
    return datetime.now(timezone.utc).isoformat()

def usable(c):
    if (c.get("combined_score",c.get("score",0)) or 0) < 9:
        return False
    line=(c.get("line") or "").strip()
    if ANC_LINE.search(line):
        return False
    a=c.get("amount")
    return isinstance(a,(int,float)) and a>0

def best_page(cands):
    by=defaultdict(list)
    for c in cands:
        if usable(c):
            by[c.get("source_url","")].append(c)
    if not by:
        return []
    # Pick one page to avoid duplicate price tables copied across the same site.
    ranked=sorted(
        by.items(),
        key=lambda kv:(max((x.get("combined_score",x.get("score",0)) or 0) for x in kv[1]),len(kv[1])),
        reverse=True
    )
    selected=ranked[0][1]
    out=[];seen=set()
    for c in sorted(selected,key=lambda x:(-(x.get("combined_score",x.get("score",0)) or 0),x.get("amount",0))):
        key=(c.get("amount"),c.get("tax","不明"),c.get("line",""))
        if key in seen: continue
        seen.add(key)
        out.append({
            "amount":int(c["amount"]),
            "tax":c.get("tax","不明"),
            "unit":"",
            "score":c.get("combined_score",c.get("score",0)),
            "products":[],
            "makers":[],
            "excerpt":c.get("excerpt",""),
            "line":c.get("line",""),
            "source_url":c.get("source_url",""),
            "source_type":"official_facility_site",
            "label_match":c.get("label_match",""),
            "target_kind":c.get("target_kind",""),
            "postprocess_keep":True,
            "postprocess_reason":"official_site_exact_treatment_label",
        })
    return out[:12]

def main():
    base=json.loads(BASE.read_text(encoding="utf-8"))
    crawl=json.loads(CRAWL.read_text(encoding="utf-8"))
    by_plan={}
    for r in crawl.get("results",[]):
        for p in r.get("plans",[]):
            by_plan[p.get("plan_id")]={
                "facility_id":r.get("facility_id"),
                "facility":r.get("facility"),
                "seed_url":r.get("seed_url"),
                "candidates":p.get("candidates") or []
            }

    promoted=[]
    touched=0
    for it in base.get("items",[]):
        if it.get("status_normalized")!="QC":
            continue
        rec=by_plan.get(it.get("plan_id"))
        if not rec:
            continue
        touched+=1
        prices=best_page(rec["candidates"])
        if not prices:
            it["official_fallback_status"]="reviewed_no_safe_price"
            continue
        it["official_fallback_status"]="promoted"
        it["official_fallback_seed_url"]=rec.get("seed_url")
        it["status_normalized"]="AUTO"
        it["postprocess_resolution"]="official_site_fallback"
        it["prices_normalized"]=prices
        promoted.append({
            "plan_id":it.get("plan_id"),"facility_id":it.get("facility_id"),
            "facility":it.get("facility"),"prefecture":it.get("prefecture"),
            "treatment":it.get("treatment"),"treatment_class":it.get("treatment_class"),
            "prices":prices
        })

    items=base.get("items",[])
    auto=sum(1 for x in items if x.get("status_normalized")=="AUTO")
    qc=sum(1 for x in items if x.get("status_normalized")=="QC")
    fail=sum(1 for x in items if x.get("status_normalized")=="FAIL")
    summary={
        "version":"v41-official-fallback-1.0","updated_at":now(),
        "total":len(items),"crawl_facilities":crawl.get("total_facilities",0),
        "qc_plans_touched":touched,"official_site_promoted":len(promoted),
        "normalized_auto":auto,"normalized_qc":qc,"normalized_fail":fail,
        "promoted_price_records":sum(len(x["prices"]) for x in promoted),
    }
    OUT.write_text(json.dumps({"version":"v41-official-fallback-1.0","updated_at":now(),"summary":summary,"items":items},ensure_ascii=False,indent=2),encoding="utf-8")
    SUM.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")

    cols=["plan_id","facility_id","prefecture","facility","treatment_class","treatment","amount","tax","source_url","score","target_kind","label_match","excerpt"]
    with CSV.open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=cols);w.writeheader()
        for x in promoted:
            for p in x["prices"]:
                w.writerow({
                    "plan_id":x["plan_id"],"facility_id":x["facility_id"],"prefecture":x["prefecture"],
                    "facility":x["facility"],"treatment_class":x["treatment_class"],"treatment":x["treatment"],
                    "amount":p["amount"],"tax":p["tax"],"source_url":p["source_url"],"score":p["score"],
                    "target_kind":p["target_kind"],"label_match":p["label_match"],"excerpt":p["excerpt"]
                })
    print(json.dumps(summary,ensure_ascii=False))

if __name__=="__main__":
    main()
