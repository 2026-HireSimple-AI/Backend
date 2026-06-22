"""
채용공고 URL + 이력서 PDF → OpenAI 분석 → Supabase 저장.
- POST /api/v1/analyze                     : 분석 실행 + DB 저장
- GET  /api/v1/job-postings/{id}           : 공고+분석 전체 조회
- GET  /api/v1/job-postings/{id}/criteria  : 평가 기준만 조회
- GET  /api/v1/job-postings/{id}/resumes   : 이력서 목록 조회
"""

from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from openai import OpenAI
import pdfplumber
from io import BytesIO
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
import os
import json

load_dotenv()

router = APIRouter(prefix="/api/v1", tags=["analyze"])


# ──────────────────────────────────────────────
# 유틸: Supabase 클라이언트
# ──────────────────────────────────────────────
def get_supabase():
    from database import supabase
    if not supabase:
        raise HTTPException(
            status_code=500,
            detail="Supabase가 설정되지 않았습니다. .env의 SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY를 확인하세요."
        )
    return supabase


# ──────────────────────────────────────────────
# 1. 채용공고 URL 스크래핑
# ──────────────────────────────────────────────
def scrape_job_posting(url: str) -> str:
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9",
        }
        resp = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
        resp.encoding = "utf-8"

        soup = BeautifulSoup(resp.text, "html.parser")

        for tag in soup(["script", "style", "header", "footer", "nav", "iframe", "noscript"]):
            tag.decompose()

        selectors = [
            ".jv_cont", ".jv_detail", ".job_detail_area",
            ".wrap_jv_cont", ".area_job", ".recruit-detail",
            ".job-article", ".job_summary",
            "article", "main", "#content", ".content",
        ]

        for sel in selectors:
            areas = soup.select(sel)
            if areas:
                text = "\n".join(a.get_text("\n", strip=True) for a in areas)
                if len(text) > 100:
                    return text[:8000]

        body = soup.find("body")
        text = body.get_text("\n", strip=True) if body else soup.get_text("\n", strip=True)
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        return "\n".join(lines)[:8000]

    except Exception as e:
        return f"채용공고 URL 스크래핑 실패: {str(e)}"


# ──────────────────────────────────────────────
# 2. PDF 텍스트 추출
# ──────────────────────────────────────────────
def extract_pdf_text(data: bytes) -> str:
    try:
        with pdfplumber.open(BytesIO(data)) as pdf:
            return "\n".join(p.extract_text() or "" for p in pdf.pages).strip()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"PDF 파싱 실패: {e}")


# ──────────────────────────────────────────────
# 3. OpenAI 분석
# ──────────────────────────────────────────────
def call_openai(job_text: str, resume_text: str) -> dict:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY가 설정되지 않았습니다.")

    client = OpenAI(api_key=api_key)

    prompt = f"""당신은 채용 분석 전문가 AI입니다.
아래 채용공고와 지원자 이력서를 꼼꼼히 분석하세요.

=== 채용공고 ===
{job_text[:6000]}

=== 지원자 이력서 ===
{resume_text[:3000]}

=== 분석 지침 ===
1. 채용공고에서 회사명과 직무명을 추출하세요.
2. 공고를 "자격 조건", "주요 업무", "우대 사항" 3개 카테고리로 구조화하세요.
3. 요구 기술 스택을 배열로 추출하세요.
4. 평가 기준(type_criteria)을 만드세요. 대분류 type_weight 총합 = 100, 각 대분류 내 소분류 weight 합 = 해당 type_weight.
5. 이력서를 공고 기준에 맞게 0~100점으로 평가하세요.
6. 이력서 요약과 매칭된 기술을 정리하세요.
7. 종합 평가 코멘트를 2~3문장으로 작성하세요.

아래 JSON 형식으로만 응답하세요:
{{
  "company_name": "회사명",
  "job_title": "직무명",
  "formatted_postings": [
    {{"category": "자격 조건", "content": "구체적 요건 나열"}},
    {{"category": "주요 업무", "content": "구체적 업무 나열"}},
    {{"category": "우대 사항", "content": "구체적 우대사항 나열"}}
  ],
  "skills_stack": ["기술1", "기술2"],
  "type_criteria": [
    {{
      "id": 1,
      "criterion_type": "자격 조건",
      "description": "평가 설명",
      "type_weight": 30,
      "detail_criteria": [
        {{"id": 1, "detail": "세부 항목", "weight": 15}},
        {{"id": 2, "detail": "세부 항목", "weight": 15}}
      ]
    }},
    {{
      "id": 2,
      "criterion_type": "주요 업무",
      "description": "평가 설명",
      "type_weight": 40,
      "detail_criteria": [
        {{"id": 3, "detail": "세부 항목", "weight": 20}},
        {{"id": 4, "detail": "세부 항목", "weight": 20}}
      ]
    }},
    {{
      "id": 3,
      "criterion_type": "우대 사항",
      "description": "평가 설명",
      "type_weight": 30,
      "detail_criteria": [
        {{"id": 5, "detail": "세부 항목", "weight": 15}},
        {{"id": 6, "detail": "세부 항목", "weight": 15}}
      ]
    }}
  ],
  "applicant_scores": {{
    "total_score": 80.0,
    "requirement_score": 85,
    "skill_score": 78,
    "task_score": 82,
    "preference_score": 70
  }},
  "resume_summary": {{
    "career_summary": "경력 요약 1~2문장",
    "project_summary": "프로젝트 경험 요약",
    "skill_summary": "보유 기술 요약"
  }},
  "matched_skills": ["매칭기술1", "매칭기술2"],
  "analysis_comment": "종합 평가 코멘트 (2~3문장)"
}}"""

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": "당신은 채용 분석 전문가입니다. 반드시 유효한 JSON 형식으로만 응답하세요.",
            },
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.3,
    )

    return json.loads(resp.choices[0].message.content)


# ──────────────────────────────────────────────
# 4. POST /api/v1/analyze — 분석 + Supabase 저장
# ──────────────────────────────────────────────
@router.post("/analyze")
def analyze(
    job_url: str = Form(...),
    resume: UploadFile = File(...),
):
    """채용공고 URL + 이력서 PDF → ChatGPT 분석 → Supabase 저장"""

    sb = get_supabase()

    # 1) 채용공고 스크래핑
    job_text = scrape_job_posting(job_url)
    if not job_text or len(job_text) < 20:
        raise HTTPException(status_code=400, detail="채용공고 내용을 가져올 수 없습니다. URL을 확인해 주세요.")

    # 2) PDF 텍스트 추출
    resume_bytes = resume.file.read()
    resume_text = extract_pdf_text(resume_bytes)
    if not resume_text:
        raise HTTPException(status_code=400, detail="이력서에서 텍스트를 추출할 수 없습니다.")

    # 3) OpenAI 분석
    try:
        analysis = call_openai(job_text, resume_text)
    except HTTPException:
        raise
    except Exception as e:
        raise e
        

    # 4) Supabase에 저장
    try:
        insert_result = sb.table("job_analyses").insert({
            "job_url":            job_url,
            "company_name":       analysis.get("company_name"),
            "job_title":          analysis.get("job_title"),
            "formatted_postings": analysis.get("formatted_postings", []),
            "skills_stack":       analysis.get("skills_stack", []),
            "type_criteria":      analysis.get("type_criteria", []),
            "applicant_scores":   analysis.get("applicant_scores", {}),
            "resume_summary":     analysis.get("resume_summary", {}),
            "matched_skills":     analysis.get("matched_skills", []),
            "analysis_comment":   analysis.get("analysis_comment"),
        }).execute()

        job_posting_id = insert_result.data[0]["id"]

        # 이력서 정보 저장
        sb.table("analysis_resumes").insert({
            "job_analysis_id":   job_posting_id,
            "original_filename": resume.filename,
            "resume_text":       resume_text,
            "processing_status": "uploaded",
        }).execute()

    except HTTPException:
        raise
    except Exception as e:
        raise e
        

    return {
        "success": True,
        "data": {
            "job_posting_id": job_posting_id,
            **analysis,
        },
    }


# ──────────────────────────────────────────────
# 5. GET /api/v1/job-postings/{id} — 전체 조회
# ──────────────────────────────────────────────
@router.get("/job-postings/{job_posting_id}")
def get_job_posting(job_posting_id: int):
    sb = get_supabase()
    result = sb.table("job_analyses").select("*").eq("id", job_posting_id).execute()

    if not result.data:
        raise HTTPException(status_code=404, detail="채용공고를 찾을 수 없습니다.")

    row = result.data[0]
    return {
        "success": True,
        "data": {
            "job_posting_id":     row["id"],
            "title":              f"{row['company_name']} - {row['job_title']}",
            "input_type":         "url",
            "source_url":         row["job_url"],
            "company_name":       row["company_name"],
            "job_title":          row["job_title"],
            "formatted_postings": row["formatted_postings"],
            "skills_stack":       row["skills_stack"],
            "type_criteria":      row["type_criteria"],
            "applicant_scores":   row["applicant_scores"],
            "resume_summary":     row["resume_summary"],
            "matched_skills":     row["matched_skills"],
            "analysis_comment":   row["analysis_comment"],
            "created_at":         row["created_at"],
        },
    }


# ──────────────────────────────────────────────
# 6. GET /api/v1/job-postings/{id}/criteria — 평가 기준
# ──────────────────────────────────────────────
@router.get("/job-postings/{job_posting_id}/criteria")
def get_criteria(job_posting_id: int):
    sb = get_supabase()
    result = sb.table("job_analyses").select("type_criteria").eq("id", job_posting_id).execute()

    if not result.data:
        raise HTTPException(status_code=404, detail="평가 기준을 찾을 수 없습니다.")

    return {
        "success": True,
        "data": {
            "type_criteria": result.data[0]["type_criteria"],
        },
    }


# ──────────────────────────────────────────────
# 7. GET /api/v1/job-postings/{id}/resumes — 이력서 목록
# ──────────────────────────────────────────────
@router.get("/job-postings/{job_posting_id}/resumes")
def get_resumes(job_posting_id: int):
    sb = get_supabase()
    result = sb.table("analysis_resumes").select("*").eq("job_analysis_id", job_posting_id).execute()

    files = [
        {
            "resume_file_id":    r["id"],
            "applicant_id":      r["id"],
            "original_filename": r["original_filename"],
            "processing_status": r["processing_status"],
        }
        for r in (result.data or [])
    ]

    return {
        "success": True,
        "data": {
            "uploaded_count": len(files),
            "files": files,
        },
    }
