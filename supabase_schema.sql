-- ══════════════════════════════════════════════════════════════════
-- مراقب الامتثال الضريبي — SQL Schema لـ Supabase
-- نسخة مصححة: تستخدم BIGINT بدل UUID لتوافق psycopg2
-- شغّل هذا في Supabase → SQL Editor
-- ══════════════════════════════════════════════════════════════════

-- حذف الجداول القديمة إن وُجدت (بالترتيب الصحيح)
DROP TABLE IF EXISTS session_approvals  CASCADE;
DROP TABLE IF EXISTS alerts             CASCADE;
DROP TABLE IF EXISTS client_permissions CASCADE;
DROP TABLE IF EXISTS payments           CASCADE;
DROP TABLE IF EXISTS contracts          CASCADE;
DROP TABLE IF EXISTS checklists         CASCADE;
DROP TABLE IF EXISTS visits             CASCADE;
DROP TABLE IF EXISTS balance_sessions   CASCADE;
DROP TABLE IF EXISTS network_devices    CASCADE;
DROP TABLE IF EXISTS bank_accounts      CASCADE;
DROP TABLE IF EXISTS branches           CASCADE;
DROP TABLE IF EXISTS users              CASCADE;
DROP TABLE IF EXISTS clients            CASCADE;

-- ══ العملاء ══════════════════════════════════════════════════════
CREATE TABLE clients (
    id          BIGSERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    sector      TEXT,
    tax_number  TEXT,
    tax_type    TEXT DEFAULT 'VAT',
    risk        TEXT DEFAULT 'متوسط',
    status      TEXT DEFAULT 'قيد المراجعة',
    notes       TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- ══ الفروع ═══════════════════════════════════════════════════════
CREATE TABLE branches (
    id          BIGSERIAL PRIMARY KEY,
    client_id   BIGINT NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    address     TEXT,
    manager     TEXT,
    phone       TEXT,
    status      TEXT DEFAULT 'نشط',
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- ══ الحسابات البنكية ════════════════════════════════════════════
CREATE TABLE bank_accounts (
    id          BIGSERIAL PRIMARY KEY,
    client_id   BIGINT NOT NULL REFERENCES clients(id)  ON DELETE CASCADE,
    branch_id   BIGINT          REFERENCES branches(id) ON DELETE SET NULL,
    bank_name   TEXT,
    iban        TEXT,
    balance     NUMERIC DEFAULT 0,
    status      TEXT DEFAULT 'نشط'
);

-- ══ أجهزة الشبكة ════════════════════════════════════════════════
CREATE TABLE network_devices (
    id              BIGSERIAL PRIMARY KEY,
    client_id       BIGINT NOT NULL REFERENCES clients(id)       ON DELETE CASCADE,
    branch_id       BIGINT          REFERENCES branches(id)      ON DELETE SET NULL,
    bank_account_id BIGINT          REFERENCES bank_accounts(id) ON DELETE SET NULL,
    device_uid      TEXT,
    name            TEXT,
    type            TEXT DEFAULT 'POS',
    location        TEXT,
    status          TEXT DEFAULT 'نشط'
);

-- ══ جلسات المطابقة ══════════════════════════════════════════════
CREATE TABLE balance_sessions (
    id                  BIGSERIAL PRIMARY KEY,
    client_id           BIGINT NOT NULL REFERENCES clients(id)        ON DELETE CASCADE,
    branch_id           BIGINT          REFERENCES branches(id)       ON DELETE SET NULL,
    device_id           BIGINT          REFERENCES network_devices(id) ON DELETE SET NULL,
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

-- ══ الزيارات ════════════════════════════════════════════════════
CREATE TABLE visits (
    id              BIGSERIAL PRIMARY KEY,
    client_id       BIGINT NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
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

-- ══ قوائم المراجعة ════════════════════════════════════════════════
CREATE TABLE checklists (
    id          BIGSERIAL PRIMARY KEY,
    client_id   BIGINT NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    category    TEXT NOT NULL,
    item        TEXT NOT NULL,
    done        INTEGER DEFAULT 0,
    notes       TEXT,
    sort_order  INTEGER DEFAULT 0
);

-- ══ العقود ══════════════════════════════════════════════════════
CREATE TABLE contracts (
    id              BIGSERIAL PRIMARY KEY,
    contractor_name TEXT NOT NULL,
    description     TEXT,
    agreed_amount   NUMERIC DEFAULT 0,
    due_date        TEXT,
    status          TEXT DEFAULT 'جارٍ',
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ══ المدفوعات ════════════════════════════════════════════════════
CREATE TABLE payments (
    id          BIGSERIAL PRIMARY KEY,
    contract_id BIGINT NOT NULL REFERENCES contracts(id) ON DELETE CASCADE,
    amount      NUMERIC NOT NULL,
    pay_date    TEXT NOT NULL,
    notes       TEXT
);

-- ══ المستخدمون ════════════════════════════════════════════════════
CREATE TABLE users (
    id          BIGSERIAL PRIMARY KEY,
    username    TEXT NOT NULL UNIQUE,
    full_name   TEXT NOT NULL,
    email       TEXT,
    password    TEXT NOT NULL,
    role        TEXT DEFAULT 'viewer',
    client_id   BIGINT REFERENCES clients(id) ON DELETE SET NULL,
    is_active   INTEGER DEFAULT 1,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    last_login  TIMESTAMPTZ
);

-- ══ صلاحيات العملاء ════════════════════════════════════════════
CREATE TABLE client_permissions (
    id                   BIGSERIAL PRIMARY KEY,
    user_id              BIGINT NOT NULL REFERENCES users(id)   ON DELETE CASCADE,
    client_id            BIGINT NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
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

-- ══ طلبات الموافقة ════════════════════════════════════════════════
CREATE TABLE session_approvals (
    id              BIGSERIAL PRIMARY KEY,
    session_id      BIGINT NOT NULL REFERENCES balance_sessions(id) ON DELETE CASCADE,
    submitted_by    BIGINT NOT NULL REFERENCES users(id),
    reviewed_by     BIGINT          REFERENCES users(id),
    status          TEXT DEFAULT 'pending',
    client_notes    TEXT,
    reviewer_notes  TEXT,
    submitted_at    TIMESTAMPTZ DEFAULT NOW(),
    reviewed_at     TIMESTAMPTZ
);

-- ══ التنبيهات ════════════════════════════════════════════════════
CREATE TABLE alerts (
    id          BIGSERIAL PRIMARY KEY,
    client_id   BIGINT REFERENCES clients(id) ON DELETE SET NULL,
    level       TEXT DEFAULT 'info',
    message     TEXT,
    is_read     INTEGER DEFAULT 0,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- ══ إيقاف RLS (التطبيق يدير الأمان بنفسه عبر JWT) ════════════
ALTER TABLE clients            DISABLE ROW LEVEL SECURITY;
ALTER TABLE branches           DISABLE ROW LEVEL SECURITY;
ALTER TABLE bank_accounts      DISABLE ROW LEVEL SECURITY;
ALTER TABLE network_devices    DISABLE ROW LEVEL SECURITY;
ALTER TABLE balance_sessions   DISABLE ROW LEVEL SECURITY;
ALTER TABLE visits             DISABLE ROW LEVEL SECURITY;
ALTER TABLE checklists         DISABLE ROW LEVEL SECURITY;
ALTER TABLE contracts          DISABLE ROW LEVEL SECURITY;
ALTER TABLE payments           DISABLE ROW LEVEL SECURITY;
ALTER TABLE users              DISABLE ROW LEVEL SECURITY;
ALTER TABLE client_permissions DISABLE ROW LEVEL SECURITY;
ALTER TABLE session_approvals  DISABLE ROW LEVEL SECURITY;
ALTER TABLE alerts             DISABLE ROW LEVEL SECURITY;

-- ══ تحقق — يجب أن يظهر 13 جدول ══════════════════════════════
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_type = 'BASE TABLE'
ORDER BY table_name;
