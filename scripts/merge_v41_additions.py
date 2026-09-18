#!/usr/bin/env python3
import argparse, csv, json
from datetime import datetime, timezone
from pathlib import Path

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

def now(): return datetime.now(timezone.utc).isoformat()

def normalize_price(p):
    q=dict(p)
    raw=q.get("products") or []
    products=[]
    for x in raw:
        y=PRODUCT_CANON.get(x,x)
        if y and y not in products: products.append(y)
    q["products_source"]=raw
    q["products"]=products
    q["makers"]=list(dict.fromkeys(MAKER[x] for x in products if x in MAKER))
    return q

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",default="v41-shards")
    ap.add_argument("--outdir",default="results")
    args=ap.parse_args()
    root=Path(args.root)
    items=[]
    shard_summaries={}
    for lane in "ABCD":
        candidates=list(root.glob(f"**/v41_{lane.lower()}_latest.json"))
        if not candidates:
            raise RuntimeError(f"missing shard {lane}")
        d=json.loads(candidates[0].read_text(encoding="utf-8"))
        xs=d.get("items",[])
        for x in xs:
            y=dict(x); y["v41_lane"]=lane
            y["prices"]=[normalize_price(p) for p in (y.get("prices") or [])]
            items.append(y)
        shard_summaries[lane]=d.get("summary",{})

    ids=[x.get("plan_id") for x in items]
    if len(items)!=774 or len(set(ids))!=774:
        raise RuntimeError(f"v41 merge integrity failure rows={len(items)} unique={len(set(ids))}")

    items.sort(key=lambda x:(x.get("prefecture",""),x.get("facility",""),x.get("mhlw_plan_code","")))
    counts={"AUTO":0,"QC":0,"FAIL":0}
    price_records=0
    for x in items:
        counts[x.get("status","QC")]=counts.get(x.get("status","QC"),0)+1
        price_records+=len(x.get("prices") or [])

    summary={
        "version":"v41-additions",
        "updated_at":now(),
        "total":774,"processed":774,"pending":0,
        "auto":counts["AUTO"],"qc":counts["QC"],"fail":counts["FAIL"],
        "price_records":price_records,
        "unique_facilities":len({x.get("facility_id") for x in items}),
        "shards":shard_summaries
    }
    outdir=Path(args.outdir);outdir.mkdir(parents=True,exist_ok=True)
    (outdir/"v41_additions_latest.json").write_text(json.dumps({"version":"v41-additions","updated_at":now(),"summary":summary,"items":items},ensure_ascii=False,indent=2),encoding="utf-8")
    (outdir/"v41_additions_summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")

    cols=["plan_id","mhlw_plan_code","v41_lane","facility_id","region","prefecture","facility","address","category","treatment","treatment_class","status","match_score","document_url","amount","tax","unit","products","makers","score","page","excerpt","qc_reason","error"]
    with (outdir/"v41_additions_prices.csv").open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=cols);w.writeheader()
        for it in items:
            for p in (it.get("prices") or [None]):
                w.writerow({
                    "plan_id":it.get("plan_id",""),"mhlw_plan_code":it.get("mhlw_plan_code",""),"v41_lane":it.get("v41_lane",""),
                    "facility_id":it.get("facility_id",""),"region":it.get("region",""),"prefecture":it.get("prefecture",""),"facility":it.get("facility",""),"address":it.get("address",""),
                    "category":it.get("category",""),"treatment":it.get("treatment",""),"treatment_class":it.get("treatment_class",""),
                    "status":it.get("status",""),"match_score":it.get("match_score",""),"document_url":it.get("document_url",""),
                    "amount":"" if not p else p.get("amount",""),"tax":"" if not p else p.get("tax",""),"unit":"" if not p else p.get("unit",""),
                    "products":"" if not p else "|".join(p.get("products",[])),"makers":"" if not p else "|".join(p.get("makers",[])),
                    "score":"" if not p else p.get("score",""),"page":"" if not p else p.get("page",""),"excerpt":"" if not p else p.get("excerpt",""),
                    "qc_reason":it.get("qc_reason",""),"error":it.get("error","")
                })
    print(json.dumps(summary,ensure_ascii=False))

if __name__=="__main__":
    main()
