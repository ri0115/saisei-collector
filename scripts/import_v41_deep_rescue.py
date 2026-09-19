#!/usr/bin/env python3
import json
from datetime import datetime, timezone
from pathlib import Path

SRC=Path("results/v41_deep_mhlw_rescue.json")
OVR=Path("data/v41_verified_price_overrides.json")

def now():
    return datetime.now(timezone.utc).isoformat()

def main():
    if not SRC.exists():
        raise SystemExit("deep rescue output missing")
    deep=json.loads(SRC.read_text(encoding="utf-8"))
    data=json.loads(OVR.read_text(encoding="utf-8")) if OVR.exists() else {"plans":{}}
    plans=data.setdefault("plans",{})

    added=0
    skipped_existing=0
    skipped_ambiguous=0
    added_ids=[]

    for item in deep.get("items",[]):
        if item.get("status")!="AUTO_DEEP":
            continue
        pid=item.get("plan_id")
        if not pid:
            continue
        if pid in plans:
            skipped_existing+=1
            continue

        accepted=item.get("accepted") or []
        # Deep auto-import remains conservative: point prices only.
        point=[x for x in accepted if x.get("price_type","point")=="point" and x.get("amount")]
        if not point or len(point)!=len(accepted) or len(point)>12:
            skipped_ambiguous+=1
            continue

        prices=[]
        seen=set()
        for x in point:
            amount=int(x["amount"])
            product=x.get("product")
            key=(amount,x.get("tax","不明"),x.get("unit",""),product)
            if key in seen:
                continue
            seen.add(key)
            prices.append({
                "amount":amount,
                "tax":x.get("tax","不明"),
                "unit":x.get("unit",""),
                "product":product,
                "note":f"MHLW deep rescue; score={x.get('score')}; page={x.get('page')}; mode={x.get('source_mode')}"
            })

        if not prices:
            skipped_ambiguous+=1
            continue

        plans[pid]={
            "facility":item.get("facility",""),
            "source_type":"mhlw_published_plan_deep_ocr",
            "source_url":item.get("document_url",""),
            "prices":prices,
        }
        added+=1
        added_ids.append(pid)

    data["updated_at"]=now()
    data["note"]="Verified price overrides from official facility pages and MHLW published-plan attachments. Deep OCR imports are restricted to AUTO_DEEP point-price candidates."
    OVR.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({
        "deep_total":deep.get("total"),
        "deep_auto":deep.get("auto"),
        "added":added,
        "added_ids":added_ids,
        "skipped_existing":skipped_existing,
        "skipped_ambiguous":skipped_ambiguous,
        "override_total":len(plans)
    },ensure_ascii=False))

if __name__=="__main__":
    main()
