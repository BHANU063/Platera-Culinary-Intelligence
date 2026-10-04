const recipeEndpoint = window.PLATERA_CONFIG?.recipeEndpoint || window.PLATERA_CONFIG?.apiEndpoint;
const historyKey = 'platera-recipe-history';
const historyLimit = 60;
const scoreFields = ['authenticity', 'practicality', 'ingredientUtilization', 'flavorComplexity', 'originality'];
const recipeFields = ['name', 'cuisine', 'difficulty', 'servings', 'prepTime', 'cookTime', 'calories', 'scores', 'ingredients', 'instructions', 'chefTips', 'commonMistakes', 'storageAdvice', 'nutrition'];
const isText = value => typeof value === 'string' && value.trim().length > 0;
const isTextList = value => Array.isArray(value) && value.length > 0 && value.every(isText);
const hasKeys = (value, keys) => value && typeof value === 'object' && !Array.isArray(value) && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
const identity = value => value.toLowerCase().replace(/[^\p{L}\p{N}_]+/gu, ' ').trim();

function readStoredArray(key) {
    try {
        const stored = JSON.parse(localStorage.getItem(key));
        return Array.isArray(stored) ? stored : null;
    } catch {
        return null;
    }
}

function summarizeHistory(recipes) {
    return recipes.filter(recipe => recipe && isText(recipe.name)).slice(-historyLimit).map(recipe => ({
        name: recipe.name.slice(0, 200),
        cuisine: typeof recipe.cuisine === 'string' ? recipe.cuisine.slice(0, 100) : '',
        ingredients: (Array.isArray(recipe.ingredients) ? recipe.ingredients : []).map(ingredient => typeof ingredient === 'string' ? ingredient : ingredient?.name).filter(isText).slice(0, 60).map(name => name.slice(0, 200)),
        instructions: (Array.isArray(recipe.instructions) ? recipe.instructions : []).filter(isText).slice(0, 40).map(step => step.slice(0, 2000))
    }));
}

let recentHistory = summarizeHistory(readStoredArray(historyKey) ?? readStoredArray('platera-recipes') ?? []);

export function getRecipeHistory() {
    return structuredClone(recentHistory);
}

function persistHistory() {
    try {
        localStorage.setItem(historyKey, JSON.stringify(recentHistory));
    } catch {
        console.warn('Recent recipe history is only available for this session because browser storage is unavailable.');
    }
}

export function clearRecipeHistory() {
    recentHistory = [];
    persistHistory();
}

function validateRecipes(recipes, cuisine) {
    if (!Array.isArray(recipes) || recipes.length !== 3) return null;
    const valid = recipes.every(recipe =>
        hasKeys(recipe, recipeFields) &&
        ['name', 'cuisine', 'prepTime', 'cookTime', 'storageAdvice'].every(field => isText(recipe[field])) &&
        ['Beginner', 'Intermediate', 'Advanced'].includes(recipe.difficulty) &&
        Number.isInteger(recipe.servings) && recipe.servings > 0 &&
        Number.isInteger(recipe.calories) && recipe.calories >= 0 &&
        hasKeys(recipe.scores, scoreFields) && scoreFields.every(field => Number.isInteger(recipe.scores[field]) && recipe.scores[field] >= 0 && recipe.scores[field] <= 100) &&
        Array.isArray(recipe.ingredients) && recipe.ingredients.length > 0 && recipe.ingredients.every(ingredient =>
            hasKeys(ingredient, ['name', 'amount', 'optional']) && isText(ingredient.name) && isText(ingredient.amount) && /\d/.test(ingredient.amount) && typeof ingredient.optional === 'boolean') &&
        ['instructions', 'chefTips', 'commonMistakes'].every(field => isTextList(recipe[field])) &&
        ['prepTime', 'cookTime'].every(field => /\d/.test(recipe[field])) &&
        hasKeys(recipe.nutrition, ['protein', 'carbohydrates', 'fat']) && Object.values(recipe.nutrition).every(value => isText(value) && /^\s*\d+(?:\.\d+)?\s*g\s*$/.test(value))
    );
    const uniqueFields = cuisine === 'auto' ? ['name', 'cuisine'] : ['name'];
    if (!valid || uniqueFields.some(field => new Set(recipes.map(recipe => identity(recipe[field]))).size !== 3)) return null;
    if (cuisine !== 'auto' && recipes.some(recipe => recipe.cuisine !== cuisine)) return null;
    return recipes;
}

export async function generateRecipes(ingredientsText, options = {}) {
    if (!recipeEndpoint) throw new Error('The Gemini recipe endpoint is not configured.');
    const history = getRecipeHistory();
    const constraints = { servings: 2, diet: 'any', time: 'normal', cuisine: 'auto', ...options };
    let response;
    try {
        response = await fetch(recipeEndpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ingredients: ingredientsText, options: constraints, PREVIOUS_RECIPE_HISTORY: history }),
            signal: AbortSignal.timeout(250000)
        });
    } catch {
        throw new Error('The recipe server could not be reached or took too long. Check the connection and try again.');
    }
    const data = await response.json().catch(() => null);
    if (!response.ok) throw new Error(isText(data?.error) ? data.error : 'Gemini could not generate recipes. Please try again later.');
    const recipes = hasKeys(data, ['recipes']) ? validateRecipes(data.recipes, constraints.cuisine) : null;
    if (!recipes) throw new Error('Gemini returned incomplete recipes. Please try again.');
    if (recipes.some(recipe => recipe.servings !== constraints.servings)) throw new Error('Gemini returned the wrong number of servings. Please try again.');
    if (recipes.some(recipe => history.some(previous => identity(previous.name) === identity(recipe.name) || (constraints.cuisine === 'auto' && previous.cuisine && identity(previous.cuisine) === identity(recipe.cuisine))))) {
        throw new Error('Gemini repeated a recent dish or cuisine. Try different ingredients or clear recent history.');
    }
    recentHistory = summarizeHistory([...recentHistory, ...recipes]);
    persistHistory();
    return recipes;
}

export function calculateImpact(ingredientsText, recipes) {
    const scores = recipes.map(recipe => recipe.scores?.ingredientUtilization).filter(Number.isFinite);
    const coverage = scores.length ? `${Math.round(scores.reduce((total, score) => total + score, 0) / scores.length)}%` : 'Not estimated';
    return { coverage, waste: 'AI-estimated ingredient use', ideas: recipes.length };
}
