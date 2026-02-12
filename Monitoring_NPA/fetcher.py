import requests
import json


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
        """Загружает одну страницу проектов"""

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
            response = self.session.post(url, json=payload, timeout=15)

            if response.status_code == 200:
                data = response.json()
                projects = data.get('result', [])
                print(f"   ✅ Страница {page}: {len(projects)} проектов")
                return projects
            else:
                print(f"   ❌ Ошибка {response.status_code} на странице {page}")
                return []

        except Exception as e:
            print(f"   ❌ Ошибка: {e}")
            return []

    def fetch_all_projects(self, max_pages=20):
        """Загружает ВСЕ доступные страницы"""

        print("=" * 70)
        print("🚀 ЗАГРУЗКА ВСЕХ ПРОЕКТОВ")
        print("=" * 70)

        all_projects = []

        for page in range(1, max_pages + 1):
            projects = self.fetch_projects(page=page, pageSize=20)

            if not projects:
                print(f"\n📦 Остановлено на странице {page} — проекты кончились")
                break

            all_projects.extend(projects)
            print(f"   📊 Всего проектов: {len(all_projects)}")

        # Убираем дубликаты
        unique = {p['id']: p for p in all_projects}.values()
        projects_list = list(unique)

        print("\n" + "=" * 70)
        print(f"🎯 ИТОГО ЗАГРУЖЕНО: {len(projects_list)} ПРОЕКТОВ")
        print("=" * 70)

        return projects_list

    def print_projects_with_links(self, projects, limit=5):
        """Показывает проекты с АКТИВНЫМИ ссылками"""

        print("\n📌 ПЕРВЫЕ 5 ПРОЕКТОВ С ССЫЛКАМИ:")
        print("=" * 70)

        for i, p in enumerate(projects[:limit], 1):
            project_id = p.get('id')
            # ФОРМИРУЕМ ССЫЛКУ
            url = f"https://regulation.gov.ru/projects#npa={project_id}"

            print(f"\n{i}. 🆔 ID: {project_id}")

            title = p.get('title', '').strip()
            if title:
                title = title[:100] + '...' if len(title) > 100 else title
                print(f"   📌 {title}")

            dept = p.get('developedDepartment', {}).get('description', '')
            if dept:
                print(f"   🏢 {dept}")

            date = p.get('publicationDate') or p.get('creationDate', '')
            if date:
                print(f"   📅 {date[:10]}")


            print(f"   🔍 Перейти: {url}")  # дублирую для наглядности

    def print_statistics(self, projects):
        """Показывает статистику по проектам"""

        print("\n📊 СТАТИСТИКА ПО ВЕДОМСТВАМ:")
        print("-" * 70)

        dept_stats = {}
        for p in projects:
            dept = p.get('developedDepartment', {}).get('description', 'Не указано')
            dept_stats[dept] = dept_stats.get(dept, 0) + 1

        for dept, count in sorted(dept_stats.items(), key=lambda x: x[1], reverse=True)[:15]:
            print(f"   {dept}: {count} проектов")

        print("\n📅 ПРОЕКТЫ ПО ДАТАМ:")
        print("-" * 70)

        date_stats = {}
        for p in projects:
            date = p.get('publicationDate') or p.get('creationDate', '')
            if date:
                date = date[:10]
                date_stats[date] = date_stats.get(date, 0) + 1

        for date, count in sorted(date_stats.items(), reverse=True)[:10]:
            print(f"   {date}: {count} проектов")


# ============= ЗАПУСК =============
if __name__ == "__main__":
    api = RegulationAPI()

    # Загружаем ВСЕ страницы
    projects = api.fetch_all_projects(max_pages=50)

    if projects:
        # Сохраняем всё в JSON
        with open('all_projects.json', 'w', encoding='utf-8') as f:
            json.dump(projects, f, ensure_ascii=False, indent=2)
        print(f"\n💾 Сохранено {len(projects)} проектов в all_projects.json")

        # Показываем первые 5 проектов с АКТИВНЫМИ ССЫЛКАМИ
        api.print_projects_with_links(projects, limit=20)

        # Показываем статистику
        api.print_statistics(projects)
    else:
        print("\n❌ Не удалось загрузить проекты")

    input("\n✅ Нажми Enter для выхода...")