
import logging
import asyncio
import time
import hashlib
import json
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List, Set
from collections import OrderedDict
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.error import RetryAfter
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
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


STAGE_DESCRIPTIONS = {
    'Text': '📝 Текст проекта',
    'Discussion': '💬 Обсуждение',
    'Evaluation': '📊 Оценка регулирующего воздействия',
    'Expertise': '🔍 Экспертиза',
    'Approval': '✅ Согласование',
    'Signing': '✍️ Подписание',
    'Registration': '📋 Регистрация',
    'Publication': '📢 Опубликован',
    'Cancelled': '❌ Отменен',
    'Completed': '✔️ Завершен'
}

STATUS_DESCRIPTIONS = {
    'Developing': '🔄 Разработка',
    'Discussion': '💬 Публичное обсуждение',
    'Evaluation': '📊 Оценка регулирующего воздействия',
    'Conclusion': '📝 Подготовка заключения',
    'Approval': '✅ Согласование',
    'Signing': '✍️ Подписание',
    'Registered': '📋 Зарегистрирован',
    'Published': '📢 Опубликован',
    'Cancelled': '❌ Отменен',
    'EndDiscussion': '✅ Обсуждение завершено',
    'StartDiscussion': '🆕 Начало обсуждения',
    'OnApprove': '⏳ На согласовании',
    'Rejected': '❌ Отклонен',
    'Draft': '📝 Черновик',
}

PROCEDURE_TYPES = {
    '1': '📢 Раскрытие информации о подготовке проектов',
    '2': '💬 Публичное обсуждение',
    '3': '📊 Оценка регулирующего воздействия',
    '4': '🔍 Экспертиза',
    '5': '✅ Согласование'
}

PROJECT_TYPES = {
    '1': '📜 Проект федерального закона',
    '2': '📋 Проект ведомственного акта',
    '3': '📌 Проект указа Президента РФ',
    '4': '📑 Проект постановления Правительства РФ',
    '5': '📄 Проект распоряжения Правительства РФ'
}

async def send_daily_notifications(application: Application):
    logger.info("🕐 Запуск ежедневной рассылки уведомлений")
    users = db.get_all_users()

    if not users:
        logger.info("Нет пользователей для уведомлений")
        return

    yesterday = datetime.now() - timedelta(days=1)
    yesterday_str = yesterday.strftime('%Y-%m-%d')

    cache_key = f"daily_projects_{yesterday_str}"
    projects = projects_cache.get(cache_key)

    if projects is None:
        projects = await fetch_with_retry_simple(
            api.fetch_all_projects,
            max_retries=3,
            delay=2,
            max_pages=20
        )
        if projects:
            projects_cache.set(cache_key, projects)

    if not projects:
        logger.error("Не удалось загрузить проекты для уведомлений")
        return

    yesterday_projects = []
    for p in projects:
        date_str = p.get('publicationDate') or p.get('creationDate', '')
        if date_str:
            try:
                project_date = datetime.strptime(date_str[:10], '%Y-%m-%d').date()
                if project_date == yesterday.date():

                    topics = ProjectClassifier.classify(title=p.get('title', ''))
                    if topics:
                        p['classified_topics'] = topics
                        yesterday_projects.append(p)
            except (ValueError, TypeError):
                continue

    projects_by_topic = {}
    for p in yesterday_projects:
        for topic in p.get('classified_topics', []):
            if topic not in projects_by_topic:
                projects_by_topic[topic] = []
            projects_by_topic[topic].append(p)

    sent_count = 0
    for user in users:
        user_id = user['telegram_id']
        user_subs = db.get_subscriptions(user_id)
        if not user_subs:
            continue

        user_projects = []
        for p in yesterday_projects:
            project_topics = set(p.get('classified_topics', []))
            if project_topics.intersection(set(user_subs)):
                user_projects.append(p)

        if user_projects:
            message = format_projects_notification(user_projects, user_subs, yesterday)
        else:
            message = format_no_projects_notification(user_subs, yesterday)
        try:
            await application.bot.send_message(
                chat_id=user_id,
                text=message,
                parse_mode='Markdown'
            )
            sent_count += 1
            logger.info(f"Уведомление отправлено пользователю {user_id}")

            # Небольшая задержка между отправками
            await asyncio.sleep(0.5)

        except Exception as e:
            logger.error(f"Ошибка отправки уведомления пользователю {user_id}: {e}")

        logger.info(f"✅ Уведомления отправлены {sent_count} пользователям")
def format_projects_notification(projects: List[Dict], subscriptions: List[str], date: datetime) -> str:
    date_str = date.strftime('%d.%m.%Y')

    projects_by_topic = {}
    for p in projects:
        for topic in p.get('classified_topics', []):
            if topic in subscriptions:
                if topic not in projects_by_topic:
                    projects_by_topic[topic] = []
                projects_by_topic[topic].append(p)

    text = f"📅 **Дайджест за {date_str}**\n\n"

    text += "📊 **Статистика по вашим подпискам:**\n"
    for topic in subscriptions:
        topic_name = TOPICS_SHORT.get(topic, topic)
        count = len([p for p in projects if topic in p.get('classified_topics', [])])
        if count > 0:
            text += f"✅ {topic_name}: **{count}** проектов\n"
        else:
            text += f"❌ {topic_name}: **0** проектов\n"

    text += "\n"

    if projects:
        text += "🔍 **Новые проекты:**\n\n"

        for i, p in enumerate(projects[:5], 1):  # Показываем первые 5 проектов
            title = p.get('title', 'Без названия')[:100]
            dept = p.get('developedDepartment', {}).get('description', 'Не указано')
            project_id = p.get('id')

            # Показываем темы проекта
            project_topics = [TOPICS_SHORT.get(t, t) for t in p.get('classified_topics', [])]
            topics_str = ', '.join(project_topics)

            url = f"https://regulation.gov.ru/projects#npa={project_id}"

            text += f"{i}. **{topics_str}**\n"
            text += f"   📌 {title}...\n"
            text += f"   🏢 {dept}\n"
            text += f"   🔗 {url}\n\n"

        if len(projects) > 5:
            text += f"... и еще {len(projects) - 5} проектов\n"
    else:
        text += "😴 Проектов не найдено\n"

        # Добавляем полезные ссылки
    text += "\n━━━━━━━━━━━━━━━━━━━━\n"
    text += "🔔 **Управление подписками:** /start"

    return text

def format_no_projects_notification(subscriptions: List[str], date: datetime) -> str:
    date_str = date.strftime('%d.%m.%Y')

    text = f"📅 **Дайджест за {date_str}**\n\n"
    text += "😴 **За вчера не вышло ни одного проекта** по вашим темам:\n\n"

    # Показываем подписки пользователя
    for topic in subscriptions:
        topic_name = TOPICS_SHORT.get(topic, topic)
        text += f"• {topic_name}\n"

    text += "\n📊 **Общая статистика:**\n"
    text += f"• Отслеживается тем: **{len(subscriptions)}**\n"

    text += "\n💡 **Совет:**\n"
    text += "Вы можете добавить новые темы через меню '🔍 Поиск по темам'\n\n"

    text += "🔔 **Управление подписками:** через меню '📌 Мои подписки'"

    return text


async def test_notifications(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Команда для тестирования уведомлений
    """
    await update.message.reply_text("🔍 Проверяю проекты за вчера...")

    # Имитируем отправку уведомления только этому пользователю
    yesterday = datetime.now() - timedelta(days=1)

    # Получаем проекты
    projects = await fetch_with_retry_simple(
        api.fetch_all_projects,
        max_retries=2,
        delay=2,
        max_pages=50
    )

    if not projects:
        await update.message.reply_text("❌ Не удалось загрузить проекты")
        return

    # Фильтруем за вчера
    yesterday_projects = []
    for p in projects:
        date_str = p.get('publicationDate') or p.get('creationDate', '')
        if date_str:
            try:
                project_date = datetime.strptime(date_str[:10], '%Y-%m-%d').date()
                if project_date == yesterday.date():
                    topics = ProjectClassifier.classify(title=p.get('title', ''))
                    if topics:
                        p['classified_topics'] = topics
                        yesterday_projects.append(p)
            except:
                continue

    # Получаем подписки пользователя
    user_subs = db.get_subscriptions(update.effective_user.id)

    if not user_subs:
        await update.message.reply_text(
            "❌ У вас нет подписок. Сначала подпишитесь на темы!",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔍 Перейти к подписке", callback_data="menu_search")
            ]])
        )
        return

    # Фильтруем по подпискам
    user_projects = []
    for p in yesterday_projects:
        if set(p.get('classified_topics', [])).intersection(set(user_subs)):
            user_projects.append(p)

    if user_projects:
        message = format_projects_notification(user_projects, user_subs, yesterday)
    else:
        message = format_no_projects_notification(user_subs, yesterday)

    await update.message.reply_text(message, parse_mode='Markdown')



def format_project_stage(project: Dict) -> str:
    stage = project.get('stage', '')
    status = project.get('status', '')
    procedure = project.get('procedure', {})
    project_type = project.get('projectType', {})

    stage_text = []

    if project_type and project_type.get('id'):
        type_desc = PROJECT_TYPES.get(project_type.get('id'), project_type.get('description', 'Неизвестный тип'))
        stage_text.append(f"📌 **Тип:** {type_desc}")

    if stage:
        stage_desc = STAGE_DESCRIPTIONS.get(stage, stage)
        stage_text.append(f"\n📍 **Этап:** {stage_desc}")

    if status:
        status_desc = STATUS_DESCRIPTIONS.get(status, status)
        stage_text.append(f"  ⚡ **Статус:** {status_desc}")

    if procedure and procedure.get('id'):
        proc_desc = PROCEDURE_TYPES.get(procedure.get('id'), procedure.get('description', 'Неизвестная процедура'))
        stage_text.append(f"  🔄 **Процедура:** {proc_desc}")

    dates = []

    if project.get('startPublicDiscussion') and project.get('endPublicDiscussion'):
        start = project['startPublicDiscussion'][:10] if project['startPublicDiscussion'] else ''
        end = project['endPublicDiscussion'][:10] if project['endPublicDiscussion'] else ''
        if start and end:
            dates.append(f"🗓 **Публичное обсуждение:** {start} - {end}")

    if project.get('startParallelPublicDiscussion') and project.get('endParallelPublicDiscussion'):
        start = project['startParallelPublicDiscussion'][:10] if project['startParallelPublicDiscussion'] else ''
        end = project['endParallelPublicDiscussion'][:10] if project['endParallelPublicDiscussion'] else ''
        if start and end:
            dates.append(f"🔄 **Параллельное обсуждение:** {start} - {end}")

    if project.get('deadline'):
        deadline = project['deadline'][:10] if project['deadline'] else ''
        if deadline:
            dates.append(f"⏰ **Крайний срок:** {deadline}")

    if dates:
        stage_text.append("\n".join(dates))

    return "\n".join(stage_text)


def get_stage_emoji(stage: str) -> str:
    emoji_map = {
        'Text': '📝',
        'Discussion': '💬',
        'Evaluation': '📊',
        'Expertise': '🔍',
        'Approval': '✅',
        'Signing': '✍️',
        'Registration': '📋',
        'Publication': '📢',
        'Cancelled': '❌',
        'Completed': '✔️'
    }
    return emoji_map.get(stage, '📌')


def get_status_emoji(status: str) -> str:
    emoji_map = {
        'Developing': '🔄',
        'Discussion': '💬',
        'Evaluation': '📊',
        'Conclusion': '📝',
        'Approval': '✅',
        'Signing': '✍️',
        'Registered': '📋',
        'Published': '📢',
        'Cancelled': '❌'
    }
    return emoji_map.get(status, '⚡')



class Cache:
    def __init__(self, max_size: int = 100, ttl: int = 300):
        self.max_size = max_size
        self.ttl = ttl
        self.cache = OrderedDict()
        self.timestamps = {}

    def _generate_key(self, data: Any) -> str:
        if isinstance(data, (dict, list)):
            data_str = json.dumps(data, sort_keys=True)
        else:
            data_str = str(data)
        return hashlib.md5(data_str.encode()).hexdigest()

    def get(self, key: str) -> Optional[Any]:
        if key in self.cache:
            if time.time() - self.timestamps[key] < self.ttl:
                self.cache.move_to_end(key)
                logger.debug(f"Cache HIT for key: {key[:8]}...")
                return self.cache[key]
            else:
                self.delete(key)
                logger.debug(f"Cache EXPIRED for key: {key[:8]}...")
        return None

    def set(self, key: str, value: Any):
        if key in self.cache:
            self.cache.move_to_end(key)
        self.cache[key] = value
        self.timestamps[key] = time.time()

        while len(self.cache) > self.max_size:
            oldest_key, _ = self.cache.popitem(last=False)
            self.timestamps.pop(oldest_key, None)
            logger.debug(f"Cache EVICTED oldest key: {oldest_key[:8]}...")

        logger.debug(f"Cache SET for key: {key[:8]}...")

    def delete(self, key: str):
        if key in self.cache:
            self.cache.pop(key)
            self.timestamps.pop(key, None)
            logger.debug(f"Cache DELETED key: {key[:8]}...")

    def clear(self):
        self.cache.clear()
        self.timestamps.clear()
        logger.info("Cache CLEARED")

    def get_stats(self) -> Dict:
        return {
            "size": len(self.cache),
            "max_size": self.max_size,
            "ttl": self.ttl,
            "keys": list(self.cache.keys())[:5]
        }


projects_cache = Cache(max_size=50, ttl=36000)
archive_cache = Cache(max_size=30, ttl=36000)
subscriptions_cache = Cache(max_size=200, ttl=60)

async def safe_send_message(update_or_context, text: str, parse_mode: str = 'Markdown',
                            reply_markup=None, chunk_size: int = 4096):

    if hasattr(update_or_context, 'message'):
        send_func = update_or_context.message.reply_text

    elif hasattr(update_or_context, 'bot') and hasattr(update_or_context, 'effective_chat'):
        send_func = lambda t, **kwargs: update_or_context.bot.send_message(
            chat_id=update_or_context.effective_chat.id,
            text=t,
            **kwargs
        )
    elif hasattr(update_or_context, 'edit_message_text'):

        return await split_long_message_for_query(update_or_context, text, parse_mode, reply_markup, chunk_size)
    else:

        send_func = update_or_context


    if len(text) <= chunk_size:
        try:
            return await send_func(text, parse_mode=parse_mode, reply_markup=reply_markup)
        except Exception as e:
            logger.error(f"Error sending message: {e}")
            return await send_func(text, reply_markup=reply_markup)

    parts = []
    current_part = ""

    for line in text.split('\n'):
        if len(current_part) + len(line) + 1 <= chunk_size:
            if current_part:
                current_part += '\n' + line
            else:
                current_part = line
        else:
            if current_part:
                parts.append(current_part)
            current_part = line

    if current_part:
        parts.append(current_part)

    sent_messages = []
    for i, part in enumerate(parts):
        try:
            if i == len(parts) - 1 and reply_markup:
                msg = await send_func(part, parse_mode=parse_mode, reply_markup=reply_markup)
            else:
                msg = await send_func(part, parse_mode=parse_mode)
            sent_messages.append(msg)

            if i < len(parts) - 1:
                await asyncio.sleep(0.5)
        except RetryAfter as e:
            logger.warning(f"Rate limited, waiting {e.retry_after} seconds")
            await asyncio.sleep(e.retry_after)

            msg = await send_func(part, parse_mode=parse_mode)
            sent_messages.append(msg)
        except Exception as e:
            logger.error(f"Error sending message part {i}: {e}")

    return sent_messages



from functools import partial


async def fetch_with_retry_simple(fetch_func, max_retries=3, delay=2, *args, **kwargs):

    last_error = None

    func_with_args = partial(fetch_func, *args, **kwargs)

    for attempt in range(1, max_retries + 1):
        try:
            logger.info(f"Попытка {attempt} из {max_retries}")

            result = await asyncio.get_event_loop().run_in_executor(
                None, func_with_args
            )

            if result:
                logger.info(f"Успешно на попытке {attempt}")
                return result
            else:
                logger.warning(f"Попытка {attempt} вернула пустой результат")

        except Exception as e:
            last_error = e
            logger.error(f"Ошибка на попытке {attempt}: {e}")

        if attempt < max_retries:
            wait_time = delay * attempt
            logger.info(f"Ждем {wait_time} секунд...")
            await asyncio.sleep(wait_time)

    logger.error(f"Все {max_retries} попыток провалились")
    return None

async def split_long_message_for_query(query, text: str, parse_mode: str = 'Markdown',
                                       reply_markup=None, chunk_size: int = 4096):

    if len(text) <= chunk_size:
        try:
            return await query.edit_message_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
        except Exception as e:
            logger.error(f"Error editing message: {e}")
            return await query.edit_message_text(text, reply_markup=reply_markup)


    parts = []
    current_part = ""

    for line in text.split('\n'):
        if len(current_part) + len(line) + 1 <= chunk_size:
            if current_part:
                current_part += '\n' + line
            else:
                current_part = line
        else:
            if current_part:
                parts.append(current_part)
            current_part = line

    if current_part:
        parts.append(current_part)

    try:
        await query.edit_message_text(parts[0], parse_mode=parse_mode)
    except Exception as e:
        await query.edit_message_text(parts[0])

    for i, part in enumerate(parts[1:], 1):
        try:
            if i == len(parts) - 1 and reply_markup:
                await query.message.reply_text(part, parse_mode=parse_mode, reply_markup=reply_markup)
            else:
                await query.message.reply_text(part, parse_mode=parse_mode)
            await asyncio.sleep(0.5)
        except Exception as e:
            logger.error(f"Error sending part {i}: {e}")

    return None



TOPICS = {
    'kedo': '👥 КЭДО (кадровый электронный документооборот)',
    'mchd': '📄 МЧД (машиночитаемые доверенности)',
    'epd': '🚛 ЭПД (электронные перевозочные документы)',
    'ep': '✍️ ЭП (электронная подпись)',
    'ofd': '🧾 ОФД (операторы фискальных данных)',
    'reporting': '📊 Отчетность (электронная отчетность)',
    'edo_b2b': '🔄 B2B ЭДО (коммерческий документооборот)',
    'ecosystem': '🌐 Экосистема / 152-ФЗ'
}

TOPICS_SHORT = {
    'kedo': '👥 КЭДО',
    'mchd': '📄 МЧД',
    'epd': '🚛 ЭПД',
    'ep': '✍️ ЭП',
    'ofd': '🧾 ОФД',
    'reporting': '📊 Отчетность',
    'edo_b2b': '🔄 B2B ЭДО',
    'ecosystem': '🌐 Экосистема'
}

USER_ROLES = {
    'analyst': {
        'name': '📊 Аналитик',
        'description': 'Краткие уведомления о новых проектах',
        'format': 'analyst'
    },
    'lawyer': {
        'name': '⚖️ Юрист',
        'description': 'Полный обзор проектов НПА',
        'format': 'lawyer'
    },
    'product': {
        'name': '📈 Product-менеджер',
        'description': 'Еженедельный дайджест',
        'format': 'product'
    }
}


def get_main_menu_keyboard():
    keyboard = [
        [InlineKeyboardButton("📋 Текущие проекты", callback_data="menu_current")],
        [InlineKeyboardButton("🔍 Поиск по темам", callback_data="menu_search")],
        [InlineKeyboardButton("📌 Мои подписки", callback_data="menu_subs")],
        [InlineKeyboardButton("🗂 Архив", callback_data="menu_archive")],
        [InlineKeyboardButton("⚙️ Настройки", callback_data="menu_settings")],
        [InlineKeyboardButton("❓ Помощь", callback_data="menu_help")],
        [InlineKeyboardButton("📅 Последние обновления", callback_data="menu_last")]
    ]
    return InlineKeyboardMarkup(keyboard)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.add_user(
        telegram_id=user.id,
        first_name=user.first_name,
        last_name=user.last_name,
        username=user.username
    )

    logger.info(f"Новый пользователь: {user.first_name} (ID: {user.id})")

    cache_key = f"subs_{user.id}"
    subscriptions_cache.delete(cache_key)

    text = (
        f"👋 Привет, {user.first_name}! 🎉\n\n"
        f"📋 **Выберите пункт меню:**"
    )

    await update.message.reply_text(
        text,
        parse_mode='Markdown',
        reply_markup=get_main_menu_keyboard()
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    elif data == "menu_archive":
        await show_archive_topics(query)
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
    elif data == "clear_cache":
        projects_cache.clear()
        archive_cache.clear()
        subscriptions_cache.clear()
        await query.edit_message_text(
            "✅ Кеш успешно очищен!",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_main")
            ]])
        )
    elif data.startswith('archive_'):
        topic = data.replace('archive_', '')
        await show_archive_projects(query, context, topic)
    elif data.startswith('sub_'):
        topic = data.replace('sub_', '')
        success = db.subscribe(user_id, topic)
        if success:
            cache_key = f"subs_{user_id}"
            subscriptions_cache.delete(cache_key)

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
            cache_key = f"subs_{user_id}"
            subscriptions_cache.delete(cache_key)

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


async def show_archive_topics(query):
    keyboard = []
    row = []
    for i, (topic_code, topic_name) in enumerate(TOPICS.items(), 1):
        button = InlineKeyboardButton(
            topic_name,
            callback_data=f"archive_{topic_code}"
        )
        row.append(button)
        if i % 2 == 0:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_main")])

    await query.edit_message_text(
        "🗂 **Архив проектов за 30 дней**\n\n"
        "Выберите тему для просмотра:",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def show_archive_projects(query, context, topic):
    await query.answer()
    await query.edit_message_text(f"🔍 Загружаю архив проектов по теме {TOPICS_SHORT.get(topic, topic)}...")

    # 1. Сначала получаем ВСЕ проекты (общий кэш)
    all_projects_cache_key = f"all_projects_{datetime.now().strftime('%Y%m%d')}"
    all_projects = projects_cache.get(all_projects_cache_key)

    if all_projects is None:
        # Загружаем проекты только если их нет в кэше
        all_projects = await fetch_with_retry_simple(
            api.fetch_all_projects,
            max_retries=3,
            delay=2,
            max_pages=500
        )
        if all_projects:
            projects_cache.set(all_projects_cache_key, all_projects)
            logger.info(f"Cached {len(all_projects)} projects for all topics")

    if not all_projects:
        await query.edit_message_text(
            "❌ Не удалось загрузить проекты после 3 попыток.\n"
        "Попробуйте позже.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Назад к темам", callback_data="menu_archive")
            ]])
        )
        return

    filtered_cache_key = f"archive_{topic}_{datetime.now().strftime('%Y%m%d')}"
    filtered_projects = archive_cache.get(filtered_cache_key)

    if filtered_projects is None:
        thirty_days_ago = datetime.now() - timedelta(days=30)
        filtered_projects = []

        for p in all_projects:
            date_str = p.get('publicationDate') or p.get('creationDate', '')
            if date_str:
                try:
                    project_date = datetime.strptime(date_str[:10], '%Y-%m-%d')
                    if project_date >= thirty_days_ago:
                        p_topics = ProjectClassifier.classify(
                            title=p.get('title', '')
                        )
                        if topic in p_topics:
                            filtered_projects.append(p)
                except:
                    continue


        filtered_projects.sort(
            key=lambda x: x.get('publicationDate') or x.get('creationDate', ''),
            reverse=True
        )


        archive_cache.set(filtered_cache_key, filtered_projects)
        logger.info(f"Cached {len(filtered_projects)} projects for topic {topic}")

    if not filtered_projects:
        await query.edit_message_text(
            f"❌ Нет проектов по теме {TOPICS_SHORT.get(topic, topic)} за последние 30 дней",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Назад к темам", callback_data="menu_archive")
            ]])
        )
        return

    text = f"🗂 **Архив {TOPICS_SHORT.get(topic, topic)} за 30 дней**\n\n"
    text += f"📅 Период: {(datetime.now() - timedelta(days=30)).strftime('%d.%m.%Y')} - {datetime.now().strftime('%d.%m.%Y')}\n\n"
    text += f"📊 Найдено проектов: {len(filtered_projects)}\n\n"
    text += "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

    count = 0
    for p in filtered_projects[:30]:
        count += 1
        title = p.get('title', 'Без названия')
        dept = p.get('developedDepartment', {}).get('description', 'Не указано')
        date = p.get('publicationDate') or p.get('creationDate', '')
        project_id = p.get('id')

        stage_info = format_project_stage(p)

        status_emoji = get_status_emoji(p.get('status', ''))

        url = f"https://regulation.gov.ru/projects#npa={project_id}"

        text += f"{count}. {status_emoji} **{TOPICS_SHORT.get(topic, topic)}**\n\n"
        text += f"   📌 {title[:150]}...\n\n"
        text += f"   🏢 {dept[:100]}\n\n"

        if stage_info:
            for line in stage_info.split('\n'):
                text += f"   {line}\n"

        text += f"   📅 {date[:10] if date else 'Нет даты'}\n\n"
        text += f"   🔗 {url}\n\n"
        text += "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

    if len(filtered_projects) > 30:
        text += f"\n... и еще {len(filtered_projects) - 30} проектов"

    keyboard = [
        [InlineKeyboardButton("◀️ Назад к темам", callback_data="menu_archive")],
        [InlineKeyboardButton("◀️ В главное меню", callback_data="back_to_main")]
    ]

    await split_long_message_for_query(
        query,
        text,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def show_current_projects(query, context):
    await query.edit_message_text("🔍 Загружаю проекты по вашим подпискам...")

    user_id = query.from_user.id

    cache_key_subs = f"subs_{user_id}"
    user_subs = subscriptions_cache.get(cache_key_subs)

    if user_subs is None:
        user_subs = db.get_subscriptions(user_id)
        subscriptions_cache.set(cache_key_subs, user_subs)
        logger.info(f"Cached subscriptions for user {user_id}")

    if not user_subs:
        await query.edit_message_text(
            "❌ У вас нет активных подписок.\n\nХотите подписаться?",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📋 Перейти к подписке", callback_data="menu_search")],
                [InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_main")]
            ])
        )
        return

    cache_key_projects = f"all_projects_{datetime.now().strftime('%Y%m%d_%H')}"
    projects = projects_cache.get(cache_key_projects)

    if projects is None:
        projects = await fetch_with_retry_simple(
            api.fetch_all_projects,
            max_retries=3,
            delay=2,
            max_pages=500
        )
        if projects:
            projects_cache.set(cache_key_projects, projects)
            logger.info(f"Cached {len(projects)} projects")

    if not projects:
        await query.edit_message_text(
            "❌ Не удалось загрузить проекты.\n"
            "Попробуйте позже.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_main")
            ]])
        )
        return

    text = "📋 **Текущие проекты (по вашим подпискам):**\n\n"
    count = 0
    matching_projects = []

    for p in projects:
        title = p.get('title', 'Без названия')
        dept = p.get('developedDepartment', {}).get('description', 'Не указано')
        date = p.get('publicationDate') or p.get('creationDate', '')
        project_id = p.get('id')

        topics = ProjectClassifier.classify(
            title=p.get('title')
        )

        project_topics = set(topics)
        user_topics_set = set(user_subs)

        if project_topics.intersection(user_topics_set):
            count += 1
            topic_str = ProjectClassifier.format_topics(topics)
            url = f"https://regulation.gov.ru/projects#npa={project_id}"

            stage_info = format_project_stage(p)

            project_info = {
                'number': count,
                'topic_str': topic_str,
                'title': title[:100],
                'dept': dept,
                'date': date[:10] if date else 'Нет даты',
                'url': url,
                'stage_info': stage_info,
                'status_emoji': get_status_emoji(p.get('status', ''))
            }
            matching_projects.append(project_info)

    if not matching_projects:
        text = "❌ Нет проектов по вашим подпискам.\n\nИспользуйте '🔍 Поиск по темам' чтобы подписаться на новые темы."
        await query.edit_message_text(
            text,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_main")
            ]])
        )
        return

    for project in matching_projects:
        text += f"{project['number']}. {project['status_emoji']} {project['topic_str']}\n\n"
        text += f"   📌 {project['title']}...\n\n"
        text += f"   🏢 {project['dept']}\n\n"

        if project['stage_info']:
            for line in project['stage_info'].split('\n\n')[:3]:
                text += f"   {line}\n\n"

        text += f"   📅 {project['date']}\n\n"
        text += f"   🔗 {project['url']}\n\n"
        text += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"



    await split_long_message_for_query(
        query,
        text,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_main")
        ]])
    )


async def show_search_menu(query):
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
    cache_key = f"subs_{user_id}"
    subscriptions = subscriptions_cache.get(cache_key)

    if subscriptions is None:
        subscriptions = db.get_subscriptions(user_id)
        subscriptions_cache.set(cache_key, subscriptions)
        logger.info(f"Cached subscriptions for user {user_id}")

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
    cache_stats = (
        f"\n\n📊 **Статистика кеша:**\n"
        f"Проекты: {projects_cache.get_stats()['size']}/{projects_cache.get_stats()['max_size']}\n"
        f"Архив: {archive_cache.get_stats()['size']}/{archive_cache.get_stats()['max_size']}\n"
        f"Подписки: {subscriptions_cache.get_stats()['size']}/{subscriptions_cache.get_stats()['max_size']}"
    )

    keyboard = [
        [InlineKeyboardButton("🔔 Вкл/Выкл уведомления", callback_data="settings_notify")],
        [InlineKeyboardButton("⏰ Время уведомлений", callback_data="settings_time")],
        [InlineKeyboardButton("🗑 Очистить кеш", callback_data="clear_cache")],
        [InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_main")]
    ]

    await query.edit_message_text(
        f"⚙️ **Настройки**\n\nВыберите что хотите изменить:{cache_stats}",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def show_help(query):
    text = (
        "📚 **СПРАВКА**\n\n"
        "📌 **О ТЕМАХ МОНИТОРИНГА:**\n"
        "👥 **КЭДО** - кадровый электронный документооборот\n"
        "📄 **МЧД** - машиночитаемые доверенности\n"
        "🚛 **ЭПД** - электронные перевозочные документы\n"
        "✍️ **ЭП** - электронная подпись / удостоверяющие центры\n"
        "🧾 **ОФД** - операторы фискальных данных\n"
        "📊 **Отчетность** - электронная налоговая и бухгалтерская отчетность\n"
        "🔄 **B2B ЭДО** - коммерческий документооборот и роуминг\n"
        "🌐 **Экосистема** - 152-ФЗ, 125-ФЗ, хранение, архив\n\n"
        "📊 **ЭТАПЫ ПРОЕКТОВ:**\n"
        "📝 **Text** - Текст проекта\n"
        "💬 **Discussion** - Публичное обсуждение\n"
        "📊 **Evaluation** - Оценка регулирующего воздействия\n"
        "🔍 **Expertise** - Экспертиза\n"
        "✅ **Approval** - Согласование\n"
        "✍️ **Signing** - Подписание\n"
        "📋 **Registration** - Регистрация\n"
        "📢 **Publication** - Опубликован\n\n"
        "ℹ️ **Как это работает:**\n"
        "1. Нажмите '🔍 Поиск по темам'\n"
        "2. Выберите интересующие темы\n"
        "3. Бот покажет проекты по вашим подпискам\n\n"
        "📋 **Кнопки меню:**\n"
        "• 📋 Текущие проекты - только по вашим подпискам\n"
        "• 📅 Последние обновления - все проекты\n"
        "• 🔍 Поиск по темам - подписаться на темы\n"
        "• 📌 Мои подписки - управление подписками\n"
        "• 🗂 Архив - проекты за 30 дней по теме\n\n"
    )

    await query.edit_message_text(
        text,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_main")
        ]])
    )


async def show_last_projects(query, context):
    await query.edit_message_text("🔍 Загружаю последние проекты...")

    cache_key = f"last_projects_{datetime.now().strftime('%Y%m%d_%H')}"
    projects = projects_cache.get(cache_key)

    if projects is None:
        projects = await fetch_with_retry_simple(
            api.fetch_all_projects,
            max_retries=3,
            delay=2,
            max_pages=10
        )
        if projects:
            projects_cache.set(cache_key, projects)
            logger.info(f"Cached {len(projects)} projects")

    if not projects:
        await query.edit_message_text(
            "❌ Не удалось загрузить проекты",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_main")
            ]])
        )
        return

    text = "📅 **Последние проекты:**\n\n"
    today = datetime.now().date()
    yesterday = today - timedelta(days=1)
    projects_shown = 0



    for i, p in enumerate(projects, 1):
        date_str = p.get('publicationDate') or p.get('creationDate', '')
        if date_str:
            try:
                project_date = datetime.strptime(date_str[:10], '%Y-%m-%d').date()
                if project_date != yesterday :
                    continue
            except (ValueError, TypeError):
                continue

        projects_shown += 1
        title = p.get('title', 'Без названия')
        dept = p.get('developedDepartment', {}).get('description', 'Не указано')
        date = p.get('publicationDate') or p.get('creationDate', '')
        project_id = p.get('id')

        topics = ProjectClassifier.classify(
            title=p.get('title')
        )
        topic_str = ProjectClassifier.format_topics(topics)

        stage_info = format_project_stage(p)
        status_emoji = get_status_emoji(p.get('status', ''))

        url = f"https://regulation.gov.ru/projects#npa={project_id}"

        text += f"{i}. {status_emoji} {topic_str}\n\n"
        text += f"   📌 {title[:200]}...\n\n"
        text += f"   🏢 {dept}\n\n"

        if stage_info:
            for line in stage_info.split('\n')[:3]:
                text += f"   {line}\n"

        text += f"   📅 {date[:10] if date else 'Нет даты'}\n\n"
        text += f"   🔗 {url}\n\n"

        separator = "━" * 18
        text += separator + "\n"

    if projects_shown == 0:
        await query.edit_message_text(
            "📅 За сегодня проектов нет",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_main")
            ]])
        )
        return

    await split_long_message_for_query(
        query,
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
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
             send_daily_notifications,
             trigger=CronTrigger(hour="7" , minute='0'),
             args=[application],
             id='test_notifications',
             replace_existing=True
         )

    scheduler.start()
    logger.info("⏰ Планировщик уведомлений запущен (ежедневно в 9:00)")



    commands = [
        BotCommand("start", "🚀 Запустить бота"),
    ]


    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("test_notify", test_notifications))
    application.add_handler(CallbackQueryHandler(button_handler))

    logger.info("🚀 Бот запущен с поддержкой кеша и отображением этапов проектов!")
    logger.info(f"📊 Настройки кеша:")

    logger.info(f"   • Проекты: макс={projects_cache.max_size}, TTL={projects_cache.ttl}с")
    logger.info(f"   • Архив: макс={archive_cache.max_size}, TTL={archive_cache.ttl}с")
    logger.info(f"   • Подписки: макс={subscriptions_cache.max_size}, TTL={subscriptions_cache.ttl}с")

    application.run_polling(allowed_updates=Update.ALL_TYPES)
    scheduler.shutdown()

if __name__ == "__main__":
    main()
