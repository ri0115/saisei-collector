#!/usr/bin/env python3
import csv, json
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path("results")

PRODUCT_CANON={
    "GPS III":"GPS","GPSⅢ":"GPS","GPS":"GPS",
    "APS":"APS",
    "ACP MAX":"ACP MAX","ACP":"ACP",
    "Angel":"Angel",
    "Condensia":"Condensia","コンデンシア":"Condensia",
    "Mycells":"MyCells","MyCells":"MyCells",
    "TriCeLL":"TriCeLL","PEAK":"PEAK",
    "PRGF":"PRGF-Endoret","Endoret":"PRGF-Endoret",
    "MAGELLAN":"MAGELLAN","マゼラン":"MAGELLAN",
}
MAKER_BY_PRODUCT={
    "GPS":"Zimmer Biomet","APS":"Zimmer Biomet",
    "ACP":"Arthrex","ACP MAX":"Arthrex","Angel":"Arthrex",
    "Condensia":"京セラ",
    "MyCells":"MyCells系",
    "TriCeLL":"TriCeLL系",
    "PEAK":"PEAK系",
    "PRGF-Endoret":"BTI",
    "MAGELLAN":"MAGELLAN系",
}
FACILITY_ALIASES={
    "東京整形外科":"東京先進整形外科",
}

def normalize_products(price):
    raw=price.get("products") or []
    products=[]
    for p in raw:
        q=PRODUCT_CANON.get(p,p)
        if q and q not in products:
            products.append(q)
    price["products_source"]=raw
    price["products"]=products
    price["makers"]=list(dict.fromkeys(MAKER_BY_PRODUCT[p] for p in products if p in MAKER_BY_PRODUCT))
    return price

sources=[
    ("A",ROOT/"latest.json"),
    ("B",ROOT/"b_latest.json"),
    ("C",ROOT/"c_latest.json"),
]

def now():
    return datetime.now(timezone.utc).isoformat()

all_items=[]
lane_summary={}
for lane,path in sources:
    if not path.exists():
        lane_summary[lane]={"total":0,"processed":0,"auto":0,"qc":0,"fail":0}
        continue
    data=json.loads(path.read_text(encoding="utf-8"))
    items=data.get("items",[])
    for x in items:
        y=dict(x)
        y["priority"]=lane
        source_name=y.get("facility","")
        y["facility_source"]=source_name
        y["facility_normalized"]=FACILITY_ALIASES.get(source_name,source_name)
        y["prices"]=[normalize_products(dict(p)) for p in (y.get("prices") or [])]
        all_items.append(y)
    s=data.get("summary",{})
    lane_summary[lane]={
        "total":s.get("total",len(items)),
        "processed":s.get("processed",len(items)),
        "auto":s.get("auto",0),
        "qc":s.get("qc",0),
        "fail":s.get("fail",0),
    }

all_items.sort(key=lambda x:({"A":0,"B":1,"C":2}.get(x.get("priority"),9),x.get("plan_id","")))
counts={"AUTO":0,"QC":0,"FAIL":0}
price_records=0
for x in all_items:
    counts[x.get("status","QC")]=counts.get(x.get("status","QC"),0)+1
    price_records+=len(x.get("prices") or [])

summary={
    "queue_total":656,
    "processed":len(all_items),
    "pending":656-len(all_items),
    "auto":counts["AUTO"],
    "qc":counts["QC"],
    "fail":counts["FAIL"],
    "price_records":price_records,
    "lanes":lane_summary,
}
out={"version":"1.0","updated_at":now(),"summary":summary,"items":all_items}
(ROOT/"all_latest.json").write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
(ROOT/"all_summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")

cols=["priority","plan_id","facility_id","region","prefecture","facility","facility_source","facility_normalized","category","treatment","treatment_class","status","match_score","document_url","amount","tax","unit","products","makers","score","page","excerpt","qc_reason","error"]
with open(ROOT/"all_prices.csv","w",encoding="utf-8-sig",newline="") as f:
    w=csv.DictWriter(f,fieldnames=cols)
    w.writeheader()
    for it in all_items:
        prices=it.get("prices") or [None]
        for p in prices:
            w.writerow({
                "priority":it.get("priority",""),"plan_id":it.get("plan_id",""),"facility_id":it.get("facility_id",""),
                "region":it.get("region",""),"prefecture":it.get("prefecture",""),"facility":it.get("facility_normalized",it.get("facility","")),
                "facility_source":it.get("facility_source",it.get("facility","")),"facility_normalized":it.get("facility_normalized",it.get("facility","")),
                "category":it.get("category",""),"treatment":it.get("treatment",""),"treatment_class":it.get("treatment_class",""),
                "status":it.get("status",""),"match_score":it.get("match_score",""),"document_url":it.get("document_url",""),
                "amount":"" if not p else p.get("amount",""),"tax":"" if not p else p.get("tax",""),
                "unit":"" if not p else p.get("unit",""),"products":"" if not p else "|".join(p.get("products",[])),
                "makers":"" if not p else "|".join(p.get("makers",[])),
                "score":"" if not p else p.get("score",""),"page":"" if not p else p.get("page",""),
                "excerpt":"" if not p else p.get("excerpt",""),"qc_reason":it.get("qc_reason",""),"error":it.get("error","")
            })

print(json.dumps(summary,ensure_ascii=False))
