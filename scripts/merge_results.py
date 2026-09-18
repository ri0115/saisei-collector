#!/usr/bin/env python3
import csv, json
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path("results")
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

cols=["priority","plan_id","facility_id","region","prefecture","facility","category","treatment","treatment_class","status","match_score","document_url","amount","tax","unit","products","score","page","excerpt","qc_reason","error"]
with open(ROOT/"all_prices.csv","w",encoding="utf-8-sig",newline="") as f:
    w=csv.DictWriter(f,fieldnames=cols)
    w.writeheader()
    for it in all_items:
        prices=it.get("prices") or [None]
        for p in prices:
            w.writerow({
                "priority":it.get("priority",""),"plan_id":it.get("plan_id",""),"facility_id":it.get("facility_id",""),
                "region":it.get("region",""),"prefecture":it.get("prefecture",""),"facility":it.get("facility",""),
                "category":it.get("category",""),"treatment":it.get("treatment",""),"treatment_class":it.get("treatment_class",""),
                "status":it.get("status",""),"match_score":it.get("match_score",""),"document_url":it.get("document_url",""),
                "amount":"" if not p else p.get("amount",""),"tax":"" if not p else p.get("tax",""),
                "unit":"" if not p else p.get("unit",""),"products":"" if not p else "|".join(p.get("products",[])),
                "score":"" if not p else p.get("score",""),"page":"" if not p else p.get("page",""),
                "excerpt":"" if not p else p.get("excerpt",""),"qc_reason":it.get("qc_reason",""),"error":it.get("error","")
            })

print(json.dumps(summary,ensure_ascii=False))
