"""
Supabase 클라이언트 초기화.
- .env에 SUPABASE_URL, SUPABASE_KEY가 설정되어 있어야 합니다.
- 설정되지 않은 경우 supabase = None (API 호출 시 에러 반환).
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
    print("[WARNING] SUPABASE_URL 또는 SUPABASE_KEY가 비어 있습니다. DB 기능이 비활성화됩니다.")
