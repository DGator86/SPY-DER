from __future__ import annotations

import pytest

from spy_der.contracts import OrderStatus
from spy_der.execution.state_machine import validate_order_transition


def test_invalid_order_state_transition_fails_closed() -> None:
    with pytest.raises(ValueError):
        validate_order_transition(OrderStatus.CREATED, OrderStatus.FILLED)
