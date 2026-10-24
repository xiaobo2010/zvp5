import axios from '@/plugins/axios.ts'
import { ElMessage } from 'element-plus'
import { getXuehaiId } from '@/plugins/ua.ts'

const transfer = async ({ headers = {}, ...options } = {}) => {
    const result = (
        await axios({
            method: 'POST',
            url: '/../xh/api',
            data: {
                method: 'POST',
                url: 'https://admin.yunzuoye.net/Other/TransferInterface2',
                headers: { 'content-type': 'application/json' },
                options: { sign_version: 'disabled' },
                body: {
                    ...options,
                    header: {
                        'content-type': 'application/json',
                        ...headers,
                    }
                }
            }
        })
    ).data
    return result
}

export const getXHProfile = async (userId = getXuehaiId()) => {
    const result = (await transfer({
        method: 'GET',
        url: `http://ztp.yunzuoye.cloud/api/v1/pub/platform/users/${userId}/profile`,
    }))
    if(!result.status || result.status >= 400) {
        ElMessage({
            type: 'error',
            message: result.msg ?? result.message ?? result.detail ?? '未知错误',
            plain: true,
            grouping: true,
        })
        throw new Error('request failed with status code ' + result.status)
    }
    return result.body
}