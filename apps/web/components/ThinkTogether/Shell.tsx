'use client'
import { Button } from '@components/ui/button'


import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { bindTTSessionRefresh, TTApiError, TTBootstrap, useTTQuery } from '@services/thinktogether/api'
import { useAuth } from '@components/Contexts/AuthContext'
import { useOrgMembership } from '@components/Contexts/OrgContext'
import GeneralWrapperStyled from '@components/Objects/StyledElements/Wrappers/GeneralWrapper'
import { getUriWithOrg } from '@services/config/config'
import styles from './thinktogether.module.css'

const Workspace = createContext<TTBootstrap | null>(null)
export const useWorkspace = () => useContext(Workspace)

export default function Shell({ children }: { children: ReactNode }) {
  const path = usePathname()
  const { orgslug } = useOrgMembership()
  const { refreshAccessToken, signOut } = useAuth()
  const query = useTTQuery<TTBootstrap>('bootstrap')
  const [recovering, setRecovering] = useState(false)
  const [recoveryError, setRecoveryError] = useState('')
  const recoveryStarted = useRef(false)
  const loginHref = `/login?redirect=${encodeURIComponent(path)}`
  const signIn = useCallback(async () => {
    setRecovering(true); setRecoveryError('')
    try {
      // Logout clears both httpOnly credentials and the session marker before
      // navigating, preventing the login proxy from bouncing back to this page.
      await signOut({ callbackUrl: loginHref, redirect: true })
    } catch {
      recoveryStarted.current = false; setRecovering(false)
      setRecoveryError('We could not open sign in. Please try again.')
    }
  }, [signOut, loginHref])
  useEffect(() => bindTTSessionRefresh(refreshAccessToken), [refreshAccessToken])
  useEffect(() => {
    if (query.error instanceof TTApiError && query.error.status === 401 && query.error.authFailure === 'terminal' && !recoveryStarted.current) {
      recoveryStarted.current = true
      void signIn()
    }
  }, [query.error, signIn])
  return <div className={`${styles.app} ${styles.native}`}>
    <a className={styles.skip} href="#tt-main">Skip to content</a>
    <GeneralWrapperStyled>
      <div className="mb-5 flex flex-wrap items-center justify-between gap-3 border-b pb-4">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
          <Link href={getUriWithOrg(orgslug, '/courses')} className="text-muted-foreground hover:text-foreground">Courses</Link>
          <span aria-hidden className="text-muted-foreground">/</span>
          <span className="font-semibold">ThinkTogether</span>
          <span className="rounded-md bg-muted px-2 py-1 text-xs text-muted-foreground">PMP preparation</span>
        </div>
      </div>
      <main id="tt-main">
        {query.isPending ? <div className={styles.loading} role="status">Opening your workspace…</div> : query.error ? <div className={styles.notice} role="alert"><h2>We couldn’t open your workspace.</h2><p>{query.error.message}</p>{recoveryError && <p>{recoveryError}</p>}<div className={styles.actions}><Button variant="outline"  disabled={recovering || query.isFetching} onClick={() => query.refetch()}>Try again</Button>{query.error instanceof TTApiError && query.error.authFailure === 'terminal' && <Button  disabled={recovering} onClick={() => void signIn()}>{recovering ? 'Opening sign in…' : 'Sign in'}</Button>}</div></div> : <Workspace.Provider value={query.data ?? null}>{children}</Workspace.Provider>}
        <footer className="mt-8 flex flex-wrap gap-x-4 gap-y-2 border-t pt-4 text-xs text-muted-foreground"><a className="underline underline-offset-4" href={query.data?.source_url || '/thinktogether-source.tar.gz'}>ThinkTogether source code · AGPL</a><a className="underline underline-offset-4" href="https://skiper-ui.com">UI components by Skiper UI</a></footer>
      </main>
    </GeneralWrapperStyled>
  </div>
}

export function PageHeading({ title, description, action }: { title: string; description?: string; action?: ReactNode }) {
  return <header className="mb-6 flex flex-wrap items-start justify-between gap-4"><div className="min-w-0 space-y-2"><h1 className="text-2xl font-semibold tracking-tight">{title}</h1>{description && <p className="max-w-prose text-sm text-muted-foreground">{description}</p>}</div>{action}</header>
}

export function EmptyState({ title, children }: { title: string; children: ReactNode }) {
  return <section className="space-y-3 py-6"><h2 className="text-lg font-semibold">{title}</h2><div className="space-y-3 text-sm text-muted-foreground">{children}</div></section>
}

export { styles }
