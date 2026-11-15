'use client'

import { forwardRef, type ComponentPropsWithoutRef } from 'react'
import Link from 'next/link'
import { cn } from '@/lib/utils'

// Adapted from Skiper UI's free Link000 (skiper40).
// https://skiper-ui.com/v1/skiper40 — attribution and changes: ./README.md
export const SkiperLink = forwardRef<HTMLAnchorElement, ComponentPropsWithoutRef<typeof Link>>(
  ({ children, className, ...props }, ref) => <Link
    {...props}
    ref={ref}
    className={cn(
      'relative inline-flex w-fit items-center gap-2 rounded-sm no-underline focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2',
      "before:pointer-events-none before:absolute before:-bottom-0.5 before:start-0 before:h-px before:w-full before:bg-current before:content-['']",
      'before:origin-right before:scale-x-0 before:transition-transform before:duration-200 before:ease-out',
      'hover:before:origin-left hover:before:scale-x-100 focus-visible:before:origin-left focus-visible:before:scale-x-100 motion-reduce:before:transition-none',
      'rtl:before:origin-left rtl:hover:before:origin-right rtl:focus-visible:before:origin-right',
      className,
    )}
  >{children}</Link>,
)
SkiperLink.displayName = 'SkiperLink'
