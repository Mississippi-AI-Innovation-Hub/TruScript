"""
Fraud Analyzer Lambda
Takes extracted transcript text, invokes Amazon Bedrock (Claude) to detect
fraud indicators, and returns a structured analysis result.
"""
import json
import os
import boto3

bedrock = boto3.client("bedrock-runtime", region_name=os.environ["AWS_REGION_NAME"])
MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "anthropic.claude-3-haiku-20240307-v1:0")

FRAUD_ANALYSIS_PROMPT = """
You are an expert document fraud analyst specializing in academic transcripts.
Analyze the following transcript text for indicators of fraud or tampering.

Look for:
1. Inconsistent formatting or fonts (if described)
2. Unusual GPA values or grade patterns
3. Course names or credit hours that seem implausible
4. Missing or inconsistent institution information
5. Suspicious date patterns or semester sequences
6. Unusual degree designations

Transcript text:
{transcript_text}

Respond ONLY with a valid JSON object:
{{
  "fraud_score": <0.0-1.0, where 1.0 is highly suspicious>,
  "risk_level": "<LOW|MEDIUM|HIGH>",
  "indicators": [<list of specific suspicious elements found>],
  "summary": "<brief human-readable explanation>",
  "recommendation": "<APPROVE|REVIEW|REJECT>"
}}
"""


def lambda_handler(event: dict, context) -> dict:
    """
    Expected event payload:
    {
        "document_id": "abc123",
        "extracted_text": "Full transcript text here..."
    }
    """
    document_id = event.get("document_id")
    extracted_text = event.get("extracted_text", "")

    if not extracted_text:
        return {"statusCode": 400, "error": "extracted_text is required"}

    try:
        prompt = FRAUD_ANALYSIS_PROMPT.format(transcript_text=extracted_text[:8000])

        request_body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": prompt}],
        }

        response = bedrock.invoke_model(
            modelId=MODEL_ID,
            body=json.dumps(request_body),
            contentType="application/json",
            accept="application/json",
        )

        response_body = json.loads(response["body"].read())
        raw_text = response_body["content"][0]["text"]
        analysis = json.loads(raw_text)

        return {
            "statusCode": 200,
            "document_id": document_id,
            "analysis": analysis,
        }

    except json.JSONDecodeError as e:
        return {
            "statusCode": 500,
            "error": f"Failed to parse Bedrock response as JSON: {str(e)}",
            "document_id": document_id,
        }
    except Exception as e:
        return {
            "statusCode": 500,
            "error": str(e),
            "document_id": document_id,
        }
