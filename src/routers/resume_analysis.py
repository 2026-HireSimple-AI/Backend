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
    type_criteria = supabase.table("type_criteria")\
        .select("*")\
        .eq("job_posting_id", job_posting_id)\
        .execute()

    detail_criteria = supabase.table("detail_criteria")\
        .select("*")\
        .execute()
    
    # 5. 기술 스택 비교 (텍스트 매칭)
    skill_names = [skill_name["skill_name"] for skill_name in skills.data]
    matched_skills= [skill_name for skill_name in skill_names if skill_name.lower() in resume_text.lower()]
    skill_score = round((len(matched_skills) / len(skill_names)) * 100, 2) if skill_names else 0

    # 6. 평가 기준 텍스트로 변환
    criteria_text = ""
    for tc in type_criteria.data:
        criteria_text += f"\n[{tc['criterion_type']}] 가중치: {tc['type_weight']}%\n"
        for dc in detail_criteria.data:
            if dc["type_criteria_id"] == tc["id"]:
                criteria_text += f"  - ID:{dc['id']} {dc['detail']} (가중치: {dc['weight']}%)\n"

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

    return {
        "success": True,
        "data": {
            "applicant_id": applicant_id,
            "skill_score": skill_score,
            "matched_skills": matched_skills,
            "requirement_score": scores.get("requirement_score", 0),
            "task_score": scores.get("task_score", 0),
            "preference_score": scores.get("preference_score", 0),
            "detail_scores": scores.get("detail_scores", [])
        }
    }