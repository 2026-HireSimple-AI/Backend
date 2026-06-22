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
            "career": f"경력 {applicant.get('career', '-')}",
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
    
    # 기술 스택 조회
    skills = supabase.table("skills_stack")\
        .select("skill_name")\
        .eq("job_posting_id", applicant.data[0]["job_posting_id"])\
        .execute()
    
    # 프론트가 기대하는 구조로 맞춤
    score_data = scores.data[0] if scores.data else {}
    matched_skills = [s["skill_name"] for s in skills.data] if skills.data else []

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
            "detail_scores": detail_scores.data,
            "matched_skills": matched_skills,
        }
    }

@router.get("/applicants/{applicant_id}/scores")
async def get_applicant_scores(applicant_id: int):
    """지원자 상세 점수 조회"""
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
    
    if not scores.data:
        raise HTTPException(status_code=404, detail="점수 데이터가 없습니다.")

    return {
        "success":True,
        "data": {
            "scores": scores.data,
            "detail_scores": detail_scores.data
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
