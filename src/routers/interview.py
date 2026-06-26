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

@router.post("/applicants/{applicant_id}/interview-questions")
async def generate_interview_questions(applicant_id: int, body: GenerateRequest):
    """
    1. 지원자 이력서 + 공고문 조회
    2. RAG로 공정채용 법령 컨텍스트 검색
    3. GPT-4o-mini에게 질문 생성 + 법령 준수 검수를 한 번에 요청
    4. DB 저장
    """

    # 1) 지원자 정보
    applicant_res = supabase.table("applicants").select("*").eq("id", applicant_id).execute()
    if not applicant_res.data:
        raise HTTPException(status_code=404, detail="지원자를 찾을 수 없습니다.")
    applicant = applicant_res.data[0]
    job_posting_id = applicant.get("job_posting_id")

    # 2) 채용 공고
    job_info = "채용 공고 정보 없음"
    if job_posting_id:
        try:
            job_res = supabase.table("job_postings").select("*").eq("id", job_posting_id).execute()
            if job_res.data:
                jp = job_res.data[0]
                job_info = f"제목: {jp.get('title', '')}\n\n{jp.get('raw_content', '')[:3000]}"
        except Exception:
            pass

    # 3) 이력서 텍스트
    resume_text = "이력서 정보 없음"
    try:
        # resumes 테이블에서 job_posting_id로 조회
        resume_res = supabase.table("resumes") \
            .select("resume_text, original_filename") \
            .eq("job_posting_id", job_posting_id) \
            .execute()
        if resume_res.data:
            texts = [r.get("resume_text", "") or "" for r in resume_res.data if r.get("resume_text")]
            if texts:
                resume_text = "\n\n---\n\n".join(texts[:3])  # 최대 3개
    except Exception:
        pass

    # resume_files 테이블 fallback (extracted_text)
    if resume_text == "이력서 정보 없음":
        try:
            rf_res = supabase.table("resume_files") \
                .select("extracted_text, original_filename") \
                .eq("applicant_id", applicant_id) \
                .execute()
            if rf_res.data:
                texts = [r.get("extracted_text", "") or "" for r in rf_res.data if r.get("extracted_text")]
                if texts:
                    resume_text = "\n\n---\n\n".join(texts[:3])
        except Exception:
            pass

    # 4) RAG: 공정채용 법령 검색
    rag_query = "면접 질문 금지 항목 개인정보 블라인드 채용 위반 출신지역 가족관계 혼인 나이 성별"
    law_context = await retrieve(rag_query, n_results=6)

    if not law_context:
        # RAG 문서 없을 때 핵심 법령 하드코딩 fallback
        law_context = """[채용절차법 제4조의3] 구인자는 직무 수행에 필요하지 않은 다음 정보를 요구할 수 없다:
1. 용모·키·체중 등 신체적 조건
2. 출신지역·혼인여부·재산
3. 직계 존비속 및 형제자매의 학력·직업·재산

[블라인드 채용 위반 기준]
- 출신학교를 유추할 수 있는 질문 금지
- 출신지역 유추 가능 질문 금지
- 가족관계 유추 가능 질문 금지
- 생년월일·연령 유추 가능 질문 금지
- 성별 유추 가능 질문 금지 (군복무, 여대 졸업 등)
- 종교, 정치적 성향, 임신·출산 계획 질문 금지"""

    # 5) GPT-4o-mini 호출
    openai_key = os.getenv("OPENAI_API_KEY")
    if not openai_key:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY가 설정되지 않았습니다.")

    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    question_types_str = ", ".join(body.question_types)

    system_prompt = f"""당신은 공정채용 전문가이자 HR 면접 질문 설계자입니다.
채용 공고와 지원자 이력서를 분석하여 맞춤 면접 질문을 생성하고,
아래 공정채용 법령 기준에 따라 각 질문의 법령 준수 여부를 판단합니다.

=== 공정채용 법령 기준 (RAG 검색 결과) ===
{law_context}

=== compliance_status 판단 기준 ===
- "준수": 직무와 관련된 질문, 법령 위반 없음
- "경고": 개인 신상을 간접적으로 유추할 수 있거나 주의가 필요한 질문
- "심각": 채용절차법 제4조의3 또는 블라인드 채용 가이드라인을 명백히 위반하는 질문
  (출신지역, 가족관계, 혼인여부, 나이, 성별, 신체조건, 학벌 직접 질문 등)

=== 출력 형식 ===
반드시 JSON 배열만 출력하세요. 마크다운, 설명, 코드블록 없이 순수 JSON 배열만:
[
  {{
    "question_type": "행동 또는 역량 또는 우려검증 또는 기술검증 또는 기타",
    "question_text": "질문 내용",
    "compliance_status": "준수 또는 경고 또는 심각",
    "compliance_reason": "법령 준수 판단 근거 한 줄"
  }}
]"""

    user_prompt = f"""아래 정보를 바탕으로 면접 질문 {body.question_count}개를 생성하고 법령 준수 여부를 판단하세요.
질문 유형: [{question_types_str}] 에서 골고루 선택

=== 채용 공고 ===
{job_info}

=== 지원자 이력서 ===
{resume_text[:2000]}

반드시 정확히 {body.question_count}개의 질문을 JSON 배열로 출력하세요. {body.question_count}개 미만이면 안 됩니다."""

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
                    "temperature": 0.7
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

    # 6) 기존 질문 삭제
    try:
        supabase.table("interview_questions").delete().eq("applicant_id", applicant_id).execute()
    except Exception:
        pass

    # 7) DB 저장
    valid_statuses = {"준수", "경고", "심각"}
    valid_types = {"행동", "역량", "우려검증", "기술검증", "기타"}

    inserted = []
    for q in questions_list[:body.question_count]:
        raw_type = (q.get("question_type") or "기타").replace(" ", "")
        compliance = q.get("compliance_status", "준수")

        row = {
            "applicant_id": applicant_id,
            "question_type": raw_type if raw_type in valid_types else "기타",
            "question_text": q.get("question_text", ""),
            "created_by": "AI",
            "compliance_status": compliance if compliance in valid_statuses else "준수",
            "revised_question_text": None
        }
        print(f"[DEBUG] INSERT 시도: {row}")
        try:
            ins = supabase.table("interview_questions").insert(row).execute()
            print(f"[DEBUG] INSERT 결과: {ins.data}")
            if ins.data:
                ins.data[0]["compliance_reason"] = q.get("compliance_reason", "")
                inserted.append(ins.data[0])
        except Exception as e:
            print(f"[ERROR] 질문 삽입 실패 (applicant_id={applicant_id}): {type(e).__name__}: {e}")

    return {
        "success": True,
        "message": f"면접 질문 {len(inserted)}개 생성 완료 (RAG 검수 적용)",
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
