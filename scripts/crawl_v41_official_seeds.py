#!/usr/bin/env python3
import json,re,unicodedata,time
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    r=requests.get(url,headers={"User-Agent":UA,"Accept":"text/html,*/*"},timeout=12,allow_redirects=True)
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

def target_regex(tclass,treatment):
    tc=(tclass or "")+" "+(treatment or "")
    if re.search(r"APS|多血小板血漿抽出液",tc,re.I):
        return re.compile(r"APS|多血小板血漿抽出液",re.I), "APS"
    if re.search(r"PRGF|Endoret",tc,re.I):
        return re.compile(r"PRGF|Endoret",re.I), "PRGF"
    if re.search(r"SVF|間質血管",tc,re.I):
        return re.compile(r"SVF|間質血管",re.I), "SVF"
    if re.search(r"滑膜",tc,re.I) and re.search(r"幹細胞|間葉系",tc,re.I):
        return re.compile(r"滑膜.*(?:幹細胞|間葉系)|(?:幹細胞|間葉系).*滑膜",re.I), "synovial_stem"
    if re.search(r"脂肪由来|ASC|ADRC",tc,re.I) and re.search(r"幹細胞|MSC|間葉系|ASC|ADRC",tc,re.I):
        return re.compile(r"ASC|ADRC|脂肪由来(?:間葉系)?幹細胞|脂肪由来(?:間葉系)?細胞",re.I), "adipose_stem"
    if re.search(r"幹細胞|MSC|間葉系",tc,re.I):
        return re.compile(r"幹細胞|MSC|間葉系",re.I), "stem"
    return re.compile(r"PRP|ACP(?: MAX)?|GPS(?:III| III|Ⅲ)?|多血小板血漿|血小板|Condensia|コンデンシア|Angel",re.I), "PRP"

def other_therapy_regex(target_kind):
    pats={
        "APS": r"PRP|ACP|GPS|Condensia|コンデンシア|Angel|ASC|SVF|幹細胞",
        "PRGF": r"APS|ACP|GPS|Condensia|コンデンシア|Angel|ASC|SVF|幹細胞",
        "SVF": r"ASC|ADRC|脂肪由来.*幹細胞|PRP|APS|ACP|GPS",
        "adipose_stem": r"SVF|間質血管|PRP|APS|ACP|GPS",
        "synovial_stem": r"脂肪由来|ASC|SVF|PRP|APS",
        "stem": r"PRP|APS|ACP|GPS",
        "PRP": r"ASC|ADRC|SVF|間質血管|脂肪由来.*幹細胞",
    }
    return re.compile(pats.get(target_kind,r"$^"),re.I)

def extract(text,tclass,treatment=""):
    t=norm(text)
    target,target_kind=target_regex(tclass,treatment)
    other=other_therapy_regex(target_kind)
    stem=target_kind in ("SVF","adipose_stem","synovial_stem","stem")
    lo,hi=(100000,20000000) if stem else (15000,2000000)
    raw_lines=[re.sub(r"\s+"," ",x).strip() for x in t.splitlines()]
    lines=[x for x in raw_lines if x]
    out=[];seen=set()

    for i,line in enumerate(lines):
        for m in PRICE_RE.finditer(line):
            a=amount(m)
            if not lo<=a<=hi:
                continue

            prev=lines[i-1] if i>0 else ""
            nxt=lines[i+1] if i+1<len(lines) else ""
            same_target=bool(target.search(line))
            prev_target=bool(target.search(prev))
            next_target=bool(target.search(nxt))

            # Reject rows explicitly labeled as another therapy unless target is
            # also present in that same row.
            if other.search(line) and not same_target:
                continue

            # Require a target label either on the same row or an immediately
            # adjacent row. This prevents a page-wide PRP/ASC/SVF mix-up.
            if not (same_target or prev_target or next_target):
                continue

            near=" ".join(lines[max(0,i-1):min(len(lines),i+2)])
            score=0
            if same_target: score+=10
            elif prev_target: score+=7
            elif next_target: score+=5
            if PRICE_WORD.search(line): score+=4
            elif PRICE_WORD.search(near): score+=2
            if ANC.search(line) and not same_target: score-=10
            elif ANC.search(near) and not same_target: score-=4
            if re.search(r"税込|税別|税抜",line): score+=2
            elif re.search(r"税込|税別|税抜",near): score+=1

            tax="不明"
            if re.search(r"税込|消費税込",near): tax="税込"
            elif re.search(r"税別|税抜",near): tax="税別"

            key=(a,line,target_kind)
            if key in seen: continue
            seen.add(key)
            out.append({
                "amount":a,"tax":tax,"score":score,"line":line,
                "excerpt":near,"target_kind":target_kind,
                "label_match":"same_line" if same_target else ("previous_line" if prev_target else "next_line")
            })
    out.sort(key=lambda x:(-x["score"],x["amount"]))
    return out[:20]

def crawl_seed(s,byfac):
    fid=s["facility_id"]; plans=byfac.get(fid,[])
    rec={"facility_id":fid,"facility":s["facility"],"seed_url":s["url"],"pages":[],"plans":[]}
    try:
        root_text,links,final_url=text_and_links(s["url"])
        pages=[(final_url,root_text)]
        child_urls=links[:6]
        if child_urls:
            with ThreadPoolExecutor(max_workers=3) as ex:
                futs={ex.submit(text_and_links,u):u for u in child_urls}
                for fut in as_completed(futs):
                    u=futs[fut]
                    try:
                        txt,_,fu=fut.result()
                        pages.append((fu,txt))
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
                for cc in extract(txt,p.get("treatment_class"),p.get("treatment","")):
                    z={**cc,"source_url":u}
                    z["combined_score"]=cc["score"]
                    cand.append(z)
            ded=[];ks=set()
            for cc in sorted(cand,key=lambda z:(-z["combined_score"],z["amount"])):
                k=(cc["amount"],cc["source_url"],cc["line"])
                if k in ks: continue
                ks.add(k);ded.append(cc)
            rec["plans"].append({
                "plan_id":p["plan_id"],"treatment_class":p.get("treatment_class"),
                "treatment":p.get("treatment"),"candidates":ded[:15]
            })
    except Exception as e:
        rec["error"]=f"{type(e).__name__}: {e}"
    return rec

def main():
    seeds=json.loads(Path("data/v41_official_url_seeds.json").read_text(encoding="utf-8"))
    queue=json.loads(Path("results/v41_official_site_fallback_queue.json").read_text(encoding="utf-8"))
    byfac={}
    for x in queue["items"]: byfac.setdefault(x["facility_id"],[]).append(x)

    results=[]
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs={ex.submit(crawl_seed,s,byfac):s for s in seeds["items"]}
        done=0
        for fut in as_completed(futs):
            s=futs[fut]
            rec=fut.result()
            results.append(rec)
            done+=1
            print(done,s["facility"],sum(len(x.get("candidates",[])) for x in rec.get("plans",[])),flush=True)

    order={s["facility_id"]:i for i,s in enumerate(seeds["items"])}
    results.sort(key=lambda r:order.get(r.get("facility_id"),999999))
    out={"version":"v41-seed-crawl-1.1","total_facilities":len(results),"results":results}
    Path("results/v41_official_seed_crawl.json").write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({
      "facilities":len(results),
      "plans":sum(len(x.get("plans",[])) for x in results),
      "plans_with_candidates":sum(bool(p.get("candidates")) for x in results for p in x.get("plans",[]))
    },ensure_ascii=False))

if __name__=="__main__": main()
