"""
면접 질문 생성/조회/수정 라우터
- GET   /api/v1/applicants/{applicant_id}/interview-questions
- POST  /api/v1/applicants/{applicant_id}/interview-questions
- PATCH /api/v1/interview-questions/{question_id}
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List
from database import supabase
from rag.store import retrieve  # async
import os
import json
import httpx

router = APIRouter(prefix="/api/v1", tags=["interview"])


# ---------- Models ----------

class GenerateRequest(BaseModel):
    question_count: int = 5
    question_types: List[str] = ["행동", "역량", "우려검증", "기술검증", "기타"]


class UpdateQuestionRequest(BaseModel):
    question_text: str

class AddQuestionRequest(BaseModel):
    question_type: str = "기타"
    question_text: str
    compliance_status: str = "준수"
    created_by: str = "USER"


# ---------- GET ----------

@router.get("/applicants/{applicant_id}/interview-questions")
async def get_interview_questions(applicant_id: int):
    result = supabase.table("interview_questions") \
        .select("*") \
        .eq("applicant_id", applicant_id) \
        .order("created_at", desc=False) \
        .execute()

    questions = result.data or []
    total = len(questions)

    for idx, q in enumerate(questions):
        # DB 띄어쓰기 normalize
        if q.get("question_type"):
            q["question_type"] = q["question_type"].replace(" ", "")
        # importance 계산
        if total == 0:
            q["importance"] = 2
        elif idx < total * 0.4:
            q["importance"] = 3
        elif idx < total * 0.8:
            q["importance"] = 2
        else:
            q["importance"] = 1

    return {"success": True, "data": questions}


# ---------- POST: RAG 검수 + GPT-4o-mini 질문 생성 ----------

# 공정채용 법령 — 매번 RAG 조회 대신 핵심만 하드코딩 (RAG 지연 제거)
_LAW_CONTEXT = """금지 질문: 결혼·혼인·출산·임신·육아·나이·고향·출신지역·가족관계·종교·정치·신체조건·학벌
경고 질문: 야근가능여부·주말근무·지방발령·군복무"""

@router.post("/applicants/{applicant_id}/interview-questions")
async def generate_interview_questions(applicant_id: int, body: GenerateRequest):
    import asyncio

    openai_key = os.getenv("OPENAI_API_KEY")
    if not openai_key:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY가 설정되지 않았습니다.")

    # 1) DB 쿼리 병렬 실행 (순차→동시)
    def _get_applicant():
        return supabase.table("applicants").select("*").eq("id", applicant_id).execute()

    def _get_resume_files():
        return supabase.table("resume_files").select("extracted_text").eq("applicant_id", applicant_id).execute()

    loop = asyncio.get_event_loop()
    applicant_res, rf_res = await asyncio.gather(
        loop.run_in_executor(None, _get_applicant),
        loop.run_in_executor(None, _get_resume_files),
    )

    if not applicant_res.data:
        raise HTTPException(status_code=404, detail="지원자를 찾을 수 없습니다.")
    applicant = applicant_res.data[0]
    job_posting_id = applicant.get("job_posting_id")

    # 2) 공고 + 이력서 병렬 조회
    def _get_job():
        if not job_posting_id:
            return None
        return supabase.table("job_postings").select("title, raw_content").eq("id", job_posting_id).execute()

    def _get_resumes():
        if not job_posting_id:
            return None
        return supabase.table("resumes").select("resume_text").eq("job_posting_id", job_posting_id).execute()

    job_res, resume_res = await asyncio.gather(
        loop.run_in_executor(None, _get_job),
        loop.run_in_executor(None, _get_resumes),
    )

    # 공고 텍스트 (800자로 축약 — 핵심만)
    job_info = "채용 공고 정보 없음"
    if job_res and job_res.data:
        jp = job_res.data[0]
        raw = jp.get("raw_content") or ""
        if isinstance(raw, list):
            raw = " ".join(str(r) for r in raw)
        job_info = f"{jp.get('title', '')}\n{raw[:800]}"

    # 이력서 텍스트 (800자로 축약)
    resume_text = ""
    if resume_res and resume_res.data:
        texts = [r.get("resume_text") or "" for r in resume_res.data if r.get("resume_text")]
        resume_text = "\n".join(texts[:2])[:800]
    if not resume_text and rf_res and rf_res.data:
        texts = [r.get("extracted_text") or "" for r in rf_res.data if r.get("extracted_text")]
        resume_text = "\n".join(texts[:2])[:800]
    if not resume_text:
        resume_text = "이력서 정보 없음"

    # 3) GPT-4o-mini 호출 — 경량 프롬프트
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    question_types_str = ", ".join(body.question_types)

    system_prompt = f"""HR 면접 질문 전문가. JSON 배열만 출력.
금지({_LAW_CONTEXT})에 해당하면 compliance_status="심각", 우회표현이면"경고", 직무관련이면"준수".
출력형식(마크다운·설명 금지):
[{{"question_type":"행동|역량|우려검증|기술검증|기타","question_text":"질문","compliance_status":"준수|경고|심각","compliance_reason":"한줄근거"}}]"""

    user_prompt = f"""공고: {job_info}
이력서: {resume_text}
유형[{question_types_str}]에서 골고루 정확히 {body.question_count}개 생성."""

    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {openai_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    "temperature": 0.3,
                    "max_tokens": 1500
                }
            )

        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail=f"OpenAI 오류: {resp.status_code}")

        content = resp.json()["choices"][0]["message"]["content"]
        import sys
        sys.stderr.write(f"\n[GPT RAW]\n{content}\n[/GPT RAW]\n")
        sys.stderr.flush()

        # JSON 배열 추출 (GPT가 마크다운 코드블록으로 감쌀 수 있음)
        import re
        json_match = re.search(r'\[.*\]', content, re.DOTALL)
        if json_match:
            questions_list = json.loads(json_match.group())
        else:
            parsed = json.loads(content)
            if isinstance(parsed, dict):
                questions_list = next((v for v in parsed.values() if isinstance(v, list)), [])
            elif isinstance(parsed, list):
                questions_list = parsed
            else:
                questions_list = []

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"GPT 호출 실패: {str(e)}")

    # 6) 기존 질문 삭제 + 신규 일괄 INSERT 병렬 실행
    valid_statuses = {"준수", "경고", "심각"}
    valid_types = {"행동", "역량", "우려검증", "기술검증", "기타"}

    rows = []
    compliance_reasons = []
    for q in questions_list[:body.question_count]:
        raw_type = (q.get("question_type") or "기타").replace(" ", "")
        compliance = q.get("compliance_status", "준수")
        rows.append({
            "applicant_id": applicant_id,
            "question_type": raw_type if raw_type in valid_types else "기타",
            "question_text": q.get("question_text", ""),
            "created_by": "AI",
            "compliance_status": compliance if compliance in valid_statuses else "준수",
            "revised_question_text": None
        })
        compliance_reasons.append(q.get("compliance_reason", ""))

    def _delete_and_insert():
        supabase.table("interview_questions").delete().eq("applicant_id", applicant_id).execute()
        if rows:
            return supabase.table("interview_questions").insert(rows).execute()
        return None

    ins_res = await loop.run_in_executor(None, _delete_and_insert)

    inserted = []
    if ins_res and ins_res.data:
        for i, row_data in enumerate(ins_res.data):
            row_data["compliance_reason"] = compliance_reasons[i] if i < len(compliance_reasons) else ""
            inserted.append(row_data)

    return {
        "success": True,
        "message": f"면접 질문 {len(inserted)}개 생성 완료",
        "applicant_id": applicant_id,
        "data": inserted
    }

# ---------- PATCH ----------

@router.patch("/interview-questions/{question_id}")
async def update_interview_question(question_id: int, body: UpdateQuestionRequest):
    try:
        result = supabase.table("interview_questions") \
            .update({
                "revised_question_text": body.question_text,
                "question_text": body.question_text
            }) \
            .eq("id", question_id) \
            .execute()

        if not result.data:
            raise HTTPException(status_code=404, detail="질문을 찾을 수 없습니다.")

        return {"success": True, "data": result.data[0]}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"수정 실패: {str(e)}")


# ---------- DELETE ----------

@router.delete("/interview-questions/{question_id}")
async def delete_interview_question(question_id: int):
    try:
        result = supabase.table("interview_questions").delete().eq("id", question_id).execute()
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"삭제 실패: {str(e)}")


# ---------- POST: 단일 질문 직접 추가 ----------

@router.post("/applicants/{applicant_id}/interview-questions/add")
async def add_interview_question(applicant_id: int, body: AddQuestionRequest):
    valid_types = {"행동", "역량", "우려검증", "기술검증", "기타"}
    valid_statuses = {"준수", "경고", "심각"}

    row = {
        "applicant_id": applicant_id,
        "question_type": body.question_type if body.question_type in valid_types else "기타",
        "question_text": body.question_text,
        "created_by": body.created_by,
        "compliance_status": body.compliance_status if body.compliance_status in valid_statuses else "준수",
        "revised_question_text": None
    }

    try:
        res = supabase.table("interview_questions").insert(row).execute()
        if not res.data:
            raise HTTPException(status_code=500, detail="질문 저장 실패")
        return {"success": True, "data": res.data[0]}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"질문 추가 실패: {str(e)}")
