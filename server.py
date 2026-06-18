from flask import Flask, request, jsonify, send_from_directory, redirect
import anthropic, os, uuid, sqlite3, smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timedelta
from dotenv import load_dotenv
from functools import wraps

load_dotenv()

app    = Flask(__name__, static_folder='static')
claude = anthropic.Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))
ADMIN_KEY = os.getenv('ADMIN_KEY', 'jarvis-admin-2024')
BASE_URL  = os.getenv('BASE_URL', 'http://localhost:8081')

# ── Database ───────────────────────────────────────────────
def db():
    conn = sqlite3.connect('customers.db')
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    c = db()
    c.execute('''CREATE TABLE IF NOT EXISTS customers (
        id TEXT PRIMARY KEY,
        email TEXT UNIQUE NOT NULL,
        api_key TEXT UNIQUE NOT NULL,
        active INTEGER DEFAULT 0,
        trial_ends_at TEXT,
        business_name TEXT DEFAULT '',
        created_at TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        api_key TEXT NOT NULL,
        reviewer TEXT DEFAULT 'Anoniem',
        rating INTEGER DEFAULT 5,
        review_text TEXT NOT NULL,
        ai_response TEXT DEFAULT '',
        responded INTEGER DEFAULT 0,
        created_at TEXT
    )''')
    try: c.execute('ALTER TABLE customers ADD COLUMN trial_ends_at TEXT')
    except: pass
    c.commit(); c.close()

init_db()

# ── Helpers ────────────────────────────────────────────────
def get_customer(api_key):
    c = db()
    row = c.execute('SELECT * FROM customers WHERE api_key=?', (api_key,)).fetchone()
    if not row:
        c.close()
        return None
    cust = dict(row)
    if cust.get('trial_ends_at') and cust['active'] and datetime.now().isoformat() > cust['trial_ends_at']:
        c.execute('UPDATE customers SET active=0 WHERE api_key=?', (api_key,))
        c.commit()
        cust['active'] = 0
    c.close()
    return cust

def require_admin(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if request.headers.get('X-Admin-Key') != ADMIN_KEY and request.args.get('admin_key') != ADMIN_KEY:
            return jsonify({'error': 'Unauthorized'}), 401
        return f(*args, **kwargs)
    return wrapper

def send_welcome_email(email, api_key, business_name, trial=False):
    gmail = os.getenv('GMAIL_ADDRESS', '')
    pwd   = os.getenv('GMAIL_APP_PASSWORD', '')
    if not gmail or not pwd:
        return
    trial_end = (datetime.now() + timedelta(days=7)).strftime('%d-%m-%Y')
    trial_block = f"""
    <div style="background:#fff3cd;border:1px solid #ffc107;border-radius:8px;padding:16px;margin:16px 0">
      <strong>Je hebt 7 dagen gratis toegang tot en met {trial_end}.</strong><br>
      <span style="font-size:13px;color:#666">Daarna: €54,50/maand via bankoverschrijving (zie onderaan).</span>
    </div>""" if trial else ''
    iban_block = """
    <hr style="border:none;border-top:1px solid #eee;margin:24px 0">
    <p style="font-size:13px;color:#888"><strong>Na je proefperiode doorgaan?</strong><br>
    Maak €54,50 over met je e-mailadres als omschrijving:<br>
    <strong>IBAN: NL26 REVO 1741 4708 03</strong> · R. Spronken<br>
    Je account blijft dan actief.</p>""" if trial else ''
    body = f"""
<div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;color:#333">
  <div style="background:linear-gradient(135deg,#ff6b35,#ff8c42);padding:32px;border-radius:12px 12px 0 0;text-align:center">
    <h1 style="color:#fff;margin:0;font-size:28px">Welkom bij ReviewHost!</h1>
    <p style="color:rgba(255,255,255,.8);margin:8px 0 0;font-size:15px">Je AI review-assistent staat klaar</p>
  </div>
  <div style="background:#f9f9f9;padding:32px;border-radius:0 0 12px 12px">
    <p>Hallo{' ' + business_name if business_name else ''},</p>
    {trial_block}
    <p style="margin-top:12px">Ga naar je dashboard om reviews toe te voegen en AI-antwoorden te genereren:</p>
    <div style="text-align:center;margin:24px 0">
      <a href="{BASE_URL}/dashboard?key={api_key}" style="background:linear-gradient(135deg,#ff6b35,#ff8c42);color:#fff;padding:14px 32px;border-radius:8px;text-decoration:none;font-weight:700;font-size:15px">Open mijn dashboard</a>
    </div>
    {iban_block}
    <p style="color:#888;font-size:13px;margin-top:20px">Vragen? Mail naar spronken1234@gmail.com</p>
  </div>
</div>"""
    msg = MIMEMultipart('alternative')
    msg['Subject'] = 'Je ReviewHost proefperiode is gestart!'
    msg['From']    = gmail
    msg['To']      = email
    msg.attach(MIMEText(body, 'html'))
    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as s:
            s.login(gmail, pwd)
            s.sendmail(gmail, email, msg.as_string())
    except Exception as e:
        print(f'[EMAIL ERROR] {e}')

# ── Static pages ───────────────────────────────────────────
@app.route('/')
def index():
    return send_from_directory('static', 'index.html')

@app.route('/dashboard')
def dashboard():
    return send_from_directory('static', 'dashboard.html')

@app.route('/admin')
def admin():
    return send_from_directory('static', 'admin.html')

@app.route('/live')
@require_admin
def live():
    return send_from_directory('static', 'live.html')

# ── Checkout ───────────────────────────────────────────────
@app.route('/api/checkout', methods=['POST'])
def checkout():
    data     = request.json
    email    = data.get('email', '').strip()
    business = data.get('business', '').strip()
    if not email:
        return jsonify({'error': 'Email required'}), 400
    c = db()
    existing = c.execute('SELECT * FROM customers WHERE email=?', (email,)).fetchone()
    if existing and existing['active']:
        c.close()
        return jsonify({'redirect': f'/dashboard?key={existing["api_key"]}'}), 200
    trial_ends = (datetime.now() + timedelta(days=7)).isoformat()
    if existing:
        api_key = existing['api_key']
        c.execute('UPDATE customers SET active=1, trial_ends_at=? WHERE email=?', (trial_ends, email))
        c.commit(); c.close()
    else:
        cid = str(uuid.uuid4())
        api_key = str(uuid.uuid4()).replace('-', '')
        c.execute('INSERT INTO customers (id,email,api_key,active,trial_ends_at,business_name,created_at) VALUES (?,?,?,1,?,?,?)',
                  (cid, email, api_key, trial_ends, business, datetime.now().isoformat()))
        c.commit(); c.close()
    send_welcome_email(email, api_key, business, trial=True)
    print(f'[TRIAL] {email} | {business}')
    return jsonify({'ok': True, 'trial': True})

# ── Admin: activate ────────────────────────────────────────
@app.route('/api/admin/activate', methods=['POST'])
@require_admin
def admin_activate():
    data     = request.json
    email    = data.get('email', '').strip()
    business = data.get('business', '').strip()
    c = db()
    row = c.execute('SELECT * FROM customers WHERE email=?', (email,)).fetchone()
    if row:
        c.execute('UPDATE customers SET active=1, trial_ends_at=NULL WHERE email=?', (email,))
        c.commit()
        api_key = row['api_key']
    else:
        api_key = str(uuid.uuid4()).replace('-', '')
        cid     = str(uuid.uuid4())
        c.execute('INSERT INTO customers (id,email,api_key,active,business_name,created_at) VALUES (?,?,?,1,?,?)',
                  (cid, email, api_key, business, datetime.now().isoformat()))
        c.commit()
    c.close()
    send_welcome_email(email, api_key, business)
    return jsonify({'ok': True, 'api_key': api_key})

# ── Admin: stats ───────────────────────────────────────────
@app.route('/api/admin/stats')
@require_admin
def admin_stats():
    c = db()
    total   = c.execute('SELECT COUNT(*) FROM customers').fetchone()[0]
    active  = c.execute('SELECT COUNT(*) FROM customers WHERE active=1').fetchone()[0]
    reviews = c.execute('SELECT COUNT(*) FROM reviews').fetchone()[0]
    today   = c.execute("SELECT COUNT(*) FROM reviews WHERE created_at>=date('now')").fetchone()[0]
    custs   = c.execute('SELECT email,business_name,active,api_key,created_at FROM customers ORDER BY created_at DESC').fetchall()
    c.close()
    mrr = active * 54.5
    return jsonify({
        'total_customers': total, 'active_customers': active,
        'mrr': mrr, 'arr': mrr * 12,
        'total_reviews': reviews, 'reviews_today': today,
        'customers': [dict(r) for r in custs]
    })

# ── Live stats ─────────────────────────────────────────────
@app.route('/api/live-stats')
@require_admin
def live_stats():
    c = db()
    total   = c.execute('SELECT COUNT(*) FROM customers').fetchone()[0]
    active  = c.execute('SELECT COUNT(*) FROM customers WHERE active=1').fetchone()[0]
    reviews = c.execute('SELECT COUNT(*) FROM reviews').fetchone()[0]
    today   = c.execute("SELECT COUNT(*) FROM reviews WHERE created_at>=date('now')").fetchone()[0]
    custs   = c.execute('SELECT email,business_name,active,created_at FROM customers ORDER BY created_at DESC').fetchall()
    days    = c.execute("SELECT date(created_at) as d, COUNT(*) as n FROM reviews GROUP BY date(created_at) ORDER BY date(created_at) DESC LIMIT 14").fetchall()
    c.close()
    mrr = active * 54.5
    return jsonify({
        'active': active, 'total': total, 'mrr': mrr, 'arr': mrr * 12,
        'reviews_total': reviews, 'reviews_today': today,
        'customers': [{'name': r['business_name'] or r['email'], 'email': r['email'],
                       'active': bool(r['active']), 'date': (r['created_at'] or '')[:10]} for r in custs],
        'chart': [{'date': r['d'], 'count': r['n']} for r in reversed(days)],
    })

# ── Customer: get reviews ──────────────────────────────────
@app.route('/api/reviews')
def get_reviews():
    key  = request.args.get('key', '')
    cust = get_customer(key)
    if not cust or not cust['active']:
        return jsonify({'error': 'Unauthorized'}), 403
    c = db()
    rows = c.execute('SELECT * FROM reviews WHERE api_key=? ORDER BY created_at DESC', (key,)).fetchall()
    c.close()
    return jsonify([dict(r) for r in rows])

# ── Customer: add review ───────────────────────────────────
@app.route('/api/reviews', methods=['POST'])
def add_review():
    key  = request.args.get('key', '')
    cust = get_customer(key)
    if not cust or not cust['active']:
        return jsonify({'error': 'Unauthorized'}), 403
    data = request.json
    c = db()
    c.execute('INSERT INTO reviews (api_key,reviewer,rating,review_text,created_at) VALUES (?,?,?,?,?)',
              (key, data.get('reviewer', 'Anoniem'), data.get('rating', 5),
               data.get('review_text', ''), datetime.now().isoformat()))
    c.commit()
    rid = c.execute('SELECT last_insert_rowid()').fetchone()[0]
    c.close()
    return jsonify({'ok': True, 'id': rid})

# ── Customer: generate AI response ────────────────────────
@app.route('/api/respond', methods=['POST'])
def generate_response():
    key  = request.args.get('key', '')
    cust = get_customer(key)
    if not cust or not cust['active']:
        return jsonify({'error': 'Unauthorized'}), 403
    data       = request.json
    review_id  = data.get('id')
    c = db()
    row = c.execute('SELECT * FROM reviews WHERE id=? AND api_key=?', (review_id, key)).fetchone()
    if not row:
        c.close()
        return jsonify({'error': 'Review not found'}), 404

    stars   = '★' * row['rating'] + '☆' * (5 - row['rating'])
    biz     = cust['business_name'] or 'ons bedrijf'
    prompt  = f"""You are writing a professional, warm review response on behalf of {biz}.

Review by {row['reviewer']} ({stars}):
"{row['review_text']}"

Write a short (2-4 sentences), genuine and professional response.
- Thank the reviewer by name
- Address their specific feedback
- If negative (1-2 stars): apologize sincerely and offer to make it right
- If positive (4-5 stars): express genuine gratitude
- End with an invitation to return
- Match the language of the review (Dutch review = Dutch response, English = English, etc.)
- Do NOT use generic phrases like "We value your feedback"
- Sound human, not corporate"""

    resp = claude.messages.create(
        model='claude-haiku-4-5-20251001',
        max_tokens=300,
        messages=[{'role': 'user', 'content': prompt}]
    )
    response_text = resp.content[0].text.strip()
    c.execute('UPDATE reviews SET ai_response=? WHERE id=?', (response_text, review_id))
    c.commit(); c.close()
    return jsonify({'ok': True, 'response': response_text})

# ── Customer: mark responded ───────────────────────────────
@app.route('/api/reviews/<int:rid>/responded', methods=['POST'])
def mark_responded(rid):
    key = request.args.get('key', '')
    if not get_customer(key):
        return jsonify({'error': 'Unauthorized'}), 403
    c = db()
    c.execute('UPDATE reviews SET responded=1 WHERE id=? AND api_key=?', (rid, key))
    c.commit(); c.close()
    return jsonify({'ok': True})

if __name__ == '__main__':
    port = int(os.getenv('PORT', 8081))
    print(f'ReviewHost - online op poort {port}')
    app.run(debug=False, host='0.0.0.0', port=port, threaded=True)
