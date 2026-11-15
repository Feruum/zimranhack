import { expect, test } from 'bun:test'
import { createProfileSchema, learnerToday } from '../services/thinktogether/profile'
import { formatDate } from '../services/thinktogether/helpers'

const now = new Date('2026-11-15T01:00:00Z')
const schema = createProfileSchema(() => now)
const profile = { goal: 'Prepare for PMP', exam_date: '2026-12-27', minutes_per_day: 45,
  timezone: 'Asia/Qyzylorda', language: 'en', program_id: '' }

test('a learner can save a goal and schedule before a program becomes available', async () => {
  expect(await schema.validate(profile)).toMatchObject(profile)
})

test('exam date validation follows the learner calendar instead of UTC', async () => {
  expect(learnerToday('America/Los_Angeles', now)).toBe('2026-11-14')
  expect(learnerToday('Asia/Qyzylorda', now)).toBe('2026-11-15')
  expect(await schema.isValid({ ...profile, program_id:'chosen', timezone: 'America/Los_Angeles', exam_date: '2026-11-14' })).toBe(true)
  expect(await schema.isValid({ ...profile, program_id:'chosen', exam_date: '2026-11-14' })).toBe(false)
})

test('unreal calendar dates, unknown timezones and fractional minutes cannot be saved', async () => {
  expect(await schema.isValid({ ...profile, program_id:'chosen', exam_date: '2027-02-30' })).toBe(false)
  expect(await schema.isValid({ ...profile, program_id:'chosen', timezone: 'Mars/Olympus' })).toBe(false)
  expect(learnerToday('Mars/Olympus', now)).toBe('')
  expect(await schema.isValid({ ...profile, program_id:'chosen', minutes_per_day: 5.5 })).toBe(false)
  expect(await schema.isValid({ ...profile, program_id:'chosen', minutes_per_day: 241 })).toBe(false)
})

test('an exam calendar date does not shift to tomorrow when displayed in UTC+14', () => {
  expect(formatDate('2026-12-27','Pacific/Kiritimati')).toBe('Dec 27, 2026')
  expect(formatDate('2026-12-27T23:00:00Z','Pacific/Kiritimati')).toContain('Dec 28, 2026')
})
