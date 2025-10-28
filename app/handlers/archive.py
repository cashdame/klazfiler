# app/handlers/archive.py
# -*- coding: utf-8 -*-
"""
Обработчик «Архив».
Функции: список, просмотр, скачивание, удаление, закрытие, ЗАГРУЗКА ZIP, ПОИСК, ВВОД ЦЕНЫ.
Поддержка двух форматов архивов:
  1. info.txt (из граббера Kleinanzeigen)
  2. output.txt + images/ (ручные архивы)
"""

import os
import re
import logging
import hashlib
from contextlib import suppress
from zipfile import ZipFile
from typing import List, Tuple, Optional
from datetime import datetime

from aiogram import Router, F
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    FSInputFile,
)
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext

from app.config import settings
from app.tools.archive_publish import (
    start_publish_from_archive,
    handle_account_page_cb,
    handle_account_sort_cb,
    publish_from_archive_with_price,
)
from app.tools.data_storage import (
    get_archive_last_published,
    update_archive_published,
)

router = Router()
log = logging.getLogger("klazfiler.archive")

PAGE_SIZE = 10

# ==================== FSM ====================

class ArchiveSearchSG(StatesGroup):
    """FSM для поиска в архиве"""
    waiting_query = State()

class ArchivePublishSG(StatesGroup):
    """FSM для публикации с вводом цены"""
    waiting_price = State()
    confirm_price = State()

# ==================== Утилиты ====================

def _archive_dir() -> str:
    """Возвращает путь к папке с архивами"""
    d = settings.ARCHIVE_DIR.strip() if settings.ARCHIVE_DIR else ""
    if not d:
        d = os.path.join(os.getcwd(), "archive")
    os.makedirs(d, exist_ok=True)
    return d

def _list_archives(search_query: Optional[str] = None) -> List[str]:
    """
    Возвращает список .zip файлов в архиве.
    Если search_query указан - фильтрует по совпадению в имени файла или содержимом.
    """
    d = _archive_dir()
    files: List[str] = []
    
    with suppress(Exception):
        for name in os.listdir(d):
            if not name.lower().endswith(".zip"):
                continue
            
            # Если нет поиска - добавляем все
            if not search_query:
                files.append(name)
                continue
            
            # Поиск по имени файла
            query_lower = search_query.lower()
            if query_lower in name.lower():
                files.append(name)
                continue
            
            # Поиск по содержимому архива
            path = os.path.join(d, name)
            if _search_in_archive(path, query_lower):
                files.append(name)
    
    files.sort(key=lambda n: os.path.getmtime(os.path.join(d, n)), reverse=True)
    return files

def _search_in_archive(zip_path: str, query: str) -> bool:
    """
    Ищет query в содержимом архива (info.txt или output.txt)
    Возвращает True если найдено совпадение
    """
    try:
        with ZipFile(zip_path, "r") as z:
            # Ищем info.txt или output.txt
            text_files = []
            for zi in z.infolist():
                if zi.is_dir():
                    continue
                fname_lower = zi.filename.lower()
                if fname_lower.endswith("info.txt") or fname_lower.endswith("output.txt"):
                    text_files.append(zi.filename)
            
            # Читаем содержимое и ищем
            for fname in text_files:
                try:
                    content = z.read(fname).decode("utf-8", errors="ignore").lower()
                    if query in content:
                        return True
                except Exception:
                    continue
    except Exception:
        pass
    return False

def _id_for(name: str) -> str:
    """Короткий ID для callback_data (<= 64 байт)"""
    return hashlib.sha1(name.encode("utf-8")).hexdigest()[:16]

def _find_name_by_id(file_id: str) -> Optional[str]:
    """Находит имя файла по короткому ID"""
    for name in _list_archives():
        if _id_for(name) == file_id:
            return name
    return None

def _read_archive_info(zip_path: str) -> Tuple[str, str, str, str, str]:
    """
    Читает информацию из архива.
    Поддерживает два формата:
    
    1. info.txt (от граббера):
       Title: ...
       Price: ...
       URL: ...
       Saved at: ...
       Description:
       ...
    
    2. output.txt (ручной):
       Заголовок: ...
       Цена: ...
       Описание: ...
    
    Возвращает: (title, price, url, saved, desc)
    """
    title = price = url = saved = desc = ""
    
    try:
        with ZipFile(zip_path, "r") as z:
            # Ищем info.txt или output.txt
            info_file = None
            output_file = None
            
            for zi in z.infolist():
                if zi.is_dir():
                    continue
                fname_lower = zi.filename.lower()
                if fname_lower.endswith("info.txt"):
                    info_file = zi.filename
                elif fname_lower.endswith("output.txt"):
                    output_file = zi.filename
            
            # Приоритет: info.txt, затем output.txt
            target_file = info_file or output_file
            if not target_file:
                return title, price, url, saved, desc
            
            text = z.read(target_file).decode("utf-8", errors="ignore")
            
            # Парсинг в зависимости от формата
            if target_file == info_file:
                # Формат info.txt (от граббера)
                m_title = re.search(r"^Title:\s*(.+)$", text, re.M)
                m_price = re.search(r"^Price:\s*(.+)$", text, re.M)
                m_url = re.search(r"^URL:\s*(.+)$", text, re.M)
                m_saved = re.search(r"^Saved at:\s*(.+)$", text, re.M)
                m_desc = re.search(r"^Description:\s*\n([\s\S]+)$", text, re.M)
                
                title = (m_title.group(1).strip() if m_title else "")
                price = (m_price.group(1).strip() if m_price else "")
                url = (m_url.group(1).strip() if m_url else "")
                saved = (m_saved.group(1).strip() if m_saved else "")
                desc = (m_desc.group(1).strip() if m_desc else "")
            else:
                # Формат output.txt (ручной)
                m_title = re.search(r"^Заголовок:\s*(.+)$", text, re.M)
                m_price = re.search(r"^Цена:\s*(.+)$", text, re.M)
                m_desc = re.search(r"^Описание:\s*(.+?)(?:^[А-ЯA-Z]|\Z)", text, re.M | re.S)
                
                title = (m_title.group(1).strip() if m_title else "")
                price = (m_price.group(1).strip() if m_price else "")
                desc = (m_desc.group(1).strip() if m_desc else "")
                # URL и saved не заполняются для output.txt
                
    except Exception as e:
        log.warning(f"Failed to read archive info from {zip_path}: {e}")
    
    return title, price, url, saved, desc

def _validate_zip(file_path: str) -> Tuple[bool, str]:
    """
    Проверяет что файл - валидный ZIP архив.
    Также проверяет наличие info.txt или output.txt + images/.
    Возвращает (is_valid, error_message)
    """
    try:
        with ZipFile(file_path, 'r') as z:
            # Проверяем что архив не пустой
            if not z.namelist():
                return False, "Архив пустой"
            
            # Проверяем что архив не повреждён
            bad = z.testzip()
            if bad:
                return False, f"Повреждён файл: {bad}"
            
            # Проверяем наличие info.txt ИЛИ output.txt
            has_info = False
            has_output = False
            has_images = False
            
            for name in z.namelist():
                name_lower = name.lower()
                if name_lower.endswith("info.txt"):
                    has_info = True
                elif name_lower.endswith("output.txt"):
                    has_output = True
                elif "images/" in name_lower and any(name_lower.endswith(ext) for ext in [".jpg", ".jpeg", ".png", ".gif"]):
                    has_images = True
            
            # Допустимые варианты:
            # 1. info.txt (граббер)
            # 2. output.txt + images/ (ручной)
            if has_info:
                return True, ""
            elif has_output and has_images:
                return True, ""
            else:
                return False, "Не найден info.txt или output.txt+images/"
                
    except Exception as e:
        return False, f"Ошибка чтения: {str(e)}"

def _generate_unique_filename(original_name: str, archive_dir: str) -> str:
    """
    Генерирует уникальное имя файла если такое уже есть.
    Добавляет timestamp если нужно.
    """
    base_name = original_name
    if not base_name.lower().endswith('.zip'):
        base_name += '.zip'
    
    # Sanitize filename
    base_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', base_name)
    
    target_path = os.path.join(archive_dir, base_name)
    
    # Если файл уже существует, добавляем timestamp
    if os.path.exists(target_path):
        name_without_ext = base_name.rsplit('.zip', 1)[0]
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_name = f"{name_without_ext}_{timestamp}.zip"
    
    return base_name

def _parse_price(text: str) -> Optional[float]:
    """
    Парсит цену из текста пользователя.
    Поддерживает форматы: 245, 245.50, 245€, 245 eur, 245,50
    Возвращает float или None
    """
    # Убираем всё кроме цифр, точки и запятой
    text = text.strip()
    text = re.sub(r'[^\d.,]', '', text)
    
    if not text:
        return None
    
    # Заменяем запятую на точку
    text = text.replace(',', '.')
    
    try:
        price = float(text)
        # Валидация
        if price < 1:
            return None
        if price > 999999:
            return None
        return price
    except ValueError:
        return None

# ==================== Клавиатуры ====================

def _kb_archive_main() -> InlineKeyboardMarkup:
    """Главное меню архива"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Список архивов", callback_data="arch:list:0")],
        [InlineKeyboardButton(text="🔍 Поиск", callback_data="arch:search")],
        [InlineKeyboardButton(text="❌ Закрыть", callback_data="arch:close")],
    ])

def _kb_archive_page(page: int, search_query: Optional[str] = None) -> InlineKeyboardMarkup:
    """Клавиатура со списком архивов (пагинация)"""
    items = _list_archives(search_query)
    total = len(items)
    start = page * PAGE_SIZE
    end = start + PAGE_SIZE
    slice_items = items[start:end]
    
    rows = []
    
    # Заголовок если поиск
    if search_query:
        rows.append([InlineKeyboardButton(
            text=f"🔍 Найдено: {total}",
            callback_data="arch:noop"
        )])
    
    # Список архивов
    for name in slice_items:
        fid = _id_for(name)
        
        # Получаем дату последней публикации
        last_pub = get_archive_last_published(name)
        
        # Формируем отображаемое имя с датой
        if last_pub:
            date_suffix = f" - {last_pub}"
        else:
            date_suffix = " - Никогда не публиковался"
        
        # Ограничиваем длину имени с учётом даты
        max_name_len = 55 - len(date_suffix)
        if len(name) > max_name_len:
            display_name = name[:max_name_len] + "..." + date_suffix
        else:
            display_name = name + date_suffix
        
        rows.append([InlineKeyboardButton(
            text=display_name,
            callback_data=f"arch:open:{fid}"
        )])
    
    # Навигация
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(
            text="⟨ Назад",
            callback_data=f"arch:page:{page-1}:{search_query or ''}"
        ))
    if end < total:
        nav.append(InlineKeyboardButton(
            text="Вперёд ⟩",
            callback_data=f"arch:page:{page+1}:{search_query or ''}"
        ))
    if nav:
        rows.append(nav)
    
    # Кнопки управления
    control_row = []
    if search_query:
        control_row.append(InlineKeyboardButton(
            text="🔍 Новый поиск",
            callback_data="arch:search"
        ))
        control_row.append(InlineKeyboardButton(
            text="📋 Все архивы",
            callback_data="arch:list:0"
        ))
    else:
        control_row.append(InlineKeyboardButton(
            text="🔍 Поиск",
            callback_data="arch:search"
        ))
    
    rows.append(control_row)
    rows.append([InlineKeyboardButton(text="❌ Закрыть", callback_data="arch:close")])
    
    return InlineKeyboardMarkup(inline_keyboard=rows)

def _kb_archive_item(name: str) -> InlineKeyboardMarkup:
    """Клавиатура для конкретного архива"""
    fid = _id_for(name)
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📤 Опубликовать", callback_data=f"arch:publish:{fid}")],
        [InlineKeyboardButton(text="⬇️ Скачать", callback_data=f"arch:send:{fid}")],
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"arch:delete:{fid}")],
        [InlineKeyboardButton(text="← Назад к списку", callback_data="arch:list:0")],
    ])

def _kb_price_confirm(price: float, is_vb: bool = False) -> InlineKeyboardMarkup:
    """Клавиатура подтверждения цены"""
    price_text = f"{price:.2f}" if price % 1 else f"{int(price)}"
    
    if is_vb:
        # Если уже VB, показываем кнопку публикации и возврат к фиксированной
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"✅ Опубликовать ({price_text} € VB)", callback_data="apub:publish")],
            [InlineKeyboardButton(text="💰 Сделать фиксированной", callback_data="apub:toggle_vb")],
            [InlineKeyboardButton(text="🔙 Изменить цену", callback_data="apub:change_price")],
        ])
    else:
        # Фиксированная цена
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"✅ Опубликовать ({price_text} €)", callback_data="apub:publish")],
            [InlineKeyboardButton(text="💬 Сделать VB", callback_data="apub:toggle_vb")],
            [InlineKeyboardButton(text="🔙 Изменить цену", callback_data="apub:change_price")],
        ])

# ==================== Обработчики ====================

@router.message(F.text.contains("Архив"))
async def archive_open(message: Message) -> None:
    """Открывает главное меню архива"""
    total = len(_list_archives())
    kb = _kb_archive_main()
    await message.answer(
        f"📦 <b>Архив товаров</b>\n\n"
        f"Всего архивов: <b>{total}</b>\n\n"
        f"💡 <i>Подсказка:</i>\n"
        f"• Можешь прислать .zip файл прямо в чат - я сохраню его!\n"
        f"• Поддерживаю оба формата: <code>info.txt</code> и <code>output.txt+images/</code>\n"
        f"• Используй поиск для быстрого нахождения нужного товара",
        reply_markup=kb
    )

# ==================== Загрузка ZIP файлов ====================

@router.message(F.document)
async def handle_document(message: Message) -> None:
    """
    Обрабатывает загруженные документы.
    Если это .zip файл - сохраняет в архив.
    """
    doc = message.document
    
    # Проверяем что это ZIP файл
    if not doc.file_name or not doc.file_name.lower().endswith('.zip'):
        return
    
    # Проверяем размер (макс 20MB)
    max_size = 20 * 1024 * 1024
    if doc.file_size and doc.file_size > max_size:
        await message.answer(
            f"❌ Файл слишком большой ({doc.file_size / 1024 / 1024:.1f} MB).\n"
            f"Максимум: {max_size / 1024 / 1024:.0f} MB"
        )
        return
    
    progress = await message.answer("⏳ Загружаю архив...")
    
    try:
        # Скачиваем файл
        file = await message.bot.download(doc.file_id)
        if not file:
            await progress.edit_text("❌ Не удалось скачать файл")
            return
        
        file_content = file.read()
        archive_dir = _archive_dir()
        temp_path = os.path.join(archive_dir, f"temp_{doc.file_id}.zip")
        
        try:
            # Сохраняем во временный файл
            with open(temp_path, 'wb') as f:
                f.write(file_content)
            
            # Валидируем ZIP
            is_valid, error_msg = _validate_zip(temp_path)
            if not is_valid:
                os.remove(temp_path)
                await progress.edit_text(
                    f"❌ <b>Невалидный архив</b>\n\n"
                    f"Причина: {error_msg}\n\n"
                    f"Архив должен содержать:\n"
                    f"• <code>info.txt</code> (от граббера)\n"
                    f"ИЛИ\n"
                    f"• <code>output.txt</code> + папка <code>images/</code>"
                )
                return
            
            # Генерируем уникальное имя
            final_name = _generate_unique_filename(doc.file_name, archive_dir)
            final_path = os.path.join(archive_dir, final_name)
            
            # Переименовываем
            os.rename(temp_path, final_path)
            
            # Читаем информацию
            title, price, url, saved, desc = _read_archive_info(final_path)
            
            # Формируем сообщение
            info_text = f"✅ <b>Архив сохранён!</b>\n\n"
            info_text += f"📁 <b>Файл:</b> <code>{final_name}</code>\n"
            info_text += f"📊 <b>Размер:</b> {len(file_content) / 1024:.1f} KB\n\n"
            
            if title:
                info_text += f"📦 <b>Товар:</b> {title}\n"
            if price:
                info_text += f"💰 <b>Цена:</b> {price}\n"
            if url:
                url_display = url[:50] + "..." if len(url) > 50 else url
                info_text += f"🔗 <b>URL:</b> {url_display}\n"
            if saved:
                info_text += f"📅 <b>Сохранён:</b> {saved}\n"
            
            if desc:
                desc_preview = desc[:200] + "..." if len(desc) > 200 else desc
                info_text += f"\n📝 <b>Описание:</b>\n{desc_preview}"
            
            info_text += f"\n\n✨ Архив доступен в разделе «🗂 Архив товаров»"
            
            await progress.edit_text(info_text)
            log.info(f"User {message.from_user.id} uploaded archive: {final_name}")
            
        finally:
            # Удаляем временный файл если остался
            if os.path.exists(temp_path):
                with suppress(Exception):
                    os.remove(temp_path)
        
    except Exception as e:
        log.error(f"Error handling ZIP upload: {e}", exc_info=True)
        await progress.edit_text(f"❌ Ошибка при сохранении:\n<code>{str(e)}</code>")

# ==================== Список и навигация ====================

@router.callback_query(F.data.startswith("arch:list:"))
async def archive_list(call: CallbackQuery) -> None:
    """Показывает список всех архивов"""
    try:
        page = int(call.data.split(":")[-1])
    except Exception:
        page = 0
    
    kb = _kb_archive_page(max(0, page))
    total = len(_list_archives())
    
    with suppress(Exception):
        await call.message.edit_text(
            f"📦 <b>Архив товаров</b>\n\n"
            f"Всего архивов: <b>{total}</b>\n\n"
            f"💡 Можешь прислать .zip файл в чат!",
            reply_markup=kb
        )
    await call.answer()

@router.callback_query(F.data.startswith("arch:page:"))
async def archive_page(call: CallbackQuery) -> None:
    """Пагинация списка архивов"""
    parts = call.data.split(":")
    try:
        page = int(parts[2])
        search_query = parts[3] if len(parts) > 3 else None
        if search_query == '':
            search_query = None
    except Exception:
        page = 0
        search_query = None
    
    kb = _kb_archive_page(max(0, page), search_query)
    total = len(_list_archives(search_query))
    
    text = f"📦 <b>Архив товаров</b>\n\n"
    if search_query:
        text += f"🔍 Поиск: <code>{search_query}</code>\n"
    text += f"Найдено: <b>{total}</b>"
    
    with suppress(Exception):
        await call.message.edit_text(text, reply_markup=kb)
    await call.answer()

@router.callback_query(F.data.startswith("arch:open:"))
async def archive_open_item(call: CallbackQuery) -> None:
    """Открывает детали конкретного архива"""
    file_id = call.data.split(":", 2)[-1]
    name = _find_name_by_id(file_id)
    if not name:
        await call.answer("Файл не найден", show_alert=True)
        return
    
    path = os.path.join(_archive_dir(), name)
    title, price, url, saved, desc = _read_archive_info(path)
    
    # Получаем размер файла
    file_size = os.path.getsize(path) / 1024  # KB
    
    # Определяем тип архива
    archive_type = "Граббер" if url else "Ручной"
    
    info = f"📦 <b>{name}</b>\n\n"
    info += f"📊 <b>Размер:</b> {file_size:.1f} KB\n"
    info += f"🏷 <b>Тип:</b> {archive_type}\n\n"
    
    if title:
        info += f"<b>Название:</b> {title}\n"
    if price:
        info += f"<b>Цена:</b> {price}\n"
    if url:
        url_display = url[:60] + "..." if len(url) > 60 else url
        info += f"<b>URL:</b> {url_display}\n"
    if saved:
        info += f"<b>Дата:</b> {saved}\n"
    
    if desc:
        desc_preview = (desc[:1000] + '…') if len(desc) > 1000 else desc
        info += f"\n<b>Описание:</b>\n{desc_preview}"
    
    kb = _kb_archive_item(name)
    with suppress(Exception):
        await call.message.edit_text(info, reply_markup=kb)
    await call.answer()

# ==================== Поиск ====================

@router.callback_query(F.data == "arch:search")
async def archive_search_start(call: CallbackQuery, state: FSMContext) -> None:
    """Запускает режим поиска"""
    await state.set_state(ArchiveSearchSG.waiting_query)
    await call.message.answer(
        "🔍 <b>Поиск в архиве</b>\n\n"
        "Введи поисковый запрос (слово или фразу).\n"
        "Я найду совпадения в названиях файлов и содержимом архивов.\n\n"
        "Для отмены отправь /cancel"
    )
    await call.answer()

@router.message(ArchiveSearchSG.waiting_query, F.text.cast(str).as_("text"))
async def archive_search_process(message: Message, state: FSMContext, text: str) -> None:
    """Обрабатывает поисковый запрос"""
    # Проверка на отмену
    if text.lower() in ["/cancel", "/stop", "отмена"]:
        await state.clear()
        await message.answer("❌ Поиск отменён")
        return
    
    # Минимальная длина запроса
    if len(text.strip()) < 2:
        await message.answer("⚠️ Запрос слишком короткий. Минимум 2 символа.")
        return
    
    await state.clear()
    
    # Поиск
    progress = await message.answer("🔍 Ищу...")
    query = text.strip()
    results = _list_archives(query)
    
    if not results:
        await progress.edit_text(
            f"🔍 <b>Поиск:</b> <code>{query}</code>\n\n"
            f"❌ Ничего не найдено\n\n"
            f"Попробуй другой запрос или проверь список всех архивов"
        )
        return
    
    # Показываем результаты
    kb = _kb_archive_page(0, query)
    await progress.edit_text(
        f"🔍 <b>Поиск:</b> <code>{query}</code>\n\n"
        f"✅ Найдено: <b>{len(results)}</b>",
        reply_markup=kb
    )

@router.callback_query(F.data == "arch:noop")
async def archive_noop(call: CallbackQuery) -> None:
    """Пустой callback для неактивных кнопок"""
    await call.answer()

# ==================== Действия с архивами ====================

@router.callback_query(F.data.startswith("arch:send:"))
async def archive_send(call: CallbackQuery) -> None:
    """Отправляет архив пользователю"""
    file_id = call.data.split(":", 2)[-1]
    name = _find_name_by_id(file_id)
    if not name:
        await call.answer("Файл не найден", show_alert=True)
        return
    
    path = os.path.join(_archive_dir(), name)
    with suppress(Exception):
        await call.message.answer_document(
            FSInputFile(path),
            caption=f"📦 <b>{name}</b>"
        )
    await call.answer("📤 Архив отправлен!")

@router.callback_query(F.data.startswith("arch:delete:"))
async def archive_delete(call: CallbackQuery) -> None:
    """Удаляет архив"""
    file_id = call.data.split(":", 2)[-1]
    name = _find_name_by_id(file_id)
    if not name:
        await call.answer("Файл не найден", show_alert=True)
        return
    
    path = os.path.join(_archive_dir(), name)
    try:
        os.remove(path)
        kb = _kb_archive_page(0)
        total = len(_list_archives())
        with suppress(Exception):
            await call.message.edit_text(
                f"✅ <b>Архив удалён!</b>\n\n"
                f"📦 Осталось архивов: <b>{total}</b>",
                reply_markup=kb
            )
        await call.answer("🗑 Удалено!")
        log.info(f"Archive deleted: {name}")
    except Exception as e:
        log.error(f"Failed to delete archive {name}: {e}")
        await call.answer("❌ Не удалось удалить", show_alert=True)

@router.callback_query(F.data == "arch:close")
async def archive_close(call: CallbackQuery) -> None:
    """Закрывает меню архива"""
    with suppress(Exception):
        await call.message.delete()
    await call.answer()

# ==================== Публикация с вводом цены ====================

@router.callback_query(F.data.startswith("arch:publish:"))
async def archive_publish_forward(call: CallbackQuery) -> None:
    """Запускает процесс публикации из архива"""
    file_id = call.data.split(":", 2)[-1]
    name = _find_name_by_id(file_id)
    if not name:
        await call.answer("Файл не найден", show_alert=True)
        return
    path = os.path.join(_archive_dir(), name)
    await start_publish_from_archive(call, archive_path=path, archive_name=name)

@router.callback_query(F.data.startswith("apub:pick:"))
async def apub_pick(call: CallbackQuery, state: FSMContext) -> None:
    """Обработка выбора аккаунта для публикации"""
    # Просто вызываем обработчик из archive_publish
    from app.tools.archive_publish import handle_account_pick_cb
    await handle_account_pick_cb(call, state)


@router.message(ArchivePublishSG.waiting_price, F.text.cast(str).as_("text"))
async def apub_price_input(message: Message, state: FSMContext, text: str) -> None:
    """Обработка ввода цены"""
    
    # Проверка на отмену
    if text.lower() in ["/cancel", "/stop", "отмена"]:
        await state.clear()
        await message.answer("❌ Публикация отменена")
        return
    
    # Парсим цену
    price = _parse_price(text)
    
    if price is None:
        await message.answer(
            "⚠️ <b>Неправильная цена!</b>\n\n"
            "Введи число от 1 до 999999\n"
            "Примеры: <code>245</code>, <code>199.99</code>, <code>1500</code>\n\n"
            "Или отправь /cancel для отмены"
        )
        return
    
    # Сохраняем цену и тип
    await state.update_data(
        custom_price=price,
        price_is_vb=False
    )
    
    # Переходим к подтверждению
    await state.set_state(ArchivePublishSG.confirm_price)
    
    # Показываем кнопки подтверждения
    kb = _kb_price_confirm(price, is_vb=False)
    
    data = await state.get_data()
    title = data.get("title", "")
    
    msg = f"✅ <b>Цена установлена!</b>\n\n"
    if title:
        msg += f"📦 <b>Товар:</b> {title}\n"
    msg += f"💰 <b>Цена:</b> {price:.2f} € (фиксированная)\n\n"
    msg += f"Выбери действие:"
    
    await message.answer(msg, reply_markup=kb)

@router.callback_query(ArchivePublishSG.confirm_price, F.data == "apub:toggle_vb")
async def apub_toggle_vb(call: CallbackQuery, state: FSMContext) -> None:
    """Переключение типа цены (фиксированная/VB)"""
    data = await state.get_data()
    price = data.get("custom_price", 0)
    is_vb = data.get("price_is_vb", False)
    title = data.get("title", "")
    
    # Переключаем
    is_vb = not is_vb
    await state.update_data(price_is_vb=is_vb)
    
    # Обновляем сообщение
    kb = _kb_price_confirm(price, is_vb=is_vb)
    
    price_text = f"{price:.2f}" if price % 1 else f"{int(price)}"
    price_type = "VB (Verhandlungsbasis)" if is_vb else "фиксированная"
    
    msg = f"✅ <b>Цена установлена!</b>\n\n"
    if title:
        msg += f"📦 <b>Товар:</b> {title}\n"
    msg += f"💰 <b>Цена:</b> {price_text} € ({price_type})\n\n"
    msg += f"Выбери действие:"
    
    with suppress(Exception):
        await call.message.edit_text(msg, reply_markup=kb)
    await call.answer(f"✅ Тип цены: {price_type}")

@router.callback_query(ArchivePublishSG.confirm_price, F.data == "apub:change_price")
async def apub_change_price(call: CallbackQuery, state: FSMContext) -> None:
    """Возврат к вводу цены"""
    await state.set_state(ArchivePublishSG.waiting_price)
    
    data = await state.get_data()
    price_from_archive = data.get("price_from_archive", "")
    
    msg = f"💰 <b>Введи новую цену</b>\n\n"
    msg += f"Просто напиши число (например: <code>245</code>)\n\n"
    
    if price_from_archive:
        msg += f"💡 <i>Цена из архива:</i> {price_from_archive}"
    
    msg += f"\n\n<i>Для отмены отправь</i> /cancel"
    
    await call.message.answer(msg)
    await call.answer()

@router.callback_query(ArchivePublishSG.confirm_price, F.data == "apub:publish")
async def apub_publish_execute(call: CallbackQuery, state: FSMContext) -> None:
    """Выполнение публикации с пользовательской ценой"""
    data = await state.get_data()
    
    username = data.get("username")
    archive_path = data.get("archive_path")
    archive_name = data.get("archive_name")
    custom_price = data.get("custom_price")
    price_is_vb = data.get("price_is_vb", False)
    publish_token = data.get("publish_token")
    
    if not all([username, archive_path, custom_price is not None, publish_token]):
        await call.message.answer("❌ Данные потеряны. Начни заново из архива.")
        await state.clear()
        return
    
    # Очищаем state
    await state.clear()
    
    # Определяем тип цены
    price_type = "PLEASE_CONTACT" if price_is_vb else "SPECIFIED_AMOUNT"
    
    await call.answer("🚀 Публикую...")
    
    # Вызываем функцию публикации с пользовательской ценой
    await publish_from_archive_with_price(
        call=call,
        username=username,
        archive_path=archive_path,
        archive_name=archive_name,
        custom_price=custom_price,
        price_type=price_type
    )

@router.callback_query(F.data.startswith("apub:page:"))
async def apub_page(call: CallbackQuery) -> None:
    """Обработка пагинации при выборе аккаунта"""
    await handle_account_page_cb(call)

@router.callback_query(F.data.startswith("apub:sort:"))
async def apub_sort(call: CallbackQuery) -> None:
    """Обработчик смены сортировки аккаунтов"""
    await handle_account_sort_cb(call)

@router.callback_query(F.data == "apub:none")
async def apub_none(call: CallbackQuery) -> None:
    """Заглушка для активных кнопок сортировки"""
    await call.answer()

# ==================== Совместимость ====================

async def open_archive(message: Message):
    """Алиас для других модулей"""
    await archive_open(message)
