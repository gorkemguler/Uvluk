import os
import sqlite3
import re
from functools import wraps
from flask import Flask, request, Response, render_template, jsonify, session, redirect, url_for
import pandas as pd

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'uvluk-super-secret-key-123')

DB_PATH = '/app/data/uvluk.db'

# Basic Auth Credentials from .env
AUTH_USER = os.environ.get('DASHBOARD_USER', 'admin')
AUTH_PASS = os.environ.get('DASHBOARD_PASS', 'admin')

def check_auth(username, password):
    return username == AUTH_USER and password == AUTH_PASS

def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if check_auth(username, password):
            session['logged_in'] = True
            return redirect(url_for('index'))
        else:
            error = 'Invalid Credentials. Please try again.'
    return render_template('login.html', error=error)

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('login'))

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # Ensure WAL mode
    conn.execute('PRAGMA journal_mode=WAL;')
    # Create whitelist table if not exists
    conn.execute('''
        CREATE TABLE IF NOT EXISTS whitelist (
            ip TEXT PRIMARY KEY,
            description TEXT,
            added_at TEXT
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    # Set default template if not exists
    conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('active_template', 'generic')")
    conn.commit()
    return conn

def fetch_data(exclude_whitelist=True):
    if not os.path.exists(DB_PATH):
        return pd.DataFrame()
    conn = get_db_connection()
    if exclude_whitelist:
        query = "SELECT * FROM events WHERE ip NOT IN (SELECT ip FROM whitelist)"
    else:
        query = "SELECT * FROM events"
    df = pd.read_sql_query(query, conn)
    conn.close()
    return df

def mask_ip(ip):
    if not ip or not isinstance(ip, str):
        return ip
    parts = ip.split('.')
    if len(parts) == 4:
        return f"{parts[0]}.{parts[1]}.{parts[2]}.xxx"
    # Handling IPv6 masking (basic)
    if ':' in ip:
        parts = ip.split(':')
        return ":".join(parts[:4]) + "::xxx"
    return ip

def mask_email(username):
    if not username or not isinstance(username, str):
        return username
    if '@' in username:
        return "***[EMAIL REDACTED]***"
    return username

def get_stats(df, is_public=False):
    if df.empty:
        return {}

    # Basic counters
    total_requests = len(df)
    unique_ips = df['ip'].nunique()
    login_attempts = len(df[df['event_type'] == 'login_attempt'])

    # Apply masking if public
    if is_public:
        df['ip'] = df['ip'].apply(mask_ip)
        df['username'] = df['username'].apply(mask_email)
        # Filter out rows where username was redacted, or just show redacted
        # Requirement: "o satırı gizle" -> If we need to hide the entire row, we can filter it out from Top 10 lists
        # Let's drop rows from Top 10 usernames where username contains @ for public view before grouping

    # Top 10 IPs
    top_ips = df['ip'].value_counts().head(10).reset_index().values.tolist()
    
    # Top 10 Paths
    top_paths = df['path'].value_counts().head(10).reset_index().values.tolist()
    
    # Top 10 User Agents
    top_uas = df['user_agent'].value_counts().head(10).reset_index().values.tolist()

    # Top Credentials
    if is_public:
        # Filter out redacted emails for the credentials list
        cred_df = df[(df['event_type'] == 'login_attempt') & (df['username'] != '***[EMAIL REDACTED]***')]
    else:
        cred_df = df[df['event_type'] == 'login_attempt']
    
    top_creds = cred_df.groupby(['username', 'password']).size().reset_index(name='count').sort_values(by='count', ascending=False).head(10).values.tolist()

    # Time series data (last 7 days by default)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    daily_counts = df.resample('D', on='timestamp').size().reset_index(name='count')
    daily_counts['date'] = daily_counts['timestamp'].dt.strftime('%Y-%m-%d')
    time_series = {'labels': daily_counts['date'].tolist(), 'data': daily_counts['count'].tolist()}

    return {
        'total_requests': total_requests,
        'unique_ips': unique_ips,
        'login_attempts': login_attempts,
        'top_ips': top_ips,
        'top_paths': top_paths,
        'top_uas': top_uas,
        'top_creds': top_creds,
        'time_series': time_series
    }

@app.route('/')
@requires_auth
def index():
    df = fetch_data()
    stats = get_stats(df, is_public=False)
    
    # Pagination for raw logs
    page = request.args.get('page', 1, type=int)
    per_page = 50
    
    if not df.empty:
        total_logs = len(df)
        total_pages = (total_logs + per_page - 1) // per_page
        
        # Sort by latest first
        df_sorted = df.sort_values(by='timestamp', ascending=False)
        start = (page - 1) * per_page
        end = start + per_page
        sliced_df = df_sorted.iloc[start:end].copy()
        
        # Ensure timestamp is formatted as string for JSON/Template compatibility
        if pd.api.types.is_datetime64_any_dtype(sliced_df['timestamp']):
            sliced_df['timestamp'] = sliced_df['timestamp'].dt.strftime('%Y-%m-%d %H:%M:%S')
        
        raw_logs = sliced_df.to_dict('records')
    else:
        raw_logs = []
        total_pages = 0
        total_logs = 0

    return render_template('dashboard.html', stats=stats, logs=raw_logs, page=page, total_pages=total_pages, total_logs=total_logs)

@app.route('/public')
def public_dashboard():
    df = fetch_data()
    stats = get_stats(df, is_public=True)
    return render_template('public.html', stats=stats)

@app.route('/api/stats')
def api_stats():
    # Simple API for auto-refresh
    df = fetch_data()
    is_public = request.args.get('public', 'false').lower() == 'true'
    stats = get_stats(df, is_public=is_public)
    return jsonify({
        'total_requests': stats.get('total_requests', 0),
        'unique_ips': stats.get('unique_ips', 0),
        'login_attempts': stats.get('login_attempts', 0)
    })

@app.route('/public/threat_feed.txt')
def threat_feed():
    df = fetch_data()
    if df.empty:
        return Response("", mimetype='text/plain')
    
    # Get unique IPs, potentially filter by those who made login attempts
    # For a threat feed, we usually want IPs that have done malicious actions
    suspicious_ips = df[df['event_type'].isin(['login_attempt', 'path_probe'])]['ip'].value_counts()
    
    # Generate simple text list
    feed_lines = [
        "# Uvluk Threat Feed",
        "# IPs detected actively scanning or attempting logins",
        "# Format: <IP> - <Reason Count>",
    ]
    for ip, count in suspicious_ips.items():
        feed_lines.append(ip)
        
    return Response("\n".join(feed_lines), mimetype='text/plain')

@app.route('/settings')
@requires_auth
def settings():
    conn = get_db_connection()
    whitelist_df = pd.read_sql_query("SELECT * FROM whitelist ORDER BY added_at DESC", conn)
    active_template_row = conn.execute("SELECT value FROM settings WHERE key='active_template'").fetchone()
    active_template = active_template_row['value'] if active_template_row else 'generic'
    conn.close()
    whitelist = whitelist_df.to_dict('records')
    return render_template('settings.html', whitelist=whitelist, active_template=active_template)

@app.route('/api/settings/template', methods=['POST'])
@requires_auth
def update_template():
    template = request.form.get('template')
    allowed_templates = ['generic', 'wordpress', 'fortinet', 'cpanel', 'phpmyadmin', 'nginx', 'apache']
    if template in allowed_templates:
        conn = get_db_connection()
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('active_template', ?)", (template,))
        conn.commit()
        conn.close()
    return redirect(url_for('settings'))

@app.route('/api/whitelist', methods=['POST'])
@requires_auth
def add_whitelist():
    data = request.form
    ip = data.get('ip')
    desc = data.get('description', '')
    if ip:
        from datetime import datetime
        now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
        conn = get_db_connection()
        try:
            conn.execute("INSERT OR REPLACE INTO whitelist (ip, description, added_at) VALUES (?, ?, ?)", (ip, desc, now))
            conn.commit()
        finally:
            conn.close()
    from flask import redirect, url_for
    return redirect(url_for('settings'))

@app.route('/api/whitelist/delete', methods=['POST'])
@requires_auth
def delete_whitelist():
    ip = request.form.get('ip')
    if ip:
        conn = get_db_connection()
        conn.execute("DELETE FROM whitelist WHERE ip = ?", (ip,))
        conn.commit()
        conn.close()
    from flask import redirect, url_for
    return redirect(url_for('settings'))

@app.route('/api/logs/delete', methods=['POST'])
@requires_auth
def delete_logs():
    ip = request.form.get('ip')
    if ip:
        conn = get_db_connection()
        conn.execute("DELETE FROM events WHERE ip = ?", (ip,))
        conn.commit()
        conn.close()
    from flask import redirect, url_for
    return redirect(request.referrer or url_for('settings'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5050)
