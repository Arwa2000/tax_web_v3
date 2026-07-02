"""
Vercel Serverless Entry Point — FastAPI + PostgreSQL (Supabase)
"""
import sys, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# ── تحديد مصدر قاعدة البيانات ─────────────────────────────────────────────
# متغيرات البيئة المطلوبة على Vercel:
#   DATABASE_URL   → postgresql://postgres:<password>@db.<ref>.supabase.co:5432/postgres?sslmode=require
#   أو:
#   SUPABASE_HOST     → db.<ref>.supabase.co
#   SUPABASE_PASSWORD → كلمة مرور قاعدة البيانات
#
# إذا لم تُعيَّن، يتراجع إلى SQLite تلقائياً (للتشغيل المحلي)

import database as _db
# SQLite fallback path for local dev
if not (os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_PASSWORD")):
    if os.environ.get("VERCEL") or not os.access(ROOT, os.W_OK):
        _db.DB_PATH = "/tmp/tax_compliance.db"

_db.init()

from fastapi import FastAPI, Response
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from routers.auth   import router as auth_router
from routers.api    import router as api_router
from routers.portal import router as portal_router

app = FastAPI(title="مراقب الامتثال الضريبي")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

app.include_router(auth_router)
app.include_router(api_router)
app.include_router(portal_router)

STATIC = os.path.join(ROOT, "static")

@app.get("/static/{filename:path}")
def static_file(filename: str):
    path = os.path.join(STATIC, filename)
    if not os.path.isfile(path):
        return Response(status_code=404)
    ext = filename.rsplit(".", 1)[-1].lower()
    types = {"css":"text/css","js":"application/javascript","png":"image/png",
             "jpg":"image/jpeg","svg":"image/svg+xml","ico":"image/x-icon"}
    return FileResponse(path, media_type=types.get(ext, "application/octet-stream"))

def _page(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()

@app.get("/",          response_class=HTMLResponse)
def index():    return _page("index.html")
@app.get("/matching",  response_class=HTMLResponse)
def matching(): return _page("matching.html")
@app.get("/zatca",     response_class=HTMLResponse)
def zatca():    return _page("zatca_checker.html")
@app.get("/login",     response_class=HTMLResponse)
def login_pg(): return _page("login.html")
@app.get("/portal",    response_class=HTMLResponse)
def portal_pg(): return _page("portal.html")
@app.get("/health")
def health():
    db_type = "postgresql" if _db.USE_POSTGRES else f"sqlite:{_db.DB_PATH}"
    return {"status": "ok", "db": db_type}
