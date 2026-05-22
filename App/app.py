from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
import hmac
import json
import os
import smtplib
import ssl
import uuid
from email.message import EmailMessage

from flask import Flask, redirect, render_template_string, request, send_from_directory, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent
DATA_DIR = PROJECT_DIR / 'Data'
ASSETS_DIR = PROJECT_DIR / 'Assets'
USERS_FILE = DATA_DIR / 'access_users.json'


def load_env_file(path):
    if not path.exists():
        return
    for raw_line in path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env_file(PROJECT_DIR / '.env')
load_env_file(PROJECT_DIR / '.env.local')

users_file_default = Path('/tmp/access_users.json') if os.environ.get('VERCEL') else DATA_DIR / 'access_users.json'
users_file_override = os.environ.get('ACCESS_USERS_FILE')
USERS_FILE = Path(users_file_override) if users_file_override else users_file_default

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY') or (os.urandom(32) if os.environ.get('VERCEL') else 'dev-only-change-this-secret-key')
PER_PAGE = 10
PREVIEW_LIMIT = 100
ACCESS_NOTIFICATION_RECIPIENTS = [email.strip() for email in os.environ.get('ACCESS_NOTIFICATION_RECIPIENTS', '').split(',') if email.strip()]
APP_BASE_URL = os.environ.get('APP_BASE_URL', 'http://127.0.0.1:5000')
SMTP_HOST = os.environ.get('SMTP_HOST')
SMTP_PORT = int(os.environ.get('SMTP_PORT', '587'))
SMTP_USERNAME = os.environ.get('SMTP_USERNAME')
SMTP_PASSWORD = os.environ.get('SMTP_PASSWORD')
SMTP_FROM = os.environ.get('SMTP_FROM') or SMTP_USERNAME
SMTP_USE_SSL = os.environ.get('SMTP_USE_SSL', '').lower() in {'1', 'true', 'yes'}
ADMIN_USER_ID = 'vercel-env-admin'
ADMIN_EMAIL = os.environ.get('ADMIN_EMAIL', '').strip().lower()
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', '')
ADMIN_NAME = os.environ.get('ADMIN_NAME', 'Admin').strip() or 'Admin'

MISSING_DATA_NOTICE = 'Private data file is not available in this deployment. Add private storage or deploy with a secure data source.'


def load_member_data():
    data_file = DATA_DIR / (os.environ.get('MEMBERS_DATA_FILE') or 'cleaned_members.json')
    if not data_file.exists():
        print(f'Member data file not found: {data_file}')
        return {}
    with open(data_file, encoding='utf-8') as f:
        return json.load(f)


data = load_member_data()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def send_access_request_email(name, email, reason):
    if not SMTP_HOST or not SMTP_FROM or not ACCESS_NOTIFICATION_RECIPIENTS:
        return False, 'SMTP email recipients or SMTP settings are not configured.'

    admin_url = f"{APP_BASE_URL.rstrip('/')}/admin"
    message = EmailMessage()
    message['Subject'] = 'New Mansoura Uni data-base access request'
    message['From'] = SMTP_FROM
    message['To'] = ', '.join(ACCESS_NOTIFICATION_RECIPIENTS)
    message.set_content(
        f"New access request for Mansoura Uni data-base\n\n"
        f"Name: {name}\n"
        f"Email: {email}\n"
        f"Reason:\n{reason}\n\n"
        f"Review it here: {admin_url}\n"
    )

    try:
        if SMTP_USE_SSL:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ssl.create_default_context()) as server:
                if SMTP_USERNAME and SMTP_PASSWORD:
                    server.login(SMTP_USERNAME, SMTP_PASSWORD)
                server.send_message(message)
        else:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
                server.ehlo()
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
                if SMTP_USERNAME and SMTP_PASSWORD:
                    server.login(SMTP_USERNAME, SMTP_PASSWORD)
                server.send_message(message)
    except Exception as exc:
        print(f'Access request email failed: {exc}')
        return False, str(exc)

    return True, None


def env_admin_user():
    if not ADMIN_EMAIL or not ADMIN_PASSWORD:
        return None
    return {
        'id': ADMIN_USER_ID,
        'name': ADMIN_NAME,
        'email': ADMIN_EMAIL,
        'reason': 'Environment configured administrator',
        'role': 'admin',
        'created_at': 'environment',
        'auth_source': 'env',
    }


def load_stored_users():
    if not USERS_FILE.exists():
        return []
    with open(USERS_FILE, encoding='utf-8') as f:
        return json.load(f)


def load_users():
    users = load_stored_users()
    admin_user = env_admin_user()
    if not admin_user:
        return users
    users_without_duplicate_admin = [
        user for user in users
        if user.get('email', '').lower() != admin_user['email']
    ]
    return [admin_user] + users_without_duplicate_admin


def save_users(users):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    stored_users = [user for user in users if user.get('auth_source') != 'env']
    with open(USERS_FILE, 'w', encoding='utf-8') as f:
        json.dump(stored_users, f, ensure_ascii=False, indent=2)


def find_user_by_email(email):
    email = (email or '').strip().lower()
    return next((user for user in load_users() if user.get('email', '').lower() == email), None)


def find_user_by_id(user_id):
    return next((user for user in load_users() if user.get('id') == user_id), None)


def password_matches(user, password):
    if user.get('auth_source') == 'env':
        return bool(ADMIN_PASSWORD) and hmac.compare_digest(password, ADMIN_PASSWORD)
    return bool(user.get('password_hash')) and check_password_hash(user.get('password_hash', ''), password)


def current_user():
    user_id = session.get('user_id')
    if not user_id:
        return None
    return find_user_by_id(user_id)


def admin_exists():
    return any(user.get('role') == 'admin' for user in load_users())


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not admin_exists():
            return redirect(url_for('setup_admin'))
        if not current_user():
            return redirect(url_for('login'))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if not admin_exists():
            return redirect(url_for('setup_admin'))
        if not user:
            return redirect(url_for('login'))
        if user.get('role') != 'admin':
            return redirect(url_for('index'))
        return view(*args, **kwargs)
    return wrapped


def merge_duplicates(entries):
    merged = {}
    for entry in entries:
        name = (entry.get('name') or '').strip()
        if not name:
            continue
        key = name
        if key not in merged:
            merged[key] = entry.copy()
            for k in ['telephone', 'job', 'workplace', 'section']:
                merged[key][k] = set([merged[key].get(k, '').strip()]) if merged[key].get(k) else set()
        else:
            for k in ['telephone', 'job', 'workplace', 'section']:
                val = (entry.get(k) or '').strip()
                if val:
                    merged[key][k].add(val)

    result = []
    for v in merged.values():
        for k in ['telephone', 'job', 'workplace', 'section']:
            v[k] = ' | '.join(sorted(x for x in v[k] if x))
        result.append(v)
    return result


def get_all_entries():
    all_entries = []
    for section, entries in data.items():
        for entry in entries:
            entry = entry.copy()
            entry['section'] = section
            all_entries.append(entry)
    return merge_duplicates(all_entries)


def build_page_numbers(page, total_pages):
    pages = {1, total_pages}
    pages.update(range(max(1, page - 2), min(total_pages, page + 2) + 1))

    ordered_pages = []
    previous = 0
    for page_number in sorted(pages):
        if page_number - previous > 1:
            ordered_pages.append(None)
        ordered_pages.append(page_number)
        previous = page_number
    return ordered_pages


APP_CSS = '''
<style>
    :root {
        --navy: #10233f;
        --teal: #007c78;
        --gold: #f2ad21;
        --ink: #182235;
        --muted: #687386;
        --line: #dfe7ef;
        --paper: #ffffff;
        --soft: #f4f8fb;
        --danger: #c23b3b;
        --shadow: 0 24px 60px rgba(16, 35, 63, 0.14);
    }

    * { box-sizing: border-box; }

    body {
        min-height: 100vh;
        margin: 0;
        color: var(--ink);
        font-family: "Trebuchet MS", "Segoe UI", sans-serif;
        background:
            radial-gradient(circle at 8% 12%, rgba(242, 173, 33, 0.22), transparent 28rem),
            radial-gradient(circle at 92% 0%, rgba(0, 124, 120, 0.20), transparent 24rem),
            linear-gradient(135deg, #eef5f8 0%, #f8fbfd 48%, #edf3f1 100%);
    }

    .shell {
        width: min(1180px, calc(100% - 32px));
        margin: 0 auto;
        padding: 32px 0 44px;
    }

    .hero {
        position: relative;
        overflow: hidden;
        display: grid;
        grid-template-columns: 1fr auto;
        gap: 28px;
        align-items: center;
        padding: 34px;
        border: 1px solid rgba(255, 255, 255, 0.68);
        border-radius: 28px;
        color: #fff;
        background:
            linear-gradient(135deg, rgba(16, 35, 63, 0.96), rgba(0, 124, 120, 0.88)),
            linear-gradient(45deg, var(--navy), var(--teal));
        box-shadow: var(--shadow);
    }

    .hero::after {
        content: "";
        position: absolute;
        inset: auto -80px -140px auto;
        width: 310px;
        height: 310px;
        border-radius: 999px;
        background: rgba(242, 173, 33, 0.24);
    }

    .brand, .logo-card, .top-actions { position: relative; z-index: 1; }

    .eyebrow {
        margin: 0 0 12px;
        color: rgba(255, 255, 255, 0.80);
        font-size: 0.78rem;
        font-weight: 700;
        letter-spacing: 0.18em;
        text-transform: uppercase;
    }

    h1 {
        margin: 0;
        font-size: clamp(2.1rem, 5vw, 4rem);
        line-height: 0.98;
        letter-spacing: -0.06em;
    }

    h2 { margin: 0 0 14px; color: var(--navy); }

    .subtitle {
        max-width: 690px;
        margin: 16px 0 0;
        color: rgba(255, 255, 255, 0.82);
        font-size: 1.02rem;
        line-height: 1.65;
    }

    .logo-card {
        display: grid;
        place-items: center;
        width: 190px;
        min-height: 138px;
        padding: 22px;
        border-radius: 24px;
        background: rgba(255, 255, 255, 0.92);
        box-shadow: inset 0 0 0 1px rgba(16, 35, 63, 0.08), 0 18px 35px rgba(0, 0, 0, 0.16);
    }

    .logo-card img { width: 100%; height: auto; display: block; }

    .panel, .auth-card {
        margin-top: 24px;
        padding: 24px;
        border: 1px solid rgba(223, 231, 239, 0.9);
        border-radius: 24px;
        background: rgba(255, 255, 255, 0.88);
        box-shadow: 0 16px 40px rgba(16, 35, 63, 0.08);
        backdrop-filter: blur(12px);
    }

    .auth-card { max-width: 540px; margin-inline: auto; }

    .toolbar {
        display: grid;
        grid-template-columns: 1fr auto;
        gap: 18px;
        align-items: end;
    }

    label, .search-label {
        display: block;
        margin: 0 0 8px;
        color: var(--muted);
        font-size: 0.85rem;
        font-weight: 700;
        letter-spacing: 0.04em;
        text-transform: uppercase;
    }

    .field { margin-top: 16px; }
    .search-row, .inline-actions { display: flex; gap: 10px; flex-wrap: wrap; }

    input[type="text"], input[type="email"], input[type="password"], textarea {
        width: 100%;
        min-height: 48px;
        padding: 0 16px;
        border: 1px solid var(--line);
        border-radius: 14px;
        outline: none;
        color: var(--ink);
        font: inherit;
        background: #fff;
        box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.9);
        transition: border-color 160ms ease, box-shadow 160ms ease;
    }

    textarea { min-height: 110px; padding-top: 14px; resize: vertical; }
    .search-row input[type="text"] { width: min(620px, 100%); }

    input:focus, textarea:focus {
        border-color: var(--teal);
        box-shadow: 0 0 0 4px rgba(0, 124, 120, 0.13);
    }

    button, .button, .button-secondary, .button-danger {
        min-height: 48px;
        display: inline-grid;
        place-items: center;
        padding: 0 22px;
        border: 0;
        border-radius: 14px;
        color: #fff;
        font: inherit;
        font-weight: 800;
        text-decoration: none;
        cursor: pointer;
        background: linear-gradient(135deg, var(--teal), #005f68);
        box-shadow: 0 12px 24px rgba(0, 124, 120, 0.25);
        transition: transform 160ms ease, box-shadow 160ms ease;
    }

    button:hover, .button:hover, .button-secondary:hover, .button-danger:hover {
        transform: translateY(-1px);
        box-shadow: 0 16px 30px rgba(0, 124, 120, 0.30);
    }

    button:disabled {
        cursor: not-allowed;
        opacity: 0.55;
        transform: none;
        box-shadow: none;
    }

    .button-secondary {
        color: var(--navy);
        border: 1px solid var(--line);
        background: #fff;
        box-shadow: none;
    }

    .button-danger { background: linear-gradient(135deg, var(--danger), #912b2b); }
    .top-actions { display: flex; gap: 10px; justify-content: flex-end; flex-wrap: wrap; }

    .stats { display: flex; gap: 12px; flex-wrap: wrap; justify-content: flex-end; }

    .stat {
        min-width: 132px;
        padding: 13px 15px;
        border: 1px solid var(--line);
        border-radius: 18px;
        background: var(--soft);
    }

    .stat strong { display: block; color: var(--navy); font-size: 1.35rem; line-height: 1; }
    .stat span { display: block; margin-top: 6px; color: var(--muted); font-size: 0.78rem; font-weight: 700; text-transform: uppercase; }

    .notice, .error {
        margin: 18px 0 0;
        padding: 14px 16px;
        border-radius: 16px;
        font-weight: 700;
    }

    .notice { color: #295d2c; background: rgba(66, 160, 76, 0.12); border: 1px solid rgba(66, 160, 76, 0.22); }
    .error { color: #8f2525; background: rgba(194, 59, 59, 0.12); border: 1px solid rgba(194, 59, 59, 0.22); }

    .pager-summary { margin: 18px 0 0; color: var(--muted); font-size: 0.92rem; font-weight: 700; }

    .table-wrap {
        overflow: auto;
        margin-top: 22px;
        border: 1px solid var(--line);
        border-radius: 20px;
        background: var(--paper);
    }

    table { width: 100%; border-collapse: collapse; min-width: 880px; }
    th, td { padding: 15px 16px; text-align: left; border-bottom: 1px solid var(--line); vertical-align: top; }
    th { position: sticky; top: 0; z-index: 2; color: #fff; font-size: 0.78rem; letter-spacing: 0.08em; text-transform: uppercase; background: var(--navy); }
    tr:last-child td { border-bottom: 0; }
    tr:nth-child(even) td { background: #fbfdfe; }
    tr:hover td { background: rgba(242, 173, 33, 0.10); }
    td:first-child { font-weight: 800; color: var(--navy); }

    .role-pill {
        display: inline-flex;
        padding: 6px 10px;
        border-radius: 999px;
        color: var(--navy);
        font-size: 0.78rem;
        font-weight: 900;
        text-transform: uppercase;
        background: rgba(242, 173, 33, 0.18);
    }

    .pagination { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; justify-content: center; margin-top: 22px; }
    .page-link, .page-current, .page-gap { min-width: 42px; min-height: 42px; display: inline-grid; place-items: center; padding: 0 12px; border-radius: 13px; font-weight: 800; text-decoration: none; }
    .page-link { border: 1px solid var(--line); color: var(--navy); background: #fff; transition: transform 160ms ease, border-color 160ms ease, box-shadow 160ms ease; }
    .page-link:hover { transform: translateY(-1px); border-color: var(--teal); box-shadow: 0 10px 22px rgba(16, 35, 63, 0.10); }
    .page-current { color: #fff; background: linear-gradient(135deg, var(--teal), var(--navy)); box-shadow: 0 10px 22px rgba(0, 124, 120, 0.18); }
    .page-gap { color: var(--muted); }

    .empty { margin: 22px 0 0; padding: 34px; border: 1px dashed #b8c5d4; border-radius: 20px; color: var(--muted); text-align: center; background: rgba(244, 248, 251, 0.78); }

    @media (max-width: 780px) {
        .shell { width: min(100% - 20px, 1180px); padding-top: 10px; }
        .hero, .toolbar { grid-template-columns: 1fr; }
        .hero { padding: 24px; }
        .logo-card { width: 160px; }
        .search-row, .stats, .top-actions { flex-direction: column; justify-content: stretch; }
        button, .button, .button-secondary, .button-danger, .search-row input[type="text"] { width: 100%; }
    }
</style>
'''


BASE_HEAD = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Mansoura Uni data-base</title>
    <link rel="icon" href="{{ url_for('logo') }}">
    ''' + APP_CSS + '''
</head>
'''


HERO = '''
<section class="hero">
    <div class="brand">
        <p class="eyebrow">Mansoura University Directory</p>
        <h1>Mansoura Uni data-base</h1>
        <p class="subtitle">
            A protected, searchable staff and member directory for names, jobs, workplaces,
            phone numbers, and university sections.
        </p>
    </div>
    <div>
        <div class="top-actions">
            {% if user %}
                {% if user.role == 'admin' %}<a class="button-secondary" href="{{ url_for('admin') }}">Admin</a>{% endif %}
                <a class="button-secondary" href="{{ url_for('logout') }}">Logout</a>
            {% endif %}
        </div>
        <div class="logo-card" aria-label="Mansoura University logo" style="margin-top: 14px;">
            <img src="{{ url_for('logo') }}" alt="Mansoura University logo">
        </div>
    </div>
</section>
'''


INDEX_TEMPLATE = BASE_HEAD + '''
<body>
    <main class="shell">
        ''' + HERO + '''
        <section class="panel">
            {% if missing_data_notice %}<p class="error">{{ missing_data_notice }}</p>{% endif %}
            <form method="get" class="toolbar">
                <div>
                    <label class="search-label" for="q">Search the database</label>
                    <div class="search-row">
                        <input id="q" type="text" name="q" placeholder="Search by name, job, workplace, phone, or section..." value="{{ q }}">
                        <button type="submit">Search</button>
                    </div>
                    {% if access_limited %}
                        <p class="notice">Preview access is active. You can search and view only the first {{ preview_limit }} records.</p>
                    {% endif %}
                </div>
                <div class="stats" aria-label="Database statistics">
                    <div class="stat"><strong>{{ results_count }}</strong><span>Shown</span></div>
                    <div class="stat"><strong>{{ visible_count }}</strong><span>Accessible</span></div>
                    <div class="stat"><strong>{{ matched_count }}</strong><span>Matches</span></div>
                    <div class="stat"><strong>{{ total_count }}</strong><span>Total records</span></div>
                </div>
            </form>

            {% if results %}
                <p class="pager-summary">Showing {{ start_index }}-{{ end_index }} of {{ visible_count }} accessible records.</p>
                <div class="table-wrap">
                    <table>
                        <tr>
                            <th>Name</th>
                            <th>Job</th>
                            <th>Workplace</th>
                            <th>Phone</th>
                            <th>Section</th>
                        </tr>
                        {% for e in results %}
                        <tr>
                            <td>{{ e.name }}</td>
                            <td>{{ e.job }}</td>
                            <td>{{ e.workplace }}</td>
                            <td>{{ e.telephone }}</td>
                            <td>{{ e.section }}</td>
                        </tr>
                        {% endfor %}
                    </table>
                </div>

                {% if total_pages > 1 %}
                    <nav class="pagination" aria-label="Pagination">
                        {% if page > 1 %}<a class="page-link" href="{{ url_for('index', q=q, page=page - 1) }}">Previous</a>{% endif %}
                        {% for page_number in page_numbers %}
                            {% if page_number %}
                                {% if page_number == page %}
                                    <span class="page-current" aria-current="page">{{ page_number }}</span>
                                {% else %}
                                    <a class="page-link" href="{{ url_for('index', q=q, page=page_number) }}">{{ page_number }}</a>
                                {% endif %}
                            {% else %}
                                <span class="page-gap">...</span>
                            {% endif %}
                        {% endfor %}
                        {% if page < total_pages %}<a class="page-link" href="{{ url_for('index', q=q, page=page + 1) }}">Next</a>{% endif %}
                    </nav>
                {% endif %}
            {% else %}
                <div class="empty">No matching records found. Try a different name, workplace, phone number, or section.</div>
            {% endif %}
        </section>
    </main>
</body>
</html>
'''


AUTH_TEMPLATE = BASE_HEAD + '''
<body>
    <main class="shell">
        ''' + HERO + '''
        <section class="auth-card">
            <h2>{{ title }}</h2>
            <p style="color: var(--muted); line-height: 1.6;">{{ message }}</p>
            {% if error %}<p class="error">{{ error }}</p>{% endif %}
            {% if notice %}<p class="notice">{{ notice }}</p>{% endif %}
            <form method="post">
                {% if show_name %}
                    <div class="field"><label for="name">Name</label><input id="name" name="name" type="text" required></div>
                {% endif %}
                <div class="field"><label for="email">Email</label><input id="email" name="email" type="email" required></div>
                <div class="field"><label for="password">{{ password_label }}</label><input id="password" name="password" type="password" minlength="6" required></div>
                {% if show_reason %}
                    <div class="field"><label for="reason">Reason for access</label><textarea id="reason" name="reason" required></textarea></div>
                {% endif %}
                <div class="field inline-actions">
                    <button type="submit">{{ button_text }}</button>
                    {% if secondary_href %}<a class="button-secondary" href="{{ secondary_href }}">{{ secondary_text }}</a>{% endif %}
                </div>
            </form>
        </section>
    </main>
</body>
</html>
'''


WAITING_TEMPLATE = BASE_HEAD + '''
<body>
    <main class="shell">
        ''' + HERO + '''
        <section class="auth-card">
            <h2>Access request pending</h2>
            <p style="color: var(--muted); line-height: 1.6;">
                Your account is created, but it still needs approval from the admin before you can view the database.
            </p>
            <div class="inline-actions">
                <a class="button-secondary" href="{{ url_for('logout') }}">Logout</a>
            </div>
        </section>
    </main>
</body>
</html>
'''


ADMIN_TEMPLATE = BASE_HEAD + '''
<body>
    <main class="shell">
        ''' + HERO + '''
        <section class="panel">
            <h2>Access management</h2>
            <p style="color: var(--muted); line-height: 1.6;">
                Approve requests as full or preview users, contact applicants by email, or delete requests you do not need. Preview users can only access and search within the first {{ preview_limit }} records.
            </p>
            {% if error %}<p class="error">{{ error }}</p>{% endif %}
            {% if notice %}<p class="notice">{{ notice }}</p>{% endif %}
            <div class="table-wrap">
                <table>
                    <tr>
                        <th>Name</th>
                        <th>Email</th>
                        <th>Reason</th>
                        <th>Role</th>
                        <th>Actions</th>
                    </tr>
                    {% for account in users %}
                    <tr>
                        <td>{{ account.name }}</td>
                        <td>{{ account.email }}</td>
                        <td>{{ account.reason }}</td>
                        <td><span class="role-pill">{{ account.role }}</span></td>
                        <td>
                            <form method="post" class="inline-actions">
                                <input type="hidden" name="user_id" value="{{ account.id }}">
                                <button name="role" value="full" type="submit">Full</button>
                                <button name="role" value="preview" type="submit">Preview</button>
                                <a class="button-secondary" href="mailto:{{ account.email }}?subject=Mansoura%20Uni%20data-base%20access%20request">Contact</a>
                                <button name="role" value="delete" type="submit" class="button-danger" onclick="return confirm('Delete this user/request?')" {% if account.id == user.id %}disabled title="You cannot delete your own admin account"{% endif %}>Delete</button>
                            </form>
                        </td>
                    </tr>
                    {% endfor %}
                </table>
            </div>
        </section>
    </main>
</body>
</html>
'''


@app.route('/logo.png')
def logo():
    return send_from_directory(ASSETS_DIR, 'logo.png')


@app.route('/setup-admin', methods=['GET', 'POST'])
def setup_admin():
    if admin_exists():
        return redirect(url_for('login'))

    error = None
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        if not name or not email or len(password) < 6:
            error = 'Please enter a name, email, and password with at least 6 characters.'
        else:
            admin_user = {
                'id': str(uuid.uuid4()),
                'name': name,
                'email': email,
                'password_hash': generate_password_hash(password),
                'reason': 'Initial administrator',
                'role': 'admin',
                'created_at': now_iso(),
            }
            save_users([admin_user])
            session['user_id'] = admin_user['id']
            return redirect(url_for('index'))

    return render_template_string(
        AUTH_TEMPLATE,
        title='Create admin account',
        message='Create the first admin account. After this, new users must request access and wait for your approval.',
        button_text='Create admin',
        show_name=True,
        show_reason=False,
        secondary_href=None,
        secondary_text=None,
        password_label='Password',
        error=error,
        notice=None,
        user=None,
    )


@app.route('/request-access', methods=['GET', 'POST'])
def request_access():
    if not admin_exists():
        return redirect(url_for('setup_admin'))

    error = None
    notice = None
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        reason = request.form.get('reason', '').strip()
        if not name or not email or len(password) < 6 or not reason:
            error = 'Please fill all fields. Password must be at least 6 characters.'
        elif find_user_by_email(email):
            error = 'An account or request already exists for this email.'
        else:
            users = load_users()
            users.append({
                'id': str(uuid.uuid4()),
                'name': name,
                'email': email,
                'password_hash': generate_password_hash(password),
                'reason': reason,
                'role': 'pending',
                'created_at': now_iso(),
            })
            save_users(users)
            email_sent, email_error = send_access_request_email(name, email, reason)
            if email_sent:
                notice = 'Your request was sent. The admin was notified by email.'
            else:
                notice = f'Your request was saved in the admin page, but no email was sent yet. Configure SMTP settings in .env or Vercel environment variables. Details: {email_error}'

    return render_template_string(
        AUTH_TEMPLATE,
        title='Request database access',
        message='Create an account request. The admin will choose preview access or full access for you.',
        button_text='Send request',
        show_name=True,
        show_reason=True,
        secondary_href=url_for('login'),
        secondary_text='Back to login',
        password_label='Create a password for your account',
        error=error,
        notice=notice,
        user=None,
    )


@app.route('/login', methods=['GET', 'POST'])
def login():
    if not admin_exists():
        return redirect(url_for('setup_admin'))

    error = None
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        user = find_user_by_email(email)
        if not user or not password_matches(user, password):
            error = 'Invalid email or password.'
        else:
            session['user_id'] = user['id']
            return redirect(url_for('index'))

    return render_template_string(
        AUTH_TEMPLATE,
        title='Sign in',
        message='Sign in to access the Mansoura Uni data-base. If you do not have access yet, request it first.',
        button_text='Sign in',
        show_name=False,
        show_reason=False,
        secondary_href=url_for('request_access'),
        secondary_text='Request access',
        password_label='Password',
        error=error,
        notice=None,
        user=None,
    )


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route('/admin', methods=['GET', 'POST'])
@admin_required
def admin():
    error = None
    notice = None
    admin_user = current_user()

    if request.method == 'POST':
        target_id = request.form.get('user_id')
        action = request.form.get('role')
        allowed_actions = {'preview', 'full', 'delete'}
        users = load_users()
        target = next((account for account in users if account.get('id') == target_id), None)

        if action not in allowed_actions or not target:
            error = 'Invalid user or action.'
        elif target.get('id') == admin_user.get('id'):
            error = 'You cannot change or delete your own admin account from this page.'
        elif target.get('auth_source') == 'env':
            error = 'The Vercel admin account is controlled by environment variables.'
        elif action == 'delete':
            users = [account for account in users if account.get('id') != target_id]
            save_users(users)
            notice = f"Deleted {target.get('email')} from access requests."
        else:
            target['role'] = action
            save_users(users)
            notice = f"Updated {target.get('email')} to {action} access."

    users = sorted(load_users(), key=lambda item: (item.get('role') != 'pending', item.get('created_at', '')))
    return render_template_string(
        ADMIN_TEMPLATE,
        users=users,
        user=admin_user,
        preview_limit=PREVIEW_LIMIT,
        error=error,
        notice=notice,
    )


@app.route('/')
@login_required
def index():
    user = current_user()
    role = user.get('role')
    if role == 'pending':
        return render_template_string(WAITING_TEMPLATE, user=user)
    if role not in {'preview', 'full', 'admin'}:
        session.clear()
        return redirect(url_for('login'))

    q = request.args.get('q', '').strip()
    page = request.args.get('page', 1, type=int) or 1
    all_entries = get_all_entries()
    access_limited = role == 'preview'
    searchable_entries = all_entries[:PREVIEW_LIMIT] if access_limited else all_entries

    if q:
        q_lower = q.lower()
        visible_results = [
            e for e in searchable_entries
            if any(q_lower in str(e.get(k, '')).lower() for k in ['name', 'job', 'workplace', 'telephone', 'section'])
        ]
    else:
        visible_results = searchable_entries

    matched_count = len(visible_results)
    visible_count = len(visible_results)
    total_pages = max(1, (visible_count + PER_PAGE - 1) // PER_PAGE)
    page = min(max(page, 1), total_pages)
    start = (page - 1) * PER_PAGE
    end = start + PER_PAGE
    results = visible_results[start:end]

    return render_template_string(
        INDEX_TEMPLATE,
        results=results,
        q=q,
        page=page,
        total_pages=total_pages,
        page_numbers=build_page_numbers(page, total_pages),
        start_index=start + 1 if visible_count else 0,
        end_index=min(end, visible_count),
        results_count=len(results),
        visible_count=visible_count,
        matched_count=matched_count,
        total_count=len(all_entries),
        missing_data_notice=MISSING_DATA_NOTICE if not data else None,
        access_limited=access_limited,
        preview_limit=PREVIEW_LIMIT,
        user=user,
    )


if __name__ == '__main__':
    app.run(debug=True)













