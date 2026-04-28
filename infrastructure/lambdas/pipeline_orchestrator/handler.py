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

# aws clients
_region = os.environ["AWS_REGION_NAME"]
textract = boto3.client("textract", region_name=_region)
rekognition = boto3.client("rekognition", region_name=_region)
bedrock_agent = boto3.client("bedrock-agent-runtime", region_name=_region)
s3 = boto3.client("s3", region_name=_region)

_REGION_DENY_ERRORS = ("us-east-2", "us-west-2", "AccessDeniedException", "explicit deny")

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

# config
DOCUMENTS_BUCKET = os.environ["S3_DOCUMENTS_BUCKET"]
ML_DATA_BUCKET = os.environ["S3_ML_DATA_BUCKET"]
KB_ID = os.environ["BEDROCK_KB_ID"]
KB_MODEL_ARN = os.environ["BEDROCK_KB_MODEL_ARN"]
API_INTERNAL_URL = os.environ.get("API_INTERNAL_URL", "")
API_CALLBACK_SECRET = os.environ.get("API_CALLBACK_SECRET", "")
REKOGNITION_PROJECT_ARN = os.environ.get("REKOGNITION_PROJECT_VERSION_ARN", "")

AUDIT_QUESTIONS = [
    "does the transcript display an overall gpa",
    "are all courses listed with specific designated semester hours",
    "is the presentation of clock hours or credit hours clear and unambiguous",
    "is a specific title provided for the school official who signed the document",
    "do the program dates or enrollment length statements contain any internal conflicts",
    "is the signature consistent with known official signatures from the institution",
    "is the school seal embossed rather than stamped if that is the institutional standard",
    "does the course list exclude suspicious or non_nursing subjects for the program",
    "does the institution avoid advertising degrees based on no study or life experience",
    "are prior learning credits granted in a manner inconsistent with diploma mills",
    "is the attendance duration at least the minimum required for the degree type",
    "does the sum of individual course credits match the reported total credit hours",
    "was the applicant a resident of the state where the institution is located",
    "are attendance dates free of overlaps with enrollment at other distant schools",
    "does the document avoid physical anomalies like pixelated logos or atypical paper",
    "do the course codes and titles exist in the official institutional catalog",
    "is the institution accredited by an approved body",
    "does the transcript avoid credits transferred from known fraudulent sources",
    "is the school name absent from known diploma mill lists like med life or ideal",
    "was the document sent directly from the institution rather than the applicant",
    "does the paper size match the standard for the country of origin such as a4",
    "are the grading scales and formats typical for the purported country of study",
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

    print(f"[INFO] Frontend extraction: institution='{institution_name}' degree='{degree_awarded}' confidence={extraction_confidence}")

    return {
        "institution_name": institution_name,
        "program_name": program_name,
        "degree_awarded": degree_awarded,
        "graduation_date": graduation_date,
        "graduation_confirmed": graduation_confirmed,
        "total_credits": total_credits,
        "nursing_credits": 0.0,  # Could add nursing credit extraction if needed
        "accreditation_type": accreditation_type,
        "accreditation_body": accreditation_body,
        "extraction_confidence": extraction_confidence,
        "extraction_notes": notes,
    }



def run_textract(s3_key: str) -> dict:
    """
    Textract:
    - Images: synchronous AnalyzeDocument
    - PDFs: asynchronous StartDocumentAnalysis + GetDocumentAnalysis polling
    """
    is_pdf = s3_key.lower().endswith(".pdf")
    try:
        if not is_pdf:
            resp = textract.analyze_document(
                Document={"S3Object": {"Bucket": DOCUMENTS_BUCKET, "Name": s3_key}},
                FeatureTypes=["SIGNATURES", "LAYOUT"],
            )
            blocks = resp.get("Blocks", [])
        else:
            start = textract.start_document_analysis(
                DocumentLocation={"S3Object": {"Bucket": DOCUMENTS_BUCKET, "Name": s3_key}},
                FeatureTypes=["SIGNATURES", "LAYOUT"],
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

        lines = [b["Text"] for b in blocks if b.get("BlockType") == "LINE" and "Text" in b]
        words = [b["Text"] for b in blocks if b.get("BlockType") == "WORD" and "Text" in b]
        signatures = [
            {
                "confidence": round(_safe_float(b.get("Confidence", 0.0), 0.0), 3),
                "bounding_box": b.get("Geometry", {}).get("BoundingBox", {}),
            }
            for b in blocks
            if b.get("BlockType") == "SIGNATURE"
        ]
        return {
            "success": True,
            "full_text": "\n".join(lines),
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


def run_audit_questions(
    verification_id: str,
    extracted_text: str,
    signature_count: int,
    rekognition_result: dict,
) -> dict:
    """
    Executes audit questions using a strict fine-tuning prompt style 
    and returns only claim and reasoning.
    """
    if not extracted_text.strip():
        return {
            "success": False,
            "error": "No text extracted; cannot run audit",
            "question_results": [],
        }

    seal_detected = bool(rekognition_result.get("seal_detected"))

    question_results: list[dict] = []
    
    for idx, question in enumerate(AUDIT_QUESTIONS, start=1):
        # Embed transcript context in input.text — generationConfiguration forces JSON format
        audit_question_input = (
            f"Transcript excerpt:\n{extracted_text[:1500]}\n\n"
            f"Signatures detected: {signature_count}. "
            f"Seal detected: {'Yes' if seal_detected else 'No'}.\n\n"
            f"Audit question: {question}"
        )

        try:
            # Call Bedrock RetrieveAndGenerate (JSON output enforced by generationConfiguration)
            response = _retrieve_and_generate_with_retry(
                question=audit_question_input,
                kb_id=KB_ID,
                model_arn=KB_MODEL_ARN,
            )

            raw_output = response.get("output", {}).get("text", "")

            # Parse — JSON first, then natural language fallback
            answer, reasoning = _parse_claim_reasoning(raw_output)
            # uncertain is a valid answer (not a parse failure)
            parsing_failed = False
            confidence = 0.8 if answer in {"yes", "no"} else 0.5
            print(f"[INFO] Q{idx}: {answer} — {reasoning[:80]}")

            result = {
                "question_index": idx,
                "question": question,
                "answer": answer,
                "reasoning": reasoning,
                "raw_response": raw_output,
                "confidence": confidence,
                "parsing_failed": parsing_failed,
            }

        except Exception as e:
            print(f"[ERROR] Bedrock call failed for question {idx}: {e}")
            result = {
                "question_index": idx,
                "question": question,
                "answer": "parsing_failed",
                "reasoning": f"Inference error: {str(e)[:150]}",
                "raw_response": "",
                "confidence": None,
                "parsing_failed": True,
            }

        question_results.append(result)
        save_question_result_to_s3(verification_id, idx, result)

    # Save final aggregated results
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
        response = _retrieve_and_generate_with_retry(
            question=fraud_question_input,
            kb_id=KB_ID,
            model_arn=KB_MODEL_ARN,
        )

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
        # Fallback: tally-based assessment from audit results
        yes_c = sum(1 for q in question_results if q.get("answer") == "yes")
        no_c  = sum(1 for q in question_results if q.get("answer") == "no")
        unc_c = len(question_results) - yes_c - no_c
        if no_c >= 3:
            claim, reason = "yes", f"{no_c} audit checks failed — likely fraudulent"
        elif no_c == 0 and yes_c >= len(question_results) * 0.5:
            claim, reason = "no", f"{yes_c}/{len(question_results)} checks passed"
        else:
            claim, reason = "uncertain", f"{yes_c} passed, {no_c} failed, {unc_c} uncertain"
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
        if answer in {"no", "uncertain", "parsing_failed"}:
            flags.append(
                {
                    "flag_id": str(uuid.uuid4()),
                    "category": "fraud_indicator",
                    "question": q.get("question", ""),
                    "answer": answer,
                    "explanation": q.get("reasoning", ""),
                    "source_section": "audit_question",
                    "created_at": now,
                }
            )

    rekognition_skipped = bool(rekognition_result.get("skipped"))
    seal_detected = bool(rekognition_result.get("seal_detected"))

    if not rekognition_skipped and not seal_detected:
        flags.append(
            {
                "flag_id": str(uuid.uuid4()),
                "category": "document_integrity",
                "question": "Is institutional seal present?",
                "answer": "no",
                "explanation": "rekognition found no institutional seal or stamp",
                "source_section": "document_header",
                "created_at": now,
            }
        )

    yes_count = len([q for q in question_results if str(q.get("answer", "")).lower() == "yes"])
    no_count = len([q for q in question_results if str(q.get("answer", "")).lower() == "no"])
    uncertain_count = len([q for q in question_results if str(q.get("answer", "")).lower() == "uncertain"])
    parsing_failed_count = len(
        [q for q in question_results if str(q.get("answer", "")).lower() == "parsing_failed"]
    )

    valid_confidences = [
        _safe_float(q.get("confidence", 0.0), 0.0)
        for q in question_results
        if str(q.get("answer", "")).lower() in {"yes", "no"} and q.get("confidence") is not None
    ]
    confidence_score = (sum(valid_confidences) / len(valid_confidences)) if valid_confidences else 0.0

    valid_yes_no = yes_count + no_count
    qa_risk_score = (no_count / valid_yes_no) if valid_yes_no > 0 else 0.5
    # Apply seal penalty only if Rekognition actually ran (not skipped for PDFs).
    seal_penalty = 0.1 if (not rekognition_skipped and not seal_detected) else 0.0
    fraud_score = min(1.0, max(0.0, qa_risk_score + seal_penalty))

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

    # Attach raw_text and signature_count into extracted_data for storage
    extracted_data_full = {
        **extracted_data,
        "raw_text": textract_result.get("full_text", ""),  # No truncation - keep full text
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
        textract_result.get("signature_count", 0),
        rekognition_result,
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
