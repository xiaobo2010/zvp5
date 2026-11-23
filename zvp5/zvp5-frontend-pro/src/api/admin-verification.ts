import { fetchProfile, verifyPassword } from './canary'

/** Password proofs are returned to the caller only; never replace the session. */
export async function verifyAdminPassword(password: string): Promise<string> {
  const profile = await fetchProfile()
  return verifyPassword(String(profile.id), password)
}
