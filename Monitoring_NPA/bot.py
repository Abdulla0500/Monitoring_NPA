import logging
import asyncio
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram import ReplyKeyboardRemove

from database import Database
from fetcher import RegulationAPI
from classifier import ProjectClassifier

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TOKEN = "8218361501:AAFS9tTT2coSdo1Pk2mhWd7odDsjUq41jpQ"

db = Database()
api = RegulationAPI()

TOPICS = {
    'epd': '🚛 ЭПД (электронные перевозочные документы)',
    'mchd': '📄 МЧД (машиночитаемые доверенности)',
    'edo': '📁 ЭДО (электронный документооборот)',
    'ep': '✍️ ЭП (электронная подпись)',
    'ofd': '🧾 ОФД (операторы фискальных данных)'
}
TOPICS_SHORT = {
    'epd': '🚛 ЭПД',
    'mchd': '📄 МЧД',
    'edo': '📁 ЭДО',
    'ep': '✍️ ЭП',
    'ofd': '🧾 ОФД'
}


def get_main_menu_keyboard():
    """Создает клавиатуру главного меню"""
    keyboard = [
        [InlineKeyboardButton("📋 Текущие проекты", callback_data="menu_current")],
        [InlineKeyboardButton("🔍 Поиск по темам", callback_data="menu_search")],
        [InlineKeyboardButton("📌 Мои подписки", callback_data="menu_subs")],
        [InlineKeyboardButton("⚙️ Настройки", callback_data="menu_settings")],
        [InlineKeyboardButton("❓ Помощь", callback_data="menu_help")],
        [InlineKeyboardButton("📅 Последние обновления", callback_data="menu_last")]
    ]
    return InlineKeyboardMarkup(keyboard)


async def clean_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Очищает интерфейс от старых кнопок"""
    await update.message.reply_text(
        "🧹 Очищаю интерфейс...",
        reply_markup=ReplyKeyboardRemove()
    )
    await update.message.reply_text(
        "✅ Интерфейс очищен! Теперь можете нажать /start",
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start - как на скриншоте"""
    user = update.effective_user
    db.add_user(
        telegram_id=user.id,
        first_name=user.first_name,
        last_name=user.last_name,
        username=user.username
    )

    logger.info(f"Новый пользователь: {user.first_name} (ID: {user.id})")

    # Приветственное сообщение как на скриншоте
    text = (
        f"👋 Привет, {user.first_name}! 🎉\n\n"
        f"📋 **Выберите пункт меню:**"
    )

    await update.message.reply_text(
        text,
        parse_mode='Markdown',
        reply_markup=get_main_menu_keyboard()  # Кнопки в сообщении
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает нажатия на кнопки меню"""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    logger.info(f"Пользователь {user_id} нажал кнопку: {data}")

    if data == "menu_current":
        await show_current_projects(query, context)
    elif data == "menu_search":
        await show_search_menu(query)
    elif data == "menu_subs":
        await show_my_subscriptions(query, user_id)
    elif data == "menu_settings":
        await show_settings_menu(query)
    elif data == "menu_help":
        await show_help(query)
    elif data == "menu_last":
        await show_last_projects(query, context)
    elif data == "back_to_main":
        await query.edit_message_text(
            "📋 **Выберите пункт меню:**",
            parse_mode='Markdown',
            reply_markup=get_main_menu_keyboard()
        )
    elif data.startswith('sub_'):
        topic = data.replace('sub_', '')
        success = db.subscribe(user_id, topic)
        if success:
            topic_name = TOPICS_SHORT.get(topic, topic)
            await query.edit_message_text(
                f"✅ Вы подписались на тему {topic_name}",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_main")
                ]])
            )
            logger.info(f"Пользователь {user_id} подписался на {topic}")
        else:
            await query.edit_message_text(
                "❌ Ошибка подписки.\nВозможно, вы уже подписаны на эту тему",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("◀️ Назад", callback_data="menu_search")
                ]])
            )
    elif data.startswith('unsub_'):
        topic = data.replace('unsub_', '')
        success = db.unsubscribe(user_id, topic)
        if success:
            topic_name = TOPICS_SHORT.get(topic, topic)
            await query.edit_message_text(
                f"✅ Вы отписались от темы {topic_name}",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_main")
                ]])
            )
            logger.info(f"Пользователь {user_id} отписался от {topic}")
        else:
            await query.edit_message_text(
                "❌ Ошибка отписки.\nВозможно, вы не были подписаны на эту тему",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("◀️ Назад", callback_data="menu_subs")
                ]])
            )


async def show_current_projects(query, context):
    """Показывает текущие проекты (по подпискам)"""
    await query.edit_message_text("🔍 Загружаю проекты по вашим подпискам...")

    projects = api.fetch_all_projects(max_pages=5)
    user_id = query.from_user.id
    user_subs = db.get_subscriptions(user_id)

    if not projects:
        await query.edit_message_text(
            "❌ Не удалось загрузить проекты",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_main")
            ]])
        )
        return

    text = "📋 **Текущие проекты (по вашим подпискам):**\n\n"
    count = 0

    for p in projects[:20]:
        title = p.get('title', 'Без названия')
        dept = p.get('developedDepartment', {}).get('description', 'Не указано')
        date = p.get('publicationDate') or p.get('creationDate', '')
        project_id = p.get('id')

        topics = ProjectClassifier.classify(
            title=p.get('title', ''),
            department=dept
        )

        # Проверяем совпадение с подписками
        project_topics = set(topics)
        user_topics = set(user_subs)

        if project_topics.intersection(user_topics):
            count += 1
            topic_str = ProjectClassifier.format_topics(topics)
            url = f"https://regulation.gov.ru/projects#npa={project_id}"

            text += f"{count}. {topic_str}\n"
            text += f"   📌 {title[:100]}...\n"
            text += f"   🏢 {dept[:50]}...\n"
            text += f"   📅 {date[:10] if date else 'Нет даты'}\n"
            text += f"   🔗 {url}\n\n"
            text += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"

    if count == 0:
        text = "❌ Нет проектов по вашим подпискам.\n\nИспользуйте '🔍 Поиск по темам' чтобы подписаться на новые темы."
    else:
        text += f"\n📊 Найдено {count} проектов"

    await query.edit_message_text(
        text,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_main")
        ]])
    )


async def show_search_menu(query):
    """Показывает меню поиска/подписки"""
    keyboard = []
    row = []
    for i, (topic_code, topic_name) in enumerate(TOPICS.items(), 1):
        button = InlineKeyboardButton(
            topic_name,
            callback_data=f"sub_{topic_code}"
        )
        row.append(button)
        if i % 2 == 0:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_main")])

    await query.edit_message_text(
        "📋 **Выберите темы для подписки:**\n(можно подписаться на несколько)",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def show_my_subscriptions(query, user_id):
    """Показывает подписки пользователя"""
    subscriptions = db.get_subscriptions(user_id)

    if not subscriptions:
        await query.edit_message_text(
            "❌ У вас нет активных подписок.\n\nХотите подписаться?",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📋 Перейти к подписке", callback_data="menu_search")],
                [InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_main")]
            ])
        )
        return

    # Показываем подписки с возможностью отписки
    text = "📌 **Ваши подписки:**\n\n"
    keyboard = []

    for topic in subscriptions:
        text += f"• {TOPICS_SHORT.get(topic, topic)}\n"
        keyboard.append([
            InlineKeyboardButton(
                f"❌ Отписаться от {TOPICS_SHORT.get(topic, topic)}",
                callback_data=f"unsub_{topic}"
            )
        ])

    keyboard.append([InlineKeyboardButton("➕ Добавить подписки", callback_data="menu_search")])
    keyboard.append([InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_main")])

    await query.edit_message_text(
        text,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def show_settings_menu(query):
    """Показывает меню настроек"""
    keyboard = [
        [InlineKeyboardButton("🔔 Вкл/Выкл уведомления", callback_data="settings_notify")],
        [InlineKeyboardButton("⏰ Время уведомлений", callback_data="settings_time")],
        [InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_main")]
    ]

    await query.edit_message_text(
        "⚙️ **Настройки**\n\nВыберите что хотите изменить:",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def show_help(query):
    """Показывает справку"""
    text = (
        "📚 **СПРАВКА**\n\n"
        "📌 **О ТЕМАХ МОНИТОРИНГА:**\n"
        "🚛 **ЭПД** - электронные перевозочные документы\n"
        "📄 **МЧД** - машиночитаемые доверенности\n"
        "📁 **ЭДО** - электронный документооборот\n"
        "✍️ **ЭП** - электронная подпись\n"
        "🧾 **ОФД** - операторы фискальных данных\n\n"
        "ℹ️ **Как это работает:**\n"
        "1. Нажмите '🔍 Поиск по темам'\n"
        "2. Выберите интересующие темы\n"
        "3. Будет присылать уведомления о новых проектах\n\n"
        "📋 **Кнопки меню:**\n"
        "• 📋 Текущие проекты - только по вашим подпискам\n"
        "• 📅 Последние обновления - все проекты\n"
        "• 🔍 Поиск по темам - подписаться на темы\n"
        "• 📌 Мои подписки - управление подписками"
    )

    await query.edit_message_text(
        text,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_main")
        ]])
    )


async def show_last_projects(query, context):
    """Показывает последние проекты (все)"""
    await query.edit_message_text("🔍 Загружаю последние проекты...")

    projects = api.fetch_all_projects(max_pages=5)

    if not projects:
        await query.edit_message_text(
            "❌ Не удалось загрузить проекты",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_main")
            ]])
        )
        return

    text = "📅 **Последние проекты:**\n\n"

    for i, p in enumerate(projects[:10], 1):
        title = p.get('title', 'Без названия')
        dept = p.get('developedDepartment', {}).get('description', 'Не указано')
        date = p.get('publicationDate') or p.get('creationDate', '')
        project_id = p.get('id')

        topics = ProjectClassifier.classify(
            title=p.get('title', ''),
            department=dept
        )
        topic_str = ProjectClassifier.format_topics(topics)
        url = f"https://regulation.gov.ru/projects#npa={project_id}"

        text += f"{i}. {topic_str}\n\n"
        text += f"   📌 {title[:300]}...\n\n"
        text += f"   🏢 {dept}\n\n"
        text += f"   📅 {date[:10] if date else 'Нет даты'}\n\n"
        text += f"   🔗 {url}\n\n"
        text += "━━━━━━━━━━━━━━━━\n"

    await query.edit_message_text(
        text,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_main")
        ]])
    )


def main():
    if TOKEN == "8218361501:AAFS9tTT2coSdo1Pk2mhWd7odDsjUq41jpQ":
        print("⚠️  Внимание! Используется токен по умолчанию!")

    application = Application.builder().token(TOKEN).build()

    # ✅ УСТАНАВЛИВАЕМ ТОЛЬКО ОДНУ КОМАНДУ /start В СИНЕМ МЕНЮ
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # Только одна команда - /start
    commands = [
        BotCommand("start", "🚀 Запустить бота"),
    ]
    loop.run_until_complete(application.bot.set_my_commands(commands))

    # Добавляем обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))

    print("🚀 Бот запущен!")
    print("📋 В сообщениях - все кнопки меню")
    print("💙 В синем меню - только команда /start")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()