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

@router.post("job-posting/{job_posting_id}/criteria")
def create_criteria(job_posting_id: int):
    response = (
        supabase.table("formatted_postings")
        .select("*")
        .eq("job_posting_id", job_posting_id)
        .execute()
    )

    title_response  = (
        supabase.table("job_postings")
        .select("title")
        .eq("id", job_posting_id)
        .execute()
    )

    model= "gpt-4o-mini",
    temperature = 0,

    prompt = ChatPromptTemplate.from_template(
"""
이 채용 공고문으로 사람을 채용하고 싶어.
이 채용 공고문을 바탕으로 지원자 적합성 평가 기준을 만들거야.
공고문에서 자격조건, 주요업무, 우대사항을 주로 필수 역량을 뽑아서 각 항목별 가중치를 설정해서 평가 기준을 만들거야.

각 카테고리별로 반드시 1개만 만들지 말고,
공고문 내용에 따라 여러 개의 세부 평가 기준을 만들어줘.

예를 들어 자격조건에 Python, FastAPI, DB 경험이 있다면
각각을 별도의 평가 기준으로 분리해줘.

단, 전체 weight의 합은 반드시 100이 되어야 해.

평가기준을 테이블로 만들어줘.

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
  }},
  {{
    "category": "주요업무",
    "description": "세부 평가 기준",
    "weight": 15
  }}
]

규칙:
- category는 반드시 "자격조건", "주요업무", "우대사항" 중 하나
- 한 category 안에 여러 개의 평가 기준이 들어갈 수 있음
- description은 구체적인 평가 기준으로 작성
- weight 전체 합은 반드시 100
- JSON 외의 설명 문장은 출력하지 마
"""
    )
    llm = ChatOpenAI(model=model, temperature=temperature)
    chain = prompt | llm | JsonOutputParser()
    result = chain.invoke({"title": title_response.data[0]["title"], "job_posting": response.data})

    # supabase.table("job_postings").upsert({
    #         "title": result['title'],
    #         "input_type": result["input_type"],
    #         "source_url": result["source_url"],
    #         "raw_content": result["raw_content"],
    #         "conts_summary": result["conts_summary"]
    #         }).execute()