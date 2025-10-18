# app/tools/registration_bot.py
import asyncio
import time
import random
import string
import re
import json
import os
import ssl
import imaplib
import subprocess
import email
import threading
import socks
from email.header import decode_header
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict, Optional, Set, Tuple, Awaitable, Callable
from dataclasses import dataclass

from app.config import settings
from app.logging_setup import get_logger

log = get_logger("klazfiler.registration")

# === Настройки ===
API_BASE_URL = (settings.SUITEPRO_API_URL or "https://api.suitepro.to").rstrip("/")
SMS_ACTIVATE_API_BASE_URL = settings.SMS_ACTIVATE_URL
MAX_WAIT_TIME = int(settings.REG_MAX_WAIT_SMS)
EMAIL_CHECK_INTERVAL = int(settings.REG_EMAIL_CHECK_INTERVAL)
MAX_THREADS = int(settings.REG_MAX_THREADS)
MAX_IMAP_WORKERS = int(settings.REG_MAX_IMAP_WORKERS)
CONNECTION_TIMEOUT = int(settings.REG_CONNECTION_TIMEOUT)
LOGIN_TIMEOUT = int(settings.REG_LOGIN_TIMEOUT)

SOCKS5_HOST = settings.REG_SOCKS5_HOST
SOCKS5_PORT = int(settings.REG_SOCKS5_PORT)
SOCKS5_USERNAME_TEMPLATE = settings.REG_SOCKS5_USER_TEMPLATE
SOCKS5_PASSWORD = settings.REG_SOCKS5_PASSWORD
USE_PROXY = bool(settings.REG_USE_PROXY)

# Коды стран — как просил
COUNTRY_CODES = [117, 34, 56, 49]  # можно сузить в .env при желании

# Глобальный лимитер: не больше 2 регистраций за 11 минут
_LIMIT_WINDOW_SEC = 11 * 60
_LIMIT_MAX_REG = 2
_rate_lock = threading.Lock()
_rate_timestamps: List[float] = []  # моменты вызовов /accounts/register

def _rate_limit_blocking():
    """Блокирует поток до разрешения делать новый вызов регистрации SuitePro."""
    while True:
        now = time.time()
        with _rate_lock:
            while _rate_timestamps and (now - _rate_timestamps[0]) > _LIMIT_WINDOW_SEC:
                _rate_timestamps.pop(0)
            if len(_rate_timestamps) < _LIMIT_MAX_REG:
                _rate_timestamps.append(now)
                return
            wait_sec = _LIMIT_WINDOW_SEC - (now - _rate_timestamps[0])
        time.sleep(max(1.0, min(wait_sec, 10.0)))

# Статистика
success_count_lock = threading.Lock()
success_count = 0

# --- модели ---
@dataclass
class IMAPSettings:
    host: str
    port: int
    ssl: bool
    email: str
    password: str
    proxy_session: Optional[str] = None
    ssl_method: Optional[str] = None

@dataclass
class EmailAccount:
    email: str
    password: str
    imap_settings: Optional[IMAPSettings] = None
    proxy_session: Optional[str] = None

# Известные IMAP
KNOWN_PROVIDERS = {
    'gmail.com': {'host': 'imap.gmail.com', 'port': 993, 'ssl': True},
    'outlook.com': {'host': 'outlook.office365.com', 'port': 993, 'ssl': True},
    'hotmail.com': {'host': 'outlook.office365.com', 'port': 993, 'ssl': True},
    'yahoo.com': {'host': 'imap.mail.yahoo.com', 'port': 993, 'ssl': True},
    'msn.com': {'host': 'outlook.office365.com', 'port': 993, 'ssl': True},
    'gmx.com': {'host': 'imap.gmx.com', 'port': 993, 'ssl': True},
    'gmx.net': {'host': 'imap.gmx.net', 'port': 993, 'ssl': True},
    'web.de': {'host': 'imap.web.de', 'port': 993, 'ssl': True},
    'mail.com': {'host': 'imap.mail.com', 'port': 993, 'ssl': True},
    'freenet.de': {'host': 'mx.freenet.de', 'port': 993, 'ssl': True},
    'ewetel.net': {'host': 'imap.ewe.net', 'port': 993, 'ssl': True},
    'ewe.net': {'host': 'imap.ewe.net', 'port': 993, 'ssl': True},
    't-online.de': {'host': 'secureimap.t-online.de', 'port': 993, 'ssl': True},
}

GERMAN_FIRST_NAMES = ["Alexander","Andreas","Bernd","Christian","Daniel","David","Dennis","Dieter","Erik","Felix","Florian","Frank","Hans","Jan","Jens","Johannes","Jonas","Klaus","Lars","Lukas","Marcel","Marco","Martin","Matthias","Max","Michael","Oliver","Patrick","Paul","Peter","Philipp","Robert","Sebastian","Stefan","Thomas","Tim"]
GERMAN_LAST_NAMES = ["Bauer","Beck","Becker","Fischer","Frank","Fuchs","Graf","Hartmann","Hoffmann","Klein","Koch","Krause","Lehmann","Ludwig","Maier","Meyer","Müller","Neumann","Richter","Schmidt","Schneider","Schulz","Schwarz","Wagner","Walter","Weber","Werner","Wolf"]

# --- утилиты ---
def safe_print(msg: str) -> None:
    log.info(msg)

def generate_session_id(length: int = 8) -> str:
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

@contextmanager
def proxy_socket(session_id: Optional[str] = None):
    if not USE_PROXY:
        yield
        return
    import socket
    if session_id is None:
        session_id = generate_session_id()
    username = SOCKS5_USERNAME_TEMPLATE.format(session_id=session_id)
    original = socket.socket
    try:
        socks.set_default_proxy(socks.SOCKS5, SOCKS5_HOST, SOCKS5_PORT, username=username, password=SOCKS5_PASSWORD)
        socket.socket = socks.socksocket
        yield session_id
    finally:
        socket.socket = original
        socks.set_default_proxy()

def extract_domain(email_addr: str) -> str:
    return email_addr.split('@')[-1].lower()

def get_mx_records_concurrent(domain: str) -> Set[str]:
    hosts = set()
    def nslookup():
        try:
            r = subprocess.run(['nslookup','-type=mx',domain], capture_output=True, text=True, timeout=5)
            out = []
            for line in r.stdout.splitlines():
                if 'mail exchanger' in line.lower() or '\tmx' in line.lower():
                    parts = line.split()
                    if parts:
                        h = parts[-1].rstrip('.')
                        if h and '=' not in h:
                            out.append(h)
                            if not h.startswith('imap'):
                                base = '.'.join(h.split('.')[1:]) if '.' in h else h
                                out.append(f'imap.{base}')
            return out
        except:
            return []
    try:
        import dns.resolver
        res = dns.resolver.Resolver()
        res.timeout = 5
        res.lifetime = 5
        for mx in res.resolve(domain, 'MX'):
            h = str(mx.exchange).rstrip('.')
            hosts.add(h)
            if not h.startswith('imap'):
                base = '.'.join(h.split('.')[1:]) if '.' in h else h
                hosts.add(f'imap.{base}')
    except:
        pass
    hosts.update(nslookup())
    return hosts

def create_ssl_ctx(method=None):
    try:
        if method == 'legacy':
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS)
            if hasattr(ssl, 'OP_LEGACY_SERVER_CONNECT'):
                ctx.options |= ssl.OP_LEGACY_SERVER_CONNECT
        elif method == 'tls1_2':
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLSv1_2)
        elif method == 'no_sni':
            ctx = ssl.create_default_context(); ctx.check_hostname = False
        elif method == 'permissive':
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS)
            ctx.options |= getattr(ssl, 'OP_ALL', 0)
            for flag in ('OP_NO_SSLv3','OP_NO_TLSv1','OP_NO_TLSv1_1'):
                if hasattr(ssl, flag):
                    ctx.options &= ~getattr(ssl, flag)
        else:
            ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        try: ctx.set_ciphers('DEFAULT@SECLEVEL=0')
        except: 
            try: ctx.set_ciphers('ALL')
            except: pass
        return ctx
    except Exception as e:
        safe_print(f"[SSL] ctx error {method}: {e}")
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

def test_imap_with_fallback(host: str, port: int, email_addr: str, password: str, use_ssl: bool=True, session_id: str|None=None) -> Tuple[bool,float,str,str|None]:
    import socket
    start = time.time()
    if session_id is None:
        session_id = generate_session_id()
    methods = [None,'permissive','legacy','tls1_2','no_sni'] if use_ssl else [None]
    for m in methods:
        try:
            with proxy_socket(session_id):
                orig = socket.getdefaulttimeout()
                socket.setdefaulttimeout(LOGIN_TIMEOUT)
                try:
                    if use_ssl:
                        imap = imaplib.IMAP4_SSL(host, port, ssl_context=create_ssl_ctx(m))
                    else:
                        imap = imaplib.IMAP4(host, port)
                    imap.login(email_addr, password)
                    imap.logout()
                    return True, time.time()-start, session_id, m
                finally:
                    socket.setdefaulttimeout(orig)
        except Exception:
            continue
    if use_ssl and port == 993:
        return test_imap_with_fallback(host, 143, email_addr, password, False, session_id)
    return False, 0.0, session_id, None

def quick_port(host: str, port: int, session_id: str|None=None) -> bool:
    import socket
    if session_id is None:
        session_id = generate_session_id()
    try:
        with proxy_socket(session_id):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(CONNECTION_TIMEOUT)
            ok = (s.connect_ex((host, port)) == 0)
            s.close()
            return ok
    except:
        return False

def find_imap_settings_fast(email_addr: str, password: str) -> Optional[IMAPSettings]:
    domain = extract_domain(email_addr)
    base_sid = generate_session_id()
    if domain in KNOWN_PROVIDERS:
        st = KNOWN_PROVIDERS[domain]
        ok, _, sid, m = test_imap_with_fallback(st['host'], st['port'], email_addr, password, st['ssl'], f"{base_sid}_known")
        if ok:
            return IMAPSettings(st['host'], st['port'], st['ssl'], email_addr, password, sid, m)
    hosts = list(get_mx_records_concurrent(domain))
    hosts += list({f'imap.{domain}', f'mail.{domain}', f'imap-mail.{domain}', f'secure.{domain}', f'email.{domain}', domain})
    hosts = list(set(hosts))
    ports = [(993, True), (143, False), (995, True), (110, False)]
    with ThreadPoolExecutor(max_workers=MAX_IMAP_WORKERS) as ex:
        futs = []
        for i, h in enumerate(hosts):
            for p, ssl_on in ports:
                sid = f"{base_sid}_{i}"
                if quick_port(h, p, sid):
                    futs.append((ex.submit(test_imap_with_fallback, h, p, email_addr, password, ssl_on, sid), h, p, ssl_on))
        for fut, h, p, ssl_on in futs:
            try:
                ok, _, sid, m = fut.result(timeout=LOGIN_TIMEOUT*2)
                if ok:
                    return IMAPSettings(h, p, ssl_on, email_addr, password, sid, m)
            except Exception:
                continue
    return None

def create_imap_with_retry(st: IMAPSettings, session_id: str|None=None, retries: int = 3):
    sid = session_id or st.proxy_session or generate_session_id()
    for _ in range(retries):
        try:
            with proxy_socket(sid):
                if st.ssl:
                    imap_conn = imaplib.IMAP4_SSL(st.host, st.port, ssl_context=create_ssl_ctx(st.ssl_method))
                else:
                    imap_conn = imaplib.IMAP4(st.host, st.port)
                imap_conn.login(st.email, st.password)
                return imap_conn
        except Exception:
            time.sleep(2)
    return None

def wait_for_verification_email(st: IMAPSettings, timeout: int = 120) -> Optional[str]:
    TARGET_SUBJECT = "Aktiviere dein Konto bei Kleinanzeigen"
    PATTERN = r'https://www\.kleinanzeigen\.de/m-benutzer-verifizieren\.html\?uuid=[a-f0-9\-]+[^"\s<>]*'
    FALLBACK = r'https?://[^\s<>"]*kleinanzeigen\.de/[^\s<>"]*uuid=[a-f0-9\-]+[^\s<>"]*'

    def _close(m):
        if not m: return
        try: m.close()
        except: pass
        try: m.logout()
        except: pass

    start = time.time()
    mail = None
    while time.time() - start < timeout:
        if mail is None:
            mail = create_imap_with_retry(st, st.proxy_session)
            if mail is None:
                time.sleep(EMAIL_CHECK_INTERVAL); continue
        try:
            mail.select('INBOX')
            ids = []
            for crit in ['(UNSEEN SUBJECT "Aktiviere dein Konto bei Kleinanzeigen")',
                         '(SUBJECT "Aktiviere dein Konto bei Kleinanzeigen")']:
                try:
                    typ, data = mail.search(None, crit)
                    if typ == 'OK' and data and data[0]:
                        ids = data[0].split() if isinstance(data[0], bytes) else str(data[0]).split()
                        if ids: break
                except Exception:
                    mail = None; break
            if not ids:
                time.sleep(EMAIL_CHECK_INTERVAL); continue
            for num in reversed(ids[-10:]):
                if isinstance(num, str): num = num.encode()
                typ, msg_data = mail.fetch(num, '(RFC822)')
                if typ != 'OK' or not msg_data: continue
                blob = None
                if isinstance(msg_data[0], tuple) and len(msg_data[0]) >= 2:
                    blob = msg_data[0][1]
                elif len(msg_data) > 1 and isinstance(msg_data[1], tuple) and len(msg_data[1]) >= 2:
                    blob = msg_data[1][1]
                if not isinstance(blob, (bytes, bytearray)): continue
                msg = email.message_from_bytes(blob)
                subject = msg.get('Subject', '')
                try:
                    subject = ''.join([(b.decode(enc or 'utf-8', errors='ignore') if isinstance(b, bytes) else str(b))
                                       for b, enc in decode_header(subject)])
                except Exception:
                    subject = str(subject)
                if TARGET_SUBJECT not in subject and "Aktiviere" not in subject:
                    continue
                sender = (msg.get('From', '') or '').lower()
                if 'kleinanzeigen' not in sender and 'ebay' not in sender:
                    continue
                html_body, text_body = "", ""
                if msg.is_multipart():
                    for part in msg.walk():
                        ctype = part.get_content_type()
                        if ctype == "text/html":
                            html_body += part.get_payload(decode=True).decode('utf-8', errors='ignore')
                        elif ctype == "text/plain":
                            text_body += part.get_payload(decode=True).decode('utf-8', errors='ignore')
                else:
                    content = msg.get_payload(decode=True).decode('utf-8', errors='ignore')
                    if '<html' in content.lower(): html_body = content
                    else: text_body = content
                body = html_body or text_body
                for rx in (PATTERN, FALLBACK):
                    m = re.findall(rx, body)
                    if m:
                        url = m[0].replace('&amp;', '&').rstrip('.,;:')
                        try: mail.store(num, '+FLAGS', '\\Seen')
                        except: pass
                        _close(mail)
                        return url
            time.sleep(EMAIL_CHECK_INTERVAL)
        except Exception:
            mail = None; time.sleep(EMAIL_CHECK_INTERVAL)
    try: _close(mail)
    except: pass
    return None

def generate_password() -> str:
    lower = random.choice(string.ascii_lowercase)
    upper = random.choice(string.ascii_uppercase)
    digit = random.choice(string.digits)
    symbol = random.choice('!@#$%^&*()-_=+')
    length = random.randint(8, 12)
    rest = ''.join(random.choices(string.ascii_letters + string.digits + '!@#$%^&*()-_=+', k=length-4))
    arr = list(lower + upper + digit + symbol + rest); random.shuffle(arr)
    return ''.join(arr)

def generate_contact_name() -> str:
    f = random.choice(GERMAN_FIRST_NAMES)
    l = random.choice(GERMAN_LAST_NAMES)
    t = random.randint(1,3)
    return f if t==1 else (f + " " + l[0] + "." if t==2 else f + " " + l)

def get_sms_balance(api_key: str) -> float:
    import requests
    try:
        r = requests.get(SMS_ACTIVATE_API_BASE_URL, params={"api_key": api_key, "action": "getBalance"}, timeout=30)
        r.raise_for_status()
        if r.text.startswith("ACCESS_BALANCE:"):
            return float(r.text.split(":")[1])
    except Exception:
        pass
    return 0.0

def order_phone(api_key: str, max_retries: int = 20) -> Optional[Dict]:
    import requests
    delay = 0.2
    for attempt in range(1, max_retries+1):
        try:
            params = {
                "api_key": api_key,
                "action": "getNumberV2",
                "service": "dh",
                "country": random.choice(COUNTRY_CODES),
            }
            # оператор не указываем
            r = requests.get(SMS_ACTIVATE_API_BASE_URL, params=params, timeout=30)
            r.raise_for_status()
            body = r.text.strip()
            try:
                data = r.json()
                safe_print(f"Ordered phone: +{data['phoneNumber']} (ID: {data['activationId']})")
                return {"activation_id": data["activationId"], "phone_number": data["phoneNumber"], "cost": data["activationCost"]}
            except Exception:
                if body.startswith("NO_NUMBERS"):
                    time.sleep(delay); delay *= 1.3; continue
                if body.startswith(("NO_BALANCE","BAD_KEY")):
                    safe_print(f"SMS-activate error: {body}")
                    return None
                safe_print(f"Unexpected getNumberV2 response: {body}")
                return None
        except Exception as e:
            safe_print(f"order_phone attempt {attempt}: {e}")
            time.sleep(min(delay, 10)); delay *= 1.3
    return None

def wait_sms(api_key: str, activation_id: str, max_wait: int = MAX_WAIT_TIME) -> Optional[str]:
    import requests
    safe_print(f"Waiting SMS (id={activation_id})…")
    start = time.time()
    while time.time() - start < max_wait:
        try:
            r = requests.get(SMS_ACTIVATE_API_BASE_URL, params={"api_key": api_key, "action": "getStatusV2", "id": activation_id}, timeout=30)
            r.raise_for_status()
            try:
                data = r.json()
                if data.get("sms"):
                    code = data["sms"].get("code") or re.search(r'(\d{6})', data["sms"].get("text","") or "")
                    return code if isinstance(code, str) else (code.group(1) if code else None)
            except Exception:
                if r.text.startswith("STATUS_OK:"):
                    return r.text.split(":")[1]
            time.sleep(5)
        except Exception:
            time.sleep(5)
    return None

def update_activation(api_key: str, activation_id: str, status: int) -> bool:
    import requests
    try:
        r = requests.get(SMS_ACTIVATE_API_BASE_URL, params={"api_key": api_key,"action":"setStatus","id":activation_id,"status":status}, timeout=30)
        return r.text.startswith("ACCESS_")
    except Exception:
        return False

def initiate_registration(suite_api_key: str, email_address: str) -> Dict:
    import requests
    # лимитер на вызов /accounts/register: 2 за 11 минут
    _rate_limit_blocking()

    password = generate_password()
    contact = generate_contact_name()
    payload = {"username": email_address, "password": password, "contactName": contact, "accountType": "PRIVATE"}
    r = requests.post(f"{API_BASE_URL}/accounts/register", headers={"X-API-Key": suite_api_key,"Content-Type":"application/json"}, json=payload, timeout=60)
    r.raise_for_status()
    data = r.json()
    data["username"] = email_address
    data["password"] = password
    data["contactName"] = contact
    return data

def start_phone_verification(suite_api_key: str, reg_data: Dict, verify_url: str, phone: str) -> Dict:
    import requests
    # код страны выбираем из заданного списка
    cc = str(random.choice(COUNTRY_CODES))
    national = phone.replace(cc, "", 1) if phone.startswith(cc) else phone
    payload = {
        "countryCallingCode": cc,
        "nationalNumber": national,
        "fingerprint": reg_data["fingerprint"],
        "url": verify_url,
        "fcmToken": reg_data["fcmToken"],
        "device": reg_data["device"],
        "proxyURL": "",
        "installedAt": reg_data["installedAt"],
        "previousSession": reg_data.get("session", ""),
        "lastProfileInterval": reg_data.get("lastProfileInterval", 0)
    }
    r = requests.post(f"{API_BASE_URL}/accounts/register/phone/start", headers={"X-API-Key": suite_api_key,"Content-Type":"application/json"}, json=payload, timeout=60)
    r.raise_for_status()
    data = r.json(); data["nationalNumber"] = national
    return data

def complete_phone_verification(suite_api_key: str, reg_data: Dict, verify_url: str, code: str) -> Dict:
    import requests
    payload = {
        "username": reg_data["username"],
        "password": reg_data["password"],
        "token": code,
        "fingerprint": reg_data["fingerprint"],
        "url": verify_url,
        "device": reg_data["device"],
        "proxyURL": "",
        "installedAt": reg_data["installedAt"],
        "privKey": reg_data["privKey"],
        "pubKey": reg_data["pubKey"],
        "fcmToken": reg_data["fcmToken"],
        "previousSession": reg_data.get("session", ""),
        "lastProfileInterval": reg_data.get("lastProfileInterval", 0),
        "autoLogin": True
    }
    r = requests.post(f"{API_BASE_URL}/accounts/register/phone/complete", headers={"X-API-Key": suite_api_key,"Content-Type":"application/json"}, json=payload, timeout=60)
    log.info("Complete status for %s: %s", reg_data["username"], r.status_code)
    r.raise_for_status()
    return r.json()

def save_account_info(email_addr: str, data: Dict, phone_info: Dict|None = None, st: IMAPSettings|None = None):
    info = {
        "email": email_addr,
        "password": data.get("password",""),
        "contactName": data.get("contactName",""),
        "registrationDate": time.strftime("%Y-%m-%d %H:%M:%S"),
        "emailPassword": st.password if st else "",
        "imapHost": st.host if st else "",
        "imapPort": st.port if st else "",
        "ssl": st.ssl if st else True,
        "sslMethod": st.ssl_method if st else None,
        "proxyUsed": USE_PROXY,
        "proxySession": st.proxy_session if st else None,
    }
    if phone_info:
        info["phoneNumber"] = phone_info.get("phone_number")
    with open("registered_accounts.txt","a",encoding="utf-8") as f:
        f.write(json.dumps(info, ensure_ascii=False) + "\n")

def _process_single(suite_key: str, sms_key: str, acc: EmailAccount) -> bool:
    activation_id = None
    try:
        if not acc.imap_settings:
            st = find_imap_settings_fast(acc.email, acc.password)
            if not st:
                safe_print(f"[IMAP] settings not found: {acc.email}")
                return False
            acc.imap_settings = st
        reg_data = initiate_registration(suite_key, acc.email)
        safe_print(f"[REG] initiated for {acc.email}")
        verify_url = wait_for_verification_email(acc.imap_settings, timeout=MAX_WAIT_TIME)
        if not verify_url:
            safe_print(f"[EMAIL] no verify mail: {acc.email}")
            return False
        phone = order_phone(sms_key)
        if not phone:
            safe_print("[SMS] no phone number")
            return False
        activation_id = phone["activation_id"]
        update_activation(sms_key, activation_id, 1)
        start_phone_verification(suite_key, reg_data, verify_url, phone["phone_number"])
        code = wait_sms(sms_key, activation_id, MAX_WAIT_TIME)
        if not code:
            update_activation(sms_key, activation_id, 8)
            safe_print("[SMS] code not received")
            return False
        complete_phone_verification(suite_key, reg_data, verify_url, code)
        update_activation(sms_key, activation_id, 6)
        save_account_info(acc.email, reg_data, phone, acc.imap_settings)
        global success_count
        with success_count_lock:
            success_count += 1
        safe_print(f"[REG] success {acc.email}")
        return True
    except Exception as e:
        safe_print(f"[REG] failed {acc.email}: {e}")
        if activation_id:
            update_activation(sms_key, activation_id, 8)
        return False

def _load_accounts_from_file(path: str) -> List[EmailAccount]:
    pairs: List[EmailAccount] = []
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        raw = f.read().replace("\r","\n").replace("|","\n")
    for line in [l.strip() for l in raw.splitlines() if l.strip()]:
        if ":" in line:
            email_addr, pwd = line.split(":", 1)
            pairs.append(EmailAccount(email=email_addr.strip().lower(), password=pwd.strip(), proxy_session=generate_session_id()))
    return pairs

# === точка входа из хендлера ===
async def run_registration_batch(
    *,
    file_path: str,
    notify: Callable[[str], Awaitable[None]],
    limit: Optional[int] = None,
    batch_size: int = 2,
    pause_sec: int = 0,
) -> None:
    suite_key = (settings.SUITEPRO_API_KEY or "").strip()
    sms_key = (settings.SMS_ACTIVATE_KEY or "").strip()

    if not suite_key:
        await notify("Нет SUITEPRO_API_KEY в .env.")
        return
    if not sms_key:
        await notify("Нет SMS_ACTIVATE_KEY в .env.")
        return

    bal = await asyncio.to_thread(get_sms_balance, sms_key)
    if bal < 2.0:
        await notify(f"Баланс SMS-Activate слишком мал: ${bal}")
        return

    accounts = await asyncio.to_thread(_load_accounts_from_file, file_path)
    if not accounts:
        await notify("Файл пустой или формат не распознан (нужно email:pass, по строкам).")
        return
    if isinstance(limit, int) and limit > 0:
        accounts = accounts[:limit]

    await notify(
        f"Запускаю регистрацию. Аккаунтов: {len(accounts)}. Потоки: {MAX_THREADS}. "
        f"SuitePro: {API_BASE_URL}. Лимит: 2 регистрации / 11 мин. "
        f"Коды стран: {', '.join(map(str, COUNTRY_CODES))}"
    )

    def _worker(acc: EmailAccount) -> Tuple[str, bool]:
        ok = _process_single(suite_key, sms_key, acc)
        return acc.email, ok

    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor(max_workers=MAX_THREADS) as pool:
        tasks = [loop.run_in_executor(pool, _worker, acc) for acc in accounts]
        done = 0
        for coro in asyncio.as_completed(tasks):
            email_addr, ok = await coro
            done += 1
            await notify(f"{'✅' if ok else '❌'} {email_addr} ({done}/{len(accounts)})")

    await notify("Готово. Смотри registered_accounts.txt.")
