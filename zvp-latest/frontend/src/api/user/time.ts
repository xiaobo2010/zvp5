import axios from '@/plugins/axios'

async function getUserTime(user: string) {
  return (await axios(`/v3/users/${user}/time`)).data as {
    'on-campus': number
    'off-campus': number
    'social-practice': number
  }
}

export { getUserTime as read }
