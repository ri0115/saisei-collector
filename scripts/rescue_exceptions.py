#!/usr/bin/env python3
import argparse, csv, json, re, time, unicodedata
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import pymupdf as fitz
import requests
from bs4 import BeautifulSoup
import pytesseract
from PIL import Image

BASE="https://saiseiiryo.mhlw.go.jp"
UA="Mozilla/5.0 (compatible; RegenMedCollector-Rescue/1.0; +https://github.com/ri0115/saisei-collector)"
AMOUNT_PATTERNS=[
    (re.compile(r"(?<![0-9])([0-9]{1,3}(?:,[0-9]{3})+|[0-9]{4,8})\s*円"),1),
    (re.compile(r"(?<![0-9])([0-9]+(?:\.[0-9]+)?)\s*万円"),10000),
]
POS_PRICE=r"治療費|施術料|治療料金|料金|費用|価格|自己負担|自由診療|自費"
POS_TREAT=r"PRP|APS|ACP|GPS|幹細胞|再生医療|血小板|投与|注入|関節|腱|靭帯|筋|脂肪"
NEG_ANC=r"初診|再診|診察料|検査料|感染症検査|血液検査|採血料|キャンセル|返金|文書料|証明書|送料|保管料"
COURSE=r"1回|一回|1部位|一部位|片側|両側|回分|コース|クール|投与"

def now(): return datetime.now(timezone.utc).isoformat()

def fetch(url):
    headers={"User-Agent":UA,"Accept":"application/pdf,text/html,*/*","Referer":BASE+"/"}
    last=None
    for n in range(3):
        try:
            r=requests.get(url,headers=headers,timeout=30)
            if r.status_code in (403,429,500,502,503,504):
                last=RuntimeError(f"HTTP {r.status_code}")
                time.sleep(4*(n+1)); continue
            r.raise_for_status(); return r
        except Exception as e:
            last=e; time.sleep(3*(n+1))
    raise last or RuntimeError("request failed")

def page_texts(url):
    r=fetch(url)
    ct=(r.headers.get("content-type") or "").lower()
    if not (r.content[:4]==b"%PDF" or "pdf" in ct):
        return [BeautifulSoup(r.content,"html.parser").get_text("\n",strip=True)], "html", []
    doc=fitz.open(stream=r.content,filetype="pdf")
    texts=[p.get_text("text") or "" for p in doc]
    return texts,"pdf",doc

def safe_ocr(doc,max_pages=12):
    out=[]; timed_out=0
    for i,p in enumerate(doc):
        if i>=max_pages: break
        try:
            pix=p.get_pixmap(matrix=fitz.Matrix(1.25,1.25),alpha=False)
            img=Image.open(BytesIO(pix.tobytes("png")))
            txt=pytesseract.image_to_string(img,lang="jpn+eng",config="--psm 6",timeout=35)
            out.append(txt or "")
        except RuntimeError:
            timed_out+=1
            out.append("")
        except Exception:
            out.append("")
    return out,timed_out

def score_line(line,near):
    s=0
    if re.search(POS_PRICE,line,re.I): s+=7
    elif re.search(POS_PRICE,near,re.I): s+=3
    if re.search(POS_TREAT,line,re.I): s+=5
    elif re.search(POS_TREAT,near,re.I): s+=2
    if re.search(COURSE,line): s+=2
    elif re.search(COURSE,near): s+=1
    if re.search(r"税込|税別|税抜|消費税",line): s+=2
    elif re.search(r"税込|税別|税抜|消費税",near): s+=1
    if re.search(NEG_ANC,line) and not re.search(POS_TREAT,line,re.I): s-=10
    return s

def extract_candidates(pages):
    out=[]; seen=set()
    for page_no,page in enumerate(pages,1):
        lines=[re.sub(r"\s+"," ",unicodedata.normalize("NFKC",x)).strip() for x in page.splitlines()]
        lines=[x for x in lines if x]
        for i,line in enumerate(lines):
            near=" ".join(lines[max(0,i-2):min(len(lines),i+3)])
            for pat,mult in AMOUNT_PATTERNS:
                for m in pat.finditer(line):
                    try: amount=int(float(m.group(1).replace(",",""))*mult)
                    except: continue
                    if not 1000<=amount<=20000000: continue
                    tax="不明"
                    if re.search(r"税込|消費税込",near): tax="税込"
                    elif re.search(r"税別|税抜|消費税別",near): tax="税別"
                    unit=""
                    um=re.search(r"((?:1|一)\s*回|(?:1|一)\s*部位|片側|両側|[0-9]+\s*回(?:分|コース)?|[0-9]+\s*クール)",near)
                    if um: unit=um.group(1)
                    k=(amount,tax,unit,line)
                    if k in seen: continue
                    seen.add(k)
                    out.append({"amount":amount,"tax":tax,"unit":unit,"score":score_line(line,near),"products":[],"excerpt":near,"page":page_no,"line":line})
    out.sort(key=lambda x:(-x["score"],x["amount"]))
    return out[:30]

def treatment_min(item):
    t=(item.get("treatment_class","")+" "+item.get("treatment",""))
    return 100000 if re.search(r"幹細胞|MSC|ASC|ADRC|脂肪由来",t,re.I) else 15000

def finalize(item,cands,mode,ocr_timeouts=0):
    item["qc_refined_at"]=now()
    item["rescue_refined_at"]=now()
    item["document_type"]=mode
    item["prices_refined"]=cands
    strong=[p for p in cands if p["score"]>=9 and p["amount"]>=treatment_min(item)]
    medium=[p for p in cands if p["score"]>=7 and p["amount"]>=treatment_min(item)]
    match=item.get("match_score",0) or 0
    if match>=0.84 and 1<=len(strong)<=16:
        item["prices"]=strong; item["status"]="AUTO"; item["error"]=""; item["qc_reason"]="rescue strong price context"
    elif match>=0.84 and 1<=len(medium)<=6:
        item["prices"]=medium; item["status"]="AUTO"; item["error"]=""; item["qc_reason"]="rescue medium price context"
    elif cands:
        item["prices"]=cands[:16]; item["status"]="QC"; item["error"]=""; item["qc_reason"]="rescue price candidates remain ambiguous"
    else:
        item["status"]="QC"; item["prices"]=item.get("prices") or []
        item["qc_reason"]="no price found after rescue text/OCR pass"
        item["error"]=f"Rescue completed; OCR page timeouts={ocr_timeouts}"
    return item

def rescue_one(item):
    url=item.get("document_url")
    if not url:
        item["qc_refined_at"]=now(); item["rescue_refined_at"]=now(); item["status"]="QC"; item["qc_reason"]="no document URL"; return item
    try:
        texts,mode,doc=page_texts(url)
        cands=extract_candidates(texts)
        ocr_to=0
        if not cands and doc:
            ocr,ocr_to=safe_ocr(doc)
            oc=extract_candidates(ocr)
            if oc: cands=oc; mode="ocr-rescue"
        return finalize(item,cands,mode,ocr_to)
    except Exception as e:
        item["qc_refined_at"]=now(); item["rescue_refined_at"]=now(); item["status"]="QC"
        item["qc_reason"]="terminal rescue fetch/parse failure"
        item["error"]=f"{type(e).__name__}: {e}"[:400]
        return item

def load(p): return json.loads(Path(p).read_text(encoding="utf-8"))
def save(p,o): Path(p).write_text(json.dumps(o,ensure_ascii=False,indent=2),encoding="utf-8")

def write_csv(path,items):
    cols=["plan_id","facility_id","prefecture","facility","category","treatment","status","match_score","document_url","amount","tax","unit","score","page","excerpt","qc_reason","error"]
    with open(path,"w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=cols); w.writeheader()
        for it in items:
            for p in (it.get("prices") or [None]):
                w.writerow({
                    "plan_id":it.get("plan_id",""),"facility_id":it.get("facility_id",""),"prefecture":it.get("prefecture",""),
                    "facility":it.get("facility",""),"category":it.get("category",""),"treatment":it.get("treatment",""),
                    "status":it.get("status",""),"match_score":it.get("match_score",""),"document_url":it.get("document_url",""),
                    "amount":"" if not p else p.get("amount",""),"tax":"" if not p else p.get("tax",""),"unit":"" if not p else p.get("unit",""),
                    "score":"" if not p else p.get("score",""),"page":"" if not p else p.get("page",""),"excerpt":"" if not p else p.get("excerpt",""),
                    "qc_reason":it.get("qc_reason",""),"error":it.get("error","")
                })

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--results",required=True); ap.add_argument("--state",required=True); ap.add_argument("--csv",required=True)
    args=ap.parse_args()
    data=load(args.results); items=data.get("items",[])
    targets=[x for x in items if x.get("status") in ("QC","FAIL") and not x.get("qc_refined_at")]
    by={x["plan_id"]:x for x in items}
    for i,x in enumerate(targets,1):
        y=rescue_one(dict(x)); by[y["plan_id"]]=y
        print(f"{i}/{len(targets)} {y['plan_id']} {y['status']} {y.get('qc_reason','')}",flush=True)
    items=[by[x["plan_id"]] for x in items]
    counts={"AUTO":0,"QC":0,"FAIL":0}
    for x in items: counts[x.get("status","QC")]=counts.get(x.get("status","QC"),0)+1
    remaining=sum(1 for x in items if x.get("status") in ("QC","FAIL") and not x.get("qc_refined_at"))
    data["items"]=items; data["version"]="0.8"; data["updated_at"]=now()
    data["summary"]={"total":len(items),"processed":len(items),"pending":0,"auto":counts["AUTO"],"qc":counts["QC"],"fail":counts["FAIL"],"qc_refine_remaining":remaining}
    save(args.results,data); write_csv(args.csv,items)
    st=load(args.state); st.update(data["summary"]); st["version"]="0.8"; st["phase"]="qc_refine_complete" if remaining==0 else "qc_refine"; st["updated_at"]=now(); save(args.state,st)
    print(json.dumps(data["summary"],ensure_ascii=False))

if __name__=="__main__": main()
