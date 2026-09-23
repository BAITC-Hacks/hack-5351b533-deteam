import type { CSSProperties } from 'react'
import arrowRight from '../assets/icons/arrow-right.svg'
import check from '../assets/icons/check.svg'
import chevronDown from '../assets/icons/chevron-down.svg'
import close from '../assets/icons/close.svg'
import lock from '../assets/icons/lock.svg'
import microphone from '../assets/icons/microphone.svg'
import microphoneOff from '../assets/icons/microphone-off.svg'
import phone from '../assets/icons/phone.svg'
import phoneOff from '../assets/icons/phone-off.svg'
import search from '../assets/icons/search.svg'
import sound from '../assets/icons/sound.svg'
import statusDot from '../assets/icons/status-dot.svg'
import stop from '../assets/icons/stop.svg'
import unlock from '../assets/icons/unlock.svg'

const sources = {
  arrowRight,
  check,
  chevronDown,
  close,
  lock,
  microphone,
  microphoneOff,
  phone,
  phoneOff,
  search,
  sound,
  statusDot,
  stop,
  unlock,
} as const

export type IconName = keyof typeof sources

/** SVG assets are masked so icons inherit the surrounding text and button colour. */
export function Icon({ name, size = 16, className = '', spacing = 'before' }: { name: IconName; size?: number; className?: string; spacing?: 'before' | 'after' | 'none' }) {
  const source = `url("${sources[name]}")`
  const style: CSSProperties = {
    width: size,
    height: size,
    display: 'inline-block',
    flex: 'none',
    verticalAlign: '-0.18em',
    marginInlineStart: spacing === 'after' ? '0.35em' : undefined,
    marginInlineEnd: spacing === 'before' ? '0.35em' : undefined,
    backgroundColor: 'currentColor',
    maskImage: source,
    maskSize: 'contain',
    maskRepeat: 'no-repeat',
    maskPosition: 'center',
    WebkitMaskImage: source,
    WebkitMaskSize: 'contain',
    WebkitMaskRepeat: 'no-repeat',
    WebkitMaskPosition: 'center',
  }

  return <span className={`ui-icon ${className}`.trim()} style={style} aria-hidden="true" />
}
