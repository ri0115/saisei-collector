#!/usr/bin/env python3
import csv, json, re, unicodedata, gzip
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE="https://saiseiiryo.mhlw.go.jp"
UA="Mozilla/5.0 (compatible; RegenMedFinalAudit/1.0; +https://github.com/ri0115/saisei-collector)"

QUEUE_FILES=[
    ("A",Path("data/queue_a_v40.json")),
    ("B",Path("data/queue_b_v40.json")),
    ("C",Path("data/queue_c_v40.json")),
]
OUT_JSON=Path("results/mhlw_final_audit.json")
OUT_CSV=Path("results/mhlw_final_audit_candidates.csv")

STRONG=re.compile(r"整形|運動器|筋骨格|関節|変形性|膝|股関節|肩|腱板|腱|靭帯|靱帯|筋肉|筋膜|軟骨|半月板|骨軟骨|骨折|腰痛|腰椎|頚椎|頸椎|脊椎|脊柱|椎間|足関節|スポーツ|外傷",re.I)
BROAD=re.compile(r"慢性疼痛|疼痛|PRP|多血小板|APS|BMAC|間葉系幹細胞|脂肪由来幹細胞|滑膜幹細胞",re.I)
NON_MSK=re.compile(r"子宮|卵巣|不妊|歯|歯科|口腔|顎|皮膚|美容|毛髪|脱毛|乳房|顔面|しわ|瘢痕|勃起|ED|肝|腎|心筋|肺|角膜|眼|網膜|脳梗塞|脳卒中|認知症|神経変性|糖尿病|動脈硬化|下肢虚血|潰瘍",re.I)

def now():
    return datetime.now(timezone.utc).isoformat()

def norm(s):
    s=unicodedata.normalize("NFKC",s or "").lower()
    return re.sub(r"[\s　・･\-‐–—―_()（）\[\]【】「」『』,.，。、:：/／®™]", "", s)

def get(url):
    r=requests.get(url,headers={"User-Agent":UA},timeout=60)
    r.raise_for_status()
    return r

def parse_index(kind):
    url=f"{BASE}/published_plan/index/{kind}"
    soup=BeautifulSoup(get(url).text,"html.parser")
    out=[]
    for tr in soup.select("tr"):
        td=tr.find_all("td")
        if len(td)<8:
            continue
        facility=td[0].get_text(" ",strip=True)
        pref=td[1].get_text(" ",strip=True)
        address=td[2].get_text(" ",strip=True)
        treatment=td[4].get_text(" ",strip=True)
        if not facility or not treatment:
            continue
        links=[{"label":a.get_text(" ",strip=True),"url":urljoin(url,a.get("href",""))} for a in td[7].find_all("a",href=True)]
        out.append({
            "kind":str(kind),"facility":facility,"prefecture":pref,
            "address":address,"treatment":treatment,"links":links
        })
    return out

def load_v40():
    out=[]
    for lane,path in QUEUE_FILES:
        d=json.loads(path.read_text(encoding="utf-8"))
        for x in d.get("plans",[]):
            y=dict(x); y["lane"]=lane
            y["kind"]="2" if "第二" in y.get("category","") else "3"
            out.append(y)
    return out

def similarity(a,b):
    fs=SequenceMatcher(None,norm(a.get("facility")),norm(b.get("facility"))).ratio()
    ts=SequenceMatcher(None,norm(a.get("treatment")),norm(b.get("treatment"))).ratio()
    score=.44*fs+.56*ts
    if norm(a.get("facility"))==norm(b.get("facility")):
        score+=.12
    elif norm(a.get("facility")) in norm(b.get("facility")) or norm(b.get("facility")) in norm(a.get("facility")):
        score+=.05
    if norm(a.get("treatment"))==norm(b.get("treatment")):
        score+=.10
    return min(score,1.0),fs,ts

def scope_class(row):
    t=row.get("treatment","")
    if STRONG.search(t):
        if NON_MSK.search(t) and not re.search(r"関節|膝|股関節|肩|腱|靭帯|靱帯|筋肉|筋膜|軟骨|腰|脊椎|椎間|スポーツ|整形",t):
            return "excluded_non_msk"
        return "strong_msk"
    if BROAD.search(t) and not NON_MSK.search(t):
        f=row.get("facility","")
        if re.search(r"整形|ひざ|膝|スポーツ|関節|リハビリ",f):
            return "broad_msk"
        return "broad_review"
    return "other"

v40=load_v40()
current=parse_index(2)+parse_index(3)
with gzip.open("results/mhlw_current_rows.json.gz","wt",encoding="utf-8") as gz:
    json.dump(current,gz,ensure_ascii=False,separators=(",",":"))

current_by_kind_pref={}
for x in current:
    current_by_kind_pref.setdefault((x["kind"],norm(x["prefecture"])),[]).append(x)

v40_by_kind_pref={}
for x in v40:
    v40_by_kind_pref.setdefault((x["kind"],norm(x.get("prefecture",""))),[]).append(x)

exact_current_keys={(x["kind"],norm(x["prefecture"]),norm(x["facility"]),norm(x["treatment"])) for x in current}
exact_v40_keys={(x["kind"],norm(x.get("prefecture","")),norm(x["facility"]),norm(x["treatment"])) for x in v40}

v40_not_found=[]
v40_fuzzy=[]
v40_exact=0
for b in v40:
    key=(b["kind"],norm(b.get("prefecture","")),norm(b["facility"]),norm(b["treatment"]))
    if key in exact_current_keys:
        v40_exact+=1
        continue
    pool=current_by_kind_pref.get((b["kind"],norm(b.get("prefecture",""))),[])
    best=None
    for c in pool:
        score,fs,ts=similarity(b,c)
        if best is None or score>best[0]:
            best=(score,fs,ts,c)
    rec={
        "plan_id":b.get("plan_id"),"lane":b.get("lane"),"kind":b["kind"],
        "prefecture":b.get("prefecture"),"facility":b.get("facility"),
        "treatment":b.get("treatment")
    }
    if best and best[0]>=0.84:
        rec.update({"score":round(best[0],4),"facility_score":round(best[1],4),"treatment_score":round(best[2],4),
                    "current_facility":best[3]["facility"],"current_treatment":best[3]["treatment"],"current_links":best[3]["links"]})
        v40_fuzzy.append(rec)
    else:
        if best:
            rec.update({"best_score":round(best[0],4),"current_facility":best[3]["facility"],"current_treatment":best[3]["treatment"],"current_links":best[3]["links"]})
        v40_not_found.append(rec)

candidates=[]
alias_candidates=[]
current_scope={"strong_msk":0,"broad_msk":0,"broad_review":0,"excluded_non_msk":0,"other":0}
for c in current:
    sc=scope_class(c); current_scope[sc]=current_scope.get(sc,0)+1
    if sc not in ("strong_msk","broad_msk"):
        continue
    key=(c["kind"],norm(c["prefecture"]),norm(c["facility"]),norm(c["treatment"]))
    if key in exact_v40_keys:
        continue
    pool=v40_by_kind_pref.get((c["kind"],norm(c["prefecture"])),[])
    best=None
    for b in pool:
        score,fs,ts=similarity(c,b)
        if best is None or score>best[0]:
            best=(score,fs,ts,b)
    rec={
        "scope":sc,"kind":c["kind"],"prefecture":c["prefecture"],"facility":c["facility"],
        "address":c["address"],"treatment":c["treatment"],"links":c["links"]
    }
    if best and best[0]>=0.84:
        rec.update({"match_type":"alias_or_wording_candidate","score":round(best[0],4),
                    "facility_score":round(best[1],4),"treatment_score":round(best[2],4),
                    "matched_plan_id":best[3].get("plan_id"),"matched_facility":best[3].get("facility"),
                    "matched_treatment":best[3].get("treatment")})
        alias_candidates.append(rec)
    else:
        rec.update({"match_type":"possible_missing"})
        if best:
            rec.update({"best_score":round(best[0],4),"nearest_plan_id":best[3].get("plan_id"),
                        "nearest_facility":best[3].get("facility"),"nearest_treatment":best[3].get("treatment")})
        candidates.append(rec)

audit={
    "version":"1.0","generated_at":now(),
    "mhlw_current_counts":{"second":sum(1 for x in current if x["kind"]=="2"),"third":sum(1 for x in current if x["kind"]=="3"),"total":len(current)},
    "v40":{"total":len(v40),"unique_plan_ids":len({x.get("plan_id") for x in v40})},
    "current_scope_counts":current_scope,
    "v40_to_current":{"exact":v40_exact,"fuzzy_or_alias":len(v40_fuzzy),"not_found":len(v40_not_found)},
    "current_to_v40":{"possible_missing":len(candidates),"alias_or_wording_candidates":len(alias_candidates)},
    "v40_fuzzy_or_alias":v40_fuzzy,
    "v40_not_found":v40_not_found,
    "possible_missing":candidates,
    "alias_or_wording_candidates":alias_candidates,
}
OUT_JSON.write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding="utf-8")

cols=["match_type","scope","kind","prefecture","facility","address","treatment","best_score","nearest_plan_id","nearest_facility","nearest_treatment","score","matched_plan_id","matched_facility","matched_treatment","document_urls"]
with OUT_CSV.open("w",encoding="utf-8-sig",newline="") as f:
    w=csv.DictWriter(f,fieldnames=cols);w.writeheader()
    for x in candidates+alias_candidates:
        row={k:x.get(k,"") for k in cols}
        row["document_urls"]="|".join(z.get("url","") for z in x.get("links",[]))
        w.writerow(row)

print(json.dumps({
    "mhlw_current_counts":audit["mhlw_current_counts"],
    "v40":audit["v40"],
    "v40_to_current":audit["v40_to_current"],
    "current_to_v40":audit["current_to_v40"],
    "scope":audit["current_scope_counts"]
},ensure_ascii=False))
