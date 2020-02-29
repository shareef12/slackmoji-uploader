# pylint: disable=invalid-name,too-few-public-methods,global-statement

"""Helper for caching emoji metadata."""

import hashlib
import os
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Column, Integer, String, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import scoped_session, sessionmaker

CACHE_FILE_FMT = ".emojicache.{:s}.db"

DbBase = declarative_base()
Session = None


@contextmanager
def session_scope() -> Any:
    """Provide a transactional scope around a series of operations."""
    assert Session is not None, "Cache is not initialized"
    session = Session()
    try:
        yield session
        session.commit()
    except:     # noqa: E722
        session.rollback()
        raise
    finally:
        session.close()


class Emoji(DbBase):
    """Database object for an Emoji."""
    __tablename__ = "emoji"

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True)
    hash = Column(String(hashlib.sha256().digest_size * 2))
    slackmojis_url = Column(String)

    def __repr__(self) -> str:
        return "<Emoji(name='{!s}')>".format(self.name)


def initialize(workspace_name: str) -> None:
    """Create or open the cache database."""
    global Session

    cache_file = CACHE_FILE_FMT.format(workspace_name)
    cache_uri = "sqlite:///" + cache_file

    engine = create_engine(cache_uri)
    session_factory = sessionmaker(bind=engine)
    Session = scoped_session(session_factory)

    if not os.path.isfile(cache_file):
        DbBase.metadata.create_all(engine)
