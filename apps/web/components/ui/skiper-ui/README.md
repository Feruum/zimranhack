# Skiper UI components

These components adapt the **free** components published by **Skiper UI**:

- `animated-number.tsx`: [Animated number / AnimatedNumber_002](https://skiper-ui.com/v1/skiper37), from the [official skiper37 registry](https://skiper-ui.com/r/skiper37.json).
- `css-link.tsx`: [CSS Link / Link000](https://skiper-ui.com/v1/skiper40), from the [official skiper40 registry](https://skiper-ui.com/r/skiper40.json).

Source retrieved on 2026-11-15. Skiper UI's [quick start](https://skiper-ui.com/docs/quick-start) permits use and modification of its free components for personal and commercial projects with attribution. This notice and the visible ThinkTogether footer credit preserve that attribution. The registry does not specify an MIT license; these components are not represented as MIT-licensed. No Pro components are included.

Adaptations use LearnHouse's existing `motion/react`, Next.js Link, theme and focus tokens. The number starts at the real value, animates subsequent changes through Motion values without React updates on every frame, and exposes the current value to screen readers. Reduced-motion users see the value immediately. CSS links preserve Next.js navigation and support keyboard focus and reduced motion. No demo data, viewport resets, custom palette or additional runtime dependencies are included.

Retrieved registry SHA-256 hashes:

- `skiper37.json`: `bb018e08b92110610e6d70329d099d8b11f6a5c262bfd998936607f7d6c876b3`
- `skiper40.json`: `df4c9c719080a9454f47005a939a0ee408f8067930db0ff83fca52e3e734e28f`
