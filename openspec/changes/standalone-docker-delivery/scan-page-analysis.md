# 扫描资料页码定位核查（2026-11-05）

## 隔离合成测试

在临时 WeKnora v0.9.0 和 DocReader 中导入三页无文本层的合成 PDF；每页图像有不同颜色和页号，由本地合成视觉服务按图像颜色返回不同的 `MARKER_1`、`MARKER_2`、`MARKER_3`。Embedding 也使用本地合成服务，未访问原知识库或外部模型。对第二页标记发起混合检索，共返回 7 条：3 条 `image_ocr` 和 3 条 `image_caption` 分块的标记分别与来源页 1、2、3 一致；另 1 条 `text` 分块携带三个页面的来源及各自标记。返回的图片 URL 是 `resource://` 随机资源标识，不包含页码。测试环境和合成数据已清理。

这说明本次合成样本的单页分块确实包含可用页码，也说明检索结果可能合并多页；不能用资源 URL 直接推断页面，亦不能将多页结果简单当作单页精确定位。合成视觉服务不验证真实 OCR 质量；先前对实际上海扫描资料抽查时，目标结果未返回 `source_locators`，不能因合成样本成功而声称真实资料页码已修复。

## 代码风险与待决事项

当前 `weknora_adapter.py` 在缺少页码时，会从内容中匹配任意 `_page_数字.jpg` 等文件名并设置页码；这段逻辑没有验证图片是否为该 PDF 的扫描页或属于当前引用文本，普通附件名称也可能触发，存在错误页码风险。已向用户提出停用这条未经来源验证的回退、保留服务提供的可信定位字段；此选择尚未答复，当前代码未改。

后续需要在已授权的资料和目标模型条件下，逐条比较分块正文、具体定位字段与 PDF 原始物理页，验证页码、页范围及视觉高亮。无法确认时维持未定位；多页分块的具体引用所在页不能只由页范围的起点确定。

上游依据：[PDF 扫描解析器](https://github.com/Tencent/WeKnora/blob/v0.9.0/docreader/parser/pdf_parser.py)为扫描页构造定位信息，[图片多模态处理](https://github.com/Tencent/WeKnora/blob/v0.9.0/internal/application/service/image_multimodal.go)将来源定位传递给 OCR 子分块，[检索结果类型](https://github.com/Tencent/WeKnora/blob/v0.9.0/internal/types/search.go)支持携带图片和来源信息；版本、上下文扩展及实际返回仍需核验。
