#!/usr/bin/env python3

"""Upload emojis from slackmojis.com to a Slack workspace.

Dependencies:

* cairosvg
* pillow
* requests
* selenium
* sqlalchemy
* chromedriver (https://chromedriver.chromium.org/downloads)
"""

import argparse
import hashlib
import io
import os
import queue
import threading
import time
from multiprocessing.pool import ThreadPool
from typing import Optional
from urllib.parse import urlparse

import cairosvg
import requests
from PIL import Image

from slackmoji_uploader import cache
from slackmoji_uploader.cache import session_scope, Emoji
from slackmoji_uploader.slack import SlackClient
from slackmoji_uploader.slackmojis import SlackmojisClient

MAX_QUEUE_SIZE = 20


def emoji_name_in_cache(emoji_name: str) -> bool:
    """Check to see if an emoji name exists in the cache."""
    with session_scope() as session:
        result = session.query(Emoji).filter(Emoji.name == emoji_name).first()
        return result is not None


def emoji_url_in_cache(emoji_url: str) -> bool:
    """Check to see if the emoji's url is in the cache."""
    with session_scope() as session:
        result = session.query(Emoji).filter(Emoji.slackmojis_url == emoji_url).first()
        return result is not None


def download_emojis(queue: queue.Queue) -> None:
    """Download new emojis and insert them into the specified queue."""
    client = SlackmojisClient()

    for emoji_url in client.find_all_emojis():
        if emoji_url_in_cache(emoji_url):
            continue

        # This is a new emoji - download it, perform any necessary file
        # conversions, and add it to the queue.
        print("[*] Downloading emoji:", emoji_url)
        try:
            emoji_fname, emoji_content = client.download_emoji(emoji_url)
        except requests.HTTPError:
            print("[-] Error downloading emoji:", emoji_url)
            continue

        # Slack only supports emojis in gif, jpeg, and png formats
        emoji_name, emoji_ext = os.path.splitext(emoji_fname)
        emoji_ext = emoji_ext.lower()
        if emoji_ext == ".svg":
            emoji_content = cairosvg.svg2png(bytestring=emoji_content)
        elif emoji_ext in (".bmp", ".ico"):
            im = Image.open(io.BytesIO(emoji_content))
            fout = io.BytesIO()
            im.save(fout, format="PNG")
            fout.seek(0)
            emoji_content = fout.read()
        elif emoji_ext not in (".gif", ".jpg", ".jpeg", ".png"):
            print("[-] Unsupported emoji extension:", emoji_fname)
            continue

        queue.put((emoji_name, emoji_url, emoji_content))


def synchronize_cache(client: SlackClient, num_workers: Optional[int] = 25) -> None:
    """Populate the slack with emojis already uploaded to slack.

    Args:
        client: The slack client to use to enumerate emojis.
        num_workers: The number of threadpool workers to use. Note that these
            workers are IO bound, so having a large amount is ok.
    """
    def sync_emoji(emoji_name, emoji_url):
        with session_scope() as session:
            # If the emoji is not in the cache, we need to download it, hash
            # the contents, and add it to the cache.
            emoji = session.query(Emoji).filter(Emoji.name == emoji_name).first()
            if not emoji:
                response = requests.get(emoji_url)  # Authentication is not required for these images
                response.raise_for_status()
                emoji_hash = hashlib.sha256(response.content).digest()
                emoji = Emoji(name=emoji_name, hash=emoji_hash)
                session.add(emoji)

    pool = ThreadPool(processes=num_workers)
    pool.starmap(sync_emoji, client.list_emojis(batchsize=500), chunksize=500//num_workers)
    pool.close()
    pool.join()


def compute_emoji_name(emoji_fname: str) -> str:
    """Compute a unique emoji name for the specified emoji."""
    emoji_name, _ = os.path.splitext(emoji_fname)
    if not emoji_name_in_cache(emoji_name):
        return emoji_name

    # The emoji's name is already in the cache. Add a numeric suffix.
    for i in range(1000):
        candidate = "{:s}{:d}".format(emoji_name, i)
        if not emoji_name_in_cache(candidate):
            return candidate

    raise RuntimeError("Unable to find unique emoji name:", emoji_fname)


def upload_emoji(client: SlackClient, emoji: Emoji, emoji_data: bytes) -> None:
    """Upload a single emoji to slack.

    If the upload was successful, insert the emoji into the cache so we don't
    try to re-insert it on another run.
    """
    data = io.BytesIO(emoji_data)
    if client.upload_emoji(emoji.name, data):
        with session_scope() as session:
            session.add(emoji)


def upload_all_emojis(workspace_url: str, email: str, password: str, relogin_on_rate_limit: bool = False) -> None:
    """Try to upload all emojis from slackmojis.com to a slack workspace."""

    # Login to the slack workspace
    print("[*] Logging into slack workspace:", workspace_url)
    client = SlackClient(workspace_url)
    client.login(email, password)

    # Synchronize our cache with a list of already uploaded emojis
    print("[*] Synchronizing cache")
    workspace_name = urlparse(workspace_url).netloc.split(".")[0]
    cache.initialize(workspace_name)
    synchronize_cache(client)

    # Start a downloader thread
    print("[*] Starting downloader thread")
    emoji_queue = queue.Queue(maxsize=MAX_QUEUE_SIZE)
    download_thread = threading.Thread(target=download_emojis, args=(emoji_queue,), daemon=True)
    download_thread.start()

    # Continuously try to pull an emoji off the queue and upload it
    while download_thread.is_alive():
        try:
            emoji_fname, emoji_url, emoji_data = emoji_queue.get(timeout=1)
        except queue.Empty:
            continue

        # Check the emoji's hash to see if we've already uploaded it
        emoji_hash = hashlib.sha256(emoji_data).digest()
        with session_scope() as session:
            emoji = session.query(Emoji).filter(Emoji.hash == emoji_hash).first()
            if emoji:
                # If the cache item was from an emoji already uploaded to
                # slack, it won't have a slackmojis url. Add this to the item
                # so we don't try to download it again on a subsequent run.
                if not emoji.slackmojis_url:
                    emoji.slackmojis_url = emoji_url
                    session.add(emoji)
                continue

        # Compute a unique name for the emoji and upload it to slack
        emoji_name = compute_emoji_name(emoji_fname)

        print("[*] Uploading emoji:", emoji_name)
        emoji = Emoji(name=emoji_name, hash=emoji_hash, slackmojis_url=emoji_url)
        try:
            upload_emoji(client, emoji, emoji_data)
        except requests.HTTPError as err:
            # If we are being rate-limited, wait the requested time period
            # before trying again. We cannot log off and back on again, as
            # slack will detect this and present a captcha on the login page.
            if err.response.status_code == 429:
                retry_after = int(err.response.headers["Retry-After"])
                time.sleep(retry_after)
                upload_emoji(client, emoji, emoji_data)
            else:
                raise


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("workspace", help="Full workspace base url")

    args = parser.parse_args()

    email = os.environ.get("SLACK_EMAIL")
    password = os.environ.get("SLACK_PASSWORD")
    if not email or not password:
        parser.exit(1, "SLACK_EMAIL and SLACK_PASSWORD must exist in the environment\n")

    upload_all_emojis(args.workspace, email, password)


if __name__ == "__main__":
    main()
