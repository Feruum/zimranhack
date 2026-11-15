import RoomPage from '@components/ThinkTogether/RoomPage'
export default async function Page({ params }: { params: Promise<{ roomId: string }> }) { const { roomId } = await params; return <RoomPage roomId={roomId} /> }
