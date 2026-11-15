'use client'
import { Textarea } from '@components/ui/textarea'
import { Input } from '@components/ui/input'
import { Label } from '@components/ui/label'
import { Button } from '@components/ui/button'

import { Formik, Form } from 'formik'
import { createProfileSchema, learnerToday } from '@services/thinktogether/profile'
import { useTTQuery } from '@services/thinktogether/api'
import type { Profile, Program } from '@services/thinktogether/types'
import { PageHeading, styles } from './Shell'
import { ErrorNotice, Field, Loading, Status, useTTMutation } from './Shared'
import Diagnostics from './Diagnostics'
import SupportPanel from './SupportPanel'

const schema = createProfileSchema()
export default function ProfilePage() {
  const profile = useTTQuery<Profile>('me/profile')
  const programs = useTTQuery<Program[]>('programs')
  const save = useTTMutation<Profile>('me/profile', 'PUT')
  const preferences = useTTQuery<{ email_reminders: boolean }>('notification-preferences')
  const preference = useTTMutation('notification-preferences', 'PUT')
  if (profile.isPending || programs.isPending) return <Loading>Loading your profile…</Loading>
  if (!profile.data) return <ErrorNotice error={profile.error} retry={() => profile.refetch()} />
  return <><PageHeading  title="My profile"  /><div className={styles.grid}><section className={styles.panel}><h2>Your learning profile</h2><Formik initialValues={{ ...profile.data, program_id: profile.data.program_id || '' }} enableReinitialize validationSchema={schema} onSubmit={async values => { await save.mutateAsync(values).catch(() => undefined) }}>{({ values, errors, touched, handleChange, setFieldValue, isSubmitting }) => <Form className={styles.form}>
    <Field label="What do you want to achieve?"><Textarea name="goal" value={values.goal} onChange={handleChange} required maxLength={2000} placeholder="Your learning goal" /></Field>{touched.goal && errors.goal && <span role="alert" className={styles.inlineError}>{errors.goal}</span>}
    <Field label="Program"><select className="h-10 w-full min-w-0 rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50" name="program_id" value={values.program_id} onChange={handleChange} disabled={!programs.data?.length || !!programs.error}><option value="" disabled>Choose your program</option>{programs.data?.map(p => <option key={p.id} value={p.id}>{p.title}{p.is_sample ? ' — staff sample preview' : ''}</option>)}</select></Field><ErrorNotice error={programs.error} retry={() => programs.refetch()} />{programs.isSuccess && !programs.data.length && <p className={styles.muted}>No programs are available yet. You can still save your goal and schedule.</p>}
    <div className={styles.formColumns}><Field label="Target exam date"><Input name="exam_date" type="date" value={values.exam_date} onChange={handleChange} min={learnerToday(values.timezone) || undefined} required /></Field><Field label="Daily learning time (minutes)"><Input name="minutes_per_day" type="number" value={values.minutes_per_day} onChange={handleChange} min={5} max={240} step={1} required /></Field></div>
    <div className={styles.formColumns}><Field label="Your timezone" hint="For example: Asia/Qyzylorda or America/New_York."><Input name="timezone" value={values.timezone} onChange={handleChange} list="tt-timezones" required /><datalist id="tt-timezones">{['UTC', 'Asia/Qyzylorda', 'Asia/Almaty', 'Europe/London', 'Europe/Berlin', 'America/New_York', 'America/Los_Angeles', 'Asia/Dubai'].map(tz => <option key={tz}>{tz}</option>)}</datalist></Field><Field label="Learning language"><select className="h-10 w-full min-w-0 rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50" name="language" value={values.language} onChange={handleChange}><option value="en">English</option><option value="ru">Russian</option><option value="kk">Kazakh</option></select></Field></div>
    <fieldset className={styles.fieldset}><legend>Weekly group availability</legend><p className={styles.muted}>Meeting times in your local timezone.</p>{values.schedule_slots.map((slot, index) => <div className={styles.slot} key={index}><Label><span className={styles.srOnly}>Day for availability {index + 1}</span><select className="h-10 w-full min-w-0 rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50" value={slot.weekday} onChange={e => setFieldValue(`schedule_slots.${index}.weekday`, Number(e.target.value))}>{['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'].map((day, i) => <option value={i} key={day}>{day}</option>)}</select></Label><Label><span className={styles.srOnly}>Time for availability {index + 1}</span><Input type="time" required value={slot.local_time} onChange={e => setFieldValue(`schedule_slots.${index}.local_time`, e.target.value)} /></Label><Button variant="link" size="sm" className="h-auto px-0 text-xs" type="button"  onClick={() => setFieldValue('schedule_slots', values.schedule_slots.filter((_, i) => i !== index))}>Remove</Button></div>)}<Button variant="outline" type="button"  disabled={values.schedule_slots.length >= 30} onClick={() => setFieldValue('schedule_slots', [...values.schedule_slots, { weekday: 0, local_time: '18:00' }])}>Add available time</Button></fieldset>
    {Object.keys(errors).length > 0 && Object.keys(touched).length > 0 && <p className={styles.inlineError} role="alert">Check your goal, exam date, daily time, and timezone.</p>}<ErrorNotice error={save.error} />{save.isSuccess && <Status>Profile saved.</Status>}<Button  disabled={isSubmitting}>Save my profile</Button>
  </Form>}</Formik></section><aside className={styles.stack}><section className={styles.panel}><h2>Email reminders</h2>{preferences.isPending ? <Loading /> : <Label className={styles.choice}><input className="mt-0.5 size-4 shrink-0 accent-primary" type="checkbox" checked={preferences.data?.email_reminders || false} disabled={preference.isPending || !!preferences.error} onChange={e => preference.mutate({ email_reminders: e.target.checked })} /><span>Learning and session reminders</span></Label>}<ErrorNotice error={preferences.error || preference.error} />{preference.isSuccess && <Status>Reminder preference saved.</Status>}</section></aside></div><Diagnostics profileReady={profile.data.readiness.profile_complete} /><SupportPanel /></>
}
