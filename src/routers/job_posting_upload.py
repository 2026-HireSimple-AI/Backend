# 채용 공고 url을 입력 받음
# 스크랩핑 -> 형식 분류(이미지 or 텍스트)
# 여기서 포메팅까지 해서 DB 저장까지 하자

from fastapi import APIRouter, Request
# from bs4 import BeautifulSoup
# import requests
from playwright.async_api import async_playwright

router = APIRouter(
    prefix="/job-posting",
    tags=["job-posting"]
)

@router.post("/upload")
async def upload_job_posting(request: Request):
    data = await request.json()
    url = data.get("source_url", '')
    print(url)
    # if data:
    #     response = requests.get(url)
    #     print(response.status_code)
    #     soup = BeautifulSoup(response.text, "html.parser")
    #     print(soup)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless = False)
        page = await browser.new_page(user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36")
        await page.goto(url, timeout=60000)


    return {"message": "채용공고 업로드 API 연결 성공"}