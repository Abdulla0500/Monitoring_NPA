import requests
import json
from classifier import ProjectClassifier
import math

class RegulationAPI:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:147.0) Gecko/20100101 Firefox/147.0',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
            'Content-Type': 'application/json',
            'Origin': 'https://regulation.gov.ru',
            'Referer': 'https://regulation.gov.ru/',
            'Connection': 'keep-alive'
        })

    def fetch_projects(self, page=1, pageSize=20):
        url = "https://regulation.gov.ru/api/public/PublicProjects/GetFiltered"

        payload = {
            "listParams": {
                "filterModel": {
                    "filters": "",
                    "page": page,
                    "pageSize": pageSize
                }
            },
            "orderedFields": [
                "title", "developedDepartment", "projectId", "projectType",
                "creationDate", "publicationDate", "stage", "status", "procedure"
            ]
        }

        try:
            response = self.session.post(url, json=payload, timeout=30)

            if response.status_code == 200:
                data = response.json()
                projects = data.get('result', [])
                total_count = data.get('totalCount', 0)

                print(f"   ✅ Страница {page}: {len(projects)} проектов")
                return projects, total_count
            else:
                print(f"   ❌ Ошибка {response.status_code} на странице {page}")
                return [], 0

        except Exception as e:
            print(f"   ❌ Ошибка: {e}")
            return [], 0

    def wrap_text(self, text, width=60):
        """Разбивает текст на строки по width символов"""
        if not text:
            return text

        lines = []
        for i in range(0, len(text), width):
            lines.append(text[i:i + width])
        return '\n'.join(lines)

    def fetch_all_projects_full(self, page_size=20):
        print("=" * 70)
        print("🗂 ПОЛНАЯ ЗАГРУЗКА ВСЕХ ПРОЕКТОВ (ARCHIVE)")
        print("=" * 70)

        all_projects = []

        # 1️⃣ Первая страница
        projects, total_count = self.fetch_projects(page=1, pageSize=page_size)

        if not projects:
            return []

        all_projects.extend(projects)

        total_pages = math.ceil(total_count / page_size)

        print(f"📊 Всего проектов: {total_count}")
        print(f"📄 Всего страниц: {total_pages}")

        # 2️⃣ Остальные страницы
        for page in range(2, total_pages + 1):
            projects, _ = self.fetch_projects(page=page, pageSize=page_size)
            all_projects.extend(projects)

        # 3️⃣ Убираем дубликаты
        unique = {p['id']: p for p in all_projects}.values()
        projects_list = list(unique)

        print(f"🎯 ИТОГО ЗАГРУЖЕНО: {len(projects_list)} ПРОЕКТОВ")

        return projects_list
    def fetch_all_projects(self, max_pages=500, page_size=20):
        print("=" * 70)
        print("🚀 ЗАГРУЗКА ВСЕХ ПРОЕКТОВ")
        print("=" * 70)

        all_projects = []

        # === 1. Загружаем первую страницу ===
        projects, total_count = self.fetch_projects(page=1, pageSize=page_size)

        if not projects:
            print("❌ Не удалось получить первую страницу")
            return []

        all_projects.extend(projects)

        # === 2. Считаем общее количество страниц ===
        total_pages = math.ceil(total_count / page_size)

        print(f"\n📊 Всего проектов в API: {total_count}")
        print(f"📄 Всего страниц: {total_pages}")

        # Ограничиваем max_pages
        pages_to_load = min(total_pages, max_pages)

        print(f"📥 Будем загружать: {pages_to_load} страниц\n")

        # === 3. Загружаем остальные страницы ===
        for page in range(2, pages_to_load + 1):
            projects, _ = self.fetch_projects(page=page, pageSize=page_size)

            all_projects.extend(projects)
            print(f"   📊 Всего проектов: {len(all_projects)}")

        # === 4. Убираем дубликаты ===
        unique = {p['id']: p for p in all_projects}.values()
        projects_list = list(unique)

        print("\n" + "=" * 70)
        print(f"🎯 ИТОГО ЗАГРУЖЕНО: {len(projects_list)} ПРОЕКТОВ")
        print("=" * 70)

        return projects_list

    def print_projects(self, projects, limit=10, filter_topic=None):
        """
        Показывает проекты (только нужная информация)
        filter_topic: если указан, показывает только проекты этой темы
        Проекты сортируются по дате публикации (сначала новые)
        """
        # Фильтруем проекты, если нужно
        filtered_projects = []
        for p in projects:
            topics = ProjectClassifier.classify(
                title=p.get('title', ''),
                department=p.get('developedDepartment', {}).get('description', '')
            )

            if filter_topic:
                if filter_topic in topics:
                    filtered_projects.append(p)
            else:
                filtered_projects.append(p)

        if filter_topic and not filtered_projects:
            print(f"\n❌ Проектов с темой {ProjectClassifier.get_topic_name(filter_topic)} не найдено")
            return

        # ===== ВАЖНО: СОРТИРУЕМ ПО ДАТЕ (СНАЧАЛА НОВЫЕ) =====
        def get_date(project):
            """Извлекает дату для сортировки"""
            date = project.get('publicationDate') or project.get('creationDate', '')
            return date if date else '0000-00-00'  # проекты без даты в конец

        filtered_projects.sort(key=get_date, reverse=True)  # reverse=True = новые сверху

        # Заголовок
        if filter_topic:
            topic_name = ProjectClassifier.get_topic_name(filter_topic)
            print(
                f"\n📌 ПОКАЗАНО {min(len(filtered_projects), limit)} ИЗ {len(filtered_projects)} ПРОЕКТОВ ПО ТЕМЕ {topic_name}")
        else:
            print(
                f"\n📌 ПОКАЗАНО {min(len(filtered_projects), limit)} ИЗ {len(filtered_projects)} ПРОЕКТОВ (ВСЕ ТЕМЫ)")
        print(f"   ⏱️  Сортировка: сначала новые")
        print("=" * 70)

        # Выводим проекты
        for i, p in enumerate(filtered_projects[:limit], 1):
            project_id = p.get('id')
            url = f"https://regulation.gov.ru/projects#npa={project_id}"
            title = p.get('title', '').strip()
            dept = p.get('developedDepartment', {}).get('description', '')
            date = p.get('publicationDate') or p.get('creationDate', '')

            # Определяем тематику
            topics = ProjectClassifier.classify(title, dept)
            topic_str = ProjectClassifier.format_topics(topics)


            print(f"\n{i}. 🆔 {project_id} {topic_str}")
            print(f"   📌 {self.wrap_text(title, 70)}")
            print(f"   🏢 {dept}")
            print(f"   📅 {date[:10] if date else 'Нет даты'}")
            print(f"   🔗 {url}")


# ============= ЗАПУСК =============
if __name__ == "__main__":
    api = RegulationAPI()

    # Загружаем проекты (ОДИН РАЗ)
    projects = api.fetch_all_projects(max_pages=100)

    if not projects:
        print("\n❌ Не удалось загрузить проекты")
        input("\nНажми Enter для выхода...")
        exit()

    # Сохраняем в JSON (на всякий случай)
    with open('../all_projects.json', 'w', encoding='utf-8') as f:
        json.dump(projects, f, ensure_ascii=False, indent=2)
    print(f"\n💾 Сохранено {len(projects)} проектов в all_projects.json")

    # Меню выбора
    while True:
        print("\n" + "=" * 70)
        print("📋 ВЫБЕРИТЕ ТЕМУ ДЛЯ ПРОСМОТРА:")
        print("=" * 70)
        print("1. 🚛 ЭПД (электронные перевозочные документы)")
        print("2. 📄 МЧД (машиночитаемые доверенности)")
        print("3. 📁 ЭДО (электронный документооборот)")
        print("4. ✍️ ЭП (электронная подпись)")
        print("5. 🧾 ОФД (операторы фискальных данных)")
        print("6. 📊 ВСЕ проекты")
        print("0. 🚪 Выход")

        choice = input("\n👉 Ваш выбор: ").strip()

        topic_map = {
            '1': 'epd',
            '2': 'mchd',
            '3': 'edo',
            '4': 'ep',
            '5': 'ofd'
        }

        if choice == '0':
            print("\n👋 До свидания!")
            break
        elif choice == '6':
            api.print_projects(projects, limit=10)
        elif choice in topic_map:
            topic = topic_map[choice]
            topic_name = ProjectClassifier.get_topic_name(topic)
            print(f"\n🔍 Ищем проекты по теме {topic_name}...")
            api.print_projects(projects, limit=10, filter_topic=topic)
        else:
            print("\n❌ Неверный выбор, попробуйте снова")

    input("\n✅ Нажми Enter для выхода...")