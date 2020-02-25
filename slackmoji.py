#!/usr/bin/env python3

"""Upload emojis from slackmojis.com to a Slack workspace.

Dependencies:

* requests
* selenium
* chromedriver
"""

import argparse
import io
import os
import queue
import random
import re
import string
import threading
import time
from contextlib import contextmanager
from typing import BinaryIO, Generator, List, Tuple
from urllib.parse import urlparse

import requests
from selenium import webdriver
from selenium.common.exceptions import NoSuchFrameException
from sqlalchemy import create_engine
from sqlalchemy import Column, ForeignKey, Integer, String
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker


CACHE_FILE = ".emojicache.db"
CACHE_URI = "sqlite:///" + CACHE_FILE

MAX_QUEUE_SIZE = 20

DbBase = declarative_base()
engine = create_engine(CACHE_URI)
Session = sessionmaker(bind=engine)


@contextmanager
def session_scope():
    """Provide a transactional scope around a series of operations."""
    session = Session()
    try:
        yield session
        session.commit()
    except:
        session.rollback()
        raise
    finally:
        session.close()


class EmojiName(DbBase):
    __tablename__ = "emoji_name"

    id = Column(Integer, primary_key=True)
    name = Column(String)

    urls = relationship("EmojiUrl", back_populates="name")

    def __repr__(self) -> str:
        return "<EmojiName(name='{:s}', nr_urls='{:d}')>".format(self.name, len(self.urls))


class EmojiUrl(DbBase):
    __tablename__ = "emoji_url"

    id = Column(Integer, primary_key=True)
    url = Column(String)

    name_id = Column(Integer, ForeignKey("emoji_name.id"))
    name = relationship("EmojiName", back_populates="urls")

    def __repr__(self) -> str:
        return "<EmojiUrl(url='{:s}')>".format(self.url)


class EmojiDownloader:
    """A runnable class that will iteratively download emojis from slackmojis.com.

    Pass this class a queue, and it will populate it with emoji data. It will
    use the cache to ensure duplicates are not returned.
    """

    SLACKMOJIS_BASE_URL = "https://slackmojis.com"
    SLACKMOJIS_QUERY_URLS = [
        "https://slackmojis.com",
        "https://slackmojis.com/categories/19-random-emojis",
    ]

    def __init__(self, queue: queue.Queue):
        self._queue = queue

    def _is_new_emoji_name(self, name: str) -> bool:
        """Check to see if the name provides a new emoji."""
        with session_scope() as session:
            result = session.query(EmojiName).filter(EmojiName.name == name).first()
            return result is None

    def _is_new_emoji_url(self, url: str) -> bool:
        """Check to see if the url provides a new emoji.

        slackmojis.com urls are typically of the form:

            https://slackmojis.com/emojis/1739-royals/download

        Although not guaranteed, we try to extract the name from the this url
        to speed up cases where the cache was destroyed, but the workspace
        already has a bunch of uploaded emojis.
        """

        m = re.search('[0-9]+-(.*?)/download$', url)
        if m and not self._is_new_emoji_name(m.group(1)):
            return False

        with session_scope() as session:
            result = session.query(EmojiUrl).filter(EmojiUrl.url == url).first()
            return result is None

    def _download_emoji(self, url: str) -> None:
        """Download a specific emoji and add it to the queue."""
        # Download the emoji
        print("[*] Downloading emoji:", url)
        response = requests.get(url)
        response.raise_for_status()

        path = urlparse(response.request.url).path
        emoji_fname = path.split("/")[-1]
        emoji_name, _ = os.path.splitext(emoji_fname)

        # Insert it into the queue if it's new
        if self._is_new_emoji_name(emoji_name):
            self._queue.put((emoji_name, url, response.content))
            return

        # The emoji's name is not unique, but we have not seen its url
        # before. Add the url to the cache so we don't query it again.
        with session_scope() as session:
            name_obj = session.query(EmojiName).filter(EmojiName.name == emoji_name).first()
            url_obj = EmojiUrl(url=url, name=name_obj)
            session.add(url_obj)

    def _download_emojis(self, url: str) -> None:
        """Download all emojis linked to from a slackmojis.com page."""
        print("[*] Finding emojis from:", url)
        response = requests.get(url)
        response.raise_for_status()

        for match in re.finditer("/emojis/[-0-9a-zA-Z]+/download", response.text):
            link = self.SLACKMOJIS_BASE_URL + match.group(0)

            # Don't download the emoji if we've already seen it
            if not self._is_new_emoji_url(link):
                continue

            try:
                self._download_emoji(link)
            except requests.HTTPError:
                print("Error fetching emoji:", link)

    def run(self) -> None:
        """Continuously fetch emojis."""
        for url in EmojiDownloader.SLACKMOJIS_QUERY_URLS:
            try:
                self._download_emojis(url)
            except requests.HTTPError:
                print("Error fetching page:", url)


class LoginError(Exception):
    """There was an error logging into slack."""


class SlackClient:

    def __init__(self, workspace_url: str):
        self._workspace_url = workspace_url
        self._session = None
        self._token = None

    def login(self, email: str, password: str) -> None:
        """Login to a slack workspace.

        Returns:
            A tuple (session, token) that can be used for authenticated requests.

        Raises:
            LoginError if there was an issue logging in.
        """

        # Login using selenium. Slack does some javascript magic to detect which
        # browser you're using and will bail out if you try to use raw requests
        # (even if you set the user agent correctly).
        options = webdriver.ChromeOptions()
        options.add_argument("headless")

        driver = webdriver.Chrome(chrome_options=options)

        driver.set_window_size(800, 600)
        driver.get(self._workspace_url + "/")
        driver.find_element_by_id("email").send_keys(email)
        driver.find_element_by_id("password").send_keys(password)
        driver.find_element_by_id("signin_btn").click()

        # Slack includes an iframe that has your token. Grab the token so we can
        # use it later.
        try:
            driver.switch_to.frame("gantry-auth")
        except NoSuchFrameException:
            driver.close()
            raise LoginError(driver)

        m = re.search(r'"token":"([^"]+)', driver.page_source)
        if not m:
            driver.close()
            raise LoginError("Unable to find token in login page")
        self._token = m.groups()[0]

        # Create a session object with the required cookies.
        self._session = requests.Session()
        for cookie in driver.get_cookies():
            self._session.cookies.set(cookie["name"], value=cookie["value"])

        driver.close()

    def logout(self) -> None:
        """Logout of a slack workspace."""
        assert self._session and self._token
        self._session.close()
        self._session = None
        self._token = None

    def list_emojis(self, batch_size: int = 100) -> Generator[str, None, None]:
        """Download a list of emoji names in a slack workspace."""
        assert self._session and self._token

        cur_page = 1

        while True:
            # Process a page of data
            data = {
                "page": cur_page,
                "count": batch_size,
                "token": self._token,
            }

            resp = self._session.post(self._workspace_url + "/api/emoji.adminList", data=data)
            resp.raise_for_status()

            rdata = resp.json()
            for emoji in rdata["emoji"]:
                yield emoji["name"]

            # Check the paging information in the response to see if we've hit the end of the list
            paging = rdata["paging"]
            if paging["page"] >= paging["pages"]:
                break

            cur_page += 1

    def upload_emoji(self, name: str, content: BinaryIO) -> bool:
        """Upload an emoji to a slack workspace."""
        assert self._session and self._token

        data = {
            "name": name,
            "mode": "data",
            "token": self._token,
        }
        files = {
            "image": content,
        }

        resp = self._session.post(self._workspace_url + "/api/emoji.add", data=data, files=files)
        resp.raise_for_status()

        rdata = resp.json()
        if not rdata["ok"]:
            if rdata["error"] in ("error_name_taken", "error_name_taken_i18n"):
                print("[*] Emoji already uploaded:", name)
                return True
            if rdata["error"] == "error_bad_format":
                print("[!] Bad emoji format:", name)
                return False

            # This is an unknown error
            print("[!] Unknown error:", name, resp.text)
            return False

        return True

    def upload_emoji_from_file(self, pathname: str) -> bool:
        name, _ = os.path.splitext(os.path.basename(pathname))
        with open(pathname, "rb") as f:
            return self.upload_emoji(name, f)


def synchronize_cache(client: SlackClient):
    """Ensure emojis already uploaded are present in the cache."""
    with session_scope() as session:
        for emoji_name in client.list_emojis():
            result = session.query(EmojiName).filter(EmojiName.name == emoji_name).first()
            if not result:
                emoji = EmojiName(name=emoji_name)
                session.add(emoji)


def upload_emoji(client: SlackClient, emoji_name: str, emoji_url: str, emoji_data: bytes) -> None:
    """Upload a single emoji to slack."""
    # If the upload was successful, insert the emoji into the cache so we don't
    # try to re-insert it on another run.
    data = io.BytesIO(emoji_data)
    if client.upload_emoji(emoji_name, data):
        with session_scope() as session:
            cache_item = EmojiName(name=emoji_name, urls=[EmojiUrl(url=emoji_url)])
            session.add(cache_item)


def upload_all_emojis(workspace_url: str, email: str, password: str, relogin_on_rate_limit: bool = False) -> None:
    """Try to upload all emojis from slackmojis.com to a slack workspace."""

    # Initialize the cache
    if not os.path.isfile(CACHE_FILE):
        DbBase.metadata.create_all(engine)

    # Login to the slack workspace
    print("[*] Logging into slack workspace:", workspace_url)
    client = SlackClient(workspace_url)
    client.login(email, password)

    # Synchronize our cache with a list of already uploaded emojis
    print("[*] Synchronizing cache")
    synchronize_cache(client)

    # Start a downloader thread
    print("[*] Starting downloader thread")
    emoji_queue = queue.Queue(maxsize=MAX_QUEUE_SIZE)
    downloader = EmojiDownloader(emoji_queue)
    download_thread = threading.Thread(target=downloader.run, daemon=True)
    download_thread.start()

    # Continuously try to pull an emoji off the queue and upload it
    while download_thread.is_alive():
        emoji_name, emoji_url, emoji_data = emoji_queue.get()

        print("[*] Uploading emoji:", emoji_name)
        try:
            upload_emoji(client, emoji_name, emoji_url, emoji_data)
        except requests.HTTPError as err:
            # If we are being rate-limited, wait the requested time period
            # before trying again. We cannot log off and back on again, as
            # slack will detect this and present a captcha on the login page.
            if err.response.status_code == 429:
                retry_after = int(err.response.headers["Retry-After"])
                time.sleep(retry_after)
                upload_emoji(client, emoji_name, emoji_url, emoji_data)
            else:
                raise


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("workspace", help="Full workspace base url.")

    args = parser.parse_args()

    email = os.environ.get("SLACK_EMAIL")
    password = os.environ.get("SLACK_PASSWORD")
    if not email or not password:
        parser.exit(1, "SLACK_EMAIL and SLACK_PASSWORD must exist in the environment\n")

    upload_all_emojis(args.workspace, email, password)


if __name__ == "__main__":
    main()
