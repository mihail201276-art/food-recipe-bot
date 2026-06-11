import pytest
from telegram import Update, Message, Chat, User
from telegram.ext import ContextTypes

from utils.helpers import sanitize_query


def test_sanitize_query_removes_xss():
    result = sanitize_query("<script>alert('xss')</script>")
    assert result is not None
    assert "<" not in result
    assert ">" not in result
    assert "'" not in result


def test_sanitize_query_short():
    assert sanitize_query("a") is None


def test_sanitize_query_empty():
    assert sanitize_query("") is None
    assert sanitize_query("   ") is None


def test_sanitize_query_truncates():
    long = "x" * 200
    result = sanitize_query(long, max_length=100)
    assert result is not None
    assert len(result) == 100


def test_sanitize_query_normal():
    assert sanitize_query("паста с курицей") == "паста с курицей"


def test_sanitize_query_strips():
    assert sanitize_query("  борщ  ") == "борщ"
