"""
מנועי תמלול ו-OCR - גרסה עצמאית לפרויקט המעבדה.

הערה חשובה: הגרסאות כאן הן פישוט של המנועים המקוריים במערכת הראשית
(routes/email_inbound.py, services/transcribe.py) - אותם מודלים, אותו פרומפט
בסיסי, אבל בלי הלוגיקה המורכבת של פיצול קבצים ארוכים לחלקים ועיבוד מקבילי
(שקיימת שם כדי לתמוך בקבצים ארוכים מאוד ולשפר דיוק בכתב יד).
ל-90% ממקרי הבדיקה זה מספיק; לקבצים ארוכים מאוד (מעל ~15 דקות) יתכן שתצטרך
להתאים את זה או להשתמש במנוע AlefBot במקום.
"""
import os
import io
import logging
import requests

log = logging.getLogger(__name__)

OCR_PROMPT_TEXT = """אתה סורק OCR מכני לכתב יד עברי בלבד (לא דפוס, לא כתב רש"י) - בד"כ תוכן תורני. אינך מבין עברית, רק מעתיק צורות אותיות כמו מצלמה.

כללים:
• העתק כל אות ומילה בדיוק כפי שמצוירת - גם אם לא נראית כמילה מוכרת, אסור להחליפה במילה "הגיונית"
• אסור: לתקן איות/ניקוד/דקדוק, להוסיף/להסיר מילים, לחזור על מילה שמופיעה פעם אחת
• מילה לא קריאה: כתוב [?] והמשך, אל תנחש
• שמור פיסוק ומספרים כפי שהם, בלי כותרות/הסברים
• עמוד שלם - העתק הכל, שורה אחר שורה מלמעלה למטה

התחל ישירות:"""


def run_engine(filepath, original_filename, engine, language, result_email, app_base_url):
    """נקודת הכניסה היחידה - נקראת מ-thread נפרד ב-app.py."""
    try:
        if engine == 'gemini_ocr':
            text = _gemini_ocr(filepath, original_filename)
            _send_result_email(result_email, original_filename, engine, text)
        elif engine == 'claude_ocr':
            text = _claude_ocr(filepath, original_filename)
            _send_result_email(result_email, original_filename, engine, text)
        elif engine == 'gpt4o_ocr':
            text = _gpt4o_ocr(filepath, original_filename)
            _send_result_email(result_email, original_filename, engine, text)
        elif engine == 'gemini':
            public_url = f"{app_base_url}/files/{os.path.basename(filepath)}"
            text = _gemini_transcribe(public_url, language)
            _send_result_email(result_email, original_filename, engine, text)
        elif engine == 'gemini_no_thinking':
            public_url = f"{app_base_url}/files/{os.path.basename(filepath)}"
            text = _gemini_transcribe(public_url, language, thinking_budget=0)
            _send_result_email(result_email, original_filename, engine, text)
        elif engine == 'gemini_focused_thinking':
            # חשיבה מוגבלת (budget קטן) שמכוונת בפרומפט רק לירידות שורה/פיסוק,
            # לא לתיקון מילים - וכוללת את סיכום החשיבה עצמו במייל, לצורך בדיקה.
            public_url = f"{app_base_url}/files/{os.path.basename(filepath)}"
            text, thoughts = _gemini_transcribe_focused(public_url, language)
            body = text or ''
            if thoughts:
                body += f"\n\n---\n🧠 סיכום החשיבה (thought summary):\n{thoughts}"
            _send_result_email(result_email, original_filename, engine, body)
        elif engine == 'gemini_default_thinking_debug':
            # בדיוק כמו מנוע 'gemini' הרגיל (אותו פרומפט, אותה חשיבה) - ההבדל היחיד:
            # חושף את סיכום החשיבה במייל, כדי להשוות מול gemini_focused_thinking.
            public_url = f"{app_base_url}/files/{os.path.basename(filepath)}"
            text, thoughts = _gemini_transcribe_default_with_thoughts(public_url, language)
            body = text or ''
            if thoughts:
                body += f"\n\n---\n🧠 סיכום החשיבה (thought summary):\n{thoughts}"
            _send_result_email(result_email, original_filename, engine, body)
        elif engine == 'alefbot':
            public_url = f"{app_base_url}/files/{os.path.basename(filepath)}"
            _alefbot_run(public_url, original_filename, language, result_email)
            return  # alefbot שולח מייל בעצמו בסוף ה-polling
        else:
            _send_result_email(result_email, original_filename, engine, None, error=f"מנוע לא מוכר: {engine}")
    except Exception as e:
        log.error(f"engine error ({engine}): {e}")
        _send_result_email(result_email, original_filename, engine, None, error=str(e))
    finally:
        try:
            if engine != 'alefbot' and os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass


# ---------------------------------------------------------------- OCR (כתב יד)

def _claude_ocr(filepath, original_filename):
    import anthropic
    import base64

    client = anthropic.Anthropic(api_key=os.environ.get('ANTHROPIC_API_KEY'))
    ext = os.path.splitext(original_filename or filepath)[1].lstrip('.').lower()

    def ocr_image_bytes(img_bytes, mime='image/png'):
        img_b64 = base64.standard_b64encode(img_bytes).decode('utf-8')
        for attempt in range(3):
            try:
                response = client.messages.create(
                    model='claude-opus-4-5',
                    max_tokens=4096,
                    messages=[{
                        'role': 'user',
                        'content': [
                            {'type': 'image', 'source': {'type': 'base64', 'media_type': mime, 'data': img_b64}},
                            {'type': 'text', 'text': OCR_PROMPT_TEXT}
                        ]
                    }]
                )
                return response.content[0].text.strip()
            except Exception as e:
                log.warning(f"Claude OCR attempt {attempt + 1} failed: {e}")
                if attempt < 2:
                    import time; time.sleep(8)
        return None

    if ext == 'pdf':
        import fitz
        all_pages = []
        doc = fitz.open(filepath)
        for i in range(len(doc)):
            pix = doc[i].get_pixmap(matrix=fitz.Matrix(4.0, 4.0))
            img_bytes = pix.tobytes('png')
            text = ocr_image_bytes(img_bytes)
            all_pages.append(f"--- עמוד {i + 1} ---\n{text or '[לא קריא]'}")
        doc.close()
        return '\n\n'.join(all_pages)

    mime_map = {'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png', 'gif': 'image/gif', 'webp': 'image/webp'}
    mime = mime_map.get(ext, 'image/jpeg')
    with open(filepath, 'rb') as f:
        img_bytes = f.read()
    return ocr_image_bytes(img_bytes, mime)


def _gpt4o_ocr(filepath, original_filename):
    import base64
    from openai import OpenAI

    client = OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))
    ext = os.path.splitext(original_filename or filepath)[1].lstrip('.').lower()

    def ocr_image_bytes(img_bytes, mime='image/png'):
        img_b64 = base64.b64encode(img_bytes).decode('utf-8')
        for attempt in range(3):
            try:
                response = client.chat.completions.create(
                    model='gpt-4o',
                    max_tokens=4096,
                    messages=[{
                        'role': 'user',
                        'content': [
                            {'type': 'text', 'text': OCR_PROMPT_TEXT},
                            {'type': 'image_url', 'image_url': {'url': f'data:{mime};base64,{img_b64}', 'detail': 'high'}}
                        ]
                    }]
                )
                return response.choices[0].message.content.strip()
            except Exception as e:
                log.warning(f"GPT-4o OCR attempt {attempt + 1} failed: {e}")
                if attempt < 2:
                    import time; time.sleep(8)
        return None

    if ext == 'pdf':
        import fitz
        all_pages = []
        doc = fitz.open(filepath)
        for i in range(len(doc)):
            pix = doc[i].get_pixmap(matrix=fitz.Matrix(4.0, 4.0))
            img_bytes = pix.tobytes('png')
            text = ocr_image_bytes(img_bytes)
            all_pages.append(f"--- עמוד {i + 1} ---\n{text or '[לא קריא]'}")
        doc.close()
        return '\n\n'.join(all_pages)

    mime_map = {'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png', 'gif': 'image/gif', 'webp': 'image/webp'}
    mime = mime_map.get(ext, 'image/jpeg')
    with open(filepath, 'rb') as f:
        img_bytes = f.read()
    return ocr_image_bytes(img_bytes, mime)


def _gemini_ocr(filepath, original_filename):
    """גרסה מפושטת (מעבר יחיד, לא מפוצל שורות/מקביל כמו במערכת הראשית)."""
    from google import genai
    from google.genai import types as gtypes

    api_key = os.environ.get('GOOGLE_API_KEY_OCR') or os.environ.get('GOOGLE_API_KEY')
    client = genai.Client(api_key=api_key)
    ext = os.path.splitext(original_filename or filepath)[1].lstrip('.').lower()

    def ocr_image_bytes(img_bytes, mime='image/png'):
        for attempt in range(3):
            try:
                response = client.models.generate_content(
                    model='gemini-3.5-flash',
                    contents=[
                        gtypes.Part.from_bytes(data=img_bytes, mime_type=mime),
                        OCR_PROMPT_TEXT,
                    ]
                )
                return (response.text or '').strip()
            except Exception as e:
                log.warning(f"Gemini OCR attempt {attempt + 1} failed: {e}")
                if attempt < 2:
                    import time; time.sleep(8)
        return None

    if ext == 'pdf':
        import fitz
        all_pages = []
        doc = fitz.open(filepath)
        for i in range(len(doc)):
            pix = doc[i].get_pixmap(matrix=fitz.Matrix(4.0, 4.0))
            img_bytes = pix.tobytes('png')
            text = ocr_image_bytes(img_bytes)
            all_pages.append(f"--- עמוד {i + 1} ---\n{text or '[לא קריא]'}")
        doc.close()
        return '\n\n'.join(all_pages)

    mime_map = {'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png', 'gif': 'image/gif', 'webp': 'image/webp'}
    mime = mime_map.get(ext, 'image/jpeg')
    with open(filepath, 'rb') as f:
        img_bytes = f.read()
    return ocr_image_bytes(img_bytes, mime)


# ---------------------------------------------------------------- תמלול אודיו/וידאו

def _gemini_transcribe(url, language='he', thinking_budget=None):
    """גרסה מפושטת (מעבר יחיד, בלי פיצול לחלקים - טוב לקבצים עד ~15 דקות).
    thinking_budget=None -> התנהגות ברירת מחדל של גמיני (חשיבה מלאה).
    thinking_budget=0    -> מכבה חשיבה לגמרי, בדיוק כמו שכבר עשינו בקלדן."""
    from google import genai
    from google.genai import types as gtypes

    api_key = os.environ.get('GOOGLE_API_KEY')
    client = genai.Client(api_key=api_key)

    r = requests.get(url, timeout=300)
    r.raise_for_status()
    audio_content = r.content
    log.info(f"Downloaded {len(audio_content)} bytes for Gemini transcription")

    url_lower = url.lower().split('?')[0]
    is_video = any(url_lower.endswith(ext) for ext in ('.mp4', '.mov', '.avi', '.mkv', '.3gp', '.m4v', '.webm'))
    mime_type = 'video/mp4' if is_video else 'audio/wav'

    input_lang_map = {'he': 'עברית', 'yi': 'יידיש', 'en': 'English', 'ar': 'ארמית'}
    input_lang_name = input_lang_map.get(language, 'עברית')

    prompt = f"""תמלל את קובץ השמע/וידאו הזה במדויק.
שפת הדובר: {input_lang_name}.
כתוב את התמלול בעברית, אלא אם הדובר מדבר אנגלית - אז כתוב באנגלית.
חשוב ביותר - תמלול מדויק ומלא:
- תמלל כל מילה ומילה ללא יוצא מן הכלל.
- אל תדלג על אף מילה, אפילו אם הקול לא ברור - כתוב את מה שנשמע גם אם אינך בטוח.
- אל תסכם, אל תקצר, אל תדלג על חלקים.
- שמור על מינוח תורני נכון, ארמית, ראשי תיבות וגרסאות.
- החזר רק את הטקסט המתומלל ללא הערות נוספות."""

    config = None
    if thinking_budget is not None:
        config = gtypes.GenerateContentConfig(
            thinking_config=gtypes.ThinkingConfig(thinking_budget=thinking_budget)
        )

    for attempt in range(3):
        try:
            kwargs = dict(
                model='gemini-3.5-flash',
                contents=[
                    gtypes.Part.from_bytes(data=audio_content, mime_type=mime_type),
                    prompt,
                ]
            )
            if config is not None:
                kwargs['config'] = config
            response = client.models.generate_content(**kwargs)
            try:
                thoughts = response.usage_metadata.thoughts_token_count or 0
                total = response.usage_metadata.total_token_count
                log.info(f"Gemini transcribe usage (thinking_budget={thinking_budget}): thoughts={thoughts}, total={total}")
            except Exception:
                pass
            return (response.text or '').strip()
        except Exception as e:
            log.warning(f"Gemini transcribe attempt {attempt + 1} failed: {e}")
            if attempt < 2:
                import time; time.sleep(8)
    return None


def _gemini_transcribe_default_with_thoughts(url, language='he'):
    """זהה לחלוטין למנוע 'gemini' הרגיל (אותו פרומפט, אותה חשיבה דינמית/ברירת מחדל) -
    ההבדל היחיד: include_thoughts=True, כדי לחשוף את סיכום החשיבה להשוואה מול
    _gemini_transcribe_focused. לא נועד לשימוש קבוע - רק להשוואה בבדיקה."""
    from google import genai
    from google.genai import types as gtypes

    api_key = os.environ.get('GOOGLE_API_KEY')
    client = genai.Client(api_key=api_key)

    r = requests.get(url, timeout=300)
    r.raise_for_status()
    audio_content = r.content

    url_lower = url.lower().split('?')[0]
    is_video = any(url_lower.endswith(ext) for ext in ('.mp4', '.mov', '.avi', '.mkv', '.3gp', '.m4v', '.webm'))
    mime_type = 'video/mp4' if is_video else 'audio/wav'

    input_lang_map = {'he': 'עברית', 'yi': 'יידיש', 'en': 'English', 'ar': 'ארמית'}
    input_lang_name = input_lang_map.get(language, 'עברית')

    # אותו פרומפט בדיוק כמו ב-_gemini_transcribe (המנוע הרגיל) - בלי שום שינוי.
    prompt = f"""תמלל את קובץ השמע/וידאו הזה במדויק.
שפת הדובר: {input_lang_name}.
כתוב את התמלול בעברית, אלא אם הדובר מדבר אנגלית - אז כתוב באנגלית.
חשוב ביותר - תמלול מדויק ומלא:
- תמלל כל מילה ומילה ללא יוצא מן הכלל.
- אל תדלג על אף מילה, אפילו אם הקול לא ברור - כתוב את מה שנשמע גם אם אינך בטוח.
- אל תסכם, אל תקצר, אל תדלג על חלקים.
- שמור על מינוח תורני נכון, ארמית, ראשי תיבות וגרסאות.
- החזר רק את הטקסט המתומלל ללא הערות נוספות."""

    # thinking_budget לא מוגדר בכלל (כמו במנוע הרגיל) - רק include_thoughts=True נוסף,
    # כדי לקבל חשיבה דינמית/ברירת מחדל בדיוק כמו קודם, ובנוסף לראות אותה.
    config = gtypes.GenerateContentConfig(
        thinking_config=gtypes.ThinkingConfig(include_thoughts=True)
    )

    for attempt in range(3):
        try:
            response = client.models.generate_content(
                model='gemini-3.5-flash',
                contents=[
                    gtypes.Part.from_bytes(data=audio_content, mime_type=mime_type),
                    prompt,
                ],
                config=config,
            )
            text_parts = []
            thought_parts = []
            for part in response.candidates[0].content.parts:
                if not getattr(part, 'text', None):
                    continue
                if getattr(part, 'thought', False):
                    thought_parts.append(part.text)
                else:
                    text_parts.append(part.text)
            return ('\n'.join(text_parts).strip() or None), ('\n'.join(thought_parts).strip() or None)
        except Exception as e:
            log.warning(f"Gemini default-with-thoughts transcribe attempt {attempt + 1} failed: {e}")
            if attempt < 2:
                import time; time.sleep(8)
    return None, None


def _gemini_transcribe_focused(url, language='he'):
    """נסיוני: budget חשיבה קטן ומכוון (לא כבוי לגמרי, לא חופשי) + הנחיה מפורשת
    בפרומפט לאן להפנות את החשיבה - רק החלטות על ירידת שורה/פיסוק, לא תיקון תוכן.
    מחזיר גם את סיכום החשיבה (include_thoughts=True) כדי לבדוק בפועל על מה הוא חושב."""
    from google import genai
    from google.genai import types as gtypes

    api_key = os.environ.get('GOOGLE_API_KEY')
    client = genai.Client(api_key=api_key)

    r = requests.get(url, timeout=300)
    r.raise_for_status()
    audio_content = r.content

    url_lower = url.lower().split('?')[0]
    is_video = any(url_lower.endswith(ext) for ext in ('.mp4', '.mov', '.avi', '.mkv', '.3gp', '.m4v', '.webm'))
    mime_type = 'video/mp4' if is_video else 'audio/wav'

    input_lang_map = {'he': 'עברית', 'yi': 'יידיש', 'en': 'English', 'ar': 'ארמית'}
    input_lang_name = input_lang_map.get(language, 'עברית')

    prompt = f"""תמלל את קובץ השמע/וידאו הזה במדויק.
שפת הדובר: {input_lang_name}.
כתוב את התמלול בעברית, אלא אם הדובר מדבר אנגלית - אז כתוב באנגלית.
חשוב ביותר - תמלול מדויק ומלא:
- תמלל כל מילה ומילה ללא יוצא מן הכלל, בדיוק כפי שנשמעת - אל תתקן, תשלים או "תנקה" ניסוח, גמגום או מילים חוזרות.
- אל תדלג על אף מילה, אפילו אם הקול לא ברור - כתוב את מה שנשמע גם אם אינך בטוח.
- אל תסכם, אל תקצר, אל תדלג על חלקים.
- שמור על מינוח תורני נכון, ארמית, ראשי תיבות וגרסאות.

השתמש בחשיבה שלך אך ורק כדי להחליט:
1. היכן לשים ירידת שורה (סוף משפט/מעבר נושא), כדי שהטקסט יהיה קריא.
2. פיסוק (פסיקים, נקודות, מירכאות) לפי תחביר המשפט הנשמע.
אל תשתמש בחשיבה כדי לשנות, לתקן או "לשפר" מילה כלשהי מעבר למה שנשמע בפועל.

החזר רק את הטקסט המתומלל ללא הערות נוספות."""

    config = gtypes.GenerateContentConfig(
        thinking_config=gtypes.ThinkingConfig(thinking_budget=512, include_thoughts=True)
    )

    for attempt in range(3):
        try:
            response = client.models.generate_content(
                model='gemini-3.5-flash',
                contents=[
                    gtypes.Part.from_bytes(data=audio_content, mime_type=mime_type),
                    prompt,
                ],
                config=config,
            )
            text_parts = []
            thought_parts = []
            for part in response.candidates[0].content.parts:
                if not getattr(part, 'text', None):
                    continue
                if getattr(part, 'thought', False):
                    thought_parts.append(part.text)
                else:
                    text_parts.append(part.text)
            return ('\n'.join(text_parts).strip() or None), ('\n'.join(thought_parts).strip() or None)
        except Exception as e:
            log.warning(f"Gemini focused-thinking transcribe attempt {attempt + 1} failed: {e}")
            if attempt < 2:
                import time; time.sleep(8)
    return None, None


def _alefbot_run(rec_url, original_filename, language, result_email):
    """שולח ל-AlefBot ואז מבצע polling עד שיש תוצאה, בלי לחכות על ה-request עצמו."""
    import uuid as _uuid
    api_key = os.environ.get('ALEFBOT_API_KEY')
    base_url = 'https://alef-bot.top/api/v1'
    call_id = f"lab_{_uuid.uuid4().hex[:8]}"

    try:
        r = requests.get(rec_url, timeout=300)
        r.raise_for_status()
        file_bytes = r.content
        log.info(f"Downloaded {len(file_bytes)} bytes for AlefBot")

        upload_res = requests.post(
            f'{base_url}/uploads',
            headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
            json={'filename': f'{call_id}.wav', 'content_type': 'audio/wav', 'size_bytes': len(file_bytes)},
            timeout=30
        )
        upload_res.raise_for_status()
        upload_id = upload_res.json().get('upload_id')

        put_res = requests.put(
            f'{base_url}/uploads/{upload_id}/binary',
            headers={'Authorization': f'Bearer {api_key}'}, data=file_bytes, timeout=300
        )
        put_res.raise_for_status()

        transcribe_res = requests.post(
            f'{base_url}/transcriptions',
            headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
            json={
                'upload_id': upload_id,
                'output_format': 'plain_text',
                'model_tier': 'standard',
                'translate_to_hebrew': (language == 'he'),
            },
            timeout=30
        )
        transcribe_res.raise_for_status()
        job_id = transcribe_res.json().get('job_id') or transcribe_res.json().get('id')
        log.info(f"AlefBot job created: {job_id}")

    except Exception as e:
        log.error(f"AlefBot submit error: {e}")
        _send_result_email(result_email, original_filename, 'alefbot', None, error=f"שליחה נכשלה: {e}")
        return

    if not job_id:
        _send_result_email(result_email, original_filename, 'alefbot', None, error="לא התקבל job_id מ-AlefBot")
        return

    import time
    for attempt in range(60):  # עד 30 דקות
        time.sleep(30)
        try:
            status_res = requests.get(
                f'{base_url}/transcriptions/{job_id}',
                headers={'Authorization': f'Bearer {api_key}'}, timeout=15
            )
            status = status_res.json().get('status', '')
            log.info(f"AlefBot poll {attempt + 1}/60: job={job_id} status={status}")
            if status == 'completed':
                art = requests.get(
                    f'{base_url}/transcriptions/{job_id}/artifact?format=txt',
                    headers={'Authorization': f'Bearer {api_key}'}, timeout=30
                )
                art.raise_for_status()
                _send_result_email(result_email, original_filename, 'alefbot', art.text.strip())
                return
            elif status in ('failed', 'cancelled'):
                _send_result_email(result_email, original_filename, 'alefbot', None, error=f"AlefBot job {status}")
                return
        except Exception as e:
            log.warning(f"AlefBot poll error: {e}")

    _send_result_email(result_email, original_filename, 'alefbot', None, error="timeout אחרי 30 דקות")


# ---------------------------------------------------------------- מייל

def _send_result_email(to, original_filename, engine, text, error=None):
    try:
        import sendgrid
        from sendgrid.helpers.mail import Mail, Email

        if error or not text:
            subject = f"🧪 מעבדה - שגיאה - {original_filename}"
            html = f"""<div dir='rtl' style='font-family:Arial'>
<h3>שגיאה בעיבוד {original_filename} (מנוע: {engine})</h3>
<p>{error or 'לא התקבלה תוצאה מהמנוע'}</p></div>"""
        else:
            subject = f"🧪 מעבדה - תוצאה - {original_filename} ({engine})"
            html = f"""<div dir='rtl' style='font-family:Arial;max-width:600px'>
<h3>תוצאה: {original_filename}</h3>
<p style='color:#6b7280'>מנוע: <b>{engine}</b> | תווים: <b>{len(text)}</b></p>
<div style='white-space:pre-wrap;background:#f0fdf4;border-right:4px solid #10b981;padding:16px;border-radius:8px;line-height:1.8'>{text}</div>
</div>"""

        sg = sendgrid.SendGridAPIClient(api_key=os.environ.get('SENDGRID_API_KEY'))
        message = Mail(
            from_email=Email(os.environ.get('SENDGRID_FROM_EMAIL', ''), 'מעבדת בדיקות'),
            to_emails=to,
            subject=subject,
            html_content=html
        )
        sg.send(message)
        log.info(f"result email sent to {to}")
    except Exception as e:
        log.error(f"email error: {e}")
