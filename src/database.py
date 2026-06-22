"""
Supabase 클라이언트 초기화.
- .env에 SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY(또는 SUPABASE_KEY) 필요.
"""

from supabase import create_client, Client
from dotenv import load_dotenv
import os

load_dotenv()

_url = os.getenv("SUPABASE_URL", "")
_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY", "")

supabase: Client | None = None

if _url and _key:
    supabase = create_client(_url, _key)
else:
    print("[WARNING] SUPABASE_URL 또는 SUPABASE_KEY가 비어 있습니다. DB 기능 비활성화.")
