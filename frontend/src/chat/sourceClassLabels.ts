/* Visible source-class labels at the glass. Must stay aligned with
 * ``SOURCE_CLASS_LABELS`` in app/core/rag/source_class.py — the Python
 * glass tests assert both maps list the same four wordings.
 *
 * Do not re-classify here. The backend already tagged the chunk (#468);
 * this file only turns the machine class into the operator-facing label.
 */

export const SOURCE_CLASS_LABELS: Record<string, string> = {
  project_corpus: 'this contract',
  master_corpus: 'master corpus',
  knowledge_base: 'knowledge base',
  template: 'template',
}

export function sourceClassLabel(sourceClass?: string | null): string {
  const key = (sourceClass || 'project_corpus').trim().toLowerCase()
  return SOURCE_CLASS_LABELS[key] || key
}
