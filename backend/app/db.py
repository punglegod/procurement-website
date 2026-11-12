from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from .config import COMMODITIES, DATA_DIR, SOURCES
from .models import Base, Commodity, SourceState


def make_engine(url: str):
    engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 15})

    @event.listens_for(engine, "connect")
    def set_pragmas(connection, _):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    return engine


DATA_DIR.mkdir(parents=True, exist_ok=True)
engine = make_engine(f"sqlite:///{(DATA_DIR / 'procurement.sqlite3').as_posix()}")
SessionLocal = sessionmaker(engine, expire_on_commit=False)


def initialize_database(db_engine=engine):
    Base.metadata.create_all(db_engine)
    with sessionmaker(db_engine)() as session:
        for item in COMMODITIES:
            session.merge(Commodity(**vars(item)))
        for source in SOURCES:
            if session.get(SourceState, source) is None:
                session.add(SourceState(source=source))
        session.commit()
