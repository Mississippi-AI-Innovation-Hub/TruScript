"""
MSBN Transcript Fraud Detection — Pipeline Orchestrator Lambda

Entry point called by FastAPI after a transcript image is uploaded.
Runs: Textract → Rekognition → Bedrock and maps the results into the
domain model structures (VerificationSummary, TranscriptFlag) that
FastAPI expects, then PATCHes the result back.

Event payload:
{
    "verification_id": "uuid",
    "document_id": "uuid",
    "s3_key": "uploads/abc123.png",
    "applicant_id": "string"
}
"""
from __future__ import annotations

import json
import os
import uuid
import urllib.request
import urllib.error
from datetime import datetime, timezone

import boto3

# ── AWS clients ───────────────────────────────────────────────────────────────
_region = os.environ["AWS_REGION_NAME"]
textract    = boto3.client("textract",       region_name=_region)
rekognition = boto3.client("rekognition",    region_name=_region)
bedrock     = boto3.client("bedrock-runtime", region_name=_region)
s3          = boto3.client("s3",             region_name=_region)

# ── Config ────────────────────────────────────────────────────────────────────
DOCUMENTS_BUCKET             = os.environ["S3_DOCUMENTS_BUCKET"]
ML_DATA_BUCKET               = os.environ["S3_ML_DATA_BUCKET"]
BEDROCK_MODEL_ID             = os.environ.get("BEDROCK_MODEL_ID", "anthropic.claude-3-haiku-20240307-v1:0")
API_INTERNAL_URL             = os.environ.get("API_INTERNAL_URL", "")        # e.g. http://internal-alb/
API_CALLBACK_SECRET          = os.environ.get("API_CALLBACK_SECRET", "")     # shared secret for Lambda→API calls
REKOGNITION_PROJECT_ARN      = os.environ.get("REKOGNITION_PROJECT_VERSION_ARN", "")  # optional custom model

# ── Fraud rule categories mapped to domain FlagCategory enum values ───────────
CATEGORY_MAP = {
    "accreditation":    "accreditation",
    "graduation":       "graduation",
    "program_completion": "program_completion",
    "document_integrity": "document_integrity",
    "fraud_indicator":  "fraud_indicator",
    "formatting":       "formatting",
}

SEVERITY_MAP = {
    "critical": "critical",
    "warning":  "warning",
    "info":     "info",
}


# ─────────────────────────────────────────────────────────────────────────────
# Step helpers
# ─────────────────────────────────────────────────────────────────────────────

def load_rules() -> list[dict]:
    """Load MSBN fraud detection rules from S3."""
    try:
        obj = s3.get_object(Bucket=ML_DATA_BUCKET, Key="rules/fraud_rules.json")
        data = json.loads(obj["Body"].read())
        return data.get("rules", [])
    except Exception as e:
        print(f"[WARN] Could not load rules from S3: {e}")
        return []


def run_textract(s3_key: str) -> dict:
    """OCR the transcript image — extract all text lines."""
    try:
        resp = textract.detect_document_text(
            Document={"S3Object": {"Bucket": DOCUMENTS_BUCKET, "Name": s3_key}}
        )
        blocks = resp.get("Blocks", [])
        lines  = [b["Text"] for b in blocks if b["BlockType"] == "LINE" and "Text" in b]
        words  = [b["Text"] for b in blocks if b["BlockType"] == "WORD"  and "Text" in b]
        return {
            "success":    True,
            "full_text":  "\n".join(lines),
            "line_count": len(lines),
            "word_count": len(words),
        }
    except Exception as e:
        print(f"[ERROR] Textract failed: {e}")
        return {"success": False, "error": str(e), "full_text": "", "line_count": 0}


def run_rekognition(s3_key: str) -> dict:
    """Detect labels and seals on the transcript image."""
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

        # Optional: Custom Labels model (if trained and deployed)
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
                print(f"[WARN] Custom Labels failed (model may not be running): {e}")

        result = {
            "success":       True,
            "seal_detected": seal_detected,
            "labels":        labels,
            "custom_labels": custom_labels,
        }
    except Exception as e:
        print(f"[ERROR] Rekognition failed: {e}")
        result["error"] = str(e)
    return result


def run_bedrock(extracted_text: str, rekognition_result: dict, rules: list[dict]) -> dict:
    """
    Pass extracted text + visual signals + MSBN rules to Bedrock (Claude).
    Returns a structured analysis that maps to TranscriptFlag domain objects.
    """
    if not extracted_text.strip():
        return {"success": False, "error": "No text extracted — cannot analyze"}

    seal_info  = "Official seal/stamp WAS detected" if rekognition_result.get("seal_detected") else "No official seal/stamp detected"
    label_list = [l["name"] for l in rekognition_result.get("labels", [])[:15]]

    # Format rules for the prompt
    rules_block = "\n".join(
        f"[{r['code']}] ({r['severity'].upper()}) {r['description']}: {r['check']}"
        for r in rules
    ) if rules else "No rules loaded."

    prompt = f"""You are an expert fraud analyst for the Mississippi State Board of Nursing (MSBN).

Your job is to analyze a submitted nursing school transcript and evaluate it against the MSBN rules below.

=== VISUAL ANALYSIS ===
{seal_info}
Rekognition detected labels: {label_list}

=== MSBN FRAUD DETECTION RULES ===
{rules_block}

=== EXTRACTED TRANSCRIPT TEXT ===
{extracted_text[:7000]}

=== INSTRUCTIONS ===
Evaluate the transcript text against EVERY rule above. For each violated rule, create a flag entry.
Also extract the structured data you can identify from the transcript.

Respond ONLY with a valid JSON object — no extra text:
{{
  "fraud_score": <0.0 to 1.0, where 0.0 = clearly legitimate, 1.0 = clear fraud>,
  "risk_level": "<LOW|MEDIUM|HIGH>",
  "recommendation": "<APPROVE|MANUAL_REVIEW|REJECT>",
  "ai_recommendation": "<2-3 sentence human-readable assessment for MSBN staff>",
  "flags": [
    {{
      "rule_code": "<rule code from above>",
      "category": "<accreditation|graduation|program_completion|document_integrity|fraud_indicator|formatting>",
      "severity": "<critical|warning|info>",
      "rule_description": "<rule description>",
      "evidence": "<exact text from transcript that triggered this flag, or empty string>",
      "explanation": "<why this is flagged>",
      "source_section": "<section of transcript where this was found>"
    }}
  ],
  "extracted_data": {{
    "institution_name": "<institution name or empty>",
    "program_name": "<nursing program name or empty>",
    "degree_awarded": "<BSN|ADN|MSN|other|empty>",
    "graduation_date": "<date string or empty>",
    "graduation_confirmed": <true|false>,
    "total_credits": <number or 0>,
    "nursing_credits": <number or 0>,
    "accreditation_type": "<ACEN|CCNE|NLNAC|OTHER|empty>",
    "accreditation_body": "<full name or empty>",
    "extraction_confidence": <0.0 to 1.0>,
    "extraction_notes": "<any notes about data quality>"
  }}
}}"""

    try:
        resp = bedrock.invoke_model(
            modelId=BEDROCK_MODEL_ID,
            body=json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 4096,
                "messages": [{"role": "user", "content": prompt}],
            }),
            contentType="application/json",
            accept="application/json",
        )
        body    = json.loads(resp["body"].read())
        raw     = body["content"][0]["text"].strip()
        analysis = json.loads(raw)
        return {"success": True, "analysis": analysis}
    except json.JSONDecodeError as e:
        print(f"[ERROR] Bedrock response was not valid JSON: {e}")
        return {"success": False, "error": f"JSON parse error: {e}"}
    except Exception as e:
        print(f"[ERROR] Bedrock call failed: {e}")
        return {"success": False, "error": str(e)}


def build_verification_summary(
    verification_id: str,
    textract_result: dict,
    rekognition_result: dict,
    bedrock_result: dict,
    rules: list[dict],
) -> dict:
    """
    Map ML results → VerificationSummary domain structure.
    This dict is sent directly as the `summary` field in the PATCH to FastAPI.
    """
    now = datetime.now(timezone.utc).isoformat()

    # Use Bedrock analysis if available; fall back to basic heuristics
    if bedrock_result.get("success") and bedrock_result.get("analysis"):
        analysis = bedrock_result["analysis"]
        flags_raw       = analysis.get("flags", [])
        extracted_data  = analysis.get("extracted_data", {})
        fraud_score     = analysis.get("fraud_score", 0.5)
        risk_level      = analysis.get("risk_level", "MEDIUM")
        recommendation  = analysis.get("recommendation", "MANUAL_REVIEW")
        ai_recommendation = analysis.get("ai_recommendation", "")
    else:
        flags_raw       = []
        extracted_data  = {}
        fraud_score     = 0.5
        risk_level      = "MEDIUM"
        recommendation  = "MANUAL_REVIEW"
        ai_recommendation = "Automated analysis failed. Manual review required."

    # Add a flag if no seal was detected and Bedrock didn't already flag it
    existing_codes = {f.get("rule_code") for f in flags_raw}
    if not rekognition_result.get("seal_detected") and "INTEGRITY_001" not in existing_codes:
        flags_raw.append({
            "rule_code":        "INTEGRITY_001",
            "category":         "document_integrity",
            "severity":         "critical",
            "rule_description": "Official institutional seal or stamp must be present",
            "evidence":         "",
            "explanation":      "Rekognition image analysis found no institutional seal or stamp on this document.",
            "source_section":   "Document Header",
        })

    # Build flag objects in domain format
    flags = [
        {
            "flag_id":          str(uuid.uuid4()),
            "category":         CATEGORY_MAP.get(f.get("category", "document_integrity"), "document_integrity"),
            "severity":         SEVERITY_MAP.get(f.get("severity", "warning"), "warning"),
            "rule_code":        f.get("rule_code", ""),
            "rule_description": f.get("rule_description", ""),
            "evidence":         f.get("evidence", ""),
            "explanation":      f.get("explanation", ""),
            "source_section":   f.get("source_section", ""),
            "created_at":       now,
        }
        for f in flags_raw
    ]

    # Determine overall status
    has_critical = any(f["severity"] == "critical" for f in flags)
    has_warnings = any(f["severity"] == "warning" for f in flags)
    if has_critical or recommendation == "REJECT":
        overall_status = "flagged"
    elif has_warnings or recommendation == "MANUAL_REVIEW":
        overall_status = "flagged"
    else:
        overall_status = "cleared"

    # Build composite score (Bedrock 70%, seal check 20%, text quality 10%)
    seal_penalty   = 0.2 if not rekognition_result.get("seal_detected") else 0.0
    text_penalty   = 0.1 if textract_result.get("line_count", 0) < 5 else 0.0
    composite      = min(1.0, (fraud_score * 0.70) + seal_penalty + text_penalty)

    return {
        "summary_id":       str(uuid.uuid4()),
        "verification_id":  verification_id,
        "rules_applied":    [r["code"] for r in rules],
        "flags":            flags,
        "extracted_data": {
            "institution_name":      extracted_data.get("institution_name", ""),
            "program_name":          extracted_data.get("program_name", ""),
            "degree_awarded":        extracted_data.get("degree_awarded", ""),
            "graduation_date":       extracted_data.get("graduation_date", ""),
            "graduation_confirmed":  extracted_data.get("graduation_confirmed", False),
            "total_credits":         extracted_data.get("total_credits", 0.0),
            "nursing_credits":       extracted_data.get("nursing_credits", 0.0),
            "accreditation_type":    extracted_data.get("accreditation_type", ""),
            "accreditation_body":    extracted_data.get("accreditation_body", ""),
            "raw_text":              textract_result.get("full_text", "")[:2000],
            "extraction_confidence": extracted_data.get("extraction_confidence", 0.0),
            "extraction_notes":      extracted_data.get("extraction_notes", ""),
        },
        "overall_status":     overall_status,
        "ai_recommendation":  ai_recommendation,
        "fraud_score":        round(composite, 3),
        "risk_level":         risk_level,
        "recommendation":     recommendation,
        "pipeline_meta": {
            "textract_success":     textract_result.get("success"),
            "rekognition_success":  rekognition_result.get("success"),
            "bedrock_success":      bedrock_result.get("success"),
            "seal_detected":        rekognition_result.get("seal_detected"),
            "line_count":           textract_result.get("line_count", 0),
        },
        "created_at": now,
    }


def save_result_to_s3(verification_id: str, result: dict) -> None:
    """Persist full ML result to S3 for audit trail."""
    try:
        s3.put_object(
            Bucket=ML_DATA_BUCKET,
            Key=f"results/{verification_id}/result.json",
            Body=json.dumps(result, indent=2, default=str),
            ContentType="application/json",
        )
    except Exception as e:
        print(f"[WARN] Could not save result to S3: {e}")


def patch_fastapi(verification_id: str, summary: dict) -> bool:
    """PATCH the VerificationSummary back to FastAPI."""
    if not API_INTERNAL_URL:
        print("[WARN] API_INTERNAL_URL not set — skipping callback")
        return False
    try:
        payload = json.dumps({
            "summary": summary,
            "status": summary["overall_status"],
        }).encode()

        url = f"{API_INTERNAL_URL.rstrip('/')}/api/v1/transcripts/{verification_id}"
        req = urllib.request.Request(
            url,
            data=payload,
            method="PATCH",
            headers={
                "Content-Type":  "application/json",
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


# ─────────────────────────────────────────────────────────────────────────────
# Lambda entry point
# ─────────────────────────────────────────────────────────────────────────────

def lambda_handler(event: dict, context) -> dict:
    """
    Main entry point. Called async by FastAPI after document upload.

    Expected event:
    {
        "verification_id": "uuid",
        "document_id":     "uuid",
        "s3_key":          "uploads/abc123.png",
        "applicant_id":    "string"
    }
    """
    verification_id = event.get("verification_id")
    document_id     = event.get("document_id")
    s3_key          = event.get("s3_key")

    if not all([verification_id, document_id, s3_key]):
        return {"statusCode": 400, "error": "verification_id, document_id, and s3_key are required"}

    print(f"[INFO] Starting pipeline for verification={verification_id}, s3_key={s3_key}")

    # ── 1. Load rules ─────────────────────────────────────────────────────────
    rules = load_rules()
    print(f"[INFO] Loaded {len(rules)} fraud detection rules")

    # ── 2. Textract OCR ───────────────────────────────────────────────────────
    print("[INFO] Running Textract...")
    textract_result = run_textract(s3_key)
    print(f"[INFO] Textract extracted {textract_result.get('line_count', 0)} lines")

    # ── 3. Rekognition visual analysis ────────────────────────────────────────
    print("[INFO] Running Rekognition...")
    rekognition_result = run_rekognition(s3_key)
    print(f"[INFO] Rekognition seal_detected={rekognition_result.get('seal_detected')}, "
          f"labels={len(rekognition_result.get('labels', []))}")

    # ── 4. Bedrock fraud analysis ─────────────────────────────────────────────
    print("[INFO] Running Bedrock analysis...")
    bedrock_result = run_bedrock(
        textract_result.get("full_text", ""),
        rekognition_result,
        rules,
    )
    if bedrock_result.get("success"):
        analysis = bedrock_result["analysis"]
        print(f"[INFO] Bedrock fraud_score={analysis.get('fraud_score')}, "
              f"risk={analysis.get('risk_level')}, flags={len(analysis.get('flags', []))}")
    else:
        print(f"[WARN] Bedrock failed: {bedrock_result.get('error')}")

    # ── 5. Build VerificationSummary ──────────────────────────────────────────
    summary = build_verification_summary(
        verification_id, textract_result, rekognition_result, bedrock_result, rules
    )
    print(f"[INFO] Summary built: status={summary['overall_status']}, "
          f"flags={len(summary['flags'])}, fraud_score={summary['fraud_score']}")

    # ── 6. Save to S3 (audit trail) ───────────────────────────────────────────
    save_result_to_s3(verification_id, summary)

    # ── 7. PATCH FastAPI ──────────────────────────────────────────────────────
    patched = patch_fastapi(verification_id, summary)
    print(f"[INFO] FastAPI PATCH {'succeeded' if patched else 'failed or skipped'}")

    return {
        "statusCode":       200,
        "verification_id":  verification_id,
        "document_id":      document_id,
        "overall_status":   summary["overall_status"],
        "fraud_score":      summary["fraud_score"],
        "risk_level":       summary["risk_level"],
        "recommendation":   summary["recommendation"],
        "flag_count":       len(summary["flags"]),
        "patched_api":      patched,
    }
