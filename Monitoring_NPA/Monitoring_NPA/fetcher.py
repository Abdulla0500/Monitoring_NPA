import requests
import math
import logging

# Добавим логирование
logger = logging.getLogger(__name__)

class RegulationAPI:
    def __init__(self):
        self.base_url = "https://regulation.gov.ru"
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

    # !!! ИСПРАВЛЕНО: Добавлен правильный метод для получения этапов !!!
    def fetch_project_stages(self, project_id: str):
        """
        Получает этапы конкретного проекта по его ID
        """
        url = f"{self.base_url}/api/public/PublicProjects/GetProjectStages/{project_id}"

        try:
            logger.info(f"Запрос этапов для проекта {project_id}")
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Ошибка получения этапов проекта {project_id}: {e}")
            return None

    def fetch_projects(self, page=1, pageSize=20):
        url = f"{self.base_url}/api/public/PublicProjects/GetFiltered"

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

        projects, total_count = self.fetch_projects(page=1, pageSize=page_size)

        if not projects:
            return []

        all_projects.extend(projects)

        total_pages = math.ceil(total_count / page_size)

        print(f"📊 Всего проектов: {total_count}")
        print(f"📄 Всего страниц: {total_pages}")

        for page in range(2, total_pages + 1):
            projects, _ = self.fetch_projects(page=page, pageSize=page_size)
            all_projects.extend(projects)

        unique = {p['id']: p for p in all_projects}.values()
        projects_list = list(unique)

        print(f"🎯 ИТОГО ЗАГРУЖЕНО: {len(projects_list)} ПРОЕКТОВ")

        return projects_list

    def fetch_all_projects(self, max_pages=500, page_size=20):
        print("=" * 70)
        print("🚀 ЗАГРУЗКА ВСЕХ ПРОЕКТОВ")
        print("=" * 70)

        all_projects = []

        projects, total_count = self.fetch_projects(page=1, pageSize=page_size)

        if not projects:
            print("❌ Не удалось получить первую страницу")
            return []

        all_projects.extend(projects)

        total_pages = math.ceil(total_count / page_size)

        print(f"\n📊 Всего проектов в API: {total_count}")
        print(f"📄 Всего страниц: {total_pages}")

        pages_to_load = min(total_pages, max_pages)

        print(f"📥 Будем загружать: {pages_to_load} страниц\n")

        for page in range(2, pages_to_load + 1):
            projects, _ = self.fetch_projects(page=page, pageSize=page_size)

            all_projects.extend(projects)
            print(f"   📊 Всего проектов: {len(all_projects)}")

        unique = {p['id']: p for p in all_projects}.values()
        projects_list = list(unique)

        print("\n" + "=" * 70)
        print(f"🎯 ИТОГО ЗАГРУЖЕНО: {len(projects_list)} ПРОЕКТОВ")
        print("=" * 70)

        return projects_list