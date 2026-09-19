#!/usr/bin/env python3
import csv, json, re, unicodedata
from pathlib import Path
from collections import Counter
import requests
from bs4 import BeautifulSoup

ROOT=Path(".")
BASE=Path("results/v42_maker_recovery_full366.tsv")
MANUAL=Path("results/v42_maker_manual_verified_additions.tsv")
OUT=Path("results/maker_locator_crossmatch")
OUT.mkdir(parents=True,exist_ok=True)

ARTHREX_URL="https://seikei-saisei.jp/institution.html"
ZIMMER_URL="https://m.kansetsu-life.com/saisei/index.php"

LEGAL=[
"社会医療法人","医療法人社団","医療法人財団","医療法人","一般社団法人","一般財団法人",
"公益財団法人","公益社団法人","社会福祉法人","学校法人","国立大学法人","公立大学法人",
"地方独立行政法人","独立行政法人国立病院機構","独立行政法人地域医療機能推進機構",
"独立行政法人","国家公務員共済組合連合会","公立学校共済組合","株式会社"
]

def norm(s):
    s=unicodedata.normalize("NFKC",str(s or "")).replace("\u3000"," ").strip()
    s=re.sub(r"\s+","",s)
    s=re.sub(r"[・･\-‐‑–—―,，.。()（）「」『』【】\[\]　]","",s)
    return s.lower()

def strip_legal(s):
    x=norm(s)
    for p in LEGAL:
        x=x.replace(norm(p),"")
    return x

def read_tsv(path):
    with path.open(encoding="utf-8-sig",newline="") as f:
        return list(csv.DictReader(f,delimiter="\t"))

def get(url):
    r=requests.get(url,timeout=30,headers={"User-Agent":"Mozilla/5.0 manufacturer-locator-crossmatch/1.0"})
    r.raise_for_status()
    # Arthrex locator is UTF-8 but its HTTP charset can be misdetected by requests.
    if "seikei-saisei.jp" in url:
        r.encoding="utf-8"
    elif not r.encoding:
        r.encoding=r.apparent_encoding or "utf-8"
    return BeautifulSoup(r.text,"html.parser")

def parse_arthrex():
    soup=get(ARTHREX_URL)
    out=[]
    current_pref=""
    current=None
    prefs=set(["北海道","青森県","岩手県","宮城県","秋田県","山形県","福島県","茨城県","栃木県","群馬県","埼玉県","千葉県","東京都","神奈川県","新潟県","富山県","石川県","福井県","山梨県","長野県","岐阜県","静岡県","愛知県","三重県","滋賀県","京都府","大阪府","兵庫県","奈良県","和歌山県","鳥取県","島根県","岡山県","広島県","山口県","徳島県","香川県","愛媛県","高知県","福岡県","佐賀県","長崎県","熊本県","大分県","宮崎県","鹿児島県","沖縄県"])
    # Headings are nested in cards, so read all headings in document order rather than siblings.
    for h in soup.find_all(["h2","h3","h4"]):
        txt=h.get_text(" ",strip=True)
        if h.name=="h2" and txt in prefs:
            current_pref=txt
        elif h.name=="h3":
            if current and current["products"]:
                out.append(current)
            current={"source":"Arthrex","name":txt,"products":[],"address":current_pref,"source_url":ARTHREX_URL}
        elif h.name=="h4" and current:
            if "ACP-PRP" in txt: current["products"].append("ACP-PRP")
            if "HD-PRP" in txt: current["products"].append("HD-PRP")
    if current and current["products"]:
        out.append(current)
    for x in out:
        x["products"]="|".join(sorted(set(x["products"])))
    return out

def parse_zimmer():
    soup=get(ZIMMER_URL)
    text=soup.get_text("\n",strip=True)
    lines=[re.sub(r"\s+"," ",x).strip() for x in text.splitlines() if x.strip()]
    out=[]
    cur_pref=""
    i=0
    while i<len(lines):
        line=lines[i]
        # facility blocks rendered as "名称 | X"; BeautifulSoup often splits labels/values.
        if line=="名称" and i+1<len(lines):
            name=lines[i+1].lstrip("|｜ ").strip()
            therapies=""
            address=""
            j=i+2
            while j<min(i+12,len(lines)) and lines[j]!="名称":
                if "PRP療法" in lines[j] or "APS療法" in lines[j]:
                    therapies=lines[j]
                if "〒" in lines[j]:
                    address=lines[j]
                j+=1
            products=[]
            if "PRP療法" in therapies: products.append("PRP")
            if "APS療法" in therapies: products.append("APS")
            if products:
                out.append({"source":"Zimmer Biomet","name":name,"products":"|".join(products),
                            "address":address,"source_url":ZIMMER_URL})
            i=j; continue
        # Alternative table text can be "名称 | facility"
        if line.startswith("名称") and ("|" in line or "｜" in line):
            name=re.split(r"[|｜]",line,1)[1].strip()
            therapies="";address=""
            for z in lines[i+1:min(i+10,len(lines))]:
                if "PRP療法" in z or "APS療法" in z: therapies+=(" "+z)
                if "〒" in z and not address:address=z
            products=[]
            if "PRP療法" in therapies:products.append("PRP")
            if "APS療法" in therapies:products.append("APS")
            if products:
                out.append({"source":"Zimmer Biomet","name":name,"products":"|".join(products),
                            "address":address,"source_url":ZIMMER_URL})
        i+=1
    return out

def match_source_to_targets(source_rows, targets):
    idx_strict={}
    idx_legal={}
    for r in targets:
        idx_strict.setdefault((r["prefecture"],norm(r["facility"])),[]).append(r)
        idx_legal.setdefault((r["prefecture"],strip_legal(r["facility"])),[]).append(r)

    results=[]
    for s in source_rows:
        # prefecture from address or source name is safer than guessing from section HTML.
        pref=""
        for p in [
"北海道","青森県","岩手県","宮城県","秋田県","山形県","福島県","茨城県","栃木県","群馬県","埼玉県","千葉県","東京都","神奈川県",
"新潟県","富山県","石川県","福井県","山梨県","長野県","岐阜県","静岡県","愛知県","三重県","滋賀県","京都府","大阪府","兵庫県","奈良県",
"和歌山県","鳥取県","島根県","岡山県","広島県","山口県","徳島県","香川県","愛媛県","高知県","福岡県","佐賀県","長崎県","熊本県","大分県",
"宮崎県","鹿児島県","沖縄県"]:
            if p in s.get("address",""):
                pref=p;break
        cands=[]
        match_type=""
        if pref:
            cands=idx_strict.get((pref,norm(s["name"])),[])
            if cands: match_type="EXACT_STRICT"
            if not cands:
                cands=idx_legal.get((pref,strip_legal(s["name"])),[])
                if cands: match_type="EXACT_LEGAL_STRIPPED"
        # fallback across all prefectures only when unique exact normalized
        if not cands:
            allc=[r for r in targets if norm(r["facility"])==norm(s["name"])]
            if len(allc)==1:cands=allc;match_type="EXACT_STRICT_NO_PREF"
        if not cands:
            allc=[r for r in targets if strip_legal(r["facility"])==strip_legal(s["name"]) and len(strip_legal(s["name"]))>=5]
            if len(allc)==1:cands=allc;match_type="EXACT_LEGAL_STRIPPED_NO_PREF"

        for t in cands:
            products=set(filter(None,s["products"].split("|")))
            confidence="REVIEW"
            inferred=""
            if s["source"]=="Arthrex" and "ACP-PRP" in products:
                confidence="HIGH"
                inferred="Arthrex"
            elif s["source"]=="Zimmer Biomet" and "APS" in products:
                confidence="HIGH"
                inferred="Zimmer Biomet"
            elif s["source"]=="Arthrex" and "HD-PRP" in products:
                confidence="REVIEW"
                inferred="Arthrex"
            elif s["source"]=="Zimmer Biomet" and "PRP" in products:
                confidence="REVIEW"
                inferred="Zimmer Biomet"
            results.append({
                "facility_key":t["facility_key"],"prefecture":t["prefecture"],"facility":t["facility"],
                "mhlw_plan_codes":t["mhlw_plan_codes"],
                "baseline_safe_resolved":t.get("safe_resolved",""),
                "source_operator":s["source"],"source_facility_name":s["name"],
                "source_products":s["products"],"inferred_manufacturer":inferred,
                "match_type":match_type,"confidence":confidence,
                "source_address":s.get("address",""),"source_url":s["source_url"]
            })
    return results

def main():
    targets=read_tsv(BASE)
    manual=read_tsv(MANUAL) if MANUAL.exists() else []
    manual_keys={(r["prefecture"],strip_legal(r["facility"])) for r in manual if r.get("prefecture") and r.get("facility")}
    arth=parse_arthrex()
    zim=parse_zimmer()

    # Save locator extracts for audit.
    for name,rows in [("arthrex",arth),("zimmer",zim)]:
        fields=["source","name","products","address","source_url"]
        with (OUT/f"{name}_locator.tsv").open("w",encoding="utf-8",newline="") as f:
            w=csv.DictWriter(f,fieldnames=fields,delimiter="\t");w.writeheader();w.writerows(rows)

    matches=match_source_to_targets(arth+zim,targets)
    for r in matches:
        r["already_mhlw_safe"]="YES" if r["baseline_safe_resolved"]=="YES" else "NO"
        r["already_manual_verified"]="YES" if (r["prefecture"],strip_legal(r["facility"])) in manual_keys else "NO"
        r["is_new_candidate"]="YES" if r["already_mhlw_safe"]=="NO" and r["already_manual_verified"]=="NO" else "NO"

    fields=list(matches[0].keys()) if matches else ["facility_key"]
    with (OUT/"matches.tsv").open("w",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields,delimiter="\t");w.writeheader();w.writerows(matches)

    new_high=[r for r in matches if r["is_new_candidate"]=="YES" and r["confidence"]=="HIGH"]
    new_review=[r for r in matches if r["is_new_candidate"]=="YES" and r["confidence"]=="REVIEW"]
    conflicts=[]
    byfac={}
    for r in matches:
        if r["is_new_candidate"]!="YES":continue
        byfac.setdefault(r["facility_key"],set()).add(r["inferred_manufacturer"])
    for k,makers in byfac.items():
        if len(makers)>1:
            conflicts.append({"facility_key":k,"manufacturers":"|".join(sorted(makers))})

    summary={
      "arthrex_locator_facilities":len(arth),
      "zimmer_locator_facilities":len(zim),
      "target_unknown_pool_rows":len(targets),
      "all_exact_matches":len(matches),
      "new_high_confidence_matches":len(new_high),
      "new_review_matches":len(new_review),
      "new_high_unique_facilities":len({r["facility_key"] for r in new_high}),
      "new_review_unique_facilities":len({r["facility_key"] for r in new_review}),
      "high_by_manufacturer":dict(Counter(r["inferred_manufacturer"] for r in new_high)),
      "review_by_manufacturer":dict(Counter(r["inferred_manufacturer"] for r in new_review)),
      "multi_manufacturer_new_facilities":conflicts,
      "policy":{
        "Arthrex ACP-PRP":"HIGH because manufacturer-operated locator explicitly labels ACP-PRP",
        "Arthrex HD-PRP":"REVIEW",
        "Zimmer APS":"HIGH because Zimmer-operated locator explicitly labels APS therapy",
        "Zimmer PRP only":"REVIEW"
      }
    }
    (OUT/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=False))

if __name__=="__main__":
    main()
