import sqlite3
import json
from datetime import datetime

class Database:
    def __init__(self, db_name='monitoring.db'):
        self.conn = sqlite3.connect(db_name, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self.create_tables()

    def create_tables(self):
        self.cursor.execute('''
        CREATE TABLE IF NOT EXISTS users(
        user_id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id INTEGER UNIQUE,
        first_name TEXT,
        last_name TEXT,
        username TEXT,
        department TEXT,
        registered_at TIMESTAMP)''')

        self.cursor.execute('''
        CREATE TABLE IF NOT EXISTS subscriptions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        topic TEXT,
        subscribed_at TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (user_id))''')

        self.cursor.execute('''
        CREATE TABLE IF NOT EXISTS projects(
        project_id INTEGER PRIMARY KEY AUTOINCREMENT,
        external_id INTEGER UNIQUE,
        title TEXT,
        department TEXT,
        publication_date TEXT,
        raw_json TEXT,
        topics TEXT,
        created_at TIMESTAMP)''')

        self.cursor.execute('''
        CREATE TABLE IF NOT EXISTS notifications_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        project_id INTEGER,
        sent_at TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (user_id),
        FOREIGN KEY (project_id) REFERENCES projects (project_id))''')
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_roles (
                user_id INTEGER PRIMARY KEY,
                role TEXT DEFAULT 'analyst',
                updated_at TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )''')

        self.conn.commit()
        print("Таблицы успешно созданы (или уже существовали)")
    def add_user(self, telegram_id, first_name, last_name, username):
        self.cursor.execute('''
                    INSERT OR IGNORE INTO users 
                    (telegram_id, first_name, last_name, username, registered_at)
                    VALUES (?, ?, ?, ?, ?)
                ''', (
            telegram_id,
            first_name,
            last_name,
            username,
            datetime.now().isoformat()
        ))

        self.conn.commit()

        self.cursor.execute(
            'SELECT user_id FROM users WHERE telegram_id = ?',
            (telegram_id,)
        )
        result = self.cursor.fetchone()
        return result[0]

    def get_user(self, telegram_id):
        self.cursor.execute(
            'SELECT * FROM users WHERE telegram_id = ?',
            (telegram_id,)
        )
        return self.cursor.fetchone()

    def get_all_users(self):
        try:
            self.cursor.execute('''
                SELECT telegram_id, first_name, last_name, username 
                FROM users
            ''')

            return [
                {
                    'telegram_id': row[0],
                    'first_name': row[1],
                    'last_name': row[2],
                    'username': row[3]
                }
                for row in self.cursor.fetchall()
            ]

        except Exception as e:
            print(f"Error getting all users: {e}")
            return []
    def subscribe(self, telegram_id, topic):
        self.cursor.execute(
            'SELECT user_id FROM users WHERE telegram_id = ?',
            (telegram_id,)
        )
        user = self.cursor.fetchone()
        if not user:
            print(f" Пользователь {telegram_id} не найден")
            return False
        user_id = user[0]

        self.cursor.execute('''
                    SELECT id FROM subscriptions 
                    WHERE user_id = ? AND topic = ?
                ''', (user_id, topic))
        if self.cursor.fetchone():
            print(f" Пользователь {user_id} уже подписан на {topic}")
            return False
        self.cursor.execute('''
                    INSERT INTO subscriptions (user_id, topic, subscribed_at)
                    VALUES (?, ?, ?)
                ''', (user_id, topic, datetime.now().isoformat()))

        self.conn.commit()
        print(f" Пользователь {user_id} подписан на {topic}")
        return True

    def unsubscribe(self, telegram_id, topic):
        self.cursor.execute(
            'SELECT user_id FROM users WHERE telegram_id = ?',
            (telegram_id,)
        )
        user = self.cursor.fetchone()
        if not user:
            return False

        user_id = user[0]
        self.cursor.execute('''
                    DELETE FROM subscriptions 
                    WHERE user_id = ? AND topic = ?
                ''', (user_id, topic))

        self.conn.commit()
        return self.cursor.rowcount > 0

    def get_subscriptions(self, telegram_id):
        self.cursor.execute(
            'SELECT user_id FROM users WHERE telegram_id = ?',
            (telegram_id,)
        )
        user = self.cursor.fetchone()

        if not user:
            return []

        user_id = user[0]
        self.cursor.execute(
            'SELECT topic FROM subscriptions WHERE user_id = ?',
            (user_id,)
        )
        subscriptions = [row[0] for row in self.cursor.fetchall()]

        return subscriptions
    def get_users_by_topic(self, topic):
        self.cursor.execute('''
                    SELECT u.telegram_id 
                    FROM users u
                    JOIN subscriptions s ON u.user_id = s.user_id
                    WHERE s.topic = ?
                ''', (topic,))
        return [row[0] for row in self.cursor.fetchall()]

    def save_project(self, project):
        from classifier import ProjectClassifier
        topics = ProjectClassifier.classify(
            title=project.get('title', '')
        )

        try:
            self.cursor.execute('''
                            INSERT OR IGNORE INTO projects 
                            (external_id, title, department, publication_date, raw_json, topics, created_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                        ''', (
                project.get('id'),
                project.get('title', ''),
                project.get('developedDepartment', {}).get('description', ''),
                project.get('publicationDate') or project.get('creationDate', ''),
                json.dumps(project, ensure_ascii=False),
                json.dumps(list(topics)),
                datetime.now().isoformat()
            ))
            self.conn.commit()
            return self.cursor.rowcount > 0
        except Exception as e:
            print(f" Ошибка при сохранении проекта: {e}")
            return False
    def get_new_projects_since(self, last_check):
        self.cursor.execute('''
                    SELECT * FROM projects 
                    WHERE publication_date > ?
                    ORDER BY publication_date DESC
                ''', (last_check,))
        return self.cursor.fetchall()
    def was_notified(self, user_id, project_id):
        self.cursor.execute('''
                    SELECT id FROM notifications_log 
                    WHERE user_id = ? AND project_id = ?
                ''', (user_id, project_id))
        return self.cursor.fetchone() is not None
    def mark_notified(self, user_id, project_id):
        self.cursor.execute('''
                    INSERT INTO notifications_log (user_id, project_id, sent_at)
                    VALUES (?, ?, ?)
                ''', (user_id, project_id, datetime.now().isoformat()))
        self.conn.commit()


    def __del__(self):
        if hasattr(self, 'conn'):
            self.conn.close()
            print(" Соединение с БД закрыто")


