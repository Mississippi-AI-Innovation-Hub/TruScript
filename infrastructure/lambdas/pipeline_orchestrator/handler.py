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
bedrock_runtime = boto3.client("bedrock-runtime", region_name=_region)
s3 = boto3.client("s3", region_name=_region)

_REGION_DENY_ERRORS = ("us-east-2", "us-west-2", "AccessDeniedException", "explicit deny")

def _is_region_deny(exc: Exception) -> bool:
    """Return True if the error is a cross-region SCP block we should retry."""
    msg = str(exc)
    return any(kw in msg for kw in _REGION_DENY_ERRORS)


def _invoke_model_with_retry(body: str, max_attempts: int = 5) -> dict:
    """Invoke Bedrock model with retry on SCP/region-routing denials."""
    last_exc: Exception = RuntimeError("no attempts made")
    for attempt in range(1, max_attempts + 1):
        try:
            response = bedrock_runtime.invoke_model(
                modelId=KB_MODEL_ARN,
                body=body,
                contentType="application/json",
                accept="application/json",
            )
            return json.loads(response["body"].read())
        except ClientError as exc:
            last_exc = exc
            if _is_region_deny(exc):
                print(f"[WARN] InvokeModel routed to blocked region (attempt {attempt}), retrying…")
                time.sleep(0.5 * attempt)
            else:
                raise
    raise last_exc


def _retrieve_and_generate_with_retry(question: str, kb_id: str, model_arn: str,
                                       system_prompt: str, max_attempts: int = 5) -> dict:
    """retrieve_and_generate with retry on SCP/region-routing denials."""
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
                            "promptTemplate": {"textPromptTemplate": system_prompt}
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

# mappings
CATEGORY_MAP = {
    "accreditation": "accreditation",
    "graduation": "graduation",
    "program_completion": "program_completion",
    "document_integrity": "document_integrity",
    "fraud_indicator": "fraud_indicator",
    "formatting": "formatting",
}
SEVERITY_MAP = {"critical": "critical", "warning": "warning", "info": "info"}
RISK_WEIGHT_MAP = {"high": 1.0, "medium": 0.6, "low": 0.3}

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

DEFAULT_PROMPT = """
You are an expert in analyzing academic transcripts for authenticity and fraud detection.
You have access to a knowledge base of a manual, approved programs, and known fraud cases.
"""

QUESTION_PROMPT_TEMPLATE = """
Given the following transcript text and signature data, answer the audit question.
Respond with valid JSON only.

transcript text content:
{transcript_text}

signature count:
{signature_count}

detected seal in visual analysis:
{seal_detected}

rules:
{rules_block}

audit question:
{audit_question}

json schema:
{{
  "question": "{audit_question}",
  "answer": "<yes|no|uncertain>",
  "confidence": <0.0 to 1.0>,
  "risk_weight": "<low|medium|high>",
  "category": "<accreditation|graduation|program_completion|document_integrity|fraud_indicator|formatting>",
  "severity": "<critical|warning|info>",
  "rule_codes": ["<rule code>", "..."],
  "evidence": ["<quoted evidence snippet>", "..."],
  "reasoning": "<concise explanation>"
}}
"""

EXTRACTION_PROMPT_TEMPLATE = """
You are extracting structured information from an academic transcript for nursing licensure verification.

Transcript text:
{transcript_text}

Extract the following fields. Return ONLY valid JSON — no explanation, no markdown, no code fences.

{{
  "institution_name": "full legal name of the educational institution (empty string if not found)",
  "program_name": "full name of the nursing program e.g. Bachelor of Science in Nursing (empty string if not found)",
  "degree_awarded": "specific degree abbreviation e.g. BSN, ADN, MSN (empty string if not found)",
  "graduation_date": "graduation or degree conferral date e.g. May 2023 or 2023-05-15 (empty string if not found)",
  "graduation_confirmed": true or false based on whether the degree award is explicitly stated,
  "total_credits": total credit or semester hours as a number (0.0 if not found),
  "nursing_credits": nursing-specific credit hours as a number (0.0 if not found),
  "accreditation_type": "ACEN, CCNE, NLNAC, Regional, National, or empty string if not found",
  "accreditation_body": "full name of the accrediting body if mentioned (empty string if not found)",
  "extraction_confidence": confidence from 0.0 to 1.0 on how clearly the document states these values,
  "extraction_notes": "brief note on ambiguities or data quality issues"
}}
"""


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _normalize_question_result(raw_text: str, fallback_question: str) -> dict:
    try:
        parsed = json.loads(raw_text)
    except Exception:
        return {
            "question": fallback_question,
            "answer": "uncertain",
            "confidence": 0.0,
            "risk_weight": "medium",
            "category": "fraud_indicator",
            "severity": "warning",
            "rule_codes": [],
            "evidence": [],
            "reasoning": f"unparseable model output: {raw_text[:500]}",
        }

    return {
        "question": parsed.get("question", fallback_question),
        "answer": str(parsed.get("answer", "uncertain")).lower(),
        "confidence": max(0.0, min(1.0, _safe_float(parsed.get("confidence", 0.0), 0.0))),
        "risk_weight": str(parsed.get("risk_weight", "medium")).lower(),
        "category": str(parsed.get("category", "fraud_indicator")).lower(),
        "severity": str(parsed.get("severity", "warning")).lower(),
        "rule_codes": parsed.get("rule_codes", []) or [],
        "evidence": parsed.get("evidence", []) or [],
        "reasoning": str(parsed.get("reasoning", "")),
    }


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


# ── Regex-based structured extraction (no Bedrock required) ───────────────────

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
_NURSING_PREFIXES = re.compile(
    r"\b(NUR|NURS|NSG|NRSG|RN|PN|LPN|BSNC)\s*\d", re.IGNORECASE
)


def extract_structured_data(transcript_text: str) -> dict:
    """
    Extract structured fields from Textract raw text using regex patterns.
    No Bedrock / cross-region invocation required.
    """
    if not transcript_text.strip():
        return _empty_extracted_data("no text extracted from document")

    text = transcript_text
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    confidence_hits = 0
    confidence_total = 8  # number of fields we attempt to extract

    # ── Institution name (first non-empty line) ───────────────────────────────
    institution_name = lines[0] if lines else ""
    if institution_name:
        confidence_hits += 1

    # ── Degree and program ────────────────────────────────────────────────────
    degree_awarded = ""
    program_name = ""
    for pattern, abbr, full_name in _DEGREE_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            degree_awarded = abbr
            program_name = full_name
            confidence_hits += 1
            break

    # ── Graduation / conferral date ───────────────────────────────────────────
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

    # ── Total credits (cumulative earned hours) ───────────────────────────────
    total_credits = 0.0
    cum_matches = re.findall(
        r"CUM[:\s]+[\d.]+\s+[\d.]+\s+([\d.]+)", text, re.IGNORECASE
    )
    if cum_matches:
        try:
            total_credits = max(float(v) for v in cum_matches)
            if total_credits > 0:
                confidence_hits += 1
        except ValueError:
            pass

    # If no CUM pattern, look for "Total Credits" or similar
    if total_credits == 0:
        m = re.search(r"total\s+(?:credit|semester)\s+hours?[:\s]+([\d.]+)", text, re.IGNORECASE)
        if m:
            total_credits = _safe_float(m.group(1))
            if total_credits > 0:
                confidence_hits += 1

    # ── Nursing credits (courses with nursing prefixes) ───────────────────────
    nursing_credits = 0.0
    for m in re.finditer(
        r"\b(?:NUR|NURS|NSG|NRSG)\s*\d+\b.*?([\d.]+)\s+(?:P|A|B|C|D|F|W|CR|NC|\d\.\d+)",
        text, re.IGNORECASE,
    ):
        try:
            nursing_credits += float(m.group(1))
        except ValueError:
            pass
    if nursing_credits > 0:
        confidence_hits += 1

    # ── Accreditation ─────────────────────────────────────────────────────────
    accreditation_type = ""
    accreditation_body = ""
    for pattern, acc_type, acc_body in _ACCREDITATION_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            accreditation_type = acc_type
            accreditation_body = acc_body
            confidence_hits += 1
            break

    # ── GPA present (bonus confidence signal) ────────────────────────────────
    if re.search(r"\bGPA\b|\b[0-4]\.\d{3}\b", text):
        confidence_hits += 1

    extraction_confidence = round(confidence_hits / confidence_total, 2)
    notes = (
        f"Regex extraction: {confidence_hits}/{confidence_total} fields found. "
        f"Institution: {institution_name or 'not found'}. "
        f"Degree: {degree_awarded or 'not found'}."
    )

    print(f"[INFO] extracted institution='{institution_name}' degree='{degree_awarded}' "
          f"confidence={extraction_confidence}")

    return {
        "institution_name": institution_name,
        "program_name": program_name,
        "degree_awarded": degree_awarded,
        "graduation_date": graduation_date,
        "graduation_confirmed": graduation_confirmed,
        "total_credits": total_credits,
        "nursing_credits": round(nursing_credits, 1),
        "accreditation_type": accreditation_type,
        "accreditation_body": accreditation_body,
        "extraction_confidence": extraction_confidence,
        "extraction_notes": notes,
    }


# ── Rule-based audit question evaluator (no Bedrock required) ─────────────────

def _evaluate_audit_question(question: str, transcript_text: str,
                              signature_count: int, seal_detected: bool,
                              rules: list[dict], extracted: dict) -> dict:
    """Evaluate a single audit question using deterministic text analysis."""
    q = question.lower()
    text = transcript_text.lower()

    answer = "uncertain"
    confidence = 0.4
    evidence = []
    reasoning = ""
    risk_weight = "medium"
    category = "fraud_indicator"
    severity = "warning"
    rule_codes: list[str] = []

    # Map question keywords to deterministic checks
    if "overall gpa" in q or "gpa" in q:
        category = "accreditation"
        if re.search(r"\bgpa\b|\b[0-4]\.\d{3}\b", transcript_text, re.IGNORECASE):
            answer, confidence = "yes", 0.9
            reasoning = "GPA value detected in transcript."
            evidence = [m.group() for m in re.finditer(r"[0-4]\.\d{3}", transcript_text)][:3]
        else:
            answer, confidence = "no", 0.8
            reasoning = "No GPA value found in transcript text."

    elif "semester hours" in q or "credit hours" in q or "clock hours" in q:
        category = "program_completion"
        if re.search(r"\b\d+\.\d+\s+(?:credit|semester|clock)?\s*hours?\b|\b(?:ATT|ERN|HRS)\b", transcript_text, re.IGNORECASE):
            answer, confidence = "yes", 0.85
            reasoning = "Credit/semester hour data found in transcript."
        else:
            answer, confidence = "uncertain", 0.5
            reasoning = "Could not confirm credit hour presentation."

    elif "school official" in q or "signature" in q and "title" in q:
        category = "document_integrity"
        severity = "warning"
        if signature_count > 0:
            answer, confidence = "yes", 0.7
            reasoning = f"Document contains {signature_count} signature(s)."
            evidence = [f"{signature_count} signature(s) detected"]
        else:
            answer, confidence = "uncertain", 0.5
            reasoning = "Signature presence could not be confirmed from text extraction."

    elif "signature consistent" in q:
        category = "document_integrity"
        severity = "warning"
        answer, confidence = "uncertain", 0.3
        reasoning = "Signature consistency requires visual comparison not available in text."

    elif "seal" in q or "embossed" in q or "stamped" in q:
        category = "document_integrity"
        severity = "warning"
        if seal_detected:
            answer, confidence = "yes", 0.8
            reasoning = "Institutional seal detected in document."
            evidence = ["seal detected by visual analysis"]
        else:
            answer, confidence = "uncertain", 0.4
            reasoning = "Seal detection requires visual analysis; not confirmed."

    elif "program dates" in q or "enrollment length" in q or "internal conflicts" in q:
        category = "document_integrity"
        terms = re.findall(r"\b(?:FA|SP|SU|WI)-\d{2}\b", transcript_text, re.IGNORECASE)
        if len(terms) > 1:
            answer, confidence = "yes", 0.75
            reasoning = f"Multiple terms found ({', '.join(terms[:5])}); no obvious overlaps detected."
            evidence = terms[:5]
        else:
            answer, confidence = "uncertain", 0.4
            reasoning = "Could not verify program date consistency."

    elif "nursing subjects" in q or "course list" in q:
        category = "program_completion"
        nur_courses = re.findall(r"\b(?:NUR|NURS|NSG)\s*\d+", transcript_text, re.IGNORECASE)
        if nur_courses:
            answer, confidence = "yes", 0.8
            reasoning = f"Found {len(nur_courses)} nursing course(s)."
            evidence = list(set(nur_courses))[:5]
        else:
            answer, confidence = "uncertain", 0.5
            reasoning = "No nursing-specific course codes found."

    elif "diploma mill" in q or "life experience" in q or "no study" in q:
        category = "fraud_indicator"
        severity = "warning"
        risk_weight = "high"
        mill_keywords = ["diploma mill", "life experience", "no study", "guaranteed degree", "instant degree"]
        found = [kw for kw in mill_keywords if kw in text]
        if found:
            answer, confidence = "no", 0.9
            reasoning = f"Diploma mill indicators found: {found}"
            evidence = found
        else:
            answer, confidence = "yes", 0.7
            reasoning = "No diploma mill language detected in transcript."

    elif "prior learning" in q or "transferred" in q and "fraudulent" in q:
        category = "fraud_indicator"
        if "transfer" in text:
            answer, confidence = "uncertain", 0.5
            reasoning = "Transfer credits present; source authenticity cannot be auto-verified."
            evidence = ["Transfer credits detected"]
        else:
            answer, confidence = "yes", 0.7
            reasoning = "No transferred credits detected."

    elif "attendance duration" in q or "minimum required" in q:
        category = "accreditation"
        if extracted.get("total_credits", 0) >= 60:
            answer, confidence = "yes", 0.75
            reasoning = f"Total credits ({extracted.get('total_credits')}) meets typical minimum."
        elif extracted.get("total_credits", 0) > 0:
            answer, confidence = "uncertain", 0.5
            reasoning = f"Total credits ({extracted.get('total_credits')}) found; minimum threshold varies by degree."
        else:
            answer, confidence = "uncertain", 0.3
            reasoning = "Could not determine total credit hours."

    elif "sum of individual" in q or "total credit hours" in q:
        category = "document_integrity"
        if extracted.get("total_credits", 0) > 0:
            answer, confidence = "uncertain", 0.5
            reasoning = "Credit totals found; cross-sum verification requires full course list parsing."
        else:
            answer, confidence = "uncertain", 0.3
            reasoning = "Credit data insufficient for sum verification."

    elif "accredited" in q:
        category = "accreditation"
        severity = "critical"
        risk_weight = "high"
        rule_codes = ["ACCREDITATION_001"]
        acc_keywords = ["accredited", "acen", "ccne", "nlnac", "accreditation", "regional"]
        found = [kw for kw in acc_keywords if kw in text]
        if found:
            answer, confidence = "yes", 0.75
            reasoning = f"Accreditation-related terms found: {found}"
            evidence = found
        else:
            answer, confidence = "uncertain", 0.4
            reasoning = "No accreditation keywords found in transcript."

    elif "diploma mill" in q or "med life" in q or "ideal" in q or "known" in q and "list" in q:
        category = "fraud_indicator"
        severity = "warning"
        risk_weight = "high"
        answer, confidence = "uncertain", 0.4
        reasoning = "School name check against diploma mill lists requires external database lookup."

    elif "sent directly" in q or "institution rather than" in q:
        category = "document_integrity"
        answer, confidence = "uncertain", 0.3
        reasoning = "Document chain-of-custody cannot be determined from transcript text."

    elif "paper size" in q or "a4" in q:
        category = "formatting"
        answer, confidence = "uncertain", 0.3
        reasoning = "Physical paper dimensions cannot be determined from digital text extraction."

    elif "grading scale" in q or "grading format" in q or "country of study" in q:
        category = "formatting"
        if re.search(r"\b[ABCDF]\b|\bP\b|\bCR\b|\bNC\b", transcript_text):
            answer, confidence = "yes", 0.75
            reasoning = "Standard letter/pass-fail grading format detected."
            evidence = ["Standard grading scale found"]
        else:
            answer, confidence = "uncertain", 0.4
            reasoning = "Grading format could not be confirmed."

    elif "resident" in q or "state where" in q:
        category = "fraud_indicator"
        answer, confidence = "uncertain", 0.3
        reasoning = "Applicant residency verification requires external data."

    elif "overlaps" in q or "attendance dates" in q:
        category = "fraud_indicator"
        answer, confidence = "uncertain", 0.4
        reasoning = "Enrollment overlap detection requires cross-institution records."

    elif "physical anomalies" in q or "pixelated" in q:
        category = "document_integrity"
        answer, confidence = "uncertain", 0.3
        reasoning = "Physical document anomaly detection requires visual inspection."

    elif "course codes" in q or "institutional catalog" in q:
        category = "accreditation"
        nur_courses = re.findall(r"\b(?:NUR|NURS|NSG|BIO|ENG|MAT|PSY|SOC)\s*\d+", transcript_text, re.IGNORECASE)
        if nur_courses:
            answer, confidence = "uncertain", 0.5
            reasoning = f"Found {len(nur_courses)} course codes; catalog verification requires external lookup."
            evidence = list(set(nur_courses))[:5]
        else:
            answer, confidence = "uncertain", 0.3
            reasoning = "No standard course codes found."

    return {
        "question": question,
        "answer": answer,
        "confidence": confidence,
        "risk_weight": risk_weight,
        "category": category,
        "severity": severity,
        "rule_codes": rule_codes,
        "evidence": evidence,
        "reasoning": reasoning,
    }




def load_rules() -> list[dict]:
    try:
        obj = s3.get_object(Bucket=ML_DATA_BUCKET, Key="rules/fraud_rules.json")
        data = json.loads(obj["Body"].read())
        return data.get("rules", [])
    except Exception as e:
        print(f"[WARN] could not load rules from s3: {e}")
        return []


def run_textract(s3_key: str) -> dict:
    try:
        resp = textract.analyze_document(
            Document={"S3Object": {"Bucket": DOCUMENTS_BUCKET, "Name": s3_key}},
            FeatureTypes=["SIGNATURES", "LAYOUT"],
        )
        blocks = resp.get("Blocks", [])
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
        }


def run_rekognition(s3_key: str) -> dict:
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
    rules: list[dict],
    extracted_data: dict | None = None,
) -> dict:
    if not extracted_text.strip():
        return {
            "success": False,
            "error": "No text extracted cannot run audit",
            "question_results": [],
        }

    seal_detected = bool(rekognition_result.get("seal_detected"))
    extracted = extracted_data or {}

    question_results: list[dict] = []
    for idx, question in enumerate(AUDIT_QUESTIONS, start=1):
        result = _evaluate_audit_question(
            question=question,
            transcript_text=extracted_text,
            signature_count=signature_count,
            seal_detected=seal_detected,
            rules=rules,
            extracted=extracted,
        )
        result["question_index"] = idx
        question_results.append(result)
        save_question_result_to_s3(verification_id, idx, result)

    try:
        s3.put_object(
            Bucket=ML_DATA_BUCKET,
            Key=f"results/{verification_id}/audit_questions.json",
            Body=json.dumps(question_results, indent=2, default=str),
            ContentType="application/json",
        )
    except Exception as e:
        print(f"[WARN] could not save full audit results to s3: {e}")

    return {"success": True, "question_results": question_results}


def _build_ai_recommendation(
    extracted_data: dict,
    flags: list[dict],
    fraud_score: float,
    risk_level: str,
    yes_count: int,
    no_count: int,
    uncertain_count: int,
) -> str:
    """Build a human-readable AI recommendation from real audit results."""
    institution = extracted_data.get("institution_name") or "the institution"
    degree = extracted_data.get("degree_awarded") or "the degree"
    accred = extracted_data.get("accreditation_type") or ""
    graduation = extracted_data.get("graduation_date") or ""

    critical_flags = [f for f in flags if f.get("severity") == "critical"]
    warning_flags = [f for f in flags if f.get("severity") == "warning"]
    total_checks = yes_count + no_count + uncertain_count

    if risk_level == "HIGH":
        rec = (
            f"HIGH RISK — {len(critical_flags)} critical issue(s) detected "
            f"(fraud score {round(fraud_score * 100)}%). "
        )
        if critical_flags:
            issues = "; ".join(
                f["rule_description"] for f in critical_flags[:3]
            )
            rec += f"Critical findings: {issues}. "
        rec += "Immediate manual review required before any licensure action."

    elif risk_level == "MEDIUM":
        rec = (
            f"MEDIUM RISK — {len(warning_flags)} warning(s) require staff review "
            f"(fraud score {round(fraud_score * 100)}%). "
        )
        if warning_flags:
            issues = "; ".join(
                f["rule_description"] for f in warning_flags[:2]
            )
            rec += f"Review items: {issues}. "
        rec += "Manual review recommended before approval."

    else:  # LOW
        parts = []
        if institution and institution != "the institution":
            parts.append(f"{institution}")
        if degree and degree != "the degree":
            parts.append(f"{degree}")
        if accred:
            parts.append(f"{accred} accredited")
        if graduation:
            parts.append(f"graduation {graduation}")

        detail = ". ".join(parts) if parts else "Transcript reviewed"
        rec = (
            f"LOW RISK — {detail}. "
            f"No significant fraud indicators detected (fraud score {round(fraud_score * 100)}%). "
            "Recommend approval pending standard staff review."
        )

    if total_checks > 0:
        rec += (
            f" Audit: {yes_count}/{total_checks} checks passed, "
            f"{no_count} failed, {uncertain_count} uncertain."
        )

    return rec


def build_verification_summary(
    verification_id: str,
    textract_result: dict,
    rekognition_result: dict,
    audit_result: dict,
    rules: list[dict],
    extracted_data: dict,
) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    question_results = audit_result.get("question_results", []) if audit_result.get("success") else []
    rule_lookup = {r.get("code"): r for r in rules}
    flags: list[dict] = []
    weighted_sum = 0.0
    weight_total = 0.0

    for q in question_results:
        answer = str(q.get("answer", "uncertain")).lower()
        confidence = _safe_float(q.get("confidence", 0.0), 0.0)
        risk_weight = RISK_WEIGHT_MAP.get(str(q.get("risk_weight", "medium")).lower(), 0.6)
        weight_total += risk_weight

        if answer == "no":
            weighted_sum += 1.0 * risk_weight * max(0.4, confidence)
        elif answer == "uncertain":
            weighted_sum += 0.6 * risk_weight * max(0.3, confidence)

        if answer in {"no", "uncertain"}:
            rule_codes = q.get("rule_codes", []) or []
            first_rule = rule_lookup.get(rule_codes[0], {}) if rule_codes else {}
            flags.append(
                {
                    "flag_id": str(uuid.uuid4()),
                    "category": CATEGORY_MAP.get(
                        str(q.get("category", "fraud_indicator")).lower(),
                        "fraud_indicator",
                    ),
                    "severity": SEVERITY_MAP.get(str(q.get("severity", "warning")).lower(), "warning"),
                    "rule_code": rule_codes[0] if rule_codes else "",
                    "rule_description": first_rule.get("description", q.get("question", "")),
                    "evidence": "\n".join(q.get("evidence", [])[:3]) if isinstance(q.get("evidence"), list) else "",
                    "explanation": q.get("reasoning", ""),
                    "source_section": "audit_question",
                    "created_at": now,
                }
            )

    existing_codes = {f.get("rule_code") for f in flags}
    if not rekognition_result.get("seal_detected") and "INTEGRITY_001" not in existing_codes:
        rules_entry = rule_lookup.get("INTEGRITY_001", {})
        flags.append(
            {
                "flag_id": str(uuid.uuid4()),
                "category": "document_integrity",
                "severity": "critical",
                "rule_code": "INTEGRITY_001",
                "rule_description": rules_entry.get(
                    "description",
                    "Official institutional seal or stamp must be present",
                ),
                "evidence": "",
                "explanation": "rekognition found no institutional seal or stamp",
                "source_section": "document_header",
                "created_at": now,
            }
        )

    question_risk = (weighted_sum / weight_total) if weight_total > 0 else 0.5
    seal_penalty = 0.2 if not rekognition_result.get("seal_detected") else 0.0
    text_penalty = 0.1 if textract_result.get("line_count", 0) < 5 else 0.0
    fraud_score = min(1.0, question_risk + seal_penalty + text_penalty)

    if fraud_score >= 0.75:
        risk_level = "HIGH"
    elif fraud_score >= 0.4:
        risk_level = "MEDIUM"
    else:
        risk_level = "LOW"

    yes_count = len([q for q in question_results if str(q.get("answer", "")).lower() == "yes"])
    no_count = len([q for q in question_results if str(q.get("answer", "")).lower() == "no"])
    uncertain_count = len([q for q in question_results if str(q.get("answer", "")).lower() == "uncertain"])

    ai_recommendation = _build_ai_recommendation(
        extracted_data=extracted_data,
        flags=flags,
        fraud_score=fraud_score,
        risk_level=risk_level,
        yes_count=yes_count,
        no_count=no_count,
        uncertain_count=uncertain_count,
    )

    has_critical = any(f["severity"] == "critical" for f in flags)
    has_warnings = any(f["severity"] == "warning" for f in flags)
    if has_critical or fraud_score >= 0.75:
        overall_status = "flagged"
    elif has_warnings or fraud_score >= 0.4:
        overall_status = "flagged"
    else:
        overall_status = "cleared"

    # Attach raw_text and signature_count into extracted_data for storage
    extracted_data_full = {
        **extracted_data,
        "raw_text": textract_result.get("full_text", "")[:2000],
        "signature_count": textract_result.get("signature_count", 0),
    }

    return {
        "summary_id": str(uuid.uuid4()),
        "verification_id": verification_id,
        "rules_applied": [r["code"] for r in rules if "code" in r],
        "flags": flags,
        "extracted_data": extracted_data_full,
        "overall_status": overall_status,
        "ai_recommendation": ai_recommendation,
        "fraud_score": round(fraud_score, 3),
        "risk_level": risk_level,
        "audit_question_results": question_results,
        "pipeline_meta": {
            "textract_success": textract_result.get("success"),
            "rekognition_success": rekognition_result.get("success"),
            "kb_audit_success": audit_result.get("success"),
            "seal_detected": rekognition_result.get("seal_detected"),
            "line_count": textract_result.get("line_count", 0),
            "signature_count": textract_result.get("signature_count", 0),
            "question_count": len(question_results),
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
    try:
        payload = json.dumps({"summary": summary, "status": summary["overall_status"]}).encode()
        url = f"{API_INTERNAL_URL.rstrip('/')}/api/v1/transcripts/{verification_id}/ml-result"
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
            print(f"[INFO] FastAPI ml-result POST response: {resp.status}")
            return resp.status in (200, 201, 204)
    except urllib.error.HTTPError as e:
        print(f"[ERROR] FastAPI ml-result POST HTTP error {e.code}: {e.read()}")
    except Exception as e:
        print(f"[ERROR] FastAPI ml-result POST failed: {e}")
    return False


def lambda_handler(event: dict, context) -> dict:
    verification_id = event.get("verification_id")
    document_id = event.get("document_id")
    s3_key = event.get("s3_key")
    if not all([verification_id, document_id, s3_key]):
        return {"statusCode": 400, "error": "verification_id, document_id, and s3_key are required"}

    print(f"[INFO] starting pipeline for verification={verification_id}, s3_key={s3_key}")

    # step 1 — load fraud rules
    rules = load_rules()
    print(f"[INFO] loaded {len(rules)} fraud detection rules")

    # step 2 — Textract OCR
    textract_result = run_textract(s3_key)
    print(
        "[INFO] textract extracted "
        f"{textract_result.get('line_count', 0)} lines and "
        f"{textract_result.get('signature_count', 0)} signatures"
    )

    # step 3 — Rekognition visual analysis
    rekognition_result = run_rekognition(s3_key)
    print(
        f"[INFO] rekognition seal_detected={rekognition_result.get('seal_detected')}, "
        f"labels={len(rekognition_result.get('labels', []))}"
    )

    # step 4 — structured data extraction from OCR text (regex-based)
    extracted_data = extract_structured_data(textract_result.get("full_text", ""))
    print(
        f"[INFO] extracted institution={extracted_data.get('institution_name')!r} "
        f"degree={extracted_data.get('degree_awarded')!r} "
        f"confidence={extracted_data.get('extraction_confidence')}"
    )

    # step 5 — rule-based audit questions
    audit_result = run_audit_questions(
        verification_id,
        textract_result.get("full_text", ""),
        textract_result.get("signature_count", 0),
        rekognition_result,
        rules,
        extracted_data=extracted_data,
    )
    print(
        "[INFO] kb audit result "
        f"success={audit_result.get('success')} "
        f"questions={len(audit_result.get('question_results', []))}"
    )

    # step 6 — build summary with real extracted data
    summary = build_verification_summary(
        verification_id,
        textract_result,
        rekognition_result,
        audit_result,
        rules,
        extracted_data,
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
