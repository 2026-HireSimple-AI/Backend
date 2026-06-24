from urllib.parse import urlparse, parse_qs
from fastapi import HTTPException
from playwright.sync_api import sync_playwright

def scrape_job_posting(url: str):
    print("서비스 들어옴 - sync 버전")

    id_list = parse_qs(urlparse(url).query).get("rec_idx")
    print("rec_idx 파싱 완료")

    if not id_list:
        raise HTTPException(status_code=400, detail="rec_idx가 없는 url입니다.")

    rec_idx = id_list[0]

    with sync_playwright() as p:
        print("playwright 시작")
        browser = p.chromium.launch(headless=True, slow_mo=300)
        print("브라우저 실행 완료")
        page = browser.new_page(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
            )
        )

        print("페이지 생성 완료")

        try:
            try:
                page.goto("https://www.saramin.co.kr", wait_until="commit", timeout=30000)
                print("사람인 접속")
            except Exception as e:
                print("사람인 메인 접속 실패:", e)
                print("현재 URL:", page.url)
                print("제목:", page.title())

            title = (page.locator("h1.tit_job").first.text_content() or "").strip()
            details = page.locator("div.col > dl > dd")

            d_list = []
            for i in range(3):
                detail = details.nth(i).text_content()
                d_list.append((detail or "").strip())

            career = d_list[0] if len(d_list) > 0 else ""
            education = d_list[1] if len(d_list) > 1 else ""
            employment_type = d_list[2] if len(d_list) > 2 else ""

            result = {
                "title": title,
                "url": url,
                "career": career,
                "education": education,
                "employment_type": employment_type,
            }

            detail_url = (
                f"https://www.saramin.co.kr/zf_user/jobs/relay/view-detail"
                f"?rec_idx={rec_idx}"
            )

            page.goto(detail_url, timeout=60000)

            contents = page.locator("div.user_content")

            text1 = contents.locator("div.job-content")
            text2 = contents.locator("div.content")
            text3_locator = page.locator("body > div > div > div:nth-child(2)")
            images = contents.locator("img")

            text3_text = ""
            if text3_locator.count():
                text3_text = (text3_locator.text_content() or "").strip()

            if text1.count():
                data = (text1.text_content() or "").strip()
            elif text2.count():
                data = (text2.text_content() or "").strip()
            elif text3_text:
                data = text3_text
            else:
                image_list = []
                img_count = images.count()

                for i in range(img_count):
                    src = images.nth(i).get_attribute("src")

                    if not src:
                        continue

                    if src.startswith("//"):
                        src = "https:" + src

                    image_list.append(src)

                data = image_list

            result["content"] = data
            return result

        finally:
            browser.close()