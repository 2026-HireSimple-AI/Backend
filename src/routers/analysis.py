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
    
    # total_score 기준 내림차순 정렬 (화면에서 랭킹 1위, 2위, 3위 순서)
    sorted_data = sorted(
        response.data, # 정렬할 대상 (지원자 목록)
        key=lambda x: x["applicant_scores"][0]["total_score"] if x ["applicant_scores"] else 0,
        reverse=True # 내림차순 (높은 점수가 위로)
    )
    
    return sorted_data

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
        "scores": scores.data,
        "detail_scores": detail_scores.data
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

    return response.data
