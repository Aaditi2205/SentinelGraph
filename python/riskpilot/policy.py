"""Financial decision policy for RiskPilot.

The classifier estimates risk. This module decides what to do with that risk.
It is intentionally deterministic, auditable, and independent of the model.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class PolicyConfig:
    fraud_loss_multiplier: float = 1.0
    chargeback_fee: float = 1_500.0
    review_cost: float = 120.0
    false_hold_rate: float = 0.12
    customer_friction_cost: float = 250.0
    review_residual_risk: float = 0.10


@dataclass(frozen=True)
class Decision:
    action: str
    reason: str
    costs: dict[str, float]
    risk_probability: float | None
    degraded: bool

    def to_dict(self) -> dict:
        return asdict(self)


def expected_costs(probability: float, amount: float, config: PolicyConfig) -> dict[str, float]:
    """Return expected rupee cost for every permitted action."""
    p = min(max(float(probability), 0.0), 1.0)
    amount = max(float(amount), 0.0)
    exposure = amount * config.fraud_loss_multiplier + config.chargeback_fee

    return {
        "allow": p * exposure,
        "review": config.review_cost + p * exposure * config.review_residual_risk,
        "hold": (1.0 - p)
        * (amount * config.false_hold_rate + config.customer_friction_cost),
    }


def decide(
    probability: float | None,
    amount: float,
    config: PolicyConfig,
    *,
    model_available: bool = True,
    distribution_stable: bool = True,
) -> Decision:
    """Choose the minimum-cost action, failing safely to review on model outage."""
    if not model_available or probability is None:
        return Decision(
            action="review",
            reason=(
                "Risk evidence is unavailable. Automatic holds are disabled, so this "
                "transaction is routed to a human reviewer."
            ),
            costs={"allow": 0.0, "review": config.review_cost, "hold": 0.0},
            risk_probability=None,
            degraded=True,
        )

    if not distribution_stable:
        return Decision(
            action="review",
            reason=(
                "Input drift exceeds the approved operating envelope. Automated action is "
                "paused until a reviewer confirms the case."
            ),
            costs={"allow": 0.0, "review": config.review_cost, "hold": 0.0},
            risk_probability=round(float(probability), 6),
            degraded=True,
        )

    costs = expected_costs(probability, amount, config)
    action = min(costs, key=costs.get)
    reasons = {
        "allow": "Expected fraud loss is lower than review or customer-friction cost.",
        "review": "Human review has the lowest expected cost at this uncertainty level.",
        "hold": "Expected fraud exposure exceeds the estimated cost of a temporary hold.",
    }
    return Decision(
        action=action,
        reason=reasons[action],
        costs={key: round(value, 2) for key, value in costs.items()},
        risk_probability=round(float(probability), 6),
        degraded=False,
    )
