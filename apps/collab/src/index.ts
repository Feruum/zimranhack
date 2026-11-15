import 'dotenv/config'
import { createThinkTogetherGuard } from './thinktogether.js'
import * as Y from 'yjs'
import { appendAgreedNote, handleBoardNoteRequest, type AgreedNote } from './thinktogether-notes.js'
import { Server } from '@hocuspocus/server'
import type {
  onRequestPayload,
  onUpgradePayload,
  onAuthenticatePayload,
  beforeHandleMessagePayload,
  beforeHandleAwarenessPayload,
  onChangePayload,
  afterLoadDocumentPayload,
  afterUnloadDocumentPayload,
} from '@hocuspocus/server'
import { Database } from '@hocuspocus/extension-database'
import jwt from 'jsonwebtoken'
import Redis from 'ioredis'
import {
  CONNECTION_ID_HEADER,
  clientIp,
  createConnectionGuard,
  createRateLimiter,
} from './connectionGuard.js'
import { createDocSizeGuard } from './docLimits.js'
import { sanitizeAwareness, seatRefusal } from './boardPresence.js'
import { createBoardPersistence } from './persistence.js'
import { createBoardSnapshotWriter } from './httpPersistence.js'
import { createRedisBoardBacklog, DIRTY_BOARDS_KEY } from './redisBacklog.js'
import {
  type ConnectionAuth,
  type TokenState,
  createAccessChecker,
  dropConnection,
  recheckConnections,
} from './accessRecheck.js'

const PORT = parseInt(process.env.COLLAB_PORT || '4000', 10)
const API_URL = process.env.LEARNHOUSE_API_URL || 'http://localhost:8000'
const SECRET_KEY = process.env.LEARNHOUSE_AUTH_JWT_SECRET_KEY || ''
const INTERNAL_KEY = process.env.COLLAB_INTERNAL_KEY || ''
const REDIS_URL = process.env.LEARNHOUSE_REDIS_URL || 'redis://localhost:6379'
// Only honor x-forwarded-for when a trusted proxy is known to set it.
// Without this flag, a direct client can spoof the header per request and
// bypass the rate limiter by presenting a fresh IP each time.
const TRUST_PROXY = process.env.COLLAB_TRUST_PROXY === 'true'

// Timeout for all outbound HTTP requests (ms)
const FETCH_TIMEOUT_MS = 10_000

// Debounce interval before flushing ydoc state to the database (ms)
const DB_FLUSH_DELAY = 5000
// Redis TTL for cached ydoc state (seconds): 1 hour
const REDIS_YDOC_TTL = 3600
// Largest Yjs state we persist (bytes): 5 MB
const MAX_YDOC_BYTES = 5 * 1024 * 1024
// Largest single incoming WebSocket message (bytes): 2 MB
const MAX_MESSAGE_BYTES = 2 * 1024 * 1024
// How often open connections re-check their access to the board (ms)
const ACCESS_RECHECK_MS = 60_000
// Longest a connection stays open without a successful re-check (ms)
const MAX_UNVERIFIED_MS = 10 * 60_000

// ── Startup validation ──────────────────────────────────────────────────────

// Placeholders shipped in .env.example (old and current) must never be used.
const PLACEHOLDER_SECRETS = new Set([
  'your-jwt-secret-key',
  'your-internal-key',
  'CHANGE_ME_generate_with_openssl_rand_hex_32',
])
const MIN_SECRET_LENGTH = 32

function requireStrongSecret(name: string, value: string) {
  if (!value) {
    console.error(`[collab] FATAL: ${name} is not set`)
    process.exit(1)
  }
  if (PLACEHOLDER_SECRETS.has(value)) {
    console.error(
      `[collab] FATAL: ${name} is still the example placeholder (generate one with: openssl rand -hex 32)`,
    )
    process.exit(1)
  }
  // Warn rather than refuse so an existing deployment keeps booting.
  if (value.length < MIN_SECRET_LENGTH) {
    console.warn(
      `[collab] ${name} is shorter than ${MIN_SECRET_LENGTH} characters; rotate it (openssl rand -hex 32)`,
    )
  }
}

requireStrongSecret('LEARNHOUSE_AUTH_JWT_SECRET_KEY', SECRET_KEY)
requireStrongSecret('COLLAB_INTERNAL_KEY', INTERNAL_KEY)

// ── Fetch with timeout helper ─────────────────────────────────────────────

function fetchWithTimeout(
  url: string,
  options: RequestInit = {},
  timeoutMs = FETCH_TIMEOUT_MS,
): Promise<Response> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  return fetch(url, { ...options, signal: controller.signal }).finally(() =>
    clearTimeout(timer),
  )
}

// ── Rate limiting ───────────────────────────────────────────────────────────
// Plain HTTP requests (health checks, probes) per IP
const RATE_LIMIT_WINDOW_MS = 60_000 // 1 minute
const RATE_LIMIT_MAX = 30 // max requests per IP per window
const requestLimiter = createRateLimiter(RATE_LIMIT_WINDOW_MS, RATE_LIMIT_MAX)

// WebSocket upgrades never reach onRequest, so they are limited separately
const connectionGuard = createConnectionGuard({
  trustProxy: TRUST_PROXY,
  rateLimitWindowMs: RATE_LIMIT_WINDOW_MS,
  // Sized for a whole classroom behind one NAT reconnecting at once
  rateLimitMax: 300, // new sockets per IP per window (reconnects included)
  maxConnectionsPerIp: 200, // open sockets per IP (shared NAT, many tabs)
  // Last-resort memory bound; per-socket limits below keep each one cheap,
  // so this only bites under a flood
  maxPendingConnections: 2000, // unauthenticated sockets across all IPs
  authDeadlineMs: 10_000,
  preAuthMaxBytes: 256 * 1024, // auth plus the first sync step is a few KB
  // Room for a few full-size messages in flight; anything else is queued for
  // a document the socket never authenticated
  maxUnhandledBytes: 4 * MAX_MESSAGE_BYTES,
  maxHandledBytesPerWindow: 16 * 1024 * 1024,
})

// Stops accepting updates for a document once it outgrows what we persist
const docSizes = createDocSizeGuard({
  maxDocBytes: MAX_YDOC_BYTES,
  remeasureEveryBytes: 256 * 1024,
})
const DOC_TOO_LARGE = { code: 4413, reason: 'Board is too large' }

// Clean up stale entries every 5 minutes
const rateLimitCleanupInterval = setInterval(() => {
  requestLimiter.sweep()
  connectionGuard.sweep()
}, 5 * 60_000)

function extractBoardUuid(documentName: string): string | null {
  // Room naming: board:{board_uuid}. The uuid goes straight into API paths
  // and Redis keys, so only the exact shape the API mints is accepted.
  const match = documentName.match(/^board:(board_[0-9a-f-]{36})$/)
  return match ? match[1] : null
}

function fetchMembership(boardUuid: string, token: string): Promise<Response> {
  return fetchWithTimeout(`${API_URL}/api/v1/boards/${boardUuid}/membership`, {
    headers: { Authorization: `Bearer ${token}` },
  })
}

// Tokens this close to expiry are treated as expired by the re-check, so a
// clock skew with the API can't turn "about to lapse" into a revoke.
const TOKEN_EXPIRY_MARGIN_MS = 30_000

function tokenState(token: string): TokenState {
  try {
    const payload = jwt.verify(token, SECRET_KEY, { algorithms: ['HS256'] })
    const exp = typeof payload === 'object' ? payload.exp : undefined
    if (typeof exp === 'number' && exp * 1000 - Date.now() < TOKEN_EXPIRY_MARGIN_MS) {
      return 'expired'
    }
    return 'valid'
  } catch (err) {
    return err instanceof Error && err.name === 'TokenExpiredError' ? 'expired' : 'invalid'
  }
}

/** Current access of a token to a board, for re-checking open connections. */
const checkAccess = createAccessChecker({ tokenState, fetchMembership })

const checkThinkTogether = createThinkTogetherGuard(({ token, boardUuid }) =>
  fetchWithTimeout(`${API_URL}/api/v1/tt/internal/boards/${boardUuid}/access`, {
    headers: { Authorization: `Bearer ${token}`, 'X-Internal-Key': INTERNAL_KEY },
  }),
)
// ── Redis connection ────────────────────────────────────────────────────────

let redis: Redis | null = null

function getRedis(): Redis {
  if (!redis) {
    redis = new Redis(REDIS_URL, {
      maxRetriesPerRequest: 3,
      lazyConnect: true,
    })
    redis.on('error', (err) => {
      console.error('[collab] Redis error:', err.message)
    })
    redis.connect().catch((err) => {
      console.error('[collab] Redis connection failed:', err)
    })
  }
  return redis
}

function redisYdocKey(boardUuid: string): string {
  return `collab:ydoc:${boardUuid}`
}

// ── Debounced DB persistence ────────────────────────────────────────────────

const dbPersistence = createBoardPersistence({
  delayMs: DB_FLUSH_DELAY,
  backlog: createRedisBoardBacklog(getRedis, MAX_YDOC_BYTES, (boardUuid, error) => {
    console.error(`[collab] Rejecting invalid dirty snapshot for ${boardUuid}:`, error)
  }),
  write: createBoardSnapshotWriter({
    apiUrl: API_URL,
    internalKey: INTERNAL_KEY,
    request: fetchWithTimeout,
    // Agreed notes and ordinary edits use the same writer. Invalidate any
    // older read cache; dirty memory/journal takes precedence until the ack.
    invalidate: boardUuid => getRedis().del(redisYdocKey(boardUuid)),
  }),
  onError(boardUuid, error) {
    console.error(`[collab] DB persistence failed for ${boardUuid}; retaining dirty state:`, error)
  },
})

const scheduleDbFlush = dbPersistence.schedule

// ── Server ──────────────────────────────────────────────────────────────────

// Awareness client id -> owning connection, per loaded document
const awarenessClaims = new WeakMap<object, Map<number, any>>()

const server = new Server({
  port: PORT,
  // ws drops oversized frames before they are buffered into a document
  websocketOptions: { maxPayload: MAX_MESSAGE_BYTES },

  async beforeHandleMessage({
    update,
    documentName,
    connection,
    requestHeaders,
  }: beforeHandleMessagePayload) {
    // Backstop for the ws cap: throwing closes the connection
    if (update.byteLength > MAX_MESSAGE_BYTES) {
      console.warn(
        `[collab] Rejecting ${update.byteLength}-byte message for ${documentName}`,
      )
      throw new Error('Message too large')
    }
    if (!connectionGuard.markHandled(requestHeaders.get(CONNECTION_ID_HEADER), update.byteLength)) {
      throw new Error('Too much traffic')
    }
    if (['true', '1', 'yes'].includes((process.env.TT_ENABLED || '').toLowerCase())) {
      await checkThinkTogether(connection.context?.auth, update)
    }
    if (docSizes.isOversized(documentName)) {
      dropConnection(connection, DOC_TOO_LARGE)
      throw new Error('Board is too large')
    }
  },

  async beforeHandleAwareness({
    connection,
    document,
    states,
    removedClients,
  }: beforeHandleAwarenessPayload) {
    // Server-internal writes have no connection and are trusted
    if (!connection) return
    let claims = awarenessClaims.get(document)
    if (!claims) {
      claims = new Map()
      awarenessClaims.set(document, claims)
    }
    sanitizeAwareness(connection, states, removedClients, claims, document.getConnections())
  },

  async afterLoadDocument({ documentName, document }: afterLoadDocumentPayload) {
    docSizes.measure(documentName, document)
  },

  async onChange({ documentName, document, update }: onChangePayload) {
    if (docSizes.isOversized(documentName)) return
    if (docSizes.recordUpdate(documentName, document, update.byteLength)) {
      // Everyone leaves so the document unloads; store() refuses the oversized
      // state, so it reloads from the last state that fit.
      console.warn(`[collab] ${documentName} passed the size cap; closing its connections`)
      for (const connection of document.getConnections()) {
        dropConnection(connection, DOC_TOO_LARGE)
      }
    }
  },

  async afterUnloadDocument({ documentName }: afterUnloadDocumentPayload) {
    docSizes.forget(documentName)
  },

  async onRequest({ request, response }: onRequestPayload) {
    if (await handleBoardNoteRequest(request, response, {
      internalKey: ['true', '1', 'yes'].includes((process.env.TT_ENABLED || '').toLowerCase()) ? INTERNAL_KEY : '',
      write: persistAgreedNote,
    })) throw null
    // Health check endpoint. Handles both "/" (k8s probe) and "/health"
    if (request.url === '/' || request.url === '/health') {
      response.writeHead(200, { 'Content-Type': 'application/json' })
      response.end(JSON.stringify({ status: 'ok' }))
      // Throw falsy value to stop the hook chain without crashing.
      // Hocuspocus re-throws truthy errors from onRequest which would
      // crash the process; falsy values are silently swallowed.
      // eslint-disable-next-line no-throw-literal
      throw null
    }

    if (requestLimiter.isLimited(clientIp(request, TRUST_PROXY))) {
      response.writeHead(429, { 'Content-Type': 'application/json' })
      response.end(JSON.stringify({ error: 'Too many connection attempts' }))
      // eslint-disable-next-line no-throw-literal
      throw null
    }
  },

  async onUpgrade({ request, socket }: onUpgradePayload) {
    if (!connectionGuard.admit(request, socket)) {
      // Falsy throw stops the upgrade without crashing (see onRequest)
      // eslint-disable-next-line no-throw-literal
      throw null
    }
  },

  async onAuthenticate({
    token,
    documentName,
    connectionConfig,
    requestHeaders,
    instance,
    socketId,
  }: onAuthenticatePayload) {
    if (!token) {
      throw new Error('Authentication required')
    }

    // Verify JWT using same secret + algorithm as backend
    let payload: any
    try {
      payload = jwt.verify(token, SECRET_KEY, { algorithms: ['HS256'] })
    } catch {
      throw new Error('Invalid token')
    }

    const boardUuid = extractBoardUuid(documentName)
    if (!boardUuid) {
      throw new Error('Invalid document name')
    }

    // Verify board membership via backend API (with timeout)
    let response: Response
    try {
      response = await fetchMembership(boardUuid, token)
    } catch (err) {
      console.error(
        `[collab] Membership check failed for ${boardUuid}:`,
        err,
      )
      throw new Error('Authentication service unavailable')
    }

    if (!response.ok) {
      throw new Error('Not authorized for this board')
    }

    const membership = await response.json()

    // The API answers "viewer" for anyone who may only read the board (public
    // boards, org admins without membership). Their socket must not write.
    connectionConfig.readOnly = membership.role === 'viewer'

    // Seats are checked here, once the role is known: viewers and editors
    // have separate seats so viewers can't crowd editors out.
    const existing = instance.documents.get(documentName)?.getConnections() ?? []
    const refusal = seatRefusal(existing, { readOnly: connectionConfig.readOnly, socketId })
    if (refusal) {
      throw new Error(refusal)
    }

    connectionGuard.markAuthenticated(requestHeaders.get(CONNECTION_ID_HEADER))

    return {
      user: {
        id: payload.sub,
        name: membership.username || 'Unknown',
        role: membership.role,
      },
      // Kept for the periodic access re-check
      auth: { token, boardUuid, verifiedAt: Date.now() } satisfies ConnectionAuth,
    }
  },

  extensions: [
    new Database({
      async fetch({ documentName }) {
        const boardUuid = extractBoardUuid(documentName)
        if (!boardUuid) return null

        // Pending edits win, including one acknowledged during a slow read.
        return dbPersistence.load(boardUuid, async () => {
        // 1. Try Redis first
        try {
          const r = getRedis()
          const journaled = await r.hgetBuffer(DIRTY_BOARDS_KEY, boardUuid)
          if (journaled && journaled.byteLength > 0 && journaled.byteLength <= MAX_YDOC_BYTES) {
            // Recovery may still be retrying after a startup Redis outage.
            // Do not enqueue a slow Redis read over a newer live edit. The
            // startup recovery loop owns journal replay and guards that race.
            return new Uint8Array(journaled)
          }
          const cached = await r.getBuffer(redisYdocKey(boardUuid))
          if (cached && cached.byteLength > 0) {
            console.log(
              `[collab] Redis hit for ${boardUuid}: ${cached.byteLength} bytes`,
            )
            return new Uint8Array(cached)
          }
        } catch (err) {
          console.error(
            `[collab] Redis fetch error for ${boardUuid}:`,
            err,
          )
        }

        // 2. Fall back to database (with timeout)
        try {
          const url = `${API_URL}/api/v1/boards/${boardUuid}/ydoc`
          console.log(`[collab] Fetching ydoc from DB for ${boardUuid}`)
          const response = await fetchWithTimeout(url, {
            headers: {
              'X-Internal-Key': INTERNAL_KEY,
            },
          })

          if (!response.ok) {
            console.error(
              `[collab] Failed to fetch ydoc for ${boardUuid}: ${response.status} ${response.statusText}`,
            )
            return null
          }

          const buffer = await response.arrayBuffer()
          if (buffer.byteLength === 0) return null

          const state = new Uint8Array(buffer)
          console.log(
            `[collab] Fetched ydoc from DB for ${boardUuid}: ${buffer.byteLength} bytes`,
          )

          return state
        } catch (err) {
          console.error(
            `[collab] Error fetching ydoc for ${boardUuid}:`,
            err,
          )
          return null
        }
        })
      },

      async store({ documentName, state }) {
        const boardUuid = extractBoardUuid(documentName)
        if (!boardUuid) return
        if (state.byteLength > MAX_YDOC_BYTES) {
          console.warn(
            `[collab] Refusing to persist ${boardUuid}: ${state.byteLength} bytes exceeds the cap`,
          )
          return
        }

        // Journal without a TTL before returning from the store hook. A
        // restart during an API outage can replay the final edit without a
        // client reconnecting or making another change.
        await scheduleDbFlush(boardUuid, state)

        // Keep the expiring read cache separate from the durable dirty journal.
        try {
          const r = getRedis()
          await r.setex(
            redisYdocKey(boardUuid),
            REDIS_YDOC_TTL,
            Buffer.from(state),
          )
        } catch (err) {
          console.error(
            `[collab] Redis store error for ${boardUuid}:`,
            err,
          )
        }

      },
    }),
  ],
})

async function persistAgreedNote(note: AgreedNote): Promise<void> {
  // Fetch a known existing board before opening a direct connection. The
  // normal extension treats a storage outage as an empty document; agreed
  // notes must fail closed and merge a verified snapshot instead.
  const url = `${API_URL}/api/v1/boards/${note.board_uuid}/ydoc`
  const snapshotResponse = await fetchWithTimeout(url, { headers: { 'X-Internal-Key': INTERNAL_KEY } })
  if (!snapshotResponse.ok) throw Error('Board snapshot unavailable')
  const snapshot = new Uint8Array(await snapshotResponse.arrayBuffer())
  if (snapshot.byteLength > MAX_YDOC_BYTES) throw Error('Board is too large')
  const connection = await server.hocuspocus.openDirectConnection(`board:${note.board_uuid}`, { thinkTogetherAgreedNote: true })
  let state: Uint8Array | undefined
  let journaled: Promise<void> | undefined
  try {
    await connection.transact(document => {
      if (snapshot.byteLength) Y.applyUpdate(document, snapshot)
      if (Y.encodeStateAsUpdate(document).byteLength + 8192 > MAX_YDOC_BYTES) throw Error('Board is too large')
      appendAgreedNote(document, note)
      state = Y.encodeStateAsUpdate(document)
      // Register this exact document version before disconnect/store can
      // capture a newer edit. Never enqueue an older note snapshot later.
      journaled = scheduleDbFlush(note.board_uuid, state)
    })
  } finally {
    await connection.disconnect()
  }
  if (!state) throw Error('Board update unavailable')
  // A note response acknowledges the shared serial writer's DB success.
  await journaled
  await dbPersistence.flush(note.board_uuid)
}

// ── Access re-check ──────────────────────────────────────────────────────

let recheckRunning = false
const accessRecheckInterval = setInterval(async () => {
  if (recheckRunning) return
  recheckRunning = true
  try {
    // One document at a time keeps the burst of API calls to one board's users
    for (const document of [...server.hocuspocus.documents.values()]) {
      const closed = await recheckConnections(document.getConnections(), checkAccess, {
        maxUnverifiedMs: MAX_UNVERIFIED_MS,
      })
      if (closed > 0) {
        console.log(`[collab] Closed ${closed} connection(s) on ${document.name} after the access re-check`)
      }
    }
  } catch (err) {
    console.error('[collab] Access re-check failed:', err)
  } finally {
    recheckRunning = false
  }
}, ACCESS_RECHECK_MS)

// ── Graceful shutdown ─────────────────────────────────────────────────────

let shuttingDown = false
async function gracefulShutdown() {
  if (shuttingDown) return
  shuttingDown = true
  console.log('[collab] Shutting down, flushing pending writes...')
  // Hocuspocus may retain documents when a store hook fails. Bound its unload
  // wait as well as our HTTP attempts, and make an incomplete stop explicit.
  const deadline = setTimeout(() => {
    console.error('[collab] Shutdown exceeded 25 seconds; dirty Redis snapshots will replay on restart, but unjournaled memory could not be confirmed')
    process.exit(1)
  }, 25_000)

  // Stop accepting new connections
  clearInterval(rateLimitCleanupInterval)
  clearInterval(accessRecheckInterval)
  clearInterval(backlogRecoveryInterval)
  let exitCode = 0
  try {
    await server.destroy()
    const remaining = await dbPersistence.shutdown()
    if (remaining) console.warn(`[collab] ${remaining} dirty board(s) retained in Redis for startup recovery`)
  } catch (error) {
    console.error('[collab] Shutdown could not complete document cleanup or acknowledge durable board state:', error)
    exitCode = 1
  }

  try { if (redis) await redis.quit() } catch (error) {
    console.error('[collab] Redis shutdown failed:', error)
    exitCode = 1
  }
  clearTimeout(deadline)
  process.exit(exitCode)
}

process.on('SIGINT', gracefulShutdown)
process.on('SIGTERM', gracefulShutdown)

let backlogRecovered = false
let recoveryRunning = false
async function recoverBacklog() {
  if (backlogRecovered || shuttingDown || recoveryRunning) return
  recoveryRunning = true
  try {
    await dbPersistence.recover()
    backlogRecovered = true
  } catch (error) {
    console.error('[collab] Dirty board recovery failed; retrying in 30 seconds:', error)
  } finally { recoveryRunning = false }
}
const backlogRecoveryInterval = setInterval(recoverBacklog, 30_000)

recoverBacklog().then(() => {
  if (!shuttingDown) return server.listen().then(() => {
    console.log(`Hocuspocus collab server running on port ${PORT}`)
  })
})
