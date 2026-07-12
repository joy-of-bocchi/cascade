from __future__ import annotations

from pydantic import BaseModel

from cascade.engine import BuiltPipeline, Pipeline

from .models import ApprovalPacket, Invoice, Leg, Summary, Trip


def needs_manual_approval(trip: Trip) -> bool:
    return trip.needs_manual_approval


def does_not_need_manual_approval(trip: Trip) -> bool:
    return not trip.needs_manual_approval


def _summary_pipeline() -> Pipeline:
    pipeline: Pipeline = Pipeline(root_types=(Trip,), cadence="per trip")

    @pipeline.stage(output=Summary, section="Summarize")
    def summarize_trip_details(trip: Trip) -> Summary:
        total_miles: float = sum(leg.leg_miles for leg in trip.trip_legs)
        total_hours: float = sum(leg.leg_hours for leg in trip.trip_legs)
        average_velocity: float = total_miles / total_hours
        return Summary(
            trip=trip,
            total_miles=total_miles,
            total_hours=total_hours,
            average_velocity=average_velocity,
        )

    return pipeline


def build_demo() -> BuiltPipeline:
    parent: Pipeline = Pipeline(root_types=(Trip,), cadence="per trip")
    parent.include(
        _summary_pipeline(),
        output=Summary,
        name="summarize_trip",
        section="Summarize",
    )

    approval_question: str = "Which approval path applies?"

    @parent.stage(
        output=ApprovalPacket,
        when=needs_manual_approval,
        section="Approve",
        question=approval_question,
    )
    def prepare_manual_approval(summary: Summary) -> ApprovalPacket:
        return ApprovalPacket(
            approval_summary=summary,
            approval_reason="Manual approval requested.",
        )

    @parent.stage(
        output=ApprovalPacket,
        when=does_not_need_manual_approval,
        section="Approve",
        question=approval_question,
    )
    def prepare_auto_approval(summary: Summary) -> ApprovalPacket:
        return ApprovalPacket(
            approval_summary=summary,
            approval_reason="No manual approval requested.",
        )

    @parent.stage(output=Invoice, section="Invoice")
    def create_invoice(packet: ApprovalPacket) -> Invoice:
        invoice_units: int = len(packet.approval_summary.trip.trip_legs)
        payable_amount: float = packet.approval_summary.total_miles * 1.25
        return Invoice(
            billing_packet=packet,
            invoice_units=invoice_units,
            payable_amount=payable_amount,
        )

    return parent.build()


def demo_roots() -> dict[type[BaseModel], BaseModel]:
    leg_a: Leg = Leg(
        leg_miles=45.0,
        leg_hours=0.75,
        carrier_label="local",
    )
    leg_b: Leg = Leg(
        leg_miles=75.0,
        leg_hours=1.25,
        carrier_label="regional",
    )
    trip: Trip = Trip(
        trip_code="TRIP-001",
        trip_legs=(leg_a, leg_b),
        traveler_count=2,
        needs_manual_approval=False,
    )
    return {Trip: trip}
