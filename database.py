"""
قاعدة البيانات — النسخة الثالثة
PostgreSQL عبر Supabase (psycopg2)
مع fallback إلى SQLite للتشغيل المحلي بدون إنترنت
"""
import os, sqlite3, json, threading
from contextlib import contextmanager

# ── إعدادات Supabase ──────────────────────────────────────────────────────────
SUPABASE_DB_URL = os.environ.get("DATABASE_URL", "")
# يمكن تعيينها أيضاً كمتغيرات منفصلة
SUPABASE_HOST     = os.environ.get("SUPABASE_HOST", "db.gpnzbuobfpgjahmfxyjv.supabase.co")
SUPABASE_PASSWORD = os.environ.get("SUPABASE_PASSWORD", "")

USE_POSTGRES = bool(SUPABASE_DB_URL or SUPABASE_PASSWORD)

# ── SQLite fallback path ──────────────────────────────────────────────────────
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tax.db")

_INIT_DONE = False
_INIT_LOCK = threading.Lock()

_PG_POOL = None
_PG_POOL_LOCK = threading.Lock()
_PG_POOL_DSN = None

PG_POOL_MIN = int(os.environ.get("PG_POOL_MIN", "1") or "1")
PG_POOL_MAX = int(os.environ.get("PG_POOL_MAX", "3") or "3")
PG_POOL_DISABLE = (os.environ.get("PG_POOL_DISABLE", "").strip().lower() in ("1", "true", "yes"))


# ════════════════════════════════════════════════════════════════════════════════
# PostgreSQL wrapper — يحوّل ? → %s ويعمل مثل sqlite3.Connection
# ════════════════════════════════════════════════════════════════════════════════
class PGRow(dict):
    """يحاكي sqlite3.Row: الوصول بالاسم والرقم"""
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


class PGCursor:
    def __init__(self, cur, desc=None):
        self._cur = cur
        self._desc = desc

    @property
    def lastrowid(self):
        return self._cur.fetchone()[0] if self._cur.rowcount != 0 else None

    def fetchone(self):
        row = self._cur.fetchone()
        if row is None:
            return None
        cols = [d[0] for d in self._cur.description]
        return PGRow(zip(cols, row))

    def fetchall(self):
        rows = self._cur.fetchall()
        if not rows:
            return []
        cols = [d[0] for d in self._cur.description]
        return [PGRow(zip(cols, r)) for r in rows]


class PGConnection:
    def __init__(self, dsn=None, _cn=None, _pool=None):
        if _cn is None:
            import psycopg2
            _cn = psycopg2.connect(dsn, connect_timeout=15)
        _cn.autocommit = False
        self._cn = _cn
        self._pool = _pool

    def _fix(self, sql, params=None):
        """تحويل ? إلى %s وإزالة AUTOINCREMENT"""
        sql = sql.replace("?", "%s")
        sql = sql.replace("AUTOINCREMENT", "")
        sql = sql.replace("INTEGER PRIMARY KEY", "SERIAL PRIMARY KEY")
        return sql, params or []

    def execute(self, sql, params=None):
        sql, params = self._fix(sql, params)
        # إضافة RETURNING id لعمليات INSERT
        if sql.strip().upper().startswith("INSERT") and "RETURNING" not in sql.upper():
            sql = sql.rstrip("; \n") + " RETURNING id"
        cur = self._cn.cursor()
        cur.execute(sql, params)
        return PGCursor(cur)

    def executescript(self, script):
        """تنفيذ مجموعة أوامر DDL"""
        import psycopg2
        cur = self._cn.cursor()
        # تحويل نص SQL
        script = script.replace("AUTOINCREMENT", "")
        script = script.replace("INTEGER PRIMARY KEY", "SERIAL PRIMARY KEY")
        # تجاهل PRAGMA
        lines = [l for l in script.split("\n") if not l.strip().upper().startswith("PRAGMA")]
        script = "\n".join(lines)
        try:
            cur.execute(script)
            self._cn.commit()
        except Exception as e:
            self._cn.rollback()
            raise

    def commit(self):
        self._cn.commit()

    def rollback(self):
        self._cn.rollback()

    def close(self):
        if self._pool is None:
            self._cn.close()
            return
        try:
            if getattr(self._cn, "closed", 0) == 0:
                try:
                    self._cn.rollback()
                except Exception:
                    pass
        finally:
            self._pool.putconn(self._cn)


# ════════════════════════════════════════════════════════════════════════════════
# SQLite wrapper — يجعله يتصرف مثل PGConnection (يُرجع dict-like rows)
# ════════════════════════════════════════════════════════════════════════════════
class SQLiteConnection:
    def __init__(self, path):
        self._cn = sqlite3.connect(path)
        self._cn.row_factory = sqlite3.Row
        self._cn.execute("PRAGMA foreign_keys = ON")

    def execute(self, sql, params=None):
        cur = self._cn.execute(sql, params or [])
        return _SQLiteCursor(cur)

    def executescript(self, script):
        self._cn.executescript(script)

    def commit(self):
        self._cn.commit()

    def rollback(self):
        self._cn.rollback()

    def close(self):
        self._cn.close()


class _SQLiteCursor:
    def __init__(self, cur):
        self._cur = cur

    @property
    def lastrowid(self):
        return self._cur.lastrowid

    def fetchone(self):
        row = self._cur.fetchone()
        if row is None:
            return None
        return dict(row)

    def fetchall(self):
        return [dict(r) for r in self._cur.fetchall()]


# ════════════════════════════════════════════════════════════════════════════════
# دالة الاتصال الموحّدة
# ════════════════════════════════════════════════════════════════════════════════
def _build_dsn():
    if SUPABASE_DB_URL:
        return SUPABASE_DB_URL
    host     = SUPABASE_HOST
    password = SUPABASE_PASSWORD
    user     = os.environ.get("SUPABASE_USER", "postgres")
    port     = os.environ.get("SUPABASE_PORT", "5432")
    dbname   = os.environ.get("SUPABASE_DB",   "postgres")
    return f"postgresql://{user}:{password}@{host}:{port}/{dbname}?sslmode=require"

def _get_pg_pool():
    global _PG_POOL, _PG_POOL_DSN
    if not USE_POSTGRES or PG_POOL_DISABLE:
        return None
    dsn = _build_dsn()
    with _PG_POOL_LOCK:
        if _PG_POOL is not None and _PG_POOL_DSN == dsn:
            return _PG_POOL

        import psycopg2
        from psycopg2.pool import ThreadedConnectionPool

        if _PG_POOL is not None:
            try:
                _PG_POOL.closeall()
            except Exception:
                pass
            _PG_POOL = None
            _PG_POOL_DSN = None

        minc = max(1, PG_POOL_MIN)
        maxc = max(minc, PG_POOL_MAX)
        _PG_POOL = ThreadedConnectionPool(minc, maxc, dsn, connect_timeout=15)
        _PG_POOL_DSN = dsn
        return _PG_POOL


def conn():
    if USE_POSTGRES:
        pool = _get_pg_pool()
        if pool is None:
            return PGConnection(_build_dsn())
        cn = pool.getconn()
        if getattr(cn, "closed", 0):
            try:
                pool.putconn(cn, close=True)
            except Exception:
                pass
            cn = pool.getconn()
        return PGConnection(_cn=cn, _pool=pool)
    else:
        return SQLiteConnection(DB_PATH)


# ════════════════════════════════════════════════════════════════════════════════
# إنشاء الجداول
# ════════════════════════════════════════════════════════════════════════════════
CREATE_TABLES_PG = """
CREATE TABLE IF NOT EXISTS clients (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    sector      TEXT,
    tax_number  TEXT,
    tax_type    TEXT DEFAULT 'VAT',
    risk        TEXT DEFAULT 'متوسط',
    status      TEXT DEFAULT 'قيد المراجعة',
    notes       TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS branches (
    id          SERIAL PRIMARY KEY,
    client_id   INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    address     TEXT,
    manager     TEXT,
    phone       TEXT,
    status      TEXT DEFAULT 'نشط',
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS bank_accounts (
    id          SERIAL PRIMARY KEY,
    client_id   INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    branch_id   INTEGER REFERENCES branches(id) ON DELETE SET NULL,
    bank_name   TEXT,
    iban        TEXT,
    balance     NUMERIC DEFAULT 0,
    status      TEXT DEFAULT 'نشط'
);

CREATE TABLE IF NOT EXISTS network_devices (
    id              SERIAL PRIMARY KEY,
    client_id       INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    branch_id       INTEGER REFERENCES branches(id) ON DELETE SET NULL,
    bank_account_id INTEGER REFERENCES bank_accounts(id) ON DELETE SET NULL,
    device_uid      TEXT,
    name            TEXT,
    type            TEXT DEFAULT 'POS',
    location        TEXT,
    status          TEXT DEFAULT 'نشط'
);

CREATE TABLE IF NOT EXISTS balance_sessions (
    id                  SERIAL PRIMARY KEY,
    client_id           INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    branch_id           INTEGER REFERENCES branches(id) ON DELETE SET NULL,
    device_id           INTEGER REFERENCES network_devices(id) ON DELETE SET NULL,
    reviewer_name       TEXT,
    prev_date           TEXT NOT NULL,
    prev_time           TEXT NOT NULL,
    prev_net_count      INTEGER DEFAULT 0,
    prev_net_amount     NUMERIC DEFAULT 0,
    curr_date           TEXT NOT NULL,
    curr_time           TEXT NOT NULL,
    net_tx_count        INTEGER DEFAULT 0,
    net_tx_amount       NUMERIC DEFAULT 0,
    prog_inv_count      INTEGER DEFAULT 0,
    prog_inv_amount     NUMERIC DEFAULT 0,
    diff_count          INTEGER DEFAULT 0,
    diff_amount         NUMERIC DEFAULT 0,
    match_status        TEXT,
    notes               TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS visits (
    id              SERIAL PRIMARY KEY,
    client_id       INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    visit_date      TEXT NOT NULL,
    visit_time      TEXT NOT NULL,
    visitor_name    TEXT,
    visit_type      TEXT DEFAULT 'دورية',
    status          TEXT DEFAULT 'مخططة',
    summary         TEXT,
    issues          TEXT,
    recommendations TEXT,
    actions         TEXT,
    next_visit_date TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS checklists (
    id          SERIAL PRIMARY KEY,
    client_id   INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    category    TEXT NOT NULL,
    item        TEXT NOT NULL,
    done        INTEGER DEFAULT 0,
    notes       TEXT,
    sort_order  INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS contracts (
    id              SERIAL PRIMARY KEY,
    contractor_name TEXT NOT NULL,
    description     TEXT,
    agreed_amount   NUMERIC DEFAULT 0,
    due_date        TEXT,
    status          TEXT DEFAULT 'جارٍ',
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS payments (
    id          SERIAL PRIMARY KEY,
    contract_id INTEGER NOT NULL REFERENCES contracts(id) ON DELETE CASCADE,
    amount      NUMERIC NOT NULL,
    pay_date    TEXT NOT NULL,
    notes       TEXT
);

CREATE TABLE IF NOT EXISTS users (
    id          SERIAL PRIMARY KEY,
    username    TEXT NOT NULL UNIQUE,
    full_name   TEXT NOT NULL,
    email       TEXT,
    password    TEXT NOT NULL,
    role        TEXT DEFAULT 'viewer',
    client_id   INTEGER REFERENCES clients(id) ON DELETE SET NULL,
    is_active   INTEGER DEFAULT 1,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    last_login  TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS client_permissions (
    id                   SERIAL PRIMARY KEY,
    user_id              INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    client_id            INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    can_view_matching    INTEGER DEFAULT 1,
    can_view_visits      INTEGER DEFAULT 0,
    can_view_checklists  INTEGER DEFAULT 0,
    can_view_devices     INTEGER DEFAULT 0,
    can_view_banks       INTEGER DEFAULT 0,
    can_submit_session   INTEGER DEFAULT 1,
    notes                TEXT,
    created_at           TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, client_id)
);

CREATE TABLE IF NOT EXISTS session_approvals (
    id              SERIAL PRIMARY KEY,
    session_id      INTEGER NOT NULL REFERENCES balance_sessions(id) ON DELETE CASCADE,
    submitted_by    INTEGER NOT NULL REFERENCES users(id),
    reviewed_by     INTEGER REFERENCES users(id),
    status          TEXT DEFAULT 'pending',
    client_notes    TEXT,
    reviewer_notes  TEXT,
    submitted_at    TIMESTAMPTZ DEFAULT NOW(),
    reviewed_at     TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS alerts (
    id          SERIAL PRIMARY KEY,
    client_id   INTEGER REFERENCES clients(id) ON DELETE SET NULL,
    level       TEXT DEFAULT 'info',
    message     TEXT,
    is_read     INTEGER DEFAULT 0,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
"""

CREATE_TABLES_SQLITE = """
CREATE TABLE IF NOT EXISTS clients (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    sector      TEXT,
    tax_number  TEXT,
    tax_type    TEXT DEFAULT 'VAT',
    risk        TEXT DEFAULT 'متوسط',
    status      TEXT DEFAULT 'قيد المراجعة',
    notes       TEXT,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS branches (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id   INTEGER NOT NULL,
    name        TEXT NOT NULL,
    address     TEXT,
    manager     TEXT,
    phone       TEXT,
    status      TEXT DEFAULT 'نشط',
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(client_id) REFERENCES clients(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS bank_accounts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id   INTEGER NOT NULL,
    branch_id   INTEGER,
    bank_name   TEXT,
    iban        TEXT,
    balance     REAL DEFAULT 0,
    status      TEXT DEFAULT 'نشط',
    FOREIGN KEY(client_id)  REFERENCES clients(id)  ON DELETE CASCADE,
    FOREIGN KEY(branch_id)  REFERENCES branches(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS network_devices (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id       INTEGER NOT NULL,
    branch_id       INTEGER,
    bank_account_id INTEGER,
    device_uid      TEXT,
    name            TEXT,
    type            TEXT DEFAULT 'POS',
    location        TEXT,
    status          TEXT DEFAULT 'نشط',
    FOREIGN KEY(client_id)       REFERENCES clients(id)       ON DELETE CASCADE,
    FOREIGN KEY(branch_id)       REFERENCES branches(id)      ON DELETE SET NULL,
    FOREIGN KEY(bank_account_id) REFERENCES bank_accounts(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS balance_sessions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id           INTEGER NOT NULL,
    branch_id           INTEGER,
    device_id           INTEGER,
    reviewer_name       TEXT,
    prev_date           TEXT NOT NULL,
    prev_time           TEXT NOT NULL,
    prev_net_count      INTEGER DEFAULT 0,
    prev_net_amount     REAL    DEFAULT 0,
    curr_date           TEXT NOT NULL,
    curr_time           TEXT NOT NULL,
    net_tx_count        INTEGER DEFAULT 0,
    net_tx_amount       REAL    DEFAULT 0,
    prog_inv_count      INTEGER DEFAULT 0,
    prog_inv_amount     REAL    DEFAULT 0,
    diff_count          INTEGER DEFAULT 0,
    diff_amount         REAL    DEFAULT 0,
    match_status        TEXT,
    notes               TEXT,
    created_at          TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(client_id)  REFERENCES clients(id)         ON DELETE CASCADE,
    FOREIGN KEY(branch_id)  REFERENCES branches(id)        ON DELETE SET NULL,
    FOREIGN KEY(device_id)  REFERENCES network_devices(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS visits (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id       INTEGER NOT NULL,
    visit_date      TEXT NOT NULL,
    visit_time      TEXT NOT NULL,
    visitor_name    TEXT,
    visit_type      TEXT DEFAULT 'دورية',
    status          TEXT DEFAULT 'مخططة',
    summary         TEXT,
    issues          TEXT,
    recommendations TEXT,
    actions         TEXT,
    next_visit_date TEXT,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(client_id) REFERENCES clients(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS checklists (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id   INTEGER NOT NULL,
    category    TEXT NOT NULL,
    item        TEXT NOT NULL,
    done        INTEGER DEFAULT 0,
    notes       TEXT,
    sort_order  INTEGER DEFAULT 0,
    FOREIGN KEY(client_id) REFERENCES clients(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS contracts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    contractor_name TEXT NOT NULL,
    description     TEXT,
    agreed_amount   REAL DEFAULT 0,
    due_date        TEXT,
    status          TEXT DEFAULT 'جارٍ',
    notes           TEXT,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS payments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id INTEGER NOT NULL,
    amount      REAL NOT NULL,
    pay_date    TEXT NOT NULL,
    notes       TEXT,
    FOREIGN KEY(contract_id) REFERENCES contracts(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    username    TEXT NOT NULL UNIQUE,
    full_name   TEXT NOT NULL,
    email       TEXT,
    password    TEXT NOT NULL,
    role        TEXT DEFAULT 'viewer',
    client_id   INTEGER,
    is_active   INTEGER DEFAULT 1,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
    last_login  TEXT,
    FOREIGN KEY(client_id) REFERENCES clients(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS client_permissions (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id              INTEGER NOT NULL,
    client_id            INTEGER NOT NULL,
    can_view_matching    INTEGER DEFAULT 1,
    can_view_visits      INTEGER DEFAULT 0,
    can_view_checklists  INTEGER DEFAULT 0,
    can_view_devices     INTEGER DEFAULT 0,
    can_view_banks       INTEGER DEFAULT 0,
    can_submit_session   INTEGER DEFAULT 1,
    notes                TEXT,
    created_at           TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, client_id),
    FOREIGN KEY(user_id)   REFERENCES users(id)   ON DELETE CASCADE,
    FOREIGN KEY(client_id) REFERENCES clients(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS session_approvals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      INTEGER NOT NULL,
    submitted_by    INTEGER NOT NULL,
    reviewed_by     INTEGER,
    status          TEXT DEFAULT 'pending',
    client_notes    TEXT,
    reviewer_notes  TEXT,
    submitted_at    TEXT DEFAULT CURRENT_TIMESTAMP,
    reviewed_at     TEXT,
    FOREIGN KEY(session_id)   REFERENCES balance_sessions(id) ON DELETE CASCADE,
    FOREIGN KEY(submitted_by) REFERENCES users(id),
    FOREIGN KEY(reviewed_by)  REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id   INTEGER,
    level       TEXT DEFAULT 'info',
    message     TEXT,
    is_read     INTEGER DEFAULT 0,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(client_id) REFERENCES clients(id) ON DELETE SET NULL
);
"""


def init():
    global _INIT_DONE
    if _INIT_DONE:
        return
    with _INIT_LOCK:
        if _INIT_DONE:
            return

        db = conn()
        try:
            skip_ddl = (os.environ.get("SKIP_DB_DDL", "").strip().lower() in ("1", "true", "yes"))
            skip_seed = (os.environ.get("SKIP_DB_SEED", "").strip().lower() in ("1", "true", "yes"))

            if not skip_ddl:
                if USE_POSTGRES:
                    db.executescript(CREATE_TABLES_PG)
                else:
                    db.executescript(CREATE_TABLES_SQLITE)

            if not skip_seed:
                if not db.execute("SELECT 1 FROM clients LIMIT 1").fetchone():
                    _seed(db)
            db.commit()
            _INIT_DONE = True
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


def _seed(db):
    clients = [
        ("شركة النخيل للتجارة", "تجارة",  "300123456700003", "VAT",          "متوسط", "قيد المراجعة"),
        ("مؤسسة الشروق",        "خدمات",  "300987654300003", "ضريبة الدخل",  "عالي",  "متأخرة"),
        ("مجموعة الأمل",        "صناعة",  "300456789100003", "زكاة",         "منخفض", "مكتملة"),
        ("شركة الأفق",          "عقارات", "300654321200003", "VAT",          "منخفض", "مكتملة"),
    ]
    client_ids = []
    for c in clients:
        cur = db.execute(
            "INSERT INTO clients(name,sector,tax_number,tax_type,risk,status) VALUES(%s,%s,%s,%s,%s,%s)" if USE_POSTGRES
            else "INSERT INTO clients(name,sector,tax_number,tax_type,risk,status) VALUES(?,?,?,?,?,?)", c
        )
        client_ids.append(cur.lastrowid)

    branch_ids = []
    branches = [
        (client_ids[0], "الفرع الرئيسي",  "الرياض — حي العليا",         "أحمد محمد",  "0501234567", "نشط"),
        (client_ids[0], "فرع الشمال",     "الرياض — حي النخيل",        "سعد علي",    "0507654321", "نشط"),
        (client_ids[1], "المقر الرئيسي",  "جدة — حي الروضة",           "محمد سالم",  "0509876543", "نشط"),
        (client_ids[2], "المصنع الرئيسي", "الدمام — المنطقة الصناعية", "خالد ناصر",  "0551234567", "نشط"),
    ]
    for b in branches:
        sql = ("INSERT INTO branches(client_id,name,address,manager,phone,status) VALUES(%s,%s,%s,%s,%s,%s)"
               if USE_POSTGRES else
               "INSERT INTO branches(client_id,name,address,manager,phone,status) VALUES(?,?,?,?,?,?)")
        cur = db.execute(sql, b)
        branch_ids.append(cur.lastrowid)

    bank_ids = []
    bank_accounts = [
        (client_ids[0], branch_ids[0], "بنك الراجحي",  "SA002000000012345678", 850234),
        (client_ids[0], branch_ids[1], "البنك الأهلي", "SA001000000098765432", 420100),
        (client_ids[1], branch_ids[2], "بنك سامبا",    "SA004000000045678912", 680000),
    ]
    for b in bank_accounts:
        sql = ("INSERT INTO bank_accounts(client_id,branch_id,bank_name,iban,balance) VALUES(%s,%s,%s,%s,%s)"
               if USE_POSTGRES else
               "INSERT INTO bank_accounts(client_id,branch_id,bank_name,iban,balance) VALUES(?,?,?,?,?)")
        cur = db.execute(sql, b)
        bank_ids.append(cur.lastrowid)

    devices = [
        (client_ids[0], branch_ids[0], bank_ids[0], "DEV-001", "كاشير رئيسي",  "POS", "المدخل الرئيسي", "نشط"),
        (client_ids[0], branch_ids[0], bank_ids[0], "DEV-002", "كاشير فرعي",   "POS", "القسم الثاني",   "نشط"),
        (client_ids[0], branch_ids[0], bank_ids[0], "DEV-003", "جهاز NFC",      "NFC", "المدخل",         "غير نشط"),
        (client_ids[0], branch_ids[1], bank_ids[1], "DEV-004", "كاشير الشمال", "POS", "المدخل",         "نشط"),
        (client_ids[1], branch_ids[2], bank_ids[2], "DEV-005", "POS الشروق",   "POS", "الاستقبال",      "نشط"),
    ]
    for d in devices:
        sql = ("INSERT INTO network_devices(client_id,branch_id,bank_account_id,device_uid,name,type,location,status) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)"
               if USE_POSTGRES else
               "INSERT INTO network_devices(client_id,branch_id,bank_account_id,device_uid,name,type,location,status) VALUES(?,?,?,?,?,?,?,?)")
        db.execute(sql, d)

    from werkzeug.security import generate_password_hash as _hash
    default_users = [
        ("admin",    "المدير العام",      "admin@system.local",    _hash("Admin@2026"),   "admin"),
        ("reviewer", "عبدالعزيز المراجع", "reviewer@system.local", _hash("Review@2026"), "reviewer"),
        ("viewer",   "مستخدم عرض",        "viewer@system.local",   _hash("View@2026"),   "viewer"),
    ]
    for u in default_users:
        try:
            sql = ("INSERT INTO users(username,full_name,email,password,role) VALUES(%s,%s,%s,%s,%s)"
                   if USE_POSTGRES else
                   "INSERT INTO users(username,full_name,email,password,role) VALUES(?,?,?,?,?)")
            db.execute(sql, u)
        except Exception:
            pass

    alerts_data = [
        (client_ids[1], "danger",  "مؤسسة الشروق — يوجد فرق في المطابقة"),
        (client_ids[0], "warning", "شركة النخيل — يوجد فرق في المطابقة"),
        (client_ids[0], "info",    "شركة النخيل — زيارة مجدولة"),
        (client_ids[2], "success", "مجموعة الأمل — المراجعة مكتملة"),
    ]
    for a in alerts_data:
        sql = ("INSERT INTO alerts(client_id,level,message) VALUES(%s,%s,%s)"
               if USE_POSTGRES else
               "INSERT INTO alerts(client_id,level,message) VALUES(?,?,?)")
        db.execute(sql, a)
