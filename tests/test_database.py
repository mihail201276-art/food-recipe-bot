import os
import tempfile
from pathlib import Path

import pytest

from database import (
    init_db, add_favorite, remove_favorite, get_favorites, is_favorite,
    update_rating, get_rating, set_premium, get_profile, save_profile,
    add_history, get_history, check_and_increment_translation,
    log_meal, get_daily_nutrition, get_recent_meals,
)


@pytest.fixture(autouse=True)
def _patch_db(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db_path = Path(path)
    monkeypatch.setattr("database.DB_PATH", db_path)
    init_db()
    yield
    try:
        import gc; gc.collect()
        db_path.unlink(missing_ok=True)
    except PermissionError:
        pass


SAMPLE_RECIPE = {
    "idMeal": "12345",
    "strMeal": "Test Dish",
    "strMealThumb": "https://example.com/img.jpg",
    "strArea": "Italian",
    "strCategory": "Pasta",
    "strInstructions": "Cook it.",
    "strYoutube": "https://youtube.com/watch?v=test",
}


def test_add_favorite():
    ok = add_favorite(1, SAMPLE_RECIPE)
    assert ok
    assert is_favorite(1, "12345")


def test_add_favorite_duplicate():
    add_favorite(1, SAMPLE_RECIPE)
    ok = add_favorite(1, SAMPLE_RECIPE)
    assert not ok


def test_remove_favorite():
    add_favorite(1, SAMPLE_RECIPE)
    assert remove_favorite(1, "12345")
    assert not is_favorite(1, "12345")


def test_get_favorites():
    add_favorite(1, SAMPLE_RECIPE)
    favs = get_favorites(1)
    assert len(favs) == 1
    assert favs[0]["recipe_name"] == "Test Dish"


def test_get_favorites_empty():
    assert get_favorites(99) == []


def test_rating():
    add_favorite(1, SAMPLE_RECIPE)
    assert update_rating(1, "12345", 5)
    assert get_rating(1, "12345") == 5


def test_rating_default_zero():
    add_favorite(1, SAMPLE_RECIPE)
    assert get_rating(1, "12345") == 0


def test_profile_defaults():
    prof = get_profile(1)
    assert prof["allergies"] == ""
    assert prof["premium"] == 0


def test_save_profile():
    save_profile(1, "allergies", "gluten")
    prof = get_profile(1)
    assert prof["allergies"] == "gluten"


def test_set_premium():
    set_premium(1)
    prof = get_profile(1)
    assert prof["premium"] == 1


def test_history():
    add_history(1, "user", "hello")
    add_history(1, "assistant", "hi")
    history = get_history(1)
    assert len(history) == 2
    assert history[0]["content"] == "hello"
    assert history[1]["role"] == "assistant"


def test_history_limits_to_10():
    for i in range(15):
        add_history(1, "user", str(i))
    history = get_history(1)
    assert len(history) == 10


def test_translation_limit():
    assert check_and_increment_translation(1, limit=3)
    assert check_and_increment_translation(1, limit=3)
    assert check_and_increment_translation(1, limit=3)
    assert not check_and_increment_translation(1, limit=3)


def test_nutrition_log():
    log_meal(1, "2025-06-09", "обед", "Суп", 250, 12, 8, 30)
    day = get_daily_nutrition(1, "2025-06-09")
    assert day["calories"] == 250
    assert day["protein"] == 12


def test_nutrition_empty():
    day = get_daily_nutrition(99, "2099-01-01")
    assert day["calories"] == 0


def test_recent_meals():
    log_meal(1, "2025-06-09", "завтрак", "Каша")
    meals = get_recent_meals(1, "2025-06-09")
    assert len(meals) == 1
    assert meals[0]["food_name"] == "Каша"


def test_recent_meals_empty():
    assert get_recent_meals(99, "2099-01-01") == []
