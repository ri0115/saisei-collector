#!/usr/bin/env python3
import argparse,csv,json,re,time,unicodedata
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timezone
from pathlib import Path

import requests
import pymupdf as fitz
from bs4 import BeautifulSoup

UA="Mozilla/5.0 (compatible; RegenMedMHLWAllDocs/1.0; +https://github.com/ri0115/saisei-collector)"
BASE="https://saiseiiryo.mhlw.go.jp/published_plan/download"
PRICE_RE=re.compile(r"(?<!\d)(\d{1,3}(?:,\d{3})+|\d{4,8})\s*円|(?<!\d)(\d+(?:\.\d+)?)\s*万円")
PRICE_WORD=re.compile(r"治療費|施術料|料金|費用|価格|自己負担|自費|自由診療",re.I)
ANC=re.compile(r"初診|再診|診察料|検査料|血液検査|キャンセル|返金|保管|文書料|送料|手数料",re.I)

def now(): return datetime.now(timezone.utc).isoformat()

def get(url):
    r=requests.get(url,headers={"User-Agent":UA,"Accept":"application/pdf,text/html,*/*","Referer":"https://saiseiiryo.mhlw.go.jp/"},timeout=30)
    if r.status_code==404: return None
    r.raise_for_status(); return r

def doc_pages(r):
    ct=(r.headers.get("content-type") or "").lower()
    if r.content[:4]==b"%PDF" or "pdf" in ct:
        doc=fitz.open(stream=r.content,filetype="pdf")
        return [(i+1,p.get_text("text") or "") for i,p in enumerate(doc)],"pdf"
    soup=BeautifulSoup(r.text,"html.parser")
    return [(1,soup.get_text("\n",strip=True))],"html"

def amount(m):
    if m.group(1): return int(m.group(1).replace(",",""))
    return int(round(float(m.group(2))*10000))

def target_regex(tclass,treatment):
    s=unicodedata.normalize("NFKC",(tclass or "")+" "+(treatment or ""))
    if re.search(r"APS|多血小板血漿抽出液",s,re.I): return re.compile(r"APS|多血小板血漿抽出液",re.I),False
    if re.search(r"PRGF|Endoret",s,re.I): return re.compile(r"PRGF|Endoret",re.I),False
    if re.search(r"脂肪|ASC|ADRC",s,re.I) and re.search(r"幹細胞|MSC|間葉系|ASC|ADRC",s,re.I):
        return re.compile(r"脂肪由来|ASC|ADRC|幹細胞|間葉系",re.I),True
    if re.search(r"滑膜",s,re.I) and re.search(r"幹細胞|間葉系",s,re.I):
        return re.compile(r"滑膜|幹細胞|間葉系",re.I),True
    if re.search(r"骨髄",s,re.I) and re.search(r"幹細胞|MSC|間葉系",s,re.I):
        return re.compile(r"骨髄|幹細胞|間葉系",re.I),True
    if re.search(r"幹細胞|MSC|間葉系",s,re.I):
        return re.compile(r"幹細胞|MSC|間葉系",re.I),True
    return re.compile(r"PRP|ACP|GPS|多血小板血漿|血小板|Condensia|コンデンシア|Angel",re.I),False

def extract(pages,tclass,treatment):
    target,stem=target_regex(tclass,treatment)
    lo,hi=(100000,20000000) if stem else (15000,2000000)
    out=[];seen=set()
    for page_no,text in pages:
        t=unicodedata.normalize("NFKC",text or "")
        lines=[re.sub(r"\s+"," ",x).strip() for x in t.splitlines() if x.strip()]
        for i,line in enumerate(lines):
            for m in PRICE_RE.finditer(line):
                a=amount(m)
                if not lo<=a<=hi: continue
                near=" ".join(lines[max(0,i-3):min(len(lines),i+4)])
                score=0
                if PRICE_WORD.search(line): score+=7
                elif PRICE_WORD.search(near): score+=3
                if target.search(line): score+=7
                elif target.search(near): score+=3
                if ANC.search(line) and not target.search(line): score-=10
                elif ANC.search(near) and not target.search(line): score-=4
                if re.search(r"税込|税別|税抜",line): score+=2
                elif re.search(r"税込|税別|税抜",near): score+=1
                tax="不明"
                if re.search(r"税込|消費税込",near): tax="税込"
                elif re.search(r"税別|税抜",near): tax="税別"
                key=(a,page_no,line)
                if key in seen: continue
                seen.add(key)
                out.append({"amount":a,"tax":tax,"score":score,"page":page_no,"line":line,"excerpt":near})
    out.sort(key=lambda x:(-x["score"],x["amount"]))
    return out[:30]

def process(item,max_doc_index):
    code=item.get("mhlw_plan_code") or item.get("plan_id","").replace("V41_","")
    rec={k:item.get(k) for k in ["plan_id","mhlw_plan_code","facility_id","prefecture","facility","category","treatment","treatment_class"]}
    rec["documents"]=[]
    best=[]
    # /5/0 is already read; focus on additional documents first but include 0 for audit.
    for idx in range(max_doc_index+1):
        url=f"{BASE}/{code}/5/{idx}"
        try:
            r=get(url)
            if r is None: continue
            pages,dtype=doc_pages(r)
            text_chars=sum(len(x[1]) for x in pages)
            cand=extract(pages,item.get("treatment_class"),item.get("treatment"))
            rec["documents"].append({"index":idx,"url":url,"type":dtype,"pages":len(pages),"text_chars":text_chars,"candidates":cand})
            for c in cand:
                best.append({**c,"document_index":idx,"source_url":url})
        except Exception as e:
            rec["documents"].append({"index":idx,"url":url,"error":f"{type(e).__name__}: {e}"})
        time.sleep(.08)
    best.sort(key=lambda x:(-x["score"],x["amount"]))
    rec["candidates"]=best[:40]
    rec["additional_doc_candidates"]=[x for x in best if x["document_index"]>0][:30]
    return rec

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--queue",default="results/v41_official_site_fallback_queue.json")
    ap.add_argument("--out",default="results/v41_mhlw_all_docs_fallback.json")
    ap.add_argument("--csv",default="results/v41_mhlw_all_docs_fallback.csv")
    ap.add_argument("--workers",type=int,default=8)
    ap.add_argument("--max-doc-index",type=int,default=3)
    args=ap.parse_args()
    q=json.loads(Path(args.queue).read_text(encoding="utf-8"))
    items=q.get("items",[])
    results=[]
    with ThreadPoolExecutor(max_workers=max(1,args.workers)) as ex:
        futs={ex.submit(process,x,args.max_doc_index):x["plan_id"] for x in items}
        for n,fut in enumerate(as_completed(futs),1):
            r=fut.result();results.append(r)
            print(n,r["plan_id"],len(r.get("documents",[])),len(r.get("additional_doc_candidates",[])),flush=True)
    results.sort(key=lambda x:x["plan_id"])
    summary={
        "version":"v41-mhlw-all-docs-1.0","updated_at":now(),"total":len(results),
        "plans_with_additional_documents":sum(any(d.get("index",0)>0 and not d.get("error") for d in x["documents"]) for x in results),
        "plans_with_additional_doc_candidates":sum(bool(x.get("additional_doc_candidates")) for x in results),
        "high_confidence_additional":sum(any(c.get("score",0)>=9 for c in x.get("additional_doc_candidates",[])) for x in results)
    }
    Path(args.out).write_text(json.dumps({"summary":summary,"items":results},ensure_ascii=False,indent=2),encoding="utf-8")
    cols=["plan_id","facility","prefecture","treatment_class","document_index","source_url","amount","tax","score","page","line","excerpt"]
    with Path(args.csv).open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=cols);w.writeheader()
        for x in results:
            for c in x.get("additional_doc_candidates",[]):
                w.writerow({"plan_id":x["plan_id"],"facility":x.get("facility",""),"prefecture":x.get("prefecture",""),
                    "treatment_class":x.get("treatment_class",""),"document_index":c["document_index"],"source_url":c["source_url"],
                    "amount":c["amount"],"tax":c["tax"],"score":c["score"],"page":c["page"],"line":c["line"],"excerpt":c["excerpt"]})
    print(json.dumps(summary,ensure_ascii=False))

if __name__=="__main__": main()
