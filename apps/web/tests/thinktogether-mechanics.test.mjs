import { describe, expect, it } from 'bun:test'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import PracticeMechanics, { mechanicsInitialStep, mechanicsCanRespond, mechanicsTransition, practiceRequest } from '../components/ThinkTogether/PracticeMechanics'

describe('authored practice mechanics', () => {
  it('requires closing retell material before the explanation form can appear', () => {
    let step = mechanicsInitialStep('retell')
    expect(mechanicsCanRespond('retell', step)).toBe(false)
    step = mechanicsTransition('retell', step, 'close_source')
    expect(mechanicsCanRespond('retell', step)).toBe(true)
    step = mechanicsTransition('retell', step, 'read_again')
    expect(mechanicsCanRespond('retell', step)).toBe(false)
    expect(mechanicsCanRespond('think_first', mechanicsInitialStep('think_first'))).toBe(true)
  })
  it('selects authored banks and returns to a fresh same-principle case after coaching', () => {
    expect(practiceRequest('retell', 'skill-1')).toBe('practice/next?mode=retell&skill_id=skill-1')
    expect(practiceRequest('think_first', 'skill-1')).toBe('practice/next?mode=think_first&skill_id=skill-1')
    expect(practiceRequest('retell', 'skill-1', '', true)).toBe('practice/next?mode=case')
    expect(practiceRequest('case', '', 'review-1')).toBe('practice/next?mode=case&review_id=review-1')
  })
  it('renders retell material without an explanation input and gives ThinkFirst an explicit flawed example', () => {
    const props = { classes: {}, material: createElement('p', null, 'Authored teaching text'), children: createElement('textarea', { name: 'reasoning' }) }
    const retell = renderToStaticMarkup(createElement(PracticeMechanics, { ...props, mode: 'retell' }))
    expect(retell).toContain('Authored teaching text')
    expect(retell).not.toContain('<textarea')
    const critique = renderToStaticMarkup(createElement(PracticeMechanics, { ...props, mode: 'think_first' }))
    expect(critique).toContain('A flawed AI answer to critique')
    expect(critique).toContain('<textarea')
  })
})
