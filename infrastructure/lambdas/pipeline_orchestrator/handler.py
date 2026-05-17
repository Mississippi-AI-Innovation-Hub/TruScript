"""
MSBN transcript fraud detection pipeline lambda.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone

import time

import boto3
from botocore.exceptions import ClientError

# ---------------------------------------------------------------------------
# IS_FINETUNED — set to True to call a model directly via
# bedrock_runtime.converse() instead of the KB RetrieveAndGenerate path.
#
# When True:  BEDROCK_CONVERSE_MODEL_ID controls which model is called.
#             Set it to a foundation model ID (e.g. amazon.nova-lite-v1:0)
#             or, create provisioned throughput for fine-tuned
#             model, set it to that provisioned-model ARN.
#             No Knowledge Base is used; transcript text is passed directly.
# When False: standard KB RetrieveAndGenerate flow (BEDROCK_KB_MODEL_ARN
#             must be a foundation-model ARN that RetrieveAndGenerate accepts).
# ---------------------------------------------------------------------------
IS_FINETUNED = True

# aws clients
_region = os.environ["AWS_REGION_NAME"]
textract = boto3.client("textract", region_name=_region)
rekognition = boto3.client("rekognition", region_name=_region)
bedrock_agent = boto3.client("bedrock-agent-runtime", region_name=_region)
bedrock_runtime = boto3.client("bedrock-runtime", region_name=_region)
s3 = boto3.client("s3", region_name=_region)

_REGION_DENY_ERRORS = ("us-east-2", "us-west-2", "explicit deny in a service control policy")

def _is_region_deny(exc: Exception) -> bool:
    """Return True if the error is a cross-region SCP block we should retry."""
    msg = str(exc)
    return any(kw in msg for kw in _REGION_DENY_ERRORS)


_JSON_PROMPT_TEMPLATE = (
    "You are a nursing transcript fraud reviewer.\n\n"
    "Retrieved reference knowledge:\n$search_results$\n\n"
    "IMPORTANT: You MUST respond with ONLY a single valid JSON object — no prose, "
    "no explanation, no markdown, nothing outside the JSON.\n\n"
    "Required format (pick one):\n"
    "{\"claim\": \"yes\", \"reasoning\": \"one brief sentence\"}\n"
    "{\"claim\": \"no\", \"reasoning\": \"one brief sentence\"}\n"
    "{\"claim\": \"uncertain\", \"reasoning\": \"one brief sentence\"}\n\n"
    "Rules:\n"
    "  claim=yes   → the audit check passes / condition IS met\n"
    "  claim=no    → the audit check fails / condition is NOT met\n"
    "  claim=uncertain → not enough information to decide\n\n"
    "Do NOT include any text before or after the JSON object."
)


def _retrieve_and_generate_with_retry(question: str, kb_id: str, model_arn: str,
                                       max_attempts: int = 3) -> dict:
    """retrieve_and_generate with Nova Pro, forcing JSON output via generationConfiguration."""
    last_exc: Exception = RuntimeError("no attempts made")

    for attempt in range(1, max_attempts + 1):
        try:
            return bedrock_agent.retrieve_and_generate(
                input={"text": question},
                retrieveAndGenerateConfiguration={
                    "type": "KNOWLEDGE_BASE",
                    "knowledgeBaseConfiguration": {
                        "knowledgeBaseId": kb_id,
                        "modelArn": model_arn,
                        "generationConfiguration": {
                            "promptTemplate": {
                                "textPromptTemplate": _JSON_PROMPT_TEMPLATE,
                            }
                        },
                    },
                },
            )
        except ClientError as exc:
            last_exc = exc
            if _is_region_deny(exc):
                print(f"[WARN] RetrieveAndGenerate routed to blocked region (attempt {attempt}), retrying…")
                time.sleep(0.5 * attempt)
            else:
                raise
    raise last_exc


_FINETUNED_SYSTEM = "You are an expert nursing transcript reviewer."

def _converse_with_retry(transcript_text: str, question: str, model_id: str,
                         max_attempts: int = 3) -> dict:
    """Call the fine-tuned model via Converse API — matches the nova2_conversation training format.

    Training format (training/nova2_conversation.json):
      system:    [{"text": "You are an expert nursing transcript reviewer."}]
      user:      ### TRANSCRIPT ###\\n{text}\\n### QUESTION ###\\n{question}
      assistant: [reasoningContent block, text block with {"claim": "yes|no|uncertain", "reasoning": "..."}]

    Training format matches nova2_conversation.json exactly:
      system:    [{"text": "You are an expert nursing transcript reviewer."}]
      user:      ### TRANSCRIPT ###\\n{text}\\n### QUESTION ###\\n{question}
      assistant: {"claim": "yes|no|uncertain", "reasoning": "..."}
    """
    user_text = f"### TRANSCRIPT ###\n{transcript_text}\n### QUESTION ###\n{question}"
    last_exc: Exception = RuntimeError("no attempts made")

    for attempt in range(1, max_attempts + 1):
        try:
            response = bedrock_runtime.converse(
                modelId=model_id,
                system=[{"text": _FINETUNED_SYSTEM}],
                messages=[{"role": "user", "content": [{"text": user_text}]}],
                inferenceConfig={"maxTokens": 512, "temperature": 0.0},
            )
            content = response["output"]["message"]["content"]
            text = next(
                (block["text"] for block in reversed(content) if "text" in block),
                None,
            )
            if text is None:
                raise ValueError("converse response had no text block")
            return {"output": {"text": text}}
        except ClientError as exc:
            last_exc = exc
            if _is_region_deny(exc):
                print(f"[WARN] converse routed to blocked region (attempt {attempt}), retrying…")
                time.sleep(0.5 * attempt)
            else:
                raise
    raise last_exc


def _bedrock_call(transcript_text: str, question: str) -> dict:
    """Dispatch to converse or KB retrieve-and-generate based on IS_FINETUNED."""
    if IS_FINETUNED:
        return _converse_with_retry(transcript_text, question, model_id=CONVERSE_MODEL_ID)
    return _retrieve_and_generate_with_retry(question, kb_id=KB_ID, model_arn=KB_MODEL_ARN)


# config
DOCUMENTS_BUCKET = os.environ["S3_DOCUMENTS_BUCKET"]
ML_DATA_BUCKET = os.environ["S3_ML_DATA_BUCKET"]
KB_ID = os.environ["BEDROCK_KB_ID"]
KB_MODEL_ARN = os.environ["BEDROCK_KB_MODEL_ARN"]
# Model for direct converse() calls (IS_FINETUNED=True path).
# Use a foundation model ID or a provisioned-model ARN for a fine-tuned model.
# AWS no longer allows direct converse() on custom-model ARNs without provisioned throughput.
CONVERSE_MODEL_ID = os.environ.get("BEDROCK_CONVERSE_MODEL_ID", "amazon.nova-lite-v1:0")
API_INTERNAL_URL = os.environ.get("API_INTERNAL_URL", "")
API_CALLBACK_SECRET = os.environ.get("API_CALLBACK_SECRET", "")
REKOGNITION_PROJECT_ARN = os.environ.get("REKOGNITION_PROJECT_VERSION_ARN", "")

# each entry: question text, flag_on (which answer signals fraud), and optional fields:
#   context: injected into this question's prompt only, not shared with other questions
#   gate: key into _GATES — question is skipped if the gate function returns False
#   compute: key into _COMPUTATIONS — answer derived programmatically, no Bedrock call
AUDIT_QUESTIONS = [
    # ── timing / speed red flags (5) ─────────────────────────────────────────
    {
        "question": "Does the transcript show program completion in two months or less for a 60+ credit program?",
        "flag_on": "yes",
    },
    {
        "question": "Was the entire nursing program completed within a single semester?",
        "flag_on": "yes",
    },
    {
        "question": "Does the transcript show completion within the same month as enrollment start?",
        "flag_on": "yes",
    },
    {
        "question": "Does the transcript show 60 or more credits completed in less than one year?",
        "flag_on": "yes",
        "context": (
            "Only use per-semester or per-term credit counts with their actual dates — do NOT use the "
            "cumulative total credits shown in any summary line. "
            "Add up only the credits from semesters whose dates fall within a single 12-month window. "
            "A transcript spanning multiple years with 60+ total credits does NOT flag this question."
        ),
    },
    {
        "question": "Is the program duration less than 12 months for an ADN/ASN program without documented LPN-to-RN advanced placement?",
        "flag_on": "yes",
        "gate": "is_adn_asn",
    },
    # ── credit hour integrity (3) ─────────────────────────────────────────────
    {
        "question": "Does the sum of all individual course credit hours equal the total credit hours stated on the transcript?",
        "flag_on": "no",
        "context": (
            "Some institutions use large integrated clinical blocks worth 14-16 credit hours for a single course "
            "(e.g. 'Practical Nursing Foundations 15 credits'). This is normal for PN/LPN programs. "
            "Only flag if the stated total is mathematically impossible given the listed courses."
        ),
        "compute": "credit_sum_matches",
    },
    {
        "question": "Does the total credit hours meet the minimum for the claimed degree level (at least 60 for RN/ADN/ASN, at least 45 for LPN/PN, at least 120 for BSN)?",
        "flag_on": "no",
        "context": (
            "Always sum BOTH transfer credits AND institutional credits for the total. "
            "Accelerated BSN transcripts show a 'Total Transfer Units' block (typically 60-90 credits from the prior degree) "
            "plus institutional program units — add both together before comparing to the 120-credit BSN minimum. "
            "A transcript showing 78 transfer units + 49 institutional units = 127 total DOES meet the 120 minimum."
        ),
    },
    {
        "question": "Is the total credit hours consistent with the time between enrollment start and graduation?",
        "flag_on": "no",
    },
    # ── transfer credit integrity (3, gated) ─────────────────────────────────
    {
        "question": "Do transfer credit hours exceed what the listed transfer courses could provide?",
        "flag_on": "yes",
        "gate": "has_transfer_credits",
        "context": (
            "Transfer blocks often list courses with abbreviated credit notations or use semester-hour "
            "equivalents. Count only the credits explicitly attributed to transfer courses, not institution credits."
        ),
    },
    {
        "question": "Are transfer credits accounted for by listed transfer courses?",
        "flag_on": "no",
        "gate": "has_transfer_credits",
    },
    {
        "question": "Are all clinical practicum courses marked as transferred rather than completed at the issuing institution?",
        "flag_on": "yes",
        "gate": "has_transfer_credits",
        "context": (
            "LPN-to-RN or LPN-to-BSN advanced placement programs legitimately transfer nursing fundamentals "
            "and clinical credits from prior LPN education. These show as 'ADVANCED PLACEMENT' or 'AWARDED BY NUR DEPT' "
            "and are not fraud indicators."
        ),
    },
    # ── GPA and grade integrity (3) ───────────────────────────────────────────
    {
        "question": "Is the cumulative GPA mathematically consistent with the grades listed?",
        "flag_on": "no",
        "context": (
            "Some institutions use a non-4.0 scale or assign quality points differently. "
            "W (withdrawal), WP (withdraw passing), WF (withdraw failing), NC (no credit), and P (pass) "
            "grades are typically excluded from GPA calculation. "
            "A grade suffix like B(R) means the course was repeated; only the latest grade counts toward GPA."
        ),
    },
    {
        "question": "Does the transcript show a GPA of 3.9 or higher while the grades listed do not support it?",
        "flag_on": "yes",
    },
    {
        "question": "Are there failing grades in required nursing courses without a subsequent passing retake?",
        "flag_on": "yes",
        "context": (
            "W (withdrawal), WP (withdraw passing), and NC (no credit) are not failing grades. "
            "At many institutions the (R) suffix on ANY grade means the course was or will be repeated — "
            "so D(R), C(R), F(R) all indicate the student subsequently retook the course. "
            "B(R) or A(R) means this attempt IS the successful retake. "
            "Only flag if a clearly failing grade (F or D) appears with NO (R) suffix AND no later passing attempt for the same course. "
            "Scholastic Probation noted on a term is a warning indicator but not itself a failing grade."
        ),
    },
    # ── date and calendar consistency (3) ────────────────────────────────────
    {
        "question": "Do semester dates follow recognizable academic calendar patterns (Aug-Dec, Jan-May)?",
        "flag_on": "no",
        "context": (
            "Nursing programs use many term naming conventions — all are legitimate: "
            "simple season-year ('Fall 2023', 'Spring 2024'); "
            "academic-year spans ('2020-2021 FALL', '2024-2025 Spring') where the first year applies to Fall "
            "and the second year applies to Spring/Summer by standard US academic calendar; "
            "short season codes ('FA-24', 'SP-25', 'SU-25', 'WT-24') where FA=Fall, SP=Spring, SU=Summer, WT=Winter "
            "and the suffix is a 2-digit year; "
            "explicit date ranges embedded in the term name ('(01/13/25)-(05/09/25)'). "
            "Summer terms, 8-week blocks, 10-week sessions, and winter terms are all normal. "
            "Only flag if a date is genuinely impossible (e.g. a Fall term starting in April)."
        ),
        "compute": "dates_chronological",
    },
    {
        "question": "Do program dates or enrollment length statements conflict with each other?",
        "flag_on": "yes",
        "context": (
            "When reading academic-year span notation like '2024-2025 Fall', Fall aligns with the first year (2024). "
            "Spring and Summer in that span align with the second year (2025). "
            "Read any stated graduation or completion date as a literal calendar date, not as part of an academic-year span. "
            "Only flag when two explicit date statements are mathematically contradictory."
        ),
    },
    {
        "question": "Does the graduation date fall after the final course completion date?",
        "flag_on": "no",
        "context": (
            "In academic-year span notation ('YYYY-YYYY+1 Season'), Fall aligns with the first year; "
            "Spring and Summer align with the second year. "
            "Resolve both the graduation date and the last course term to actual calendar dates before comparing. "
            "Only flag if the graduation date is provably before the last term's end date."
        ),
    },
    # ── document internal consistency (3) ────────────────────────────────────
    {
        "question": "Is the institution name spelled identically throughout the transcript (header, body, degree section)?",
        "flag_on": "no",
        "context": (
            "OCR line-wrapping may split a long institution name across multiple lines — this is not an inconsistency. "
            "Official transcripts include security watermarks: repeated words or phrases printed diagonally across the page "
            "as a background security pattern. The OCR will capture these as stray text mixed into the main content. "
            "Completely ignore any text that appears to be a repeated background pattern or security phrase. "
            "Only flag if the clearly readable institution name in the document header differs from the degree award section."
        ),
    },
    {
        "question": "Is the student name spelled consistently throughout the transcript?",
        "flag_on": "no",
        "context": (
            "Redacted fields appear as solid black bars or repeated dashes — ignore them entirely. "
            "Security watermarks produce stray character fragments that the OCR may place near name fields — "
            "these are background security artifacts, not name text. "
            "Only flag if two clearly readable, non-redacted name instances spell the name differently."
        ),
    },
    {
        "question": "Are course codes, department prefixes, and numbering levels (1000s, 2000s, 3000s, 4000s) consistent throughout?",
        "flag_on": "no",
        "context": (
            "Course codes ending in -1, -2, -3 (e.g. N 412-1, N 412-2, N 413-3) are sequential sections "
            "of one multi-part practicum course taken across multiple semesters — this is normal and not an inconsistency. "
            "Level suffixes like 'L' (lab), 'Lec' (lecture), 'Lab', 'CL' (clinical) on the same base number are also normal."
        ),
    },
    # ── fraud red flags (3) ───────────────────────────────────────────────────
    {
        "question": "Is the same course listed multiple times without indication of retake after failure?",
        "flag_on": "yes",
        "context": (
            "Course codes ending in -1, -2, -3 (e.g. N 412-1, N 412-2) are sequential sections of one "
            "multi-semester practicum, NOT duplicate listings of the same course. "
            "A grade suffix like B(R) or A(R) explicitly marks a retake — this is expected after an earlier failure. "
            "Only flag if the identical course code and section appear twice with no failure to justify it."
        ),
    },
    {
        "question": "Does the transcript mention life experience credits, no admission exams, or prior learning credits for core nursing clinicals?",
        "flag_on": "yes",
    },
    {
        "question": "Does the degree or credential title on the transcript match a nursing program (not a non-nursing field)?",
        "flag_on": "no",
        "context": (
            "Accepted nursing credentials: Associate of Arts (Nursing), Associate of Applied Science (Nursing), "
            "Career Certificate (Practical Nursing), ADN, ASN, LPN, PN, BSN, RN, Collaborative Nursing Care. "
            "Flag if the only degree listed is in an unrelated field (e.g. Radiological Sciences, Business)."
        ),
    },
    # ── approved institution (1) ──────────────────────────────────────────────
    {
        "question": "Is the issuing institution present on the Mississippi Board of Nursing approved nursing schools list?",
        "flag_on": "no",
    },
    # ── curriculum completeness (12 total, 6 gated to RN/BSN) ────────────────
    # applies to all programs — PN/LPN included
    {
        "question": "Is Nursing Fundamentals or equivalent foundational nursing course present?",
        "flag_on": "no",
        "context": (
            "Foundational nursing content covers basic nursing skills, patient safety, care principles, and nursing theory. "
            "It may appear as a standalone course ('Nursing Fundamentals', 'Fundamentals of Nursing Practice', "
            "'Intro to Nursing', 'Practical Nursing Foundations') or bundled into a large integrated nursing course. "
            "In accelerated BSN programs, foundational content from a prior degree appears as a transfer credit block — "
            "treat any substantial transfer block as satisfying this requirement. "
            "In bridge programs, a transition course covering foundational competency review also satisfies this."
        ),
    },
    # gated to RN/BSN only — PN/LPN programs legitimately lack separate clinical listings and adult specialty courses
    {
        "question": "Does the transcript list clinical practicum, clinical rotation, or nursing lab courses?",
        "flag_on": "no",
        "gate": "is_bsn_or_rn_not_accelerated",
        "context": (
            "RN and BSN programs must list explicit clinical practicum, rotation, or lab courses separate from lecture. "
            "LPN-to-RN/BSN advanced placement students may show clinicals as transfer credits — accept that. "
            "Do NOT accept a PN/LPN program format (large integrated lecture+clinical blocks) as satisfying this for an RN/BSN claim."
        ),
    },
    {
        "question": "Is Medical-Surgical Nursing or an adult health nursing equivalent present?",
        "flag_on": "no",
        "gate": "is_bsn_or_rn_not_accelerated",
        "context": (
            "Medical-Surgical or adult health nursing covers care of acutely or chronically ill adult patients "
            "in medical or surgical settings. It may appear under many titles: 'Medical-Surgical Nursing', "
            "'Adult Health Nursing', 'Adult Health I/II', 'Nursing Care of Adults', 'Clinical Nursing Practice', "
            "or any course whose description covers acute illness, surgical care, or adult patient populations. "
            "In accelerated BSN programs with a substantial transfer credit block, this content is included "
            "in the prior-degree block — treat as satisfied if a large transfer block is present. "
            "Advanced practice bridge courses covering complex nursing care of adults also satisfy this requirement."
        ),
    },
    {
        "question": "Is Community Health Nursing or Public Health Nursing present?",
        "flag_on": "no",
        "gate": "is_bsn_or_rn_not_accelerated",
        "context": (
            "Community or public health nursing covers disease prevention, health promotion, and care of populations "
            "outside acute hospital settings — including home health, school health, and community-based care. "
            "Accept any course whose content addresses public health principles, community nursing practice, "
            "population-based health assessment, or epidemiology applied to nursing."
        ),
    },
    {
        "question": "Is Maternal Child Nursing or Obstetrics Nursing present?",
        "flag_on": "no",
        "gate": "is_bsn_or_rn_not_accelerated",
        "context": (
            "Maternal-child or obstetric nursing covers care during pregnancy, labor, delivery, postpartum recovery, "
            "care of newborns, and pediatric nursing. Accept any course whose content addresses maternity care, "
            "childbearing families, obstetrics, labor and delivery, postpartum, or pediatric patients."
        ),
    },
    {
        "question": "Is Mental Health Nursing or Psychiatric Nursing present?",
        "flag_on": "no",
        "gate": "is_bsn_or_rn_not_accelerated",
        "context": (
            "Mental health or psychiatric nursing covers care of patients with mental health disorders, "
            "psychiatric conditions, or substance use disorders — including therapeutic communication, "
            "behavioral health assessment, and psychosocial nursing. Accept any course whose content "
            "addresses psychiatric or behavioral health nursing practice."
        ),
    },
]

QUESTION_PROMPT_TEMPLATE = """
You are an expert nursing transcript reviewer. Below is a raw Textract export. Based ONLY on the provided text and information, answer the question in a concise, grounded JSON format with 'claim' and 'reasoning'.

IMPORTANT: Keep reasoning to ONE LINE ONLY. Be direct and concise.

### TRANSCRIPT TEXT ###
{transcript_text}

### SIGNATURE ANALYSIS ###
Signature Count: {signature_count}
Seal Detected: {seal_detected}

### EXAMPLES ###
Question: Does the transcript display an overall GPA?
Answer: {{"claim": "Yes", "reasoning": "Cumulative GPA: 3.125 is displayed in the summary section[cite: 145]."}}

Question: Are any passed courses assigned more than 8 credit hours?
Answer: {{"claim": "Yes", "reasoning": "Courses NUR 101 (12 credits) and NUR 201 (15 credits) exceed 8 credit hours[cite: 67, 89]."}}

Question: Is the institution accredited by an approved body?
Answer: {{"claim": "No", "reasoning": "No accreditation information is stated in the document[cite: none]."}}

### QUESTION ###
{audit_question}
"""

SUMMARY_PROMPT_TEMPLATE = """
You are an expert nursing transcript fraud detector. Based on the transcript text and audit Q&A results below, provide a final fraud assessment.

IMPORTANT: Keep response to 2 LINES MAXIMUM. Be direct and decisive. Your response should be based on the qa.

### TRANSCRIPT TEXT ###
{transcript_text}

### SIGNATURE ANALYSIS ###
Signature Count: {signature_count}
Seal Detected: {seal_detected}

### AUDIT Q&A RESULTS ###
{qa_results}

### EXAMPLES ###
Question: Is this transcript fraudulent?
Answer: {{"claim": "Yes", "reasoning": "Multiple red flags: excessive credit hours, missing accreditation, suspicious GPA patterns, and no institutional seal detected."}}

Question: Is this transcript fraudulent?  
Answer: {{"claim": "No", "reasoning": "All standard elements present: proper credit allocation, CCNE accreditation confirmed, consistent grading, institutional seal detected."}}

### QUESTION ###
Is this transcript fraudulent?
"""



def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _extract_first_json_object(raw_text: str) -> dict | None:
    """Best-effort JSON object extraction from LLM output."""
    txt = (raw_text or "").strip()
    if not txt:
        return None

    # Fast path: raw output is valid JSON object.
    try:
        parsed = json.loads(txt)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        pass

    # Markdown fenced JSON/code fallback.
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", txt, re.DOTALL | re.IGNORECASE)
    if fence_match:
        try:
            parsed = json.loads(fence_match.group(1))
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            pass

    # Find first JSON-like object region.
    obj_match = re.search(r"\{.*\}", txt, re.DOTALL)
    if obj_match:
        try:
            parsed = json.loads(obj_match.group(0))
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            pass

    return None


def _infer_claim_from_natural_language(text: str) -> tuple[str, str]:
    """
    Fallback: infer yes/no/uncertain from Nova Pro natural language output
    when the model ignores the JSON instruction.
    """
    tl = text.lower()
    reasoning = text[:300].strip()

    # Cannot-determine patterns → uncertain
    uncertain_signals = [
        "cannot find sufficient information",
        "cannot determine",
        "does not provide",
        "not provide specific",
        "no information",
        "insufficient information",
        "unable to determine",
        "not explicitly",
        "search results do not",
        "results do not contain",
        "results do not provide",
        "model cannot find",
    ]
    # Positive signals (check passes)
    yes_signals = [
        r"\bdoes display\b",
        r"\bdoes show\b",
        r"\bdoes include\b",
        r"\bdoes exclude\b",
        r"\bdoes avoid\b",
        r"\bdoes match\b",
        r"\bis accredited\b",
        r"\bare accredited\b",
        r"\bappears to have been sent\b",
        r"\bsum.*match\b",
        r"\bdo match\b",
        r"\bis present\b",
        r"\bare present\b",
        r"\bdoes meet\b",
    ]
    # Negative signals (check fails)
    no_signals = [
        r"\bdoes not display\b",
        r"\bdoes not include\b",
        r"\bis not accredited\b",
        r"\bnot accredited\b",
        r"\bno seal\b",
        r"\bno signature\b",
        r"\bdoes not show\b",
    ]

    # Check first 300 chars for dominant signal
    excerpt = tl[:300]

    for pat in no_signals:
        if re.search(pat, excerpt):
            return "no", reasoning

    for pat in yes_signals:
        if re.search(pat, excerpt):
            return "yes", reasoning

    if any(sig in tl for sig in uncertain_signals):
        return "uncertain", reasoning

    # Last resort: check overall sentiment of first sentence
    first_sentence = re.split(r"[.!?]", text)[0].lower() if text else ""
    if re.search(r"\bdoes\b|\bis\b|\bare\b|\bhas\b|\bhave\b", first_sentence):
        if not re.search(r"\bnot\b|\bno\b|\bcannot\b|\bunable\b", first_sentence):
            return "yes", reasoning

    return "uncertain", reasoning


def _parse_claim_reasoning(raw_text: str) -> tuple[str, str]:
    """
    Parse model output into (answer, reasoning).
    Supports:
    1) JSON: {"claim":"Yes/No", "reasoning":"..."}
    2) free text: 'claim: Yes; reasoning: ...'
    3) Nova Pro natural language (pattern-based fallback)
    """
    parsed = _extract_first_json_object(raw_text)
    if parsed is not None:
        claim = str(parsed.get("claim", "")).strip().lower()
        reasoning = str(parsed.get("reasoning", "")).strip()
    else:
        claim_match = re.search(r"claim\s*:\s*(yes|no|uncertain|true|false)", raw_text, re.IGNORECASE)
        reasoning_match = re.search(r"reasoning\s*:\s*(.+)$", raw_text, re.IGNORECASE | re.DOTALL)
        if claim_match:
            claim = claim_match.group(1).strip().lower()
            reasoning = (reasoning_match.group(1).strip() if reasoning_match else raw_text.strip())
        else:
            # Nova Pro natural language fallback
            return _infer_claim_from_natural_language(raw_text)

    if claim in {"yes", "y", "true"}:
        answer = "yes"
    elif claim in {"no", "n", "false"}:
        answer = "no"
    else:
        answer = "uncertain"

    if not reasoning:
        reasoning = "model returned no reasoning"

    return answer, reasoning


def _empty_extracted_data(notes: str = "") -> dict:
    return {
        "institution_name": "",
        "program_name": "",
        "degree_awarded": "",
        "graduation_date": "",
        "graduation_confirmed": False,
        "total_credits": 0.0,
        "nursing_credits": 0.0,
        "accreditation_type": "",
        "accreditation_body": "",
        "extraction_confidence": 0.0,
        "extraction_notes": notes or "no text available for extraction",
    }


# ── Regex extraction for frontend metadata (separate from ML pipeline) ──────

_DEGREE_PATTERNS = [
    (r"\bBachelor\s+of\s+Science\s+in\s+Nursing\b", "BSN", "Bachelor of Science in Nursing"),
    (r"\bAssociate\s+(?:of|in)\s+(?:Science\s+in\s+)?Nursing\b", "ADN", "Associate of Science in Nursing"),
    (r"\bMaster\s+of\s+Science\s+in\s+Nursing\b", "MSN", "Master of Science in Nursing"),
    (r"\bDoctor\s+of\s+Nursing\s+Practice\b", "DNP", "Doctor of Nursing Practice"),
    (r"\bBSN\b", "BSN", "Bachelor of Science in Nursing"),
    (r"\bADN\b", "ADN", "Associate Degree in Nursing"),
    (r"\bMSN\b", "MSN", "Master of Science in Nursing"),
    (r"\bDNP\b", "DNP", "Doctor of Nursing Practice"),
]

_ACCREDITATION_PATTERNS = [
    (r"\bACEN\b", "ACEN", "Accreditation Commission for Education in Nursing"),
    (r"\bCCNE\b", "CCNE", "Commission on Collegiate Nursing Education"),
    (r"\bNLNAC\b", "NLNAC", "National League for Nursing Accrediting Commission"),
    (r"\bregionally\s+accredited\b", "Regional", "Regional Accrediting Body"),
    (r"\bnationally\s+accredited\b", "National", "National Accrediting Body"),
]

def extract_structured_data(transcript_text: str) -> dict:
    """
    Extract structured fields from Textract text for frontend metadata display.
    This is separate from ML pipeline - used only for UI placeholders.
    """
    if not transcript_text.strip():
        return _empty_extracted_data("no text extracted from document")

    text = transcript_text
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    confidence_hits = 0
    confidence_total = 6  # number of fields we attempt to extract

    # Institution name (first non-empty line)
    institution_name = lines[0] if lines else ""
    if institution_name:
        confidence_hits += 1

    # Degree and program
    degree_awarded = ""
    program_name = ""
    for pattern, abbr, full_name in _DEGREE_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            degree_awarded = abbr
            program_name = full_name
            confidence_hits += 1
            break

    # Graduation date
    graduation_date = ""
    graduation_confirmed = False
    date_patterns = [
        r"(?:graduated|conferred|awarded|degree\s+date|graduation\s+date)[:\s]+([A-Za-z]+\s+\d{1,2},?\s+\d{4}|\d{4}-\d{2}-\d{2})",
        r"((?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4})",
        r"((?:Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},?\s+\d{4})",
    ]
    for dp in date_patterns:
        m = re.search(dp, text, re.IGNORECASE)
        if m:
            graduation_date = m.group(1).strip()
            graduation_confirmed = True
            confidence_hits += 1
            break

    # Total credits
    total_credits = 0.0
    cum_matches = re.findall(r"CUM[:\s]+[\d.]+\s+[\d.]+\s+([\d.]+)", text, re.IGNORECASE)
    if cum_matches:
        try:
            total_credits = max(float(v) for v in cum_matches)
            if total_credits > 0:
                confidence_hits += 1
        except ValueError:
            pass
    
    # Fallback for total credits
    if total_credits == 0:
        m = re.search(r"total\s+(?:credit|semester)\s+hours?[:\s]+([\d.]+)", text, re.IGNORECASE)
        if m:
            total_credits = _safe_float(m.group(1))
            if total_credits > 0:
                confidence_hits += 1

    # Accreditation
    accreditation_type = ""
    accreditation_body = ""
    for pattern, acc_type, acc_body in _ACCREDITATION_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            accreditation_type = acc_type
            accreditation_body = acc_body
            confidence_hits += 1
            break

    # GPA present (bonus confidence signal)
    if re.search(r"\bGPA\b|\b[0-4]\.\d{3}\b", text):
        confidence_hits += 1

    extraction_confidence = round(confidence_hits / confidence_total, 2)
    notes = f"Frontend metadata extraction: {confidence_hits}/{confidence_total} fields found"

    # transfer credits present — used by gate functions to skip irrelevant questions
    has_transfer_credits = bool(re.search(
        r"transfer\s+credit|credits?\s+accepted|credits?\s+transferred|prior\s+college|advanced\s+placement",
        text, re.IGNORECASE
    ))

    print(f"[INFO] Frontend extraction: institution='{institution_name}' degree='{degree_awarded}' confidence={extraction_confidence}")

    return {
        "institution_name": institution_name,
        "program_name": program_name,
        "degree_awarded": degree_awarded,
        "graduation_date": graduation_date,
        "graduation_confirmed": graduation_confirmed,
        "total_credits": total_credits,
        "nursing_credits": 0.0,
        "accreditation_type": accreditation_type,
        "accreditation_body": accreditation_body,
        "has_transfer_credits": has_transfer_credits,
        "extraction_confidence": extraction_confidence,
        "extraction_notes": notes,
    }



_TEXTRACT_FEATURES = ["SIGNATURES", "LAYOUT", "TABLES"]

# line blocks below this confidence are likely watermark or background print artifacts
_MIN_LINE_CONFIDENCE = 65.0


def _extract_tables_from_blocks(blocks: list[dict]) -> list[list[list[str]]]:
    """reconstruct tables from textract CELL blocks into row/col string arrays."""
    block_map = {b["Id"]: b for b in blocks}
    tables = []

    for block in blocks:
        if block.get("BlockType") != "TABLE":
            continue

        cells: dict[tuple[int, int], str] = {}
        max_row = 0
        max_col = 0

        for rel in block.get("Relationships", []):
            if rel["Type"] != "CHILD":
                continue
            for cell_id in rel["Ids"]:
                cell = block_map.get(cell_id)
                if not cell or cell.get("BlockType") not in ("CELL", "MERGED_CELL"):
                    continue
                row = cell.get("RowIndex", 1)
                col = cell.get("ColumnIndex", 1)
                max_row = max(max_row, row)
                max_col = max(max_col, col)

                words = []
                for cell_rel in cell.get("Relationships", []):
                    if cell_rel["Type"] != "CHILD":
                        continue
                    for word_id in cell_rel["Ids"]:
                        word = block_map.get(word_id)
                        if word and word.get("BlockType") == "WORD":
                            words.append(word.get("Text", ""))
                cells[(row, col)] = " ".join(words)

        if not cells:
            continue

        table_rows = [
            [cells.get((r, c), "") for c in range(1, max_col + 1)]
            for r in range(1, max_row + 1)
        ]
        tables.append(table_rows)

    return tables


def _format_tables_as_text(tables: list[list[list[str]]]) -> str:
    """format extracted tables as pipe-delimited rows for the model prompt."""
    parts = []
    for t_idx, table in enumerate(tables, 1):
        rows = []
        for row in table:
            if not any(cell.strip() for cell in row):
                continue
            rows.append("| " + " | ".join(cell.strip() for cell in row) + " |")
        if rows:
            parts.append(f"Table {t_idx}:\n" + "\n".join(rows))
    return "\n\n".join(parts)


def run_textract(s3_key: str) -> dict:
    """
    Textract with SIGNATURES, LAYOUT, and TABLES.
    images use synchronous AnalyzeDocument; PDFs use async StartDocumentAnalysis.
    """
    is_pdf = s3_key.lower().endswith(".pdf")
    try:
        if not is_pdf:
            resp = textract.analyze_document(
                Document={"S3Object": {"Bucket": DOCUMENTS_BUCKET, "Name": s3_key}},
                FeatureTypes=_TEXTRACT_FEATURES,
            )
            blocks = resp.get("Blocks", [])
        else:
            start = textract.start_document_analysis(
                DocumentLocation={"S3Object": {"Bucket": DOCUMENTS_BUCKET, "Name": s3_key}},
                FeatureTypes=_TEXTRACT_FEATURES,
            )
            job_id = start["JobId"]
            blocks: list[dict] = []
            next_token: str | None = None
            # Poll for completion (max ~5 minutes, backoff).
            deadline = time.time() + 300
            sleep_s = 1.0
            while True:
                if time.time() > deadline:
                    raise TimeoutError(f"Textract job timed out (JobId={job_id})")
                kwargs = {"JobId": job_id}
                if next_token:
                    kwargs["NextToken"] = next_token
                resp = textract.get_document_analysis(**kwargs)
                status = resp.get("JobStatus")
                if status == "FAILED":
                    raise RuntimeError(f"Textract job failed (JobId={job_id}): {resp.get('StatusMessage')}")
                if status in ("IN_PROGRESS", "SUCCEEDED"):
                    # Only collect blocks once succeeded.
                    if status == "SUCCEEDED":
                        blocks.extend(resp.get("Blocks", []) or [])
                        next_token = resp.get("NextToken")
                        if not next_token:
                            break
                    else:
                        time.sleep(sleep_s)
                        sleep_s = min(sleep_s * 1.5, 5.0)
                        continue
                else:
                    raise RuntimeError(
                        f"Textract job returned unexpected status '{status}' (JobId={job_id})"
                    )

        # filter line blocks below confidence threshold to strip watermark and background print text
        lines = [
            b["Text"] for b in blocks
            if b.get("BlockType") == "LINE"
            and "Text" in b
            and _safe_float(b.get("Confidence", 0.0)) >= _MIN_LINE_CONFIDENCE
        ]
        words = [b["Text"] for b in blocks if b.get("BlockType") == "WORD" and "Text" in b]
        signatures = [
            {
                "confidence": round(_safe_float(b.get("Confidence", 0.0), 0.0), 3),
                "bounding_box": b.get("Geometry", {}).get("BoundingBox", {}),
            }
            for b in blocks
            if b.get("BlockType") == "SIGNATURE"
        ]

        tables = _extract_tables_from_blocks(blocks)
        tables_text = _format_tables_as_text(tables)
        print(f"[INFO] textract extracted {len(tables)} tables")

        return {
            "success": True,
            "full_text": "\n".join(lines),
            "tables_text": tables_text,
            "line_count": len(lines),
            "word_count": len(words),
            "signature_count": len(signatures),
            "signature_details": signatures,
            "is_pdf": is_pdf,
        }
    except Exception as e:
        print(f"[ERROR] textract failed: {e}")
        return {
            "success": False,
            "error": str(e),
            "full_text": "",
            "tables_text": "",
            "line_count": 0,
            "word_count": 0,
            "signature_count": 0,
            "signature_details": [],
            "is_pdf": is_pdf,
        }


def run_rekognition(s3_key: str) -> dict:
    # Rekognition DetectLabels does not support PDF input directly.
    if s3_key.lower().endswith(".pdf"):
        return {
            "success": True,
            "skipped": True,
            "reason": "rekognition skipped for pdf input",
            "seal_detected": False,
            "labels": [],
            "custom_labels": None,
        }

    result = {"success": False, "seal_detected": False, "labels": [], "custom_labels": None}
    try:
        label_resp = rekognition.detect_labels(
            Image={"S3Object": {"Bucket": DOCUMENTS_BUCKET, "Name": s3_key}},
            MaxLabels=40,
            MinConfidence=55.0,
        )
        labels = [
            {"name": l["Name"], "confidence": round(l["Confidence"], 2)}
            for l in label_resp.get("Labels", [])
        ]
        label_names = {l["name"].lower() for l in labels}
        seal_keywords = {"seal", "emblem", "logo", "stamp", "badge", "crest", "symbol"}
        seal_detected = bool(seal_keywords & label_names)

        custom_labels = None
        if REKOGNITION_PROJECT_ARN:
            try:
                cl_resp = rekognition.detect_custom_labels(
                    ProjectVersionArn=REKOGNITION_PROJECT_ARN,
                    Image={"S3Object": {"Bucket": DOCUMENTS_BUCKET, "Name": s3_key}},
                    MinConfidence=60.0,
                )
                custom_labels = cl_resp.get("CustomLabels", [])
            except Exception as e:
                print(f"[WARN] custom labels failed: {e}")

        result = {
            "success": True,
            "seal_detected": seal_detected,
            "labels": labels,
            "custom_labels": custom_labels,
        }
    except Exception as e:
        print(f"[ERROR] rekognition failed: {e}")
        result["error"] = str(e)
    return result


def save_question_result_to_s3(verification_id: str, index: int, result: dict) -> None:
    key = f"results/{verification_id}/audit_questions/{index:02d}.json"
    try:
        s3.put_object(
            Bucket=ML_DATA_BUCKET,
            Key=key,
            Body=json.dumps(result, indent=2, default=str),
            ContentType="application/json",
        )
    except Exception as e:
        print(f"[WARN] could not save question result to s3: {e}")


# gate functions: return True if the question should run for this transcript
_GATES: dict[str, object] = {
    "is_adn_asn": lambda d: any(
        kw in d.get("degree_awarded", "").upper()
        for kw in ("ADN", "ASN", "ASSOCIATE")
    ),
    "is_bsn_or_rn": lambda d: any(
        kw in d.get("degree_awarded", "").upper()
        for kw in ("BSN", "RN", "BACHELOR")
    ),
    # accelerated BSN has Community Health/OB/Mental Health only in the unlisted transfer block —
    # these questions are unanswerable and should be skipped
    "is_bsn_or_rn_not_accelerated": lambda d: (
        any(kw in d.get("degree_awarded", "").upper() for kw in ("BSN", "RN", "BACHELOR"))
        and not any(kw in d.get("program_name", "").lower() for kw in ("accelerated", "accelerated bsn", "accel"))
    ),
    "has_transfer_credits": lambda d: bool(d.get("has_transfer_credits")),
}


def _compute_dates_chronological(full_text: str) -> dict:
    """
    Parse term/semester dates from transcript text and check for impossible gaps.

    Checks: no consecutive gap > 10 years between any two sorted terms.
    Does NOT check document order against sorted order — Textract multi-column
    reading makes positional ordering unreliable.
    """
    _MONTH = {
        "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
        "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6,
        "july": 7, "jul": 7, "august": 8, "aug": 8, "september": 9, "sep": 9,
        "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
        "fall": 9, "spring": 1, "summer": 6, "winter": 12,
        "fa": 9, "sp": 1, "su": 6, "wt": 12,
    }
    dates: list[tuple[int, int, str]] = []

    # format 1: simple "Fall 2023", "Spring 2024", "Summer 2023"
    for m in re.finditer(r"\b(fall|spring|summer|winter)\s+(\d{4})\b", full_text, re.IGNORECASE):
        season, year = m.group(1).lower(), int(m.group(2))
        dates.append((year, _MONTH.get(season, 6), m.group(0)))

    # format 2: academic-year span with separator, e.g. "--- 2020-2021 FALL ---"
    # fall uses first year, spring/summer use second year (standard US academic calendar)
    for m in re.finditer(r"---\s*(\d{4})-(\d{4})\s+(fall|spring|summer|winter)\s*---", full_text, re.IGNORECASE):
        yr1, yr2, season = int(m.group(1)), int(m.group(2)), m.group(3).lower()
        year = yr1 if season == "fall" else yr2
        dates.append((year, _MONTH.get(season, 6), m.group(0).strip()))

    # format 3: academic-year span inline, e.g. "2024-2025 Fall Ten Week" or "2024-2025 Spring"
    # use first year for all seasons to avoid false positives from cohort labeling variation
    for m in re.finditer(r"\b(\d{4})-(\d{4})\s+(fall|spring|summer|winter)", full_text, re.IGNORECASE):
        yr1, _yr2, season = int(m.group(1)), int(m.group(2)), m.group(3).lower()
        dates.append((yr1, _MONTH.get(season, 6), m.group(0).strip()))

    # format 4: short season codes, e.g. "FA-17", "SP-18", "SU-24", "WT-24", "FA-2025"
    for m in re.finditer(r"\b(FA|SP|SU|WT)-(\d{2,4})\b", full_text, re.IGNORECASE):
        code, yr_str = m.group(1).upper(), m.group(2)
        year = int(yr_str) + 2000 if len(yr_str) == 2 else int(yr_str)
        dates.append((year, _MONTH.get(code.lower(), 6), m.group(0)))

    # format 5: year + season + qualifier, e.g. "2025 Spring Full Term Session (01/13/25)-(05/09/25)"
    for m in re.finditer(r"\b(\d{4})\s+(fall|spring|summer|winter)\s+(?:full\s+term|8\s+week|session)", full_text, re.IGNORECASE):
        year, season = int(m.group(1)), m.group(2).lower()
        dates.append((year, _MONTH.get(season, 6), m.group(0).strip()))

    # format 6: season run together with year, e.g. "FALL1998", "Spring 1999"
    for m in re.finditer(r"\b(fall|spring|summer)(\d{4})\b", full_text, re.IGNORECASE):
        season, year = m.group(1).lower(), int(m.group(2))
        dates.append((year, _MONTH.get(season, 6), m.group(0)))

    if len(dates) < 2:
        return {"answer": "uncertain", "reasoning": f"found {len(dates)} parseable term dates, cannot verify"}

    # deduplicate by (year, month)
    seen: set[tuple[int, int]] = set()
    unique: list[tuple[int, int, str]] = []
    for entry in dates:
        key = (entry[0], entry[1])
        if key not in seen:
            seen.add(key)
            unique.append(entry)

    sorted_dates = sorted(unique, key=lambda x: (x[0], x[1]))
    earliest = sorted_dates[0]
    latest = sorted_dates[-1]
    span_months = (latest[0] - earliest[0]) * 12 + (latest[1] - earliest[1])

    # flag only if any two consecutive terms have a gap > 10 years (fabricated timeline)
    for i in range(1, len(sorted_dates)):
        prev, curr = sorted_dates[i - 1], sorted_dates[i]
        gap = (curr[0] - prev[0]) * 12 + (curr[1] - prev[1])
        if gap > 120:
            return {
                "answer": "no",
                "reasoning": f"gap of {gap // 12} years between '{prev[2]}' and '{curr[2]}' is implausible",
            }

    return {
        "answer": "yes",
        "reasoning": f"verified {len(unique)} terms spanning {span_months} months ({earliest[2]} through {latest[2]})",
    }


def _compute_credit_sum_matches(full_text: str, tables_text: str) -> dict:
    """
    Attempt to verify credit sum from tables_text or raw text.
    Parses stated totals and summed course credits where available.
    """
    # extract stated cumulative total — look for patterns like "TOTAL INSTITUTION 160 160 438 2.73"
    # or "Cumulative Attempted Credits: 43" or "OVERALL 183 163 447 2.74"
    stated_total: float | None = None
    for pattern in [
        r"(?:total\s+institution|cumulative\s+attempted\s+credits|overall|cum\s+hours)[:\s]+(\d+\.?\d*)",
        r"earned\s+hrs[:\s]+(\d+\.?\d*)",
    ]:
        m = re.search(pattern, full_text, re.IGNORECASE)
        if m:
            try:
                stated_total = float(m.group(1))
                break
            except ValueError:
                pass

    # sum credits from tables_text rows if available
    summed: float = 0.0
    if tables_text.strip():
        # rows look like "NUR 1116 | Health Care Pro | 6.00 | B | ..." or similar
        for val in re.findall(r"\|\s*([\d]+\.?\d*)\s*\|", tables_text):
            try:
                v = float(val)
                if 0.5 <= v <= 20:  # plausible single-course credit range
                    summed += v
            except ValueError:
                pass

    if stated_total is None:
        return {"answer": "uncertain", "reasoning": "could not locate a stated total credit figure in the transcript"}

    if summed == 0.0:
        return {"answer": "uncertain", "reasoning": f"stated total {stated_total} credits found but could not sum individual courses from tables"}

    # Guard against triple-counting: Textract tables for PN/LPN programs emit per-semester
    # "Total Attempted Credits" and "Total Earned Credits" rows alongside each course credit,
    # inflating the sum by ~3x.  If summed exceeds 2x the stated total it is almost certainly
    # a parsing artifact — return uncertain rather than a false mismatch.
    if stated_total > 0 and summed > stated_total * 2:
        return {
            "answer": "uncertain",
            "reasoning": f"summed value ({summed}) is more than 2x stated total ({stated_total}); likely a table-parsing artifact, cannot verify",
        }

    diff = abs(summed - stated_total)
    if diff <= 3.0:
        return {
            "answer": "yes",
            "reasoning": f"summed course credits ({summed}) within 3 of stated total ({stated_total})",
        }
    return {
        "answer": "no",
        "reasoning": f"summed course credits ({summed}) differ from stated total ({stated_total}) by {diff:.1f}",
    }


def _compute_degree_conferred_present(full_text: str) -> dict:
    """
    Check whether a nursing degree or credential conferral is explicitly stated.
    This is a key rejection reason: transcripts missing degree/conferral are rejected.
    """
    # look for degree-related markers
    degree_signals = [
        r"degrees?\s+(?:awarded|conferred|earned|received)",
        r"degree\s+conferred",
        r"graduation\s+date",
        r"grad\s+date",
        r"grad\s+term",
        r"completion\s+date",
        r"career\s+certificate",
        r"associate\s+of\s+(?:arts|science|applied)",
        r"bachelor\s+of\s+science",
        r"practical\s+nursing.*(?:certificate|diploma|degree)",
        r"confer\s+date",
        r"date\s+conferred",
        r"educational\s+credential",
    ]
    nursing_signals = [
        r"\bnursing\b",
        r"\blpn\b",
        r"\bpn\b",
        r"\brn\b",
        r"\bbsn\b",
        r"\badn\b",
    ]
    found_degree = any(re.search(p, full_text, re.IGNORECASE) for p in degree_signals)
    found_nursing = any(re.search(p, full_text, re.IGNORECASE) for p in nursing_signals)

    if found_degree and found_nursing:
        return {"answer": "yes", "reasoning": "nursing degree/credential conferral statement found in transcript"}
    if found_degree and not found_nursing:
        return {
            "answer": "no",
            "reasoning": "degree conferral found but no nursing program name associated with it — may be a non-nursing degree",
        }
    return {"answer": "no", "reasoning": "no degree conferral or graduation date found in transcript"}


# map compute keys to functions; each takes (full_text, tables_text) and returns {"answer", "reasoning"}
_COMPUTATIONS: dict[str, object] = {
    "dates_chronological": lambda ft, tt: _compute_dates_chronological(ft),
    "credit_sum_matches": lambda ft, tt: _compute_credit_sum_matches(ft, tt),
    "degree_conferred_present": lambda ft, tt: _compute_degree_conferred_present(ft),
}


def run_audit_questions(
    verification_id: str,
    extracted_text: str,
    tables_text: str,
    signature_count: int,
    rekognition_result: dict,
    extracted_data: dict | None = None,
) -> dict:
    if not extracted_text.strip():
        return {
            "success": False,
            "error": "No text extracted; cannot run audit",
            "question_results": [],
        }

    seal_detected = bool(rekognition_result.get("seal_detected"))
    ed = extracted_data or {}

    question_results: list[dict] = []

    # structured table section injected into every Bedrock prompt
    table_section = (
        f"Structured course tables (use these for credit and grade calculations):\n{tables_text}\n\n"
        if tables_text.strip() else ""
    )

    for idx, q_def in enumerate(AUDIT_QUESTIONS, start=1):
        question = q_def["question"]
        flag_on = q_def["flag_on"]
        gate_key = q_def.get("gate")
        compute_key = q_def.get("compute")
        context_note = q_def.get("context", "")

        # gate: skip question if it does not apply to this transcript type
        if gate_key:
            gate_fn = _GATES.get(gate_key)
            if gate_fn and not gate_fn(ed):
                print(f"[INFO] Q{idx} skipped (gate={gate_key} not met for degree={ed.get('degree_awarded')})")
                continue

        # compute: derive answer programmatically without a Bedrock call
        if compute_key:
            compute_fn = _COMPUTATIONS.get(compute_key)
            if compute_fn:
                try:
                    computed = compute_fn(extracted_text, tables_text)
                    answer = computed.get("answer", "uncertain")
                    reasoning = computed.get("reasoning", "")
                    print(f"[INFO] Q{idx} computed ({compute_key}): {answer} -- {reasoning[:80]}")
                    result = {
                        "question_index": idx,
                        "question": question,
                        "flag_on": flag_on,
                        "answer": answer,
                        "reasoning": reasoning,
                        "raw_response": "",
                        "confidence": 0.9 if answer in {"yes", "no"} else 0.5,
                        "parsing_failed": False,
                        "source": compute_key,
                    }
                    question_results.append(result)
                    save_question_result_to_s3(verification_id, idx, result)
                    continue
                except Exception as e:
                    print(f"[WARN] compute {compute_key} failed for Q{idx}: {e}, falling through to Bedrock")

        # build prompt with optional per-question context note
        context_block = f"Context for this question: {context_note}\n\n" if context_note else ""
        audit_question_input = (
            f"Transcript text:\n{extracted_text}\n\n"
            f"{table_section}"
            f"Signatures detected: {signature_count}. "
            f"Seal detected: {'Yes' if seal_detected else 'No'}.\n\n"
            f"{context_block}"
            f"Audit question: {question}"
        )

        try:
            response = _bedrock_call(extracted_text, audit_question_input)

            raw_output = response.get("output", {}).get("text", "")

            # json first, natural language fallback; uncertain is a valid model answer not a failure
            answer, reasoning = _parse_claim_reasoning(raw_output)
            parsing_failed = False
            confidence = 0.8 if answer in {"yes", "no"} else 0.5
            print(f"[INFO] Q{idx} ({flag_on} flags): {answer} -- {reasoning[:80]}")

            result = {
                "question_index": idx,
                "question": question,
                "flag_on": flag_on,
                "answer": answer,
                "reasoning": reasoning,
                "raw_response": raw_output,
                "confidence": confidence,
                "parsing_failed": parsing_failed,
                "source": "bedrock",
            }

        except Exception as e:
            print(f"[ERROR] Bedrock call failed for question {idx}: {e}")
            result = {
                "question_index": idx,
                "question": question,
                "flag_on": flag_on,
                "answer": "parsing_failed",
                "reasoning": f"inference error: {str(e)[:150]}",
                "raw_response": "",
                "confidence": None,
                "parsing_failed": True,
                "source": "bedrock",
            }

        question_results.append(result)
        save_question_result_to_s3(verification_id, idx, result)

    # save final aggregated results
    try:
        s3.put_object(
            Bucket=ML_DATA_BUCKET,
            Key=f"results/{verification_id}/audit_questions.json",
            Body=json.dumps(question_results, indent=2, default=str),
            ContentType="application/json",
        )
    except Exception as e:
        print(f"[WARN] could not save results to s3: {e}")

    return {"success": True, "question_results": question_results}


def run_fraud_summary(
    verification_id: str,
    extracted_text: str,
    signature_count: int,
    rekognition_result: dict,
    question_results: list[dict],
) -> dict:
    """
    Run final fraud assessment using all audit Q&A results.
    """
    if not extracted_text.strip():
        return {
            "success": False,
            "error": "No text extracted; cannot run fraud summary",
            "fraud_claim": "uncertain",
            "fraud_reasoning": "No text available for analysis",
        }

    seal_detected = bool(rekognition_result.get("seal_detected"))
    
    # Format Q&A results for the prompt
    qa_formatted = []
    for q in question_results:
        qa_formatted.append(f"Q: {q.get('question', '')}")
        qa_formatted.append(f"A: {q.get('answer', 'uncertain')} - {q.get('reasoning', '')}")
        qa_formatted.append("")  # Empty line for separation
    
    qa_results = "\n".join(qa_formatted)
    
    # Embed all context in the question input — Nova Pro receives this + KB retrieval
    fraud_question_input = (
        f"You are a nursing transcript fraud analyst.\n\n"
        f"Audit Q&A Summary:\n{qa_results[:2500]}\n\n"
        f"Signatures detected: {signature_count}. "
        f"Seal detected: {'Yes' if seal_detected else 'No'}.\n\n"
        f"Passed checks: {sum(1 for q in question_results if q.get('answer') == 'yes')}. "
        f"Failed checks: {sum(1 for q in question_results if q.get('answer') == 'no')}.\n\n"
        "Based on the audit results and retrieved reference data, is this nursing transcript fraudulent?\n"
        "Respond ONLY in JSON: {{\"claim\": \"yes|no|uncertain\", \"reasoning\": \"one line\"}}"
    )

    try:
        response = _bedrock_call(extracted_text, fraud_question_input)

        raw_output = response.get("output", {}).get("text", "")
        fraud_answer, fraud_reasoning = _parse_claim_reasoning(raw_output)
        print(f"[INFO] Fraud summary (Bedrock): {fraud_answer} - {fraud_reasoning[:100]}")

        result = {
            "success": True,
            "fraud_claim": fraud_answer,
            "fraud_reasoning": fraud_reasoning,
            "raw_response": raw_output,
        }

        try:
            s3.put_object(
                Bucket=ML_DATA_BUCKET,
                Key=f"results/{verification_id}/fraud_summary.json",
                Body=json.dumps(result, indent=2, default=str),
                ContentType="application/json",
            )
        except Exception as e:
            print(f"[WARN] Could not save fraud summary to S3: {e}")

        return result

    except Exception as e:
        print(f"[ERROR] Fraud summary Bedrock call failed: {e}")
        # tally-based fallback using per-question flag_on so polarity is respected
        flagged_c = sum(1 for q in question_results if q.get("answer") == q.get("flag_on", "no"))
        valid_c = sum(1 for q in question_results if q.get("answer") in {"yes", "no"})
        unc_c = len(question_results) - valid_c
        if valid_c > 0 and (flagged_c / valid_c) >= 0.5:
            claim = "yes"
            reason = f"{flagged_c} of {valid_c} answered questions indicated fraud"
        elif valid_c > 0 and (flagged_c / valid_c) < 0.25:
            claim = "no"
            reason = f"{valid_c - flagged_c} of {valid_c} answered questions passed"
        else:
            claim = "uncertain"
            reason = f"{flagged_c} flagged out of {valid_c} answered, {unc_c} uncertain or failed"
        return {
            "success": False,
            "error": str(e),
            "fraud_claim": claim,
            "fraud_reasoning": reason,
            "raw_response": "",
        }


def _build_ai_recommendation(
    fraud_claim: str,
    fraud_reasoning: str,
    fraud_score: float,
    yes_count: int,
    no_count: int,
    uncertain_count: int,
    parsing_failed_count: int,
) -> str:
    """Build final user-facing summary directly from fraud summary output."""
    total_checks = yes_count + no_count + uncertain_count + parsing_failed_count
    claim_text = "yes" if fraud_claim == "yes" else "no" if fraud_claim == "no" else "uncertain"
    base = f"Fraud {claim_text} for the following reasons: {fraud_reasoning}"
    metrics = (
        f" Score: {round(fraud_score * 100)}%. "
        f"Audit checks: yes={yes_count}, no={no_count}, uncertain={uncertain_count}, parsing_failed={parsing_failed_count}"
    )
    if total_checks > 0:
        return f"{base}. {metrics}"
    return base


def build_verification_summary(
    verification_id: str,
    textract_result: dict,
    rekognition_result: dict,
    audit_result: dict,
    extracted_data: dict,
    fraud_summary: dict,
) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    question_results = audit_result.get("question_results", []) if audit_result.get("success") else []
    flags: list[dict] = []

    for q in question_results:
        answer = str(q.get("answer", "uncertain")).lower()
        flag_on = q.get("flag_on", "no")
        # only flag when the model's answer matches the per-question fraud signal
        if answer == flag_on:
            flags.append(
                {
                    "flag_id": str(uuid.uuid4()),
                    "category": "fraud_indicator",
                    "question": q.get("question", ""),
                    "flag_on": flag_on,
                    "answer": answer,
                    "explanation": q.get("reasoning", ""),
                    "source_section": "audit_question",
                    "created_at": now,
                }
            )

    yes_count = sum(1 for q in question_results if str(q.get("answer", "")).lower() == "yes")
    no_count = sum(1 for q in question_results if str(q.get("answer", "")).lower() == "no")
    uncertain_count = sum(1 for q in question_results if str(q.get("answer", "")).lower() == "uncertain")
    parsing_failed_count = sum(1 for q in question_results if str(q.get("answer", "")).lower() == "parsing_failed")

    # flagged_count counts answers that match their per-question fraud signal
    flagged_count = len(flags)
    valid_yes_no = yes_count + no_count

    valid_confidences = [
        _safe_float(q.get("confidence", 0.0), 0.0)
        for q in question_results
        if str(q.get("answer", "")).lower() in {"yes", "no"} and q.get("confidence") is not None
    ]
    confidence_score = (sum(valid_confidences) / len(valid_confidences)) if valid_confidences else 0.0

    # default to 0.0 rather than 0.5 so ocr failures do not auto-flag as fraud
    qa_risk_score = (flagged_count / valid_yes_no) if valid_yes_no > 0 else 0.0
    fraud_score = min(1.0, max(0.0, qa_risk_score))

    fraud_claim = str(fraud_summary.get("fraud_claim", "uncertain")).lower()
    if fraud_claim == "yes":
        overall_status = "flagged"
        risk_level = "HIGH" if fraud_score >= 0.67 else "MEDIUM"
    elif fraud_claim == "no":
        overall_status = "cleared"
        risk_level = "LOW" if fraud_score < 0.34 else "MEDIUM"
    else:
        overall_status = "flagged" if fraud_score >= 0.5 else "cleared"
        risk_level = "MEDIUM"

    ai_recommendation = _build_ai_recommendation(
        fraud_claim=fraud_claim,
        fraud_reasoning=str(fraud_summary.get("fraud_reasoning", "")),
        fraud_score=fraud_score,
        yes_count=yes_count,
        no_count=no_count,
        uncertain_count=uncertain_count,
        parsing_failed_count=parsing_failed_count,
    )

    extracted_data_full = {
        **extracted_data,
        "raw_text": textract_result.get("full_text", ""),
        "signature_count": textract_result.get("signature_count", 0),
    }

    return {
        "summary_id": str(uuid.uuid4()),
        "verification_id": verification_id,
        "rules_applied": [],
        "flags": flags,
        "extracted_data": extracted_data_full,
        "overall_status": overall_status,
        "ai_recommendation": ai_recommendation,
        "fraud_score": round(fraud_score, 3),
        "risk_level": risk_level,
        "audit_question_results": question_results,
        "fraud_summary": {
            "claim": fraud_summary.get("fraud_claim", "uncertain"),
            "reasoning": fraud_summary.get("fraud_reasoning", ""),
            "success": fraud_summary.get("success", False),
            "confidence_score": round(confidence_score, 3),
        },
        "pipeline_meta": {
            "textract_success": textract_result.get("success"),
            "rekognition_success": rekognition_result.get("success"),
            "kb_audit_success": audit_result.get("success"),
            "fraud_summary_success": fraud_summary.get("success", False),
            "seal_detected": rekognition_result.get("seal_detected"),
            "line_count": textract_result.get("line_count", 0),
            "signature_count": textract_result.get("signature_count", 0),
            "question_count": len(question_results),
            "flagged_count": flagged_count,
            "parsing_failed_count": parsing_failed_count,
            "confidence_score": round(confidence_score, 3),
        },
        "created_at": now,
    }


def save_result_to_s3(verification_id: str, result: dict) -> None:
    try:
        s3.put_object(
            Bucket=ML_DATA_BUCKET,
            Key=f"results/{verification_id}/result.json",
            Body=json.dumps(result, indent=2, default=str),
            ContentType="application/json",
        )
    except Exception as e:
        print(f"[WARN] could not save result to s3: {e}")


def patch_fastapi(verification_id: str, summary: dict) -> bool:
    if not API_INTERNAL_URL:
        print("[WARN] API_INTERNAL_URL not set — skipping callback")
        return False
    if not API_CALLBACK_SECRET:
        print("[WARN] API_CALLBACK_SECRET not set — skipping callback")
        return False
    payload = json.dumps({"summary": summary, "status": summary["overall_status"]}).encode()
    url = f"{API_INTERNAL_URL.rstrip('/')}/api/v1/transcripts/{verification_id}/ml-result"
    max_attempts = 4
    for attempt in range(1, max_attempts + 1):
        try:
            req = urllib.request.Request(
                url,
                data=payload,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "X-Lambda-Secret": API_CALLBACK_SECRET,
                },
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                print(f"[INFO] FastAPI ml-result POST response: {resp.status} (attempt {attempt})")
                return resp.status in (200, 201, 204)
        except urllib.error.HTTPError as e:
            body = e.read()
            print(f"[ERROR] FastAPI ml-result POST HTTP error {e.code} (attempt {attempt}): {body}")
            # Retry only transient HTTP failures.
            if e.code not in (408, 429, 500, 502, 503, 504) or attempt == max_attempts:
                return False
            time.sleep(min(2 ** (attempt - 1), 8))
        except Exception as e:
            print(f"[ERROR] FastAPI ml-result POST failed (attempt {attempt}): {e}")
            if attempt == max_attempts:
                return False
            time.sleep(min(2 ** (attempt - 1), 8))
    return False


def lambda_handler(event: dict, context) -> dict:
    verification_id = event.get("verification_id")
    document_id = event.get("document_id")
    s3_key = event.get("s3_key")
    if not all([verification_id, document_id, s3_key]):
        return {"statusCode": 400, "error": "verification_id, document_id, and s3_key are required"}

    print(f"[INFO] starting pipeline for verification={verification_id}, s3_key={s3_key}")

    # step 1 — Textract OCR
    textract_result = run_textract(s3_key)
    print(
        "[INFO] textract extracted "
        f"{textract_result.get('line_count', 0)} lines and "
        f"{textract_result.get('signature_count', 0)} signatures"
    )

    # step 2 — Rekognition visual analysis
    rekognition_result = run_rekognition(s3_key)
    print(
        f"[INFO] rekognition seal_detected={rekognition_result.get('seal_detected')}, "
        f"labels={len(rekognition_result.get('labels', []))}"
    )

    # step 3 — basic structured data extraction for metadata
    extracted_data = extract_structured_data(textract_result.get("full_text", ""))
    print(
        f"[INFO] extracted institution={extracted_data.get('institution_name')!r} "
        f"degree={extracted_data.get('degree_awarded')!r} "
        f"confidence={extracted_data.get('extraction_confidence')}"
    )

    # step 4 — bedrock audit questions
    audit_result = run_audit_questions(
        verification_id,
        textract_result.get("full_text", ""),
        textract_result.get("tables_text", ""),
        textract_result.get("signature_count", 0),
        rekognition_result,
        extracted_data,
    )
    print(
        "[INFO] kb audit result "
        f"success={audit_result.get('success')} "
        f"questions={len(audit_result.get('question_results', []))}"
    )

    # step 5 — bedrock fraud summary
    fraud_summary = run_fraud_summary(
        verification_id,
        textract_result.get("full_text", ""),
        textract_result.get("signature_count", 0),
        rekognition_result,
        audit_result.get("question_results", []),
    )
    print(
        f"[INFO] fraud summary: {fraud_summary.get('fraud_claim')} - "
        f"{fraud_summary.get('fraud_reasoning', '')[:100]}"
    )

    # step 6 — build summary with final fraud output
    summary = build_verification_summary(
        verification_id,
        textract_result,
        rekognition_result,
        audit_result,
        extracted_data,
        fraud_summary,
    )
    print(
        f"[INFO] summary built status={summary['overall_status']} "
        f"flags={len(summary['flags'])} fraud_score={summary['fraud_score']}"
    )

    # step 7 — persist to S3
    save_result_to_s3(verification_id, summary)

    # step 8 — callback to FastAPI
    patched = patch_fastapi(verification_id, summary)
    print(f"[INFO] fastapi patch {'succeeded' if patched else 'failed or skipped'}")

    return {
        "statusCode": 200,
        "verification_id": verification_id,
        "document_id": document_id,
        "overall_status": summary["overall_status"],
        "fraud_score": summary["fraud_score"],
        "risk_level": summary["risk_level"],
        "flag_count": len(summary["flags"]),
        "institution": extracted_data.get("institution_name", ""),
        "degree": extracted_data.get("degree_awarded", ""),
        "patched_api": patched,
    }

