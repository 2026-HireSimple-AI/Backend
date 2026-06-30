from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser

from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel
from database import supabase, supabase_auth
from typing import Optional

router = APIRouter(
    prefix="/api/v1",
    tags=["criteria"]
)

@router.post("/job-posting/{job_posting_id}/criteria")
def create_criteria(job_posting_id: int):
    response = (
        supabase.table("formatted_postings")
        .select("*")
        .eq("job_posting_id", job_posting_id)
        .execute()
    )

    title_response = (
        supabase.table("job_postings")
        .select("title")
        .eq("id", job_posting_id)
        .execute()
    )

    if not title_response.data:
        raise HTTPException(status_code=404, detail="채용공고를 찾을 수 없습니다.")

    model = "gpt-4o-mini"
    temperature = 0

    prompt = ChatPromptTemplate.from_template(
        """
이 채용 공고문으로 사람을 채용하고 싶어.
이 채용 공고문을 바탕으로 지원자 적합성 평가 기준을 만들거야.

채용 공고 제목:
{title}

채용 공고문:
{job_posting}

OUTPUT(JSON):
반드시 JSON 배열로만 출력해줘.

[
  {{
    "category": "자격조건",
    "description": "세부 평가 기준",
    "weight": 10
  }}
]

규칙:
- category는 반드시 "자격조건", "주요업무", "우대사항" 중 하나
- 한 category 안에 여러 개의 평가 기준이 들어갈 수 있음
- description은 구체적인 평가 기준으로 작성
- weight 전체 합은 반드시 100
- JSON 외의 설명 문장은 출력하지 마
- 자격조건에는 학력과 경력은 평가 기준으로 만들지마.
"""
    )

    llm = ChatOpenAI(model=model, temperature=temperature)
    chain = prompt | llm | JsonOutputParser()

    results = chain.invoke({
        "title": title_response.data[0]["title"],
        "job_posting": response.data
    })

    category_map = {
        "자격조건": "자격 조건",
        "자격 조건": "자격 조건",
        "주요업무": "주요 업무",
        "주요 업무": "주요 업무",
        "우대사항": "우대 사항",
        "우대 사항": "우대 사항",
    }

    sort_order_map = {
        "자격 조건": 1,
        "주요 업무": 2,
        "우대 사항": 3,
    }

    inserted_criteria = []

    for item in results:
        raw_category = item["category"]
        category = category_map.get(raw_category)

        if category is None:
            continue

        insert_response = (
            supabase.table("criteria")
            .insert({
                "job_posting_id": job_posting_id,
                "criterion_type": category,
                "details": item["description"],
                "type_weight": item["weight"],
                "sort_order": sort_order_map.get(category)
            })
            .execute()
        )

        inserted_criteria.extend(insert_response.data)

    grouped = {}

    for row in inserted_criteria:
        criterion_type = row["criterion_type"]

        if criterion_type not in grouped:
            grouped[criterion_type] = {
                "id": row["id"],
                "criterion_type": criterion_type,
                "description": f"{criterion_type} 관련 평가 기준입니다.",
                "type_weight": 0,
                "detail_criteria": []
            }

        grouped[criterion_type]["type_weight"] += row["type_weight"]

        grouped[criterion_type]["detail_criteria"].append({
            "id": row["id"],
            "detail": row["details"],
            "weight": row["type_weight"]
        })

    type_criteria = sorted(
        grouped.values(),
        key=lambda x: sort_order_map.get(x["criterion_type"], 999)
    )

    return {
        "success": True,
        "data": {
            "type_criteria": type_criteria
        }
    }

@router.get("/job-posting/{job_posting_id}/criteria")
def get_criteria(job_posting_id: int):
    response = (
        supabase.table("criteria")
        .select("*")
        .eq("job_posting_id", job_posting_id)
        .order("sort_order")
        .execute()
    )

    rows = response.data

    grouped = {}

    for row in rows:
        criterion_type = row["criterion_type"]

        if criterion_type not in grouped:
            grouped[criterion_type] = {
                "id": row["id"],
                "criterion_type": criterion_type,
                "description": f"{criterion_type} 관련 평가 기준입니다.",
                "type_weight": 0,
                "detail_criteria": []
            }

        grouped[criterion_type]["type_weight"] += row["type_weight"]

        grouped[criterion_type]["detail_criteria"].append({
            "id": row["id"],
            "detail": row["details"],
            "weight": row["type_weight"]
        })

    return {
        "success": True,
        "data": {
            "type_criteria": list(grouped.values())
        }
    }

class DetailCriterionPayload(BaseModel):
    id: Optional[int] = None
    detail: str
    weight: int

class TypeCriterionPayload(BaseModel):
    id: Optional[int] = None
    criterion_type: str
    type_weight: int
    detail_criteria: list[DetailCriterionPayload]

class UpdateCriteriaPayload(BaseModel):
    type_criteria: list[TypeCriterionPayload]


@router.put("/job-posting/{job_posting_id}/criteria")
def update_criteria(job_posting_id: int, payload: UpdateCriteriaPayload):
    category_map = {
        "자격조건": "자격 조건",
        "자격 조건": "자격 조건",
        "주요업무": "주요 업무",
        "주요 업무": "주요 업무",
        "우대사항": "우대 사항",
        "우대 사항": "우대 사항",
    }

    sort_order_map = {
        "자격 조건": 1,
        "주요 업무": 2,
        "우대 사항": 3,
    }

    # 1. 가중치 총합 검증
    total_weight = sum(t.type_weight for t in payload.type_criteria)
    if total_weight != 100:
        raise HTTPException(
            status_code=400,
            detail=f"가중치의 총합은 100이어야 합니다. (현재 {total_weight})"
        )

    # 2. 빈 텍스트 검증
    for t in payload.type_criteria:
        if not t.criterion_type.strip():
            raise HTTPException(status_code=400, detail="평가 항목명이 비어있습니다.")
        if not t.detail_criteria:
            raise HTTPException(status_code=400, detail=f"'{t.criterion_type}'에 세부 평가 기준이 없습니다.")
        for d in t.detail_criteria:
            if not d.detail.strip():
                raise HTTPException(
                    status_code=400,
                    detail=f"'{t.criterion_type}' 안에 비어있는 세부 평가 지표가 있습니다."
                )

    # 3. 해당 공고의 기존 criteria 전체 삭제 (덮어쓰기)
    supabase.table("criteria").delete().eq("job_posting_id", job_posting_id).execute()

    # 4. draft 내용을 row 단위로 풀어서 재삽입
    insert_rows = []
    for t in payload.type_criteria:
        category = category_map.get(t.criterion_type)
        if category is None:
            raise HTTPException(
                status_code=400,
                detail=f"알 수 없는 평가 항목 분류입니다: '{t.criterion_type}'"
            )
        sort_order = sort_order_map[category]

        for d in t.detail_criteria:
            insert_rows.append({
                "job_posting_id": job_posting_id,
                "criterion_type": category,
                "details": d.detail,
                "type_weight": d.weight,
                "sort_order": sort_order
            })

    inserted_criteria = []
    if insert_rows:
        insert_response = supabase.table("criteria").insert(insert_rows).execute()
        inserted_criteria = insert_response.data

    # 5. 응답을 위해 grouped 형태로 재구성 (GET과 동일한 형식)
    grouped = {}
    for row in inserted_criteria:
        criterion_type = row["criterion_type"]

        if criterion_type not in grouped:
            grouped[criterion_type] = {
                "id": row["id"],
                "criterion_type": criterion_type,
                "description": f"{criterion_type} 관련 평가 기준입니다.",
                "type_weight": 0,
                "detail_criteria": []
            }

        grouped[criterion_type]["type_weight"] += row["type_weight"]
        grouped[criterion_type]["detail_criteria"].append({
            "id": row["id"],
            "detail": row["details"],
            "weight": row["type_weight"]
        })

    type_criteria = sorted(
        grouped.values(),
        key=lambda x: sort_order_map.get(x["criterion_type"], 999)
    )

    return {
        "success": True,
        "data": {
            "type_criteria": type_criteria
        }
    }