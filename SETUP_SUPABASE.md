# دليل الإعداد — Supabase + Vercel

## المشكلة التي تم حلها
كانت البيانات تُحذف لأن النظام كان يستخدم SQLite داخل `/tmp`
على Vercel، وهو مؤقت ويُعاد ضبطه عند كل نشر أو توقف الدالة.
**الحل:** نقل قاعدة البيانات إلى Supabase (PostgreSQL دائم).

---

## الخطوة 1 — إنشاء الجداول في Supabase

1. افتح مشروعك على https://supabase.com/dashboard
2. اذهب إلى **SQL Editor** → **New query**
3. انسخ محتوى ملف `supabase_schema.sql` والصقه
4. اضغط **Run**
5. تحقق أن 13 جدول ظهرت في القائمة

---

## الخطوة 2 — الحصول على كلمة مرور قاعدة البيانات

1. في Supabase Dashboard → **Project Settings** → **Database**
2. انسخ **Database Password** (ليست الـ anon key)
3. أو من **Connection string** انسخ كامل الـ URI

---

## الخطوة 3 — إعداد متغيرات البيئة على Vercel

في Vercel Dashboard → مشروعك → **Settings** → **Environment Variables**:

### الطريقة السهلة (متغير واحد):
```
DATABASE_URL = postgresql://postgres.gpnzbuobfpgjahmfxyjv:[DB_PASSWORD]@aws-0-me-central-1.pooler.supabase.com:6543/postgres
```

### أو بالتفصيل:
```
SUPABASE_HOST     = db.gpnzbuobfpgjahmfxyjv.supabase.co
SUPABASE_PASSWORD = [كلمة مرور قاعدة البيانات من الخطوة 2]
```

> ⚠️ استخدم **كلمة مرور قاعدة البيانات** من Project Settings → Database
> وليس الـ `service_role` أو `anon` keys

---

## الخطوة 4 — رفع الكود على Vercel

```bash
# إذا كنت تستخدم Git:
cd tax_web_v3
git init
git add .
git commit -m "migrate to Supabase PostgreSQL"
# ارفعه على GitHub ثم اربطه بـ Vercel

# أو مباشرة بـ Vercel CLI:
npx vercel --prod
```

---

## الخطوة 5 — التحقق

افتح `https://your-app.vercel.app/health`

يجب أن يظهر:
```json
{"status": "ok", "db": "postgresql"}
```

---

## بيانات الدخول الافتراضية

| المستخدم  | كلمة المرور | الدور     |
|-----------|-------------|-----------|
| admin     | Admin@2026  | مدير      |
| reviewer  | Review@2026 | مراجع     |
| viewer    | View@2026   | مشاهد     |

**غيّر كلمات المرور فور تسجيل الدخول الأول!**

---

## التشغيل المحلي (بدون Supabase)

```bash
pip install -r requirements.txt
python main.py
# يفتح على http://localhost:8000
# يستخدم SQLite محلياً تلقائياً
```
