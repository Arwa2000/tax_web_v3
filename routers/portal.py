"""
بوابة العميل — Client Portal API
كل endpoint هنا خاص بالمستخدمين من نوع 'client'
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from database import conn
from datetime import datetime
from routers.auth import get_current_user, require_permission

router = APIRouter(prefix="/api/portal", tags=["portal"])

# ── Helpers ──────────────────────────────────────────────────────────────────

def get_client_user(user=Depends(get_current_user)):
    """يتحقق أن المستخدم من نوع client وله client_id"""
    if user["role"] not in ("client", "admin", "reviewer"):
        raise HTTPException(403, "هذه البوابة للعملاء فقط")
    return user

def get_client_perms(user_id: int, client_id: int):
    db = conn()
    perms = db.execute(
        "SELECT * FROM client_permissions WHERE user_id=? AND client_id=?",
        (user_id, client_id)
    ).fetchone()
    db.close()
    # admin & reviewer see everything
    return dict(perms) if perms else {
        "can_view_matching":   1,
        "can_view_visits":     0,
        "can_view_checklists": 0,
        "can_view_devices":    0,
        "can_view_banks":      0,
        "can_submit_session":  1,
    }

def get_client_perms_in_db(db, user_id: int, client_id: int):
    perms = db.execute(
        "SELECT * FROM client_permissions WHERE user_id=? AND client_id=?",
        (user_id, client_id),
    ).fetchone()
    return dict(perms) if perms else {
        "can_view_matching":   1,
        "can_view_visits":     0,
        "can_view_checklists": 0,
        "can_view_devices":    0,
        "can_view_banks":      0,
        "can_submit_session":  1,
    }

# ── My Portal ─────────────────────────────────────────────────────────────────

@router.get("/me")
def portal_me(user=Depends(get_client_user)):
    """بيانات العميل الحالي + صلاحياته"""
    db = conn()
    client_id = user.get("client_id")

    # Admin/reviewer can access all clients
    if user["role"] in ("admin", "reviewer") and not client_id:
        db.close()
        return {
            "user":        user,
            "client":      None,
            "permissions": {},
            "is_admin":    user["role"] in ("admin","reviewer"),
        }

    if not client_id:
        raise HTTPException(400, "هذا الحساب غير مرتبط بعميل")

    client = db.execute("SELECT * FROM clients WHERE id=?", (client_id,)).fetchone()
    if not client:
        db.close()
        raise HTTPException(404, "العميل غير موجود")

    perms = get_client_perms_in_db(db, user["id"], client_id)

    # Stats
    sessions = db.execute(
        "SELECT * FROM balance_sessions WHERE client_id=? ORDER BY curr_date DESC, curr_time DESC LIMIT 5",
        (client_id,)
    ).fetchall()
    pending = db.execute(
        """SELECT COUNT(*) FROM session_approvals sa
           JOIN balance_sessions bs ON sa.session_id=bs.id
           WHERE bs.client_id=? AND sa.status='pending'""",
        (client_id,)
    ).fetchone()[0]
    db.close()

    return {
        "user":         user,
        "client":       dict(client),
        "permissions":  perms,
        "is_admin":     False,
        "stats": {
            "pending_approvals": pending,
            "recent_sessions":   len(sessions),
        },
        "recent_sessions": [dict(s) for s in sessions],
    }

@router.get("/sessions")
def portal_sessions(user=Depends(get_client_user)):
    """جلسات المطابقة الخاصة بالعميل"""
    client_id = user.get("client_id")
    if not client_id:
        raise HTTPException(400, "غير مرتبط بعميل")
    db = conn()
    try:
        perms = get_client_perms_in_db(db, user["id"], client_id)
        if not perms.get("can_view_matching"):
            raise HTTPException(403, "ليس لديك صلاحية عرض المطابقات")
        rows = db.execute("""
            SELECT bs.*, b.name as branch_name, d.name as device_name,
                   sa.status as approval_status, sa.reviewer_notes, sa.client_notes,
                   sa.submitted_at, sa.reviewed_at,
                   u.full_name as reviewer_name
            FROM balance_sessions bs
            LEFT JOIN branches b ON bs.branch_id=b.id
            LEFT JOIN network_devices d ON bs.device_id=d.id
            LEFT JOIN session_approvals sa ON sa.session_id=bs.id
            LEFT JOIN users u ON sa.reviewed_by=u.id
            WHERE bs.client_id=?
            ORDER BY bs.curr_date DESC, bs.curr_time DESC
        """, (client_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()

@router.get("/devices")
def portal_devices(user=Depends(get_client_user)):
    client_id = user.get("client_id")
    if not client_id: raise HTTPException(400, "غير مرتبط بعميل")
    db = conn()
    try:
        perms = get_client_perms_in_db(db, user["id"], client_id)
        if not perms.get("can_view_devices"):
            raise HTTPException(403, "ليس لديك صلاحية عرض الأجهزة")
        rows = db.execute("""
            SELECT d.*, b.name as branch_name, ba.bank_name
            FROM network_devices d
            LEFT JOIN branches b ON d.branch_id=b.id
            LEFT JOIN bank_accounts ba ON d.bank_account_id=ba.id
            WHERE d.client_id=?
        """, (client_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()

@router.get("/branches")
def portal_branches(user=Depends(get_client_user)):
    client_id = user.get("client_id")
    if not client_id: raise HTTPException(400, "غير مرتبط بعميل")
    db = conn()
    rows = db.execute("SELECT * FROM branches WHERE client_id=?", (client_id,)).fetchall()
    db.close()
    return [dict(r) for r in rows]

# ── Submit Session ────────────────────────────────────────────────────────────

class PortalSessionModel(BaseModel):
    branch_id:       Optional[int]   = None
    device_id:       Optional[int]   = None
    prev_date:       str
    prev_time:       str
    prev_net_count:  Optional[int]   = 0
    prev_net_amount: Optional[float] = 0
    curr_date:       str
    curr_time:       str
    net_tx_count:    int
    net_tx_amount:   float
    prog_inv_count:  int
    prog_inv_amount: float
    client_notes:    Optional[str]   = ""

@router.post("/sessions")
def submit_session(data: PortalSessionModel, user=Depends(get_client_user)):
    """العميل يرفع موازنة — تذهب للمدير للموافقة"""
    client_id = user.get("client_id")
    if not client_id: raise HTTPException(400, "غير مرتبط بعميل")
    db = conn()
    try:
        perms = get_client_perms_in_db(db, user["id"], client_id)
        if not perms.get("can_submit_session"):
            raise HTTPException(403, "ليس لديك صلاحية رفع الموازنات")
        diff_count  = data.net_tx_count  - data.prog_inv_count
        diff_amount = round(data.net_tx_amount - data.prog_inv_amount, 2)
        if diff_count == 0 and abs(diff_amount) < 0.01:
            match_status = "متطابقة"
        elif abs(diff_count) <= 2 and abs(diff_amount) <= 100:
            match_status = "تحتاج مراجعة"
        else:
            match_status = "يوجد فرق"

        cur = db.execute("""
            INSERT INTO balance_sessions
            (client_id,branch_id,device_id,reviewer_name,
             prev_date,prev_time,prev_net_count,prev_net_amount,
             curr_date,curr_time,net_tx_count,net_tx_amount,
             prog_inv_count,prog_inv_amount,diff_count,diff_amount,match_status,notes)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (client_id, data.branch_id, data.device_id, user["full_name"],
              data.prev_date, data.prev_time, data.prev_net_count or 0, data.prev_net_amount or 0,
              data.curr_date, data.curr_time, data.net_tx_count, data.net_tx_amount,
              data.prog_inv_count, data.prog_inv_amount,
              diff_count, diff_amount, match_status, data.client_notes or ""))
        session_id = cur.lastrowid

        db.execute("""
            INSERT INTO session_approvals(session_id,submitted_by,status,client_notes)
            VALUES(?,?,?,?)
        """, (session_id, user["id"], "pending", data.client_notes or ""))

        client = db.execute("SELECT name FROM clients WHERE id=?", (client_id,)).fetchone()
        db.execute("""
            INSERT INTO alerts(client_id,level,message)
            VALUES(?,?,?)
        """, (client_id, "info",
              f"طلب موافقة جديد — {client['name']} — {data.curr_date} "
              f"| الحالة: {match_status} | فرق: {diff_count} عملية / {diff_amount:,.2f} ر.س"))

        db.commit()
        row = db.execute("SELECT * FROM balance_sessions WHERE id=?", (session_id,)).fetchone()
        return {**dict(row), "approval_status": "pending",
                "diff_count": diff_count, "diff_amount": diff_amount}
    finally:
        db.close()


# ── Admin: Approval Management ────────────────────────────────────────────────

def require_admin_or_reviewer(user=Depends(get_current_user)):
    if user["role"] not in ("admin", "reviewer"):
        raise HTTPException(403, "هذه العملية للمدير والمراجع فقط")
    return user

@router.get("/approvals")
def get_approvals(status: Optional[str] = None, client_id: Optional[int] = None,
                  user=Depends(require_admin_or_reviewer)):
    db = conn()
    q = """
        SELECT sa.*, bs.curr_date, bs.curr_time, bs.prev_date, bs.prev_time,
               bs.net_tx_count, bs.net_tx_amount, bs.prog_inv_count, bs.prog_inv_amount,
               bs.diff_count, bs.diff_amount, bs.match_status, bs.client_id,
               bs.branch_id, bs.device_id,
               c.name as client_name,
               b.name as branch_name,
               d.name as device_name,
               su.full_name as submitted_by_name,
               ru.full_name as reviewed_by_name
        FROM session_approvals sa
        JOIN balance_sessions bs ON sa.session_id=bs.id
        JOIN clients c ON bs.client_id=c.id
        LEFT JOIN branches b ON bs.branch_id=b.id
        LEFT JOIN network_devices d ON bs.device_id=d.id
        LEFT JOIN users su ON sa.submitted_by=su.id
        LEFT JOIN users ru ON sa.reviewed_by=ru.id
        WHERE 1=1
    """
    params = []
    if status:    q += " AND sa.status=?"; params.append(status)
    if client_id: q += " AND bs.client_id=?"; params.append(client_id)
    q += " ORDER BY sa.submitted_at DESC"
    rows = db.execute(q, params).fetchall()
    db.close()
    return [dict(r) for r in rows]

class ApprovalAction(BaseModel):
    action:         str   # approve / reject / request_edit
    reviewer_notes: Optional[str] = ""
    # للتعديل المباشر
    net_tx_count:   Optional[int]   = None
    net_tx_amount:  Optional[float] = None
    prog_inv_count: Optional[int]   = None
    prog_inv_amount:Optional[float] = None

@router.post("/approvals/{approval_id}/review")
def review_approval(approval_id: int, data: ApprovalAction,
                    user=Depends(require_admin_or_reviewer)):
    if data.action not in ("approve","reject","request_edit"):
        raise HTTPException(400, "إجراء غير صالح")

    db = conn()
    approval = db.execute("SELECT * FROM session_approvals WHERE id=?", (approval_id,)).fetchone()
    if not approval:
        db.close(); raise HTTPException(404, "الطلب غير موجود")

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # If admin edits values directly
    if data.action == "approve" and any(v is not None for v in [
        data.net_tx_count, data.net_tx_amount, data.prog_inv_count, data.prog_inv_amount
    ]):
        sid = approval["session_id"]
        session = db.execute("SELECT * FROM balance_sessions WHERE id=?", (sid,)).fetchone()
        net_c = data.net_tx_count    if data.net_tx_count    is not None else session["net_tx_count"]
        net_a = data.net_tx_amount   if data.net_tx_amount   is not None else session["net_tx_amount"]
        prg_c = data.prog_inv_count  if data.prog_inv_count  is not None else session["prog_inv_count"]
        prg_a = data.prog_inv_amount if data.prog_inv_amount is not None else session["prog_inv_amount"]
        diff_c = net_c - prg_c
        diff_a = round(net_a - prg_a, 2)
        if diff_c == 0 and abs(diff_a) < 0.01: ms = "متطابقة"
        elif abs(diff_c) <= 2 and abs(diff_a) <= 100: ms = "تحتاج مراجعة"
        else: ms = "يوجد فرق"
        db.execute("""UPDATE balance_sessions
            SET net_tx_count=?,net_tx_amount=?,prog_inv_count=?,prog_inv_amount=?,
                diff_count=?,diff_amount=?,match_status=? WHERE id=?""",
            (net_c,net_a,prg_c,prg_a,diff_c,diff_a,ms,sid))

    # Update approval record
    db.execute("""UPDATE session_approvals
        SET status=?,reviewer_notes=?,reviewed_by=?,reviewed_at=? WHERE id=?""",
        (data.action, data.reviewer_notes or "", user["id"], now, approval_id))

    # Notify client via alert
    session = db.execute(
        "SELECT bs.*, c.name as client_name FROM balance_sessions bs JOIN clients c ON bs.client_id=c.id WHERE bs.id=?",
        (approval["session_id"],)
    ).fetchone()
    msg_map = {
        "approve":      f"✅ تمت الموافقة على موازنة {session['client_name']} بتاريخ {session['curr_date']}",
        "reject":       f"❌ تم رفض موازنة {session['client_name']} بتاريخ {session['curr_date']}",
        "request_edit": f"✏️ طُلب تعديل موازنة {session['client_name']} بتاريخ {session['curr_date']}",
    }
    level_map = {"approve":"success","reject":"danger","request_edit":"warning"}
    db.execute("INSERT INTO alerts(client_id,level,message) VALUES(?,?,?)",
               (session["client_id"], level_map[data.action], msg_map[data.action]
                + (f" — {data.reviewer_notes}" if data.reviewer_notes else "")))
    db.commit(); db.close()
    return {"ok": True, "action": data.action}


# ── Admin: Client User Management ─────────────────────────────────────────────

class ClientUserCreate(BaseModel):
    username:   str
    full_name:  str
    email:      Optional[str] = ""
    password:   str
    client_id:  int
    # permissions
    can_view_matching:   Optional[int] = 1
    can_view_visits:     Optional[int] = 0
    can_view_checklists: Optional[int] = 0
    can_view_devices:    Optional[int] = 0
    can_view_banks:      Optional[int] = 0
    can_submit_session:  Optional[int] = 1

class ClientPermUpdate(BaseModel):
    can_view_matching:   Optional[int] = None
    can_view_visits:     Optional[int] = None
    can_view_checklists: Optional[int] = None
    can_view_devices:    Optional[int] = None
    can_view_banks:      Optional[int] = None
    can_submit_session:  Optional[int] = None

@router.post("/create-client-user")
def create_client_user(data: ClientUserCreate,
                       admin=Depends(require_admin_or_reviewer)):
    from werkzeug.security import generate_password_hash
    if len(data.password) < 8:
        raise HTTPException(400, "كلمة المرور 8 أحرف على الأقل")
    db = conn()
    if db.execute("SELECT id FROM users WHERE username=?", (data.username,)).fetchone():
        db.close(); raise HTTPException(400, f"اسم المستخدم '{data.username}' موجود")
    if not db.execute("SELECT id FROM clients WHERE id=?", (data.client_id,)).fetchone():
        db.close(); raise HTTPException(404, "العميل غير موجود")

    cur = db.execute(
        "INSERT INTO users(username,full_name,email,password,role,client_id) VALUES(?,?,?,?,?,?)",
        (data.username, data.full_name, data.email,
         generate_password_hash(data.password), "client", data.client_id)
    )
    uid = cur.lastrowid
    db.execute("""
        INSERT INTO client_permissions
        (user_id,client_id,can_view_matching,can_view_visits,can_view_checklists,
         can_view_devices,can_view_banks,can_submit_session)
        VALUES(?,?,?,?,?,?,?,?)
    """, (uid, data.client_id,
          data.can_view_matching, data.can_view_visits, data.can_view_checklists,
          data.can_view_devices, data.can_view_banks, data.can_submit_session))
    db.commit()
    row = db.execute("SELECT id,username,full_name,email,role,client_id FROM users WHERE id=?", (uid,)).fetchone()
    db.close()
    return dict(row)

@router.get("/client-users/{client_id}")
def get_client_users(client_id: int, admin=Depends(require_admin_or_reviewer)):
    db = conn()
    users = db.execute("""
        SELECT u.id,u.username,u.full_name,u.email,u.is_active,u.last_login,
               cp.can_view_matching,cp.can_view_visits,cp.can_view_checklists,
               cp.can_view_devices,cp.can_view_banks,cp.can_submit_session
        FROM users u
        LEFT JOIN client_permissions cp ON cp.user_id=u.id AND cp.client_id=?
        WHERE u.client_id=? AND u.role='client'
    """, (client_id, client_id)).fetchall()
    db.close()
    return [dict(u) for u in users]

@router.put("/permissions/{user_id}")
def update_permissions(user_id: int, data: ClientPermUpdate,
                       admin=Depends(require_admin_or_reviewer)):
    db = conn()
    user = db.execute("SELECT * FROM users WHERE id=? AND role='client'", (user_id,)).fetchone()
    if not user: db.close(); raise HTTPException(404, "المستخدم غير موجود")
    existing = db.execute("SELECT * FROM client_permissions WHERE user_id=?", (user_id,)).fetchone()
    updates = {k:v for k,v in data.dict().items() if v is not None}
    if existing:
        if updates:
            sets = ",".join(f"{k}=?" for k in updates)
            db.execute(f"UPDATE client_permissions SET {sets} WHERE user_id=?",
                      (*updates.values(), user_id))
    else:
        db.execute("""INSERT INTO client_permissions(user_id,client_id) VALUES(?,?)""",
                   (user_id, user["client_id"]))
        if updates:
            sets = ",".join(f"{k}=?" for k in updates)
            db.execute(f"UPDATE client_permissions SET {sets} WHERE user_id=?",
                      (*updates.values(), user_id))
    db.commit(); db.close()
    return {"ok": True}
