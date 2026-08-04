/** Force a fresh document load for security-critical authentication routes. */
export function hardNavigate(path: string): void {
  window.location.replace(path);
}
