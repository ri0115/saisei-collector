#!/usr/bin/env python3
import argparse, csv, json, re, time, unicodedata, urllib.parse
from pathlib import Path
from collections import defaultdict
import requests
from bs4 import BeautifulSoup
from ddgs import DDGS

IN=Path("results/v42_maker_recovery_full366.tsv")

PRODUCT_PATTERNS=[
("ACP MAX",re.compile(r"(?i)ACP[\s・_-]*MAX|HD[- ]?PRP\s*[（(]ACP\s*MAX")),
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
("BTI",re.compile(r"(?i)\bBTI\b")),
("Ycellbio Medical",re.compile(r"(?i)Ycellbio\s*Medical")),
("ESTAR Technologies",re.compile(r"(?i)ESTAR\s*TECHNOLOGIES")),
("メッド・アライアンス",re.compile(r"(?i)メッド[・･ ]?(?:アライアンス|アイアンス)")),
("ハイレックスメディカル",re.compile(r"(?i)ハイレックスメディカル|HI[- ]?LEX\s*MEDICAL")),
]
# Only mappings already validated for automatic manufacturer assignment.
VALIDATED_MAP={"GPS":"Zimmer Biomet","APS":"Zimmer Biomet","ACP":"Arthrex","ACP MAX":"Arthrex","Angel":"Arthrex","Condensia":"京セラ"}
DENY_DOMAINS=[
"caloo.jp","medicalnote.jp","doctorsfile.jp","hotpepper.jp","mynavi.jp","qlife.jp","byoinnavi.jp",
"mapion.co.jp","navitime.co.jp","wikipedia.org","facebook.com","instagram.com","x.com","youtube.com",
"researchgate.net","pubmed.ncbi.nlm.nih.gov","arthrex.com","zimmerbiomet.com","kyocera.co.jp"
]
LEGAL=["社会医療法人","医療法人社団","医療法人","一般社団法人","独立行政法人地域医療機能推進機構",
"公立学校共済組合","株式会社","学校法人","公益財団法人","社会福祉法人","地方独立行政法人","国立大学法人"]

def norm(s):
    return unicodedata.normalize("NFKC",str(s or "")).replace("\u3000"," ").strip()
def compact(s):
    return re.sub(r"[\s・･\-‐‑–—―,，.。()（）「」『』]+","",norm(s)).lower()
def aliases(name):
    base=compact(name); out={base}
    y=base
    for p in LEGAL:
        y=y.replace(compact(p),"")
    if len(y)>=5: out.add(y)
    # Remove common suffixes only for a secondary alias, not as sole evidence.
    z=y
    for p in ["クリニック","病院","医院","診療所","整形外科"]:
        z=z.replace(compact(p),"")
    if len(z)>=6: out.add(z)
    return sorted(out,key=len,reverse=True)

def hits(text):
    text=norm(text)
    ps=[p for p,rx in PRODUCT_PATTERNS if rx.search(text)]
    ms=[m for m,rx in MAKER_PATTERNS if rx.search(text)]
    if "ACP MAX" in ps and "ACP" in ps: ps.remove("ACP")
    return sorted(set(ps)),sorted(set(ms))

def denied(url):
    d=urllib.parse.urlparse(url).netloc.lower()
    return any(x in d for x in DENY_DOMAINS)

def fetch(session,url):
    try:
        r=session.get(url,timeout=12,headers={"User-Agent":"Mozilla/5.0 maker-full-web/1.0"},allow_redirects=True)
        ct=(r.headers.get("content-type") or "").lower()
        if r.status_code!=200 or "html" not in ct or len(r.content)>3_500_000:
            return None
        return r.url, BeautifulSoup(r.text,"html.parser")
    except Exception:
        return None

def page_matches_facility(soup,facility):
    title=compact(soup.title.get_text(" ",strip=True) if soup.title else "")
    body=compact(soup.get_text(" ",strip=True))
    als=aliases(facility)
    title_hit=next((a for a in als[:2] if len(a)>=5 and a in title),None)
    body_hit=next((a for a in als[:2] if len(a)>=5 and a in body),None)
    # Strict: full/legal-stripped facility alias must occur in title or body.
    return bool(title_hit or body_hit), ("TITLE" if title_hit else "BODY" if body_hit else "")

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--batch",type=int,required=True)
    ap.add_argument("--batches",type=int,default=17)
    args=ap.parse_args()
    rows=list(csv.DictReader(IN.open(encoding="utf-8-sig"),delimiter="\t"))
    candidates=[r for r in rows if str(r.get("safe_resolved","")).upper()!="YES"]
    chunk=[r for i,r in enumerate(candidates) if i%args.batches==args.batch]
    outdir=Path(f"results/maker_full_web_sharded/batch{args.batch:02d}")
    outdir.mkdir(parents=True,exist_ok=True)
    ddgs=DDGS(); sess=requests.Session()
    out=[]
    for row in chunk:
        fac=row["facility"]
        queries=[
            f'"{fac}" PRP ACP APS GPS Condensia',
            f'"{fac}" 再生医療 PRP キット',
            f'"{fac}" Arthrex Zimmer Biomet 京セラ'
        ]
        seen=set(); evidence=[]; products=set(); explicit=set()
        for q in queries:
            try: rs=list(ddgs.text(q,max_results=5))
            except Exception: rs=[]
            for x in rs:
                url=x.get("href") or x.get("url") or ""
                if not url or url in seen or denied(url): continue
                seen.add(url)
                got=fetch(sess,url)
                if not got: continue
                final_url,soup=got
                if denied(final_url): continue
                matched,match_type=page_matches_facility(soup,fac)
                if not matched: continue
                text=soup.get_text(" ",strip=True)
                ps,ms=hits(text)
                if not ps and not ms: continue
                products.update(ps); explicit.update(ms)
                evidence.append({
                    "url":final_url,"match_type":match_type,"products":ps,"makers":ms,
                    "title":soup.title.get_text(" ",strip=True)[:180] if soup.title else ""
                })
            time.sleep(0.3)
        inferred={VALIDATED_MAP[p] for p in products if p in VALIDATED_MAP}
        final_makers=explicit|inferred
        evtype="EXPLICIT_OFFICIAL" if explicit else ("VALIDATED_PRODUCT_MAP_OFFICIAL" if inferred else ("PRODUCT_ONLY_OFFICIAL" if products else "NONE"))
        out.append({
            "facility_key":row["facility_key"],"prefecture":row["prefecture"],"facility":fac,
            "mhlw_plan_codes":row["mhlw_plan_codes"],
            "mhlw_safe_resolved":row.get("safe_resolved",""),
            "mhlw_products":row.get("products_found",""),
            "official_products":"|".join(sorted(products)),
            "official_explicit_makers":"|".join(sorted(explicit)),
            "official_final_makers":"|".join(sorted(final_makers)),
            "official_evidence_type":evtype,
            "official_safe_resolved":"YES" if final_makers else "NO",
            "official_product_resolved":"YES" if products else "NO",
            "evidence_json":json.dumps(evidence[:10],ensure_ascii=False)
        })
    fields=list(out[0].keys()) if out else ["facility_key"]
    with (outdir/"results.tsv").open("w",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields,delimiter="\t");w.writeheader();w.writerows(out)
    summary={
        "batch":args.batch,"batches":args.batches,"input":len(chunk),
        "manufacturer_safe":sum(r["official_safe_resolved"]=="YES" for r in out),
        "product_resolved":sum(r["official_product_resolved"]=="YES" for r in out),
        "explicit":sum(r["official_evidence_type"]=="EXPLICIT_OFFICIAL" for r in out),
        "validated_product_map":sum(r["official_evidence_type"]=="VALIDATED_PRODUCT_MAP_OFFICIAL" for r in out),
    }
    (outdir/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=False))
if __name__=="__main__": main()
