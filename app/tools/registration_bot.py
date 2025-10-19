# app/tools/registration_bot.py
# -*- coding: utf-8 -*-
"""
v2.2 — поштучная покупка почт: после полной обработки текущей — покупаем следующую.
Остальное поведение сохранено: лимит 2 регистрации за 11 минут, отмены ресурсов при сбоях,
аккуратные логи, страна SMS-Activate только 117.
"""

from __future__ import annotations

import asyncio
import html
import json
import os
import random
import re
import string
import time
from typing import List, Dict, Optional, Tuple, Awaitable, Callable

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from app.config import settings
from app.logging_setup import get_logger

log = get_logger("klazfiler.registration.v2")

# ==========================
# Константы/настройки
# ==========================

API_BASE_URL = (settings.SUITEPRO_API_URL or "https://api.suitepro.to").rstrip("/")
SMS_ACTIVATE_API_BASE_URL = settings.SMS_ACTIVATE_URL  # handler_api для телефонов

EMAIL_API_BASE = (
    getattr(settings, "SMS_ACTIVATE_EMAIL_API_URL", None)
    or os.getenv("SMS_ACTIVATE_EMAIL_API_URL")
    or "https://api.sms-activate.ae/api/v2/"
).rstrip("/") + "/"
EMAIL_API_KEY = (
    getattr(settings, "SMS_ACTIVATE_API_KEY", None)
    or os.getenv("SMS_ACTIVATE_API_KEY")
    or getattr(settings, "SMS_ACTIVATE_KEY", None)
    or ""
).strip()

SUITEPRO_HTTP_READ_TIMEOUT = 120  # сек (общий)
COMPLETE_TIMEOUT = 60            # сек (complete)

MAX_WAIT_TIME = int(getattr(settings, "REG_MAX_WAIT_SMS", os.getenv("REG_MAX_WAIT_SMS", 180)))
REG_POLL_TIMEOUT = int(getattr(settings, "REG_POLL_TIMEOUT", os.getenv("REG_POLL_TIMEOUT", 180)))
REG_POLL_INTERVAL = int(getattr(settings, "REG_POLL_INTERVAL", os.getenv("REG_POLL_INTERVAL", 5)))

REG_BATCH = int(getattr(settings, "REG_RATE_LIMIT_BATCH", os.getenv("REG_RATE_LIMIT_BATCH", 2)))
REG_INTERVAL_MIN = int(getattr(settings, "REG_RATE_LIMIT_INTERVAL_MIN", os.getenv("REG_RATE_LIMIT_INTERVAL_MIN", 11)))

REG_SITE = str(getattr(settings, "REG_SITE", os.getenv("REG_SITE", "kleinanzeigen.de")))
REG_MAIL_DOMAIN = str(getattr(settings, "REG_MAIL_DOMAIN", os.getenv("REG_MAIL_DOMAIN", "gmail.com")))
REG_CURRENCY = int(getattr(settings, "REG_CURRENCY", os.getenv("REG_CURRENCY", 840)))

VERIFICATION_URL_PATTERN = re.compile(
    r'https://www\.kleinanzeigen\.de/m-benutzer-verifizieren\.html\?uuid=[a-f0-9\-]+[^"\s<>]*',
    re.IGNORECASE,
)
URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)

# только 117
COUNTRY_CODES = [117]

# rate limit 2/11
_LIMIT_WINDOW_SEC = 11 * 60
_LIMIT_MAX_REG = 2
_rate_timestamps: List[float] = []

_session = requests.Session()
_retry = Retry(
    total=5,
    connect=5,
    read=5,
    status=5,
    backoff_factor=1.2,
    status_forcelist=(408, 429, 500, 502, 503, 504, 522, 524),
    allowed_methods=frozenset(["GET", "POST", "DELETE"]),
    raise_on_status=False,
)
_adapter = HTTPAdapter(max_retries=_retry, pool_connections=50, pool_maxsize=50)
_session.mount("https://", _adapter)
_session.mount("http://", _adapter)

_SENSITIVE_KEYS = {"password", "token", "privKey", "pubKey", "fcmToken", "fingerprint", "X-API-Key"}


def _mask(v: str) -> str:
    if not isinstance(v, str) or not v:
        return "***"
    return (v[:2] + "***" + v[-2:]) if len(v) > 6 else "***"

def _pln(k: str, v: object) -> None:
    val = str(v)
    if k in _SENSITIVE_KEYS:
        val = _mask(val)
    log.info("%s: %s", k, val)

def log_step(title: str) -> None:
    log.info("%s", title)

def log_kv_block(title: str, kv: Dict[str, object]) -> None:
    log.info("%s", title)
    for k, v in kv.items():
        _pln(k, v)


def _rate_limit_blocking():
    while True:
        now = time.time()
        while _rate_timestamps and (now - _rate_timestamps[0]) > _LIMIT_WINDOW_SEC:
            _rate_timestamps.pop(0)
        if len(_rate_timestamps) < _LIMIT_MAX_REG:
            _rate_timestamps.append(now)
            return
        wait_sec = _LIMIT_WINDOW_SEC - (now - _rate_timestamps[0])
        time.sleep(max(1.0, min(wait_sec, 10.0)))


def _sp_post(path: str, api_key: str, payload: Dict, timeout: int, max_attempts: int = 5) -> requests.Response:
    """POST с ретраями на сетевые/5xx. На 4xx — без ретраев, отдаём ответ."""
    url = f"{API_BASE_URL}{path}"
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "de-DE,de;q=0.9,en;q=0.8,ru;q=0.7",
        "Origin": "https://api.suitepro.to",
        "Referer": "https://api.suitepro.to/",
        "Content-Type": "application/json",
        "X-API-Key": api_key,
    }
    attempt = 0
    while True:
        attempt += 1
        log_step(f"[SP] POST {path} attempt={attempt}")
        t0 = time.time()
        try:
            r = _session.post(url, headers=headers, json=payload, timeout=(15, timeout))
            ms = int((time.time() - t0) * 1000)
            log_step(f"[SP] RESP {path} {r.status_code} in {ms}ms")
            if 400 <= r.status_code < 500:
                try:
                    body = r.json()
                    log_kv_block("[SP] ERROR", {k: body.get(k) for k in ("title", "message", "detail", "error", "code")})
                except Exception:
                    log_step(f"[SP] ERROR RAW: {r.text[:400]}")
                return r
            if r.status_code >= 500:
                if attempt >= max_attempts:
                    try:
                        log_step(f"[SP] ERROR RAW: {r.text[:400]}")
                    except Exception:
                        pass
                    log_step(f"[SP] FAIL {path} after {attempt} attempts: {r.status_code}")
                    return r
                sleep_s = 1.2 * attempt
                log_step(f"[SP] retry {path} after {r.status_code}; sleep {sleep_s:.1f}s")
                time.sleep(sleep_s)
                continue
            return r
        except requests.RequestException as e:
            if attempt >= max_attempts:
                log_step(f"[SP] FAIL {path} after {attempt} attempts: {e}")
                raise
            sleep_s = 1.2 * attempt
            log_step(f"[SP] retry {path} after error: {e}; sleep {sleep_s:.1f}s")
            time.sleep(sleep_s)


def generate_password() -> str:
    lower = random.choice(string.ascii_lowercase)
    upper = random.choice(string.ascii_uppercase)
    digit = random.choice(string.digits)
    symbol = random.choice('!@#$%^&*()-_=+')
    length = random.randint(8, 12)
    rest = ''.join(random.choices(string.ascii_letters + string.digits + '!@#$%^&*()-_=+', k=length - 4))
    arr = list(lower + upper + digit + symbol + rest)
    random.shuffle(arr)
    return ''.join(arr)

def generate_contact_name() -> str:
    first = random.choice(["Max", "Paul", "Leon", "Ben", "Luis", "Finn", "Luca", "Elias", "Noah", "Felix"])
    last = random.choice(["Müller", "Schmidt", "Schneider", "Fischer", "Weber", "Meyer", "Wagner", "Becker", "Hoffmann", "Schäfer"])
    return f"{first} {last}"

def initiate_registration(suite_api_key: str, email_address: str) -> Dict:
    _rate_limit_blocking()  # 2/11
    password = generate_password()
    contact = generate_contact_name()
    payload = {
        "username": email_address,
        "password": password,
        "contactName": contact,
        "accountType": "PRIVATE",
    }
    log_step(f"[INIT] email={email_address}")
    r = _sp_post("/accounts/register", suite_api_key, payload, timeout=SUITEPRO_HTTP_READ_TIMEOUT, max_attempts=5)
    r.raise_for_status()
    data = r.json()
    data["username"] = email_address
    data["password"] = password
    data["contactName"] = contact
    for key in ("fingerprint", "device", "fcmToken", "privKey", "pubKey", "installedAt"):
        if key in data:
            _pln(key, data[key])
    return data

def split_e164_phone(phone: str) -> tuple[str, str]:
    digits = re.sub(r"\D", "", phone or "")
    if digits.startswith("00"):
        digits = digits[2:]
    known_cc = {"371"}  # оставляем строго 371 для 117
    for k in (3, 2, 1):
        if len(digits) > k and digits[:k] in known_cc:
            return digits[:k], digits[k:]
    return (digits[:3], digits[3:]) if len(digits) > 3 else ("", digits)

def start_phone_verification(suite_api_key: str, reg_data: Dict, verify_url: str, phone: str) -> Dict:
    cc, national = split_e164_phone(phone)
    if not cc or not national:
        raise ValueError(f"Bad phone {phone}")
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
        "lastProfileInterval": reg_data.get("lastProfileInterval", 0),
    }
    log_kv_block("[PHONE start]", {"email": reg_data["username"], "cc": cc, "national": national})
    r = _sp_post("/accounts/register/phone/start", suite_api_key, payload, timeout=SUITEPRO_HTTP_READ_TIMEOUT, max_attempts=5)
    r.raise_for_status()
    return r.json()

def complete_phone_verification(suite_api_key: str, reg_data: Dict, verify_url: str, code: str) -> Tuple[bool, Optional[Dict], Optional[Dict]]:
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
        "autoLogin": True,
    }
    log_kv_block("[PHONE complete]", {"email": reg_data["username"], "code_len": len(code)})
    r = _sp_post("/accounts/register/phone/complete", suite_api_key, payload, timeout=COMPLETE_TIMEOUT, max_attempts=2)
    if r.status_code == 200:
        return True, r.json(), None
    try:
        body = r.json()
    except Exception:
        body = {"raw": r.text[:400]}
    return False, None, body


# ==========================
# SMS-Activate (handler_api)
# ==========================

def get_sms_balance(api_key: str) -> float:
    try:
        r = _session.get(SMS_ACTIVATE_API_BASE_URL, params={"api_key": api_key, "action": "getBalance"}, timeout=30)
        r.raise_for_status()
        if r.text.startswith("ACCESS_BALANCE:"):
            bal = float(r.text.split(":")[1])
            log_step(f"[SMS] balance=${bal:.2f}")
            return bal
    except Exception as e:
        log_step(f"[SMS] balance error: {e}")
    return 0.0

def order_phone(api_key: str, max_retries: int = 20) -> Optional[Dict]:
    delay = 0.2
    for attempt in range(1, max_retries + 1):
        try:
            params = {
                "api_key": api_key,
                "action": "getNumberV2",
                "service": "dh",
                "country": random.choice(COUNTRY_CODES),
            }
            r = _session.get(SMS_ACTIVATE_API_BASE_URL, params=params, timeout=30)
            r.raise_for_status()
            try:
                data = r.json()
                phone = {
                    "activation_id": data["activationId"],
                    "phone_number": data["phoneNumber"],
                    "cost": data.get("activationCost"),
                }
                log_kv_block("[SMS] number", {"id": phone["activation_id"], "phone": phone["phone_number"]})
                return phone
            except Exception:
                body = r.text.strip()
                if body.startswith("NO_NUMBERS"):
                    log_step("[SMS] NO_NUMBERS")
                    time.sleep(delay)
                    delay *= 1.3
                    continue
                if body.startswith(("NO_BALANCE", "BAD_KEY")):
                    log_step(f"[SMS] fail: {body}")
                    return None
                log_step(f"[SMS] unknown: {body[:120]}")
                return None
        except Exception as e:
            log_step(f"[SMS] getNumber error: {e}")
            time.sleep(min(delay, 10))
            delay *= 1.3
    return None

def wait_sms(api_key: str, activation_id: str, max_wait: int = MAX_WAIT_TIME) -> Optional[str]:
    start = time.time()
    heartbeat = start
    while time.time() - start < max_wait:
        try:
            r = _session.get(
                SMS_ACTIVATE_API_BASE_URL,
                params={"api_key": api_key, "action": "getStatusV2", "id": activation_id},
                timeout=30,
            )
            r.raise_for_status()
            try:
                data = r.json()
                if data.get("sms"):
                    text = data["sms"].get("text", "") or ""
                    m = re.search(r"(\d{6})", text)
                    if m:
                        code = m.group(1)
                        log_step("[SMS] code received")
                        return code
            except Exception:
                if r.text.startswith("STATUS_OK:"):
                    code = re.sub(r"\D", "", r.text.split(":")[1])[:6]
                    if code:
                        log_step("[SMS] code received (plain)")
                        return code
        except Exception:
            pass

        now = time.time()
        if now - heartbeat >= 30:
            log_step("[SMS] wait…")
            heartbeat = now
        time.sleep(5)
    log_step("[SMS] code timeout")
    return None

def update_activation(api_key: str, activation_id: str, status: int) -> bool:
    try:
        r = _session.get(
            SMS_ACTIVATE_API_BASE_URL,
            params={"api_key": api_key, "action": "setStatus", "id": activation_id, "status": status},
            timeout=30,
        )
        ok = r.text.startswith("ACCESS_")
        log_kv_block("[SMS] setStatus", {"id": activation_id, "status": status, "ok": ok})
        return ok
    except Exception as e:
        log_kv_block("[SMS] setStatus error", {"id": activation_id, "err": str(e)})
        return False


# ==========================
# Email rent v2 (почты)
# ==========================

def _email_headers() -> Dict[str, str]:
    return {"accept": "application/json", "Content-Type": "application/json", "X-API-Key": EMAIL_API_KEY}

def _email_url(path: str) -> str:
    return f"{EMAIL_API_BASE}{path.lstrip('/')}"

def _extract_email_id(item: Dict) -> Optional[int]:
    if item.get("id"):
        return int(item["id"])
    if item.get("kopeechkaId"):
        return int(item["kopeechkaId"])
    if item.get("userId"):
        return int(item["userId"])
    return None

def rent_emails_batch(count: int) -> Tuple[bool, List[Dict], str]:
    body = {"site": REG_SITE, "mailDomain": REG_MAIL_DOMAIN, "count": int(count), "currency": REG_CURRENCY}
    try:
        r = _session.post(_email_url("emails/batch"), headers=_email_headers(), json=body, timeout=(15, 60))
        if r.status_code == 201:
            data = r.json()
            items = data.get("data", []) or []
            log_kv_block("[EMAIL buy]", {"count": len(items), "site": REG_SITE, "domain": REG_MAIL_DOMAIN})
            return True, items, ""
        else:
            try:
                err = r.json()
                msg = f"{err.get('title')}: {err.get('details')}"
            except Exception:
                msg = f"HTTP {r.status_code}: {r.text[:200]}"
            log_step(f"[EMAIL buy] fail: {msg}")
            return False, [], msg
    except Exception as e:
        log_step(f"[EMAIL buy] error: {e}")
        return False, [], str(e)

def _pick_verification_link_from_text(text: str) -> Optional[str]:
    if not text:
        return None
    m = VERIFICATION_URL_PATTERN.search(text)
    if m:
        return html.unescape(m.group(0))
    urls = URL_RE.findall(text)
    for u in urls:
        if "kleinanzeigen.de" in u.lower():
            return html.unescape(u)
    return None

def poll_rented_email(email_id: int, timeout_sec: Optional[int] = None, interval: Optional[int] = None) -> Tuple[bool, Optional[str], str, Optional[Dict]]:
    timeout_sec = timeout_sec or REG_POLL_TIMEOUT
    interval = interval or REG_POLL_INTERVAL
    url = _email_url(f"emails/{email_id}?currency={REG_CURRENCY}")
    deadline = time.time() + max(timeout_sec, 1)
    log_kv_block("[EMAIL poll start]", {"id": email_id, "timeout": timeout_sec, "every": interval})
    last_beat = time.time()
    while time.time() < deadline:
        try:
            r = _session.get(url, headers=_email_headers(), timeout=(15, 60))
            if r.status_code == 200:
                j = r.json()
                item = j.get("data", {}) if isinstance(j, dict) else {}
                text = ""
                full_msg = item.get("full_message")
                value = item.get("value")
                if isinstance(full_msg, str) and full_msg.strip():
                    text = full_msg
                elif isinstance(value, str) and value:
                    text = value
                if text:
                    link = _pick_verification_link_from_text(text)
                    if link:
                        log_step("[EMAIL] link received")
                        return True, link, "", item
            elif r.status_code in (401, 404, 422):
                return False, None, f"poll {r.status_code}", None
        except Exception:
            pass
        now = time.time()
        if now - last_beat >= 30:
            log_step("[EMAIL] wait…")
            last_beat = now
        time.sleep(interval)
    log_step("[EMAIL] timeout")
    return False, None, "timeout", None

def cancel_rented_email(email_id: int) -> bool:
    try:
        r = _session.delete(_email_url(f"emails/{email_id}"), headers=_email_headers(), timeout=(15, 30))
        ok = r.status_code in (200, 204)
        log_kv_block("[EMAIL cancel]", {"id": email_id, "ok": ok})
        return ok
    except Exception as e:
        log_kv_block("[EMAIL cancel error]", {"id": email_id, "err": str(e)})
        return False


def save_account_info(email_addr: str, data: Dict, phone_info: Dict | None = None):
    info = {
        "email": email_addr,
        "password": data.get("password", ""),
        "contactName": data.get("contactName", ""),
        "registrationDate": time.strftime("%Y-%m-%d %H:%M:%S"),
        "proxyUsed": False,
    }
    if phone_info:
        info["phoneNumber"] = phone_info.get("phone_number")
    with open("registered_accounts.txt", "a", encoding="utf-8") as f:
        f.write(json.dumps(info, ensure_ascii=False) + "\n")


async def run_registration_rent_batch(
    *,
    count: int,
    notify: Callable[[str], Awaitable[None]],
    batch_size: Optional[int] = None,      # игнорируется в поштучном режиме
    interval_min: Optional[int] = None,    # игнорируется в поштучном режиме
) -> None:
    """
    Поштучный режим: покупаем и регистрируем по одному.
    Лимит 2/11 соблюдается через _rate_limit_blocking внутри initiate_registration.
    """
    suite_key = (settings.SUITEPRO_API_KEY or os.getenv("SUITEPRO_API_KEY", "")).strip()
    sms_key = (
        getattr(settings, "SMS_ACTIVATE_KEY", None)
        or os.getenv("SMS_ACTIVATE_KEY")
        or getattr(settings, "SMS_ACTIVATE_API_KEY", None)
        or os.getenv("SMS_ACTIVATE_API_KEY")
        or ""
    ).strip()

    if not suite_key:
        await notify("Нет SUITEPRO_API_KEY в .env.")
        return
    if not sms_key:
        await notify("Нет SMS_ACTIVATE_KEY в .env.")
        return
    if not EMAIL_API_KEY:
        await notify("Нет SMS_ACTIVATE_API_KEY (v2) в .env.")
        return

    bal = await asyncio.to_thread(get_sms_balance, sms_key)
    if bal < 2.0:
        await notify(f"SMS-Activate баланс низкий: ${bal:.2f}")
        return

    await notify(f"Покупаю и регистрирую по одному. Всего: {count} шт.")
    done = 0
    failed = 0
    start_ts = time.time()

    for n in range(1, count + 1):
        await notify(f"#{n}: покупаю почту {REG_SITE}/{REG_MAIL_DOMAIN}")
        ok_buy, items, err_buy = await asyncio.to_thread(rent_emails_batch, 1)
        if not ok_buy or not items:
            await notify(f"#{n}: ❌ не удалось купить почту: {err_buy or 'пусто'} — пропускаю")
            failed += 1
            continue

        it = items[0]
        email_addr = it.get("email")
        email_id = _extract_email_id(it)
        await notify(f"#{n}: куплено {email_addr} (id={email_id})")

        if not email_id:
            await notify(f"#{n}: ❌ нет emailId — пропускаю")
            failed += 1
            continue

        try:
            reg_data = await asyncio.to_thread(initiate_registration, suite_key, email_addr)
            await notify(f"#{n}: регистрация начата для {email_addr}")
            await asyncio.sleep(10)

            ok_mail, link, err_mail, _ = await asyncio.to_thread(
                poll_rented_email, email_id, REG_POLL_TIMEOUT, REG_POLL_INTERVAL
            )
            if not ok_mail or not link:
                await notify(f"#{n}: ❌ письмо не пришло ({err_mail}). Отменяю аренду почты")
                await asyncio.to_thread(cancel_rented_email, email_id)
                failed += 1
                continue
            await notify(f"#{n}: письмо получено")

            phone = await asyncio.to_thread(order_phone, sms_key)
            if not phone:
                await notify(f"#{n}: ❌ нет номеров. Отменяю аренду почты")
                await asyncio.to_thread(cancel_rented_email, email_id)
                failed += 1
                continue

            activation_id = phone["activation_id"]
            await asyncio.to_thread(update_activation, sms_key, activation_id, 1)
            await asyncio.to_thread(start_phone_verification, suite_key, reg_data, link, phone["phone_number"])
            await notify(f"#{n}: номер {phone['phone_number']} взят, жду SMS")

            code = await asyncio.to_thread(wait_sms, sms_key, activation_id, MAX_WAIT_TIME)
            if not code:
                await asyncio.to_thread(update_activation, sms_key, activation_id, 8)
                await notify(f"#{n}: ❌ SMS timeout. Отменяю аренду почты")
                await asyncio.to_thread(cancel_rented_email, email_id)
                failed += 1
                continue
            await notify(f"#{n}: код получен, завершаю регистрацию")

            ok_complete, data_complete, err_body = await asyncio.to_thread(
                complete_phone_verification, suite_key, reg_data, link, code
            )
            if not ok_complete:
                err_text = ""
                if isinstance(err_body, dict):
                    err_text = (err_body.get("error") or err_body.get("detail") or err_body.get("message") or "") or ""
                if isinstance(err_text, str) and "account restricted" in err_text.lower():
                    await notify(f"#{n}: ❌ account restricted — отменяю ресурсы")
                else:
                    await notify(f"#{n}: ❌ ошибка complete — отменяю ресурсы")
                await asyncio.to_thread(update_activation, sms_key, activation_id, 8)
                await asyncio.to_thread(cancel_rented_email, email_id)
                failed += 1
                continue

            await asyncio.to_thread(update_activation, sms_key, activation_id, 6)
            await asyncio.to_thread(save_account_info, email_addr, reg_data, phone)
            await notify(f"#{n}: ✅ готово для {email_addr}")
            done += 1

        except Exception as e:
            await notify(f"#{n}: ❌ ошибка: {e}")
            try:
                if email_id:
                    await asyncio.to_thread(cancel_rented_email, email_id)
            except Exception:
                pass
            failed += 1

    dur = int(time.time() - start_ts)
    await notify(f"Готово. Успешно: {done}. Неудачно: {failed}. Время: {dur}s. Данные — registered_accounts.txt.")
