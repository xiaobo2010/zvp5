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

/**
 * 临时密码登入（异设备登录的唯一正路）。
 *
 * 挑战令牌来自 403 DEVICE_NOT_PRIMARY 响应体的 detail.challenge；挑战与临时
 * 密码都是一次一用。成功响应不套 envelope，签发的是 1 小时 tp 会话（带
 * temp_session=true）；临时密码是明文 8 位码，不走 RSA 加密链。
 */
export async function useTempPasswordAuth(
  challenge: string,
  tempPassword: string
): Promise<LoginResult> {
  const result = (await axios<LoginResult>('/users/auth/device', {
    method: 'POST',
    data: {
      challenge: challenge,
      temp_password: tempPassword
    }
  })).data
  localStorage.setItem('token', result.token)
  return result
}
