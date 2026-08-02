from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


def create_session_factory(database_url: str) -> sessionmaker[Session]:
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    engine = create_engine(database_url, connect_args=connect_args, future=True)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)
