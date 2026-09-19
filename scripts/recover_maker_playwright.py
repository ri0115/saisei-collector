#!/usr/bin/env python3
import argparse, asyncio, csv, io, json, re
from pathlib import Path
from pypdf import PdfReader
from playwright.async_api import async_playwright

IN=Path("data/maker_pilot50_unresolved.tsv")

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
("メッド・アライアンス",re.compile(r"(?i)メッド[・･ ]?(?:アライアンス|アイアンス)")),
("ハイレックスメディカル",re.compile(r"(?i)ハイレックスメディカル|HI[- ]?LEX\s*MEDICAL")),
("BTI",re.compile(r"(?i)\bBTI\b")),
("Ycellbio Medical",re.compile(r"(?i)Ycellbio\s*Medical")),
]
PRODUCT_DEFAULT_MAKER={
"GPS":"Zimmer Biomet","APS":"Zimmer Biomet",
"ACP":"Arthrex","ACP MAX":"Arthrex","Angel":"Arthrex",
"Condensia":"京セラ","MyCells":"ESTAR Technologies",
"TriCeLL":"メッド・アライアンス","MAGELLAN":"ハイレックスメディカル",
"PRGF-Endoret":"BTI","YCELL":"Ycellbio Medical"
}

def norm(s): return re.sub(r"\s+"," ",str(s or "")).strip()

def read_rows():
    with IN.open(encoding="utf-8-sig",newline="") as f:
        return list(csv.DictReader(f,delimiter="\t"))

def pdf_pages(data):
    try: rd=PdfReader(io.BytesIO(data))
    except Exception: return []
    out=[]
    for i,p in enumerate(rd.pages,1):
        try: t=p.extract_text() or ""
        except Exception: t=""
        out.append((i,norm(t)))
    return out

def scan_pages(pages):
    products=set(); explicit=set(); evidence=[]
    for page_no,text in pages:
        for p,rx in PRODUCT_PATTERNS:
            m=rx.search(text)
            if m:
                products.add(p)
                a=max(0,m.start()-100); b=min(len(text),m.end()+160)
                evidence.append({"page":page_no,"type":"product","hit":p,"context":text[a:b]})
        for maker,rx in MAKER_PATTERNS:
            m=rx.search(text)
            if m:
                explicit.add(maker)
                a=max(0,m.start()-100); b=min(len(text),m.end()+160)
                evidence.append({"page":page_no,"type":"maker","hit":maker,"context":text[a:b]})
    if "ACP MAX" in products and "ACP" in products: products.remove("ACP")
    inferred={PRODUCT_DEFAULT_MAKER[p] for p in products if p in PRODUCT_DEFAULT_MAKER}
    return sorted(products),sorted(explicit),sorted(inferred),evidence[:16]

async def fetch_pdf(context,url):
    try:
        resp=await context.request.get(url,timeout=8000,fail_on_status_code=False)
        status=resp.status
        ctype=(resp.headers.get("content-type") or "").lower()
        body=await resp.body()
        if status==200 and (body.startswith(b"%PDF") or "pdf" in ctype):
            return body,status,ctype
        return None,status,ctype
    except Exception:
        return None,None,None

async def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--batch",type=int,required=True)
    ap.add_argument("--size",type=int,default=12)
    ap.add_argument("--max-index",type=int,default=6)
    args=ap.parse_args()
    allrows=read_rows()
    start=args.batch*args.size
    rows=allrows[start:start+args.size]
    outdir=Path(f"results/maker_playwright_pilot/batch{args.batch:02d}")
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
            await page.goto("https://saiseiiryo.mhlw.go.jp/published_plan/index/3",wait_until="domcontentloaded",timeout=30000)
        except Exception:
            pass

        for row in rows:
            products=set(); explicit=set(); inferred=set(); evidence=[]
            downloads=0; attempts=0; download_urls=[]; statuses=[]
            for code in [c for c in row["mhlw_plan_codes"].split("|") if c]:
                consecutive_missing=0
                for idx in range(args.max_index+1):
                    attempts+=1
                    url=f"https://saiseiiryo.mhlw.go.jp/published_plan/download/{code}/5/{idx}"
                    data,status,ctype=await fetch_pdf(context,url)
                    statuses.append(f"{code}:{idx}:{status}")
                    if not data:
                        if status==404:
                            consecutive_missing+=1
                            if idx>=2 and consecutive_missing>=2:
                                break
                        continue
                    consecutive_missing=0
                    downloads+=1; download_urls.append(url)
                    ps,ms,ims,ev=scan_pages(pdf_pages(data))
                    products.update(ps); explicit.update(ms); inferred.update(ims)
                    for e in ev: evidence.append({"code":code,"idx":idx,"url":url,**e})
            final=set(explicit) if explicit else set(inferred)
            results.append({
                **row,
                "playwright_products_found":"|".join(sorted(products)),
                "playwright_explicit_makers":"|".join(sorted(explicit)),
                "playwright_inferred_makers":"|".join(sorted(inferred)),
                "playwright_final_makers":"|".join(sorted(final)),
                "maker_evidence_type":"EXPLICIT" if explicit else ("PRODUCT_MAP" if inferred else "NONE"),
                "downloads":downloads,"attempts":attempts,
                "resolved":"YES" if final else "NO",
                "download_urls":"|".join(download_urls),
                "http_statuses":"|".join(statuses),
                "evidence_json":json.dumps(evidence[:20],ensure_ascii=False),
            })
        await browser.close()

    fields=list(results[0].keys()) if results else []
    if results:
        with (outdir/"results.tsv").open("w",encoding="utf-8",newline="") as f:
            w=csv.DictWriter(f,fieldnames=fields,delimiter="\t"); w.writeheader(); w.writerows(results)
    summary={
        "batch":args.batch,"start":start,"input":len(results),
        "facilities_with_any_pdf":sum(int(r["downloads"])>0 for r in results),
        "pdf_downloads":sum(int(r["downloads"]) for r in results),
        "resolved":sum(r["resolved"]=="YES" for r in results),
        "explicit":sum(r["maker_evidence_type"]=="EXPLICIT" for r in results),
        "product_map":sum(r["maker_evidence_type"]=="PRODUCT_MAP" for r in results),
        "unresolved":sum(r["resolved"]=="NO" for r in results),
    }
    (outdir/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=False))

asyncio.run(main())
