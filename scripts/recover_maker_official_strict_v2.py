#!/usr/bin/env python3
import argparse,csv,json,re,time,unicodedata,urllib.parse
from pathlib import Path
from functools import lru_cache
import requests
from bs4 import BeautifulSoup
from ddgs import DDGS

IN=Path("results/v42_maker_recovery_full366.tsv")
REG=Path("data/product_manufacturer_registry.json")

DENY=[
"iryou.teikyouseido.mhlw.go.jp","scuel.me","saiseiiryo.mhlw.go.jp","jrct.mhlw.go.jp",
"caloo.jp","medicalnote.jp","doctorsfile.jp","hotpepper.jp","mynavi.jp","qlife.jp","byoinnavi.jp",
"mapion.co.jp","navitime.co.jp","medley.life","hospital-navi","regene-m.jp",
"wikipedia.org","facebook.com","instagram.com","x.com","youtube.com","researchgate.net","pubmed.ncbi.nlm.nih.gov",
"arthrex.com","arthrex.co.jp","zimmerbiomet.com","kyocera.co.jp","bti-japan.com","hi-lexmed.com",
"ycellbio.com","pmda.go.jp"
]
LEGAL=["社会医療法人","医療法人社団","医療法人","一般社団法人","公立学校共済組合",
"独立行政法人地域医療機能推進機構","独立行政法人国立病院機構","地方独立行政法人","国立大学法人",
"学校法人","株式会社","公益財団法人","社会福祉法人","公益社団法人","一般財団法人"]

PATTERNS=[
("ACP MAX",re.compile(r"(?i)ACP[\s・_-]*MAX|HD[- ]?PRP\s*[（(]ACP\s*MAX")),
("ACP",re.compile(r"(?i)(?<![A-Za-z])ACP(?![A-Za-z])|ACPダブルシリンジ|ACP[- ]?PRP")),
("Angel",re.compile(r"(?i)(?<![A-Za-z])Angel(?:\s*c?PRP)?(?![A-Za-z])")),
("GPS",re.compile(r"(?i)(?<![A-Za-z])GPS(?:\s*(?:III|Ⅲ|3))?(?![A-Za-z])")),
("APS",re.compile(r"(?i)(?<![A-Za-z])APS(?![A-Za-z])|Autologous\s+Protein\s+Solution")),
("Condensia",re.compile(r"(?i)Condensia|コンデンシア")),
("MyCells",re.compile(r"(?i)My\s*cells?|Mycells|マイセル(?:ズ)?")),
("TriCeLL",re.compile(r"(?i)Tri\s*Cell|TriCeLL|トライセル")),
("MAGELLAN",re.compile(r"(?i)MAGELLAN|マゼラン")),
("PRGF-Endoret",re.compile(r"(?i)PRGF[- ]?Endoret|Endoret")),
("PEAK",re.compile(r"(?i)PEAK\s+(?:Platelet\s+Rich\s+Plasma|PRP)(?:\s+System)?")),
("YCELL",re.compile(r"(?i)(?<![A-Za-z])Y\s*CELL(?:BIO)?(?:\s*Medical)?|ワイセル")),
]
GENERIC={"ACP","APS","GPS","Angel"}
PRPCTX=re.compile(r"(?i)PRP|多血小板|血小板|platelet|再生医療|再生療法|自己タンパク|Autologous\s+Protein|ダブルシリンジ|濃縮血小板")
MAKER_PATTERNS=[
("Zimmer Biomet",re.compile(r"(?i)Zimmer\s*Biomet|ジンマー(?:・|\s|-)?バイオメット")),
("Arthrex",re.compile(r"(?i)Arthrex|アースレックス")),
("京セラ",re.compile(r"(?i)Kyocera|京セラ")),
("BTI",re.compile(r"(?i)\bBTI\b")),
("Ycellbio Medical",re.compile(r"(?i)Ycellbio\s*Medical")),
("ESTAR Technologies",re.compile(r"(?i)ESTAR\s*TECHNOLOGIES")),
("メッド・アライアンス",re.compile(r"(?i)メッド[・･ ]?アライアンス")),
("HI-LEX medical",re.compile(r"(?i)ハイレックスメディカル|HI[- ]?LEX\s*MEDICAL")),
("DSM Biomedical",re.compile(r"(?i)DSM\s*Biomedical")),
]

def norm(s): return unicodedata.normalize("NFKC",str(s or "")).replace("\u3000"," ").strip()
def compact(s): return re.sub(r"[\s・･\-‐‑–—―,，.。()（）「」『』]+","",norm(s)).lower()

def aliases(name):
    base=compact(name); out={base}
    stripped=base
    for p in LEGAL: stripped=stripped.replace(compact(p),"")
    if len(stripped)>=5: out.add(stripped)
    return sorted(out,key=len,reverse=True)

def denied(url):
    d=urllib.parse.urlparse(url).netloc.lower()
    return any(x in d for x in DENY)

def clean_soup(html):
    soup=BeautifulSoup(html,"html.parser")
    for t in soup(["script","style","noscript","svg","template"]): t.decompose()
    return soup

def fetch(session,url):
    try:
        r=session.get(url,timeout=12,headers={"User-Agent":"Mozilla/5.0 strict-maker-audit/2.0"},allow_redirects=True)
        ct=(r.headers.get("content-type") or "").lower()
        if r.status_code!=200 or "html" not in ct or len(r.content)>4_000_000:return None
        return r.url,clean_soup(r.text)
    except Exception:return None

def text_title(soup):
    title=soup.title.get_text(" ",strip=True) if soup.title else ""
    text=soup.get_text(" ",strip=True)
    return title,text

def facility_in_title(title,fac):
    t=compact(title)
    return any(len(a)>=5 and a in t for a in aliases(fac))

_home_cache={}
def domain_home_matches(session,url,fac):
    u=urllib.parse.urlparse(url); home=f"{u.scheme}://{u.netloc}/"
    key=(home,fac)
    if key in _home_cache:return _home_cache[key]
    got=fetch(session,home)
    ok=False
    if got:
        _,s=got; title,text=text_title(s); c=compact(title+" "+text[:8000])
        ok=any(len(a)>=5 and a in c for a in aliases(fac))
    _home_cache[key]=ok
    return ok

def contexts(text,rx,width=180):
    out=[]
    for m in rx.finditer(text):
        a=max(0,m.start()-width);b=min(len(text),m.end()+width)
        out.append(re.sub(r"\s+"," ",text[a:b]).strip())
        if len(out)>=4:break
    return out

def product_hits(text):
    hits=[]; evidence={}
    for p,rx in PATTERNS:
        ctxs=contexts(text,rx)
        if not ctxs:continue
        if p in GENERIC:
            good=[c for c in ctxs if PRPCTX.search(c)]
            if not good:continue
            ctxs=good
        elif p=="PEAK":
            pass
        else:
            # Distinctive product names still require the page to be about PRP/regenerative care.
            if not PRPCTX.search(text):
                continue
        hits.append(p);evidence[p]=ctxs[:2]
    if "ACP MAX" in hits and "ACP" in hits:hits.remove("ACP");evidence.pop("ACP",None)
    return sorted(set(hits)),evidence

def maker_hits(text,products):
    makers=[];evidence={}
    for m,rx in MAKER_PATTERNS:
        ctxs=contexts(text,rx,220)
        if not ctxs:continue
        good=[c for c in ctxs if PRPCTX.search(c)]
        if good or products:
            makers.append(m);evidence[m]=(good or ctxs)[:2]
    return sorted(set(makers)),evidence

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--batch",type=int,required=True);ap.add_argument("--batches",type=int,default=17)
    args=ap.parse_args()
    registry=json.loads(REG.read_text(encoding="utf-8"))["products"]
    rows=list(csv.DictReader(IN.open(encoding="utf-8-sig"),delimiter="\t"))
    candidates=[r for r in rows if str(r.get("safe_resolved","")).upper()!="YES"]
    chunk=[r for i,r in enumerate(candidates) if i%args.batches==args.batch]
    outdir=Path(f"results/maker_official_strict_v2_sharded/batch{args.batch:02d}");outdir.mkdir(parents=True,exist_ok=True)
    sess=requests.Session();ddgs=DDGS();out=[]
    for row in chunk:
        fac=row["facility"];seen=set();products=set();explicit=set();evidence=[]
        queries=[f'"{fac}" PRP 再生医療',f'"{fac}" ACP APS GPS',f'"{fac}" Condensia MyCells TriCeLL',f'"{fac}" Arthrex Zimmer Biomet']
        for q in queries:
            try:rs=list(ddgs.text(q,max_results=6))
            except Exception:rs=[]
            for x in rs:
                url=x.get("href") or x.get("url") or ""
                if not url or url in seen or denied(url):continue
                seen.add(url)
                got=fetch(sess,url)
                if not got:continue
                final_url,soup=got
                if denied(final_url):continue
                title,text=text_title(soup)
                official=facility_in_title(title,fac) or domain_home_matches(sess,final_url,fac)
                if not official:continue
                ps,pev=product_hits(text)
                ms,mev=maker_hits(text,ps)
                if not ps and not ms:continue
                products.update(ps);explicit.update(ms)
                evidence.append({"url":final_url,"title":title[:200],"products":ps,"makers":ms,
                                 "product_context":pev,"maker_context":mev})
            time.sleep(0.25)
        inferred=set();legal=set();japan=set()
        for p in products:
            x=registry.get(p)
            if not x:continue
            g=norm(x.get("manufacturer_share_group",""))
            if g:inferred.add(g)
            lm=norm(x.get("legal_manufacturer",""))
            if lm:legal.add(lm)
            jm=norm(x.get("japan_mah_or_supplier",""))
            if jm:japan.add(jm)
        final=explicit|inferred
        evtype="EXPLICIT_OFFICIAL" if explicit else ("VALIDATED_PRODUCT_MAP_OFFICIAL" if inferred else ("PRODUCT_ONLY_OFFICIAL" if products else "NONE"))
        out.append({
          "facility_key":row["facility_key"],"prefecture":row["prefecture"],"facility":fac,"mhlw_plan_codes":row["mhlw_plan_codes"],
          "official_products":"|".join(sorted(products)),"official_explicit_makers":"|".join(sorted(explicit)),
          "manufacturer_share_groups":"|".join(sorted(final)),"legal_manufacturers":"|".join(sorted(legal)),
          "japan_mah_or_suppliers":"|".join(sorted(japan)),"evidence_type":evtype,
          "safe_resolved":"YES" if final else "NO","product_resolved":"YES" if products else "NO",
          "evidence_json":json.dumps(evidence[:10],ensure_ascii=False)
        })
    fields=list(out[0].keys()) if out else ["facility_key"]
    with (outdir/"results.tsv").open("w",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields,delimiter="\t");w.writeheader();w.writerows(out)
    summary={"batch":args.batch,"batches":args.batches,"input":len(chunk),
             "manufacturer_safe":sum(r["safe_resolved"]=="YES" for r in out),
             "product_resolved":sum(r["product_resolved"]=="YES" for r in out),
             "explicit":sum(r["evidence_type"]=="EXPLICIT_OFFICIAL" for r in out),
             "validated_product_map":sum(r["evidence_type"]=="VALIDATED_PRODUCT_MAP_OFFICIAL" for r in out)}
    (outdir/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=False))
if __name__=="__main__":main()
