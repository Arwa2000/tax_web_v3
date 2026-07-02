from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from database import conn, USE_POSTGRES
from datetime import datetime, date, time
from routers.auth import require_read, require_write, require_delete, require_admin, get_current_user

router = APIRouter()

# ══ helpers ═══════════════════════════════════════════════════════════════════
def p(n=1):
    """إرجاع placeholders: %s للـ PG، ? للـ SQLite"""
    ph = "%s" if USE_POSTGRES else "?"
    return ", ".join([ph] * n)

def ph():
    return "%s" if USE_POSTGRES else "?"

# ══ Pydantic Models ═══════════════════════════════════════════════════════════

class ClientModel(BaseModel):
    name: str
    sector: Optional[str] = ""
    tax_number: Optional[str] = ""
    tax_type: Optional[str] = "VAT"
    risk: Optional[str] = "متوسط"
    status: Optional[str] = "قيد المراجعة"
    notes: Optional[str] = ""

class BranchModel(BaseModel):
    client_id: int
    name: str
    address: Optional[str] = ""
    manager: Optional[str] = ""
    phone: Optional[str] = ""
    status: Optional[str] = "نشط"

class BankAccountModel(BaseModel):
    client_id: int
    branch_id: Optional[int] = None
    bank_name: str
    iban: Optional[str] = ""
    balance: Optional[float] = 0

class DeviceModel(BaseModel):
    client_id: int
    branch_id: Optional[int] = None
    bank_account_id: Optional[int] = None
    device_uid: Optional[str] = ""
    name: str
    type: Optional[str] = "POS"
    location: Optional[str] = ""
    status: Optional[str] = "نشط"

class SessionModel(BaseModel):
    client_id: int
    branch_id: Optional[int] = None
    prev_date: str
    prev_time: str
    curr_date: str
    curr_time: str
    net_tx_count: int
    net_tx_amount: float
    prog_inv_count: int
    prog_inv_amount: float
    notes: Optional[str] = ""

class VisitModel(BaseModel):
    client_id: int
    visit_date: str
    visit_time: str
    visitor_name: Optional[str] = ""
    visit_type: Optional[str] = "دورية"
    status: Optional[str] = "مخططة"
    summary: Optional[str] = ""
    issues: Optional[str] = ""
    recommendations: Optional[str] = ""
    actions: Optional[str] = ""
    next_visit_date: Optional[str] = ""

class ChecklistItemModel(BaseModel):
    client_id: int
    category: str
    item: str
    sort_order: Optional[int] = 0

class ChecklistUpdateModel(BaseModel):
    done: Optional[int] = None
    item: Optional[str] = None
    notes: Optional[str] = None

class ContractModel(BaseModel):
    contractor_name: str
    description: Optional[str] = ""
    agreed_amount: float
    due_date: Optional[str] = ""
    status: Optional[str] = "جارٍ"
    notes: Optional[str] = ""

class PaymentModel(BaseModel):
    contract_id: int
    amount: float
    pay_date: str
    notes: Optional[str] = ""

class MatchingSessionModel(BaseModel):
    client_id: int
    branch_id: Optional[int] = None
    device_id: Optional[int] = None
    reviewer_name: Optional[str] = ""
    prev_date: str
    prev_time: str
    prev_net_count: Optional[int] = 0
    prev_net_amount: Optional[float] = 0
    curr_date: str
    curr_time: str
    net_tx_count: int
    net_tx_amount: float
    prog_inv_count: int
    prog_inv_amount: float
    notes: Optional[str] = ""


# ══ Dashboard ═════════════════════════════════════════════════════════════════

@router.get("/api/dashboard")
def dashboard(q: Optional[str] = None, _auth=Depends(require_read)):
    db = conn()
    try:
        if q:
            clients = db.execute(
                f"SELECT * FROM clients WHERE name LIKE {ph()} OR tax_number LIKE {ph()} OR sector LIKE {ph()}",
                (f"%{q}%", f"%{q}%", f"%{q}%")
            ).fetchall()
        else:
            clients = db.execute("SELECT * FROM clients ORDER BY name").fetchall()

        total = len(clients)
        done  = sum(1 for c in clients if c['status'] == 'مكتملة')
        late  = sum(1 for c in clients if c['status'] == 'متأخرة')
        prog  = sum(1 for c in clients if c['status'] == 'قيد المراجعة')

        sessions = db.execute("""
            SELECT s.*, c.name as client_name, b.name as branch_name
            FROM balance_sessions s
            JOIN clients c ON s.client_id=c.id
            LEFT JOIN branches b ON s.branch_id=b.id
            ORDER BY s.created_at DESC LIMIT 10
        """).fetchall()

        visits = db.execute("""
            SELECT v.*, c.name as client_name
            FROM visits v JOIN clients c ON v.client_id=c.id
            ORDER BY v.visit_date DESC, v.visit_time DESC LIMIT 8
        """).fetchall()

        alerts = db.execute("""
            SELECT a.*, c.name as client_name FROM alerts a
            LEFT JOIN clients c ON a.client_id=c.id
            ORDER BY a.created_at DESC LIMIT 8
        """).fetchall()

        return {
            "stats": {"total": total, "done": done, "late": late, "in_progress": prog},
            "clients":  [dict(c) for c in clients],
            "sessions": [dict(s) for s in sessions],
            "visits":   [dict(v) for v in visits],
            "alerts":   [dict(a) for a in alerts],
        }
    finally:
        db.close()


# ══ Clients ═══════════════════════════════════════════════════════════════════

@router.get("/api/clients")
def get_clients(_auth=Depends(require_read)):
    db = conn()
    try:
        rows = db.execute("SELECT * FROM clients ORDER BY name").fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()

@router.get("/api/clients/{id}")
def get_client(id: int, _auth=Depends(require_read)):
    db = conn()
    try:
        client   = db.execute(f"SELECT * FROM clients WHERE id={ph()}", (id,)).fetchone()
        branches = db.execute(f"SELECT * FROM branches WHERE client_id={ph()}", (id,)).fetchall()
        banks    = db.execute(f"SELECT * FROM bank_accounts WHERE client_id={ph()}", (id,)).fetchall()
        devices  = db.execute(f"""
            SELECT d.*, b.name as branch_name, ba.bank_name
            FROM network_devices d
            LEFT JOIN branches b ON d.branch_id=b.id
            LEFT JOIN bank_accounts ba ON d.bank_account_id=ba.id
            WHERE d.client_id={ph()}""", (id,)).fetchall()
        sessions = db.execute(f"""
            SELECT s.*, b.name as branch_name FROM balance_sessions s
            LEFT JOIN branches b ON s.branch_id=b.id
            WHERE s.client_id={ph()} ORDER BY s.curr_date DESC, s.curr_time DESC
        """, (id,)).fetchall()
        visits = db.execute(f"SELECT * FROM visits WHERE client_id={ph()} ORDER BY visit_date DESC", (id,)).fetchall()
        last_session = db.execute(
            f"SELECT * FROM balance_sessions WHERE client_id={ph()} ORDER BY curr_date DESC, curr_time DESC LIMIT 1", (id,)
        ).fetchone()
        return {
            "client":       dict(client) if client else {},
            "branches":     [dict(b) for b in branches],
            "banks":        [dict(b) for b in banks],
            "devices":      [dict(d) for d in devices],
            "sessions":     [dict(s) for s in sessions],
            "visits":       [dict(v) for v in visits],
            "last_session": dict(last_session) if last_session else None,
        }
    finally:
        db.close()

@router.post("/api/clients")
def add_client(data: ClientModel, _auth=Depends(require_write)):
    db = conn()
    try:
        cur = db.execute(
            f"INSERT INTO clients(name,sector,tax_number,tax_type,risk,status,notes) VALUES({p(7)})",
            (data.name, data.sector, data.tax_number, data.tax_type, data.risk, data.status, data.notes)
        )
        db.commit()
        row = db.execute(f"SELECT * FROM clients WHERE id={ph()}", (cur.lastrowid,)).fetchone()
        return dict(row)
    except Exception as e:
        db.rollback(); raise
    finally:
        db.close()

@router.put("/api/clients/{id}")
def update_client(id: int, data: ClientModel, _auth=Depends(require_write)):
    db = conn()
    try:
        db.execute(
            f"UPDATE clients SET name={ph()},sector={ph()},tax_number={ph()},tax_type={ph()},risk={ph()},status={ph()},notes={ph()} WHERE id={ph()}",
            (data.name, data.sector, data.tax_number, data.tax_type, data.risk, data.status, data.notes, id)
        )
        db.commit()
        return {"ok": True}
    except Exception as e:
        db.rollback(); raise
    finally:
        db.close()

@router.delete("/api/clients/{id}")
def delete_client(id: int, _auth=Depends(require_delete)):
    db = conn()
    try:
        db.execute(f"DELETE FROM clients WHERE id={ph()}", (id,))
        db.commit()
        return {"ok": True}
    except Exception as e:
        db.rollback(); raise
    finally:
        db.close()


# ══ Branches ══════════════════════════════════════════════════════════════════

@router.get("/api/branches/{client_id}")
def get_branches(client_id: int, _auth=Depends(require_read)):
    db = conn()
    try:
        rows = db.execute(f"SELECT * FROM branches WHERE client_id={ph()}", (client_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()

@router.post("/api/branches")
def add_branch(data: BranchModel, _auth=Depends(require_write)):
    db = conn()
    try:
        cur = db.execute(
            f"INSERT INTO branches(client_id,name,address,manager,phone,status) VALUES({p(6)})",
            (data.client_id, data.name, data.address, data.manager, data.phone, data.status)
        )
        db.commit()
        row = db.execute(f"SELECT * FROM branches WHERE id={ph()}", (cur.lastrowid,)).fetchone()
        return dict(row)
    except Exception as e:
        db.rollback(); raise
    finally:
        db.close()

@router.put("/api/branches/{id}")
def update_branch(id: int, data: BranchModel, _auth=Depends(require_write)):
    db = conn()
    try:
        db.execute(
            f"UPDATE branches SET name={ph()},address={ph()},manager={ph()},phone={ph()},status={ph()} WHERE id={ph()}",
            (data.name, data.address, data.manager, data.phone, data.status, id)
        )
        db.commit()
        return {"ok": True}
    except Exception as e:
        db.rollback(); raise
    finally:
        db.close()

@router.delete("/api/branches/{id}")
def delete_branch(id: int, _auth=Depends(require_delete)):
    db = conn()
    try:
        db.execute(f"DELETE FROM branches WHERE id={ph()}", (id,))
        db.commit()
        return {"ok": True}
    except Exception as e:
        db.rollback(); raise
    finally:
        db.close()


# ══ Bank Accounts ════════════════════════════════════════════════════════════

@router.post("/api/banks")
def add_bank(data: BankAccountModel, _auth=Depends(require_write)):
    db = conn()
    try:
        cur = db.execute(
            f"INSERT INTO bank_accounts(client_id,branch_id,bank_name,iban,balance) VALUES({p(5)})",
            (data.client_id, data.branch_id, data.bank_name, data.iban, data.balance)
        )
        db.commit()
        row = db.execute(f"SELECT * FROM bank_accounts WHERE id={ph()}", (cur.lastrowid,)).fetchone()
        return dict(row)
    except Exception as e:
        db.rollback(); raise
    finally:
        db.close()

@router.delete("/api/banks/{id}")
def delete_bank(id: int, _auth=Depends(require_delete)):
    db = conn()
    try:
        db.execute(f"DELETE FROM bank_accounts WHERE id={ph()}", (id,))
        db.commit()
        return {"ok": True}
    except Exception as e:
        db.rollback(); raise
    finally:
        db.close()


# ══ Network Devices ════════════════════════════════════════════════════════════

@router.get("/api/devices/{client_id}")
def get_devices(client_id: int, _auth=Depends(require_read)):
    db = conn()
    try:
        rows = db.execute(f"""
            SELECT d.*, b.name as branch_name, ba.bank_name
            FROM network_devices d
            LEFT JOIN branches b ON d.branch_id=b.id
            LEFT JOIN bank_accounts ba ON d.bank_account_id=ba.id
            WHERE d.client_id={ph()}""", (client_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()

@router.post("/api/devices")
def add_device(data: DeviceModel, _auth=Depends(require_write)):
    db = conn()
    try:
        cur = db.execute(
            f"INSERT INTO network_devices(client_id,branch_id,bank_account_id,device_uid,name,type,location,status) VALUES({p(8)})",
            (data.client_id, data.branch_id, data.bank_account_id,
             data.device_uid, data.name, data.type, data.location, data.status)
        )
        db.commit()
        row = db.execute(f"SELECT * FROM network_devices WHERE id={ph()}", (cur.lastrowid,)).fetchone()
        return dict(row)
    except Exception as e:
        db.rollback(); raise
    finally:
        db.close()

@router.put("/api/devices/{id}")
def update_device(id: int, data: DeviceModel, _auth=Depends(require_write)):
    db = conn()
    try:
        db.execute(
            f"UPDATE network_devices SET name={ph()},type={ph()},location={ph()},status={ph()},branch_id={ph()},bank_account_id={ph()} WHERE id={ph()}",
            (data.name, data.type, data.location, data.status, data.branch_id, data.bank_account_id, id)
        )
        db.commit()
        return {"ok": True}
    except Exception as e:
        db.rollback(); raise
    finally:
        db.close()

@router.delete("/api/devices/{id}")
def delete_device(id: int, _auth=Depends(require_delete)):
    db = conn()
    try:
        db.execute(f"DELETE FROM network_devices WHERE id={ph()}", (id,))
        db.commit()
        return {"ok": True}
    except Exception as e:
        db.rollback(); raise
    finally:
        db.close()


# ══ Balance Sessions ══════════════════════════════════════════════════════════

@router.get("/api/sessions/{client_id}")
def get_sessions(client_id: int, _auth=Depends(require_read)):
    db = conn()
    try:
        rows = db.execute(f"""
            SELECT s.*, b.name as branch_name FROM balance_sessions s
            LEFT JOIN branches b ON s.branch_id=b.id
            WHERE s.client_id={ph()} ORDER BY s.curr_date DESC, s.curr_time DESC""", (client_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()

@router.get("/api/sessions/{client_id}/last")
def get_last_session(client_id: int, _auth=Depends(require_read)):
    db = conn()
    try:
        row = db.execute(
            f"SELECT * FROM balance_sessions WHERE client_id={ph()} ORDER BY curr_date DESC, curr_time DESC LIMIT 1",
            (client_id,)
        ).fetchone()
        return dict(row) if row else {}
    finally:
        db.close()

@router.post("/api/sessions")
def add_session(data: SessionModel, _auth=Depends(require_write)):
    diff_count  = data.net_tx_count  - data.prog_inv_count
    diff_amount = round(data.net_tx_amount - data.prog_inv_amount, 2)
    if diff_count == 0 and abs(diff_amount) < 0.01:
        status = "متطابقة"
    elif abs(diff_count) <= 2 and abs(diff_amount) <= 100:
        status = "تحتاج مراجعة"
    else:
        status = "يوجد فرق"

    db = conn()
    try:
        cur = db.execute(f"""
            INSERT INTO balance_sessions
            (client_id,branch_id,prev_date,prev_time,curr_date,curr_time,
             net_tx_count,net_tx_amount,prog_inv_count,prog_inv_amount,
             diff_count,diff_amount,match_status,notes)
            VALUES({p(14)})""",
            (data.client_id, data.branch_id,
             data.prev_date, data.prev_time, data.curr_date, data.curr_time,
             data.net_tx_count, data.net_tx_amount,
             data.prog_inv_count, data.prog_inv_amount,
             diff_count, diff_amount, status, data.notes)
        )
        session_id = cur.lastrowid
        if status != "متطابقة":
            client = db.execute(f"SELECT name FROM clients WHERE id={ph()}", (data.client_id,)).fetchone()
            level = "warning" if status == "تحتاج مراجعة" else "danger"
            db.execute(f"INSERT INTO alerts(client_id,level,message) VALUES({p(3)})",
                (data.client_id, level,
                 f"{client['name']} — {status} في المطابقة: فرق {diff_count} عملية / {diff_amount:,.2f} ر.س ({data.curr_date})"))
        db.commit()
        row = db.execute(f"SELECT * FROM balance_sessions WHERE id={ph()}", (session_id,)).fetchone()
        return {**dict(row), "diff_count": diff_count, "diff_amount": diff_amount, "match_status": status}
    except Exception as e:
        db.rollback(); raise
    finally:
        db.close()

@router.delete("/api/sessions/row/{id}")
def delete_session(id: int, _auth=Depends(require_delete)):
    db = conn()
    try:
        db.execute(f"DELETE FROM balance_sessions WHERE id={ph()}", (id,))
        db.commit()
        return {"ok": True}
    except Exception as e:
        db.rollback(); raise
    finally:
        db.close()


# ══ Visits ════════════════════════════════════════════════════════════════════

@router.get("/api/visits/{client_id}")
def get_visits(client_id: int, _auth=Depends(require_read)):
    db = conn()
    try:
        rows = db.execute(
            f"SELECT * FROM visits WHERE client_id={ph()} ORDER BY visit_date DESC, visit_time DESC", (client_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()

@router.post("/api/visits")
def add_visit(data: VisitModel, _auth=Depends(require_write)):
    db = conn()
    try:
        cur = db.execute(f"""
            INSERT INTO visits(client_id,visit_date,visit_time,visitor_name,visit_type,status,
            summary,issues,recommendations,actions,next_visit_date) VALUES({p(11)})""",
            (data.client_id, data.visit_date, data.visit_time,
             data.visitor_name, data.visit_type, data.status,
             data.summary, data.issues, data.recommendations,
             data.actions, data.next_visit_date)
        )
        db.commit()
        row = db.execute(f"SELECT * FROM visits WHERE id={ph()}", (cur.lastrowid,)).fetchone()
        return dict(row)
    except Exception as e:
        db.rollback(); raise
    finally:
        db.close()

@router.put("/api/visits/{id}")
def update_visit(id: int, data: VisitModel, _auth=Depends(require_write)):
    db = conn()
    try:
        db.execute(f"""
            UPDATE visits SET visit_date={ph()},visit_time={ph()},visitor_name={ph()},visit_type={ph()},status={ph()},
            summary={ph()},issues={ph()},recommendations={ph()},actions={ph()},next_visit_date={ph()} WHERE id={ph()}""",
            (data.visit_date, data.visit_time, data.visitor_name, data.visit_type, data.status,
             data.summary, data.issues, data.recommendations, data.actions, data.next_visit_date, id)
        )
        db.commit()
        return {"ok": True}
    except Exception as e:
        db.rollback(); raise
    finally:
        db.close()

@router.delete("/api/visits/{id}")
def delete_visit(id: int, _auth=Depends(require_delete)):
    db = conn()
    try:
        db.execute(f"DELETE FROM visits WHERE id={ph()}", (id,))
        db.commit()
        return {"ok": True}
    except Exception as e:
        db.rollback(); raise
    finally:
        db.close()


# ══ Checklists ════════════════════════════════════════════════════════════════

DEFAULT_ITEMS = {
    "VAT": [
        "التحقق من صحة رقم التسجيل الضريبي",
        "مراجعة الفواتير الضريبية الصادرة",
        "التحقق من صحة الفواتير المستلمة",
        "مراجعة الإقرار الضريبي الدوري",
        "التحقق من ضريبة المدخلات والمخرجات",
        "مراجعة إعادة الاسترداد الضريبي",
        "التحقق من الإعفاءات المطبقة",
        "مراجعة العقود والاتفاقيات المرتبطة",
    ],
    "فاتورة إلكترونية": [
        "التحقق من تسجيل المنشأة في منصة فاتورة",
        "التحقق من صحة شهادة CSID",
        "مراجعة إعدادات الربط مع ZATCA",
        "فحص ملفات XML المُصدَّرة",
        "التحقق من التوقيع الرقمي على الفواتير",
        "مراجعة أكواد التصنيف الضريبي",
        "التحقق من حقول UUID وICV",
        "مراجعة سجلات الإرسال والاستلام",
        "التحقق من معالجة الفواتير المرفوضة",
        "اختبار الاتصال بالبيئة الإنتاجية",
    ],
    "زكاة": [
        "حساب وعاء الزكاة",
        "التحقق من الأصول الخاضعة للزكاة",
        "مراجعة الالتزامات المخصومة",
        "التحقق من نسبة الزكاة 2.5%",
        "مراجعة إقرار الزكاة المقدم",
        "التحقق من شهادة الزكاة السارية",
    ],
}

@router.get("/api/checklists/{client_id}")
def get_checklists(client_id: int, category: Optional[str] = None, _auth=Depends(require_read)):
    db = conn()
    try:
        if category:
            items = db.execute(
                f"SELECT * FROM checklists WHERE client_id={ph()} AND category={ph()} ORDER BY sort_order",
                (client_id, category)
            ).fetchall()
            if not items:
                for i, text in enumerate(DEFAULT_ITEMS.get(category, [])):
                    db.execute(
                        f"INSERT INTO checklists(client_id,category,item,sort_order) VALUES({p(4)})",
                        (client_id, category, text, i)
                    )
                db.commit()
                items = db.execute(
                    f"SELECT * FROM checklists WHERE client_id={ph()} AND category={ph()} ORDER BY sort_order",
                    (client_id, category)
                ).fetchall()
        else:
            items = db.execute(
                f"SELECT * FROM checklists WHERE client_id={ph()} ORDER BY category, sort_order", (client_id,)
            ).fetchall()
        return [dict(i) for i in items]
    finally:
        db.close()

@router.post("/api/checklists")
def add_checklist_item(data: ChecklistItemModel, _auth=Depends(require_write)):
    db = conn()
    try:
        cur = db.execute(
            f"INSERT INTO checklists(client_id,category,item,sort_order) VALUES({p(4)})",
            (data.client_id, data.category, data.item, data.sort_order)
        )
        db.commit()
        row = db.execute(f"SELECT * FROM checklists WHERE id={ph()}", (cur.lastrowid,)).fetchone()
        return dict(row)
    except Exception as e:
        db.rollback(); raise
    finally:
        db.close()

@router.put("/api/checklists/{id}")
def update_checklist_item(id: int, data: ChecklistUpdateModel, _auth=Depends(require_write)):
    db = conn()
    try:
        if data.done is not None:
            db.execute(f"UPDATE checklists SET done={ph()} WHERE id={ph()}", (data.done, id))
        if data.item is not None:
            db.execute(f"UPDATE checklists SET item={ph()} WHERE id={ph()}", (data.item, id))
        if data.notes is not None:
            db.execute(f"UPDATE checklists SET notes={ph()} WHERE id={ph()}", (data.notes, id))
        db.commit()
        return {"ok": True}
    except Exception as e:
        db.rollback(); raise
    finally:
        db.close()

@router.delete("/api/checklists/{id}")
def delete_checklist_item(id: int, _auth=Depends(require_delete)):
    db = conn()
    try:
        db.execute(f"DELETE FROM checklists WHERE id={ph()}", (id,))
        db.commit()
        return {"ok": True}
    except Exception as e:
        db.rollback(); raise
    finally:
        db.close()


# ══ Contracts & Payments ══════════════════════════════════════════════════════

@router.get("/api/contracts")
def get_contracts(_auth=Depends(require_read)):
    db = conn()
    try:
        contracts = db.execute("SELECT * FROM contracts ORDER BY due_date").fetchall()
        result = []
        for c in contracts:
            paid = db.execute(
                f"SELECT COALESCE(SUM(amount),0) as total FROM payments WHERE contract_id={ph()}", (c['id'],)
            ).fetchone()['total']
            payments = db.execute(
                f"SELECT * FROM payments WHERE contract_id={ph()} ORDER BY pay_date DESC", (c['id'],)
            ).fetchall()
            row = dict(c)
            row['paid_amount'] = float(paid or 0)
            row['remaining']   = float(c['agreed_amount'] or 0) - float(paid or 0)
            row['payments']    = [dict(p) for p in payments]
            result.append(row)
        return result
    finally:
        db.close()

@router.post("/api/contracts")
def add_contract(data: ContractModel, _auth=Depends(require_write)):
    db = conn()
    try:
        cur = db.execute(
            f"INSERT INTO contracts(contractor_name,description,agreed_amount,due_date,status,notes) VALUES({p(6)})",
            (data.contractor_name, data.description, data.agreed_amount, data.due_date, data.status, data.notes)
        )
        db.commit()
        row = db.execute(f"SELECT * FROM contracts WHERE id={ph()}", (cur.lastrowid,)).fetchone()
        return dict(row)
    except Exception as e:
        db.rollback(); raise
    finally:
        db.close()

@router.put("/api/contracts/{id}")
def update_contract(id: int, data: ContractModel, _auth=Depends(require_write)):
    db = conn()
    try:
        db.execute(
            f"UPDATE contracts SET contractor_name={ph()},description={ph()},agreed_amount={ph()},due_date={ph()},status={ph()},notes={ph()} WHERE id={ph()}",
            (data.contractor_name, data.description, data.agreed_amount, data.due_date, data.status, data.notes, id)
        )
        db.commit()
        return {"ok": True}
    except Exception as e:
        db.rollback(); raise
    finally:
        db.close()

@router.delete("/api/contracts/{id}")
def delete_contract(id: int, _auth=Depends(require_delete)):
    db = conn()
    try:
        db.execute(f"DELETE FROM contracts WHERE id={ph()}", (id,))
        db.commit()
        return {"ok": True}
    except Exception as e:
        db.rollback(); raise
    finally:
        db.close()

@router.post("/api/payments")
def add_payment(data: PaymentModel, _auth=Depends(require_write)):
    db = conn()
    try:
        cur = db.execute(
            f"INSERT INTO payments(contract_id,amount,pay_date,notes) VALUES({p(4)})",
            (data.contract_id, data.amount, data.pay_date, data.notes)
        )
        db.commit()
        row = db.execute(f"SELECT * FROM payments WHERE id={ph()}", (cur.lastrowid,)).fetchone()
        return dict(row)
    except Exception as e:
        db.rollback(); raise
    finally:
        db.close()

@router.delete("/api/payments/{id}")
def delete_payment(id: int, _auth=Depends(require_delete)):
    db = conn()
    try:
        db.execute(f"DELETE FROM payments WHERE id={ph()}", (id,))
        db.commit()
        return {"ok": True}
    except Exception as e:
        db.rollback(); raise
    finally:
        db.close()


# ══ Alerts ════════════════════════════════════════════════════════════════════

@router.get("/api/alerts")
def get_alerts(_auth=Depends(require_read)):
    db = conn()
    try:
        rows = db.execute("""
            SELECT a.*, c.name as client_name FROM alerts a
            LEFT JOIN clients c ON a.client_id=c.id
            ORDER BY a.created_at DESC
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()

@router.delete("/api/alerts/{id}")
def delete_alert(id: int, _auth=Depends(require_delete)):
    db = conn()
    try:
        db.execute(f"DELETE FROM alerts WHERE id={ph()}", (id,))
        db.commit()
        return {"ok": True}
    except Exception as e:
        db.rollback(); raise
    finally:
        db.close()


# ══ Enhanced Matching API ═════════════════════════════════════════════════════

@router.post("/api/matching/sessions")
def create_matching_session(data: MatchingSessionModel, _auth=Depends(require_write)):
    diff_count  = data.net_tx_count  - data.prog_inv_count
    diff_amount = round(data.net_tx_amount - data.prog_inv_amount, 2)
    if diff_count == 0 and abs(diff_amount) < 0.01:
        status = "متطابقة"
    elif abs(diff_count) <= 2 and abs(diff_amount) <= 100:
        status = "تحتاج مراجعة"
    else:
        status = "يوجد فرق"

    db = conn()
    try:
        cur = db.execute(f"""
            INSERT INTO balance_sessions
            (client_id,branch_id,device_id,reviewer_name,
             prev_date,prev_time,prev_net_count,prev_net_amount,
             curr_date,curr_time,net_tx_count,net_tx_amount,
             prog_inv_count,prog_inv_amount,diff_count,diff_amount,match_status,notes)
            VALUES({p(18)})""",
            (data.client_id, data.branch_id, data.device_id, data.reviewer_name,
             data.prev_date, data.prev_time, data.prev_net_count or 0, data.prev_net_amount or 0,
             data.curr_date, data.curr_time, data.net_tx_count, data.net_tx_amount,
             data.prog_inv_count, data.prog_inv_amount,
             diff_count, diff_amount, status, data.notes)
        )
        sid = cur.lastrowid
        if status != "متطابقة":
            client = db.execute(f"SELECT name FROM clients WHERE id={ph()}", (data.client_id,)).fetchone()
            level = "warning" if status == "تحتاج مراجعة" else "danger"
            db.execute(f"INSERT INTO alerts(client_id,level,message) VALUES({p(3)})",
                (data.client_id, level,
                 f"{client['name']} — {status}: فرق {diff_count} عملية / {diff_amount:,.2f} ر.س ({data.curr_date})"))
        db.commit()
        row = db.execute(f"SELECT * FROM balance_sessions WHERE id={ph()}", (sid,)).fetchone()
        return {**dict(row), "diff_count": diff_count, "diff_amount": diff_amount, "match_status": status}
    except Exception as e:
        db.rollback(); raise
    finally:
        db.close()

@router.get("/api/matching/sessions")
def get_all_matching_sessions(
    client_id: Optional[int] = None, device_id: Optional[int] = None,
    status: Optional[str] = None, date_from: Optional[str] = None,
    date_to: Optional[str] = None, _auth=Depends(require_read)
):
    db = conn()
    try:
        q = """SELECT s.*, c.name as client_name, b.name as branch_name,
               d.name as device_name, d.device_uid
               FROM balance_sessions s
               JOIN clients c ON s.client_id=c.id
               LEFT JOIN branches b ON s.branch_id=b.id
               LEFT JOIN network_devices d ON s.device_id=d.id
               WHERE 1=1"""
        params = []
        if client_id: q += f" AND s.client_id={ph()}"; params.append(client_id)
        if device_id: q += f" AND s.device_id={ph()}"; params.append(device_id)
        if status:    q += f" AND s.match_status={ph()}"; params.append(status)
        if date_from: q += f" AND s.curr_date>={ph()}"; params.append(date_from)
        if date_to:   q += f" AND s.curr_date<={ph()}"; params.append(date_to)
        q += " ORDER BY s.curr_date DESC, s.curr_time DESC"
        rows = db.execute(q, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()

@router.get("/api/matching/sessions/{session_id}")
def get_matching_session(session_id: int, _auth=Depends(require_read)):
    db = conn()
    try:
        row = db.execute(f"""
            SELECT s.*, c.name as client_name, b.name as branch_name,
                   d.name as device_name, d.device_uid, d.type as device_type
            FROM balance_sessions s
            JOIN clients c ON s.client_id=c.id
            LEFT JOIN branches b ON s.branch_id=b.id
            LEFT JOIN network_devices d ON s.device_id=d.id
            WHERE s.id={ph()}""", (session_id,)).fetchone()
        return dict(row) if row else {}
    finally:
        db.close()

@router.delete("/api/matching/sessions/{session_id}")
def delete_matching_session(session_id: int, _auth=Depends(require_delete)):
    db = conn()
    try:
        db.execute(f"DELETE FROM balance_sessions WHERE id={ph()}", (session_id,))
        db.commit()
        return {"ok": True}
    except Exception as e:
        db.rollback(); raise
    finally:
        db.close()

@router.get("/api/matching/last/{client_id}")
def get_last_matching_session(client_id: int, device_id: Optional[int] = None, _auth=Depends(require_read)):
    db = conn()
    try:
        if device_id:
            row = db.execute(f"""SELECT * FROM balance_sessions
                WHERE client_id={ph()} AND device_id={ph()}
                ORDER BY curr_date DESC, curr_time DESC LIMIT 1""",
                (client_id, device_id)).fetchone()
        else:
            row = db.execute(f"""SELECT * FROM balance_sessions
                WHERE client_id={ph()}
                ORDER BY curr_date DESC, curr_time DESC LIMIT 1""",
                (client_id,)).fetchone()
        return dict(row) if row else {}
    finally:
        db.close()

@router.get("/api/matching/dashboard")
def matching_dashboard(_auth=Depends(require_read)):
    db = conn()
    try:
        today       = date.today().isoformat()
        month_start = date.today().replace(day=1).isoformat()
        total       = db.execute("SELECT COUNT(*) as c FROM balance_sessions").fetchone()['c']
        today_count = db.execute(f"SELECT COUNT(*) as c FROM balance_sessions WHERE curr_date={ph()}", (today,)).fetchone()['c']
        month_count = db.execute(f"SELECT COUNT(*) as c FROM balance_sessions WHERE curr_date>={ph()}", (month_start,)).fetchone()['c']
        matched     = db.execute(f"SELECT COUNT(*) as c FROM balance_sessions WHERE match_status={ph()}", ('متطابقة',)).fetchone()['c']
        has_diff    = db.execute(f"SELECT COUNT(*) as c FROM balance_sessions WHERE match_status={ph()}", ('يوجد فرق',)).fetchone()['c']
        needs_rev   = db.execute(f"SELECT COUNT(*) as c FROM balance_sessions WHERE match_status={ph()}", ('تحتاج مراجعة',)).fetchone()['c']
        total_diff  = db.execute(f"SELECT COALESCE(SUM(ABS(diff_amount)),0) as s FROM balance_sessions WHERE match_status!={ph()}", ('متطابقة',)).fetchone()['s']

        top_devices = db.execute("""
            SELECT d.name, d.device_uid, COUNT(*) as diff_count,
                   SUM(ABS(s.diff_amount)) as total_diff
            FROM balance_sessions s
            JOIN network_devices d ON s.device_id=d.id
            WHERE s.match_status != 'متطابقة' AND s.device_id IS NOT NULL
            GROUP BY s.device_id, d.name, d.device_uid ORDER BY diff_count DESC LIMIT 5
        """).fetchall()

        recent = db.execute("""
            SELECT s.*, c.name as client_name, b.name as branch_name, d.name as device_name
            FROM balance_sessions s
            JOIN clients c ON s.client_id=c.id
            LEFT JOIN branches b ON s.branch_id=b.id
            LEFT JOIN network_devices d ON s.device_id=d.id
            ORDER BY s.curr_date DESC, s.curr_time DESC LIMIT 10
        """).fetchall()

        monthly = db.execute("""
            SELECT substr(curr_date,1,7) as month,
                   COUNT(*) as total,
                   SUM(CASE WHEN match_status='متطابقة' THEN 1 ELSE 0 END) as matched,
                   SUM(CASE WHEN match_status='يوجد فرق' THEN 1 ELSE 0 END) as diff
            FROM balance_sessions
            GROUP BY month ORDER BY month DESC LIMIT 6
        """).fetchall()

        return {
            "stats": {"total": total, "today": today_count, "month": month_count,
                      "matched": matched, "has_diff": has_diff, "needs_review": needs_rev,
                      "total_diff_amount": float(total_diff or 0)},
            "top_devices": [dict(d) for d in top_devices],
            "recent":      [dict(r) for r in recent],
            "monthly":     [dict(m) for m in monthly],
        }
    finally:
        db.close()

@router.get("/api/matching/device/{device_id}")
def get_device_sessions(device_id: int, _auth=Depends(require_read)):
    db = conn()
    try:
        device = db.execute(f"""
            SELECT d.*, b.name as branch_name, ba.bank_name, c.name as client_name
            FROM network_devices d
            LEFT JOIN branches b ON d.branch_id=b.id
            LEFT JOIN bank_accounts ba ON d.bank_account_id=ba.id
            LEFT JOIN clients c ON d.client_id=c.id
            WHERE d.id={ph()}""", (device_id,)).fetchone()
        sessions = db.execute(f"""
            SELECT s.*, c.name as client_name, b.name as branch_name
            FROM balance_sessions s
            JOIN clients c ON s.client_id=c.id
            LEFT JOIN branches b ON s.branch_id=b.id
            WHERE s.device_id={ph()}
            ORDER BY s.curr_date DESC, s.curr_time DESC
        """, (device_id,)).fetchall()
        return {
            "device":   dict(device) if device else {},
            "sessions": [dict(s) for s in sessions],
        }
    finally:
        db.close()
