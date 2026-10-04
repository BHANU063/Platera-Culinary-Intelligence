import { generateRecipes, getRecipeHistory, clearRecipeHistory } from './aiService.js';

const $ = selector => document.querySelector(selector);
const state = { recipes: [], saved: readSavedRecipes(), cuisinesLoaded: false };
const photoRequests = new Map();
let toastTimer;
let recipeCardCount = 0;
const escapeHtml = value => String(value).replace(/[&<>'"]/g, character => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', "'":'&#39;', '"':'&quot;' }[character]));
const ingredientLabel = ingredient => typeof ingredient === 'string' ? ingredient : `${ingredient.optional ? 'OPTIONAL: ' : ''}${ingredient.amount} ${ingredient.name}`;

function readSavedRecipes() {
  try {
    const saved = JSON.parse(localStorage.getItem('platera-recipes') || '[]');
    const text = value => typeof value === 'string' && value.trim().length > 0;
    return Array.isArray(saved) ? saved.filter(recipe => recipe && text(recipe.name) &&
      Array.isArray(recipe.ingredients) && recipe.ingredients.length > 0 && recipe.ingredients.every(ingredient =>
        text(ingredient) || (ingredient && text(ingredient.name) && text(ingredient.amount))) &&
      Array.isArray(recipe.instructions) && recipe.instructions.length > 0 && recipe.instructions.every(text)) : [];
  } catch {
    return [];
  }
}

function safeWebUrl(value) {
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && !url.username && !url.password ? url.href : '';
  } catch {
    return '';
  }
}

function safeImageUrl(value) {
  return safeWebUrl(value) || (typeof value === 'string' && /^data:image\/(?:jpeg|png|webp);base64,[A-Za-z0-9+/=]+$/.test(value) ? value : '');
}

async function imageFor(recipe) {
  if (recipe.photo?.dish === recipe.name && safeImageUrl(recipe.photo.imageUrl) && safeWebUrl(recipe.photo.sourceUrl)) return recipe.photo;
  const endpoint = window.PLATERA_CONFIG?.imageEndpoint;
  if (!endpoint) throw new Error('The dish photo service is not configured.');
  const query = new URLSearchParams({ dish: recipe.name, cuisine: recipe.cuisine || recipe.tag || '' });
  const key = query.toString();
  if (!photoRequests.has(key)) {
    const request = fetch(`${endpoint}?${query}`, { signal: AbortSignal.timeout(70000) }).then(async response => {
      const photo = await response.json();
      if (!response.ok) throw new Error(photo.error || 'No matching dish photo was found.');
      if (photo.dish !== recipe.name || !safeImageUrl(photo.imageUrl) || !safeWebUrl(photo.sourceUrl)) throw new Error('No matching dish photo was found.');
      return photo;
    }).catch(error => {
      photoRequests.delete(key);
      throw error;
    });
    photoRequests.set(key, request);
  }
  return photoRequests.get(key);
}

function googleImagesUrl(recipe) {
  return `https://www.google.com/search?${new URLSearchParams({ q: `${recipe.name} ${recipe.cuisine || ''} dish`, udm: '2', safe: 'active' })}`;
}

function recipeImage(recipe, className) {
  const search = googleImagesUrl(recipe);
  return `<figure class="recipe-visual ${className}"><img class="recipe-photo" hidden alt="${escapeHtml(`Reference photo of ${recipe.name}`)}" decoding="async" referrerpolicy="no-referrer"><p class="recipe-image-status" role="status">Finding dish photo...</p><figcaption class="recipe-image-caption"><span class="recipe-image-label" hidden>Reference photo</span><a class="recipe-image-source" target="_blank" rel="noopener noreferrer" hidden>Source</a><a class="recipe-image-credit" target="_blank" rel="noopener noreferrer" hidden>Photo credit &amp; license</a><a class="recipe-image-search" href="${escapeHtml(search)}" target="_blank" rel="noopener noreferrer">Google Images</a></figcaption></figure>`;
}

async function connectImageState(card, recipe) {
  const image = card.querySelector('.recipe-photo');
  const status = card.querySelector('.recipe-image-status');
  const label = card.querySelector('.recipe-image-label');
  const showFailure = () => {
    card.querySelector('.recipe-visual').hidden = true;
    const title = card.querySelector('h3');
    if (!title.querySelector('a')) title.innerHTML = `<a class="dish-search-link underline underline-offset-4 decoration-teal-600 hover:text-teal-700" href="${escapeHtml(googleImagesUrl(recipe))}" target="_blank" rel="noopener noreferrer" title="Search Google Images for this dish">${escapeHtml(recipe.name)}</a>`;
  };
  try {
    const photo = await imageFor(recipe);
    recipe.photo = photo;
    const saved = state.saved.find(item => item.name === recipe.name && item.cuisine === recipe.cuisine);
    if (saved) {
      saved.photo = photo;
      try { localStorage.setItem('platera-recipes', JSON.stringify(state.saved)); } catch { console.warn('The photo source could not be saved.'); }
    }
    const source = card.querySelector('.recipe-image-source');
    source.href = safeWebUrl(photo.sourceUrl);
    source.title = photo.sourceTitle || recipe.name;
    source.hidden = false;
    const credit = card.querySelector('.recipe-image-credit');
    const attribution = safeWebUrl(photo.attributionUrl);
    if (attribution) { credit.href = attribution; credit.hidden = false; }
    let thumbnailTried = false;
    image.addEventListener('load', () => { image.hidden = false; status.hidden = true; label.hidden = false; });
    image.addEventListener('error', () => {
      const thumbnail = safeImageUrl(photo.thumbnailUrl);
      if (!thumbnailTried && thumbnail && thumbnail !== image.src) {
        thumbnailTried = true;
        image.src = thumbnail;
      } else {
        showFailure();
      }
    });
    image.src = safeImageUrl(photo.imageUrl);
  } catch {
    showFailure();
  }
}

async function loadCuisineOptions() {
  try {
    const response = await fetch(window.PLATERA_CONFIG?.cuisineEndpoint || '/api/cuisines', { signal: AbortSignal.timeout(10000) });
    const data = await response.json();
    if (!response.ok || !Array.isArray(data.cuisines) || !data.cuisines.length || !data.cuisines.every(cuisine => typeof cuisine === 'string' && cuisine.trim())) throw new Error();
    data.cuisines.forEach(cuisine => $('#cuisine-select').add(new Option(cuisine, cuisine)));
    state.cuisinesLoaded = true;
    $('#cuisine-select').disabled = $('#generate-btn').disabled;
  } catch {
    showToast('Cuisine choices are unavailable. Automatic selection is still available.');
  }
}

function updateHistoryControls() {
  const count = getRecipeHistory().length;
  $('#history-status').textContent = `Recent history: ${count} recipes`;
  $('#clear-history-btn').disabled = count === 0 || $('#generate-btn').disabled;
}

function showError(message) { window.clearTimeout(toastTimer); const display = $('#error-display'); display.setAttribute('role', 'alert'); display.className = 'max-w-2xl mx-auto mt-4 p-4 bg-red-100 text-red-700 rounded-lg shadow-sm'; display.textContent = message; display.style.display = 'block'; }
function clearError() { $('#error-display').style.display = 'none'; }
function showRecovery(message) {
  window.clearTimeout(toastTimer);
  const display = $('#error-display');
  display.setAttribute('role', 'status');
  display.className = 'max-w-2xl mx-auto mt-4 p-4 bg-white text-stone-700 rounded-lg shadow-sm space-y-3';
  const description = document.createElement('p');
  description.textContent = message;
  const actions = document.createElement('div');
  actions.className = 'flex flex-wrap items-center gap-4';
  const retry = document.createElement('button');
  retry.type = 'button';
  retry.className = 'px-4 py-2 bg-teal-600 hover:bg-teal-700 text-white rounded-lg';
  retry.textContent = 'Try Again';
  retry.addEventListener('click', handleGenerate);
  actions.appendChild(retry);
  if (state.saved.length) {
    const saved = document.createElement('a');
    saved.href = '#saved-recipes-section';
    saved.className = 'text-teal-700 underline';
    saved.textContent = 'View Saved Recipes';
    actions.appendChild(saved);
  }
  display.replaceChildren(description, actions);
  display.style.display = 'block';
}
function setLoading(isLoading) { $('#loader').style.display = isLoading ? 'block' : 'none'; $('#generate-btn').disabled = isLoading; $('#generated-recipes-section').setAttribute('aria-busy', String(isLoading)); $('#cuisine-select').disabled = isLoading || !state.cuisinesLoaded; updateHistoryControls(); }
function showToast(message) { window.clearTimeout(toastTimer); const display = $('#error-display'); display.setAttribute('role', 'status'); display.className = 'max-w-2xl mx-auto mt-4 p-4 bg-teal-100 text-teal-800 rounded-lg shadow-sm'; display.textContent = message; display.style.display = 'block'; toastTimer = window.setTimeout(clearError, 5000); }
function saveRecipes(recipes) {
  let persisted = true;
  try {
    localStorage.setItem('platera-recipes', JSON.stringify(recipes));
  } catch {
    persisted = false;
  }
  state.saved = recipes;
  displaySavedRecipes();
  if (!persisted) showToast('Recipe box updated for this visit only. Browser storage is unavailable.');
  return true;
}
function recipeDetails(recipe) { $('#ingredient-select').innerHTML = recipe.ingredients.map(ingredient => `<option value="${escapeHtml(typeof ingredient === 'string' ? ingredient : ingredient.name)}">${escapeHtml(ingredientLabel(ingredient))}</option>`).join(''); $('#substitutions-result').innerHTML = ''; $('#substitution-modal').showModal(); }
function openWine(recipe) { $('#wine-pairing-result').innerHTML = '<p class="text-stone-600">Consulting the sommelier...</p>'; $('#wine-modal').showModal(); window.setTimeout(() => { const bold = recipe.name.toLowerCase().includes('broth') ? 'Sauvignon Blanc' : 'Gamay'; $('#wine-pairing-result').innerHTML = `<div class="border-b pb-4"><h4 class="font-bold text-lg text-red-800">Red Wine Pairing</h4><p class="font-semibold">${bold}</p><p class="text-sm text-stone-600">Its fresh acidity keeps the dish bright without overpowering the pantry ingredients.</p></div><div><h4 class="font-bold text-lg text-blue-800">White Wine Pairing</h4><p class="font-semibold">Albariño</p><p class="text-sm text-stone-600">Citrus-led freshness mirrors the recipe's herbs and bright finishing notes.</p></div>`; }, 450); }
function createRecipeCard(recipe) {
  const item = document.createElement('article');
  const stepsId = `recipe-steps-${++recipeCardCount}`;
  item.className = 'recipe-card bg-white rounded-xl shadow-strong p-6 md:p-8';
  const list = values => (Array.isArray(values) ? values : []).map(value => `<li>${escapeHtml(value)}</li>`).join('');
  const chefBody = recipe.chefTips ? `<div class="grid grid-cols-1 md:grid-cols-3 gap-6 mt-4"><div><h4 class="text-xl font-semibold text-stone-700 playfair-display mb-2">Chef Tips</h4><ul class="list-disc list-inside text-stone-600 space-y-1">${list(recipe.chefTips)}</ul></div><div><h4 class="text-xl font-semibold text-stone-700 playfair-display mb-2">Common Mistakes</h4><ul class="list-disc list-inside text-stone-600 space-y-1">${list(recipe.commonMistakes || recipe.mistakesToAvoid)}</ul></div><div><h4 class="text-xl font-semibold text-stone-700 playfair-display mb-2">Storage Advice</h4><p class="text-stone-600">${escapeHtml(recipe.storageAdvice || recipe.storage || '')}</p></div></div>` : '';
  const scoreLabels = { authenticity: 'Authenticity', practicality: 'Practicality', ingredientUtilization: 'Ingredient use', flavorComplexity: 'Flavor complexity', originality: 'Originality' };
  const scores = recipe.scores ? `<p class="text-xs text-stone-500 mt-4">AI-estimated scores: ${Object.entries(scoreLabels).map(([key, label]) => `${label} ${escapeHtml(recipe.scores[key])}/100`).join(' | ')}</p>` : '';
  const chefSection = (chefBody || scores) ? `<details class="recipe-more mt-6 pt-4 border-t"><summary class="cursor-pointer text-teal-700 font-medium">More details: tips, mistakes, storage, scores</summary>${chefBody}${scores}</details>` : '';
  const nutrition = recipe.nutrition ? `<p class="text-sm text-stone-600 mt-3">Per serving (est.): ${escapeHtml(recipe.calories)} kcal | Protein ${escapeHtml(recipe.nutrition.protein)} | Carbs ${escapeHtml(recipe.nutrition.carbohydrates)} | Fat ${escapeHtml(recipe.nutrition.fat)}</p>` : '';
  const metadata = [recipe.cuisine || recipe.tag || 'Platera recipe', recipe.servings ? `${recipe.servings} servings` : '', recipe.prepTime ? `Prep: ${recipe.prepTime}` : recipe.time, recipe.cookTime ? `Cook: ${recipe.cookTime}` : '', recipe.difficulty].filter(Boolean).map(escapeHtml).join(' | ');
  item.innerHTML = `${recipeImage(recipe, 'generated-recipe-image rounded-lg')}<div class="flex justify-between items-start mb-4 gap-4"><h3 class="min-w-0 flex-1 text-2xl md:text-3xl font-bold text-stone-800 playfair-display">${escapeHtml(recipe.name)}</h3><button class="save-btn shrink-0 inline-flex items-center px-4 py-2 bg-emerald-500 hover:bg-emerald-600 text-white font-medium rounded-lg shadow-md transition-colors duration-300">Save</button></div><p class="text-sm text-teal-700 font-medium mb-1">${metadata}</p><a class="steps-link inline-block mt-2 text-teal-700 font-medium underline underline-offset-4" href="#${stepsId}">Cooking Steps</a>${nutrition}<div class="grid grid-cols-1 md:grid-cols-3 gap-8 mt-5"><div class="md:col-span-1 min-w-0"><div class="flex flex-wrap justify-between items-center gap-2 mb-2"><h4 class="text-xl font-semibold text-stone-700 playfair-display">Ingredients</h4><button class="sub-btn text-xs text-teal-600 hover:underline">Find Substitute</button></div><ul class="list-disc list-inside text-stone-600 space-y-1">${list(recipe.ingredients.map(ingredientLabel))}</ul></div><div class="md:col-span-2 min-w-0"><h4 id="${stepsId}" tabindex="-1" class="cooking-steps text-xl font-semibold text-stone-700 mb-2 playfair-display">Cooking Steps</h4><ol class="list-decimal pl-5 text-stone-600 space-y-3 leading-relaxed">${list(recipe.instructions)}</ol></div></div>${chefSection}<div class="text-center mt-6 pt-4 border-t"><button class="wine-btn inline-flex items-center px-4 py-2 bg-indigo-600 hover:bg-indigo-700 text-white font-medium rounded-lg shadow-md transition-colors duration-300">Suggest Wine Pairing</button></div>`;
  connectImageState(item, recipe);
  item.querySelector('.steps-link').addEventListener('click', event => {
    event.preventDefault();
    const heading = item.querySelector('.cooking-steps');
    heading.focus({ preventScroll: true });
    heading.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });
  item.querySelector('.save-btn').addEventListener('click', event => {
    if (state.saved.some(saved => saved.name === recipe.name)) { showToast('This dish is already in your recipe box.'); return; }
    if (saveRecipes([...state.saved, recipe])) { event.currentTarget.textContent = 'Saved!'; event.currentTarget.disabled = true; }
  });
  item.querySelector('.sub-btn').addEventListener('click', () => recipeDetails(recipe));
  item.querySelector('.wine-btn').addEventListener('click', () => openWine(recipe));
  return item;
}

function displayGeneratedRecipes(recipes) {
  const container = $('#generated-recipes-container');
  container.innerHTML = '';
  recipes.forEach(recipe => container.appendChild(createRecipeCard(recipe)));
  $('#generated-recipes-section').style.display = 'block';
  $('#generated-recipes-section').scrollIntoView({ behavior: 'smooth' });
}

function displaySavedRecipes(recipes = state.saved) {
  const section = $('#saved-recipes-section');
  const container = $('#saved-recipes-container');
  container.innerHTML = '';
  section.style.display = recipes.length ? 'block' : 'none';
  recipes.forEach(recipe => {
    const card = document.createElement('article');
    card.className = 'recipe-card bg-white rounded-xl overflow-hidden shadow-strong shadow-strong-hover transition-all duration-300 flex flex-col';
    const summary = recipe.cuisine ? `${recipe.cuisine} | ${recipe.servings} servings | Prep: ${recipe.prepTime} | Cook: ${recipe.cookTime}` : recipe.summary || 'Saved from your Platera kitchen.';
    card.innerHTML = `${recipeImage(recipe, 'recipe-image')}<div class="p-6 flex-grow flex flex-col"><h3 class="text-2xl font-bold text-stone-800 mb-3 playfair-display flex-grow">${escapeHtml(recipe.name)}</h3><p class="text-stone-600 text-sm mb-4">${escapeHtml(summary)}</p><div class="flex flex-wrap justify-between gap-3"><button class="view-btn px-4 py-2 bg-teal-600 hover:bg-teal-700 text-white font-medium rounded-lg">View Recipe</button><button class="delete-btn px-4 py-2 bg-red-500 hover:bg-red-600 text-white font-medium rounded-lg">Delete</button></div></div>`;
    connectImageState(card, recipe);
    card.querySelector('.view-btn').addEventListener('click', () => displayGeneratedRecipes([recipe]));
    card.querySelector('.delete-btn').addEventListener('click', () => saveRecipes(state.saved.filter(saved => saved.name !== recipe.name)));
    container.appendChild(card);
  });
}

async function handleGenerate() {
  if ($('#generate-btn').disabled) return;
  const ingredients = $('#ingredients-input').value.trim();
  if (!ingredients) { showError('Please enter some ingredients first.'); return; }
  clearError();
  setLoading(true);
  try {
    state.recipes = await generateRecipes(ingredients, { time: 'normal', servings: 2, diet: 'any', cuisine: $('#cuisine-select').value });
    displayGeneratedRecipes(state.recipes);
  } catch (error) {
    showRecovery(error.message || 'Your ingredients are still here. Please try again shortly.');
  } finally {
    setLoading(false);
  }
}

$('#clear-history-btn').addEventListener('click', () => {
  clearRecipeHistory();
  updateHistoryControls();
  showToast('Recent history cleared. Your saved recipes were kept.');
});
updateHistoryControls();
loadCuisineOptions();

$('#generate-btn').addEventListener('click', handleGenerate); $('#close-sub-modal').addEventListener('click', () => $('#substitution-modal').close()); $('#close-wine-modal').addEventListener('click', () => $('#wine-modal').close()); $('#get-subs-btn').addEventListener('click', async () => { const ingredient = $('#ingredient-select').value; $('#substitutions-result').innerHTML = '<p class="text-stone-600">Finding substitutions...</p>'; try { const response = await fetch('/api/substitutions', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ingredient }) }); const data = await response.json(); if (!response.ok) throw new Error(data.error || 'Failed to get substitutions'); const subsHtml = data.substitutions.map(sub => `<div class="mb-4 pb-4 border-b last:border-0"><h4 class="font-semibold text-stone-800">${escapeHtml(sub.name)}</h4><p class="text-sm text-stone-600 mt-1">${escapeHtml(sub.reason)}</p><p class="text-sm text-teal-700 mt-1"><strong>Note:</strong> ${escapeHtml(sub.adjustments)}</p></div>`).join(''); $('#substitutions-result').innerHTML = subsHtml; } catch (error) { $('#substitutions-result').innerHTML = `<p class="text-red-600">Error: ${escapeHtml(error.message)}</p>`; } }); window.addEventListener('click', event => { if (event.target === $('#substitution-modal')) $('#substitution-modal').close(); if (event.target === $('#wine-modal')) $('#wine-modal').close(); }); displaySavedRecipes();
