#!/usr/bin/env python3
import argparse, hashlib, json, re, unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE="https://saiseiiryo.mhlw.go.jp"
UA="Mozilla/5.0 (compatible; RegenMedV41Queue/1.0; +https://github.com/ri0115/saisei-collector)"

REGION={
    "北海道":"北海道","青森県":"東北","岩手県":"東北","宮城県":"東北","秋田県":"東北","山形県":"東北","福島県":"東北",
    "茨城県":"関東","栃木県":"関東","群馬県":"関東","埼玉県":"関東","千葉県":"関東","東京都":"関東","神奈川県":"関東",
    "新潟県":"中部","富山県":"中部","石川県":"中部","福井県":"中部","山梨県":"中部","長野県":"中部","岐阜県":"中部","静岡県":"中部","愛知県":"中部",
    "三重県":"近畿","滋賀県":"近畿","京都府":"近畿","大阪府":"近畿","兵庫県":"近畿","奈良県":"近畿","和歌山県":"近畿",
    "鳥取県":"中国四国","島根県":"中国四国","岡山県":"中国四国","広島県":"中国四国","山口県":"中国四国","徳島県":"中国四国","香川県":"中国四国","愛媛県":"中国四国","高知県":"中国四国",
    "福岡県":"九州沖縄","佐賀県":"九州沖縄","長崎県":"九州沖縄","熊本県":"九州沖縄","大分県":"九州沖縄","宮崎県":"九州沖縄","鹿児島県":"九州沖縄","沖縄県":"九州沖縄"
}

def now():
    return datetime.now(timezone.utc).isoformat()

def norm(s):
    s=unicodedata.normalize("NFKC",s or "").lower()
    return re.sub(r"[\s　・･\-‐–—―_()（）\[\]【】「」『』,.，。、:：/／]","",s)

def infer_class(t):
    s=unicodedata.normalize("NFKC",t or "")
    labels=[]
    if re.search(r"PRGF|Endoret",s,re.I): labels.append("PRGF")
    if re.search(r"APS|多血小板血漿抽出液",s,re.I): labels.append("APS")
    if re.search(r"PRP|多血小板血漿",s,re.I): labels.append("PRP")
    if re.search(r"BMAC|骨髄.*濃縮",s,re.I): labels.append("BMAC")
    if "滑膜" in s and re.search(r"幹細胞|間葉系",s): labels.append("滑膜幹細胞")
    if re.search(r"脂肪|adipose|ASC",s,re.I) and re.search(r"幹細胞|間葉系|ASC",s,re.I): labels.append("脂肪由来幹細胞")
    if re.search(r"SVF|間質血管",s,re.I): labels.append("SVF")
    if re.search(r"MFAT|微小細断脂肪",s,re.I): labels.append("MFAT")
    return "/".join(dict.fromkeys(labels)) or "その他MSK"

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
        if len(td)<8: continue
        facility=td[0].get_text(" ",strip=True)
        pref=td[1].get_text(" ",strip=True)
        address=td[2].get_text(" ",strip=True)
        treatment=td[4].get_text(" ",strip=True)
        links=[urljoin(url,a.get("href","")) for a in td[7].find_all("a",href=True)]
        codes=[]
        for u in links:
            m=re.search(r"/published_plan/download/([^/]+)/",u)
            if m: codes.append(m.group(1))
        if facility and treatment and codes:
            out.append({"kind":str(kind),"facility":facility,"prefecture":pref,"address":address,"treatment":treatment,"links":links,"codes":codes})
    return out

def facility_id(pref,facility,address):
    key="|".join([norm(pref),norm(facility),norm(address)])
    return "V41F"+hashlib.sha1(key.encode("utf-8")).hexdigest()[:10].upper()

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--codes",default="data/v41_addition_codes.txt")
    ap.add_argument("--output",default="data/queue_v41_additions.json")
    args=ap.parse_args()

    codes=[x.strip() for x in Path(args.codes).read_text(encoding="utf-8").splitlines() if x.strip()]
    if len(codes)!=774 or len(set(codes))!=774:
        raise RuntimeError(f"expected 774 unique codes, got {len(codes)}/{len(set(codes))}")

    rows=parse_index(2)+parse_index(3)
    by_code={}
    for r in rows:
        for code in r["codes"]:
            by_code.setdefault(code,r)

    missing=[c for c in codes if c not in by_code]
    if missing:
        raise RuntimeError("MHLW current-list codes missing: "+",".join(missing[:30]))

    plans=[]
    for code in codes:
        r=by_code[code]
        doc=next((u for u in r["links"] if f"/download/{code}/5/0" in u),None)
        if not doc:
            doc=next((u for u in r["links"] if f"/download/{code}/" in u),r["links"][0] if r["links"] else "")
        plans.append({
            "plan_id":f"V41_{code}",
            "mhlw_plan_code":code,
            "facility_id":facility_id(r["prefecture"],r["facility"],r["address"]),
            "region":REGION.get(r["prefecture"],""),
            "prefecture":r["prefecture"],
            "facility":r["facility"],
            "address":r["address"],
            "category":"第二種" if r["kind"]=="2" else "第三種",
            "treatment":r["treatment"],
            "treatment_class":infer_class(r["treatment"]),
            "document_url_source":doc,
            "evidence_url":doc,
            "plan_status":"厚労省現行一覧確認済 / v41高確度追加 / 価格回収待ち"
        })

    if len(plans)!=774 or len({p["plan_id"] for p in plans})!=774:
        raise RuntimeError("queue integrity failure")

    obj={
        "version":"v41-additions",
        "generated_at":now(),
        "priority":"V41",
        "total":774,
        "source":"MHLW current second/third class lists + audited 774-code allowlist",
        "plans":plans
    }
    Path(args.output).parent.mkdir(parents=True,exist_ok=True)
    Path(args.output).write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"total":len(plans),"facilities":len({p["facility_id"] for p in plans})},ensure_ascii=False))

if __name__=="__main__":
    main()
