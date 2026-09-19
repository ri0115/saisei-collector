#!/usr/bin/env python3
import json, requests
from pathlib import Path
CODES=["01E2605018","03F2304004","01C2407019","01G2404003","01E2303002"]
OUT=Path("results/mhlw_requests_warmup_probe.json")
s=requests.Session()
s.headers.update({
 "User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
 "Accept-Language":"ja-JP,ja;q=0.9,en;q=0.8"
})
rows=[]
try:
 r=s.get("https://saiseiiryo.mhlw.go.jp/published_plan/index/3",timeout=20)
 rows.append({"stage":"warmup","status":r.status_code,"cookies":len(s.cookies),"bytes":len(r.content)})
except Exception as e:
 rows.append({"stage":"warmup","error":repr(e)})
for code in CODES:
 for idx in [0,1]:
  url=f"https://saiseiiryo.mhlw.go.jp/published_plan/download/{code}/5/{idx}"
  try:
   r=s.get(url,timeout=20)
   rows.append({"code":code,"idx":idx,"status":r.status_code,"content_type":r.headers.get("content-type"),"bytes":len(r.content),"pdf_magic":r.content.startswith(b"%PDF")})
  except Exception as e:
   rows.append({"code":code,"idx":idx,"error":repr(e)})
OUT.write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps(rows,ensure_ascii=False))
