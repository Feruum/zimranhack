'use client'

import { useState } from 'react'
import Link from 'next/link'
import { ArrowRight, CalendarDays, Check, Circle, Clock3 } from 'lucide-react'
import { Badge } from '@components/ui/badge'
import { Button } from '@components/ui/button'
import { Input } from '@components/ui/input'
import { Label } from '@components/ui/label'
import { AnimatedNumber } from '@components/ui/skiper-ui/animated-number'
import { SkiperLink } from '@components/ui/skiper-ui/css-link'
import { useTTQuery } from '@services/thinktogether/api'
import type { Today, Room } from '@services/thinktogether/types'
import { activityHref, formatDate, humanize } from '@services/thinktogether/helpers'
import { ErrorNotice, Loading, useTTMutation } from './Shared'
import Notifications from './Notifications'
import ExamPacing from './ExamPacing'

export default function TodayPage() {
  const today = useTTQuery<Today>('today')
  const rooms = useTTQuery<Room[]>('rooms')
  const [budget, setBudget] = useState('')
  const [now] = useState(() => Date.now())
  const plan = useTTMutation('plan')

  if (today.isPending) return <Loading />
  if (!today.data) return <ErrorNotice error={today.error} retry={() => today.refetch()} />

  const { profile, plan: daily, skills } = today.data
  const nextRoom = rooms.data?.find(room => new Date(room.starts_at).getTime() + room.duration_minutes * 60000 > now)
  const checks = [
    ['Goal, program and schedule', profile.readiness.profile_complete],
    ['Domain diagnostic', profile.readiness.domain_diagnostic_complete],
    ['Cognitive assessment', profile.readiness.cognitive_complete],
  ] as const

  return <div className="space-y-6">
    <header className="flex flex-wrap items-center justify-between gap-3">
      <h1 className="text-2xl font-semibold tracking-tight">Today</h1>
      <span className="flex items-center gap-2 text-sm text-muted-foreground">
        <CalendarDays size={16} aria-hidden="true" />
        {formatDate(daily.plan_date)}
      </span>
    </header>

    {!profile.readiness.ready && <section aria-labelledby="tt-setup-title" className="space-y-4 rounded-xl border bg-muted/40 p-5 sm:p-6">
      <div className="space-y-1">
        <h2 id="tt-setup-title" className="text-lg font-semibold">Complete your setup</h2>
        <p className="text-sm text-muted-foreground">Set your goal and complete both assessments.</p>
      </div>
      <ul className="flex flex-wrap gap-x-6 gap-y-3 text-sm">
        {checks.map(([label, done]) => <li key={label} className="flex items-center gap-2">
          {done ? <Check size={16} aria-hidden="true" /> : <Circle size={16} aria-hidden="true" className="text-muted-foreground" />}
          <span>{label}<span className="sr-only">: {done ? 'complete' : 'incomplete'}</span></span>
        </li>)}
      </ul>
      <Button asChild><Link href="/thinktogether/profile">
        {profile.readiness.profile_complete ? 'Continue assessments' : 'Set up my profile'}
        <ArrowRight size={16} aria-hidden="true" />
      </Link></Button>
    </section>}

    <ExamPacing deadline={daily.deadline} />

    <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(16rem,20rem)]">
      <section aria-labelledby="tt-plan-title" className="min-w-0 rounded-xl border bg-background">
        <div className="flex flex-wrap items-center justify-between gap-3 px-5 pt-5 sm:px-6 sm:pt-6">
          <h2 id="tt-plan-title" className="text-lg font-semibold">Learning plan</h2>
          <Badge variant="secondary" className="gap-1.5">
            <Clock3 size={14} aria-hidden="true" />
            <AnimatedNumber value={daily.total_minutes} /> / <AnimatedNumber value={daily.budget_minutes} /> min
          </Badge>
        </div>

        <div className="px-5 sm:px-6">
          {daily.activities.length ? <ol className="divide-y py-2">
            {daily.activities.map((activity, index) => {
              const title = activity.title || skills.find(skill => skill.id === activity.skill_id)?.title || 'Independent practice'
              return <li key={`${activity.kind}-${activity.lesson_id || activity.item_id || index}`} className="flex items-start justify-between gap-4 py-5">
                <div className="min-w-0 flex-1 space-y-2">
                  <p className="text-xs text-muted-foreground">{humanize(activity.kind)} · {activity.minutes} min</p>
                  <h3 className="font-medium">{title}</h3>
                  {activity.reason && <details className="text-sm text-muted-foreground">
                    <summary className="w-fit cursor-pointer rounded-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring">Why this task?</summary>
                    <p className="mt-2 max-w-prose">{activity.reason}</p>
                  </details>}
                </div>
                <Button variant="outline" size="sm" asChild><Link href={activityHref(activity)} aria-label={`Start ${title}`}>
                  Start <ArrowRight size={14} aria-hidden="true" />
                </Link></Button>
              </li>
            })}
          </ol> : <div className="space-y-2 py-8">
            <h3 className="font-medium">{skills.length === 0 && profile.readiness.ready ? 'Your curriculum is being prepared.' : 'No tasks assigned yet.'}</h3>
            {!profile.readiness.ready && <p className="text-sm text-muted-foreground">Complete your setup to continue.</p>}
          </div>}

          {daily.status === 'partial' && daily.activities.length > 0 && <p className="pb-4 text-sm text-muted-foreground">More tasks are needed to fill this plan.</p>}

          {daily.rationale.length > 0 && <details className="border-t py-4 text-sm">
            <summary className="w-fit cursor-pointer rounded-sm font-medium focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring">Why this plan?</summary>
            <ul className="mt-3 list-disc space-y-2 ps-5 text-muted-foreground">
              {daily.rationale.map((reason, index) => <li key={`${index}-${reason}`}>{reason}</li>)}
            </ul>
          </details>}
        </div>

        <form className="space-y-3 border-t p-5 sm:p-6" onSubmit={event => {
          event.preventDefault()
          plan.mutate({ budget_minutes: Number(budget || daily.budget_minutes) })
        }}>
          <Label htmlFor="tt-budget">Time available today</Label>
          <div className="flex flex-wrap items-center gap-3">
            <Input id="tt-budget" type="number" className="h-10 w-24" min={5} max={240} value={budget || daily.budget_minutes} onChange={event => setBudget(event.target.value)} required />
            <span className="text-sm text-muted-foreground">minutes</span>
            <Button variant="outline" type="submit" disabled={plan.isPending} aria-busy={plan.isPending}>
              {plan.isPending ? 'Updating…' : 'Adjust plan'}
            </Button>
          </div>
          <ErrorNotice error={plan.error} />
        </form>
      </section>

      <aside className="min-w-0 space-y-6">
        <section aria-labelledby="tt-goal-title" className="space-y-3 rounded-xl border p-5 sm:p-6">
          <h2 id="tt-goal-title" className="text-lg font-semibold">Your goal</h2>
          <p className="text-sm">{profile.goal || 'No goal set.'}</p>
          <p className="text-sm text-muted-foreground">{profile.exam_date ? `Exam · ${formatDate(profile.exam_date)}` : 'No exam date set.'}</p>
          <SkiperLink className="text-sm font-medium" href="/thinktogether/profile">Edit goal <ArrowRight size={14} aria-hidden="true" /></SkiperLink>
        </section>

        <section aria-labelledby="tt-session-title" className="space-y-3 rounded-xl border p-5 sm:p-6">
          <h2 id="tt-session-title" className="text-lg font-semibold">Next session</h2>
          {rooms.isPending ? <Loading /> : rooms.error ? <ErrorNotice error={rooms.error} retry={() => rooms.refetch()} /> : nextRoom ? <>
            <h3 className="font-medium">{nextRoom.title}</h3>
            <p className="text-sm text-muted-foreground">{formatDate(nextRoom.starts_at, profile.timezone)}</p>
            <Badge variant="secondary">{nextRoom.duration_minutes} min · {humanize(nextRoom.phase)}</Badge>
            <div><Button asChild><Link href={`/thinktogether/rooms/${nextRoom.id}`}>Open session <ArrowRight size={16} aria-hidden="true" /></Link></Button></div>
          </> : <>
            <p className="text-sm text-muted-foreground">No sessions scheduled.</p>
            <SkiperLink className="text-sm font-medium" href="/thinktogether/group">View group <ArrowRight size={14} aria-hidden="true" /></SkiperLink>
          </>}
        </section>

        <Notifications />
      </aside>
    </div>
  </div>
}
