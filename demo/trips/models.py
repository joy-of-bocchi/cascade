from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, computed_field


class Leg(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    leg_miles: float = Field(description="Distance covered by this leg.")
    leg_hours: float = Field(description="Elapsed hours for this leg.")
    carrier_label: str = Field(description="Generic carrier label for this leg.")

    @computed_field(description="Elapsed minutes for this leg.")
    @property
    def duration_minutes(self) -> float:
        return self.leg_hours * 60.0


class Trip(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    trip_code: str = Field(description="Stable generic trip identifier.")
    trip_legs: tuple[Leg, ...] = Field(description="Ordered legs in the trip.")
    traveler_count: int = Field(description="Number of travelers on the trip.")
    needs_manual_approval: bool = Field(
        description="Whether this trip requires manual approval."
    )


class Summary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    trip: Trip = Field(description="Trip summarized by the pipeline.")
    total_miles: float = Field(description="Total distance across all legs.")
    total_hours: float = Field(description="Total elapsed hours across all legs.")
    average_velocity: float = Field(description="Total miles divided by total hours.")


class ApprovalPacket(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    approval_summary: Summary = Field(description="Summary used for approval.")
    approval_reason: str = Field(description="Reason attached to the approval path.")


class Invoice(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    billing_packet: ApprovalPacket = Field(
        description="Approval packet used for billing."
    )
    invoice_units: int = Field(description="Number of billable trip legs.")
    payable_amount: float = Field(description="Deterministic payable demo amount.")
