"""
MSBN transcript fraud detection pipeline lambda.
"""
from __future__ import annotations

import json
import os
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


def build_verification_summary(
    verification_id: str,
    textract_result: dict,
    rekognition_result: dict,
    audit_result: dict,
    rules: list[dict],
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
        recommendation = "REJECT"
    elif fraud_score >= 0.4:
        risk_level = "MEDIUM"
        recommendation = "MANUAL_REVIEW"
    else:
        risk_level = "LOW"
        recommendation = "APPROVE"

    yes_count = len([q for q in question_results if str(q.get("answer", "")).lower() == "yes"])
    no_count = len([q for q in question_results if str(q.get("answer", "")).lower() == "no"])
    uncertain_count = len([q for q in question_results if str(q.get("answer", "")).lower() == "uncertain"])
    ai_recommendation = (
        f"audit completed with {len(question_results)} checks, "
        f"{yes_count} yes, {no_count} no, {uncertain_count} uncertain"
    )

    has_critical = any(f["severity"] == "critical" for f in flags)
    has_warnings = any(f["severity"] == "warning" for f in flags)
    if has_critical or recommendation == "REJECT":
        overall_status = "flagged"
    elif has_warnings or recommendation == "MANUAL_REVIEW":
        overall_status = "flagged"
    else:
        overall_status = "cleared"

    return {
        "summary_id": str(uuid.uuid4()),
        "verification_id": verification_id,
        "rules_applied": [r["code"] for r in rules if "code" in r],
        "flags": flags,
        "extracted_data": {
            "institution_name": "",
            "program_name": "",
            "degree_awarded": "",
            "graduation_date": "",
            "graduation_confirmed": False,
            "total_credits": 0.0,
            "nursing_credits": 0.0,
            "accreditation_type": "",
            "accreditation_body": "",
            "raw_text": textract_result.get("full_text", "")[:2000],
            "extraction_confidence": 0.0,
            "extraction_notes": "derived from kb audit and textract analyze document",
            "signature_count": textract_result.get("signature_count", 0),
        },
        "overall_status": overall_status,
        "ai_recommendation": ai_recommendation,
        "fraud_score": round(fraud_score, 3),
        "risk_level": risk_level,
        "recommendation": recommendation,
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
        print("[WARN] API_INTERNAL_URL not set skipping callback")
        return False
    try:
        payload = json.dumps({"summary": summary, "status": summary["overall_status"]}).encode()
        url = f"{API_INTERNAL_URL.rstrip('/')}/api/v1/transcripts/{verification_id}"
        req = urllib.request.Request(
            url,
            data=payload,
            method="PATCH",
            headers={
                "Content-Type": "application/json",
                "X-Lambda-Secret": API_CALLBACK_SECRET,
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            print(f"[INFO] FastAPI PATCH response: {resp.status}")
            return resp.status in (200, 204)
    except urllib.error.HTTPError as e:
        print(f"[ERROR] FastAPI PATCH HTTP error {e.code}: {e.read()}")
    except Exception as e:
        print(f"[ERROR] FastAPI PATCH failed: {e}")
    return False


def lambda_handler(event: dict, context) -> dict:
    verification_id = event.get("verification_id")
    document_id = event.get("document_id")
    s3_key = event.get("s3_key")
    if not all([verification_id, document_id, s3_key]):
        return {"statusCode": 400, "error": "verification_id, document_id, and s3_key are required"}

    print(f"[INFO] starting pipeline for verification={verification_id}, s3_key={s3_key}")

    # step 1
    rules = load_rules()
    print(f"[INFO] loaded {len(rules)} fraud detection rules")

    # step 2
    textract_result = run_textract(s3_key)
    print(
        "[INFO] textract extracted "
        f"{textract_result.get('line_count', 0)} lines and "
        f"{textract_result.get('signature_count', 0)} signatures"
    )

    # step 3
    rekognition_result = run_rekognition(s3_key)
    print(
        f"[INFO] rekognition seal_detected={rekognition_result.get('seal_detected')}, "
        f"labels={len(rekognition_result.get('labels', []))}"
    )

    # step 4
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

    # step 5
    summary = build_verification_summary(
        verification_id,
        textract_result,
        rekognition_result,
        audit_result,
        rules,
    )
    print(
        f"[INFO] summary built status={summary['overall_status']} "
        f"flags={len(summary['flags'])} fraud_score={summary['fraud_score']}"
    )

    # step 6
    save_result_to_s3(verification_id, summary)

    # step 7
    patched = patch_fastapi(verification_id, summary)
    print(f"[INFO] fastapi patch {'succeeded' if patched else 'failed or skipped'}")

    return {
        "statusCode": 200,
        "verification_id": verification_id,
        "document_id": document_id,
        "overall_status": summary["overall_status"],
        "fraud_score": summary["fraud_score"],
        "risk_level": summary["risk_level"],
        "recommendation": summary["recommendation"],
        "flag_count": len(summary["flags"]),
        "patched_api": patched,
    }
