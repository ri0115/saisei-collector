#!/usr/bin/env python3
import csv, json, re, unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from difflib import SequenceMatcher

AUDIT=Path("results/mhlw_final_audit.json")
OUT=Path("results/mhlw_final_audit_refined.json")
CSV=Path("results/mhlw_high_confidence_missing.csv")
QUEUE=[Path("data/queue_a_v40.json"),Path("data/queue_b_v40.json"),Path("data/queue_c_v40.json")]

def norm(s):
    s=unicodedata.normalize("NFKC",s or "").lower()
    return re.sub(r"[\s　・･\-‐–—―_()（）\[\]【】「」『』,.，。、:：/／®™]", "", s)

def norm_addr(s):
    s=unicodedata.normalize("NFKC",s or "").lower()
    s=s.replace("丁目","-").replace("番地","-").replace("番","-").replace("号","")
    return re.sub(r"[^0-9a-z一-龥ぁ-んァ-ヶ]+","",s)

def tclass(t):
    s=unicodedata.normalize("NFKC",t or "").upper()
    labels=[]
    if "PRGF" in s: labels.append("PRGF")
    if "APS" in s or "多血小板血漿抽出液" in s: labels.append("APS")
    if "PRP" in s or "多血小板血漿" in s: labels.append("PRP")
    if "BMAC" in s or "骨髄濃縮" in s: labels.append("BMAC")
    if "滑膜" in s and ("幹細胞" in s or "間葉系" in s): labels.append("滑膜幹細胞")
    if ("脂肪" in s or "ADIPOSE" in s) and ("幹細胞" in s or "間葉系" in s or "ASC" in s): labels.append("脂肪由来幹細胞")
    if "SVF" in s or "間質血管細胞" in s: labels.append("SVF")
    if "MFAT" in s or "微小細断脂肪" in s: labels.append("MFAT")
    if "GMSC" in s or "三次元人工組織" in s: labels.append("人工組織")
    return "/".join(dict.fromkeys(labels)) or "その他MSK"

def load_v40():
    out=[]
    for p in QUEUE:
        d=json.loads(p.read_text(encoding="utf-8"))
        out.extend(d.get("plans",[]))
    return out

audit=json.loads(AUDIT.read_text(encoding="utf-8"))
raw=audit.get("possible_missing",[])
v40=load_v40()

fac_norm={norm(x.get("facility")) for x in v40}
addr_norm={norm_addr(x.get("address","")) for x in v40 if x.get("address")}
fac_addr={(norm(x.get("facility")),norm_addr(x.get("address",""))) for x in v40 if x.get("address")}
v40_by_addr=defaultdict(list)
for x in v40:
    a=norm_addr(x.get("address",""))
    if a: v40_by_addr[a].append(x)

# collapse obvious duplicate current rows caused by formatting/name variants at same address
groups=defaultdict(list)
for x in raw:
    key=(x.get("kind",""),norm_addr(x.get("address","")),norm(x.get("treatment","")))
    groups[key].append(x)

collapsed=[]
for key,rows in groups.items():
    rows=sorted(rows,key=lambda x:(len(norm(x.get("facility",""))),x.get("facility","")))
    base=dict(rows[0])
    base["duplicate_current_rows"]=len(rows)-1
    base["duplicate_facility_names"]=sorted({x.get("facility","") for x in rows})
    collapsed.append(base)

for x in collapsed:
    nf=norm(x.get("facility","")); na=norm_addr(x.get("address",""))
    x["treatment_class"]=tclass(x.get("treatment",""))
    x["existing_facility_name"]=nf in fac_norm
    x["existing_address"]=bool(na and na in addr_norm)
    x["candidate_type"]="new_facility"
    if x["existing_facility_name"]:
        x["candidate_type"]="existing_facility_new_plan"
    elif x["existing_address"]:
        x["candidate_type"]="possible_rename_or_same_site"
        best=None
        for b in v40_by_addr.get(na,[]):
            sc=SequenceMatcher(None,nf,norm(b.get("facility",""))).ratio()
            if best is None or sc>best[0]: best=(sc,b)
        if best:
            x["same_address_plan_id"]=best[1].get("plan_id","")
            x["same_address_facility"]=best[1].get("facility","")
            x["facility_name_similarity"]=round(best[0],4)

by_pref=Counter(x.get("prefecture","") for x in collapsed)
by_kind=Counter(x.get("kind","") for x in collapsed)
by_type=Counter(x.get("candidate_type","") for x in collapsed)
by_tclass=Counter(x.get("treatment_class","") for x in collapsed)

# high confidence = explicit strong MSK, plus broad_msk already constrained by orthopedic facility name
high=[x for x in collapsed if x.get("scope") in ("strong_msk","broad_msk")]

summary={
    "raw_possible_missing":len(raw),
    "after_exact_address_treatment_collapse":len(collapsed),
    "high_confidence":len(high),
    "unique_facilities":len({(norm(x.get("facility","")),norm_addr(x.get("address",""))) for x in high}),
    "by_kind":dict(by_kind),
    "by_candidate_type":dict(by_type),
    "by_treatment_class":dict(by_tclass),
    "by_prefecture":dict(sorted(by_pref.items(),key=lambda kv:(-kv[1],kv[0]))),
    "duplicate_rows_collapsed":sum(x.get("duplicate_current_rows",0) for x in collapsed),
}
OUT.write_text(json.dumps({"summary":summary,"candidates":high},ensure_ascii=False,indent=2),encoding="utf-8")

cols=["candidate_type","kind","prefecture","facility","address","treatment","treatment_class","existing_facility_name","existing_address","same_address_plan_id","same_address_facility","facility_name_similarity","duplicate_current_rows","document_urls"]
with CSV.open("w",encoding="utf-8-sig",newline="") as f:
    w=csv.DictWriter(f,fieldnames=cols);w.writeheader()
    for x in sorted(high,key=lambda z:(z.get("prefecture",""),z.get("facility",""),z.get("treatment",""))):
        row={k:x.get(k,"") for k in cols}
        row["document_urls"]="|".join(y.get("url","") for y in x.get("links",[]))
        w.writerow(row)
print(json.dumps(summary,ensure_ascii=False))
