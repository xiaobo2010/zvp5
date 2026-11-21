import { aiRequest } from './ai/http'

/** 主题定义项：后端注册表返回的每条主题记录。 */
export interface ThemeItem {
  id: string
  label: string
  description: string
  status: 'stable' | 'experimental' | 'deprecated'
}

/** GET /v1/themes 响应：系统默认主题 + 主题列表。 */
export interface ListThemesResponse {
  default: string
  items: ThemeItem[]
}

/** GET /v1/themes/me 响应：当前用户选择的主题。 */
export interface GetMyThemeResponse {
  theme: string
}

/** PUT /v1/themes/me 请求体：要切换到的主题 id。 */
export interface SetMyThemeRequest {
  theme: string
}

/** PUT /v1/themes/me 响应：确认已切换的主题。 */
export interface SetMyThemeResponse {
  theme: string
}

/** GET /v1/themes/stats 响应：主题使用统计。 */
export interface ThemeStatsItem {
  id: string
  label: string
  count: number
  percent: number
}

export interface GetThemeStatsResponse {
  total: number
  items: ThemeStatsItem[]
  other: number
}

/**
 * 获取主题列表（需登录）。
 */
export async function listThemes(): Promise<ListThemesResponse> {
  return await aiRequest<ListThemesResponse>({
    method: 'get',
    url: '/v1/themes'
  })
}

/**
 * 获取当前用户选择的主题（需登录）。
 */
export async function getMyTheme(): Promise<GetMyThemeResponse> {
  return await aiRequest<GetMyThemeResponse>({
    method: 'get',
    url: '/v1/themes/me'
  })
}

/**
 * 切换当前用户的主题（需登录）。
 * 未知主题会返回 400。
 */
export async function setMyTheme(theme: string): Promise<SetMyThemeResponse> {
  return await aiRequest<SetMyThemeResponse>({
    method: 'put',
    url: '/v1/themes/me',
    data: { theme }
  })
}

/**
 * 获取主题使用统计（严格 admin）。
 */
export async function getThemeStats(): Promise<GetThemeStatsResponse> {
  return await aiRequest<GetThemeStatsResponse>({
    method: 'get',
    url: '/v1/themes/stats'
  })
}
