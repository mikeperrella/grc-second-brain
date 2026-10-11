"""The questionnaire engine (CLAUDE.md 8.4, ADR-013, ADR-014).

M5e: the grounding checks (`grounding`) and the status computation (`status`), both pure functions
of facts, with no model, no file and no clock inside them. Later chunks add the answer service, the
Excel reader and writer, and the run orchestration.
"""
