import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

from database import Database
from fetcher import RegulationAPI
from classifier import ProjectClassifier

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TOKEN ="8218361501:AAFS9tTT2coSdo1Pk2mhWd7odDsjUq41jpQ"

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
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.add_user(
        telegram_id=user.id,
        first_name=user.first_name,
        last_name=user.last_name,
        username=user.username
    )

    logger.info(f"Новый пользователь: {user.first_name} (ID: {user.id})")

    text = (
        f"👋 Привет, {user.first_name}!\n\n"
        f"Я бот для мониторинга проектов нормативных правовых актов "
        f"на сайте regulation.gov.ru\n\n"
        f"Для работы со мной воспользуйтесь следующими функциями\n"
        f"/start - начало работы (регистрация)\n"
        f"/subscribe - подписаться на темы\n"
        f"/unsubscribe - отписаться от тем\n"
        f"/mysubs - показать мои подписки\n"
        f"/last - показать последние проекты\n"
        f"/help - помощь\n\n"
        f"Если возникнут какие либо проблемы обращаться к @Daudov0500"
    )
    await update.message.reply_text(text)

async def subscribe_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "📋 **Выберите темы для подписки:**\n"
        "(можно подписаться на несколько)",
        reply_markup=reply_markup
    )
async def unsubscribe_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    subscriptions = db.get_subscriptions(user_id)
    if not subscriptions:
        await update.message.reply_text("❌ У вас нет активных подписок")
        return
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data
    if data.startswith('sub_'):
        topic = data.replace('sub_', '')
        success = db.subscribe(user_id, topic)
        if success:
            topic_name = TOPICS_SHORT.get(topic, topic)
            await query.edit_message_text(f"✅ Вы подписались на тему {topic_name}")
            logger.info(f"Пользователь {user_id} подписался на {topic}")
        else:
            await query.edit_message_text("❌ Ошибка подписки.\n"
                "Возможно, вы уже подписаны на эту тему")
    elif data.startswith('unsub_'):
        topic = data.replace('unsub_', '')
        success = db.unsubscribe(user_id, topic)

        if success:
            topic_name = TOPICS_SHORT.get(topic, topic)
            await query.edit_message_text(
                f"✅ Вы отписались от темы {topic_name}"
            )
            logger.info(f"Пользователь {user_id} отписался от {topic}")
        else:
            await query.edit_message_text(
                "❌ Ошибка отписки.\n"
                "Возможно, вы не были подписаны на эту тему"
            )
async def mysubs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    subscriptions = db.get_subscriptions(user_id)

    if not subscriptions:
        await update.message.reply_text(
            "❌ У вас нет активных подписок.\n"
            "Используйте /subscribe чтобы подписаться"
        )
        return

    topics_list = []
    for topic in subscriptions:
        topics_list.append(f"• {TOPICS_SHORT.get(topic, topic)}")

    text = "📋 **Ваши подписки:**\n\n" + "\n".join(topics_list)

    await update.message.reply_text(text)
async def last_projects(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Загружаю последние проекты...")

    projects = api.fetch_all_projects(max_pages=5)

    saved_count = 0
    for p in projects[:10]:
        if db.save_project(p):
            saved_count += 1
    text = "📋 **Последние проекты:**\n\n"
    for i, p in enumerate(projects[:5], 1):
        # получаем данные проекта
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

        text += f"{i}. {topic_str}\n"
        text += f"\n"
        text += f"   📌 {title}\n"
        text += f"\n"
        text += f"   🏢 {dept}\n"
        text += f"\n"
        text += f"   📅 {date[:10] if date else 'Нет даты'}\n"
        text += f"\n"
        text += f"   🔗 {url}\n\n"
        text += f"\n"
        text += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"

    text += "💡 Чтобы получать уведомления о новых проектах - подпишитесь на темы через /subscribe"

    await update.message.reply_text(text)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📌 **О ТЕМАХ МОНИТОРИНГА:**\n"
        "🚛 **ЭПД** - электронные перевозочные документы\n"
        "   • ГИС ЭПД, ЭТрН, путевые листы\n\n"
        "📄 **МЧД** - машиночитаемые доверенности\n"
        "   • Форматы доверенностей, XSD-схемы\n\n"
        "📁 **ЭДО** - электронный документооборот\n"
        "   • Операторы ЭДО, роуминг, форматы\n\n"
        "✍️ **ЭП** - электронная подпись\n"
        "   • УКЭП, удостоверяющие центры, криптография\n\n"
        "🧾 **ОФД** - операторы фискальных данных\n"
        "   • ККТ, онлайн-кассы, фискальные накопители\n\n"

        "ℹ️ **Как это работает:**\n"
        "1. Подпишитесь на нужные темы\n"
        "2. Бот каждое утро проверяет новые проекты\n"
        "3. Если найден проект по вашей теме - вы получите уведомление"
    )

    await update.message.reply_text(text)

async def notify_user(telegram_id: int, project: dict, topic: str):
    logger.info(f"Уведомление для {telegram_id} по теме {topic}: {project.get('id')}")


def main():
    if TOKEN == "8218361501:AAFS9tTT2coSdo1Pk2mhWd7odDsjUq41jpQ":
        print("⚠️  Внимание! Используется токен по умолчанию!")
    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("subscribe", subscribe_menu))
    application.add_handler(CommandHandler("unsubscribe", unsubscribe_menu))
    application.add_handler(CommandHandler("mysubs", mysubs))
    application.add_handler(CommandHandler("last", last_projects))
    application.add_handler(CommandHandler("help", help_command))

    application.add_handler(CallbackQueryHandler(button_handler))

    print("🚀 Бот запущен и готов к работе!")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()

