import os
import json
import asyncio
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from playwright.async_api import async_playwright, BrowserContext, Page

app = FastAPI(title="Persistent Playwright TeraBox Vault API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

TERABOX_STATE_JSON = os.getenv("TERABOX_STATE_JSON", "")
STATE_FILE = "state.json"

playwright_instance = None
browser = None
context: BrowserContext = None
page: Page = None
lock = asyncio.Lock()


async def ensure_browser_started():
    """Guarantees Playwright and Chromium instances are running safely before use."""
    global playwright_instance, browser
    if not playwright_instance:
        playwright_instance = await async_playwright().start()
    if not browser or not browser.is_connected():
        browser = await playwright_instance.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"]
        )


async def init_session():
    """Boots browser context using complete storage state matching domain 1024terabox.com."""
    global context, page

    if page and not page.is_closed():
        return

    await ensure_browser_started()

    # Write state.json from environment variable if missing on disk
    if TERABOX_STATE_JSON and not os.path.exists(STATE_FILE):
        print("Writing state.json from TERABOX_STATE_JSON environment variable...")
        try:
            parsed_state = json.loads(TERABOX_STATE_JSON)
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(parsed_state, f)
        except Exception as e:
            print(f"Error parsing TERABOX_STATE_JSON env var: {e}")

    if os.path.exists(STATE_FILE):
        try:
            print("Found state.json. Restoring 1024terabox.com session...")
            context = await browser.new_context(
                storage_state=STATE_FILE,
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            page = await context.new_page()
            
            # Navigate directly to the matching domain to initialize inline script variables
            await page.goto("https://www.1024terabox.com/main", wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(3000)
            return
        except Exception as e:
            print(f"Failed to restore session from state.json: {e}")

    raise HTTPException(
        status_code=500,
        detail="No valid session state found. Please set TERABOX_STATE_JSON in environment variables."
    )


@app.on_event("startup")
async def startup_event():
    asyncio.create_task(init_session())


@app.on_event("shutdown")
async def shutdown_event():
    global playwright_instance, browser
    if browser:
        await browser.close()
    if playwright_instance:
        await playwright_instance.stop()


@app.get("/")
async def root():
    return {"status": "online", "message": "TeraBox Vault Backend Active", "endpoint": "/api/files"}


@app.get("/api/files")
async def list_files(dir_path: str = Query("/", description="Folder path on TeraBox")):
    async with lock:
        try:
            await init_session()

            # Safely extract jsToken from page context if available
            js_token = await page.evaluate("""() => {
                return window.jsToken || (window.locals && window.locals.jsToken) || '';
            }""")

            # Construct query URL
            url = f"https://www.1024terabox.com/api/list?app_id=250528&web=1&channel=dubox&clienttype=0&dir={dir_path}&order=time&desc=1"
            if js_token:
                url += f"&jsToken={js_token}"

            # Make native request using Playwright APIRequestContext (Bypasses CORS/CSP entirely)
            response = await context.request.get(
                url,
                headers={
                    "Accept": "application/json, text/plain, */*",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Referer": "https://www.1024terabox.com/main"
                }
            )

            if not response.ok:
                raise HTTPException(
                    status_code=response.status,
                    detail=f"HTTP Error from TeraBox: {response.status_text}"
                )

            res = await response.json()

            if res.get("errno") != 0:
                raise HTTPException(
                    status_code=400,
                    detail=f"TeraBox Error ({res.get('errno')}): {res.get('errmsg', 'Failed to fetch files')}"
                )

            files = [
                {
                    "fs_id": item.get("fs_id"),
                    "filename": item.get("server_filename"),
                    "path": item.get("path"),
                    "size_bytes": item.get("size", 0),
                    "is_dir": item.get("isdir") == 1,
                    "dlink": item.get("dlink"),
                    "category": item.get("category"),
                    "create_time": item.get("server_ctime"),
                }
                for item in res.get("list", [])
            ]

            return {"status": "success", "count": len(files), "files": files}

        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Playwright Error: {str(e)}")
