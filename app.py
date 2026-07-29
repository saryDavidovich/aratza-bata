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
import sqlite3
import base64
from datetime import datetime

from flask import Flask, request, render_template_string, send_from_directory, redirect, url_for

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

app = Flask(__name__)

LAB_ACCESS_CODE = os.environ.get('LAB_ACCESS_CODE', '')
LAB_DEFAULT_EMAIL = os.environ.get('LAB_DEFAULT_EMAIL', '')
APP_BASE_URL = os.environ.get('APP_BASE_URL', '').rstrip('/')

UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lab_uploads')
os.makedirs(UPLOAD_DIR, exist_ok=True)

# ============================================================
# צ'אט יצירת תמונות עם Gemini (Nano Banana 2) - שיחה עם זיכרון,
# תיקונים חוזרים על אותה תמונה, ושליחת כל תמונה שנוצרת למייל.
# ============================================================
IMAGE_CHAT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'image_chat_files')
os.makedirs(IMAGE_CHAT_DIR, exist_ok=True)
IMAGE_CHAT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'image_chat.db')

GEMINI_IMAGE_MODEL = 'gemini-3.1-flash-image'  # Nano Banana 2 - GA, לא ה-preview שהופסק ב-25.6.2026


def _image_chat_db():
    conn = sqlite3.connect(IMAGE_CHAT_DB)
    conn.execute("""CREATE TABLE IF NOT EXISTS image_chats (
        id TEXT PRIMARY KEY,
        email TEXT,
        created_at TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS image_chat_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id TEXT,
        role TEXT,
        text TEXT,
        image_filename TEXT,
        created_at TEXT
    )""")
    conn.commit()
    return conn


def _save_image_chat_message(chat_id, role, text, image_bytes=None):
    """שומר הודעה בשיחה. אם יש image_bytes - שומר את התמונה כקובץ בדיסק
    ומחזיר את שם הקובץ, כדי שאפשר יהיה לטעון אותה שוב בפניות הבאות (זיכרון
    השיחה) ולהציג אותה בממשק."""
    image_filename = None
    if image_bytes:
        image_filename = f"{uuid.uuid4().hex}.png"
        with open(os.path.join(IMAGE_CHAT_DIR, image_filename), 'wb') as f:
            f.write(image_bytes)
    conn = _image_chat_db()
    conn.execute(
        "INSERT INTO image_chat_messages (chat_id, role, text, image_filename, created_at) VALUES (?, ?, ?, ?, ?)",
        (chat_id, role, text, image_filename, datetime.utcnow().isoformat())
    )
    conn.commit()
    conn.close()
    return image_filename


def _load_image_chat_messages(chat_id):
    conn = _image_chat_db()
    rows = conn.execute(
        "SELECT role, text, image_filename, created_at FROM image_chat_messages WHERE chat_id=? ORDER BY id",
        (chat_id,)
    ).fetchall()
    conn.close()
    return rows


def _build_gemini_contents(chat_id, new_prompt):
    """בונה את רשימת ה-contents לשיחה מרובת-תורות - כולל התמונות שכבר נוצרו
    בשיחה הזו (לא רק טקסט), כדי ש-Gemini 'יזכור' בדיוק על איזו תמונה מדובר
    כשמבקשים תיקון ('תשנה את הרקע לכחול' וכו')."""
    from google.genai import types as gtypes
    contents = []
    for role, text, image_filename, _ in _load_image_chat_messages(chat_id):
        parts = []
        if text:
            parts.append(gtypes.Part(text=text))
        if image_filename:
            with open(os.path.join(IMAGE_CHAT_DIR, image_filename), 'rb') as f:
                parts.append(gtypes.Part.from_bytes(data=f.read(), mime_type='image/png'))
        if parts:
            contents.append(gtypes.Content(role=('user' if role == 'user' else 'model'), parts=parts))
    contents.append(gtypes.Content(role='user', parts=[gtypes.Part(text=new_prompt)]))
    return contents


def _generate_image_turn(chat_id, prompt_text):
    from google import genai
    from google.genai import types as gtypes

    client = genai.Client(api_key=os.environ.get('GOOGLE_API_KEY'))
    contents = _build_gemini_contents(chat_id, prompt_text)
    response = client.models.generate_content(
        model=GEMINI_IMAGE_MODEL,
        contents=contents,
        config=gtypes.GenerateContentConfig(response_modalities=['IMAGE', 'TEXT']),
    )
    image_bytes = None
    reply_text = None
    for part in response.candidates[0].content.parts:
        if getattr(part, 'inline_data', None) is not None:
            image_bytes = part.inline_data.data
        elif getattr(part, 'text', None):
            reply_text = (reply_text or '') + part.text
    return image_bytes, reply_text


def _send_image_email(to, prompt, image_bytes):
    try:
        import sendgrid
        from sendgrid.helpers.mail import Mail, Email, Attachment, FileContent, FileName, FileType, Disposition

        html = f"""<div dir='rtl' style='font-family:Arial;max-width:600px'>
<h3>🎨 תמונה חדשה מצ'אט התמונות</h3>
<p style='color:#6b7280'>הבקשה: {prompt}</p>
<p>התמונה מצורפת לקובץ.</p>
</div>"""
        sg = sendgrid.SendGridAPIClient(api_key=os.environ.get('SENDGRID_API_KEY'))
        message = Mail(
            from_email=Email(os.environ.get('SENDGRID_FROM_EMAIL', ''), 'מעבדת בדיקות'),
            to_emails=to,
            subject="🎨 מעבדה - תמונה חדשה מ-Gemini",
            html_content=html,
        )
        encoded = base64.b64encode(image_bytes).decode()
        attachment = Attachment(
            FileContent(encoded), FileName('image.png'), FileType('image/png'), Disposition('attachment')
        )
        message.attachment = attachment
        sg.send(message)
        log.info(f"image email sent to {to}")
        return True
    except Exception as e:
        log.error(f"image email error: {e}")
        return False

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
<p><a href="/imagechat" style="color:#2563eb;text-decoration:none;font-weight:bold">🎨 צ'אט תמונות עם Gemini (יצירת תמונות + שליחה למייל) ←</a></p>
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
    <option value="gemini_ocr_10x_vote">🧪 קלדן כתב יד - Gemini (10 הצעות לשורה) + Claude מכריע</option>
    <option value="gemini_5x_gpt4o_vote">🧪 קלדן כתב יד - Gemini (5 הצעות, אפס חשיבה) + GPT-4o בוחר בלבד</option>
    <option value="gemini_pro_gpt5_vote">🧪 קלדן כתב יד - Gemini Pro (5 הצעות) + GPT-5 בוחר בלבד</option>
    <option value="gemini_ocr_preprocessed">🧪 קלדן כתב יד - עם ניקוי רעש + הגברת ניגודיות (לפני Gemini)</option>
    <option value="gemini_ocr_redrawn">🧪 קלדן כתב יד - ציור מחדש דטרמיניסטי (סף אדפטיבי, לא AI)</option>
    <option value="gemini_ocr_redrawn_preview">🔍 תצוגה מקדימה - התמונה המעובדת בלבד (בלי Gemini)</option>
    <option value="gemini_ocr_template_match">🧪 ניסוי - התאמת תבניות כתב-יד (בלי Gemini בכלל)</option>
    <option value="gemini_ocr_shape_match">🧪 ניסוי - Gemini משייך צורות בלבד (עם דף ייחוס, בלי הבנת תוכן)</option>
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


IMAGE_CHAT_HTML = """<!doctype html><html dir="rtl" lang="he"><head><meta charset="utf-8">
<title>צ'אט תמונות - Gemini</title>
<style>
body{font-family:Arial,sans-serif;max-width:720px;margin:30px auto;padding:0 16px;color:#111}
.msg-row{display:flex;margin:14px 0}
.msg-row.user{justify-content:flex-end}
.bubble{max-width:75%;padding:10px 14px;border-radius:10px;font-size:14px;line-height:1.6}
.bubble.user{background:#2563eb;color:#fff}
.bubble.model{background:#f0f0f0;color:#111}
.bubble img{max-width:100%;border-radius:8px;margin-top:8px;display:block}
form.chatform{display:flex;gap:8px;margin-top:20px;align-items:flex-end}
textarea{flex:1;padding:10px;border:1px solid #ccc;border-radius:6px;font-size:14px;font-family:Arial}
input[type=email]{width:100%;padding:10px;border:1px solid #ccc;border-radius:6px;font-size:14px;box-sizing:border-box}
button{padding:10px 20px;background:#2563eb;color:#fff;border:none;border-radius:6px;cursor:pointer}
button:hover{background:#1d4ed8}
.topbar{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}
a.newchat{font-size:13px;color:#2563eb;text-decoration:none}
.note{color:#6b7280;font-size:12px;margin-top:6px}
label{display:block;font-weight:bold;font-size:13px;margin-top:10px}
</style></head><body>
<div class="topbar">
  <h2>🎨 צ'אט תמונות - Gemini</h2>
  <a class="newchat" href="/imagechat?access_code={{ access_code }}">🆕 שיחה חדשה</a>
</div>
<p class="note">כל תמונה שנוצרת נשלחת גם למייל. אפשר לבקש תיקונים בהמשך השיחה - הוא זוכר על איזו תמונה מדובר.</p>

{% if error %}<div style="color:#991b1b;background:#fef2f2;padding:10px;border-radius:6px;margin:10px 0">{{ error }}</div>{% endif %}

{% for role, text, image_filename, created_at in messages %}
<div class="msg-row {{ 'user' if role == 'user' else '' }}">
  <div class="bubble {{ 'user' if role == 'user' else 'model' }}">
    {% if text %}{{ text }}{% endif %}
    {% if image_filename %}<img src="/imagechat/file/{{ image_filename }}">{% endif %}
  </div>
</div>
{% endfor %}

<form class="chatform" method="post" action="/imagechat/send" style="flex-direction:column;align-items:stretch">
  <input type="hidden" name="access_code" value="{{ access_code }}">
  <input type="hidden" name="chat_id" value="{{ chat_id }}">
  {% if not messages %}
  <label>שלח את התמונות למייל</label>
  <input type="email" name="email" value="{{ default_email }}" required>
  {% endif %}
  <label>{% if messages %}תיקון / המשך{% else %}מה ליצור{% endif %}</label>
  <div style="display:flex;gap:8px;align-items:flex-end">
    <textarea name="prompt" rows="2" required placeholder="{% if messages %}תאר תיקון או המשך...{% else %}תאר את התמונה שתרצה ליצור...{% endif %}"></textarea>
    <button type="submit">שלח</button>
  </div>
</form>
</body></html>"""


def _require_access(code):
    return LAB_ACCESS_CODE and code == LAB_ACCESS_CODE


ACCESS_GATE_HTML = """<!doctype html><html dir="rtl" lang="he"><head><meta charset="utf-8">
<title>צ'אט תמונות - Gemini</title>
<style>
body{font-family:Arial,sans-serif;max-width:400px;margin:80px auto;padding:0 16px;color:#111}
input{width:100%;padding:10px;margin-top:8px;box-sizing:border-box;border:1px solid #ccc;border-radius:6px;font-size:15px}
button{margin-top:16px;padding:10px 24px;background:#2563eb;color:#fff;border:none;border-radius:6px;cursor:pointer}
.err{color:#991b1b;background:#fef2f2;padding:10px;border-radius:6px;margin-top:12px}
</style></head><body>
<h2>🎨 צ'אט תמונות - Gemini</h2>
<form method="get" action="/imagechat">
  <label>קוד גישה</label>
  <input type="password" name="access_code" required autofocus>
  <button type="submit">כניסה</button>
</form>
{% if wrong %}<div class="err">קוד גישה שגוי</div>{% endif %}
</body></html>"""


@app.route('/imagechat', methods=['GET'])
def imagechat_page():
    access_code = request.args.get('access_code', '')
    if not access_code:
        return render_template_string(ACCESS_GATE_HTML, wrong=False)
    if not _require_access(access_code):
        return render_template_string(ACCESS_GATE_HTML, wrong=True), 403

    chat_id = request.args.get('chat_id', '') or uuid.uuid4().hex
    messages = _load_image_chat_messages(chat_id)
    return render_template_string(
        IMAGE_CHAT_HTML, messages=messages, chat_id=chat_id,
        access_code=access_code, default_email=LAB_DEFAULT_EMAIL, error=None
    )


@app.route('/imagechat/send', methods=['POST'])
def imagechat_send():
    access_code = request.form.get('access_code', '')
    if not _require_access(access_code):
        return "קוד גישה שגוי", 403

    chat_id = request.form.get('chat_id') or uuid.uuid4().hex
    prompt = (request.form.get('prompt') or '').strip()
    email = (request.form.get('email') or LAB_DEFAULT_EMAIL or '').strip()

    if not prompt:
        messages = _load_image_chat_messages(chat_id)
        return render_template_string(
            IMAGE_CHAT_HTML, messages=messages, chat_id=chat_id,
            access_code=access_code, default_email=LAB_DEFAULT_EMAIL, error="יש לכתוב בקשה"
        )

    conn = _image_chat_db()
    exists = conn.execute("SELECT email FROM image_chats WHERE id=?", (chat_id,)).fetchone()
    if not exists:
        conn.execute("INSERT INTO image_chats (id, email, created_at) VALUES (?, ?, ?)",
                     (chat_id, email, datetime.utcnow().isoformat()))
        conn.commit()
    else:
        email = exists[0] or email  # שיחה קיימת - משתמשים במייל שכבר נשמר לה, לא בשדה הטופס (שריק בהמשך שיחה)
    conn.close()

    _save_image_chat_message(chat_id, 'user', prompt)

    try:
        image_bytes, reply_text = _generate_image_turn(chat_id, prompt)
    except Exception as e:
        log.error(f"imagechat generate error: {e}")
        messages = _load_image_chat_messages(chat_id)
        return render_template_string(
            IMAGE_CHAT_HTML, messages=messages, chat_id=chat_id,
            access_code=access_code, default_email=LAB_DEFAULT_EMAIL, error=f"שגיאה ביצירת התמונה: {e}"
        )

    _save_image_chat_message(chat_id, 'model', reply_text, image_bytes)

    if image_bytes and email:
        threading.Thread(target=_send_image_email, args=(email, prompt, image_bytes), daemon=True).start()

    return redirect(f"/imagechat?access_code={access_code}&chat_id={chat_id}")


@app.route('/imagechat/file/<path:filename>')
def imagechat_file(filename):
    return send_from_directory(IMAGE_CHAT_DIR, filename)


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
