'use client'

import { useEffect } from 'react'
import { motion, useReducedMotion, useSpring, useTransform } from 'motion/react'
import { cn } from '@/lib/utils'

// Adapted from Skiper UI's free AnimatedNumber_002 (skiper37).
// https://skiper-ui.com/v1/skiper37 — attribution and changes: ./README.md
export function AnimatedNumber({ value, className }: { value: number; className?: string }) {
  const reduceMotion = useReducedMotion()
  // Start at the actual count: hydration and page navigation never invent progress.
  const spring = useSpring(value, { bounce: 0, duration: 400 })
  const display = useTransform(spring, current => String(Math.round(current)))

  useEffect(() => {
    if (reduceMotion) spring.jump(value)
    else spring.set(value)
  }, [value, reduceMotion, spring])

  return <span className={cn('tabular-nums', className)} style={{ font: 'inherit', color: 'inherit' }}>
    <span className="sr-only">{value}</span>
    <motion.span aria-hidden="true" className="motion-reduce:hidden" style={{ font: 'inherit', color: 'inherit' }}>{display}</motion.span>
    <span aria-hidden="true" className="hidden motion-reduce:inline" style={{ font: 'inherit', color: 'inherit' }}>{value}</span>
  </span>
}
