// blockchain-supervision Plus UI 注册元数据。真相源在此文件，设置页「同步注册表」入库。
// code 全局唯一，须与后端模块 backend/app/plus/blockchain_supervision/manifest.py 的 code 一致。
// supportedSlugs 必须与智能卡片配置里该智能体的「英文标识」严格一致，否则绑定校验 400。
export const manifest = {
  code: 'blockchain-supervision',
  name: '工程分包审查智能体',
  supportedSlugs: ['blockchain-supervision'],
  description: '面向城投业主，按五步审查组织分包合同与报审材料核查，展示问题、依据及待补材料，输出供人工复核的审查意见；外部核验缺口如实提示',
  directPlus: true, // 独立后端，不与 host 普通对话共享消息流：首帧直入 Plus，不闪原版
  prototypeFrame: true, // 只隐藏普通对话辅助区；系统级 MainLayout/Sidebar 与 Plus 顶栏仍保留
}
