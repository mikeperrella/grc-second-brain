// Test fixture for the TypeScript rules in .semgrep/grc-airgap.yml. Run with:
//   semgrep --test --config .semgrep/ .semgrep/fixtures
// This file is never compiled. It is excluded from the main scan, because it violates the
// rules on purpose. All data here is synthetic.

export function renderHtml(html: string, el: HTMLElement) {
  // ruleid: grc-ts-dangerously-set-inner-html
  const a = <div dangerouslySetInnerHTML={{ __html: html }} />;
  // ruleid: grc-ts-dangerously-set-inner-html
  const b = <div dangerouslySetInnerHTML={{ __html: html }}>child</div>;
  // ruleid: grc-ts-dangerously-set-inner-html
  el.innerHTML = html;
  // ruleid: grc-ts-dangerously-set-inner-html
  el.outerHTML = html;
  // ok: grc-ts-dangerously-set-inner-html
  el.textContent = html;
  return [a, b];
}

export async function callOut() {
  // ruleid: grc-ts-external-fetch
  await fetch("https://example.com/data");
  // ruleid: grc-ts-external-fetch
  await fetch("http://example.com/data", { method: "POST" });
  // ok: grc-ts-external-fetch
  await fetch("http://127.0.0.1:8000/health");
  // ok: grc-ts-external-fetch
  await fetch("http://localhost:1420/");
  // ok: grc-ts-external-fetch
  await fetch("http://ipc.localhost/command");
  // ok: grc-ts-external-fetch
  await fetch("/relative/path");
}
