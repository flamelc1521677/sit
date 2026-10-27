"""Shared SIT MySQL connection. Only SELECT statements are exposed to tests."""
import os
import re
from contextlib import contextmanager

import pymysql


def schema_name(environment, default):
    name = os.getenv(environment, default)
    if not re.fullmatch(r"[A-Za-z0-9_]+", name):
        raise ValueError("数据库名只能包含字母、数字和下划线")
    return name


def database_password():
    password = os.getenv("SIT_DB_PASSWORD")
    if not password and os.name == "nt":
        # Running VS Code processes can predate a Windows user environment update.
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                password, _ = winreg.QueryValueEx(key, "SIT_DB_PASSWORD")
        except FileNotFoundError:
            pass
    return password


def connection_options():
    password = database_password()
    if not password:
        raise RuntimeError("请设置 SIT_DB_PASSWORD 环境变量；接口场景必须完成数据库校验")
    return dict(host=os.getenv("SIT_DB_HOST", "sit-db01.suxi-test.com"),
        port=int(os.getenv("SIT_DB_PORT", "3306")), user=os.getenv("SIT_DB_USER", "user_readwrite"),
        password=password, charset="utf8mb4", autocommit=True,
        connect_timeout=10, read_timeout=15, write_timeout=15,
        cursorclass=pymysql.cursors.DictCursor)


@contextmanager
def connect_database():
    connection = None
    try:
        connection = pymysql.connect(**connection_options())
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION TRANSACTION READ ONLY")
        yield Database(connection)
    except pymysql.MySQLError as error:
        raise RuntimeError(f"SIT MySQL 连接或查询失败（错误码 {error.args[0]}）") from None
    finally:
        if connection is not None:
            connection.close()


class Database:
    def __init__(self, connection):
        self.connection = connection

    def select(self, sql, parameters=None):
        if not sql.lstrip().upper().startswith("SELECT "):
            raise ValueError("数据库校验只允许 SELECT")
        with self.connection.cursor() as cursor:
            cursor.execute(sql, parameters)
            return cursor.fetchall()
