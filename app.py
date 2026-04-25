import os
import uuid
import asyncio
import sys
from fastapi import FastAPI, UploadFile, File, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Dict

app = FastAPI()

# Configuration
UPLOAD_DIR = "uploads"
OUTPUT_DIR = "outputs"
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Store job status
jobs: Dict[str, Dict] = {}

async def convert_notebook(job_id: str, input_path: str, output_path: str):
    jobs[job_id]["status"] = "converting"
    try:
        # Run nbconvert as a subprocess
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "nbconvert",
            "--to", "webpdf",
            input_path,
            "--output", os.path.basename(output_path),
            "--output-dir", OUTPUT_DIR,
            "--allow-chromium-download",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        stdout, stderr = await process.communicate()
        
        if process.returncode == 0:
            # Check if output exists. Note: nbconvert might append .pdf if not present
            if os.path.exists(output_path):
                jobs[job_id]["status"] = "completed"
                jobs[job_id]["download_url"] = f"/download/{job_id}"
            else:
                 # Check if it was renamed to something else
                 potential_output = os.path.join(OUTPUT_DIR, os.path.basename(output_path))
                 if os.path.exists(potential_output):
                    jobs[job_id]["status"] = "completed"
                    jobs[job_id]["download_url"] = f"/download/{job_id}"
                 else:
                    jobs[job_id]["status"] = "failed"
                    jobs[job_id]["error"] = "Output file not found after conversion."
        else:
            jobs[job_id]["status"] = "failed"
            jobs[job_id]["error"] = stderr.decode().strip() or stdout.decode().strip()
            
    except Exception as e:
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["error"] = str(e)

@app.post("/upload")
async def upload_file(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    if not file.filename.endswith(".ipynb"):
        raise HTTPException(status_code=400, detail="Only .ipynb files are allowed")
    
    job_id = str(uuid.uuid4())
    # Clean filename to avoid issues
    safe_filename = "".join([c for c in file.filename if c.isalnum() or c in "._- "]).strip()
    input_path = os.path.join(UPLOAD_DIR, f"{job_id}_{safe_filename}")
    output_filename = os.path.splitext(safe_filename)[0] + ".pdf"
    output_path = os.path.join(OUTPUT_DIR, f"{job_id}_{output_filename}")
    
    with open(input_path, "wb") as f:
        f.write(await file.read())
    
    jobs[job_id] = {
        "filename": file.filename,
        "status": "pending",
        "error": None,
        "output_path": output_path
    }
    
    background_tasks.add_task(convert_notebook, job_id, input_path, output_path)
    
    return {"job_id": job_id}

@app.get("/status/{job_id}")
async def get_status(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    return jobs[job_id]

@app.get("/download/{job_id}")
async def download_file(job_id: str):
    if job_id not in jobs or jobs[job_id]["status"] != "completed":
        raise HTTPException(status_code=404, detail="File not ready or not found")
    
    return FileResponse(
        jobs[job_id]["output_path"], 
        filename=os.path.basename(jobs[job_id]["output_path"]).split("_", 1)[1],
        media_type="application/pdf"
    )

@app.post("/setup")
async def setup_playwright():
    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "playwright", "install", "chromium",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await process.communicate()
        return {"status": "success", "detail": "Chromium installed"}
    except Exception as e:
        return {"status": "error", "detail": str(e)}

# Serve frontend
app.mount("/", StaticFiles(directory="static", html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
