// B2.3 / W3 δ — Send-back-to-AI inline panel inside the Promote dialog.
//
// HONEST AFFORDANCE: the panel and submit button render visibly so the
// design intent is preserved, but the submit button is DISABLED with an
// "Available in next release" tooltip. The send-back handoff requires a
// backend redraft endpoint Session α hasn't surfaced yet — wiring it
// optimistically with a fake "sent to AI" toast would be a fabricated
// capability claim under the user's capability-vs-usage rule. When α
// ships the redraft endpoint, drop the disabled flag and dispatch the
// instructions.

export interface SendBackPanelProps {
  open: boolean;
  value: string;
  onChange: (next: string) => void;
  onCancel: () => void;
}

export function SendBackPanel({
  open,
  value,
  onChange,
  onCancel,
}: SendBackPanelProps) {
  if (!open) return null;
  return (
    <div className="pm-sendback">
      <div className="pm-sendback__label">
        TELL INSPIRA WHAT TO DO DIFFERENTLY
      </div>
      <textarea
        className="pm-sendback__textarea"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="e.g., 'Focus more on the cookie migration approach, less on the service worker angle.'"
      />
      <div className="pm-sendback__actions">
        <button
          type="button"
          className="pm-sendback__cancel"
          onClick={onCancel}
        >
          Cancel
        </button>
        <button
          type="button"
          className="pm-sendback__submit"
          disabled
          aria-disabled="true"
          title="Available in next release"
        >
          Send back
        </button>
      </div>
    </div>
  );
}
