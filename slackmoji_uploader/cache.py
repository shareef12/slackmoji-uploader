"""Helper for caching emoji metadata."""

import hashlib
import os
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy import Column, ForeignKey, Integer, String
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, scoped_session, sessionmaker

CACHE_FILE_FMT = ".emojicache.{:s}.db"

DbBase = declarative_base()
Session = None


@contextmanager
def session_scope():
    """Provide a transactional scope around a series of operations."""
    assert Session is not None, "Cache is not initialized"
    session = Session()
    try:
        yield session
        session.commit()
    except:
        session.rollback()
        raise
    finally:
        session.close()


class Emoji(DbBase):
    __tablename__ = "emoji"

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True)
    hash = Column(String(hashlib.sha256().digest_size))
    slackmojis_url = Column(String)

    def __repr__(self) -> str:
        return "<EmojiName(name='{!s}')>".format(self.name)


def initialize(workspace_name):
    """Create or open the cache database."""
    global Session

    cache_file = CACHE_FILE_FMT.format(workspace_name)
    cache_uri = "sqlite:///" + cache_file

    engine = create_engine(cache_uri)
    session_factory = sessionmaker(bind=engine)
    Session = scoped_session(session_factory)

    if not os.path.isfile(cache_file):
        DbBase.metadata.create_all(engine)
