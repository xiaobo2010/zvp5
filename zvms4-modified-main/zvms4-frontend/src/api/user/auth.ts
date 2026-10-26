import axios from '@/plugins/axios'
import type { Response, LoginResult } from '@/../types'
import { ElNotification } from 'element-plus'
import { byteArrayToHex } from './utils'
import { encryptData, importPublicKey } from './crypto'

export async function getRSAPublicCert(): Promise<string> {
  const result = (
    await axios('/cert', {
      method: 'GET',
      params: {
        type: 'public',
        method: 'RSA'
      }
    })
  ).data as Response<string>
  if (result.status === 'error') {
    ElNotification({
      title: 'Error when fetching RSA public key',
      message: result.message,
      type: 'error'
    })
    return ''
  }
  return result.data
}

export async function resetPassword(
  user: string,
  password: string,
  token: string
) {
  const payload = JSON.stringify({
    password: password,
    time: Date.now()
  })
  const publicKey = await importPublicKey(await getRSAPublicCert())
  const credential = await encryptData(publicKey, payload)
  const hex = byteArrayToHex(new Uint8Array(credential))
  const result = (
    await axios(`/users/${user}/password`, {
      method: 'PUT',
      data: {
        credential: hex
      },
      headers: {
        Authorization: `Bearer ${token}`
      }
    })
  ).data as Response<LoginResult>
  if (result.status === 'error') {
    ElNotification({
      title: 'Error when resetting password',
      message: result.message,
      type: 'error'
    })
    return false
  }
  return result.status === 'ok'
}

export async function resetToInitial(user: string, token: string) {
  const result = (
    await axios(`/v3/users/${user}/password`, {
      method: 'DELETE',
      headers: {
        Authorization: `Bearer ${token}`
      }
    })
  ).data as Response<{ initial_password: string }>
  if (result.status === 'error') {
    ElNotification({
      title: 'Error when resetting password',
      message: result.message,
      type: 'error'
    })
    return
  }
  // 一次性初始密码只在本次响应中出现，后端不落库、不写日志
  return result.data.initial_password
}

async function UserLogin(
  user: string,
  password: string,
  term: 'long' | 'short' = 'long',
  purpose: 'login' | 'verify' = 'login'
) {
  const payload = JSON.stringify({
    password: password,
    time: Date.now()
  })
  const publicKey = await importPublicKey(await getRSAPublicCert())
  const credential = await encryptData(publicKey, payload)
  const hex = byteArrayToHex(new Uint8Array(credential))
  const result = (await axios<LoginResult>('/users/auth', {
    method: 'POST',
    data: {
      id: user.toString(),
      credential: hex,
      mode: term,
      purpose: purpose
    }
  })).data
  if (term === 'long' && purpose === 'login') {
    localStorage.setItem('token', result.token)
  }
  return result
}

export { UserLogin as useLongTermAuth }
export function useVerificationAuth(user: string, password: string) {
  return UserLogin(user, password, 'short', 'verify')
}
