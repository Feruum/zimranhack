import { describe, expect, test } from 'bun:test'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { AnimatedNumber } from '../components/ui/skiper-ui/animated-number'
import { SkiperLink } from '../components/ui/skiper-ui/css-link'

describe('Skiper UI in the learning interface', () => {
  test.each([0, 6, 180])('renders the real initial count %s before hydration', value => {
    const html = renderToStaticMarkup(createElement(AnimatedNumber, { value }))
    expect(html).toContain(`>${value}</span>`)
    expect(html).toContain('class="sr-only"')
    expect(html).toContain('aria-hidden="true"')
    if (value !== 0) expect(html).not.toContain('>0</span>')
  })

  test('a learning link preserves its destination, accessible name and same-tab navigation', () => {
    const html = renderToStaticMarkup(createElement(SkiperLink, {
      href: '/thinktogether/practice?skill=communication#case',
      'aria-label': 'Practice communication',
      prefetch: false,
    }, 'Practice this skill'))
    expect(html).toContain('href="/thinktogether/practice?skill=communication#case"')
    expect(html).toContain('aria-label="Practice communication"')
    expect(html).toContain('Practice this skill</a>')
    expect(html).not.toContain('target="_blank"')
  })
})
