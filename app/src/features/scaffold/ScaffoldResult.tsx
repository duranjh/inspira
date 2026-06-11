/**
 * ScaffoldResult — after a successful generation, shows the file tree
 * plus a "Download zip" CTA and a "Regenerate" secondary action.
 *
 * The content of each file isn't shipped in the manifest response —
 * downloads hit the dedicated /download streaming endpoint. Preview on
 * hover shows the README preview (first ~500 chars) as the only
 * textual preview; file-level previews would require a second round
 * trip that isn't worth the complexity for v1.
 */

import { useState, type ReactElement } from "react";
import { t } from "../../i18n";

export type ScaffoldResultProps = {
  scaffold: {
    scaffold_id: string;
    framework: string;
    language: string;
    file_count: number;
    readme_preview: string;
    post_install_steps: string[];
    truncation_note: string;
    files: Array<{ path: string; size: number }>;
  };
  canRegen: boolean;
  onDownload: () => Promise<void>;
  onRegenerate: () => Promise<void>;
};

// Human-readable file-size formatting. Tiny files are common (a few
// hundred bytes) so we fall back to "0.X KB" rather than the raw byte
// count. Keeps the tree column narrow.
function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const kb = bytes / 1024;
  if (kb < 10) return `${kb.toFixed(1)} KB`;
  return `${Math.round(kb)} KB`;
}

export function ScaffoldResult(props: ScaffoldResultProps): ReactElement {
  const {
    scaffold,
    canRegen,
    onDownload,
    onRegenerate,
  } = props;
  const [downloading, setDownloading] = useState(false);
  const [regenerating, setRegenerating] = useState(false);

  const handleDownload = async (): Promise<void> => {
    if (downloading) return;
    setDownloading(true);
    try {
      await onDownload();
    } finally {
      setDownloading(false);
    }
  };

  const handleRegen = async (): Promise<void> => {
    if (regenerating || !canRegen) return;
    setRegenerating(true);
    try {
      await onRegenerate();
    } finally {
      setRegenerating(false);
    }
  };

  return (
    <section
      className="scaffold-result"
      aria-label={t("scaffold_result.aria")}
    >
      <header className="scaffold-result__header">
        <h3 className="scaffold-result__title">{t("scaffold_result.title")}</h3>
        <span className="scaffold-result__chip">{scaffold.framework}</span>
        <span className="scaffold-result__chip">{scaffold.language}</span>
        <span className="scaffold-result__chip">
          {scaffold.file_count}{" "}
          {scaffold.file_count === 1
            ? t("scaffold_result.file_one")
            : t("scaffold_result.file_many")}
        </span>
      </header>

      {scaffold.truncation_note ? (
        <p className="scaffold-result__truncation">
          {scaffold.truncation_note}
        </p>
      ) : null}

      {scaffold.readme_preview ? (
        <p className="scaffold-result__preview">
          {scaffold.readme_preview}
        </p>
      ) : null}

      <ul
        className="scaffold-result__tree"
        aria-label={t("scaffold_result.files_aria", { count: String(scaffold.file_count) })}
      >
        {scaffold.files.map((f) => (
          <li key={f.path} title={f.path}>
            <span>{f.path}</span>
            <span className="scaffold-result__tree-size">
              {formatSize(f.size)}
            </span>
          </li>
        ))}
      </ul>

      <div className="scaffold-result__side">
        <button
          type="button"
          className="scaffold-result__download"
          onClick={() => void handleDownload()}
          disabled={downloading}
        >
          {downloading ? t("scaffold_result.downloading") : t("scaffold_result.download")}
        </button>
        <button
          type="button"
          className="scaffold-result__regen"
          onClick={() => void handleRegen()}
          disabled={!canRegen || regenerating}
          title={
            canRegen
              ? t("scaffold_result.regen_title_ok")
              : t("scaffold_result.regen_title_upgrade")
          }
        >
          {regenerating
            ? t("scaffold_result.regenerating")
            : t("scaffold_result.regen")}
        </button>
        {scaffold.post_install_steps.length > 0 ? (
          <div>
            <span className="scaffold-result__post-label">
              {t("scaffold_result.after_unzip")}
            </span>
            <pre className="scaffold-result__post">
              {scaffold.post_install_steps.join("\n")}
            </pre>
          </div>
        ) : null}
      </div>
    </section>
  );
}
