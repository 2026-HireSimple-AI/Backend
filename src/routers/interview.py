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

class BulkGenerateRequest(BaseModel):
    applicant_ids: List[int]
    question_count: int = 5
    question_types: List[str] = ["행동", "역량", "우려검증", "기술검증", "기타"]
    force: bool = False  # True이면 기존 질문 삭제 후 재생성


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

# 질문 유형 정의 — LLM이 정확하게 분류하도록 프롬프트에 주입
_TYPE_DEFINITIONS = """질문 유형 정의 (반드시 아래 기준에 따라 분류):
- 행동: 과거 경험 기반. "~했던 경험", "~했을 때 어떻게", 갈등·협업·문제해결·성과 관련 행동 중심 질문
- 역량: 직무 전문성·실무 능력 평가. "설계한다면", "노하우", "접근 방식" 등 능력의 깊이를 측정하는 질문
- 우려검증: 이력서 공백·잦은 이직·기술 부족 등 리스크 확인. "짧은 재직", "공백기", "부족한 부분" 관련 질문
- 기술검증: CS 기초(자료구조·알고리즘·네트워크·DB)·코딩·특정 기술 개념을 객관적으로 묻는 질문
- 기타: 지원동기·커리어목표·문화적합도 등 위 4가지에 해당하지 않는 경우에만 사용 (최소화할 것)"""

def _build_system_prompt() -> str:
    return f"""HR 면접 질문 전문가. JSON 배열만 출력. 마크다운·설명 금지.

{_TYPE_DEFINITIONS}

공정채용 법령 준수:
{_LAW_CONTEXT}
→ 금지 질문이면 compliance_status="심각", 우회표현이면"경고", 직무관련이면"준수"

출력형식:
[{{"question_type":"행동|역량|우려검증|기술검증|기타","question_text":"질문","compliance_status":"준수|경고|심각","compliance_reason":"한줄근거"}}]"""

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

    # 3) GPT-4o-mini 호출
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    question_types_str = ", ".join(body.question_types)
    system_prompt = _build_system_prompt()
    user_prompt = f"""공고: {job_info}
이력서: {resume_text}
요청 유형[{question_types_str}]에서 골고루 정확히 {body.question_count}개 생성. 반드시 {body.question_count}개를 모두 포함해야 하며 더 적거나 많으면 안 됨. 기타 유형은 위 4가지에 모두 해당하지 않을 때만 사용."""

    max_tokens = max(1500, body.question_count * 120)

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
                    "max_tokens": max_tokens
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


# ---------- POST: 일괄 생성 (분석 페이지 → 인원 확정 시) ----------

@router.post("/interview-questions/bulk-generate")
async def bulk_generate_interview_questions(body: BulkGenerateRequest):
    """
    여러 지원자 면접 질문을 동시에 생성.
    이미 질문이 있는 지원자는 건너뜀.
    """
    import asyncio

    openai_key = os.getenv("OPENAI_API_KEY")
    if not openai_key:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY가 설정되지 않았습니다.")

    if not body.applicant_ids:
        return {"success": True, "results": [], "generated": 0, "skipped": 0}

    async def _generate_one(applicant_id: int) -> dict:
        loop = asyncio.get_event_loop()

        # 이미 질문 존재 시 처리
        def _check_existing():
            return supabase.table("interview_questions")\
                .select("id").eq("applicant_id", applicant_id).limit(1).execute()

        existing = await loop.run_in_executor(None, _check_existing)
        if existing.data:
            if not body.force:
                return {"applicant_id": applicant_id, "status": "skipped"}
            # force=True 이면 기존 질문 전부 삭제
            def _delete_existing():
                return supabase.table("interview_questions")\
                    .delete().eq("applicant_id", applicant_id).execute()
            await loop.run_in_executor(None, _delete_existing)

        # 지원자 + 이력서 조회 (HTTP/2 커넥션 충돌 방지를 위해 순차 실행)
        applicant_res = await loop.run_in_executor(
            None, lambda: supabase.table("applicants").select("*").eq("id", applicant_id).execute()
        )
        rf_res = await loop.run_in_executor(
            None, lambda: supabase.table("resume_files").select("extracted_text").eq("applicant_id", applicant_id).execute()
        )

        if not applicant_res.data:
            return {"applicant_id": applicant_id, "status": "error", "detail": "지원자 없음"}

        applicant = applicant_res.data[0]
        job_posting_id = applicant.get("job_posting_id")

        job_res = None
        resume_res = None
        if job_posting_id:
            job_res = await loop.run_in_executor(
                None, lambda: supabase.table("job_postings").select("title, raw_content").eq("id", job_posting_id).execute()
            )
            resume_res = await loop.run_in_executor(
                None, lambda: supabase.table("resumes").select("resume_text").eq("job_posting_id", job_posting_id).execute()
            )

        job_info = "채용 공고 정보 없음"
        if job_res and job_res.data:
            jp = job_res.data[0]
            raw = jp.get("raw_content") or ""
            if isinstance(raw, list):
                raw = " ".join(str(r) for r in raw)
            job_info = f"{jp.get('title', '')}\n{raw[:800]}"

        resume_text = ""
        if resume_res and resume_res.data:
            texts = [r.get("resume_text") or "" for r in resume_res.data if r.get("resume_text")]
            resume_text = "\n".join(texts[:2])[:800]
        if not resume_text and rf_res and rf_res.data:
            texts = [r.get("extracted_text") or "" for r in rf_res.data if r.get("extracted_text")]
            resume_text = "\n".join(texts[:2])[:800]
        if not resume_text:
            resume_text = "이력서 정보 없음"

        model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        question_types_str = ", ".join(body.question_types)

        system_prompt = _build_system_prompt()
        user_prompt = f"""공고: {job_info}
이력서: {resume_text}
요청 유형[{question_types_str}]에서 골고루 정확히 {body.question_count}개 생성. 반드시 {body.question_count}개를 모두 포함해야 하며 더 적거나 많으면 안 됨. 기타 유형은 위 4가지에 모두 해당하지 않을 때만 사용."""

        max_tokens = max(1500, body.question_count * 120)

        try:
            async with httpx.AsyncClient(timeout=90.0) as client:
                resp = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {openai_key}", "Content-Type": "application/json"},
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt}
                        ],
                        "temperature": 0.3,
                        "max_tokens": max_tokens
                    }
                )

            if resp.status_code != 200:
                return {"applicant_id": applicant_id, "status": "error", "detail": f"OpenAI {resp.status_code}"}

            content = resp.json()["choices"][0]["message"]["content"]
            import re
            json_match = re.search(r'\[.*\]', content, re.DOTALL)
            if json_match:
                questions_list = json.loads(json_match.group())
            else:
                parsed = json.loads(content)
                questions_list = parsed if isinstance(parsed, list) else []

        except Exception as e:
            return {"applicant_id": applicant_id, "status": "error", "detail": str(e)}

        valid_statuses = {"준수", "경고", "심각"}
        valid_types = {"행동", "역량", "우려검증", "기술검증", "기타"}
        rows = []
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

        def _insert():
            if rows:
                return supabase.table("interview_questions").insert(rows).execute()
            return None

        await loop.run_in_executor(None, _insert)
        return {"applicant_id": applicant_id, "status": "generated", "count": len(rows)}

    results = await asyncio.gather(*[_generate_one(aid) for aid in body.applicant_ids])
    results_list = list(results)

    generated = sum(1 for r in results_list if r["status"] == "generated")
    skipped = sum(1 for r in results_list if r["status"] == "skipped")
    errors = sum(1 for r in results_list if r["status"] == "error")

    return {
        "success": True,
        "results": results_list,
        "generated": generated,
        "skipped": skipped,
        "errors": errors
    }


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
