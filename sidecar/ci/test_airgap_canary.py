"""Tests for the canary's decision logic. The network checks themselves run in the container."""

import unittest

import airgap_canary as canary


class Decide(unittest.TestCase):
    def test_isolated_when_loopback_works_and_outside_is_blocked(self) -> None:
        code, checks, result = canary.decide(True, False, False)
        self.assertEqual(code, 0)
        self.assertEqual(len(checks), 3)
        self.assertIn("ISOLATED", result)
        self.assertNotIn("NOT", result)

    def test_reachable_outbound_fails(self) -> None:
        code, checks, result = canary.decide(True, True, False)
        self.assertEqual(code, 1)
        self.assertIn("REACHABLE", checks[1])
        self.assertIn("NOT ISOLATED", result)

    def test_resolving_dns_fails(self) -> None:
        code, checks, result = canary.decide(True, False, True)
        self.assertEqual(code, 1)
        self.assertIn("RESOLVES", checks[2])
        self.assertIn("NOT ISOLATED", result)

    def test_everything_reachable_fails(self) -> None:
        code, _, _ = canary.decide(True, True, True)
        self.assertEqual(code, 1)

    def test_broken_loopback_is_never_reported_as_isolated(self) -> None:
        # Outside blocked AND loopback broken: the blocks prove nothing, so this must not pass.
        code, _, result = canary.decide(False, False, False)
        self.assertEqual(code, 2)
        self.assertIn("BROKEN CANARY", result)

    def test_broken_loopback_with_reachable_network_is_still_broken(self) -> None:
        code, _, _ = canary.decide(False, True, True)
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
