#!/usr/bin/env python3
import csv, glob, io, json, re, unicodedata, zipfile
from collections import defaultdict, deque
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

OUT = Path("results/maker_pilot")
OUT.mkdir(parents=True, exist_ok=True)

def norm(s):
    return unicodedata.normalize("NFKC", str(s or "")).replace("\u3000"," ").strip()

def facility_key(pref, fac):
    return norm(pref) + "|" + re.sub(r"\\s+", "", norm(fac))

PRODUCT_PATTERNS = [
    ("ACP MAX", re.compile(r"(?i)ACP[\\s・_-]*MAX|HD[- ]?PRP\\s*[（(]ACP\\s*MAX")),
    ("ACP", re.compile(r"(?i)(?<!MAX[\\s・_-])\\bACP\\b|ACPダブルシリンジ|ACP[- ]?PRP")),
    ("Angel", re.compile(r"(?i)\\bAngel\\b(?:\\s*c?PRP)?")),
    ("GPS", re.compile(r"(?i)G\\s*P\\s*S\\s*(?:III|Ⅲ|3)?(?:\\s*(?:system|システム|PRPキット))?")),
    ("APS", re.compile(r"(?i)(?<![A-Za-z])A\\s*P\\s*S(?![A-Za-z])|Autologous\\s+Protein\\s+Solution")),
    ("Condensia", re.compile(r"(?i)Condensia|コンデンシア")),
    ("MyCells", re.compile(r"(?i)My\\s*cells?|Mycells")),
    ("TriCeLL", re.compile(r"(?i)Tri\\s*Cell|TriCeLL|トライセル")),
    ("MAGELLAN", re.compile(r"(?i)MAGELLAN|Magellan|マゼラン")),
    ("PRGF-Endoret", re.compile(r"(?i)PRGF[- ]?Endoret|Endoret|PRGF")),
    ("PEAK", re.compile(r"(?i)PEAK\\s*(?:PRP)?\\s*(?:System|システム)?")),
    ("YCELL", re.compile(r"(?i)(?<![A-Za-z])Y\\s*CELL(?:BIO)?(?:\\s*Medical)?|ワイセル")),
]

MAKER_PATTERNS = [
    ("Zimmer Biomet", re.compile(r"(?i)Zimmer\\s*Biomet|Zimmer|Biomet|ジンマー(?:・|\\s|-)?バイオメット|ジンマー")),
    ("Arthrex", re.compile(r"(?i)Arthrex|アースレックス")),
    ("京セラ", re.compile(r"(?i)Kyocera|京セラ")),
    ("ESTAR Technologies", re.compile(r"(?i)ESTAR\\s*TECHNOLOGIES")),
    ("ベリタス", re.compile(r"(?i)Veritas|ベリタス")),
    ("メッド・アライアンス", re.compile(r"(?i)メッド[・･ ]?(?:アライアンス|アイアンス)|MEDD?\\s*ALLIANCE")),
    ("ヤマト科学", re.compile(r"(?i)ヤマト科学|Yamato\\s*Scientific")),
    ("ハイレックスメディカル", re.compile(r"(?i)ハイレックスメディカル|HI[- ]?LEX\\s*MEDICAL")),
    ("BTI", re.compile(r"(?i)\\bBTI\\b")),
    ("Ycellbio Medical", re.compile(r"(?i)Ycellbio\\s*Medical")),
    ("Johnson & Johnson", re.compile(r"(?i)Johnson\\s*&\\s*Johnson|ジョンソン[・･ ]?エンド[・･ ]?ジョンソン")),
]

PRODUCT_DEFAULT_MAKER = {
    "GPS":"Zimmer Biomet", "APS":"Zimmer Biomet",
    "ACP":"Arthrex", "ACP MAX":"Arthrex", "Angel":"Arthrex",
    "Condensia":"京セラ", "MyCells":"ESTAR Technologies",
    "TriCeLL":"メッド・アライアンス", "MAGELLAN":"ハイレックスメディカル",
    "PRGF-Endoret":"BTI", "YCELL":"Ycellbio Medical"
}

def read_tsv(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))

def build_pilot(limit=50):
    rows=[]
    for p in sorted(glob.glob("results/v42_v40_price_stage_[ABC]_batch*.tsv")):
        for r in read_tsv(p):
            rows.append({
                "source":"v40_queue","prefecture":r.get("prefecture",""),"facility":r.get("facility",""),
                "class":r.get("treatment_class",""),"code":r.get("mhlw_plan_code",""),
                "maker":r.get("makers",""),"product":r.get("products","")
            })
    for p in sorted(glob.glob("results/v42_price_merge_stage_batch*.tsv")):
        for r in read_tsv(p):
            rows.append({
                "source":"v41","prefecture":r.get("prefecture",""),"facility":r.get("facility",""),
                "class":r.get("treatment_class",""),"code":r.get("mhlw_plan_code",""),
                "maker":r.get("maker",""),"product":r.get("product","")
            })

    fac=defaultdict(lambda: {"prefecture":"","facility":"","codes":set(),"classes":set(),"makers":set(),"products":set(),"sources":set()})
    for r in rows:
        k=facility_key(r["prefecture"], r["facility"])
        x=fac[k]; x["prefecture"]=norm(r["prefecture"]); x["facility"]=norm(r["facility"])
        x["sources"].add(r["source"])
        if norm(r["code"]): x["codes"].add(norm(r["code"]))
        if norm(r["class"]): x["classes"].add(norm(r["class"]))
        for m in re.split(r"[|/]", norm(r["maker"])):
            if m and m not in {"未確認","不明"}: x["makers"].add(m)
        for p in re.split(r"[|/]", norm(r["product"])):
            if p: x["products"].add(p)

    candidates=[]
    for k,x in fac.items():
        if x["makers"]: continue
        if not x["codes"]: continue
        joined="|".join(x["classes"])
        if "PRP" not in joined and "APS" not in joined: continue
        candidates.append((k,x))

    by_pref=defaultdict(deque)
    for k,x in sorted(candidates, key=lambda kv:(kv[1]["prefecture"], kv[1]["facility"])):
        by_pref[x["prefecture"]].append((k,x))
    prefs=sorted(by_pref)
    picked=[]
    while len(picked)<limit and any(by_pref[p] for p in prefs):
        for p in prefs:
            if by_pref[p] and len(picked)<limit:
                picked.append(by_pref[p].popleft())

    out=[]
    for i,(k,x) in enumerate(picked,1):
        out.append({
            "pilot_id":f"PILOT{i:03d}","prefecture":x["prefecture"],"facility":x["facility"],
            "mhlw_plan_codes":"|".join(sorted(x["codes"])),"treatment_classes":"|".join(sorted(x["classes"])),
            "source_generations":"|".join(sorted(x["sources"]))
        })
    return out, len(candidates), len(fac)

def text_from_response(resp):
    b=resp.content
    ct=(resp.headers.get("content-type") or "").lower()
    if b.startswith(b"%PDF") or "pdf" in ct:
        try:
            rd=PdfReader(io.BytesIO(b))
            return "\n".join((p.extract_text() or "") for p in rd.pages)
        except Exception:
            return ""
    if b.startswith(b"PK") or "wordprocessingml" in ct:
        try:
            z=zipfile.ZipFile(io.BytesIO(b))
            xml=z.read("word/document.xml")
            soup=BeautifulSoup(xml, "xml")
            return " ".join(t.get_text(" ", strip=True) for t in soup.find_all(["w:t","t"]))
        except Exception:
            return ""
    try:
        enc=resp.encoding or "utf-8"
        s=b.decode(enc, errors="ignore")
    except Exception:
        s=b.decode("utf-8", errors="ignore")
    return BeautifulSoup(s, "html.parser").get_text("\n", strip=True)

def contexts(text, regex, width=120):
    out=[]
    for m in regex.finditer(text):
        a=max(0,m.start()-width); b=min(len(text),m.end()+width)
        out.append(re.sub(r"\\s+"," ",text[a:b]).strip())
        if len(out)>=3: break
    return out

def scan_code(session, code):
    found_products=set(); explicit_makers=set(); evidence=[]; docs_checked=0
    for idx in range(0,7):
        url=f"https://saiseiiryo.mhlw.go.jp/published_plan/download/{code}/5/{idx}"
        try:
            resp=session.get(url, timeout=25, allow_redirects=True)
        except Exception:
            continue
        if resp.status_code!=200 or len(resp.content)<300:
            continue
        text=norm(text_from_response(resp))
        if not text or ("ページが見つかりません" in text and len(text)<2000):
            continue
        docs_checked += 1
        for product,rx in PRODUCT_PATTERNS:
            if rx.search(text):
                found_products.add(product)
                evidence.append({"url":url,"type":"product","hit":product,"context":contexts(text,rx)[0] if contexts(text,rx) else ""})
        for maker,rx in MAKER_PATTERNS:
            if rx.search(text):
                explicit_makers.add(maker)
                evidence.append({"url":url,"type":"maker","hit":maker,"context":contexts(text,rx)[0] if contexts(text,rx) else ""})
    inferred=set(PRODUCT_DEFAULT_MAKER[p] for p in found_products if p in PRODUCT_DEFAULT_MAKER)
    return {
        "products":sorted(found_products),"explicit_makers":sorted(explicit_makers),
        "inferred_makers":sorted(inferred),"docs_checked":docs_checked,"evidence":evidence
    }

def main():
    pilot,total_candidates,total_facilities=build_pilot(50)
    sess=requests.Session()
    sess.headers["User-Agent"]="Mozilla/5.0 maker-recovery-audit/1.0"
    result=[]
    for item in pilot:
        products=set(); explicit=set(); inferred=set(); docs=0; ev=[]
        for code in item["mhlw_plan_codes"].split("|"):
            sc=scan_code(sess, code)
            products.update(sc["products"]); explicit.update(sc["explicit_makers"]); inferred.update(sc["inferred_makers"])
            docs += sc["docs_checked"]
            for e in sc["evidence"]:
                ev.append({"code":code,**e})
        makers=explicit or inferred
        result.append({**item,
            "products_found":"|".join(sorted(products)),
            "makers_found":"|".join(sorted(makers)),
            "maker_evidence_type":"EXPLICIT" if explicit else ("PRODUCT_MAP" if inferred else "NONE"),
            "docs_checked":docs,
            "resolved":"YES" if makers else "NO",
            "evidence_json":json.dumps(ev[:12], ensure_ascii=False)
        })

    fields=list(result[0].keys())
    with open(OUT/"maker_pilot50_results.tsv","w",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields,delimiter="\t"); w.writeheader(); w.writerows(result)
    unresolved=[r for r in result if r["resolved"]=="NO"]
    with open(OUT/"maker_pilot50_unresolved.tsv","w",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields,delimiter="\t"); w.writeheader(); w.writerows(unresolved)
    product_counts=defaultdict(int); maker_counts=defaultdict(int)
    for r in result:
        for p in filter(None,r["products_found"].split("|")): product_counts[p]+=1
        for m in filter(None,r["makers_found"].split("|")): maker_counts[m]+=1
    summary={
        "pilot_facilities":len(result),"candidate_facilities_in_repo_scope":total_candidates,
        "facilities_in_repo_scope":total_facilities,
        "resolved_facilities":sum(r["resolved"]=="YES" for r in result),
        "resolution_rate":round(sum(r["resolved"]=="YES" for r in result)/len(result),4),
        "explicit_maker_facilities":sum(r["maker_evidence_type"]=="EXPLICIT" for r in result),
        "product_map_only_facilities":sum(r["maker_evidence_type"]=="PRODUCT_MAP" for r in result),
        "unresolved_facilities":len(unresolved),
        "product_counts":dict(sorted(product_counts.items(), key=lambda kv:(-kv[1],kv[0]))),
        "maker_counts":dict(sorted(maker_counts.items(), key=lambda kv:(-kv[1],kv[0]))),
    }
    (OUT/"maker_pilot50_summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
