import logging
import os

logger = logging.getLogger(__name__)

# Try to import available translation libraries
try:
    import deepl
    HAS_DEEPL = True
except ImportError:
    HAS_DEEPL = False

try:
    from google.cloud import translate_v2
    HAS_GOOGLE_TRANSLATE = True
except ImportError:
    HAS_GOOGLE_TRANSLATE = False


def translate_text(text: str, target_language: str = "EN") -> str:
    """
    Translate text using DeepL or Google Translate API.

    Args:
        text: Text to translate
        target_language: Target language code (e.g., 'EN' for English, 'FR' for French)

    Returns:
        Translated text, or original text if translation fails
    """
    if not text:
        return text

    # Try DeepL first (recommended for German translations)
    if HAS_DEEPL:
        try:
            deepl_api_key = os.getenv('DEEPL_API_KEY')
            if deepl_api_key:
                translator = deepl.Translator(deepl_api_key)
                result = translator.translate_text(text, target_lang=target_language)
                logger.debug(f"Translated text using DeepL to {target_language}")
                return result.text
            else:
                logger.debug("DEEPL_API_KEY not configured, skipping DeepL translation")
        except Exception as e:
            logger.warning(f"DeepL translation failed: {e}")

    # Fallback to Google Translate
    if HAS_GOOGLE_TRANSLATE:
        try:
            google_project_id = os.getenv('GOOGLE_CLOUD_PROJECT')
            if google_project_id:
                client = translate_v2.Client(project_id=google_project_id)
                result = client.translate_text(text, target_language=target_language)
                logger.debug(f"Translated text using Google Translate to {target_language}")
                return result['translatedText']
            else:
                logger.debug("GOOGLE_CLOUD_PROJECT not configured, skipping Google Translate")
        except Exception as e:
            logger.warning(f"Google Translate failed: {e}")

    # Return original text if translation unavailable
    logger.debug("Translation unavailable, returning original text")
    return text


def install_instructions():
    """Print installation instructions for translation libraries"""
    print("\nTranslation Setup Instructions:")
    print("================================\n")

    print("Option 1: DeepL API (Recommended for German -> English)")
    print("-" * 50)
    print("1. Sign up for free at: https://www.deepl.com/en/pro-api")
    print("2. Copy your API key")
    print("3. Add to .env.local file:")
    print("   DEEPL_API_KEY=your_api_key_here")
    print("4. Install Python library:")
    print("   pip3 install deepl")
    print("\nFree tier: 500,000 characters/month\n")

    print("Option 2: Google Translate API (Free, needs setup)")
    print("-" * 50)
    print("1. Create a Google Cloud project")
    print("2. Enable Cloud Translation API")
    print("3. Create a service account and download JSON key")
    print("4. Set environment variable:")
    print("   export GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json")
    print("5. Add to .env.local file:")
    print("   GOOGLE_CLOUD_PROJECT=your_project_id")
    print("6. Install Python library:")
    print("   pip3 install google-cloud-translate")
    print("\nFree tier: 500,000 characters/month\n")

    print("Note: If neither is configured, messages will NOT be translated.")
    print("Set ENABLE_TRANSLATION=true in .env.local to require translation (will fail if no API available).")


if __name__ == "__main__":
    install_instructions()
