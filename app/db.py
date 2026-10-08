import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


def normalize_database_url(url: str) -> str:
    # Neon/Vercel commonly supplies postgres:// or postgresql://.
    # SQLAlchemy uses psycopg3 explicitly here.
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://"): ]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://"): ]
    if os.getenv("VERCEL") and url.startswith("sqlite"):
        tmp_db = Path(tempfile.gettempdir()) / "news.db"
        return f"sqlite:///{tmp_db.as_posix()}"
    return url


database_url = normalize_database_url(settings.database_url)
connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}

engine_kwargs = {
    "future": True,
    "pool_pre_ping": True,
}

# Keep a small pool for serverless warm instances. Neon should supply a pooled
# connection URL when available.
if database_url.startswith("postgresql+"):
    engine_kwargs.update({"pool_size": 3, "max_overflow": 2, "pool_timeout": 10, "pool_recycle": 60})

engine = create_engine(
    database_url,
    connect_args=connect_args,
    **engine_kwargs,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)


_db_initialized = False


def ensure_db_initialized() -> None:
    global _db_initialized
    if not _db_initialized:
        try:
            init_db()
        except Exception:
            pass


@contextmanager
def session_scope():
    ensure_db_initialized()
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db() -> None:
    global _db_initialized
    from . import models  # noqa: F401
    Base.metadata.create_all(bind=engine)
    if engine.dialect.name == "postgresql":
        stmts = [
            "ALTER TABLE articles ALTER COLUMN title TYPE TEXT;",
            "ALTER TABLE articles ALTER COLUMN url TYPE TEXT;",
            "ALTER TABLE articles ALTER COLUMN author TYPE TEXT;",
            "ALTER TABLE articles ALTER COLUMN image_url TYPE TEXT;",
            "ALTER TABLE articles ALTER COLUMN source_domain TYPE TEXT;",
            "ALTER TABLE articles ALTER COLUMN external_id TYPE VARCHAR(256);",
            "ALTER TABLE articles ALTER COLUMN language TYPE VARCHAR(50);",
            "ALTER TABLE research_items ALTER COLUMN title TYPE TEXT;",
            "ALTER TABLE research_items ALTER COLUMN landing_url TYPE TEXT;",
            "ALTER TABLE research_items ALTER COLUMN pdf_url TYPE TEXT;",
            "ALTER TABLE research_items ALTER COLUMN source_domain TYPE TEXT;",
            "ALTER TABLE research_items ALTER COLUMN venue TYPE TEXT;",
            "ALTER TABLE research_items ALTER COLUMN source TYPE TEXT;",
            "ALTER TABLE research_items ALTER COLUMN doi TYPE TEXT;",
            "ALTER TABLE research_items ALTER COLUMN paper_id TYPE TEXT;",
            "ALTER TABLE research_items ALTER COLUMN external_id TYPE VARCHAR(300);",
            "ALTER TABLE research_items ALTER COLUMN item_type TYPE VARCHAR(50);",
        ]
        with engine.connect() as conn:
            for stmt in stmts:
                try:
                    conn.execute(text(stmt))
                    conn.commit()
                except Exception:
                    pass
    _db_initialized = True
