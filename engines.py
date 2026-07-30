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
import base64
import io
import re
import logging
import requests

log = logging.getLogger(__name__)


def _remove_single_letter_stutters(text):
    """מוחק 'מילים' שהן אות עברית בודדת ומבודדת (עם או בלי נקודה/פסיק/שלוש-נקודות
    אחריה) - כמעט תמיד שארית של גמגום/התחלה כפולה (למשל 'אומר ב בתחילת' ->
    'אומר בתחילת'). לא נוגע באות בודדת שאחריה גרש/אפוסטרוף (' או ׳) - כי אז
    האורך אחרי הסרת פיסוק הוא 2 תווים, לא 1, וזה מגן אוטומטית על 'ה' (שם ה')
    ועל מספור באותיות ('א', 'ב', 'ג' וכו') שנפוצים מאוד בתוכן הלכתי."""
    if not text:
        return text
    HEBREW_LETTERS = set('אבגדהוזחטיכלמנסעפצקרשתךםןףץ')
    tokens = text.split(' ')
    cleaned = []
    for tok in tokens:
        core = tok
        if core.endswith('...'):
            core = core[:-3]
        core = core.rstrip('.,')
        if len(core) == 1 and core in HEBREW_LETTERS:
            continue  # אות בודדת בלי גרש - גמגום, מוחקים את כל האסימון
        cleaned.append(tok)
    # ניקוי רווחים כפולים שנוצרו מהמחיקה
    return re.sub(r' {2,}', ' ', ' '.join(cleaned)).strip()


def _add_line_breaks(text, min_chars=80):
    """מפצל לשורות קריאות: מצרף משפטים רצופים (שמסתיימים ב. ! ?) לשורה אחת
    עד שמגיעים למינימום תווים (min_chars), ואז יורד שורה - כדי למנוע שורות
    קצרות מדי בודדות (כמו "נכון." או "כן?" בשורה נפרדת משלהן) שמרגישות
    מקוטעות. דטרמיניסטי לגמרי, עובד גם עם thinking_budget=0."""
    if not text:
        return text
    marked = re.sub(r'([.!?]["\'”]?)\s+', r'\1\n', text.strip())
    sentences = [s.strip() for s in marked.split('\n') if s.strip()]
    lines = []
    current = ''
    for s in sentences:
        current = f'{current} {s}'.strip() if current else s
        if len(current) >= min_chars:
            lines.append(current)
            current = ''
    if current:
        lines.append(current)
    return '\n'.join(lines)

OCR_PROMPT_TEXT = """אתה סורק OCR מכני לכתב יד עברי בלבד (לא דפוס, לא כתב רש"י) - בד"כ תוכן תורני. אינך מבין עברית, רק מעתיק צורות אותיות כמו מצלמה.

כללים:
• העתק כל אות ומילה בדיוק כפי שמצוירת - גם אם לא נראית כמילה מוכרת, אסור להחליפה במילה "הגיונית"
• אסור: לתקן איות/ניקוד/דקדוק, להוסיף/להסיר מילים, לחזור על מילה שמופיעה פעם אחת
• מילה לא קריאה: כתוב [?] והמשך, אל תנחש
• שמור פיסוק ומספרים כפי שהם, בלי כותרות/הסברים
• עמוד שלם - העתק הכל, שורה אחר שורה מלמעלה למטה

התחל ישירות:"""


def run_engine(filepath, original_filename, engine, language, result_email, app_base_url,
                ref_image_path=None, ref_text=None):
    """נקודת הכניסה היחידה - נקראת מ-thread נפרד ב-app.py.
    ref_image_path/ref_text רלוונטיים רק למנוע gemini_ocr_with_reference - דוגמת
    ייחוס (תמונת כתב יד + התמלול המדויק שהוכן לה בעבר) מאותו כותב."""
    try:
        if engine == 'gemini_ocr':
            text = _gemini_ocr(filepath, original_filename)
            _send_result_email(result_email, original_filename, engine, text)
        elif engine == 'gemini_ocr_with_reference':
            text = _gemini_ocr_with_reference(filepath, original_filename, ref_image_path, ref_text)
            _send_result_email(result_email, original_filename, engine, text)
        elif engine == 'claude_ocr':
            text = _claude_ocr(filepath, original_filename)
            _send_result_email(result_email, original_filename, engine, text)
        elif engine == 'gpt4o_ocr':
            text = _gpt4o_ocr(filepath, original_filename)
            _send_result_email(result_email, original_filename, engine, text)
        elif engine == 'gemini_ocr_10x_vote':
            text = _gemini_ocr_10x_vote(filepath, original_filename)
            _send_result_email(result_email, original_filename, engine, text)
        elif engine == 'gemini_5x_gpt4o_vote':
            text = _gemini_5x_gpt4o_vote(filepath, original_filename)
            _send_result_email(result_email, original_filename, engine, text)
        elif engine == 'gemini_pro_gpt5_vote':
            text = _gemini_pro_gpt5_vote(filepath, original_filename)
            _send_result_email(result_email, original_filename, engine, text)
        elif engine == 'gemini_ocr_preprocessed':
            text = _gemini_ocr_preprocessed(filepath, original_filename)
            _send_result_email(result_email, original_filename, engine, text)
        elif engine == 'gemini_ocr_redrawn':
            text = _gemini_ocr_redrawn(filepath, original_filename)
            _send_result_email(result_email, original_filename, engine, text)
        elif engine == 'gemini_ocr_redrawn_preview':
            image_bytes = _gemini_ocr_redrawn_preview(filepath, original_filename)
            _send_image_preview_email(result_email, original_filename, image_bytes)
        elif engine == 'gemini_ocr_template_match':
            image_bytes, text = _gemini_ocr_template_match(filepath, original_filename)
            _send_ocr_experiment_email(result_email, original_filename, engine, image_bytes, text)
        elif engine == 'gemini_ocr_shape_match':
            image_bytes, text = _gemini_ocr_shape_match(filepath, original_filename)
            _send_ocr_experiment_email(result_email, original_filename, engine, image_bytes, text or '[נכשל - בדוק לוגים]')
        elif engine == 'gemini':
            public_url = f"{app_base_url}/files/{os.path.basename(filepath)}"
            text = _gemini_transcribe(public_url, language)
            _send_result_email(result_email, original_filename, engine, text)
        elif engine == 'gemini_no_thinking':
            public_url = f"{app_base_url}/files/{os.path.basename(filepath)}"
            text = _gemini_transcribe(public_url, language, thinking_budget=0)
            _send_result_email(result_email, original_filename, engine, text)
        elif engine == 'gemini_no_thinking_postprocessed':
            # הפתרון המומלץ: thinking_budget=0 קבוע (אפס עלות חשיבה, לא תלוי
            # בהחלטה לא-ודאית של המודל) + ניקוי אותיות-בודדות (גמגום) + ירידות
            # שורה - שני השלבים דטרמיניסטיים בקוד שלנו, לא "חשיבה" של המודל.
            public_url = f"{app_base_url}/files/{os.path.basename(filepath)}"
            text = _gemini_transcribe(public_url, language, thinking_budget=0)
            text = _remove_single_letter_stutters(text)
            text = _add_line_breaks(text)
            _send_result_email(result_email, original_filename, engine, text)
        elif engine == 'gemini_low_cost_formatted':
            # thinking_budget=0 לגמרי (אפס טוקני חשיבה, אפס עלות חשיבה) -
            # הפורמט (ירידות שורה/פיסוק) מגיע רק מהוראה בפרומפט, לא מחשיבה.
            # הערה: בבדיקה נמצא שב-budget=0 גם הפורמט נעלם - ראה gemini_min_thinking_formatted.
            public_url = f"{app_base_url}/files/{os.path.basename(filepath)}"
            text = _gemini_transcribe_formatted_no_thinking(public_url, language, thinking_budget=0)
            _send_result_email(result_email, original_filename, engine, text)
        elif engine == 'gemini_min_thinking_formatted':
            # budget מינימלי (128, לעומת 512 בגרסה הממוקדת ו-0 בגרסת החיסכון המלא) -
            # בבדיקה: ב-128 גמיני בחר להשתמש ב-0 טוקני חשיבה בפועל (זו תקרה, לא הבטחה) -
            # והפורמט לא הופיע. ראה gemini_mid_thinking_formatted לערך ביניים.
            public_url = f"{app_base_url}/files/{os.path.basename(filepath)}"
            text = _gemini_transcribe_formatted_no_thinking(public_url, language, thinking_budget=128)
            _send_result_email(result_email, original_filename, engine, text)
        elif engine == 'gemini_mid_thinking_formatted':
            # המשך חיפוש בינארי: 128 לא הספיק (0 טוקני חשיבה בפועל, אין פורמט),
            # 512 הספיק (פורמט תקין) - בודקים את האמצע, 256, כדי לצמצם את הטווח.
            public_url = f"{app_base_url}/files/{os.path.basename(filepath)}"
            text = _gemini_transcribe_formatted_no_thinking(public_url, language, thinking_budget=256)
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
        try:
            if ref_image_path and os.path.exists(ref_image_path):
                os.remove(ref_image_path)
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


def _gemini_ocr_10x_vote(filepath, original_filename):
    """ניסוי: שלב א' - Gemini מפיק 10 השערות שונות לכל שורה בכתב היד (JSON מובנה).
    שלב ב' - Claude (מודל אחר לגמרי) מקבל את התמונה + רשימת המועמדים, ומכריע
    לכל שורה מה הנכון (או מתקן בעצמו אם אף מועמד לא מדויק). נותן ל-Claude גם
    את התמונה עצמה (לא רק את הטקסטים) כדי שההכרעה תהיה מבוססת ראייה, לא ניחוש עיוור."""
    from google import genai
    from google.genai import types as gtypes
    import anthropic
    import base64
    import json as _json

    gemini_key = os.environ.get('GOOGLE_API_KEY_OCR') or os.environ.get('GOOGLE_API_KEY')
    gemini_client = genai.Client(api_key=gemini_key)
    claude_client = anthropic.Anthropic(api_key=os.environ.get('ANTHROPIC_API_KEY'))

    CANDIDATES_PROMPT = OCR_PROMPT_TEXT + """

חלק את הכתב לשורות בדיוק כפי שהן מסודרות בתמונה. עבור כל שורה, ספק 10 השערות
שונות וסבירות לגבי הכיתוב - גם אם חלקן קרובות זו לזו (הבדל של אות אחת וכו'),
זה בסדר גמור, המטרה לכסות את טווח האפשרויות הסביר ולא להמציא 10 זהות.
החזר אך ורק JSON תקין (בלי שום טקסט לפני/אחרי, בלי ```), בפורמט הבא:
[{"line": 1, "candidates": ["אפשרות 1", "אפשרות 2", ..., "אפשרות 10"]}, ...]"""

    JUDGE_PROMPT = """אתה מקבל תמונה של כתב יד עברי (בד"כ תוכן תורני), ורשימת
מועמדים לכל שורה שהופקו ע"י מודל OCR אחר (Gemini). תפקידך: להסתכל בעצמך
בתמונה, ולבחור לכל שורה את המועמד המדויק ביותר מהרשימה - או לתקן בעצמך אם
אתה בטוח שאף מועמד לא מדויק. החזר את הטקסט הסופי המלא בלבד, שורה אחר שורה,
בלי מספור שורות ובלי הסברים נוספים.

רשימת המועמדים (JSON):
"""

    def get_candidates(img_bytes, mime):
        for attempt in range(3):
            try:
                response = gemini_client.models.generate_content(
                    model='gemini-3.5-flash',
                    contents=[gtypes.Part.from_bytes(data=img_bytes, mime_type=mime), CANDIDATES_PROMPT],
                    config=gtypes.GenerateContentConfig(response_mime_type='application/json'),
                )
                raw = (response.text or '').strip()
                return _json.loads(raw)
            except Exception as e:
                log.warning(f"10x-vote candidates attempt {attempt + 1} failed: {e}")
                if attempt < 2:
                    import time; time.sleep(8)
        return None

    def judge(img_bytes, mime, candidates_json):
        img_b64 = base64.standard_b64encode(img_bytes).decode('utf-8')
        candidates_text = _json.dumps(candidates_json, ensure_ascii=False, indent=1)
        for attempt in range(3):
            try:
                response = claude_client.messages.create(
                    model='claude-opus-4-5',
                    max_tokens=4096,
                    messages=[{
                        'role': 'user',
                        'content': [
                            {'type': 'image', 'source': {'type': 'base64', 'media_type': mime, 'data': img_b64}},
                            {'type': 'text', 'text': JUDGE_PROMPT + candidates_text}
                        ]
                    }]
                )
                return response.content[0].text.strip()
            except Exception as e:
                log.warning(f"10x-vote judge attempt {attempt + 1} failed: {e}")
                if attempt < 2:
                    import time; time.sleep(8)
        return None

    def process_page(img_bytes, mime):
        candidates_json = get_candidates(img_bytes, mime)
        if not candidates_json:
            return '[שלב 1 (Gemini) נכשל - לא הופקו מועמדים]'
        final_text = judge(img_bytes, mime, candidates_json)
        if not final_text:
            return '[שלב 2 (Claude) נכשל להכריע]'
        return final_text

    ext = os.path.splitext(original_filename or filepath)[1].lstrip('.').lower()
    if ext == 'pdf':
        import fitz
        all_pages = []
        doc = fitz.open(filepath)
        for i in range(len(doc)):
            pix = doc[i].get_pixmap(matrix=fitz.Matrix(4.0, 4.0))
            img_bytes = pix.tobytes('png')
            text = process_page(img_bytes, 'image/png')
            all_pages.append(f"--- עמוד {i + 1} ---\n{text}")
        doc.close()
        return '\n\n'.join(all_pages)

    mime_map = {'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png', 'gif': 'image/gif', 'webp': 'image/webp'}
    mime = mime_map.get(ext, 'image/jpeg')
    with open(filepath, 'rb') as f:
        img_bytes = f.read()
    return process_page(img_bytes, mime)


def _gemini_5x_gpt4o_vote(filepath, original_filename):
    """ניסוי גרסה 3: שלב א' - Gemini מפיק 5 השערות לכל שורה, thinking_budget=0.
    שלב ב' - GPT-4o מקבל את התמונה + רשימת המועמדים, ומכריע לפי התאמה חזותית
    לתמונה (לא לפי מה ש"מסתדר" לו תוכנית/דקדוקית) - אבל חייב לבחור מילה-במילה
    מתוך מה שגימיני כתב, לא להמציא ולא לנסח מחדש. גרסה 1 (Claude+תמונה, בלי
    הגבלה לבחור-רק-מהרשימה) המציאה מילים. גרסה 2 (GPT-4o, בלי תמונה כלל) לא
    יכולה בכלל לשפוט קרבה למקור כי אין לה מקור להשוות אליו. זו הגרסה שמאזנת
    בין השתיים: יש תמונה לצורך השוואה חזותית, אבל אין חופש להמציא.
    המייל כולל את שני השלבים בנפרד לצורך ביקורת."""
    from google import genai
    from google.genai import types as gtypes
    from openai import OpenAI
    import base64
    import json as _json

    gemini_key = os.environ.get('GOOGLE_API_KEY_OCR') or os.environ.get('GOOGLE_API_KEY')
    gemini_client = genai.Client(api_key=gemini_key)
    openai_client = OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))

    CANDIDATES_PROMPT = OCR_PROMPT_TEXT + """

חלק את הכתב לשורות בדיוק כפי שהן מסודרות בתמונה. עבור כל שורה, ספק 5 השערות
שונות וסבירות לגבי הכיתוב - גם אם חלקן קרובות זו לזו (הבדל של אות אחת וכו'),
זה בסדר גמור, המטרה לכסות את טווח האפשרויות הסביר ולא להמציא 5 זהות.
החזר אך ורק JSON תקין (בלי שום טקסט לפני/אחרי, בלי ```), בפורמט הבא:
[{"line": 1, "candidates": ["אפשרות 1", "אפשרות 2", ..., "אפשרות 5"]}, ...]"""

    JUDGE_PROMPT = """קיבלת תמונה של כתב יד עברי, ורשימת מועמדים לכל שורה שהופקו
ע"י מודל OCR אחר (Gemini). תפקידך: להשוות כל מועמד מול הכיתוב בפועל בתמונה,
ולבחור לכל שורה את המועמד שהכי תואם חזותית לאותיות שכתובות שם - לא את המועמד
שהכי "מסתדר" לך מבחינת תוכן, דקדוק או הקשר. אם מועמד נראה לך משונה או לא
הגיוני מבחינה תוכנית, אבל הוא הכי קרוב לצורת האותיות בפועל בתמונה - תבחר בו,
ולא במועמד ה"יפה" יותר שלא תואם את מה שבאמת כתוב.

חשוב מאוד: אתה מבצע אימות חזותי מול הרשימה, לא תמלול חופשי משלך. אסור לך
להמציא מילים שלא מופיעות ברשימת המועמדים, אסור לנסח מחדש, אסור לשלב חלקים
ממועמדים שונים לתוך משפט חדש - גם אם נראה לך שאתה "רואה" בתמונה משהו אחר
לגמרי. תמיד תבחר מועמד קיים מהרשימה, מילה במילה בדיוק כפי שהוא מופיע. אם כל
המועמדים באותה שורה נראים גרועים במידה שווה - עדיין תבחר את הפחות גרוע
מביניהם, בלי לשנות אף מילה בו. החזר את הטקסט הסופי בלבד, שורה אחר שורה,
בלי מספור ובלי הסברים.

רשימת המועמדים (JSON):
"""

    def get_candidates(img_bytes, mime):
        for attempt in range(3):
            try:
                response = gemini_client.models.generate_content(
                    model='gemini-3.5-flash',
                    contents=[gtypes.Part.from_bytes(data=img_bytes, mime_type=mime), CANDIDATES_PROMPT],
                    config=gtypes.GenerateContentConfig(
                        response_mime_type='application/json',
                        thinking_config=gtypes.ThinkingConfig(thinking_budget=0),
                    ),
                )
                raw = (response.text or '').strip()
                return _json.loads(raw)
            except Exception as e:
                log.warning(f"5x-vote candidates attempt {attempt + 1} failed: {e}")
                if attempt < 2:
                    import time; time.sleep(8)
        return None

    def judge(img_bytes, mime, candidates_json):
        img_b64 = base64.b64encode(img_bytes).decode('utf-8')
        candidates_text = _json.dumps(candidates_json, ensure_ascii=False, indent=1)
        for attempt in range(3):
            try:
                response = openai_client.chat.completions.create(
                    model='gpt-4o',
                    max_tokens=4096,
                    messages=[{
                        'role': 'user',
                        'content': [
                            {'type': 'text', 'text': JUDGE_PROMPT + candidates_text},
                            {'type': 'image_url', 'image_url': {'url': f'data:{mime};base64,{img_b64}', 'detail': 'high'}}
                        ]
                    }]
                )
                return response.choices[0].message.content.strip()
            except Exception as e:
                log.warning(f"5x-vote judge attempt {attempt + 1} failed: {e}")
                if attempt < 2:
                    import time; time.sleep(8)
        return None

    def process_page(img_bytes, mime):
        candidates_json = get_candidates(img_bytes, mime)
        if not candidates_json:
            return None, '[שלב 1 (Gemini) נכשל - לא הופקו מועמדים]'
        candidates_str = _json.dumps(candidates_json, ensure_ascii=False, indent=1)
        final_text = judge(img_bytes, mime, candidates_json)
        if not final_text:
            final_text = '[שלב 2 (GPT-4o) נכשל להכריע]'
        return candidates_str, final_text

    ext = os.path.splitext(original_filename or filepath)[1].lstrip('.').lower()
    pages_candidates = []
    pages_final = []

    if ext == 'pdf':
        import fitz
        doc = fitz.open(filepath)
        for i in range(len(doc)):
            pix = doc[i].get_pixmap(matrix=fitz.Matrix(4.0, 4.0))
            img_bytes = pix.tobytes('png')
            cand, final = process_page(img_bytes, 'image/png')
            pages_candidates.append(f"--- עמוד {i + 1} ---\n{cand}")
            pages_final.append(f"--- עמוד {i + 1} ---\n{final}")
        doc.close()
    else:
        mime_map = {'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png', 'gif': 'image/gif', 'webp': 'image/webp'}
        mime = mime_map.get(ext, 'image/jpeg')
        with open(filepath, 'rb') as f:
            img_bytes = f.read()
        cand, final = process_page(img_bytes, mime)
        pages_candidates.append(cand)
        pages_final.append(final)

    body = (
        "✅ תוצאה סופית (GPT-4o בחר מתוך המועמדים של Gemini):\n"
        + '\n\n'.join(pages_final)
        + "\n\n" + "=" * 40 + "\n\n"
        + "🔤 המועמדים הגולמיים מ-Gemini (5 לשורה, אפס חשיבה):\n"
        + '\n\n'.join(pages_candidates)
    )
    return body


def _gemini_pro_gpt5_vote(filepath, original_filename):
    """זהה בעיקרו לניסוי gemini_5x_gpt4o_vote, עם שני שינויים: מודל המועמדים
    הוא gemini-3.1-pro-preview במקום gemini-3.5-flash (Pro חזק משמעותית מ-Flash,
    ומצוין ספציפית בעברית/כתבי יד), ומודל השופט הוא gpt-5 במקום gpt-4o. שאר
    ההיגיון (אימות חזותי מול רשימה סגורה, אסור להמציא) זהה. הבדל טכני חשוב:
    ל-Pro Preview, בניגוד ל-Flash, אי אפשר לכבות חשיבה (thinking_budget=0
    נדחה עם שגיאה - "this model only works in thinking mode") - אז כאן
    משאירים לו חשיבה דינמית/ברירת מחדל, מה שגם עולה יותר וגם איטי יותר
    מהגרסה עם Flash."""
    from google import genai
    from google.genai import types as gtypes
    from openai import OpenAI
    import base64
    import json as _json

    gemini_key = os.environ.get('GOOGLE_API_KEY_OCR') or os.environ.get('GOOGLE_API_KEY')
    gemini_client = genai.Client(api_key=gemini_key)
    openai_client = OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))

    CANDIDATES_PROMPT = OCR_PROMPT_TEXT + """

חלק את הכתב לשורות בדיוק כפי שהן מסודרות בתמונה. עבור כל שורה, ספק 5 השערות
שונות וסבירות לגבי הכיתוב - גם אם חלקן קרובות זו לזו (הבדל של אות אחת וכו'),
זה בסדר גמור, המטרה לכסות את טווח האפשרויות הסביר ולא להמציא 5 זהות.
החזר אך ורק JSON תקין (בלי שום טקסט לפני/אחרי, בלי ```), בפורמט הבא:
[{"line": 1, "candidates": ["אפשרות 1", "אפשרות 2", ..., "אפשרות 5"]}, ...]"""

    JUDGE_PROMPT = """קיבלת תמונה של כתב יד עברי, ורשימת מועמדים לכל שורה שהופקו
ע"י מודל OCR אחר (Gemini). תפקידך: להשוות כל מועמד מול הכיתוב בפועל בתמונה,
ולבחור לכל שורה את המועמד שהכי תואם חזותית לאותיות שכתובות שם - לא את המועמד
שהכי "מסתדר" לך מבחינת תוכן, דקדוק או הקשר. אם מועמד נראה לך משונה או לא
הגיוני מבחינה תוכנית, אבל הוא הכי קרוב לצורת האותיות בפועל בתמונה - תבחר בו,
ולא במועמד ה"יפה" יותר שלא תואם את מה שבאמת כתוב.

חשוב מאוד: אתה מבצע אימות חזותי מול הרשימה, לא תמלול חופשי משלך. אסור לך
להמציא מילים שלא מופיעות ברשימת המועמדים, אסור לנסח מחדש, אסור לשלב חלקים
ממועמדים שונים לתוך משפט חדש - גם אם נראה לך שאתה "רואה" בתמונה משהו אחר
לגמרי. תמיד תבחר מועמד קיים מהרשימה, מילה במילה בדיוק כפי שהוא מופיע. אם כל
המועמדים באותה שורה נראים גרועים במידה שווה - עדיין תבחר את הפחות גרוע
מביניהם, בלי לשנות אף מילה בו. החזר את הטקסט הסופי בלבד, שורה אחר שורה,
בלי מספור ובלי הסברים.

רשימת המועמדים (JSON):
"""

    def get_candidates(img_bytes, mime):
        for attempt in range(3):
            try:
                response = gemini_client.models.generate_content(
                    model='gemini-3.1-pro-preview',
                    contents=[gtypes.Part.from_bytes(data=img_bytes, mime_type=mime), CANDIDATES_PROMPT],
                    config=gtypes.GenerateContentConfig(
                        response_mime_type='application/json',
                        # Pro Preview, בניגוד ל-Flash, לא מאפשר thinking_budget=0
                        # ("Budget 0 is invalid. This model only works in thinking mode")
                        # - משאירים חשיבה דינמית/ברירת מחדל, לא קובעים תקציב בכלל
                    ),
                )
                raw = (response.text or '').strip()
                return _json.loads(raw)
            except Exception as e:
                log.warning(f"pro-gpt5-vote candidates attempt {attempt + 1} failed: {e}")
                if attempt < 2:
                    import time; time.sleep(8)
        return None

    def judge(img_bytes, mime, candidates_json):
        img_b64 = base64.b64encode(img_bytes).decode('utf-8')
        candidates_text = _json.dumps(candidates_json, ensure_ascii=False, indent=1)
        for attempt in range(3):
            try:
                response = openai_client.chat.completions.create(
                    model='gpt-5',
                    max_tokens=4096,
                    messages=[{
                        'role': 'user',
                        'content': [
                            {'type': 'text', 'text': JUDGE_PROMPT + candidates_text},
                            {'type': 'image_url', 'image_url': {'url': f'data:{mime};base64,{img_b64}', 'detail': 'high'}}
                        ]
                    }]
                )
                return response.choices[0].message.content.strip()
            except Exception as e:
                log.warning(f"pro-gpt5-vote judge attempt {attempt + 1} failed: {e}")
                if attempt < 2:
                    import time; time.sleep(8)
        return None

    def process_page(img_bytes, mime):
        candidates_json = get_candidates(img_bytes, mime)
        if not candidates_json:
            return None, '[שלב 1 (Gemini Pro) נכשל - לא הופקו מועמדים]'
        candidates_str = _json.dumps(candidates_json, ensure_ascii=False, indent=1)
        final_text = judge(img_bytes, mime, candidates_json)
        if not final_text:
            final_text = '[שלב 2 (GPT-5) נכשל להכריע]'
        return candidates_str, final_text

    ext = os.path.splitext(original_filename or filepath)[1].lstrip('.').lower()
    pages_candidates = []
    pages_final = []

    if ext == 'pdf':
        import fitz
        doc = fitz.open(filepath)
        for i in range(len(doc)):
            pix = doc[i].get_pixmap(matrix=fitz.Matrix(4.0, 4.0))
            img_bytes = pix.tobytes('png')
            cand, final = process_page(img_bytes, 'image/png')
            pages_candidates.append(f"--- עמוד {i + 1} ---\n{cand}")
            pages_final.append(f"--- עמוד {i + 1} ---\n{final}")
        doc.close()
    else:
        mime_map = {'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png', 'gif': 'image/gif', 'webp': 'image/webp'}
        mime = mime_map.get(ext, 'image/jpeg')
        with open(filepath, 'rb') as f:
            img_bytes = f.read()
        cand, final = process_page(img_bytes, mime)
        pages_candidates.append(cand)
        pages_final.append(final)

    body = (
        "✅ תוצאה סופית (GPT-5 בחר מתוך המועמדים של Gemini Pro):\n"
        + '\n\n'.join(pages_final)
        + "\n\n" + "=" * 40 + "\n\n"
        + "🔤 המועמדים הגולמיים מ-Gemini Pro (5 לשורה, אפס חשיבה):\n"
        + '\n\n'.join(pages_candidates)
    )
    return body


def _enhance_handwriting_image(img_bytes):
    """עיבוד תמונה קלאסי לפני OCR - לא גנרטיבי, לא 'מצייר מחדש' שום דבר.
    שני שלבים בלבד: (1) ניקוי רעש עדין (מסיר גרעיניות/כתמים, לא נוגע בצורת
    הקווים עצמה), (2) הגברת ניגודיות אדפטיבית (CLAHE) שמבליטה דיו מול נייר
    גם בתאורה לא אחידה. בכוונה *לא* עושה 'שחזור'/'החדדה גנרטיבית' של
    האותיות - זה בדיוק מה שעלול להמציא צורות אותיות שלא היו במקור."""
    import cv2
    import numpy as np

    arr = np.frombuffer(img_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return img_bytes  # לא הצליח לפענח את התמונה - מחזירים את המקור בלי נגיעה

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    denoised = cv2.fastNlMeansDenoising(gray, h=10, templateWindowSize=7, searchWindowSize=21)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(denoised)

    success, encoded = cv2.imencode('.png', enhanced)
    return encoded.tobytes() if success else img_bytes


def _redraw_handwriting_deterministic(img_bytes):
    """'ציור מחדש' דטרמיניסטי לגמרי - לא AI, לא מנחש שום צורה. כל פיקסל
    מוכרע בנפרד לפי כלל מתמטי קבוע וזהה לכולם (ניגודיות מול הפיקסלים
    בסביבתו המקומית) - לא לפי "הבנה" של איך אמורה להיראות אות. זו בדיוק
    ההבחנה מציור-מחדש גנרטיבי: כאן אין שום "ידע קודם" על צורת אותיות
    שמופעל - התוצאה נקבעת אך ורק מהניגודיות הפיזית שכבר הייתה בתמונה.

    שלב 0: הגדלה פי 4 (400%) עם אינטרפולציה חלקה (cubic) *לפני* הסף -
    בבדיקה הראשונה (בלי הגדלה) קווי עיפרון דקים נשברו לגמרי בסף האדפטיבי
    כי blockSize=25 היה גס מדי יחסית לעובי הקו בפועל. הגדלה נותנת לקו
    "מקום לנשום" כך שהסף מקבל גבולות חלקים במקום קפיצות חדות שמייצרות
    שברים. עדיין 100% דטרמיניסטי - שינוי גודל הוא אינטרפולציה מתמטית, לא ניחוש.
    שלב 1: סף אדפטיבי (adaptive threshold) - הופך כל פיקסל לשחור/לבן חד,
    לפי ניגודיות מקומית. זה נותן את המראה ה"מסותת" - קצוות חדים במקום
    מעברי אפור מטושטשים. blockSize/C כוילו מחדש ביחס לגודל התמונה המוגדל.
    שלב 2: ניקוי זעיר (קרנל 2x2 בלבד, בכוונה קטן) שסוגר רק פערים זעירים
    בתוך אותו קו - לא ממזג בין קווים/אותיות נפרדים."""
    import cv2
    import numpy as np

    arr = np.frombuffer(img_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return img_bytes

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    upscaled = cv2.resize(gray, None, fx=4.0, fy=4.0, interpolation=cv2.INTER_CUBIC)
    denoised = cv2.fastNlMeansDenoising(upscaled, h=10, templateWindowSize=7, searchWindowSize=21)

    binary = cv2.adaptiveThreshold(
        denoised, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY,
        blockSize=51, C=12
    )

    kernel = np.ones((2, 2), np.uint8)
    cleaned = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)

    success, encoded = cv2.imencode('.png', cleaned)
    return encoded.tobytes() if success else img_bytes


def _send_image_preview_email(to, original_filename, image_bytes):
    """שולח את התמונה המעובדת עצמה כקובץ מצורף - בלי שום קריאה ל-Gemini.
    מיועד לבדיקה/כיוונון: לראות בדיוק מה השרת 'צייר' לפני שמשלמים על קריאת
    OCR ומחכים לתשובה ממודל."""
    try:
        import sendgrid
        from sendgrid.helpers.mail import Mail, Email, Attachment, FileContent, FileName, FileType, Disposition

        html = f"""<div dir='rtl' style='font-family:Arial'>
<h3>🔍 תצוגה מקדימה - עיבוד תמונה (בלי Gemini)</h3>
<p>קובץ מקור: {original_filename}</p>
<p>זו התמונה בדיוק כפי שהיא נשלחת ל-Gemini אחרי העיבוד - מצורפת לקובץ.</p>
</div>"""
        sg = sendgrid.SendGridAPIClient(api_key=os.environ.get('SENDGRID_API_KEY'))
        message = Mail(
            from_email=Email(os.environ.get('SENDGRID_FROM_EMAIL', ''), 'מעבדת בדיקות'),
            to_emails=to,
            subject=f"🔍 מעבדה - תצוגה מקדימה - {original_filename}",
            html_content=html,
        )
        encoded = base64.b64encode(image_bytes).decode()
        message.attachment = Attachment(
            FileContent(encoded), FileName('preview.png'), FileType('image/png'), Disposition('attachment')
        )
        sg.send(message)
        log.info(f"image preview email sent to {to}")
    except Exception as e:
        log.error(f"image preview email error: {e}")


GVERET_LEVIN_FONT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'GveretLevin.ttf')
GVERET_LEVIN_FONT_URL = 'https://github.com/google/fonts/raw/main/ofl/gveretlevin/GveretLevin-Regular.ttf'
_LETTER_TEMPLATES_CACHE = None


def _ensure_gveret_levin_font():
    """מוריד את הפונט Gveret Levin (כתב-יד רהוט עברי, רישיון OFL פתוח דרך
    Google Fonts) פעם אחת ושומר מקומית, כדי לא להוריד בכל בקשה."""
    if not os.path.exists(GVERET_LEVIN_FONT_PATH):
        r = requests.get(GVERET_LEVIN_FONT_URL, timeout=30)
        r.raise_for_status()
        with open(GVERET_LEVIN_FONT_PATH, 'wb') as f:
            f.write(r.content)
    return GVERET_LEVIN_FONT_PATH


def _build_letter_templates():
    """בונה תבניות ייחוס לכל אות עברית (כולל סופיות) מתוך פונט כתב-יד רהוט,
    לא פונט מודפס - כי בבדיקה אמפירית זה שיפר את ציון ההתאמה פי 2.5. נשמר
    בזיכרון אחרי הבנייה הראשונה (לא נבנה מחדש בכל בקשה)."""
    global _LETTER_TEMPLATES_CACHE
    if _LETTER_TEMPLATES_CACHE is not None:
        return _LETTER_TEMPLATES_CACHE

    import cv2
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont

    font_path = _ensure_gveret_levin_font()
    font = ImageFont.truetype(font_path, 150)
    letters = list('אבגדהוזחטיכלמנסעפצקרשתךםןףץ')
    TEMPLATE_SIZE = 64
    templates = {}
    for letter in letters:
        img = Image.new('L', (200, 200), color=255)
        draw = ImageDraw.Draw(img)
        bbox = draw.textbbox((0, 0), letter, font=font)
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        if w == 0 or h == 0:
            continue
        draw.text((100 - w / 2 - bbox[0], 100 - h / 2 - bbox[1]), letter, font=font, fill=0)
        arr = np.array(img)
        _, binary = cv2.threshold(arr, 127, 255, cv2.THRESH_BINARY_INV)
        ys, xs = np.where(binary > 0)
        if len(xs) == 0:
            continue
        crop = binary[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
        templates[letter] = cv2.resize(crop, (TEMPLATE_SIZE, TEMPLATE_SIZE))

    _LETTER_TEMPLATES_CACHE = templates
    return templates


def _template_match_ocr(processed_gray_img):
    """שלב 2: מפלח את התמונה המעובדת (שחור/לבן) לרכיבים מחוברים, ומתאים כל
    רכיב לאות הכי קרובה מתוך התבניות - קרבה גיאומטרית בלבד (correlation),
    לא הבנה/הקשר. מקבץ לשורות לפי מיקום Y, וממיין ימין-לשמאל בתוך כל שורה.
    זו בדיוק השיטה שבדקנו ידנית על שורה אחת - כאן על התמונה כולה."""
    import cv2
    import numpy as np

    templates = _build_letter_templates()
    TEMPLATE_SIZE = 64

    binary_img = cv2.bitwise_not(processed_gray_img)
    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(binary_img, connectivity=8)

    components = []
    for i in range(1, num_labels):
        x, y, w, h, area = stats[i]
        if area < 15 or area > 3000:
            continue
        components.append((x, y, w, h))

    if not components:
        return '(לא זוהו רכיבי כתב בתמונה)'

    components.sort(key=lambda c: c[1] + c[3] / 2)
    lines = [[components[0]]]
    current_y = components[0][1] + components[0][3] / 2
    for comp in components[1:]:
        cy = comp[1] + comp[3] / 2
        if abs(cy - current_y) > 60:
            lines.append([comp])
        else:
            lines[-1].append(comp)
        current_y = cy

    def match_component(comp_binary):
        ys, xs = np.where(comp_binary > 0)
        if len(xs) == 0:
            return ''
        crop = comp_binary[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
        resized = cv2.resize(crop, (TEMPLATE_SIZE, TEMPLATE_SIZE))
        best_letter, best_score = '', -1
        for letter, tmpl in templates.items():
            result = cv2.matchTemplate(resized.astype(np.float32), tmpl.astype(np.float32), cv2.TM_CCOEFF_NORMED)
            score = result[0][0]
            if score > best_score:
                best_score, best_letter = score, letter
        return best_letter

    result_lines = []
    for line in lines:
        line.sort(key=lambda c: -c[0])  # ימין לשמאל
        line_text = ''.join(match_component(binary_img[y:y + h, x:x + w]) for (x, y, w, h) in line)
        result_lines.append(line_text)

    return '\n'.join(result_lines)


def _build_reference_sheet():
    """בונה דף ייחוס אחד עם כל 27 אותיות העברית (כתב-יד רהוט, Gveret Levin),
    כל אחת מתויגת - כדי לשלוח ל-Gemini כ'מילון חזותי' לצורך שיוך צורה בלבד."""
    from PIL import Image, ImageDraw, ImageFont
    import io as _io

    font_path = _ensure_gveret_levin_font()
    font = ImageFont.truetype(font_path, 90)
    letters = list('אבגדהוזחטיכלמנסעפצקרשתךםןףץ')

    cols, rows = 7, 4
    cell_w, cell_h = 150, 150
    sheet = Image.new('L', (cols * cell_w, rows * cell_h), color=255)
    draw = ImageDraw.Draw(sheet)

    for idx, letter in enumerate(letters):
        r, c = divmod(idx, cols)
        cx, cy = c * cell_w, r * cell_h
        draw.rectangle([cx, cy, cx + cell_w, cy + cell_h], outline=180)
        bbox = draw.textbbox((0, 0), letter, font=font)
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text((cx + cell_w / 2 - w / 2 - bbox[0], cy + 15), letter, font=font, fill=0)

    buf = _io.BytesIO()
    sheet.save(buf, format='PNG')
    return buf.getvalue()


GEMINI_SHAPE_MATCH_PROMPT = """קיבלת שתי תמונות:
1. "דף ייחוס" - 27 אותיות עברית בכתב-יד רהוט, כל אחת בתא נפרד עם האות הכתובה
   ליד הצורה (בתוך התא, מעל).
2. תמונת כתב יד מעובדת (שחור-לבן, אחרי הגדלה וסף).

המשימה שלך: לעבור על הכתב בתמונה השנייה, אות אחר אות (מימין לשמאל, שורה
אחר שורה), ולכל צורת-כתב לשייך את האות מדף הייחוס שהצורה שלה הכי דומה לה
מבחינה חזותית טהורה - עקומות, זוויות, מספר קווים. 

חשוב מאוד: זו משימת התאמת-צורות בלבד, לא משימת קריאה/הבנה. אסור לך להשתמש
בידע שלך על השפה העברית, על מילים נפוצות, או על הקשר תוכני כדי "לנחש" מה
אמורה להיות האות לפי מה שהגיוני שיהיה כתוב שם. גם אם הצורה מזכירה כמה
אותיות אפשריות, תבחר את הדומה ביותר גיאומטרית מתוך דף הייחוס בלבד - לא לפי
מה שהיה "הגיוני" שיהיה שם. אם צורה לא ברורה לחלוטין, סמן אותה ב-# במקום
לנחש.

החזר את התוצאה כטקסט רציף, שורה אחר שורה כמו במקור, רק אותיות מדף הייחוס
(ו-# לצורות לא ברורות) - בלי רווחים בין אותיות, בלי הסברים נוספים."""


def _gemini_ocr_shape_match(filepath, original_filename):
    """ניסוי: משלב את החוזק של Gemini (זיהוי חזותי הרבה יותר טוב מהתאמת-
    פיקסלים גולמית) עם המגבלה הבטוחה שכבר בדקנו (לא לתת לו "להבין תוכן").
    שולחים לו את התמונה המעובדת + 'דף ייחוס' עם כל האותיות, ומבקשים שיוך
    צורה-לצורה בלבד, לא תמלול חופשי. לא נבדק בפועל (אין מפתח Gemini אמיתי
    בסביבת הבדיקה) - יש להריץ ולבדוק תוצאה אמיתית."""
    from google import genai
    from google.genai import types as gtypes

    ext = os.path.splitext(original_filename or filepath)[1].lstrip('.').lower()
    if ext == 'pdf':
        import fitz
        doc = fitz.open(filepath)
        pix = doc[0].get_pixmap(matrix=fitz.Matrix(4.0, 4.0))
        raw_bytes = pix.tobytes('png')
        doc.close()
    else:
        with open(filepath, 'rb') as f:
            raw_bytes = f.read()

    processed_bytes = _redraw_handwriting_deterministic(raw_bytes)
    reference_sheet_bytes = _build_reference_sheet()

    api_key = os.environ.get('GOOGLE_API_KEY_OCR') or os.environ.get('GOOGLE_API_KEY')
    client = genai.Client(api_key=api_key)

    for attempt in range(3):
        try:
            response = client.models.generate_content(
                model='gemini-3.5-flash',
                contents=[
                    gtypes.Part.from_bytes(data=reference_sheet_bytes, mime_type='image/png'),
                    "^ דף הייחוס (27 אותיות מתויגות)",
                    gtypes.Part.from_bytes(data=processed_bytes, mime_type='image/png'),
                    "^ תמונת הכתב לשיוך",
                    GEMINI_SHAPE_MATCH_PROMPT,
                ],
                config=gtypes.GenerateContentConfig(
                    thinking_config=gtypes.ThinkingConfig(thinking_budget=0),
                ),
            )
            text = (response.text or '').strip()
            return processed_bytes, text
        except Exception as e:
            log.warning(f"shape-match attempt {attempt + 1} failed: {e}")
            if attempt < 2:
                import time; time.sleep(8)
    return processed_bytes, None


def _gemini_ocr_template_match(filepath, original_filename):
    """ניסוי: עוקף את Gemini לגמרי. שלב 1 - אותו עיבוד תמונה שכבר אישרנו
    שעובד (הגדלה פי 4 + סף אדפטיבי, ראו _redraw_handwriting_deterministic).
    שלב 2 - התאמת כל רכיב-כתב לאות הכי קרובה מתוך תבניות כתב-יד רהוט
    (Gveret Levin), בלי שום 'הבנה' - קרבה גיאומטרית בלבד. מחזיר גם את
    התמונה המעובדת וגם את הטקסט, כדי לשלוח את שניהם למייל יחד."""
    ext = os.path.splitext(original_filename or filepath)[1].lstrip('.').lower()

    if ext == 'pdf':
        import fitz
        doc = fitz.open(filepath)
        pix = doc[0].get_pixmap(matrix=fitz.Matrix(4.0, 4.0))
        raw_bytes = pix.tobytes('png')
        doc.close()
    else:
        with open(filepath, 'rb') as f:
            raw_bytes = f.read()

    processed_bytes = _redraw_handwriting_deterministic(raw_bytes)

    import cv2
    import numpy as np
    arr = np.frombuffer(processed_bytes, dtype=np.uint8)
    processed_gray = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)

    text_result = _template_match_ocr(processed_gray)
    return processed_bytes, text_result


def _send_ocr_experiment_email(to, original_filename, engine, image_bytes, text):
    """שולח מייל אחד עם שני חלקים: התמונה המעובדת (מצורפת) + התוצאה הכתובה
    (בגוף המייל) - כדי שאפשר יהיה להשוות בין הקלט לתמונה לתוצאה בבת אחת."""
    try:
        import sendgrid
        from sendgrid.helpers.mail import Mail, Email, Attachment, FileContent, FileName, FileType, Disposition

        safe_text = (text or '').replace('<', '&lt;').replace('>', '&gt;')
        html = f"""<div dir='rtl' style='font-family:Arial;max-width:600px'>
<h3>🧪 מעבדה - התאמת תבניות (בלי Gemini) - {original_filename}</h3>
<p style='color:#6b7280'>מנוע: {engine}</p>
<p>התמונה המעובדת (זו שנשלחה להתאמה) מצורפת לקובץ.</p>
<h4>התוצאה שהמערכת "הקלידה" (התאמה גיאומטרית בלבד, בלי הבנה):</h4>
<div style='white-space:pre-wrap;background:#fef3c7;border-right:4px solid #f59e0b;padding:16px;border-radius:8px;line-height:1.8;font-size:20px;direction:rtl'>{safe_text}</div>
</div>"""
        sg = sendgrid.SendGridAPIClient(api_key=os.environ.get('SENDGRID_API_KEY'))
        message = Mail(
            from_email=Email(os.environ.get('SENDGRID_FROM_EMAIL', ''), 'מעבדת בדיקות'),
            to_emails=to,
            subject=f"🧪 מעבדה - התאמת תבניות - {original_filename}",
            html_content=html,
        )
        encoded = base64.b64encode(image_bytes).decode()
        message.attachment = Attachment(
            FileContent(encoded), FileName('processed.png'), FileType('image/png'), Disposition('attachment')
        )
        sg.send(message)
        log.info(f"ocr experiment email sent to {to}")
    except Exception as e:
        log.error(f"ocr experiment email error: {e}")


def _gemini_ocr_redrawn_preview(filepath, original_filename):
    """כמו _gemini_ocr_redrawn, אבל בלי קריאה ל-Gemini בכלל - רק מריץ את
    העיבוד הדטרמיניסטי ומחזיר/שולח את התמונה המעובדת עצמה, כדי לבדוק/לכוונן
    את הפרמטרים (blockSize, C, גודל קרנל) בלי לבזבז קריאות API."""
    ext = os.path.splitext(original_filename or filepath)[1].lstrip('.').lower()

    if ext == 'pdf':
        import fitz
        doc = fitz.open(filepath)
        pix = doc[0].get_pixmap(matrix=fitz.Matrix(4.0, 4.0))
        raw_bytes = pix.tobytes('png')
        doc.close()
    else:
        with open(filepath, 'rb') as f:
            raw_bytes = f.read()

    processed_bytes = _redraw_handwriting_deterministic(raw_bytes)
    return processed_bytes  # bytes, לא טקסט - הטיפול בשליחה שונה (ראו run_engine)


def _gemini_ocr_redrawn(filepath, original_filename):
    """ניסוי: 'ציור מחדש' דטרמיניסטי (סף אדפטיבי + ניקוי זעיר, לא AI - ראו
    _redraw_handwriting_deterministic) לפני שליחה ל-Gemini OCR. שלב אחד
    מעבר לניקוי-רעש/ניגודיות הרגיל - כאן כל פיקסל מוכרע בבירור כדיו או נייר."""
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
                log.warning(f"Gemini OCR (redrawn) attempt {attempt + 1} failed: {e}")
                if attempt < 2:
                    import time; time.sleep(8)
        return None

    if ext == 'pdf':
        import fitz
        all_pages = []
        doc = fitz.open(filepath)
        for i in range(len(doc)):
            pix = doc[i].get_pixmap(matrix=fitz.Matrix(4.0, 4.0))
            raw_bytes = pix.tobytes('png')
            processed_bytes = _redraw_handwriting_deterministic(raw_bytes)
            text = ocr_image_bytes(processed_bytes, 'image/png')
            all_pages.append(f"--- עמוד {i + 1} ---\n{text or '[לא קריא]'}")
        doc.close()
        return '\n\n'.join(all_pages)

    mime_map = {'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png', 'gif': 'image/gif', 'webp': 'image/webp'}
    mime = mime_map.get(ext, 'image/jpeg')
    with open(filepath, 'rb') as f:
        raw_bytes = f.read()
    processed_bytes = _redraw_handwriting_deterministic(raw_bytes)
    return ocr_image_bytes(processed_bytes, 'image/png')


def _gemini_ocr_preprocessed(filepath, original_filename):
    """ניסוי: מריץ עיבוד תמונה קלאסי (ניקוי רעש + הגברת ניגודיות בלבד, לא
    גנרטיבי - ראו _enhance_handwriting_image) לפני שליחה ל-Gemini OCR.
    בודק אם ניקוי/הבלטה עוזרים לדיוק, בלי הסיכון של 'שחזור' אותיות גנרטיבי
    שעלול להמציא צורות שלא היו במקור."""
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
                log.warning(f"Gemini OCR (preprocessed) attempt {attempt + 1} failed: {e}")
                if attempt < 2:
                    import time; time.sleep(8)
        return None

    if ext == 'pdf':
        import fitz
        all_pages = []
        doc = fitz.open(filepath)
        for i in range(len(doc)):
            pix = doc[i].get_pixmap(matrix=fitz.Matrix(4.0, 4.0))
            raw_bytes = pix.tobytes('png')
            processed_bytes = _enhance_handwriting_image(raw_bytes)
            text = ocr_image_bytes(processed_bytes, 'image/png')
            all_pages.append(f"--- עמוד {i + 1} ---\n{text or '[לא קריא]'}")
        doc.close()
        return '\n\n'.join(all_pages)

    mime_map = {'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png', 'gif': 'image/gif', 'webp': 'image/webp'}
    mime = mime_map.get(ext, 'image/jpeg')
    with open(filepath, 'rb') as f:
        raw_bytes = f.read()
    processed_bytes = _enhance_handwriting_image(raw_bytes)
    return ocr_image_bytes(processed_bytes, 'image/png')


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


def _load_image_bytes_and_mime(path):
    """טוען תמונה מהדיסק כ-bytes+mime; אם זה PDF, ממיר את העמוד הראשון לתמונה.
    משותף בין _gemini_ocr_with_reference (לתמונת הדוגמה) לבין הקובץ החדש."""
    p_ext = os.path.splitext(path)[1].lstrip('.').lower()
    if p_ext == 'pdf':
        import fitz
        doc = fitz.open(path)
        pix = doc[0].get_pixmap(matrix=fitz.Matrix(4.0, 4.0))
        img_bytes = pix.tobytes('png')
        doc.close()
        return img_bytes, 'image/png'
    mime_map = {'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png', 'gif': 'image/gif', 'webp': 'image/webp'}
    with open(path, 'rb') as f:
        return f.read(), mime_map.get(p_ext, 'image/jpeg')


REFERENCE_INTRO_TEXT = """להלן דוגמת ייחוס: תמונת כתב יד קודמת מאותו כותב, ומיד אחריה התמלול
המדויק שהוכן לה בעבר (נבדק ואושר ע"י בנאדם). המטרה היחידה של הדוגמה הזו היא
שתכיר את צורת האותיות/סגנון הכתיבה האישי של הכותב הספציפי הזה - לא כמקור
תוכן. אסור לך להעתיק ממנה מילים אל תוך התמלול של התמונה החדשה למטה; היא
משמשת רק לכיול חזותי לצורת הכתב שלו."""

NEW_IMAGE_INTRO_TEXT = "כעת התמונה החדשה לתמלול (כתב יד של אותו כותב) - תעתיק אך ורק אותה, לפי הכללים הבאים:"


def _gemini_ocr_with_reference(filepath, original_filename, ref_image_path, ref_text):
    """כמו _gemini_ocr הרגיל, אבל עם דוגמת ייחוס אחת (one-shot in-context):
    תמונת כתב יד קודמת מאותו כותב + התמלול המדויק שהוכן לה בעבר, נשלחים יחד
    עם התמונה החדשה באותה קריאה - כדי ש-Gemini "יתכייל" לצורת האותיות
    הספציפית של הכותב הזה. הערה: זה לא אימון אמיתי של המודל (אין fine-tuning) -
    הדוגמה נשלחת מחדש כהקשר (few-shot) בכל קריאה, מה שמגדיל מעט את עלות/גודל
    הקריאה אבל לא דורש שום תשתית נוספת."""
    from google import genai
    from google.genai import types as gtypes

    if not ref_image_path or not ref_text:
        raise ValueError("מנוע gemini_ocr_with_reference דורש גם תמונת דוגמה וגם תמלול מדויק עבורה")

    api_key = os.environ.get('GOOGLE_API_KEY_OCR') or os.environ.get('GOOGLE_API_KEY')
    client = genai.Client(api_key=api_key)
    ext = os.path.splitext(original_filename or filepath)[1].lstrip('.').lower()
    mime_map = {'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png', 'gif': 'image/gif', 'webp': 'image/webp'}

    ref_img_bytes, ref_mime = _load_image_bytes_and_mime(ref_image_path)

    def ocr_new_image_bytes(img_bytes, mime):
        for attempt in range(3):
            try:
                response = client.models.generate_content(
                    model='gemini-3.5-flash',
                    contents=[
                        REFERENCE_INTRO_TEXT,
                        gtypes.Part.from_bytes(data=ref_img_bytes, mime_type=ref_mime),
                        f"התמלול המדויק של תמונת הדוגמה:\n{ref_text}",
                        NEW_IMAGE_INTRO_TEXT,
                        gtypes.Part.from_bytes(data=img_bytes, mime_type=mime),
                        OCR_PROMPT_TEXT,
                    ]
                )
                return (response.text or '').strip()
            except Exception as e:
                log.warning(f"Gemini OCR (with reference) attempt {attempt + 1} failed: {e}")
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
            text = ocr_new_image_bytes(img_bytes, 'image/png')
            all_pages.append(f"--- עמוד {i + 1} ---\n{text or '[לא קריא]'}")
        doc.close()
        return '\n\n'.join(all_pages)

    mime = mime_map.get(ext, 'image/jpeg')
    with open(filepath, 'rb') as f:
        img_bytes = f.read()
    return ocr_new_image_bytes(img_bytes, mime)


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


def _gemini_transcribe_formatted_no_thinking(url, language='he', thinking_budget=0):
    """נסיוני לחיסכון: thinking_budget קטן/אפס (כמעט או לגמרי בלי טוקני חשיבה) -
    הפורמט (ירידת שורה בסוף כל משפט/פסוקית, פיסוק) מבוקש כהוראת ציות ישירה
    בפרומפט. גילינו ש-budget=0 מבטל גם את הפורמט (אין תכנון מבנה בלי חשיבה
    בכלל) - לכן פרמטר זה מאפשר לבדוק ערכים קטנים אחרים (למשל 128) כדי למצוא
    את המינימום שעדיין "שומר" על הפורמט, בלי לשלם על חשיבה כמו בגרסה הרגילה."""
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

פורמט (הוראת עיצוב פשוטה, לא דורשת ניתוח):
- רד שורה בסוף כל משפט או פסוקית שלמה, כדי שהטקסט יהיה קריא וברור לעין.
- הוסף פיסוק (פסיקים, נקודות, מירכאות) לפי מבנה המשפט הנשמע.

החזר רק את הטקסט המתומלל ללא הערות נוספות."""

    config = gtypes.GenerateContentConfig(
        thinking_config=gtypes.ThinkingConfig(thinking_budget=thinking_budget)
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
            return (response.text or '').strip()
        except Exception as e:
            log.warning(f"Gemini low-cost-formatted transcribe attempt {attempt + 1} failed: {e}")
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
