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


async def login_and_save_state():
    """Performs full UI login and saves the authenticated state to state.json."""
    global context, page
    
    print("State invalid or missing. Performing fresh login...")
    if page and not page.is_closed():
        await page.close()

    # Create fresh context without saved state
    context = await browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    page = await context.new_page()

    await page.goto("https://www.terabox.com/main", wait_until="networkidle")

    # Perform UI login
    await page.fill("input[type='text']", TERABOX_EMAIL)
    await page.fill("input[type='password']", TERABOX_PASSWORD)
    await page.click("button[type='submit']")

    # Wait for file list container to confirm successful login
    await page.wait_for_selector(".file-list", timeout=20000)

    # Save session state (cookies, localStorage, etc.)
    await context.storage_state(path=STATE_FILE)
    print(f"Session state saved successfully to {STATE_FILE}")


async def init_session():
    """Boots browser using state.json if available, speeding up startup."""
    global playwright_instance, browser, context, page

    if not TERABOX_EMAIL or not TERABOX_PASSWORD:
        raise HTTPException(
            status_code=500,
            detail="TERABOX_EMAIL or TERABOX_PASSWORD missing in environment."
        )

    if page and not page.is_closed():
        return

    if not playwright_instance:
        playwright_instance = await async_playwright().start()
        browser = await playwright_instance.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"]
        )

    # Try loading existing session state
    if os.path.exists(STATE_FILE):
        try:
            print("Found state.json. Restoring session...")
            context = await browser.new_context(
                storage_state=STATE_FILE,
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            page = await context.new_page()
            await page.goto("https://www.terabox.com/main", wait_until="domcontentloaded")
            return
        except Exception as e:
            print(f"Failed to load state.json: {e}")

    # Fallback to fresh login if state file doesn't exist or is invalid
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


@app.get("/api/files")
async def list_files(dir_path: str = Query("/", description="Folder path on TeraBox")):
    async with lock:
        try:
            await init_session()

            # Execute API call inside authenticated browser context
            js_script = f"""
                async () => {{
                    const res = await fetch('https://www.terabox.com/api/list?dir={dir_path}&order=time&desc=1');
                    return await res.json();
                }}
            """
            res = await page.evaluate(js_script)

            # If token expired while using saved state, re-login once automatically
            if res.get("errno") in [-6, 400]:
                print("Session expired during request. Re-authenticating...")
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
