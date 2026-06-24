# 채용 공고 url을 입력 받음
# 스크랩핑 -> 형식 분류(이미지 or 텍스트)
# 여기서 포메팅까지 해서 DB 저장까지 하자
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from routers.job_posting_service import scrape_job_posting

router = APIRouter(
    prefix="/job-posting",
    tags=["job-posting"]
)

class UrlRequest(BaseModel):
    url: str

@router.post("/upload")
def upload_job_posting(req: UrlRequest):
    result = scrape_job_posting(req.url)
    print(result)
    return result