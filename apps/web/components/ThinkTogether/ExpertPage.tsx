'use client'

import { useState } from 'react'
import Link from 'next/link'
import { Button } from '@components/ui/button'
import { Badge } from '@components/ui/badge'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@components/ui/tabs'
import { useTTQuery } from '@services/thinktogether/api'
import { PageHeading, styles, useWorkspace } from './Shell'
import { ErrorNotice, Loading } from './Shared'
import ExpertContent from './expert/ExpertContent'
import ExpertGroups from './expert/ExpertGroups'
import ExpertReviews from './expert/ExpertReviews'
import ExpertBilling from './expert/ExpertBilling'
import ExpertSupport from './expert/ExpertSupport'

export default function ExpertPage() {
  const workspace = useWorkspace()
  const [tab, setTab] = useState('content')
  const readiness = useTTQuery<{ ready: boolean; checks: Record<string, boolean>; active_groups: number; pilot_capacity: number }>('readiness', !!workspace?.is_staff)
  if (!workspace?.is_staff) return <section className={styles.notice}><h2>Staff access required</h2><Button asChild variant="outline"><Link href="/thinktogether/today">Back to Today</Link></Button></section>
  const tabs = [
    ['content', 'Content', workspace.permissions.includes('content') || workspace.permissions.includes('psychometrics')],
    ['reviews', 'Assessment reviews', workspace.permissions.includes('content') || workspace.permissions.includes('psychometrics')],
    ['groups', 'Groups & sessions', workspace.permissions.includes('groups')],
    ['billing', 'Access & billing', workspace.permissions.includes('billing')],
    ['support', 'Support', workspace.permissions.includes('support')],
    ['readiness', 'Launch readiness', true],
  ] as const
  const active = tabs.some(([id, , allowed]) => id === tab && allowed) ? tab : tabs.find(([, , allowed]) => allowed)?.[0]
  return <>
    <PageHeading title="Expert workspace" action={<Button asChild variant="outline"><Link href="/dash">Course editor</Link></Button>} />
    <Tabs value={active} onValueChange={setTab}>
      <TabsList aria-label="Expert workspace" className="mb-4 h-auto max-w-full flex-wrap justify-start gap-1">
        {tabs.filter(([, , allowed]) => allowed).map(([id, label]) => <TabsTrigger key={id} value={id}>{label}</TabsTrigger>)}
      </TabsList>
      <TabsContent value="content">{active === 'content' && <ExpertContent />}</TabsContent>
      <TabsContent value="reviews">{active === 'reviews' && <ExpertReviews />}</TabsContent>
      <TabsContent value="groups">{active === 'groups' && <ExpertGroups />}</TabsContent>
      <TabsContent value="billing">{active === 'billing' && <ExpertBilling />}</TabsContent>
      <TabsContent value="support">{active === 'support' && <ExpertSupport />}</TabsContent>
      <TabsContent value="readiness"><section className={styles.panel}>
        <h2>{readiness.data?.ready ? 'Ready for launch' : 'Launch requirements'}</h2>
        {readiness.isPending ? <Loading /> : readiness.error ? <ErrorNotice error={readiness.error} retry={() => readiness.refetch()} /> : <>
          <p className={styles.muted}>{readiness.data?.active_groups} eligible groups · capacity {readiness.data?.pilot_capacity}</p>
          <ul className={styles.list}>{Object.entries(readiness.data?.checks || {}).map(([name, passed]) => <li className={styles.panelHeader} key={name}><span>{name.replace(/_/g, ' ')}</span><Badge variant="secondary">{passed ? 'Passed' : 'Required'}</Badge></li>)}</ul>
        </>}
        <div className={styles.actions}><Button variant="outline" onClick={() => readiness.refetch()}>Recheck</Button></div>
      </section></TabsContent>
    </Tabs>
  </>
}
