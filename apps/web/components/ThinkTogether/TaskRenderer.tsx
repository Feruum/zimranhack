'use client'
import { Badge } from '@components/ui/badge'
import { Label } from '@components/ui/label'
import { Input } from '@components/ui/input'
import { Textarea } from '@components/ui/textarea'
import { Button } from '@components/ui/button'

import { useState } from 'react'
import { Formik, Form } from 'formik'
import type { Item, ResponseValue } from '@services/thinktogether/types'
import { responseFor } from '@services/thinktogether/helpers'
import { ErrorNotice, Field, Markdown } from './Shared'
import { styles } from './Shell'
import PracticeMechanics from './PracticeMechanics'

export default function TaskRenderer({ item, submit, pending, button = 'Submit answer', initial }: { item: Item; submit: (_response: ResponseValue) => Promise<unknown>; pending?: boolean; button?: string; initial?: ResponseValue }) {
  const [error, setError] = useState<Error | null>(null)
  const mode = item.practice_mode || 'case'
  return <section aria-label={item.title}>
    {item.is_sample && <Badge variant="secondary" >Draft sample preview · no mastery evidence</Badge>}
    <h2>{item.title}</h2><Markdown>{item.prompt}</Markdown>
    <PracticeMechanics mode={mode} classes={styles} material={mode === 'retell' ? <Markdown>{item.source_material || ''}</Markdown> : mode === 'think_first' ? <Markdown>{item.ai_answer || ''}</Markdown> : undefined}><Formik initialValues={{ choice: initial && 'choice' in initial ? initial.choice : '', choices: initial && 'choices' in initial ? initial.choices : [] as string[], value: initial && 'value' in initial ? String(initial.value) : '', text: initial && 'text' in initial ? initial.text : '', matches: initial && 'matches' in initial ? initial.matches : {} as Record<string, string> }} onSubmit={async (values) => {
      setError(null)
      try { await submit(responseFor(item, values)) } catch (e) { setError(e instanceof Error ? e : new Error('We could not save your answer.')) }
    }}>{({ values, setFieldValue, isSubmitting }) => <Form>
      {(item.kind === 'single_choice' || item.kind === 'multiple_choice') && <fieldset className={styles.fieldset}><legend>{item.kind === 'single_choice' ? 'Choose one answer' : 'Choose all answers that apply'}</legend>{item.choices.map(choice => <Label className={styles.choice} key={choice.id}><input className="mt-0.5 size-4 shrink-0 accent-primary" type={item.kind === 'single_choice' ? 'radio' : 'checkbox'} name={item.kind === 'single_choice' ? 'choice' : 'choices'} value={choice.id} checked={item.kind === 'single_choice' ? values.choice === choice.id : values.choices.includes(choice.id)} onChange={event => item.kind === 'single_choice' ? setFieldValue('choice', choice.id) : setFieldValue('choices', event.target.checked ? [...values.choices, choice.id] : values.choices.filter(c => c !== choice.id))} /><span>{choice.text}</span></Label>)}</fieldset>}
      {item.kind === 'matching' && <fieldset className={styles.fieldset}><legend>Match each item with one option</legend><div className={styles.stack}>{item.choices.filter(c => c.side === 'left').map(choice => <Field key={choice.id} label={choice.text}><select className="h-10 w-full min-w-0 rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50" value={values.matches[choice.id] || ''} onChange={e => setFieldValue('matches', { ...values.matches, [choice.id]: e.target.value })} required><option value="">Choose a match</option>{item.choices.filter(c => c.side === 'right').map(option => <option key={option.id} value={option.id}>{option.text}</option>)}</select></Field>)}</div></fieldset>}
      {item.kind === 'numeric' && <Field label="Your answer"><Input name="value" inputMode="decimal" value={values.value} onChange={e => setFieldValue('value', e.target.value)} required autoComplete="off" /></Field>}
      {item.kind === 'reasoning' && <Field label={mode === 'retell' ? 'Explain it in your own words' : mode === 'think_first' ? 'Your critique and correction' : 'Your reasoning'} hint={mode === 'think_first' ? 'Identify the flawed claim, correct it, and justify your decision.' : mode === 'retell' ? 'Explain the principle from memory and give an example of when it applies.' : 'Explain the decision and the evidence behind it.'}><Textarea name="text" value={values.text} onChange={e => setFieldValue('text', e.target.value)} maxLength={20000} required rows={7} /></Field>}
      <ErrorNotice error={error} /><div className={styles.actions}><Button  type="submit" disabled={pending || isSubmitting}>{isSubmitting || pending ? 'Saving…' : button}</Button></div>
    </Form>}</Formik></PracticeMechanics>
  </section>
}
