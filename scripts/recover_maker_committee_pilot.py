#!/usr/bin/env python3
import csv,json,re,unicodedata,urllib.parse,time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,as_completed
import requests
from bs4 import BeautifulSoup

IN=Path("data/maker_pilot50_unresolved.tsv")
OUT=Path("results/maker_committee_pilot")
OUT.mkdir(parents=True,exist_ok=True)

def norm(s):
    return unicodedata.normalize("NFKC",str(s or "")).replace("\u3000"," ").strip()
def compact(s):
    return re.sub(r"\s+","",norm(s))
LEGAL=["社会医療法人","医療法人社団","医療法人","一般社団法人","公立学校共済組合","独立行政法人地域医療機能推進機構","株式会社","学校法人","公益財団法人","社会福祉法人"]

PRODUCT_PATTERNS=[
("ACP MAX",re.compile(r"(?i)ACP[\s・_-]*MAX")),
("ACP",re.compile(r"(?i)\bACP\b|ACPダブルシリンジ|ACP[- ]?PRP")),
("Angel",re.compile(r"(?i)\bAngel\b(?:\s*c?PRP)?")),
("GPS",re.compile(r"(?i)G\s*P\s*S\s*(?:III|Ⅲ|3)?")),
("APS",re.compile(r"(?i)(?<![A-Za-z])A\s*P\s*S(?![A-Za-z])|Autologous\s+Protein\s+Solution")),
("Condensia",re.compile(r"(?i)Condensia|コンデンシア")),
("MyCells",re.compile(r"(?i)My\s*cells?|Mycells|マイセル")),
("TriCeLL",re.compile(r"(?i)Tri\s*Cell|TriCeLL|トライセル")),
("MAGELLAN",re.compile(r"(?i)MAGELLAN|Magellan|マゼラン")),
("PRGF-Endoret",re.compile(r"(?i)PRGF[- ]?Endoret|Endoret|PRGF")),
("PEAK",re.compile(r"(?i)PEAK\s*(?:PRP)?")),
("YCELL",re.compile(r"(?i)(?<![A-Za-z])Y\s*CELL(?:BIO)?(?:\s*Medical)?|ワイセル")),
]
MAKER_PATTERNS=[
("Zimmer Biomet",re.compile(r"(?i)Zimmer\s*Biomet|ジンマー(?:・|\s|-)?バイオメット")),
("Arthrex",re.compile(r"(?i)Arthrex|アースレックス")),
("京セラ",re.compile(r"(?i)Kyocera|京セラ")),
("ESTAR Technologies",re.compile(r"(?i)ESTAR\s*TECHNOLOGIES")),
("メッド・アライアンス",re.compile(r"(?i)メッド[・･ ]?(?:アライアンス|アイアンス)")),
("ハイレックスメディカル",re.compile(r"(?i)ハイレックスメディカル|HI[- ]?LEX\s*MEDICAL")),
("BTI",re.compile(r"(?i)\bBTI\b")),
("Ycellbio Medical",re.compile(r"(?i)Ycellbio\s*Medical")),
("Johnson & Johnson",re.compile(r"(?i)Johnson\s*&\s*Johnson|ジョンソン[・･ ]?エンド[・･ ]?ジョンソン")),
]
PRODUCT_DEFAULT_MAKER={"GPS":"Zimmer Biomet","APS":"Zimmer Biomet","ACP":"Arthrex","ACP MAX":"Arthrex","Angel":"Arthrex","Condensia":"京セラ","MyCells":"ESTAR Technologies","TriCeLL":"メッド・アライアンス","MAGELLAN":"ハイレックスメディカル","PRGF-Endoret":"BTI","YCELL":"Ycellbio Medical"}

def aliases(name):
    x=compact(name)
    out=[x]
    y=x
    for p in LEGAL:y=y.replace(p,"")
    if len(y)>=6:out.append(y)
    return sorted(set(out),key=len,reverse=True)

def hits(text):
    text=norm(text); ps=[];ms=[]
    for p,rx in PRODUCT_PATTERNS:
        if rx.search(text):ps.append(p)
    for m,rx in MAKER_PATTERNS:
        if rx.search(text):ms.append(m)
    return sorted(set(ps)),sorted(set(ms))

def get(session,url,timeout=15):
    try:
        r=session.get(url,timeout=timeout,headers={"User-Agent":"Mozilla/5.0"},allow_redirects=True)
        if r.status_code==200:return r.text
    except Exception:pass
    return ""

def discover_histories(session):
    links=set()
    for typ in [1,2]:
        for page in range(1,8):
            url=f"https://saiseiiryo.mhlw.go.jp/disclosed_committee/index/{typ}" + ("" if page==1 else f"/page:{page}")
            html=get(session,url)
            if not html:continue
            soup=BeautifulSoup(html,"html.parser")
            before=len(links)
            for a in soup.find_all("a",href=True):
                href=a["href"]
                if "/disclosed_committee/history/" in href:
                    links.add(urllib.parse.urljoin(url,href))
            if page>1 and len(links)==before:
                # page may be beyond the listing
                break
    return sorted(links)

def fetch_history(url):
    s=requests.Session()
    html=get(s,url,20)
    if not html:return url,""
    text=BeautifulSoup(html,"html.parser").get_text(" ",strip=True)
    return url,norm(text)

def main():
    rows=list(csv.DictReader(open(IN,encoding="utf-8-sig"),delimiter="\t"))
    sess=requests.Session()
    histories=discover_histories(sess)
    pages=[]
    with ThreadPoolExecutor(max_workers=12) as ex:
        futs={ex.submit(fetch_history,u):u for u in histories}
        for fut in as_completed(futs):
            u,t=fut.result()
            if t:pages.append((u,t,compact(t)))
    results=[]
    for row in rows:
        ev=[];products=set();explicit=set()
        for url,text,ctext in pages:
            matched_alias=None
            pos=-1
            for a in aliases(row["facility"]):
                pos=ctext.find(a)
                if pos>=0:
                    matched_alias=a;break
            if pos<0:continue
            # Search the whole page for the exact facility; then use text chunks around facility mentions.
            # Compact-position cannot map exactly back to spaced text, so inspect table-row-like chunks split by dates/known separators.
            chunks=re.split(r"(?=20\d{2}/\d{2}/\d{2})|(?=令和\d)",text)
            candidate_chunks=[c for c in chunks if any(a in compact(c) for a in aliases(row["facility"]))]
            if not candidate_chunks:
                candidate_chunks=[text]
            for c in candidate_chunks:
                ps,ms=hits(c)
                if ps or ms:
                    products.update(ps);explicit.update(ms)
                    ev.append({"url":url,"products":ps,"makers":ms,"context":re.sub(r"\s+"," ",c)[:800]})
        inferred={PRODUCT_DEFAULT_MAKER[p] for p in products if p in PRODUCT_DEFAULT_MAKER}
        final=explicit or inferred
        results.append({**row,"committee_products_found":"|".join(sorted(products)),"committee_makers_found":"|".join(sorted(final)),
                        "committee_evidence_type":"EXPLICIT" if explicit else ("PRODUCT_MAP" if inferred else "NONE"),
                        "committee_resolved":"YES" if final else "NO","committee_evidence_json":json.dumps(ev[:8],ensure_ascii=False)})
    fields=list(results[0].keys())
    with open(OUT/"results.tsv","w",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields,delimiter="\t");w.writeheader();w.writerows(results)
    summary={"committee_history_pages":len(histories),"fetched_pages":len(pages),"input":len(results),
             "resolved":sum(r["committee_resolved"]=="YES" for r in results),
             "explicit":sum(r["committee_evidence_type"]=="EXPLICIT" for r in results),
             "product_map":sum(r["committee_evidence_type"]=="PRODUCT_MAP" for r in results)}
    (OUT/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=False))

if __name__=="__main__":main()
