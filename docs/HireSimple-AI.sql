CREATE TABLE users (
    id BIGINT NOT NULL,
    email VARCHAR(255) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    name VARCHAR(100) NOT NULL,
    company_name VARCHAR(150) NULL,
    role VARCHAR(30) DEFAULT 'recruiter' NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE TABLE job_postings (
    id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    title VARCHAR(200) NOT NULL,
    input_type VARCHAR(10) CHECK (input_type IN ('url')) NOT NULL,
    source_url TEXT NULL,
    raw_content TEXT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE TABLE formating_postings (
    id BIGINT NOT NULL,
    category VARCHAR(20) CHECK (category IN ('자격 요건', '기술 스택', '주요 업무', '우대 사항')) NOT NULL,
    content TEXT NOT NULL,
    is_required BOOLEAN DEFAULT FALSE NOT NULL,
    sort_order INT DEFAULT 0 NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    job_posting_id BIGINT NOT NULL
);

CREATE TABLE skills_stack (
    id BIGINT NOT NULL,
    job_posting_id BIGINT NOT NULL,
    skill_name VARCHAR(100) NOT NULL,
    sort_order INT DEFAULT 0 NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE TABLE type_criteria (
    id BIGINT NOT NULL,
    job_posting_id BIGINT NOT NULL,
    criterion_type VARCHAR(20) CHECK (criterion_type IN ('자격 조건', '주요 업무', '우대 사항')) NOT NULL,
    description TEXT NULL,
    type_weight DECIMAL(5,2) NOT NULL,
    sort_order INT DEFAULT 0 NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE TABLE detail_criteria (
    id BIGINT NOT NULL,
    type_criteria_id BIGINT NOT NULL,
    detail TEXT NULL,
    weight DECIMAL(5,2) NOT NULL,
    sort_order INT DEFAULT 0 NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE TABLE applicants (
    id BIGINT NOT NULL,
    job_posting_id BIGINT NOT NULL,
    masked_code VARCHAR(50) NOT NULL,
    real_name VARCHAR(100) NULL,
    phone VARCHAR(50) NULL,
    email VARCHAR(255) NULL,
    address TEXT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE TABLE resume_files (
    id BIGINT NOT NULL,
    applicant_id BIGINT NOT NULL,
    original_filename VARCHAR(255) NOT NULL,
    file_path TEXT NOT NULL,
    file_type VARCHAR(20) NOT NULL,
    file_size_bytes BIGINT NULL,
    extracted_text TEXT NULL,
    masked_text TEXT NULL,
    processing_status VARCHAR(20) CHECK (processing_status IN ('uploaded', 'extracted', 'masked', 'failed')) DEFAULT 'uploaded' NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE TABLE applicant_scores (
    id BIGINT NOT NULL,
    applicant_id BIGINT NOT NULL,
    job_posting_id BIGINT NOT NULL,
    total_score DECIMAL(5,2) NOT NULL,
    requirement_score DECIMAL(5,2) NOT NULL,
    skill_score DECIMAL(5,2) NOT NULL,
    task_score DECIMAL(5,2) NOT NULL,
    preference_score DECIMAL(5,2) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE TABLE detail_scores (
    id BIGINT NOT NULL,
    applicant_id BIGINT NOT NULL,
    type_criteria_id BIGINT NOT NULL,
    detail_criteria_id BIGINT NOT NULL,
    score DECIMAL(5,2) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE TABLE interview_questions (
    id BIGINT NOT NULL,
    applicant_id BIGINT NOT NULL,
    question_type VARCHAR(20) CHECK (question_type IN ('행동', '역량', '우려 검증', '기술 검증', '기타')) NOT NULL,
    question_text TEXT NOT NULL,
    created_by VARCHAR(10) CHECK (created_by IN ('AI', 'USER')) NULL,
    compliance_status VARCHAR(10) CHECK (compliance_status IN ('준수', '경고', '심각')) NOT NULL,
    revised_question_text TEXT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE TABLE comparison_sets (
    id BIGINT NOT NULL,
    job_posting_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    title VARCHAR(200) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE TABLE comparison_items (
    id BIGINT NOT NULL,
    comparison_set_id BIGINT NOT NULL,
    applicant_id BIGINT NOT NULL,
    rank_no INT NULL,
    comparison_summary TEXT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);

-- PK 설정
ALTER TABLE users ADD CONSTRAINT PK_USERS PRIMARY KEY (id);
ALTER TABLE job_postings ADD CONSTRAINT PK_JOB_POSTINGS PRIMARY KEY (id);
ALTER TABLE formating_postings ADD CONSTRAINT PK_FORMATING_POSTINGS PRIMARY KEY (id);
ALTER TABLE skills_stack ADD CONSTRAINT PK_SKILLS_STACK PRIMARY KEY (id);
ALTER TABLE type_criteria ADD CONSTRAINT PK_TYPE_CRITERIA PRIMARY KEY (id);
ALTER TABLE detail_criteria ADD CONSTRAINT PK_DETAIL_CRITERIA PRIMARY KEY (id);
ALTER TABLE applicants ADD CONSTRAINT PK_APPLICANTS PRIMARY KEY (id);
ALTER TABLE resume_files ADD CONSTRAINT PK_RESUME_FILES PRIMARY KEY (id);
ALTER TABLE applicant_scores ADD CONSTRAINT PK_APPLICANT_SCORES PRIMARY KEY (id);
ALTER TABLE detail_scores ADD CONSTRAINT PK_DETAIL_SCORES PRIMARY KEY (id);
ALTER TABLE interview_questions ADD CONSTRAINT PK_INTERVIEW_QUESTIONS PRIMARY KEY (id);
ALTER TABLE comparison_sets ADD CONSTRAINT PK_COMPARISON_SETS PRIMARY KEY (id);
ALTER TABLE comparison_items ADD CONSTRAINT PK_COMPARISON_ITEMS PRIMARY KEY (id);

-- FK 설정
ALTER TABLE job_postings ADD CONSTRAINT FK_JOB_POSTINGS_USER FOREIGN KEY (user_id) REFERENCES users(id);
ALTER TABLE formating_postings ADD CONSTRAINT FK_FORMATING_POSTINGS_JOB FOREIGN KEY (job_posting_id) REFERENCES job_postings(id);
ALTER TABLE skills_stack ADD CONSTRAINT FK_SKILLS_STACK_JOB FOREIGN KEY (job_posting_id) REFERENCES job_postings(id);
ALTER TABLE type_criteria ADD CONSTRAINT FK_TYPE_CRITERIA_JOB FOREIGN KEY (job_posting_id) REFERENCES job_postings(id);
ALTER TABLE detail_criteria ADD CONSTRAINT FK_DETAIL_CRITERIA_TYPE FOREIGN KEY (type_criteria_id) REFERENCES type_criteria(id);
ALTER TABLE applicants ADD CONSTRAINT FK_APPLICANTS_JOB FOREIGN KEY (job_posting_id) REFERENCES job_postings(id);
ALTER TABLE resume_files ADD CONSTRAINT FK_RESUME_FILES_APPLICANT FOREIGN KEY (applicant_id) REFERENCES applicants(id);
ALTER TABLE applicant_scores ADD CONSTRAINT FK_APPLICANT_SCORES_APPLICANT FOREIGN KEY (applicant_id) REFERENCES applicants(id);
ALTER TABLE applicant_scores ADD CONSTRAINT FK_APPLICANT_SCORES_JOB FOREIGN KEY (job_posting_id) REFERENCES job_postings(id);
ALTER TABLE detail_scores ADD CONSTRAINT FK_DETAIL_SCORES_APPLICANT FOREIGN KEY (applicant_id) REFERENCES applicants(id);
ALTER TABLE interview_questions ADD CONSTRAINT FK_INTERVIEW_QUESTIONS_APPLICANT FOREIGN KEY (applicant_id) REFERENCES applicants(id);
ALTER TABLE comparison_sets ADD CONSTRAINT FK_COMPARISON_SETS_JOB FOREIGN KEY (job_posting_id) REFERENCES job_postings(id);
ALTER TABLE comparison_sets ADD CONSTRAINT FK_COMPARISON_SETS_USER FOREIGN KEY (user_id) REFERENCES users(id);
ALTER TABLE comparison_items ADD CONSTRAINT FK_COMPARISON_ITEMS_SET FOREIGN KEY (comparison_set_id) REFERENCES comparison_sets(id);
ALTER TABLE comparison_items ADD CONSTRAINT FK_COMPARISON_ITEMS_APPLICANT FOREIGN KEY (applicant_id) REFERENCES applicants(id);