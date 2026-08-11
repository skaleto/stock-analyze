import type { TermKind } from "./terminology";
import { termMeta } from "./terminology";

export function TermDisplay({
  code,
  kind,
  compact = false,
}: {
  code: string;
  kind: TermKind;
  compact?: boolean;
}) {
  const meta = termMeta(code, kind);
  return (
    <span
      className={`term-display${compact ? " compact" : ""}${meta.known ? "" : " unknown"}`}
      title={meta.explanation}
    >
      <strong>{meta.label}</strong>
      {!compact ? <small>{meta.explanation}</small> : null}
      <code>{code || "-"}</code>
    </span>
  );
}
