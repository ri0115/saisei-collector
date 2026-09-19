#!/usr/bin/env python3
import json,re,unicodedata,time
from concurrent.futures import ThreadPoolExecutor,as_completed
from pathlib import Path
import requests,pymupdf as fitz

UA="Mozilla/5.0 (compatible; RegenMedFastTextRescue/1.0; +https://github.com/ri0115/saisei-collector)"
AMOUNT=re.compile(r"(?<!\d)(\d[\d,\s]{2,14}(?:\.\d+)?)\s*(万円|円)")
RANGE=re.compile(r"(?<!\d)(\d[\d,\s]{1,12})\s*[~〜～]\s*(\d[\d,\s]{1,12})\s*円")
FEE=re.compile(r"治療費|施術料|施術料金|料金|費用|価格|自己負担|自費|自由診療",re.I)
ANC=re.compile(r"初診|再診|診察料|検査料|血液検査|採血料|保管|キャンセル|返金|文書料|送料|手数料",re.I)

def norm(s): return unicodedata.normalize("NFKC",s or "")
def num(s):
    s=re.sub(r"\s+","",s).replace(",","")
    try:return int(round(float(s)))
    except:return None

def kind_regex(item):
    t=norm((item.get("treatment_class") or "")+" "+(item.get("treatment") or ""))
    if re.search(r"APS",t,re.I): return "APS",re.compile(r"APS|多血小板血漿抽出液",re.I),15000
    if re.search(r"脂肪由来|ASC|ADRC",t,re.I) and re.search(r"幹細胞|MSC|間葉系|ASC|ADRC",t,re.I):
        return "adipose_stem",re.compile(r"脂肪由来|ASC|ADRC|幹細胞|間葉系|MSC|cell",re.I),100000
    if re.search(r"滑膜",t,re.I): return "synovial_stem",re.compile(r"滑膜|幹細胞|間葉系|MSC|cell",re.I),100000
    if re.search(r"幹細胞|MSC|間葉系",t,re.I): return "stem",re.compile(r"幹細胞|MSC|間葉系|cell",re.I),100000
    return "PRP",re.compile(r"PRP|多血小板血漿|血小板|ACP|GPS|APS|Condensia|コンデンシア|Angel|PRGF",re.I),15000

def product(s,kind):
    pats=[("ACP MAX",r"ACP\s*MAX"),("ACP",r"\bACP\b"),("APS",r"\bAPS\b"),("GPS",r"GPS\s*(?:III|Ⅲ|3)?"),("Condensia",r"Condensia|コンデンシア"),("Angel",r"Angel"),("PRGF-Endoret",r"PRGF|Endoret")]
    xs=[n for n,p in pats if re.search(p,s,re.I)]
    if kind=="adipose_stem" and not xs: xs=["脂肪由来幹細胞"]
    return xs

def fetch(url):
    last=None
    for i in range(3):
        try:
            r=requests.get(url,headers={"User-Agent":UA},timeout=25)
            r.raise_for_status()
            if r.content[:4]!=b"%PDF": raise RuntimeError("not pdf")
            return r.content
        except Exception as e:
            last=e;time.sleep(1.5*(i+1))
    raise last

def scan(item):
    out={k:item.get(k) for k in ["plan_id","facility","treatment","treatment_class","document_url"]}
    out.update({"accepted":[],"candidates":[],"status":"NO_MATCH","error":""})
    try:
        data=fetch(item["document_url"])
        doc=fitz.open(stream=data,filetype="pdf")
        kind,target,minamt=kind_regex(item)
        cand=[]
        for pi,p in enumerate(doc,1):
            text=norm(p.get_text("text") or "")
            text=re.sub(r"(?<=\d)\s+(?=[\d,])","",text)
            lines=[re.sub(r"[ \t　]+"," ",x).strip() for x in text.splitlines() if x.strip()]
            for i,line in enumerate(lines):
                near=" ".join(lines[max(0,i-2):min(len(lines),i+3)])
                if not target.search(near):continue
                same=bool(target.search(line))
                for m in RANGE.finditer(line):
                    lo=num(m.group(1));hi=num(m.group(2))
                    if lo is None or hi is None:continue
                    if lo>hi:lo,hi=hi,lo
                    if not(minamt<=lo<=hi<=20000000):continue
                    if ANC.search(line) and not same:continue
                    sc=(10 if same else 6)+(4 if FEE.search(line) else 2 if FEE.search(near) else 0)
                    cand.append({"price_type":"range","amount_min":lo,"amount_max":hi,"amount":None,"score":sc,"page":pi,"line":line,"excerpt":near,"products":product(near,kind)})
                for m in AMOUNT.finditer(line):
                    a=num(m.group(1))
                    if a is None:continue
                    if m.group(2)=="万円":a*=10000
                    if not(minamt<=a<=20000000):continue
                    if any(rm.start()<=m.start()<=rm.end() for rm in RANGE.finditer(line)):continue
                    if ANC.search(line) and not same:continue
                    sc=(10 if same else 6)+(4 if FEE.search(line) else 2 if FEE.search(near) else 0)
                    if re.search(r"税込|税別|税抜",near):sc+=1
                    cand.append({"price_type":"point","amount":a,"score":sc,"page":pi,"line":line,"excerpt":near,"products":product(near,kind)})
        best={}
        for x in cand:
            k=(x.get("price_type"),x.get("amount"),x.get("amount_min"),x.get("amount_max"),tuple(x.get("products") or []))
            if k not in best or x["score"]>best[k]["score"]:best[k]=x
        xs=sorted(best.values(),key=lambda x:(-x["score"],x.get("amount") or x.get("amount_min") or 0))
        out["candidates"]=xs[:20]
        high=[x for x in xs if x["score"]>=12]
        if 1<=len(high)<=10:
            out["accepted"]=high;out["status"]="AUTO_TEXT"
        elif xs:out["status"]="QC_TEXT"
    except Exception as e:
        out["status"]="ERROR";out["error"]=f"{type(e).__name__}: {e}"[:300]
    return out

def main():
    q=json.loads(Path("results/v41_official_site_fallback_queue.json").read_text(encoding="utf-8"))
    items=q.get("items",[])
    results=[]
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs={ex.submit(scan,x):x["plan_id"] for x in items}
        for n,f in enumerate(as_completed(futs),1):
            y=f.result();results.append(y)
            print(n,y["plan_id"],y["status"],len(y["accepted"]),flush=True)
    order={x["plan_id"]:i for i,x in enumerate(items)}
    results.sort(key=lambda x:order.get(x["plan_id"],999999))
    o={"version":"v41-fast-text-rescue-1.0","total":len(results),
       "auto":sum(x["status"]=="AUTO_TEXT" for x in results),
       "qc":sum(x["status"]=="QC_TEXT" for x in results),
       "no_match":sum(x["status"]=="NO_MATCH" for x in results),
       "error":sum(x["status"]=="ERROR" for x in results),"items":results}
    Path("results/v41_fast_text_rescue.json").write_text(json.dumps(o,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({k:o[k] for k in ["total","auto","qc","no_match","error"]},ensure_ascii=False))

if __name__=="__main__":main()
