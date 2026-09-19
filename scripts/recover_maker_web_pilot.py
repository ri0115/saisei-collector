#!/usr/bin/env python3
import csv, json, re, unicodedata, urllib.parse
from pathlib import Path
from collections import defaultdict

import requests
from bs4 import BeautifulSoup
from ddgs import DDGS

IN=Path("data/maker_pilot50_unresolved.tsv")
OUT=Path("results/maker_web_pilot")
OUT.mkdir(parents=True, exist_ok=True)

def norm(s):
    return unicodedata.normalize("NFKC", str(s or "")).replace("\u3000"," ").strip()

PRODUCT_PATTERNS = [
    ("ACP MAX", re.compile(r"(?i)ACP[\\s・_-]*MAX|HD[- ]?PRP\\s*[（(]ACP\\s*MAX")),
    ("ACP", re.compile(r"(?i)\\bACP\\b|ACPダブルシリンジ|ACP[- ]?PRP")),
    ("Angel", re.compile(r"(?i)\\bAngel\\b(?:\\s*c?PRP)?")),
    ("GPS", re.compile(r"(?i)GPS\\s*(?:III|Ⅲ|3)?")),
    ("APS", re.compile(r"(?i)\\bAPS\\b|Autologous\\s+Protein\\s+Solution")),
    ("Condensia", re.compile(r"(?i)Condensia|コンデンシア")),
    ("MyCells", re.compile(r"(?i)My\\s*cells?|Mycells|マイセル")),
    ("TriCeLL", re.compile(r"(?i)Tri\\s*Cell|TriCeLL|トライセル")),
    ("MAGELLAN", re.compile(r"(?i)MAGELLAN|Magellan|マゼラン")),
    ("PRGF-Endoret", re.compile(r"(?i)PRGF[- ]?Endoret|Endoret|PRGF")),
    ("PEAK", re.compile(r"(?i)PEAK\\s*(?:PRP)?")),
    ("YCELL", re.compile(r"(?i)YCELL|Ycellbio|ワイセル")),
]
MAKER_PATTERNS = [
    ("Zimmer Biomet", re.compile(r"(?i)Zimmer\\s*Biomet|ジンマー(?:・|\\s|-)?バイオメット")),
    ("Arthrex", re.compile(r"(?i)Arthrex|アースレックス")),
    ("京セラ", re.compile(r"(?i)Kyocera|京セラ")),
    ("ESTAR Technologies", re.compile(r"(?i)ESTAR\\s*TECHNOLOGIES")),
    ("ベリタス", re.compile(r"(?i)Veritas|ベリタス")),
    ("メッド・アライアンス", re.compile(r"(?i)メッド[・･ ]?(?:アライアンス|アイアンス)")),
    ("ヤマト科学", re.compile(r"(?i)ヤマト科学|Yamato\\s*Scientific")),
    ("ハイレックスメディカル", re.compile(r"(?i)ハイレックスメディカル|HI[- ]?LEX\\s*MEDICAL")),
    ("BTI", re.compile(r"(?i)\\bBTI\\b")),
    ("Ycellbio Medical", re.compile(r"(?i)Ycellbio\\s*Medical")),
    ("Johnson & Johnson", re.compile(r"(?i)Johnson\\s*&\\s*Johnson|ジョンソン[・･ ]?エンド[・･ ]?ジョンソン")),
    ("Kaylight", re.compile(r"(?i)Kaylight|ケイライト")),
]
PRODUCT_DEFAULT_MAKER={"GPS":"Zimmer Biomet","APS":"Zimmer Biomet","ACP":"Arthrex","ACP MAX":"Arthrex","Angel":"Arthrex","Condensia":"京セラ","MyCells":"ESTAR Technologies","TriCeLL":"メッド・アライアンス","MAGELLAN":"ハイレックスメディカル","PRGF-Endoret":"BTI","YCELL":"Ycellbio Medical"}

DENY=["caloo.jp","medicalnote.jp","doctorsfile.jp","hotpepper.jp","mynavi.jp","hospital-navi","qlife.jp","byoinnavi.jp","mapion.co.jp","navitime.co.jp","wikipedia.org","facebook.com","instagram.com","x.com"]

def core_name(name):
    s=norm(name)
    for p in ["社会医療法人","医療法人社団","医療法人","一般社団法人","独立行政法人地域医療機能推進機構","公立学校共済組合","株式会社"]:
        s=s.replace(p,"")
    return re.sub(r"\\s+","",s)

def official_like(url,title,facility):
    dom=urllib.parse.urlparse(url).netloc.lower()
    if any(d in dom for d in DENY): return False
    key=core_name(facility)
    t=core_name(title)
    # Search-targeted result on a non-directory domain; exact/partial facility-name match boosts confidence.
    return (len(key)>=4 and (key in t or t in key)) or any(tok in norm(title) for tok in ["クリニック","病院","Clinic","CLINIC"])

def hits(text):
    text=norm(text)
    ps=[]; ms=[]
    for p,rx in PRODUCT_PATTERNS:
        if rx.search(text): ps.append(p)
    for m,rx in MAKER_PATTERNS:
        if rx.search(text): ms.append(m)
    return sorted(set(ps)), sorted(set(ms))

def fetch_text(session,url):
    try:
        r=session.get(url,timeout=15,headers={"User-Agent":"Mozilla/5.0"},allow_redirects=True)
        if r.status_code!=200 or len(r.content)>3_000_000: return ""
        ct=(r.headers.get("content-type") or "").lower()
        if "html" not in ct and not url.lower().endswith((".html",".htm","/")): return ""
        return BeautifulSoup(r.text,"html.parser").get_text(" ",strip=True)
    except Exception:
        return ""

def main():
    rows=list(csv.DictReader(open(IN,encoding="utf-8-sig"),delimiter="\t"))
    ddgs=DDGS()
    sess=requests.Session()
    out=[]
    for row in rows:
        facility=row["facility"]
        query=f'"{facility}" PRP ACP APS GPS Condensia MyCells Arthrex Zimmer Biomet'
        evidence=[]; products=set(); explicit=set(); inferred=set()
        try:
            results=list(ddgs.text(query,max_results=6))
        except Exception as e:
            results=[]
        for res in results:
            url=res.get("href") or res.get("url") or ""
            title=res.get("title") or ""
            body=res.get("body") or ""
            if not url: continue
            off=official_like(url,title,facility)
            snippet=title+" "+body
            p0,m0=hits(snippet)
            page=fetch_text(sess,url) if off else ""
            p1,m1=hits(page)
            ps=sorted(set(p0+p1)); ms=sorted(set(m0+m1))
            if not ps and not ms: continue
            # Only official-like pages can resolve; third-party hits are retained for audit only.
            evidence.append({"url":url,"title":title,"official_like":off,"products":ps,"makers":ms,"snippet":body[:400]})
            if off:
                products.update(ps); explicit.update(ms)
        for p in products:
            if p in PRODUCT_DEFAULT_MAKER: inferred.add(PRODUCT_DEFAULT_MAKER[p])
        final_makers=explicit or inferred
        out.append({**row,
            "web_products_found":"|".join(sorted(products)),
            "web_makers_found":"|".join(sorted(final_makers)),
            "web_evidence_type":"EXPLICIT" if explicit else ("PRODUCT_MAP" if inferred else "NONE"),
            "web_resolved":"YES" if final_makers else "NO",
            "web_evidence_json":json.dumps(evidence[:10],ensure_ascii=False)
        })

    fields=list(out[0].keys())
    with open(OUT/"maker_web_pilot_results.tsv","w",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields,delimiter="\t"); w.writeheader(); w.writerows(out)
    unresolved=[r for r in out if r["web_resolved"]=="NO"]
    with open(OUT/"maker_web_pilot_unresolved.tsv","w",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields,delimiter="\t"); w.writeheader(); w.writerows(unresolved)
    mc=defaultdict(int); pc=defaultdict(int)
    for r in out:
        for m in filter(None,r["web_makers_found"].split("|")): mc[m]+=1
        for p in filter(None,r["web_products_found"].split("|")): pc[p]+=1
    summary={
        "input_unresolved":len(rows),
        "web_resolved":sum(r["web_resolved"]=="YES" for r in out),
        "web_resolution_rate":round(sum(r["web_resolved"]=="YES" for r in out)/len(rows),4),
        "web_explicit_maker":sum(r["web_evidence_type"]=="EXPLICIT" for r in out),
        "web_product_map_only":sum(r["web_evidence_type"]=="PRODUCT_MAP" for r in out),
        "still_unresolved":len(unresolved),
        "maker_counts":dict(sorted(mc.items(), key=lambda x:(-x[1],x[0]))),
        "product_counts":dict(sorted(pc.items(), key=lambda x:(-x[1],x[0]))),
    }
    (OUT/"maker_web_pilot_summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
