// Returns the element, or throws if it is missing. Replaces the template's
// `document.getElementById("root") as HTMLElement`, which turned a missing element into a confusing
// crash later. Generic, so tests can use plain objects and need no DOM.
export function requireRoot<T>(element: T | null): T {
  if (element === null) {
    throw new Error("the #root element is missing from index.html");
  }
  return element;
}
