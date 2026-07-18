from __future__ import annotations

import pytest

from system_b.contracts import OrderStatus
from system_b.execution.state_machine import validate_order_transition


def test_invalid_order_state_transition_fails_closed() -> None:
    with pytest.raises(ValueError):
        validate_order_transition(OrderStatus.CREATED, OrderStatus.FILLED)
