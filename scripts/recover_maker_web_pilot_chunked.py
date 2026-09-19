#!/usr/bin/env python3
import argparse, csv, json, re, unicodedata, urllib.parse
from pathlib import Path
from collections import defaultdict

import requests
from bs4 import BeautifulSoup
from ddgs import DDGS

IN=Path("data/maker_pilot50_unresolved.tsv")

def norm(s):
    return unicodedata.normalize("NFKC", str(s or "")).replace("\u3000"," ").strip()

PRODUCT_PATTERNS=[
("ACP MAX",re.compile(r"(?i)ACP[\\s・_-]*MAX|HD[- ]?PRP\\s*[（(]ACP\\s*MAX")),
("ACP",re.compile(r"(?i)\\bACP\\b|ACPダブルシリンジ|ACP[- ]?PRP")),
("Angel",re.compile(r"(?i)\\bAngel\\b(?:\\s*c?PRP)?")),
("GPS",re.compile(r"(?i)G\\s*P\\s*S\\s*(?:III|Ⅲ|3)?")),
("APS",re.compile(r"(?i)(?<![A-Za-z])A\\s*P\\s*S(?![A-Za-z])|Autologous\\s+Protein\\s+Solution")),
("Condensia",re.compile(r"(?i)Condensia|コンデンシア")),
("MyCells",re.compile(r"(?i)My\\s*cells?|Mycells|マイセル")),
("TriCeLL",re.compile(r"(?i)Tri\\s*Cell|TriCeLL|トライセル")),
("MAGELLAN",re.compile(r"(?i)MAGELLAN|Magellan|マゼラン")),
("PRGF-Endoret",re.compile(r"(?i)PRGF[- ]?Endoret|Endoret|PRGF")),
("PEAK",re.compile(r"(?i)PEAK\\s*(?:PRP)?")),
("YCELL",re.compile(r"(?i)(?<![A-Za-z])Y\\s*CELL(?:BIO)?(?:\\s*Medical)?|ワイセル")),
]
MAKER_PATTERNS=[
("Zimmer Biomet",re.compile(r"(?i)Zimmer\\s*Biomet|ジンマー(?:・|\\s|-)?バイオメット")),
("Arthrex",re.compile(r"(?i)Arthrex|アースレックス")),
("京セラ",re.compile(r"(?i)Kyocera|京セラ")),
("ESTAR Technologies",re.compile(r"(?i)ESTAR\\s*TECHNOLOGIES")),
("ベリタス",re.compile(r"(?i)Veritas|ベリタス")),
("メッド・アライアンス",re.compile(r"(?i)メッド[・･ ]?(?:アライアンス|アイアンス)")),
("ヤマト科学",re.compile(r"(?i)ヤマト科学|Yamato\\s*Scientific")),
("ハイレックスメディカル",re.compile(r"(?i)ハイレックスメディカル|HI[- ]?LEX\\s*MEDICAL")),
("BTI",re.compile(r"(?i)\\bBTI\\b")),
("Ycellbio Medical",re.compile(r"(?i)Ycellbio\\s*Medical")),
("Johnson & Johnson",re.compile(r"(?i)Johnson\\s*&\\s*Johnson|ジョンソン[・･ ]?エンド[・･ ]?ジョンソン")),
("Kaylight",re.compile(r"(?i)Kaylight|ケイライト")),
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
    key=core_name(facility); t=core_name(title)
    if len(key)>=4 and (key in t or t in key): return True
    tokens=[x for x in re.split(r"[・&＆/\\s]",key) if len(x)>=4]
    return any(tok in t for tok in tokens)

GENERIC_TOKENS=["医療法人","社会医療法人","医療法人社団","一般社団法人","整形外科","スポーツ","クリニック","病院","医院","CLINIC","Clinic"]

def facility_matches_page(text, facility):
    page=re.sub(r"\\s+","",norm(text)).lower()
    key=core_name(facility).lower()
    if key and key in page: return True
    reduced=key
    for g in GENERIC_TOKENS:
        reduced=reduced.replace(g.lower(),"")
    if len(reduced)>=4 and reduced in page: return True
    toks=[t.lower() for t in re.split(r"[・&＆/\\s]",norm(facility)) if len(t)>=4 and t not in GENERIC_TOKENS]
    return any(t.replace(" ","") in page for t in toks)

def hits(text):
    text=norm(text); ps=[]; ms=[]
    for p,rx in PRODUCT_PATTERNS:
        if rx.search(text): ps.append(p)
    for m,rx in MAKER_PATTERNS:
        if rx.search(text): ms.append(m)
    return sorted(set(ps)),sorted(set(ms))

def fetch_text(session,url):
    try:
        r=session.get(url,timeout=8,headers={"User-Agent":"Mozilla/5.0"},allow_redirects=True)
        if r.status_code!=200 or len(r.content)>2_000_000: return ""
        ct=(r.headers.get("content-type") or "").lower()
        if "html" not in ct and not url.lower().endswith((".html",".htm","/")): return ""
        return BeautifulSoup(r.text,"html.parser").get_text(" ",strip=True)
    except Exception:
        return ""

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--batch",type=int,required=True)
    ap.add_argument("--size",type=int,default=12)
    args=ap.parse_args()
    allrows=list(csv.DictReader(open(IN,encoding="utf-8-sig"),delimiter="\t"))
    start=args.batch*args.size
    rows=allrows[start:start+args.size]
    outdir=Path(f"results/maker_web_pilot_chunked/batch{args.batch:02d}")
    outdir.mkdir(parents=True,exist_ok=True)
    ddgs=DDGS(); sess=requests.Session(); out=[]
    for row in rows:
        facility=row["facility"]
        queries=[
            f'"{facility}" PRP ACP APS GPS Condensia MyCells Arthrex Zimmer Biomet',
            f'"{facility}" PRP キット メーカー'
        ]
        evidence=[]; products=set(); explicit=set(); inferred=set(); seen_urls=set()
        for query in queries:
            try: results=list(ddgs.text(query,max_results=4))
            except Exception: results=[]
            for res in results:
                url=res.get("href") or res.get("url") or ""
                title=res.get("title") or ""; body=res.get("body") or ""
                if not url or url in seen_urls: continue
                seen_urls.add(url)
                off=official_like(url,title,facility)
                p0,m0=hits(title+" "+body)
                page=fetch_text(sess,url) if off else ""
                p1,m1=hits(page)
                ps=sorted(set(p0+p1)); ms=sorted(set(m0+m1))
                if not ps and not ms: continue
                evidence.append({"url":url,"title":title,"official_like":off,"products":ps,"makers":ms,"snippet":body[:300]})
                if off:
                    products.update(ps); explicit.update(ms)
        for p in products:
            if p in PRODUCT_DEFAULT_MAKER: inferred.add(PRODUCT_DEFAULT_MAKER[p])
        final=explicit or inferred
        out.append({**row,"web_products_found":"|".join(sorted(products)),"web_makers_found":"|".join(sorted(final)),
                    "web_evidence_type":"EXPLICIT" if explicit else ("PRODUCT_MAP" if inferred else "NONE"),
                    "web_resolved":"YES" if final else "NO","web_evidence_json":json.dumps(evidence[:8],ensure_ascii=False)})
    fields=list(out[0].keys()) if out else []
    if out:
        with open(outdir/"results.tsv","w",encoding="utf-8",newline="") as f:
            w=csv.DictWriter(f,fieldnames=fields,delimiter="\t");w.writeheader();w.writerows(out)
    summary={"batch":args.batch,"start":start,"input":len(rows),"resolved":sum(r["web_resolved"]=="YES" for r in out),
             "unresolved":sum(r["web_resolved"]=="NO" for r in out)}
    (outdir/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=False))

if __name__=="__main__": main()
