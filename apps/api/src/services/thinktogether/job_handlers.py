from src.db.thinktogether.learning import TTReview
from src.services.thinktogether.notifications import notify_user, email_job
from src.services.thinktogether.room_integrations import revoke_media_member, dispatch_room_agent, expire_commercial_access
from src.services.thinktogether.community import session_reminders_job
from src.services.thinktogether.board_notes import write_board_note
from src.services.thinktogether.content_generation import generate_step
from src.services.thinktogether.course_generation.generator import generate_step as generate_document_course


async def grade(db, job):
    from src.services.thinktogether.learning import learning_grade_job
    await learning_grade_job(db, job)


async def review_due(db, job):
    review = await db.get(TTReview, job.payload['review_id'])
    if not review or review.org_id != job.org_id or review.status != 'pending':
        return
    await notify_user(db, job.org_id, review.user_id, f'review:{review.id}',
                      'A skill is ready for a fresh check', f'/thinktogether/practice?review={review.public_id}')


handlers = {'learning.grade': grade, 'learning.review_due': review_due, 'notification.email': email_job,
            'room_member_revoked': revoke_media_member, 'room_dispatch': dispatch_room_agent, 'access.expire': expire_commercial_access,
            'community.reminders': session_reminders_job, 'board.note': write_board_note,
            'content.generate': generate_step, 'content.course_pipeline': generate_document_course}
