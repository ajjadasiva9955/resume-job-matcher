import re
import json
import hashlib
import os
import concurrent.futures
from PyPDF2 import PdfReader
import google.generativeai as genai
from skill_extractor import SKILL_SET, format_skill_name, SKILL_DISPLAY_MAP


# =====================================================================
# 1. EXPERIENCE EXTRACTION & FRESHER FILTERING
# =====================================================================

EXP_RANGE_PATTERN = re.compile(
    r'\b(?:(?:experience|exp)\s*(?:required|needed|level)?\s*[:\-–]?\s*)?(\d+)\s*(?:[-–to]+)\s*(\d+)\s*(?:years?|yrs?)(?:\s*(?:of\s*)?experience)?\b',
    re.IGNORECASE
)
EXP_MIN_PLUS_PATTERN = re.compile(
    r'\b(?:(?:min(?:imum)?\.?\s*(?:of)?\s*)?(\d+)\s*(?:\+|plus)\s*(?:years?|yrs?)(?:\s*(?:of\s*)?experience)?)\b',
    re.IGNORECASE
)
EXP_MIN_EXACT_PATTERN = re.compile(
    r'\b(?:min(?:imum)?\.?\s*(?:of\s*)?(\d+)\s*(?:years?|yrs?)(?:\s*(?:of\s*)?experience)?)\b',
    re.IGNORECASE
)
EXP_SIMPLE_YEARS_PATTERN = re.compile(
    r'\b(\d+)\s*(?:years?|yrs?)\s*(?:of\s*)?(?:experience|exp|hands[- ]on)\b',
    re.IGNORECASE
)
FRESHER_KEYWORDS_PATTERN = re.compile(
    r'\b(freshers?\s*welcome|freshers?\s*can\s*apply|for\s*freshers?|fresher|freshers|entry[\s-]level|no\s*experience\s*(?:is\s*)?required|no\s*prior\s*experience|0\s*years?(?:\s*of\s*experience)?|0\s*yrs?|recent\s*graduates?|college\s*graduates?|trainee|internship|intern)\b',
    re.IGNORECASE
)
SENIOR_TITLE_KEYWORDS = re.compile(
    r'\b(senior|sr\.?|lead|principal|architect|director|vp|vice president|staff engineer|head of|manager)\b',
    re.IGNORECASE
)


def extract_experience_info(job):
    """
    Extracts experience requirement from job extensions, detected_extensions,
    description, and title.
    
    Returns a dict:
    {
        "display_text": str (e.g. "0-2 Yrs", "Freshers", "No experience required", or "" if not mentioned),
        "min_years": float/int or None,
        "max_years": float/int or None,
        "is_fresher_compatible": bool,
        "is_mentioned": bool,
    }
    """
    title = str(job.get("title") or "")
    description = str(job.get("description") or job.get("job_description") or "")
    detected_ext = job.get("detected_extensions") or {}
    extensions = job.get("extensions") or []

    display_text = ""
    min_years = None
    max_years = None
    is_fresher_kw = False

    # 1. Check extensions first (high precision)
    for ext in extensions:
        if not isinstance(ext, str):
            continue
        ext_clean = ext.strip()
        m_range = EXP_RANGE_PATTERN.search(ext_clean)
        if m_range:
            min_y = int(m_range.group(1))
            max_y = int(m_range.group(2))
            min_years = min_y
            max_years = max_y
            display_text = f"{min_y}-{max_y} Yrs"
            break

        m_plus = EXP_MIN_PLUS_PATTERN.search(ext_clean)
        if m_plus:
            min_y = int(m_plus.group(1))
            min_years = min_y
            display_text = f"{min_y}+ Yrs"
            break

        m_fresh = FRESHER_KEYWORDS_PATTERN.search(ext_clean)
        if m_fresh:
            is_fresher_kw = True
            kw_match = m_fresh.group(1).lower()
            if "no experience" in kw_match:
                display_text = "No experience required"
            elif "entry" in kw_match:
                display_text = "Entry level"
            elif "fresh" in kw_match:
                display_text = "Freshers"
            else:
                display_text = "Freshers"
            min_years = 0
            max_years = 1
            break

    # 2. Check detected_extensions schedule_type or explicit field
    if not display_text:
        sched = str(detected_ext.get("schedule_type") or "")
        if any(w in sched.lower() for w in ["yr", "year", "fresher", "entry"]):
            m_range = EXP_RANGE_PATTERN.search(sched)
            if m_range:
                min_y = int(m_range.group(1))
                max_y = int(m_range.group(2))
                min_years = min_y
                max_years = max_y
                display_text = f"{min_y}-{max_y} Yrs"
            elif FRESHER_KEYWORDS_PATTERN.search(sched):
                is_fresher_kw = True
                display_text = "Freshers"
                min_years = 0

    # 3. Check description text if not yet found
    if not display_text and description:
        m_fresh = FRESHER_KEYWORDS_PATTERN.search(description)
        if m_fresh:
            is_fresher_kw = True
            kw_match = m_fresh.group(1).lower()
            if "no experience" in kw_match:
                display_text = "No experience required"
            elif "entry" in kw_match:
                display_text = "Entry level"
            else:
                display_text = "Freshers"
            min_years = 0
            max_years = 1

        if not display_text:
            m_range = EXP_RANGE_PATTERN.search(description)
            if m_range:
                min_y = int(m_range.group(1))
                max_y = int(m_range.group(2))
                min_years = min_y
                max_years = max_y
                display_text = f"{min_y}-{max_y} Yrs"

        if not display_text:
            m_plus = EXP_MIN_PLUS_PATTERN.search(description)
            if m_plus:
                min_y = int(m_plus.group(1))
                min_years = min_y
                display_text = f"{min_y}+ Yrs"

        if not display_text:
            m_min = EXP_MIN_EXACT_PATTERN.search(description)
            if m_min:
                min_y = int(m_min.group(1))
                min_years = min_y
                display_text = f"{min_y}+ Yrs" if min_y > 0 else "Freshers"

        if not display_text:
            m_simple = EXP_SIMPLE_YEARS_PATTERN.search(description)
            if m_simple:
                min_y = int(m_simple.group(1))
                min_years = min_y
                display_text = f"{min_y}+ Yrs" if min_y > 0 else "Freshers"

    # 4. Check title for Senior / Lead / Fresher indicators
    is_senior_title = bool(SENIOR_TITLE_KEYWORDS.search(title))
    if not display_text and "fresher" in title.lower():
        display_text = "Freshers"
        min_years = 0
        is_fresher_kw = True

    is_fresher_compatible = True
    is_mentioned = bool(display_text)

    if min_years is not None:
        if min_years >= 3:
            is_fresher_compatible = False
        elif min_years > 2:
            is_fresher_compatible = False
        elif min_years == 2 and max_years is not None and max_years > 5:
            is_fresher_compatible = False
    elif is_senior_title and not is_fresher_kw:
        is_fresher_compatible = False

    return {
        "display_text": display_text if is_mentioned else "",
        "min_years": min_years,
        "max_years": max_years,
        "is_fresher_compatible": is_fresher_compatible,
        "is_mentioned": is_mentioned,
    }


# =====================================================================
# 2. SALARY EXTRACTION (NEVER FABRICATE)
# =====================================================================

SALARY_REGEX = re.compile(
    r'(?:₹|INR|Rs\.?|\$|USD|EUR|£)\s*[\d,]+(?:\.\d+)?\s*(?:[-–to]+\s*(?:₹|INR|Rs\.?|\$|USD|EUR|£)?\s*[\d,]+(?:\.\d+)?\s*)?(?:LPA|PA|Per Annum|per year|a year|/yr|/year|k/mo|per month|/month)?',
    re.IGNORECASE
)
SALARY_LPA_REGEX = re.compile(
    r'\b\d+(?:\.\d+)?\s*(?:[-–to]+\s*\d+(?:\.\d+)?\s*)?LPA\b',
    re.IGNORECASE
)


def extract_salary_info(job):
    """
    Extracts salary explicitly from job data.
    If no explicit salary exists, returns 'Not mentioned'.
    Never invents or estimates salary.
    """
    detected_ext = job.get("detected_extensions") or {}
    extensions = job.get("extensions") or []
    description = str(job.get("description") or job.get("job_description") or "")

    det_sal = detected_ext.get("salary")
    if det_sal and isinstance(det_sal, str) and det_sal.strip():
        return det_sal.strip()

    for field in ["salary", "salary_range", "compensation", "pay", "estimated_salary", "salary_text"]:
        val = job.get(field)
        if val and isinstance(val, str) and val.strip() and val.strip().lower() not in ["not mentioned", "none", "null", ""]:
            if any(curr in val for curr in ["₹", "INR", "Rs", "$", "USD", "£", "€", "LPA", "PA", "per year", "a year", "/yr", "month"]):
                return val.strip()

    for ext in extensions:
        if not isinstance(ext, str):
            continue
        ext_clean = ext.strip()
        if any(kw in ext_clean.lower() for kw in ["full-time", "part-time", "contractor", "internship", "ago", "posted", "yesterday"]):
            continue
        if any(curr in ext_clean for curr in ["₹", "INR", "Rs.", "Rs ", "$", "USD", "£", "€", "LPA", "PA", "per year", "a year", "/yr", "per month", "/mo"]):
            return ext_clean

    if description:
        m_sal = SALARY_REGEX.search(description)
        if m_sal:
            matched = m_sal.group(0).strip()
            if len(matched) >= 3 and any(c in matched for c in ["₹", "LPA", "PA", "year", "$", "INR", "Rs", "month"]):
                return matched
        m_lpa = SALARY_LPA_REGEX.search(description)
        if m_lpa:
            return f"₹ {m_lpa.group(0).strip()}"

    return "Not mentioned"


# =====================================================================
# 3. POSTED TIME & OPENINGS EXTRACTION
# =====================================================================

def extract_posted_time(job):
    """
    Extracts posted time from actual job data.
    Preserves relative strings from SerpAPI ("3 days ago", "1 day ago", "Today").
    Never invents random timestamps.
    """
    detected_ext = job.get("detected_extensions") or {}
    extensions = job.get("extensions") or []

    det_posted = detected_ext.get("posted_at")
    if det_posted and isinstance(det_posted, str) and det_posted.strip():
        val = det_posted.strip()
        return val if val.lower().startswith("posted") else f"Posted {val}"

    for ext in extensions:
        if not isinstance(ext, str):
            continue
        ext_clean = ext.strip()
        ext_lower = ext_clean.lower()
        if any(kw in ext_lower for kw in ["ago", "yesterday", "today", "posted", "hours", "days", "weeks", "months"]):
            if len(ext_clean) < 40 and not any(kw in ext_lower for kw in ["apply", "salary", "lpa", "experience"]):
                return ext_clean if ext_lower.startswith("posted") else f"Posted {ext_clean}"

    for field in ["posted_at", "date_posted", "published_at", "posted_time", "detected_at"]:
        val = job.get(field)
        if val and isinstance(val, str) and val.strip():
            val_clean = val.strip()
            return val_clean if val_clean.lower().startswith("posted") else f"Posted {val_clean}"

    return ""


OPENINGS_PATTERNS = [
    re.compile(r'(?:number\s+of\s+)?openings\s*[:=-]\s*(\d+)', re.IGNORECASE),
    re.compile(r'(?:number\s+of\s+)?vacanc(?:ies|y)\s*[:=-]\s*(\d+)', re.IGNORECASE),
    re.compile(r'\b(\d+)\s+vacanc(?:ies|y)\b', re.IGNORECASE),
    re.compile(r'\bhiring\s+for\s+(\d+)\s+(?:openings|vacancies|positions)\b', re.IGNORECASE),
    re.compile(r'\b(\d+)\s+positions?\s+available\b', re.IGNORECASE),
    re.compile(r'\b(\d+)\s+open\s+positions?\b', re.IGNORECASE),
    re.compile(r'\b(\d+)\s+openings?\b', re.IGNORECASE),
]


def _clean_and_validate_openings_count(raw_val):
    if raw_val is None or isinstance(raw_val, bool):
        return None
    if isinstance(raw_val, (int, float)):
        try:
            val_int = int(raw_val)
            if 1 <= val_int <= 9999:
                return str(val_int)
        except Exception:
            return None
        return None

    val_str = str(raw_val).strip()
    if not val_str or val_str.lower() in ["na", "none", "null", "not mentioned", "not specified", "undefined"]:
        return None

    if "multiple" in val_str.lower() or "several" in val_str.lower():
        return None

    if val_str.isdigit():
        try:
            val_int = int(val_str)
            if 1 <= val_int <= 9999:
                return str(val_int)
        except Exception:
            pass
        return None

    for pat in OPENINGS_PATTERNS:
        m = pat.search(val_str)
        if m:
            try:
                val_int = int(m.group(1))
                if 1 <= val_int <= 9999:
                    return str(val_int)
            except Exception:
                pass

    return None


def extract_openings_info(job):
    if not isinstance(job, dict):
        return "NA"

    detected_ext = job.get("detected_extensions") or {}
    extensions = job.get("extensions") or []
    description = str(job.get("description") or job.get("job_description") or "")

    for field in ["openings", "openings_count", "vacancy", "vacancies", "vacancy_count", "number_of_openings"]:
        val = job.get(field)
        cleaned = _clean_and_validate_openings_count(val)
        if cleaned:
            return cleaned

    if isinstance(detected_ext, dict):
        for field in ["openings", "openings_count", "vacancies", "vacancy", "vacancy_count", "number_of_openings"]:
            val = detected_ext.get(field)
            cleaned = _clean_and_validate_openings_count(val)
            if cleaned:
                return cleaned

    if isinstance(extensions, list):
        for ext in extensions:
            if not isinstance(ext, str):
                continue
            cleaned = _clean_and_validate_openings_count(ext)
            if cleaned:
                return cleaned

    if description:
        for pat in OPENINGS_PATTERNS:
            m = pat.search(description)
            if m:
                cleaned = _clean_and_validate_openings_count(m.group(1))
                if cleaned:
                    return cleaned

    return "NA"


# =====================================================================
# 4. CANONICAL SKILL MAPPING, NORMALIZATION & TAXONOMY
# =====================================================================

SKILL_CANONICAL_MAP = {
    "js": "JavaScript", "javascript": "JavaScript", "ecmascript": "JavaScript",
    "ts": "TypeScript", "typescript": "TypeScript",
    "aws": "AWS", "amazon web services": "AWS",
    "gcp": "GCP", "google cloud": "GCP", "google cloud platform": "GCP",
    "azure": "Azure", "microsoft azure": "Azure",
    "postgres": "PostgreSQL", "postgresql": "PostgreSQL",
    "node": "Node.js", "node.js": "Node.js", "nodejs": "Node.js",
    "react": "React", "react.js": "React", "reactjs": "React",
    "ml": "Machine Learning", "machine learning": "Machine Learning",
    "nlp": "NLP", "natural language processing": "NLP",
    "k8s": "Kubernetes", "kubernetes": "Kubernetes",
    "ci/cd": "CI/CD", "cicd": "CI/CD", "continuous integration": "CI/CD", "ci cd": "CI/CD",
    "rest": "REST API", "rest api": "REST API", "restful api": "REST API", "rest apis": "REST API", "restful apis": "REST API", "restful": "REST API",
    "ai": "Artificial Intelligence", "artificial intelligence": "Artificial Intelligence",
    "tf": "TensorFlow", "tensorflow": "TensorFlow",
    "cv": "Computer Vision", "computer vision": "Computer Vision",
    "mongo": "MongoDB", "mongodb": "MongoDB",
    "nextjs": "Next.js", "next.js": "Next.js",
    "express": "Express.js", "expressjs": "Express.js", "express.js": "Express.js",
    "vue": "Vue.js", "vuejs": "Vue.js", "vue.js": "Vue.js",
    "tailwind": "Tailwind CSS", "tailwindcss": "Tailwind CSS", "tailwind css": "Tailwind CSS",
    "bootstrap": "Bootstrap", "bootstrap5": "Bootstrap", "bootstrap 5": "Bootstrap",
    "spring": "Spring", "spring framework": "Spring",
    "spring boot": "Spring Boot", "springboot": "Spring Boot", "spring-boot": "Spring Boot",
    "microservices": "Microservices", "microservice": "Microservices", "micro-services": "Microservices",
    "fastapi": "FastAPI", "fast api": "FastAPI",
    "scikit-learn": "Scikit-Learn", "sklearn": "Scikit-Learn",
    "docker": "Docker", "git": "Git", "github": "GitHub", "gitlab": "GitLab",
    "linux": "Linux", "sql": "SQL", "sqlite": "SQLite", "mysql": "MySQL", "redis": "Redis",
    "java": "Java", "python": "Python", "c++": "C++", "cpp": "C++",
    "c#": "C#", "csharp": "C#", "c": "C",
    "react native": "React Native", "react-native": "React Native",
    "nosql": "NoSQL", "django": "Django", "flask": "Flask",
    "html": "HTML", "html5": "HTML", "css": "CSS", "css3": "CSS",
    "pandas": "Pandas", "numpy": "NumPy", "postman": "Postman", "jira": "Jira",
    "graphql": "GraphQL",
    "deep learning": "Deep Learning", "pytorch": "PyTorch", "keras": "Keras",
    "opencv": "OpenCV", "streamlit": "Streamlit", "angular": "Angular",
    "problem solving": "Problem Solving", "data structures": "Data Structures", "dsa": "Data Structures",
    "algorithms": "Algorithms", "oop": "OOP", "object oriented programming": "OOP", "oops": "OOP",
    "communication": "Communication", "teamwork": "Teamwork",
    "unit testing": "Unit Testing", "junit": "JUnit", "pytest": "PyTest", "testing": "Unit Testing",
    "agile": "Agile", "scrum": "Scrum", "agile/scrum": "Agile/Scrum",
    "security": "Security", "secure coding": "Secure Coding",
    "kafka": "Apache Kafka", "apache kafka": "Apache Kafka", "rabbitmq": "RabbitMQ",
    "event-driven": "Event-driven Architecture", "event driven": "Event-driven Architecture",
    "relational database": "Relational Databases", "relational db": "Relational Databases", "rdbms": "Relational Databases",
    "system design": "System Design", "architecture": "Software Architecture",
}

STRICT_NON_EQUIVALENCES = [
    ({"java"}, {"javascript"}),
    ({"c"}, {"c++"}),
    ({"c++"}, {"c#"}),
    ({"c"}, {"c#"}),
    ({"react"}, {"react native"}),
    ({"sql"}, {"nosql"}),
    ({"django"}, {"flask"}),
    ({"spring"}, {"spring boot"}),
    ({"python"}, {"php"}),
    ({"html"}, {"css"}),
    ({"postgresql"}, {"mongodb"}),
    ({"angular"}, {"react"}),
]

STOP_WORDS = {
    "and", "the", "for", "with", "this", "that", "you", "your", "will", "are",
    "have", "from", "looking", "seeking", "requirements", "skills", "experience",
    "years", "role", "work", "team", "join", "our", "must", "plus", "bonus", "good",
    "knowledge", "strong", "preferred", "qualifications", "developer", "engineer",
    "candidate", "candidates", "opportunity", "responsibilities"
}


def normalize_skill(skill):
    if not skill or not isinstance(skill, str):
        return ""
    clean = skill.strip().lower()
    return SKILL_CANONICAL_MAP.get(clean, format_skill_name(skill))


def are_skills_equivalent(s1, s2):
    if not s1 or not s2:
        return False
    n1 = normalize_skill(s1).lower()
    n2 = normalize_skill(s2).lower()
    if n1 == n2:
        return True

    for g1, g2 in STRICT_NON_EQUIVALENCES:
        if (n1 in g1 and n2 in g2) or (n1 in g2 and n2 in g1):
            return False

    return False


def extract_job_skills_and_keywords(title, description):
    text = f"{title} {description}".lower()
    found_skills = set()

    for skill in SKILL_SET:
        pattern = rf"\b{re.escape(skill.lower())}\b"
        if re.search(pattern, text):
            norm = normalize_skill(skill)
            if norm:
                found_skills.add(norm)

    for alias, canonical in SKILL_CANONICAL_MAP.items():
        pattern = rf"\b{re.escape(alias.lower())}\b"
        if re.search(pattern, text):
            found_skills.add(canonical)

    return found_skills


# =====================================================================
# 5. MAIN PROFILE RESUME PARSING & STRUCTURED REPRESENTATION
# =====================================================================

RESUME_PARSED_CACHE = {}
ATS_SCORE_CACHE = {}


def invalidate_resume_cache(user_id=None):
    """
    Clears in-memory ATS parsed and score caches for a user or globally.
    Ensures that when a new Main Profile Resume is uploaded, previous scores are invalidated.
    """
    global RESUME_PARSED_CACHE, ATS_SCORE_CACHE
    if user_id is None:
        RESUME_PARSED_CACHE.clear()
        ATS_SCORE_CACHE.clear()
    else:
        RESUME_PARSED_CACHE = {k: v for k, v in RESUME_PARSED_CACHE.items() if k[0] != user_id and k[0] != str(user_id)}
        ATS_SCORE_CACHE = {k: v for k, v in ATS_SCORE_CACHE.items() if k[0] != user_id and k[0] != str(user_id)}


def parse_main_profile_resume(resume_source):
    """
    Parses the candidate's Main Profile Resume deeply and returns a rich structured representation:
    {
        candidate_name, contact, summary, stated_career_level, skills, normalized_skills,
        skills_by_category, skills_context, experience, internships,
        total_years_experience, seniority_level, is_fresher, education, highest_degree,
        education_field, projects, certifications, achievements, ats_readability_score,
        resume_text, resume_hash
    }
    Caches parsed result by resume_hash so it runs ONCE per resume version.
    """
    if not resume_source:
        return None

    raw_text = ""
    file_path = None
    user_id = None
    input_skills = []
    input_roles = []

    if isinstance(resume_source, dict):
        user_id = resume_source.get("user_id")
        file_path = resume_source.get("file_path")
        input_skills = resume_source.get("skills") or []
        input_roles = resume_source.get("roles") or []
        raw_text = resume_source.get("resume_text") or ""
        if not raw_text and resume_source.get("extracted_data"):
            ext = resume_source["extracted_data"]
            if isinstance(ext, dict):
                input_skills = input_skills or ext.get("skills", [])
                input_roles = input_roles or ext.get("roles", [])
                raw_text = ext.get("resume_text_preview", "")
    elif isinstance(resume_source, str):
        if os.path.exists(resume_source):
            file_path = resume_source
        else:
            raw_text = resume_source
    elif isinstance(resume_source, (int, float)):
        user_id = int(resume_source)
        try:
            import auth_db
            user_res = auth_db.get_user_resume(user_id, current_only=True)
            if user_res:
                file_path = user_res.get("file_path")
                if user_res.get("extracted_data"):
                    ext = user_res["extracted_data"]
                    input_skills = ext.get("skills", [])
                    input_roles = ext.get("roles", [])
                    raw_text = ext.get("resume_text_preview", "")
        except Exception:
            pass

    extracted_doc = None
    parsed_data = None
    if file_path:
        if not os.path.isabs(file_path):
            base_dir = os.path.dirname(os.path.abspath(__file__))
            cand_path = os.path.join(base_dir, file_path)
            if os.path.exists(cand_path):
                file_path = cand_path

        if os.path.exists(file_path):
            try:
                from ats_engine import extract_resume_document, parse_resume_structure
                extracted_doc = extract_resume_document(file_path)
                if extracted_doc and extracted_doc.get("raw_text"):
                    raw_text = extracted_doc["raw_text"]
                    parsed_data = parse_resume_structure(extracted_doc)
            except Exception:
                pass

    if not raw_text and not input_skills:
        return None

    resume_hash = hashlib.sha256(f"{raw_text}_{'_'.join(sorted(input_skills))}".encode("utf-8")).hexdigest()[:16]
    cache_key = (user_id or "anon", resume_hash)
    if cache_key in RESUME_PARSED_CACHE:
        return RESUME_PARSED_CACHE[cache_key]

    text_lower = raw_text.lower() if raw_text else ""

    # Contact & Personal Information
    candidate_name = ""
    contact_info = {}
    if parsed_data:
        candidate_name = parsed_data.get("candidate_name", "")
        contact_info = parsed_data.get("contact_info", {})
    if not candidate_name and raw_text:
        lines = [l.strip() for l in raw_text.splitlines() if l.strip()]
        for l in lines[:4]:
            if "@" not in l and "http" not in l and len(l.split()) in [2, 3, 4] and len(l) < 35:
                candidate_name = l
                break

    # Deep Skill Extraction
    all_skills_set = set()
    for s in input_skills:
        norm = normalize_skill(s)
        if norm:
            all_skills_set.add(norm)

    for skill in SKILL_SET:
        pat = rf"\b{re.escape(skill.lower())}\b"
        if re.search(pat, text_lower):
            norm = normalize_skill(skill)
            if norm:
                all_skills_set.add(norm)

    for alias, canonical in SKILL_CANONICAL_MAP.items():
        pat = rf"\b{re.escape(alias.lower())}\b"
        if re.search(pat, text_lower):
            all_skills_set.add(canonical)

    skills_by_cat = {
        "Languages": [],
        "Frontend": [],
        "Backend": [],
        "Databases": [],
        "Cloud & DevOps": [],
        "AI/ML & Data": [],
        "Tools & Methodologies": [],
    }
    lang_set = {"python", "java", "javascript", "typescript", "c++", "c#", "c", "go", "sql", "html", "css", "bash", "r", "ruby", "php", "scala", "dart", "kotlin", "swift"}
    front_set = {"react", "next.js", "vue.js", "angular", "bootstrap", "tailwind css", "html", "css", "redux", "jquery"}
    back_set = {"flask", "django", "fastapi", "spring boot", "spring", "node.js", "express.js", "rest api", "graphql", "microservices", "event-driven architecture"}
    db_set = {"postgresql", "mysql", "mongodb", "sqlite", "redis", "nosql", "oracle", "relational databases"}
    cloud_set = {"aws", "gcp", "azure", "docker", "kubernetes", "git", "github", "gitlab", "ci/cd", "linux", "jenkins", "terraform"}
    ai_set = {"machine learning", "deep learning", "nlp", "tensorflow", "pytorch", "pandas", "numpy", "scikit-learn", "opencv", "keras"}

    for s in all_skills_set:
        sl = s.lower()
        if sl in lang_set:
            skills_by_cat["Languages"].append(s)
        if sl in front_set:
            skills_by_cat["Frontend"].append(s)
        if sl in back_set:
            skills_by_cat["Backend"].append(s)
        if sl in db_set:
            skills_by_cat["Databases"].append(s)
        if sl in cloud_set:
            skills_by_cat["Cloud & DevOps"].append(s)
        if sl in ai_set:
            skills_by_cat["AI/ML & Data"].append(s)

    # Structured Projects
    projects_list = []
    if parsed_data:
        proj_lines = parsed_data.get("sections", {}).get("projects", [])
        if proj_lines:
            curr_proj = {"title": "Project", "description": "", "technologies": set()}
            for pl in proj_lines:
                curr_proj["description"] += " " + pl
                for s in all_skills_set:
                    if rf"\b{re.escape(s.lower())}\b" in pl.lower() or s.lower() in pl.lower():
                        curr_proj["technologies"].add(s)
            curr_proj["technologies"] = list(curr_proj["technologies"])
            projects_list.append(curr_proj)

    if not projects_list and raw_text:
        # Match lines like: "1. SkillBridge AI: Developed Python Flask REST API backend..."
        proj_matches = re.findall(r"(?:project|\d+\.|\bbuilt\b|\bdeveloped\b|\bcreated\b)\s*[:\-–]?\s*([^\n\.\;]{10,160})", raw_text, re.IGNORECASE)
        for pm in proj_matches[:4]:
            p_tech = [s for s in all_skills_set if s.lower() in pm.lower()]
            projects_list.append({
                "title": pm.strip()[:60],
                "description": pm.strip(),
                "technologies": p_tech
            })

    if not projects_list and (raw_text or input_skills):
        summary_skills = [s for s in all_skills_set if s.lower() in text_lower]
        projects_list.append({
            "title": "Technical Implementation & Systems Development",
            "description": raw_text[:400] if raw_text else ", ".join(input_skills),
            "technologies": summary_skills or list(all_skills_set)
        })

    # Structured Experience & Internships
    exp_lines = parsed_data.get("sections", {}).get("experience", []) if parsed_data else []
    intern_lines = parsed_data.get("sections", {}).get("internships", []) if parsed_data else []
    has_internships = bool(intern_lines) or "intern" in text_lower or "trainee" in text_lower
    has_experience = bool(exp_lines)

    total_years = 0.0
    is_fresher = True
    if has_experience and len(exp_lines) >= 3:
        years_found = re.findall(r"\b(20\d{2})\b", " ".join(exp_lines))
        if len(years_found) >= 2:
            try:
                y_ints = sorted([int(y) for y in years_found])
                diff = y_ints[-1] - y_ints[0]
                if 1 <= diff <= 15:
                    total_years = float(diff)
                    is_fresher = False
            except Exception:
                pass

    # Stated Career Level & Seniority
    if total_years >= 8:
        seniority_level = 5  # Lead / Principal / Architect
        stated_career_level = "Lead / Principal / Architect"
    elif total_years >= 5:
        seniority_level = 4  # Senior
        stated_career_level = "Senior Engineer"
    elif total_years >= 2:
        seniority_level = 3  # Mid
        stated_career_level = "Mid-level Engineer"
    elif total_years >= 1:
        seniority_level = 2  # Junior / Associate
        stated_career_level = "Junior / Associate Engineer"
    else:
        seniority_level = 1  # Fresher / Entry Level
        stated_career_level = "Fresher / Entry Level"

    education_field = "Computer Science" if any(w in text_lower for w in ["computer science", "information technology", "cse", "it", "software engineering"]) else "Engineering / Graduate"
    highest_degree = "Bachelor of Technology" if any(w in text_lower for w in ["b.tech", "btech", "b.e", "be", "bachelor"]) else ("Master's" if "master" in text_lower or "mca" in text_lower or "m.tech" in text_lower else "Graduate")

    # Deep Skill Evidence Hierarchy Mapping (Levels 1 to 6)
    skills_context = {}
    for s in all_skills_set:
        sl = s.lower()
        is_in_exp = any(sl in el.lower() for el in exp_lines)
        is_in_intern = any(sl in il.lower() for il in intern_lines)
        is_in_proj_desc = any(sl in p.get("description", "").lower() for p in projects_list)
        is_in_proj_tech = any(s in p.get("technologies", []) for p in projects_list)
        is_indirect = (s == "Git" and ("github" in text_lower or "gitlab" in text_lower))

        sources = []
        confidence_weight = 0.60
        evidence_level = "LEVEL 5 (Skills Section Only)"

        if is_in_exp and total_years >= 1:
            sources.append("professional_experience")
            confidence_weight = 1.0
            evidence_level = "LEVEL 1 (Professional Experience)"
        elif is_in_intern or (is_in_exp and is_fresher):
            sources.append("internship")
            confidence_weight = 0.95
            evidence_level = "LEVEL 2 (Internship)"
        elif is_in_proj_desc:
            sources.append("projects_implementation")
            confidence_weight = 0.95
            evidence_level = "LEVEL 3 (Project Implementation)"
        elif is_in_proj_tech:
            sources.append("project_tech_stack")
            confidence_weight = 0.90
            evidence_level = "LEVEL 4 (Project Tech Stack)"
        elif is_indirect:
            sources.append("indirect_mention")
            confidence_weight = 0.40
            evidence_level = "LEVEL 6 (Indirect / Related)"
        else:
            sources.append("skills_section")
            confidence_weight = 0.60
            evidence_level = "LEVEL 5 (Skills Section Only)"

        skills_context[sl] = {
            "canonical": s,
            "sources": sources,
            "confidence_weight": confidence_weight,
            "evidence_level": evidence_level,
            "is_demonstrated": bool(is_in_proj_desc or is_in_exp or is_in_intern or is_in_proj_tech),
            "is_indirect": is_indirect
        }

    ats_readability_score = 90
    if extracted_doc:
        try:
            from ats_engine import calculate_readability_format_score
            r_eval = calculate_readability_format_score(extracted_doc, parsed_data or {"section_headings": [], "bullet_points": []})
            ats_readability_score = r_eval.get("score", 90)
        except Exception:
            ats_readability_score = 90

    structured_resume = {
        "user_id": user_id,
        "candidate_name": candidate_name,
        "contact_info": contact_info,
        "summary": " ".join(parsed_data.get("sections", {}).get("summary", [])) if parsed_data else "",
        "stated_career_level": stated_career_level,
        "seniority_level": seniority_level,
        "skills": sorted(list(all_skills_set)),
        "normalized_skills": {s.lower(): s for s in all_skills_set},
        "skills_by_category": skills_by_cat,
        "skills_context": skills_context,
        "roles": input_roles or ["Software Engineer", "Backend Developer"],
        "projects": projects_list,
        "experience": exp_lines,
        "internships": intern_lines,
        "has_internships": has_internships,
        "total_years_experience": total_years,
        "is_fresher": is_fresher,
        "highest_degree": highest_degree,
        "education_field": education_field,
        "ats_readability_score": ats_readability_score,
        "resume_text": raw_text,
        "resume_hash": resume_hash,
    }

    RESUME_PARSED_CACHE[cache_key] = structured_resume
    return structured_resume


# =====================================================================
# 6. JOB DESCRIPTION DEEP ANALYSIS ENGINE
# =====================================================================

def analyze_job_description(job_data):
    """
    Deeply parses the Job Description and extracts:
    - Role title & seniority level (1-5)
    - Domain (Backend, Frontend, AI/ML, DevOps, Full Stack)
    - Required (Must-Have) skills
    - Preferred (Nice-to-Have) skills
    - Responsibility themes (Architecture, Development, Leadership, Testing, CI/CD, Agile)
    - Experience requirement (min_years, max_years)
    - Education requirement
    """
    title = str(job_data.get("title") or job_data.get("job_title") or "").strip()
    company = str(job_data.get("company_name") or job_data.get("company") or "").strip()
    description = str(job_data.get("description") or job_data.get("job_description") or "").strip()
    extensions = job_data.get("extensions") or []
    ext_text = " ".join([str(e) for e in extensions if isinstance(e, str)])

    full_text = f"{title} \n {ext_text} \n {description}"
    full_lower = full_text.lower()
    title_lower = title.lower()

    jd_hash = hashlib.sha256(f"{title}_{company}_{description}".encode("utf-8")).hexdigest()[:16]

    exp_info = job_data.get("experience_info") or extract_experience_info(job_data)

    # Job Seniority Detection
    if any(k in title_lower for k in ["lead", "principal", "architect", "director", "vp", "manager", "head of"]):
        seniority_level = 5
        seniority_title = "Lead / Principal / Architect"
    elif any(k in title_lower for k in ["senior", "sr.", "sr ", "staff"]):
        seniority_level = 4
        seniority_title = "Senior"
    elif any(k in title_lower for k in ["mid", "intermediate"]):
        seniority_level = 3
        seniority_title = "Mid-level"
    elif any(k in title_lower for k in ["junior", "jr.", "associate", "entry"]):
        seniority_level = 2
        seniority_title = "Junior / Associate"
    elif any(k in title_lower or k in full_lower for k in ["fresher", "intern", "graduate", "trainee"]):
        seniority_level = 1
        seniority_title = "Fresher / Entry-level"
    elif exp_info.get("min_years") and exp_info["min_years"] >= 6:
        seniority_level = 4
        seniority_title = "Senior"
    elif exp_info.get("min_years") and exp_info["min_years"] >= 3:
        seniority_level = 3
        seniority_title = "Mid-level"
    else:
        seniority_level = 2
        seniority_title = "Associate"

    all_job_skills = extract_job_skills_and_keywords(title, description)
    all_job_skills_lower = {s.lower(): s for s in all_job_skills}

    required_skills = set()
    preferred_skills = set()

    # Skills in title are unconditionally MUST-HAVE
    for s_low, s_canon in all_job_skills_lower.items():
        if s_low in title_lower:
            required_skills.add(s_canon)

    req_sections = re.findall(r"(?:requirements|must\s+have|required\s+skills|qualifications|what\s+you['’]?ll\s+need|mandatory)[:\n](.*?)(?=(?:preferred|nice\s+to\s+have|bonus|plus|benefits|about\s+us|$))", full_lower, re.DOTALL)
    pref_sections = re.findall(r"(?:preferred|nice\s+to\s+have|good\s+to\s+have|bonus|plus|familiarity\s+with|desired)[:\n](.*?)(?=(?:requirements|qualifications|benefits|about\s+us|$))", full_lower, re.DOTALL)

    if req_sections:
        req_text = " ".join(req_sections)
        for s_low, s_canon in all_job_skills_lower.items():
            if re.search(rf"\b{re.escape(s_low)}\b", req_text):
                required_skills.add(s_canon)

    if pref_sections:
        pref_text = " ".join(pref_sections)
        for s_low, s_canon in all_job_skills_lower.items():
            if re.search(rf"\b{re.escape(s_low)}\b", pref_text) and s_canon not in required_skills:
                preferred_skills.add(s_canon)

    auxiliary_tools = {"git", "github", "gitlab", "docker", "postman", "jira", "linux", "ci/cd", "redis"}
    if not required_skills:
        for s_low, s_canon in all_job_skills_lower.items():
            if s_low in auxiliary_tools:
                preferred_skills.add(s_canon)
            else:
                required_skills.add(s_canon)

    if not required_skills and not preferred_skills:
        for fallback_k in ["Python", "JavaScript", "SQL", "React", "Java", "Flask", "Node.js", "Spring Boot"]:
            if fallback_k.lower() in full_lower:
                required_skills.add(fallback_k)

    # Responsibility Themes Detection in JD
    responsibilities_required = {
        "architecture_design": bool(re.search(r'\b(architect|architecture|system design|high-level design|distributed systems|microservices|scalability|event-driven)\b', full_lower)),
        "core_development": bool(re.search(r'\b(clean code|rest apis?|backend development|develop features|writing code|algorithms|apis?)\b', full_lower)),
        "mentoring_leadership": bool(re.search(r'\b(mentor|lead team|technical leadership|code reviews|guide junior|team lead|hire|own outcomes)\b', full_lower)),
        "testing_quality": bool(re.search(r'\b(unit testing|test-driven|integration test|junit|pytest|automated testing|secure coding|security|code quality)\b', full_lower)),
        "devops_deployment": bool(re.search(r'\b(ci/cd|docker|kubernetes|cloud deployment|infrastructure|pipeline|release management)\b', full_lower)),
        "agile_collaboration": bool(re.search(r'\b(agile|scrum|sprint|stakeholder|client interaction|documentation|cross-functional)\b', full_lower)),
    }

    domain = "Software Development / Backend"
    if any(k in full_lower for k in ["frontend", "ui", "react", "css", "html", "web design", "redux", "tailwind"]):
        domain = "Frontend Development"
    elif any(k in full_lower for k in ["machine learning", "ai", "data science", "nlp", "deep learning"]):
        domain = "AI / Machine Learning"
    elif any(k in full_lower for k in ["cloud", "devops", "aws", "kubernetes", "docker", "gcp", "azure"]):
        domain = "Cloud & DevOps"
    elif any(k in full_lower for k in ["full stack", "fullstack", "mern", "mean"]):
        domain = "Full Stack Development"

    return {
        "title": title,
        "company": company,
        "description": description,
        "full_text": full_text,
        "seniority_level": seniority_level,
        "seniority_title": seniority_title,
        "required_skills": sorted(list(required_skills)),
        "preferred_skills": sorted(list(preferred_skills)),
        "all_job_skills": sorted(list(all_job_skills)),
        "responsibilities_required": responsibilities_required,
        "experience_info": exp_info,
        "domain": domain,
        "jd_hash": jd_hash,
    }


# =====================================================================
# 7. PRODUCTION-GRADE 10-COMPONENT DETERMINISTIC JOB ATS SCORING ENGINE
# =====================================================================

def calculate_job_ats_score(resume_data, job_data, gemini_api_key=None):
    """
    Deterministic, Evidence-Based Job ATS Scoring Model adhering strictly to Section 18:

    1. Required Skills:              30%
    2. Experience & Seniority:       20%
    3. Relevant Projects:            10%
    4. Preferred Skills:              5%
    5. Responsibility Match:         10%
    6. Semantic Relevance:            5%
    7. Role Match:                    5%
    8. Education:                     5%
    9. Keyword / Terminology:         5%
    10. ATS Readability:              5%
    -------------------------------------
    TOTAL:                          100%

    Formula:
    final_score = round(
        required_skills * 0.30 +
        experience_seniority * 0.20 +
        project_relevance * 0.10 +
        preferred_skills * 0.05 +
        responsibility_match * 0.10 +
        semantic_relevance * 0.05 +
        role_match * 0.05 +
        education_match * 0.05 +
        keyword_coverage * 0.05 +
        ats_readability * 0.05
    )

    Hard-gate ceilings:
    - If Senior/Lead/Architect role and Candidate is Fresher -> Ceiling <= 35%
    """
    structured_resume = parse_main_profile_resume(resume_data)
    if not structured_resume or not structured_resume.get("resume_text", "").strip():
        if not structured_resume or not structured_resume.get("skills"):
            return {
                "final_score": 0,
                "match_percent": 0,
                "match_score": 0,
                "status": "RESUME_UNAVAILABLE",
                "message": "ATS Match unavailable",
                "matching_skills": [],
                "missing_required_skills": [],
                "deductions": ["No Main Profile Resume uploaded. Please upload your resume through Profile."],
            }

    jd_analysis = analyze_job_description(job_data)
    if not jd_analysis.get("full_text", "").strip():
        return {
            "final_score": 0,
            "match_percent": 0,
            "match_score": 0,
            "status": "JD_UNAVAILABLE",
            "message": "ATS Match unavailable",
            "matching_skills": [],
            "missing_required_skills": [],
            "deductions": ["Job description is unavailable."],
        }

    user_id = structured_resume.get("user_id") or "user"
    resume_hash = structured_resume.get("resume_hash", "")
    job_id = str(job_data.get("job_id") or job_data.get("id") or "")
    jd_hash = jd_analysis.get("jd_hash", "")
    cache_key = (user_id, resume_hash, job_id, jd_hash)

    if job_id and cache_key in ATS_SCORE_CACHE:
        return ATS_SCORE_CACHE[cache_key]

    user_skills_dict = structured_resume["normalized_skills"]
    user_skills_context = structured_resume.get("skills_context", {})
    req_skills = jd_analysis["required_skills"]
    pref_skills = jd_analysis["preferred_skills"]
    job_title = jd_analysis["title"].lower()
    full_jd_text = jd_analysis["full_text"].lower()
    resume_text_lower = structured_resume.get("resume_text", "").lower()

    deductions = []
    recommendations = []
    strong_matches = []
    partial_matches = []
    missing_must_haves = []
    missing_preferred = []
    experience_gaps = []
    role_seniority_gaps = []
    project_evidence = []
    education_evidence = []

    # -------------------------------------------------------------
    # 1. REQUIRED SKILLS (30%)
    # -------------------------------------------------------------
    matched_required = []
    req_weights_sum = 0.0

    if req_skills:
        for rk in req_skills:
            rk_norm = normalize_skill(rk)
            rk_low = rk_norm.lower()

            matched_user_key = None
            for uk_low in user_skills_dict.keys():
                if are_skills_equivalent(rk_low, uk_low):
                    matched_user_key = uk_low
                    break

            if matched_user_key:
                ctx = user_skills_context.get(matched_user_key, {})
                conf = ctx.get("confidence_weight", 0.60)
                req_weights_sum += conf
                matched_required.append(rk_norm)
                if conf >= 0.85:
                    strong_matches.append(f"{rk_norm} ({ctx.get('evidence_level', 'Demonstrated')})")
                else:
                    partial_matches.append(f"{rk_norm} ({ctx.get('evidence_level', 'Partial evidence')})")
            else:
                missing_must_haves.append(rk_norm)
                deductions.append(f"Missing required must-have skill: {rk_norm}. No evidence found in Skills, Experience, or Projects.")
                recommendations.append(f"Build projects or gain hands-on experience in {rk_norm}.")

        required_skills_score = int(round((req_weights_sum / len(req_skills)) * 100))
    else:
        overlap = [s for s in user_skills_dict.values() if s.lower() in full_jd_text]
        matched_required = overlap
        required_skills_score = min(90, max(40, len(overlap) * 15))

    required_skills_score = max(0, min(100, required_skills_score))

    # -------------------------------------------------------------
    # 2. EXPERIENCE & SENIORITY (20%)
    # -------------------------------------------------------------
    exp_info = jd_analysis["experience_info"]
    min_y = exp_info.get("min_years")
    cand_years = structured_resume.get("total_years_experience", 0.0)
    cand_seniority = structured_resume.get("seniority_level", 1)
    jd_seniority = jd_analysis.get("seniority_level", 2)
    has_intern = structured_resume.get("has_internships", False)
    is_fresher_job = exp_info.get("is_fresher_compatible", True)

    # Base Experience Score
    if min_y is None or min_y == 0 or is_fresher_job:
        if cand_years >= 1:
            base_exp_score = 95
        elif has_intern:
            base_exp_score = 95
        else:
            base_exp_score = 90
    elif min_y == 1:
        if cand_years >= 1:
            base_exp_score = 95
        elif has_intern:
            base_exp_score = 85
        else:
            base_exp_score = 75
    elif min_y == 2:
        if cand_years >= 2:
            base_exp_score = 95
        elif cand_years >= 1:
            base_exp_score = 80
        elif has_intern:
            base_exp_score = 70
        else:
            base_exp_score = 60
    elif min_y in [3, 4, 5]:
        if cand_years >= min_y:
            base_exp_score = 95
        elif cand_years >= 2:
            base_exp_score = 60
        elif cand_years >= 1:
            base_exp_score = 40
        else:
            base_exp_score = 20
            experience_gaps.append(f"Job requires {min_y} years experience; resume shows fresher level (0 professional years).")
    else:  # min_y >= 6 (Senior/Lead/Architect)
        if cand_years >= min_y:
            base_exp_score = 95
        elif cand_years >= 4:
            base_exp_score = 50
        elif cand_years >= 2:
            base_exp_score = 25
        else:
            base_exp_score = 5
            experience_gaps.append(f"Job requires {min_y}+ years experience; candidate has 0 years professional experience.")

    # Seniority Multiplier
    if jd_seniority == 5 and cand_seniority == 1:  # Tech Lead / Architect vs Fresher
        seniority_factor = 0.15
        role_seniority_gaps.append("Severe seniority gap: Role requires Tech Lead/Architect leadership; candidate is Fresher.")
    elif jd_seniority == 4 and cand_seniority == 1:  # Senior vs Fresher
        seniority_factor = 0.30
        role_seniority_gaps.append("Seniority gap: Role requires Senior Engineer; candidate is Entry-level/Fresher.")
    elif jd_seniority == 3 and cand_seniority == 1:  # Mid vs Fresher
        seniority_factor = 0.65
    else:
        seniority_factor = 1.0

    experience_score = int(round(base_exp_score * seniority_factor))
    experience_score = max(0, min(100, experience_score))

    # -------------------------------------------------------------
    # 3. RELEVANT PROJECTS (10%)
    # -------------------------------------------------------------
    projects = structured_resume.get("projects", [])
    if projects:
        best_proj_match = 0
        for p in projects:
            p_desc = p.get("description", "").lower()
            p_tech = [t.lower() for t in p.get("technologies", [])]
            overlap_count = sum(1 for rk in req_skills if rk.lower() in p_desc or rk.lower() in p_tech)
            
            is_same_domain = (jd_analysis["domain"].lower() in p_desc) or (
                "backend" in jd_analysis["domain"].lower() and any(w in p_desc for w in ["backend", "api", "database", "flask", "server", "microservices", "sql", "spring"])
            ) or (
                "frontend" in jd_analysis["domain"].lower() and any(w in p_desc for w in ["frontend", "ui", "react", "css", "html"])
            )
            domain_bonus = 25 if is_same_domain else 0

            # Relevance formula
            if req_skills:
                match_ratio = overlap_count / max(1, len(req_skills))
                proj_score = min(100, int(round(match_ratio * 65 + domain_bonus + 15)))
            else:
                proj_score = min(100, overlap_count * 25 + domain_bonus + 30)

            if proj_score > best_proj_match:
                best_proj_match = proj_score
                project_evidence = [f"Project '{p.get('title')}' demonstrates relevant technologies ({', '.join(p.get('technologies', [])[:3]) or 'software implementation'})."]

        projects_score = max(20, min(100, best_proj_match))
    else:
        projects_score = 15
        deductions.append("No technical projects found in resume to validate hands-on execution.")

    projects_score = max(0, min(100, projects_score))

    # -------------------------------------------------------------
    # 4. PREFERRED SKILLS (5%)
    # -------------------------------------------------------------
    matched_preferred = []
    if pref_skills:
        for pk in pref_skills:
            pk_norm = normalize_skill(pk)
            pk_low = pk_norm.lower()
            if any(are_skills_equivalent(pk_low, uk) for uk in user_skills_dict.keys()):
                matched_preferred.append(pk_norm)
            else:
                missing_preferred.append(pk_norm)
        preferred_skills_score = int(round((len(matched_preferred) / len(pref_skills)) * 100))
    else:
        tool_skills = {"git", "docker", "linux", "sql", "postman", "rest api"}
        matched_tools = [s for s in tool_skills if s in user_skills_dict]
        matched_preferred = [normalize_skill(s) for s in matched_tools]
        if required_skills_score >= 70:
            preferred_skills_score = min(100, max(80, 75 + len(matched_tools) * 5))
        else:
            preferred_skills_score = min(75, max(40, 40 + len(matched_tools) * 8))

    preferred_skills_score = max(0, min(100, preferred_skills_score))

    # -------------------------------------------------------------
    # 5. RESPONSIBILITY MATCH (10%)
    # -------------------------------------------------------------
    jd_resp = jd_analysis.get("responsibilities_required", {})
    resp_points = 0
    resp_total = 0

    # Check candidate evidence for each required responsibility category
    for cat, is_req in jd_resp.items():
        if not is_req:
            continue
        resp_total += 1
        has_evidence = False

        if cat == "architecture_design":
            has_evidence = bool(re.search(r'\b(architecture|system design|microservices|distributed|high-level design)\b', resume_text_lower)) and (cand_years >= 2 or len(projects) >= 1)
        elif cat == "core_development":
            has_evidence = bool(re.search(r'\b(developed|built|created|engineered|apis?|backend|frontend|software)\b', resume_text_lower))
        elif cat == "mentoring_leadership":
            has_evidence = bool(re.search(r'\b(lead|mentor|code review|mentored|guided|led)\b', resume_text_lower)) and cand_seniority >= 3
        elif cat == "testing_quality":
            has_evidence = bool(re.search(r'\b(unit test|junit|pytest|testing|secure coding|tdd)\b', resume_text_lower))
        elif cat == "devops_deployment":
            has_evidence = bool(re.search(r'\b(docker|ci/cd|kubernetes|deployed|cloud|aws|gcp|eks)\b', resume_text_lower))
        elif cat == "agile_collaboration":
            has_evidence = bool(re.search(r'\b(agile|scrum|team|collaborated|sprint)\b', resume_text_lower))

        if has_evidence:
            resp_points += 1

    if resp_total > 0:
        responsibility_score = int(round((resp_points / resp_total) * 100))
    else:
        responsibility_score = 85 if required_skills_score >= 60 else 45

    responsibility_score = max(0, min(100, responsibility_score))

    # -------------------------------------------------------------
    # 6. SEMANTIC RELEVANCE (5%)
    # -------------------------------------------------------------
    jd_words = {w for w in re.findall(r'[a-z]{3,}', full_jd_text) if w not in STOP_WORDS}
    res_words = {w for w in re.findall(r'[a-z]{3,}', resume_text_lower) if w not in STOP_WORDS}

    if jd_words:
        overlap_count = len(jd_words.intersection(res_words))
        overlap_ratio = overlap_count / min(len(jd_words), 25)
        semantic_score = int(round(overlap_ratio * 50 + 40))
    else:
        semantic_score = 60

    # Guardrails: Java != JavaScript, Frontend != Backend
    if "javascript" in job_title and "java" in user_skills_dict and "javascript" not in user_skills_dict:
        semantic_score = min(25, semantic_score)
    elif "java " in job_title and "javascript" in user_skills_dict and "java" not in user_skills_dict:
        semantic_score = min(25, semantic_score)

    semantic_score = max(0, min(100, semantic_score))

    # -------------------------------------------------------------
    # 7. ROLE MATCH (5%)
    # -------------------------------------------------------------
    user_roles = [r.lower() for r in structured_resume.get("roles", [])]
    role_score = 40
    for r in user_roles:
        if r in job_title:
            role_score = max(role_score, 95)
        elif any(part in job_title for part in r.split() if len(part) > 3):
            role_score = max(role_score, 80)

    if any(s.lower() in job_title for s in user_skills_dict.keys()):
        role_score = max(role_score, 85)

    # Seniority penalty in role matching
    if jd_seniority >= 4 and cand_seniority == 1:
        role_score = min(role_score, 20)
    elif jd_seniority == 3 and cand_seniority == 1:
        role_score = min(role_score, 50)

    disparate = ["embedded", "ios", "android", "flutter", "unity", "salesforce", "sap", "hardware", "qa automation"]
    for kw in disparate:
        if kw in job_title and kw not in user_skills_dict:
            role_score = max(15, role_score - 35)

    role_score = max(0, min(100, role_score))

    # -------------------------------------------------------------
    # 8. EDUCATION MATCH (5%)
    # -------------------------------------------------------------
    edu_field = structured_resume.get("education_field", "Computer Science")
    if "computer science" in full_jd_text or "engineering" in full_jd_text or "b.tech" in full_jd_text or "b.e" in full_jd_text or "mca" in full_jd_text or "graduate" in full_jd_text or "information technology" in full_jd_text:
        education_score = 95
        education_evidence.append(f"Candidate degree in {edu_field} aligns with job requirements.")
    else:
        education_score = 85

    education_score = max(0, min(100, education_score))

    # -------------------------------------------------------------
    # 9. KEYWORD COVERAGE (5%)
    # -------------------------------------------------------------
    all_jd_terms = [s.lower() for s in jd_analysis["all_job_skills"]]
    if all_jd_terms:
        matched_terms = [t for t in all_jd_terms if t in user_skills_dict or t in resume_text_lower]
        keyword_score = int(round((len(matched_terms) / len(all_jd_terms)) * 100))
    else:
        keyword_score = 70

    keyword_score = max(0, min(100, keyword_score))

    # -------------------------------------------------------------
    # 10. ATS READABILITY (5%)
    # -------------------------------------------------------------
    ats_readability_score = structured_resume.get("ats_readability_score", 90)
    ats_readability_score = max(0, min(100, ats_readability_score))

    # -------------------------------------------------------------
    # MASTER WEIGHTED FORMULA (100% Deterministic)
    # -------------------------------------------------------------
    raw_final = (
        (required_skills_score * 0.30) +
        (experience_score * 0.20) +
        (projects_score * 0.10) +
        (preferred_skills_score * 0.05) +
        (responsibility_score * 0.10) +
        (semantic_score * 0.05) +
        (role_score * 0.05) +
        (education_score * 0.05) +
        (keyword_score * 0.05) +
        (ats_readability_score * 0.05)
    )
    final_score = int(round(raw_final))

    # -------------------------------------------------------------
    # HARD-GATE CEILINGS (Section 18)
    # -------------------------------------------------------------
    # Strict Seniority Mismatch Ceiling:
    if (jd_seniority >= 4 or (min_y is not None and min_y >= 5)) and cand_seniority == 1:
        final_score = min(final_score, 35)

    final_score = max(0, min(100, final_score))
    all_matching_skills = list(dict.fromkeys(matched_required + matched_preferred))

    result_payload = {
        "final_score": final_score,
        "match_percent": final_score,
        "match_score": final_score,
        "required_skills_score": required_skills_score,
        "experience_score": experience_score,
        "projects_score": projects_score,
        "preferred_skills_score": preferred_skills_score,
        "responsibility_score": responsibility_score,
        "semantic_score": semantic_score,
        "role_score": role_score,
        "education_score": education_score,
        "keyword_score": keyword_score,
        "ats_readability_score": ats_readability_score,
        "matched_required_skills": matched_required,
        "missing_required_skills": missing_must_haves,
        "matched_preferred_skills": matched_preferred,
        "missing_preferred_skills": missing_preferred,
        "matching_skills": all_matching_skills,
        "strong_matches": strong_matches,
        "partial_matches": partial_matches,
        "missing_must_haves": missing_must_haves,
        "experience_gaps": experience_gaps,
        "role_seniority_gaps": role_seniority_gaps,
        "project_evidence": project_evidence,
        "education_evidence": education_evidence,
        "deductions": deductions,
        "recommendations": recommendations,
    }

    if job_id:
        ATS_SCORE_CACHE[cache_key] = result_payload

    return result_payload


def calculate_local_match_score(resume_data, job_data):
    res = calculate_job_ats_score(resume_data, job_data)
    return res["final_score"], res["matching_skills"]


def calculate_job_match_score(resume_data, job_data, gemini_api_key=None):
    res = calculate_job_ats_score(resume_data, job_data, gemini_api_key)
    return res["final_score"], res["matching_skills"]


# =====================================================================
# 8. COMPLETE 10-STEP PIPELINE: NORMALIZATION, FILTERING & SCORING
# =====================================================================

def normalize_and_filter_jobs(raw_results, resume_data, gemini_api_key=None, max_workers=5):
    """
    Executes the 10-step job processing pipeline:
    STEP 1: Ingest raw jobs from SerpAPI / current job source.
    STEP 2: Normalize basic job fields.
    STEP 3: Extract actual experience requirement.
    STEP 4: Filter out experienced-only jobs (keep fresher-compatible).
    STEP 5: Extract actual job description and required skills.
    STEP 6: Parse candidate Main Profile Resume ONCE.
    STEP 7: Calculate INDIVIDUAL deterministic Job ATS score for every job.
    STEP 8: Extract actual salary (or 'Not mentioned').
    STEP 9: Extract actual posted date/time.
    STEP 10: Sort by match score and return normalized jobs.
    """
    from auth_db import generate_job_id

    if not raw_results:
        return []

    seen_keys = set()
    fresher_candidates = []

    for raw_job in raw_results:
        title = (raw_job.get("title") or "Software Engineer").strip()
        company = (raw_job.get("company_name") or raw_job.get("company") or "Tech Company").strip()
        loc = (raw_job.get("location") or "India").strip()

        dedup_key = f"{title.lower()}_{company.lower()}"
        if dedup_key in seen_keys:
            continue
        seen_keys.add(dedup_key)

        description = (raw_job.get("description") or raw_job.get("job_description") or "").strip()

        exp_info = extract_experience_info(raw_job)

        if not exp_info["is_fresher_compatible"]:
            continue

        apply_link = None
        apply_options = raw_job.get("apply_options", [])
        if apply_options and isinstance(apply_options, list) and len(apply_options) > 0:
            apply_link = apply_options[0].get("link")
        if not apply_link:
            apply_link = raw_job.get("share_link") or raw_job.get("link") or raw_job.get("job_link")

        company_lower = company.lower()
        company_brand = "generic"
        for brand in [
            "microsoft", "google", "swiggy", "zomato", "amazon",
            "flipkart", "meta", "apple", "netflix", "uber",
            "adobe", "tcs", "infosys", "wipro", "accenture",
        ]:
            if brand in company_lower:
                company_brand = brand
                break

        salary = extract_salary_info(raw_job)
        posted_at = extract_posted_time(raw_job)
        openings = extract_openings_info(raw_job)

        detected_ext = raw_job.get("detected_extensions") or {}
        job_type = detected_ext.get("schedule_type", "Full-time")
        if not job_type or "yr" in job_type.lower():
            job_type = "Full-time"

        job_id = generate_job_id(company, title, loc, apply_link or "")

        fresher_candidates.append({
            "job_id": job_id,
            "title": title,
            "company_name": company,
            "company_brand": company_brand,
            "is_verified": True,
            "location": loc,
            "salary": salary,
            "experience": exp_info["display_text"],
            "experience_info": exp_info,
            "openings": openings,
            "job_type": job_type,
            "posted_at": posted_at,
            "apply_link": apply_link,
            "apply_options": apply_options,
            "thumbnail": raw_job.get("thumbnail"),
            "description": description,
            "searched_role": raw_job.get("searched_role", "Software Engineer"),
            "source": raw_job.get("via") or "Google Jobs / SerpAPI",
        })

    if not fresher_candidates:
        return []

    structured_resume = parse_main_profile_resume(resume_data)

    def score_single_job(job_obj):
        ats_result = calculate_job_ats_score(structured_resume, job_obj, gemini_api_key)
        job_obj["match_percent"] = ats_result["final_score"]
        job_obj["match_score"] = ats_result["final_score"]
        job_obj["matching_skills"] = ats_result.get("matching_skills", [])
        job_obj["ats_score_data"] = ats_result
        return job_obj

    processed_jobs = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {executor.submit(score_single_job, j): j for j in fresher_candidates}
        for future in concurrent.futures.as_completed(future_map):
            try:
                processed_jobs.append(future.result())
            except Exception as e:
                orig_job = future_map[future]
                fallback_res = calculate_job_ats_score(structured_resume, orig_job)
                orig_job["match_percent"] = fallback_res["final_score"]
                orig_job["match_score"] = fallback_res["final_score"]
                orig_job["matching_skills"] = fallback_res.get("matching_skills", [])
                orig_job["ats_score_data"] = fallback_res
                processed_jobs.append(orig_job)

    processed_jobs.sort(key=lambda j: j.get("match_percent", 0), reverse=True)

    def _safe_print(msg):
        try:
            print(msg)
        except Exception:
            try:
                cleaned = str(msg).encode('ascii', errors='replace').decode('ascii')
                print(cleaned)
            except Exception:
                pass

    _safe_print("\n" + "=" * 60)
    _safe_print("SKILLBRIDGE.AI - TRACEABLE PROCESSED JOB RESULTS")
    _safe_print("=" * 60)
    for j in processed_jobs:
        _safe_print(f"JOB: {j.get('title')} | {j.get('company_name')}")
        _safe_print(f"MATCH SCORE: {j.get('match_percent')}%")
        _safe_print(f"MATCHING SKILLS: {', '.join(j.get('matching_skills', [])) or 'General tech overlap'}")
        _safe_print(f"JOB EXPERIENCE: {j.get('experience') or 'EMPTY'}")
        _safe_print(f"JOB SALARY: {j.get('salary')}")
        _safe_print(f"JOB OPENINGS: {j.get('openings') or 'NA'}")
        _safe_print(f"POSTED: {j.get('posted_at') or 'Not specified'}")
        _safe_print("-" * 60)
    _safe_print(f"Total Fresher-Compatible Jobs Rendered: {len(processed_jobs)}\n")

    return processed_jobs
