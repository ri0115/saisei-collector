#!/usr/bin/env python3
import json,re,unicodedata,time
from pathlib import Path
from urllib.parse import urljoin,urlparse

import requests
from bs4 import BeautifulSoup

UA="Mozilla/5.0 (compatible; RegenMedOfficialSeedCrawler/1.0; +https://github.com/ri0115/saisei-collector)"
REL=re.compile(r"PRP|APS|再生医療|幹細胞|料金|費用|自由診療|自費|regen|stem|price|fee|selfpay",re.I)
PRICE_RE=re.compile(r"(?<!\d)(\d{1,3}(?:,\d{3})+|\d{4,8})\s*円|(?<!\d)(\d+(?:\.\d+)?)\s*万円")
ANC=re.compile(r"初診|再診|診察料|検査料|血液検査|キャンセル|返金|保管|文書料|送料|手数料",re.I)
PRICE_WORD=re.compile(r"料金|費用|価格|治療費|施術料|自由診療|自費",re.I)

def norm(s):
    return unicodedata.normalize("NFKC",s or "")

def get(url):
    r=requests.get(url,headers={"User-Agent":UA,"Accept":"text/html,*/*"},timeout=30,allow_redirects=True)
    r.raise_for_status()
    return r

def text_and_links(url):
    r=get(url)
    soup=BeautifulSoup(r.text,"html.parser")
    for tag in soup(["script","style","noscript"]): tag.decompose()
    text=soup.get_text("\n",strip=True)
    base=urlparse(r.url)
    links=[]
    for a in soup.find_all("a",href=True):
        label=(a.get_text(" ",strip=True) or "")+" "+a.get("href","")
        if not REL.search(label): continue
        u=urljoin(r.url,a["href"])
        p=urlparse(u)
        if p.scheme not in ("http","https"): continue
        if p.netloc!=base.netloc: continue
        links.append(u.split("#")[0])
    return text,list(dict.fromkeys(links)),r.url

def amount(m):
    if m.group(1): return int(m.group(1).replace(",",""))
    return int(round(float(m.group(2))*10000))

def extract(text,tclass):
    t=norm(text)
    stem=bool(re.search(r"幹細胞|MSC|ASC|脂肪",tclass or "",re.I))
    treat=re.compile(r"幹細胞|脂肪由来|MSC|ASC|SVF|ADRC|細胞" if stem else r"PRP|APS|ACP|GPS|多血小板|血小板|Condensia|コンデンシア|Angel|PRGF",re.I)
    lo,hi=(100000,20000000) if stem else (15000,2000000)
    raw_lines=[re.sub(r"\\s+"," ",x).strip() for x in t.splitlines()]
    lines=[x for x in raw_lines if x]
    out=[];seen=set()
    for i,line in enumerate(lines):
        for m in PRICE_RE.finditer(line):
            a=amount(m)
            if not lo<=a<=hi: continue
            near=" ".join(lines[max(0,i-2):min(len(lines),i+3)])
            score=0
            if PRICE_WORD.search(line): score+=4
            elif PRICE_WORD.search(near): score+=2
            if treat.search(line): score+=8
            elif treat.search(near): score+=4
            if ANC.search(line) and not treat.search(line): score-=10
            elif ANC.search(near) and not treat.search(line): score-=4
            if re.search(r"税込|税別|税抜",line): score+=2
            elif re.search(r"税込|税別|税抜",near): score+=1
            if not treat.search(near):
                continue
            tax="不明"
            if re.search(r"税込|消費税込",near): tax="税込"
            elif re.search(r"税別|税抜",near): tax="税別"
            key=(a,line,near[:180])
            if key in seen: continue
            seen.add(key)
            out.append({"amount":a,"tax":tax,"score":score,"line":line,"excerpt":near})
    out.sort(key=lambda x:(-x["score"],x["amount"]))
    return out[:20]

def main():
    seeds=json.loads(Path("data/v41_official_url_seeds.json").read_text(encoding="utf-8"))
    queue=json.loads(Path("results/v41_official_site_fallback_queue.json").read_text(encoding="utf-8"))
    byfac={}
    for x in queue["items"]: byfac.setdefault(x["facility_id"],[]).append(x)

    results=[]
    for i,s in enumerate(seeds["items"],1):
        fid=s["facility_id"]; plans=byfac.get(fid,[])
        rec={"facility_id":fid,"facility":s["facility"],"seed_url":s["url"],"pages":[],"plans":[]}
        try:
            root_text,links,final_url=text_and_links(s["url"])
            pages=[(final_url,root_text)]
            for u in links[:10]:
                try:
                    txt,_,fu=text_and_links(u)
                    pages.append((fu,txt))
                    time.sleep(.25)
                except Exception as e:
                    rec["pages"].append({"url":u,"error":f"{type(e).__name__}: {e}"})
            uniq=[];seen=set()
            for u,txt in pages:
                if u in seen: continue
                seen.add(u);uniq.append((u,txt))
                rec["pages"].append({"url":u,"chars":len(txt)})
            for p in plans:
                cand=[]
                for u,txt in uniq:
                    for c in extract(txt,p.get("treatment_class")):
                        z={**c,"source_url":u}
                        z["combined_score"]=c["score"]
                        cand.append(z)
                # dedupe by amount/url/context
                ded=[];ks=set()
                for c in sorted(cand,key=lambda z:(-z["combined_score"],z["amount"])):
                    k=(c["amount"],c["source_url"],c["excerpt"][:120])
                    if k in ks: continue
                    ks.add(k);ded.append(c)
                rec["plans"].append({
                    "plan_id":p["plan_id"],"treatment_class":p.get("treatment_class"),
                    "treatment":p.get("treatment"),"candidates":ded[:15]
                })
        except Exception as e:
            rec["error"]=f"{type(e).__name__}: {e}"
        results.append(rec)
        print(i,s["facility"],sum(len(x.get("candidates",[])) for x in rec.get("plans",[])),flush=True)

    out={"version":"v41-seed-crawl-1.0","total_facilities":len(results),"results":results}
    Path("results/v41_official_seed_crawl.json").write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({
      "facilities":len(results),
      "plans":sum(len(x.get("plans",[])) for x in results),
      "plans_with_candidates":sum(bool(p.get("candidates")) for x in results for p in x.get("plans",[]))
    },ensure_ascii=False))

if __name__=="__main__": main()
