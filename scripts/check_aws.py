"""Overeni AWS pripojeni a pristupu k Bedrocku (hackathonovy ucet):  python scripts/check_aws.py

Kontroluje: region, identitu (SSO), dostupne EU profily, Converse pro modely, ktere pouzivame (fast/strong/hard),
obrazkovy vstup (vizualni cesta) a embeddingy. U chyb radi podle tabulky z navodu hackathonu.
"""
import io
import sys

from botocore.exceptions import BotoCoreError, ClientError

from kb.aws import bedrock
from kb.config import get_settings

s = get_settings()


def hint(e: Exception) -> str:
    text = str(e)
    if "explicit deny in a service control policy" in text:
        return "Region v ARN neni eu-central-1 -> prepni na Frankfurt. Je-li v ARN eu-central-1, nahlas organizatorum celou zpravu."
    if "not available for this account" in text:
        return "Model nema na uctu kapacitu (Claude 5). Pouzij Sonnet 4.5 nebo Opus 4.5."
    if "ValidationException" in text and "Operation not allowed" in text:
        return "Ucet blokuje volani modelu (osobni ucet?). Na hackathonovem uctu se to nema stat."
    if "ValidationException" in text:
        return "Spatne ID modelu - Claude potrebuje prefix eu. (viz seznam profilu vyse)."
    if "ThrottlingException" in text:
        return "Sdileny limit tymu (Claude 50 pozadavku/min, Opus 25): zpomal, rozprostri paralelni ulohy, hromadnou praci presun na Qwen/Nova."
    if "AccessDenied" in text:
        return "Chybi pristup k modelu nebo regionu."
    if "Unable to locate credentials" in text or "Token has expired" in text or "SSO" in text:
        return "Prihlaseni vyprselo (12 h): aws sso login --profile hackathon"
    return ""


print(f"region={s.aws_region} profile={s.aws_profile or '(default chain)'}")
if s.aws_region != "eu-central-1":
    print("  ! Hackathonovy ucet povoluje tvorbu jen v eu-central-1 (Frankfurt); nastav AWS_REGION=eu-central-1.")

sess = bedrock.session()
try:
    ident = sess.client("sts").get_caller_identity()
    print("STS OK:", ident["Arn"])
except (BotoCoreError, ClientError) as e:
    sys.exit(f"STS selhalo: {e}\n  -> {hint(e) or 'aws sso login --profile hackathon'}")

try:
    br = sess.client("bedrock")
    profiles = [p["inferenceProfileId"] for p in br.list_inference_profiles(maxResults=100)["inferenceProfileSummaries"]]
    print(f"\nEU/globalni inference profily ({len(profiles)}), Claude a Nova:")
    for p in sorted(profiles):
        if "anthropic" in p or "nova" in p or "pixtral" in p:
            print("  ", p)
except (BotoCoreError, ClientError) as e:
    print("list_inference_profiles selhalo:", e)

failed = False
print("\nConverse test modelu pouzivanych v aplikaci:")
for label, model in (("default", s.bedrock_model_id), ("fast", s.uw_model_fast), ("strong", s.uw_model_strong), ("hard", s.uw_model_hard)):
    try:
        out = bedrock.ask("Odpovez jednim slovem: ahoj", model_id=model, max_tokens=20)
        print(f"  OK   {label:8s} {model}: {out.strip()[:30]}")
    except (BotoCoreError, ClientError) as e:
        failed = True
        print(f"  CHYBA {label:7s} {model}: {str(e)[:140]}\n        -> {hint(e)}")

print("\nObrazkovy vstup (vizualni cesta pro skeny):")
try:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (240, 80), "white")
    ImageDraw.Draw(img).text((20, 30), "FAKTURA 42", fill="black")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    out = bedrock.ask("Co je napsano na obrazku? Odpovez jen textem z obrazku.", model_id=s.uw_model_fast, max_tokens=30, images=[buf.getvalue()])
    print(f"  OK   {s.uw_model_fast}: {out.strip()[:40]}")
except Exception as e:  # noqa: BLE001 - diagnosticky skript
    failed = True
    print(f"  CHYBA {str(e)[:140]}\n        -> {hint(e)}")

print("\nEmbeddingy:")
try:
    from kb import embed

    vec = embed.embed_texts(["test"])[0]
    print(f"  OK   model={embed.model_name()} dim={len(vec)}")
except Exception as e:  # noqa: BLE001
    print(f"  CHYBA {str(e)[:160]}")
    print("        -> Kdyz Titan na uctu neni: nastav EMBED_PROVIDER=mock (lexikalni hash, pro dokumenty stejne sablony staci).")
    try:
        fm = sess.client("bedrock").list_foundation_models(byOutputModality="EMBEDDING")["modelSummaries"]
        print("        Dostupne embedding modely:", ", ".join(m["modelId"] for m in fm) or "zadne")
    except (BotoCoreError, ClientError):
        pass

sys.exit(1 if failed else 0)
