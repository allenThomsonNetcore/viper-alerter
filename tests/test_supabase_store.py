"""Supabase API key handling tests."""
from checker.supabase_store import SupabaseAPIError, SupabaseStore


def test_new_secret_key_is_not_sent_as_bearer():
    """Opaque Supabase secret keys use apikey, not a Bearer JWT header."""
    store = SupabaseStore("https://project.supabase.co", "sb_secret_example")
    assert store.headers["apikey"] == "sb_secret_example"
    assert "Authorization" not in store.headers


def test_legacy_service_role_jwt_is_sent_as_bearer():
    """Legacy service-role JWTs are sent as both API key and bearer token."""
    key = "eyJexample.legacy.signature"
    store = SupabaseStore("https://project.supabase.co", key)
    assert store.headers["apikey"] == key
    assert store.headers["Authorization"] == f"Bearer {key}"


def test_forbidden_error_has_actionable_setup_guidance():
    """403 errors point operators at schema and service-role table grants."""
    error = SupabaseAPIError(403)
    assert "schema" in error.user_message
    assert "table grants" in error.user_message
