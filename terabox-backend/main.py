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

TERABOX_EMAIL = os.getenv("TERABOX_EMAIL", "")
TERABOX_PASSWORD = os.getenv("TERABOX_PASSWORD", "")

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


async def login_and_save_state():
    """Performs full UI login and saves the authenticated state to state.json."""
    global context, page
    
    print("State invalid or missing. Performing fresh login...")
    await ensure_browser_started()

    if page and not page.is_closed():
        await page.close()
    if context:
        await context.close()

    # Open fresh context with realistic desktop User-Agent
    context = await browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    page = await context.new_page()

    # 1. Navigate using domcontentloaded to prevent networkidle 30000ms timeouts
    await page.goto("https://www.terabox.com/main", wait_until="domcontentloaded", timeout=60000)

    # 2. Fill credentials & click submit
    await page.wait_for_selector("input[type='text']", timeout=30000)
    await page.fill("input[type='text']", TERABOX_EMAIL)
    await page.fill("input[type='password']", TERABOX_PASSWORD)
    await page.click("button[type='submit']")

    # 3. Wait for post-login container to confirm auth success
    await page.wait_for_selector(".file-list, .user-info", timeout=30000)

    # 4. Save session state (cookies, localStorage, tokens) to disk
    await context.storage_state(path=STATE_FILE)
    print(f"Session state saved successfully to {STATE_FILE}")


async def init_session():
    """Boots browser using state.json if available for instant sub-second startups."""
    global context, page

    if not TERABOX_EMAIL or not TERABOX_PASSWORD:
        raise HTTPException(
            status_code=500,
            detail="TERABOX_EMAIL or TERABOX_PASSWORD missing in environment variables."
        )

    if page and not page.is_closed():
        return

    await ensure_browser_started()

    # Load existing state.json if available
    if os.path.exists(STATE_FILE):
        try:
            print("Found state.json. Restoring saved session...")
            context = await browser.new_context(
                storage_state=STATE_FILE,
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            page = await context.new_page()
            await page.goto("https://www.terabox.com/main", wait_until="domcontentloaded", timeout=60000)
            return
        except Exception as e:
            print(f"Failed to load state.json: {e}")

    # Fallback to fresh login if state file is missing or expired
    await login_and_save_state()


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
    """Root endpoint health check."""
    return {"status": "online", "message": "TeraBox Vault Backend is Active", "endpoint": "/api/files"}


@app.get("/api/files")
async def list_files(dir_path: str = Query("/", description="Folder path on TeraBox")):
    async with lock:
        try:
            await init_session()

            # Fetch file list directly from TeraBox internal API using browser session context
            js_script = f"""
                async () => {{
                    const res = await fetch('https://www.terabox.com/api/list?dir={dir_path}&order=time&desc=1');
                    return await res.json();
                }}
            """
            res = await page.evaluate(js_script)

            # Auto self-healing: if session expired, re-authenticate and retry
            if res.get("errno") in [-6, 400]:
                print("Session expired during API request. Re-authenticating...")
                await login_and_save_state()
                res = await page.evaluate(js_script)

            if res.get("errno") != 0:
                raise HTTPException(
                    status_code=400,
                    detail=f"TeraBox Error: {res.get('errmsg', 'Failed to fetch files')}"
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
