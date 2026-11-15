"""Provider configuration gates must fail closed before paid production use."""
from types import SimpleNamespace

import pytest

from src.db.thinktogether.billing import TTProductConfig
from src.db.thinktogether.learning import TTProgram
from src.db.users import User
from src.services.thinktogether import billing, learning, release


@pytest.fixture
def configured_providers(monkeypatch):
    for key in ('LIVEKIT_URL', 'LIVEKIT_API_KEY', 'LIVEKIT_API_SECRET'):
        monkeypatch.delenv(key, raising=False)
    settings = {
        'TT_LIVEKIT_URL': 'wss://configured.example',
        'TT_LIVEKIT_API_KEY': 'configured-video-key',
        'TT_LIVEKIT_API_SECRET': 'configured-video-secret',
        'TT_INTERNAL_KEY': 'configured-internal-voice-key',
        'TT_REASONING_MODEL': 'configured-reasoning-model',
        'TT_FACILITATOR_MODEL': 'configured-facilitator-model',
        'LEARNHOUSE_RESEND_API_KEY': 'configured-mail-key',
        'LEARNHOUSE_SYSTEM_EMAIL_ADDRESS': 'ThinkTogether <mail@example.com>',
    }
    for key, value in settings.items():
        monkeypatch.setenv(key, value)
    ai = SimpleNamespace(is_ai_enabled=True, api_key='configured-ai-key', gemini_api_key='')
    monkeypatch.setattr(release, 'get_learnhouse_config', lambda: SimpleNamespace(ai_config=ai))
    return ai


def test_provider_checks_accept_only_complete_configuration(configured_providers):
    assert release.provider_checks() == {
        'video_and_voice_configured': True,
        'ai_configured': True,
        'mail_configured': True,
    }


@pytest.mark.parametrize('missing, gate', [
    ('TT_LIVEKIT_URL', 'video_and_voice_configured'),
    ('TT_LIVEKIT_API_KEY', 'video_and_voice_configured'),
    ('TT_LIVEKIT_API_SECRET', 'video_and_voice_configured'),
    ('TT_INTERNAL_KEY', 'video_and_voice_configured'),
    ('TT_REASONING_MODEL', 'ai_configured'),
    ('TT_FACILITATOR_MODEL', 'ai_configured'),
    ('LEARNHOUSE_RESEND_API_KEY', 'mail_configured'),
    ('LEARNHOUSE_SYSTEM_EMAIL_ADDRESS', 'mail_configured'),
])
def test_one_missing_provider_requirement_keeps_its_release_gate_closed(
    configured_providers, monkeypatch, missing, gate,
):
    monkeypatch.delenv(missing)
    checks = release.provider_checks()
    assert checks[gate] is False
    assert all(ready for name, ready in checks.items() if name != gate)


@pytest.mark.parametrize('disabled', ['enabled', 'key'])
def test_named_models_do_not_substitute_for_an_enabled_authenticated_ai_provider(
    configured_providers, disabled,
):
    if disabled == 'enabled':
        configured_providers.is_ai_enabled = False
    else:
        configured_providers.api_key = ''
    assert release.provider_checks()['ai_configured'] is False


@pytest.fixture
async def production_ready_prerequisites(db, org, regular_user, monkeypatch, configured_providers):
    """Independently approved prerequisites isolate the production provider gate.

    These readiness stubs do not call any provider or create a checkout. Product,
    organization, and verified learner records remain real fixture rows.
    """
    user = await db.get(User, regular_user.id)
    user.email_verified = True
    program = TTProgram(org_id=org.id, title='Independently approved curriculum',
                        status='published', is_sample=False, full_curriculum=True,
                        approval_evidence='Independent release review')
    db.add_all([user, program])
    await db.flush()
    db.add(TTProductConfig(org_id=org.id, program_id=program.id,
                           polar_product_id='configured-production-product', enabled=True))
    await db.commit()
    for name in ('TT_RELEASE_APPROVED', 'TT_AI_EVALUATION_APPROVED',
                 'TT_RESTORE_DRILL_PASSED', 'TT_POLAR_PRODUCTION_APPROVED'):
        monkeypatch.setenv(name, 'true')
    monkeypatch.setenv('TT_POLAR_ENVIRONMENT', 'production')
    monkeypatch.setenv('TT_POLAR_ACCESS_TOKEN', 'test-production-token')
    monkeypatch.setenv('TT_POLAR_WEBHOOK_SECRET', 'test-production-webhook-secret')

    async def ready(*args, **kwargs):
        return {'ready': True}

    async def available(*args, **kwargs):
        return [SimpleNamespace(cohort_uuid='compatible-real-cohort', title='Compatible group',
                                schedule={}, timezone='UTC', language='en')]

    monkeypatch.setattr(learning, 'learning_readiness', ready)
    monkeypatch.setattr(learning, 'launch_readiness', ready)
    monkeypatch.setattr(billing, 'available_cohorts', available)
    return org.id, user.id, program.id


@pytest.mark.asyncio
async def test_complete_production_configuration_satisfies_provider_gate(db, production_ready_prerequisites):
    result = await billing.billing_readiness(db, *production_ready_prerequisites)
    assert result['environment'] == 'production'
    assert result['missing'] == []
    assert result['checkout_enabled'] is True


@pytest.mark.asyncio
@pytest.mark.parametrize('missing, gate', [
    ('TT_REASONING_MODEL', 'ai_configured'),
    ('TT_FACILITATOR_MODEL', 'ai_configured'),
    ('TT_INTERNAL_KEY', 'video_and_voice_configured'),
    ('LEARNHOUSE_SYSTEM_EMAIL_ADDRESS', 'mail_configured'),
])
async def test_production_checkout_stays_disabled_when_a_required_provider_is_incomplete(
    db, production_ready_prerequisites, monkeypatch, missing, gate,
):
    monkeypatch.delenv(missing)
    result = await billing.billing_readiness(db, *production_ready_prerequisites)
    assert result['environment'] == 'production'
    assert result['checkout_enabled'] is False
    assert result['missing'] == [gate]
