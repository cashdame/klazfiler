import json
import re
from typing import Any, Dict, Optional

from aiogram import Router, F, types
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext

from app.keyboards import main_keyboard
from app.services.accounts import add_account_tokens

router = Router()

class AddAccSG(StatesGroup):
    waiting_data = State()

PROMPT = (
    "Режим добавления аккаунта.\n"
    "Пришли .txt или текст с полями:\n\n"
    "Вариант 1 (key: value):\n"
    "username: user@mail.com\n"
    "accessToken: eyJhbGciOi...\n"
    "refreshToken: eyJhbGciOi...\n"
    "userIdToken: 12345678\n"
    "proxyURL: http://user:pass@host:port  (необязательно)\n\n"
    "Вариант 2 (JSON):\n"
    "{\n"
    '  "username": "user@mail.com",\n'
    '  "accessToken": "...",\n'
    '  "refreshToken": "...",\n'
    '  "userIdToken": "12345678",\n'
    '  "proxyURL": "http://user:pass@host:port"\n'
    "}\n\n"
    "Выйти: «Назад» или /cancel"
)

@router.message(F.text == "Добавить аккаунт")
async def addacc_enter(message: types.Message, state: FSMContext):
    await state.set_state(AddAccSG.waiting_data)
    await message.answer(PROMPT)

@router.message(AddAccSG.waiting_data, F.text.in_({"Назад", "Меню", "/cancel", "/stop"}))
async def addacc_exit(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Вышел из режима добавления. Главное меню:", reply_markup=main_keyboard())

# -------- парсинг входа --------

_KEYVAL_RE = re.compile(r"^\s*([A-Za-z_][\w\-]*)\s*:\s*(.+?)\s*$", re.M)
_COOKIE_ARRAY_SIGNS = ('"name"', '"value"')
_NUM_ID_RE = re.compile(r"\b(\d{6,})\b")

def _from_keyvals(text: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for m in _KEYVAL_RE.finditer(text):
        k, v = m.group(1).strip(), m.group(2).strip()
        out[k] = v
    return out

def _from_json(text: str) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(text)
    except Exception:
        return None

def _from_cookies_array(text: str) -> Optional[Dict[str, str]]:
    js = _from_json(text)
    if not isinstance(js, list):
        return None
    cookies = {str(it.get("name", "")).lower(): str(it.get("value", "")) for it in js if isinstance(it, dict)}
    access = cookies.get("access_token") or cookies.get("accessToken")
    refresh = cookies.get("refresh_token") or cookies.get("refreshToken")
    idtok  = cookies.get("id_token") or cookies.get("idToken") or cookies.get("userid") or cookies.get("userId")
    out: Dict[str, str] = {}
    if access: out["accessToken"] = access
    if refresh: out["refreshToken"] = refresh
    if idtok: out["userIdToken"] = idtok
    return out or None

def _normalize_payload(raw: Dict[str, Any]) -> Dict[str, str]:
    mapping = {
        "username": ["username", "user", "login", "email"],
        "accessToken": ["accessToken", "access_token", "access"],
        "refreshToken": ["refreshToken", "refresh_token", "refresh"],
        "userIdToken": ["userIdToken", "userId", "id_token", "idToken", "userid"],
        "proxyURL": ["proxyURL", "proxy", "proxyUrl"],
    }
    out: Dict[str, str] = {}
    for dst, keys in mapping.items():
        for k in keys:
            if k in raw and str(raw[k]).strip():
                out[dst] = str(raw[k]).strip()
                break
    if "userIdToken" not in out and "__source_text__" in raw:
        m = _NUM_ID_RE.search(str(raw["__source_text__"]))
        if m:
            out["userIdToken"] = m.group(1)
    return out

def parse_account_text(text: str) -> Dict[str, str]:
    text = text.strip()
    js = _from_json(text)
    if isinstance(js, dict):
        js["__source_text__"] = text
        return _normalize_payload(js)
    if all(s in text for s in _COOKIE_ARRAY_SIGNS):
        cookie_map = _from_cookies_array(text)
        if cookie_map:
            cookie_map["__source_text__"] = text
            return _normalize_payload(cookie_map)
    kv = _from_keyvals(text)
    if kv:
        kv["__source_text__"] = text
        return _normalize_payload(kv)
    return {}

def _validate(p: Dict[str, str]) -> Optional[str]:
    need = ["username", "accessToken", "refreshToken", "userIdToken"]
    miss = [k for k in need if not p.get(k)]
    return ("Не хватает полей: " + ", ".join(miss)) if miss else None

# -------- обработчики данных --------

@router.message(AddAccSG.waiting_data, F.document)
async def addacc_file(message: types.Message, state: FSMContext):
    doc = message.document
    if doc.mime_type and not doc.mime_type.startswith("text"):
        await message.answer("Нужен текстовый .txt файл или просто текст сообщением.")
        return
    file = await message.bot.get_file(doc.file_id)
    content = await message.bot.download_file(file.file_path)
    text = content.read().decode("utf-8", errors="ignore")
    await _process_payload_text(message, text)

@router.message(AddAccSG.waiting_data, F.text)
async def addacc_text(message: types.Message, state: FSMContext):
    await _process_payload_text(message, message.text or "")

async def _process_payload_text(message: types.Message, text: str):
    data = parse_account_text(text)
    err = _validate(data)
    if err:
        await message.answer(f"Формат не распознан. {err}\n\n" + PROMPT)
        return

    status, resp = await add_account_tokens(
        username=data["username"],
        accessToken=data["accessToken"],
        refreshToken=data["refreshToken"],
        userIdToken=data["userIdToken"],
        proxyURL=data.get("proxyURL"),
    )

    if status == 200:
        await message.answer(
            f"✅ Аккаунт добавлен: <b>{data['username']}</b>\n"
            f"Можно присылать следующий.\nДля выхода — «Назад» или /cancel."
        )
    else:
        msg = resp.get("message") if isinstance(resp, dict) else str(resp)
        await message.answer(f"❌ Не удалось добавить аккаунт.\n{msg}\nПопробуй поправить данные и прислать снова.")
