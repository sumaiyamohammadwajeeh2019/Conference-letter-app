import os
import base64
import io
import zipfile
import sqlite3
from pathlib import Path
from datetime import datetime

try:
    from flask import Flask, render_template, request, send_file, jsonify  # type: ignore[import-not-found]
    import pandas as pd  # type: ignore[import-not-found]
    from reportlab.lib.pagesizes import A4  # type: ignore[import-not-found]
    from reportlab.pdfgen import canvas  # type: ignore[import-not-found]
except ModuleNotFoundError as _e:
    raise SystemExit(
        f"Missing dependency {_e.name!r}. Install everything with: "
        f"py -m pip install -r requirements.txt (python: "
        f"C:\\Users\\T490\\AppData\\Local\\Python\\pythoncore-3.14-64\\python.exe)"
    ) from _e

try:
    import psycopg2  # type: ignore[import-not-found]
except ImportError:  # local SQLite mode, psycopg2 only needed when DATABASE_URL is set
    psycopg2 = None  # type: ignore[assignment]

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "letter_app.db"
UPLOAD_DIR = BASE_DIR / "static" / "uploads"
GENERATED_DIR = BASE_DIR / "generated"

# ========== DATABASE SETUP ==========
DATABASE_URL = os.environ.get('DATABASE_URL')

DEFAULT_TEMPLATES = {
    "invitation": {
        "title": "Letter of Invitation",
        "subject": "Invitation - {conference_name}",
        "body": "Dear Dr. {name},\n\nWe invite you as {role} to \"{conference_name}\" at {venue} on {date}.\n\nRegards,\n{organizer_name}"
    },
    "appraisal": {
        "title": "Letter of Appraisal",
        "subject": "Appraisal - {conference_name}",
        "body": "To Dr. {name},\nThank you for contribution as {role} on {date} at {venue}.\n\n{organizer_name}"
    },
    "thankyou": {
        "title": "Thank You Letter",
        "subject": "Thank You - {conference_name}",
        "body": "Dear Dr. {name},\nThank you for participating as {role} in {conference_name} on {date}.\n\nBest,\n{organizer_name}"
    },
    "acceptance": {
        "title": "Abstract Acceptance",
        "subject": "Accepted - {conference_name}",
        "body": "Dear Dr. {name},\nYour abstract \"{abstract_title}\" accepted for {conference_name}.\nDate: {date} Venue: {venue}"
    }
}

def get_db():
    if DATABASE_URL:
        if psycopg2 is None:
            raise RuntimeError("DATABASE_URL is set but psycopg2 is not installed. Run: py -m pip install -r requirements.txt")
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        return conn
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cur = conn.cursor()
    if DATABASE_URL:
        cur.execute("""CREATE TABLE IF NOT EXISTS letter_templates (
            key TEXT PRIMARY KEY, title TEXT, subject TEXT, body TEXT
        );""")
        cur.execute("""CREATE TABLE IF NOT EXISTS assets (
            key TEXT PRIMARY KEY, data TEXT
        );""")
        # Insert defaults if empty
        cur.execute("SELECT COUNT(*) FROM letter_templates;")
        if cur.fetchone()[0] == 0:
            for k,v in DEFAULT_TEMPLATES.items():
                cur.execute("INSERT INTO letter_templates VALUES (%s,%s,%s,%s)", (k,v['title'],v['subject'],v['body']))
    else:
        cur.execute("""CREATE TABLE IF NOT EXISTS letter_templates (
            key TEXT PRIMARY KEY, title TEXT, subject TEXT, body TEXT
        );""")
        cur.execute("""CREATE TABLE IF NOT EXISTS assets (
            key TEXT PRIMARY KEY, data TEXT
        );""")
        cur.execute("SELECT COUNT(*) FROM letter_templates;")
        if cur.fetchone()[0] == 0:
            for k,v in DEFAULT_TEMPLATES.items():
                cur.execute("INSERT INTO letter_templates VALUES (?,?,?,?)", (k,v['title'],v['subject'],v['body']))
    conn.commit()
    conn.close()

def load_templates():
    conn = get_db()
    cur = conn.cursor()
    if DATABASE_URL:
        cur.execute("SELECT key,title,subject,body FROM letter_templates")
        rows = cur.fetchall()
        templates = {r[0]: {"title": r[1], "subject": r[2], "body": r[3]} for r in rows}
    else:
        cur.execute("SELECT key,title,subject,body FROM letter_templates")
        templates = {r[0]: {"title": r[1], "subject": r[2], "body": r[3]} for r in cur.fetchall()}
    conn.close()
    return templates

def load_asset(asset_key):
    """Returns image path if exists, saves from DB to temp file"""
    conn = get_db()
    cur = conn.cursor()
    if DATABASE_URL:
        cur.execute("SELECT data FROM assets WHERE key=%s", (asset_key,))
    else:
        cur.execute("SELECT data FROM assets WHERE key=?", (asset_key,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    data = row[0]
    # Save to static folder for preview + pdf use
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    path = UPLOAD_DIR / f"{asset_key}.png"
    try:
        path.write_bytes(base64.b64decode(data))
    except Exception:
        return None
    return str(path)

# ========== APP ==========
app = Flask(__name__)
GENERATED_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
init_db()

# Load assets on startup so they persist after redeploy
try:
    load_asset('logo')
except Exception:
    pass
try:
    load_asset('signature')
except Exception:
    pass

def create_pdf(delegate, letter_type, conf_details, output_path):
    templates = load_templates()
    c = canvas.Canvas(output_path, pagesize=A4)
    width, height = A4

    # LOGO from DB (persists)
    logo_path = load_asset('logo')
    if logo_path and os.path.exists(logo_path):
        try:
            c.drawImage(logo_path, 50, height - 70, width=65, height=65,
                        preserveAspectRatio=True, mask='auto')
        except Exception:
            pass

    c.setFont("Helvetica-Bold", 14)
    c.drawCentredString(width/2, height-40, conf_details['conference_name'])
    c.setFont("Helvetica", 9)
    c.drawCentredString(width/2, height-53, f"{conf_details['organizer']} | {conf_details['venue']} | {conf_details['date']}")
    c.line(50, height-75, width-50, height-75)

    c.setFont("Helvetica-Bold", 12)
    c.drawString(50, height-100, templates[letter_type]['title'])
    c.setFont("Helvetica", 9)
    c.drawRightString(width-50, height-100, f"Date: {datetime.now().strftime('%d %B %Y')}")

    body = templates[letter_type]['body'].format(
        name=delegate.get('Name','Delegate'), designation=delegate.get('Designation',''),
        college=delegate.get('College',''), role=delegate.get('Role','Delegate'),
        conference_name=conf_details['conference_name'], organizer=conf_details['organizer'],
        venue=conf_details['venue'], date=conf_details['date'],
        time=conf_details.get('time','9:00 AM'), organizer_name=conf_details['organizer_name'],
        organizer_designation=conf_details.get('organizer_designation',''),
        organizer_college=conf_details.get('organizer_college',''),
        contact=conf_details.get('contact',''), abstract_title=delegate.get('Abstract','')
    )
    text = c.beginText(50, height-130)
    text.setFont("Helvetica", 11); text.setLeading(15)
    for line in body.split('\n'):
        while len(line) > 88:
            text.textLine(line[:88]); line = line[88:]
        text.textLine(line)
    c.drawText(text)

    # SIGNATURE from DB (persists)
    sig_path = load_asset('signature')
    y_sig = 140
    if sig_path and os.path.exists(sig_path):
        try:
            c.drawImage(sig_path, 50, y_sig, width=110, height=40,
                        preserveAspectRatio=True, mask='auto')
        except Exception:
            pass

    c.setFont("Helvetica-Bold", 10); c.drawString(50, y_sig-15, conf_details['organizer_name'])
    c.setFont("Helvetica", 9); c.drawString(50, y_sig-28, conf_details.get('organizer_designation',''))
    c.line(50, 60, width-50, 60)
    c.setFont("Helvetica", 7); c.drawCentredString(width/2, 45, f"Computer generated for {conf_details['conference_name']}")
    c.save()

@app.route('/')
def index():
    templates = load_templates()
    has_logo = load_asset('logo') is not None
    has_sig = load_asset('signature') is not None
    return render_template('index.html', templates=templates, has_logo=has_logo, has_sig=has_sig)

@app.route('/api/templates')
def get_templates(): return jsonify(load_templates())

@app.route('/api/templates/save', methods=['POST'])
def save_template_api():
    d = request.json; key = d['key'].lower().replace(' ','_')
    conn = get_db(); cur = conn.cursor()
    if DATABASE_URL:
        cur.execute("INSERT INTO letter_templates VALUES (%s,%s,%s,%s) ON CONFLICT(key) DO UPDATE SET title=%s,subject=%s,body=%s",
                    (key,d['title'],d['subject'],d['body'],d['title'],d['subject'],d['body']))
    else:
        cur.execute("INSERT OR REPLACE INTO letter_templates VALUES (?,?,?,?)",(key,d['title'],d['subject'],d['body']))
    conn.commit(); conn.close()
    return jsonify({"ok": True})

@app.route('/api/templates/delete', methods=['POST'])
def delete_template_api():
    key = request.json['key']
    conn = get_db(); cur = conn.cursor()
    if DATABASE_URL: cur.execute("DELETE FROM letter_templates WHERE key=%s",(key,))
    else: cur.execute("DELETE FROM letter_templates WHERE key=?",(key,))
    conn.commit(); conn.close()
    return jsonify({"ok": True})

@app.route('/api/templates/reset', methods=['POST'])
def reset_templates():
    conn = get_db(); cur = conn.cursor()
    if DATABASE_URL: cur.execute("DELETE FROM letter_templates")
    else: cur.execute("DELETE FROM letter_templates")
    for k,v in DEFAULT_TEMPLATES.items():
        if DATABASE_URL: cur.execute("INSERT INTO letter_templates VALUES (%s,%s,%s,%s)",(k,v['title'],v['subject'],v['body']))
        else: cur.execute("INSERT INTO letter_templates VALUES (?,?,?,?)",(k,v['title'],v['subject'],v['body']))
    conn.commit(); conn.close()
    return jsonify({"ok": True})

@app.route('/upload_assets', methods=['POST'])
def upload_assets():
    conn = get_db(); cur = conn.cursor()
    for asset_key in ['logo','signature']:
        uploaded = request.files.get(asset_key)
        if uploaded is not None and uploaded.filename != '':
            data = base64.b64encode(uploaded.read()).decode('utf-8')
            if DATABASE_URL:
                cur.execute("INSERT INTO assets VALUES (%s,%s) ON CONFLICT(key) DO UPDATE SET data=%s",(asset_key,data,data))
            else:
                cur.execute("INSERT OR REPLACE INTO assets VALUES (?,?)",(asset_key,data))
            # also save locally
            UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
            (UPLOAD_DIR / f"{asset_key}.png").write_bytes(base64.b64decode(data))
    conn.commit(); conn.close()
    return jsonify({"ok": True})

@app.route('/delete_asset/<asset_type>')
def delete_asset(asset_type):
    if asset_type not in ('logo', 'signature'):
        return jsonify({"ok": False, "error": "invalid asset"}), 400
    conn = get_db(); cur = conn.cursor()
    if DATABASE_URL: cur.execute("DELETE FROM assets WHERE key=%s",(asset_type,))
    else: cur.execute("DELETE FROM assets WHERE key=?",(asset_type,))
    conn.commit(); conn.close()
    target = UPLOAD_DIR / f"{asset_type}.png"
    if target.exists(): target.unlink()
    return jsonify({"ok": True})

@app.route('/generate', methods=['POST'])
def generate():
    conf = {k: request.form.get(k,'') for k in ['conference_name','organizer','venue','date','time','organizer_name','organizer_designation','organizer_college','contact']}
    letter_type = request.form.get('letter_type', '')
    templates = load_templates()
    if letter_type not in templates:
        return jsonify({"ok": False, "error": f"unknown letter_type: {letter_type}"}), 400
    upload = request.files.get('delegate_file')
    if upload is None or upload.filename == '':
        return jsonify({"ok": False, "error": "delegate_file is required"}), 400
    filename = upload.filename or ''
    if filename.lower().endswith('.csv'):
        df = pd.read_csv(upload)
    else:
        df = pd.read_excel(upload)
    zip_buffer = io.BytesIO()
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_buffer, 'w') as zip_file:
        for _, row in df.iterrows():
            delegate = row.to_dict()
            safe = "".join([c for c in str(delegate.get('Name','delegate')) if c.isalnum() or c in (' ','_')]).strip().replace(' ','_') or 'delegate'
            pdf_path = GENERATED_DIR / f"{safe}_{letter_type}.pdf"
            create_pdf(delegate, letter_type, conf, str(pdf_path))
            zip_file.write(pdf_path, pdf_path.name)
    zip_buffer.seek(0)
    return send_file(zip_buffer, as_attachment=True, download_name=f"{letter_type}_letters.zip")

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_DEBUG', '1') == '1' and not os.environ.get('RENDER')
    app.run(debug=debug, host='0.0.0.0', port=port)