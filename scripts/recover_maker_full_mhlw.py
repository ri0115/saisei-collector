#!/usr/bin/env python3
import argparse, asyncio, csv, glob, io, json, re, unicodedata
from collections import defaultdict
from pathlib import Path
from pypdf import PdfReader
from playwright.async_api import async_playwright

ROOT=Path(__file__).resolve().parents[1]
RESULTS=ROOT/"results"

PRODUCT_PATTERNS=[
("ACP MAX",re.compile(r"(?i)ACP[\s・_-]*MAX|HD[- ]?PRP\s*[（(]ACP\s*MAX")),
("ACP",re.compile(r"(?i)\bACP\b|ACPダブルシリンジ|ACP[- ]?PRP")),
("Angel",re.compile(r"(?i)\bAngel\b(?:\s*c?PRP)?")),
("GPS",re.compile(r"(?i)G\s*P\s*S\s*(?:III|Ⅲ|3)?(?:\s*(?:system|システム|PRPキット))?")),
("APS",re.compile(r"(?i)(?<![A-Za-z])A\s*P\s*S(?![A-Za-z])|Autologous\s+Protein\s+Solution")),
("Condensia",re.compile(r"(?i)Condensia|コンデンシア")),
("MyCells",re.compile(r"(?i)My\s*cells?|Mycells|マイセル")),
("TriCeLL",re.compile(r"(?i)Tri\s*Cell|TriCeLL|トライセル")),
("MAGELLAN",re.compile(r"(?i)MAGELLAN|Magellan|マゼラン")),
("PRGF-Endoret",re.compile(r"(?i)PRGF[- ]?Endoret|Endoret|PRGF")),
("PEAK",re.compile(r"(?i)PEAK\s*(?:PRP)?\s*(?:System|システム)?")),
("YCELL",re.compile(r"(?i)(?<![A-Za-z])Y\s*CELL(?:BIO)?(?:\s*Medical)?|ワイセル")),
]
MAKER_PATTERNS=[
("Zimmer Biomet",re.compile(r"(?i)Zimmer\s*Biomet|ZimmerBiomet|ジンマー(?:・|\s|-)?バイオメット")),
("Arthrex",re.compile(r"(?i)Arthrex|アースレックス")),
("京セラ",re.compile(r"(?i)Kyocera|京セラ")),
("BTI",re.compile(r"(?i)\bBTI\b")),
("ESTAR Technologies",re.compile(r"(?i)ESTAR\s*TECHNOLOGIES")),
("メッド・アライアンス",re.compile(r"(?i)メッド[・･ ]?(?:アライアンス|アイアンス)")),
("ハイレックスメディカル",re.compile(r"(?i)ハイレックスメディカル|HI[- ]?LEX\s*MEDICAL")),
("Ycellbio Medical",re.compile(r"(?i)Ycellbio\s*Medical")),
]
# Only mappings validated from manufacturer / distributor primary sources are auto-confirmed.
VALIDATED_PRODUCT_MAKER={
"GPS":"Zimmer Biomet",
"APS":"Zimmer Biomet",
"ACP":"Arthrex",
"ACP MAX":"Arthrex",
"Angel":"Arthrex",
"Condensia":"京セラ",
"PRGF-Endoret":"BTI",
}
UNVALIDATED_PRODUCTS={"MyCells","TriCeLL","MAGELLAN","PEAK","YCELL"}

def norm(s):
    return unicodedata.normalize("NFKC",str(s or "")).replace("\u3000"," ").strip()

def compact(s):
    return re.sub(r"\s+","",norm(s))

def facility_key(pref,fac):
    return norm(pref)+"|"+compact(fac)

def read_tsv(path):
    with open(path,encoding="utf-8-sig",newline="") as f:
        return list(csv.DictReader(f,delimiter="\t"))

def add_tokens(target,value):
    for x in re.split(r"[|/]",norm(value)):
        x=norm(x)
        if x and x not in {"未確認","不明","UNKNOWN"}:
            target.add(x)

def build_candidates():
    rows=[]
    for p in sorted(glob.glob(str(RESULTS/"v42_v40_price_stage_[ABC]_batch*.tsv"))):
        for r in read_tsv(p):
            rows.append({
                "source":"v40_queue",
                "prefecture":r.get("prefecture",""),
                "facility":r.get("facility",""),
                "class":r.get("treatment_class",""),
                "code":r.get("mhlw_plan_code",""),
                "maker":r.get("makers",""),
                "product":r.get("products",""),
            })
    for p in sorted(glob.glob(str(RESULTS/"v42_price_merge_stage_batch*.tsv"))):
        for r in read_tsv(p):
            rows.append({
                "source":"v41",
                "prefecture":r.get("prefecture",""),
                "facility":r.get("facility",""),
                "class":r.get("treatment_class",""),
                "code":r.get("mhlw_plan_code",""),
                "maker":r.get("maker",""),
                "product":r.get("product",""),
            })

    fac=defaultdict(lambda:{
        "prefecture":"","facility":"","codes":set(),"classes":set(),
        "makers":set(),"products":set(),"sources":set()
    })
    for r in rows:
        k=facility_key(r["prefecture"],r["facility"])
        x=fac[k]
        x["prefecture"]=norm(r["prefecture"])
        x["facility"]=norm(r["facility"])
        x["sources"].add(r["source"])
        if norm(r["code"]): x["codes"].add(norm(r["code"]))
        if norm(r["class"]): x["classes"].add(norm(r["class"]))
        add_tokens(x["makers"],r["maker"])
        add_tokens(x["products"],r["product"])

    candidates=[]
    for k,x in fac.items():
        if x["makers"]: continue
        if not x["codes"]: continue
        joined="|".join(x["classes"]).upper()
        if "PRP" not in joined and "APS" not in joined: continue
        candidates.append({
            "facility_key":k,
            "prefecture":x["prefecture"],
            "facility":x["facility"],
            "mhlw_plan_codes":"|".join(sorted(x["codes"])),
            "treatment_classes":"|".join(sorted(x["classes"])),
            "source_generations":"|".join(sorted(x["sources"])),
            "baseline_products":"|".join(sorted(x["products"])),
        })
    candidates.sort(key=lambda r:(r["prefecture"],r["facility"],r["mhlw_plan_codes"]))
    return candidates,len(fac)

def pdf_pages(data):
    try:
        rd=PdfReader(io.BytesIO(data))
    except Exception:
        return []
    out=[]
    for i,p in enumerate(rd.pages,1):
        try: t=p.extract_text() or ""
        except Exception: t=""
        out.append((i,norm(t)))
    return out

def scan_pages(pages):
    products=set(); explicit=set(); evidence=[]
    for page_no,text in pages:
        for product,rx in PRODUCT_PATTERNS:
            m=rx.search(text)
            if m:
                products.add(product)
                a=max(0,m.start()-110);b=min(len(text),m.end()+180)
                evidence.append({
                    "page":page_no,"type":"product","hit":product,
                    "context":re.sub(r"\s+"," ",text[a:b]).strip()
                })
        for maker,rx in MAKER_PATTERNS:
            m=rx.search(text)
            if m:
                explicit.add(maker)
                a=max(0,m.start()-110);b=min(len(text),m.end()+180)
                evidence.append({
                    "page":page_no,"type":"maker","hit":maker,
                    "context":re.sub(r"\s+"," ",text[a:b]).strip()
                })
    if "ACP MAX" in products and "ACP" in products:
        products.remove("ACP")
    inferred={VALIDATED_PRODUCT_MAKER[p] for p in products if p in VALIDATED_PRODUCT_MAKER}
    product_only={p for p in products if p in UNVALIDATED_PRODUCTS}
    return sorted(products),sorted(explicit),sorted(inferred),sorted(product_only),evidence[:24]

async def fetch_pdf(context,url):
    try:
        resp=await context.request.get(url,timeout=10000,fail_on_status_code=False)
        status=resp.status
        ctype=(resp.headers.get("content-type") or "").lower()
        body=await resp.body()
        if status==200 and (body.startswith(b"%PDF") or "pdf" in ctype):
            return body,status,ctype
        return None,status,ctype
    except Exception as e:
        return None,None,type(e).__name__

async def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--batch",type=int,required=True)
    ap.add_argument("--batches",type=int,default=8)
    ap.add_argument("--max-index",type=int,default=6)
    args=ap.parse_args()

    candidates,total_facilities=build_candidates()
    subset=[r for i,r in enumerate(candidates) if i%args.batches==args.batch]
    outdir=RESULTS/f"maker_full_mhlw/batch{args.batch:02d}"
    outdir.mkdir(parents=True,exist_ok=True)

    results=[]
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True)
        context=await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
            locale="ja-JP",
        )
        page=await context.new_page()
        try:
            await page.goto("https://saiseiiryo.mhlw.go.jp/published_plan/index/3",
                            wait_until="domcontentloaded",timeout=30000)
        except Exception:
            pass

        for n,row in enumerate(subset,1):
            products=set();explicit=set();inferred=set();product_only=set();evidence=[]
            pdf_count=0;attempts=0;urls=[];statuses=[]
            for code in [c for c in row["mhlw_plan_codes"].split("|") if c]:
                consecutive_404=0
                for idx in range(args.max_index+1):
                    attempts+=1
                    url=f"https://saiseiiryo.mhlw.go.jp/published_plan/download/{code}/5/{idx}"
                    data,status,ctype=await fetch_pdf(context,url)
                    statuses.append(f"{code}:{idx}:{status}")
                    if data is None:
                        if status==404:
                            consecutive_404+=1
                            if idx>=2 and consecutive_404>=2:
                                break
                        continue
                    consecutive_404=0
                    pdf_count+=1;urls.append(url)
                    ps,ms,ims,pos,ev=scan_pages(pdf_pages(data))
                    products.update(ps);explicit.update(ms);inferred.update(ims);product_only.update(pos)
                    for e in ev:
                        evidence.append({"code":code,"idx":idx,"url":url,**e})

            if explicit:
                final_makers=set(explicit)
                evidence_type="EXPLICIT_MHLW"
                safe="YES"
            elif inferred:
                final_makers=set(inferred)
                evidence_type="VALIDATED_PRODUCT_MAP"
                safe="YES"
            else:
                final_makers=set()
                evidence_type="PRODUCT_ONLY_UNVALIDATED" if products else "NONE"
                safe="NO"

            results.append({
                "global_candidate_count":len(candidates),
                "repo_facility_count":total_facilities,
                "batch":args.batch,
                "batch_index":n,
                **row,
                "products_found":"|".join(sorted(products)),
                "explicit_makers":"|".join(sorted(explicit)),
                "validated_inferred_makers":"|".join(sorted(inferred)),
                "unvalidated_products":"|".join(sorted(product_only)),
                "final_makers":"|".join(sorted(final_makers)),
                "maker_evidence_type":evidence_type,
                "safe_resolved":safe,
                "pdf_count":pdf_count,
                "attempts":attempts,
                "document_urls":"|".join(urls),
                "http_statuses":"|".join(statuses),
                "evidence_json":json.dumps(evidence[:30],ensure_ascii=False),
            })
        await browser.close()

    fields=list(results[0].keys()) if results else []
    if results:
        with (outdir/"results.tsv").open("w",encoding="utf-8",newline="") as f:
            w=csv.DictWriter(f,fieldnames=fields,delimiter="\t")
            w.writeheader();w.writerows(results)

    summary={
        "batch":args.batch,
        "batches":args.batches,
        "global_candidate_count":len(candidates),
        "repo_facility_count":total_facilities,
        "input":len(results),
        "facilities_with_pdf":sum(int(r["pdf_count"])>0 for r in results),
        "pdf_documents":sum(int(r["pdf_count"]) for r in results),
        "safe_resolved":sum(r["safe_resolved"]=="YES" for r in results),
        "explicit_mhlw":sum(r["maker_evidence_type"]=="EXPLICIT_MHLW" for r in results),
        "validated_product_map":sum(r["maker_evidence_type"]=="VALIDATED_PRODUCT_MAP" for r in results),
        "product_only_unvalidated":sum(r["maker_evidence_type"]=="PRODUCT_ONLY_UNVALIDATED" for r in results),
        "no_brand_hit":sum(r["maker_evidence_type"]=="NONE" for r in results),
    }
    (outdir/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=False))

asyncio.run(main())
