#!/usr/bin/env python3
import argparse, csv, json, re, time, unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urljoin

import pymupdf as fitz
import requests
from bs4 import BeautifulSoup

BASE="https://saiseiiryo.mhlw.go.jp"
UA="Mozilla/5.0 (compatible; RegenMedCollector/0.5; +https://github.com/ri0115/saisei-collector)"
TIMEOUT=35

def now():
    return datetime.now(timezone.utc).isoformat()

def norm(s):
    s=unicodedata.normalize("NFKC", s or "").lower()
    return re.sub(r"[\s　・･\-‐–—―_()（）\[\]【】「」『』,.，。、:：/／]", "", s)

def get(url, retries=5):
    headers={"User-Agent":UA,"Accept":"text/html,application/pdf,*/*","Referer":BASE+"/"}
    last=None
    for n in range(retries):
        try:
            r=requests.get(url,headers=headers,timeout=TIMEOUT)
            if r.status_code in (403,429,500,502,503,504):
                last=RuntimeError(f"HTTP {r.status_code}")
                time.sleep(min(45, 5*(2**n)))
                continue
            r.raise_for_status()
            return r
        except Exception as e:
            last=e
            if n<retries-1:
                time.sleep(min(45, 4*(2**n)))
    raise last or RuntimeError("request failed")

def parse_index(kind):
    url=f"{BASE}/published_plan/index/{kind}"
    soup=BeautifulSoup(get(url).text,"html.parser")
    out=[]
    for tr in soup.select("tr"):
        td=tr.find_all("td")
        if len(td)<8:
            continue
        facility=td[0].get_text(" ",strip=True)
        pref=td[1].get_text(" ",strip=True)
        treatment=td[4].get_text(" ",strip=True)
        links=[(a.get_text(" ",strip=True),urljoin(url,a["href"])) for a in td[7].find_all("a",href=True)]
        if facility and treatment:
            out.append({"facility":facility,"prefecture":pref,"treatment":treatment,"links":links,"kind":str(kind)})
    return out

def load_indexes(cache_path):
    p=Path(cache_path)
    if p.exists():
        try:
            data=json.loads(p.read_text(encoding="utf-8"))
            if data.get("2") and data.get("3"):
                return data
        except Exception:
            pass
    data={"2":parse_index(2),"3":parse_index(3),"cached_at":now()}
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(data,ensure_ascii=False),encoding="utf-8")
    return data

def find_row(plan, rows):
    pf,pt=norm(plan["facility"]),norm(plan["treatment"])
    best=None
    best_score=-1
    for r in rows:
        if plan.get("prefecture") and r["prefecture"] and norm(plan["prefecture"])!=norm(r["prefecture"]):
            continue
        rf,rt=norm(r["facility"]),norm(r["treatment"])
        fs=SequenceMatcher(None,pf,rf).ratio()
        ts=SequenceMatcher(None,pt,rt).ratio()
        score=0.44*fs+0.56*ts
        if pf==rf:
            score+=0.12
        elif pf in rf or rf in pf:
            score+=0.05
        if pt==rt:
            score+=0.10
        if score>best_score:
            best_score=score
            best=r
    return best,min(best_score,1.0)

def pdf_text(data):
    doc=fitz.open(stream=data,filetype="pdf")
    return "\n".join(p.get_text("text") for p in doc)

def html_text(data):
    return BeautifulSoup(data,"html.parser").get_text("\n",strip=True)

def extract_document(url):
    r=get(url)
    ct=(r.headers.get("content-type") or "").lower()
    if "pdf" in ct or r.content[:4]==b"%PDF":
        try:
            return pdf_text(r.content),"pdf"
        except Exception:
            txt=r.content.decode("utf-8","ignore")
            if len(txt)>500:
                return txt,"raw"
            raise
    return html_text(r.content),"html"

def amount_candidates(text):
    t=unicodedata.normalize("NFKC",text or "")
    pats=[
        re.compile(r"(?<![0-9])([0-9]{1,3}(?:,[0-9]{3})+|[0-9]{4,8})\s*円"),
        re.compile(r"(?<![0-9])([0-9]+(?:\.[0-9]+)?)\s*万円"),
    ]
    out=[]
    seen=set()
    for pidx,pat in enumerate(pats):
        for m in pat.finditer(t):
            try:
                amount=int(float(m.group(1).replace(",",""))*(10000 if pidx==1 else 1))
            except Exception:
                continue
            if not 1000<=amount<=20000000:
                continue
            a=max(0,m.start()-150)
            b=min(len(t),m.end()+170)
            ctx=re.sub(r"\s+"," ",t[a:b]).strip()
            key=(amount,ctx)
            if key in seen:
                continue
            seen.add(key)
            score=0
            if re.search(r"治療費|施術料|料金|費用|価格|治療代|自己負担",ctx):
                score+=5
            if re.search(r"PRP|APS|ACP|GPS|幹細胞|再生医療|血小板",ctx,re.I):
                score+=2
            if re.search(r"税込|税別|税抜|消費税",ctx):
                score+=1
            if re.search(r"1回|一回|1部位|一部位|1施術|コース|回分",ctx):
                score+=1
            if re.search(r"キャンセル|返金|補償|賠償|損害|保険金|慰謝料",ctx):
                score-=8
            if re.search(r"検査料|診察料|初診料|再診料",ctx) and not re.search(r"治療費|施術料",ctx):
                score-=3
            tax="不明"
            if re.search(r"税込|消費税込",ctx):
                tax="税込"
            elif re.search(r"税別|税抜|消費税別",ctx):
                tax="税別"
            unit=""
            um=re.search(r"((?:1|一)\s*回(?:\s*[（(][^）)]{0,18}[）)])?|(?:1|一)\s*部位|[0-9]+\s*回(?:分|コース)?)",ctx)
            if um:
                unit=um.group(1)
            products=[]
            for token in ["ACP MAX","ACP","APS","GPS III","GPSⅢ","GPS","PEAK","Angel","TriCeLL","Mycells","Zimmer","Arthrex"]:
                if token.lower() in ctx.lower():
                    products.append(token)
            out.append({"amount":amount,"tax":tax,"unit":unit,"score":score,"products":products[:4],"excerpt":ctx})
    out.sort(key=lambda x:(-x["score"],x["amount"]))
    ded=[]
    keys=set()
    for x in out:
        k=(x["amount"],x["tax"],x["unit"],tuple(x["products"]))
        if k in keys:
            continue
        keys.add(k)
        ded.append(x)
    return ded[:20]

def classify(item):
    prices=item.get("prices") or []
    high=[p for p in prices if p.get("score",0)>=7]
    tclass=item.get("treatment_class","")
    min_auto=100000 if re.search(r"幹細胞|MSC|ASC|ADRC|脂肪",tclass,re.I) else 20000
    if item.get("error") and not prices:
        return "FAIL" if "not matched" in item["error"].lower() or "No explanation" in item["error"] else "QC"
    if item.get("match_score",0)<0.84:
        return "QC"
    if not high:
        return "QC"
    if len(high)>4:
        return "QC"
    if any(p.get("amount",0)<min_auto for p in high):
        return "QC"
    return "AUTO"

def process_one(plan,indexes):
    kind="2" if "第二" in plan.get("category","") else "3"
    result={**plan,"checked_at":now(),"status":"QC","match_score":0,"document_url":"","document_type":"","prices":[],"error":""}
    try:
        row,score=find_row(plan,indexes[kind])
        result["match_score"]=round(score,4)
        if not row or score<0.62:
            result["error"]="MHLW index row not matched"
            result["status"]="FAIL"
            return result
        links=row.get("links") or []
        doc=next((u for label,u in links if "資料1" in label), links[0][1] if links else None)
        if not doc:
            result["error"]="No explanation/consent document link"
            result["status"]="FAIL"
            return result
        result["document_url"]=doc
        text,dtype=extract_document(doc)
        result["document_type"]=dtype
        result["prices"]=amount_candidates(text)
        if not result["prices"]:
            result["error"]="Document fetched but no price candidate"
        result["status"]=classify(result)
    except Exception as e:
        result["status"]="FAIL"
        result["error"]=f"{type(e).__name__}: {e}"[:400]
    return result

def load_json(path,default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default

def save_json(path,obj):
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    Path(path).write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding="utf-8")

def write_csv(path,items):
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    cols=["plan_id","facility_id","prefecture","facility","category","treatment","status","match_score","document_url","amount","tax","unit","products","score","excerpt","error"]
    with open(path,"w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=cols)
        w.writeheader()
        for it in items:
            ps=it.get("prices") or [None]
            for p in ps:
                w.writerow({
                    "plan_id":it.get("plan_id",""),"facility_id":it.get("facility_id",""),"prefecture":it.get("prefecture",""),
                    "facility":it.get("facility",""),"category":it.get("category",""),"treatment":it.get("treatment",""),
                    "status":it.get("status",""),"match_score":it.get("match_score",""),"document_url":it.get("document_url",""),
                    "amount":"" if not p else p.get("amount",""),"tax":"" if not p else p.get("tax",""),
                    "unit":"" if not p else p.get("unit",""),"products":"" if not p else "|".join(p.get("products",[])),
                    "score":"" if not p else p.get("score",""),"excerpt":"" if not p else p.get("excerpt",""),
                    "error":it.get("error","")
                })

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--queue",default="data/queue_a_v40.json")
    ap.add_argument("--state",default="results/state.json")
    ap.add_argument("--results",default="results/latest.json")
    ap.add_argument("--csv",default="results/prices.csv")
    ap.add_argument("--index-cache",default="results/index_cache.json")
    ap.add_argument("--max-items",type=int,default=24)
    ap.add_argument("--workers",type=int,default=8)
    args=ap.parse_args()

    queue=load_json(args.queue,{})
    plans=queue.get("plans",[])
    old=load_json(args.results,{"items":[]})
    by_id={x["plan_id"]:x for x in old.get("items",[]) if x.get("plan_id")}
    for x in by_id.values():
        if x.get("prices"):
            x["status"]=classify(x)

    pending=[p for p in plans if p["plan_id"] not in by_id][:args.max_items]
    state=load_json(args.state,{"started_at":now()})
    state.update({"version":"0.5","queue_version":queue.get("version"),"total":len(plans),"updated_at":now(),"phase":"loading_indexes"})
    save_json(args.state,state)

    if pending:
        indexes=load_indexes(args.index_cache)
        state["phase"]="processing"
        state["index_counts"]={"2":len(indexes["2"]),"3":len(indexes["3"])}
        save_json(args.state,state)
        with ThreadPoolExecutor(max_workers=max(1,args.workers)) as ex:
            futs={ex.submit(process_one,p,indexes):p["plan_id"] for p in pending}
            for fut in as_completed(futs):
                item=fut.result()
                by_id[item["plan_id"]]=item
                print(item["plan_id"],item["status"],item.get("match_score"),len(item.get("prices",[])),flush=True)

    ordered=[by_id[p["plan_id"]] for p in plans if p["plan_id"] in by_id]
    counts={"AUTO":0,"QC":0,"FAIL":0}
    for x in ordered:
        counts[x.get("status","QC")]=counts.get(x.get("status","QC"),0)+1
    summary={"total":len(plans),"processed":len(ordered),"pending":len(plans)-len(ordered),**{k.lower():v for k,v in counts.items()}}
    latest={"version":"0.5","queue_version":queue.get("version"),"updated_at":now(),"summary":summary,"items":ordered}
    save_json(args.results,latest)
    write_csv(args.csv,ordered)
    state.update(summary)
    state["phase"]="complete" if summary["pending"]==0 else "checkpoint"
    state["updated_at"]=now()
    save_json(args.state,state)
    print(json.dumps(summary,ensure_ascii=False))

if __name__=="__main__":
    main()
