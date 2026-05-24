import sqlite3
import logging
from pathlib import Path

DB_PATH = Path(__file__).parent / "favorites.db"

logger = logging.getLogger(__name__)


def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS favorites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                recipe_id TEXT NOT NULL,
                recipe_name TEXT NOT NULL,
                recipe_image TEXT,
                recipe_area TEXT,
                recipe_category TEXT,
                ingredients TEXT,
                instructions TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, recipe_id)
            )
        """)
        conn.commit()
    logger.info("Database initialized")


def add_favorite(user_id: int, recipe: dict) -> bool:
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                """INSERT OR IGNORE INTO favorites
                   (user_id, recipe_id, recipe_name, recipe_image, recipe_area, recipe_category, ingredients, instructions)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    user_id,
                    recipe["idMeal"],
                    recipe["strMeal"],
                    recipe.get("strMealThumb", ""),
                    recipe.get("strArea", ""),
                    recipe.get("strCategory", ""),
                    _serialize_ingredients(recipe),
                    recipe.get("strInstructions", ""),
                ),
            )
            conn.commit()
            return conn.total_changes > 0
    except Exception as e:
        logger.error("Error adding favorite: %s", e)
        return False


def remove_favorite(user_id: int, recipe_id: str) -> bool:
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.execute(
                "DELETE FROM favorites WHERE user_id = ? AND recipe_id = ?",
                (user_id, recipe_id),
            )
            conn.commit()
            return cursor.rowcount > 0
    except Exception as e:
        logger.error("Error removing favorite: %s", e)
        return False


def get_favorites(user_id: int) -> list[dict]:
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM favorites WHERE user_id = ? ORDER BY created_at DESC",
                (user_id,),
            ).fetchall()
            return [dict(row) for row in rows]
    except Exception as e:
        logger.error("Error getting favorites: %s", e)
        return []


def is_favorite(user_id: int, recipe_id: str) -> bool:
    try:
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT 1 FROM favorites WHERE user_id = ? AND recipe_id = ?",
                (user_id, recipe_id),
            ).fetchone()
            return row is not None
    except Exception as e:
        logger.error("Error checking favorite: %s", e)
        return False


def _serialize_ingredients(recipe: dict) -> str:
    ingredients = []
    for i in range(1, 21):
        name = recipe.get(f"strIngredient{i}")
        measure = recipe.get(f"strMeasure{i}")
        if name and name.strip():
            ingredients.append(f"{name.strip()} – {measure.strip()}" if measure else name.strip())
    return "\n".join(ingredients)
