"""
نظام المصادقة — JWT + Roles
الأدوار:
  admin    → كل الصلاحيات (قراءة + كتابة + حذف + إدارة المستخدمين)
  reviewer → قراءة + كتابة + حذف (لا يدير المستخدمين)
  viewer   → قراءة فقط
"""
from fastapi import APIRouter, HTTPException, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timedelta, timezone
from werkzeug.security import generate_password_hash, check_password_hash
from jose import JWTError, jwt
from database import conn, USE_POSTGRES

def _ph():
    return "%s" if USE_POSTGRES else "?"

router = APIRouter()

# ── Config ────────────────────────────────────────────────────────────────────
SECRET_KEY = "ZATCA-TAX-COMPLIANCE-SECRET-2026-CHANGE-IN-PRODUCTION"
ALGORITHM  = "HS256"
TOKEN_EXPIRE_HOURS = 12


bearer  = HTTPBearer(auto_error=False)

# ── Roles & Permissions ───────────────────────────────────────────────────────
ROLE_PERMISSIONS = {
    "admin":    {"read", "write", "delete", "manage_users"},
    "reviewer": {"read", "write", "delete"},
    "viewer":   {"read"},
    "client":   {"read"},   # العميل — وصول عبر البوابة فقط
}

ROLE_LABELS = {
    "admin":    "مدير النظام",
    "reviewer": "مراجع",
    "viewer":   "مشاهد فقط",
    "client":   "عميل",
}

# ── Models ────────────────────────────────────────────────────────────────────
class LoginRequest(BaseModel):
    username: str
    password: str

class UserCreate(BaseModel):
    username: str
    full_name: str
    email: Optional[str] = ""
    password: str
    role: str = "viewer"

class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    email: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[int] = None

class PasswordChange(BaseModel):
    old_password: str
    new_password: str

# ── JWT helpers ───────────────────────────────────────────────────────────────
def create_token(data: dict) -> str:
    payload = data.copy()
    payload["exp"] = datetime.now(timezone.utc) + timedelta(hours=TOKEN_EXPIRE_HOURS)
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="توكن غير صالح أو منتهي الصلاحية")

def get_current_user(creds: HTTPAuthorizationCredentials = Depends(bearer)):
    if not creds:
        raise HTTPException(status_code=401, detail="يجب تسجيل الدخول أولاً")
    payload = decode_token(creds.credentials)
    db = conn()
    user = db.execute(
        f"SELECT * FROM users WHERE id={_ph()} AND is_active=1", (int(payload.get("sub",0)),)
    ).fetchone()
    db.close()
    if not user:
        raise HTTPException(status_code=401, detail="المستخدم غير موجود أو غير نشط")
    return dict(user)

def require_permission(permission: str):
    def checker(user=Depends(get_current_user)):
        perms = ROLE_PERMISSIONS.get(user["role"], set())
        if permission not in perms:
            raise HTTPException(
                status_code=403,
                detail=f"ليس لديك صلاحية '{permission}' — دورك: {ROLE_LABELS.get(user['role'], user['role'])}"
            )
        return user
    return checker

# ── Shortcuts ─────────────────────────────────────────────────────────────────
require_read   = require_permission("read")
require_write  = require_permission("write")
require_delete = require_permission("delete")
require_admin  = require_permission("manage_users")

# ── Auth Endpoints ────────────────────────────────────────────────────────────

@router.post("/api/auth/login")
def login(data: LoginRequest):
    db = conn()
    user = db.execute(
        f"SELECT * FROM users WHERE username={_ph()} AND is_active=1",
        (data.username.strip(),)
    ).fetchone()

    if not user or not check_password_hash(user["password"], data.password):
        raise HTTPException(status_code=401, detail="اسم المستخدم أو كلمة المرور غير صحيحة")

    # Update last login
    db.execute(
        f"UPDATE users SET last_login={_ph()} WHERE id={_ph()}",
        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), user["id"])
    )
    db.commit()
    db.close()

    token = create_token({
        "sub":      str(user["id"]),
        "username": user["username"],
        "role":     user["role"],
        "name":     user["full_name"],
    })

    return {
        "access_token": token,
        "token_type":   "bearer",
        "expires_in":   TOKEN_EXPIRE_HOURS * 3600,
        "user": {
            "id":        user["id"],
            "username":  user["username"],
            "full_name": user["full_name"],
            "role":      user["role"],
            "role_label":ROLE_LABELS.get(user["role"], user["role"]),
            "permissions": list(ROLE_PERMISSIONS.get(user["role"], set())),
        }
    }

@router.get("/api/auth/me")
def me(user=Depends(get_current_user)):
    return {
        "id":          user["id"],
        "username":    user["username"],
        "full_name":   user["full_name"],
        "email":       user["email"],
        "role":        user["role"],
        "role_label":  ROLE_LABELS.get(user["role"], user["role"]),
        "permissions": list(ROLE_PERMISSIONS.get(user["role"], set())),
        "last_login":  user["last_login"],
    }

@router.post("/api/auth/logout")
def logout():
    # JWT stateless — client deletes token
    return {"ok": True, "message": "تم تسجيل الخروج"}

@router.post("/api/auth/change-password")
def change_password(data: PasswordChange, user=Depends(get_current_user)):
    db = conn()
    u = db.execute(f"SELECT * FROM users WHERE id={_ph()}", (user["id"],)).fetchone()
    if not check_password_hash(u["password"], data.old_password):
        db.close()
        raise HTTPException(status_code=400, detail="كلمة المرور الحالية غير صحيحة")
    if len(data.new_password) < 8:
        db.close()
        raise HTTPException(status_code=400, detail="كلمة المرور الجديدة يجب أن تكون 8 أحرف على الأقل")
    db.execute(
        f"UPDATE users SET password={_ph()} WHERE id={_ph()}",
        (generate_password_hash(data.new_password), user["id"])
    )
    db.commit()
    db.close()
    return {"ok": True, "message": "تم تغيير كلمة المرور بنجاح"}

# ── Users Management (Admin only) ─────────────────────────────────────────────

@router.get("/api/users")
def get_users(admin=Depends(require_admin)):
    db = conn()
    rows = db.execute(
        "SELECT id,username,full_name,email,role,is_active,created_at,last_login FROM users ORDER BY id"
    ).fetchall()
    db.close()
    return [{**dict(r), "role_label": ROLE_LABELS.get(r["role"], r["role"])} for r in rows]

@router.post("/api/users")
def create_user(data: UserCreate, admin=Depends(require_admin)):
    if data.role not in ROLE_PERMISSIONS:
        raise HTTPException(status_code=400, detail=f"الدور غير صالح. الأدوار المتاحة: {list(ROLE_PERMISSIONS)}")
    if len(data.password) < 8:
        raise HTTPException(status_code=400, detail="كلمة المرور يجب أن تكون 8 أحرف على الأقل")
    db = conn()
    existing = db.execute(f"SELECT id FROM users WHERE username={_ph()}", (data.username,)).fetchone()
    if existing:
        db.close()
        raise HTTPException(status_code=400, detail=f"اسم المستخدم '{data.username}' موجود مسبقاً")
    cur = db.execute(
        f"INSERT INTO users(username,full_name,email,password,role) VALUES({_ph()},{_ph()},{_ph()},{_ph()},{_ph()})",
        (data.username, data.full_name, data.email, pwd_ctx.hash(data.password), data.role)
    )
    db.commit()
    row = db.execute(
        f"SELECT id,username,full_name,email,role,is_active,created_at FROM users WHERE id={_ph()}",
        (cur.lastrowid,)
    ).fetchone()
    db.close()
    return {**dict(row), "role_label": ROLE_LABELS.get(row["role"], row["role"])}

@router.put("/api/users/{user_id}")
def update_user(user_id: int, data: UserUpdate, admin=Depends(require_admin)):
    db = conn()
    user = db.execute(f"SELECT * FROM users WHERE id={_ph()}", (user_id,)).fetchone()
    if not user:
        db.close()
        raise HTTPException(status_code=404, detail="المستخدم غير موجود")
    # Prevent admin from demoting themselves
    if user["username"] == "admin" and data.role and data.role != "admin":
        db.close()
        raise HTTPException(status_code=400, detail="لا يمكن تغيير دور المدير الرئيسي")
    updates = {}
    if data.full_name is not None: updates["full_name"] = data.full_name
    if data.email is not None:     updates["email"]     = data.email
    if data.role is not None:
        if data.role not in ROLE_PERMISSIONS:
            raise HTTPException(status_code=400, detail="الدور غير صالح")
        updates["role"] = data.role
    if data.is_active is not None: updates["is_active"] = data.is_active
    if updates:
        sets = ", ".join(f"{k}={_ph()}" for k in updates)
        db.execute(f"UPDATE users SET {sets} WHERE id={_ph()}", (*updates.values(), user_id))
        db.commit()
    db.close()
    return {"ok": True}

@router.delete("/api/users/{user_id}")
def delete_user(user_id: int, admin=Depends(require_admin)):
    db = conn()
    user = db.execute(f"SELECT * FROM users WHERE id={_ph()}", (user_id,)).fetchone()
    if not user:
        db.close()
        raise HTTPException(status_code=404, detail="المستخدم غير موجود")
    if user["username"] == "admin":
        db.close()
        raise HTTPException(status_code=400, detail="لا يمكن حذف المدير الرئيسي")
    db.execute(f"DELETE FROM users WHERE id={_ph()}", (user_id,))
    db.commit()
    db.close()
    return {"ok": True}

@router.post("/api/users/{user_id}/reset-password")
def reset_password(user_id: int, data: dict, admin=Depends(require_admin)):
    new_pwd = data.get("new_password", "")
    if len(new_pwd) < 8:
        raise HTTPException(status_code=400, detail="كلمة المرور يجب أن تكون 8 أحرف على الأقل")
    db = conn()
    db.execute(f"UPDATE users SET password={_ph()} WHERE id={_ph()}", (generate_password_hash(new_pwd), user_id))
    db.commit()
    db.close()
    return {"ok": True, "message": "تم إعادة تعيين كلمة المرور"}
