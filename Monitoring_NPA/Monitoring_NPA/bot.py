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
    'Completed': '✔️ Завершен',
    'Notification': '📢 Уведомление о подготовке',
    'Complete': '✅ Завершен',
    'Procedure': '🔄 Процедура'
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
    'Complete': '✅ Завершён',
    'Notification': '📢 Уведомление',
    'Procedure': '🔄 Процедура'
}

PROCEDURE_TYPES = {
    '1': '📢 Раскрытие информации о подготовке проектов',
    '2': '💬 Публичное обсуждение',
    '3': '📊 Оценка регулирующего воздействия',
    '4': '🔍 Экспертиза',
    '5': '✅ Согласование',
    'Notification': '📢 Уведомление о подготовке'
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

    def get(self, key):
        if key in self.cache:
            if time.time() - self.timestamps[key] < self.ttl:
                self.cache.move_to_end(key)
                return self.cache[key]
            else:
                self.delete(key)
        return None

    def set(self, key, value):
        if key in self.cache:
            self.cache.move_to_end(key)
        self.cache[key] = value
        self.timestamps[key] = time.time()
        while len(self.cache) > self.max_size:
            oldest_key, _ = self.cache.popitem(last=False)
            self.timestamps.pop(oldest_key, None)

    def delete(self, key):
        if key in self.cache:
            self.cache.pop(key)
            self.timestamps.pop(key, None)

    def clear(self):
        self.cache.clear()
        self.timestamps.clear()

    def get_stats(self):
        return {
            "size": len(self.cache),
            "max_size": self.max_size,
            "ttl": self.ttl,
            "keys": list(self.cache.keys())[:5]
        }


projects_cache = Cache(max_size=10000, ttl=90000)
user_subs_cache = Cache(max_size=1000, ttl=36000)
last_modified_cache = Cache(max_size=20000, ttl=86400)

def get_user_subs_cached(user_id):
    cache_key = f"subs_{user_id}"
    subs = user_subs_cache.get(cache_key)
    if subs is not None:
        logger.info(f"📦 Подписки для {user_id} взяты из кеша")
        return subs

    subs = db.get_subscriptions(user_id)
    user_subs_cache.set(cache_key, subs)
    logger.info(f"💾 Подписки для {user_id} загружены в кеш")
    return subs


def invalidate_user_subs_cache(user_id):
    cache_key = f"subs_{user_id}"
    user_subs_cache.delete(cache_key)
    logger.info(f"♻️ Кеш подписок для {user_id} сброшен")

def safe_get_date_str(date_value):
    if date_value is None:
        return None
    if isinstance(date_value, str) and len(date_value) >= 10:
        return date_value[:10]
    if isinstance(date_value, datetime):
        return date_value.strftime('%Y-%m-%d')
    return None


async def get_project_last_modified(project_id: str) -> Optional[str]:
    """
    Получает дату последнего изменения проекта из его этапов.
    Возвращает дату в формате YYYY-MM-DD или None.
    """
    # Проверяем кеш
    cache_key = f"last_mod_{project_id}"
    cached_date = last_modified_cache.get(cache_key)
    if cached_date:
        logger.info(f"📦 Дата изменения для проекта {project_id} взята из кеша")
        return cached_date

    try:
        # Используем уже существующий api объект
        stages = await fetch_with_retry_simple(
            api.fetch_project_stages,
            max_retries=2,
            delay=1,
            project_id=project_id
        )

        if not stages:
            return None

        last_date = None
        for stage in stages:
            # Проверяем даты во всех возможных местах
            if stage.get('file') and stage['file'].get('date'):
                date_str = stage['file']['date'][:10]
                if not last_date or date_str > last_date:
                    last_date = date_str

            if stage.get('modifiedFile') and stage['modifiedFile'].get('date'):
                date_str = stage['modifiedFile']['date'][:10]
                if not last_date or date_str > last_date:
                    last_date = date_str

        if last_date:
            # Сохраняем в кеш
            last_modified_cache.set(cache_key, last_date)
            logger.info(f"💾 Дата изменения {last_date} для проекта {project_id} сохранена в кеш")

        return last_date
    except Exception as e:
        logger.error(f"Ошибка получения даты изменения для проекта {project_id}: {e}")
        return None
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

    return "\n".join(stage_text)


def format_project_analyst(project):
    title = project.get("title", "Без названия")
    department = project.get("developedDepartment", {}).get("description", "Не указано")
    project_type_id = project.get("projectType", {}).get("id", "")
    project_type = PROJECT_TYPES.get(project_type_id, project.get("projectType", {}).get("description", ""))
    procedure_id = project.get("procedure", {}).get("id", "")
    procedure = PROCEDURE_TYPES.get(procedure_id, project.get("procedure", {}).get("description", ""))
    stage = project.get("stage", "")
    stage_ru = STAGE_DESCRIPTIONS.get(stage, stage)
    status = project.get("status", "")
    status_ru = STATUS_DESCRIPTIONS.get(status, status)
    pub_date = project.get("publicationDate") or project.get("creationDate")
    project_id = project.get("id")
    topics = project.get("classified_topics", [])
    last_modified = project.get("last_modified")
    last_modified_str = f"\n\n📅 *Последнее изменение:* {last_modified}" if last_modified else ""
    if topics:
        topic_labels = [TOPICS_SHORT.get(t, t) for t in topics]
        topic_str = "| ".join(topic_labels)
    else:
        topic_str = "Не определено"

    if pub_date:
        pub_date = pub_date[:10]

    url = f"https://regulation.gov.ru/projects#npa={project_id}"

    text = (
        f"{topic_str}\n\n"
        f"🏢 *{department}*\n\n"
        f"📂 {project_type}\n"
        f"⚖ {procedure}\n\n"
        f"📍 *Стадия:* {stage_ru}\n"
        f"🔄 *Статус:* {status_ru}\n"
        f"📅 *Дата публикации:* {pub_date}{last_modified_str}\n\n"
        f"📌 *{title}*\n\n"
        f"🔗 {url}\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
    )

    return text


def format_project_lawyer(project):
    title = project.get("title", "Без названия")
    project_number = project.get("projectId", "Не указан")
    department = project.get("developedDepartment", {}).get("description", "Не указано")
    project_type_id = project.get("projectType", {}).get("id", "")
    project_type = PROJECT_TYPES.get(project_type_id, project.get("projectType", {}).get("description", "Не указано"))
    procedure_id = project.get("procedure", {}).get("id", "")
    procedure = PROCEDURE_TYPES.get(procedure_id, project.get("procedure", {}).get("description", "Не указано"))
    stage = project.get("stage", "Не указано")
    stage_ru = STAGE_DESCRIPTIONS.get(stage, stage)
    status = project.get("status", "Не указано")
    status_ru = STATUS_DESCRIPTIONS.get(status, status)
    pub_date = project.get("publicationDate") or project.get("creationDate")
    project_id = project.get("id")
    topics = project.get("classified_topics", [])

    # Получаем дату последнего изменения из этапов (если есть)
    last_modified = project.get("last_modified")
    last_modified_str = f"\n\n📅 *Последнее изменение:* {last_modified}" if last_modified else ""

    if topics:
        topic_labels = [TOPICS.get(t, t) for t in topics]
        topic_str = ", ".join(topic_labels)
    else:
        topic_str = "НПА"

    if pub_date:
        pub_date = pub_date[:10]

    url = f"https://regulation.gov.ru/projects#npa={project_id}"

    text = (
        "📄 *НОРМАТИВНЫЙ ПРОЕКТ*\n\n"
        f"📌 *Наименование:* {title}\n\n"
        f"🆔 *Номер проекта:* {project_number}\n\n"
        f"🏢 *Разработчик:* {department}\n\n"
        f"🧭 *Тематика:* {topic_str}\n\n"
        f"📂 *Тип акта:* {project_type}\n\n"
        f"⚖ *Процедура:* {procedure}\n\n"
        f"📍 *Стадия:* {stage_ru}\n\n"
        f"🔄 *Статус:* {status_ru}\n\n"
        f"📅 *Дата публикации:* {pub_date}{last_modified_str}\n\n"
        f"🔗 {url}\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
    )

    return text


def format_project_product(project):
    title = project.get("title", "Без названия")
    department = project.get("developedDepartment", {}).get("description", "Не указано")
    project_type_id = project.get("projectType", {}).get("id", "")
    project_type = PROJECT_TYPES.get(project_type_id, project.get("projectType", {}).get("description", "Не указано"))
    procedure_id = project.get("procedure", {}).get("id", "")
    procedure = PROCEDURE_TYPES.get(procedure_id, project.get("procedure", {}).get("description", "Не указано"))
    status = project.get("status", "Не указано")
    status_ru = STATUS_DESCRIPTIONS.get(status, status)
    project_id = project.get("id")
    topics = project.get("classified_topics", [])
    pub_date = project.get("publicationDate") or project.get("creationDate")
    last_modified = project.get("last_modified")
    last_modified_str = f"\n\n📅 *Последнее изменение:* {last_modified}" if last_modified else ""
    if topics:
        topic_labels = [TOPICS_SHORT.get(t, t) for t in topics]
        topic_str = " | ".join(topic_labels)
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
        f"🏢 *{department}* | {status_ru} | {pub_date}|{last_modified_str}\n\n"
        f"📌 *{short_title}*\n\n"
        f"📂 {project_type}\n\n"
        f"⚖ {procedure}\n\n"
        f"🔗 {url}\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
    )

    return text


def format_project_by_role(project, role):
    if role == 'analyst':
        return format_project_analyst(project)
    elif role == 'lawyer':
        return format_project_lawyer(project)
    elif role == 'product':
        return format_project_product(project)
    return format_project_analyst(project)


def format_weekly_digest(projects: List, start_date, end_date):
    start_str = start_date.strftime('%d.%m')
    end_str = end_date.strftime('%d.%m')

    if (end_date - start_date).days <= 7:
        text = f"📊 **НЕДЕЛЬНЫЙ ДАЙДЖЕСТ ({start_str}–{end_str})**\n\n"
    else:
        text = f"📊 **СВОДКА ЗА ПЕРИОД ({start_str}–{end_str})**\n\n"

    text = f"📊 **ЕЖЕНЕДЕЛЬНЫЙ ДАЙДЖЕСТ НПА ({start_str}–{end_str})**\n\n"
    text += f"📈 Всего новых проектов по нашим темам: {len(projects)}\n\n"

    by_topic = {}
    for p in projects:
        topics = p.get('classified_topics', [])
        if isinstance(topics, set):
            topics = list(topics)
        for topic in topics:
            if topic not in by_topic:
                by_topic[topic] = []
            by_topic[topic].append(p)



    for topic, projs in by_topic.items():
        topic_name = TOPICS_SHORT.get(topic, topic)
        text += f"\n━━━━━━━ {topic_name} ━━━━━━━\n\n"
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

def format_projects_notification(projects, subs, start_date, end_date):
    from datetime import datetime

    if start_date == end_date:
        date_str = start_date.strftime("%d.%m.%Y")
        header = f"📅 *Проекты за {date_str}*\n\n"
    else:
        header = (
            f"📅 *Дайджест за "
            f"{start_date.strftime('%d.%m')}–{end_date.strftime('%d.%m.%Y')}*\n\n"
        )

    header += f"📊 Найдено проектов: *{len(projects)}*\n"
    header += "━━━━━━━━━━━━━━━━━━\n\n"

    text = header

    for i, p in enumerate(projects, 1):
        title = p.get("title", "Без названия")
        dept = p.get("developedDepartment", {}).get("description", "Не указано")
        date = p.get("publicationDate") or p.get("creationDate", "")
        project_id = p.get("id")

        topics = p.get("classified_topics", [])
        topic_str = " ".join([TOPICS_SHORT.get(t, t) for t in topics]) if topics else "НПА"

        url = f"https://regulation.gov.ru/projects#npa={project_id}"

        text += f"{i}.{topic_str}\n\n"
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

    header += "🔔 *Ваши подписки:*\n\n"
    header += "\n\n ".join([TOPICS_SHORT.get(s, s) for s in subs])

    header += "\n\nВы получите уведомление, как только появятся новые проекты."

    return header

async def split_long_message_for_query(query, text, parse_mode = 'Markdown', reply_markup=None,
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
            await asyncio.sleep(1.0)
        except Exception as e:
            logger.error(f"Error sending part {i}: {e}")

    return None

async def send_projects_chunked(query, projects, user_role, title_prefix="📋 **Текущие проекты**", start_index=0, chunk_size=10, additional_data=None):
    total_projects = len(projects)
    end_index = min(start_index + chunk_size, total_projects)

    current_chunk = projects[start_index:end_index]
    text = f"{title_prefix}\n\n"
    text += f"📊 Найдено проектов: **{total_projects}**\n"
    text += f"📄 Показано {start_index + 1}-{end_index} из {total_projects}\n\n"
    text += "━━━━━━━━━━━━━━━━━━\n\n"

    for i, p in enumerate(current_chunk, start=start_index + 1):
        status = p.get('status', '')
        project_text = format_project_by_role(p, user_role)
        text += f"**{i}.** {project_text}\n"

    keyboard = []

    if end_index < total_projects:
        callback_data = f"continue_{start_index + chunk_size}"
        if additional_data:
            callback_data += f"_{additional_data}"
        keyboard.append([
            InlineKeyboardButton(
                f"▶️ Продолжить ({end_index + 1}-{min(end_index + chunk_size, total_projects)} из {total_projects})",
                callback_data=callback_data
            )
        ])
    keyboard.append([InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_main")])

    await split_long_message_for_query(
        query,
        text,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def send_archive_chunked(query, projects, topic, start_index=0, chunk_size=50):
    total_projects = len(projects)
    end_index = min(start_index + chunk_size, total_projects)

    current_chunk = projects[start_index:end_index]

    text = f"🗂 **Архив {TOPICS_SHORT.get(topic, topic)} (все проекты)**\n\n"
    text += f"📊 Найдено проектов: **{total_projects}**\n"
    text += f"📄 Показано {start_index + 1}-{end_index} из {total_projects}\n\n"
    text += "━━━━━━━━━━━━━━━━━━\n\n"

    for i, p in enumerate(current_chunk, start=start_index + 1):
        title = p.get('title', 'Без названия')
        dept = p.get('developedDepartment', {}).get('description', 'Не указано')
        date = p.get('publicationDate') or p.get('creationDate', '')
        date_str = date[:10] if date else 'Дата не указана'
        project_id = p.get('id')
        stage_info = format_project_stage(p)
        url = f"https://regulation.gov.ru/projects#npa={project_id}"

        text += f"{i}. **{TOPICS_SHORT.get(topic, topic)}**\n\n"
        text += f"   📌 {title[:150]}...\n\n"
        text += f"   🏢 {dept[:100]}\n\n"

        if stage_info:
            for line in stage_info.split('\n'):
                text += f"   {line}\n"

        text += f"   📅 {date_str}\n\n"
        text += f"   🔗 {url}\n\n"
        text += "━━━━━━━━━━━━━━━━━━\n\n"

    keyboard = []

    if end_index < total_projects:
        keyboard.append([
            InlineKeyboardButton(
                f"▶️ Продолжить ({end_index + 1}-{min(end_index + chunk_size, total_projects)} из {total_projects})",
                callback_data=f"continue_archive_{topic}_{start_index + chunk_size}"
            )
        ])

    keyboard.append([
        InlineKeyboardButton("◀️ Назад к темам", callback_data="menu_archive"),
        InlineKeyboardButton("◀️ В главное меню", callback_data="back_to_main")
    ])

    await split_long_message_for_query(
        query,
        text,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

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
def get_hourly_cache_key():
    return f"projects_hourly_{datetime.now().strftime('%Y%m%d_%H')}"

def get_archive_cache_key():
    return f"projects_archive_{datetime.now().strftime('%Y%m%d')}"

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

async def send_daily_notifications(application: Application):
    logger.info("🕐 Запуск ежедневной рассылки уведомлений")

    users = db.get_all_users()
    if not users:
        logger.info("Нет пользователей для уведомлений")
        return

    now = datetime.now()
    weekday = now.weekday()  # 0=пн, 6=вск

    if weekday in (5, 6):
        logger.info("Сегодня выходной — уведомления не отправляем")
        return

    if weekday == 0:
        dates_to_check = [
            (now - timedelta(days=3)).date(),
            (now - timedelta(days=2)).date(),
            (now - timedelta(days=1)).date()
        ]
    else:
        dates_to_check = [(now - timedelta(days=1)).date()]

    start_date = min(dates_to_check)
    end_date = max(dates_to_check)

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
        if projects is not None:
            projects_cache.set(cache_key, projects)

    if projects is None:
        logger.error("Не удалось загрузить проекты")
        return

    projects_for_period = []

    for p in projects:
        date_str = p.get('publicationDate') or p.get('creationDate')
        if not date_str:
            continue

        try:
            project_date = datetime.strptime(date_str[:10], '%Y-%m-%d').date()
        except (ValueError, TypeError):
            continue

        if project_date in dates_to_check:
            department = p.get('developedDepartment', {}).get('description')
            topics = ProjectClassifier.classify_as_list(
                title=p.get('title', ''),
                department=department
            )
            p['classified_topics'] = topics
            projects_for_period.append(p)

    logger.info(f"Найдено проектов за период: {len(projects_for_period)}")

    sent_count = 0
    current_time_str = now.strftime("%H:%M")

    for user in users:
        user_id = user['telegram_id']
        user_time = db.get_notification_time(user_id)

        if user_time != current_time_str:
            continue

        today_key = f"sent_{user_id}_{date_range_key}"
        if projects_cache.get(today_key):
            continue

        user_subs = get_user_subs_cached(user_id)
        if not user_subs:
            continue

        user_projects = []

        for p in projects_for_period:
            topics = p.get('classified_topics', [])
            if set(topics).intersection(set(user_subs)):
                user_projects.append(p)

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
            await asyncio.sleep(0.4)

        except Exception as e:
            logger.error(f"Ошибка отправки пользователю {user_id}: {e}")

    logger.info(f"Рассылка завершена. Отправлено: {sent_count}")
async def show_current_projects(query, context):
    await query.edit_message_text("🔍 Загружаю текущие проекты по вашим подпискам...")

    user_id = query.from_user.id
    user_role = db.get_user_role(user_id)
    user_subs = get_user_subs_cached(user_id)
    logger.info(f"Загружены подписки для пользователя {user_id}: {user_subs}")

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

    # Получаем проекты из кеша (уже отсортированные!)
    cache_key_projects = get_hourly_cache_key()
    all_projects = projects_cache.get(cache_key_projects)

    if all_projects is None:
        # Если кеша нет, загружаем через warm_up_cache
        logger.info("Кеш пуст, загружаем проекты...")
        all_projects = await warm_up_cache(context.application)  # <--- Добавлен await

    if not all_projects:
        await query.edit_message_text(
            "❌ Не удалось загрузить проекты.\nПопробуйте позже.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_main")
            ]])
        )
        return

    # Логируем статистику по last_modified
    projects_with_dates = [p for p in all_projects if p.get('last_modified')]
    logger.info(f"📊 В кеше {len(all_projects)} проектов, из них {len(projects_with_dates)} с датами изменений")

    # Статусы, которые считаем активными
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
        'PreDiscussion': '💬 Предварительное обсуждение',
        'Procedure': '🔄 Процедура'
    }

    # Статусы, которые считаем завершенными
    completed_statuses = {
        'Registered': '📋 Зарегистрирован',
        'Published': '📢 Опубликован',
        'Cancelled': '❌ Отменен',
        'EndDiscussion': '✅ Обсуждение завершено',
        'Rejected': '❌ Отклонен',
        'Complete': '✅ Завершён',
        'Completed': '✔️ Завершен'
    }

    # Фильтруем по подпискам и активности
    matching_projects = []
    today = datetime.now().date()

    for p in all_projects:
        # Проверяем подписки
        topics = p.get('classified_topics', [])
        if not topics or not set(topics).intersection(set(user_subs)):
            continue

        # Проверяем активность
        is_active = False
        status = p.get('status', '')

        # 1. По дате последнего изменения
        if p.get('last_modified'):
            try:
                last_mod = datetime.strptime(p['last_modified'], '%Y-%m-%d').date()
                days_since_change = (today - last_mod).days
                if days_since_change <= 90:
                    is_active = True
                    logger.debug(f"Проект {p.get('id')} активен по дате изменения: {days_since_change} дней")
            except (ValueError, TypeError) as e:
                logger.debug(f"Ошибка парсинга даты {p.get('last_modified')}: {e}")

        # 2. По статусу
        if not is_active:
            if status in active_statuses:
                is_active = True
                logger.debug(f"Проект {p.get('id')} активен по статусу: {status}")
            elif not status:
                is_active = True
                logger.debug(f"Проект {p.get('id')} активен (пустой статус)")
            elif status not in completed_statuses:
                is_active = True
                logger.debug(f"Проект {p.get('id')} активен (неизвестный статус: {status})")

        # 3. По дате окончания обсуждения
        if not is_active:
            end_date_str = p.get('endPublicDiscussion')
            if end_date_str:
                try:
                    end_date = datetime.strptime(end_date_str[:10], '%Y-%m-%d').date()
                    days_since_end = (today - end_date).days
                    if days_since_end <= 30:
                        is_active = True
                        logger.debug(f"Проект {p.get('id')} активен по дате окончания: {days_since_end} дней")
                except (ValueError, TypeError):
                    pass

        # 4. Проверка завершенных проектов
        if status in completed_statuses:
            if p.get('last_modified'):
                try:
                    last_mod = datetime.strptime(p['last_modified'], '%Y-%m-%d').date()
                    days_since_change = (today - last_mod).days
                    if days_since_change <= 30:
                        is_active = True
                        logger.debug(f"Завершенный проект {p.get('id')} активен по дате изменения: {days_since_change} дней")
                    else:
                        is_active = False
                except (ValueError, TypeError):
                    is_active = False
            else:
                is_active = False

        if is_active:
            matching_projects.append(p)

    logger.info(f"Найдено {len(matching_projects)} активных проектов из {len(all_projects)}")

    if not matching_projects:
        await query.edit_message_text(
            "❌ Нет активных проектов по вашим подпискам.\n\n"
            "Попробуйте посмотреть архив или изменить подписки.",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🗂 Перейти в архив", callback_data="menu_archive")],
                [InlineKeyboardButton("🔍 Изменить подписки", callback_data="menu_search")],
                [InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_main")]
            ])
        )
        return

    # Проекты УЖЕ ОТСОРТИРОВАНЫ из кеша, дополнительная сортировка не нужна!
    context.user_data['current_projects'] = matching_projects

    title = f"📋 **Текущие активные проекты**\n📊 Всего: {len(matching_projects)}\n"
    if projects_with_dates:
        title += f"📅 С сортировкой по дате изменения: {len([p for p in matching_projects if p.get('last_modified')])} проектов\n\n"
    else:
        title += f"📅 Сортировка по дате публикации (даты изменений загружаются в фоне)\n\n"

    await send_projects_chunked(
        query=query,
        projects=matching_projects,
        user_role=user_role,
        title_prefix=title,
        start_index=0,
        chunk_size=50
    )
async def show_search_menu(query, context):
    user_id = query.from_user.id

    if 'selected_topics' not in context.user_data:
        current_subs = set(get_user_subs_cached(user_id))
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
    user_id = query.from_user.id
    all_projects_cache_key = get_archive_cache_key()
    all_projects = projects_cache.get(all_projects_cache_key)

    if all_projects is None:
        all_projects = await fetch_with_retry_simple(api.fetch_all_projects_full,
        max_retries=3,
        delay=2)
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
        p_topics = p.get('classified_topics', [])
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
    text += "━━━━━━━━━━━━━━━━━━\n\n"

    count = 0
    for p in filtered_projects:
        count += 1
        title = p.get('title', 'Без названия')
        dept = p.get('developedDepartment', {}).get('description', 'Не указано')
        date = p.get('publicationDate') or p.get('creationDate', '')
        date_str = date[:10] if date else 'Дата не указана'
        project_id = p.get('id')
        stage_info = format_project_stage(p)
        url = f"https://regulation.gov.ru/projects#npa={project_id}"

        text += f"{count}. **{TOPICS_SHORT.get(topic, topic)}**\n\n"
        text += f"   📌 {title}\n\n"
        text += f"   🏢 {dept}\n\n"

        if stage_info:
            for line in stage_info.split('\n'):
                text += f"   {line}\n"

        text += f"   📅 {date_str}\n\n"
        text += f"   🔗 {url}\n\n"
        text += "━━━━━━━━━━━━━━━━━━\n\n"


    keyboard = [
        [InlineKeyboardButton("◀️ Назад к темам", callback_data="menu_archive")],
        [InlineKeyboardButton("◀️ В главное меню", callback_data="back_to_main")]
    ]
    archive_key = f"archive_{topic}_{user_id}"
    context.user_data[archive_key] = filtered_projects
    await send_archive_chunked(
        query=query,
        projects=filtered_projects,
        topic=topic,
        start_index=0,
        chunk_size=50
    )

async def show_settings_menu(query):
    user_id = query.from_user.id
    current_role = db.get_user_role(user_id)
    role_name = USER_ROLES.get(current_role, {}).get('name', 'Не выбрана')
    subscriptions = get_user_subs_cached(user_id)

    if subscriptions:
        sorted_subs = sorted(subscriptions)

        items = [TOPICS_SHORT.get(topic, topic) for topic in sorted_subs]

        rows = []
        for i in range(0, len(items), 2):
            left = items[i]
            if i + 1 < len(items):
                right = items[i + 1]
                rows.append(f"{left:<20}{right:<20}")
            else:
                rows.append(f"{left:<20}")

        subs_text = "📋 **Текущие подписки:**\n\n" + "\n\n".join(rows) + f"\n\n📊 Всего: {len(subscriptions)} подписок\n"
    else:
        subs_text = "❌ У вас нет активных подписок\n\n"


    keyboard = [
        [InlineKeyboardButton(f"👤 Сменить роль (сейчас: {role_name})", callback_data="change_role")],
        [InlineKeyboardButton("🔧 Управление подписками", callback_data="menu_search")],
        [InlineKeyboardButton("⏰ Время уведомлений", callback_data="settings_time")],
        [InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_main")]
    ]

    await query.edit_message_text(
        f"⚙️ **Настройки**\n\nТекущая роль: {role_name}\n\n{subs_text}\n\nВыберите что хотите изменить:",
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
        "📚 **СПРАВКА ПО БОТУ**\n\n"

        "🔍 **ОСНОВНЫЕ ФУНКЦИИ:**\n"
        "• 📋 **Текущие проекты** - активные проекты по вашим подпискам\n"
        "  • Считаются активными, если:\n"
        "    - менялись за последние 90 дней\n"
        "    - ИЛИ имеют активный статус (разработка, обсуждение, ОРВ, согласование)\n"
        "    - ИЛИ у них недавно (до 30 дней) закончилось обсуждение\n"
        "  • Даже завершенные проекты показываются, если в них были правки за последние 30 дней\n"
        "  • Сортировка по дате последнего изменения (самые свежие сверху)\n\n"
        "• 🔍 **Поиск по темам** - управление подписками на темы\n"
        "• 🗂 **Архив** - все проекты по выбранной теме (сортировка по дате публикации)\n"
        "• ⚙️ **Настройки** - смена роли, управление подписками, время уведомлений\n"
        "• 📅 **Последние обновления** - проекты за сегодня/вчера/3/7 дней\n\n"

        "📌 **ТЕМЫ МОНИТОРИНГА:**\n"
        "👥 **КЭДО** - кадровый электронный документооборот\n"
        "📄 **МЧД** - машиночитаемые доверенности\n"
        "🚛 **ЭПД** - электронные перевозочные документы\n"
        "✍️ **ЭП** - электронная подпись / удостоверяющие центры\n"
        "🧾 **ОФД** - операторы фискальных данных\n"
        "📊 **Отчетность** - электронная налоговая и бухгалтерская отчетность\n"
        "🔄 **B2B ЭДО** - коммерческий документооборот и роуминг\n"
        "🌐 **Экосистема** - 152-ФЗ, 125-ФЗ, хранение, архив\n\n"

        "👤 **РОЛИ ПОЛЬЗОВАТЕЛЕЙ:**\n"
        "📊 **Аналитик** - краткие уведомления о новых проектах\n"
        "⚖️ **Юрист** - полный обзор проектов НПА с детальной информацией\n"
        "📈 **Product-менеджер** - еженедельный дайджест\n\n"

        "📊 **ЭТАПЫ ПРОЕКТОВ:**\n"
        "📝 **Text** - Текст проекта\n"
        "💬 **Discussion** - Публичное обсуждение\n"
        "📊 **Evaluation** - Оценка регулирующего воздействия\n"
        "🔍 **Expertise** - Экспертиза\n"
        "✅ **Approval** - Согласование\n"
        "✍️ **Signing** - Подписание\n"
        "📋 **Registration** - Регистрация\n"
        "📢 **Publication** - Опубликован\n"
        "❌ **Cancelled** - Отменен\n"
        "✔️ **Completed** - Завершен\n\n"

        "⏰ **ВРЕМЯ УВЕДОМЛЕНИЙ:**\n"
        "🕐 Бот использует **UTC (Всемирное координированное время)**\n"
        "🇷🇺 **Москва (UTC+3)**: вычитайте 3 часа\n"
        "• 09:00 MSK → 06:00 UTC\n"
        "• 12:00 MSK → 09:00 UTC\n"
        "• 18:00 MSK → 15:00 UTC\n\n"

        "📋 **ДРУГИЕ ЧАСОВЫЕ ПОЯСА РФ:**\n"
        "• Калининград (UTC+2): -2 часа\n"
        "• Самара (UTC+4): -4 часа\n"
        "• Екатеринбург (UTC+5): -5 часов\n"
        "• Красноярск (UTC+7): -7 часов\n"
        "• Владивосток (UTC+10): -10 часов\n\n"

        "ℹ️ **КАК ЭТО РАБОТАЕТ:**\n"
        "1. Нажмите '🔍 Поиск по темам' и выберите интересующие темы\n"
        "2. Используйте '📋 Текущие проекты' для просмотра активных проектов\n"
        "3. Для более детального анализа используйте '🗂 Архив' (все проекты по теме)\n"
        "4. Настройте время уведомлений в ⚙️ Настройки\n"
        "5. Получайте ежедневные уведомления о новых проектах\n\n"

        "💡 **СОВЕТЫ:**\n"
        "• В '📋 Текущие проекты' попадают проекты, которые:\n"
        "  - менялись за последние 90 дней\n"
        "  - имеют активный статус\n"
        "  - или недавно завершили обсуждение\n"
        "• Дата последнего изменения загружается для всех ролей\n"
        "• Для юриста отображается наиболее полная информация\n"
        "• Для просмотра всех проектов за период используйте '📅 Последние обновления'\n"
        "• В архиве доступны все проекты по теме, включая завершенные\n"
        "• Роль можно сменить в любой момент в настройках\n"
        "• Подписки можно редактировать через '🔍 Поиск по темам'\n\n"

        "❓ **ПРОБЛЕМЫ:**\n"
        "• Если даты проектов не совпадают - учитывайте разницу часовых поясов (МСК vs UTC)\n"
        "• При ошибках попробуйте команду /start для перезапуска\n\n"

        "📢 **ОБНОВЛЕНИЯ:**\n"
        "• Бот проверяет новые проекты каждый час\n"
        "• Уведомления приходят в выбранное вами время (UTC)\n"
        "• Проекты обновляются каждый час\n"
        "• Архив обновляется ежедневно в 3:00 UTC\n"
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
        f"⏰ **Выберите время уведомлений**\n\n"
        f"🕐 Бот использует время **UTC (Всемирное координированное время)**\n\n"
        f"💡 **Как перевести ваше местное время в UTC:**\n\n"
        f"🇷🇺  **Для России (MSK/московское время):**\n"
        f"• Москва (UTC+3): вычитайте 3 часа\n\n"
        f"  Пример: хотите в 10:00 MSK → выбирайте 07:00 UTC\n\n",
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

    cache_key = get_hourly_cache_key()
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

    user_subs = get_user_subs_cached(query.from_user.id) if scope == "mine" else []
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

        department = p.get('developedDepartment', {}).get('description')
        topics = ProjectClassifier.classify_as_list(
            title=p.get("title", ""),
            department=department
        )
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
        f"━━━━━━━━━━━━━━━━━━\n\n"

    )

    for i, p in enumerate(matching_projects, 1):
        title = p.get("title", "Без названия")
        dept = p.get("developedDepartment", {}).get("description", "Не указано")
        date = p.get("publicationDate") or p.get("creationDate", "")
        project_id = p.get("id")

        topics = p.get("classified_topics", [])
        topic_str = " ".join([TOPICS_SHORT.get(t, t) for t in topics]) if topics else "НПА"

        url = f"https://regulation.gov.ru/projects#npa={project_id}"

        text += f"{i}. {topic_str}\n\n"
        text += f"   📌 {title}\n\n"
        text += f"   🏢 {dept[:100]}\n\n"
        text += f"   📅 {date[:10] if date else 'Нет даты'}\n\n"
        text += f"   🔗 {url}\n\n"
        text += "━━━━━━━━━━━━━━━━━━\n\n"

    await split_long_message_for_query(
        query,
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_main")
        ]])
    )


async def warm_up_archive_cache(application):
    logger.info("🗂 Прогрев архивного кеша")
    cache_key = get_archive_cache_key()

    if projects_cache.get(cache_key):
        logger.info("Архивный кеш уже существует")
        return

    projects = await fetch_with_retry_simple(
        api.fetch_all_projects_full,
        max_retries=3,
        delay=2
    )

    if projects:
        for p in projects:
            dept_dict = p.get('developedDepartment')
            if dept_dict and isinstance(dept_dict, dict):
                department = dept_dict.get('description')
                p['department_name'] = department or 'Не указано'
            else:
                department = None
                p['department_name'] = 'Не указано'

            p['classified_topics'] = ProjectClassifier.classify_as_list(
                title=p.get('title', ''),
                department=department
            )

        projects_cache.set(cache_key, projects)
        logger.info(f"Архивный кеш прогрет: {len(projects)} проектов")
    else:
        logger.error("Ошибка прогрева архивного кеша")


async def warm_up_cache(application):
    logger.info("🔥 Прогрев кеша проектов")

    cache_key_projects = get_hourly_cache_key()

    # Проверяем, есть ли уже проекты в кеше
    cached_projects = projects_cache.get(cache_key_projects)
    if cached_projects:
        logger.info(f"Кеш уже прогрет: {len(cached_projects)} проектов")
        return cached_projects

    logger.info("Кеш пуст, загружаем проекты...")
    projects = await fetch_with_retry_simple(
        api.fetch_all_projects,
        max_retries=3,
        delay=2,
        max_pages=500
    )

    if not projects:
        logger.error("Не удалось загрузить проекты")
        return None

    enriched_projects = []

    for p in projects:
        department = p.get('developedDepartment', {}).get('description')
        p['classified_topics'] = ProjectClassifier.classify_as_list(
            title=p.get('title', ''),
            department=department
        )
        enriched_projects.append(p)

    # Функция сортировки - по дате публикации (быстрая начальная сортировка)
    def get_sort_date(proj):
        pub = proj.get('publicationDate') or proj.get('creationDate', '')
        return pub[:10] if pub else '0000-00-00'

    # Сортируем все проекты
    enriched_projects.sort(
        key=get_sort_date,
        reverse=True
    )

    # Сохраняем в кеш
    projects_cache.set(cache_key_projects, enriched_projects)
    logger.info(f"Кеш прогрет: {len(enriched_projects)} проектов (отсортированы по дате публикации)")

    asyncio.create_task(load_missing_dates(enriched_projects))
    return enriched_projects


async def load_missing_dates(projects):
    """Загружает отсутствующие даты изменений"""
    logger.info(f"📅 Загрузка отсутствующих дат изменений для {len(projects)} проектов...")

    loaded = 0
    for p in projects:
        if p.get('last_modified'):
            continue  # Уже есть дата

        project_id = p.get('id')
        if not project_id:
            continue

        try:
            last_modified = await get_project_last_modified(project_id)
            if last_modified:
                p['last_modified'] = last_modified
                loaded += 1

            await asyncio.sleep(0.3)  # Защита API

        except Exception as e:
            logger.error(f"Ошибка загрузки даты для {project_id}: {e}")

    logger.info(f"📊 Загружено {loaded} новых дат")

    # Пересортировываем если были загружены новые даты
    if loaded > 0:
        projects.sort(
            key=lambda x: x.get('last_modified') or x.get('publicationDate') or x.get('creationDate',
                                                                                      '') or '0000-00-00',
            reverse=True
        )
        cache_key = get_hourly_cache_key()
        projects_cache.set(cache_key, projects)
        logger.info("🔄 Кеш пересортирован с учетом новых дат")
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    logger.info(f"Пользователь {user_id} нажал кнопку: {data}")

    if data.startswith('continue_') and not data.startswith('continue_archive_'):
        parts = data.split('_')
        start_index = int(parts[1])

        matching_projects = context.user_data.get('current_projects', [])
        if matching_projects:
            user_role = db.get_user_role(user_id)

            await send_projects_chunked(
                query=query,
                projects=matching_projects,
                user_role=user_role,
                title_prefix="📋 **Текущие проекты (активные)**\n\n",
                start_index=start_index,
                chunk_size=50
            )
    elif data.startswith('continue_archive_'):
        parts = data.split('_')
        topic = parts[2]
        start_index = int(parts[3])

        archive_key = f"archive_{topic}_{user_id}"
        filtered_projects = context.user_data.get(archive_key, [])

        if filtered_projects:
            await send_archive_chunked(
                query=query,
                projects=filtered_projects,
                topic=topic,
                start_index=start_index,
                chunk_size=50
            )
        else:
            await show_archive_projects(query, context, topic)
    elif data == "menu_current":
        await show_current_projects(query, context)
    elif data.startswith('select_role_'):
        role_id = data.replace('select_role_', '')
        await handle_role_selection(query, role_id)
    elif data == "change_role":
        await show_role_selection(query)
    elif data == "menu_search":
        await show_search_menu(query, context)
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
            invalidate_user_subs_cache(user_id)
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

        invalidate_user_subs_cache(user_id)

        context.user_data.pop('selected_topics', None)
        await query.edit_message_text(
            "✅ Подписки обновлены!",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_main")
            ]])
        )


def main():
    application = Application.builder().token(TOKEN).build()
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        send_daily_notifications,
        trigger=CronTrigger(hour="*"),
        args=[application],
        id='daily_notifications',
        replace_existing=True
    )
    scheduler.add_job(
        warm_up_cache,
        trigger=CronTrigger(minute="16"),
        args=[application],
        id='cache_warmup',
        replace_existing=True
    )

    scheduler.add_job(
        warm_up_archive_cache,
        trigger=CronTrigger(hour=13 , minute=50),
        args=[application],
        id='archive_cache_warmup',
        replace_existing=True
    )


    scheduler.start()
    logger.info("⏰ Планировщик уведомлений запущен (проверка каждую минуту)")

    application.add_handler(CommandHandler("start", start))
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