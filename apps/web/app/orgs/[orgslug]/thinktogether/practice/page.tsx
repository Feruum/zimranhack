import PracticePage from '@components/ThinkTogether/PracticePage'
export default async function Page({ searchParams }: { searchParams: Promise<{ skill?: string; review?: string; review_id?: string }> }) { const params = await searchParams; return <PracticePage initialSkill={params.skill} initialReview={params.review || params.review_id} /> }
