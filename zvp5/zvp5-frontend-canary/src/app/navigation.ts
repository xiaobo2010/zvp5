import { computed } from 'vue'
import { useSessionStore } from '@/shared/stores/session'

export interface NavItem {
  to: string
  labelKey: string
  icon: string
}

/** 主导航由角色与后端 feature 共同决定，避免在多个组件里重复硬编码。 */
export function useNavItems() {
  const session = useSessionStore()

  return computed<NavItem[]>(() => {
    const items: NavItem[] = [
      { to: '/', labelKey: 'nav.home', icon: 'home' },
      { to: '/activities', labelKey: 'nav.activities', icon: 'compass' },
      { to: '/notifications', labelKey: 'nav.notifications', icon: 'bell' }
    ]

    if (session.isManager) {
      items.push({ to: '/manage', labelKey: 'nav.manage', icon: 'shield' })
    }

    if (session.isAdmin) {
      items.push({ to: '/server', labelKey: 'nav.server', icon: 'server' })
    }

    if (session.isAdmin || session.can('ai.chat') || session.can('ai.agent')) {
      items.push({ to: '/ai', labelKey: 'nav.ai', icon: 'sparkles' })
    }

    items.push({ to: '/me', labelKey: 'nav.profile', icon: 'user' })
    return items
  })
}

export function useRoleLabel() {
  const session = useSessionStore()
  return computed(() => {
    const role = session.roles[0]
    return role ? `role.${role}` : 'role.student'
  })
}
