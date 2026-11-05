import { useEffect } from 'react'

// directPlus 工作台向宿主上报"当前会话是否已有内容"。
// 宿主据此决定侧边栏「新建对话」是否真的落库;空白会话点击不应新建。
// hasContent 由各工作台按自身持久化态给出(如 safety-review 的 session 非空、
// 结构/开工的 tasks 非空、区块链的 taskId 非空);onContentChange 由宿主传入。
export function usePlusContentReport(onContentChange, hasContent) {
  useEffect(() => {
    if (typeof onContentChange !== 'function') return
    onContentChange(!!hasContent)
  }, [onContentChange, hasContent])
}