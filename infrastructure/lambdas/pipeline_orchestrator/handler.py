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

import boto3

# aws clients
_region = os.environ["AWS_REGION_NAME"]
textract = boto3.client("textract", region_name=_region)
rekognition = boto3.client("rekognition", region_name=_region)
bedrock_agent = boto3.client("bedrock-agent-runtime", region_name=_region)
bedrock_runtime = boto3.client("bedrock-runtime", region_name=_region)
s3 = boto3.client("s3", region_name=_region)

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


def extract_structured_data(transcript_text: str) -> dict:
    """
    Uses Bedrock (Claude) to extract structured fields from raw Textract text.
    Returns a dict matching the ExtractedTranscriptData schema.
    """
    if not transcript_text.strip():
        return _empty_extracted_data("no text extracted from document")

    try:
        prompt = EXTRACTION_PROMPT_TEMPLATE.format(
            transcript_text=transcript_text[:12000]
        )
        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 1024,
            "temperature": 0.0,
            "messages": [{"role": "user", "content": prompt}],
        })
        response = bedrock_runtime.invoke_model(
            modelId=KB_MODEL_ARN,
            body=body,
            contentType="application/json",
            accept="application/json",
        )
        response_body = json.loads(response["body"].read())
        content = response_body.get("content", [])
        raw_text = content[0].get("text", "") if content else ""

        # Strip markdown code fences if present
        clean = re.sub(r"```(?:json)?\s*|\s*```", "", raw_text).strip()

        try:
            extracted = json.loads(clean)
        except json.JSONDecodeError:
            # Last resort: find JSON object in the text
            match = re.search(r"\{.*\}", clean, re.DOTALL)
            if match:
                extracted = json.loads(match.group())
            else:
                raise ValueError(f"No JSON found in: {clean[:300]}")

        return {
            "institution_name": str(extracted.get("institution_name") or ""),
            "program_name": str(extracted.get("program_name") or ""),
            "degree_awarded": str(extracted.get("degree_awarded") or ""),
            "graduation_date": str(extracted.get("graduation_date") or ""),
            "graduation_confirmed": bool(extracted.get("graduation_confirmed", False)),
            "total_credits": _safe_float(extracted.get("total_credits", 0.0)),
            "nursing_credits": _safe_float(extracted.get("nursing_credits", 0.0)),
            "accreditation_type": str(extracted.get("accreditation_type") or ""),
            "accreditation_body": str(extracted.get("accreditation_body") or ""),
            "extraction_confidence": max(
                0.0, min(1.0, _safe_float(extracted.get("extraction_confidence", 0.5)))
            ),
            "extraction_notes": str(extracted.get("extraction_notes") or ""),
        }

    except Exception as e:
        print(f"[ERROR] structured extraction failed: {e}")
        return _empty_extracted_data(f"extraction error: {str(e)[:200]}")


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
) -> dict:
    if not extracted_text.strip():
        return {
            "success": False,
            "error": "No text extracted cannot run kb audit",
            "question_results": [],
        }

    rules_block = "\n".join(
        f"[{r.get('code')}] ({str(r.get('severity', '')).upper()}) {r.get('description')}: {r.get('check')}"
        for r in rules
    ) if rules else "No rules loaded."

    question_results: list[dict] = []
    for idx, question in enumerate(AUDIT_QUESTIONS, start=1):
        prompt = QUESTION_PROMPT_TEMPLATE.format(
            transcript_text=extracted_text[:15000],
            signature_count=signature_count,
            seal_detected=bool(rekognition_result.get("seal_detected")),
            rules_block=rules_block,
            audit_question=question,
        )
        full_system_prompt = f"{DEFAULT_PROMPT}\n{prompt}\nSearch results:\n$search_results$"

        try:
            response = bedrock_agent.retrieve_and_generate(
                input={"text": question},
                retrieveAndGenerateConfiguration={
                    "type": "KNOWLEDGE_BASE",
                    "knowledgeBaseConfiguration": {
                        "knowledgeBaseId": KB_ID,
                        "modelArn": KB_MODEL_ARN,
                        "generationConfiguration": {
                            "promptTemplate": {"textPromptTemplate": full_system_prompt}
                        },
                    },
                },
            )
            output_text = response.get("output", {}).get("text", "")
            result = _normalize_question_result(output_text, question)
            result["question_index"] = idx
            result["raw_response"] = output_text[:2000]
        except Exception as e:
            result = {
                "question": question,
                "question_index": idx,
                "answer": "uncertain",
                "confidence": 0.0,
                "risk_weight": "medium",
                "category": "fraud_indicator",
                "severity": "warning",
                "rule_codes": [],
                "evidence": [],
                "reasoning": f"retrieve and generate failed: {e}",
                "raw_response": "",
            }

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

    # step 4 — Bedrock structured data extraction from OCR text
    extracted_data = extract_structured_data(textract_result.get("full_text", ""))
    print(
        f"[INFO] extracted institution={extracted_data.get('institution_name')!r} "
        f"degree={extracted_data.get('degree_awarded')!r} "
        f"confidence={extracted_data.get('extraction_confidence')}"
    )

    # step 5 — Bedrock KB audit questions
    audit_result = run_audit_questions(
        verification_id,
        textract_result.get("full_text", ""),
        textract_result.get("signature_count", 0),
        rekognition_result,
        rules,
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
