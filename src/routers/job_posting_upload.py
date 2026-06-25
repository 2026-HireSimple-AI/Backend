# 채용 공고 url을 입력 받음
# 스크랩핑 -> 형식 분류(이미지 or 텍스트)
# 여기서 포메팅까지 해서 DB 저장까지 하자
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from routers.job_posting_service import scrape_job_posting, extract_job_posting_text, job_posting_formating
from database import supabase

router = APIRouter(
    prefix="/api/v1",
    tags=["job-posting"]
)

class UrlRequest(BaseModel):
    url: str

def json_to_str(data: dict) -> str:
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

@router.post("/job-posting/upload")
def upload_job_posting(req: UrlRequest):
    result = scrape_job_posting(req.url)
    print(result)

    supabase.table("job_postings").upsert({
    "user_id": None,
    "title": result['title'],
    "input_type": result["input_type"],
    "source_url": result["source_url"],
    "raw_content": result["raw_content"],
    "conts_summary": result["conts_summary"]
    }).execute()

    title = result['title']
    summary = json_to_str(result["conts_summary"])

    # if result["raw_content"] == str:
    #     raw_posting = result["raw_content"]
    #     formatted_posting = job_posting_formating(title, summary, raw_posting)
    # else:
    #     raw_image_posting = extract_job_posting_text(result["raw_content"])
    #     formatted_posting = job_posting_formating(title, summary, raw_image_posting)

    REQUIRED_FIELDS = ["requirement", "skill_stack", "task", "preference"]

    def has_required_values(formatted_posting):
        for field in REQUIRED_FIELDS:
            value = formatted_posting.get(field, [])

            # 리스트가 아니거나 비어있으면 실패
            if not isinstance(value, list) or len(value) == 0:
                return False

            # ["확인 필요"]만 있으면 실패
            if len(value) == 1 and value[0].strip() == "확인 필요":
                return False

        return True

    for category in formatted_posting.keys():
        sorted_id = {
            "requirement": 1,
            "task": 2,
            "preference": 3,
        }.get(category, None)
        
        if formatted_posting.get(category, 0):
            supabase.table("formatted_postings").upsert({
            "category": category,
            "content": formatted_posting[category],
            "sort_order": sorted_id
            }).execute()
    return result