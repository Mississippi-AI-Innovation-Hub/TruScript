"""
Textract Processor Lambda
Receives an S3 key, runs AWS Textract to extract text from the document,
and returns structured text blocks back to the caller (FastAPI).
"""
import json
import os
import boto3

textract = boto3.client("textract", region_name=os.environ["AWS_REGION_NAME"])
s3_bucket = os.environ["S3_BUCKET"]


def lambda_handler(event: dict, context) -> dict:
    """
    Expected event payload:
    {
        "s3_key": "uploads/transcript_abc123.pdf",
        "document_id": "abc123"
    }
    """
    s3_key = event.get("s3_key")
    document_id = event.get("document_id")

    if not s3_key:
        return {"statusCode": 400, "error": "s3_key is required"}

    try:
        response = textract.detect_document_text(
            Document={
                "S3Object": {
                    "Bucket": s3_bucket,
                    "Name": s3_key,
                }
            }
        )

        blocks = response.get("Blocks", [])
        lines = [
            block["Text"]
            for block in blocks
            if block["BlockType"] == "LINE" and "Text" in block
        ]
        full_text = "\n".join(lines)

        return {
            "statusCode": 200,
            "document_id": document_id,
            "s3_key": s3_key,
            "extracted_text": full_text,
            "block_count": len(blocks),
            "line_count": len(lines),
        }

    except Exception as e:
        return {
            "statusCode": 500,
            "error": str(e),
            "document_id": document_id,
        }
