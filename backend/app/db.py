import os
from functools import lru_cache

from supabase import Client, create_client


@lru_cache
def get_db() -> Client:
    """Returns the shared Supabase client. Use it as a FastAPI dependency."""
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set")
    return create_client(url, key)
