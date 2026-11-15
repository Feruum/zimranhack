"""Optional course-only provider configuration; never changes grading/voice."""
import os
from fastapi import HTTPException


def build_authoring_model(model_name):
    provider = os.getenv('TT_CONTENT_PROVIDER', '').strip()
    if provider:
        if os.getenv('TT_CONTENT_ENABLED', '').lower() != 'true':
            raise HTTPException(503, 'Enable TT_CONTENT_ENABLED for course generation.')
        if provider != 'openrouter':
            raise HTTPException(503, 'Unsupported course-only AI provider.')
        key = os.getenv('TT_CONTENT_API_KEY', '').strip()
        if not key:
            raise HTTPException(503, 'Configure the course AI provider credential.')
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.openrouter import OpenRouterProvider
        return OpenAIChatModel(model_name,
            provider=OpenRouterProvider(api_key=key, app_title='ThinkTogether'),
            settings={'extra_body': {'reasoning': {'effort': 'low'}}})
    from config.config import get_learnhouse_config
    if not get_learnhouse_config().ai_config.is_ai_enabled:
        raise HTTPException(503, 'Enable ThinkTogether AI before generating course drafts.')
    from src.services.ai.llm.provider import build_model
    return build_model(model_name)
