import json
import re
import time
import unicodedata
from functools import lru_cache
from threading import Lock
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlparse
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup
from playwright.sync_api import Error as BrowserError, sync_playwright

SCRAPER_LOCK = Lock()
GOOGLE_RETRY_AT = 0.0
WIKIMEDIA_IMAGE_HOSTS = {"upload.wikimedia.org", "thumb.wikimedia.org"}
WIKIMEDIA_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}
MATCH_STOP_WORDS = {"with", "and", "the", "a", "in", "of", "con", "y", "de", "del", "la", "el", "al", "au", "aux", "et", "di", "alla"}
USER_AGENT = {"User-Agent": "Platera/1.0 (local recipe image reference)"}
UNAVAILABLE = "The dish-photo source is temporarily unavailable. Please try again later."


class ImageLookupError(RuntimeError):
    pass


def google_image_search_url(dish, cuisine=""):
    return "https://www.google.com/search?" + urlencode({"q": f"{dish} {cuisine} recipe".strip(), "udm": "2", "hl": "en", "safe": "active"})


def select_dish_image(results, dish, search_url):
    dish_words = set(re.findall(r"\w+", dish.casefold())) - {"with", "and", "the", "a", "in", "of"}
    ranked = []
    for result in results:
        image_url = result.get("imageUrl", "")
        source_url = result.get("sourceUrl", "")
        link_query = parse_qs(urlparse(source_url).query)
        source_url = link_query.get("imgrefurl", link_query.get("url", [source_url]))[0]
        original_url = link_query.get("imgurl", [image_url])[0]
        if not source_url.startswith("https://") or not urlparse(source_url).hostname:
            continue
        if urlparse(source_url).hostname in {"google.com", "www.google.com"}:
            continue
        if not image_url.startswith(("https://", "data:image/jpeg;base64,", "data:image/png;base64,", "data:image/webp;base64,")):
            continue
        title = result.get("title", "").strip()
        title_words = set(re.findall(r"\w+", title.casefold()))
        score = len(dish_words.intersection(title_words)) / max(1, len(dish_words))
        if score < 0.5 or result.get("width", 0) < 80 or result.get("height", 0) < 60:
            continue
        ranked.append((score, {
            "imageUrl": original_url if original_url.startswith("https://") else image_url,
            "thumbnailUrl": image_url,
            "sourceUrl": source_url,
            "sourceTitle": title or dish,
            "searchUrl": search_url,
            "dish": dish,
            "provider": "Google Images",
        }))
    if not ranked:
        raise ImageLookupError("No matching dish photo was found. Open the image search to check available sources.")
    return max(ranked, key=lambda item: item[0])[1]


@lru_cache(maxsize=128)
def scrape_dish_image(dish, cuisine=""):
    global GOOGLE_RETRY_AT
    search_url = google_image_search_url(dish, cuisine)
    with SCRAPER_LOCK:
        if time.monotonic() < GOOGLE_RETRY_AT:
            raise ImageLookupError("Google image lookup is paused after a verification request.")
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                try:
                    page = browser.new_page(viewport={"width": 1280, "height": 900})
                    page.goto(search_url, wait_until="domcontentloaded", timeout=20000)
                    if "/sorry/" in page.url or "consent.google" in page.url:
                        GOOGLE_RETRY_AT = time.monotonic() + 1800
                        raise ImageLookupError("Google requires verification before this image search. Open the image search directly.")
                    page.locator('img[src*="gstatic.com"], img[src^="data:image/"]').first.wait_for(timeout=10000)
                    results = page.locator("img").evaluate_all("""images => images.map(image => {
                        const container = image.closest('[data-lpage], [data-docid], .isv-r') || image.closest('a') || image.parentElement;
                        const link = container.querySelector('a[href*="imgres"]') || image.closest('a') || container.querySelector('a[href^="https://"]');
                        return {
                            imageUrl: image.currentSrc || image.src,
                            sourceUrl: container.getAttribute('data-lpage') || link?.href || '',
                            title: image.alt || container.innerText || '',
                            width: image.naturalWidth,
                            height: image.naturalHeight
                        };
                    })""")
                    return select_dish_image(results, dish, search_url)
                finally:
                    browser.close()
        except BrowserError:
            GOOGLE_RETRY_AT = time.monotonic() + 300
            raise ImageLookupError("The image scraper could not load Google Images. Check the connection and install Playwright Chromium.") from None


def parse_wikipedia_image(html, dish, source_url):
    page = BeautifulSoup(html, "html.parser")
    title = page.title.get_text(" ", strip=True).removesuffix(" - Wikipedia") if page.title else ""
    dish_words = set(re.findall(r"\w+", dish.casefold())) - {"with", "and", "the", "a", "in", "of"}
    title_words = set(re.findall(r"\w+", title.casefold()))
    image = page.select_one('meta[property="og:image"]')
    article_image = page.select_one('.infobox img[data-file-type="bitmap"], .infobox img')
    image_url = image.get("content", "") if image else article_image.get("src", "") if article_image else ""
    image_url = urljoin(source_url, image_url)
    if not dish_words or len(dish_words.intersection(title_words)) / len(dish_words) < 0.5:
        raise ImageLookupError("No public photo matched this dish name.")
    if urlparse(image_url).hostname not in {"upload.wikimedia.org", "thumb.wikimedia.org"} or not image_url.startswith("https://"):
        raise ImageLookupError("The matching dish page has no usable photo.")
    image_url = urlparse(image_url)._replace(query="", fragment="").geturl()
    credit = page.select_one('.infobox a.mw-file-description, .infobox a[href*="File:"]')
    return {
        "imageUrl": image_url,
        "thumbnailUrl": image_url,
        "sourceUrl": source_url,
        "attributionUrl": urljoin(source_url, credit.get("href", "")) if credit else source_url,
        "sourceTitle": title,
        "searchUrl": google_image_search_url(dish),
        "dish": dish,
        "provider": "Wikipedia / Wikimedia Commons",
    }


def title_words(text):
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().casefold()
    return set(re.findall(r"[a-z]+", folded)) - MATCH_STOP_WORDS


def title_match(dish_words, title):
    words = title_words(re.sub(r"\([^)]*\)", " ", title))
    union = dish_words | words
    return len(dish_words & words) / len(union) if union else 0.0


def wikimedia_search(host, params):
    url = f"https://{host}/w/api.php?" + urlencode({"action": "query", "format": "json", "generator": "search", "gsrlimit": 5, **params})
    try:
        with urlopen(Request(url, headers=USER_AGENT), timeout=15) as response:
            pages = json.loads(response.read(2000000).decode("utf-8")).get("query", {}).get("pages", {})
    except HTTPError as error:
        error.close()
        raise ImageLookupError(UNAVAILABLE) from None
    except (URLError, TimeoutError, ValueError, UnicodeError, AttributeError):
        raise ImageLookupError(UNAVAILABLE) from None
    return sorted((page for page in pages.values() if isinstance(page, dict)), key=lambda page: page.get("index", 99))


def wikimedia_image_url(url):
    parsed = urlparse(url) if isinstance(url, str) else None
    return bool(parsed and parsed.scheme == "https" and parsed.hostname in WIKIMEDIA_IMAGE_HOSTS)


def public_photo_candidates(query, dish, failures):
    search_url = google_image_search_url(dish)
    try:
        wikipedia_pages = wikimedia_search("en.wikipedia.org", {"gsrsearch": query, "gsrnamespace": 0, "prop": "pageimages|info", "piprop": "thumbnail|name", "pithumbsize": 800, "inprop": "url"})
    except ImageLookupError:
        wikipedia_pages = []
        failures.append("wikipedia")
    for page in wikipedia_pages:
        image_url = page.get("thumbnail", {}).get("source", "")
        article_url = page.get("fullurl", "")
        if wikimedia_image_url(image_url) and article_url.startswith("https://en.wikipedia.org/"):
            file_name = page.get("pageimage", "")
            credit = "https://en.wikipedia.org/wiki/File:" + quote(file_name, safe="_()-,.") if file_name else article_url
            yield page.get("title", ""), {"imageUrl": image_url, "thumbnailUrl": image_url, "sourceUrl": article_url, "attributionUrl": credit,
                                          "sourceTitle": page.get("title", dish), "searchUrl": search_url, "dish": dish, "provider": "Wikipedia / Wikimedia Commons"}
    try:
        commons_pages = wikimedia_search("commons.wikimedia.org", {"gsrsearch": query, "gsrnamespace": 6, "prop": "imageinfo", "iiprop": "url|mime", "iiurlwidth": 800})
    except ImageLookupError:
        commons_pages = []
        failures.append("commons")
    for page in commons_pages:
        info = (page.get("imageinfo") or [{}])[0]
        image_url = info.get("thumburl", "")
        page_url = info.get("descriptionurl", "")
        if info.get("mime") in WIKIMEDIA_MIME_TYPES and wikimedia_image_url(image_url) and page_url.startswith("https://commons.wikimedia.org/"):
            title = re.sub(r"^File:|\.[A-Za-z0-9]+$", "", page.get("title", ""))
            yield title, {"imageUrl": image_url, "thumbnailUrl": image_url, "sourceUrl": page_url, "attributionUrl": page_url,
                          "sourceTitle": title, "searchUrl": search_url, "dish": dish, "provider": "Wikimedia Commons"}


def search_public_photo(dish):
    dish_words = title_words(dish)
    shortened = re.split(r"\s+(?:with|and|con|y|in)\s+|\(", dish.strip(), maxsplit=1, flags=re.IGNORECASE)[0].strip()
    best = None
    failures = []
    for query in dict.fromkeys(filter(None, (dish.strip(), shortened))):
        for title, photo in public_photo_candidates(query, dish, failures):
            score = title_match(dish_words, title)
            if score >= 0.5 and (best is None or score > best[0]):
                best = (score, photo)
        if best:
            return best[1]
    if failures:
        raise ImageLookupError(UNAVAILABLE)
    raise ImageLookupError("No matching public dish photo is available. Use the image-search link to check other sources.")


@lru_cache(maxsize=128)
def find_dish_image(dish, cuisine=""):
    try:
        return scrape_dish_image(dish, cuisine)
    except ImageLookupError:
        titles = dict.fromkeys((dish.strip(), dish.strip().capitalize()))
        for title in titles:
            source_url = "https://en.wikipedia.org/wiki/" + quote(title.replace(" ", "_"), safe="_()-")
            try:
                request = Request(source_url, headers=USER_AGENT)
                with urlopen(request, timeout=15) as response:
                    return parse_wikipedia_image(response.read(2000000), dish, source_url)
            except ImageLookupError:
                continue
            except HTTPError as error:
                error.close()
                if error.code == 404:
                    continue
                raise ImageLookupError(UNAVAILABLE) from None
            except (URLError, TimeoutError):
                raise ImageLookupError(UNAVAILABLE) from None
        return search_public_photo(dish)