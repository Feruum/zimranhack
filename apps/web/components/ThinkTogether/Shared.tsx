'use client'
import { Button } from '@components/ui/button'
import { Label } from '@components/ui/label'
import { Alert, AlertDescription } from '@components/ui/alert'

import type { ReactNode } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { ttApi } from '@services/thinktogether/api'
import { styles } from './Shell'

export function useTTMutation<T = unknown>(path: string, method = 'POST') {
  const client = useQueryClient()
  return useMutation({ mutationFn: (body: unknown) => ttApi<T>(path, { method, body: JSON.stringify(body) }), onSuccess: () => client.invalidateQueries({ queryKey: ['thinktogether'] }) })
}
export function ErrorNotice({ error, retry }: { error: Error | null | undefined; retry?: () => void }) {
  if (!error) return null
  return <Alert variant="destructive" className="my-3"><AlertDescription>{error.message}</AlertDescription>{retry && <Button variant="outline" size="sm" className="mt-3" onClick={retry}>Try again</Button>}</Alert>
}
export function Loading({ children = 'Loading…' }: { children?: ReactNode }) { return <p className="text-sm text-muted-foreground" role="status">{children}</p> }
export function Markdown({ children }: { children: string }) { return <div className={styles.prose}><ReactMarkdown remarkPlugins={[remarkGfm]}>{children}</ReactMarkdown></div> }
export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) { return <Label className="flex min-w-0 flex-col gap-2"><span>{label}</span>{children}{hint && <small className="text-xs font-normal leading-relaxed text-muted-foreground">{hint}</small>}</Label> }
export function Status({ children }: { children: ReactNode }) { return <p className="rounded-md bg-muted px-3 py-2 text-sm" role="status">{children}</p> }
