'use client'
import { Button } from '@components/ui/button'
import { Badge } from '@components/ui/badge'

import { useState } from 'react'
import Link from 'next/link'
import { ArrowUpRight, ShieldCheck } from 'lucide-react'
import { useTTQuery, ttPost } from '@services/thinktogether/api'
import type { Billing, Profile, Program } from '@services/thinktogether/types'
import { checkoutTarget, formatDate, humanize, scheduleLabel } from '@services/thinktogether/helpers'
import { EmptyState, PageHeading, styles } from './Shell'
import { ErrorNotice, Field, Loading } from './Shared'

export default function BillingPage({ returned = false }: { returned?: boolean }) {
  const profile = useTTQuery<Profile>('me/profile')
  const programs = useTTQuery<Program[]>('programs')
  const billing = useTTQuery<Billing>(`billing/status${profile.data?.program_id ? `?program_uuid=${encodeURIComponent(profile.data.program_id)}` : ''}`, !profile.isPending)
  const [cohort, setCohort] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)
  const data = billing.data
  const product = data?.products.find(p => p.program_uuid === profile.data?.program_id) || data?.products[0]
  async function checkout() {
    if (!product || !cohort) return
    setBusy(true); setError(null)
    try { const response = await ttPost<{ checkout_url: string }>('billing/checkout', { program_uuid: product.program_uuid, cohort_uuid: cohort }); window.location.assign(checkoutTarget(response.checkout_url)) } catch (e) { setError(e as Error); setBusy(false) }
  }
  return <><PageHeading  title="My access"  />{returned && <div className={styles.notice}><h2>Checking your payment</h2><p>Access activates after the payment provider confirms your order.</p><Button variant="outline"  onClick={() => billing.refetch()}>Refresh access</Button></div>}{billing.isPending ? <Loading>Checking your access…</Loading> : billing.error ? <ErrorNotice error={billing.error} retry={() => billing.refetch()} /> : data && <div className={styles.grid}><section className={styles.panel}><div className={styles.panelHeader}><h2>{data.has_active_access ? 'Your access is active' : 'Program access'}</h2><ShieldCheck size={22} /></div>{product ? <><p className="text-sm text-muted-foreground">{programs.data?.find(p => p.id === product.program_uuid)?.title || 'Learning program'}</p><div className={styles.price}>{new Intl.NumberFormat('en', { style: 'currency', currency: product.currency.toUpperCase(), maximumFractionDigits: 2 }).format(product.price_amount / 100)}<span> / {product.access_days} days</span></div><p className={styles.muted}>Program learning and scheduled group access for {product.access_days} days.</p></> : <p className={styles.muted}>Enrollment is not open yet.</p>}{!data.has_active_access && <>{data.cohorts.length > 0 ? <Field label="Choose a compatible group"><select className="h-10 w-full min-w-0 rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50" value={cohort} onChange={e => setCohort(e.target.value)}><option value="">Choose a group</option>{data.cohorts.map(c => <option key={c.cohort_uuid} value={c.cohort_uuid}>{c.title} · {scheduleLabel(c.schedule, c.timezone)}</option>)}</select></Field> : <p className={styles.notice}>No group currently matches your program, exam window, language, timezone, and availability.</p>}{!data.checkout_enabled && <p className={styles.muted}>{data.missing.includes('verified_email') ? 'Verify your email to continue.' : data.missing.includes('learning_readiness') ? 'Complete your profile and both assessments before enrolling.' : 'Enrollment is not open yet.'}</p>}<div className={styles.actions}><Button  disabled={!data.checkout_enabled || !product || !cohort || busy} onClick={checkout}>{busy ? 'Opening checkout…' : 'Continue to checkout'}<ArrowUpRight size={15} /></Button><Button asChild variant="outline"><Link  href="/thinktogether/profile">Review my profile</Link></Button></div><ErrorNotice error={error} /></>}</section><aside className={styles.panel}><h2>Access history</h2>{data.entitlements.length ? <ul className={styles.list}>{data.entitlements.map(grant => <li key={grant.entitlement_uuid}><h3>{programs.data?.find(p => p.id === grant.program_uuid)?.title || 'Program access'}</h3><Badge variant="secondary" >{humanize(grant.status)}</Badge><p>{formatDate(grant.starts_at)} — {formatDate(grant.expires_at)}</p><p>{grant.source === 'complimentary' ? 'Complimentary access' : humanize(grant.source)}</p></li>)}</ul> : <EmptyState title="No access history yet"><p>No confirmed access yet.</p></EmptyState>}</aside></div>}</>
}
