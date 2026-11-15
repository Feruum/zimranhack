"""Local PostgreSQL + ASGI load harness: eight rooms, six synthetic users each.

This validates API/DB behavior, not real identity, pedagogy, browser rendering,
SSE network delivery, Yjs, WebRTC, LiveKit Inference or cloud provider capacity.
The authentication dependency is explicitly replaced by isolated test identities.
It never writes to the application's public schema and drops its own schema.
"""
import argparse
import asyncio
import json
import math
import os
import time
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Header, HTTPException
import httpx
from sqlalchemy import text, select, func
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession


async def run():
    from config.config import get_learnhouse_config
    from src.core.migrations import load_models
    from src.core.events.database import get_db_session
    from src.db.organizations import Organization
    from src.db.roles import Role
    from src.db.users import User, PublicUser
    from src.db.user_organizations import UserOrganization
    from src.db.thinktogether.learning import (TTProgram, TTSkill, TTItem, TTInstrument,
        TTLearnerProfile, TTConsent, TTDiagnosticRun, TTAttempt, TTMastery)
    from src.db.thinktogether.rooms import TTCohort, TTCohortMember, TTRoom, TTRoomEvent
    from src.db.thinktogether.billing import TTEntitlement
    from src.services.thinktogether.common import TTContext, get_context
    from src.routers.thinktogether.router import router

    raw_url = str(get_learnhouse_config().database_config.sql_connection_string)
    parsed = make_url(raw_url)
    if parsed.host not in {'db', '127.0.0.1', 'localhost'} or parsed.database != 'thinktogether':
        raise RuntimeError('This harness only accepts the isolated local ThinkTogether database')
    url = parsed.set(drivername='postgresql+asyncpg')
    schema = 'tt_load_' + uuid.uuid4().hex
    metadata = load_models()
    admin = create_async_engine(url)
    async with admin.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(url, pool_size=16, max_overflow=32,
        connect_args={'server_settings':{'search_path':schema+',public'}})
    samples, identities, room_ids = [], {}, []
    counters = {'requests':0, 'expected_cas_conflicts':0}
    try:
        async with engine.begin() as conn:
            await conn.run_sync(lambda connection: metadata.create_all(connection, checkfirst=False))
        now = datetime.now(timezone.utc)
        async with AsyncSession(engine, expire_on_commit=False) as db:
            org = Organization(name='SYNTHETIC LOAD TEST', slug='synthetic-load', org_uuid=str(uuid.uuid4()), email='load@example.com')
            db.add(org)
            await db.flush()
            role = Role(name='Synthetic learner', org_id=org.id)
            operator = User(user_uuid=str(uuid.uuid4()), username='load-operator', first_name='Synthetic', last_name='Operator', email='operator@example.com', email_verified=True)
            db.add_all([role, operator])
            await db.flush()
            operator_id = operator.id
            identities['operator'] = TTContext(PublicUser.model_validate(operator), org.id, True, frozenset({'admin'}))
            program = TTProgram(org_id=org.id, title='SYNTHETIC LOAD FIXTURE — not curriculum', status='published', is_sample=False)
            db.add(program)
            await db.flush()
            skill = TTSkill(org_id=org.id, program_id=program.id, code='synthetic', title='Synthetic decision', domain='People')
            db.add(skill)
            await db.flush()
            items = [TTItem(org_id=org.id, program_id=program.id, skill_id=skill.id,
                family_id=f'load-family-{n}', title=f'Synthetic case {n}', prompt='Synthetic load-check choice',
                status='published', is_sample=False, difficulty=n+1,
                choices=[{'id':'a','text':'A'},{'id':'b','text':'B'}], answer_key={'choice':'a'},
                approved_by=operator_id, approval_evidence='Isolated load fixture only; not expert evidence') for n in range(4)]
            instruments = [TTInstrument(org_id=org.id, kind=kind, title='SYNTHETIC '+kind,
                status='published', is_sample=False, approval_evidence='Isolated load fixture only',
                license_reference='No real cognitive instrument: synthetic harness') for kind in ('domain','cognitive')]
            db.add_all(items+instruments)
            await db.flush()
            for group in range(8):
                cohort = TTCohort(org_id=org.id, program_id=program.id, title=f'Synthetic cohort {group}', language='en', timezone='UTC', status='active', capacity=6,
                    schedule={'weekday':0,'local_time':'18:00','duration_minutes':60})
                db.add(cohort)
                await db.flush()
                room = TTRoom(org_id=org.id, cohort_id=cohort.id, title=f'Synthetic room {group}', starts_at=now,
                    phase='individual_attempt', case_item_id=items[0].id, created_by=operator_id, agent_enabled=False)
                db.add(room)
                await db.flush()
                room_ids.append(room.room_uuid)
                for seat in range(6):
                    key = f'{group}-{seat}'
                    user = User(user_uuid=str(uuid.uuid4()), username='load-'+key, first_name='Synthetic', last_name=key,
                        email=f'load-{key}@example.com', email_verified=True)
                    db.add(user)
                    await db.flush()
                    identities[key] = TTContext(PublicUser.model_validate(user), org.id)
                    db.add_all([
                        UserOrganization(org_id=org.id, user_id=user.id, role_id=role.id, creation_date=now.isoformat(), update_date=now.isoformat()),
                        TTCohortMember(org_id=org.id, cohort_id=cohort.id, user_id=user.id, status='active'),
                        TTEntitlement(org_id=org.id, program_id=program.id, user_id=user.id, source='synthetic_load_fixture', starts_at=now-timedelta(days=1), expires_at=now+timedelta(days=1)),
                        TTLearnerProfile(org_id=org.id, user_id=user.id, program_id=program.id, goal='Synthetic load goal', exam_date='2027-12-01'),
                    ])
                    for instrument in instruments:
                        db.add_all([
                            TTConsent(org_id=org.id, user_id=user.id, instrument_id=instrument.id, accepted=True, version=instrument.consent_version, text_snapshot='Synthetic'),
                            TTDiagnosticRun(org_id=org.id, user_id=user.id, program_id=program.id, instrument_id=instrument.id, kind=instrument.kind, status='completed')
                        ])
            await db.commit()

        app = FastAPI()
        app.include_router(router, prefix='/api/v1')
        async def isolated_session():
            async with AsyncSession(engine, expire_on_commit=False) as session:
                yield session
        async def isolated_identity(x_load_identity: str = Header()):
            if x_load_identity not in identities:
                raise HTTPException(401, 'Isolated load identity required')
            return identities[x_load_identity]
        app.dependency_overrides[get_db_session] = isolated_session
        app.dependency_overrides[get_context] = isolated_identity
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url='http://isolated-load', timeout=60) as client:
            async def request(method, path, identity, body=None, expected=(200,201)):
                began = time.perf_counter()
                response = await client.request(method, '/api/v1/tt'+path, headers={'X-Load-Identity':identity}, json=body)
                samples.append((time.perf_counter()-began)*1000)
                counters['requests'] += 1
                if response.status_code not in expected:
                    # Only schema/error names; never print a response containing learner content.
                    raise AssertionError(f'{method} {path} returned HTTP {response.status_code}')
                return response.json()

            async def personal(group, seat, phase):
                key, room_id = f'{group}-{seat}', room_ids[group]
                task = await request('GET', f'/rooms/{room_id}/task', key)
                resumed = await request('GET', f'/rooms/{room_id}/task', key)
                assert task['delivery_id'] == resumed['delivery_id']
                assert not {'answer_key','rubric','hints'} & task['item'].keys()
                response = {'choice':'a' if seat % 2 == 0 else 'b'}
                body = {'item_id':task['item']['id'], 'delivery_id':task['delivery_id'], 'response':response, 'idempotency_key':f'{phase}-{key}'}
                result = await request('POST', '/attempts', key, body)
                repeated = await request('POST', '/attempts', key, body)
                assert result['id'] == repeated['id'] and 'answer_key' not in repeated
                return task['item']['id']

            async def advance(group, revision):
                return await request('POST', f'/rooms/{room_ids[group]}/commands', 'operator',
                    {'command_id':f'advance-{group}-{revision}', 'expected_revision':revision, 'action':'advance', 'payload':{}})

            started = time.perf_counter()
            await asyncio.gather(*(personal(group,seat,'individual') for group in range(8) for seat in range(6)))
            await asyncio.gather(*(advance(group,0) for group in range(8)))
            async def racing_chat(group, seat):
                key, room_id = f'{group}-{seat}', room_ids[group]
                for retry in range(30):
                    current = await request('GET', f'/rooms/{room_id}', key)
                    result = await request('POST', f'/rooms/{room_id}/commands', key,
                        {'command_id':f'chat-{key}', 'expected_revision':current['revision'], 'action':'chat', 'payload':{'text':'Synthetic load discussion'}}, expected=(200,409))
                    if isinstance(result,dict) and result.get('revision'):
                        return
                    counters['expected_cas_conflicts'] += 1
                raise AssertionError('Room CAS did not converge')
            await asyncio.gather(*(racing_chat(group,seat) for group in range(8) for seat in range(6)))
            await asyncio.gather(*(advance(group,7) for group in range(8)))
            transfer_ids = await asyncio.gather(*(personal(group,seat,'transfer') for group in range(8) for seat in range(6)))
            # Correct/incorrect private work must select different difficulty.
            assert transfer_ids[0] != transfer_ids[1]
            elapsed = time.perf_counter()-started
        async with AsyncSession(engine) as db:
            attempts = (await db.execute(select(func.count()).select_from(TTAttempt))).scalar_one()
            assert attempts == 96, 'HTTP retries created duplicate attempts'
            mastery = (await db.execute(select(TTMastery))).scalars().all()
            assert all(row.state not in ('independent','retained') for row in mastery), 'One-day or group work created false mastery'
            for room_id in room_ids:
                room = (await db.execute(select(TTRoom).where(TTRoom.room_uuid == room_id))).scalar_one()
                seq = (await db.execute(select(TTRoomEvent.sequence).where(TTRoomEvent.room_id == room.id).order_by(TTRoomEvent.sequence))).scalars().all()
                assert seq == list(range(1,9)), 'Room events lost, duplicated or reordered'
        ordered = sorted(samples)
        report = {'passed':True, 'rooms':8, 'synthetic_participants':48, 'immutable_attempts':attempts,
            **counters, 'elapsed_seconds':round(elapsed,3), 'requests_per_second':round(counters['requests']/elapsed,2),
            'latency_ms':{'p50':round(ordered[math.ceil(len(ordered)*.5)-1],2), 'p95':round(ordered[math.ceil(len(ordered)*.95)-1],2), 'max':round(max(ordered),2)},
            'scope':'In-process HTTP/ASGI + real local PostgreSQL; synthetic auth and test records in disposable schema; no browser/SSE/Yjs/WebRTC/cloud media measured',
            'completed_utc':datetime.now(timezone.utc).isoformat()}
        print(json.dumps(report, indent=2))
        return report
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--isolated-local-check', action='store_true', required=True,
        help='Acknowledge this is synthetic local API/DB load, not real pilot/cloud evidence')
    parser.parse_args()
    asyncio.run(run())
