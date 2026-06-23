from fastapi import APIRouter, HTTPException
from database import supabase

router = APIRouter(
    prefix="/api/v1",
    tags=["resume-analysis"]
)

@router.post("/applicants/{applicants_id}/analyze")
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

    return {
        "success": True,
        "data": {
            "applicant_id": applicant_id,
            "resume_text": resume_text,
            "skills": skills.data,
            "type_criteria": type_criteria.data,
            "detail_criteria": detail_criteria.data
        }
    }