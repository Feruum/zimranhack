"""Configuration gates shared by the operator view and production checkout."""
import os
from config.config import get_learnhouse_config


def provider_checks():
    ai = get_learnhouse_config().ai_config
    livekit = all(os.getenv('TT_'+key) or os.getenv(key) for key in ('LIVEKIT_URL', 'LIVEKIT_API_KEY', 'LIVEKIT_API_SECRET'))
    return {
        'video_and_voice_configured': bool(livekit and os.getenv('TT_INTERNAL_KEY')),
        'ai_configured': bool(ai.is_ai_enabled and (ai.api_key or ai.gemini_api_key)
                              and os.getenv('TT_REASONING_MODEL') and os.getenv('TT_FACILITATOR_MODEL')),
        'mail_configured': bool(os.getenv('LEARNHOUSE_RESEND_API_KEY') and os.getenv('LEARNHOUSE_SYSTEM_EMAIL_ADDRESS')),
    }
