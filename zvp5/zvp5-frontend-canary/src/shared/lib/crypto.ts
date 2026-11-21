/**
 * 登录凭据加密：后端要求 RSA-OAEP(SHA-256) + hex 编码。
 * 明文形状固定为 `{ password, time }`（后端 used_otp_credential 解析同一形状）。
 */

function pemToDer(pem: string): Uint8Array {
  const body = pem
    .replace(/-----BEGIN PUBLIC KEY-----/g, '')
    .replace(/-----END PUBLIC KEY-----/g, '')
    .replace(/\s+/g, '')
  const binary = window.atob(body)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i += 1) {
    bytes[i] = binary.charCodeAt(i)
  }
  return bytes
}

export async function importPublicKey(pem: string): Promise<CryptoKey> {
  const key = await window.crypto.subtle.importKey(
    'spki',
    pemToDer(pem),
    { name: 'RSA-OAEP', hash: 'SHA-256' },
    false,
    ['encrypt']
  )
  return key
}

export function toHex(bytes: Uint8Array): string {
  return Array.from(bytes)
    .map((byte) => byte.toString(16).padStart(2, '0'))
    .join('')
}

export async function encryptCredential(pem: string, password: string): Promise<string> {
  const key = await importPublicKey(pem)
  const payload = JSON.stringify({ password, time: Date.now() })
  const encrypted = await window.crypto.subtle.encrypt(
    { name: 'RSA-OAEP' },
    key,
    new TextEncoder().encode(payload)
  )
  return toHex(new Uint8Array(encrypted))
}
