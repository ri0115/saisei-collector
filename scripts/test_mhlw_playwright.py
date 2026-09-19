#!/usr/bin/env python3
import asyncio, json
from pathlib import Path
from playwright.async_api import async_playwright

CODES=["01E2605018","03F2304004","01C2407019","01G2404003","01E2303002"]
OUT=Path("results/mhlw_playwright_probe.json")

async def main():
    rows=[]
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True)
        context=await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
            locale="ja-JP",
        )
        page=await context.new_page()
        try:
            await page.goto("https://saiseiiryo.mhlw.go.jp/published_plan/index/3", wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            rows.append({"stage":"warmup","error":str(e)})
        for code in CODES:
            for idx in [0,1]:
                url=f"https://saiseiiryo.mhlw.go.jp/published_plan/download/{code}/5/{idx}"
                rec={"code":code,"idx":idx,"url":url}
                try:
                    resp=await page.goto(url, wait_until="commit", timeout=30000)
                    rec["status"]=resp.status if resp else None
                    rec["content_type"]=(resp.headers.get("content-type") if resp else None)
                    if resp:
                        body=await resp.body()
                        rec["bytes"]=len(body)
                        rec["pdf_magic"]=b"%PDF" in body[:1024]
                except Exception as e:
                    rec["error"]=str(e)
                rows.append(rec)
        await browser.close()
    OUT.write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(rows,ensure_ascii=False))

asyncio.run(main())
