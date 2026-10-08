export interface Pagination {
    page: number
    pageSize: number
}

export interface CommonParams extends Pagination {
    query: number
}

export interface PagedResponse<T> {
    data: T[]
    total: number
}

export type UserRole = 'admin' | 'department' | 'secretary' | 'teacher'

export interface User {
    _id: string
    id: string
    name: string
    roles: UserRole[]
    class_id: string
    class_name?: string // aux
}

export type LoginType = 'password' | 'password_authz'

interface BaseLoginRequest {
    loginType: LoginType
}

export interface PasswordLoginRequest extends BaseLoginRequest {
    username: string
    password: string
    loginType: 'password'
}

export interface PwdAuthzLoginRequest extends BaseLoginRequest {
    user: string
    password: string
    loginType: 'password_authz'
}

export type LoginRequest = PasswordLoginRequest | PwdAuthzLoginRequest

export interface LoginResponse {
    token: string
    profile: User
    otpauth: boolean
}

export interface Group {
    _id: string
    name: string
    enrollYear?: number // inspired by XueHai
}

export type ActivityType = 'on-campus' | 'off-campus' | 'social-practice' | 'hybrid'

export type ActivityStatus = 'effective' | 'refused' | 'pending' | 'draft'

export interface Activity {
    _id: string
    type: ActivityType
    name: string
    description: string
    status: ActivityStatus
    date: number | string
    createdAt: number | string
    updatedAt: number | string
    creator: string
    creator_name?: string // aux
    member_count?: number // aux
}

export interface ActivityTime {
    'on-campus': string
    'off-campus': string
    'social-practice': string
}

export type ActivityMemberMode = 'on-campus' | 'off-campus' | 'social-practice'

export interface LegacyActivityMember {
    _id: string
    activity: string
    member: string
    mode: ActivityMemberMode
    duration: number
}

export interface NewActivityMember extends ActivityTime {
    _id: string
    activity: string
    member: string
}

export type ActivityMember = LegacyActivityMember | NewActivityMember

export interface UserActivity extends Activity {
    mine: ActivityMember
}

export interface LegacyLog {
    _id: string
    url: string
    user: string
    clarity?: string
    xuehai?: string
    xuehai_id?: string
    data: string
    timestamp: number
    ip?: string
}

export interface LogRequestInfo {
    method: 'GET' | 'POST' | 'PATCH' | 'PUT' | 'DELETE' | 'HEAD' | 'OPTIONS' | 'TRACE' | 'PROPFIND'
    url: string
    headers: Record<string, string[]>
    body: string
    ip: string
}

// 日志级别
// DEBUG: 对常规GET请求的记录
// INFO: 对数据库修改的记录
// WARNING: 越权操作、安全警告
// ERROR: 服务器内部错误、数据库异常
export type LogLevel = 'debug' | 'info' | 'warning' | 'error'

export interface LogParams extends CommonParams {
    levels: string
    types: string
    time_start?: number
    time_end?: number
    search_props: string
    request_methods: string
    user?: string
    xuehai_id?: number
    session?: string
    net_session?: string
}

export interface Log {
    _id: string
    request: LogRequestInfo
    user?: string
    user_name?: string // aux
    xuehai_id?: number // XueHai user id is always a number
    session?: string
    net_session?: string
    summary: string
    type: string
    level: LogLevel
    timestamp: number
}

export interface OtpResponse {
    code: string
    exp: number
}
