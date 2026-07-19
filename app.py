"""
מעבדת בדיקות - תמלול וקלדן (פרויקט עצמאי)
============================================
פרויקט Flask נפרד לחלוטין מהמערכת הראשית (phone-transcription).
אין כאן שום קשר לימות המשיח, ללקוחות, לחיוב, או לבסיס הנתונים של המערכת הראשית.

מטרה: דף ניהול פשוט להעלאת קבצי אודיו/וידאו/כתב-יד, הרצה דרך כמה מנועים
(Gemini / AlefBot / Claude / GPT-4o), וקבלת התוצאה למייל.

אפשר לפרוס את זה כשירות Railway נפרד משלו, לפרוס מחדש מתי שרוצים,
בלי שום השפעה על המערכת שהלקוחות משתמשים בה עכשיו.
"""
import os
import uuid
import logging
import threading

from flask import Flask, request, render_template_string, send_from_directory

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

app = Flask(__name__)

LAB_ACCESS_CODE = os.environ.get('LAB_ACCESS_CODE', '')
LAB_DEFAULT_EMAIL = os.environ.get('LAB_DEFAULT_EMAIL', '')
APP_BASE_URL = os.environ.get('APP_BASE_URL', '').rstrip('/')

UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lab_uploads')
os.makedirs(UPLOAD_DIR, exist_ok=True)

MAX_FILE_SIZE = 200 * 1024 * 1024  # 200MB

AUDIO_VIDEO_EXT = {'wav', 'mp3', 'm4a', 'ogg', 'opus', 'mp4', 'mov', 'avi', 'webm', '3gp', 'amr'}
IMAGE_PDF_EXT = {'jpg', 'jpeg', 'png', 'gif', 'webp', 'pdf'}

FORM_HTML = """<!doctype html><html dir="rtl" lang="he"><head><meta charset="utf-8">
<title>מעבדת בדיקות - תמלול / קלדן</title>
<style>
body{font-family:Arial,sans-serif;max-width:640px;margin:40px auto;padding:0 16px;color:#111}
label{display:block;margin-top:16px;font-weight:bold;font-size:14px}
input,select{width:100%;padding:10px;margin-top:4px;box-sizing:border-box;border:1px solid #ccc;border-radius:6px;font-size:15px}
button{margin-top:22px;padding:12px 28px;background:#2563eb;color:#fff;border:none;border-radius:6px;font-size:16px;cursor:pointer}
button:hover{background:#1d4ed8}
.msg{margin-top:20px;padding:14px;border-radius:8px;font-size:14px}
.ok{background:#f0fdf4;border:1px solid #10b981;color:#065f46}
.err{background:#fef2f2;border:1px solid #ef4444;color:#991b1b}
.note{color:#6b7280;font-size:13px;margin-top:8px}
</style></head><body>
<h2>🧪 מעבדת בדיקות - תמלול וקלדן</h2>
<p class="note">פרויקט עצמאי לניסויים בלבד. אין לזה שום קשר למערכת הלקוחות הפעילה.</p>
{% if message %}<div class="msg {{ 'ok' if ok else 'err' }}">{{ message }}</div>{% endif %}
<form method="post" action="/run" enctype="multipart/form-data">
  <label>קוד גישה</label>
  <input type="password" name="access_code" required>

  <label>קובץ (אודיו / וידאו / תמונת כתב יד / PDF)</label>
  <input type="file" name="file" required>

  <label>מנוע</label>
  <select name="engine">
    <option value="auto">אוטומטי (לפי סוג הקובץ)</option>
    <option value="gemini">תמלול - Gemini (מנוע רגיל)</option>
    <option value="gemini_no_thinking">תמלול - Gemini בלי חשיבה (thinking_budget=0)</option>
    <option value="gemini_no_thinking_postprocessed">✅ תמלול - Gemini בלי חשיבה + ירידות שורה בקוד (מומלץ - זול ואמין)</option>
    <option value="gemini_low_cost_formatted">🧪 תמלול - Gemini חיסכון (אפס חשיבה + הוראת פורמט בפרומפט)</option>
    <option value="gemini_min_thinking_formatted">🧪 תמלול - Gemini חיסכון חלקי (budget=128 + הוראת פורמט)</option>
    <option value="gemini_mid_thinking_formatted">🧪 תמלול - Gemini חיסכון חלקי (budget=256 + הוראת פורמט)</option>
    <option value="gemini_focused_thinking">🧪 תמלול - Gemini חשיבה ממוקדת (רק פיסוק/ירידות שורה, עם הצגת החשיבה)</option>
    <option value="gemini_default_thinking_debug">🧪 תמלול - Gemini כרגיל (אותו פרומפט/חשיבה כמו קודם, עם הצגת החשיבה)</option>
    <option value="alefbot">תמלול - AlefBot (מנוע פרימיום)</option>
    <option value="gemini_ocr">קלדן כתב יד - Gemini</option>
    <option value="claude_ocr">קלדן כתב יד - Claude</option>
    <option value="gpt4o_ocr">קלדן כתב יד - GPT-4o</option>
  </select>

  <label>שפה (רלוונטי לתמלול אודיו/וידאו בלבד)</label>
  <select name="language">
    <option value="he">עברית</option>
    <option value="yi">אידיש</option>
    <option value="en">אנגלית</option>
    <option value="ar">ארמית</option>
  </select>

  <label>שלח את התוצאה למייל</label>
  <input type="email" name="result_email" value="{{ default_email }}" required>

  <button type="submit">הרץ בדיקה</button>
</form>
</body></html>"""


@app.route('/', methods=['GET'])
@app.route('/lab', methods=['GET'])
def form():
    return render_template_string(FORM_HTML, message=None, ok=True, default_email=LAB_DEFAULT_EMAIL)


@app.route('/run', methods=['POST'])
def run():
    if not LAB_ACCESS_CODE or request.form.get('access_code') != LAB_ACCESS_CODE:
        return render_template_string(FORM_HTML, message="קוד גישה שגוי", ok=False, default_email=''), 403

    f = request.files.get('file')
    if not f or not f.filename:
        return render_template_string(FORM_HTML, message="לא נבחר קובץ", ok=False, default_email=''), 400

    result_email = (request.form.get('result_email') or '').strip()
    engine = request.form.get('engine', 'auto')
    language = request.form.get('language', 'he')

    ext = os.path.splitext(f.filename)[1].lstrip('.').lower()

    f.seek(0, os.SEEK_END)
    size = f.tell()
    f.seek(0)
    if size > MAX_FILE_SIZE:
        return render_template_string(
            FORM_HTML, message=f"הקובץ גדול מדי ({size // 1024 // 1024}MB, מקסימום 200MB)",
            ok=False, default_email=result_email
        ), 400

    if engine == 'auto':
        if ext in AUDIO_VIDEO_EXT:
            engine = 'gemini'
        elif ext in IMAGE_PDF_EXT:
            engine = 'gemini_ocr'
        else:
            engine = None
    if not engine:
        return render_template_string(
            FORM_HTML, message=f"סיומת קובץ לא מזוהה: .{ext}", ok=False, default_email=result_email
        ), 400

    token = uuid.uuid4().hex
    save_path = os.path.join(UPLOAD_DIR, f"{token}.{ext}")
    f.save(save_path)

    from engines import run_engine
    threading.Thread(
        target=run_engine,
        args=(save_path, f.filename, engine, language, result_email, APP_BASE_URL),
        daemon=True
    ).start()

    return render_template_string(
        FORM_HTML,
        message=f"התקבל! מריץ במנוע \"{engine}\" ברקע — התוצאה תישלח ל-{result_email} תוך כמה דקות.",
        ok=True,
        default_email=result_email,
    )


@app.route('/files/<path:filename>')
def serve_file(filename):
    # נחוץ כדי ש-Gemini/AlefBot (שירותים חיצוניים) יוכלו להוריד את הקובץ שהועלה
    return send_from_directory(UPLOAD_DIR, filename)


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
