#!/usr/bin/env python3
import argparse,json,re,unicodedata,time
from pathlib import Path
from io import BytesIO

import requests
import pymupdf as fitz
import pytesseract
from PIL import Image

UA="Mozilla/5.0 (compatible; RegenMedDeepRescue/1.0; +https://github.com/ri0115/saisei-collector)"
PRICE_RE=re.compile(r"(?<!\d)(\d[\d,\s]{2,14}(?:\.\d+)?)\s*(万円|円)")
RANGE_RE=re.compile(r"(?<!\d)(\d[\d,\s]{1,12})\s*[~〜～\-–—]\s*(\d[\d,\s]{1,12})\s*円")
FEE_RE=re.compile(r"治療費|治療料金|施術料|施術料金|料金|費用|価格|自己負担|自由診療|自費",re.I)
ANC_RE=re.compile(r"初診|再診|診察料|検査料|血液検査|感染症検査|採血料|保管料|保存料|キャンセル|返金|文書料|証明書|送料|手数料|材料費のみ",re.I)
TAX_IN=re.compile(r"税込|消費税込",re.I)
TAX_OUT=re.compile(r"税別|税抜|消費税別",re.I)

def norm(s):
    return unicodedata.normalize("NFKC",s or "")

def clean_num(s):
    s=re.sub(r"\s+","",s).replace(",","")
    try: return int(round(float(s)))
    except: return None

def parse_amount(m):
    n=clean_num(m.group(1))
    if n is None:return None
    return n*10000 if m.group(2)=="万円" else n

def target_info(item):
    t=norm((item.get("treatment_class") or "")+" "+(item.get("treatment") or ""))
    if re.search(r"脂肪由来|ASC|ADRC",t,re.I) and re.search(r"幹細胞|MSC|間葉系|ASC|ADRC",t,re.I):
        return "adipose_stem",re.compile(r"脂肪由来|ASC|ADRC|幹細胞|間葉系|MSC|cell",re.I),100000
    if re.search(r"滑膜",t,re.I):
        return "synovial_stem",re.compile(r"滑膜|幹細胞|間葉系|MSC|cell",re.I),100000
    if re.search(r"幹細胞|MSC|間葉系",t,re.I):
        return "stem",re.compile(r"幹細胞|MSC|間葉系|cell",re.I),100000
    if re.search(r"APS",t,re.I):
        return "APS",re.compile(r"APS|多血小板血漿抽出液|PRP|血小板",re.I),15000
    return "PRP",re.compile(r"PRP|多血小板血漿|血小板|ACP|GPS|APS|Condensia|コンデンシア|Angel|PRGF|Endoret",re.I),15000

def detect_product(text,kind):
    s=norm(text)
    products=[]
    pairs=[
      ("ACP MAX",r"ACP\s*MAX"),("ACP",r"\bACP\b"),("APS",r"\bAPS\b"),
      ("GPS",r"GPS\s*(?:III|Ⅲ|3)?"),("Condensia",r"Condensia|コンデンシア"),
      ("Angel",r"Angel"),("PRGF-Endoret",r"PRGF|Endoret")
    ]
    for name,pat in pairs:
        if re.search(pat,s,re.I) and name not in products: products.append(name)
    if kind in ("adipose_stem","stem","synovial_stem") and not products:
        products.append("脂肪由来幹細胞" if kind=="adipose_stem" else "幹細胞")
    return products

def infer_tax(text):
    if TAX_IN.search(text): return "税込"
    if TAX_OUT.search(text): return "税別"
    return "不明"

def infer_unit(text):
    pats=[
      r"(片膝|両膝)",r"([12]\s*部位)",r"([12]\s*関節)",r"([12]\s*回)",
      r"([0-9,]+\s*万\s*(?:cells?|個))",r"([0-9]+\s*億\s*(?:cells?|個))",
    ]
    vals=[]
    for pat in pats:
        m=re.search(pat,text,re.I)
        if m:
            v=re.sub(r"\s+","",m.group(1))
            if v not in vals: vals.append(v)
    return "・".join(vals[:3])

def normalized_lines(text):
    text=norm(text)
    text=re.sub(r"(?<=\d)\s+(?=[\d,])","",text)
    lines=[]
    for x in text.splitlines():
        x=re.sub(r"[ \t　]+"," ",x).strip()
        if x:lines.append(x)
    return lines

def candidates_from_text(text,item,mode,page_no):
    kind,target,min_amt=target_info(item)
    lines=normalized_lines(text)
    out=[]
    seen=set()
    for i,line in enumerate(lines):
        near=" ".join(lines[max(0,i-2):min(len(lines),i+3)])
        same_target=bool(target.search(line))
        near_target=bool(target.search(near))
        if not near_target: continue

        # explicit ranges
        for rm in RANGE_RE.finditer(line):
            lo=clean_num(rm.group(1));hi=clean_num(rm.group(2))
            if lo is None or hi is None: continue
            if lo>hi:lo,hi=hi,lo
            if not(min_amt<=lo<=hi<=20000000):continue
            if ANC_RE.search(line) and not same_target:continue
            score=(10 if same_target else 6)+(4 if FEE_RE.search(line) else 2 if FEE_RE.search(near) else 0)
            if mode.startswith("ocr"): score-=1
            product=detect_product(near,kind)
            key=("range",lo,hi,tuple(product),line)
            if key in seen:continue
            seen.add(key)
            out.append({
              "amount":None,"amount_min":lo,"amount_max":hi,"price_type":"range",
              "tax":infer_tax(near),"unit":infer_unit(near),"product":product[0] if len(product)==1 else None,
              "products":product,"score":score,"page":page_no,"source_mode":mode,
              "line":line,"excerpt":near
            })

        for m in PRICE_RE.finditer(line):
            amount=parse_amount(m)
            if amount is None or not(min_amt<=amount<=20000000):continue
            if amount==0:continue
            # Ignore a scalar if it is part of a displayed range; range row above is safer.
            if any(rm.start()<=m.start()<=rm.end() for rm in RANGE_RE.finditer(line)):
                continue
            if ANC_RE.search(line) and not same_target:
                continue
            score=0
            score+=10 if same_target else 6
            if FEE_RE.search(line):score+=4
            elif FEE_RE.search(near):score+=2
            if re.search(r"税込|税別|税抜",line):score+=2
            elif re.search(r"税込|税別|税抜",near):score+=1
            if re.search(r"1\s*回|1\s*部位|片膝|両膝|万\s*(?:個|cell)|億\s*(?:個|cell)",near,re.I):score+=1
            if ANC_RE.search(near) and not same_target:score-=4
            if mode.startswith("ocr"):score-=1
            product=detect_product(line+" "+near,kind)
            key=("point",amount,tuple(product),line)
            if key in seen:continue
            seen.add(key)
            out.append({
              "amount":amount,"price_type":"point","tax":infer_tax(line+" "+near),
              "unit":infer_unit(line+" "+near),"product":product[0] if len(product)==1 else None,
              "products":product,"score":score,"page":page_no,"source_mode":mode,
              "line":line,"excerpt":near
            })
    return out

def ocr_page(page,psm):
    pix=page.get_pixmap(matrix=fitz.Matrix(2.0,2.0),alpha=False)
    img=Image.open(BytesIO(pix.tobytes("png")))
    return pytesseract.image_to_string(img,lang="jpn+eng",config=f"--psm {psm}",timeout=45)

def fetch_pdf(url):
    last=None
    for n in range(3):
        try:
            headers={
                "User-Agent":UA,
                "Accept":"application/pdf,text/html;q=0.9,*/*;q=0.8",
                "Referer":"https://saiseiiryo.mhlw.go.jp/"
            }
            r=requests.get(url,headers=headers,timeout=35,allow_redirects=True)
            if r.status_code in (403,429,500,502,503,504):
                raise RuntimeError(f"HTTP {r.status_code}")
            r.raise_for_status()
            if r.content[:4]!=b"%PDF":
                raise RuntimeError(f"not a PDF content-type={r.headers.get('content-type','')} final={r.url}")
            return r.content
        except Exception as e:
            last=e
            time.sleep(3*(n+1))
    raise last

def process(item):
    rec={k:item.get(k) for k in ["plan_id","mhlw_plan_code","facility_id","prefecture","facility","treatment","treatment_class","document_url"]}
    rec.update({"candidates":[],"accepted":[],"status":"NO_MATCH","error":""})
    try:
        data=fetch_pdf(item["document_url"])
        doc=fitz.open(stream=data,filetype="pdf")
        allc=[]
        text_pages=[]
        for i,p in enumerate(doc):
            txt=p.get_text("text") or ""
            text_pages.append(txt)
            allc.extend(candidates_from_text(txt,item,"pdf_text",i+1))

        # OCR only if text pass did not yield a high-confidence treatment price.
        high=[x for x in allc if x["score"]>=12]
        if not high:
            for i,p in enumerate(doc):
                for psm in (6,11):
                    try:
                        txt=ocr_page(p,psm)
                    except Exception:
                        continue
                    allc.extend(candidates_from_text(txt,item,f"ocr_psm{psm}",i+1))
                    if any(x["score"]>=12 for x in allc):
                        # Continue current page's alternate PSM only; avoid needless OCR pages.
                        break

        # Deduplicate, keeping highest score for same structured price.
        best={}
        for x in allc:
            key=(x.get("price_type"),x.get("amount"),x.get("amount_min"),x.get("amount_max"),x.get("product"),x.get("unit"))
            if key not in best or x["score"]>best[key]["score"]:best[key]=x
        cand=sorted(best.values(),key=lambda x:(-x["score"],x.get("amount") or x.get("amount_min") or 0))
        rec["candidates"]=cand[:30]

        accepted=[x for x in cand if x["score"]>=12]
        # Keep automated promotion conservative.
        if 1<=len(accepted)<=12:
            rec["accepted"]=accepted
            rec["status"]="AUTO_DEEP"
        elif cand:
            rec["status"]="QC_DEEP"
    except Exception as e:
        rec["status"]="ERROR"
        rec["error"]=f"{type(e).__name__}: {e}"[:400]
    return rec

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--queue",default="results/v41_official_site_fallback_queue.json")
    ap.add_argument("--shard",type=int,default=0)
    ap.add_argument("--shards",type=int,default=4)
    ap.add_argument("--output",required=True)
    args=ap.parse_args()
    q=json.loads(Path(args.queue).read_text(encoding="utf-8"))
    items=[x for i,x in enumerate(q.get("items",[])) if i%args.shards==args.shard]
    results=[]
    for i,x in enumerate(items,1):
        y=process(x);results.append(y)
        print(f"{i}/{len(items)} {y['plan_id']} {y['status']} accepted={len(y['accepted'])} cand={len(y['candidates'])}",flush=True)
    out={
      "version":"v41-deep-mhlw-rescue-1.0","shard":args.shard,"shards":args.shards,
      "total":len(results),"auto":sum(x["status"]=="AUTO_DEEP" for x in results),
      "qc":sum(x["status"]=="QC_DEEP" for x in results),
      "no_match":sum(x["status"]=="NO_MATCH" for x in results),
      "error":sum(x["status"]=="ERROR" for x in results),
      "items":results
    }
    Path(args.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({k:out[k] for k in ["total","auto","qc","no_match","error"]},ensure_ascii=False))

if __name__=="__main__":main()
