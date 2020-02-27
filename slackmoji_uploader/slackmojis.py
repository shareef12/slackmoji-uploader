"""Download emojis from slackmojis.com."""

import re
from typing import Generator, Optional, Tuple
from urllib.parse import urlparse

import requests

SLACKMOJIS_BASE_URL = "https://slackmojis.com"

SLACKMOJIS_CHILD_PAGE_CRE = re.compile(r'href="(/categories/[^"]+)"')
SLACKMOJIS_EMOJI_LINK_CRE = re.compile(r'/emojis/[-0-9a-zA-Z]+/download')


class SlackmojisClient:
    """A client for slackmojis.com."""

    def _find_emojis_on_page(self, url: str, text: Optional[bytes] = None) -> Generator[str, None, None]:
        """Yield links to all emojis on a single page.

        Args:
            url: The URL for the page to scrape.
            text: The content of the specified url. This argument should be
                used when the specified page has already been fetched by the
                caller.

        Yields:
            Download links for emojis on the page.

        Raises:
            requests.HTTPError: If we could not fetch the page.
        """
        if not text:
            response = requests.get(url)
            response.raise_for_status()
            text = response.text

        for match in SLACKMOJIS_EMOJI_LINK_CRE.finditer(text):
            yield SLACKMOJIS_BASE_URL + match.group(0)


    def download_emoji(self, url: str) -> Tuple[str, bytes]:
        """Download an emoji.

        Args:
            emoji_url: The download url for the emoji.

        Returns:
            A tuple (filename, content) representing the emoji.

        Raises:
            requests.HTTPError: If the request failed.
        """
        response = requests.get(url)
        response.raise_for_status()

        # Links on slackmojis.com are redirects to the actual path for the
        # emoji. We can't determine the emoji's filename until after we request
        # follow the redirect chain.
        path = urlparse(response.request.url).path
        emoji_fname = path.split("/")[-1]
        return (emoji_fname, response.content)


    def find_all_emojis(self) -> Generator[str, None, None]:
        """Yield links to all emojis.

        Yields:
            Download links for all emojis.

        Raises:
            requests.HTTPError: If we couldn't fetch the slackmojis.com homepage.
        """
        # Process the base page
        print("[*] Finding emojis from:", SLACKMOJIS_BASE_URL)
        response = requests.get(SLACKMOJIS_BASE_URL)
        response.raise_for_status()

        for emoji_url in self._find_emojis_on_page(SLACKMOJIS_BASE_URL, text=response.text):
            yield emoji_url

        # Process child pages
        for match in SLACKMOJIS_CHILD_PAGE_CRE.finditer(response.text):
            child_page = SLACKMOJIS_BASE_URL + match.group(0)
            print("[*] Finding emojis from:", child_page)

            try:
                for emoji_url in self._find_emojis_on_page(child_page):
                    yield emoji_url
            except requests.HTTPError:
                print("Error fetching page:", child_page)
