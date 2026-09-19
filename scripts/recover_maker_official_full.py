#!/usr/bin/env python3
import csv,json,re,subprocess,glob,unicodedata,urllib.parse
from pathlib import Path
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor,as_completed
import requests
from bs4 import BeautifulSoup
from ddgs import DDGS

RUN_ID="35432363044"
WORK=Path("tmp/maker_official_full"); WORK.mkdir(parents=True,exist_ok=True)
OUT=Path("results/maker_official_full"); OUT.mkdir(parents=True,exist_ok=True)

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
("ESTAR Technologies",re.compile(r"(?i)ESTAR\s*TECHNOLOGIES")),
("メッド・アライアンス",re.compile(r"(?i)メッド[・･ ]?アライアンス")),
("ハイレックスメディカル",re.compile(r"(?i)ハイレックスメディカル|HI[- ]?LEX\s*MEDICAL")),
("BTI",re.compile(r"(?i)\bBTI\b")),
("Ycellbio Medical",re.compile(r"(?i)Ycellbio\s*Medical")),
]
PRODUCT_DEFAULT_MAKER={
"GPS":"Zimmer Biomet","APS":"Zimmer Biomet","ACP":"Arthrex","ACP MAX":"Arthrex","Angel":"Arthrex",
"Condensia":"京セラ","TriCeLL":"メッド・アライアンス","PRGF-Endoret":"BTI","YCELL":"Ycellbio Medical"
}
DENY=["caloo.jp","medicalnote.jp","doctorsfile.jp","hotpepper.jp","mynavi.jp","qlife.jp","byoinnavi.jp","mapion.co.jp","navitime.co.jp","wikipedia.org","facebook.com","instagram.com","x.com","researchgate.net","arthrex.com","zimmerbiomet.com","kyocera.co.jp","med-alliance.co.jp"]
LEGAL=["社会医療法人","医療法人社団","医療法人","一般社団法人","公立学校共済組合","独立行政法人地域医療機能推進機構","株式会社","学校法人","公益財団法人","社会福祉法人","独立行政法人国立病院機構"]
GENERIC=["整形外科","クリニック","病院","医院","診療所","センター","clinic","hospital","medical"]

def norm(s): return unicodedata.normalize("NFKC",str(s or "")).replace("\u3000"," ").strip()
def compact(s): return re.sub(r"\s+","",norm(s)).lower()

def aliases(name):
    s=compact(name)
    for p in LEGAL:s=s.replace(compact(p),"")
    out={s}; t=s
    for g in GENERIC:t=t.replace(compact(g),"")
    if len(t)>=4:out.add(t)
    for x in re.split(r"[・&＆/]",norm(name)):
        x=compact(x)
        for p in LEGAL+GENERIC:x=x.replace(compact(p),"")
        if len(x)>=5:out.add(x)
    return sorted(out,key=len,reverse=True)

def page_matches_facility(text,name):
    c=compact(text); return any(a and a in c for a in aliases(name))

def hits(text):
    ps=[];ms=[]
    for p,rx in PRODUCT_PATTERNS:
        if rx.search(text):ps.append(p)
    if "ACP MAX" in ps and "ACP" in ps:ps.remove("ACP")
    for m,rx in MAKER_PATTERNS:
        if rx.search(text):ms.append(m)
    return sorted(set(ps)),sorted(set(ms))

def fetch_page(url):
    try:
        r=requests.get(url,timeout=12,headers={"User-Agent":"Mozilla/5.0"},allow_redirects=True)
        if r.status_code!=200 or len(r.content)>4_000_000:return ""
        if "html" not in (r.headers.get("content-type") or "").lower():return ""
        return BeautifulSoup(r.text,"html.parser").get_text(" ",strip=True)
    except Exception:return ""

def load_mhlw_artifacts():
    subprocess.run(["gh","run","download",RUN_ID,"-D",str(WORK/"artifacts")],check=True)
    rows=[]
    for p in glob.glob(str(WORK/"artifacts"/"**"/"results.tsv"),recursive=True):
        with open(p,encoding="utf-8-sig",newline="") as f:rows+=list(csv.DictReader(f,delimiter="\t"))
    bykey={}
    for r in rows:bykey[r["facility_key"]]=r
    return list(bykey.values())

def search_one(row):
    if row.get("safe_resolved")=="YES":return None
    fac=row["facility"]; seen=set(); ev=[]; products=set(); explicit=set()
    queries=[f'"{fac}" PRP ACP APS GPS Condensia',f'"{fac}" PRP ACP MAX Angel',f'"{fac}" PRP キット 再生医療']
    ddgs=DDGS()
    for q in queries:
        try:res=list(ddgs.text(q,max_results=8))
        except Exception:res=[]
        for x in res:
            url=x.get("href") or x.get("url") or ""
            if not url or url in seen:continue
            seen.add(url)
            dom=urllib.parse.urlparse(url).netloc.lower()
            if any(d in dom for d in DENY):continue
            page=fetch_page(url)
            if not page or not page_matches_facility(page,fac):continue
            ps,ms=hits(page)
            if not ps and not ms:continue
            products.update(ps);explicit.update(ms);ev.append({"url":url,"products":ps,"makers":ms})
    inferred={PRODUCT_DEFAULT_MAKER[p] for p in products if p in PRODUCT_DEFAULT_MAKER}
    makers=explicit|inferred
    return {"facility_key":row["facility_key"],"prefecture":row["prefecture"],"facility":fac,
            "mhlw_plan_codes":row["mhlw_plan_codes"],"official_products":"|".join(sorted(products)),
            "official_explicit_makers":"|".join(sorted(explicit)),"official_inferred_makers":"|".join(sorted(inferred)),
            "official_final_makers":"|".join(sorted(makers)),
            "official_evidence_type":"EXPLICIT" if explicit else ("VALIDATED_PRODUCT_MAP" if inferred else "NONE"),
            "official_resolved":"YES" if makers else "NO","evidence_json":json.dumps(ev[:12],ensure_ascii=False)}

def main():
    rows=load_mhlw_artifacts(); unresolved=[r for r in rows if r.get("safe_resolved")!="YES"]; out=[]
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs=[ex.submit(search_one,r) for r in unresolved]
        for fut in as_completed(futs):
            x=fut.result()
            if x:out.append(x)
    out.sort(key=lambda r:(r["prefecture"],r["facility"]))
    fields=["facility_key","prefecture","facility","mhlw_plan_codes","official_products","official_explicit_makers","official_inferred_makers","official_final_makers","official_evidence_type","official_resolved","evidence_json"]
    with open(OUT/"results.tsv","w",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields,delimiter="\t");w.writeheader();w.writerows(out)
    resolved=[r for r in out if r["official_resolved"]=="YES"];mc=defaultdict(int);pc=defaultdict(int)
    for r in resolved:
        for m in filter(None,r["official_final_makers"].split("|")):mc[m]+=1
        for p in filter(None,r["official_products"].split("|")):pc[p]+=1
    summary={"input_mhlw_rows":len(rows),"unresolved_after_mhlw":len(unresolved),"official_reviewed":len(out),
             "official_resolved":len(resolved),"official_resolution_rate":round(len(resolved)/len(out),4) if out else 0,
             "explicit":sum(r["official_evidence_type"]=="EXPLICIT" for r in resolved),
             "validated_product_map":sum(r["official_evidence_type"]=="VALIDATED_PRODUCT_MAP" for r in resolved),
             "maker_counts":dict(sorted(mc.items(),key=lambda x:(-x[1],x[0]))),
             "product_counts":dict(sorted(pc.items(),key=lambda x:(-x[1],x[0])))}
    (OUT/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=False))
if __name__=="__main__":main()
