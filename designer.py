"""
שולחן עיצוב - עמוד לעריכה ויזואלית של עיצוב עמודי HTML קיימים.

כל העבודה מתבצעת בדפדפן בלבד (אין קריאה לשרת ואין עלות): מדביקים את קוד
העמוד (Ctrl+U), משנים צבעים/גופנים/גדלים/שדות/כפתורים בתצוגה חיה, ומקבלים
קוד HTML חדש להעתקה או להורדה.
"""
import os

from flask import send_file

DESIGNER_HTML = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'designer.html')


def init_designer(app):
    @app.route('/designer', methods=['GET'])
    def designer_page():
        resp = send_file(DESIGNER_HTML, mimetype='text/html')
        resp.headers['Cache-Control'] = 'no-cache'
        return resp
