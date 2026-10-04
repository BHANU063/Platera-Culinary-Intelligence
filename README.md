# PLATERA

### Serving culinary intelligence for the imperfect pantry.

🌐 **Live App**: https://platera-culinary-intelligence.onrender.com/

Platera turns the ingredients you already own into **three complete recipe ideas**. Choose a cuisine or explore different cuisines, compare estimated quality scores, follow numbered cooking steps, and keep useful recipes in a personal recipe box.

> **The idea:** better cooking intelligence should begin with the food already in the room.

[Explore the workspace](#quick-start) · [Read the architecture](EXPLANATIONS.md) · [See the research direction](#why-this-matters)

---

## The Experience

```text
                 ingredients + cuisine
                         │
                         ▼
              ┌─────────────────────┐
              │   PLATERA ENGINE    │
              │ parse → adapt →     │
              │ explain → remember  │
              └──────────┬──────────┘
                         ▼
       three validated recipes + attributed dish photos
```

Platera produces three different dishes instead of one supposedly perfect answer. Automatic cuisine selection requires different cuisines; an explicit choice keeps all three recipes in that cuisine. Recent history helps avoid repeated dishes.

Each recipe includes measured ingredients, optional additions, preparation and cooking times, 5 to 8 short numbered steps, estimated nutrition, and a collapsed **More details** section with chef tips, common mistakes, storage advice, and five AI-estimated quality scores. These estimates are not laboratory measurements or evidence of kitchen testing.

## Why This Matters

Food waste is often a planning problem disguised as a cooking problem. Platera is a small experiment in **human-centered AI**: how can an intelligent system make its reasoning legible, respect constraints, and help a person make a decision rather than simply generate more text?

That makes this a useful hobby project and a foundation for deeper work in:

- constraint-aware generation and preference modeling
- explainable recommendations and human control
- sustainable computing and food-waste reduction
- evaluation of usefulness, not just output fluency

## Features

| Feature | What it adds |
| --- | --- |
| Ingredient-first generation | Three complete recipes validated on the server and in the browser |
| Cuisine selection | 39 choices plus automatic exploration |
| Secure AI gateway | Credentials stay on the Python server, never in browser code |
| Model failover | Retired or overloaded models are skipped within bounded attempts and time limits |
| Referenced dish photos | Exact Wikipedia page, then Wikipedia/Wikimedia Commons search with strict title matching, with source and license links |
| No-photo fallback | If no matching photo exists, the empty box is hidden and the dish name links to Google Images |
| Cooking guidance | Short numbered steps, estimated nutrition, and collapsible tips, mistakes, storage and scores |
| Recipe history | Remembers up to 60 recipe summaries; clearing history preserves saved dishes |
| Recipe box | Save, reopen, and delete recipes; session-only saving remains available if storage fails |
| Recovery | Keeps ingredients and previous results visible, with retry and saved-recipe access |
| Original visual identity | Playfair Display, Roboto, teal controls, and responsive recipe cards |

## Quick Start

Use Python 3.10 or newer. Run these commands from the root folder in PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
```

Create a local `.env` file in the root and set `GEMINI_API_KEY` to your key there. Keep the actual key private; never put it in frontend files, screenshots, or chat. The file is git-ignored and cannot be served by the gateway. An existing environment variable takes precedence over `.env`.

```powershell
.\.venv\Scripts\python.exe server.py
```

Open **https://platera-culinary-intelligence.onrender.com/** to use the live application, or run locally with **http://localhost:4173** (or the port printed in the terminal). Enter ingredients, choose a cuisine, and select **Generate Recipes**. The browser currently requests two servings with no dietary restriction. The API also accepts serving, diet, and time constraints.

The Python gateway is required. Opening the HTML directly or using a static-only server does not provide recipe or photo APIs. Internet access is required for Gemini, photo sources, Tailwind CDN, and fonts. No Node.js build or Firebase account is needed.

### Model Configuration

An API key belongs to a Google project, not to a specific Gemini model. `GEMINI_RECIPE_MODEL` defaults to `gemini-2.5-flash`. Google may list that model while rejecting generation for new users. `GEMINI_FALLBACK_MODELS` defaults to `gemini-3.8-flash,gemini-3.5-flash-lite` so retirement and temporary overload can recover automatically with the same key, subject to its permissions and quota.

Each provider pass tries at most three distinct models within a 110-second scheduling budget, with at most 90 seconds per network operation. Invalid recipe output receives at most one additional generation pass. Authentication, permission, and quota failures are not blindly retried. Set `GEMINI_FALLBACK_MODELS` to an empty value to disable alternate models. Restart after changing configuration.

Set `PORT` to use another free port. The development server binds only to `127.0.0.1`. There are no fabricated local recipes during an outage: saved dishes and previous results remain accessible while generation offers recovery.

### Dish Photos

Photos come from public sources only. Google Images is tried first, but Google normally answers automated requests with a verification page, which Platera never bypasses and then pauses for 30 minutes. The fallback is the dish's Wikipedia page, then a Wikipedia and Wikimedia Commons search where a photo is accepted only if its title closely matches the dish name. A wrong photo is treated as worse than none. Wikimedia can rate-limit repeated requests, so a lookup may occasionally fail temporarily.

### Verification

```powershell
.\.venv\Scripts\python.exe -m unittest test_server test_ui -v
```

Tests cover recipe contracts, history, cuisine selection, key handling, failover, static-file protection, photo matching and fallback, and isolated Chromium workflows (38 tests). Provider responses are mocked in automated tests; live generation depends on Google's current availability and project quota.

## Project Map

```text
index.html              experience and accessible page structure
css/style.css           visual language, responsive layout, motion, components
js/main.js              UI state, rendering, local recipe box, modal interactions
js/aiService.js         validated recipe API adapter and bounded recent history
js/config.js            browser-safe API route configuration
server.py               required local gateway, model failover, recipe validation
image_service.py        attributed dish-photo lookup and caching
test_server.py          backend, provider, security, and photo regression tests
test_ui.py              isolated Chromium workflow regression tests
requirements.txt        Python dependencies
EXPLANATIONS.md         full technical walkthrough and data flow
PLATERA APP/            archived compatibility snapshot, delegated to root modules
```

## Safety and Privacy

The maintained frontend contains no provider credentials. Generating recipes sends the entered ingredients, selected constraints, and recent recipe summaries through the local gateway to Google. Dish names and cuisines are sent to public photo sources. Saved recipes and history are stored in the browser; there is no application account or cloud recipe database.

Check allergies, safe temperatures, storage, and ingredient substitutions yourself. Photos are references, not pictures of an actual cooked result; consult their source and license links. The included Python HTTP server is for local development, not a hardened public deployment. Public hosting requires authentication, rate limits, HTTPS, and an appropriate application server.

## Roadmap

- Add a small evaluation set for pantry coverage, dietary compliance, and perceived usefulness.
- Evaluate generated recipes against reviewed reference recipes using the same structured output contract.
- Compare nutrition estimates against a verified food-composition database.
- Study whether showing trade-offs changes food waste and user confidence over repeated use.

## Made For

A portfolio project with a real question underneath it: **can AI be helpful without making the human decision disappear?**

Built with care by the Platera project team.
