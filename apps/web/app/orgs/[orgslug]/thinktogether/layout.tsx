import type { ReactNode } from 'react'
import type { Metadata } from 'next'
import Shell from '@components/ThinkTogether/Shell'
import LearnHouseLayout from '../(withmenu)/layout'

export const metadata: Metadata = { title: { default: 'ThinkTogether', template: '%s · ThinkTogether' }, robots: { index: false, follow: false } }
export default function ThinkTogetherLayout({ children, params }: { children: ReactNode; params: Promise<{ orgslug: string }> }) {
  return <LearnHouseLayout params={params}><Shell>{children}</Shell></LearnHouseLayout>
}
