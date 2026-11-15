'use client'
import { Button } from '@components/ui/button'

import { useState, type ReactNode } from 'react'
import type { PracticeMode } from '@services/thinktogether/types'

export type MechanicsStep = 'read' | 'respond'
export function mechanicsInitialStep(mode: PracticeMode): MechanicsStep { return mode === 'retell' ? 'read' : 'respond' }
export function mechanicsCanRespond(mode: PracticeMode, step: MechanicsStep) { return mode !== 'retell' || step === 'respond' }
export function mechanicsTransition(mode: PracticeMode, current: MechanicsStep, event: 'close_source' | 'read_again'): MechanicsStep {
  if (mode !== 'retell') return current
  return event === 'close_source' ? 'respond' : 'read'
}
export function practiceRequest(mode: PracticeMode, skill = '', review = '', transfer = false) {
  const params = new URLSearchParams({ mode: transfer || review ? 'case' : mode })
  if (!transfer) { if (review) params.set('review_id', review); else if (skill) params.set('skill_id', skill) }
  return `practice/next?${params}`
}

export default function PracticeMechanics({ mode, material, children, classes }: {
  mode: PracticeMode; material?: ReactNode; children: ReactNode
  classes: Readonly<Record<string, string>>
}) {
  const [step, setStep] = useState(() => mechanicsInitialStep(mode))
  if (mode === 'case') return <>{children}</>
  if (!mechanicsCanRespond(mode, step)) return <section aria-label="Retell study material">
    <aside className={classes.notice}><h3>Read the principle</h3><p>Read the material, then close it and explain the idea in your own words.</p>{material}</aside>
    <div className={classes.actions}><Button type="button" onClick={() => setStep(mechanicsTransition(mode, step, 'close_source'))}>Close material and explain</Button></div>
  </section>
  return <>
    {mode === 'retell' ? <aside className={classes.notice}><p role="status">The material is closed. Explain the principle from memory and describe when you would use it.</p><Button type="button" variant="link" size="sm" className="h-auto px-0" onClick={() => setStep(mechanicsTransition(mode, step, 'read_again'))}>Read again · clears your draft</Button></aside>
      : <aside className={classes.notice}><h3>A flawed AI answer to critique</h3><p className={classes.muted}>This authored example is intentionally flawed. Identify its mistakes and explain a better decision.</p>{material}</aside>}
    <p className={classes.muted}>This is coached practice. Your next step is a fresh independent case on the same principle.</p>
    {children}
  </>
}
