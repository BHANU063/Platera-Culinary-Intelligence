import copy
import io
import json
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

import server
from image_service import ImageLookupError, find_dish_image, parse_wikipedia_image, search_public_photo, select_dish_image


def recipe_fixture(name, cuisine):
    return {
        "name": name, "cuisine": cuisine, "difficulty": "Beginner", "servings": 2,
        "prepTime": "15 minutes", "cookTime": "30 minutes", "calories": 280,
        "scores": {"authenticity": 80, "practicality": 90, "ingredientUtilization": 85, "flavorComplexity": 75, "originality": 70},
        "ingredients": [{"name": "potatoes", "amount": "400 g", "optional": False}],
        "instructions": ["Cut potatoes into 2 cm pieces.", "Roast at 200 C / 392 F for 30 minutes until tender."],
        "chefTips": ["Cut evenly for consistent cooking."],
        "commonMistakes": ["Crowding the pan prevents browning."],
        "storageAdvice": "Refrigerate within 2 hours at 4 C for up to 3 days. Freeze for 1 month. Reheat to 74 C.",
        "nutrition": {"protein": "5 g", "carbohydrates": "45 g", "fat": "9 g"},
    }


def recipes_fixture():
    return {"recipes": [
        recipe_fixture("Pommes Anna", "French"),
        recipe_fixture("Nikujaga", "Japanese"),
        recipe_fixture("Batata Harra", "Lebanese"),
    ]}


def provider_fixture(data):
    text = json.dumps(data)
    midpoint = len(text) // 2
    return {"candidates": [{"finishReason": "STOP", "content": {"parts": [
        {"thought": True, "text": "Internal notes must never be returned."},
        {"text": text[:midpoint]}, {"text": text[midpoint:]},
    ]}}]}


@patch.dict("os.environ", {"GEMINI_API_KEY": ""})
class RecipeContractTests(unittest.TestCase):
    def test_schema_and_valid_recipe(self):
        server.Draft202012Validator.check_schema(server.RECIPE_SCHEMA)
        data = recipes_fixture()
        self.assertIs(server.validate_recipes(data, servings=2), data)
        self.assertIn("at least 20", server.CULINARY_INSTRUCTIONS)

    def test_invalid_nested_fields_are_rejected(self):
        cases = [
            ("servings", "2"), ("servings", True), ("calories", -10),
            ("difficulty", "easy"), ("chefTips", []), ("commonMistakes", [""]),
            ("storageAdvice", "  "), ("ingredients", ["400 g potatoes"]),
            ("ingredients", [{"name": "potatoes", "amount": "some", "optional": False}]),
            ("ingredients", [{"name": "potatoes", "amount": "400 g", "optional": "false"}]),
            ("scores", {"authenticity": 101}), ("nutrition", {"protein": "5 g"}),
            ("prepTime", "quick"),
        ]
        for field, value in cases:
            with self.subTest(field=field, value=value):
                data = recipes_fixture()
                data["recipes"][0][field] = value
                with self.assertRaises(ValueError):
                    server.validate_recipes(data)

    def test_exact_count_and_exact_fields(self):
        for count in (0, 1, 2, 4):
            with self.subTest(count=count), self.assertRaises(ValueError):
                server.validate_recipes({"recipes": [recipe_fixture("Dish", "French") for _ in range(count)]})
        data = recipes_fixture()
        data["recipes"][0]["legacyField"] = "not allowed"
        with self.assertRaises(ValueError):
            server.validate_recipes(data)

    def test_score_ranges_and_servings(self):
        for score in (-1, 101, "90", True):
            with self.subTest(score=score), self.assertRaises(ValueError):
                data = recipes_fixture()
                data["recipes"][0]["scores"]["practicality"] = score
                server.validate_recipes(data)
        with self.assertRaises(ValueError):
            server.validate_recipes(recipes_fixture(), servings=4)

    def test_batch_and_history_duplicates(self):
        for field in ("name", "cuisine"):
            data = recipes_fixture()
            data["recipes"][1][field] = data["recipes"][0][field]
            with self.subTest(field=field), self.assertRaises(ValueError):
                server.validate_recipes(data)
        for history in ([{"name": "  POMMES  ANNA!! "}], [{"name": "Another dish", "cuisine": " FRENCH "}]):
            with self.subTest(history=history), self.assertRaises(ValueError):
                server.validate_recipes(recipes_fixture(), history)

    def test_request_validation(self):
        for body in ([], {}, {"ingredients": " "}, {"ingredients": "rice", "options": []},
                     {"ingredients": "rice", "options": {"servings": True}},
                     {"ingredients": "rice", "options": {"cuisine": "Unknown"}},
                     {"ingredients": "rice", "PREVIOUS_RECIPE_HISTORY": ["invalid"]},
                     {"ingredients": "rice", "PREVIOUS_RECIPE_HISTORY": [{"name": "dish"}] * (server.MAX_HISTORY + 1)}):
            with self.subTest(body=body), self.assertRaises(ValueError):
                server.validate_request(body)

    def test_selected_cuisine_overrides_only_cuisine_diversity(self):
        data = recipes_fixture()
        for recipe in data["recipes"]:
            recipe["cuisine"] = "French"
        history = [{"name": "Ratatouille", "cuisine": "French"}]
        self.assertIs(server.validate_recipes(data, history, cuisine="French"), data)
        with self.assertRaises(ValueError):
            server.validate_recipes(data, history)
        with self.assertRaises(ValueError):
            server.validate_recipes(data, history, cuisine="Italian")
        history.append({"name": "Pommes Anna", "cuisine": "French"})
        with self.assertRaises(ValueError):
            server.validate_recipes(data, history, cuisine="French")

    @patch("server.gemini_request")
    def test_complete_contract_and_history_reach_provider(self, provider):
        provider.return_value = provider_fixture(recipes_fixture())
        history = [{"name": "Gozleme", "cuisine": "Turkish", "ingredients": ["flour"], "instructions": ["Grill for 5 minutes."]}]
        result = server.generate_recipe_response("potatoes", {"servings": 2}, history)
        self.assertEqual(result, recipes_fixture())
        payload = provider.call_args.args[1]
        sent = json.loads(payload["contents"][0]["parts"][0]["text"])
        self.assertEqual(sent["PREVIOUS_RECIPE_HISTORY"], history)
        self.assertEqual(payload["generationConfig"]["responseJsonSchema"], server.RECIPE_SCHEMA)
        self.assertEqual(payload["systemInstruction"]["parts"][0]["text"], server.CULINARY_INSTRUCTIONS)
        self.assertNotIn("Internal notes", json.dumps(result))

    @patch("server.gemini_request")
    def test_one_validation_retry_then_success(self, provider):
        invalid = copy.deepcopy(recipes_fixture())
        invalid["recipes"].pop()
        provider.side_effect = [provider_fixture(invalid), provider_fixture(recipes_fixture())]
        self.assertEqual(server.generate_recipe_response("potatoes", {}, []), recipes_fixture())
        self.assertEqual(provider.call_count, 2)

    @patch("server.gemini_request")
    def test_validation_retries_are_bounded(self, provider):
        provider.return_value = provider_fixture({"recipes": []})
        with self.assertRaises(RuntimeError):
            server.generate_recipe_response("potatoes", {}, [])
        self.assertEqual(provider.call_count, 2)

    def test_blocked_and_truncated_responses_are_rejected(self):
        for result in ({}, {"candidates": []}, {"candidates": [{"finishReason": "MAX_TOKENS"}]}):
            with self.subTest(result=result), self.assertRaises(RuntimeError):
                server.response_parts(result)

    @patch("server.GEMINI_API_KEY", "test-only-key")
    @patch("server.urlopen")
    def test_credentials_are_headers_not_urls(self, request_mock):
        request_mock.return_value.__enter__.return_value.read.return_value = b'{}'
        server.gemini_request("test-model", {})
        request = request_mock.call_args.args[0]
        self.assertNotIn("test-only-key", request.full_url)
        self.assertEqual(request.get_header("X-goog-api-key"), "test-only-key")

    @patch.dict("os.environ", {"GEMINI_API_KEY": " test-environment-key "})
    @patch("server.GEMINI_API_KEY", "test-legacy-key")
    @patch("server.urlopen")
    def test_standard_environment_key_takes_precedence(self, request_mock):
        request_mock.return_value.__enter__.return_value.read.return_value = b'{}'
        server.gemini_request("test-model", {})
        self.assertEqual(request_mock.call_args.args[0].get_header("X-goog-api-key"), "test-environment-key")

    @patch("server.GEMINI_API_KEY", "test-only-key")
    @patch("server.urlopen")
    def test_provider_errors_do_not_expose_secrets(self, request_mock):
        request_mock.side_effect = HTTPError("https://example.test/?key=test-only-key", 429, "test-only-key", {}, io.BytesIO(b"secret"))
        with self.assertRaisesRegex(RuntimeError, "quota") as error:
            server.gemini_request("test-model", {})
        self.assertNotIn("test-only-key", str(error.exception))
        request_mock.assert_called_once()

    @patch("server.FALLBACK_MODELS", ("backup-model",))
    @patch("server.GEMINI_API_KEY", "test-only-key")
    @patch("server.urlopen")
    def test_unavailable_model_automatically_uses_backup(self, request_mock):
        expected = provider_fixture(recipes_fixture())
        for status in (404, 500, 502, 503, 504):
            with self.subTest(status=status):
                request_mock.reset_mock()
                request_mock.side_effect = [
                    HTTPError("https://example.test", status, "Unavailable", {}, io.BytesIO()),
                    io.BytesIO(json.dumps(expected).encode("utf-8")),
                ]
                self.assertEqual(server.gemini_request("test-model", {}), expected)
                self.assertEqual(request_mock.call_count, 2)
                self.assertIn("/backup-model:generateContent", request_mock.call_args.args[0].full_url)

    @patch("server.FALLBACK_MODELS", ("test-model", "backup-model", "backup-model", "last-model", "extra-model"))
    @patch("server.GEMINI_API_KEY", "test-only-key")
    @patch("server.urlopen")
    def test_failover_is_deduplicated_and_bounded(self, request_mock):
        request_mock.side_effect = [HTTPError("https://example.test", 503, "Unavailable", {}, io.BytesIO()) for _ in range(3)]
        with self.assertRaises(server.ProviderUnavailable):
            server.gemini_request("test-model", {})
        self.assertEqual(request_mock.call_count, 3)
        self.assertEqual(len({call.args[0].full_url for call in request_mock.call_args_list}), 3)

    @patch("server.GEMINI_API_KEY", "test-only-key")
    @patch("server.urlopen")
    def test_authentication_failure_is_not_retried(self, request_mock):
        request_mock.side_effect = HTTPError("https://example.test", 401, "Unauthorized", {}, io.BytesIO())
        with self.assertRaisesRegex(RuntimeError, "authentication"):
            server.gemini_request("test-model", {})
        request_mock.assert_called_once()

    @patch("server.FALLBACK_MODELS", ("backup-model",))
    @patch("server.GEMINI_API_KEY", "test-only-key")
    @patch("server.monotonic", side_effect=[0, 0, 111])
    @patch("server.urlopen")
    def test_failover_respects_request_budget(self, request_mock, clock):
        request_mock.side_effect = HTTPError("https://example.test", 503, "Unavailable", {}, io.BytesIO())
        with self.assertRaises(server.ProviderUnavailable):
            server.gemini_request("test-model", {})
        request_mock.assert_called_once()


class DishImageTests(unittest.TestCase):
    @patch("image_service.scrape_dish_image", side_effect=ImageLookupError("Google requires verification."))
    @patch("image_service.urlopen")
    def test_title_case_fallback_and_cache(self, lookup, google_lookup):
        find_dish_image.cache_clear()
        self.addCleanup(find_dish_image.cache_clear)
        response = lookup.return_value
        response.__enter__.return_value.read.return_value = b'<title>Batata harra - Wikipedia</title><meta property="og:image" content="https://upload.wikimedia.org/wikipedia/commons/6/6f/Batata_harra.jpg">'
        lookup.side_effect = [HTTPError("https://en.wikipedia.org/wiki/Batata_Harra", 404, "Not Found", {}, io.BytesIO()), response]
        photo = find_dish_image("Batata Harra", "Lebanese")
        self.assertEqual(photo["dish"], "Batata Harra")
        self.assertEqual(photo["sourceUrl"], "https://en.wikipedia.org/wiki/Batata_harra")
        self.assertEqual(find_dish_image("Batata Harra", "Lebanese"), photo)
        self.assertEqual(lookup.call_count, 2)
        google_lookup.assert_called_once_with("Batata Harra", "Lebanese")

    @patch("image_service.scrape_dish_image", side_effect=ImageLookupError("Google requires verification."))
    @patch("image_service.urlopen")
    def test_blocked_wikipedia_is_not_retried(self, lookup, google_lookup):
        find_dish_image.cache_clear()
        self.addCleanup(find_dish_image.cache_clear)
        lookup.side_effect = HTTPError("https://en.wikipedia.org/wiki/Batata_Harra", 403, "Forbidden", {}, io.BytesIO())
        with self.assertRaises(ImageLookupError):
            find_dish_image("Batata Harra", "Lebanese")
        lookup.assert_called_once()

    def test_google_photo_must_match_dish(self):
        results = [{"imageUrl": "https://images.example.test/potatoes.jpg", "sourceUrl": "https://recipes.example.test/pommes-anna", "title": "Pommes Anna recipe", "width": 400, "height": 300}]
        photo = select_dish_image(results, "Pommes Anna", "https://www.google.com/search?q=Pommes+Anna")
        self.assertEqual(photo["sourceUrl"], results[0]["sourceUrl"])
        with self.assertRaises(ImageLookupError):
            select_dish_image(results, "Yakitori", "https://www.google.com/search?q=Yakitori")

    def test_wikipedia_photo_and_attribution(self):
        html = '<html><head><title>Pommes Anna - Wikipedia</title><meta property="og:image" content="https://upload.wikimedia.org/wikipedia/commons/4/48/Pommes_Anna.jpg?tracking=unused"></head><body><table class="infobox"><tr><td><a class="mw-file-description" href="./File:Pommes_Anna.jpg">Photo</a></td></tr></table></body></html>'
        photo = parse_wikipedia_image(html, "Pommes Anna", "https://en.wikipedia.org/wiki/Pommes_Anna")
        self.assertEqual(photo["imageUrl"], "https://upload.wikimedia.org/wikipedia/commons/4/48/Pommes_Anna.jpg")
        self.assertIn("File:Pommes_Anna.jpg", photo["attributionUrl"])
        with self.assertRaises(ImageLookupError):
            parse_wikipedia_image(html, "Yakitori", "https://en.wikipedia.org/wiki/Pommes_Anna")

    @staticmethod
    def wikimedia_responses(wikipedia, commons):
        def respond(request, **kwargs):
            if isinstance(wikipedia, Exception) and "wikipedia.org/w/api.php" in request.full_url:
                raise wikipedia
            pages = wikipedia if "wikipedia.org/w/api.php" in request.full_url else commons
            return io.BytesIO(json.dumps({"query": {"pages": pages}}).encode())
        return respond

    @staticmethod
    def commons_page(index, title):
        return {"title": title, "index": index, "imageinfo": [{"mime": "image/jpeg", "thumburl": "https://upload.wikimedia.org/thumb.jpg", "descriptionurl": "https://commons.wikimedia.org/wiki/" + title.replace(" ", "_")}]}

    @patch("image_service.urlopen")
    def test_search_picks_the_closest_title_not_the_first_result(self, lookup):
        wikipedia = {"1": {"title": "Palak paneer", "index": 1, "thumbnail": {"source": "https://upload.wikimedia.org/palak.jpg"}, "fullurl": "https://en.wikipedia.org/wiki/Palak_paneer"}}
        commons = {"2": self.commons_page(1, "File:Bocadillo de Tortilla de patatas - 34.jpg"), "3": self.commons_page(2, "File:Tortilla Española.jpg")}
        lookup.side_effect = self.wikimedia_responses(wikipedia, commons)
        photo = search_public_photo("Tortilla Espanola de Patatas")
        self.assertEqual(photo["sourceTitle"], "Tortilla Española")
        self.assertTrue(photo["sourceUrl"].startswith("https://commons.wikimedia.org/"))
        with self.assertRaises(ImageLookupError):
            search_public_photo("Aloo Palak with Egg")

    @patch("image_service.urlopen")
    def test_search_uses_a_source_when_the_other_is_rate_limited(self, lookup):
        limited = HTTPError("https://en.wikipedia.org/w/api.php", 429, "Too Many Requests", {}, io.BytesIO())
        lookup.side_effect = self.wikimedia_responses(limited, {"1": self.commons_page(1, "File:Kuku Sabzi Platter.jpg")})
        self.assertEqual(search_public_photo("Kuku Sabzi")["sourceTitle"], "Kuku Sabzi Platter")
        lookup.side_effect = self.wikimedia_responses(limited, {})
        with self.assertRaisesRegex(ImageLookupError, "temporarily unavailable"):
            search_public_photo("Kuku Sabzi")

    @patch("image_service.urlopen")
    def test_search_rejects_non_wikimedia_image_hosts(self, lookup):
        page = self.commons_page(1, "File:Kuku Sabzi.jpg")
        page["imageinfo"][0]["thumburl"] = "https://tracker.example.test/kuku.jpg"
        lookup.side_effect = self.wikimedia_responses({}, {"1": page})
        with self.assertRaises(ImageLookupError):
            search_public_photo("Kuku Sabzi")

    def test_wikimedia_thumbnail_host_and_infobox(self):
        html = '<html><head><title>Nikujaga - Wikipedia</title></head><body><table class="infobox"><tr><td><img src="//thumb.wikimedia.org/wikipedia/commons/thumb/test.jpg" data-file-type="bitmap"></td></tr></table></body></html>'
        self.assertTrue(parse_wikipedia_image(html, "Nikujaga", "https://en.wikipedia.org/wiki/Nikujaga")["imageUrl"].startswith("https://thumb.wikimedia.org/"))


@patch.dict("os.environ", {"GEMINI_API_KEY": ""})
class GatewayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.http_server = server.ThreadingHTTPServer(("127.0.0.1", 0), server.PlateraHandler)
        cls.base_url = f"http://127.0.0.1:{cls.http_server.server_port}"
        cls.thread = threading.Thread(target=cls.http_server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.http_server.shutdown()
        cls.http_server.server_close()
        cls.thread.join()

    def test_secrets_and_source_are_not_served(self):
        for path in ("/.env", "/%2eenv", "/server.py", "/test_server.py", "/__pycache__/server.pyc"):
            with self.subTest(path=path), self.assertRaises(HTTPError) as error:
                urlopen(self.base_url + path)
            self.assertEqual(error.exception.code, 404)
            error.exception.close()
        for path in ("/", "/js/main.js", "/css/style.css"):
            with urlopen(self.base_url + path) as response:
                self.assertEqual(response.status, 200)

    @patch("server.gemini_request")
    def test_invalid_request_does_not_call_provider(self, provider):
        request = Request(self.base_url + "/api/recipes", data=b'{"ingredients": ""}', headers={"Content-Type": "application/json"})
        with self.assertRaises(HTTPError) as error:
            urlopen(request)
        self.assertEqual(error.exception.code, 400)
        error.exception.close()
        provider.assert_not_called()

    @patch("server.gemini_request")
    def test_http_contract(self, provider):
        provider.return_value = provider_fixture(recipes_fixture())
        request = Request(self.base_url + "/api/recipes", data=b'{"ingredients": "potatoes"}', headers={"Content-Type": "application/json"})
        with urlopen(request) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(json.load(response), recipes_fixture())

    @patch("server.GEMINI_API_KEY", "")
    def test_missing_key_is_an_error_not_local_recipes(self):
        request = Request(self.base_url + "/api/recipes", data=b'{"ingredients": "potatoes"}', headers={"Content-Type": "application/json"})
        with self.assertRaises(HTTPError) as error:
            urlopen(request)
        self.assertEqual(error.exception.code, 503)
        self.assertIn("GEMINI_API_KEY", json.load(error.exception)["error"])
        error.exception.close()

    @patch("server.find_dish_image")
    @patch("server.GEMINI_API_KEY", "")
    def test_web_photos_work_without_gemini(self, lookup):
        lookup.return_value = {"dish": "Pommes Anna", "imageUrl": "https://images.example.test/pommes.jpg", "sourceUrl": "https://recipes.example.test/pommes"}
        with urlopen(self.base_url + "/api/images?dish=Pommes%20Anna&cuisine=French") as response:
            self.assertEqual(json.load(response), lookup.return_value)
        lookup.assert_called_once_with("Pommes Anna", "French")

    def test_cuisine_choices(self):
        with urlopen(self.base_url + "/api/cuisines") as response:
            self.assertEqual(json.load(response)["cuisines"], list(server.CUISINES))


if __name__ == "__main__":
    unittest.main()