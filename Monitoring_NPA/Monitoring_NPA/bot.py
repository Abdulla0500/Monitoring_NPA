import logging
import asyncio
import time
import hashlib
import json
import re
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List, Set
from collections import OrderedDict
from functools import partial
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
    'Undefined': '🔄 Разработка',
    'PreDiscussion': '💬 Предварительное обсуждение',
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
    'Complete': '✅ Завершён'
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


class Cache:
    def __init__(self, max_size: int = 100, ttl: int = 300):
        self.max_size = max_size
        self.ttl = ttl
        self.cache = OrderedDict()
        self.timestamps = {}

    def get(self, key: str) -> Optional[Any]:
        if key in self.cache:
            if time.time() - self.timestamps[key] < self.ttl:
                self.cache.move_to_end(key)
                return self.cache[key]
            else:
                self.delete(key)
        return None

    def set(self, key: str, value: Any):
        if key in self.cache:
            self.cache.move_to_end(key)
        self.cache[key] = value
        self.timestamps[key] = time.time()
        while len(self.cache) > self.max_size:
            oldest_key, _ = self.cache.popitem(last=False)
            self.timestamps.pop(oldest_key, None)

    def delete(self, key: str):
        if key in self.cache:
            self.cache.pop(key)
            self.timestamps.pop(key, None)

    def clear(self):
        self.cache.clear()
        self.timestamps.clear()

    def get_stats(self) -> Dict:
        return {
            "size": len(self.cache),
            "max_size": self.max_size,
            "ttl": self.ttl,
            "keys": list(self.cache.keys())[:5]
        }


projects_cache = Cache(max_size=1000, ttl=36000)


def safe_get_date_str(date_value):
    if date_value is None:
        return None
    if isinstance(date_value, str) and len(date_value) >= 10:
        return date_value[:10]
    if isinstance(date_value, datetime):
        return date_value.strftime('%Y-%m-%d')
    return None


def safe_format_date(date_str):
    if date_str and isinstance(date_str, str) and len(date_str) >= 10:
        try:
            date_obj = datetime.strptime(date_str[:10], '%Y-%m-%d')
            return date_obj.strftime('%d.%m.%Y')
        except (ValueError, TypeError):
            return 'Неверный формат'
    return 'Не указана'


def get_stage_emoji(stage):
    emoji_map = {
        'Text': '📝', 'Discussion': '💬', 'Evaluation': '📊',
        'Expertise': '🔍', 'Approval': '✅', 'Signing': '✍️',
        'Registration': '📋', 'Publication': '📢', 'Cancelled': '❌',
        'Completed': '✔️'
    }
    return emoji_map.get(stage, '📌')


def get_status_emoji(status):
    emoji_map = {
        'Developing': '🔄', 'Discussion': '💬', 'Evaluation': '📊',
        'Conclusion': '📝', 'Approval': '✅', 'Signing': '✍️',
        'Registered': '📋', 'Published': '📢', 'Cancelled': '❌'
    }
    return emoji_map.get(status, '⚡')


def format_project_stage(project):
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
        stage_text.append(f"\n⚡ **Статус:** {status_desc}")

    if procedure and procedure.get('id'):
        proc_desc = PROCEDURE_TYPES.get(procedure.get('id'), procedure.get('description', 'Неизвестная процедура'))
        stage_text.append(f"\n🔄 **Процедура:** {proc_desc}")

    dates = []
    if project.get('startPublicDiscussion') and project.get('endPublicDiscussion'):
        start = safe_get_date_str(project['startPublicDiscussion'])
        end = safe_get_date_str(project['endPublicDiscussion'])
        if start and end:
            dates.append(f"🗓 **Публичное обсуждение:** {start} - {end}")

    if project.get('startParallelPublicDiscussion') and project.get('endParallelPublicDiscussion'):
        start = safe_get_date_str(project['startParallelPublicDiscussion'])
        end = safe_get_date_str(project['endParallelPublicDiscussion'])
        if start and end:
            dates.append(f"🔄 **Параллельное обсуждение:** {start} - {end}")

    if project.get('deadline'):
        deadline = safe_get_date_str(project['deadline'])
        if deadline:
            dates.append(f"⏰ **Крайний срок:** {deadline}")

    if dates:
        stage_text.append("\n".join(dates))

    return "\n".join(stage_text)


def format_project_analyst(project):
    title = project.get("title", "Без названия")
    department = project.get("developedDepartment", {}).get("description", "Не указано")
    project_type = project.get("projectType", {}).get("description", "")
    procedure = project.get("procedure", {}).get("description", "")
    stage = project.get("stage", "")
    status = project.get("status", "")
    pub_date = project.get("publicationDate") or project.get("creationDate")
    project_id = project.get("id")
    topics = project.get("classified_topics", [])

    # Формируем строку с темами
    topics_block = ""
    if topics:
        # Берем первую тему для заголовка (или объединяем все)
        topic_labels = [TOPICS_SHORT.get(t, t) for t in topics[:3]]  # Ограничиваем 3 темами
        topic_str = " | ".join(topic_labels)
        if len(topics) > 3:
            topic_str += f" +{len(topics) - 3}"
        topics_block = f"**{topic_str}**\n\n"

    if pub_date:
        pub_date = pub_date[:10]

    url = f"https://regulation.gov.ru/projects#npa={project_id}"

    text = (
        f"{topics_block}"
        f"🏢 *{department}*\n\n"
        f"📂 {project_type}\n"
        f"⚖ {procedure}\n\n"
        f"📍 *Стадия:* {stage}\n"
        f"🔄 *Статус:* {status}\n"
        f"📅 {pub_date}\n\n"
        f"📌 *{title}*\n\n"
        f"🔗 {url}\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
    )

    return text


def format_project_lawyer(project):
    title = project.get("title", "Без названия")
    project_number = project.get("projectId", "Не указан")
    department = project.get("developedDepartment", {}).get("description", "Не указано")
    project_type = project.get("projectType", {}).get("description", "Не указано")
    procedure = project.get("procedure", {}).get("description", "Не указано")
    stage = project.get("stage", "Не указано")
    status = project.get("status", "Не указано")
    pub_date = project.get("publicationDate") or project.get("creationDate")
    project_id = project.get("id")
    topics = project.get("classified_topics", [])

    # Формируем строку с темами
    if topics:
        topic_labels = [TOPICS.get(t, t) for t in topics]  # Используем полные названия
        topic_str = ", ".join(topic_labels)
    else:
        topic_str = "Не определено"

    if pub_date:
        pub_date = pub_date[:10]

    url = f"https://regulation.gov.ru/projects#npa={project_id}"

    text = (
        "📄 *НОРМАТИВНЫЙ ПРОЕКТ*\n\n"
        f"📌 *Наименование:*\n{title}\n\n"
        f"🆔 *Номер проекта:*\n{project_number}\n\n"
        f"🏢 *Разработчик:*\n{department}\n\n"
        f"🧭 *Тематика:*\n{topic_str}\n\n"
        f"📂 *Тип акта:*\n{project_type}\n\n"
        f"⚖ *Процедура:*\n{procedure}\n\n"
        f"📍 *Стадия:*\n{stage}\n\n"
        f"🔄 *Статус:*\n{status}\n\n"
        f"📅 *Дата публикации:*\n{pub_date}\n\n"
        f"🔗 {url}\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
    )

    return text


def format_project_product(project):
    title = project.get("title", "Без названия")
    department = project.get("developedDepartment", {}).get("description", "Не указано")
    status = project.get("status", "")
    pub_date = project.get("publicationDate") or project.get("creationDate")
    project_type = project.get("projectType", {}).get("description", "")
    procedure = project.get("procedure", {}).get("description", "")
    project_id = project.get("id")
    topics = project.get("classified_topics", [])

    # Формируем строку с темами
    if topics:
        topic_labels = [TOPICS_SHORT.get(t, t) for t in topics[:2]]  # Ограничиваем 2 темами
        topic_str = " | ".join(topic_labels)
        if len(topics) > 2:
            topic_str += f" +{len(topics) - 2}"
    else:
        topic_str = "НПА"

    if pub_date:
        pub_date = pub_date[:10]

    short_title = title
    if len(title) > 120:
        short_title = title[:117] + "..."

    url = f"https://regulation.gov.ru/projects#npa={project_id}"

    text = (
        f"🧭 **{topic_str}**\n\n"
        f"🏢 *{department}* | {status} | {pub_date}\n\n"
        f"📌 *{short_title}*\n\n"
        f"📂 {project_type}\n"
        f"⚖ {procedure}\n\n"
        f"🔗 {url}\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
    )

    return text


def format_project_by_role(project: Dict, role: str) -> str:
    if role == 'analyst':
        return format_project_analyst(project)
    elif role == 'lawyer':
        return format_project_lawyer(project)
    elif role == 'product':
        return format_project_product(project)
    return format_project_analyst(project)


def format_weekly_digest(projects: List[Dict], start_date: datetime, end_date: datetime) -> str:
    start_str = start_date.strftime('%d.%m')
    end_str = end_date.strftime('%d.%m')

    text = f"📊 **ЕЖЕНЕДЕЛЬНЫЙ ДАЙДЖЕСТ НПА ({start_str}–{end_str})**\n\n"
    text += f"📈 Всего новых проектов: {len(projects)}\n"

    by_topic = {}
    for p in projects:
        topics = p.get('classified_topics', [])
        if isinstance(topics, set):
            topics = list(topics)
        for topic in topics:
            if topic not in by_topic:
                by_topic[topic] = []
            by_topic[topic].append(p)

    text += f"   • По нашим темам: {sum(len(v) for v in by_topic.values())}\n\n"

    for topic, projs in by_topic.items():
        topic_name = TOPICS_SHORT.get(topic, topic)
        text += f"### {topic_name}\n"
        for p in projs:
            text += format_project_product(p)
        text += "\n"

    deadlines = []
    today = datetime.now().strftime('%Y-%m-%d')

    for p in projects:
        end = safe_get_date_str(p.get('endPublicDiscussion'))
        if end and end >= today:
            topics_list = p.get('classified_topics', ['НПА'])
            if isinstance(topics_list, set):
                topics_list = list(topics_list)
            topic = TOPICS_SHORT.get(topics_list[0], topics_list[0]) if topics_list else 'НПА'
            title = p.get('title', '')[:50]
            deadlines.append((end, topic, title))

    if deadlines:
        deadlines.sort()
        text += "⏳ **Ближайшие дедлайны:**\n\n"
        for end, topic, title in deadlines[:5]:
            try:
                date_obj = datetime.strptime(end, '%Y-%m-%d')
                date_str = date_obj.strftime('%d.%m')
                text += f"   • {date_str} — окончание обсуждения по {topic} ({title}...)\n"
            except (ValueError, TypeError):
                continue
        text += "\n"

    text += "📌 **Рекомендации по roadmap:**\n\n"
    return text


def format_digest_by_role(projects: List[Dict], role: str, start_date: datetime, end_date: datetime) -> str:
    if role == 'product':
        return format_weekly_digest(projects, start_date, end_date)
    text = f"📅 **Дайджест за {start_date.strftime('%d.%m.%Y')} - {end_date.strftime('%d.%m.%Y')}**\n\n"
    for i, p in enumerate(projects, 1):
        text += f"{i}. {format_project_by_role(p, role)}\n"
        text += "━" * 24 + "\n"
    return text

def format_projects_notification(projects, subs, start_date, end_date):
    from datetime import datetime

    # --- Формируем диапазон дат ---
    if start_date == end_date:
        date_str = start_date.strftime("%d.%m.%Y")
        header = f"📅 *Проекты за {date_str}*\n\n"
    else:
        header = (
            f"📅 *Дайджест за "
            f"{start_date.strftime('%d.%m')}–{end_date.strftime('%d.%m.%Y')}*\n\n"
        )

    header += f"📊 Найдено проектов: *{len(projects)}*\n"
    header += "━━━━━━━━━━━━━━━━━━━━\n\n"

    text = header

    for i, p in enumerate(projects, 1):
        title = p.get("title", "Без названия")
        dept = p.get("developedDepartment", {}).get("description", "Не указано")
        date = p.get("publicationDate") or p.get("creationDate", "")
        project_id = p.get("id")
        status_emoji = get_status_emoji(p.get("status", ""))

        topics = p.get("classified_topics", [])
        topic_str = " ".join([TOPICS_SHORT.get(t, t) for t in topics]) if topics else "НПА"

        url = f"https://regulation.gov.ru/projects#npa={project_id}"

        text += f"{i}. {status_emoji} {topic_str}\n\n"
        text += f"📌 *{title}*\n\n"
        text += f"🏢 {dept[:100]}\n\n"

        if date:
            text += f"📅 {date[:10]}\n\n"

        text += f"🔗 {url}\n\n"
        text += "━━━━━━━━━━━━━━━━━━━━\n\n"

    text += "\n🔔 *Ваши подписки:*\n"
    text += ", ".join([TOPICS_SHORT.get(s, s) for s in subs])

    return text

def format_no_projects_notification(subs, start_date, end_date):

    if start_date == end_date:
        date_str = start_date.strftime("%d.%m.%Y")
        header = f"📅 *За {date_str} новых проектов не найдено*\n\n"
    else:
        header = (
            f"📅 *За период "
            f"{start_date.strftime('%d.%m')}–{end_date.strftime('%d.%m.%Y')} "
            f"новых проектов не найдено*\n\n"
        )

    header += "🔔 *Ваши подписки:*\n"
    header += ", ".join([TOPICS_SHORT.get(s, s) for s in subs])

    header += "\n\nВы получите уведомление, как только появятся новые проекты."

    return header


async def safe_send_message(update_or_context, text: str, parse_mode: str = 'Markdown', reply_markup=None,
                            chunk_size: int = 4096):
    if hasattr(update_or_context, 'message'):
        send_func = update_or_context.message.reply_text
    elif hasattr(update_or_context, 'bot') and hasattr(update_or_context, 'effective_chat'):
        send_func = lambda t, **kwargs: update_or_context.bot.send_message(
            chat_id=update_or_context.effective_chat.id, text=t, **kwargs
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


async def split_long_message_for_query(query, text: str, parse_mode: str = 'Markdown', reply_markup=None,
                                       chunk_size: int = 4096):
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


async def fetch_with_retry_simple(fetch_func, max_retries=3, delay=2, *args, **kwargs):
    func_with_args = partial(fetch_func, *args, **kwargs)
    for attempt in range(1, max_retries + 1):
        try:
            logger.info(f"Попытка {attempt} из {max_retries}")
            result = await asyncio.get_event_loop().run_in_executor(None, func_with_args)
            if result:
                logger.info(f"Успешно на попытке {attempt}")
                return result
            else:
                logger.warning(f"Попытка {attempt} вернула пустой результат")
        except Exception as e:
            logger.error(f"Ошибка на попытке {attempt}: {e}")
        if attempt < max_retries:
            wait_time = delay * attempt
            logger.info(f"Ждем {wait_time} секунд...")
            await asyncio.sleep(wait_time)
    logger.error(f"Все {max_retries} попыток провалились")
    return None


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    is_new = not db.user_exists(user.id)
    db.add_user(
        telegram_id=user.id,
        first_name=user.first_name,
        last_name=user.last_name,
        username=user.username
    )
    logger.info(f"Новый пользователь: {user.first_name} (ID: {user.id})")

    current_role = db.get_user_role(user.id)
    role_name = USER_ROLES.get(current_role, {}).get('name', 'Аналитик')

    if is_new:
        welcome_text = (
            f"👋 Привет, {user.first_name}! 🎉\n\n"
            f"✅ Вам автоматически назначена роль: **{role_name}**\n"
            f"Вы всегда можете сменить её в настройках.\n\n"
            f"📋 **Выберите пункт меню:**"
        )
    else:
        welcome_text = (
            f"👋 С возвращением, {user.first_name}! 🎉\n"
            f"Ваша текущая роль: **{role_name}**\n\n"
            f"📋 **Выберите пункт меню:**"
        )

    await update.message.reply_text(welcome_text, parse_mode='Markdown', reply_markup=get_main_menu_keyboard())


async def test_notifications(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Проверяю проекты за вчера...")
    yesterday = datetime.now() - timedelta(days=1)
    projects = await fetch_with_retry_simple(api.fetch_all_projects, max_retries=2, delay=2, max_pages=50)
    if not projects:
        await update.message.reply_text("❌ Не удалось загрузить проекты")
        return

    yesterday_projects = []
    for p in projects:
        date_str = p.get('publicationDate') or p.get('creationDate', '')
        if date_str:
            try:
                project_date = datetime.strptime(date_str[:10], '%Y-%m-%d').date()
                if project_date == yesterday.date():
                    topics = ProjectClassifier.classify_as_list(title=p.get('title'))
                    if topics:
                        p['classified_topics'] = topics
                        yesterday_projects.append(p)
            except:
                continue

    user_subs = db.get_subscriptions(update.effective_user.id)
    if not user_subs:
        await update.message.reply_text(
            "❌ У вас нет подписок. Сначала подпишитесь на темы!",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔍 Перейти к подписке", callback_data="menu_search")
            ]])
        )
        return

    user_projects = []
    for p in yesterday_projects:
        if set(p.get('classified_topics', [])).intersection(set(user_subs)):
            user_projects.append(p)

    if user_projects:
        message = format_projects_notification(user_projects, user_subs, yesterday)
    else:
        message = format_no_projects_notification(user_subs, yesterday)

    await update.message.reply_text(message, parse_mode='Markdown')

async def send_daily_notifications(application: Application):
    logger.info("🕐 Запуск ежедневной рассылки уведомлений")

    users = db.get_all_users()
    if not users:
        logger.info("Нет пользователей для уведомлений")
        return

    now = datetime.now()
    weekday = now.weekday()  # 0=пн, 6=вск

    if weekday == 5:
        logger.info("Сегодня суббота — уведомления не отправляем")
        return

    elif weekday == 6:
        logger.info("Сегодня воскресенье — уведомления не отправляем")
        return

    elif weekday == 0:
        dates_to_check = [
            (now - timedelta(days=3)).date(),
            (now - timedelta(days=2)).date(),
            (now - timedelta(days=1)).date()
        ]
    else:
        dates_to_check = [(now - timedelta(days=1)).date()]

    date_range_key = "_".join([d.strftime("%Y%m%d") for d in dates_to_check])
    cache_key = f"daily_projects_{date_range_key}"

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

    projects_for_notification = []

    for p in projects:
        date_str = p.get('publicationDate') or p.get('creationDate')
        if not date_str:
            continue

        try:
            project_date = datetime.strptime(date_str[:10], '%Y-%m-%d').date()
        except (ValueError, TypeError):
            continue

        if project_date in dates_to_check:
            topics = ProjectClassifier.classify_as_list(title=p.get('title', ''))
            if topics:
                p['classified_topics'] = topics
                projects_for_notification.append(p)

    if not projects_for_notification:
        logger.info("Нет проектов для уведомлений")
        return

    sent_count = 0
    current_time_str = now.strftime("%H:%M")

    for user in users:
        user_id = user['telegram_id']
        user_time = db.get_notification_time(user_id)

        if user_time != current_time_str:
            continue

        # уникальный ключ отправки
        today_key = f"sent_{user_id}_{date_range_key}"
        if projects_cache.get(today_key):
            continue

        user_subs = db.get_subscriptions(user_id)
        if not user_subs:
            continue

        user_projects = []
        for p in projects_for_notification:
            if set(p['classified_topics']).intersection(set(user_subs)):
                user_projects.append(p)

        # формируем диапазон дат для заголовка
        start_date = min(dates_to_check)
        end_date = max(dates_to_check)

        if user_projects:
            message = format_projects_notification(
                user_projects,
                user_subs,
                start_date,
                end_date
            )
        else:
            message = format_no_projects_notification(
                user_subs,
                start_date,
                end_date
            )

        try:
            await application.bot.send_message(
                chat_id=user_id,
                text=message,
                parse_mode='Markdown'
            )
            projects_cache.set(today_key, True)
            sent_count += 1
            logger.info(f"Уведомление отправлено пользователю {user_id}")
            await asyncio.sleep(0.5)

        except Exception as e:
            logger.error(f"Ошибка отправки уведомления пользователю {user_id}: {e}")

    logger.info(f"Рассылка завершена. Отправлено: {sent_count}")

async def show_current_projects(query, context):
    await query.edit_message_text("🔍 Загружаю текущие проекты по вашим подпискам...")

    user_id = query.from_user.id
    user_role = db.get_user_role(user_id)
    user_subs = db.get_subscriptions(user_id)
    logger.info(f"Loaded subscriptions for user {user_id} from database")

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
        projects = await fetch_with_retry_simple(api.fetch_all_projects, max_retries=3, delay=2, max_pages=500)
        if projects:
            projects_cache.set(cache_key_projects, projects)
            logger.info(f"Cached {len(projects)} projects")

    if not projects:
        await query.edit_message_text(
            "❌ Не удалось загрузить проекты.\nПопробуйте позже.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_main")
            ]])
        )
        return

    active_statuses = {
        'Developing': '🔄 Разработка',
        'Discussion': '💬 Публичное обсуждение',
        'Evaluation': '📊 Оценка регулирующего воздействия',
        'Conclusion': '📝 Подготовка заключения',
        'Approval': '✅ Согласование',
        'Undefined': '🔄 Разработка',
        'Signing': '✍️ Подписание',
        'StartDiscussion': '🆕 Начало обсуждения',
        'OnApprove': '⏳ На согласовании',
        'Draft': '📝 Черновик',
        'Text': '📝 Текст проекта',
        'PreDiscussion': '💬 Предварительное обсуждение'
    }

    completed_statuses = {
        'Registered': '📋 Зарегистрирован',
        'Published': '📢 Опубликован',
        'Cancelled': '❌ Отменен',
        'EndDiscussion': '✅ Обсуждение завершено',
        'Rejected': '❌ Отклонен',
        'Complete': '✅ Завершён',
        'Completed': '✔️ Завершен'
    }

    matching_projects = []
    today = datetime.now().date()

    for p in projects:
        topics = ProjectClassifier.classify_as_list(title=p.get('title', ''))
        project_topics = set(topics)
        user_topics_set = set(user_subs)

        if not project_topics.intersection(user_topics_set):
            continue

        status = p.get('status', '')


        is_active = False

        if status in active_statuses:
            is_active = True
        elif not status:
            is_active = True
        elif status not in completed_statuses:
            is_active = True

        if is_active:
            end_date_str = p.get('endPublicDiscussion')
            if end_date_str:
                try:
                    end_date = datetime.strptime(end_date_str[:10], '%Y-%m-%d').date()
                    if end_date < today - timedelta(days=30):
                        is_active = False
                except (ValueError, TypeError):
                    pass

        if is_active:
            p['classified_topics'] = topics
            matching_projects.append(p)

    if not matching_projects:
        await query.edit_message_text(
            "❌ Нет текущих проектов по вашим подпискам.\n\n"
            "Попробуйте посмотреть архив или изменить подписки.",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🗂 Перейти в архив", callback_data="menu_archive")],
                [InlineKeyboardButton("🔍 Изменить подписки", callback_data="menu_search")],
                [InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_main")]
            ])
        )
        return

    matching_projects.sort(
        key=lambda x: x.get('publicationDate') or x.get('creationDate', '') or '',
        reverse=True
    )

    if user_role == 'product' and len(matching_projects) > 5:
        text = format_weekly_digest(matching_projects,
                                    datetime.now() - timedelta(days=7),
                                    datetime.now())
    else:
        text = f"📋 **Текущие проекты (активные)**\n\n"
        text += f"📊 По вашим подпискам: **{len(matching_projects)}** проектов в работе\n\n"
        text += "━━━━━━━━━━━━━━━━━━━━\n\n"

        for i, p in enumerate(matching_projects, 1):
            status = p.get('status', '')
            status_emoji = get_status_emoji(status)

            project_text = format_project_by_role(p, user_role)
            text += f"**{i}.** {status_emoji} {project_text}\n"

    await split_long_message_for_query(
        query,
        text,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_main")
        ]])
    )

async def show_search_menu(query, context):
    user_id = query.from_user.id

    if 'selected_topics' not in context.user_data:
        current_subs = set(db.get_subscriptions(user_id))
        context.user_data['selected_topics'] = current_subs

    selected = context.user_data.get('selected_topics', set())

    keyboard = []
    row = []

    for i, (topic_code, topic_name) in enumerate(TOPICS.items(), 1):

        if topic_code in selected:
            button_text = f"✅ {topic_name}"
        else:
            button_text = topic_name

        row.append(
            InlineKeyboardButton(
                button_text,
                callback_data=f"toggle_{topic_code}"
            )
        )

        if i % 2 == 0:
            keyboard.append(row)
            row = []

    if row:
        keyboard.append(row)

    keyboard.append([
        InlineKeyboardButton("💾 Сохранить", callback_data="save_subscriptions")
    ])
    keyboard.append([
        InlineKeyboardButton("◀️ Назад", callback_data="back_to_main")
    ])

    await query.edit_message_text(
        "📋 Выберите темы (можно несколько):",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def show_my_subscriptions(query, user_id):
    subscriptions = db.get_subscriptions(user_id)

    if not subscriptions:
        await query.edit_message_text(
            "❌ У вас нет активных подписок.\n\n"
            "Вы можете выбрать интересующие темы в разделе управления.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⚙️ Управлять подписками", callback_data="menu_search")],
                [InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_main")]
            ])
        )
        return

    text = "📌 **Ваши подписки:**\n\n"

    for topic in subscriptions:
        full_name = TOPICS.get(topic, topic)
        text += f"• {full_name}\n\n"

    keyboard = [
        [InlineKeyboardButton("⚙️ Управлять подписками", callback_data="menu_search")],
        [InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_main")]
    ]

    await query.edit_message_text(
        text,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
async def show_archive_topics(query):
    keyboard = []
    row = []
    for i, (topic_code, topic_name) in enumerate(TOPICS.items(), 1):
        button = InlineKeyboardButton(topic_name, callback_data=f"archive_{topic_code}")
        row.append(button)
        if i % 2 == 0:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_main")])

    await query.edit_message_text(
        "🗂 **Архив проектов\n\nВыберите тему для просмотра:",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def show_archive_projects(query, context, topic):
    await query.answer()
    await query.edit_message_text(f"🔍 Загружаю архив проектов по теме {TOPICS_SHORT.get(topic, topic)}...")

    all_projects_cache_key = f"all_projects_{datetime.now().strftime('%Y%m%d')}"
    all_projects = projects_cache.get(all_projects_cache_key)

    if all_projects is None:
        all_projects = await fetch_with_retry_simple(api.fetch_all_projects, max_retries=3, delay=2, max_pages=2500)
        if all_projects:
            projects_cache.set(all_projects_cache_key, all_projects)
            logger.info(f"Cached {len(all_projects)} projects for all topics")

    if not all_projects:
        await query.edit_message_text(
            "❌ Не удалось загрузить проекты после 3 попыток.\nПопробуйте позже.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Назад к темам", callback_data="menu_archive")
            ]])
        )
        return


    filtered_projects = []
    for p in all_projects:
        p_topics = ProjectClassifier.classify_as_list(title=p.get('title', ''))
        if topic in p_topics:
            p['classified_topics'] = p_topics
            filtered_projects.append(p)

    filtered_projects.sort(
        key=lambda x: x.get('publicationDate') or x.get('creationDate', '') or '0000-00-00',
        reverse=True
    )

    if not filtered_projects:
        await query.edit_message_text(
            f"❌ Нет проектов по теме {TOPICS_SHORT.get(topic, topic)}",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Назад к темам", callback_data="menu_archive")
            ]])
        )
        return

    text = f"🗂 **Архив {TOPICS_SHORT.get(topic, topic)} (все проекты)**\n\n"
    text += f"📊 Найдено проектов: {len(filtered_projects)}\n\n"
    text += "━━━━━━━━━━━━━━━━━━━━\n\n"

    count = 0
    for p in filtered_projects:
        count += 1
        title = p.get('title', 'Без названия')
        dept = p.get('developedDepartment', {}).get('description', 'Не указано')
        date = p.get('publicationDate') or p.get('creationDate', '')
        date_str = date[:10] if date else 'Дата не указана'
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

        text += f"   📅 {date_str}\n\n"
        text += f"   🔗 {url}\n\n"
        text += "━━━━━━━━━━━━━━━━━━━━\n\n"


    keyboard = [
        [InlineKeyboardButton("◀️ Назад к темам", callback_data="menu_archive")],
        [InlineKeyboardButton("◀️ В главное меню", callback_data="back_to_main")]
    ]

    await split_long_message_for_query(query, text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

async def show_settings_menu(query):
    user_id = query.from_user.id
    current_role = db.get_user_role(user_id)
    role_name = USER_ROLES.get(current_role, {}).get('name', 'Не выбрана')



    keyboard = [
        [InlineKeyboardButton(f"👤 Сменить роль (сейчас: {role_name})", callback_data="change_role")],
        [InlineKeyboardButton("⏰ Время уведомлений", callback_data="settings_time")],
        [InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_main")]
    ]

    await query.edit_message_text(
        f"⚙️ **Настройки**\n\nТекущая роль: {role_name}\nВыберите что хотите изменить:",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def show_role_selection(query):
    user_id = query.from_user.id
    current_role = db.get_user_role(user_id)
    keyboard = []

    for role_id, role_info in USER_ROLES.items():
        button_text = f"{role_info['name']} - {role_info['description']}"
        if role_id == current_role:
            button_text = f"✅ {button_text} (текущая)"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=f"select_role_{role_id}")])

    keyboard.append([InlineKeyboardButton("◀️ Назад в настройки", callback_data="menu_settings")])

    text = "👤 **Смена роли**\n\nВыберите новую роль — от этого будет зависеть формат отображения проектов:\n\n"
    for role_id, role_info in USER_ROLES.items():
        text += f"**{role_info['name']}**\n└ {role_info['description']}\n\n"

    await query.edit_message_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))


async def handle_role_selection(query, role_id):
    user_id = query.from_user.id
    current_role = db.get_user_role(user_id)

    if role_id == current_role:
        await query.answer("Это ваша текущая роль")
        return

    success = db.set_user_role(user_id, role_id)
    if success:
        role_name = USER_ROLES.get(role_id, {}).get('name', role_id)
        text = f"✅ Роль успешно изменена на **{role_name}**!\n\nТеперь проекты будут отображаться в новом формате."
        keyboard = [
            [InlineKeyboardButton("⚙️ Вернуться в настройки", callback_data="menu_settings")],
            [InlineKeyboardButton("📋 В главное меню", callback_data="back_to_main")]
        ]
        await query.edit_message_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))
        logger.info(f"Пользователь {user_id} сменил роль на: {role_name}")
    else:
        await query.edit_message_text(
            "❌ Ошибка при смене роли. Попробуйте позже.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Назад", callback_data="menu_settings")
            ]])
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




async def show_time_selection(query):
    current_time = db.get_notification_time(query.from_user.id)

    keyboard = []
    times = ["06:00", "07:00", "08:00", "09:00", "10:00",
             "12:00", "15:00", "18:00"]

    for t in times:
        text = f"✅ {t}" if t == current_time else t
        keyboard.append([InlineKeyboardButton(text, callback_data=f"set_time_{t}")])

    keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="menu_settings")])

    await query.edit_message_text(
        f"⏰ **Выберите время уведомлений**\n\nТекущее: {current_time} (UTC)",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def show_last_filter_menu(query):
    keyboard = [
        [InlineKeyboardButton("📅 Сегодня", callback_data="last_period_today")],
        [InlineKeyboardButton("📆 Вчера", callback_data="last_period_yesterday")],
        [InlineKeyboardButton("📆 За 3 дня", callback_data="last_period_3")],
        [InlineKeyboardButton("📆 За 7 дней", callback_data="last_period_7")],
        [InlineKeyboardButton("◀️ Назад", callback_data="back_to_main")]
    ]

    await query.edit_message_text(
        "📅 **Выберите период:**",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def show_last_scope_menu(query):
    keyboard = [
        [InlineKeyboardButton("🔥 Только мои подписки", callback_data="last_scope_mine")],
        [InlineKeyboardButton("🌍 Все проекты", callback_data="last_scope_all")],
        [InlineKeyboardButton("◀️ Назад", callback_data="menu_last")]
    ]

    await query.edit_message_text(
        "🔎 **Показать проекты:**",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
async def show_last_projects(query, context, period="7", scope="all"):
    await query.edit_message_text("🔍 Загружаю проекты...")

    today = datetime.now().date()

    if period == "today":
        start_date = today
        period_label = "сегодня"
    elif period == "yesterday":
        start_date = today - timedelta(days=1)
        period_label = "вчера"
    elif period == "3":
        start_date = today - timedelta(days=3)
        period_label = "за 3 дня"
    elif period == "7":
        start_date = today - timedelta(days=7)
        period_label = "за 7 дней"
    else:
        start_date = today - timedelta(days=7)
        period_label = "за 7 дней"

    cache_key = f"last_projects_{datetime.now().strftime('%Y%m%d_%H')}"
    projects = projects_cache.get(cache_key)

    if projects is None:
        projects = await fetch_with_retry_simple(
            api.fetch_all_projects,
            max_retries=3,
            delay=2,
            max_pages=50
        )
        if projects:
            projects_cache.set(cache_key, projects)

    if not projects:
        await query.edit_message_text(
            "❌ Не удалось загрузить проекты",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_main")
            ]])
        )
        return

    user_subs = db.get_subscriptions(query.from_user.id) if scope == "mine" else []
    matching_projects = []

    for p in projects:
        date_str = p.get("publicationDate") or p.get("creationDate")
        if not date_str:
            continue

        try:
            project_date = datetime.strptime(date_str[:10], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue

        if project_date < start_date:
            continue

        topics = ProjectClassifier.classify_as_list(title=p.get("title", ""))
        if scope == "mine":
            if not topics:
                continue
            if not set(topics).intersection(set(user_subs)):
                continue

        p["classified_topics"] = topics
        matching_projects.append(p)

    matching_projects.sort(
        key=lambda x: x.get("publicationDate") or x.get("creationDate") or "",
        reverse=True
    )

    if not matching_projects:
        await query.edit_message_text(
            f"❌ Нет проектов {period_label}",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Назад", callback_data="menu_last")
            ]])
        )
        return

    scope_label = "только мои подписки" if scope == "mine" else "все проекты"

    text = (
        f"📅 **Проекты {period_label}**\n\n"
        f"🔎 Фильтр: {scope_label}\n\n"
        f"📊 Найдено: **{len(matching_projects)}**\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
    )

    for i, p in enumerate(matching_projects, 1):
        title = p.get("title", "Без названия")
        dept = p.get("developedDepartment", {}).get("description", "Не указано")
        date = p.get("publicationDate") or p.get("creationDate", "")
        project_id = p.get("id")
        status_emoji = get_status_emoji(p.get("status", ""))

        topics = p.get("classified_topics", [])
        topic_str = " ".join([TOPICS_SHORT.get(t, t) for t in topics]) if topics else "НПА"

        url = f"https://regulation.gov.ru/projects#npa={project_id}"

        text += f"{i}. {status_emoji} {topic_str}\n\n"
        text += f"   📌 {title}...\n\n"
        text += f"   🏢 {dept[:100]}\n\n"
        text += f"   📅 {date[:10] if date else 'Нет даты'}\n\n"
        text += f"   🔗 {url}\n\n"
        text += "━━━━━━━━━━━━━━━━━━━━\n\n"

    await split_long_message_for_query(
        query,
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_main")
        ]])
    )
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    logger.info(f"Пользователь {user_id} нажал кнопку: {data}")

    if data == "menu_current":
        await show_current_projects(query, context)
    elif data.startswith('select_role_'):
        role_id = data.replace('select_role_', '')
        await handle_role_selection(query, role_id)
    elif data == "change_role":
        await show_role_selection(query)
    elif data == "menu_search":
        await show_search_menu(query, context)
    elif data == "menu_subs":
        await show_my_subscriptions(query, user_id)
    elif data == "menu_archive":
        await show_archive_topics(query)
    elif data == "menu_settings":
        await show_settings_menu(query)
    elif data == "menu_help":
        await show_help(query)
    elif data == "settings_time":
        await show_time_selection(query)
    elif data.startswith("set_time_"):
        time_str = data.replace("set_time_", "")
        success = db.set_notification_time(user_id, time_str)

        if success:
            await query.edit_message_text(
                f"✅ Время уведомлений установлено на {time_str}",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("◀️ Назад в настройки", callback_data="menu_settings")]
                ])
            )
        else:
            await query.answer("Ошибка сохранения")
    elif data == "menu_last":
        await show_last_filter_menu(query)
    elif data.startswith("last_period_"):
        period = data.replace("last_period_", "")
        context.user_data["last_period"] = period
        await show_last_scope_menu(query)
    elif data.startswith("last_scope_"):
        scope = data.replace("last_scope_", "")
        period = context.user_data.get("last_period", "7")
        await show_last_projects(query, context, period, scope)
    elif data == "back_to_main":
        await query.edit_message_text(
            "📋 **Выберите пункт меню:**",
            parse_mode='Markdown',
            reply_markup=get_main_menu_keyboard()
        )

    elif data.startswith('archive_'):
        topic = data.replace('archive_', '')
        await show_archive_projects(query, context, topic)

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
    elif data.startswith('toggle_'):
        topic = data.replace('toggle_', '')

        selected = context.user_data.get('selected_topics', set())

        if topic in selected:
            selected.remove(topic)
        else:
            selected.add(topic)

        context.user_data['selected_topics'] = selected

        await show_search_menu(query, context)
    elif data == "save_subscriptions":
        selected = context.user_data.get('selected_topics', set())
        user_id = query.from_user.id

        if not selected:
            await query.answer("Ничего не выбрано")
            return

        db.clear_subscriptions(user_id)

        for topic in selected:
            db.subscribe(user_id, topic)

        context.user_data.pop('selected_topics', None)
        await query.edit_message_text(
            "✅ Подписки обновлены!",
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
        trigger=CronTrigger(minute="*"),
        args=[application],
        id='daily_notifications',
        replace_existing=True
    )

    scheduler.start()
    logger.info("⏰ Планировщик уведомлений запущен (проверка каждую минуту)")

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("test_notify", test_notifications))
    application.add_handler(CallbackQueryHandler(button_handler))

    logger.info("🚀 Бот запущен с поддержкой кеша и отображением этапов проектов!")
    logger.info(f"📊 Настройки кеша:")
    logger.info(f"   • Проекты: макс={projects_cache.max_size}, TTL={projects_cache.ttl}с")



    try:
        application.run_polling(allowed_updates=Update.ALL_TYPES)
    finally:
        logger.info("🛑 Бот останавливается...")
        scheduler.shutdown()
        logger.info("👋 Планировщик остановлен")


if __name__ == "__main__":
    main()