// zod feature-tests `new Function("")` when it builds an object schema. The shipped CSP has no
// `unsafe-eval`, so it blocks that call (zod catches the error) and reports a violation anyway.
// `jitless` makes zod skip the probe and use its interpreted parser. Import this module FIRST in
// every file that builds a zod schema (scripts/zodConfigImport.test.mjs enforces it).

import { z } from "zod";

z.config({ jitless: true });
