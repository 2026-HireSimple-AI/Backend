"""
인증 라우터 (회원가입 / 로그인 / 내 정보 조회)
- POST /api/v1/auth/signup   회원가입
- POST /api/v1/auth/login    로그인
- GET  /api/v1/auth/me       내 정보 조회 (토큰 필요)
"""

from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel
from typing import Optional
from database import supabase, supabase_auth

router = APIRouter(
    prefix="/api/v1/auth",
    tags=["auth"]
)

class SignupRequest(BaseModel):
    email: str
    password: str
    name: str
    company_name: Optional[str] = None

class LoginRequest(BaseModel):
    email: str
    password: str

# ---------- 회원가입 ----------
@router.post("/signup")
async def singup(req: SignupRequest):
    try:
        res = supabase_auth.auth.sign_up({
            "email": req.email,
            "password": req.password,
            "options":{
                "data": {
                    # 트리거가 이 값을 읽어서 자동으로 public.users에 저장
                    "name": req.name,
                    "company_name" : req.company_name
                }
            }
        })

        if not res.user:
            raise HTTPException(status_code=400, detail="회원가입 실패")
        
        return {
            "success": True,
            "message": "회원가입이 완료되었습니다.",
            "data": {
                "id": str(res.user.id),
                "email": res.user.email
            }
        }
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"회원가입 실패: {str(e)}")
    
# ---------- 로그인 ----------

@router.post("/login")
async def login(req: LoginRequest):
    try:
        # Supabase Auth로 로그인
        res = supabase_auth.auth.sign_in_with_password({
            "email": req.email,
            "password": req.password
        })

        if not res.user or not res.session:
            raise HTTPException(status_code=401, detail="이메일 또는 비밀번호가 올바르지 않습니다.")

        # public.users에서 추가 정보 조회
        user_info = supabase.table("users")\
            .select("*")\
            .eq("id", str(res.user.id))\
            .execute()

        profile = user_info.data[0] if user_info.data else {}

        return {
            "success": True,
            "message": "로그인 성공",
            "data": {
                "access_token": res.session.access_token,
                "refresh_token": res.session.refresh_token,
                "user": {
                    "id": str(res.user.id),
                    "email": res.user.email,
                    "name": profile.get("name", ""),
                    "company_name": profile.get("company_name", ""),
                    "role": profile.get("role", "recruiter")
                }
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"로그인 실패: {str(e)}")


# ---------- 내 정보 조회 ----------

@router.get("/me")
async def get_me(authorization: Optional[str] = Header(None)):
    """
    Authorization: Bearer {access_token} 헤더 필요
    로그인한 사용자 정보 반환
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="토큰이 없습니다.")

    token = authorization.replace("Bearer ", "")

    try:
        # 토큰으로 유저 정보 확인
        res = supabase_auth.auth.get_user(token)

        if not res.user:
            raise HTTPException(status_code=401, detail="유효하지 않은 토큰입니다.")

        # public.users에서 추가 정보 조회
        user_info = supabase.table("users")\
            .select("*")\
            .eq("id", str(res.user.id))\
            .execute()

        profile = user_info.data[0] if user_info.data else {}

        return {
            "success": True,
            "data": {
                "id": str(res.user.id),
                "email": res.user.email,
                "name": profile.get("name", ""),
                "company_name": profile.get("company_name", ""),
                "role": profile.get("role", "recruiter")
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"인증 실패: {str(e)}")