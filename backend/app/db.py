"""db.py  -  Database connection. One engine for the app; get_session() for each API request."""
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from app import config


class Base(DeclarativeBase):
    pass


def make_engine(url: str = None):
    url = url or config.DATABASE_URL
    kwargs = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {"pool_pre_ping": True}
    eng = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        @event.listens_for(eng, "connect")
        def _fk_on(conn, _):                      # SQLite ignores foreign keys unless asked
            cur = conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")  # live worker writes while the API reads
            cur.close()
    return eng


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db(eng=None):
    from app import models  # noqa: F401  (registers the tables)
    Base.metadata.create_all(eng or engine)


def get_session():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()
