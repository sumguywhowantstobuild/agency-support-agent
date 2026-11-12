"""The cap on connection tests: a few per person per minute, so pressing Test over and over can't make the agent call
HighLevel without end. The clock is a fake one the test moves."""

from support_agent.admin.check_cap import CheckCap


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_a_sixth_check_within_a_minute_waits_for_the_minute_to_pass():
    clock = Clock()
    cap = CheckCap(per_minute=5, clock=clock)

    assert cap.allow(1) is True  # at 0 s
    clock.now += 30
    assert [cap.allow(1) for _ in range(5)] == [True, True, True, True, False]  # four more at 30 s, then held back
    clock.now += 29.9
    assert cap.allow(1) is False  # at 59.9 s the first check is not a minute old yet
    clock.now += 0.2
    assert cap.allow(1) is True  # at 60.1 s it is: its place is free again
    assert cap.allow(1) is False  # the four from 30 s still count


def test_each_person_has_their_own_cap():
    cap = CheckCap(per_minute=5, clock=Clock())
    for _ in range(5):
        cap.allow(1)
    assert cap.allow(1) is False
    assert cap.allow(2) is True  # someone else isn't held back by Ana's presses


def test_a_refused_check_doesnt_use_up_a_place():
    clock = Clock()
    cap = CheckCap(per_minute=5, clock=clock)
    for _ in range(5):
        cap.allow(1)
    clock.now += 30
    for _ in range(10):
        assert cap.allow(1) is False  # pressing again while held back doesn't push the wait further
    clock.now += 30.1
    assert cap.allow(1) is True
