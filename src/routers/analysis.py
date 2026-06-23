from fastapi import APIRouter, HTTPException
from database import supabase

router = APIRouter(
    prefix="/api/v1",
    tags=["analysis"]
)

@router.get("/job-postings/{job_posting_id}/applicants")
async def get_applicants(job_posting_id: int):
    """지원자 목록 + 점수 조회"""
    response = supabase.table("applicants")\
        .select("*, applicant_scores(*)")\
            .eq("job_posting_id", job_posting_id)\
                .execute()

    if not response.data:
        raise HTTPException(status_code=404, detail="지원자가 없습니다.")
    
    # 프론트 ApplicantSummary 구조에 맞게 변환
    result = []
    for applicant in response.data:
        scores = applicant.get("applicant_scores", [])
        score_data = scores[0] if scores else {}
        result.append({
            "id": applicant["id"],
            "masked_code": applicant["masked_code"],
            "career": f"경력 {applicant.get('career')}" if applicant.get('career') else "신입",
            "total_score": score_data.get("total_score", 0),
            "requirement_score": score_data.get("requirement_score", 0),
            "skill_score": score_data.get("skill_score", 0),
            "task_score": score_data.get("task_score", 0),
            "preference_score": score_data.get("preference_score", 0),
        })

    # total_score 기준 내림차순 정렬 (화면에서 랭킹 1위, 2위, 3위 순서)
    sorted_data = sorted(
        result, # 정렬할 대상 (지원자 목록)
        key=lambda x: x["total_score"],
        reverse=True # 내림차순 (높은 점수가 위로)
    )
    
    return {
        "success":True,
        "data": sorted_data
    }

@router.get("/applicants/{applicant_id}")
async def get_applicant_detail(applicant_id: int):
    """지원자 상세 정보 + 점수 조회"""
    # 지원자 기본 정보 조회
    applicant = supabase.table("applicants")\
        .select("*")\
        .eq("id", applicant_id)\
        .execute()

    if not applicant.data:
        raise HTTPException(status_code=404, detail="지원자가 없습니다.")

    # 종합 점수 조회
    scores = supabase.table("applicant_scores")\
        .select("*")\
        .eq("applicant_id", applicant_id)\
        .execute()

    # 세부 점수 조회
    detail_scores = supabase.table("detail_scores")\
        .select("*")\
        .eq("applicant_id", applicant_id)\
        .execute()

    # type_criteria 조회
    type_criteria_list = supabase.table("type_criteria")\
        .select("*")\
        .execute()

    # detail_criteria 조회
    detail_criteria_list = supabase.table("detail_criteria")\
        .select("*")\
        .execute()

    # 딕셔너리로 변환 (빠른 조회용)
    type_criteria_map = {tc["id"]: tc for tc in type_criteria_list.data}
    detail_criteria_map = {dc["id"]: dc for dc in detail_criteria_list.data}

    # 프론트 구조에 맞게 변환
    converted_detail_scores = []
    for ds in detail_scores.data:
        type_criteria = type_criteria_map.get(ds.get("type_criteria_id"), {})
        detail_criteria = detail_criteria_map.get(ds.get("detail_criteria_id"), {})
        score = ds.get("score", 0)
        weight = detail_criteria.get("weight", 0)

        # detail이 없거나 weight가 0이면 제외
        if not detail_criteria.get("detail") or weight == 0:
            continue

        converted_detail_scores.append({
            "criterion_type": type_criteria.get("criterion_type", ""),
            "type_weight": type_criteria.get("type_weight", 0),
            "score": score,
            "weight": weight,
            "weighted_score": round(score * weight / 100, 1)
        })
    
    # 기술 스택 조회
    skills = supabase.table("skills_stack")\
        .select("skill_name")\
        .eq("job_posting_id", applicant.data[0]["job_posting_id"])\
        .execute()
    
    # 프론트가 기대하는 구조로 맞춤
    score_data = scores.data[0] if scores.data else {}
    matched_skills = [s["skill_name"] for s in skills.data] if skills.data else []

    # 이력서 텍스트 조회
    resume = supabase.table("resume_files")\
        .select("extracted_text")\
        .eq("applicant_id", applicant_id)\
        .execute()

    # LLM 이력서 요약
    from langchain_openai import ChatOpenAI
    from langchain_core.prompts import PromptTemplate
    import json
    import os

    llm = ChatOpenAI(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        temperature=0,
        openai_api_key=os.getenv("OPENAI_API_KEY")
    )

    resume_summary = {
        "career_summary": "",
        "project_summary": "",
        "skill_summary": ""
    }

    if resume.data and resume.data[0].get("extracted_text"):
        summary_prompt = PromptTemplate(
            input_variables=["resume"],
            template="""
아래 이력서를 읽고 3가지로 요약해주세요.

[이력서]
{resume}

아래 JSON 형식으로만 응답하세요. 다른 설명은 절대 하지 마세요.
{{
    "career_summary": "총 경력 : X년\\n\\n• 직무1\\n\\n• 직무2",
    "project_summary": "• 프로젝트1\\n\\n• 프로젝트2",
    "skill_summary": "• 기술1\\n\\n• 기술2\\n\\n• 기술3"
}}
"""
)
        chain = summary_prompt | llm
        result = chain.invoke({"resume": resume.data[0]["extracted_text"]})
        try:
            resume_summary = json.loads(result.content)
        except:
            pass

    return {
        "success": True,
        "data": {
            **applicant.data[0],
            "score": {
                "total_score": score_data.get("total_score", 0),
                "requirement_score": score_data.get("requirement_score", 0),
                "skill_score": score_data.get("skill_score", 0),
                "task_score": score_data.get("task_score", 0),
                "preference_score": score_data.get("preference_score", 0),
            },
            "detail_scores": converted_detail_scores,
            "matched_skills": matched_skills,
            "resume_summary": resume_summary
        }
    }

@router.get("/comparison-sets/{comparison_id}")
async def get_comparison(comparison_id: int):
    """지원자 비교 데이터 조회"""
    response =  supabase.table("comparison_sets")\
        .select("*, comparison_items(*)")\
            .eq("id", comparison_id)\
                .execute()
    
    if not response.data:
        raise HTTPException(status_code=404, detail="비교 데이터가 없습니다.")

    return {
        "success":True,
        "data": response.data
    }