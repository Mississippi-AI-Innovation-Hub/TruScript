"""
Rekognition Checker Lambda
Uses Amazon Rekognition to detect institutional seals, signatures,
and official markings on transcript documents stored in S3.
"""
import json
import os
import boto3

rekognition = boto3.client("rekognition", region_name=os.environ["AWS_REGION_NAME"])
s3_bucket = os.environ["S3_BUCKET"]

# Labels that indicate an official document (broad match)
SEAL_LABELS = {"seal", "emblem", "logo", "stamp", "badge", "crest", "symbol"}
SIGNATURE_LABELS = {"signature", "handwriting", "text", "autograph"}


def lambda_handler(event: dict, context) -> dict:
    """
    Expected event payload:
    {
        "s3_key": "uploads/transcript_abc123.png",
        "document_id": "abc123"
    }
    Note: Rekognition works on image files (PNG, JPEG).
    For PDFs, the caller should convert first or pass a rendered page image.
    """
    s3_key = event.get("s3_key")
    document_id = event.get("document_id")

    if not s3_key:
        return {"statusCode": 400, "error": "s3_key is required"}

    try:
        label_response = rekognition.detect_labels(
            Image={"S3Object": {"Bucket": s3_bucket, "Name": s3_key}},
            MaxLabels=50,
            MinConfidence=60.0,
        )

        detected_labels = [
            {"name": label["Name"], "confidence": round(label["Confidence"], 2)}
            for label in label_response.get("Labels", [])
        ]

        label_names_lower = {l["name"].lower() for l in detected_labels}
        seal_found = bool(SEAL_LABELS & label_names_lower)
        signature_found = bool(SIGNATURE_LABELS & label_names_lower)

        text_response = rekognition.detect_text(
            Image={"S3Object": {"Bucket": s3_bucket, "Name": s3_key}}
        )

        detected_text_blocks = [
            {
                "text": t["DetectedText"],
                "type": t["Type"],
                "confidence": round(t["Confidence"], 2),
            }
            for t in text_response.get("TextDetections", [])
            if t["Confidence"] >= 70.0
        ]

        return {
            "statusCode": 200,
            "document_id": document_id,
            "s3_key": s3_key,
            "seal_detected": seal_found,
            "signature_detected": signature_found,
            "detected_labels": detected_labels,
            "detected_text_blocks": detected_text_blocks[:20],
            "summary": {
                "label_count": len(detected_labels),
                "text_block_count": len(detected_text_blocks),
                "has_official_markings": seal_found or signature_found,
            },
        }

    except Exception as e:
        return {
            "statusCode": 500,
            "error": str(e),
            "document_id": document_id,
        }
