import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse, parse_qs

# from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path
from typing import Union

from PIL import Image
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser

def clean(text):
    return text.replace("\xa0", "").strip()

def parse_summary(soup):
    result = {}
    for dl in soup.select("div.cont dl"):
        dt = dl.find("dt")
        dd = dl.find("dd")
        if not dt or not dd:
            continue

        key = clean(dt.get_text())

        tooltip_wrap = dd.select_one(".toolTipWrap")
        details = []
        if tooltip_wrap:
            for li in tooltip_wrap.select(".toolTipCont li"):
                label_tag = li.find("span")
                if label_tag:
                    label = clean(label_tag.get_text())
                    label_tag.extract()
                    value = clean(li.get_text())
                    details.append({label: value})
                else:
                    details.append(clean(li.get_text()))
            tooltip_wrap.extract()

        for btn in dd.select("button"):
            btn.extract()

        main_text = clean(dd.get_text(" "))
        result[key] = details if details else main_text

        if key == "근무지역":
            break

    return result

def scrape_job_posting(url):
    rec_idx = parse_qs(urlparse(url).query)["rec_idx"][0]

    session = requests.Session()
    session.headers.update({
        "accept": "text/html, */*; q=0.01",
        "accept-language": "ko",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
        "x-requested-with": "XMLHttpRequest",
    })

    result = {
        "title": None,
        "input_type": "url",
        "source_url": url,
        "raw_content": None,
        "conts_summary": None
        }

    # ---- 1번째 요청: view-ajax (POST) → 요약 정보 테이블 ----
    ajax_url = "https://www.saramin.co.kr/zf_user/jobs/relay/view-ajax"
    ajax_payload = {
        "rec_idx": rec_idx,
        "rec_seq": "0",
        "view_type": "list",
        "t_ref": "",
        "t_ref_content": "",
        "ref_dp": "SRI_050_VIEW_MIX_RCT_NONMEM",
    }

    try:
        res = session.post(
            ajax_url,
            data=ajax_payload,
            headers={
                "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
                "origin": "https://www.saramin.co.kr",
                "referer": url,
            },
        )
        res.raise_for_status()
        soup = BeautifulSoup(res.content, "html.parser", from_encoding="utf-8")
        title = soup.select("h1.tit_job")[0].text.strip()
        result["title"] = title
        # cont = soup.select_one("div.cont")

        # if cont:
        #     lines = [l.strip() for l in cont.text.split("\n") if l.strip()]
        #     detail = {}
        #     for i in range(1, len(lines), 2):
        #         detail[lines[i - 1]] = lines[i]
        #         if lines[i - 1] == "근무형태":
        #             break
        #     result["conts_summary"] = lines

        details = parse_summary(soup)
        result["conts_summary"] = details

    except Exception as e:
        print(f"[view-ajax 실패] {rec_idx}: {e}")

    # ---- 2번째 요청: view-detail (GET) → 실제 본문 ----
    detail_url = (
        "https://www.saramin.co.kr/zf_user/jobs/relay/view-detail"
        f"?rec_idx={rec_idx}&rec_seq=0"
        "&t_category=non-logged_relay_view&t_content=view_detail"
        "&t_ref=&t_ref_content="
    )

    try:
        res = session.get(detail_url, headers={"referer": url})
        res.raise_for_status()
        detail_soup = BeautifulSoup(res.content, "html.parser", from_encoding="utf-8")

        # 가능한 텍스트 컨테이너 순서대로 시도
        SELECTORS = [
            "div.user_content div.job-content",
            "div.user_content div.content",
            "div.user_content",
            "div#job_detail_description",
            "div.wrap_jd_cont",
            "section.jv_cont",
            "div.jv_cont",
        ]
        data = ""
        contents = None
        for sel in SELECTORS:
            node = detail_soup.select_one(sel)
            if node:
                text = node.get_text(separator="\n", strip=True).replace("\xa0", " ")
                if len(text) > len(data):
                    data = text
                    contents = node

        # 텍스트가 충분하지 않으면 이미지 URL 수집
        if len(data) < 100:
            img_container = detail_soup.select_one("div.user_content") or detail_soup.body
            images = img_container.select("img") if img_container else []
            img_urls = []
            for img in images:
                src = img.get("src") or img.get("data-src")
                if not src:
                    continue
                if src.startswith("//"):
                    src = "https:" + src
                if src.startswith("http"):
                    img_urls.append(src)
            data = img_urls if img_urls else data

        result["raw_content"] = data
        print(f"[scrape] raw_content 타입={type(data).__name__}, 크기={len(data)}")

    except Exception as e:
        print(f"[view-detail 실패] {rec_idx}: {e}")

    return result

# 지금까지 관찰된 채용공고 1건당 최대 "원본" 이미지 수. 초과해도 막지는 않고 경고만 출력.
OBSERVED_MAX_IMAGES = 5

# 세로 분할 기준. 이 값보다 세로가 길면 조각냄.
DEFAULT_MAX_CHUNK_HEIGHT = 1500
DEFAULT_OVERLAP = 150  # 조각 사이에 겹치는 픽셀 (경계에서 글자 잘리는 것 방지)

# 이미지가 "긴지" 판단할 때 헤더 확인용으로 받아오는 바이트 수.
# PNG/JPEG 헤더는 보통 이 정도면 충분히 파싱됨.
DEFAULT_PROBE_BYTES = 131072  # 128KB

# ---------------------------------------------------------------------------
# 핵심: "원문 그대로" 뽑아내기 위한 프롬프트.
# ---------------------------------------------------------------------------
EXTRACTION_PROMPT = """You are a strict OCR engine extracting text from images of a Korean job posting.
Treat this as pure character-level transcription, NOT content editing or proofreading.

If multiple images are given, they are parts of ONE job posting, ordered top to bottom.

CRITICAL RULE — NEVER SUBSTITUTE A WORD WITH A DIFFERENT WORD:
- Do not replace a word with another word that seems more common, more "correct,"
  or more contextually expected — even if the word in the image looks unusual or
  like uncommon jargon.
- This especially applies to technical terms and loanwords (외래어). For example,
  if the image actually shows "프록시" (proxy), you must output "프록시" — do NOT
  output "프로토콜" (protocol) or any other word just because it is statistically
  more common in IT job postings. Trust the pixels, not your assumption about
  which word is more likely to appear.
- Do not normalize spelling, spacing, or terminology to what you think is "standard."
- This rule is ABSOLUTE for IT / technical jargon (programming languages, frameworks,
  protocols, tools, acronyms, English loanwords written in Korean, etc.). Even if a
  technical term looks like it might be a typo or misspelling, transcribe it EXACTLY
  as shown. Do NOT "fix" it to the term you think was intended. The job posting itself
  may contain a real typo, and your job is to faithfully reproduce the source document,
  not to correct it. Getting a typo "right" by copying it exactly is correct behavior;
  silently fixing it is a transcription error.
- If a character is genuinely blurry, cut off, or obscured, mark only that exact
  spot as [판독불가]. Do NOT use [판독불가] as a substitute for a word that is
  legible but unfamiliar to you — if you can actually read it, transcribe it
  exactly, no matter how unusual it seems.

OTHER RULES:
1. Do not summarize or paraphrase anything. Copy the exact wording, character by character.
2. If there are multiple images, merge them in order into one continuous text.
   Do not add labels like "Image 1" / "이미지 1" or separators between them.
3. If content overlaps between images (from scroll captures or image splitting),
   include the overlapping part only once.
4. Preserve original line breaks, indentation, and bullet/list markers
   (•, -, ①, * etc.) exactly as shown.
5. If there is a table, preserve its row/column structure as closely as possible
   (markdown table format is fine).
6. Do not add anything that is not actually visible in the image.
7. Do not add any commentary, explanation, or summary. Output ONLY the extracted text.
8. Ignore non-text elements such as logos and decorative photos.
9. Output the text in its original language, exactly as written — this is a
   Korean job posting, so the output must be in Korean exactly as shown. Do not translate.

Output only the extracted raw text:"""

_REQUEST_HEADERS = {"User-Agent": "Mozilla/5.0"}


def _load_image_bytes(image_source: Union[str, Path], is_url: bool) -> bytes:
    """로컬 경로 또는 URL에서 이미지 전체 바이트를 읽어온다."""
    if is_url:
        resp = requests.get(str(image_source), timeout=15, headers=_REQUEST_HEADERS)
        resp.raise_for_status()
        return resp.content
    with open(image_source, "rb") as f:
        return f.read()


def _probe_remote_image(
    url: str, probe_bytes: int = DEFAULT_PROBE_BYTES
) -> tuple[int, bytes, bool]:
    """
    이미지 전체를 받지 않고 Range 요청으로 앞부분만 받아서 세로 길이를 확인한다.
    서버가 Range를 지원하지 않으면 어차피 전체가 오므로 재다운로드 없이 재사용한다.

    Returns:
        (height, data, is_full)
        - height: 파싱 성공한 세로 픽셀 길이. 실패하면 -1.
        - data: 받아온 바이트 (Range 지원 시 일부, 미지원 시 전체).
        - is_full: data가 이미지 전체 데이터인지 여부.
    """
    resp = requests.get(
        url,
        headers={**_REQUEST_HEADERS, "Range": f"bytes=0-{probe_bytes - 1}"},
        timeout=10,
    )
    resp.raise_for_status()
    is_full = resp.status_code != 206
    data = resp.content
    try:
        height = Image.open(BytesIO(data)).size[1]
    except Exception:
        height = -1
    return height, data, is_full


def _prepare_image_parts(
    image_bytes: bytes,
    max_chunk_height: int = DEFAULT_MAX_CHUNK_HEIGHT,
    overlap: int = DEFAULT_OVERLAP,
) -> list[tuple[bytes, str]]:
    """
    이미지가 충분히 짧으면 원본 그대로 반환.
    세로로 너무 길면 max_chunk_height 단위로 겹치게 잘라서 여러 조각으로 반환.
    """
    img = Image.open(BytesIO(image_bytes))
    width, height = img.size
    original_mime = Image.MIME.get(img.format, "image/png")

    if height <= max_chunk_height:
        return [(image_bytes, original_mime)]

    parts: list[tuple[bytes, str]] = []
    top = 0
    while top < height:
        bottom = min(top + max_chunk_height, height)
        chunk = img.convert("RGB").crop((0, top, width, bottom))
        buf = BytesIO()
        chunk.save(buf, format="PNG")
        parts.append((buf.getvalue(), "image/png"))
        if bottom >= height:
            break
        top = bottom - overlap

    return parts

def _bytes_to_data_url(image_bytes: bytes, mime_type: str) -> str:
    encoded = base64.b64encode(image_bytes).decode("utf-8")
    return f"data:{mime_type};base64,{encoded}"

def extract_job_posting_text(
    image_sources: Union[Union[str, Path], list[Union[str, Path]]],
    model: str = "gpt-4o-mini",
    is_url: bool = False,
    temperature: float = 0,
    max_chunk_height: int = DEFAULT_MAX_CHUNK_HEIGHT,
    overlap: int = DEFAULT_OVERLAP,
) -> str:
    
    if not image_sources:
        raise ValueError("image_sources가 비어 있습니다.")

    if isinstance(image_sources, (str, Path)):
        image_sources = [image_sources]

    if len(image_sources) > OBSERVED_MAX_IMAGES:
        print(
            f"경고: 원본 이미지가 {len(image_sources)}장입니다. "
            f"지금까지 관찰된 최대치({OBSERVED_MAX_IMAGES}장)를 초과합니다. "
            "그래도 처리는 진행합니다."
        )

    llm = ChatOpenAI(model=model, temperature=temperature)
    content = [{"type": "text", "text": EXTRACTION_PROMPT}]

    for src in image_sources:
        if is_url:
            height, probe_data, is_full = _probe_remote_image(str(src))
            if height != -1 and height <= max_chunk_height:
                # 짧은 이미지: 다운로드/재인코딩 없이 URL을 그대로 전달 (빠른 경로)
                content.append({"type": "image_url", "image_url": {"url": str(src)}})
                continue
            # 길거나 헤더 파싱 실패 -> 분할이 필요할 수 있으니 전체를 확보
            raw_bytes = probe_data if is_full else _load_image_bytes(src, is_url=True)
        else:
            raw_bytes = _load_image_bytes(src, is_url=False)

        parts = _prepare_image_parts(
            raw_bytes, max_chunk_height=max_chunk_height, overlap=overlap
        )
        for chunk_bytes, mime_type in parts:
            data_url = _bytes_to_data_url(chunk_bytes, mime_type)
            content.append({"type": "image_url", "image_url": {"url": data_url}})

    message = HumanMessage(content=content)
    response = llm.invoke([message])
    return response.content.strip()

def job_posting_formating(
        title:str,
        summary:str,
        raw_posting:str,
        model: str = "gpt-4o-mini",
        temperature: float = 0,
        ) -> dict:
    
    prompt = ChatPromptTemplate.from_template(
"""
공고문을 보고 채용 조건을 JSON으로 분류해줘.

가장 중요한 규칙:
- "requirement", "skill_stack", "task", "preference" 4개 필드는 반드시 값을 채워야 한다.
- 원문에 정확히 같은 제목이 없어도, 의미상 해당하는 내용을 찾아서 반드시 분류한다.
- 단, 원문에 전혀 근거가 없는 내용을 새로 만들면 안 된다.
- 내용이 부족하면 title, summary, raw_posting 전체를 함께 보고 추론 가능한 범위에서 분류한다.
- 그래도 정말 근거가 없으면 "확인 필요"라고 적는다.
- 절대 빈 문자열 ""로 두지 마라.

분류 기준:
career:
- 경력 조건
- 예: 신입, 경력 3년 이상, 무관

education:
- 학력 조건
- 예: 학력무관, 대졸 이상

requirement:
- 지원자가 반드시 갖춰야 하는 자격조건
- 전공 조건
- 필수 경험
- 필수 기술 사용 경험
- 경력/학력 외의 지원 필수 조건

skill_stack:
- 공고에 등장하는 기술, 언어, 프레임워크, DB, 클라우드, 협업 도구
- 예: Java, Python, Spring, React, Docker, AWS, PostgreSQL, Git

task:
- 입사 후 실제 수행할 주요업무
- 예: API 개발, 서비스 유지보수, 데이터 파이프라인 구축, 운영 자동화

preference:
- 우대사항
- "우대", "선호", "~경험자", "~가능자"처럼 필수가 아닌 가산 조건
- 우대사항이 명시되어 있으면 원문에 있는 우대사항만 넣는다.
- 우대사항이 명시되어 있지 않으면 "확인 필요"라고 적는다.

field:
- 산업/도메인
- 예: 의료, 교육, 커머스, 요식업, 금융

금지 규칙:
- 카테고리 간 조건을 이동하지 마라.
- 우대사항에 없는 조건을 임의로 추가하지 마라.
- 원문에 없는 기술스택을 상상해서 추가하지 마라.
- JSON 밖에 설명을 붙이지 마라.
- 마크다운 코드블록을 쓰지 마라.
- OUTPUT: 같은 접두어를 붙이지 마라.

INPUT:
title:
{title}

summary:
{summary}

raw_posting:
{raw_posting}

반드시 아래 JSON 형식으로만 답변해라.

{{
    "career": "",
    "education": "",
    "requirement": [],
    "skill_stack": [],
    "task": [],
    "preference": [],
    "field": []
}}
"""
    )
    llm = ChatOpenAI(model=model, temperature=temperature)
    chain = prompt | llm | JsonOutputParser()
    result = chain.invoke({"title": title, "summary": summary, "raw_posting": raw_posting})

    return result