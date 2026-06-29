from fastapi import APIRouter, HTTPException
from database import supabase

from langchain_openai import ChatOpenAI
from langchain_core.prompts import PromptTemplate

import json
import os
from dotenv import load_dotenv

router = APIRouter(
    prefix="/api/v1",
    tags=["resume-analysis"]
)

# LLM 초기화
llm = ChatOpenAI(
    model=os.getenv("OPENAI_MODEL"),
    temperature=0,
    openai_api_key=os.getenv("OPENAI_API_KEY")
)

@router.post("/applicants/{applicant_id}/analyze")
async def analyze_resume(applicant_id: int):
    """이력서 적합도 분석 실행"""

    # 1. 지원자 정보 조회
    applicant = supabase.table("applicants")\
        .select("*")\
        .eq("id", applicant_id)\
        .execute()
    
    if not applicant.data:
        raise HTTPException(status_code=404, detail="지원자가 없습니다.")
    
    job_posting_id = applicant.data[0]["job_posting_id"]

    # 2. 이력서 텍스트 조회
    resume = supabase.table("resume_files")\
        .select("*")\
        .eq("applicant_id", applicant_id)\
        .execute()

    if not resume.data or not resume.data[0].get("extracted_text"):
        raise HTTPException(status_code=404, detail="이력서 텍스트가 없습니다.")

    resume_text = resume.data[0]["extracted_text"]

    # 3. 공고 기술 스택 조회
    skills = supabase.table("skills_stack")\
        .select("*")\
        .eq("job_posting_id", job_posting_id)\
        .execute()

    # 4. 평가 기준 조회
    # type_criteria = supabase.table("type_criteria")\
    #     .select("*")\
    #     .eq("job_posting_id", job_posting_id)\
    #     .execute()

    # detail_criteria = supabase.table("detail_criteria")\
    #     .select("*")\
    #     .execute()

    # 4. 평가 기준 조회
    criteria = supabase.table("criteria")\
        .select("*")\
        .eq("job_posting_id", job_posting_id)\
        .execute()
    
    # 5. 기술 스택 비교 (텍스트 매칭)
    skill_names = [skill_name["skill_name"] for skill_name in skills.data]
    matched_skills= [skill_name for skill_name in skill_names if skill_name.lower() in resume_text.lower()]
    skill_score = round((len(matched_skills) / len(skill_names)) * 100, 2) if skill_names else 0

    # 6. 평가 기준 텍스트로 변환
    # criteria_text = ""
    # for tc in type_criteria.data:
    #     criteria_text += f"\n[{tc['criterion_type']}] 가중치: {tc['type_weight']}%\n"
    #     for dc in detail_criteria.data:
    #         if dc["type_criteria_id"] == tc["id"]:
    #             criteria_text += f"  - ID:{dc['id']} {dc['detail']} (가중치: {dc['weight']}%)\n"

    # 6. 평가 기준 텍스트로 변환
    criteria_text = ""
    for c in criteria.data:
        criteria_text += f"  - ID:{c['id']} [{c['criterion_type']}] {c['details']} (가중치: {c['type_weight']}%)\n"

    # 7. LLM 프롬프트 작성
    prompt = PromptTemplate(
        input_variables=["resume", "criteria"],
        template="""
당신은 채용 전문가입니다. 아래 이력서와 평가 기준을 보고 점수를 매겨주세요.

[이력서]
{resume}

[평가 기준]
{criteria}

아래 JSON 형식으로만 응답하세요. 다른 설명은 절대 하지 마세요.
{{
    "requirement_score": 자격조건 점수(0-100),
    "task_score": 주요업무 점수(0-100),
    "preference_score": 우대사항 점수(0-100),
    "detail_scores": [
        {{"detail_criteria_id": 세부기준ID, "score": 점수(0-100)}}
    ]
}}
"""
    )

    # 8. LLM 호출
    chain = prompt | llm
    result = chain.invoke({
        "resume": resume_text,
        "criteria": criteria_text
    })

    # 9. JSON 파싱
    try:
        scores = json.loads(result.content)
    except:
        raise HTTPException(status_code=500, detail="LLM 응답 파싱 실패")

    valid_detail_scores = []
    for ds in scores.get("detail_scores", []):
        try:
            detail_id = int(ds["detail_criteria_id"])  # 숫자로 변환
            valid_detail_scores.append({
                "detail_criteria_id": detail_id,
                "score": ds["score"]
            })
        except (ValueError, TypeError):
            continue  # 변환 안 되면 스킵

    scores["detail_scores"] = valid_detail_scores

    # 10. 종합 점수 계산
    total_score = round(
        (scores.get("requirement_score", 0) * 0.3) +
        (skill_score * 0.3) +
        (scores.get("task_score", 0) * 0.3) +
        (scores.get("preference_score", 0) * 0.1),
        2
    )
    
    # 11. applicant_scores 저장
    supabase.table("applicant_scores").upsert({
        "applicant_id": applicant_id,
        "job_posting_id": job_posting_id,
        "total_score": total_score,
        "requirement_score": scores.get("requirement_score", 0),
        "skill_score": skill_score,
        "task_score": scores.get("task_score", 0),
        "preference_score": scores.get("preference_score", 0),
    }).execute()

    # 12. detail_scores 저장
    # for ds in scores.get("detail_scores", []):
    #     supabase.table("detail_scores").upsert({
    #         "applicant_id": applicant_id,
    #         "type_criteria_id": next(
    #             (dc["type_criteria_id"] for dc in detail_criteria.data
    #                 if dc["id"] == ds["detail_criteria_id"]), None
    #         ),
    #         "detail_criteria_id": ds["detail_criteria_id"],
    #         "score": ds["score"]
    #     }).execute()

    # 12. detail_scores 저장
    for ds in scores.get("detail_scores", []):
        supabase.table("detail_scores").upsert({
            "applicant_id": applicant_id,
            "criteria_id": ds["detail_criteria_id"],
            "score": ds["score"]
        }).execute()

    return {
        "success": True,
        "data": {
            "applicant_id": applicant_id,
            "total_score": total_score,
            "skill_score": skill_score,
            "matched_skills": matched_skills,
            "requirement_score": scores.get("requirement_score", 0),
            "task_score": scores.get("task_score", 0),
            "preference_score": scores.get("preference_score", 0),
            "detail_scores": scores.get("detail_scores", [])
        }
    }

@router.post("/job-postings/{job_posting_id}/analyze-all")
async def analyze_all_resumes(job_posting_id: int):
    """해당 공고의 모든 지원자 이력서 일괄 분석"""
    applicants = supabase.table("applicants")\
        .select("id")\
        .eq("job_posting_id", job_posting_id)\
        .execute()

    if not applicants.data:
        raise HTTPException(status_code=404, detail="지원자가 없습니다.")

    results = []
    for applicant in applicants.data:
        try:
            await analyze_resume(applicant["id"])
            results.append({
                "applicant_id": applicant["id"],
                "status": "success"
            })
        except Exception as e:
            results.append({
                "applicant_id": applicant["id"],
                "status": "failed",
                "error": str(e)
            })

    return {
        "success": True,
        "data": {
            "total": len(applicants.data),
            "results": results
        }
    }