/**
 * Separate zero-byte selections before the upload pipeline.
 * Empty files must be surfaced to the user instead of being silently dropped.
 */
export function partitionSelectedFiles(fileList) {
  const selected = Array.from(fileList || [])
  return {
    files: selected.filter((file) => Number(file?.size) > 0),
    emptyFiles: selected.filter((file) => Number(file?.size) === 0),
  }
}

export function validateUploadFiles(fileList) {
  const { files: nonempty, emptyFiles } = partitionSelectedFiles(fileList)
  const supported = nonempty.filter((file) => /\.(pdf|zip)$/i.test(file?.name || ''))
  return {
    files: supported.filter((file) => Number(file.size) <= 200 * 1024 * 1024),
    emptyFiles,
    unsupportedFiles: nonempty.filter((file) => !/\.(pdf|zip)$/i.test(file?.name || '')),
    oversizedFiles: supported.filter((file) => Number(file.size) > 200 * 1024 * 1024),
  }
}
