#!/usr/bin/env python3
import argparse,csv,json,re,time,unicodedata
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urljoin

import pymupdf as fitz
import requests
from bs4 import BeautifulSoup

BASE="https://saiseiiryo.mhlw.go.jp"
UA="Mozilla/5.0 (compatible; RegenMedMHLWMultiDoc/1.0; +https://github.com/ri0115/saisei-collector)"
PRICE_RE=[
    (re.compile(r"(?<![0-9])([0-9]{1,3}(?:,[0-9]{3})+|[0-9]{4,8})\s*円"),1),
    (re.compile(r"(?<![0-9])([0-9]+(?:\.[0-9]+)?)\s*万円"),10000),
]
FEE=re.compile(r"治療費|施術料|施術料金|治療料金|費用|料金|価格|自己負担|自由診療|自費",re.I)
ANC=re.compile(r"キャンセル|返金|初診料|再診料|診察料|検査料|血液検査|感染症検査|採血料|保管料|保存料|文書料|証明書|送料|手数料",re.I)

def now(): return datetime.now(timezone.utc).isoformat()

def get(url,retries=3):
    last=None
    for i in range(retries):
        try:
            r=requests.get(url,headers={"User-Agent":UA,"Accept":"application/pdf,text/html,*/*","Referer":BASE+"/"},timeout=22)
            if r.status_code in (403,429,500,502,503,504):
                last=RuntimeError(f"HTTP {r.status_code}")
                time.sleep(2*(i+1));continue
            r.raise_for_status();return r
        except Exception as e:
            last=e
            if i<retries-1: time.sleep(2*(i+1))
    raise last or RuntimeError("fetch failed")

def current_index():
    by_code={}
    for kind in (2,3):
        url=f"{BASE}/published_plan/index/{kind}"
        soup=BeautifulSoup(get(url).text,"html.parser")
        for tr in soup.select("tr"):
            td=tr.find_all("td")
            if len(td)<8: continue
            facility=td[0].get_text(" ",strip=True)
            pref=td[1].get_text(" ",strip=True)
            treatment=td[4].get_text(" ",strip=True)
            links=[]
            for a in td[7].find_all("a",href=True):
                u=urljoin(url,a.get("href",""))
                label=a.get_text(" ",strip=True)
                m=re.search(r"/published_plan/download/([^/]+)/",u)
                if m:
                    links.append({"label":label,"url":u,"code":m.group(1)})
            codes={x["code"] for x in links}
            for code in codes:
                by_code[code]={
                    "kind":str(kind),"facility":facility,"prefecture":pref,
                    "treatment":treatment,
                    "links":[{"label":x["label"],"url":x["url"]} for x in links if x["code"]==code]
                }
    return by_code

def doc_text(url):
    r=get(url)
    ct=(r.headers.get("content-type") or "").lower()
    if r.content[:4]==b"%PDF" or "pdf" in ct:
        doc=fitz.open(stream=r.content,filetype="pdf")
        return "\n".join(p.get_text("text") or "" for p in doc),"pdf",len(doc)
    soup=BeautifulSoup(r.text,"html.parser")
    for tag in soup(["script","style","noscript"]): tag.decompose()
    return soup.get_text("\n",strip=True),"html",1

def tclass_terms(tclass,treatment):
    s=(tclass or "")+" "+(treatment or "")
    if re.search(r"幹細胞|MSC|ASC|ADRC|脂肪|SVF|MFAT|滑膜",s,re.I):
        return re.compile(r"幹細胞|間葉系|MSC|ASC|ADRC|脂肪由来|SVF|MFAT|滑膜|細胞",re.I),100000
    if re.search(r"PRP|APS|PRGF|多血小板",s,re.I):
        return re.compile(r"PRP|APS|ACP|GPS|PRGF|Endoret|多血小板|血小板|Condensia|コンデンシア|Angel",re.I),15000
    return re.compile(r"治療|移植|軟骨|関節|筋骨格|疼痛|細胞",re.I),15000

def extract(text,tclass,treatment):
    t=unicodedata.normalize("NFKC",text or "")
    lines=[re.sub(r"\s+"," ",x).strip() for x in t.splitlines() if x.strip()]
    terms,min_amt=tclass_terms(tclass,treatment)
    out=[];seen=set()
    for i,line in enumerate(lines):
        near=" ".join(lines[max(0,i-2):min(len(lines),i+3)])
        for pat,mult in PRICE_RE:
            for m in pat.finditer(line):
                try: amt=int(float(m.group(1).replace(",",""))*mult)
                except: continue
                if not min_amt<=amt<=20000000: continue
                score=0
                if FEE.search(line): score+=7
                elif FEE.search(near): score+=4
                if terms.search(line): score+=7
                elif terms.search(near): score+=4
                if re.search(r"税込|税別|税抜|消費税",line): score+=2
                elif re.search(r"税込|税別|税抜|消費税",near): score+=1
                if ANC.search(line) and not terms.search(line): score-=12
                elif ANC.search(near) and not terms.search(line): score-=5
                tax="不明"
                if re.search(r"税込|消費税込",near): tax="税込"
                elif re.search(r"税別|税抜|別途消費税",near): tax="税別"
                unit=""
                um=re.search(r"((?:1|一)\s*回|(?:1|一)\s*部位|片膝|両膝|片側|両側|[0-9]+\s*回(?:分|コース)?|[0-9]+\s*クール|[0-9.,]+\s*(?:万|億)?\s*(?:cells|個))",near,re.I)
                if um: unit=um.group(1)
                key=(amt,line,near[:180])
                if key in seen: continue
                seen.add(key)
                out.append({"amount":amt,"tax":tax,"unit":unit,"score":score,"line":line,"excerpt":near})
    out.sort(key=lambda x:(-x["score"],x["amount"]))
    return out[:40]

def process(plan,row):
    links=row.get("links") or []
    docs=[]
    all_candidates=[]
    for link in links:
        u=link["url"]
        try:
            txt,dtype,pages=doc_text(u)
            cands=extract(txt,plan.get("treatment_class"),plan.get("treatment"))
            docs.append({
                "label":link.get("label",""),"url":u,"document_type":dtype,
                "pages":pages,"chars":len(txt),"price_candidates":len(cands)
            })
            for c in cands:
                z=dict(c);z["source_url"]=u;z["source_label"]=link.get("label","")
                all_candidates.append(z)
        except Exception as e:
            docs.append({"label":link.get("label",""),"url":u,"error":f"{type(e).__name__}: {e}"[:300]})
    # deduplicate same amount/line across repeated attachments
    ded=[];seen=set()
    for c in sorted(all_candidates,key=lambda x:(-x["score"],x["amount"])):
        k=(c["amount"],c.get("source_url"),c.get("line",""))
        if k in seen: continue
        seen.add(k);ded.append(c)
    strong=[x for x in ded if x["score"]>=11]
    medium=[x for x in ded if x["score"]>=8]
    return {
        **{k:plan.get(k) for k in ("plan_id","mhlw_plan_code","facility_id","prefecture","facility","category","treatment","treatment_class","document_url")},
        "current_index_facility":row.get("facility"),"current_index_treatment":row.get("treatment"),
        "documents":docs,"candidates":ded[:50],
        "strong_candidates":len(strong),"medium_candidates":len(medium),
        "rescue_status":"STRONG" if strong else ("MEDIUM" if medium else "NO_PRICE")
    }

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--queue",default="results/v41_official_site_fallback_queue.json")
    ap.add_argument("--output",default="results/v41_mhlw_multidoc_rescue.json")
    ap.add_argument("--csv",default="results/v41_mhlw_multidoc_rescue.csv")
    ap.add_argument("--workers",type=int,default=8)
    ap.add_argument("--max-plans",type=int,default=0)
    args=ap.parse_args()

    q=json.loads(Path(args.queue).read_text(encoding="utf-8"))
    plans=q.get("items",[])
    if args.max_plans>0: plans=plans[:args.max_plans]
    idx=current_index()
    results=[];missing=[]
    with ThreadPoolExecutor(max_workers=max(1,args.workers)) as ex:
        futs={}
        for p in plans:
            code=p.get("mhlw_plan_code") or (p.get("plan_id","").replace("V41_",""))
            row=idx.get(code)
            if not row:
                missing.append(p.get("plan_id"));continue
            futs[ex.submit(process,p,row)]=p.get("plan_id")
        done=0
        for fut in as_completed(futs):
            x=fut.result();results.append(x);done+=1
            print(done,"/",len(futs),x["plan_id"],x["rescue_status"],len(x["documents"]),len(x["candidates"]),flush=True)

    results.sort(key=lambda x:(x.get("prefecture",""),x.get("facility",""),x.get("plan_id","")))
    summary={
        "version":"v41-mhlw-multidoc-1.0","generated_at":now(),
        "input_total":len(plans),"processed":len(results),"index_missing":len(missing),
        "strong":sum(x["rescue_status"]=="STRONG" for x in results),
        "medium":sum(x["rescue_status"]=="MEDIUM" for x in results),
        "no_price":sum(x["rescue_status"]=="NO_PRICE" for x in results),
        "plans_with_more_than_one_document":sum(len(x["documents"])>1 for x in results),
        "plans_where_nonzero_document_has_price":sum(any(d.get("price_candidates",0)>0 and not d.get("url","").endswith("/5/0") for d in x["documents"]) for x in results)
    }
    Path(args.output).write_text(json.dumps({"summary":summary,"missing":missing,"items":results},ensure_ascii=False,indent=2),encoding="utf-8")

    cols=["plan_id","facility","prefecture","treatment_class","rescue_status","source_label","source_url","amount","tax","unit","score","line","excerpt"]
    with Path(args.csv).open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=cols);w.writeheader()
        for x in results:
            for c in x["candidates"]:
                w.writerow({
                    "plan_id":x.get("plan_id"),"facility":x.get("facility"),"prefecture":x.get("prefecture"),
                    "treatment_class":x.get("treatment_class"),"rescue_status":x.get("rescue_status"),
                    "source_label":c.get("source_label"),"source_url":c.get("source_url"),
                    "amount":c.get("amount"),"tax":c.get("tax"),"unit":c.get("unit"),"score":c.get("score"),
                    "line":c.get("line"),"excerpt":c.get("excerpt")
                })
    print(json.dumps(summary,ensure_ascii=False))

if __name__=="__main__": main()
