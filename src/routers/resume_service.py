from pathlib import Path
import pdfplumber
from docx import Document


def extract_pdf_text(file_path: Path) -> str:
    texts = []

    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                texts.append(page_text)

    return "\n".join(texts)


def extract_docx_text(file_path: Path) -> str:
    doc = Document(file_path)

    texts = []

    for paragraph in doc.paragraphs:
        if paragraph.text.strip():
            texts.append(paragraph.text)

    return "\n".join(texts)


def extract_resume_text(file_path: Path) -> str:
    suffix = file_path.suffix.lower()

    if suffix == ".pdf":
        return extract_pdf_text(file_path)

    if suffix == ".docx":
        return extract_docx_text(file_path)

    raise ValueError(f"지원하지 않는 파일 형식입니다: {suffix}")

import re
import random
from dataclasses import dataclass


@dataclass
class PIIMatch:
    type: str
    value: str
    start: int
    end: int

def get_pii_value(matches: list[PIIMatch], pii_type: str):
    values = [
        m.value.strip()
        for m in matches
        if m.type == pii_type and m.value.strip()
    ]

    if not values:
        return None

    # 너무 긴 값 방지
    if pii_type == "name":
        values = [
            v for v in values
            if re.fullmatch(r"[가-힣]{2,4}", v)
        ]

    return values[0] if values else None

# --------------------------------------------------
# 1차: 정규식 + 이력서 라벨 룰
# --------------------------------------------------

REGEX_RULES = {
    "resident_id": r"\d{6}[-\s]?[1-4]\d{6}",
    "phone": r"01[016789][-\s]?\d{3,4}[-\s]?\d{4}",
    "email": r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
}

FORMAT_RULES = {
    "name": r"(?:이름|성명|지원자명)\s*[:：]?\s*([가-힣]{2,4})",
    "birth": r"(?:생년월일|생일|출생)\s*[:：]?\s*([\d.\-]{6,10})",
    "address": r"(?:주소|거주지)\s*[:：]?\s*([^\n]+)",
}

LABEL_KEYWORDS = {
    "name": ["이름", "성명", "지원자명"],
    "birth": ["생년월일", "생일", "출생"],
    "phone": ["휴대폰", "핸드폰", "연락처", "전화"],
    "email": ["이메일", "메일"],
    "address": ["주소", "거주지"],
}

SAVABLE_TYPES = {"name", "email", "phone"}

_ner = None

def split_text_with_offsets(text: str, chunk_size: int = 400, overlap: int = 50):
    """
    긴 이력서 텍스트를 NER 모델에 넣기 전에 조각내는 함수.
    문자 기준으로 자르고, 각 조각의 원문 시작 위치도 함께 반환한다.
    """
    chunks = []
    start = 0

    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunk = text[start:end]

        chunks.append(
            {
                "text": chunk,
                "offset": start,
            }
        )

        if end == len(text):
            break

        start = end - overlap

    return chunks

def _add_if_no_overlap(matches: list[PIIMatch], new_match: PIIMatch) -> None:
    overlap = any(
        not (new_match.end <= m.start or new_match.start >= m.end)
        for m in matches
    )

    if not overlap:
        matches.append(new_match)


def extract_by_label_lines(text: str) -> list[PIIMatch]:
    lines = text.split("\n")

    offsets = []
    pos = 0

    for line in lines:
        offsets.append(pos)
        pos += len(line) + 1

    matches = []

    for i, raw_line in enumerate(lines):
        line = raw_line.strip()

        if not line or len(line) > 15:
            continue

        for pii_type, keywords in LABEL_KEYWORDS.items():
            if not any(kw.lower() in line.lower() for kw in keywords):
                continue

            if ":" in line or "：" in line:
                break

            j = i + 1

            while j < len(lines) and not lines[j].strip():
                j += 1

            if j < len(lines):
                value = lines[j].strip()

                if value:
                    start = offsets[j] + lines[j].index(value)
                    matches.append(
                        PIIMatch(
                            type=pii_type,
                            value=value,
                            start=start,
                            end=start + len(value),
                        )
                    )

            break

    return matches


def extract_known_name_mentions(
    text: str,
    matches: list[PIIMatch],
) -> list[PIIMatch]:
    names = {m.value for m in matches if m.type == "name"}
    found = []

    for name in names:
        for m in re.finditer(re.escape(name), text):
            found.append(
                PIIMatch(
                    type="name",
                    value=name,
                    start=m.start(),
                    end=m.end(),
                )
            )

    return found


def extract_stage1(text: str) -> list[PIIMatch]:
    matches: list[PIIMatch] = []

    for pii_type, pattern in REGEX_RULES.items():
        for m in re.finditer(pattern, text):
            _add_if_no_overlap(
                matches,
                PIIMatch(
                    type=pii_type,
                    value=m.group(),
                    start=m.start(),
                    end=m.end(),
                ),
            )

    for pii_type, pattern in FORMAT_RULES.items():
        for m in re.finditer(pattern, text):
            _add_if_no_overlap(
                matches,
                PIIMatch(
                    type=pii_type,
                    value=m.group(1),
                    start=m.start(1),
                    end=m.end(1),
                ),
            )

    for m in extract_by_label_lines(text):
        _add_if_no_overlap(matches, m)

    for m in extract_known_name_mentions(text, matches):
        _add_if_no_overlap(matches, m)

    return matches


def mask(text: str, matches: list[PIIMatch]) -> str:
    masked = text

    for m in sorted(matches, key=lambda x: x.start, reverse=True):
        masked = masked[:m.start] + f"[{m.type.upper()}]" + masked[m.end:]

    return masked


# --------------------------------------------------
# 2차: NER
# - 이메일/전화번호/주민번호는 정규식이 담당
# - NER은 이름 보완용으로 사용
# --------------------------------------------------

def _get_ner():
    global _ner

    if _ner is None:
        from transformers import pipeline

        _ner = pipeline(
            "token-classification",
            model="monologg/koelectra-base-v3-naver-ner",
            aggregation_strategy="simple",
        )

    return _ner

def extract_stage2_batch(
    texts: list[str],
    stage1_matches_list: list[list[PIIMatch]],
) -> list[list[PIIMatch]]:
    ner = _get_ner()
    combined_list = []

    for text, stage1_matches in zip(texts, stage1_matches_list):
        combined = list(stage1_matches)

        chunks = split_text_with_offsets(
            text,
            chunk_size=400,
            overlap=50,
        )

        chunk_texts = [chunk["text"] for chunk in chunks]
        ner_results = ner(chunk_texts)

        for chunk, entities in zip(chunks, ner_results):
            offset = chunk["offset"]

            for ent in entities:
                entity_group = ent.get("entity_group")
                word = ent.get("word", "").strip()

                if entity_group != "PS":
                    continue

                if len(word) < 2:
                    continue

                start = offset + ent["start"]
                end = offset + ent["end"]

                new_match = PIIMatch(
                    type="name",
                    value=word,
                    start=start,
                    end=end,
                )

                _add_if_no_overlap(combined, new_match)

        combined_list.append(combined)

    return combined_list


# --------------------------------------------------
# 3차: LLM 잔여 개인정보 검사
# - 전체 이력서에 매번 쓰지 말고
# - 의심 케이스 또는 샘플에만 사용
# --------------------------------------------------

def _residual_check_chain():
    from langchain_openai import ChatOpenAI
    from langchain_core.output_parsers import JsonOutputParser
    from langchain_core.prompts import ChatPromptTemplate

    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)

    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "당신은 마스킹된 이력서 텍스트의 개인정보 검수자입니다. "
                "이미 [NAME], [PHONE], [EMAIL], [RESIDENT_ID]처럼 마스킹된 부분은 무시하세요. "
                "아직 남아있는 이름, 전화번호, 이메일, 주소, 생년월일, 주민번호만 찾으세요. "
                "반드시 JSON 배열로만 응답하세요. "
                '예: [{{"type": "name", "value": "홍길동"}}]. '
                "없으면 []를 반환하세요.",
            ),
            ("user", "{masked_text}"),
        ]
    )

    return prompt | llm | JsonOutputParser()


def check_residual_pii(masked_text: str) -> list[dict]:
    return _residual_check_chain().invoke({"masked_text": masked_text})


def _mask_known_values(
    text: str,
    found: list[dict],
) -> tuple[str, list[PIIMatch]]:
    new_matches = []

    for item in found:
        value = item.get("value")
        pii_type = item.get("type", "pii")

        if not value:
            continue

        for m in re.finditer(re.escape(value), text):
            new_matches.append(
                PIIMatch(
                    type=pii_type,
                    value=value,
                    start=m.start(),
                    end=m.end(),
                )
            )

    return mask(text, new_matches), new_matches


# --------------------------------------------------
# QA 대상 선정
# --------------------------------------------------

def should_check_with_llm(
    original_text: str,
    masked_text: str,
    matches: list[PIIMatch],
) -> bool:
    types = {m.type for m in matches}

    if "name" not in types:
        return True

    if "phone" not in types and "email" not in types:
        return True

    if len(original_text) - len(masked_text) < 10:
        return True

    return False


def select_for_qa(
    texts: list[str],
    masked_texts: list[str],
    matches_list: list[list[PIIMatch]],
    sample_rate: float = 0.05,
) -> list[int]:
    n = len(texts)

    if n == 0:
        return []

    indices = set()

    sample_size = max(1, int(n * sample_rate))
    sample_size = min(sample_size, n)

    indices.update(random.sample(range(n), sample_size))

    for i, (text, masked_text, matches) in enumerate(
        zip(texts, masked_texts, matches_list)
    ):
        if should_check_with_llm(text, masked_text, matches):
            indices.add(i)

    return sorted(indices)

# --------------------------------------------------
# 대량 처리
# --------------------------------------------------

def process_resume_batch(
    resumes: list[dict],
    qa_sample_rate: float = 0.05,
) -> list[dict]:
    """
    입력 예시:
    [
        {"filename": "resume1.pdf", "text": "..."},
        {"filename": "resume2.docx", "text": "..."},
    ]

    반환:
    [
        {
            "filename": "resume1.pdf",
            "masked_text": "...",
            "pii": [...],
            "masking_status": "MASKED",
            "qa_checked": True,
            "residual": [],
        }
    ]
    """

    texts = [resume["text"] for resume in resumes]

    stage1_matches_list = [
        extract_stage1(text)
        for text in texts
    ]

    combined_matches_list = extract_stage2_batch(
        texts=texts,
        stage1_matches_list=stage1_matches_list,
    )

    masked_texts = [
        mask(text, matches)
        for text, matches in zip(texts, combined_matches_list)
    ]

    qa_indices = select_for_qa(
        texts=texts,
        masked_texts=masked_texts,
        matches_list=combined_matches_list,
        sample_rate=qa_sample_rate,
    )

    results = []

    for i, resume in enumerate(resumes):
        filename = resume["filename"]
        masked_text = masked_texts[i]
        matches = combined_matches_list[i]
        residual = []
        qa_checked = i in qa_indices

        if qa_checked:
            residual = check_residual_pii(masked_text)

            if residual:
                masked_text, residual_matches = _mask_known_values(
                    masked_text,
                    residual,
                )
                matches += residual_matches

        status = "NEEDS_REVIEW" if residual else "MASKED"

        pii = [
            {
                "type": m.type,
                "value": m.value,
            }
            for m in matches
            if m.type in SAVABLE_TYPES
        ]

        real_name = get_pii_value(matches, "name")
        phone = get_pii_value(matches, "phone")
        email = get_pii_value(matches, "email")

        results.append(
            {
                "filename": filename,
                "real_name": real_name,
                "phone": phone,
                "email": email,
                "masked_text": masked_text,
                "pii": pii,
                "masking_status": status,
                "qa_checked": qa_checked,
                "residual": residual,
            }
        )
    return results