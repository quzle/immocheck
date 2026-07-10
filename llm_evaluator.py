import logging
import json
import re
from pathlib import Path
from datetime import datetime, date
from config import LLM_API_KEY, LLM_PROVIDER, MOCK_LLM, MAX_WARMMIETE, LLM_MODEL, OFFICE_LOCATION, TRANSIT_LINES

logger = logging.getLogger(__name__)

def _load_prompts() -> dict:
    """Load prompts from templates/prompts.json."""
    prompts_file = Path(__file__).parent / 'templates' / 'prompts.json'
    with open(prompts_file) as f:
        return json.load(f)

PROMPTS = _load_prompts()

def _get_evaluation_prompts() -> tuple[str, str]:
    """Build evaluation system and user prompts with runtime values."""
    system = PROMPTS['evaluation']['system'].format(max_warmmiete=MAX_WARMMIETE)
    user = PROMPTS['evaluation']['user']
    logger.debug(f"[EVAL_CONFIG] MAX_WARMMIETE={MAX_WARMMIETE}, prompt contains: {repr(system[100:200])}")
    return system, user

def _get_drafting_prompts(template: str, listing_info: str, profile: str) -> tuple[str, str]:
    """Build drafting system and user prompts with runtime values."""
    system = PROMPTS['drafting']['system']
    user = PROMPTS['drafting']['user'].format(
        template=template,
        listing_info=listing_info,
        profile=profile
    )
    return system, user

# Use mock LLM responses if explicitly enabled or if a required provider has no API key
def _should_use_mock() -> bool:
    if MOCK_LLM:
        return True
    # Providers that require an API key
    requires_key = ['anthropic', 'gemini']
    if LLM_PROVIDER.lower() in requires_key and not LLM_API_KEY:
        return True
    return False

USE_MOCK_LLM = _should_use_mock()


def evaluate_listing(listing: dict, profile: str) -> dict:
    """
    Send listing description and renter profile to LLM for evaluation.
    Returns {"decision": "APPROVE"|"REJECT"|"ERROR", "reason": str, "error_type": str|None}
    error_type: "api_error" (always retry), "parse_error" (retry up to 3x), or None for normal decisions
    """
    if USE_MOCK_LLM:
        return _evaluate_mock(listing, profile)

    # Only check API key if the provider requires one
    if LLM_PROVIDER.lower() in ['anthropic', 'gemini'] and not LLM_API_KEY:
        logger.error(f"LLM_API_KEY not configured for provider {LLM_PROVIDER}")
        return {"decision": "REJECT", "reason": f"LLM API key not configured for {LLM_PROVIDER}", "error_type": None}

    url = listing.get('url', '')
    title = listing.get('title', '')
    description = listing.get('description', '')

    system_prompt, user_prompt_template = _get_evaluation_prompts()
    user_message = user_prompt_template.format(
        title=title,
        url=url,
        description=description,
        profile=profile
    )

    try:
        if LLM_PROVIDER.lower() == 'anthropic':
            return _evaluate_with_anthropic(system_prompt, user_message)
        elif LLM_PROVIDER.lower() == 'gemini':
            return _evaluate_with_gemini(system_prompt, user_message)
        elif LLM_PROVIDER.lower() == 'ollama':
            return _evaluate_with_ollama(system_prompt, user_message)
        else:
            logger.error(f"Unsupported LLM provider: {LLM_PROVIDER}")
            return {"decision": "REJECT", "reason": f"Unsupported LLM provider: {LLM_PROVIDER}", "error_type": None}
    except Exception as e:
        error_msg = str(e).lower()
        # Classify error type
        if any(x in error_msg for x in ["connection", "timeout", "rate limit", "503", "502", "api", "network"]):
            error_type = "api_error"
            logger.error(f"[LLM_ERROR_API] Evaluation failed (retryable): {e}")
        else:
            error_type = "parse_error"
            logger.error(f"[LLM_ERROR_PARSE] Evaluation failed: {e}")
        return {"decision": "ERROR", "reason": f"LLM error: {str(e)}", "error_type": error_type}


def _evaluate_with_anthropic(system_prompt: str, user_message: str) -> dict:
    """Evaluate using Anthropic Claude API."""
    from anthropic import Anthropic

    logger.info(f"[LLM_REQ] Evaluate: {LLM_MODEL}, msg_len={len(user_message)}, max_tokens=500")
    logger.debug(f"[LLM_REQ_DETAIL] System: {system_prompt[:200]}...")
    logger.debug(f"[LLM_REQ_DETAIL] User: {user_message[:200]}...")

    client = Anthropic(api_key=LLM_API_KEY)
    response = client.messages.create(
        model=LLM_MODEL,
        max_tokens=500,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}]
    )

    response_text = response.content[0].text.strip()
    logger.info(f"[LLM_RESP] Evaluate: {len(response_text)} chars, stop_reason={response.stop_reason}")
    logger.debug(f"[LLM_RESP_DETAIL] Response: {response_text[:300]}...")
    return _parse_llm_response(response_text)


def _evaluate_with_gemini(system_prompt: str, user_message: str) -> dict:
    """Evaluate using Google Gemini API."""
    import google.generativeai as genai

    logger.info(f"[LLM_REQ] Evaluate: {LLM_MODEL}, msg_len={len(user_message)}")
    logger.debug(f"[LLM_REQ_DETAIL] System: {system_prompt[:200]}...")
    logger.debug(f"[LLM_REQ_DETAIL] User: {user_message[:200]}...")

    genai.configure(api_key=LLM_API_KEY)
    model = genai.GenerativeModel(LLM_MODEL, system_instruction=system_prompt)
    response = model.generate_content(user_message)
    response_text = response.text.strip()
    logger.info(f"[LLM_RESP] Evaluate: {len(response_text)} chars")
    logger.debug(f"[LLM_RESP_DETAIL] Response: {response_text[:300]}...")
    return _parse_llm_response(response_text)


def _evaluate_with_ollama(system_prompt: str, user_message: str) -> dict:
    """Evaluate using Ollama local LLM."""
    from config import OLLAMA_HOST, OLLAMA_MODEL, OLLAMA_NUM_CTX
    import ollama

    try:
        logger.info(f"[LLM_REQ] Evaluate: {OLLAMA_MODEL} at {OLLAMA_HOST}, msg_len={len(user_message)}")
        logger.debug(f"[LLM_REQ_DETAIL] System: {system_prompt[:200]}...")
        logger.debug(f"[LLM_REQ_DETAIL] User: {user_message[:200]}...")

        response = ollama.chat(
            model=OLLAMA_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            stream=False,
            format="json",
            options={
                "num_ctx": OLLAMA_NUM_CTX,
            }
        )
        response_text = response['message']['content'].strip()
        logger.info(f"[LLM_RESP] Evaluate: {len(response_text)} chars")
        logger.debug(f"[LLM_RESP_DETAIL] Response: {response_text[:300]}...")
        return _parse_llm_response(response_text)
    except Exception as e:
        error_msg = str(e)
        if "Failed to connect" in error_msg or "connection" in error_msg.lower():
            logger.error(f"[LLM_ERROR] Cannot connect to Ollama at {OLLAMA_HOST}. Start it with: ollama serve")
            return {"decision": "REJECT", "reason": f"Ollama not running at {OLLAMA_HOST}. Start with: ollama serve"}
        else:
            logger.error(f"[LLM_ERROR] Ollama evaluation failed: {e}")
            return {"decision": "REJECT", "reason": f"Ollama error: {error_msg}"}


def _parse_llm_response(response_text: str) -> dict:
    """Parse LLM response as JSON."""
    try:
        # Try to extract JSON if wrapped in markdown code blocks
        if '```' in response_text:
            json_match = re.search(r'```(?:json)?\s*(\{[^`]*\})\s*```', response_text, re.DOTALL)
            if json_match:
                response_text = json_match.group(1)

        data = json.loads(response_text)
        decision = data.get('decision', 'REJECT').upper()
        reason = data.get('reason', 'No reason provided')

        if decision not in ['APPROVE', 'REJECT']:
            decision = 'REJECT'

        logger.debug(f"[LLM_PARSE] Decision: {decision}, reason: {reason[:100]}")
        return {"decision": decision, "reason": reason}
    except json.JSONDecodeError:
        logger.error(f"[LLM_PARSE_ERROR] Failed to parse LLM response as JSON: {response_text[:200]}")
        return {"decision": "REJECT", "reason": "LLM response parsing error"}


def _evaluate_mock(listing: dict, profile: str) -> dict:
    """Simulate LLM evaluation for development/testing."""
    description = listing.get('description', '').lower()

    # Basic heuristics for mock evaluation
    if any(x in description for x in ['tausch', 'wg', 'zwischenmiete', 'pendler']):
        return {"decision": "REJECT", "reason": "Listing type not suitable (WG, Tausch, Pendler, or Zwischenmiete)"}

    # Check price if mentioned
    price_match = re.search(r'(\d+)\s*€?\s*(?:warm|kalt)', description)
    if price_match:
        price = int(price_match.group(1))
        if price > MAX_WARMMIETE:
            return {"decision": "REJECT", "reason": f"Price exceeds budget: €{price} > €{MAX_WARMMIETE}"}

    return {"decision": "APPROVE", "reason": "Listing appears suitable for tenant profile"}


def draft_application(listing: dict, profile: str, template: str) -> dict:
    """
    Generate a personalized German application message for an approved listing.
    Returns {"message": str, "error_type": str|None}
    error_type: "api_error" (always retry), "parse_error" (retry up to 3x), or None on success
    """
    if USE_MOCK_LLM:
        return {"message": _draft_mock(listing, profile, template), "error_type": None}

    # Only check API key if the provider requires one
    if LLM_PROVIDER.lower() in ['anthropic', 'gemini'] and not LLM_API_KEY:
        logger.error(f"LLM_API_KEY not configured for provider {LLM_PROVIDER}")
        return {"message": "", "error_type": None}

    url = listing.get('url', '')
    title = listing.get('title', '')
    description = listing.get('description', '')
    landlord_name = listing.get('landlord_name', '')
    size_sqm = listing.get('size_sqm', 0)
    warmmiete = listing.get('warmmiete', 0)
    rooms = listing.get('rooms', 0)
    availability = listing.get('availability', '')

    listing_info = f"""Listing Title: {title}
Listing URL: {url}"""
    if rooms:
        listing_info += f"\nRooms: {rooms}"
    if size_sqm:
        listing_info += f"\nSize: {size_sqm}m²"
    if warmmiete:
        listing_info += f"\nRent: €{warmmiete}/month"
    if availability:
        listing_info += f"\nAvailability: {availability}"
    listing_info += f"\nDescription: {description}"
    if landlord_name:
        listing_info += f"\nLandlord/Contact: {landlord_name}"

    system_prompt, user_message = _get_drafting_prompts(template, listing_info, profile)

    try:
        if LLM_PROVIDER.lower() == 'anthropic':
            message = _draft_with_anthropic(system_prompt, user_message)
        elif LLM_PROVIDER.lower() == 'gemini':
            message = _draft_with_gemini(system_prompt, user_message)
        elif LLM_PROVIDER.lower() == 'ollama':
            message = _draft_with_ollama(system_prompt, user_message)
        else:
            logger.error(f"Unsupported LLM provider: {LLM_PROVIDER}")
            return {"message": "", "error_type": None}

        return {"message": message.strip(), "error_type": None}
    except Exception as e:
        error_msg = str(e).lower()
        # Classify error type
        if any(x in error_msg for x in ["connection", "timeout", "rate limit", "503", "502", "api", "network"]):
            error_type = "api_error"
            logger.error(f"[LLM_ERROR_API] Drafting failed (retryable): {e}")
        else:
            error_type = "parse_error"
            logger.error(f"[LLM_ERROR_PARSE] Drafting failed: {e}")
        return {"message": "", "error_type": error_type}


def _draft_with_anthropic(system_prompt: str, user_message: str) -> str:
    """Draft using Anthropic Claude API."""
    from anthropic import Anthropic

    logger.info(f"[LLM_REQ] Draft: {LLM_MODEL}, msg_len={len(user_message)}, max_tokens=1500")
    logger.debug(f"[LLM_REQ_DETAIL] System: {system_prompt[:200]}...")
    logger.debug(f"[LLM_REQ_DETAIL] User: {user_message[:200]}...")

    client = Anthropic(api_key=LLM_API_KEY)
    response = client.messages.create(
        model=LLM_MODEL,
        max_tokens=1500,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}]
    )

    response_text = response.content[0].text.strip()
    logger.info(f"[LLM_RESP] Draft: {len(response_text)} chars, stop_reason={response.stop_reason}")
    logger.debug(f"[LLM_RESP_DETAIL] Response: {response_text[:300]}...")
    return response_text


def _draft_with_gemini(system_prompt: str, user_message: str) -> str:
    """Draft using Google Gemini API."""
    import google.generativeai as genai

    logger.info(f"[LLM_REQ] Draft: {LLM_MODEL}, msg_len={len(user_message)}")
    logger.debug(f"[LLM_REQ_DETAIL] System: {system_prompt[:200]}...")
    logger.debug(f"[LLM_REQ_DETAIL] User: {user_message[:200]}...")

    genai.configure(api_key=LLM_API_KEY)
    model = genai.GenerativeModel(LLM_MODEL, system_instruction=system_prompt)
    response = model.generate_content(user_message)
    response_text = response.text.strip()
    logger.info(f"[LLM_RESP] Draft: {len(response_text)} chars")
    logger.debug(f"[LLM_RESP_DETAIL] Response: {response_text[:300]}...")
    return response_text


def _draft_with_ollama(system_prompt: str, user_message: str) -> str:
    """Draft using Ollama local LLM."""
    from config import OLLAMA_HOST, OLLAMA_MODEL, OLLAMA_NUM_CTX
    import ollama

    try:
        logger.info(f"[LLM_REQ] Draft: {OLLAMA_MODEL} at {OLLAMA_HOST}, msg_len={len(user_message)}")
        logger.debug(f"[LLM_REQ_DETAIL] System: {system_prompt[:200]}...")
        logger.debug(f"[LLM_REQ_DETAIL] User: {user_message[:200]}...")

        response = ollama.chat(
            model=OLLAMA_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            stream=False,
            options={
                "num_ctx": OLLAMA_NUM_CTX,
            }
        )
        response_text = response['message']['content'].strip()
        logger.info(f"[LLM_RESP] Draft: {len(response_text)} chars")
        logger.debug(f"[LLM_RESP_DETAIL] Response: {response_text[:300]}...")
        return response_text
    except Exception as e:
        error_msg = str(e)
        if "Failed to connect" in error_msg or "connection" in error_msg.lower():
            logger.error(f"[LLM_ERROR] Cannot connect to Ollama at {OLLAMA_HOST}. Start it with: ollama serve")
        else:
            logger.error(f"[LLM_ERROR] Ollama drafting failed: {e}")
        return ""


def _draft_mock(listing: dict, profile: str, template: str) -> str:
    """Simulate LLM message drafting for development/testing."""
    url = listing.get('url', '')
    landlord_name = listing.get('landlord_name', '')
    size_sqm = listing.get('size_sqm', 0)
    warmmiete = listing.get('warmmiete', 0)
    rooms = listing.get('rooms', 0)

    # Use landlord name if available, otherwise generic greeting
    if landlord_name:
        # Try to determine if it's a male/female name based on common endings (very basic heuristic)
        if landlord_name.endswith(('a', 'e', 'ie')):
            greeting = f"Sehr geehrte Frau {landlord_name},"
        else:
            greeting = f"Sehr geehrter Herr {landlord_name},"
    else:
        greeting = "Sehr geehrte Damen und Herren,"

    # Build details string for personalization
    details = []
    if rooms:
        details.append(f"{rooms}-Zimmer Wohnung")
    if size_sqm:
        details.append(f"{size_sqm}m²")
    if warmmiete:
        details.append(f"€{warmmiete}/Monat")
    details_str = " - ".join(details) if details else "Wohnung"

    # Extract some details from title for personalization
    mock_message = f"""{greeting}

ich interessiere mich sehr für die {details_str} unter {url}, da sie meinen Anforderungen entspricht. Die Lage und Ausstattung sind für mich ideal.

Kurz zu meinem Profil:
* Ich bin 32 Jahre alt, alleinstehend und Nichtraucher.
* Ich bin Software Engineer mit sicherem, hohem Einkommen.
* Ich besitze keine Haustiere und suche ein ruhiges, langfristiges Zuhause.

Ich bin zuverlässig und gehe sorgsam mit Eigentum um. Meine Bewerbungsunterlagen stelle ich gerne zur Verfügung.

Über ein Kennenlernen würde ich mich freuen.

Mit freundlichen Grüßen,
[Bewerber:in]"""

    return mock_message


# ============ SCORING FUNCTIONS ============

_LEASE_END = date(2026, 8, 31)
_OPTIMAL_DATE = date(2026, 8, 17)   # 14 days before lease end


def _score_availability(availability_str: str) -> tuple[float, str]:
    """Score 0-5 based on how close the start date is to the optimal move-in date (Aug 17, 2026)."""
    if not availability_str:
        return 2.5, "No date provided"

    raw = availability_str.split(' - ')[0].strip()  # Handle "DD.MM.YYYY - DD.MM.YYYY"

    if 'sofort' in raw.lower():
        avail = date.today()
    else:
        for fmt in ('%d.%m.%Y', '%d.%m.%y'):
            try:
                avail = datetime.strptime(raw, fmt).date()
                break
            except ValueError:
                pass
        else:
            return 2.5, f"Unparseable date: {raw}"

    days_from_optimal = (avail - _OPTIMAL_DATE).days
    abs_days = abs(days_from_optimal)

    if abs_days <= 7:
        return 5.0, f"Available {raw} — ideal timing"
    if abs_days <= 14:
        return 4.5, f"Available {raw} — very good timing"
    if abs_days <= 28:
        return 4.0, f"Available {raw} — good timing"
    if abs_days <= 45:
        return 3.0, f"Available {raw} — acceptable timing"
    if abs_days <= 62:
        return 2.0, f"Available {raw} — early/late"
    return 1.0, f"Available {raw} — too early or too late"


def _score_price(warmmiete: int) -> tuple[float, str]:
    """Score 0-5 based on price relative to MAX_WARMMIETE budget."""
    if not warmmiete:
        return 2.5, "Price unknown"
    ratio = warmmiete / MAX_WARMMIETE
    if ratio <= 0.60:
        return 5.0, f"€{warmmiete} — excellent value"
    if ratio <= 0.70:
        return 4.0, f"€{warmmiete} — good value"
    if ratio <= 0.80:
        return 3.0, f"€{warmmiete} — reasonable"
    if ratio <= 0.90:
        return 2.0, f"€{warmmiete} — pricey"
    return 1.0, f"€{warmmiete} — near budget limit"


def _score_size(listing: dict) -> tuple[float, str]:
    """Score 0-5 based on rooms, sqm, and outdoor space."""
    rooms = listing.get('rooms', 0)
    sqm = listing.get('size_sqm', 0)
    outdoor = listing.get('outdoor_space', '')

    # Base score from rooms: 1=1.5, 2=3.5, 3+=4.0
    if rooms >= 3:
        base = 4.0
    elif rooms == 2:
        base = 3.5
    elif rooms == 1:
        base = 1.5
    else:
        base = 2.5  # unknown

    # Size bonus
    if sqm > 90:
        base += 1.0
    elif sqm > 75:
        base += 0.5
    elif sqm < 50:
        base -= 0.5

    # Outdoor bonus
    if outdoor:
        base += 0.5

    score = min(5.0, max(0.5, round(base * 2) / 2))  # clamp, round to 0.5

    parts = []
    if rooms:
        parts.append(f"{rooms} rooms")
    if sqm:
        parts.append(f"{sqm}m²")
    if outdoor:
        parts.append(outdoor)
    return score, ', '.join(parts) if parts else "Size unknown"


def _score_location_with_llm(listing: dict) -> dict:
    """Call LLM to score commute and location quality. Returns dict with commute + location keys."""
    location = listing.get('location', '')
    description = listing.get('description', '')[:500]

    prompts = _load_prompts()
    user_msg = prompts['scoring']['user'].format(
        location=location, description=description,
        office_location=OFFICE_LOCATION, transit_lines=TRANSIT_LINES,
    )
    system_msg = prompts['scoring']['system']

    try:
        if LLM_PROVIDER.lower() == 'anthropic':
            from anthropic import Anthropic
            client = Anthropic(api_key=LLM_API_KEY)
            resp = client.messages.create(
                model=LLM_MODEL, max_tokens=200,
                system=system_msg,
                messages=[{"role": "user", "content": user_msg}]
            )
            raw = resp.content[0].text.strip()
        elif LLM_PROVIDER.lower() == 'gemini':
            import google.generativeai as genai
            genai.configure(api_key=LLM_API_KEY)
            m = genai.GenerativeModel(LLM_MODEL, system_instruction=system_msg)
            raw = m.generate_content(user_msg).text.strip()
        elif LLM_PROVIDER.lower() == 'ollama':
            import ollama
            from config import OLLAMA_MODEL, OLLAMA_NUM_CTX
            resp = ollama.chat(
                model=OLLAMA_MODEL,
                messages=[{"role": "system", "content": system_msg},
                          {"role": "user", "content": user_msg}],
                stream=False, format="json",
                options={"num_ctx": OLLAMA_NUM_CTX}
            )
            raw = resp['message']['content'].strip()
        else:
            raise ValueError(f"Unsupported provider: {LLM_PROVIDER}")

        # Strip markdown fences if present
        if '```' in raw:
            m = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', raw, re.DOTALL)
            if m:
                raw = m.group(1)

        data = json.loads(raw)
        return {
            'commute': {
                'score': float(data['commute']['score']),
                'reason': str(data['commute']['reason'])
            },
            'location': {
                'score': float(data['location']['score']),
                'reason': str(data['location']['reason'])
            }
        }
    except Exception as e:
        logger.warning(f"Location scoring LLM call failed: {e}; defaulting to 2.5")
        return {
            'commute': {'score': 2.5, 'reason': 'Scoring unavailable'},
            'location': {'score': 2.5, 'reason': 'Scoring unavailable'},
        }


def score_listing(listing: dict) -> dict:
    """Score an approved listing on 5 criteria. Returns dict with per-criterion scores and overall."""
    avail_score, avail_reason = _score_availability(listing.get('availability', ''))
    price_score, price_reason = _score_price(listing.get('warmmiete', 0))
    size_score, size_reason = _score_size(listing)

    if USE_MOCK_LLM:
        commute = {'score': 3.0, 'reason': 'Mock score'}
        location = {'score': 3.0, 'reason': 'Mock score'}
    else:
        loc_scores = _score_location_with_llm(listing)
        commute = loc_scores['commute']
        location = loc_scores['location']

    all_scores = [commute['score'], location['score'], size_score, price_score, avail_score]
    overall = round(sum(all_scores) / len(all_scores) * 2) / 2  # round to 0.5

    return {
        'commute': {'score': commute['score'], 'reason': commute['reason']},
        'location': {'score': location['score'], 'reason': location['reason']},
        'size': {'score': size_score, 'reason': size_reason},
        'price': {'score': price_score, 'reason': price_reason},
        'availability': {'score': avail_score, 'reason': avail_reason},
        'overall': overall,
    }


if __name__ == "__main__":
    # Simple test
    sample_listing = {
        'url': 'https://www.immobilienscout24.de/expose/123456789',
        'title': 'Schöne 2-Zimmer Wohnung in Schwabing',
        'description': 'Wunderschöne Wohnung in Schwabing. 1200€ Warmmiete. Balkon, EBK, nahe U-Bahn.'
    }

    with open('renter_profile.txt', 'r') as f:
        profile = f.read()

    print("Testing LLM Evaluator...")
    print(f"Provider: {LLM_PROVIDER}")

    result = evaluate_listing(sample_listing, profile)
    print(f"Evaluation: {result}")

    if result['decision'] == 'APPROVE':
        with open('email_application_template.txt', 'r') as f:
            template = f.read()
        message = draft_application(sample_listing, profile, template)
        print(f"\nDrafted message preview:\n{message[:300]}...")
