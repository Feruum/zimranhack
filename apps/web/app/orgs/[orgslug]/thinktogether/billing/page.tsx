import BillingPage from '@components/ThinkTogether/BillingPage'
export default async function Page({ searchParams }: { searchParams: Promise<{ checkout?: string }> }) { const params = await searchParams; return <BillingPage returned={params.checkout !== undefined} /> }
