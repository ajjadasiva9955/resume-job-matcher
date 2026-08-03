from flask import Flask, render_template, request, session, redirect, url_for, jsonify
import os
import requests
import concurrent.futures
import google.generativeai as genai
from uuid import uuid4
from PyPDF2 import PdfReader
from skill_extractor import extract_skills, map_skills_to_roles, get_missing_skills

app = Flask(__name__)
# SECURITY: Change this to a random string for production!
app.secret_key = "resume_matcher_secret_key"

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
SNAKE_GAMES = {}

# --- HELPER: Get PDF Text ---
def get_pdf_text(pdf_path):
    try:
        reader = PdfReader(pdf_path)
        text = " ".join(page.extract_text() for page in reader.pages)
        return text
    except:
        return ""

# --- HELPER: Smart Model Selector ---
def get_gemini_model():
    """
    Tries to get the Flash model, falls back to Pro if unavailable.
    """
    try:
        # Try the latest fast model
        return genai.GenerativeModel('gemini-2.5-flash')
    except:
        # Fallback to the stable 1.5 Pro model (Updated from 'gemini-pro')
        print("Switching to Gemini 2.5 Pro model...")
        return genai.GenerativeModel('gemini-2.5-pro')

# --- AGENT 1: COVER LETTER WRITER ---
def agent_write_cover_letter(resume_text, job_title, company, location, api_key):
    try:
        print(f"Generating Cover Letter for {company}...") # Debug Log
        genai.configure(api_key=api_key)
        
        # Use fallback logic inside the generation call
        try:
            model = genai.GenerativeModel('gemini-2.5-flash')
        except:
            model = genai.GenerativeModel('gemini-2.5-pro')
        
        prompt = f"""
        Act as a Professional Career Coach. Write a Cover Letter.
        
        JOB DETAILS:
        Role: {job_title}
        Company: {company}
        Location: {location}
        
        MY RESUME:
        {resume_text[:3000]}... (truncated)
        
        INSTRUCTIONS:
        1. Keep it under 200 words.
        2. Specifically mention 2 skills from my resume that fit this job.
        3. Professional and confident tone.
        4. Output ONLY the body of the letter (no placeholders like [Your Name]).
        """
        
        # Explicitly handling the generation call error
        try:
            response = model.generate_content(prompt)
            return response.text
        except Exception as inner_e:
            # If Flash fails during generation, try 2.5 Pro
            print(f"Flash failed ({inner_e}), retrying with Gemini 2.5 Pro...")
            model_backup = genai.GenerativeModel('gemini-2.5-pro')
            response = model_backup.generate_content(prompt)
            return response.text

    except Exception as e:
        print(f"Cover Letter Error: {e}")
        return f"Error: {str(e)}. Please check your API Key."

# --- AGENT 2: GAP ANALYSIS COACH (Uses Tools) ---
def search_tutorial(skill, serp_key):
    """Tool: Searches for the best free tutorial for a specific skill."""
    if not skill: return None
    
    print(f"Searching for tutorial: {skill}...") # Debug Log
    url = "https://serpapi.com/search.json"
    params = {
        "engine": "google",
        "q": f"best free {skill} tutorial youtube 2025", 
        "api_key": serp_key,
        "num": 1
    }
    try:
        response = requests.get(url, params=params, timeout=5)
        data = response.json()
        if "organic_results" in data and len(data["organic_results"]) > 0:
            result = data["organic_results"][0]
            return {"title": result.get("title"), "link": result.get("link")}
    except Exception as e:
        print(f"Search failed for {skill}: {e}")
        pass
    return None

def agent_gap_analysis(role, missing_skills, serp_key, gemini_key):
    try:
        print(f"Starting Gap Analysis for {role}...")
        genai.configure(api_key=gemini_key)
        
        # 1. Limit to top 3 skills to save time
        top_skills = missing_skills[:3]
        
        # 2. Tool Execution (Parallel Search)
        resources = {}
        # Only run search if we have a SerpApi key, otherwise skip tool
        if serp_key:
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future_to_skill = {executor.submit(search_tutorial, skill, serp_key): skill for skill in top_skills}
                for future in concurrent.futures.as_completed(future_to_skill):
                    skill = future_to_skill[future]
                    try:
                        resources[skill] = future.result()
                    except:
                        resources[skill] = None
        
        # 3. Create Context for AI
        resource_text = ""
        for skill in top_skills:
            res = resources.get(skill)
            if res:
                resource_text += f"- For {skill}: Found tutorial '{res['title']}' at {res['link']}\n"
            else:
                resource_text += f"- For {skill}: Please recommend a popular free resource (like Coursera, YouTube, or Documentation).\n"

        # 4. Generate Plan
        try:
            model = genai.GenerativeModel('gemini-2.5-flash')
        except:
            model = genai.GenerativeModel('gemini-2.5-pro')

        prompt = f"""
        You are a Technical Mentor, your name is "RJM-agent" introduce your self in single line in professional way. Create a "Gap Analysis Learning Plan" for a user wanting to be a {role}.
        
        MISSING SKILLS: {', '.join(top_skills)}
        
        FOUND RESOURCES (Prioritize these links):
        {resource_text}
        
        INSTRUCTIONS:
        1. Create a concise 2-week plan.
        2. For each skill, explain WHY it is critical.
        3. Provide the specific link if I gave one, otherwise suggest a general one.
        4. Use HTML formatting: <b>Bold</b> for emphasis, <br> for line breaks.
        5. Do NOT use Markdown (like ** or ##), use HTML tags only.
        """
        
        try:
            response = model.generate_content(prompt)
            return response.text
        except Exception as inner_e:
            print(f"Flash failed ({inner_e}), retrying with Gemini 2.5 Pro...")
            model_backup = genai.GenerativeModel('gemini-2.5-pro')
            response = model_backup.generate_content(prompt)
            return response.text

    except Exception as e:
        print(f"Gap Analysis Error: {e}")
        return f"Error creating plan: {str(e)}"

# --- EXISTING FUNCTIONS (Market Data etc.) ---
def fetch_market_count(role, api_key):
    url = "https://serpapi.com/search.json"
    try:
        params = {"engine": "google", "q": f"{role} jobs in India", "location": "India", "api_key": api_key}
        response = requests.get(url, params=params, timeout=8)
        data = response.json()
        search_info = data.get("search_information", {})
        total_results = search_info.get("total_results_formatted")
        if not total_results:
             if search_info.get("total_results"): total_results = f"{int(search_info.get('total_results')):,}+"
        return {"role": role, "count": total_results if total_results else "High Demand"}
    except:
        return {"role": role, "count": "Data Unavailable"}

def get_market_insights(api_key):
    trending_roles = ["Full Stack Developer", "Data Scientist", "Python Developer", "DevOps Engineer", "Cyber Security Engineer", "Java Developer", "Cloud Architect", "Frontend Developer", "Machine Learning Engineer", "UI/UX Designer"]
    insights = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        future_to_role = {executor.submit(fetch_market_count, role, api_key): role for role in trending_roles}
        for future in concurrent.futures.as_completed(future_to_role):
            insights.append(future.result())
    insights.sort(key=lambda x: trending_roles.index(x["role"]) if x["role"] in trending_roles else 999)
    return insights

def fetch_jobs(job_roles, api_key):
    url = "https://serpapi.com/search.json"
    all_jobs = []
    # Fetch top 2 roles
    for role in job_roles[:2]:
        try:
            params = {"engine": "google_jobs", "q": role, "location": "India", "hl": "en", "api_key": api_key}
            response = requests.get(url, params=params); response.raise_for_status()
            data = response.json()
            jobs = data.get("jobs_results", [])
            for job in jobs: job["searched_role"] = role
            all_jobs.extend(jobs)
        except: continue
    return all_jobs

# --- ROUTES ---

def get_snake_id():
    if "snake_game_id" not in session:
        session["snake_game_id"] = str(uuid4())
    return session["snake_game_id"]

@app.route("/")
def home():
    if "serpapi_key" not in session: return redirect(url_for("setup"))
    return render_template("index.html")

@app.route("/snake")
def snake():
    return render_template("snake.html")

@app.route("/setup", methods=["GET", "POST"])
def setup():
    if request.method == "POST":
        api_key = request.form.get("api_key")
        gemini_key = request.form.get("gemini_key") 
        
        if api_key: session["serpapi_key"] = api_key.strip()
        if gemini_key: session["gemini_api_key"] = gemini_key.strip()
            
        return redirect(url_for("home"))
    return render_template("setup.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("setup"))

@app.route("/api/snake/new", methods=["POST"])
def snake_new():
    game_id = get_snake_id()
    SNAKE_GAMES[game_id] = new_game()
    return jsonify(SNAKE_GAMES[game_id])

@app.route("/api/snake/turn", methods=["POST"])
def snake_turn():
    game_id = get_snake_id()
    state = SNAKE_GAMES.get(game_id) or new_game()
    state = change_direction(state, (request.get_json(silent=True) or {}).get("direction"))
    SNAKE_GAMES[game_id] = state
    return jsonify(state)

@app.route("/api/snake/step", methods=["POST"])
def snake_step():
    game_id = get_snake_id()
    state = SNAKE_GAMES.get(game_id) or new_game()
    state = step(state)
    SNAKE_GAMES[game_id] = state
    return jsonify(state)

@app.route("/upload", methods=["POST"])
def upload_resume():
    if "serpapi_key" not in session: return redirect(url_for("setup"))
    if "resume" not in request.files: return redirect(url_for("home"))
    
    resume = request.files["resume"]
    if resume.filename == "": return redirect(url_for("home"))

    resume_path = os.path.join(app.config["UPLOAD_FOLDER"], resume.filename)
    resume.save(resume_path)
    session['current_resume_path'] = resume_path 

    skills = extract_skills(resume_path)
    job_roles = map_skills_to_roles(skills)
    missing_skills = get_missing_skills(skills, job_roles)
    
    # Store missing skills in session
    session['missing_skills_data'] = missing_skills
    
    jobs = fetch_jobs(job_roles, session["serpapi_key"])
    market_insights = get_market_insights(session["serpapi_key"])

    return render_template("results.html", skills=skills, roles=job_roles, 
                           missing_skills = missing_skills, market_insights=market_insights, jobs=jobs)

# --- API ENDPOINTS FOR AGENTS ---

@app.route("/generate-cover-letter", methods=["POST"])
def generate_cover_letter_route():
    if 'gemini_api_key' not in session: return jsonify({"content": "Error: Gemini API Key missing. Please go to Setup."})
    
    data = request.json
    resume_text = get_pdf_text(session.get('current_resume_path'))
    
    letter = agent_write_cover_letter(resume_text, data['job_title'], data['company'], data['location'], session['gemini_api_key'])
    return jsonify({"content": letter})

@app.route("/generate-learning-plan", methods=["POST"])
def generate_learning_plan_route():
    if 'gemini_api_key' not in session: return jsonify({"content": "Error: Gemini API Key missing. Please go to Setup."})
    
    data = request.json
    role = data.get("role")
    
    # Debug print to check if data is in session
    print(f"Generating plan for {role}. Session missing skills: {session.get('missing_skills_data')}")

    all_missing = session.get('missing_skills_data', {})
    skills_for_role = all_missing.get(role, [])
    
    if not skills_for_role:
        return jsonify({"content": "No missing skills found for this role! You are good to go."})

    plan = agent_gap_analysis(role, skills_for_role, session.get('serpapi_key'), session['gemini_api_key'])
    return jsonify({"content": plan})

if __name__ == "__main__":
    app.run(debug=True)
