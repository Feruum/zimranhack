'use client'
import { Button } from '@components/ui/button'
import { Badge } from '@components/ui/badge'

import Link from 'next/link'
import { AnimatedNumber } from '@components/ui/skiper-ui/animated-number'
import { ArrowRight, Users } from 'lucide-react'
import { useTTQuery } from '@services/thinktogether/api'
import type { Cohort, Room } from '@services/thinktogether/types'
import { formatDate, humanize, scheduleLabel } from '@services/thinktogether/helpers'
import { EmptyState, PageHeading, styles } from './Shell'
import { ErrorNotice, Loading } from './Shared'
import GroupDiscussion from './GroupDiscussion'

export default function GroupPage() {
  const cohorts = useTTQuery<Cohort[]>('me/cohorts')
  const available = useTTQuery<Cohort[]>('cohorts')
  const rooms = useTTQuery<Room[]>('rooms')
  return <><PageHeading  title="My group"  /><div className={styles.grid}><div className={styles.stack}><section className={styles.panel}><h2>My learning group</h2>{cohorts.isPending ? <Loading /> : cohorts.error ? <ErrorNotice error={cohorts.error} retry={() => cohorts.refetch()} /> : !cohorts.data?.length ? <EmptyState title="No active group"><p>Complete your profile, assessments and weekly availability to find a group.</p><Button asChild><Link  href="/thinktogether/profile">Set my availability</Link></Button></EmptyState> : cohorts.data.map(cohort => <div key={cohort.id}><Badge variant="secondary" ><Users size={12} />{cohort.language}</Badge><h3>{cohort.title}</h3><p className={styles.muted}>{scheduleLabel(cohort.schedule, cohort.timezone)}</p>{!!cohort.members?.length && <ul className={styles.members}>{cohort.members.map((member, i) => <li key={member.user_uuid || i}><span className={styles.avatar}>{member.name.slice(0, 1)}</span><span>{member.name}</span></li>)}</ul>}</div>)}</section><section className={styles.panel}><h2>My sessions</h2>{rooms.isPending ? <Loading /> : rooms.error ? <ErrorNotice error={rooms.error} retry={() => rooms.refetch()} /> : !rooms.data?.length ? <p className={styles.muted}>No sessions scheduled.</p> : <ul className={styles.list}>{rooms.data.map(room => <li key={room.id}><div className={styles.panelHeader}><div><h3>{room.title}</h3><p>{formatDate(room.starts_at)} · {room.duration_minutes} minutes</p></div><Button asChild variant="outline" size="icon" className="shrink-0"><Link  aria-label={`Open ${room.title}`} href={`/thinktogether/rooms/${room.id}`}><ArrowRight size={18} /></Link></Button></div><Badge variant="secondary" >{humanize(room.phase)}</Badge></li>)}</ul>}</section>{!cohorts.isPending && !cohorts.error && <GroupDiscussion cohorts={cohorts.data || []} />}</div><aside className={styles.stack}><section className={styles.panel}><h2>Groups accepting learners</h2>{available.isPending ? <Loading /> : available.error ? <ErrorNotice error={available.error} /> : !available.data?.length ? <p className={styles.muted}>No groups accepting learners yet.</p> : <ul className={styles.list}>{available.data.map(cohort => <li key={cohort.id}><h3>{cohort.title}</h3><p>{scheduleLabel(cohort.schedule, cohort.timezone)}</p><p>{typeof cohort.member_count === 'number' ? <AnimatedNumber value={cohort.member_count} /> : '—'} confirmed learners · {cohort.language}</p></li>)}</ul>}<div className={styles.actions}><Button asChild variant="outline"><Link  href="/thinktogether/billing">View compatible access options</Link></Button></div></section></aside></div></>
}


