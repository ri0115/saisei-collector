#!/usr/bin/env python3
import argparse, json, re, time, unicodedata, csv
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from io import BytesIO

import pymupdf as fitz
import requests
from bs4 import BeautifulSoup

try:
    import pytesseract
    from PIL import Image
    OCR_OK=True
except Exception:
    OCR_OK=False

BASE="https://saiseiiryo.mhlw.go.jp"
UA="Mozilla/5.0 (compatible; RegenMedCollector-QC/0.6; +https://github.com/ri0115/saisei-collector)"
TIMEOUT=30

def now():
    return datetime.now(timezone.utc).isoformat()

def get(url,retries=3):
    headers={"User-Agent":UA,"Accept":"application/pdf,text/html,*/*","Referer":BASE+"/"}
    last=None
    for n in range(retries):
        try:
            r=requests.get(url,headers=headers,timeout=TIMEOUT)
            if r.status_code in (403,429,500,502,503,504):
                last=RuntimeError(f"HTTP {r.status_code}")
                time.sleep(min(60, 6*(2**n)))
                continue
            r.raise_for_status()
            return r
        except Exception as e:
            last=e
            if n<retries-1: time.sleep(min(60,5*(2**n)))
    raise last or RuntimeError("request failed")

def pdf_pages(data):
    doc=fitz.open(stream=data,filetype="pdf")
    pages=[]
    for p in doc:
        pages.append(p.get_text("text") or "")
    return pages,doc

def ocr_doc(doc,max_pages=6):
    if not OCR_OK:
        return []
    out=[]
    for i,p in enumerate(doc):
        if i>=max_pages: break
        pix=p.get_pixmap(matrix=fitz.Matrix(1.6,1.6),alpha=False)
        img=Image.open(BytesIO(pix.tobytes("png")))
        txt=pytesseract.image_to_string(img,lang="jpn+eng",config="--psm 6",timeout=20)
        out.append(txt or "")
    return out

def extract_pages(url,use_ocr=True):
    r=get(url)
    ct=(r.headers.get("content-type") or "").lower()
    if r.content[:4]==b"%PDF" or "pdf" in ct:
        try:
            pages,doc=pdf_pages(r.content)
        except Exception:
            txt=BeautifulSoup(r.content,"html.parser").get_text("\n",strip=True)
            return [txt],"html"
        joined="\n".join(pages)
        if use_ocr and (len(joined.strip())<500 or "円" not in joined):
            ocr=ocr_doc(doc)
            if any(x.strip() for x in ocr):
                pages=ocr
                return pages,"ocr"
        return pages,"pdf"
    return [BeautifulSoup(r.content,"html.parser").get_text("\n",strip=True)],"html"

AMOUNT_PATTERNS=[
    (re.compile(r"(?<![0-9])([0-9]{1,3}(?:,[0-9]{3})+|[0-9]{4,8})\s*円"),1),
    (re.compile(r"(?<![0-9])([0-9]+(?:\.[0-9]+)?)\s*万円"),10000),
]

POS_PRICE=r"治療費|施術料|治療料金|料金|費用|価格|自己負担|自由診療|自費"
POS_TREAT=r"PRP|APS|ACP|GPS|幹細胞|再生医療|血小板|投与|注入|関節|腱|靭帯|筋|脂肪"
NEG_ANC=r"初診|再診|診察料|検査料|感染症検査|血液検査|採血料|キャンセル|返金|文書料|証明書|送料|保管料|再処理|研究費|補償|賠償"
COURSE=r"1回|一回|1部位|一部位|片側|両側|回分|コース|クール|投与"

def classify_line(line,near):
    score=0
    if re.search(POS_PRICE,line,re.I): score+=7
    elif re.search(POS_PRICE,near,re.I): score+=3
    if re.search(POS_TREAT,line,re.I): score+=5
    elif re.search(POS_TREAT,near,re.I): score+=2
    if re.search(COURSE,line): score+=2
    elif re.search(COURSE,near): score+=1
    if re.search(r"税込|税別|税抜|消費税",line): score+=2
    elif re.search(r"税込|税別|税抜|消費税",near): score+=1
    if re.search(NEG_ANC,line) and not re.search(POS_TREAT,line,re.I): score-=10
    elif re.search(NEG_ANC,line) and not re.search(POS_PRICE,line,re.I): score-=5
    return score

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
                    um=re.search(r"((?:1|一)\s*回(?:\s*[（(][^）)]{0,20}[）)])?|(?:1|一)\s*部位|片側|両側|[0-9]+\s*回(?:分|コース)?|[0-9]+\s*クール)",near)
                    if um: unit=um.group(1)
                    products=[]
                    for token in ["ACP MAX","ACP","APS","GPS III","GPSⅢ","GPS","PEAK","Angel","TriCeLL","Mycells","Zimmer","Arthrex","Condensia","コンデンシア","PRGF","Endoret","MAGELLAN","マゼラン"]:
                        if token.lower() in near.lower(): products.append(token)
                    score=classify_line(line,near)
                    key=(amount,tax,unit,line)
                    if key in seen: continue
                    seen.add(key)
                    out.append({"amount":amount,"tax":tax,"unit":unit,"score":score,"products":products[:4],"excerpt":near,"page":page_no,"line":line})
    out.sort(key=lambda x:(-x["score"],x["amount"]))
    return out[:30]

def treatment_min(item):
    t=(item.get("treatment_class","")+" "+item.get("treatment",""))
    return 100000 if re.search(r"幹細胞|MSC|ASC|ADRC|脂肪由来",t,re.I) else 15000

def finalize(item,cands,doc_type):
    item["qc_refined_at"]=now()
    item["document_type"]=doc_type
    item["prices_refined"]=cands
    strong=[p for p in cands if p["score"]>=9 and p["amount"]>=treatment_min(item)]
    medium=[p for p in cands if p["score"]>=7 and p["amount"]>=treatment_min(item)]
    # Strong lines can be accepted even when multiple product/course prices are listed.
    if item.get("match_score",0)>=0.84 and 1<=len(strong)<=16:
        item["prices"]=strong
        item["status"]="AUTO"
        item["error"]=""
        item["qc_reason"]="line-level strong price context"
    elif item.get("match_score",0)>=0.84 and 1<=len(medium)<=6:
        item["prices"]=medium
        item["status"]="AUTO"
        item["error"]=""
        item["qc_reason"]="line-level medium price context"
    elif cands:
        item["prices"]=cands[:16]
        item["status"]="QC"
        item["error"]=""
        item["qc_reason"]="price candidates remain ambiguous"
    else:
        item["status"]="QC"
        item["qc_reason"]="no price found after text/OCR pass"
        if not item.get("error"): item["error"]="No price found after refined text/OCR pass"
    return item

def refine_one(item):
    url=item.get("document_url")
    if not url:
        item["qc_refined_at"]=now()
        item["status"]="QC"
        item["qc_reason"]="no document URL; retained as verified-plan QC"
        if not item.get("error"): item["error"]="No document URL"
        return item
    try:
        pages,dtype=extract_pages(url,True)
        cands=extract_candidates(pages)
        return finalize(item,cands,dtype)
    except Exception as e:
        item["status"]="FAIL"
        item["qc_refine_failed_at"]=now()
        item["qc_reason"]="document fetch/parse failed; deferred to terminal rescue"
        item["error"]=f"{type(e).__name__}: {e}"[:400]
        return item

def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))

def save(path,obj):
    Path(path).write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding="utf-8")

def write_csv(path,items):
    cols=["plan_id","facility_id","prefecture","facility","category","treatment","status","match_score","document_url","amount","tax","unit","products","score","page","excerpt","qc_reason","error"]
    with open(path,"w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=cols); w.writeheader()
        for it in items:
            for p in (it.get("prices") or [None]):
                w.writerow({
                    "plan_id":it.get("plan_id",""),"facility_id":it.get("facility_id",""),"prefecture":it.get("prefecture",""),
                    "facility":it.get("facility",""),"category":it.get("category",""),"treatment":it.get("treatment",""),
                    "status":it.get("status",""),"match_score":it.get("match_score",""),"document_url":it.get("document_url",""),
                    "amount":"" if not p else p.get("amount",""),"tax":"" if not p else p.get("tax",""),"unit":"" if not p else p.get("unit",""),
                    "products":"" if not p else "|".join(p.get("products",[])),"score":"" if not p else p.get("score",""),
                    "page":"" if not p else p.get("page",""),"excerpt":"" if not p else p.get("excerpt",""),
                    "qc_reason":it.get("qc_reason",""),"error":it.get("error","")
                })

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--results",default="results/latest.json")
    ap.add_argument("--state",default="results/state.json")
    ap.add_argument("--csv",default="results/prices.csv")
    ap.add_argument("--max-items",type=int,default=5)
    ap.add_argument("--workers",type=int,default=2)
    args=ap.parse_args()
    data=load(args.results)
    items=data.get("items",[])
    targets=[x for x in items if x.get("status") in ("QC","FAIL") and not x.get("qc_refined_at") and not x.get("qc_refine_failed_at")][:args.max_items]
    if targets:
        by={x["plan_id"]:x for x in items}
        with ThreadPoolExecutor(max_workers=max(1,args.workers)) as ex:
            futs={ex.submit(refine_one,dict(x)):x["plan_id"] for x in targets}
            for fut in as_completed(futs):
                x=fut.result();by[x["plan_id"]]=x
                print(x["plan_id"],x["status"],x.get("document_type"),len(x.get("prices") or []),x.get("qc_reason",""),flush=True)
        items=[by[x["plan_id"]] for x in items]
    counts={"AUTO":0,"QC":0,"FAIL":0}
    for x in items: counts[x.get("status","QC")]=counts.get(x.get("status","QC"),0)+1
    remaining=sum(1 for x in items if x.get("status") in ("QC","FAIL") and not x.get("qc_refined_at") and not x.get("qc_refine_failed_at"))
    data["version"]="0.7";data["updated_at"]=now();data["items"]=items
    data["summary"]={"total":len(items),"processed":len(items),"pending":0,"auto":counts["AUTO"],"qc":counts["QC"],"fail":counts["FAIL"],"qc_refine_remaining":remaining}
    save(args.results,data);write_csv(args.csv,items)
    st=load(args.state)
    st.update(data["summary"]);st["version"]="0.7";st["phase"]="qc_refine_complete" if remaining==0 else "qc_refine";st["updated_at"]=now()
    save(args.state,st)
    print(json.dumps(data["summary"],ensure_ascii=False))

if __name__=="__main__":
    main()
