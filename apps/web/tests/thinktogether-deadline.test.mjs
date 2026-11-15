import { expect, test } from 'bun:test'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import ExamPacing from '../components/ThinkTogether/ExamPacing'

function route(overrides = {}) {
  return { version:'exam-pacing-v1', status:'at_risk', exam_date:'2027-01-01', days_remaining:42, weeks_remaining:6,
    available_minutes:1890, required_minutes:2100, shortfall_minutes:210, recommended_minutes_per_day:50,
    independent_applications_remaining:12, pending_review_count:3, content_gaps:[],
    warnings:[{code:'time_shortfall', message:'More study time is needed before this date.'}],
    mocks:[{id:'m1',title:'Full mock A',duration_minutes:240,target_date:'2026-12-18',href:'/thinktogether/program'}],
    weeks:[{number:1,starts_on:'2026-11-20',ends_on:'2026-11-26',available_minutes:315,planned_minutes:340,
      focus_skills:[{id:'risk',title:'Risk response',target_date:'2026-11-20'}],review_count:2,mocks:[],over_capacity:true}],
    notice:'Estimated remaining study work, not an exam pass prediction.', capacity_basis:'Daily individual study time.', ...overrides }
}

test('deadline view shows the learner the actual time deficit, weekly work and recovery links', () => {
  const html = renderToStaticMarkup(createElement(ExamPacing, {deadline:route()}))
  expect(html).toContain('42 study days remaining')
  expect(html).toContain('31.5 h')
  expect(html).toContain('35 h')
  expect(html).toContain('50 min / day')
  expect(html).toContain('3.5 h more study time')
  expect(html).toContain('Risk response')
  expect(html).toContain('340 min estimated / 315 min available')
  expect(html).toContain('href="/thinktogether/profile"')
  expect(html).toContain('href="/thinktogether/program"')
  expect(html).toContain('not an exam pass prediction')
})

test('absence of an exam date does not invent a countdown or preparation promise', () => {
  expect(renderToStaticMarkup(createElement(ExamPacing))).toBe('')
  expect(renderToStaticMarkup(createElement(ExamPacing, {deadline:route({status:'not_configured'})}))).toBe('')
})

test('an expired exam date asks for a new date without offering a negative schedule', () => {
  const html = renderToStaticMarkup(createElement(ExamPacing, {deadline:route({status:'exam_passed',days_remaining:0,weeks:[],mocks:[],shortfall_minutes:0,warnings:[]})}))
  expect(html).toContain('Update your exam date')
  expect(html).not.toContain('Average pace needed')
  expect(html).not.toContain('View weekly targets')
})

test('incomplete course material cannot appear as a complete workload or a daily preparation recommendation', () => {
  const html = renderToStaticMarkup(createElement(ExamPacing, {deadline:route({
    status:'needs_content', required_minutes:45, recommended_minutes_per_day:2, shortfall_minutes:0,
    content_gaps:['risk'], mocks:[], warnings:[{code:'content_gap',message:'More course material is needed.'}],
  })}))
  expect(html).toContain('31.5 h')
  expect(html).toContain('315 min available')
  expect(html).toContain('View weekly availability')
  expect(html).not.toContain('0.8 h')
  expect(html).not.toContain('2 min / day')
  expect(html).not.toContain('340 min estimated')
  expect(html).not.toContain('Focus:')
})
