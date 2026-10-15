import axios from '@/plugins/axios.ts'
import { downloadBlob } from '@/utils/download'

export { getGroup as readOne, getGroups as read, getGroupsV2 as read_v2, updateMethods as update, deleteGroup as delete, createGroupV3, renameGroupV3, addGroupUserV3, removeGroupUserV3 } from './crud'
export * as reads from './reads'
export async function template(id: string, name: string) {
  const result = await axios(`/groups/${id}/template?export_format=excel`, {
    method: 'get',
    responseType: 'blob'
  })
  const mime = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
  downloadBlob(result.data, mime, `${name}.xlsx`)
}
