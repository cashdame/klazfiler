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
    "username: user@mail.com\naccessToken: ...\nrefreshToken: ...\nuserIdToken: ...\n"
    "proxyURL: http://user:pass@host:port  (необязательно)\n\n"
    "Выйти: «⬅️ Назад» или /cancel"
)

async def _switch_to(target: str, message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Переключаюсь…")
    if target == "list_accounts":
        from app.handlers.list_accounts import list_accounts_open
        await list_accounts_open(message)
    elif target == "registration":
        from app.handlers.registration import registration_entry
        await registration_entry(message, state)
    elif target == "archive":
        from app.handlers.archive import open_archive
        await open_archive(message)
    elif target == "quick":
        from app.handlers.menu import quick_publish
        await quick_publish(message)
    elif target == "filters":
        from app.handlers.menu import filters
        await filters(message)
    elif target == "test":
        from app.handlers.menu import test_section
        await test_section(message)
    elif target == "back":
        from app.handlers.menu import back_to_menu
        await back_to_menu(message)

_SWITCH_MAP = {
    "👥 Список аккаунтов": "list_accounts",
    "Список аккаунтов": "list_accounts",
    "📝 Регистрация": "registration",
    "Регистрация": "registration",
    "🗂 Архив товаров": "archive",
    "Архив товаров": "archive",
    "⚡ Быстрая публикация": "quick",
    "Быстрая публикация": "quick",
    "⚙️ Фильтры": "filters",
    "Фильтры": "filters",
    "🔢 123": "test",
    "123": "test",
    "⬅️ Назад": "back",
    "Назад": "back",
}

@router.message(F.text.in_({"➕ Добавить аккаунт", "Добавить аккаунт"}))
async def addacc_enter(message: types.Message, state: FSMContext):
    await state.set_state(AddAccSG.waiting_data)
    await message.answer(PROMPT)

@router.message(AddAccSG.waiting_data, F.text.cast(str).as_("text"))
async def addacc_switch_or_text(message: types.Message, state: FSMContext, text: str):
    target = _SWITCH_MAP.get(text)
    if target:
        await _switch_to(target, message, state)
        return
    await _process_payload_text(message, text)

@router.message(AddAccSG.waiting_data, F.document)
async def addacc_file(message: types.Message, state: FSMContext):
    doc = message.document
    fn = (doc.file_name or "").lower()
    if not (fn.endswith(".txt") or (doc.mime_type and "text" in doc.mime_type.lower())):
        await message.answer("Нужен .txt или просто текст сообщением.")
        return
    file = await message.bot.get_file(doc.file_id)
    content = await message.bot.download_file(file.file_path)
    text = content.read().decode("utf-8", errors="ignore")
    await _process_payload_text(message, text)

_KEYVAL_RE = re.compile(r"^\s*([A-Za-z_][\w\-]*)\s*:\s*(.+?)\s*$", re.M)
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
    out: Dict[str, str] = {}
    if v := cookies.get("access_token") or cookies.get("accessToken"): out["accessToken"] = v
    if v := cookies.get("refresh_token") or cookies.get("refreshToken"): out["refreshToken"] = v
    if v := cookies.get("id_token") or cookies.get("idToken") or cookies.get("userid") or cookies.get("userId"):
        out["userIdToken"] = v
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
    if isinstance(js := _from_json(text), dict):
        js["__source_text__"] = text
        return _normalize_payload(js)
    if text.startswith("[") and '"name"' in text and '"value"' in text:
        if m := _from_cookies_array(text):
            m["__source_text__"] = text
            return _normalize_payload(m)
    if kv := _from_keyvals(text):
        kv["__source_text__"] = text
        return _normalize_payload(kv)
    return {}

def _validate(p: Dict[str, str]) -> Optional[str]:
    need = ["username", "accessToken", "refreshToken", "userIdToken"]
    miss = [k for k in need if not p.get(k)]
    return ("Не хватает полей: " + ", ".join(miss)) if miss else None

async def _process_payload_text(message: types.Message, text: str):
    data = parse_account_text(text)
    if err := _validate(data):
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
            f"Можно присылать следующий.\nВыход — «⬅️ Назад» или /cancel.",
            reply_markup=main_keyboard()
        )
    else:
        msg = resp.get("message") if isinstance(resp, dict) else str(resp)
        await message.answer(f"❌ Не удалось добавить аккаунт.\n{msg}\nПопробуй поправить данные и прислать снова.")
