import json
import threading
import unittest
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import expect, sync_playwright

import server
from test_server import recipes_fixture


class RecipeBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.addClassCleanup(cls.playwright.stop)
        cls.browser = cls.playwright.chromium.launch()
        cls.addClassCleanup(cls.browser.close)
        cls.http_server = server.ThreadingHTTPServer(("127.0.0.1", 0), server.PlateraHandler)
        cls.base_url = f"http://127.0.0.1:{cls.http_server.server_port}"
        cls.thread = threading.Thread(target=cls.http_server.serve_forever, daemon=True)
        cls.thread.start()
        cls.addClassCleanup(cls.thread.join)
        cls.addClassCleanup(cls.http_server.server_close)
        cls.addClassCleanup(cls.http_server.shutdown)

    def setUp(self):
        self.context = self.browser.new_context(viewport={"width": 1280, "height": 900})
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.page.set_default_timeout(5000)
        self.page_errors = []
        self.requests = []
        self.responses = [(200, recipes_fixture())]
        self.page.on("pageerror", lambda error: self.page_errors.append(str(error)))
        self.page.route("https://**", lambda route: route.abort())
        self.page.route("**/api/images?*", self.fulfill_photo)
        self.page.route("**/api/recipes", self.fulfill_recipes)

    def fulfill_photo(self, route):
        dish = parse_qs(urlparse(route.request.url).query)["dish"][0]
        route.fulfill(json={
            "dish": dish,
            "imageUrl": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=",
            "sourceUrl": "https://example.test/dish",
        })

    def fulfill_recipes(self, route):
        self.requests.append(route.request.post_data_json)
        status, data = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        route.fulfill(status=status, json=data)

    def open_generator(self):
        self.page.goto(self.base_url)
        expect(self.page.locator("#cuisine-select")).to_be_enabled()
        self.page.locator("#ingredients-input").fill("potatoes, onions, garlic")

    def generate(self):
        self.page.locator("#generate-btn").click()
        expect(self.page.locator("#generated-recipes-container > article")).to_have_count(3)
        expect(self.page.locator("#generate-btn")).to_be_enabled()

    def test_corrupt_saved_entries_do_not_break_startup(self):
        stored = [None, 42, {}, {"name": "Incomplete"}, recipes_fixture()["recipes"][0]]
        self.page.add_init_script(f"localStorage.setItem('platera-recipes', JSON.stringify({json.dumps(stored)}));")
        self.open_generator()
        expect(self.page.locator("#saved-recipes-container > article")).to_have_count(1)
        self.assertEqual(self.page_errors, [])

    def test_provider_recovery_preserves_results_and_history(self):
        next_batch = recipes_fixture()
        for recipe, name, cuisine in zip(next_batch["recipes"], ("Patatas Bravas", "Ribollita", "Spanakorizo"), ("Spanish", "Italian", "Greek")):
            recipe.update(name=name, cuisine=cuisine)
        self.responses = [
            (200, recipes_fixture()),
            (503, {"error": "The recipe service is busy right now."}),
            (200, next_batch),
        ]
        self.open_generator()
        self.generate()
        self.page.locator("#generate-btn").click()
        expect(self.page.locator("#error-display")).to_be_visible()
        expect(self.page.locator("#generated-recipes-section")).to_be_visible()
        expect(self.page.locator("#history-status")).to_contain_text("3 recipes")
        expect(self.page.locator("#ingredients-input")).to_have_value("potatoes, onions, garlic")
        self.page.get_by_role("button", name="Try Again", exact=True).click()
        expect(self.page.locator("#generated-recipes-container")).to_contain_text("Patatas Bravas")
        expect(self.page.locator("#history-status")).to_contain_text("6 recipes")
        self.assertEqual(len(self.requests[2]["PREVIOUS_RECIPE_HISTORY"]), 3)
        self.assertEqual(self.page_errors, [])

    def test_storage_failure_keeps_session_recipe_box(self):
        self.page.add_init_script("Storage.prototype.setItem = () => { throw new DOMException('Storage full', 'QuotaExceededError'); };")
        self.open_generator()
        self.generate()
        self.page.locator(".save-btn").first.click()
        expect(self.page.locator("#saved-recipes-container > article")).to_have_count(1)
        expect(self.page.locator("#error-display")).to_contain_text("this visit")
        self.assertEqual(self.page_errors, [])

    def test_photo_is_displayed_and_secondary_details_are_collapsed(self):
        self.open_generator()
        self.generate()
        card = self.page.locator("#generated-recipes-container > article").first
        photo = card.locator(".recipe-photo")
        expect(photo).to_be_visible()
        self.assertTrue(photo.evaluate("image => image.complete && image.naturalWidth > 0"))
        expect(card.locator(".recipe-image-status")).to_be_hidden()
        expect(card.locator("ol li").first).to_be_visible()
        expect(card.get_by_text("Cut evenly for consistent cooking.")).to_be_hidden()
        card.locator(".recipe-more summary").focus()
        self.page.keyboard.press("Enter")
        expect(card.get_by_text("Cut evenly for consistent cooking.")).to_be_visible()
        self.assertEqual(self.page_errors, [])

    def test_missing_photo_hides_box_and_links_dish_name_to_google_images(self):
        self.page.route("**/api/images?*", lambda route: route.fulfill(status=404, json={"error": "No matching dish photo was found."}))
        self.open_generator()
        self.generate()
        card = self.page.locator("#generated-recipes-container > article").first
        expect(card.locator(".recipe-visual")).to_be_hidden()
        link = card.locator("h3 a.dish-search-link")
        expect(link).to_have_text("Pommes Anna")
        href = link.get_attribute("href")
        self.assertTrue(href.startswith("https://www.google.com/search?"))
        self.assertEqual(parse_qs(urlparse(href).query)["udm"], ["2"])
        self.assertEqual(self.page_errors, [])

    def test_save_reopen_steps_history_and_modal_keyboard(self):
        self.open_generator()
        self.generate()
        self.page.locator(".steps-link").first.click()
        expect(self.page.locator(".cooking-steps").first).to_be_focused()
        self.page.locator(".save-btn").first.click()
        expect(self.page.locator("#saved-recipes-container > article")).to_have_count(1)
        self.page.locator("#clear-history-btn").click()
        expect(self.page.locator("#history-status")).to_contain_text("0 recipes")
        expect(self.page.locator("#saved-recipes-container > article")).to_have_count(1)
        self.page.locator(".view-btn").click()
        expect(self.page.locator("#generated-recipes-container > article")).to_have_count(1)
        self.page.locator(".sub-btn").click()
        expect(self.page.locator("#substitution-modal")).to_be_visible()
        self.page.keyboard.press("Escape")
        expect(self.page.locator("#substitution-modal")).not_to_be_visible()
        expect(self.page.locator(".sub-btn")).to_be_focused()
        self.page.locator(".delete-btn").click()
        expect(self.page.locator("#saved-recipes-section")).not_to_be_visible()
        self.assertEqual(self.page_errors, [])


if __name__ == "__main__":
    unittest.main()