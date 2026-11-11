from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from typing import Any

HELPER_VERSION = "v3_direct_ollama_json"
def positive_bounded_int(name: str, default: int, maximum: int = 3600) -> int:
    try:
        value = int(str(os.getenv(name, "")).strip())
    except (TypeError, ValueError):
        return default
    return value if 0 < value <= maximum else default


def ollama_base_url() -> str:
    return (os.getenv("OLLAMA_BASE_URL") or "http://127.0.0.1:11434").strip().rstrip("/")


OLLAMA_BASE_URL = ollama_base_url()
OLLAMA_URL = f"{OLLAMA_BASE_URL}/api/chat"
OLLAMA_MODEL = os.getenv("HR_AGENT_OLLAMA_MODEL", "gemma3:4b").strip() or "gemma3:4b"
OLLAMA_TIMEOUT_SECONDS = positive_bounded_int(
    "HR_AGENT_OLLAMA_TIMEOUT_SECONDS", 600
)


def finish(payload: dict[str, Any], exit_code: int = 0) -> None:
    print(json.dumps(payload, ensure_ascii=False))
    raise SystemExit(exit_code)


def extract_json_object(text: str) -> dict[str, Any] | None:
    cleaned = str(text or "").strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{[\s\S]*\}", cleaned)
    if not match:
        return None

    try:
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def normalize_score(value: Any, maximum: int) -> int | None:
    try:
        score = round(float(value))
    except (TypeError, ValueError):
        return None
    return max(0, min(maximum, score))


def normalize_text_list(value: Any) -> list[str]:
    if isinstance(value, list):
        items = value
    elif isinstance(value, str):
        items = re.split(r"[\n;]+", value)
    else:
        return []

    cleaned_items: list[str] = []
    for item in items:
        cleaned = re.sub(r"^\s*(?:[-•]|\d+[.)])\s*", "", str(item)).strip()
        if cleaned and cleaned not in cleaned_items:
            cleaned_items.append(cleaned)
    return cleaned_items[:5]


def call_ollama(system_prompt: str, user_prompt: str) -> str:
    body = {
        "model": OLLAMA_MODEL,
        "stream": False,
        "format": "json",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "options": {"temperature": 0.1},
    }

    request = urllib.request.Request(
        OLLAMA_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=OLLAMA_TIMEOUT_SECONDS) as response:
        response_data = json.loads(response.read().decode("utf-8"))

    return str(response_data.get("message", {}).get("content") or "").strip()


try:
    input_payload = json.load(sys.stdin)
except Exception as exc:
    finish(
        {
            "helper_version": HELPER_VERSION,
            "success": False,
            "overall_score": None,
            "hr_operations_score": None,
            "people_analytics_score": None,
            "production_score": None,
            "technical_skills_score": None,
            "bonus_points": None,
            "key_strengths": [],
            "areas_for_improvement": [],
            "status": "hr_agent_input_failed",
            "error": f"Could not read input JSON: {exc}",
        },
        1,
    )

resume_text = str(input_payload.get("resume_text") or "").strip()
company = str(input_payload.get("company") or "").strip()
job_title = str(input_payload.get("title") or "").strip()

if len(resume_text) < 500:
    finish(
        {
            "helper_version": HELPER_VERSION,
            "success": False,
            "overall_score": None,
            "hr_operations_score": None,
            "people_analytics_score": None,
            "production_score": None,
            "technical_skills_score": None,
            "bonus_points": None,
            "key_strengths": [],
            "areas_for_improvement": [],
            "status": "hr_agent_input_failed",
            "error": f"Resume text is too short: {len(resume_text)} characters.",
        },
        1,
    )

system_prompt = """
You are a strict Human Resources resume evaluator.

Evaluate only information explicitly supported by the resume.
Do not invent experience, systems, achievements, ownership, or metrics.
Return one valid JSON object only, with no Markdown or commentary.

Use exactly these fields:
{
  "hr_operations_score": 0,
  "people_analytics_score": 0,
  "production_score": 0,
  "technical_skills_score": 0,
  "bonus_points": 0,
  "key_strengths": [],
  "areas_for_improvement": []
}

All scores must be integers.

Scoring rubric:
1. hr_operations_score: 0 to 35
Recruiting coordination, candidate communication, interview scheduling,
onboarding, offboarding, employee records, HR documentation, compliance,
confidentiality, payroll/benefits support, HRIS/ATS usage, engagement,
training coordination, and employee lifecycle support.

2. people_analytics_score: 0 to 30
HR reporting, workforce or people analytics, Excel analysis, Power BI,
Tableau, Python, dashboards, data cleaning, statistics, predictive models,
visualization, workforce insights, and business recommendations.

3. production_score: 0 to 25
Professional real-world experience in actual organizations. Consider work
with real candidates or employee information, repeated operational work,
measurable results, collaboration, ownership, and practical business impact.
Academic projects alone should not receive full credit.

4. technical_skills_score: 0 to 10
Demonstrated ability with Excel, Google Sheets, Power BI, Tableau, Python,
HRIS, ATS, reporting systems, data tools, and Microsoft Office. Separate
hands-on use from familiarity.

5. bonus_points: 0 to 5
Meaningful differentiators such as graduate education, excellent academic
performance, international experience, measurable achievements, relevant
certifications, cross-cultural experience, or a strong HR-plus-analytics mix.

Provide three to five concise key strengths and three to five concise areas
for improvement.
""".strip()

user_prompt = f"""
Evaluate this resume for early-career Human Resources, HR Operations,
Talent Acquisition, People Analytics, HRIS, and People Operations roles.

Target company: {company or "Not supplied"}
Target role: {job_title or "Not supplied"}

RESUME START
{resume_text}
RESUME END

Return only the required JSON object.
""".strip()

last_raw_output = ""
last_error = ""

for attempt in range(1, 4):
    try:
        raw_output = call_ollama(system_prompt, user_prompt)
        last_raw_output = raw_output
        result = extract_json_object(raw_output)

        if not result:
            last_error = "Ollama did not return a valid JSON object."
            continue

        scores_object = result.get("scores")
        if isinstance(scores_object, dict):
            result = {**scores_object, **result}

        hr_operations_score = normalize_score(result.get("hr_operations_score"), 35)
        people_analytics_score = normalize_score(result.get("people_analytics_score"), 30)
        production_score = normalize_score(result.get("production_score"), 25)
        technical_skills_score = normalize_score(result.get("technical_skills_score"), 10)
        bonus_points = normalize_score(result.get("bonus_points"), 5)

        required_scores = [
            hr_operations_score,
            people_analytics_score,
            production_score,
            technical_skills_score,
            bonus_points,
        ]
        if any(score is None for score in required_scores):
            last_error = "Ollama JSON was missing one or more required scores."
            continue

        overall_score = min(
            100,
            hr_operations_score
            + people_analytics_score
            + production_score
            + technical_skills_score
            + bonus_points,
        )

        finish(
            {
                "helper_version": HELPER_VERSION,
                "success": True,
                "overall_score": overall_score,
                "hr_operations_score": hr_operations_score,
                "people_analytics_score": people_analytics_score,
                "production_score": production_score,
                "technical_skills_score": technical_skills_score,
                "bonus_points": bonus_points,
                "key_strengths": normalize_text_list(result.get("key_strengths")),
                "areas_for_improvement": normalize_text_list(
                    result.get("areas_for_improvement")
                ),
                "attempts_used": attempt,
                "status": (
                    "passed_hr_quality_gate"
                    if overall_score >= 90
                    else "review_required_below_hr_quality_gate"
                ),
                "error": "",
                "scoring_method": "direct_local_ollama_structured_json",
            }
        )

    except urllib.error.URLError as exc:
        last_error = f"Could not connect to Ollama at {OLLAMA_URL}: {exc}"
    except TimeoutError:
        last_error = "The Ollama request timed out."
    except Exception as exc:
        last_error = f"Unexpected HR Agent error: {exc}"

finish(
    {
        "helper_version": HELPER_VERSION,
        "success": False,
        "overall_score": None,
        "hr_operations_score": None,
        "people_analytics_score": None,
        "production_score": None,
        "technical_skills_score": None,
        "bonus_points": None,
        "key_strengths": [],
        "areas_for_improvement": [],
        "attempts_used": 3,
        "status": "hr_agent_parse_failed",
        "error": last_error or "The local model did not return usable scores.",
        "raw_output_tail": last_raw_output[-3000:],
        "scoring_method": "direct_local_ollama_structured_json",
    }
)
