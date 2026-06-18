"""
Volledig automatisch outreach systeem voor ReviewHost.
- Zoekt bedrijven die veel Google reviews ontvangen
- Haalt contactemails op
- Stuurt gepersonaliseerde cold emails over AI review-antwoorden
- Verstuurt follow-ups na 3 dagen
"""
import sqlite3, smtplib, requests, time, random, re, os, builtins
from datetime import datetime, timedelta

_orig_print = builtins.print
def print(*args, **kwargs):
    _orig_print(f'[{datetime.now().strftime("%H:%M:%S")}]', *args, **kwargs)
builtins.print = print

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

GMAIL_ADDRESS  = os.getenv('GMAIL_ADDRESS', '')
GMAIL_PASSWORD = os.getenv('GMAIL_APP_PASSWORD', '')
BASE_URL       = os.getenv('BASE_URL', 'https://web-production-e2e5c.up.railway.app')
SENDER_NAME    = os.getenv('SENDER_NAME', 'Robin')

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36'
}

# Niches die veel Google reviews ontvangen
NICHES = {
    'restaurants':     'restaurant',
    'hair salons':     'kapsalon',
    'dentists':        'tandarts',
    'hotels':          'hotel',
    'beauty spas':     'schoonheidssalon',
    'gyms':            'sportschool',
    'bakeries':        'bakkerij',
    'auto repair':     'autogarage',
    'plumbers':        'loodgieter',
    'electricians':    'elektricien',
    'florists':        'bloemist',
    'lawyers':         'advocaat',
    'accountants':     'accountant',
    'physical therapy':'fysiotherapeut',
    'real estate agents': 'makelaar',
}
NICHE_KEYS = list(NICHES.keys())

STEDEN = [
    'Amsterdam, Netherlands', 'Rotterdam, Netherlands', 'Den Haag, Netherlands',
    'Utrecht, Netherlands', 'Eindhoven, Netherlands', 'Groningen, Netherlands',
    'Tilburg, Netherlands', 'Breda, Netherlands', 'Haarlem, Netherlands',
    'New York, NY', 'Los Angeles, CA', 'Chicago, IL', 'Houston, TX',
    'Phoenix, AZ', 'Philadelphia, PA', 'San Antonio, TX', 'San Diego, CA',
    'Dallas, TX', 'San Jose, CA', 'Austin, TX', 'Jacksonville, FL',
    'Fort Worth, TX', 'Columbus, OH', 'Charlotte, NC', 'San Francisco, CA',
    'Seattle, WA', 'Denver, CO', 'Nashville, TN', 'Las Vegas, NV',
    'Portland, OR', 'Atlanta, GA', 'Miami, FL', 'Minneapolis, MN',
    'Tampa, FL', 'Cleveland, OH', 'Pittsburgh, PA', 'Orlando, FL',
    'London, UK', 'Manchester, UK', 'Birmingham, UK', 'Leeds, UK',
    'Glasgow, UK', 'Liverpool, UK', 'Edinburgh, UK', 'Bristol, UK',
    'Toronto, Canada', 'Vancouver, Canada', 'Montreal, Canada', 'Calgary, Canada',
    'Sydney, Australia', 'Melbourne, Australia', 'Brisbane, Australia',
    'Perth, Australia', 'Adelaide, Australia',
    'Berlin, Germany', 'Munich, Germany', 'Hamburg, Germany', 'Cologne, Germany',
    'Frankfurt, Germany', 'Stuttgart, Germany',
    'Brussels, Belgium', 'Antwerp, Belgium', 'Ghent, Belgium',
    'Madrid, Spain', 'Barcelona, Spain', 'Valencia, Spain',
    'Rome, Italy', 'Milan, Italy', 'Naples, Italy', 'Florence, Italy',
    'Paris, France', 'Lyon, France', 'Marseille, France',
    'Dublin, Ireland', 'Cork, Ireland',
    'Lisbon, Portugal', 'Porto, Portugal',
    'Vienna, Austria', 'Zurich, Switzerland',
    'Stockholm, Sweden', 'Oslo, Norway', 'Copenhagen, Denmark',
    'Warsaw, Poland', 'Prague, Czech Republic', 'Budapest, Hungary',
    'Athens, Greece', 'Dubai, UAE', 'Singapore', 'Hong Kong',
    'Auckland, New Zealand', 'Wellington, New Zealand',
    'Cape Town, South Africa', 'Nairobi, Kenya',
    'Mexico City, Mexico', 'Sao Paulo, Brazil', 'Buenos Aires, Argentina',
]

# ── Database ────────────────────────────────────────────────
def db():
    conn = sqlite3.connect('C:\\Users\\r\\reviewhost\\outreach.db')
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    c = db()
    c.execute('''CREATE TABLE IF NOT EXISTS leads (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        url TEXT UNIQUE,
        business_name TEXT DEFAULT '',
        email TEXT DEFAULT '',
        niche TEXT DEFAULT '',
        city TEXT DEFAULT '',
        email_sent INTEGER DEFAULT 0,
        email_sent_at TEXT,
        followup_sent INTEGER DEFAULT 0,
        followup_sent_at TEXT,
        replied INTEGER DEFAULT 0,
        converted INTEGER DEFAULT 0,
        created_at TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS email_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        lead_id INTEGER,
        to_email TEXT,
        subject TEXT,
        type TEXT,
        sent_at TEXT
    )''')
    c.commit(); c.close()

init_db()

OSM_TAGS = {
    'restaurants':        [('amenity','restaurant'),('amenity','fast_food'),('amenity','cafe')],
    'hair salons':        [('shop','hairdresser'),('amenity','hairdresser')],
    'dentists':           [('amenity','dentist'),('healthcare','dentist')],
    'hotels':             [('tourism','hotel'),('tourism','motel')],
    'beauty spas':        [('amenity','spa'),('shop','beauty'),('leisure','spa')],
    'gyms':               [('leisure','fitness_centre'),('amenity','gym')],
    'bakeries':           [('shop','bakery')],
    'auto repair':        [('shop','car_repair'),('craft','car_repair')],
    'plumbers':           [('craft','plumber'),('office','plumber')],
    'electricians':       [('craft','electrician'),('office','electrician')],
    'florists':           [('shop','florist')],
    'lawyers':            [('office','lawyer')],
    'accountants':        [('office','accountant')],
    'physical therapy':   [('amenity','physiotherapist'),('healthcare','physiotherapist')],
    'real estate agents': [('office','estate_agent')],
}

_city_coords_cache = {}

def get_city_coords(city):
    if city in _city_coords_cache:
        return _city_coords_cache[city]
    try:
        r = requests.get('https://nominatim.openstreetmap.org/search',
            params={'q': city, 'format': 'json', 'limit': 1},
            headers={'User-Agent': 'ReviewHost-Outreach/1.0'}, timeout=10)
        data = r.json()
        if data:
            coords = float(data[0]['lat']), float(data[0]['lon'])
            _city_coords_cache[city] = coords
            return coords
    except: pass
    return None, None

def search_businesses(niche, city, count=10, radius=6000):
    lat, lon = get_city_coords(city)
    if not lat:
        print(f'  [OSM] Geocode mislukt voor {city}')
        return []
    tag_list = OSM_TAGS.get(niche, [('amenity','restaurant')])
    union_parts = []
    for key, val in tag_list:
        union_parts.append(f'node["{key}"="{val}"](around:{radius},{lat},{lon});')
        union_parts.append(f'way["{key}"="{val}"](around:{radius},{lat},{lon});')
    query = f'[out:json][timeout:30];({" ".join(union_parts)});out body;'
    try:
        r = requests.post('https://overpass-api.de/api/interpreter',
            data={'data': query},
            headers={'User-Agent': 'ReviewHost-Outreach/1.0'}, timeout=35)
        elements = r.json().get('elements', [])
    except Exception as e:
        print(f'  [OSM ERROR] {city}: {e}')
        return []
    random.shuffle(elements)
    urls = []
    for e in elements:
        if len(urls) >= count:
            break
        tags = e.get('tags', {})
        website = (tags.get('website') or tags.get('contact:website')
                   or tags.get('url') or tags.get('contact:url') or '')
        if not website:
            continue
        if not website.startswith('http'):
            website = 'https://' + website
        try:
            parsed = urlparse(website)
            base = f"{parsed.scheme}://{parsed.netloc}"
            if base not in urls and len(parsed.netloc) > 3:
                urls.append(base)
                name = tags.get('name', '?')
                try:
                    print(f'  [OSM] {name}: {base}')
                except UnicodeEncodeError:
                    print(f'  [OSM] (naam): {base}')
        except: continue
    return urls[:count]

def find_email(url):
    emails = set()
    pages = [url, urljoin(url, '/contact'), urljoin(url, '/over-ons'), urljoin(url, '/contact-us')]
    for page in pages:
        try:
            r = requests.get(page, headers=HEADERS, timeout=8)
            found = re.findall(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}', r.text)
            for e in found:
                if not any(x in e.lower() for x in ['example','test','placeholder','@sentry','@schema','@w3','noreply','no-reply']):
                    emails.add(e.lower())
            if emails: break
        except: continue
    if emails:
        for prefix in ['info@','contact@','hallo@','hello@','sales@']:
            for e in emails:
                if e.startswith(prefix): return e
        return sorted(emails)[0]
    return ''

def get_business_name(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=8)
        soup = BeautifulSoup(r.text, 'html.parser')
        og = soup.find('meta', property='og:site_name')
        if og and og.get('content'): return og['content'].strip()[:60]
        title = soup.find('title')
        if title: return title.text.split('|')[0].split('-')[0].strip()[:60]
    except: pass
    return urlparse(url).netloc.replace('www.','').split('.')[0].capitalize()

def detect_language(city: str) -> str:
    c = city.lower()
    if any(x in c for x in ['netherlands','amsterdam','rotterdam','den haag','utrecht','eindhoven',
        'groningen','tilburg','almere','breda','haarlem','antwerp','ghent','bruges']):
        return 'nl'
    if any(x in c for x in ['germany','berlin','munich','hamburg','cologne','frankfurt',
        'austria','vienna','zurich','switzerland']):
        return 'de'
    if any(x in c for x in ['france','paris','lyon','marseille','brussels','belgium','geneva']):
        return 'fr'
    if any(x in c for x in ['spain','madrid','barcelona','valencia','mexico','colombia',
        'peru','chile','argentina']):
        return 'es'
    if any(x in c for x in ['italy','rome','milan','naples','florence']):
        return 'it'
    if any(x in c for x in ['portugal','lisbon','porto','brazil','sao paulo','rio']):
        return 'pt'
    return 'en'

COPY = {
    'nl': {
        'tagline':    'AI Antwoorden op Google Reviews',
        'greeting':   'Beste {name},',
        'followup':   'Ik stuurde u vorige week een bericht - ik wil even controleren of het is aangekomen.',
        'intro':      'Ik bezocht <strong>{domain}</strong> en zag dat u actief reviews ontvangt op Google.',
        'hook':       'Elke onbeantwoorde review kost u klanten. Met ReviewHost beantwoordt AI elke review in 2 seconden:',
        'bullets':    ['✅&nbsp; Professioneel antwoord op elke review - positief en negatief',
                       '✅&nbsp; AI schrijft in de taal van de reviewer (NL, EN, DE, FR...)',
                       '✅&nbsp; Klaar in 2 seconden - kopieer en plak op Google',
                       '✅&nbsp; Bijhouden welke reviews al beantwoord zijn'],
        'cta':        'Zie een live voorbeeld',
        'price':      '<strong style="color:#333">7 dagen gratis proberen</strong> - geen creditcard nodig. Daarna slechts €54,50/maand.',
        'sign':       'Met vriendelijke groet,',
        'unsub':      'U ontvangt dit omdat uw bedrijf online vindbaar is.',
        'unsub_link': 'Uitschrijven',
        'subject':    'Uw Google reviews verdienen een antwoord',
        'subject_fu': 'Nog even - AI review-antwoorden voor {name}',
    },
    'en': {
        'tagline':    'AI Responses to Google Reviews',
        'greeting':   'Hi {name},',
        'followup':   'I reached out last week and just wanted to make sure my message got through.',
        'intro':      'I visited <strong>{domain}</strong> and noticed you are actively receiving Google reviews.',
        'hook':       'Every unanswered review costs you customers. With ReviewHost, AI responds to every review in 2 seconds:',
        'bullets':    ['✅&nbsp; Professional response to every review - positive and negative',
                       '✅&nbsp; AI writes in the reviewer\'s language (EN, NL, DE, FR...)',
                       '✅&nbsp; Ready in 2 seconds - copy and paste to Google',
                       '✅&nbsp; Track which reviews have already been answered'],
        'cta':        'See a live example',
        'price':      '<strong style="color:#333">7-day free trial</strong> - no credit card needed. Then just €54.50/month.',
        'sign':       'Best regards,',
        'unsub':      'You received this because your business is publicly listed online.',
        'unsub_link': 'Unsubscribe',
        'subject':    'Quick question about {domain} - Google reviews',
        'subject_fu': 'Following up about AI reviews for {name}',
    },
    'de': {
        'tagline':    'KI-Antworten auf Google-Bewertungen',
        'greeting':   'Sehr geehrte Damen und Herren,',
        'followup':   'Ich habe Ihnen letzte Woche eine Nachricht gesendet und wollte sicherstellen, dass sie ankam.',
        'intro':      'Ich habe <strong>{domain}</strong> besucht und gesehen, dass Sie aktiv Google-Bewertungen erhalten.',
        'hook':       'Jede unbeantwortete Bewertung kostet Sie Kunden. Mit ReviewHost antwortet KI auf jede Bewertung in 2 Sekunden:',
        'bullets':    ['✅&nbsp; Professionelle Antwort auf jede Bewertung - positiv und negativ',
                       '✅&nbsp; KI schreibt in der Sprache des Bewerters (DE, EN, NL, FR...)',
                       '✅&nbsp; Fertig in 2 Sekunden - kopieren und auf Google einfuegen',
                       '✅&nbsp; Verfolgen Sie, welche Bewertungen bereits beantwortet wurden'],
        'cta':        'Live-Beispiel ansehen',
        'price':      '<strong style="color:#333">7 Tage kostenlos</strong> - keine Kreditkarte. Danach nur 54,50 Euro/Monat.',
        'sign':       'Mit freundlichen Gruessen,',
        'unsub':      'Sie erhalten diese E-Mail, weil Ihr Unternehmen oeffentlich online gelistet ist.',
        'unsub_link': 'Abmelden',
        'subject':    'Kurze Frage zu {domain} - Google Bewertungen',
        'subject_fu': 'Nachfrage wegen KI-Reviews fuer {name}',
    },
    'fr': {
        'tagline':    'Reponses IA aux Avis Google',
        'greeting':   'Bonjour {name},',
        'followup':   'Je vous ai contacte la semaine derniere et voulais m\'assurer que mon message vous est parvenu.',
        'intro':      'J\'ai visite <strong>{domain}</strong> et j\'ai vu que vous recevez activement des avis Google.',
        'hook':       'Chaque avis sans reponse vous fait perdre des clients. Avec ReviewHost, l\'IA repond a chaque avis en 2 secondes:',
        'bullets':    ['✅&nbsp; Reponse professionnelle a chaque avis - positif et negatif',
                       '✅&nbsp; L\'IA ecrit dans la langue de l\'auteur (FR, EN, NL, DE...)',
                       '✅&nbsp; Pret en 2 secondes - copier-coller sur Google',
                       '✅&nbsp; Suivre quels avis ont deja ete traites'],
        'cta':        'Voir un exemple en direct',
        'price':      '<strong style="color:#333">7 jours gratuits</strong> - sans carte bancaire. Ensuite 54,50 euros/mois.',
        'sign':       'Cordialement,',
        'unsub':      'Vous recevez ceci car votre entreprise est referencee en ligne.',
        'unsub_link': 'Se desabonner',
        'subject':    'Question sur {domain} - avis Google',
        'subject_fu': 'Suivi pour {name}',
    },
    'es': {
        'tagline':    'Respuestas IA a Resenas de Google',
        'greeting':   'Hola {name},',
        'followup':   'Le contacte la semana pasada y queria asegurarme de que mi mensaje le llego.',
        'intro':      'Visite <strong>{domain}</strong> y vi que recibe activamente resenas de Google.',
        'hook':       'Cada resena sin respuesta le cuesta clientes. Con ReviewHost, la IA responde en 2 segundos:',
        'bullets':    ['✅&nbsp; Respuesta profesional a cada resena - positiva y negativa',
                       '✅&nbsp; IA escribe en el idioma del autor (ES, EN, NL, DE...)',
                       '✅&nbsp; Listo en 2 segundos - copiar y pegar en Google',
                       '✅&nbsp; Seguimiento de que resenas ya han sido respondidas'],
        'cta':        'Ver un ejemplo en vivo',
        'price':      '<strong style="color:#333">7 dias gratis</strong> - sin tarjeta. Despues 54,50 euros/mes.',
        'sign':       'Saludos cordiales,',
        'unsub':      'Recibe esto porque su empresa esta listada publicamente en linea.',
        'unsub_link': 'Darse de baja',
        'subject':    'Pregunta sobre {domain} - resenas Google',
        'subject_fu': 'Seguimiento para {name}',
    },
    'it': {
        'tagline':    'Risposte IA alle Recensioni Google',
        'greeting':   'Gentile {name},',
        'followup':   'La scorsa settimana le ho inviato un messaggio e volevo assicurarmi che fosse arrivato.',
        'intro':      'Ho visitato <strong>{domain}</strong> e ho visto che riceve attivamente recensioni su Google.',
        'hook':       'Ogni recensione senza risposta le costa clienti. Con ReviewHost, l\'IA risponde in 2 secondi:',
        'bullets':    ['✅&nbsp; Risposta professionale a ogni recensione - positiva e negativa',
                       '✅&nbsp; L\'IA scrive nella lingua dell\'autore (IT, EN, NL, DE...)',
                       '✅&nbsp; Pronto in 2 secondi - copia e incolla su Google',
                       '✅&nbsp; Tieni traccia di quali recensioni sono gia state gestite'],
        'cta':        'Guarda un esempio live',
        'price':      '<strong style="color:#333">7 giorni gratuiti</strong> - nessuna carta. Poi solo 54,50 euro/mese.',
        'sign':       'Cordiali saluti,',
        'unsub':      'Riceve questa email perche la sua azienda e elencata pubblicamente online.',
        'unsub_link': 'Annulla iscrizione',
        'subject':    'Domanda su {domain} - recensioni Google',
        'subject_fu': 'Aggiornamento per {name}',
    },
    'pt': {
        'tagline':    'Respostas IA a Avaliacoes Google',
        'greeting':   'Ola {name},',
        'followup':   'Entrei em contacto na semana passada e queria garantir que a minha mensagem chegou.',
        'intro':      'Visitei <strong>{domain}</strong> e vi que recebe ativamente avaliacoes no Google.',
        'hook':       'Cada avaliacao sem resposta custa-lhe clientes. Com ReviewHost, a IA responde em 2 segundos:',
        'bullets':    ['✅&nbsp; Resposta profissional a cada avaliacao - positiva e negativa',
                       '✅&nbsp; IA escreve na lingua do autor (PT, EN, NL, DE...)',
                       '✅&nbsp; Pronto em 2 segundos - copiar e colar no Google',
                       '✅&nbsp; Acompanhar quais avaliacoes ja foram respondidas'],
        'cta':        'Ver um exemplo ao vivo',
        'price':      '<strong style="color:#333">7 dias gratuitos</strong> - sem cartao. Depois 54,50 euros/mes.',
        'sign':       'Com os melhores cumprimentos,',
        'unsub':      'Recebe isto porque o seu negocio esta listado publicamente online.',
        'unsub_link': 'Cancelar subscricao',
        'subject':    'Pergunta sobre {domain} - avaliacoes Google',
        'subject_fu': 'Seguimento para {name}',
    },
}

def email_html(name, url, follow_up=False, city='', to_email=''):
    lang   = detect_language(city)
    t      = COPY[lang]
    domain = urlparse(url).netloc.replace('www.', '')
    clean_name = name if name and name.lower() not in ('there', '', '-') else ''

    subject = (t['subject_fu'].format(name=clean_name or domain)
               if follow_up else t['subject'].format(domain=domain, name=clean_name or domain))
    greeting = t['greeting'].format(name=clean_name) if clean_name else t['greeting'].format(name='')
    greeting = greeting.strip().rstrip(',').strip() + ',' if not clean_name and '{name}' in t['greeting'] else greeting

    followup_html = (f'<p style="margin:0 0 16px;font-size:15px;color:#333">{t["followup"]}</p>'
                     if follow_up else '')
    bullets_html = ''.join(f'<tr><td style="padding:8px 0;font-size:14px;color:#555">{b}</td></tr>'
                            for b in t['bullets'])

    body = f"""<!DOCTYPE html><html><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f5f5f5;font-family:Arial,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:30px 10px">
<table width="580" style="background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 20px rgba(0,0,0,.08)">
<tr><td style="background:linear-gradient(135deg,#ff6b35,#ff8c42);padding:30px 40px">
  <h1 style="color:#fff;margin:0;font-size:22px">ReviewHost</h1>
  <p style="color:rgba(255,255,255,.7);margin:6px 0 0;font-size:13px">{t['tagline']}</p>
</td></tr>
<tr><td style="padding:36px 40px">
  <p style="margin:0 0 16px;font-size:15px;color:#333">{greeting}</p>
  {followup_html}
  <p style="margin:0 0 16px;font-size:15px;color:#333">{t['intro'].format(domain=domain)}</p>
  <p style="font-size:15px;color:#333;margin:0 0 20px">{t['hook']}</p>
  <table style="margin:0 0 24px;width:100%">{bullets_html}</table>
  <table cellpadding="0" cellspacing="0" style="margin:0 auto 28px"><tr>
    <td style="background:linear-gradient(135deg,#ff6b35,#ff8c42);border-radius:8px;padding:14px 32px">
      <a href="{BASE_URL}?ref={domain}" style="color:#fff;text-decoration:none;font-size:15px;font-weight:700">{t['cta']}</a>
    </td>
  </tr></table>
  <p style="font-size:14px;color:#888;margin:0 0 6px">{t['price']}</p>
  <p style="font-size:15px;color:#333;margin:20px 0 0">{t['sign']}<br><strong>{SENDER_NAME}</strong><br>
  <span style="color:#888;font-size:13px">ReviewHost - <a href="{BASE_URL}" style="color:#ff6b35">{BASE_URL.replace('https://','').replace('http://','')}</a></span></p>
</td></tr>
<tr><td style="padding:16px 40px;border-top:1px solid #eee">
  <p style="font-size:11px;color:#aaa;margin:0">{t['unsub']}
    <a href="{BASE_URL}/unsubscribe?email={to_email}" style="color:#aaa">{t['unsub_link']}</a>
  </p>
</td></tr>
</table></td></tr></table>
</body></html>"""
    return subject, body

def send_email(to_email, business_name, url, follow_up=False, city=''):
    if not GMAIL_ADDRESS or not GMAIL_PASSWORD:
        print('[MAIL] Geen Gmail credentials.')
        return False
    try:
        subject, html = email_html(business_name, url, follow_up, city=city, to_email=to_email)
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From']    = f'{SENDER_NAME} <{GMAIL_ADDRESS}>'
        msg['To']      = to_email
        msg['Reply-To']= GMAIL_ADDRESS
        msg.attach(MIMEText(html, 'html'))
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as s:
            s.login(GMAIL_ADDRESS, GMAIL_PASSWORD)
            s.sendmail(GMAIL_ADDRESS, to_email, msg.as_string())
        print(f'[MAIL OK] {to_email} - {business_name}')
        return True
    except Exception as e:
        print(f'[MAIL FOUT] {to_email} - {e}')
        return False

def find_and_queue_leads(niche=None, city=None, count=10):
    yelp_key   = niche if niche in NICHES else random.choice(NICHE_KEYS)
    niche_name = NICHES.get(yelp_key, yelp_key)
    city       = city or random.choice(STEDEN)
    print(f'\n[FINDER] Zoeken: {niche_name} ({yelp_key}) in {city}...')
    urls = search_businesses(yelp_key, city, count)
    new_leads = 0
    c = db()
    for url in urls:
        existing = c.execute('SELECT id FROM leads WHERE url=?', (url,)).fetchone()
        if existing:
            continue
        time.sleep(random.uniform(1.5, 3.5))
        email = find_email(url)
        name  = get_business_name(url)
        print(f'  [LEAD] {url} | {name} | {email or "geen email"}')
        c.execute('''INSERT OR IGNORE INTO leads
            (url,business_name,email,niche,city,created_at)
            VALUES (?,?,?,?,?,?)''',
            (url, name, email, niche_name, city, datetime.now().isoformat()))
        c.commit()
        if email: new_leads += 1
    c.close()
    print(f'[FINDER] {new_leads} nieuwe leads met email gevonden in {city}')
    return new_leads

def send_cold_emails(max_per_day=100):
    c = db()
    leads = c.execute('''SELECT * FROM leads
        WHERE email != '' AND email_sent=0
        ORDER BY created_at ASC LIMIT ?''', (max_per_day,)).fetchall()
    sent = 0
    for lead in leads:
        lang = detect_language(lead['city'] or '')
        if send_email(lead['email'], lead['business_name'] or '', lead['url'], city=lead['city'] or ''):
            c.execute('UPDATE leads SET email_sent=1, email_sent_at=? WHERE id=?',
                (datetime.now().isoformat(), lead['id']))
            c.execute('INSERT INTO email_log (lead_id,to_email,subject,type,sent_at) VALUES (?,?,?,?,?)',
                (lead['id'], lead['email'], f'Cold outreach [{lang}]', 'cold', datetime.now().isoformat()))
            c.commit()
            sent += 1
            time.sleep(random.uniform(30, 90))
    c.close()
    print(f'[MAILER] {sent} cold emails verstuurd')
    return sent

def send_followups():
    cutoff = (datetime.now() - timedelta(days=3)).isoformat()
    c = db()
    leads = c.execute('''SELECT * FROM leads
        WHERE email_sent=1 AND followup_sent=0 AND replied=0
        AND email_sent_at < ? AND email != ''
        LIMIT 10''', (cutoff,)).fetchall()
    sent = 0
    for lead in leads:
        lang = detect_language(lead['city'] or '')
        if send_email(lead['email'], lead['business_name'] or '', lead['url'],
                      follow_up=True, city=lead['city'] or ''):
            c.execute('UPDATE leads SET followup_sent=1, followup_sent_at=? WHERE id=?',
                (datetime.now().isoformat(), lead['id']))
            c.execute('INSERT INTO email_log (lead_id,to_email,subject,type,sent_at) VALUES (?,?,?,?,?)',
                (lead['id'], lead['email'], f'Follow-up [{lang}]', 'followup', datetime.now().isoformat()))
            c.commit()
            sent += 1
            time.sleep(random.uniform(30, 90))
    c.close()
    print(f'[FOLLOWUP] {sent} follow-ups verstuurd')
    return sent

def daily_run():
    print(f'\n[AUTO] ReviewHost run gestart: {datetime.now().strftime("%d/%m/%Y %H:%M")}')
    cities_today = random.sample(STEDEN, min(50, len(STEDEN)))
    niches_today = random.sample(NICHE_KEYS, min(15, len(NICHE_KEYS)))
    print(f'[AUTO] {len(cities_today)} steden x {len(niches_today)} niches')
    print(f'[AUTO] Steden: {", ".join(c.split(",")[0] for c in cities_today)}')
    print(f'[AUTO] Niches: {", ".join(niches_today)}')
    for i, city in enumerate(cities_today):
        niche = niches_today[i % len(niches_today)]
        find_and_queue_leads(niche=niche, city=city, count=30)
        time.sleep(random.uniform(2, 5))
    send_cold_emails(max_per_day=300)
    send_followups()
    c = db()
    total   = c.execute('SELECT COUNT(*) FROM leads').fetchone()[0]
    emailed = c.execute('SELECT COUNT(*) FROM leads WHERE email_sent=1').fetchone()[0]
    c.close()
    print(f'\n[RAPPORT] Leads: {total} | Gemaild: {emailed}')

if __name__ == '__main__':
    daily_run()
