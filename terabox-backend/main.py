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

# Retrieve credentials or cookies from environment variables
TERABOX_NDUS = os.getenv("TERABOX_NDUS", "")
TERABOX_EMAIL = os.getenv("TERABOX_EMAIL", "")

def get_session_cookie() -> str:
    cookie_val = TERABOX_NDUS or os.getenv("TERABOX_NDUS_COOKIE", "")
    if not cookie_val:
        raise HTTPException(
            status_code=500,
            detail="Server Error: TERABOX_NDUS environment variable is missing on Render."
        )
    return cookie_val if "ndus=" in cookie_val else f"ndus={cookie_val}"

@app.get("/")
def read_root():
    return {"status": "online", "vault": "TeraBox TLS-Bridge Active"}

@app.get("/api/files")
def list_files(dir_path: str = Query("/", description="Directory path on TeraBox")):
    cookie_str = get_session_cookie()
    
    url = "https://www.terabox.com/api/list"
    params = {
        "dir": dir_path,
        "order": "time",
        "desc": "1"
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Cookie": cookie_str,
        "Referer": "https://www.terabox.com/main",
        "Accept": "application/json, text/plain, */*",
    }

    try:
        # Impersonate standard Chrome browser TLS fingerprint to bypass 400 errors
        response = requests.get(
            url, 
            params=params, 
            headers=headers, 
            impersonate="chrome124", 
            timeout=15
        )
        
        if response.status_code != 200:
            raise HTTPException(
                status_code=response.status_code,
                detail=f"TeraBox HTTP Error: {response.status_code}"
            )

        data = response.json()
        if data.get("errno") != 0:
            raise HTTPException(
                status_code=400,
                detail=f"TeraBox API Error: {data.get('errmsg', 'Failed to retrieve directory list')}"
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

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Request Failed: {str(e)}")

        return {"status": "success", "message": f"Successfully deleted: {file_path}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
