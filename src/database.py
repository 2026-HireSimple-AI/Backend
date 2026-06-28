from supabase import create_client, Client
from dotenv import load_dotenv
import os

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY", "")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("[WARNING] SUPABASE_URL 또는 SUPABASE_KEY가 비어 있습니다. DB 기능 비활성화")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Auth 전용 클라이언트 (ANON_KEY)
supabase_auth: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)