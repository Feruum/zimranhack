'use client'
import { Badge } from '@components/ui/badge'
import { Button } from '@components/ui/button'

import { useState } from 'react'
import { DisconnectButton, LiveKitRoom, RoomAudioRenderer, TrackToggle, useTracks, ParticipantTile } from '@livekit/components-react'
import { Track } from 'livekit-client'
import { ttPost } from '@services/thinktogether/api'
import { styles } from './Shell'
import { ErrorNotice } from './Shared'

export default function RoomMedia({ roomId, enabled }: { roomId: string; enabled: boolean }) {
  const [credentials, setCredentials] = useState<{ url: string; token: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [connected, setConnected] = useState(false)
  const [error, setError] = useState<Error | null>(null)
  async function join() { setBusy(true); setError(null); try { setCredentials(await ttPost(`rooms/${roomId}/media-token`)) } catch (e) { setError(e as Error) } finally { setBusy(false) } }
  return <section className={styles.panel}><div className={styles.panelHeader}><h2>Live conversation</h2>{connected && enabled && <Badge variant="secondary" >Connected session</Badge>}</div><ErrorNotice error={error} />{!enabled ? <p className={styles.muted}>Live conversation is closed during individual attempts, transfer, and the summary.</p> : credentials ? <LiveKitRoom token={credentials.token} serverUrl={credentials.url} connect video={false} audio={false} onConnected={() => setConnected(true)} onDisconnected={() => { setConnected(false); setCredentials(null) }} onError={e => setError(e)}><RoomParticipants /><RoomAudioRenderer /><div className={styles.mediaControls}><TrackToggle source={Track.Source.Microphone} className={styles.secondary}>Microphone</TrackToggle><TrackToggle source={Track.Source.Camera} className={styles.secondary}>Camera</TrackToggle><DisconnectButton className={styles.secondary}>Leave call</DisconnectButton></div><p className={styles.muted}>Use the controls to turn your microphone and camera on or off.</p></LiveKitRoom> : <><p className={styles.muted}>Join your group with your camera and microphone off. You control when to turn them on.</p><Button  disabled={busy} onClick={join}>{busy ? 'Connecting…' : 'Join live conversation'}</Button></>}</section>
}
function RoomParticipants() {
  const tracks = useTracks([{ source: Track.Source.Camera, withPlaceholder: true }], { onlySubscribed: false })
  return <div className={styles.videoGrid}>{tracks.map(track => <ParticipantTile trackRef={track} key={`${track.participant.identity}-${track.source}`} />)}</div>
}
