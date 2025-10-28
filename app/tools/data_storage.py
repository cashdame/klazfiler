"""
Модуль для хранения данных о публикациях и PLZ аккаунтов
"""
import json
import os
from datetime import datetime
from typing import Optional, Dict
import logging

log = logging.getLogger("klazfiler.data_storage")

# Путь к файлам данных
DATA_DIR = "/opt/klazfiler/data"
ARCHIVE_HISTORY_FILE = os.path.join(DATA_DIR, "archive_history.json")
ACCOUNT_PLZ_FILE = os.path.join(DATA_DIR, "account_plz.json")


def _ensure_data_dir():
    """Создаёт папку data если её нет"""
    os.makedirs(DATA_DIR, exist_ok=True)


def _load_json(filepath: str) -> dict:
    """Загружает JSON из файла"""
    try:
        if os.path.exists(filepath):
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        log.warning(f"Failed to load {filepath}: {e}")
    return {}


def _save_json(filepath: str, data: dict):
    """Сохраняет JSON в файл"""
    try:
        _ensure_data_dir()
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.error(f"Failed to save {filepath}: {e}")


# ==================== ARCHIVE HISTORY ====================

def get_archive_last_published(archive_name: str) -> Optional[str]:
    """
    Возвращает дату последней публикации архива.
    Формат: "27.10.2025" или None если не публиковался
    """
    data = _load_json(ARCHIVE_HISTORY_FILE)
    archive_data = data.get(archive_name, {})
    last_pub = archive_data.get("last_published")
    
    if not last_pub:
        return None
    
    try:
        # Конвертируем ISO формат в DD.MM.YYYY
        dt = datetime.fromisoformat(last_pub)
        return dt.strftime("%d.%m.%Y")
    except Exception:
        return None


def update_archive_published(archive_name: str, username: str):
    """
    Обновляет дату последней публикации архива
    """
    data = _load_json(ARCHIVE_HISTORY_FILE)
    
    if archive_name not in data:
        data[archive_name] = {
            "last_published": None,
            "publish_count": 0,
            "accounts_used": []
        }
    
    # Обновляем данные
    data[archive_name]["last_published"] = datetime.now().isoformat()
    data[archive_name]["publish_count"] = data[archive_name].get("publish_count", 0) + 1
    
    # Добавляем аккаунт в список если его там нет
    accounts = data[archive_name].get("accounts_used", [])
    if username not in accounts:
        accounts.append(username)
    data[archive_name]["accounts_used"] = accounts
    
    _save_json(ARCHIVE_HISTORY_FILE, data)
    log.info(f"Updated archive history: {archive_name} published to {username}")


# ==================== ACCOUNT PLZ ====================

def get_account_plz(username: str) -> Optional[str]:
    """
    Возвращает сохранённый PLZ для аккаунта или None если не было публикаций
    """
    data = _load_json(ACCOUNT_PLZ_FILE)
    account_data = data.get(username, {})
    return account_data.get("plz")


def save_account_plz(username: str, plz: str, city: str = ""):
    """
    Сохраняет PLZ для аккаунта (при первой публикации)
    """
    data = _load_json(ACCOUNT_PLZ_FILE)
    
    if username not in data:
        data[username] = {
            "plz": plz,
            "city": city,
            "first_used": datetime.now().isoformat()
        }
        _save_json(ACCOUNT_PLZ_FILE, data)
        log.info(f"Saved PLZ {plz} for account {username}")


def get_or_generate_plz(username: str) -> str:
    """
    Возвращает существующий PLZ для аккаунта или генерирует новый
    """
    import random
    
    # Проверяем есть ли сохранённый PLZ
    existing_plz = get_account_plz(username)
    if existing_plz:
        log.info(f"Using existing PLZ {existing_plz} for {username}")
        return existing_plz
    
    # Генерируем новый PLZ для маленьких городов на окраинах Германии
    small_city_plz = [
        "88131",  # Lindau (Bayern)
        "78176",  # Blumberg (Baden-Württemberg)
        "25980",  # Sylt (Schleswig-Holstein)
        "91781",  # Weißenburg (Bayern)
        "83435",  # Bad Reichenhall (Bayern)
        "29456",  # Hitzacker (Niedersachsen)
        "54634",  # Bitburg (Rheinland-Pfalz)
        "99734",  # Nordhausen (Thüringen)
        "02763",  # Zittau (Sachsen)
        "17489",  # Greifswald (Mecklenburg-Vorpommern)
        "37115",  # Duderstadt (Niedersachsen)
        "56759",  # Kaisersesch (Rheinland-Pfalz)
        "79809",  # Weilheim (Baden-Württemberg)
        "94481",  # Grafenau (Bayern)
        "06493",  # Ballenstedt (Sachsen-Anhalt)
    ]
    
    new_plz = random.choice(small_city_plz)
    
    # Сохраняем для будущих публикаций
    save_account_plz(username, new_plz)
    
    log.info(f"Generated new PLZ {new_plz} for {username}")
    return new_plz
