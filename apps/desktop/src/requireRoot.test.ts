import { describe, expect, it } from "vitest";
import { requireRoot } from "./requireRoot";

describe("requireRoot", () => {
  it("returns the very same element when it exists", () => {
    const element = { id: "root" };
    expect(requireRoot(element)).toBe(element);
  });

  it("throws when the element is missing", () => {
    expect(() => requireRoot(null)).toThrow("the #root element is missing");
  });
});
