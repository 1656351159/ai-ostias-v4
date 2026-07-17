# db/init_db.py
import os
import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def _default_db_path() -> str:
    """默认数据库路径：CRAWLER_DB_PATH 环境变量 > <skill目录>/../data/crawler.db"""
    env_path = os.environ.get("CRAWLER_DB_PATH")
    if env_path:
        return env_path
    return str(Path(__file__).resolve().parent.parent.parent / "data" / "crawler.db")


DB_PATH = _default_db_path()


def init_database(db_path: str = None):
    """初始化数据库，创建所有表"""
    if db_path is None:
        db_path = str(DB_PATH)

    # 确保 db 目录存在
    db_dir = os.path.dirname(os.path.abspath(db_path))
    Path(db_dir).mkdir(parents=True, exist_ok=True)

    # 读取 schema.sql
    schema_path = SCHEMA_PATH
    if not schema_path.exists():
        raise FileNotFoundError(f"schema.sql 不存在: {schema_path}")

    with open(schema_path, 'r', encoding='utf-8') as f:
        schema_sql = f.read()

    # 执行建表语句
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.executescript(schema_sql)
        conn.commit()
        print(f"✅ 数据库初始化成功: {db_path}")

        # 显示所有表
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]
        print(f"📋 已创建表: {', '.join(tables)}")

    except Exception as e:
        print(f"❌ 数据库初始化失败: {e}")
        raise
    finally:
        conn.close()


def clear_database(db_path: str = None):
    """清空所有数据（谨慎使用）"""
    if db_path is None:
        db_path = str(DB_PATH)

    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]

        # 关闭外键约束，方便删除
        cursor.execute("PRAGMA foreign_keys = OFF")

        for table in tables:
            if table not in ['sqlite_sequence']:
                cursor.execute(f"DELETE FROM {table}")
                cursor.execute(f"DELETE FROM sqlite_sequence WHERE name='{table}'")

        conn.commit()
        print(f"✅ 数据库已清空: {db_path}")
    except Exception as e:
        print(f"❌ 清空数据库失败: {e}")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    init_database()