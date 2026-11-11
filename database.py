from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker, declarative_base

DATABASE_URL = "sqlite:///./nexyn.db" # this will create the database in nexyn.db

engine = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False}
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base() # the base class from which all models will inherit


def initialize_database(db_engine: Engine) -> None:
    Base.metadata.create_all(bind=db_engine)
    additions = {
        "connection_status": "VARCHAR",
        "refreshed_at": "DATETIME",
        "first_registered_at": "DATETIME",
        "last_registered_at": "DATETIME",
        "last_seen_at": "DATETIME",
        "connector_type": "VARCHAR",
        "domain": "VARCHAR",
    }
    with db_engine.begin() as connection:
        columns = {column["name"] for column in inspect(connection).get_columns("devices")}
        for name, sql_type in additions.items():
            if name not in columns:
                connection.execute(
                    text(f"ALTER TABLE devices ADD COLUMN {name} {sql_type}")
                )