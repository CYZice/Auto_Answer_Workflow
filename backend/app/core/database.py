import logging
import os
import sqlite3
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path(os.getenv("AGENT_DB_PATH", "/app/data/agent_tasks.db")).resolve()
DEFAULT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
SQLALCHEMY_DATABASE_URL = f"sqlite:///{DEFAULT_DB_PATH.as_posix()}"

def _fk_pragma_on_connect(dbapi_con, con_record):
    """
    配置 SQLite 连接以启用 WAL (Write-Ahead Logging) 模式，
    这对于并发写入非常重要，也是 PRD 架构中的关键要求。
    """
    try:
        dbapi_con.execute('pragma journal_mode=WAL')
        dbapi_con.execute('pragma synchronous=NORMAL')
    except sqlite3.Error as e:
        logger.error(f"Failed to enable WAL mode: {e}")

# check_same_thread=False 允许跨线程共享连接，FastAPI + SQLite 必需
engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)

# 注册事件，在每次新建数据库连接时开启 WAL
event.listen(engine, 'connect', _fk_pragma_on_connect)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    """
    FastAPI 依赖注入：获取数据库会话
    """
    db = SessionLocal()
    try:
        yield db
    except Exception as e:
        # 防御性编程：捕获异常并记录
        logger.error(f"Database session error: {e}")
        raise
    finally:
        db.close()
