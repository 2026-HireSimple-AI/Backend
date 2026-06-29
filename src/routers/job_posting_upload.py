from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel
from typing import Optional, Any

from routers.job_posting_service import (
    scrape_job_posting,
    extract_job_posting_text,
    job_posting_formating,
)
from database import supabase, supabase_auth


router = APIRouter(
    prefix="/api/v1",
    tags=["job-posting"]
)


class UrlRequest(BaseModel):
    url: str


class TitleRequest(BaseModel):
    title: str


def get_user_id_from_header(authorization: Optional[str]) -> Optional[str]:
    if not authorization or not authorization.startswith("Bearer "):
        return None

    token = authorization.replace("Bearer ", "")

    try:
        res = supabase_auth.auth.get_user(token)
        if res.user:
            return str(res.user.id)
    except Exception:
        return None

    return None


def json_to_str(data: Any) -> str:
    if not data:
        return ""

    if isinstance(data, str):
        return data

    if not isinstance(data, dict):
        return str(data)

    lines = []

    for key, value in data.items():
        if isinstance(value, list):
            lines.append(f"{key}:")
            for item in value:
                if isinstance(item, dict):
                    for k, v in item.items():
                        lines.append(f"  - {k}: {v}")
                else:
                    lines.append(f"  - {item}")
        else:
            lines.append(f"{key}: {value}")

    return "\n".join(lines)


def normalize_content(value: Any) -> list[str]:
    if value is None:
        return []

    if isinstance(value, list):
        return [str(v) for v in value]

    return [str(value)]


def to_front_formatted_response(job_posting_id: int, formatted_posting: dict, title: str) -> dict:
    return {
        "job_posting_id": job_posting_id,
        "title": title,
        "formatted_posting": [
            {
                "category": "자격 조건",
                "content": normalize_content(formatted_posting.get("requirement")),
            },
            {
                "category": "주요 업무",
                "content": normalize_content(formatted_posting.get("task")),
            },
            {
                "category": "우대 사항",
                "content": normalize_content(formatted_posting.get("preference")),
            },
        ],
        "skills_stack": formatted_posting.get("skill_stack", []),
    }


def raw_content_to_posting_text(raw_content: Any) -> str:
    if isinstance(raw_content, str) and raw_content.startswith("http"):
        return extract_job_posting_text(raw_content, is_url=True)

    if isinstance(raw_content, list) and raw_content:
        return extract_job_posting_text(raw_content, is_url=True)

    return raw_content or ""


REQUIRED_FIELDS = ["requirement", "skill_stack", "task", "preference"]


def has_required_values(formatted_posting: dict) -> bool:
    for field in REQUIRED_FIELDS:
        value = formatted_posting.get(field, [])

        if not isinstance(value, list) or len(value) == 0:
            return False

        if len(value) == 1 and str(value[0]).strip() == "확인 필요":
            return False

    return True


@router.post("/job-posting/upload")
def upload_job_posting(req: UrlRequest, authorization: Optional[str] = Header(None)):
    result = scrape_job_posting(req.url)

    if not result.get("title"):
        raise HTTPException(status_code=400, detail="채용공고 제목을 가져오지 못했습니다.")

    if not result.get("raw_content"):
        raise HTTPException(status_code=400, detail="채용공고 본문을 가져오지 못했습니다.")

    user_id = get_user_id_from_header(authorization)

    response = (
        supabase.table("job_postings")
        .insert({
            "user_id": user_id,
            "title": result["title"],
            "input_type": result["input_type"],
            "source_url": result["source_url"],
            "raw_content": result["raw_content"],
            "conts_summary": result["conts_summary"],
        })
        .execute()
    )

    if not response.data:
        raise HTTPException(status_code=500, detail="채용공고 저장에 실패했습니다.")

    saved = response.data[0]

    return {
        "success": True,
        "data": {
            "job_posting_id": saved["id"],
            "title": saved["title"],
            "input_type": saved["input_type"],
            "source_url": saved["source_url"],
        },
    }


@router.get("/job-posting/{job_posting_id}")
def get_job_posting(job_posting_id: int):
    response = (
        supabase.table("job_postings")
        .select("id, title, input_type, source_url")
        .eq("id", job_posting_id)
        .execute()
    )

    if not response.data:
        raise HTTPException(
            status_code=404,
            detail=f"id={job_posting_id}인 채용공고가 없습니다.",
        )

    posting = response.data[0]

    return {
        "success": True,
        "data": {
            "job_posting_id": posting["id"],
            "title": posting["title"],
            "input_type": posting["input_type"],
            "source_url": posting["source_url"],
        },
    }


@router.post("/job-posting/{job_posting_id}/format")
def format_job_posting(job_posting_id: int):
    response = (
        supabase.table("job_postings")
        .select("*")
        .eq("id", job_posting_id)
        .execute()
    )

    if not response.data:
        raise HTTPException(
            status_code=404,
            detail=f"id={job_posting_id}인 채용공고가 없습니다.",
        )

    posting = response.data[0]

    title = posting["title"]
    summary = json_to_str(posting.get("conts_summary"))
    raw_content = posting.get("raw_content")

    posting_text = raw_content_to_posting_text(raw_content)

    max_retry = 10
    formatted_posting = None

    for i in range(max_retry):
        formatted_posting = job_posting_formating(title, summary, posting_text)

        if has_required_values(formatted_posting):
            break

        print(f"필수 항목 누락, 재시도 {i + 1}/{max_retry}")
        print(formatted_posting)

    else:
        raise HTTPException(
            status_code=500,
            detail=f"필수 항목 추출 실패: {formatted_posting}",
        )

    try:
        supabase.table("formatted_postings").delete().eq(
            "job_posting_id", job_posting_id
        ).execute()
        print("formatted_postings 삭제 성공")
    except Exception as e:
        print("formatted_postings 삭제 실패:", e)

    try:
        supabase.table("skills_stack").delete().eq(
            "job_posting_id", job_posting_id
        ).execute()
        print("skills_stack 삭제 성공")
    except Exception as e:
        print("skills_stack 삭제 실패:", e)

    sort_order_map = {
        "requirement": 1,
        "task": 2,
        "preference": 3,
    }

    for category, content in formatted_posting.items():
        if category == "skill_stack":
            continue

        if content:
            supabase.table("formatted_postings").insert({
                "job_posting_id": job_posting_id,
                "category": category,
                "content": content,
                "sort_order": sort_order_map.get(category),
            }).execute()

    skill_rows = [
        {
            "job_posting_id": job_posting_id,
            "skill_name": skill,
            "sort_order": None,
        }
        for skill in formatted_posting.get("skill_stack", [])
    ]

    if skill_rows:
        supabase.table("skills_stack").insert(skill_rows).execute()

    return {
        "success": True,
        "data": to_front_formatted_response(job_posting_id, formatted_posting, title),
    }


@router.patch("/job-posting/{job_posting_id}/title")
def update_job_posting_title(job_posting_id: int, req: TitleRequest):
    response = (
        supabase.table("job_postings")
        .update({"title": req.title})
        .eq("id", job_posting_id)
        .execute()
    )

    if not response.data:
        raise HTTPException(
            status_code=404,
            detail=f"id={job_posting_id}인 채용공고가 없습니다.",
        )

    return {
        "success": True,
        "title": req.title,
    }