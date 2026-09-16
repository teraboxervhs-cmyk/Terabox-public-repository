import os
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from curl_cffi import requests

app = FastAPI(
    title="TeraBox Vault API",
    description="Lightweight TLS-impersonated bridge for TeraBox storage."
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_session_cookie() -> str:
    cookie_val = os.getenv("TERABOX_NDUS", "").strip() or os.getenv("TERABOX_NDUS_COOKIE", "").strip()
    if not cookie_val:
        raise HTTPException(
            status_code=500,
            detail="Server Error: TERABOX_NDUS environment variable is missing on Render."
        )
    clean_val = cookie_val.split("ndus=")[-1].strip()
    return f"ndus={clean_val}"

@app.get("/")
def read_root():
    return {"status": "online", "vault": "TeraBox TLS-Bridge Active"}

@app.get("/api/files")
def list_files(dir_path: str = Query("/", description="Directory path on TeraBox")):
    cookie_str = get_session_cookie()
    js_token = os.getenv("TERABOX_JSTOKEN", "").strip()
    
    url = "https://www.terabox.com/api/list"
    params = {
        "app_id": "250528",
        "web": "1",
        "channel": "dubox",
        "clienttype": "0",
        "dir": dir_path,
        "order": "time",
        "desc": "1",
        "showempty": "0"
    }
    
    if js_token:
        params["jsToken"] = js_token
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Cookie": cookie_str,
        "Referer": "https://www.terabox.com/main",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
    }

    try:
        response = requests.get(
            url, 
            params=params, 
            headers=headers, 
            impersonate="chrome124", 
            timeout=15
        )
        
        data = response.json()
        
        if data.get("errno") != 0:
            error_code = data.get("errno")
            error_msg = data.get("errmsg", "Invalid session or parameter error")
            raise HTTPException(
                status_code=400,
                detail=f"TeraBox API Error (errno {error_code}): {error_msg}"
            )

        files = []
        for item in data.get("list", []):
            files.append({
                "fs_id": item.get("fs_id"),
                "filename": item.get("server_filename"),
                "path": item.get("path"),
                "size_bytes": item.get("size", 0),
                "is_dir": item.get("isdir") == 1,
                "dlink": item.get("dlink"),
                "category": item.get("category"),
                "create_time": item.get("server_ctime")
            })

        return {"status": "success", "count": len(files), "files": files}

    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Request Failed: {str(e)}")
