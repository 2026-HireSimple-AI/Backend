from fastapi import APIRouter, UploadFile, File, HTTPException
import uuid
from database import supabase

import io
from pypdf import PdfWriter, PdfReader
from fastapi.responses import StreamingResponse
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from pathlib import Path

from routers.resume_service import process_resume_batch, extract_resume_text

router = APIRouter(
    prefix="/api/v1",
    tags=["resume"]
)

RESUME_STORAGE_BUCKET = "resumes"


def get_pii_value(pii: list[dict], pii_type: str):
    for item in pii:
        if item["type"] == pii_type:
            return item["value"]
    return None


def upload_resume_to_storage(content: bytes, original_filename: str) -> str:
    """원본 파일을 Supabase Storage에 업로드하고 storage 내 경로를 반환한다.

    Storage 키에는 한글/공백 등이 들어가면 InvalidKey 에러가 나므로,
    경로는 UUID + 확장자로만 구성하고 원본 파일명은 DB의
    original_filename 컬럼에 따로 보관한다.
    """
    suffix = (
        original_filename.rsplit(".", 1)[-1].lower()
        if "." in original_filename
        else ""
    )
    storage_path = f"{uuid.uuid4()}.{suffix}" if suffix else str(uuid.uuid4())

    supabase.storage.from_(RESUME_STORAGE_BUCKET).upload(
        path=storage_path,
        file=content,
        file_options={"content-type": "application/octet-stream"},
    )

    return storage_path


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


@router.get("/job-postings/{job_posting_id}/resumes")
async def list_resumes(job_posting_id: int):
    """특정 공고에 달린 이력서(지원자) 목록을 조회한다."""
    response = (
        supabase
        .table("applicants")
        .select(
            "id, masked_code, real_name, phone, email,"
            "resume_files(id, original_filename, file_type, file_size_bytes, processing_status)"
        )
        .eq("job_posting_id", job_posting_id)
        .execute()
    )

    files_response = []

    for applicant in response.data:
        resume_files = applicant.get("resume_files") or []

        for resume_file in resume_files:
            files_response.append({
                "resume_file_id": resume_file["id"],
                "applicant_id": applicant["id"],
                "original_filename": resume_file["original_filename"],
                "file_type": resume_file["file_type"],
                "file_size_bytes": resume_file["file_size_bytes"],
                "processing_status": resume_file["processing_status"],
            })

    return {
        "success": True,
        "data": {
            "uploaded_count": len(files_response),
            "files": files_response,
        }
    }


@router.delete("/resumes/{resume_file_id}")
async def delete_resume(resume_file_id: int):
    """이력서 파일 1건과 연관된 지원자, 원본 Storage 파일을 함께 삭제한다."""
    resume_file_res = (
        supabase
        .table("resume_files")
        .select("id, applicant_id, file_path")
        .eq("id", resume_file_id)
        .execute()
    )

    if not resume_file_res.data:
        raise HTTPException(status_code=404, detail="이력서 파일을 찾을 수 없습니다.")

    resume_file = resume_file_res.data[0]
    applicant_id = resume_file["applicant_id"]
    file_path = resume_file.get("file_path")

    # 1. Storage 원본 파일 삭제 (실패해도 DB 정리는 계속 진행)
    if file_path:
        try:
            supabase.storage.from_(RESUME_STORAGE_BUCKET).remove([file_path])
        except Exception:
            pass

    # 2. resume_files 행 삭제
    supabase.table("resume_files").delete().eq("id", resume_file_id).execute()

    # 3. 연결된 applicant 삭제 (해당 applicant에 다른 resume_file이 없을 때만)
    remaining = (
        supabase
        .table("resume_files")
        .select("id")
        .eq("applicant_id", applicant_id)
        .execute()
    )

    if not remaining.data:
        supabase.table("applicants").delete().eq("id", applicant_id).execute()

    return {"success": True, "data": {"deleted_resume_file_id": resume_file_id}}


@router.post("/job-postings/{job_posting_id}/resumes")
async def upload_resumes(
    job_posting_id: int,
    files: list[UploadFile] = File(...),
):
    resumes = []
    file_meta_list = []

    for file in files:
        content = await file.read()

        file_type = (
            file.filename.lower().rsplit(".", 1)[-1]
            if "." in file.filename
            else ""
        )

        try:
            extracted_text = extract_resume_text(content, file.filename)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        # 디스크에 저장하지 않고 원본만 Storage에 업로드
        storage_path = upload_resume_to_storage(content, file.filename)

        resumes.append({
            "filename": file.filename,
            "text": extracted_text,
        })

        file_meta_list.append({
            "original_filename": file.filename,
            "file_path": storage_path,
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
            "file_type": resume_file["file_type"],
            "file_size_bytes": resume_file["file_size_bytes"],
            "processing_status": resume_file["processing_status"],
        })

    return {
        "success": True,
        "data": {
            "uploaded_count": len(files_response),
            "files": files_response,
        }
    }

# 한글 폰트 등록 (서버에 폰트 파일 경로 필요, 예: NanumGothic)
BASE_DIR = Path(__file__).parent.parent
FONT_PATH = str(BASE_DIR / "fonts" / "NanumGothic.ttf")


def text_to_pdf_bytes(text: str, title: str) -> bytes:
    if "NanumGothic" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("NanumGothic", FONT_PATH))
    
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    c.setFont("NanumGothic", 14)
    c.drawString(40, height - 50, title)

    c.setFont("NanumGothic", 10)
    y = height - 90
    line_height = 14
    max_width = width - 80

    for raw_line in text.split("\n"):
        # 긴 줄 wrap 처리
        line = raw_line
        while line:
            # 대략적인 글자수 기준 wrap (한글 폰트 기준 보정 필요)
            cut = 0
            for i in range(1, len(line) + 1):
                if pdfmetrics.stringWidth(line[:i], "NanumGothic", 10) > max_width:
                    break
                cut = i
            chunk = line[:cut] if cut else line
            c.drawString(40, y, chunk)
            line = line[len(chunk):]
            y -= line_height
            if y < 50:
                c.showPage()
                c.setFont("NanumGothic", 10)
                y = height - 50

        y -= line_height * 0.3  # 빈 줄 간격

    c.save()
    buffer.seek(0)
    return buffer.read()


@router.get("/job-posting/{job_posting_id}/resumes/masked-download")
def download_masked_resumes(job_posting_id: int):
    applicants_response = (
        supabase.table("applicants")
        .select("id")
        .eq("job_posting_id", job_posting_id)
        .execute()
    )
    applicant_ids = [a["id"] for a in applicants_response.data]
    print(f"[DEBUG] job_posting_id: {job_posting_id}")
    print(f"[DEBUG] applicant_ids: {applicant_ids}")  # ← 몇 개 나오는지


    if not applicant_ids:
        raise HTTPException(status_code=404, detail="해당 공고에 등록된 지원자가 없습니다.")

    response = (
        supabase.table("resume_files")
        .select("id, original_filename, masked_text, processing_status")
        .in_("applicant_id", applicant_ids)
        .in_("processing_status", ["masked", "uploaded"])
        .execute()
    )
    print(f"[DEBUG] resume_files count: {len(response.data)}")  # ← 몇 개 잡히는지
    rows = response.data
    print(f"[DEBUG] rows count: {len(rows)}")
    for row in rows:
        print(f"[DEBUG] {row['original_filename']} | status: {row['processing_status']} | masked_text 길이: {len(row.get('masked_text') or '')}")
    
    if not rows:
        raise HTTPException(status_code=404, detail="마스킹 완료된 이력서가 없습니다.")

    # 개별 PDF를 하나로 합치기
    writer = PdfWriter()

    for row in rows:
        if not row.get("masked_text"):
            continue
        try:
            pdf_bytes = text_to_pdf_bytes(row["masked_text"], row["original_filename"].rsplit(".", 1)[0])
            reader = PdfReader(io.BytesIO(pdf_bytes))
            for page in reader.pages:
                writer.add_page(page)
        except Exception as e:
            print(f"[PDF 변환 실패] {row['original_filename']}: {e}")
            continue

    if len(writer.pages) == 0:
        raise HTTPException(status_code=404, detail="변환 가능한 이력서 내용이 없습니다.")

    output = io.BytesIO()
    writer.write(output)
    output.seek(0)

    filename = f"masked_resumes_{job_posting_id}.pdf"
    return StreamingResponse(
        output,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store, no-cache, must-revalidate",  # ← 추가
            "Pragma": "no-cache",       
            }
    )