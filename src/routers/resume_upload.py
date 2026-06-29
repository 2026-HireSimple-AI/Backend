from fastapi import APIRouter, UploadFile, File, HTTPException
from pathlib import Path
import uuid
from database import supabase

from routers.resume_service import process_resume_batch, extract_resume_text

router = APIRouter(
    prefix="/api/v1",
    tags=["resume"]
)

UPLOAD_DIR = Path("uploads/resumes")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

def get_pii_value(pii: list[dict], pii_type: str):
    for item in pii:
        if item["type"] == pii_type:
            return item["value"]
    return None


def save_applicant(supabase, job_posting_id: int, result: dict) -> dict:
    pii = result.get("pii", [])

    real_name = get_pii_value(pii, "name")
    phone = get_pii_value(pii, "phone")
    email = get_pii_value(pii, "email")

    applicant_data = {
        "job_posting_id": job_posting_id,
        "masked_code": "TEMP",
        "real_name": real_name,
        "phone": phone,
        "email": email,
        "address": None,
        "career": None,
    }

    response = (
        supabase
        .table("applicants")
        .insert(applicant_data)
        .execute()
    )

    applicant = response.data[0]

    masked_code = f"APPLICANT_{applicant['id']:03d}"

    supabase.table("applicants").update(
        {"masked_code": masked_code}
    ).eq("id", applicant["id"]).execute()

    applicant["masked_code"] = masked_code
    return applicant

def save_resume_file(
    supabase,
    applicant_id: int,
    original_filename: str,
    file_path: str,
    file_type: str,
    file_size_bytes: int,
    extracted_text: str,
    masked_text: str,
    processing_status: str,
) -> dict:
    resume_file_data = {
        "applicant_id": applicant_id,
        "original_filename": original_filename,
        "file_path": file_path,
        "file_type": file_type,
        "file_size_bytes": file_size_bytes,
        "extracted_text": extracted_text,
        "masked_text": masked_text,
        "processing_status": processing_status,
    }

    response = (
        supabase
        .table("resume_files")
        .insert(resume_file_data)
        .execute()
    )

    return response.data[0]

def save_resume(
    supabase,
    job_posting_id: int,
    original_filename: str,
    resume_text: str,
    processing_status: str,
) -> dict:
    resume_data = {
        "job_posting_id": job_posting_id,
        "original_filename": original_filename,
        "resume_text": resume_text,
        "processing_status": processing_status,
    }

    response = (
        supabase
        .table("resumes")
        .insert(resume_data)
        .execute()
    )

    return response.data[0]

@router.post("/job-postings/{job_posting_id}/resumes")
async def upload_resumes(
    job_posting_id: int,
    files: list[UploadFile] = File(...),
):
    resumes = []
    file_meta_list = []

    for file in files:
        content = await file.read()

        file_type = Path(file.filename).suffix.lower().replace(".", "")
        saved_filename = f"{uuid.uuid4()}_{file.filename}"
        file_path = UPLOAD_DIR / saved_filename

        with open(file_path, "wb") as f:
            f.write(content)

        extracted_text = extract_resume_text(file_path)

        resumes.append({
            "filename": file.filename,
            "text": extracted_text,
        })

        file_meta_list.append({
            "original_filename": file.filename,
            "file_path": str(file_path),
            "file_type": file_type,
            "file_size_bytes": len(content),
            "extracted_text": extracted_text,
        })

    masking_results = process_resume_batch(resumes)

    files_response = []

    for result, meta in zip(masking_results, file_meta_list):
        status = result["masking_status"].lower()

        applicant = save_applicant(
            supabase=supabase,
            job_posting_id=job_posting_id,
            result=result,
        )

        resume_file = save_resume_file(
            supabase=supabase,
            applicant_id=applicant["id"],
            original_filename=meta["original_filename"],
            file_path=meta["file_path"],
            file_type=meta["file_type"],
            file_size_bytes=meta["file_size_bytes"],
            extracted_text=meta["extracted_text"],
            masked_text=result["masked_text"],
            processing_status=status,
        )

        resume = save_resume(
            supabase=supabase,
            job_posting_id=job_posting_id,
            original_filename=meta["original_filename"],
            resume_text=result["masked_text"],
            processing_status=status,
        )

        files_response.append({
            "resume_id": resume["id"],
            "resume_file_id": resume_file["id"],
            "applicant_id": applicant["id"],
            "original_filename": resume_file["original_filename"],
            "processing_status": resume_file["processing_status"],
        })

    # 파일 형식 떄문에 추가 - 유선님에게 공유
    return {
    "success": True,
    "data": {
        "uploaded_count": len(files_response),
        "files": files_response  # ← .files로 감싸기
    }
}