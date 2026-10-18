"""Database setup for the marketplace demo."""
from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


def build_session_factory(database_url: str):
    is_sqlite = database_url.startswith("sqlite")
    # SQLite's default 5-second busy timeout is too short for even modest
    # concurrent demo traffic. This reduces avoidable "database is locked"
    # failures; callers still need to handle transient contention.
    connect_args = {"check_same_thread": False, "timeout": 30.0} if is_sqlite else {}
    engine = create_engine(database_url, connect_args=connect_args, pool_pre_ping=True)
    if is_sqlite:
        @event.listens_for(engine, "connect")
        def _configure_sqlite(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA busy_timeout=30000")
            finally:
                cursor.close()

    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return engine, factory
