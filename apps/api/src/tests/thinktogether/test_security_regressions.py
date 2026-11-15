"""Regression checks for assessment secrecy and continuing paid access.

All records live in the disposable SQLite fixture. Authentication is replaced at
the ASGI boundary; access checks, scoring, consent, and refund handling are real.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlmodel import select

from src.db.thinktogether.billing import (
    TTEntitlement, TTPaymentOrder, TTProductConfig, TTSeatReservation,
)
from src.db.thinktogether.jobs import TTJob  # register the transactional job table
from src.db.thinktogether.learning import (
    TTAttempt, TTDelivery, TTDiagnosticRun, TTExposure, TTInstrument,
    TTItem, TTLesson, TTMastery, TTMockForm, TTMockRun, TTProgram, TTSkill,
    TTSupportCase,
)
from src.db.thinktogether.rooms import TTCohort, TTCohortMember, TTRoom
from src.db.users import User
from src.routers.thinktogether import learning as routes
from src.services.thinktogether import billing, learning, rooms
from src.services.thinktogether.room_learning import room_task


async def add_item(db, data, family, **values):
    item = TTItem(
        org_id=data.ctx.org_id, program_id=data.program.id,
        skill_id=data.skill.id, family_id=family, title=family,
        prompt='Choose the proportionate response.', status='published',
        is_sample=False, choices=[{'id': 'a', 'text': 'Assess'},
                                  {'id': 'b', 'text': 'Ignore'}],
        answer_key={'choice': 'a'}, explanation='PRIVATE expert rationale',
        hints=['PRIVATE practice hint'], approved_by=data.ctx.user.id,
        approval_evidence='Expert content review 42',
    )
    for key, value in values.items():
        setattr(item, key, value)
    db.add(item)
    await db.flush()
    return item


async def add_instrument(db, data, kind, items):
    instrument = TTInstrument(
        org_id=data.ctx.org_id, title=f'Licensed {kind} assessment', kind=kind,
        status='published', is_sample=False, license_reference='license-42',
        approval_evidence='Independent assessment review 42',
        consent_text='I consent to this private criterion assessment.',
        definition={'item_ids': [item.id for item in items],
                    'scoring': 'dimension_mean',
                    'dimensions': {'reasoning': [item.id for item in items]}},
    )
    db.add(instrument)
    await db.commit()
    await learning.give_consent(db, data.ctx, instrument.public_id, True, '1')
    return instrument


@pytest.fixture
async def learner_data(db, org, regular_user):
    user = await db.get(User, regular_user.id)
    user.email_verified = True
    program = TTProgram(org_id=org.id, title='Approved decision curriculum',
                        status='published', is_sample=False, full_curriculum=True,
                        blueprint_version='PMP-2026-07', approved_by=user.id,
                        approval_evidence='Curriculum review 42')
    db.add_all([user, program])
    await db.flush()
    skill = TTSkill(org_id=org.id, program_id=program.id, code='decision',
                    title='Decision quality', domain='People')
    db.add(skill)
    await db.flush()
    ctx = SimpleNamespace(org_id=org.id, user=user, is_staff=False,
                          can=lambda permission: False)
    data = SimpleNamespace(ctx=ctx, program=program, skill=skill)
    data.item = await add_item(db, data, 'ordinary-practice', difficulty=5)
    await db.commit()
    await learning.save_profile(db, ctx, {
        'goal': 'Improve project decisions', 'exam_date': '2099-06-01',
        'program_id': program.public_id, 'timezone': 'UTC',
    })
    return data


@pytest.fixture
async def ready_learner(db, learner_data):
    """Reach readiness through real licensed and consented diagnostic attempts."""
    data = learner_data
    for kind in ('domain', 'cognitive'):
        item = await add_item(db, data, f'baseline-{kind}')
        instrument = await add_instrument(db, data, kind, [item])
        run = await learning.start_diagnostic(db, data.ctx, kind, instrument.public_id)
        await learning.submit_attempt(db, data.ctx, item.public_id, {'choice': 'a'},
                                      f'baseline-{kind}', delivery_id=run['delivery_id'])
    assert (await learning.learning_readiness(db, data.ctx.org_id, data.ctx.user.id))['ready']
    return data


@pytest.fixture
async def learner_client(db, learner_data):
    app = FastAPI()
    app.include_router(routes.router, prefix='/tt')
    app.dependency_overrides[routes.get_context] = lambda: learner_data.ctx

    async def session():
        yield db

    app.dependency_overrides[routes.get_db_session] = session
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        yield client


async def grant_access(db, data, program_id=None):
    now = learning.utcnow()
    grant = TTEntitlement(org_id=data.ctx.org_id, user_id=data.ctx.user.id,
                          program_id=program_id or data.program.id,
                          source='complimentary', starts_at=now-timedelta(minutes=1),
                          expires_at=now+timedelta(days=56))
    db.add(grant)
    await db.commit()
    return grant


async def create_room(db, data, phase='discussion'):
    cohort = TTCohort(org_id=data.ctx.org_id, program_id=data.program.id,
                      title='Decision group', language='en', timezone='UTC',
                      schedule={}, status='active')
    db.add(cohort)
    await db.flush()
    member = TTCohortMember(org_id=data.ctx.org_id, cohort_id=cohort.id,
                            user_id=data.ctx.user.id, status='active')
    room = TTRoom(org_id=data.ctx.org_id, cohort_id=cohort.id,
                  title='Decision discussion', starts_at=learning.utcnow(),
                  created_by=data.ctx.user.id, case_item_id=data.item.id, phase=phase)
    db.add_all([member, room])
    await db.commit()
    return cohort, room


def assert_cognitive_secret(payload):
    assert 'answer_key' not in payload
    assert 'explanation' not in payload
    assert payload.get('score') is None
    assert payload.get('evaluation') is None
    assert 'PRIVATE expert rationale' not in str(payload)


@pytest.mark.asyncio
async def test_cognitive_submission_replay_and_detail_withhold_per_item_scoring(
    db, learner_data, learner_client,
):
    data = learner_data
    items = [await add_item(db, data, f'licensed-secret-{n}') for n in range(2)]
    instrument = await add_instrument(db, data, 'cognitive', items)
    response = await learner_client.post('/tt/me/diagnostics', json={
        'kind': 'cognitive', 'instrument_id': instrument.public_id,
    })
    assert response.status_code == 200
    run = response.json()
    for n, item in enumerate(items):
        body = {'item_id': item.public_id, 'delivery_id': run['delivery_id'],
                'response': {'choice': 'a' if n == 0 else 'b'},
                'idempotency_key': f'private-{n}'}
        submitted = await learner_client.post('/tt/attempts', json=body)
        assert submitted.status_code == 200
        assert_cognitive_secret(submitted.json())
        replay = await learner_client.post('/tt/attempts', json=body)
        assert replay.status_code == 200
        assert replay.json()['id'] == submitted.json()['id']
        assert_cognitive_secret(replay.json())
        detail = await learner_client.get('/tt/attempts/'+submitted.json()['id'])
        assert detail.status_code == 200
        assert_cognitive_secret(detail.json())
        resumed = await learner_client.get('/tt/me/diagnostics/'+run['run']['id'])
        assert resumed.status_code == 200
        run = resumed.json()
    assert run['run']['status'] == 'completed'
    assert run['run']['result']['dimension_scores']['reasoning'] == .5
    # A completed report still does not turn a private item into a solution oracle.
    detail = await learner_client.get('/tt/attempts/'+submitted.json()['id'])
    assert_cognitive_secret(detail.json())


@pytest.mark.asyncio
async def test_completed_instrument_resume_does_not_issue_another_scorable_attempt(
    db, learner_data, learner_client,
):
    data = learner_data
    instrument = await add_instrument(db, data, 'cognitive', [data.item])
    run = await learning.start_diagnostic(db, data.ctx, 'cognitive', instrument.public_id)
    await learning.submit_attempt(db, data.ctx, data.item.public_id, {'choice': 'a'},
                                  'first-sitting', delivery_id=run['delivery_id'])
    deliveries_before = (await db.exec(select(TTDelivery))).all()
    for _ in range(2):
        response = await learner_client.post('/tt/me/diagnostics', json={
            'kind': 'cognitive', 'instrument_id': instrument.public_id,
        })
        assert response.status_code == 200
        assert response.json()['run']['id'] == run['run']['id']
        assert response.json()['run']['status'] == 'completed'
        assert 'delivery_id' not in response.json()
    assert len((await db.exec(select(TTDiagnosticRun))).all()) == 1
    assert len((await db.exec(select(TTDelivery))).all()) == len(deliveries_before)
    second = await learner_client.post('/tt/attempts', json={
        'item_id': data.item.public_id, 'delivery_id': run['delivery_id'],
        'response': {'choice': 'b'}, 'idempotency_key': 'second-sitting',
    })
    assert second.status_code == 409
    assert len((await db.exec(select(TTAttempt))).all()) == 1


@pytest.mark.asyncio
async def test_unstarted_diagnostic_and_its_sibling_family_never_enter_practice(
    db, ready_learner, learner_client,
):
    data = ready_learner
    protected = await add_item(db, data, 'held-out-diagnostic', difficulty=1)
    sibling = await add_item(db, data, 'held-out-diagnostic', difficulty=1)
    await add_instrument(db, data, 'cognitive', [protected])
    await grant_access(db, data)
    planned = await learner_client.post('/tt/plan', json={'budget_minutes': 5})
    assert planned.status_code == 200
    assert [a['item_id'] for a in planned.json()['activities']] == [data.item.public_id]
    practice = await learner_client.get('/tt/practice/next')
    assert practice.status_code == 200
    assert practice.json()['item']['id'] == data.item.public_id
    assert practice.json()['item']['id'] not in {protected.public_id, sibling.public_id}


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['resume', 'completed_resume', 'answer', 'finalize'])
async def test_mock_http_rechecks_exact_program_access_after_full_refund(
    db, ready_learner, learner_client, operation,
):
    data = ready_learner
    cohort, _ = await create_room(db, data)
    product = TTProductConfig(org_id=data.ctx.org_id, program_id=data.program.id,
                              polar_product_id='paid-program', enabled=True)
    db.add(product)
    await db.flush()
    reservation = TTSeatReservation(
        org_id=data.ctx.org_id, user_id=data.ctx.user.id,
        user_uuid=data.ctx.user.user_uuid, program_id=data.program.id,
        cohort_id=cohort.id, product_config_id=product.id,
        polar_product_id=product.polar_product_id, status='consumed',
        expires_at=learning.utcnow()+timedelta(minutes=30),
    )
    db.add(reservation)
    await db.flush()
    order = TTPaymentOrder(org_id=data.ctx.org_id, user_id=data.ctx.user.id,
                           program_id=data.program.id, reservation_id=reservation.id,
                           polar_order_id='settled-order', status='paid', total_amount=14900)
    db.add(order)
    await db.flush()
    grant = await grant_access(db, data)
    grant.source, grant.order_id = 'polar', order.id
    form = TTMockForm(org_id=data.ctx.org_id, program_id=data.program.id,
                      title='Approved mock', form_code='A', item_ids=[data.item.id],
                      duration_minutes=30, status='published', is_sample=False)
    db.add_all([grant, form])
    await db.commit()
    started = await learner_client.post(f'/tt/mock-forms/{form.public_id}/runs')
    assert started.status_code == 200
    run_id = started.json()['id']
    assert (await learner_client.get('/tt/mock-runs/'+run_id)).status_code == 200
    if operation == 'completed_resume':
        completed = await learner_client.post('/tt/mock-runs/'+run_id+'/finalize')
        assert completed.status_code == 200
        assert completed.json()['questions'][0]['answer_key'] == data.item.answer_key

    # The refund payload represents the server-fetched Polar settlement record.
    await billing.apply_authoritative_order(db, {
        'id': order.polar_order_id, 'status': 'refunded', 'paid': True,
        'product_id': product.polar_product_id, 'total_amount': 14900,
        'refunded_amount': 14900, 'currency': 'usd',
        'customer': {'external_id': data.ctx.user.user_uuid},
        'metadata': {'tt_reservation_uuid': reservation.reservation_uuid},
    })
    await db.commit()
    assert not await billing.has_active_access(db, data.ctx.org_id, data.ctx.user.id, data.program.id)
    # An active purchase of another curriculum cannot reopen the refunded mock.
    other = TTProgram(org_id=data.ctx.org_id, title='Different curriculum')
    db.add(other)
    await db.commit()
    await grant_access(db, data, other.id)
    assert await billing.has_active_access(db, data.ctx.org_id, data.ctx.user.id, other.id)
    path = '/tt/mock-runs/'+run_id
    if operation in {'resume', 'completed_resume'}:
        response = await learner_client.get(path)
    elif operation == 'answer':
        response = await learner_client.post(path+'/answers', json={
            'item_id': data.item.public_id, 'response': {'choice': 'a'},
            'idempotency_key': 'refunded-answer',
        })
    else:
        response = await learner_client.post(path+'/finalize')
    assert response.status_code == 403
    assert 'answer_key' not in response.text and 'PRIVATE' not in response.text
    run = (await db.exec(select(TTMockRun))).one()
    assert run.status == ('completed' if operation == 'completed_resume' else 'in_progress')
    assert run.attempt_ids == []
    assert not (await db.exec(select(TTAttempt).where(TTAttempt.phase == 'mock'))).all()


@pytest.mark.asyncio
@pytest.mark.parametrize('denial', ['expired_delivery', 'revoked_access', 'wrong_program'])
async def test_hint_http_denies_stale_or_unentitled_delivery_without_exposing_help(
    db, ready_learner, learner_client, denial,
):
    data = ready_learner
    grant = await grant_access(db, data)
    issued = await learning.issue_item(db, data.ctx, data.item)
    delivery = await learning.find(db, TTDelivery, data.ctx.org_id, issued['delivery_id'])
    if denial == 'expired_delivery':
        delivery.expires_at = learning.utcnow()-timedelta(seconds=1)
        db.add(delivery)
    else:
        grant.status = 'revoked'
        db.add(grant)
        if denial == 'wrong_program':
            other = TTProgram(org_id=data.ctx.org_id, title='Unrelated paid curriculum')
            db.add(other)
            await db.flush()
            await grant_access(db, data, other.id)
    await db.commit()
    response = await learner_client.post('/tt/practice/'+issued['delivery_id']+'/hint')
    assert response.status_code == (409 if denial == 'expired_delivery' else 403)
    assert 'PRIVATE practice hint' not in response.text
    assert not (await db.exec(select(TTExposure).where(TTExposure.source == 'hint'))).all()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['single_choice', 'matching'])
async def test_room_discussion_case_whitelists_nested_choice_fields(db, learner_data, kind):
    data = learner_data
    data.item.choices = [
        {'id': 'a', 'text': 'Assess', 'side': 'left', 'correct': True,
         'rationale': 'HIDDEN CHOICE KEY', 'author': {'answer': True}},
        {'id': 'b', 'text': 'Ignore', 'side': 'right', 'score': 0,
         'answer_key': {'correct': False}},
    ]
    data.item.kind = kind
    db.add(data.item)
    await db.commit()
    await grant_access(db, data)
    _, room = await create_room(db, data)
    shared = (await rooms.room_view(db, data.ctx, room.room_uuid))['case']
    expected = [{'id': 'a', 'text': 'Assess'}, {'id': 'b', 'text': 'Ignore'}]
    if kind == 'matching':
        expected[0]['side'], expected[1]['side'] = 'left', 'right'
    assert shared['choices'] == expected
    assert 'answer_key' not in shared and 'explanation' not in shared
    assert 'HIDDEN CHOICE KEY' not in str(shared)


@pytest.mark.asyncio
async def test_room_transfer_blocks_when_only_unrelated_or_discussed_families_exist(db, learner_data):
    data = learner_data
    await grant_access(db, data)
    _, room = await create_room(db, data, 'transfer')
    unrelated = TTSkill(org_id=data.ctx.org_id, program_id=data.program.id,
                        code='unrelated', title='Unrelated skill', domain='Process')
    db.add(unrelated)
    await db.flush()
    await add_item(db, data, 'unrelated-fresh', skill_id=unrelated.id)
    await add_item(db, data, data.item.family_id)  # a paraphrase is not transfer
    await learning.record_exposure(db, data.ctx.org_id, data.ctx.user.id,
                                   data.item.family_id, 'group', room.room_uuid)
    await db.commit()
    with pytest.raises(HTTPException) as denied:
        await room_task(db, data.ctx, room.room_uuid)
    assert denied.value.status_code == 409
    support_id = denied.value.detail['support_case_id']
    support = await learning.find(db, TTSupportCase, data.ctx.org_id, support_id)
    assert support.kind == 'item_shortage'
    assert support.details['skill_ids'] == [data.skill.id]
    assert not (await db.exec(select(TTDelivery).where(TTDelivery.room_id == room.id))).all()
    fresh = await add_item(db, data, 'discussed-skill-new-context')
    await db.commit()
    transfer = await room_task(db, data.ctx, room.room_uuid)
    assert transfer['item']['id'] == fresh.public_id


@pytest.mark.asyncio
@pytest.mark.parametrize('delay_seconds, expected', [(-1, 'independent'), (0, 'retained')])
async def test_retention_waits_full_168_hours_after_second_independent_day(
    db, learner_data, delay_seconds, expected,
):
    data = learner_data
    # Dates differ by seven while the third event can still be one second early.
    second = datetime(2026, 11, 2, 23, 59, 59, tzinfo=timezone.utc)
    times = [second-timedelta(days=1), second,
             second+timedelta(days=7, seconds=delay_seconds)]
    for n, when in enumerate(times):
        item = await add_item(db, data, f'independent-clock-{n}')
        await db.commit()
        result = await learning.submit_attempt(db, data.ctx, item.public_id,
                                               {'choice': 'a'}, f'clock-{n}', now=when)
        assert result['independent_evidence']
    mastery = (await db.exec(select(TTMastery).where(TTMastery.skill_id == data.skill.id))).one()
    assert mastery.independent_days == 3 and mastery.independent_families == 3
    assert mastery.state == expected


@pytest.mark.asyncio
@pytest.mark.parametrize('resource', ['items', 'lessons', 'skills'])
async def test_author_cannot_add_content_to_a_published_program(
    db, learner_data, learner_client, resource,
):
    data = learner_data
    data.ctx.can = lambda permission: True
    data.ctx.is_staff = True
    body = {'program_id': data.program.public_id, 'skill_id': data.skill.public_id,
            'title': 'New approved material', 'is_sample': False}
    models = {'items': TTItem, 'lessons': TTLesson, 'skills': TTSkill}
    if resource == 'items':
        body.update(family_id='new-content', prompt='Choose.',
                    choices=[{'id': 'a', 'text': 'A'}, {'id': 'b', 'text': 'B'}],
                    answer_key={'choice': 'a'})
    elif resource == 'lessons':
        body.update(body_markdown='Expert-authored lesson.')
    else:
        body.update(code='new-skill')
    before = len((await db.exec(select(models[resource]))).all())
    response = await learner_client.post('/tt/content/'+resource, json=body)
    assert response.status_code == 409
    assert len((await db.exec(select(models[resource]))).all()) == before


@pytest.mark.asyncio
@pytest.mark.parametrize('resource', ['items', 'lessons', 'mock_forms'])
async def test_published_program_children_cannot_be_retired_in_place(
    db, learner_data, learner_client, resource,
):
    data = learner_data
    data.ctx.can = lambda permission: True
    data.ctx.is_staff = True
    if resource == 'items':
        child = data.item
    elif resource == 'lessons':
        child = TTLesson(org_id=data.ctx.org_id, program_id=data.program.id,
                         skill_id=data.skill.id, title='Published theory',
                         body_markdown='Authored theory', status='published', is_sample=False)
    else:
        child = TTMockForm(org_id=data.ctx.org_id, program_id=data.program.id,
                           title='Published form', form_code='freeze',
                           item_ids=[data.item.id], status='published', is_sample=False)
    db.add(child)
    await db.commit()
    response = await learner_client.post(
        f'/tt/content/{resource}/{child.public_id}/transition',
        json={'status': 'retired'},
    )
    assert response.status_code == 409
    await db.refresh(child)
    assert child.status == 'published'


@pytest.mark.asyncio
async def test_room_media_token_cannot_outlive_exact_program_entitlement(
    db, learner_data, monkeypatch,
):
    import jwt
    data = learner_data
    grant = await grant_access(db, data)
    grant.expires_at = learning.utcnow()+timedelta(seconds=59)
    db.add(grant)
    other = TTProgram(org_id=data.ctx.org_id, title='Long unrelated purchase')
    db.add(other)
    await db.commit()
    await grant_access(db, data, other.id)
    _, room = await create_room(db, data, 'preparation')
    monkeypatch.setenv('TT_LIVEKIT_URL', 'wss://test.example')
    monkeypatch.setenv('TT_LIVEKIT_API_KEY', 'test-key')
    monkeypatch.setenv('TT_LIVEKIT_API_SECRET', 'test-secret-at-least-thirty-two-characters')
    result = await rooms.media_token(db, data.ctx, room.room_uuid)
    claims = jwt.decode(result['token'], options={'verify_signature': False})
    assert claims['video']['roomJoin']
    assert claims['exp'] <= int(grant.expires_at.timestamp())
    assert result['expires_at'] == claims['exp']


@pytest.mark.asyncio
@pytest.mark.parametrize('extension', [None, 'other_program', 'same_program'])
async def test_expired_purchase_revokes_live_membership_and_board_access_unless_extended(
    db, learner_data, extension,
):
    from src.db.boards import Board, BoardMember
    from src.services.thinktogether.room_integrations import expire_commercial_access

    data = learner_data
    grant = await grant_access(db, data)
    cohort, room = await create_room(db, data)
    board = Board(org_id=data.ctx.org_id, name='Private discussion board',
                  board_uuid='expired-private-board', created_by=data.ctx.user.id)
    db.add(board)
    await db.flush()
    db.add(BoardMember(board_id=board.id, user_id=data.ctx.user.id))
    room.board_uuid = board.board_uuid
    grant.expires_at = learning.utcnow()-timedelta(seconds=1)
    db.add_all([room, grant])
    await db.commit()
    if extension == 'same_program':
        await grant_access(db, data)
    elif extension == 'other_program':
        other = TTProgram(org_id=data.ctx.org_id, title='Still active unrelated purchase')
        db.add(other)
        await db.commit()
        await grant_access(db, data, other.id)

    for _ in range(2):
        await expire_commercial_access(db, SimpleNamespace(org_id=data.ctx.org_id))
        await db.commit()
    await db.refresh(grant)
    assert grant.status == 'expired'
    member = (await db.exec(select(TTCohortMember).where(
        TTCohortMember.cohort_id == cohort.id,
        TTCohortMember.user_id == data.ctx.user.id,
    ))).one()
    boards = (await db.exec(select(BoardMember).where(BoardMember.board_id == board.id))).all()
    revocations = (await db.exec(select(TTJob).where(TTJob.kind == 'room_member_revoked'))).all()
    if extension == 'same_program':
        assert member.status == 'active' and len(boards) == 1
        assert revocations == []
        assert (await rooms.board_access(db, data.ctx, board.board_uuid))['can_write']
    else:
        assert member.status == 'revoked' and boards == []
        assert len(revocations) == 1
        assert revocations[0].payload == {'room_uuid': room.room_uuid, 'user_id': data.ctx.user.id}
        with pytest.raises(HTTPException) as denied:
            await rooms.board_access(db, data.ctx, board.board_uuid)
        assert denied.value.status_code == 403
