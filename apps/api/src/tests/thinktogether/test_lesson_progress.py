"""Lesson completion is personal and survives a curriculum reload."""
from types import SimpleNamespace

import pytest
from sqlmodel import select

from src.db.thinktogether.learning import TTLesson, TTProgram
from src.services.thinktogether import learning
from src.services.thinktogether import learning_curriculum as curriculum


@pytest.mark.asyncio
async def test_lesson_list_and_detail_include_only_requesting_learner_completion(db):
    def context(user_id, staff=False):
        return SimpleNamespace(org_id=1, user=SimpleNamespace(id=user_id), can=lambda permission: staff)

    seeded = await learning.seed_sample(db, context(101, True))
    program = (await db.exec(select(TTProgram))).first()
    lesson = (await db.exec(select(TTLesson))).first()
    program.status, program.is_sample = 'published', False
    lesson.status, lesson.is_sample = 'published', False
    db.add_all([program, lesson])
    await db.commit()
    learner, other = context(101), context(102)
    completed = await curriculum.complete_lesson(db, learner, lesson.public_id)
    again = await curriculum.complete_lesson(db, learner, lesson.public_id)
    assert completed['completed_at'] == again['completed_at']
    listed = await curriculum.lessons(db, learner, seeded['program']['id'])
    saved = next(row for row in listed if row['id'] == lesson.public_id)
    detail = await curriculum.lessons(db, learner, program.public_id, lesson.public_id)
    assert saved['completed_at'] == completed['completed_at']
    assert detail['completed_at'] == completed['completed_at']
    assert saved['completed'] is True and detail['completed'] is True
    untouched = await curriculum.lessons(db, other, program.public_id, lesson.public_id)
    assert untouched['completed_at'] is None and untouched['completed'] is False
    assert completed['mastery_evidence'] is False
