import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { UserAvatar, userInitials, avatarColor } from '../UserAvatar'

describe('userInitials', () => {
  it('returns first+last initials when both present', () => {
    expect(userInitials({ first_name: 'Mark', last_name: 'Vletter', email: 'm@v.nl' })).toBe('MV')
  })

  it('falls back to email prefix when name is missing', () => {
    expect(userInitials({ first_name: '', last_name: '', email: 'lisa@voys.nl' })).toBe('LI')
    expect(userInitials({ email: 'peter@getklai.com' })).toBe('PE')
  })
})

describe('avatarColor', () => {
  it('is deterministic per uid', () => {
    expect(avatarColor('abc123')).toBe(avatarColor('abc123'))
  })

  it('returns a tailwind class string', () => {
    const cls = avatarColor('uid-x')
    expect(cls).toMatch(/^bg-\w+-100 text-\w+-700$/)
  })
})

describe('UserAvatar component', () => {
  it('renders the initials of the user', () => {
    render(
      <UserAvatar uid="u1" first_name="Mark" last_name="Vletter" email="m@v.nl" />,
    )
    expect(screen.getByText('MV')).toBeTruthy()
  })

  it('uses the email prefix when name is empty', () => {
    render(<UserAvatar uid="u2" email="lisa@voys.nl" />)
    expect(screen.getByText('LI')).toBeTruthy()
  })
})
