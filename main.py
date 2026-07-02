"""
تشغيل محلي
للـ Vercel: يتم استخدام api/index.py تلقائياً
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# استيراد التطبيق من نقطة الدخول الموحدة
from api.index import app
import webbrowser, threading, time

def _open():
    time.sleep(1.2)
    webbrowser.open("http://localhost:8000")

if __name__ == "__main__":
    import uvicorn
    threading.Thread(target=_open, daemon=True).start()
    print("\n" + "="*55)
    print("  مراقب الامتثال الضريبي")
    print("  http://localhost:8000          ← النظام الرئيسي")
    print("  http://localhost:8000/matching ← نظام المطابقة")
    print("  http://localhost:8000/zatca    ← فاحص XML")
    print("  Ctrl+C للإيقاف")
    print("="*55 + "\n")
    uvicorn.run(app, host="127.0.0.1", port=8000, reload=False)
