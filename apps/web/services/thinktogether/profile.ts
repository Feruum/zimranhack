import * as Yup from 'yup'

/** ISO calendar date in the learner's timezone; an invalid zone has no minimum. */
export function learnerToday(timezone: string, now = new Date()): string {
  try {
    const parts = new Intl.DateTimeFormat('en', {
      timeZone: timezone, year: 'numeric', month: '2-digit', day: '2-digit',
    }).formatToParts(now)
    const value = (type: Intl.DateTimeFormatPartTypes) => parts.find(part => part.type === type)?.value
    return `${value('year')}-${value('month')}-${value('day')}`
  } catch { return '' }
}

export function createProfileSchema(now: () => Date = () => new Date()) {
  return Yup.object({
    goal: Yup.string().trim().required('Describe your learning goal.').max(2000),
    exam_date: Yup.string().required('Choose your exam date.')
      .matches(/^\d{4}-\d{2}-\d{2}$/, 'Choose a valid exam date.')
      .test('calendar-date', 'Choose a valid exam date.', value => {
        if (!value) return true
        const date = new Date(`${value}T12:00:00Z`)
        return !Number.isNaN(date.getTime()) && date.toISOString().slice(0, 10) === value
      })
      .test('not-past', 'Choose today or a future date in your timezone.', function(value) {
        const today = learnerToday(this.parent.timezone, now())
        return !value || !today || value >= today
      }),
    minutes_per_day: Yup.number().integer('Enter a whole number of minutes.').min(5).max(240).required(),
    timezone: Yup.string().required('Choose your timezone.')
      .test('timezone', 'Use a timezone such as America/New_York.', value => !!value && !!learnerToday(value, now())),
    language: Yup.string().required(),
    program_id: Yup.string().nullable().notRequired(),
  })
}
