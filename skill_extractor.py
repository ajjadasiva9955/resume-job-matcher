import re
from PyPDF2 import PdfReader

# --- 1. EXPANDED SKILL DATASET ---
SKILL_SET = [
    # Programming Languages
    "python", "java", "c++", "c", "c#", ".net", "javascript", "typescript", "html", "css", "sql", "bash", "shell scripting",
    "r", "swift", "kotlin", "php", "go", "ruby", "rust", "dart", "scala", "perl", "matlab", "assembly", "vba",
    "objective-c", "solidity", "verilog", "vhdl", "embedded c", "powershell",
    
    # Frameworks, Libraries & Platforms
    "flask", "django", "fastapi", "react", "angular", "vue", "next.js", "node.js", "express", "spring boot", "laravel", 
    "tensorflow", "pytorch", "scikit-learn", "pandas", "numpy", "keras", "opencv", "nltk", "spacy", "hugging face", "langchain",
    "flutter", "react native", "ionic", "xamarin", "unity", "unreal engine", "opengl", "vulkan", "qt",
    "bootstrap", "tailwind", "jquery", "ajax", "wordpress", "magento", "shopify",
    
    # Cloud, DevOps & Infrastructure
    "docker", "kubernetes", "aws", "azure", "gcp", "terraform", "ansible", "jenkins", "circleci", "gitlab ci", "travis ci",
    "prometheus", "grafana", "elk stack", "splunk", "nagios", "linux", "unix", "serverless", "cloudflare", "nginx",
    
    # Databases & Big Data
    "mongodb", "postgresql", "mysql", "redis", "oracle", "sql server", "sqlite", "cassandra", "dynamodb", "firebase",
    "hadoop", "spark", "kafka", "hive", "airflow", "databricks", "snowflake", "elasticsearch", "bigquery",
    
    # Tools, CI/CD & Version Control
    "git", "github", "gitlab", "bitbucket", "jira", "confluence", "trello", "slack", "notion",
    "tableau", "power bi", "looker", "excel", "spss", "sas", "google analytics",
    "figma", "adobe xd", "sketch", "photoshop", "illustrator", "invision", "zeplin",
    
    # Security & Networking
    "wireshark", "nmap", "metasploit", "burp suite", "nessus", "kali linux", "snort", "tcp/ip", "dns", "http", "https",
    "ssl/tls", "vpn", "firewalls", "cryptography", "owasp", "siem", "soc", "cissp", "ceh", "iam", "penetration testing",
    
    # Specialized Tech (Blockchain, IoT, AI)
    "blockchain", "smart contracts", "web3", "ethereum", "hyperledger", "ipfs", "solana", "nft",
    "robotics", "ros", "arduino", "raspberry pi", "iot", "mqtt", "rtos", "plc", "scada", "sensors",
    "generative ai", "llm", "gpt", "bert", "stable diffusion", "prompt engineering", "nlp", "computer vision",
    "selenium", "junit", "pytest", "cypress", "appium", "postman", "soapui", "jmeter", "loadrunner"
]

# --- 2. ROLE DEFINITIONS (For Missing Skills Suggestion) ---
# This maps the exact roles you provided to a set of "Must-Have" skills.
ROLE_REQUIREMENTS = {
    # 1. Core Software Engineering
    "Java Developer":{"java","oops","data structures","java script","spring boot","j2ee"},
    "Python Developer":{"python","oops","data structures","django","Flask"},
    "Software Engineer": {"java", "python", "c++", "git", "sql", "data structures", "algorithms", "system design"},
    "Backend Developer": {"python", "java", "node.js", "sql", "mongodb", "docker", "aws", "redis", "rest api"},
    "Frontend Developer": {"html", "css", "javascript", "react", "angular", "git", "typescript", "tailwind"},
    "Full Stack Developer": {"html", "css", "javascript", "react", "node.js", "sql", "mongodb", "git", "aws"},
    "Mobile App Developer": {"flutter", "react native", "swift", "kotlin", "java", "firebase", "ios", "android"},
    "Game Developer": {"c++", "c#", "unity", "unreal engine", "3d math", "opengl", "physics"},
    
    # 2. AI / ML / Deep Learning
    "AI Engineer": {"python", "tensorflow", "pytorch", "scikit-learn", "deep learning", "nlp", "opencv", "cloud"},
    "Machine Learning Engineer": {"python", "machine learning", "tensorflow", "pytorch", "scikit-learn", "mathematics", "aws"},
    "Generative AI Engineer": {"python", "transformers", "pytorch", "langchain", "llm", "hugging face", "vector databases"},
    "NLP Engineer": {"python", "nlp", "nltk", "spacy", "hugging face", "transformers", "pytorch"},
    
    # 3. Data Science & Analytics
    "Data Scientist": {"python", "r", "sql", "pandas", "numpy", "scikit-learn", "statistics", "tableau", "machine learning"},
    "Data Analyst": {"python", "sql", "excel", "tableau", "power bi", "statistics", "pandas","matplot lib"},
    "Data Engineer": {"python", "sql", "hadoop", "spark", "kafka", "airflow", "aws", "snowflake", "etl"},
    
    # 4. Cyber Security & Privacy
    "Cyber Security Engineer": {"linux", "networking", "firewalls", "python", "bash", "siem", "vulnerability assessment"},
    "Penetration Tester": {"kali linux", "metasploit", "burp suite", "nmap", "python", "bash", "owasp"},
    "Security Analyst": {"siem", "splunk", "wireshark", "incident response", "networking", "risk management"},
    
    # 5. Cloud, DevOps & Infrastructure
    "DevOps Engineer": {"python", "bash", "docker", "kubernetes", "aws", "jenkins", "linux", "git", "terraform", "ansible"},
    "Cloud Architect": {"aws", "azure", "gcp", "docker", "kubernetes", "terraform", "networking", "security"},
    "SRE (Site Reliability Engineer)": {"linux", "python", "go", "kubernetes", "terraform", "prometheus", "grafana", "aws"},
    
    # 6. Testing & QA
    "QA Engineer": {"selenium", "java", "python", "junit", "pytest", "jira", "sql", "postman"},
    "Automation Test Engineer": {"selenium", "cypress", "java", "python", "appium", "jenkins", "git"},
    
    # 7. ECE / Embedded / IoT
    "Embedded Systems Engineer": {"embedded c", "c++", "microcontrollers", "rtos", "communication protocols", "debugging"},
    "IoT Engineer": {"python", "c++", "arduino", "raspberry pi", "mqtt", "aws iot", "sensors", "networking"},
    
    # 8. Web3 / Blockchain
    "Blockchain Developer": {"solidity", "blockchain", "smart contracts", "web3", "ethereum", "cryptography", "javascript"},
    
    # 9. UI/UX
    "UI/UX Designer": {"figma", "adobe xd", "sketch", "photoshop", "illustrator", "html", "css", "wireframing"}
}

def extract_skills(resume_path):
    reader = PdfReader(resume_path)
    text = " ".join(page.extract_text().lower() for page in reader.pages)

    found_skills = set()
    for skill in SKILL_SET:
        # Use regex boundary \b to avoid matching "java" in "javascript"
        # Escaping skill names to handle C++, C#, .NET etc correctly
        if re.search(rf"\b{re.escape(skill)}\b", text):
            found_skills.add(skill)
    
    return list(found_skills)

def map_skills_to_roles(skills):
    roles = set()
    skills_set = set(skills)

    # --- 1. CORE SOFTWARE ---
    if "java" in skills_set and ("spring boot" in skills_set or "j2ee" in skills_set):
        roles.add("Java Developer")
    if "python" in skills_set and ("django" in skills_set or "flask" in skills_set):
        roles.add("Python Developer")
    if "html" in skills_set and ("css" in skills_set or "javascript" in skills_set):
        roles.add("Frontend Developer")
    if ("python" or"java") in skills_set and ("node.js" and "rest api" and "sql") in skills_set:
        roles.add("Backend Developer") 
    if ("node.js" in skills_set or "django" in skills_set) and "react" in skills_set:
        roles.add("Full Stack Developer")
    if "c#" in skills_set and ".net" in skills_set:
        roles.add(".NET Developer")

    # --- 2. MOBILE ---
    if "flutter" in skills_set or "dart" in skills_set:
        roles.add("Mobile App Developer")
    if "react native" in skills_set:
        roles.add("Mobile App Developer")
    if "swift" in skills_set or "ios" in skills_set:
        roles.add("iOS Developer")
    if "kotlin" in skills_set or "android" in skills_set:
        roles.add("Android Developer")

    # --- 3. DATA & AI ---
    if "python" in skills_set and ("pandas" in skills_set or "numpy" in skills_set) and "sql" in skills_set:
        roles.add("Data Analyst")
    if "python" in skills_set and ("scikit-learn" in skills_set or "tensorflow" in skills_set):
        roles.add("Machine Learning Engineer")
    if "langchain" in skills_set or "llm" in skills_set or "transformers" in skills_set:
        roles.add("Generative AI Engineer")
    if "python" in skills_set and ("hadoop" in skills_set or "spark" in skills_set):
        roles.add("Data Engineer")
    if "nlp" in skills_set or "nltk" in skills_set or "spacy" in skills_set:
        roles.add("NLP Engineer")

    # --- 4. CLOUD & DEVOPS ---
    if "docker" in skills_set and "kubernetes" in skills_set:
        roles.add("DevOps Engineer")
    if "aws" in skills_set or "azure" in skills_set or "gcp" in skills_set:
        roles.add("Cloud Engineer")
    if "terraform" in skills_set or "ansible" in skills_set:
        roles.add("SRE (Site Reliability Engineer)")

    # --- 5. SECURITY ---
    if "kali linux" in skills_set or "metasploit" in skills_set or "burp suite" in skills_set:
        roles.add("Penetration Tester")
    if "wireshark" in skills_set and "networking" in skills_set:
        roles.add("Cyber Security Engineer")
    
    # --- 6. TESTING ---
    if "selenium" in skills_set or "junit" in skills_set or "pytest" in skills_set:
        roles.add("QA Engineer")
    if "cypress" in skills_set or "appium" in skills_set:
        roles.add("Automation Test Engineer")

    # --- 7. EMBEDDED / IOT / ECE ---
    if "embedded c" in skills_set or "rtos" in skills_set or "microcontrollers" in skills_set:
        roles.add("Embedded Systems Engineer")
    if "arduino" in skills_set or "raspberry pi" in skills_set or "iot" in skills_set:
        roles.add("IoT Engineer")

    # --- 8. GAME DEV ---
    if "unity" in skills_set or "unreal engine" in skills_set:
        roles.add("Game Developer")

    # --- 9. BLOCKCHAIN ---
    if "solidity" in skills_set or "blockchain" in skills_set or "smart contracts" in skills_set:
        roles.add("Blockchain Developer")

    # --- 10. WEB / DESIGN ---
    if "figma" in skills_set or "adobe xd" in skills_set:
        roles.add("UI/UX Designer")
    
    # Default fallback
    if not roles and "python" in skills_set:
        roles.add("Python Developer")
    if not roles and "java" in skills_set:
        roles.add("Java Developer")
    if not roles and "javascript" in skills_set:
        roles.add("Web Developer")

    return list(roles)

def get_missing_skills(user_skills, matched_roles):
    """
    Returns a dictionary of missing skills for EACH matched role.
    Format: { 'Role Name': ['skill1', 'skill2'], ... }
    """
    user_skills_set = set(user_skills)
    missing_skills_map = {}

    for role in matched_roles:
        if role in ROLE_REQUIREMENTS:
            required_skills = ROLE_REQUIREMENTS[role]
            missing = list(required_skills - user_skills_set)
            
            # If there are missing skills, add them to the map
            if missing:
                missing.sort() # Sort for cleaner display
                missing_skills_map[role] = missing
    
    return missing_skills_map