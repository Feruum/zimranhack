import ProgramPage from '@components/ThinkTogether/ProgramPage'
export default async function Page({ searchParams }: { searchParams: Promise<{ lesson?: string; mock?: string }> }) { const params = await searchParams; return <ProgramPage initialLesson={params.lesson} initialMock={params.mock} /> }
