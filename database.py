import sqlite3
import logging
import threading
from pathlib import Path

DB_PATH = Path(__file__).parent / "favorites.db"

logger = logging.getLogger(__name__)

_db_lock = threading.Lock()


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
        conn.execute("""
            CREATE TABLE IF NOT EXISTS translations (
                recipe_id TEXT NOT NULL,
                lang TEXT NOT NULL DEFAULT 'ru',
                translated_text TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (recipe_id, lang)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chat_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_profiles (
                user_id INTEGER PRIMARY KEY,
                allergies TEXT DEFAULT '',
                diet TEXT DEFAULT '',
                gluten_free INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS translation_usage (
                user_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                count INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, date)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS nutrition_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                meal_type TEXT DEFAULT '',  -- завтрак/обед/ужин/перекус
                food_name TEXT NOT NULL,
                calories REAL DEFAULT 0,
                protein REAL DEFAULT 0,
                fat REAL DEFAULT 0,
                carbs REAL DEFAULT 0,
                water_ml REAL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_fav_user ON favorites(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_nutrition_user_date ON nutrition_log(user_id, date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_user ON chat_history(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_trans_usage ON translation_usage(user_id, date)")
        conn.commit()
        _migrate_db(conn)
    logger.info("Database initialized")


def _migrate_db(conn):
    for col in ["rating", "youtube_url"]:
        try:
            conn.execute(f"ALTER TABLE favorites ADD COLUMN {col} TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
    for col in ["premium"]:
        try:
            conn.execute(f"ALTER TABLE user_profiles ADD COLUMN {col} INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
    conn.commit()


def add_favorite(user_id: int, recipe: dict) -> bool:
    try:
        with _db_lock, sqlite3.connect(DB_PATH) as conn:
            cur = conn.execute(
                """INSERT OR IGNORE INTO favorites
                   (user_id, recipe_id, recipe_name, recipe_image, recipe_area, recipe_category, ingredients, instructions, youtube_url)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    user_id,
                    recipe["idMeal"],
                    recipe["strMeal"],
                    recipe.get("strMealThumb", ""),
                    recipe.get("strArea", ""),
                    recipe.get("strCategory", ""),
                    _serialize_ingredients(recipe),
                    recipe.get("strInstructions", ""),
                    recipe.get("strYoutube", ""),
                ),
            )
            conn.commit()
            ok = cur.rowcount > 0
            logger.info("add_favorite user=%s recipe=%s ok=%s rowcount=%s", user_id, recipe["idMeal"], ok, cur.rowcount)
            return ok
    except Exception as e:
        logger.error("Error adding favorite: %s", e)
        return False


def remove_favorite(user_id: int, recipe_id: str) -> bool:
    try:
        with _db_lock, sqlite3.connect(DB_PATH) as conn:
            cursor = conn.execute(
                "DELETE FROM favorites WHERE user_id = ? AND recipe_id = ?",
                (user_id, recipe_id),
            )
            conn.commit()
            return cursor.rowcount > 0
    except Exception as e:
        logger.error("Error removing favorite: %s", e)
        return False


def update_rating(user_id: int, recipe_id: str, rating: int) -> bool:
    try:
        with _db_lock, sqlite3.connect(DB_PATH) as conn:
            cursor = conn.execute(
                "UPDATE favorites SET rating = ? WHERE user_id = ? AND recipe_id = ?",
                (rating, user_id, recipe_id),
            )
            conn.commit()
            return cursor.rowcount > 0
    except Exception as e:
        logger.error("Error updating rating: %s", e)
        return False


def get_rating(user_id: int, recipe_id: str) -> int:
    try:
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT rating FROM favorites WHERE user_id = ? AND recipe_id = ?",
                (user_id, recipe_id),
            ).fetchone()
            return int(row[0]) if row else 0
    except Exception as e:
        logger.error("Error getting rating: %s", e)
        return 0


def get_favorites(user_id: int) -> list[dict]:
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM favorites WHERE user_id = ? ORDER BY rating DESC, created_at DESC",
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


def get_translation(recipe_id: str, lang: str = "ru") -> str | None:
    try:
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT translated_text FROM translations WHERE recipe_id = ? AND lang = ?",
                (recipe_id, lang),
            ).fetchone()
            return row[0] if row else None
    except Exception as e:
        logger.error("Error getting translation: %s", e)
        return None


def save_translation(recipe_id: str, lang: str, text: str):
    try:
        with _db_lock, sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO translations (recipe_id, lang, translated_text) VALUES (?, ?, ?)",
                (recipe_id, lang, text),
            )
            conn.commit()
    except Exception as e:
        logger.error("Error saving translation: %s", e)


def add_history(user_id: int, role: str, content: str):
    try:
        with _db_lock, sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT INTO chat_history (user_id, role, content) VALUES (?, ?, ?)",
                (user_id, role, content),
            )
            conn.execute(
                "DELETE FROM chat_history WHERE user_id = ? AND id NOT IN (SELECT id FROM chat_history WHERE user_id = ? ORDER BY id DESC LIMIT 10)",
                (user_id, user_id),
            )
            conn.commit()
    except Exception as e:
        logger.error("Error adding history: %s", e)


def get_history(user_id: int) -> list[dict]:
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT role, content FROM chat_history WHERE user_id = ? ORDER BY id ASC",
                (user_id,),
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error("Error getting history: %s", e)
        return []


def get_profile(user_id: int) -> dict:
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM user_profiles WHERE user_id = ?", (user_id,)
            ).fetchone()
            return dict(row) if row else {"allergies": "", "diet": "", "gluten_free": 0, "premium": 0}
    except Exception as e:
        logger.error("Error getting profile: %s", e)
        return {"allergies": "", "diet": "", "gluten_free": 0, "premium": 0}


_PROFILE_SQL = {
    "allergies": "INSERT INTO user_profiles (user_id, allergies) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET allergies = excluded.allergies",
    "diet": "INSERT INTO user_profiles (user_id, diet) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET diet = excluded.diet",
    "gluten_free": "INSERT INTO user_profiles (user_id, gluten_free) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET gluten_free = excluded.gluten_free",
    "premium": "INSERT INTO user_profiles (user_id, premium) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET premium = excluded.premium",
}

def save_profile(user_id: int, field: str, value: str | int):
    sql = _PROFILE_SQL.get(field)
    if not sql:
        logger.error("Attempt to set invalid profile field: %s", field)
        return
    try:
        with _db_lock, sqlite3.connect(DB_PATH) as conn:
            conn.execute(sql, (user_id, value))
            conn.commit()
    except Exception as e:
        logger.error("Error saving profile: %s", e)


def check_and_increment_translation(user_id: int, limit: int = 20) -> bool:
    try:
        today = __import__("datetime").date.today().isoformat()
        with _db_lock, sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT count FROM translation_usage WHERE user_id = ? AND date = ?",
                (user_id, today),
            ).fetchone()
            count = row[0] if row else 0
            if count >= limit:
                return False
            conn.execute(
                "INSERT OR REPLACE INTO translation_usage (user_id, date, count) "
                "VALUES (?, ?, COALESCE((SELECT count FROM translation_usage WHERE user_id=? AND date=?), 0) + 1)",
                (user_id, today, user_id, today),
            )
            conn.commit()
            return True
    except Exception as e:
        logger.error("Error checking/incrementing translation: %s", e)
        return False


def set_premium(user_id: int, value: int = 1):
    try:
        with _db_lock, sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT INTO user_profiles (user_id, premium) VALUES (?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET premium = excluded.premium",
                (user_id, value),
            )
            conn.commit()
    except Exception as e:
        logger.error("Error setting premium: %s", e)


def _serialize_ingredients(recipe: dict) -> str:
    ingredients = []
    for i in range(1, 21):
        name = recipe.get(f"strIngredient{i}")
        measure = recipe.get(f"strMeasure{i}")
        if name and name.strip():
            ingredients.append(f"{name.strip()} – {measure.strip()}" if measure else name.strip())
    return "\n".join(ingredients)


def log_meal(user_id: int, date: str, meal_type: str, food_name: str, calories: float = 0, protein: float = 0, fat: float = 0, carbs: float = 0, water_ml: float = 0):
    try:
        with _db_lock, sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT INTO nutrition_log (user_id, date, meal_type, food_name, calories, protein, fat, carbs, water_ml) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (user_id, date, meal_type, food_name, calories, protein, fat, carbs, water_ml),
            )
            conn.commit()
    except Exception as e:
        logger.error("Error logging meal: %s", e)


def get_daily_nutrition(user_id: int, date: str) -> dict:
    try:
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(calories),0), COALESCE(SUM(protein),0), "
                "COALESCE(SUM(fat),0), COALESCE(SUM(carbs),0), COALESCE(SUM(water_ml),0) "
                "FROM nutrition_log WHERE user_id=? AND date=?",
                (user_id, date),
            ).fetchone()
            return {
                "calories": round(row[0], 1),
                "protein": round(row[1], 1),
                "fat": round(row[2], 1),
                "carbs": round(row[3], 1),
                "water_ml": round(row[4], 1),
            } if row else {"calories": 0, "protein": 0, "fat": 0, "carbs": 0, "water_ml": 0}
    except Exception:
        return {"calories": 0, "protein": 0, "fat": 0, "carbs": 0, "water_ml": 0}


def get_recent_meals(user_id: int, date: str, limit: int = 10) -> list:
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM nutrition_log WHERE user_id=? AND date=? ORDER BY created_at DESC LIMIT ?",
                (user_id, date, limit),
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception:
        return []
