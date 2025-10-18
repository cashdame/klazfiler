from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

def main_keyboard() -> ReplyKeyboardMarkup:
    # Раскладка как на скрине: 3 ряда, по 3 кнопки, но с эмодзи
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="📥 Grabber"),
                KeyboardButton(text="🗂 Архив товаров"),
                KeyboardButton(text="⚡ Быстрая публикация"),
            ],
            [
                KeyboardButton(text="➕ Добавить аккаунт"),
                KeyboardButton(text="👥 Список аккаунтов"),
                KeyboardButton(text="📝 Регистрация"),
            ],
            [
                KeyboardButton(text="🔢 123"),
                KeyboardButton(text="⚙️ Фильтры"),
                KeyboardButton(text="⬅️ Назад"),
            ],
        ],
        resize_keyboard=True
    )


def grabber_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Выйти из Grabber")],
            [KeyboardButton(text="⬅️ Назад в меню")]
        ],
        resize_keyboard=True
    )


def back_to_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="⬅️ Назад в меню")]
        ],
        resize_keyboard=True
    )


def registration_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📤 Загрузить почты (.txt)")],
            [KeyboardButton(text="🛑 Стоп регистрация")],
            [KeyboardButton(text="⬅️ Назад в меню")]
        ],
        resize_keyboard=True
    )
