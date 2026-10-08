import { Vacation, School, CityGate, Star } from '@icon-park/vue-next'
import { memberActivityStatuses } from '@/icons/status'

export default {
  mode: {
    'on-campus': {
      icon: School,
      color: 'primary'
    },
    'off-campus': {
      icon: CityGate,
      color: 'success'
    },
    'social-practice': {
      icon: Vacation,
      color: 'warning'
    },
    hybrid: {
      icon: Star,
      color: 'danger'
    }
  },
  member: memberActivityStatuses
}
