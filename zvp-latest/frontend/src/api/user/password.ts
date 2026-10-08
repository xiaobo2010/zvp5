import { cert, encrypt } from '@/services/platform'

export async function put_v2(id: string, passwd: string, token: string) {
    const axios = (await import('@/plugins/axios')).default
    const pk = await cert()
    await axios(`/v2/users/${id}/password`, {
        method: 'PUT',
        data: {
            password: await encrypt(pk, passwd)
        },
        headers: {
            Authorization: `Bearer ${token}`
        }
    })
}

export async function reset(id: string, token: string) {
    const axios = (await import('@/plugins/axios')).default
    await axios(`/v2/users/${id}/password`, {
        method: 'DELETE',
        headers: {
            Authorization: `Bearer ${token}`
        }
    })
}