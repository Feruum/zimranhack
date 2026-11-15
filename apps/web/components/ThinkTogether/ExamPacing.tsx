import Link from 'next/link'
import { Badge } from '@components/ui/badge'
import { Button } from '@components/ui/button'
import type { ExamDeadline } from '@services/thinktogether/types'

const labels: Record<ExamDeadline['status'], string> = {
  not_configured: 'Choose your exam date', exam_today: 'Your exam is today', exam_passed: 'Update your exam date',
  needs_content: 'Your route needs more approved material', at_risk: 'Your schedule needs adjustment',
  complete: 'Current preparation tasks completed', on_pace: 'Within your current study budget',
}
const hours = (minutes: number) => `${Math.round(minutes / 6) / 10} h`
const day = (value: string) => new Intl.DateTimeFormat('en', { dateStyle: 'medium', timeZone: 'UTC' }).format(new Date(`${value}T12:00:00Z`))

export default function ExamPacing({ deadline }: { deadline?: ExamDeadline }) {
  if (!deadline || deadline.status === 'not_configured') return null
  const hasWorkload = deadline.status !== 'needs_content'
  return <section className="space-y-4 rounded-xl border bg-background p-5 sm:p-6" aria-labelledby="tt-exam-pacing-title">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div className="space-y-1">
        <h2 id="tt-exam-pacing-title" className="text-lg font-semibold">Preparation to your exam</h2>
        <p className="text-sm text-muted-foreground">{day(deadline.exam_date)} · {deadline.days_remaining} study days remaining</p>
      </div>
      <Badge variant={deadline.status === 'on_pace' || deadline.status === 'complete' ? 'secondary' : 'outline'}>{labels[deadline.status]}</Badge>
    </div>
    {deadline.days_remaining !== null && deadline.days_remaining > 0 && <dl className="grid gap-4 text-sm sm:grid-cols-3">
      <div><dt className="text-muted-foreground">Available study time</dt><dd className="mt-1 font-medium">{hours(deadline.available_minutes)}</dd></div>
      <div><dt className="text-muted-foreground">Estimated remaining work</dt><dd className="mt-1 font-medium">{hasWorkload ? hours(deadline.required_minutes) : 'Not available yet'}</dd></div>
      <div><dt className="text-muted-foreground">Average pace needed</dt><dd className="mt-1 font-medium">{hasWorkload ? `${deadline.recommended_minutes_per_day} min / day` : 'Not available yet'}</dd></div>
    </dl>}
    {hasWorkload && deadline.shortfall_minutes > 0 && <p className="text-sm font-medium">At your current pace, approximately {hours(deadline.shortfall_minutes)} more study time is needed.</p>}
    {deadline.warnings.length > 0 && <ul className="list-disc space-y-2 ps-5 text-sm text-muted-foreground">{deadline.warnings.map(warning => <li key={warning.code}>{warning.message}</li>)}</ul>}
    <div className="flex flex-wrap gap-3">
      <Button variant="outline" size="sm" asChild><Link href="/thinktogether/profile">Adjust exam date or daily time</Link></Button>
      {deadline.mocks.length > 0 && <Button variant="outline" size="sm" asChild><Link href="/thinktogether/program">Plan a full mock exam</Link></Button>}
    </div>
    {deadline.weeks.length > 0 && <details className="border-t pt-4 text-sm">
      <summary className="w-fit cursor-pointer rounded-sm font-medium focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring">{hasWorkload ? 'View weekly targets' : 'View weekly availability'}</summary>
      {!hasWorkload && <p className="mt-3 text-muted-foreground">Learning targets need the remaining course material. Your available study time is shown below.</p>}
      <ol className="mt-4 divide-y">
        {deadline.weeks.map(week => <li key={week.number} className="space-y-2 py-4 first:pt-0">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="font-medium">Week {week.number} · {day(week.starts_on)}–{day(week.ends_on)}</h3>
            <span className="text-muted-foreground">{hasWorkload ? `${week.planned_minutes} min estimated / ${week.available_minutes} min available` : `${week.available_minutes} min available`}</span>
          </div>
          {hasWorkload && week.over_capacity && <p className="font-medium">This week needs more time than currently available.</p>}
          {hasWorkload && week.focus_skills.length > 0 && <p><span className="text-muted-foreground">Focus: </span>{week.focus_skills.map(skill => skill.title).join(', ')}</p>}
          {hasWorkload && week.review_count > 0 && <p className="text-muted-foreground">{week.review_count} retrieval or delayed checks</p>}
          {week.mocks.map(mock => <p key={mock.id}><Link className="underline underline-offset-4" href={mock.href}>{mock.title}</Link> · target {day(mock.target_date)} · {mock.duration_minutes} min uninterrupted</p>)}
          {hasWorkload && !week.focus_skills.length && !week.review_count && !week.mocks.length && <p className="text-muted-foreground">Space for follow-up practice as your results change.</p>}
        </li>)}
      </ol>
      {deadline.preview_truncated && <p className="mt-3 text-muted-foreground">Showing the next 12 weeks. Your route is recalculated as you progress.</p>}
    </details>}
    <p className="text-xs text-muted-foreground">{hasWorkload ? deadline.notice : 'Workload estimates need the remaining course material.'} {deadline.capacity_basis}</p>
  </section>
}
