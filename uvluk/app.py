import os
import sqlite3
import json
from datetime import datetime, timezone
from flask import Flask, request, render_template

app = Flask(__name__)

DB_PATH = '/app/data/uvluk.db'

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute('PRAGMA journal_mode=WAL;')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            ip TEXT,
            country TEXT,
            user_agent TEXT,
            method TEXT,
            path TEXT,
            username TEXT,
            password TEXT,
            event_type TEXT,
            raw_headers TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    # Set default template if not exists
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('active_template', 'generic')")
    conn.commit()
    conn.close()

def log_event(event_type, username=None, password=None):
    # Determine IP prioritizing Cloudflare headers
    ip = request.headers.get('CF-Connecting-IP', request.remote_addr)
    country = request.headers.get('CF-IPCountry', '')
    user_agent = request.user_agent.string
    method = request.method
    path = request.path
    timestamp = datetime.now(timezone.utc).isoformat()
    raw_headers = json.dumps(dict(request.headers))

    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute('PRAGMA journal_mode=WAL;')
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO events (timestamp, ip, country, user_agent, method, path, username, password, event_type, raw_headers)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (timestamp, ip, country, user_agent, method, path, username, password, event_type, raw_headers))
    conn.commit()
    conn.close()

@app.route('/', defaults={'path': ''}, methods=['GET', 'POST'])
@app.route('/<path:path>', methods=['GET', 'POST'])
def catch_all(path):
    actual_path = '/' + path
    
    # Get active template from DB
    conn = sqlite3.connect(DB_PATH)
    active_template_row = conn.execute("SELECT value FROM settings WHERE key='active_template'").fetchone()
    conn.close()
    
    active_template = active_template_row[0] if active_template_row else 'generic'

    # Check if it's a POST request (login attempt)
    if request.method == 'POST':
        username = request.form.get('username', request.form.get('log', request.form.get('email', request.form.get('user', ''))))
        password = request.form.get('password', request.form.get('pwd', request.form.get('pass', '')))
        
        log_event('login_attempt', username=username, password=password)
        
        # Render the appropriate error state for the active template
        if active_template == 'wordpress':
            return render_template('template_wp_login.html', error="The password you entered is incorrect.")
        elif active_template == 'fortinet':
            return render_template('template_fortinet.html', error="Permission Denied.")
        elif active_template == 'cpanel':
            return render_template('template_cpanel.html', error="The login is invalid.")
        elif active_template == 'phpmyadmin':
            return render_template('template_phpmyadmin.html', error="Access denied for user.")
        else:
            return render_template('template_generic.html', error="Invalid credentials. Please try again.")

    # GET requests routing based on active template
    if active_template == 'wordpress':
        wp_login_paths = ['/wp-admin', '/wp-login.php', '/login', '/admin']
        if actual_path in wp_login_paths:
            log_event('page_view')
            return render_template('template_wp_login.html')
        elif actual_path == '/':
            log_event('page_view')
            return render_template('template_wp_blog.html')
        else:
            log_event('path_probe')
            return "404 Not Found", 404

    elif active_template == 'fortinet':
        vpn_paths = ['/', '/remote/login', '/sslvpn/portal.html', '/admin']
        if actual_path in vpn_paths:
            log_event('page_view')
            return render_template('template_fortinet.html')
        else:
            log_event('path_probe')
            return "404 Not Found", 404

    elif active_template == 'cpanel':
        cpanel_paths = ['/', '/cpanel', '/whm', '/login', '/2083', '/2082']
        if actual_path in cpanel_paths:
            log_event('page_view')
            return render_template('template_cpanel.html')
        else:
            log_event('path_probe')
            return "404 Not Found", 404

    elif active_template == 'phpmyadmin':
        pma_paths = ['/', '/phpmyadmin', '/pma', '/mysql', '/dbadmin', '/login']
        if actual_path in pma_paths:
            log_event('page_view')
            return render_template('template_phpmyadmin.html')
        else:
            log_event('path_probe')
            return "404 Not Found", 404

    elif active_template == 'nginx':
        log_event('page_view')
        return render_template('template_nginx.html')

    elif active_template == 'apache':
        log_event('page_view')
        return render_template('template_apache.html')

    else:
        # Default Generic Enterprise Gateway
        common_paths = ['/', '/wp-admin', '/wp-login.php', '/.env', '/phpmyadmin', '/admin', '/login', '/api/login']
        if actual_path in common_paths:
            log_event('page_view')
            return render_template('template_generic.html')
        else:
            log_event('path_probe')
            return "404 Not Found", 404

if __name__ == '__main__':
    init_db()
    # Run on port 8080, accessible within the docker network
    app.run(host='0.0.0.0', port=8080)
