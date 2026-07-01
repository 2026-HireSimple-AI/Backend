from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel
from typing import Optional, Any

from routers.job_posting_service import (
    scrape_job_posting,
    extract_job_posting_text,
    job_posting_formating,
)
from database import supabase, supabase_auth
import sys


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
            {
                "category": "기술 스택",
                "content": normalize_content(formatted_posting.get("skill_stack")),
            },
        ],
    }


def get_posting_text(job_posting_id: int, posting: dict) -> str:
    """
    posting.raw_content가 이미 채워져 있으면 그대로 반환.
    raw_content가 비어 있고 image_content(이미지 URL 목록)만 있으면
    그때 OCR(extract_job_posting_text)을 돌려서 결과를 DB의 raw_content에
    캐싱해두고 반환한다. (다음 포맷팅 요청부터는 재OCR하지 않도록)
    """
    raw_content = posting.get("raw_content")
    if raw_content:
        return raw_content

    image_content = posting.get("image_content")
    if not image_content:
        raise HTTPException(
            status_code=400,
            detail="채용공고 본문(raw_content/image_content)이 비어 있습니다.",
        )

    extracted_text = extract_job_posting_text(image_content, is_url=True)

    try:
        supabase.table("job_postings").update(
            {"raw_content": extracted_text}
        ).eq("id", job_posting_id).execute()
    except Exception as e:
        print(f"raw_content 캐싱 실패 (job_posting_id={job_posting_id}):", e)

    return extracted_text


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

    if not result.get("raw_content") and not result.get("image_content"):
        raise HTTPException(status_code=400, detail="채용공고 본문을 가져오지 못했습니다.")

    # 이미지 URL 리스트인 경우 업로드 시점에 즉시 텍스트 추출 (URL 만료 방지)
    raw_content = result["raw_content"]
    if isinstance(raw_content, list) and raw_content:
        try:
            raw_content = extract_job_posting_text(raw_content, is_url=True)
            print(f"이미지 텍스트 추출 완료: {len(raw_content)}자")
        except Exception as e:
            print(f"이미지 텍스트 추출 실패, URL 리스트 그대로 저장: {e}")

    user_id = get_user_id_from_header(authorization)

    response = (
        supabase.table("job_postings")
        .insert({
            "user_id": user_id,
            "title": result["title"],
            "input_type": result["input_type"],
            "source_url": result["source_url"],
            "raw_content": result["raw_content"],
            "image_content": result["image_content"],
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
    posting_text = get_posting_text(job_posting_id, posting)

    max_retry = 10
    formatted_posting = None

    for i in range(max_retry):
        formatted_posting = job_posting_formating(title, summary, posting_text)
        print(f"[format] LLM 결과: { {k: bool(v) for k, v in formatted_posting.items()} }", file=sys.stderr)

        if has_required_values(formatted_posting):
            break

        print(f"필수 항목 누락, 재시도 {i + 1}/{max_retry}", file=sys.stderr)

    else:
        raise HTTPException(
            status_code=500,
            detail=f"공고 내용이 부족하여 필수 항목을 추출할 수 없습니다. 공고 본문 텍스트가 충분한지 확인해주세요. (추출된 내용: {posting_text[:300]})",
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
        "career": 4,
        "education": 5,
        "field": 6,
    }
    # is_required: requirement만 True, 나머지는 False
    is_required_map = {
        "requirement": True,
    }

    for category, content in formatted_posting.items():
        if category == "skill_stack":
            continue

        if content:
            supabase.table("formatted_postings").insert({
                "job_posting_id": job_posting_id,
                "category": category,
                "content": content,
                "sort_order": sort_order_map.get(category, 99),
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