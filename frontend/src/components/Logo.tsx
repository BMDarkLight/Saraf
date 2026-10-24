import { useId } from "react";

/** The Saraf mark: two coins passing each other, one outlined and one solid, the trade a money changer makes.
 *  Drawn in currentColor so it follows the theme. */
export function SarafMark({ className, size }: { className?: string; size?: number }) {
  const cut = useId();
  return (
    <svg className={className} width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
      <defs>
        <mask id={cut}>
          <rect width="32" height="32" fill="#fff" />
          <circle cx="11.5" cy="16" r="10.9" fill="#000" />
        </mask>
      </defs>
      <circle cx="20.5" cy="16" r="8.5" fill="currentColor" mask={`url(#${cut})`} />
      <circle cx="11.5" cy="16" r="8.3" fill="none" stroke="currentColor" strokeWidth="2.4" />
    </svg>
  );
}

export function Wordmark() {
  return (
    <a className="brand" href="/" aria-label="صراف">
      <SarafMark className="brand-logo" />
      <span className="brand-name"><b>صراف</b><small>Saraf</small></span>
    </a>
  );
}
