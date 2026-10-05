"""Overeni AWS pripojeni a pristupu k Bedrocku:  python scripts/check_aws.py"""
import sys

from botocore.exceptions import BotoCoreError, ClientError

from kb.aws import bedrock
from kb.config import get_settings

s = get_settings()
sess = bedrock.session()
print(f"region={s.aws_region} profile={s.aws_profile or '(default chain)'} model={s.bedrock_model_id}")

try:
    ident = sess.client("sts").get_caller_identity()
    print("STS OK:", ident["Arn"])
except (BotoCoreError, ClientError) as e:
    sys.exit(f"STS selhalo (credentials?): {e}")

try:
    br = sess.client("bedrock")
    profiles = br.list_inference_profiles(maxResults=100)["inferenceProfileSummaries"]
    print("\nInference profiles (pouzij jako BEDROCK_MODEL_ID):")
    for p in profiles:
        if "anthropic" in p["inferenceProfileId"] or "amazon.nova" in p["inferenceProfileId"]:
            print("  ", p["inferenceProfileId"])
except (BotoCoreError, ClientError) as e:
    print("list_inference_profiles selhalo (chybi prava bedrock:List*?):", e)

try:
    print("\nConverse test:", bedrock.ask("Odpovez jednim slovem: ahoj", max_tokens=20))
except ClientError as e:
    code = e.response["Error"]["Code"]
    hint = {
        "AccessDeniedException": "Model nema povoleny pristup (Bedrock console -> Model access) nebo chybi IAM bedrock:InvokeModel.",
        "ValidationException": "Spatne BEDROCK_MODEL_ID - zkus inference profile ze seznamu vyse.",
        "ResourceNotFoundException": "Model/profile v tomto regionu neexistuje.",
    }.get(code, "")
    sys.exit(f"Converse selhalo: {code}: {e}\n{hint}")
