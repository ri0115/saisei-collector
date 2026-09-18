#!/usr/bin/env python3
import argparse,csv,json,re,time,unicodedata
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import quote_plus,urlparse,parse_qs,unquote,urljoin

import requests
from bs4 import BeautifulSoup

try:
    import pymupdf as fitz
except Exception:
    fitz=None

UA="Mozilla/5.0 (compatible; RegenMedOfficialFallback/1.0; +https://github.com/ri0115/saisei-collector)"
SEARCH="https://html.duckduckgo.com/html/?q="
EXCLUDE_DOMAINS=(
    "saiseiiryo.mhlw.go.jp","mhlw.go.jp","caloo.jp","medicaldoc.jp","byoinnavi.jp","hospitalnavi.jp",
    "mapion.co.jp","google.com","yahoo.co.jp","instagram.com","facebook.com","x.com","twitter.com",
    "prtimes.jp","youtube.com","note.com","ameblo.jp","wikipedia.org","doctorsfile.jp","qlife.jp",
)
PRICE_RE=re.compile(r"(?<!\d)(\d{1,3}(?:,\d{3})+|\d{4,8})\s*円|(?<!\d)(\d+(?:\.\d+)?)\s*万円")
ANC_RE=re.compile(r"初診|再診|診察料|検査料|血液検査|キャンセル|返金|保管|文書料|送料|手数料",re.I)
PRICE_WORD=re.compile(r"料金|費用|価格|治療費|施術料|自由診療|自費",re.I)

def now(): return datetime.now(timezone.utc).isoformat()

def norm(s):
    s=unicodedata.normalize("NFKC",s or "").lower()
    return re.sub(r"[\s　・･\-‐–—―_()（）\[\]【】「」『』,.，。、:：/／]", "", s)

def facility_core(name):
    s=unicodedata.normalize("NFKC",name or "")
    s=re.sub(r"医療法人社団|医療法人|社会医療法人社団|社会医療法人|一般社団法人|公益財団法人|学校法人|国立大学法人|独立行政法人|社会福祉法人","",s)
    return norm(s)

def get(url,timeout=25):
    r=requests.get(url,headers={"User-Agent":UA,"Accept":"text/html,application/pdf,*/*"},timeout=timeout,allow_redirects=True)
    r.raise_for_status()
    return r

def clean_result_url(href):
    if not href: return ""
    if href.startswith("//"): href="https:"+href
    u=urlparse(href)
    if "duckduckgo.com" in u.netloc and u.path.startswith("/l/"):
        q=parse_qs(u.query)
        if q.get("uddg"): return unquote(q["uddg"][0])
    return href if href.startswith("http") else ""

def search(query):
    r=get(SEARCH+quote_plus(query),30)
    soup=BeautifulSoup(r.text,"html.parser")
    out=[]
    for div in soup.select(".result"):
        a=div.select_one("a.result__a")
        if not a: continue
        url=clean_result_url(a.get("href",""))
        if not url: continue
        sn=div.select_one(".result__snippet")
        title=a.get_text(" ",strip=True)
        snippet=sn.get_text(" ",strip=True) if sn else ""
        out.append({"url":url,"title":title,"snippet":snippet})
    return out

def result_score(res,facility):
    core=facility_core(facility)
    title=norm(res["title"]); snippet=norm(res["snippet"]); url=res["url"].lower()
    if any(d in urlparse(url).netloc.lower() for d in EXCLUDE_DOMAINS): return -999
    sc=0
    if core and core in title: sc+=8
    elif core and core in snippet: sc+=5
    # fallback on distinctive tail tokens
    short=core[-12:] if len(core)>12 else core
    if short and short in title: sc+=3
    if re.search(r"料金|費用|再生医療|PRP|幹細胞",res["title"]+" "+res["snippet"],re.I): sc+=2
    if url.startswith("https://"): sc+=1
    return sc

def page_text(url):
    r=get(url,30)
    ct=(r.headers.get("content-type") or "").lower()
    if ("pdf" in ct or r.content[:4]==b"%PDF") and fitz:
        doc=fitz.open(stream=r.content,filetype="pdf")
        return "\n".join(p.get_text("text") or "" for p in doc),"pdf"
    soup=BeautifulSoup(r.text,"html.parser")
    for tag in soup(["script","style","noscript"]): tag.decompose()
    return soup.get_text("\n",strip=True),"html"

def parse_amount(m):
    if m.group(1):
        return int(m.group(1).replace(",",""))
    return int(round(float(m.group(2))*10000))

def extract_prices(text,tclass,treatment):
    t=unicodedata.normalize("NFKC",text or "")
    stem=bool(re.search(r"幹細胞|MSC|ASC|脂肪",tclass or "",re.I))
    terms=re.compile(r"幹細胞|脂肪由来|MSC|ASC|細胞" if stem else r"PRP|APS|ACP|GPS|多血小板|血小板|Condensia|Angel|PRGF",re.I)
    out=[];seen=set()
    for m in PRICE_RE.finditer(t):
        amount=parse_amount(m)
        if not (100000<=amount<=20000000) if stem else not (15000<=amount<=2000000):
            continue
        ctx=re.sub(r"\s+"," ",t[max(0,m.start()-180):min(len(t),m.end()+220)]).strip()
        score=0
        if PRICE_WORD.search(ctx): score+=4
        if terms.search(ctx): score+=6
        if ANC_RE.search(ctx) and not terms.search(ctx): score-=8
        if re.search(r"税込|税別|税抜",ctx): score+=1
        tax="不明"
        if re.search(r"税込|消費税込",ctx): tax="税込"
        elif re.search(r"税別|税抜",ctx): tax="税別"
        key=(amount,ctx[:180])
        if key in seen: continue
        seen.add(key)
        out.append({"amount":amount,"tax":tax,"score":score,"excerpt":ctx})
    out.sort(key=lambda x:(-x["score"],x["amount"]))
    return out[:12]

def query_for(group):
    fac=group[0]["facility"].replace("\r"," ").replace("\n"," ").strip()
    classes=" ".join(sorted({x.get("treatment_class","") for x in group}))
    if "脂肪" in classes or "幹細胞" in classes:
        kw="再生医療 幹細胞 PRP 料金"
    else:
        kw="PRP 再生医療 料金"
    return f'"{fac}" {kw}'

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--queue",default="results/v41_official_site_fallback_queue.json")
    ap.add_argument("--output",default="results/v41_official_fallback_prototype.json")
    ap.add_argument("--csv",default="results/v41_official_fallback_prototype.csv")
    ap.add_argument("--max-facilities",type=int,default=5)
    args=ap.parse_args()
    q=json.loads(Path(args.queue).read_text(encoding="utf-8"))
    groups={}
    for x in q["items"]: groups.setdefault(x["facility_id"],[]).append(x)
    selected=list(groups.items())[:args.max_facilities]
    results=[]
    for idx,(fid,plans) in enumerate(selected,1):
        facility=plans[0]["facility"]
        query=query_for(plans)
        rec={"facility_id":fid,"facility":facility,"prefecture":plans[0].get("prefecture"),"query":query,"search_results":[],"pages":[],"plans":[]}
        try:
            sr=search(query)
        except Exception as e:
            rec["search_error"]=f"{type(e).__name__}: {e}";results.append(rec);continue
        scored=[]
        for x in sr[:10]:
            sc=result_score(x,facility)
            if sc>-100: scored.append((sc,x))
        scored.sort(key=lambda z:-z[0])
        rec["search_results"]=[{"score":sc,**x} for sc,x in scored[:5]]
        pages=[]
        for sc,x in scored[:3]:
            if sc<4: continue
            try:
                txt,dtype=page_text(x["url"])
                pages.append({"url":x["url"],"title":x["title"],"search_score":sc,"document_type":dtype,"text":txt[:250000]})
                time.sleep(0.4)
            except Exception as e:
                rec["pages"].append({"url":x["url"],"error":f"{type(e).__name__}: {e}"})
        for p in pages:
            rec["pages"].append({k:v for k,v in p.items() if k!="text"})
        for plan in plans:
            cand=[]
            for p in pages:
                for price in extract_prices(p["text"],plan.get("treatment_class"),plan.get("treatment")):
                    z={**price,"source_url":p["url"],"search_score":p["search_score"],"title":p["title"]}
                    z["combined_score"]=price["score"]+p["search_score"]
                    cand.append(z)
            cand.sort(key=lambda z:(-z["combined_score"],z["amount"]))
            rec["plans"].append({
                "plan_id":plan["plan_id"],"treatment":plan["treatment"],"treatment_class":plan["treatment_class"],
                "candidates":cand[:10]
            })
        results.append(rec)
        print(idx,facility,len(rec["search_results"]),sum(len(x["candidates"]) for x in rec["plans"]),flush=True)
        time.sleep(1.0)

    out={"version":"v41-official-fallback-prototype-1.0","updated_at":now(),"facilities":len(results),"results":results}
    Path(args.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
    cols=["facility_id","facility","prefecture","plan_id","treatment_class","source_url","combined_score","amount","tax","excerpt"]
    with Path(args.csv).open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=cols);w.writeheader()
        for r in results:
            for p in r.get("plans",[]):
                for c in p.get("candidates",[]):
                    w.writerow({"facility_id":r["facility_id"],"facility":r["facility"],"prefecture":r.get("prefecture",""),
                                "plan_id":p["plan_id"],"treatment_class":p["treatment_class"],"source_url":c["source_url"],
                                "combined_score":c["combined_score"],"amount":c["amount"],"tax":c["tax"],"excerpt":c["excerpt"]})
    print(json.dumps({"facilities":len(results),"with_search":sum(bool(x.get("search_results")) for x in results),
                      "plans_with_candidates":sum(bool(p.get("candidates")) for x in results for p in x.get("plans",[]))},ensure_ascii=False))

if __name__=="__main__": main()
