import re


def sanitize_query(query: str, max_length: int = 100) -> str | None:
    query = re.sub(r"[<>\"'&;]", "", query)
    query = query.strip()[:max_length]
    if not query or len(query) < 2:
        return None
    return query
