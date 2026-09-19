#!/usr/bin/env python3
from __future__ import annotations

import csv, io, json, re, time, zipfile
from collections import defaultdict
from pathlib import Path

import requests
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
ADDITIONS = RESULTS / "v42_additions_final_compact.tsv"
AUTO = RESULTS / "v42_auto_prices_compact.tsv"

OUT_CAND = RESULTS / "v42_maker_pilot50_candidates.tsv"
OUT_RES = RESULTS / "v42_maker_pilot50_results.tsv"
OUT_SUM = RESULTS / "v42_maker_pilot50_summary.json"

PRODUCT_PATTERNS = [
    ("ACP MAX", [r"\bACP\s*MAX\b"]),
    ("GPS III", [r"\bGPS\s*(?:III|3|Ⅲ)\b"]),
    ("PRGF-Endoret", [r"\bPRGF\b", r"ENDO(?:RET|RET®)"]),
    ("Condensia", [r"CONDENSIA", r"コンデンシア"]),
    ("MyCells", [r"MY\s*CELLS?", r"MYCELLS", r"マイセル"]),
    ("TriCeLL", [r"TRICELL", r"TRI\s*CELL"]),
    ("MAGELLAN", [r"MAGELLAN"]),
    ("Angel", [r"\bANGEL\b"]),
    ("APS", [r"\bAPS\b", r"AUTologous\s*PROTEIN\s*SOLUTION"]),
    ("GPS", [r"\bGPS\b"]),
    ("ACP", [r"\bACP\b"]),
    ("PEAK", [r"\bPEAK\b"]),
    ("YCELL", [r"\bYCELL\b", r"Y\s*CELL"]),
]
MAKER_BY_PRODUCT = {
    "ACP": "Arthrex",
    "ACP MAX": "Arthrex",
    "Angel": "Arthrex",
    "GPS": "Zimmer Biomet",
    "GPS III": "Zimmer Biomet",
    "APS": "Zimmer Biomet",
    "Condensia": "京セラ",
    "PRGF-Endoret": "BTI",
    "MyCells": "MyCells系",
    "TriCeLL": "TriCeLL系",
    "PEAK": "PEAK系",
    "MAGELLAN": "MAGELLAN系",
    "YCELL": "Ycellbio Medical",
}
DIRECT_MAKER_PATTERNS = [
    ("Zimmer Biomet", [r"ZIMMER\s*BIOMET", r"ZIMMERBIOMET", r"ジンマー\s*バイオメット"]),
    ("Arthrex", [r"ARTHREX", r"アースレックス"]),
    ("京セラ", [r"京セラ", r"KYOCERA"]),
    ("BTI", [r"\bBTI\b"]),
    ("Ycellbio Medical", [r"YCELLBIO", r"Y\s*CELL\s*BIO"]),
]
PRPISH = ("PRP", "APS")

def read_tsv(path: Path):
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))

def write_tsv(path: Path, rows, fields):
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, delimiter="\t", extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

def norm(s):
    return re.sub(r"\s+", " ", (s or "")).strip()

def relevant_class(s):
    u = (s or "").upper()
    return any(x in u for x in PRPISH)

def extract_pages(raw: bytes):
    # PDF
    if b"%PDF" in raw[:1024]:
        try:
            reader = PdfReader(io.BytesIO(raw))
        except Exception:
            return []
        pages=[]
        for i,p in enumerate(reader.pages,1):
            try:
                txt=p.extract_text() or ""
            except Exception:
                txt=""
            pages.append((i, txt))
        return pages
    # DOCX / OOXML fallback
    if raw[:2] == b"PK":
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                xml=z.read("word/document.xml").decode("utf-8","ignore")
            txt=re.sub(r"<w:tab[^>]*/>", "\t", xml)
            txt=re.sub(r"</w:p>", "\n", txt)
            txt=re.sub(r"<[^>]+>", "", txt)
            return [(1, txt)]
        except Exception:
            return []
    return []

def match_page(text: str):
    upper = text.upper()
    products=[]
    for canonical, pats in PRODUCT_PATTERNS:
        if any(re.search(p, upper, re.I) for p in pats):
            products.append(canonical)
    # De-duplicate generic product tokens when specific variants found.
    if "ACP MAX" in products and "ACP" in products:
        products.remove("ACP")
    if "GPS III" in products and "GPS" in products:
        products.remove("GPS")
    makers=[]
    for maker,pats in DIRECT_MAKER_PATTERNS:
        if any(re.search(p, text, re.I) for p in pats):
            makers.append(maker)
    return sorted(set(products)), sorted(set(makers))

FETCH_DIAG=defaultdict(int)

def fetch_attachments(code: str, session: requests.Session):
    docs=[]
    for idx in range(10):
        url=f"https://saiseiiryo.mhlw.go.jp/published_plan/download/{code}/5/{idx}"
        try:
            r=session.get(
                url, timeout=25, allow_redirects=True,
                headers={"Referer":"https://saiseiiryo.mhlw.go.jp/published_plan/index/3",
                         "Accept":"application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,*/*"}
            )
        except Exception as e:
            FETCH_DIAG["exception"] += 1
            continue
        ctype=(r.headers.get("content-type") or "").lower()
        FETCH_DIAG[f"status_{r.status_code}"] += 1
        FETCH_DIAG[f"ctype_{ctype.split(';')[0]}"] += 1
        if r.status_code != 200:
            continue
        pages=extract_pages(r.content)
        if not pages:
            FETCH_DIAG["unparsed_200"] += 1
            continue
        FETCH_DIAG["parsed_attachment"] += 1
        docs.append((idx, url, pages))
        time.sleep(0.08)
    return docs

adds=read_tsv(ADDITIONS)
auto=read_tsv(AUTO)

known_maker_codes=set()
known_maker_facilities=set()
for r in auto:
    maker=norm(r.get("maker",""))
    if not maker:
        continue
    code=norm(r.get("mhlw_plan_code",""))
    if code:
        known_maker_codes.add(code)

add_by_code={r["mhlw_plan_code"]:r for r in adds}
for code in known_maker_codes:
    r=add_by_code.get(code)
    if r:
        known_maker_facilities.add((norm(r["prefecture"]), norm(r["facility"])))

groups=defaultdict(list)
for r in adds:
    if r.get("scope_status")=="OUT_OF_SCOPE":
        continue
    if not relevant_class(r.get("treatment_class","")):
        continue
    key=(norm(r["prefecture"]), norm(r["facility"]))
    groups[key].append(r)

eligible=[]
for key,rows in groups.items():
    if key in known_maker_facilities:
        continue
    codes=sorted({norm(r["mhlw_plan_code"]) for r in rows if norm(r.get("mhlw_plan_code",""))})
    if not codes:
        continue
    eligible.append((key,rows,codes))

# Round-robin by prefecture to avoid Tokyo-heavy pilot.
by_pref=defaultdict(list)
for item in sorted(eligible, key=lambda x:(x[0][0],x[0][1])):
    by_pref[item[0][0]].append(item)
prefs=sorted(by_pref)
pilot=[]
while len(pilot)<50 and any(by_pref[p] for p in prefs):
    for p in prefs:
        if by_pref[p] and len(pilot)<50:
            pilot.append(by_pref[p].pop(0))

candidate_rows=[]
for i,(key,rows,codes) in enumerate(pilot,1):
    candidate_rows.append({
        "pilot_id":f"PILOT{i:03d}",
        "prefecture":key[0],
        "facility":key[1],
        "mhlw_plan_codes":"|".join(codes),
        "treatment_classes":"|".join(sorted({norm(r["treatment_class"]) for r in rows})),
        "plan_count":len(codes),
        "baseline_maker_status":"UNKNOWN",
    })
write_tsv(OUT_CAND, candidate_rows, list(candidate_rows[0].keys()) if candidate_rows else
          ["pilot_id","prefecture","facility","mhlw_plan_codes","treatment_classes","plan_count","baseline_maker_status"])

session=requests.Session()
session.headers.update({
    "User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
    "Accept-Language":"ja,en-US;q=0.9,en;q=0.8",
})
# Warm up cookies / WAF session before direct downloads.
try:
    session.get("https://saiseiiryo.mhlw.go.jp/published_plan/index/3", timeout=20)
except Exception:
    pass

result_rows=[]
facility_found=set()
attachment_count=0
for cand in candidate_rows:
    fac_key=(cand["prefecture"],cand["facility"])
    for code in cand["mhlw_plan_codes"].split("|"):
        docs=fetch_attachments(code, session)
        attachment_count += len(docs)
        for idx,url,pages in docs:
            for page_no,text in pages:
                products,direct_makers=match_page(text)
                if not products and not direct_makers:
                    continue
                makers=set(direct_makers)
                maker_basis=[]
                if direct_makers:
                    maker_basis.append("DIRECT_TEXT")
                for product in products:
                    mapped=MAKER_BY_PRODUCT.get(product)
                    if mapped:
                        makers.add(mapped)
                        maker_basis.append("PRODUCT_DICTIONARY")
                if products or makers:
                    facility_found.add(fac_key)
                    result_rows.append({
                        "pilot_id":cand["pilot_id"],
                        "prefecture":cand["prefecture"],
                        "facility":cand["facility"],
                        "mhlw_plan_code":code,
                        "attachment_index":idx,
                        "page":page_no,
                        "products":"|".join(products),
                        "makers":"|".join(sorted(makers)),
                        "maker_basis":"|".join(sorted(set(maker_basis))),
                        "evidence_url":url,
                        "evidence_level":"MHLW_EXACT_PLAN_ATTACHMENT",
                    })

# exact duplicate evidence rows removed
seen=set(); dedup=[]
for r in result_rows:
    k=tuple(r.values())
    if k in seen: continue
    seen.add(k); dedup.append(r)
result_rows=dedup

write_tsv(OUT_RES, result_rows,
          ["pilot_id","prefecture","facility","mhlw_plan_code","attachment_index","page","products","makers","maker_basis","evidence_url","evidence_level"])

summary={
    "pilot_facilities":len(candidate_rows),
    "eligible_unknown_prp_aps_facilities":len(eligible),
    "facilities_with_maker_or_product_recovered":len(facility_found),
    "facility_recovery_rate":round(len(facility_found)/len(candidate_rows),4) if candidate_rows else 0,
    "mhlw_attachments_opened":attachment_count,
    "evidence_rows":len(result_rows),
    "direct_mhlw_attachment_only":True,
    "fetch_diagnostics":dict(FETCH_DIAG),
    "generated_files":[str(OUT_CAND.relative_to(ROOT)),str(OUT_RES.relative_to(ROOT))],
}
OUT_SUM.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(summary, ensure_ascii=False))
