import { byteArrayToHex } from '@/utils/byteArray'
import { encryptData, importPublicKey } from '@/utils/crypto'

import type {
    PagedResponse,
    LoginRequest,
    LoginResponse,
    OtpResponse,
    LogParams,
    Log
} from '@/types/services'

async function useAxios() {
    return (await import('@/plugins/axios')).default
}

export async function encrypt(key, data) {
    return byteArrayToHex(new Uint8Array(await encryptData(key, data)))
}

let cert_cache = null

export async function cert() {
    if(!window.crypto?.subtle) {
        throw new Error('window.crypto.subtle does not exist')
    }
    const axios = await useAxios()
    const data = cert_cache ?? (
        await axios('/v1/platform/cert', {
            method: 'GET',
            responseType: 'text'
        })
    ).data
    cert_cache = data
    const pk = await importPublicKey(data as string)
    return pk
}

export async function login(req: LoginRequest) {
    if(req.loginType == 'password' || req.loginType == 'password_authz') {
        const pk = await cert()
        req.password = await encrypt(
            pk, req.password
        )
        if (req.loginType == 'password' && 'newPassword' in req) {
            req.newPassword = await encrypt(
                pk, req.newPassword
            )
        }
    }
    const axios = await useAxios()
    const data = (
        await axios('/v1/platform/login', {
            method: 'POST',
            data: req
        })
    ).data as LoginResponse
    if (req.loginType != 'password_authz')
        localStorage.setItem('token', data.token)
    return data
}

export async function getLogs(params: LogParams) {
    const axios = await useAxios()
    const data = (
        await axios('/v1/platform/logs', {
            method: 'GET',
            params
        })
    ).data as PagedResponse<Log>
    return data
}

export async function getTotp() {
    const axios = await useAxios()
    const data = (
        await axios('/v1/platform/totp', {
            method: 'GET',
            nprogress: false
        })
    ).data as OtpResponse
    return data
}
