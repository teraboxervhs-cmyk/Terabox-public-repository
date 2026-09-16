import os
import time
import json
import requests
from fastapi import FastAPI, File, UploadFile, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

# Initialize FastAPI instance (Required by Uvicorn: main:app)
app = FastAPI(
    title="Dedicated TeraBox Cloud Vault API",
    description="Permanent personal cloud storage API bridge."
)

# Enable CORS for cross-origin requests from Netlify, Vercel, or local HTML
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

TEMP_DIR = "./temp_uploads"
os.makedirs(TEMP_DIR, exist_ok=True)

# 🔒 Retrieve TeraBox session token from Render Environment Variables
TERABOX_NDUS_COOKIE = os.getenv("TERABOX_NDUS_COOKIE", "")

def get_headers() -> dict:
    if not TERABOX_NDUS_COOKIE:
        raise HTTPException(
            status_code=500, 
            detail="Server Error: TERABOX_NDUS_COOKIE environment variable is missing on Render."
        )
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Cookie": f"ndus={TERABOX_NDUS_COOKIE}",
        "Referer": "https://www.terabox.com/",
    }

@app.get("/")
def read_root():
    return {"status": "online", "vault": "Permanent Personal TeraBox Vault"}

# -----------------------------------------------------------------------------
# 1. LIST FILES
# -----------------------------------------------------------------------------
@app.get("/api/files")
def list_terabox_files(dir_path: str = Query("/", description="Folder path on TeraBox")):
    try:
        url = f"https://www.terabox.com/api/list?dir={dir_path}&order=time&desc=1"
        res = requests.get(url, headers=get_headers(), timeout=10).json()
        
        if res.get("errno") != 0:
            raise HTTPException(
                status_code=400, 
                detail=f"TeraBox Error: {res.get('errmsg', 'Invalid session cookie or session expired')}"
            )

        files = []
        for item in res.get("list", []):
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
        raise HTTPException(status_code=500, detail=str(e))

# -----------------------------------------------------------------------------
# 2. UPLOAD FILE
# -----------------------------------------------------------------------------
@app.post("/api/upload")
async def upload_to_terabox(file: UploadFile = File(...)):
    temp_path = os.path.join(TEMP_DIR, f"{int(time.time())}_{file.filename}")

    try:
        with open(temp_path, "wb") as buffer:
            content = await file.read()
            buffer.write(content)

        file_size = os.path.getsize(temp_path)
        headers = get_headers()

        # Step A: Precreate file entry
        pre_url = "https://www.terabox.com/api/precreate"
        payload = {
            "path": f"/{file.filename}",
            "size": file_size,
            "isdir": "0",
            "autoinit": "1",
            "block_list": "[]"
        }
        pre_res = requests.post(pre_url, data=payload, headers=headers, timeout=10).json()
        if pre_res.get("errno") != 0:
            raise Exception(f"Precreate failed: {pre_res.get('errmsg')}")

        upload_id = pre_res.get("uploadid")

        # Step B: Upload file chunk
        upload_url = f"https://c-jp.terabox.com/rest/2.0/pcs/superfile2?method=upload&type=tmpfile&path=/{file.filename}&uploadid={upload_id}&partseq=0"
        with open(temp_path, "rb") as f:
            upload_res = requests.post(upload_url, files={"file": f}, headers=headers, timeout=60).json()

        # Step C: Finalize creation
        commit_url = "https://www.terabox.com/api/create"
        commit_payload = {
            "path": f"/{file.filename}",
            "size": file_size,
            "isdir": "0",
            "uploadid": upload_id,
            "block_list": f'["{upload_res.get("md5")}"]'
        }
        final_res = requests.post(commit_url, data=commit_payload, headers=headers, timeout=10).json()

        return {"status": "success", "filename": file.filename, "path": final_res.get("path")}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Upload Failed: {str(e)}")
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

# -----------------------------------------------------------------------------
# 3. EXTRACT DIRECT LINKS
# -----------------------------------------------------------------------------
@app.get("/api/extract")
def extract_direct_link(share_url: str = Query(...)):
    try:
        short_key = share_url.rstrip("/").split("/")[-1].replace("1", "", 1) if "/s/1" in share_url else share_url
        api_url = f"https://www.terabox.com/api/shorturlinfo?shorturl={short_key}"
        res = requests.get(api_url, headers=get_headers(), timeout=10).json()
        
        if res.get("errno") != 0:
            raise HTTPException(status_code=400, detail=f"TeraBox Error: {res.get('errmsg')}")

        file_list = res.get("list", [])
        if not file_list:
            raise HTTPException(status_code=404, detail="No files found.")

        return {
            "status": "success",
            "files": [{"filename": f.get("server_filename"), "dlink": f.get("dlink")} for f in file_list]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# -----------------------------------------------------------------------------
# 4. DELETE FILE
# -----------------------------------------------------------------------------
@app.delete("/api/delete")
def delete_terabox_file(file_path: str = Query(..., description="Full path of the file on TeraBox")):
    try:
        url = "https://www.terabox.com/api/filemanager?opera=delete"
        payload = {"filelist": json.dumps([file_path])}

        res = requests.post(url, data=payload, headers=get_headers(), timeout=10).json()

        if res.get("errno") != 0:
            raise HTTPException(
                status_code=400, 
                detail=f"TeraBox Error: {res.get('errmsg', 'Failed to delete file')}"
            )

        return {"status": "success", "message": f"Successfully deleted: {file_path}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
