'use client'
import { Button } from '@components/ui/button'

import { useTTQuery } from '@services/thinktogether/api'
import type { Attempt } from '@services/thinktogether/types'
import { attemptSummary, humanize } from '@services/thinktogether/helpers'
import { styles } from './Shell'
import { ErrorNotice, Markdown } from './Shared'

export default function AttemptResult({ attempt }: { attempt: Attempt }) {
  const current = useTTQuery<Attempt>(`attempts/${attempt.id}`, attempt.score === null && attempt.assessment_kind !== 'cognitive')
  const data = current.data || attempt
  if (data.assessment_kind === 'cognitive') return <section className={styles.result} aria-live="polite"><h3>{attemptSummary(data)}</h3><p>Response saved. Private results appear after assessment completion.</p></section>
  return <section className={styles.result} aria-live="polite"><h3>{data.score === null ? 'Awaiting evaluation' : `${Math.round(data.score * 100)}% criterion score`}</h3>{data.score === null && <p>Response saved. Awaiting evaluation.</p>}{data.evaluation?.feedback && <Markdown>{data.evaluation.feedback}</Markdown>}{data.explanation && <><h4>Explanation</h4><Markdown>{data.explanation}</Markdown></>}{data.evaluation?.dimensions && <dl className={styles.resultDimensions}>{Object.entries(data.evaluation.dimensions).map(([name, score]) => <div key={name}><dt>{humanize(name)}</dt><dd>{Math.round(score * 100)}%</dd></div>)}</dl>}<p className={styles.muted}>{data.phase === 'preview' ? 'Sample preview only. This does not count toward mastery.' : data.independent_evidence ? 'This response is eligible to contribute independent evidence.' : data.assisted ? 'This assisted response is part of your learning; it does not establish independent mastery.' : 'Assessment results contribute according to the approved instrument.'}</p>{data.score === null && <Button variant="outline"  disabled={current.isFetching} onClick={() => current.refetch()}>Check evaluation</Button>}<ErrorNotice error={current.error} /></section>
}
