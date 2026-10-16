// Control and direction-control characters become visible marks (DESIGN.md 8). The hostile
// characters are built from code points at run time, so this file holds none of them.

import { describe, expect, it } from "vitest";
import { visibleControls } from "./plainText";

/** A string made of the given code points. */
const cp = (...codes: number[]): string => String.fromCodePoint(...codes);

describe("visibleControls", () => {
  it("leaves ordinary text, tabs and newlines alone", () => {
    expect(visibleControls("Plain name (v2).xlsx")).toBe("Plain name (v2).xlsx");
    expect(visibleControls("a\tb\nc")).toBe("a\tb\nc");
    expect(visibleControls("naïve – 日本語 🙂")).toBe("naïve – 日本語 🙂");
  });

  it("shows a right-to-left override and the other direction controls as marks", () => {
    expect(visibleControls(`a${cp(0x202e)}b`)).toBe("a[U+202E]b");
    expect(visibleControls(cp(0x202a, 0x202b, 0x202c, 0x202d))).toBe("[U+202A][U+202B][U+202C][U+202D]");
    expect(visibleControls(`${cp(0x2066)}x${cp(0x2069)}`)).toBe("[U+2066]x[U+2069]");
    expect(visibleControls(cp(0x200e, 0x200f, 0x061c))).toBe("[U+200E][U+200F][U+061C]");
  });

  it("shows zero-width characters, NUL, escapes, DEL and C1 controls", () => {
    expect(visibleControls(`a${cp(0x200b)}b${cp(0x200d)}c${cp(0xfeff)}d`)).toBe("a[U+200B]b[U+200D]c[U+FEFF]d");
    expect(visibleControls(cp(0x0000, 0x001b, 0x007f, 0x0085))).toBe("[U+0000][U+001B][U+007F][U+0085]");
    expect(visibleControls(`x${cp(0x2028)}y`)).toBe("x[U+2028]y");
  });

  it("leaves the characters just outside each range alone", () => {
    expect(visibleControls(cp(0x2010, 0x2027, 0x202f, 0x2057, 0x206a, 0x00a0))).toBe(cp(0x2010, 0x2027, 0x202f, 0x2057, 0x206a, 0x00a0));
  });
});
