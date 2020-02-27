"""A Slack client emulating a user."""

import os
import re
from typing import BinaryIO, Generator, Tuple

import requests
from selenium import webdriver
from selenium.common.exceptions import NoSuchFrameException


class LoginError(Exception):
    """There was an error logging into slack."""


class SlackClient:

    def __init__(self, workspace_url: str):
        self._workspace_url = workspace_url
        self._session = None
        self._token = None

    def login(self, email: str, password: str) -> None:
        """Login to a slack workspace.

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

    def list_emojis(self, batchsize: int = 100) -> Generator[Tuple[str, str], None, None]:
        """Download a list of emoji names in a slack workspace.

        Args:
            batchsize: The batch size to use for response pagination.

        Yields:
            A tuple (name, url) representing the emoji.

        Raises:
            requests.HTTPError: On request failure.
        """
        assert self._session and self._token

        cur_page = 1

        while True:
            # Process a page of data
            data = {
                "page": cur_page,
                "count": batchsize,
                "token": self._token,
            }

            resp = self._session.post(self._workspace_url + "/api/emoji.adminList", data=data)
            resp.raise_for_status()

            rdata = resp.json()
            for emoji in rdata["emoji"]:
                yield emoji["name"], emoji["url"]

            # Check the paging information in the response to see if we've hit the end of the list
            paging = rdata["paging"]
            if paging["page"] >= paging["pages"]:
                break

            cur_page += 1

    def upload_emoji(self, name: str, content: BinaryIO) -> bool:
        """Upload an emoji to a slack workspace.

        Args:
            name: A unique name to use for the emoji.
            content: A file object containing the emoji's raw data.

        Returns:
            True if the emoji was successfully uploaded.

        Raises:
            requests.HTTPError: On request failure.
        """
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
        """Upload an emoji from a file."""
        name, _ = os.path.splitext(os.path.basename(pathname))
        with open(pathname, "rb") as f:
            return self.upload_emoji(name, f)
